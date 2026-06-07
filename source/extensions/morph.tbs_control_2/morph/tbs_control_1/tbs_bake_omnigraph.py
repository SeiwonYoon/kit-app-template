"""LAM Bake — OmniGraph(PushGraph) 등 런타임으로 구동되는 자산을 정통 USD timeSamples 로 변환.

배경
====
사용자 자산 (3ds Max → FBX → USD 변환본 / Omniverse Animation Curve 로 만든 USD 등) 은
prim 의 xform 속성에 직접 timeSamples 가 박혀있지 않고, **`OmniGraph(PushGraph)`** 같은
Kit 런타임 그래프가 매 frame 변환을 push 하는 형태로 들어온다.

LAM 의 Option E (offscreen Stage 독립 평가) 는 외부 in-memory stage 에 OmniGraph 런타임이
없어 이 형태를 평가할 수 없다. 본 모듈은 **master(default) context 의 OmniGraph 런타임이
이미 자산을 평가 가능한 상태인 점을 이용**해, master 안에 reference 된 인스턴스 prim
산하의 xform 결과를 매 frame capture 하여 새 `*_baked.usd` 파일에 표준 USD timeSamples 로
박는다.

baked USD 결과
==============
- prim 트리 구조는 원본과 동일 (원본을 reference 로 가져온 뒤 over 로 attribute 박음)
- `xformOp:*` / `visibility` 등 attribute 가 timeSamples 형태로 baked
- `/Root/PushGraph` 등 OmniGraph 류 prim 은 `over { active = false }` 로 비활성화 →
  baked 를 다시 로드해도 두 번 평가되지 않음

LAM 흐름
========
1) 사용자가 LAM Window 의 [Bake] 클릭 (인스턴스 행마다).
2) 본 모듈이 master(default) context 의 timeline 을 0..end_tc 스크럽하며 capture.
3) baked.usd 디스크 저장.
4) LAM Window 가 동일 prim_path 의 인스턴스를 baked.usd 로 자동 교체(remove_usd +
   add_usd). 이후 Option E 가 정상 동작 → 인스턴스 별 독립 timeline 재생 가능.

TBS 영향
========
본 모듈은 **`morph.tbs_control_1` 의 어떤 심볼도 import 하지 않는다.** master(default)
context 의 timeline 만 잠시 점유하며, bake 시작 전 timeline 시각·재생 상태를 저장했다가
종료 시 복원한다 (best-effort).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

from .tbs_asset_diagnostics import discover_animated_attrs
from .tbs_types import TBS_FIXED_FPS


_PRINT_PREFIX = "[TBS/Bake]"

# sparse_time_samples 분기에서 dict.get 의 "값이 없음" 과 "None 이라는 USD 값" 을 구분하기
# 위한 센티넬. (USD 값으로 None 자체가 들어오진 않지만, 명시 분기로 안전.)
_MISSING = object()

# capture 대상 attribute 이름 규칙.
# - xformOp:* (translate / rotate / scale / orient / transform / 사용자 정의 op)
# - visibility (token)
# - xformOpOrder (token[]) — 위 xformOp 들이 의미를 갖도록 default 한 번 함께 박음.
_DEFAULT_CAPTURE_NAME_PREFIXES: Tuple[str, ...] = ("xformOp:",)
_DEFAULT_CAPTURE_NAMES: Tuple[str, ...] = ("visibility",)


def _is_capture_target_attr_name(name: str) -> bool:
    if name in _DEFAULT_CAPTURE_NAMES:
        return True
    for pfx in _DEFAULT_CAPTURE_NAME_PREFIXES:
        if name.startswith(pfx):
            return True
    if name == "xformOpOrder":
        return True
    return False


def make_default_baked_path(asset_path: str) -> str:
    """`<asset>_baked.usd` 형태의 기본 출력 경로 생성 (file mode 전용)."""
    base, _ext = os.path.splitext(os.path.abspath(asset_path))
    return base + "_baked.usd"


def _fmt_timecode_key(tc: float) -> str:
    """로그용 timeCode 키 — 정수 프레임이면 정수 문자열."""
    try:
        f = float(tc)
    except Exception:
        return str(tc)
    if abs(f - round(f)) < 1e-5:
        return str(int(round(f)))
    return f"{f:.4g}"


def _format_ts_dict_preview(
    ts_info: Dict[float, Any],
    *,
    max_pairs: int,
) -> str:
    """FBX→USD usda 에서 보이는 것과 유사한 `{tc: value, ...}` 한 줄 미리보기."""
    if not ts_info:
        return "{}"
    keys: List[float] = []
    for k in ts_info.keys():
        try:
            fk = float(k)
        except Exception:
            continue
        if fk == fk:
            keys.append(fk)
    keys.sort()
    if not keys:
        return "{}"
    mp = max(2, int(max_pairs))
    if len(keys) <= mp:
        parts = [
            f"{_fmt_timecode_key(k)}: {_dump_brief_value(ts_info.get(k))}" for k in keys
        ]
        return "{" + ", ".join(parts) + "}"
    head_n = max(1, mp - 2)
    head = keys[:head_n]
    tail = keys[-1:]
    mid_idx = max(head_n, len(keys) // 2)
    mid = keys[mid_idx] if mid_idx < len(keys) - 1 else keys[len(keys) // 2]
    picks = sorted(set(list(head) + [mid] + tail))
    parts = [f"{_fmt_timecode_key(k)}: {_dump_brief_value(ts_info.get(k))}" for k in picks]
    return "{" + ", ".join(parts) + f", …(+{len(keys) - len(picks)} keys)…" + "}"


def _log_baked_ts_path_summary(
    dynamic_sdf: List[Tuple[Any, Dict[float, Any]]],
    *,
    layer_timecodes: Tuple[Any, Any],
    tps_hint: Any,
    max_prim_rows: int,
    max_attr_detail_lines: int,
    max_samples_per_attr: int,
) -> None:
    """경로별 timeSamples 요약 + FBX-USD 스타일 미리보기 + 다중 prim 스팬 정합 진단.

    OmniGraph bake 는 주로 `attrSpec.SetInfo('timeSamples', dict)` 로 기록되어
    `Usd.Attribute.GetNumTimeSamples()` 가 0 인 경우가 있어(합성 레이어 특성),
    본 블록은 **Sdf dict 기록**을 기준으로 실무 FBX 변환본과 같은 '경로별 샘플 맵'
    형태를 로그로 보여준다.
    """
    if not dynamic_sdf:
        print(
            f"{_PRINT_PREFIX}/TS (summary) dynamic timeSamples dict 가 0 개 — "
            f"정적 prune 만 있거나 bake 대상 capture 가 비었을 수 있음",
            flush=True,
        )
        return

    prim_rows: Dict[str, List[Tuple[str, str, int, float, float]]] = defaultdict(list)
    g_mn: Optional[float] = None
    g_mx: Optional[float] = None

    for spec, ts_info in dynamic_sdf:
        if not isinstance(ts_info, dict) or not ts_info:
            continue
        try:
            ppath = str(spec.path.GetPrimPath())
        except Exception:
            continue
        try:
            full = str(spec.path)
        except Exception:
            full = ""
        if full.startswith(ppath + "."):
            aname = full[len(ppath) + 1 :]
        elif full.startswith(ppath):
            aname = full[len(ppath) :].lstrip(".")
        else:
            aname = full.rsplit(".", 1)[-1] if "." in full else full
        try:
            tn = str(spec.typeName) if hasattr(spec, "typeName") else "?"
        except Exception:
            tn = "?"
        tkeys: List[float] = []
        for k in ts_info.keys():
            try:
                fk = float(k)
            except Exception:
                continue
            if fk == fk:
                tkeys.append(fk)
        if not tkeys:
            continue
        tkeys.sort()
        mn, mx = float(tkeys[0]), float(tkeys[-1])
        prim_rows[ppath].append((aname, tn, len(tkeys), mn, mx))
        g_mn = mn if g_mn is None or mn < g_mn else g_mn
        g_mx = mx if g_mx is None or mx > g_mx else g_mx

    n_prims = len(prim_rows)
    n_attrs = sum(len(v) for v in prim_rows.values())
    s_meta, e_meta = layer_timecodes
    print(
        f"{_PRINT_PREFIX}/TS ===== 경로별 timeSamples (bake 변환 / FBX-USD 유사 dict) =====",
        flush=True,
    )
    print(
        f"{_PRINT_PREFIX}/TS 요약: prims_with_timeSamples={n_prims} "
        f"attrs_total={n_attrs} union_timeCode_span=[{g_mn},{g_mx}] "
        f"layer_meta=[{s_meta},{e_meta}]@tps={tps_hint}",
        flush=True,
    )

    # 다중 경로 정합 — (1) layer 메타 start/end 와 실제 샘플 union 비교
    # (2) 동일 prim 산하 속성들의 샘플 개수가 모두 같은지 (FBX 변환본에서 흔한 패턴).
    if g_mn is not None and g_mx is not None:
        tol = 1e-2
        meta_warn = ""
        try:
            ls = float(s_meta) if s_meta is not None else None
            le = float(e_meta) if e_meta is not None else None
        except Exception:
            ls, le = None, None
        if ls is not None and le is not None:
            if g_mn < ls - tol or g_mx > le + tol:
                meta_warn = (
                    f"WARN union [{g_mn},{g_mx}] 가 layer 메타 [{ls},{le}] 을 벗어남 "
                    f"(메타 갱신 누락 가능)"
                )
        if meta_warn:
            print(f"{_PRINT_PREFIX}/TS {meta_warn}", flush=True)
        else:
            print(
                f"{_PRINT_PREFIX}/TS OK 샘플 union [{g_mn},{g_mx}] 가 layer 메타 "
                f"[{s_meta},{e_meta}] 와 정합 (또는 메타 비숫자)",
                flush=True,
            )

    uneven: List[str] = []
    for ppath, rows in prim_rows.items():
        counts = sorted({r[2] for r in rows})
        if len(counts) > 1:
            uneven.append(f"{ppath} n_keys={counts[0]}..{counts[-1]} ({len(rows)} attrs)")
    if uneven:
        print(
            f"{_PRINT_PREFIX}/TS WARN 동일 prim 내 속성별 timeSamples 키 개수 불일치 "
            f"(일부만 sparse bake 되었을 수 있음): {uneven[:12]}",
            flush=True,
        )
    else:
        print(
            f"{_PRINT_PREFIX}/TS OK 각 prim 산하 모든 기록 속성의 샘플 개수가 종류당 1 가지 "
            f"(동일 프레임 그리드에 맞춤)",
            flush=True,
        )

    prim_sorted = sorted(prim_rows.keys())
    shown_prims = prim_sorted[: max(1, int(max_prim_rows))]
    for ppath in shown_prims:
        rows = prim_rows[ppath]
        names = [f"{a}:{t}×{n}" for a, t, n, _mn, _mx in sorted(rows, key=lambda x: x[0])]
        pmn = min(r[3] for r in rows)
        pmx = max(r[4] for r in rows)
        name_preview = ", ".join(names[:12])
        if len(names) > 12:
            name_preview += f", …(+{len(names) - 12})"
        print(
            f"{_PRINT_PREFIX}/TS prim={ppath} attrs={len(rows)} "
            f"span=[{pmn},{pmx}] {name_preview}",
            flush=True,
        )
    if len(prim_sorted) > len(shown_prims):
        print(
            f"{_PRINT_PREFIX}/TS …(+{len(prim_sorted) - len(shown_prims)} more prims omitted; "
            f"LAM_BAKE_DUMP_TS_PRIM_ROWS 로 상한 조정)",
            flush=True,
        )

    # 속성별 dict 출력 — USDA (USD ASCII) 형식과 유사하게 prim 블록 단위로 출력한다.
    # 사용자 요청 (2026-05-12): "xformOp.timeSamples = ... 와 같은 방식으로 프레임별
    # 객체 좌표가 prim 별로 나오는데 bake 된 후 그런 형식의 데이터 로그를 원함."
    by_prim: Dict[str, List[Tuple[str, str, Dict[float, Any]]]] = defaultdict(list)
    for spec, ts_info in dynamic_sdf:
        if not isinstance(ts_info, dict) or not ts_info:
            continue
        try:
            ppath = str(spec.path.GetPrimPath())
            full = str(spec.path)
        except Exception:
            continue
        if full.startswith(ppath + "."):
            aname = full[len(ppath) + 1 :]
        else:
            aname = full
        try:
            tn = str(spec.typeName) if hasattr(spec, "typeName") else "?"
        except Exception:
            tn = "?"
        by_prim[ppath].append((aname, tn, ts_info))

    try:
        per_prim_attr_env = int(os.environ.get("LAM_BAKE_DUMP_TS_USDA_ATTRS_PER_PRIM", "8"))
    except Exception:
        per_prim_attr_env = 8
    try:
        per_attr_keys_env = int(
            os.environ.get(
                "LAM_BAKE_DUMP_TS_USDA_KEYS_PER_ATTR", str(int(max_samples_per_attr) * 2)
            )
        )
    except Exception:
        per_attr_keys_env = max(4, int(max_samples_per_attr) * 2)

    prim_sorted_for_usda = sorted(by_prim.keys())
    cap_lines_left = max(1, int(max_attr_detail_lines))
    prim_blocks_emitted = 0
    attrs_emitted = 0

    for ppath in prim_sorted_for_usda:
        if cap_lines_left <= 0:
            break
        attrs = sorted(by_prim[ppath], key=lambda x: x[0])
        attrs_show = attrs[: max(1, per_prim_attr_env)]
        prim_blocks_emitted += 1
        print(f"{_PRINT_PREFIX}/TS_ATTR over \"{ppath}\" {{", flush=True)
        cap_lines_left -= 1
        for aname, tn, ts_info in attrs_show:
            if cap_lines_left <= 0:
                break
            keys: List[float] = []
            for k in ts_info.keys():
                try:
                    fk = float(k)
                except Exception:
                    continue
                if fk == fk:
                    keys.append(fk)
            keys.sort()
            n_keys = len(keys)
            cap_per_attr = max(2, int(per_attr_keys_env))
            if n_keys <= cap_per_attr:
                pick = keys
                truncated = False
            else:
                half = max(1, cap_per_attr // 2)
                pick = keys[: cap_per_attr - half] + keys[-half:]
                truncated = True
            print(
                f"{_PRINT_PREFIX}/TS_ATTR     {tn} {aname}.timeSamples = "
                f"{{ /* n_keys={n_keys} */",
                flush=True,
            )
            cap_lines_left -= 1
            prev_tail = -1
            for j, tc in enumerate(pick):
                if truncated and j == len(pick) - max(1, cap_per_attr // 2):
                    print(
                        f"{_PRINT_PREFIX}/TS_ATTR         …({n_keys - len(pick)} keys omitted)…",
                        flush=True,
                    )
                    cap_lines_left -= 1
                _ = prev_tail  # keep var to placate linters
                v = _dump_brief_value(ts_info.get(tc))
                print(
                    f"{_PRINT_PREFIX}/TS_ATTR         {_fmt_timecode_key(tc)}: {v},",
                    flush=True,
                )
                cap_lines_left -= 1
                attrs_emitted += 1
                if cap_lines_left <= 0:
                    break
            print(f"{_PRINT_PREFIX}/TS_ATTR     }}", flush=True)
            cap_lines_left -= 1
        if len(attrs) > len(attrs_show):
            print(
                f"{_PRINT_PREFIX}/TS_ATTR     // …(+{len(attrs) - len(attrs_show)} "
                f"more attrs on this prim — LAM_BAKE_DUMP_TS_USDA_ATTRS_PER_PRIM 조정)",
                flush=True,
            )
            cap_lines_left -= 1
        print(f"{_PRINT_PREFIX}/TS_ATTR }}", flush=True)
        cap_lines_left -= 1

    if len(prim_sorted_for_usda) > prim_blocks_emitted:
        print(
            f"{_PRINT_PREFIX}/TS_ATTR // …(+{len(prim_sorted_for_usda) - prim_blocks_emitted} "
            f"more prim blocks omitted; LAM_BAKE_DUMP_TS_ATTR_LINES 로 상한 조정)",
            flush=True,
        )

    print(
        f"{_PRINT_PREFIX}/TS 참고: FBX→USD 변환본은 파일 곳곳에 이미 timeSamples 가 있어 "
        f"bake 가 필요 없을 수 있음. OmniGraph/curve-only 자산은 본 bake 로 dict 가 생성됨.",
        flush=True,
    )


def _log_baked_layer_dump(
    layer: Any,
    *,
    max_attrs: int = 40,
    max_samples_per_attr: int = 4,
) -> None:
    """Bake 결과 layer 의 prim / attr / timeSamples 형식을 콘솔에 dump.

    사용자 요청 (2026-05-12): "bake 하는 경우 로그에 변환된 timeSamples 데이터 형식을
    확인할 수 있게끔 되었으면 좋겠어." → in-memory bake 모드에서 baked layer 가 디스크에
    저장되지 않으므로 사용자가 형식을 시각적으로 확인할 수단이 없다. 본 함수는 layer 를
    Sdf-level 로 traverse 하여 형식을 줄 단위로 출력한다 (Sdf.Layer.ExportToString 은
    timeSamples 가 큰 경우 너무 길어 콘솔에 부적합).

    추가 (2026-05-12 후반): ``[TBS/Bake]/TS`` 블록 — FBX→USD 파일에서 보이는 것과 같은
    ``{timeCode: value, ...}`` 스타일 미리보기 + **경로(prim)별** 속성 목록·샘플 union·
    layer 메타 정합·동일 prim 내 샘플 개수 불일치 경고. 환경 변수:
    ``LAM_BAKE_DUMP_TS_PRIM_ROWS`` (기본 40), ``LAM_BAKE_DUMP_TS_ATTR_LINES`` (기본 24).

    Args:
        layer: anonymous (또는 file-backed) `pxr.Sdf.Layer`.
        max_attrs: 출력할 attribute 최대 개수 (성능 보호). 초과분은 ``…`` 로 생략.
        max_samples_per_attr: 각 attribute 당 출력할 timeSamples 개수 (앞쪽 N 개 + 마지막 1 개).
    """
    if layer is None:
        return

    Sdf = None
    try:
        from pxr import Sdf as _Sdf  # type: ignore
        Sdf = _Sdf
    except Exception:
        # pxr 가 없으면 트래버스 불가 — 헤더만 찍고 종료.
        print(
            f"{_PRINT_PREFIX}/DUMP pxr.Sdf import 실패 — layer dump skipped",
            flush=True,
        )
        return

    print(f"{_PRINT_PREFIX}/DUMP ===== baked layer dump begin =====", flush=True)
    try:
        ident = layer.identifier
    except Exception:
        ident = "<unavailable>"
    try:
        n_root_prims = len(layer.rootPrims) if hasattr(layer, "rootPrims") else -1
    except Exception:
        n_root_prims = -1
    try:
        s_tc = layer.startTimeCode if hasattr(layer, "startTimeCode") else None
        e_tc = layer.endTimeCode if hasattr(layer, "endTimeCode") else None
        tps = layer.timeCodesPerSecond if hasattr(layer, "timeCodesPerSecond") else None
    except Exception:
        s_tc, e_tc, tps = None, None, None
    print(
        f"{_PRINT_PREFIX}/DUMP layer.identifier={ident}",
        flush=True,
    )
    print(
        f"{_PRINT_PREFIX}/DUMP layer.timeRange=[{s_tc},{e_tc}]@tps={tps} "
        f"root_prims={n_root_prims}",
        flush=True,
    )

    # 1) Usd 합성 (필수) — author 가 `attrSpec.SetInfo("timeSamples", dict)` 로 박은 샘플은
    # `Sdf.Layer.GetField(path, "timeSamples")` 로는 안 잡히는 경우가 있어(사용자 로그:
    # attrs_written>0 인데 dynamic=0), 반드시 `Usd.Stage.Open(layer)` 로 합성 평가한다.
    dynamic_usd: List[Tuple[str, int]] = []
    usd_st = None
    try:
        from pxr import Usd  # type: ignore

        usd_st = Usd.Stage.Open(layer)
        if usd_st is not None:
            for prim in usd_st.Traverse():
                try:
                    for attr in prim.GetAttributes():
                        try:
                            n_ts = int(attr.GetNumTimeSamples())
                        except Exception:
                            n_ts = 0
                        if n_ts > 0:
                            dynamic_usd.append((str(attr.GetPath()), n_ts))
                except Exception:
                    continue
            print(
                f"{_PRINT_PREFIX}/DUMP Usd.Stage.Open OK — "
                f"composed attrs with timeSamples>0: {len(dynamic_usd)}",
                flush=True,
            )
        else:
            print(
                f"{_PRINT_PREFIX}/DUMP Usd.Stage.Open returned None — Sdf fallback only",
                flush=True,
            )
    except Exception as exc_usd:
        print(
            f"{_PRINT_PREFIX}/DUMP Usd traverse failed: {exc_usd} — Sdf fallback",
            flush=True,
        )

    cap = max(1, int(max_attrs))
    cap_dyn = max(1, int(cap * 0.85))
    shown = min(len(dynamic_usd), cap_dyn)
    for i, (ap, n_ts) in enumerate(dynamic_usd[:shown]):
        sample_bits: List[str] = []
        if usd_st is not None:
            try:
                from pxr import Sdf  # type: ignore

                attr2 = usd_st.GetAttributeAtPath(Sdf.Path(ap))
                if attr2 and attr2.IsValid():
                    t_codes: List[float] = []
                    try:
                        raw = attr2.GetTimeSampleTimes()
                        if raw is not None:
                            try:
                                t_codes = [float(x) for x in raw]
                            except Exception:
                                t_codes = []
                    except Exception:
                        try:
                            attr2.GetTimeSampleTimes(t_codes)  # type: ignore[attr-defined]
                        except Exception:
                            t_codes = []
                    picks_idx = {0, max(0, n_ts // 2), max(0, n_ts - 1)}
                    for j in sorted(picks_idx):
                        if j >= len(t_codes):
                            continue
                        tc = float(t_codes[j])
                        try:
                            v = attr2.Get(tc)
                            sample_bits.append(f"@{tc:.4g}={_dump_brief_value(v)}")
                        except Exception:
                            sample_bits.append(f"@{tc:.4g}=<?>")
            except Exception:
                pass
        extra = f" samples=[{', '.join(sample_bits)}]" if sample_bits else ""
        print(
            f"{_PRINT_PREFIX}/DUMP  [{i:>4}] DYN_USD {ap} timeSamples_n={n_ts}{extra}",
            flush=True,
        )
    if len(dynamic_usd) > shown:
        print(
            f"{_PRINT_PREFIX}/DUMP  …({len(dynamic_usd) - shown} more DYN_USD attrs omitted)",
            flush=True,
        )

    # 2) Sdf 스펙 기반 보조 — default-only / spec-only (진단용, timeSamples dict 가 Sdf 에
    # 직접 보이는 경우만 dynamic_sdf 에 합산).
    attr_specs: List[Any] = []

    def _collect(path: Any) -> None:  # path: Sdf.Path
        try:
            spec = layer.GetAttributeAtPath(path)
        except Exception:
            spec = None
        if spec is not None:
            attr_specs.append(spec)

    try:
        layer.Traverse(Sdf.Path.absoluteRootPath, _collect)
    except Exception as exc:
        print(
            f"{_PRINT_PREFIX}/DUMP layer.Traverse failed: {exc}",
            flush=True,
        )

    dynamic_sdf: List[Tuple[Any, Dict[float, Any]]] = []
    static: List[Tuple[Any, Any]] = []
    empty: List[Any] = []

    for spec in attr_specs:
        ts_info = None
        try:
            if hasattr(spec, "GetInfo"):
                ts_info = spec.GetInfo("timeSamples")
        except Exception:
            ts_info = None
        if ts_info is None and hasattr(layer, "GetField"):
            try:
                ts_info = layer.GetField(spec.path, "timeSamples")
            except Exception:
                ts_info = None
        if isinstance(ts_info, dict) and len(ts_info) > 0:
            dynamic_sdf.append((spec, ts_info))
            continue
        try:
            has_default = (
                bool(spec.HasDefaultValue())
                if hasattr(spec, "HasDefaultValue")
                else False
            )
        except Exception:
            has_default = False
        if has_default:
            try:
                dv = spec.default
            except Exception:
                dv = None
            static.append((spec, dv))
        else:
            empty.append(spec)

    n_total = len(attr_specs)
    n_dyn_sdf = len(dynamic_sdf)
    n_static = len(static)
    n_empty = len(empty)
    print(
        f"{_PRINT_PREFIX}/DUMP Sdf.AttributeSpec total={n_total} "
        f"sdf_dict_timeSamples={n_dyn_sdf} static(default)={n_static} empty={n_empty}",
        flush=True,
    )

    try:
        prim_rows_env = int(os.environ.get("LAM_BAKE_DUMP_TS_PRIM_ROWS", "40"))
    except Exception:
        prim_rows_env = 40
    try:
        attr_lines_env = int(os.environ.get("LAM_BAKE_DUMP_TS_ATTR_LINES", "24"))
    except Exception:
        attr_lines_env = 24
    _log_baked_ts_path_summary(
        dynamic_sdf,
        layer_timecodes=(s_tc, e_tc),
        tps_hint=tps,
        max_prim_rows=max(1, prim_rows_env),
        max_attr_detail_lines=max(1, min(cap, attr_lines_env)),
        max_samples_per_attr=max_samples_per_attr,
    )

    # Sdf dict 기반 dynamic (GetField 로 잡히는 소수) — Usd 경로와 중복 출력될 수 있으나
    # 디버그 가치가 있어 상한만 작게 찍는다.
    cap_sdf = max(1, min(8, cap - shown))
    for i, (spec, ts_info) in enumerate(dynamic_sdf[:cap_sdf]):
        try:
            path = str(spec.path)
        except Exception:
            path = "<?>"
        try:
            tn = str(spec.typeName) if hasattr(spec, "typeName") else "<?>"
        except Exception:
            tn = "<?>"
        n_ts = len(ts_info)
        tcs_sorted = sorted(ts_info.keys())
        head = tcs_sorted[: max(1, int(max_samples_per_attr) - 1)]
        tail = [tcs_sorted[-1]] if tcs_sorted else []
        picks = list(head)
        if tail and tail[0] not in picks:
            picks.append(tail[0])
        sample_lines = [f"@{tc}={_dump_brief_value(ts_info.get(tc))}" for tc in picks]
        print(
            f"{_PRINT_PREFIX}/DUMP  [{i:>4}] DYN_SDF {path} type={tn} "
            f"timeSamples_n={n_ts} samples=[{', '.join(sample_lines)}]",
            flush=True,
        )

    cap_stat = max(1, min(6, cap - shown - cap_sdf))
    for i, (spec, dv) in enumerate(static[:cap_stat]):
        try:
            path = str(spec.path)
        except Exception:
            path = "<?>"
        try:
            tn = str(spec.typeName) if hasattr(spec, "typeName") else "<?>"
        except Exception:
            tn = "<?>"
        print(
            f"{_PRINT_PREFIX}/DUMP  [{i:>4}] STA {path} type={tn} "
            f"default={_dump_brief_value(dv)}",
            flush=True,
        )

    print(f"{_PRINT_PREFIX}/DUMP ===== baked layer dump end =====", flush=True)


def _dump_brief_value(v: Any) -> str:
    """timeSamples / default 값의 짧은 표현. tuple / 큰 array 는 축약."""
    if v is None:
        return "None"
    try:
        # Gf.Vec3d / Gf.Matrix4d 등은 str() 결과가 짧다.
        s = str(v)
    except Exception:
        return "<unprintable>"
    if len(s) > 80:
        return s[:77] + "..."
    return s


def _build_sample_frames(s_frame: float, e_frame: float, stride: int) -> List[float]:
    """stride 간격으로 샘플할 timeCode 목록. 마지막 `e_frame` 은 stride 로 안 걸려도 항상 포함.

    예: 0~110, stride=30 → [0, 30, 60, 90, 110]
    """
    st = max(1, int(stride))
    out: List[float] = []
    cur = float(s_frame)
    while cur <= e_frame + 1e-6:
        out.append(cur)
        cur += float(st)
    if out and out[-1] < e_frame - 1e-6:
        out.append(float(e_frame))
    return out


def _usd_values_equivalent(a: Any, b: Any, eps: float = 1e-5) -> bool:
    """연속 프레임에서 동일한 샘플인지 (sparse capture 용). 부동소수·행렬은 허용 오차."""
    if a is b:
        return True
    try:
        if a == b:
            return True
    except Exception:
        pass
    try:
        from pxr import Gf  # type: ignore

        if isinstance(a, (Gf.Matrix4d, Gf.Matrix4f, Gf.Matrix3d, Gf.Matrix3f)):
            return Gf.IsClose(a, b, eps)  # type: ignore[attr-defined]
        if isinstance(
            a,
            (
                Gf.Vec2d,
                Gf.Vec2f,
                Gf.Vec2h,
                Gf.Vec3d,
                Gf.Vec3f,
                Gf.Vec3h,
                Gf.Vec4d,
                Gf.Vec4f,
            ),
        ):
            return Gf.IsClose(a, b, eps)  # type: ignore[attr-defined]
        if isinstance(a, (Gf.Quatd, Gf.Quatf, Gf.Quath)):
            return Gf.IsClose(a, b, eps)  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        fa = float(a)  # type: ignore[arg-type]
        fb = float(b)  # type: ignore[arg-type]
        return abs(fa - fb) <= eps
    except Exception:
        pass
    try:
        return str(a) == str(b)
    except Exception:
        return False


class BakeResult:
    # NOTE: __slots__ 에 필드를 빼먹으면 __init__ 이 `self.baked_layer = ...` 대입 시
    # AttributeError 가 나고 bake 완료 직후 attach 단계가 전부 스킵된다 (사용자 로그 재현).
    __slots__ = (
        "ok",
        "output_path",
        "error",
        "n_frames",
        "n_target_prims",
        "n_attr_authored",
        "n_attr_pruned_static",
        "elapsed_sec",
        "skipped_existing",
        "frame_stride",
        "n_sparse_skipped_capture",
        "baked_layer",
        "output_mode",
        "effective_inst_prim_path",
    )

    def __init__(
        self,
        *,
        ok: bool,
        output_path: str = "",
        error: str = "",
        n_frames: int = 0,
        n_target_prims: int = 0,
        n_attr_authored: int = 0,
        n_attr_pruned_static: int = 0,
        elapsed_sec: float = 0.0,
        skipped_existing: bool = False,
        frame_stride: int = 1,
        n_sparse_skipped_capture: int = 0,
        baked_layer: Optional[Any] = None,
        output_mode: str = "file",
        effective_inst_prim_path: str = "",
    ) -> None:
        self.ok = ok
        self.output_path = output_path
        self.error = error
        self.n_frames = n_frames
        self.n_target_prims = n_target_prims
        self.n_attr_authored = n_attr_authored
        self.n_attr_pruned_static = n_attr_pruned_static
        self.elapsed_sec = elapsed_sec
        self.skipped_existing = skipped_existing
        self.frame_stride = frame_stride
        self.n_sparse_skipped_capture = n_sparse_skipped_capture
        # X3 (in-memory bake) — output_mode == "memory" 인 경우 disk 출력은 생략하고
        # baked_layer 를 통해 anonymous Sdf.Layer 를 호출자에게 반환한다.
        # `output_mode == "file"` 인 경우 baked_layer 는 None.
        self.baked_layer = baked_layer
        self.output_mode = output_mode
        # 2026-05-14 — drag&drop 결과 자동 인식 결과. 호출자는 이 값을 attach 시 mirror
        # prim path 로 사용해야 master 의 정확한 위치에 write 된다. 정상 add_usd 흐름이면
        # 사용자 inst_prim_path 와 동일.
        self.effective_inst_prim_path = effective_inst_prim_path

    def __repr__(self) -> str:  # pragma: no cover
        mode_tag = f" mode={self.output_mode}"
        if self.output_mode == "memory":
            ident = ""
            try:
                if self.baked_layer is not None:
                    ident = self.baked_layer.identifier
            except Exception:
                ident = "<unavailable>"
            mode_tag += f" mem_layer={ident!r}"
        return (
            f"BakeResult(ok={self.ok} out={self.output_path!r} err={self.error!r} "
            f"frames={self.n_frames} prims={self.n_target_prims} "
            f"attrs={self.n_attr_authored} pruned={self.n_attr_pruned_static} "
            f"skipped={self.skipped_existing} stride={self.frame_stride} "
            f"sparse_skip={self.n_sparse_skipped_capture} elapsed={self.elapsed_sec:.3f}s"
            f"{mode_tag})"
        )


def _read_asset_root_path(asset_path: str) -> str:
    """자산 USD 의 defaultPrim path 를 반환. 없으면 첫 root prim. 실패 시 빈 문자열."""
    try:
        from pxr import Usd  # type: ignore

        st = Usd.Stage.Open(asset_path)
        if st is None:
            return ""
        try:
            dp = st.GetDefaultPrim()
            if dp and dp.IsValid():
                return str(dp.GetPath())
        except Exception:
            pass
        try:
            pseudo = st.GetPseudoRoot()
            for ch in pseudo.GetAllChildren():
                return str(ch.GetPath())
        except Exception:
            pass
    except Exception as exc:
        print(f"{_PRINT_PREFIX} read_asset_root_path failed: {exc}", flush=True)
    return ""


def _read_asset_up_axis(asset_path: str) -> str:
    """자산 USD 의 stage upAxis 메타. 'Y' / 'Z'. 실패 시 'Y' (USD 기본값)."""
    try:
        from pxr import Usd, UsdGeom  # type: ignore

        st = Usd.Stage.Open(asset_path)
        if st is None:
            return "Y"
        try:
            ax = UsdGeom.GetStageUpAxis(st)
            return str(ax) if ax else "Y"
        except Exception:
            return "Y"
    except Exception:
        return "Y"


def _read_asset_time_range_tc(asset_path: str) -> Tuple[float, float]:
    """자산 USD 의 startTimeCode/endTimeCode. 실패 시 (0, 0)."""
    try:
        from pxr import Usd  # type: ignore

        st = Usd.Stage.Open(asset_path)
        if st is None:
            return (0.0, 0.0)
        try:
            return (float(st.GetStartTimeCode()), float(st.GetEndTimeCode()))
        except Exception:
            return (0.0, 0.0)
    except Exception:
        return (0.0, 0.0)


def _map_inst_path_to_asset_root(
    inst_root: str, asset_root: str, prim_path: str
) -> str:
    """master 인스턴스 prim 산하 path 를 asset root 산하 path 로 변환.

    예) inst_root=/World/aaa, asset_root=/Root, prim_path=/World/aaa/N_07_Laser_Cutting/foo
        → /Root/N_07_Laser_Cutting/foo
    """
    ir = (inst_root or "/").rstrip("/")
    pp = (prim_path or "/").rstrip("/")
    if not ir or not pp.startswith(ir):
        return prim_path
    suf = pp[len(ir):]
    if not suf:
        return asset_root
    if not suf.startswith("/"):
        suf = "/" + suf
    return (asset_root.rstrip("/") + suf).replace("//", "/")


def read_bake_speed_env() -> Tuple[int, bool]:
    """환경 변수로 bake 가속 옵션을 읽는다.

    품질 최우선 정책 (2026-05-11): **둘 다 기본은 무손실**.

    - ``LAM_BAKE_FRAME_STRIDE``: 정수 ≥ 1. 기본 ``1`` (매 프레임 스크럽).
      2 이상을 지정하면 격프레임으로 줄어 bake 시간이 거의 선형으로 단축되지만, 중간
      프레임 변환은 USD 가 보간하므로 **품질 손실이 발생**한다. 긴급 시간 단축이 아니라면
      기본값 1 을 유지할 것.
    - ``LAM_BAKE_SPARSE_SAMPLES``: ``0`` (기본) / ``1``. 기본 ``0`` 으로 무손실 동작.
      ``1`` 이면 연속 프레임의 값이 같으면 가운데 샘플 생략 → "hold-then-jump" 패턴에서
      미세 손실이 발생할 수 있다.

    Returns:
        ``(frame_stride, sparse_time_samples)``
    """
    raw = os.environ.get("LAM_BAKE_FRAME_STRIDE", "1")
    try:
        stride = max(1, int((raw or "1").strip()))
    except ValueError:
        stride = 1
    sparse_s = os.environ.get("LAM_BAKE_SPARSE_SAMPLES", "0").strip().lower()
    sparse = sparse_s in ("1", "true", "yes", "on")
    return stride, sparse


async def bake_prim_to_timesamples_async(
    master_stage: Any,
    inst_prim_path: str,
    asset_path: str,
    *,
    output_path: str = "",
    start_frame: float = -1.0,
    end_frame: float = -1.0,
    fps: float = TBS_FIXED_FPS,
    frame_stride: int = 1,
    sparse_time_samples: bool = False,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
    output_mode: str = "memory",
    log_baked_dump: bool = True,
    log_baked_dump_max_attrs: int = 40,
    log_baked_dump_max_samples_per_attr: int = 4,
) -> BakeResult:
    """master(default) context 의 timeline 을 scrub 하며 인스턴스 산하의 xform 결과를
    capture 하여 timeSamples 로 변환.

    Args:
        master_stage: master 의 `pxr.Usd.Stage` (default context 에 attach 됨).
        inst_prim_path: master 안의 인스턴스 prim path (예: `/World/aaa`).
        asset_path: 원본 자산 USD 의 절대 경로 (asset_root 결정 / `output_mode='file'` 의 디폴트 출력 경로).
        output_path: `output_mode='file'` 일 때만 사용. 빈 문자열이면 `<asset>_baked.usd`.
        start_frame, end_frame: capture 범위 (frame, fps 기준). -1 이면 자산 메타 사용.
        fps: capture 율. LAM 정책 30.
        frame_stride: **샘플 간격 (프레임)**. 1 이면 매 프레임, 2 이면 격프레임만 스크럽.
        sparse_time_samples: True 이면 연속 프레임에서 값이 동일한 attribute 샘플은
            기록하지 않는다 (USD 가 키프레임 사이를 보간).
        progress_cb: `(cur, total, msg)` 콜백.
        cancel_cb: 매 frame 진입 시 True 면 중단.
        output_mode: **`"memory"` (기본, X3 정책) 또는 `"file"`**.
            - `"memory"`: 디스크에 `*_baked.usd` 를 생성하지 않고 in-memory anonymous
              `Sdf.Layer` 를 `BakeResult.baked_layer` 로 반환. 호출자는 이를 instance
              runtime 의 offscreen_stage root layer 로 사용할 수 있다. Kit 종료 시
              layer 는 메모리에서 소멸 (휘발성).
            - `"file"`: 기존 동작 — `output_path` 위치에 `*_baked.usd` 를 디스크 저장.
        log_baked_dump: True 면 bake 종료 시 baked layer 의 prim / attr / timeSamples
            형식을 콘솔에 dump (사용자 요청 — 변환된 데이터 형식 확인용).
        log_baked_dump_max_attrs: dump 출력의 attribute 최대 개수 (성능 보호).
        log_baked_dump_max_samples_per_attr: 각 attribute 당 출력할 timeSamples 개수 (앞/뒤 일부만).

    환경 변수: `LAM_BAKE_FRAME_STRIDE`, `LAM_BAKE_SPARSE_SAMPLES` 는 호출부에서 읽어 kwargs 로 넘긴다.

    Returns:
        `BakeResult` — `output_mode='memory'` 인 경우 `baked_layer` 필드가 채워짐.
    """
    t0 = time.perf_counter()

    def _emit(cur: int, total: int, msg: str = "") -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(cur, total, msg)
        except Exception:
            pass

    if master_stage is None:
        return BakeResult(ok=False, error="master_stage is None")
    if not inst_prim_path or not inst_prim_path.startswith("/"):
        return BakeResult(ok=False, error=f"invalid inst_prim_path: {inst_prim_path!r}")
    if not asset_path or not os.path.isfile(asset_path):
        return BakeResult(ok=False, error=f"asset not found: {asset_path}")

    # output_path 는 `output_mode='file'` 일 때만 의미를 가짐. memory 모드일 때는
    # 빈 문자열로 두어도 무방하지만, 안전을 위해 호출자가 명시 전달했을 때만 보존.
    if (output_mode or "memory").lower().strip() == "file":
        output_path = (output_path or make_default_baked_path(asset_path)).strip()
        if not output_path:
            output_path = make_default_baked_path(asset_path)

    try:
        from pxr import Sdf, Usd, UsdGeom  # type: ignore
    except Exception as exc:
        return BakeResult(ok=False, error=f"pxr import failed: {exc}")

    try:
        import omni.kit.app  # type: ignore
        import omni.timeline  # type: ignore
    except Exception as exc:
        return BakeResult(ok=False, error=f"omni import failed: {exc}")

    # 인스턴스 prim 확인.
    inst_prim = master_stage.GetPrimAtPath(inst_prim_path)
    if not inst_prim or not inst_prim.IsValid():
        return BakeResult(
            ok=False,
            error=f"inst prim not valid in master: {inst_prim_path}",
        )

    asset_root = _read_asset_root_path(asset_path)
    if not asset_root:
        asset_root = "/Root"

    # frame 범위 결정 — 인자 > 자산 메타.
    meta_s, meta_e = _read_asset_time_range_tc(asset_path)
    s_frame = float(start_frame) if start_frame >= 0 else meta_s
    e_frame = float(end_frame) if end_frame >= 0 else meta_e
    if e_frame <= s_frame:
        e_frame = max(s_frame + 1.0, 110.0)
    fps = float(fps if fps > 0 else TBS_FIXED_FPS)
    stride_i = max(1, int(frame_stride))

    sample_frames = _build_sample_frames(s_frame, e_frame, stride_i)
    n_sample_frames = len(sample_frames)

    inst_prim_path_str = str(inst_prim.GetPath())

    # W3 — 자산 기반 자동 탐지 (사용자 요구 2026-05-11 후반):
    # 자산 USD 안에서 timeSamples 가 박힌 (sub_path, attr_name) 페어를 미리 수집해
    # `_collect_targets` 의 capture 필터로 사용한다. SkelAnim / Mesh-deform / 사용자 정의
    # attribute 도 자동 포함되므로 "capture targets empty" 실패를 회피.
    #
    # 결과가 비어 있으면 (전형적인 OmniGraph 자산: 자산 안에 timeSamples 0 이고 런타임 push)
    # 기존 `_is_capture_target_attr_name` 필터로 fallback (xformOp:* / visibility 만).
    try:
        _discovered_pairs = discover_animated_attrs(asset_path)
    except Exception as _exc_disc:
        print(f"{_PRINT_PREFIX} discover_animated_attrs failed: {_exc_disc} (fallback to xformOp filter)", flush=True)
        _discovered_pairs = []

    # sub_path → set(attr_name) 룩업 dict. xformOpOrder 는 xformOp:* 가 있는 prim 마다
    # 자동 추가 (default 1 회 author 필요).
    _discovered_map: Dict[str, set] = {}
    for sub_path, attr_name in _discovered_pairs:
        bucket = _discovered_map.setdefault(sub_path, set())
        bucket.add(attr_name)
    for sub_path, names in list(_discovered_map.items()):
        if any(n.startswith("xformOp:") for n in names):
            names.add("xformOpOrder")

    use_discovery = len(_discovered_map) > 0

    # 2026-05-14 — drag&drop 자산 루트 (자산 default prim 이름과 동일한 첫 prim).
    # bake 의 sub_path 매칭 + baked layer author + UI 의 mirror_root_prim_path 설정에
    # 공통 사용. discovery 모드 여부와 무관하게 시도 (fallback bake 도 author 매핑에 필요).
    drag_drop_prefix = ""
    try:
        from .tbs_extract_from_master import discover_drag_drop_asset_root_prim

        drag_drop_prefix = discover_drag_drop_asset_root_prim(
            master_stage, inst_prim_path_str, asset_path
        ) or ""
    except Exception as _dd_exc:
        drag_drop_prefix = ""
        print(
            f"{_PRINT_PREFIX} discover_drag_drop_asset_root_prim failed: {_dd_exc}",
            flush=True,
        )
    if drag_drop_prefix and drag_drop_prefix != inst_prim_path_str:
        print(
            f"{_PRINT_PREFIX} discovery — drag&drop asset root: {drag_drop_prefix}",
            flush=True,
        )

    print(
        f"{_PRINT_PREFIX} discovery — animated_prims={len(_discovered_map)} "
        f"animated_attrs={sum(len(s) for s in _discovered_map.values())} "
        f"mode={'auto' if use_discovery else 'fallback(xformOp+visibility)'} "
        f"prefix={drag_drop_prefix or inst_prim_path_str}",
        flush=True,
    )

    # 캡처 타깃 — 각 항목에 prim/path/attr 리스트와 함께 attr 메타데이터(이름, typeName,
    # xformOpOrder 여부)를 사전 캐시한다. 매 프레임마다 `GetName()/GetTypeName()` 을
    # 다시 부르지 않도록 하기 위한 무손실 최적화. (수만~수십만 호출 절감)
    def _collect_targets() -> List[Tuple[Any, str, List[Tuple[Any, str, Any, bool]]]]:
        out: List[Tuple[Any, str, List[Tuple[Any, str, Any, bool]]]] = []
        for prim in Usd.PrimRange(inst_prim):
            try:
                pp = str(prim.GetPath())
            except Exception:
                continue
            if pp == inst_prim_path_str:
                # D7 — inst_prim 자체는 capture 제외 (add_usd 의 upAxisFix 이중 적용 방지).
                continue

            # auto-discovery 모드: 자산에서 발견된 (sub_path, attr_name) 만 capture.
            if use_discovery:
                # master prim path → 자산 default-prim-relative sub_path 변환.
                #   /World/aaa/Geom/Mesh1 → "Geom/Mesh1"  (정상 add_usd 흐름)
                #   /World/aaa/test1/N_07_Laser_Cutting/Geom/Mesh1 → "Geom/Mesh1"  (drag&drop)
                # 후자의 경우 위 `drag_drop_prefix` 로 자동 인식된 effective prefix 를 사용.
                effective_prefix = drag_drop_prefix or inst_prim_path_str
                if pp.startswith(effective_prefix + "/"):
                    sub_path = pp[len(effective_prefix) + 1:]
                elif pp == effective_prefix:
                    sub_path = ""
                elif pp.startswith(inst_prim_path_str + "/"):
                    sub_path = pp[len(inst_prim_path_str) + 1:]
                else:
                    sub_path = pp.lstrip("/")
                expected = _discovered_map.get(sub_path)
                if not expected:
                    continue
                try:
                    rec: List[Tuple[Any, str, Any, bool]] = []
                    for a in prim.GetAttributes():
                        try:
                            nm = a.GetName()
                        except Exception:
                            continue
                        if nm not in expected:
                            continue
                        try:
                            tn = a.GetTypeName()
                        except Exception:
                            tn = None
                        rec.append((a, nm, tn, nm == "xformOpOrder"))
                    if rec:
                        out.append((prim, pp, rec))
                except Exception:
                    continue
                continue

            # fallback 모드: 기존 xformOp:* / visibility 필터 (OmniGraph 자산 대비).
            try:
                rec = []
                for a in prim.GetAttributes():
                    try:
                        nm = a.GetName()
                    except Exception:
                        continue
                    if not _is_capture_target_attr_name(nm):
                        continue
                    try:
                        tn = a.GetTypeName()
                    except Exception:
                        tn = None
                    rec.append((a, nm, tn, nm == "xformOpOrder"))
                if rec:
                    out.append((prim, pp, rec))
            except Exception:
                continue
        return out

    targets = _collect_targets()

    # OmniGraph 류 prim 들 (master 안 인스턴스 산하, inst_prim 자신은 제외) — 결과
    # baked.usd 에서 비활성화 대상.
    auto_deact_in_asset: List[str] = []
    for prim in Usd.PrimRange(inst_prim):
        try:
            pp = str(prim.GetPath())
            if pp == inst_prim_path_str:
                continue
        except Exception:
            continue
        try:
            tn = str(prim.GetTypeName())
        except Exception:
            continue
        if tn in {"OmniGraph", "PushGraph", "OmniGraphFunction"} or tn.startswith("OG"):
            try:
                # drag&drop prefix 가 인식된 경우 master prim 의 effective prefix 를 자르고
                # 자산 default prim 기준 path 로 변환해야 baked layer 와 일치한다.
                _map_inst_root = drag_drop_prefix or inst_prim_path
                ap = _map_inst_path_to_asset_root(_map_inst_root, asset_root, pp)
                if ap and ap not in auto_deact_in_asset:
                    auto_deact_in_asset.append(ap)
            except Exception:
                continue

    # ─── timeline scrub + capture ───────────────────────────────────────────
    try:
        timeline = omni.timeline.get_timeline_interface()
    except Exception as exc:
        return BakeResult(ok=False, error=f"timeline iface failed: {exc}")

    app = omni.kit.app.get_app()

    # Kit 메인 루프 rate-limit 일시 해제 — 무손실 속도 가속의 핵심.
    # `next_update_async()` 는 보통 60(또는 30) FPS 캡에 묶여 한 틱 = 16~33 ms 를 기다린다.
    # 이 캡을 bake 동안만 풀어 두면 OmniGraph 평가는 그대로 일어나면서 틱 간 대기 시간만
    # 크게 줄어든다 (자산·하드웨어에 따라 3~10 배 단축 가능). bake 종료 시 원상 복구.
    _settings = None
    _saved_rate_limit_enabled: Any = None
    _saved_rate_limit_freq: Any = None
    try:
        import carb.settings  # type: ignore

        _settings = carb.settings.get_settings()
        try:
            _saved_rate_limit_enabled = _settings.get("/app/runLoops/main/rateLimitEnabled")
        except Exception:
            _saved_rate_limit_enabled = None
        try:
            _saved_rate_limit_freq = _settings.get("/app/runLoops/main/rateLimitFrequency")
        except Exception:
            _saved_rate_limit_freq = None
        try:
            _settings.set("/app/runLoops/main/rateLimitEnabled", False)
        except Exception:
            pass
    except Exception:
        _settings = None

    def _restore_run_loop() -> None:
        if _settings is None:
            return
        try:
            if _saved_rate_limit_enabled is not None:
                _settings.set("/app/runLoops/main/rateLimitEnabled", bool(_saved_rate_limit_enabled))
            else:
                _settings.set("/app/runLoops/main/rateLimitEnabled", True)
        except Exception:
            pass
        try:
            if _saved_rate_limit_freq is not None:
                _settings.set("/app/runLoops/main/rateLimitFrequency", _saved_rate_limit_freq)
        except Exception:
            pass

    # 사용자의 timeline 상태 보존.
    saved_time = 0.0
    was_playing = False
    try:
        saved_time = float(timeline.get_current_time())  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        was_playing = bool(timeline.is_playing())  # type: ignore[attr-defined]
    except Exception:
        pass
    if was_playing:
        try:
            timeline.pause()  # type: ignore[attr-defined]
        except Exception:
            pass

    # tps / framerate 강제 (TBS_FIXED_FPS 정책).
    for setter_name, value in (
        ("set_time_codes_per_second", float(fps)),
        ("set_target_framerate", float(fps)),
    ):
        fn = getattr(timeline, setter_name, None)
        if callable(fn):
            try:
                fn(value)
            except Exception:
                pass
    try:
        master_stage.SetTimeCodesPerSecond(float(fps))
        master_stage.SetFramesPerSecond(float(fps))
    except Exception:
        pass

    # capture 저장: { inst_prim_sub_path: { attr_name: { tc: value } } }
    capture: Dict[str, Dict[str, Dict[float, Any]]] = {}

    # warm-up tick — OmniGraph 가 한 번 평가되어 attribute schema 가 채워지도록 짧게 진행.
    # PushGraph 는 stage 시각 변경 즉시 평가되므로 2 tick 이면 schema 가 안정된다.
    try:
        timeline.set_current_time(float(s_frame) / float(fps))  # type: ignore[attr-defined]
    except Exception:
        pass
    for _ in range(2):
        try:
            await app.next_update_async()
        except Exception:
            await asyncio.sleep(0)
    # warm-up 이후 다시 targets 재수집. 첫 push 로 새 attribute 가 생긴 경우 catch.
    targets = _collect_targets()

    if not targets:
        try:
            timeline.set_current_time(saved_time)  # type: ignore[attr-defined]
        except Exception:
            pass
        _restore_run_loop()
        if use_discovery:
            err_msg = (
                f"capture targets empty under {inst_prim_path} — auto-discovery mode 였지만 "
                f"자산의 sub_path 들이 master 측 inst prim 산하와 매칭되지 않았습니다. "
                f"(자산 default_prim 경로 점검 필요. animated_prims={len(_discovered_map)})"
            )
        else:
            err_msg = (
                f"capture targets empty under {inst_prim_path} — fallback 모드(xformOp/visibility) "
                f"에서도 attribute 가 보이지 않습니다. 자산에 native timeSamples 가 없고 "
                f"OmniGraph 도 평가되지 않은 상태일 가능성이 큽니다. (master timeline 활성 상태 점검 필요)"
            )
        return BakeResult(ok=False, error=err_msg)

    last_by_key: Dict[Tuple[str, str], Any] = {}
    n_sparse_skipped = 0
    t_capture_start = time.perf_counter()
    t_tick_total = 0.0
    t_read_total = 0.0

    # ─── master 안의 OmniGraph 임시 활성화 (사용자 보고 2026-05-12 회귀 fix) ─────
    #
    # TIMESAMPLES_REPLAY 용으로 evaluator 가 instance sublayer 에 `over { active=false }`
    # 를 박아둔 상태에서 [Bake] 가 실행되면, master timeline 을 scrub 해도 OmniGraph 가
    # 평가되지 않아 모든 frame 동일 값 → static-prune 되어 baked layer 에 dynamic
    # timeSamples 가 0 개로 남는다 (사용자 회귀 로그 재현).
    #
    # 해결: scrub 시작 직전 master session layer 에 동일 prim path 의 `active=True`
    # opinion 을 author 한다(session 의 직접 spec 은 session 의 sublayer 의 spec 보다
    # stronger 라 instance sublayer 의 active=false 를 마스킹). capture loop 가 끝나면
    # `ClearActive()` 로 spec 의 active opinion 만 제거한다(session 은 어차피 휘발 +
    # 사용자 master USD 무변경 + evaluator 의 instance sublayer 도 그대로 유지 →
    # 다음 update tick 에서 TIMESAMPLES_REPLAY 모드 자동 복귀).
    bake_omnigraph_overrides: List[str] = []
    bake_omnigraph_typenames = (
        "OmniGraph",
        "OmniGraphNode",
        "PushGraph",
        "PushGraphNode",
    )
    try:
        bake_session_layer = master_stage.GetSessionLayer()
    except Exception:
        bake_session_layer = None
    if bake_session_layer is not None:
        try:
            ip = master_stage.GetPrimAtPath(inst_prim_path)
            if ip and ip.IsValid():
                with Usd.EditContext(
                    master_stage, Usd.EditTarget(bake_session_layer)
                ):
                    for p in Usd.PrimRange(ip):
                        try:
                            tn = str(p.GetTypeName() or "")
                        except Exception:
                            continue
                        if tn not in bake_omnigraph_typenames:
                            continue
                        try:
                            if p.IsActive():
                                continue
                        except Exception:
                            pass
                        try:
                            p.SetActive(True)
                            bake_omnigraph_overrides.append(str(p.GetPath()))
                        except Exception as exc_sa:
                            print(
                                f"{_PRINT_PREFIX} bake omnigraph temp-activate FAIL "
                                f"prim={p.GetPath()}: {exc_sa}",
                                flush=True,
                            )
        except Exception as exc_oa:
            print(
                f"{_PRINT_PREFIX} bake omnigraph temp-activate scan exc: {exc_oa}",
                flush=True,
            )
    if bake_omnigraph_overrides:
        print(
            f"{_PRINT_PREFIX} bake omnigraph temp-activated count={len(bake_omnigraph_overrides)} "
            f"paths={bake_omnigraph_overrides[:8]} "
            f"(session opinion — scrub 평가용, capture loop 후 원복)",
            flush=True,
        )

    print(
        f"{_PRINT_PREFIX} scrub start prim={inst_prim_path} frame_range=[{s_frame}, {e_frame}] "
        f"sample_count={n_sample_frames} stride={stride_i} sparse={sparse_time_samples} "
        f"fps={fps} targets={len(targets)} asset_root={asset_root} "
        f"saved_time={saved_time:.3f}s playing={was_playing}",
        flush=True,
    )

    def _revert_bake_omnigraph_overrides() -> None:
        """OmniGraph temp-activate 를 best-effort 로 ClearActive() 한다."""
        if not bake_omnigraph_overrides or bake_session_layer is None:
            return
        try:
            with Usd.EditContext(master_stage, Usd.EditTarget(bake_session_layer)):
                for pp in bake_omnigraph_overrides:
                    try:
                        p = master_stage.GetPrimAtPath(pp)
                        if p and p.IsValid():
                            p.ClearActive()
                    except Exception:
                        continue
        except Exception:
            pass

    for f_idx, cur_frame in enumerate(sample_frames):
        tc = float(cur_frame)
        if cancel_cb is not None:
            try:
                if cancel_cb():
                    try:
                        timeline.set_current_time(saved_time)  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    _revert_bake_omnigraph_overrides()
                    print(f"{_PRINT_PREFIX} cancelled at frame={tc}", flush=True)
                    _restore_run_loop()
                    return BakeResult(
                        ok=False,
                        error=f"cancelled at frame={tc}",
                        n_frames=f_idx,
                        n_target_prims=len(targets),
                        elapsed_sec=time.perf_counter() - t0,
                        frame_stride=stride_i,
                        n_sparse_skipped_capture=n_sparse_skipped,
                    )
            except Exception:
                pass

        sec = tc / float(fps)
        try:
            timeline.set_current_time(sec)  # type: ignore[attr-defined]
        except Exception:
            try:
                master_stage.SetCurrentTimeCode(tc)
            except Exception:
                pass

        _t_tick_a = time.perf_counter()
        try:
            await app.next_update_async()
        except Exception:
            await asyncio.sleep(0)
        t_tick_total += time.perf_counter() - _t_tick_a

        _t_read_a = time.perf_counter()
        tc_code = Usd.TimeCode(tc)
        for _prim, pp, rec in targets:
            pdic = capture.setdefault(pp, {})
            for a, name, _tn, is_order in rec:
                val = None
                try:
                    val = a.Get(tc_code)
                except Exception:
                    val = None
                if val is None:
                    try:
                        val = a.Get(Usd.TimeCode.Default())
                    except Exception:
                        val = None
                if val is None:
                    continue
                if is_order:
                    pdic.setdefault(name, {})[float("nan")] = val
                    continue
                if sparse_time_samples:
                    key = (pp, name)
                    prev = last_by_key.get(key, _MISSING)
                    if prev is not _MISSING and _usd_values_equivalent(val, prev):
                        n_sparse_skipped += 1
                        continue
                    last_by_key[key] = val
                pdic.setdefault(name, {})[tc] = val

        t_read_total += time.perf_counter() - _t_read_a

        if f_idx % 5 == 0 or f_idx == n_sample_frames - 1:
            _emit(f_idx, n_sample_frames, f"capture frame={tc:.1f}")

    t_capture_elapsed = time.perf_counter() - t_capture_start

    # OmniGraph 임시 활성화 원복 — session 에 박았던 active opinion 만 ClearActive() 로 제거.
    # (instance sublayer 의 `over { active = false }` 는 그대로 살아있어, 다음 update
    # tick 에서 evaluator 의 TIMESAMPLES_REPLAY 모드가 자동 복귀한다.)
    if bake_omnigraph_overrides:
        _revert_bake_omnigraph_overrides()
        print(
            f"{_PRINT_PREFIX} bake omnigraph temp-activation reverted "
            f"count={len(bake_omnigraph_overrides)} (session opinion cleared)",
            flush=True,
        )

    # timeline 복원 + 메인 루프 rate-limit 복원.
    try:
        timeline.set_current_time(saved_time)  # type: ignore[attr-defined]
    except Exception:
        pass
    _restore_run_loop()

    _emit(n_sample_frames, n_sample_frames, "frames captured, writing baked.usd")
    # ─── baked.usd 작성 ───────────────────────────────────────────────────
    try:
        out_layer = Sdf.Layer.CreateAnonymous(".usda")
    except Exception as exc:
        return BakeResult(ok=False, error=f"Sdf.Layer.CreateAnonymous failed: {exc}")

    out_stage = Usd.Stage.Open(out_layer)
    try:
        out_stage.SetStartTimeCode(s_frame)
        out_stage.SetEndTimeCode(e_frame)
        out_stage.SetTimeCodesPerSecond(float(fps))
        out_stage.SetFramesPerSecond(float(fps))
    except Exception:
        pass
    # baked.usd 의 stage upAxis 는 원본 자산과 동일하게 박는다. 그래야 baked.usd 를 다시
    # add_usd 할 때 master 의 upAxis 와 비교한 add_usd 의 RotateX 보정이 원본 자산을 직접
    # 로드할 때와 동일하게 *한 번만* 적용된다. (자산이 Y-up 이라면 baked.usd 도 Y-up.)
    try:
        src_axis = _read_asset_up_axis(asset_path)
        UsdGeom.SetStageUpAxis(out_stage, src_axis or "Y")
    except Exception as exc:
        print(
            f"{_PRINT_PREFIX} SetStageUpAxis failed: {exc} (계속 진행 — 기본 Y 로 author 됨)",
            flush=True,
        )

    # 원본 자산을 reference 로 박는다 — 메시/머티리얼/계층 그대로 합성.
    try:
        ref_path_for_layer = os.path.relpath(
            os.path.abspath(asset_path),
            start=os.path.dirname(os.path.abspath(output_path)),
        ).replace("\\", "/")
    except Exception:
        ref_path_for_layer = asset_path.replace("\\", "/")

    try:
        root_prim = out_stage.OverridePrim(asset_root)
        root_prim.GetReferences().AddReference(ref_path_for_layer)
        out_stage.SetDefaultPrim(root_prim)
    except Exception as exc:
        return BakeResult(ok=False, error=f"root over+ref failed: {exc}")

    # 캐시 타깃에서 (pp, attr_name) → typeName 매핑을 구성하여 author 단계에서 다시
    # GetAttribute()/GetTypeName() 을 부르지 않도록 한다. (수만 호출 절감)
    typename_lookup: Dict[Tuple[str, str], Any] = {}
    for _prim, _pp, _rec in targets:
        for _a, _nm, _tn, _is_ord in _rec:
            if _tn is not None:
                typename_lookup[(_pp, _nm)] = _tn

    # capture 결과를 asset_root 산하 path 로 변환해 over + attribute 박음.
    n_attr_authored = 0
    n_attr_pruned_static = 0

    def _values_all_equal(samples_dict: Dict[float, Any]) -> bool:
        """timeSamples 값이 모두 같으면 True. == 비교가 가능한 USD value 들 가정.

        부동소수 매트릭스/벡터의 경우 PushGraph 가 매 frame 동일한 byte-pattern 값을
        push 한다면 정확 == 가 성립한다. 미세 차이가 있는 경우는 timeSamples 로 박는
        쪽이 안전하므로 False 반환.
        """
        if not samples_dict:
            return True
        it = iter(samples_dict.values())
        try:
            first = next(it)
        except StopIteration:
            return True
        for v in it:
            try:
                if v != first:
                    return False
            except Exception:
                return False
        return True

    # 주의: `Sdf.ChangeBlock` 으로 감싸면 안 된다. 블록 내부에서는 막 만든 OverridePrim 의
    # `IsValid()` 가 False 를 반환 → 모든 prim 이 continue 로 빠져 0 attrs 가 된다.
    # (2026-05-11 사용자 보고로 확인.) 알림 비용은 다음 방식으로 최소화한다:
    #  - typeName 사전 캐시 → master_stage 재조회 제거
    #  - 정적 pruning → default 1 회 author 로 timeSamples 수십~수백 회 author 축소
    #
    # 2026-05-14 — drag&drop prefix 가 인식되어 있으면, master 측 prim path 를 자산
    # default prim 기준으로 자를 때 effective prefix 를 사용한다 (그래야 baked layer
    # 의 prim path 가 `/Root/Geom/Mesh1` 처럼 자산 트리와 1:1 일치).
    effective_inst_for_map = drag_drop_prefix or inst_prim_path
    t_author_start = time.perf_counter()
    for inst_pp, adict in capture.items():
        asset_pp = _map_inst_path_to_asset_root(effective_inst_for_map, asset_root, inst_pp)
        if not asset_pp:
            continue
        try:
            op_prim = out_stage.OverridePrim(asset_pp)
        except Exception:
            continue
        if not op_prim or not op_prim.IsValid():
            continue

        for name, samples in adict.items():
            if not samples:
                continue
            type_name = typename_lookup.get((inst_pp, name))
            if type_name is None:
                try:
                    src_prim = master_stage.GetPrimAtPath(inst_pp)
                    src_attr = src_prim.GetAttribute(name) if src_prim else None
                    if src_attr and src_attr.IsValid():
                        type_name = src_attr.GetTypeName()
                except Exception:
                    type_name = None
            if type_name is None:
                continue
            try:
                dst_attr = op_prim.CreateAttribute(name, type_name, custom=False)
            except Exception:
                continue
            if not dst_attr:
                continue

            try:
                if name == "xformOpOrder":
                    any_val = next(iter(samples.values()))
                    dst_attr.Set(any_val)
                    n_attr_authored += 1
                    continue
                clean_samples = {k: v for k, v in samples.items() if k == k}  # NaN drop
                if not clean_samples:
                    continue
                if _values_all_equal(clean_samples):
                    any_val = next(iter(clean_samples.values()))
                    dst_attr.Set(any_val)
                    n_attr_authored += 1
                    n_attr_pruned_static += 1
                else:
                    # 무손실 가속 — Sdf-레벨 batch write 로 timeSamples 를 한 번에 박는다.
                    # `attr.Set(val, tc)` 는 매 호출마다 Usd 합성/노티 비용이 든다. 우리는
                    # 단 1 개의 anonymous layer 에만 author 하므로 합성 단계가 필요 없다.
                    # `attrSpec.SetInfo("timeSamples", {tc: val, ...})` 한 번이면 동일 데이터가
                    # 더 빠르게 기록된다. 실패 시 일반 경로로 폴백.
                    attr_spec = None
                    try:
                        attr_spec = out_layer.GetAttributeAtPath(dst_attr.GetPath())
                    except Exception:
                        attr_spec = None
                    batch_ok = False
                    if attr_spec is not None:
                        try:
                            attr_spec.SetInfo("timeSamples", clean_samples)
                            n_attr_authored += len(clean_samples)
                            batch_ok = True
                        except Exception:
                            batch_ok = False
                    if not batch_ok:
                        for tc in sorted(clean_samples.keys()):
                            dst_attr.Set(clean_samples[tc], Usd.TimeCode(tc))
                            n_attr_authored += 1
            except Exception:
                continue

    for dp in auto_deact_in_asset:
        try:
            ov = out_stage.OverridePrim(dp)
            if ov and ov.IsValid():
                ov.SetActive(False)
        except Exception:
            continue
    t_author_elapsed = time.perf_counter() - t_author_start

    if n_attr_authored == 0:
        return BakeResult(
            ok=False,
            error=(
                "bake captured 0 attrs — OmniGraph 가 평가되지 않았거나 capture 대상이 "
                "비어있습니다. (timeline scrub 중 prim attribute 값 변화가 없음)"
            ),
            n_frames=n_sample_frames,
            n_target_prims=len(capture),
            elapsed_sec=time.perf_counter() - t0,
            frame_stride=stride_i,
            n_sparse_skipped_capture=n_sparse_skipped,
        )

    # 출력 단계 — output_mode 분기.
    mode = (output_mode or "memory").lower().strip()
    if mode not in ("memory", "file"):
        return BakeResult(ok=False, error=f"unknown output_mode={output_mode!r}")
    final_output_path = ""
    final_baked_layer: Optional[Any] = None
    if mode == "file":
        try:
            out_dir = os.path.dirname(os.path.abspath(output_path))
            if out_dir and not os.path.isdir(out_dir):
                os.makedirs(out_dir, exist_ok=True)
            ok_exp = out_layer.Export(output_path)
        except Exception as exc:
            return BakeResult(ok=False, error=f"out_layer.Export() failed: {exc}")
        if not ok_exp:
            return BakeResult(
                ok=False, error=f"out_layer.Export returned False path={output_path}"
            )
        final_output_path = output_path
    else:
        # X3 — disk 출력 생략. anonymous Sdf.Layer 를 그대로 호출자에게 넘긴다.
        # layer 의 identifier 는 디버깅 / 로그에서 식별용으로만 사용.
        try:
            out_layer.SetIdentifier(
                f"anon://__lam_bake__/{os.path.basename(asset_path)}.usda"
            )
        except Exception:
            pass
        final_baked_layer = out_layer

    # 사용자 요청 — bake 한 timeSamples 데이터의 형식을 콘솔에서 확인할 수 있도록 dump.
    if log_baked_dump:
        try:
            _log_baked_layer_dump(
                out_layer,
                max_attrs=int(log_baked_dump_max_attrs),
                max_samples_per_attr=int(log_baked_dump_max_samples_per_attr),
            )
        except Exception as _exc_dump:
            print(
                f"{_PRINT_PREFIX} baked layer dump failed: {_exc_dump}",
                flush=True,
            )

    elapsed = time.perf_counter() - t0
    print(
        f"{_PRINT_PREFIX} done mode={mode} "
        f"out={final_output_path or '(in-memory)'} "
        f"sample_frames={n_sample_frames} "
        f"stride={stride_i} sparse_skipped={n_sparse_skipped} "
        f"prims={len(capture)} attrs_written={n_attr_authored} "
        f"static_pruned={n_attr_pruned_static} "
        f"deactivated={auto_deact_in_asset} "
        f"phase[capture={t_capture_elapsed:.2f}s "
        f"(tick={t_tick_total:.2f}s read={t_read_total:.2f}s) "
        f"author={t_author_elapsed:.2f}s] "
        f"total={elapsed:.3f}s",
        flush=True,
    )
    return BakeResult(
        ok=True,
        output_path=final_output_path,
        n_frames=n_sample_frames,
        n_target_prims=len(capture),
        n_attr_authored=n_attr_authored,
        n_attr_pruned_static=n_attr_pruned_static,
        elapsed_sec=elapsed,
        frame_stride=stride_i,
        n_sparse_skipped_capture=n_sparse_skipped,
        baked_layer=final_baked_layer,
        output_mode=mode,
        effective_inst_prim_path=(drag_drop_prefix or inst_prim_path_str),
    )


__all__ = [
    "BakeResult",
    "bake_prim_to_timesamples_async",
    "make_default_baked_path",
    "read_bake_speed_env",
]
