"""LAM Extract From Master — 사용자가 master stage 의 `/World/<인스턴스>` 하위에 직접
드래그&드랍 한 자산의 timeSamples 를 anonymous Sdf.Layer 로 추출하는 신규 path.

2026-05-13 — 실무 FBX→USD 자산 로드 시 ``omni.timeline`` framerate sync 가 빌드/세션
설정에 따라 silent fail 하여 1000 프레임이 800 부근에서 평가되는 회귀가 보고됐다.
``add_usd`` 의 ``_sync_stage_and_timeline_with_source`` 로 1차 보정하지만 실무
환경에서 그래도 어긋날 경우의 안전판으로:

  1) 사용자가 [Remove] 후 직접 viewport 에 USD 를 drag&drop → ``/World/<인스턴스>``
     하위에 자산이 attach 됨. drag&drop 의 drop handler 가 timeline fps 까지 sync
     시켜주므로 viewport 평가는 정상.
  2) 사용자가 인스턴스 행의 **[Extract]** 버튼을 누름.
  3) 본 모듈이 ``stage.Flatten()`` 으로 모든 composition (reference / payload / variant)
     을 평가한 뒤 ``/World/<인스턴스>`` 트리만 떼어 anonymous layer 의 ``/Root`` 아래로
     복사. 결과 layer 는 우리 ``attach_memory_baked_layer`` 가 가정하는 형식
     (= bake 결과 layer 와 동일 구조 — root prim ``/Root`` 아래 자산 trees) 과 1:1
     호환.
  4) ``RuntimeEvaluator.attach_memory_baked_layer`` 로 attach → TIMESAMPLES_REPLAY 가
     그대로 동작.

본 모듈은 **신규 path** 다. 기존 ``add_usd`` / ``bake`` 흐름은 일절 건드리지 않는다.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Optional


_PRINT_PREFIX = "[LAM/Extract]"

# Open/자동 Extract 배치에서 인스턴스마다 stage.Flatten() 을 반복하지 않기 위한 캐시.
# 동작(CopySpec·스캔·attach)은 동일 — Flatten 결과 layer 만 재사용.
_flatten_cache_depth: int = 0
_flatten_cache_stage_id: Optional[int] = None
_flatten_cache_layer: Optional[Any] = None


def clear_master_flatten_cache() -> None:
    """Flatten 캐시 강제 해제 (Master 재오픈·스테이지 교체 시)."""
    global _flatten_cache_depth, _flatten_cache_stage_id, _flatten_cache_layer
    _flatten_cache_depth = 0
    _flatten_cache_stage_id = None
    _flatten_cache_layer = None


def begin_master_flatten_cache(stage: Any) -> None:
    """동일 stage 에 대한 Extract 배치 시작 — Flatten 은 첫 호출에서만 수행."""
    global _flatten_cache_depth, _flatten_cache_stage_id, _flatten_cache_layer
    if stage is None:
        return
    sid = id(stage)
    if _flatten_cache_depth <= 0:
        _flatten_cache_stage_id = sid
        _flatten_cache_layer = None
        _flatten_cache_depth = 1
        return
    # 중첩 begin: stage 가 바뀌면 캐시 무효 후 새 키
    if _flatten_cache_stage_id != sid:
        _flatten_cache_stage_id = sid
        _flatten_cache_layer = None
    _flatten_cache_depth += 1


def end_master_flatten_cache() -> None:
    """Extract 배치 종료 — depth 0 에서 layer 참조 해제."""
    global _flatten_cache_depth, _flatten_cache_stage_id, _flatten_cache_layer
    if _flatten_cache_depth <= 0:
        return
    _flatten_cache_depth -= 1
    if _flatten_cache_depth <= 0:
        _flatten_cache_depth = 0
        _flatten_cache_stage_id = None
        _flatten_cache_layer = None


@contextmanager
def master_flatten_cache(stage: Any) -> Iterator[None]:
    """``with master_flatten_cache(stage):`` — 배치 Extract 동안 Flatten 1회."""
    begin_master_flatten_cache(stage)
    try:
        yield
    finally:
        end_master_flatten_cache()


def _resolve_flattened_master_layer(stage: Any) -> Any:
    """``stage.Flatten()`` — 캐시 active 이면 동일 stage 결과를 재사용."""
    global _flatten_cache_layer
    if stage is None:
        return None
    sid = id(stage)
    if (
        _flatten_cache_depth > 0
        and _flatten_cache_stage_id == sid
        and _flatten_cache_layer is not None
    ):
        print(
            f"{_PRINT_PREFIX} Flatten cache HIT stage_id={sid}",
            flush=True,
        )
        return _flatten_cache_layer

    flat = stage.Flatten()
    if (
        _flatten_cache_depth > 0
        and _flatten_cache_stage_id == sid
        and flat is not None
    ):
        _flatten_cache_layer = flat
        print(
            f"{_PRINT_PREFIX} Flatten cache STORE stage_id={sid} "
            f"(reuse for remaining extracts in batch)",
            flush=True,
        )
    return flat


@dataclass
class ExtractResult:
    """추출 결과 한 묶음.

    의미 분류:
        - ``ok=True``:  timeSamples 추출 성공. ``layer`` 가 채워져 있으며
          ``attach_memory_baked_layer`` 에 그대로 넘길 수 있다. ``kind`` 는
          ``TIMESAMPLES_XFORM`` / ``TIMESAMPLES_SKEL`` / ``TIMESAMPLES_MESH`` /
          ``MIXED`` 중 하나.
        - ``ok=False`` + ``kind=ASSET_KIND_OMNIGRAPH``: 사용자가 박은 자산이
          OmniGraph 만 갖고 있어 timeSamples 가 없음 → **[Bake] 가 필요**.
          ``layer`` 는 None 또는 검사 용도로만 채워질 수 있다.
        - ``ok=False`` + ``kind=ASSET_KIND_STATIC``: 시간 데이터가 전혀 없음.
        - ``ok=False`` + ``kind=ASSET_KIND_UNKNOWN``: 스캔 단계에서 실패.

    호출자는 ``kind`` 를 ``inst.asset_kind`` 에 박아 UI 의 [Bake] 버튼 분기를
    갱신할 수 있다.
    """

    ok: bool = False
    error: Optional[str] = None
    layer: Optional[Any] = None
    kind: str = "UNKNOWN"
    n_prims: int = 0
    n_attrs_total: int = 0
    n_attrs_with_timesamples: int = 0
    n_xform_op_ts: int = 0
    n_skel_anim_ts: int = 0
    n_mesh_points_ts: int = 0
    n_visibility_ts: int = 0
    n_other_ts: int = 0
    n_omnigraph_prims: int = 0
    tc_min: float = 0.0
    tc_max: float = 0.0
    elapsed_sec: float = 0.0
    root_prim_path: str = ""
    asset_label: str = ""
    discovered_asset_path: str = ""  # drag&drop 으로 박힌 자산의 절대 경로 (추정).

    def to_log_line(self) -> str:
        return (
            f"prim={self.root_prim_path} kind={self.kind} "
            f"prims={self.n_prims} attrs={self.n_attrs_total} "
            f"ts_attrs={self.n_attrs_with_timesamples} "
            f"(xform={self.n_xform_op_ts} skel={self.n_skel_anim_ts} "
            f"mesh={self.n_mesh_points_ts} vis={self.n_visibility_ts} "
            f"other={self.n_other_ts}) "
            f"omnigraph_prims={self.n_omnigraph_prims} "
            f"tc=[{self.tc_min:.3f},{self.tc_max:.3f}] "
            f"elapsed={self.elapsed_sec:.3f}s"
        )


def _scan_layer_stats(layer: Any) -> dict:
    """추출된 anonymous layer 의 통계 + 자산 종류 판정에 필요한 카운트 산출.

    ``lam_asset_diagnostics._classify_attr_name`` / ``_is_omnigraph_prim`` /
    ``_decide_kind`` 와 동일 규칙을 적용해, 본 결과를 그대로 ``AssetDiag`` 형식으로
    변환해 ``_decide_kind`` 에 넘길 수 있다.

    Returns:
        dict — n_prims, n_attrs_total, n_attrs_with_timesamples, n_xform_op_ts,
        n_skel_anim_ts, n_mesh_points_ts, n_visibility_ts, n_other_ts,
        n_omnigraph_prims, tc_min, tc_max.
        layer 가 비었거나 열기 실패 시 모든 카운트 0, tc=(0,0).
    """
    out = {
        "n_prims": 0,
        "n_attrs_total": 0,
        "n_attrs_with_timesamples": 0,
        "n_xform_op_ts": 0,
        "n_skel_anim_ts": 0,
        "n_mesh_points_ts": 0,
        "n_visibility_ts": 0,
        "n_other_ts": 0,
        "n_omnigraph_prims": 0,
        "tc_min": 0.0,
        "tc_max": 0.0,
    }
    try:
        from pxr import Usd  # type: ignore
    except Exception:
        return out
    try:
        s = Usd.Stage.Open(layer)
    except Exception:
        return out
    if s is None:
        return out

    # 자산 진단 모듈의 분류 규칙 재사용 — UI/Bake 분기 와 1:1 동일 규칙 보장.
    try:
        from .lam_asset_diagnostics import _classify_attr_name, _is_omnigraph_prim
    except Exception:
        _classify_attr_name = None  # type: ignore
        _is_omnigraph_prim = None  # type: ignore

    tc_min = float("inf")
    tc_max = float("-inf")
    try:
        for prim in s.Traverse():
            out["n_prims"] += 1
            try:
                type_name = str(prim.GetTypeName())
            except Exception:
                type_name = ""
            if _is_omnigraph_prim is not None and _is_omnigraph_prim(type_name):
                out["n_omnigraph_prims"] += 1
            for attr in prim.GetAttributes():
                out["n_attrs_total"] += 1
                try:
                    n_ts = attr.GetNumTimeSamples()
                except Exception:
                    n_ts = 0
                if n_ts <= 0:
                    continue
                out["n_attrs_with_timesamples"] += 1
                # tc 범위 — 비싼 GetTimeSamples() 호출은 timeSamples 가 있는 attr 에만.
                try:
                    ts = attr.GetTimeSamples()
                    if ts:
                        if ts[0] < tc_min:
                            tc_min = float(ts[0])
                        if ts[-1] > tc_max:
                            tc_max = float(ts[-1])
                except Exception:
                    pass
                try:
                    name = attr.GetName()
                except Exception:
                    name = ""
                if _classify_attr_name is None:
                    continue
                cls = _classify_attr_name(name)
                if cls == "xform":
                    out["n_xform_op_ts"] += 1
                elif cls == "skel":
                    out["n_skel_anim_ts"] += 1
                elif cls == "mesh":
                    out["n_mesh_points_ts"] += 1
                elif cls == "visibility":
                    out["n_visibility_ts"] += 1
                else:
                    out["n_other_ts"] += 1
    except Exception:
        pass
    if tc_min == float("inf"):
        tc_min, tc_max = 0.0, 0.0
    out["tc_min"] = float(tc_min)
    out["tc_max"] = float(tc_max)
    return out


def scan_layer_timesample_stats(layer: Any) -> dict:
    """단일 ``Sdf.Layer`` 에 대해 timeSamples / OmniGraph 카운트 통계를 산출한다.

    ``[Copy TS]`` 가 master ``Flatten`` 이 아닌 **Option E offscreen 루트 레이어**
    (in-memory bake 결과가 들어 있는 곳) 를 덤프할 때 사용한다.
    """
    return _scan_layer_stats(layer)


def normalize_asset_uri_to_path(raw: str) -> str:
    """``file:/`` / ``file:///`` / ``file://`` URI 를 일반 OS 경로로 변환.

    Kit drop handler 가 viewport 에 박은 reference 의 ``assetPath`` 는 종종 URI 형식
    (``file:/C:/...`` 또는 ``file:///C:/...``) 으로 들어온다. 이를 그대로 ``os.path.isfile``
    에 넘기면 항상 False 가 반환되어 [Bake] 의 자산 경로 해석이 실패한다.

    본 헬퍼는 다음을 수행한다:
      - ``file:`` 계열 URI prefix 제거
      - 잔여 leading ``/`` 한 글자 제거 (Windows ``/C:/...`` → ``C:/...``)
      - ``%xx`` URL-encode 디코딩 (best-effort)
      - 백슬래시 → 슬래시 정규화

    URI 가 아니면 입력을 그대로 반환 (백슬래시 정규화는 적용).
    """
    if not raw:
        return ""
    s = str(raw).strip().replace("\\", "/")
    low = s.lower()
    for prefix in ("file:///", "file://", "file:/"):
        if low.startswith(prefix):
            s = s[len(prefix):]
            # Windows 드라이브 letter — leading "/" 제거.
            if len(s) >= 3 and s[0] == "/" and s[2] == ":":
                s = s[1:]
            break
    # 다른 스킴 (omniverse://, http(s)://) 은 그대로 둔다 — bake 입장에서 file 시스템 아님.
    if "%" in s:
        try:
            import urllib.parse  # noqa: WPS433

            s = urllib.parse.unquote(s)
        except Exception:
            pass
    return s


def _canonical_asset_path_key(path: str) -> str:
    """동일 파일 여부 비교용 키 (Windows normcase + abspath)."""
    import os

    p = normalize_asset_uri_to_path(str(path or "").strip())
    if not p:
        return ""
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(p)))
    except Exception:
        return p.replace("\\", "/").lower()


def _resolve_authored_asset_path(
    authored_raw: str,
    *,
    author_layer_identifier: str,
    stage_root_identifier: str,
) -> str:
    """Prim stack spec 에 박힌 ``assetPath`` 를 로컬 절대 경로 문자열로 best-effort 해석."""
    import os

    a = normalize_asset_uri_to_path(str(authored_raw or "").strip())
    if not a:
        return ""

    def _is_abs_win(s: str) -> bool:
        s = s.replace("\\", "/")
        if s.startswith("/"):
            return True
        return len(s) >= 2 and s[1] == ":"

    if os.path.isabs(a) or _is_abs_win(a):
        return a

    bases: list[str] = []
    for lid in (author_layer_identifier, stage_root_identifier):
        lid = (lid or "").strip().replace("\\", "/")
        if not lid or lid.startswith("anon"):
            continue
        d = os.path.dirname(lid)
        if d and d not in bases:
            bases.append(d)
    for d in bases:
        try:
            return os.path.normpath(os.path.join(d, a.replace("\\", "/")))
        except Exception:
            continue
    return a


def discover_drag_drop_asset_root_prim(stage: Any, inst_prim_path: str, asset_path: str) -> str:
    """``inst_prim_path`` 산하에서 bake / Option E mirror 가 붙어야 할 **자산 앵커** prim 경로.

    이전 구현은 자산 USD 의 default prim **이름** 과 동일한 첫 prim 만 찾았다. Kit
    drag&drop 은 ``<파일 stem>`` Xform 아래에 reference 를 박거나, default prim 이름과
    다른 중간 prim 을 두는 경우가 많아 실패하면 ``mirror_root`` 가 비어 인스턴스
    직속에 Override 가 쌓이며 "복제"처럼 보였다.

    **우선 전략 (2026-05-14 보강):** master 인스턴스 산하 각 prim 의 prim stack 에서
    reference / payload 의 ``assetPath`` 를 수집해, ``asset_path`` 와 **동일 파일**을
    가리키는 prim 중 **경로가 가장 깊은** 것을 앵커로 쓴다 (= 실제로 그 USD 를 붙인 prim).

    실패 시 예전처럼 default prim 이름 일치로 한 번 더 시도한다.

    Args:
        stage: master ``Usd.Stage``.
        inst_prim_path: 등록 인스턴스 prim (예: ``/World/aaa``).
        asset_path: 자산 USD 경로 (로컬 파일 또는 ``file:/`` URI).

    Returns:
        master 상의 자산 루트 prim path 또는 ``""``.
    """
    import os

    ap = normalize_asset_uri_to_path(str(asset_path or "").strip())
    if not ap or not os.path.isfile(ap):
        return ""
    if stage is None or not inst_prim_path or not inst_prim_path.startswith("/"):
        return ""
    try:
        from pxr import Usd  # type: ignore
    except Exception:
        return ""

    target_key = _canonical_asset_path_key(ap)
    if not target_key:
        return ""

    stage_root_id = ""
    try:
        rl = stage.GetRootLayer()
        stage_root_id = str(getattr(rl, "realPath", "") or getattr(rl, "identifier", "") or "")
    except Exception:
        stage_root_id = ""

    try:
        inst = stage.GetPrimAtPath(inst_prim_path)
    except Exception:
        inst = None
    if inst is None or not inst.IsValid():
        return ""

    best_path = ""
    best_len = -1
    try:
        for p in Usd.PrimRange(inst):
            try:
                ppath = str(p.GetPath())
            except Exception:
                continue
            try:
                stack = p.GetPrimStack()
            except Exception:
                continue
            matched_here = False
            for spec in stack:
                try:
                    lid = ""
                    lyr = getattr(spec, "layer", None)
                    if lyr is not None:
                        lid = str(getattr(lyr, "identifier", "") or "")
                except Exception:
                    lid = ""
                for ref_list_attr in ("referenceList", "payloadList"):
                    try:
                        ref_list = getattr(spec, ref_list_attr, None)
                        if ref_list is None:
                            continue
                        explicit = list(getattr(ref_list, "explicitItems", []) or [])
                        added = list(getattr(ref_list, "addedItems", []) or [])
                        prepended = list(getattr(ref_list, "prependedItems", []) or [])
                        appended = list(getattr(ref_list, "appendedItems", []) or [])
                        for item in explicit + prepended + appended + added:
                            raw_ap = getattr(item, "assetPath", "")
                            if not raw_ap:
                                continue
                            resolved = _resolve_authored_asset_path(
                                str(raw_ap),
                                author_layer_identifier=lid,
                                stage_root_identifier=stage_root_id,
                            )
                            rk = _canonical_asset_path_key(resolved)
                            if rk and rk == target_key:
                                matched_here = True
                                break
                    except Exception:
                        continue
                if matched_here:
                    break
            if matched_here:
                ln = len(ppath)
                if ln > best_len:
                    best_len = ln
                    best_path = ppath
    except Exception:
        best_path = ""

    if best_path:
        print(
            f"{_PRINT_PREFIX} discover_drag_drop_asset_root_prim(ref-match) "
            f"inst={inst_prim_path} root={best_path}",
            flush=True,
        )
        return best_path

    # --- fallback: default prim 이름 과 동일한 첫 prim (레거시) ---
    try:
        ast = Usd.Stage.Open(ap)
    except Exception:
        return ""
    if ast is None:
        return ""
    dp_name = ""
    try:
        dp = ast.GetDefaultPrim()
        if dp and dp.IsValid():
            dp_name = str(dp.GetName())
    except Exception:
        dp_name = ""
    if not dp_name:
        try:
            pseudo = ast.GetPseudoRoot()
            for ch in pseudo.GetChildren():
                dp_name = str(ch.GetName())
                break
        except Exception:
            dp_name = ""
    if not dp_name:
        return ""
    try:
        for p in Usd.PrimRange(inst):
            if p == inst:
                continue
            try:
                if p.GetName() == dp_name:
                    hit = str(p.GetPath())
                    print(
                        f"{_PRINT_PREFIX} discover_drag_drop_asset_root_prim(name-fallback) "
                        f"inst={inst_prim_path} root={hit} dp_name={dp_name!r}",
                        flush=True,
                    )
                    return hit
            except Exception:
                continue
    except Exception:
        return ""
    print(
        f"{_PRINT_PREFIX} discover_drag_drop_asset_root_prim MISS "
        f"inst={inst_prim_path} asset={ap!r}",
        flush=True,
    )
    return ""


def _discover_asset_path_from_master(stage: Any, root_prim_path: str) -> str:
    """drag&drop 으로 박힌 자산의 절대 경로를 best-effort 로 추출.

    Kit 의 drop handler 는 자산 구조에 따라 두 가지 형태로 박는다:
      (a) ``prim_path`` 자체에 reference (드물게)
      (b) ``prim_path/<file_stem>/<자산 default prim>/...`` 식으로 새 자식 Xform 을
          만들고 그 자식에 reference (사용자가 본 이미지처럼 일반적)
    본 함수는 root prim 산하 ``Usd.PrimRange`` 전체를 순회하며 가장 먼저 발견한
    유효 assetPath 를 반환한다. 실패 시 빈 문자열.
    """
    try:
        from pxr import Sdf, Usd  # type: ignore  # noqa: F401
    except Exception:
        return ""
    if stage is None or not root_prim_path:
        return ""
    try:
        root_prim = stage.GetPrimAtPath(root_prim_path)
    except Exception:
        return ""
    if not root_prim or not root_prim.IsValid():
        return ""

    # 깊이 우선 순회 — Usd.PrimRange 가 가장 빠르고 안전.
    try:
        all_prims = list(Usd.PrimRange(root_prim))
    except Exception:
        all_prims = [root_prim]

    for prim in all_prims:
        try:
            stack = prim.GetPrimStack()
        except Exception:
            continue
        for spec in stack:
            for ref_list_attr in ("referenceList", "payloadList"):
                try:
                    ref_list = getattr(spec, ref_list_attr, None)
                    if ref_list is None:
                        continue
                    explicit = list(getattr(ref_list, "explicitItems", []) or [])
                    added = list(getattr(ref_list, "addedItems", []) or [])
                    prepended = list(getattr(ref_list, "prependedItems", []) or [])
                    appended = list(getattr(ref_list, "appendedItems", []) or [])
                    for item in explicit + prepended + appended + added:
                        ap = getattr(item, "assetPath", "")
                        if ap:
                            # URI prefix 정규화 — drag&drop 결과는 보통 `file:/C:/...`.
                            return normalize_asset_uri_to_path(str(ap))
                except Exception:
                    continue
    return ""


def extract_subtree_to_anonymous_layer(
    stage: Any,
    root_prim_path: str,
    *,
    tag_hint: str = "",
) -> ExtractResult:
    """master stage 의 ``root_prim_path`` 하위 트리를 평탄화해 anonymous layer 로 복사.

    Args:
        stage: ``pxr.Usd.Stage`` (master).
        root_prim_path: 인스턴스 prim path (예: ``/World/aaa``).
        tag_hint: anonymous layer 식별자 prefix 에 들어가는 짧은 hint 문자열.

    Returns:
        ``ExtractResult`` — 성공 시 ``ok=True`` 와 ``layer`` 채워짐.
    """
    t0 = time.perf_counter()
    result = ExtractResult(root_prim_path=root_prim_path, asset_label=tag_hint or root_prim_path)

    try:
        from pxr import Sdf, Usd  # type: ignore  # noqa: F401
    except Exception as exc:
        result.error = f"pxr import failed: {exc}"
        result.elapsed_sec = time.perf_counter() - t0
        return result

    if stage is None:
        result.error = "master stage is None"
        result.elapsed_sec = time.perf_counter() - t0
        return result
    if not root_prim_path or not root_prim_path.startswith("/"):
        result.error = f"invalid root_prim_path={root_prim_path!r}"
        result.elapsed_sec = time.perf_counter() - t0
        return result

    # 추출 직전 master 의 해당 prim 이 비어 있으면 (사용자가 reference 만 제거하고
    # 아직 drag&drop 을 안 한 상태) 안내.
    try:
        master_prim = stage.GetPrimAtPath(root_prim_path)
    except Exception:
        master_prim = None
    if master_prim is None or not master_prim.IsValid():
        result.error = (
            f"prim={root_prim_path} 가 master stage 에 없습니다. drag&drop 으로 자산을 "
            f"이 경로 하위에 먼저 넣어주세요."
        )
        result.elapsed_sec = time.perf_counter() - t0
        return result

    has_child = False
    try:
        for _ in master_prim.GetChildren():
            has_child = True
            break
    except Exception:
        has_child = False
    if not has_child:
        # children 이 없어도 prim 자체에 attribute timeSamples 가 있을 수 있으므로 계속
        # 진행은 하되 진단 로그만 남긴다.
        print(
            f"{_PRINT_PREFIX} 주의: prim={root_prim_path} 아래 자식 prim 이 없음. "
            f"drag&drop 으로 자산을 박지 않았다면 추출 결과가 빈 layer 가 될 수 있습니다.",
            flush=True,
        )

    # drag&drop 으로 박힌 자산의 실제 경로 추정 — UI 가 `inst.source_asset` 을 갱신해
    # 차후 [Bake] 호출 시 올바른 자산 경로가 쓰이도록 한다.
    try:
        result.discovered_asset_path = _discover_asset_path_from_master(stage, root_prim_path)
    except Exception:
        result.discovered_asset_path = ""

    # 1) 모든 composition 을 평가한 단일 layer 로 flatten.
    #    배치 Extract(자동 로드 등)에서는 master_flatten_cache 로 1회만 Flatten.
    try:
        flat = _resolve_flattened_master_layer(stage)
    except Exception as exc:
        result.error = f"stage.Flatten() 실패: {exc}"
        result.elapsed_sec = time.perf_counter() - t0
        return result
    if flat is None:
        result.error = "stage.Flatten() returned None"
        result.elapsed_sec = time.perf_counter() - t0
        return result

    # 2) anonymous layer 생성 + /Root prim spec 컨테이너 author.
    try:
        anon = Sdf.Layer.CreateAnonymous(f"lam_extracted_{tag_hint or 'anon'}")
    except Exception as exc:
        result.error = f"CreateAnonymous 실패: {exc}"
        result.elapsed_sec = time.perf_counter() - t0
        return result

    try:
        Sdf.CreatePrimInLayer(anon, "/Root")
    except Exception as exc:
        result.error = f"CreatePrimInLayer(/Root) 실패: {exc}"
        result.elapsed_sec = time.perf_counter() - t0
        return result

    # 3) flatten layer 의 root_prim_path 트리를 anonymous layer 의 /Root 로 복사.
    try:
        ok = bool(
            Sdf.CopySpec(
                flat,
                Sdf.Path(root_prim_path),
                anon,
                Sdf.Path("/Root"),
            )
        )
    except Exception as exc:
        result.error = f"Sdf.CopySpec 실패: {exc}"
        result.elapsed_sec = time.perf_counter() - t0
        return result
    if not ok:
        result.error = "Sdf.CopySpec returned False"
        result.elapsed_sec = time.perf_counter() - t0
        return result

    # 4) anonymous layer 에 stage metadata (fps / startTime / endTime / defaultPrim) 보강 —
    #    offscreen stage 가 일관된 30fps 도메인에서 평가되도록 한다 (LAM_FIXED_FPS 정책).
    #    또한 `defaultPrim = "Root"` 를 박아 _build_attr_cache 가 GetDefaultPrim() 으로
    #    즉시 `/Root` 를 잡아 master `/World/<inst>` 와 1:1 매핑을 한다. defaultPrim 미설정
    #    시 pseudo-root 첫 자식 fallback 이 동작하나 trees 가 여러 sibling 인 실무 USD
    #    에서는 잘못된 root 가 잡힐 수 있어 명시한다.
    try:
        from .lam_types import LAM_FIXED_FPS
    except Exception:
        LAM_FIXED_FPS = 30.0  # type: ignore[assignment]
    try:
        anon.framesPerSecond = float(LAM_FIXED_FPS)
        anon.timeCodesPerSecond = float(LAM_FIXED_FPS)
    except Exception:
        pass
    try:
        anon.defaultPrim = "Root"
    except Exception:
        pass

    # 5) 통계 산출 + 자산 종류 자동 판정 (lam_asset_diagnostics 와 동일 규칙).
    stats = _scan_layer_stats(anon)
    result.n_prims = int(stats["n_prims"])
    result.n_attrs_total = int(stats["n_attrs_total"])
    result.n_attrs_with_timesamples = int(stats["n_attrs_with_timesamples"])
    result.n_xform_op_ts = int(stats["n_xform_op_ts"])
    result.n_skel_anim_ts = int(stats["n_skel_anim_ts"])
    result.n_mesh_points_ts = int(stats["n_mesh_points_ts"])
    result.n_visibility_ts = int(stats["n_visibility_ts"])
    result.n_other_ts = int(stats["n_other_ts"])
    result.n_omnigraph_prims = int(stats["n_omnigraph_prims"])
    result.tc_min = float(stats["tc_min"])
    result.tc_max = float(stats["tc_max"])

    # start/end time 자동 설정 — timeSamples 가 있을 때만.
    try:
        if result.n_attrs_with_timesamples > 0:
            anon.startTimeCode = float(result.tc_min)
            anon.endTimeCode = float(result.tc_max)
    except Exception:
        pass

    # 자산 종류 판정 — `_decide_kind` 가 카운트 조합을 보고 결정.
    try:
        from .lam_asset_diagnostics import _decide_kind
        from .lam_types import (
            ASSET_KIND_OMNIGRAPH,
            ASSET_KIND_STATIC,
            ASSET_KIND_UNKNOWN,
            AssetDiag,
        )
    except Exception as exc:
        result.error = f"asset kind 모듈 import 실패: {exc}"
        result.layer = anon
        result.kind = "UNKNOWN"
        result.elapsed_sec = time.perf_counter() - t0
        return result

    diag_obj = AssetDiag(
        n_xform_op_ts=result.n_xform_op_ts,
        n_skel_anim_ts=result.n_skel_anim_ts,
        n_mesh_points_ts=result.n_mesh_points_ts,
        n_visibility_ts=result.n_visibility_ts,
        n_other_ts=result.n_other_ts,
        n_omnigraph_prims=result.n_omnigraph_prims,
        asset_start_tc=result.tc_min,
        asset_end_tc=result.tc_max,
    )
    try:
        result.kind = str(_decide_kind(diag_obj)) or ASSET_KIND_UNKNOWN
    except Exception:
        result.kind = ASSET_KIND_UNKNOWN

    # OK / FAIL 분기 — timeSamples 가 1 개라도 있으면 ok=True (MIXED 포함).
    if result.n_attrs_with_timesamples > 0:
        result.layer = anon
        result.ok = True
        result.elapsed_sec = time.perf_counter() - t0
        print(
            f"{_PRINT_PREFIX} extract OK {result.to_log_line()}",
            flush=True,
        )
        return result

    # 여기 도달 = timeSamples 0 개. kind 에 따라 안내 메시지 달리한다.
    if result.kind == ASSET_KIND_OMNIGRAPH:
        result.error = (
            "이 자산은 OmniGraph 만 갖고 있어 timeSamples 가 없습니다. "
            "인스턴스 행의 [Bake] 버튼을 사용해 in-memory timeSamples 로 변환하세요."
        )
    elif result.kind == ASSET_KIND_STATIC:
        result.error = (
            "이 자산은 시간 데이터(timeSamples / OmniGraph) 가 전혀 없습니다 (정적 자산). "
            "TIMESAMPLES_REPLAY / Bake 대상이 아닙니다."
        )
    else:
        result.error = (
            f"timeSamples 추출 결과가 비었습니다 (kind={result.kind}). drag&drop 으로 "
            f"자산을 박았는지, prim 경로가 올바른지 확인하세요."
        )
    # layer 는 디버깅 용도로 반환. ok=False 유지.
    result.layer = anon
    result.elapsed_sec = time.perf_counter() - t0
    print(
        f"{_PRINT_PREFIX} extract NEED-BAKE-OR-EMPTY {result.to_log_line()} "
        f"reason={result.error!r}",
        flush=True,
    )
    return result


def dump_layer_to_usda_text(layer: Any, *, header_lines: Optional[list] = None) -> str:
    """anonymous (또는 일반) ``Sdf.Layer`` 를 USDA 텍스트로 직렬화.

    사용 목적 — **timeSamples 데이터 검증용 클립보드 복사**. 사용자가 텍스트 에디터에
    붙여넣으면 표준 USDA 포맷으로 모든 prim/attribute 의 ``timeSamples`` 가 그대로
    보인다 (FBX→USD 변환 결과 검증에 유용).

    Args:
        layer: ``pxr.Sdf.Layer`` — ``extract_subtree_to_anonymous_layer`` 의 결과
            layer 가 가장 일반적인 입력.
        header_lines: 본문 앞에 ``# ...`` 주석으로 붙일 추가 라인 (메타 정보 등).

    Returns:
        USDA 텍스트. 직렬화 실패 시 ``# error: ...`` 가 포함된 단일 라인.
    """
    head: list = list(header_lines or [])
    body = ""
    try:
        if layer is None:
            return "\n".join([*head, "# error: layer is None"])
        body = str(layer.ExportToString())
    except Exception as exc:
        return "\n".join([*head, f"# error: layer.ExportToString() failed: {exc}"])
    if head:
        return "\n".join(head) + "\n" + body
    return body


__all__ = [
    "ExtractResult",
    "begin_master_flatten_cache",
    "clear_master_flatten_cache",
    "end_master_flatten_cache",
    "extract_subtree_to_anonymous_layer",
    "dump_layer_to_usda_text",
    "master_flatten_cache",
    "scan_layer_timesample_stats",
    "normalize_asset_uri_to_path",
    "discover_drag_drop_asset_root_prim",
]
