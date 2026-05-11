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
from typing import Any, Callable, Dict, List, Optional, Tuple

from .lam_types import LAM_FIXED_FPS


_PRINT_PREFIX = "[LAM/Bake]"

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
    """`<asset>_baked.usd` 형태의 기본 출력 경로 생성."""
    base, _ext = os.path.splitext(os.path.abspath(asset_path))
    return base + "_baked.usd"


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

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"BakeResult(ok={self.ok} out={self.output_path!r} err={self.error!r} "
            f"frames={self.n_frames} prims={self.n_target_prims} "
            f"attrs={self.n_attr_authored} pruned={self.n_attr_pruned_static} "
            f"skipped={self.skipped_existing} stride={self.frame_stride} "
            f"sparse_skip={self.n_sparse_skipped_capture} elapsed={self.elapsed_sec:.3f}s)"
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
    fps: float = LAM_FIXED_FPS,
    frame_stride: int = 1,
    sparse_time_samples: bool = False,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> BakeResult:
    """master(default) context 의 timeline 을 scrub 하며 인스턴스 산하의 xform 결과를
    capture 하여 baked.usd 로 저장.

    Args:
        master_stage: master 의 `pxr.Usd.Stage` (default context 에 attach 됨).
        inst_prim_path: master 안의 인스턴스 prim path (예: `/World/aaa`).
        asset_path: 원본 자산 USD 의 절대 경로 (디폴트 출력 경로 / asset_root 결정용).
        output_path: 빈 문자열이면 `<asset>_baked.usd`.
        start_frame, end_frame: capture 범위 (frame, fps 기준). -1 이면 자산 메타 사용.
        fps: capture 율. LAM 정책 30.
        frame_stride: **샘플 간격 (프레임)**. 1 이면 매 프레임, 2 이면 격프레임만 스크럽.
            2000 프레임 전후 장시간 자산에서 **반복 횟수를 줄여 bake 시간을 선형으로 단축**.
            마지막 `end_frame` 은 stride 와 무관하게 항상 1 회 포함된다.
        sparse_time_samples: True 이면 연속 프레임에서 값이 동일한 attribute 샘플은
            기록하지 않는다 (USD 가 키프레임 사이를 보간). dict 기록량·파일 크기 감소.
        progress_cb: `(cur, total, msg)` 콜백.
        cancel_cb: 매 frame 진입 시 True 면 중단.

    환경 변수 (lam_window 에서 전달하지 않을 때 기본값만 사용 — UI 에서는 env 로 조정):
        `LAM_BAKE_FRAME_STRIDE`, `LAM_BAKE_SPARSE_SAMPLES` 는 호출부에서 읽어 kwargs 로 넘긴다.

    Returns:
        `BakeResult`.
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
    fps = float(fps if fps > 0 else LAM_FIXED_FPS)
    stride_i = max(1, int(frame_stride))

    sample_frames = _build_sample_frames(s_frame, e_frame, stride_i)
    n_sample_frames = len(sample_frames)

    inst_prim_path_str = str(inst_prim.GetPath())

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
                continue
            try:
                rec: List[Tuple[Any, str, Any, bool]] = []
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
                ap = _map_inst_path_to_asset_root(inst_prim_path, asset_root, pp)
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

    # tps / framerate 강제 (LAM_FIXED_FPS 정책).
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
        return BakeResult(
            ok=False,
            error=(
                f"capture targets empty under {inst_prim_path} — warm-up 후에도 "
                f"xformOp/visibility attribute 가 보이지 않습니다. (자산 구조 점검 필요)"
            ),
        )

    last_by_key: Dict[Tuple[str, str], Any] = {}
    n_sparse_skipped = 0
    t_capture_start = time.perf_counter()
    t_tick_total = 0.0
    t_read_total = 0.0

    print(
        f"{_PRINT_PREFIX} scrub start prim={inst_prim_path} frame_range=[{s_frame}, {e_frame}] "
        f"sample_count={n_sample_frames} stride={stride_i} sparse={sparse_time_samples} "
        f"fps={fps} targets={len(targets)} asset_root={asset_root} "
        f"saved_time={saved_time:.3f}s playing={was_playing}",
        flush=True,
    )

    for f_idx, cur_frame in enumerate(sample_frames):
        tc = float(cur_frame)
        if cancel_cb is not None:
            try:
                if cancel_cb():
                    try:
                        timeline.set_current_time(saved_time)  # type: ignore[attr-defined]
                    except Exception:
                        pass
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
    t_author_start = time.perf_counter()
    for inst_pp, adict in capture.items():
        asset_pp = _map_inst_path_to_asset_root(inst_prim_path, asset_root, inst_pp)
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

    # 디렉터리 보장 + Export.
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

    elapsed = time.perf_counter() - t0
    print(
        f"{_PRINT_PREFIX} done out={output_path} sample_frames={n_sample_frames} "
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
        output_path=output_path,
        n_frames=n_sample_frames,
        n_target_prims=len(capture),
        n_attr_authored=n_attr_authored,
        n_attr_pruned_static=n_attr_pruned_static,
        elapsed_sec=elapsed,
        frame_stride=stride_i,
        n_sparse_skipped_capture=n_sparse_skipped,
    )


__all__ = [
    "BakeResult",
    "bake_prim_to_timesamples_async",
    "make_default_baked_path",
    "read_bake_speed_env",
]
