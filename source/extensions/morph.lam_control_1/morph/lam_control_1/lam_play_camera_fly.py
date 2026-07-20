"""CSV Play 시작 전 뷰포트 카메라 fly-to (설정: lam_viewport_overlay_config).

순서(Play, 일시정지 이어서 제외):
  1) 체크 ON + preset 설정됨 + 현재 뷰 ≠ preset → duration 동안 eye/target 보간
  2) prim 숨김(play_start) — 호출측(simulation_play)에서 fly 이후 실행
  3) CSV 재생

「뷰 저장」: 현재 뷰를 캡처해 콘솔에 config 붙여넣기용 로그 출력.
"""

from __future__ import annotations

import contextvars
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import omni.usd as ou  # type: ignore
from pxr import Gf, Sdf, Usd, UsdGeom  # type: ignore

_PRINT_PREFIX = "[LAM/PlayCameraFly]"
_COI_ATTR = "omni:kit:centerOfInterest"
_PERSP_CAMERA_PATH = "/OmniverseKit_Persp"
_usd_context_override: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "lam_camera_fly_usd_ctx",
    default=None,
)


@dataclass(frozen=True)
class CameraViewSnapshot:
    eye_xyz: Tuple[float, float, float]
    target_xyz: Tuple[float, float, float]


@dataclass(frozen=True)
class CameraPrimBaseline:
    """USD Camera prim 최초 xform — 탑뷰·재생 시 translate 유지용."""

    prim_path: str
    local_matrix: Tuple[float, ...]
    coi_xyz: Tuple[float, float, float]
    view: CameraViewSnapshot


_camera_prim_baselines: Dict[str, CameraPrimBaseline] = {}


def _is_session_camera_path(path: str) -> bool:
    p = str(path or "").strip()
    return not p or p == _PERSP_CAMERA_PATH or "Persp" in p


def _matrix_to_tuple(m: Gf.Matrix4d) -> Tuple[float, ...]:
    return tuple(float(m[i][j]) for i in range(4) for j in range(4))


def _tuple_to_matrix(values: Tuple[float, ...]) -> Gf.Matrix4d:
    vals = list(values)
    if len(vals) < 16:
        vals.extend([0.0] * (16 - len(vals)))
    m = Gf.Matrix4d(1.0)
    for i in range(4):
        for j in range(4):
            m[i][j] = float(vals[i * 4 + j])
    return m


def _resolve_camera_up_vector(
    eye: Tuple[float, float, float],
    target: Tuple[float, float, float],
    up_hint: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    """카메라 forward 와 up 이 평행하면 안전한 대체 up 선택 (탑뷰 -Z 시선 등)."""
    fwd = _vec3(target) - _vec3(eye)
    if fwd.GetLength() < 1e-9:
        return (0.0, 0.0, 1.0)
    fwd.Normalize()
    up_v = _vec3(up_hint)
    if up_v.GetLength() < 1e-9:
        up_v = Gf.Vec3d(0.0, 0.0, 1.0)
    else:
        up_v.Normalize()
    if abs(float(fwd * up_v)) <= 0.99:
        return (float(up_v[0]), float(up_v[1]), float(up_v[2]))
    best = Gf.Vec3d(0.0, 0.0, 1.0)
    best_align = abs(float(fwd * best))
    for cand in (Gf.Vec3d(0.0, 1.0, 0.0), Gf.Vec3d(1.0, 0.0, 0.0)):
        align = abs(float(fwd * cand))
        if align < best_align:
            best_align = align
            best = cand
    return (float(best[0]), float(best[1]), float(best[2]))


def _matrix_rotation_rows_sane(m: Gf.Matrix4d, *, max_len: float = 4.0) -> bool:
    """회전 행 길이가 비정상(폭주 scale)인지 검사."""
    for i in range(3):
        row = Gf.Vec3d(float(m[i][0]), float(m[i][1]), float(m[i][2]))
        ln = row.GetLength()
        if ln > max_len or (ln > 1e-9 and ln < 0.05):
            return False
    return True


def _invalidate_camera_prim_baseline(prim_path: str) -> None:
    path = str(prim_path or "").strip()
    if path:
        _camera_prim_baselines.pop(path, None)


def _write_prim_local_xform(
    prim_path: str,
    local_matrix: Gf.Matrix4d,
    *,
    coi: Optional[Gf.Vec3d] = None,
    force_reset_ops: bool = False,
) -> bool:
    stage = _get_stage()
    path = str(prim_path or "").strip()
    if not stage or not path:
        return False
    cam_prim = stage.GetPrimAtPath(path)
    if not cam_prim or not cam_prim.IsValid():
        return False
    xformable = UsdGeom.Xformable(cam_prim)
    old_local = xformable.GetLocalTransformation(Usd.TimeCode.Default())
    if not _matrix_rotation_rows_sane(local_matrix):
        print(
            f"{_PRINT_PREFIX} 거부 — 비정상 camera matrix path={path!r}",
            flush=True,
        )
        return False
    ctx_name = _resolve_usd_context_name()
    if force_reset_ops:
        try:
            edit = Usd.EditContext(stage, Usd.EditTarget(stage.GetSessionLayer()))
            with edit:
                xformable = UsdGeom.Xformable(cam_prim)
                xformable.ClearXformOpOrder()
                op = xformable.AddTransformOp()
                op.Set(local_matrix)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} USD xform reset failed: {exc}", flush=True)
            return False
    else:
        try:
            import omni.kit.commands as cmds  # type: ignore

            cmds.execute(
                "TransformPrimCommand",
                path=path,
                new_transform_matrix=local_matrix,
                old_transform_matrix=old_local,
                time_code=Usd.TimeCode.Default(),
                usd_context_name=ctx_name,
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} TransformPrimCommand failed: {exc}", flush=True)
            try:
                edit = Usd.EditContext(stage, Usd.EditTarget(stage.GetSessionLayer()))
                with edit:
                    xformable = UsdGeom.Xformable(cam_prim)
                    xformable.ClearXformOpOrder()
                    op = xformable.AddTransformOp()
                    op.Set(local_matrix)
            except Exception as exc2:
                print(f"{_PRINT_PREFIX} USD xform set failed: {exc2}", flush=True)
                return False
    if coi is not None:
        try:
            edit = Usd.EditContext(stage, Usd.EditTarget(stage.GetSessionLayer()))
            with edit:
                coi_attr = cam_prim.GetAttribute(_COI_ATTR)
                if not coi_attr or not coi_attr.IsValid():
                    cam_prim.CreateAttribute(
                        _COI_ATTR, Sdf.ValueTypeNames.Vector3d, custom=True
                    ).Set(coi)
                else:
                    coi_attr.Set(coi)
        except Exception:
            pass
    return True


def _read_prim_baseline_from_stage(prim_path: str) -> Optional[CameraPrimBaseline]:
    path = str(prim_path or "").strip()
    if not path or _is_session_camera_path(path):
        return None
    stage = _get_stage()
    if not stage:
        return None
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return None
    try:
        xformable = UsdGeom.Xformable(prim)
        local = xformable.GetLocalTransformation(Usd.TimeCode.Default())
        coi = _read_coi_local(prim)
        view = snapshot_from_prim_path(path)
        if view is None:
            return None
        return CameraPrimBaseline(
            prim_path=path,
            local_matrix=_matrix_to_tuple(local),
            coi_xyz=(float(coi[0]), float(coi[1]), float(coi[2])),
            view=view,
        )
    except Exception:
        return None


def ensure_camera_prim_baseline(prim_path: str) -> Optional[CameraPrimBaseline]:
    """USD Camera prim 최초 translate/xform 저장 (이후 재사용)."""
    path = str(prim_path or "").strip()
    if not path or _is_session_camera_path(path):
        return None
    cached = _camera_prim_baselines.get(path)
    if cached is not None:
        return cached
    baseline = _read_prim_baseline_from_stage(path)
    if baseline is None:
        return None
    _camera_prim_baselines[path] = baseline
    eye = baseline.view.eye_xyz
    print(
        f"{_PRINT_PREFIX} Camera baseline 저장 path={path!r} "
        f"eye=({eye[0]:.3f}, {eye[1]:.3f}, {eye[2]:.3f})",
        flush=True,
    )
    return baseline


def get_camera_prim_baseline_view(prim_path: str) -> Optional[CameraViewSnapshot]:
    baseline = ensure_camera_prim_baseline(prim_path)
    if baseline is None:
        return snapshot_from_prim_path(prim_path)
    return baseline.view


def restore_camera_prim_baseline(
    prim_path: str,
    *,
    log_context: str = "",
) -> bool:
    """저장된 baseline xform/COI 로 USD Camera prim 복원 (명시적 1회 호출용)."""
    path = str(prim_path or "").strip()
    if _is_session_camera_path(path):
        return False
    baseline = _camera_prim_baselines.get(path)
    if baseline is None:
        baseline = ensure_camera_prim_baseline(path)
    if baseline is None:
        return False
    stage = _get_stage()
    if not stage:
        return False
    cam_prim = stage.GetPrimAtPath(path)
    if not cam_prim or not cam_prim.IsValid():
        return False
    new_local = _tuple_to_matrix(baseline.local_matrix)
    xformable = UsdGeom.Xformable(cam_prim)
    old_local = xformable.GetLocalTransformation(Usd.TimeCode.Default())
    if old_local == new_local:
        return True
    ctx_name = _resolve_usd_context_name()
    ok = False
    try:
        import omni.kit.commands as cmds  # type: ignore

        cmds.execute(
            "TransformPrimCommand",
            path=path,
            new_transform_matrix=new_local,
            old_transform_matrix=old_local,
            time_code=Usd.TimeCode.Default(),
            usd_context_name=ctx_name,
        )
        ok = True
    except Exception:
        ok = _write_prim_local_xform(
            path,
            new_local,
            coi=Gf.Vec3d(*baseline.coi_xyz),
        )
    else:
        try:
            edit = Usd.EditContext(stage, Usd.EditTarget(stage.GetSessionLayer()))
            with edit:
                coi_attr = cam_prim.GetAttribute(_COI_ATTR)
                coi = Gf.Vec3d(*baseline.coi_xyz)
                if not coi_attr or not coi_attr.IsValid():
                    cam_prim.CreateAttribute(
                        _COI_ATTR, Sdf.ValueTypeNames.Vector3d, custom=True
                    ).Set(coi)
                else:
                    coi_attr.Set(coi)
        except Exception:
            pass
    tag = f" ({log_context})" if log_context else ""
    if ok and log_context:
        print(f"{_PRINT_PREFIX} Camera baseline 복원{tag} path={path!r}", flush=True)
    return ok


def stop_play_camera_baseline_hold() -> None:
    """레거시 no-op — per-frame hold 제거됨."""
    pass


def _smoothstep01(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def _lerp3(
    a: Tuple[float, float, float],
    b: Tuple[float, float, float],
    u: float,
) -> Tuple[float, float, float]:
    return (
        float(a[0] + (b[0] - a[0]) * u),
        float(a[1] + (b[1] - a[1]) * u),
        float(a[2] + (b[2] - a[2]) * u),
    )


def _vec3(t: Tuple[float, float, float]) -> Gf.Vec3d:
    return Gf.Vec3d(float(t[0]), float(t[1]), float(t[2]))


def _resolve_usd_context_name() -> str:
    ov = _usd_context_override.get()
    if ov:
        return str(ov)
    try:
        from omni.kit.viewport.utility import get_active_viewport  # type: ignore

        vp = get_active_viewport()
        if vp is not None:
            return str(getattr(vp, "usd_context_name", "") or "")
    except Exception:
        pass
    return ""


@contextmanager
def camera_fly_usd_context(context_name: Optional[str]):
    """지정 USD context 의 Stage/Camera 에 fly·bind 적용 (화면2 분할)."""
    token = _usd_context_override.set(str(context_name) if context_name else None)
    try:
        yield
    finally:
        _usd_context_override.reset(token)


def _get_stage() -> Optional[Usd.Stage]:
    try:
        ctx = ou.get_context(_resolve_usd_context_name())
        if ctx is None:
            ctx = ou.get_context("")
        if ctx is None:
            return None
        return ctx.get_stage()
    except Exception:
        return None


def _get_active_viewport_api() -> Any:
    try:
        from omni.kit.viewport.utility import get_active_viewport  # type: ignore

        return get_active_viewport()
    except Exception:
        return None


def play_camera_use_preset_coords() -> bool:
    try:
        from .lam_viewport_overlay_config import PLAY_CAMERA_USE_PRESET_COORDS  # type: ignore

        return bool(PLAY_CAMERA_USE_PRESET_COORDS)
    except Exception:
        return True


def top_view_use_preset_coords() -> bool:
    try:
        from .lam_viewport_overlay_config import TOP_VIEW_USE_PRESET_COORDS  # type: ignore

        return bool(TOP_VIEW_USE_PRESET_COORDS)
    except Exception:
        return True


def play_camera_prim_path() -> str:
    try:
        from .lam_viewport_overlay_config import PLAY_CAMERA_PRIM_PATH  # type: ignore

        return str(PLAY_CAMERA_PRIM_PATH or "").strip()
    except Exception:
        return ""


def top_view_camera_prim_path() -> str:
    try:
        from .lam_viewport_overlay_config import TOP_VIEW_CAMERA_PRIM_PATH  # type: ignore

        return str(TOP_VIEW_CAMERA_PRIM_PATH or "").strip()
    except Exception:
        return ""


def play_camera_bind_viewport_to_usd_prim() -> bool:
    """Camera prim 모드에서 viewport bind 여부 (레거시·로그용).

    ``USE_PRESET_COORDS=False`` 이면 항상 USD Camera prim 에 bind (요구사항 §4.6).
    """
    return bool(play_assign_prim_path())


def top_view_bind_viewport_to_usd_prim() -> bool:
    """Camera prim 모드에서 viewport bind 여부 (레거시·로그용).

    ``USE_PRESET_COORDS=False`` 이면 항상 USD Camera prim 에 bind (요구사항 §4.6).
    """
    return bool(top_view_assign_prim_path())


def play_assign_prim_path() -> str:
    """Camera prim 모드면 fly 완료 후 bind 할 USD Camera 경로."""
    if play_camera_use_preset_coords():
        return ""
    return play_camera_prim_path()


def top_view_assign_prim_path() -> str:
    """Camera prim 모드면 fly 완료 후 bind 할 USD Camera 경로."""
    if top_view_use_preset_coords():
        return ""
    return top_view_camera_prim_path()


def bind_viewport_to_camera_prim(
    prim_path: str,
    *,
    log_context: str = "",
    viewport_api: Any = None,
    usd_context_name: str = "",
) -> bool:
    """viewport 를 USD Camera prim look-through 로 전환."""
    path = str(prim_path or "").strip()
    if not path:
        return False
    ctx = str(usd_context_name or "").strip()
    with camera_fly_usd_context(ctx or None):
        stage = _get_stage()
        if stage is None:
            return False
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            print(
                f"{_PRINT_PREFIX} viewport Camera bind 실패 — prim 없음 path={path!r}",
                flush=True,
            )
            return False
        ensure_camera_prim_baseline(path)
        if viewport_api is not None:
            ok = set_viewport_camera_prim_path_on_api(
                viewport_api,
                path,
                usd_context_name=ctx or _resolve_usd_context_name(),
            )
        else:
            ok = set_viewport_camera_prim_path(path)
    tag = f" ({log_context})" if log_context else ""
    if ok:
        print(
            f"{_PRINT_PREFIX} viewport Camera bind{tag} path={path!r}",
            flush=True,
        )
    else:
        print(
            f"{_PRINT_PREFIX} viewport Camera bind 실패{tag} path={path!r}",
            flush=True,
        )
    return ok


def _finish_fly_to_target(
    target: CameraViewSnapshot,
    *,
    up_xyz: Tuple[float, float, float],
    assign_prim_path: str = "",
    log_context: str = "",
    viewport_api: Any = None,
    usd_context_name: str = "",
) -> bool:
    """fly 종료·동기화 — camera prim 모드면 bind, preset 모드면 session view 적용."""
    path = str(assign_prim_path or "").strip()
    ctx = str(usd_context_name or "").strip()
    if path:
        return bind_viewport_to_camera_prim(
            path,
            log_context=log_context or "fly_end",
            viewport_api=viewport_api,
            usd_context_name=ctx,
        )
    with camera_fly_usd_context(ctx or None):
        ok = apply_session_view_to_target(
            target,
            up_xyz=up_xyz,
            log_context=log_context or "fly_end",
        )
        if viewport_api is not None:
            set_viewport_camera_prim_path_on_api(
                viewport_api,
                _PERSP_CAMERA_PATH,
                usd_context_name=ctx or _resolve_usd_context_name(),
            )
        return ok


def capture_view_for_viewport(
    viewport_api: Any,
    usd_context_name: str = "",
) -> Optional[CameraViewSnapshot]:
    """지정 viewport/context 의 현재 카메라 뷰 스냅샷."""
    ctx = str(usd_context_name or "").strip()
    with camera_fly_usd_context(ctx or None):
        path = ""
        try:
            cp = getattr(viewport_api, "camera_path", None) if viewport_api is not None else None
            if cp is not None:
                path = str(cp).strip()
        except Exception:
            path = ""
        if path:
            snap = snapshot_from_prim_path(path)
            if snap is not None:
                return snap
        snap = snapshot_from_prim_path(_PERSP_CAMERA_PATH)
        if snap is not None:
            return snap
        return capture_current_view()


def _iter_viewport_apis() -> List[Any]:
    apis: List[Any] = []
    seen: set[int] = set()

    def _add(api: Any) -> None:
        if api is None:
            return
        oid = id(api)
        if oid in seen:
            return
        seen.add(oid)
        apis.append(api)

    _add(_get_active_viewport_api())
    for win_name in ("Viewport", "LAM Viewport", "Scene View"):
        try:
            from omni.kit.viewport.utility import get_viewport_from_window_name  # type: ignore

            _add(get_viewport_from_window_name(str(win_name)))
        except Exception:
            pass
    return apis


def set_viewport_camera_prim_path(prim_path: str) -> bool:
    path = str(prim_path or "").strip()
    if not path:
        return False
    api = _get_active_viewport_api()
    return set_viewport_camera_prim_path_on_api(api, path)


def set_viewport_camera_prim_path_on_api(
    viewport_api: Any,
    prim_path: str,
    *,
    usd_context_name: str = "",
) -> bool:
    path = str(prim_path or "").strip()
    if not path or viewport_api is None:
        return False
    ctx = str(usd_context_name or "").strip()
    if ctx:
        for attr in ("usd_context_name", "context_name"):
            try:
                if hasattr(viewport_api, attr):
                    setattr(viewport_api, attr, ctx)
            except Exception:
                pass
    try:
        viewport_api.camera_path = Sdf.Path(path)
        return True
    except Exception:
        try:
            viewport_api.camera_path = path
            return True
        except Exception:
            pass
    return False


def restore_perspective_on_viewport(
    viewport_api: Any,
    usd_context_name: str = "",
) -> bool:
    """지정 viewport 를 Kit 기본 Perspective 로 복귀."""
    ctx = str(usd_context_name or _resolve_usd_context_name() or "").strip()
    if viewport_api is None:
        return False
    return set_viewport_camera_prim_path_on_api(
        viewport_api,
        _PERSP_CAMERA_PATH,
        usd_context_name=ctx,
    )


def switch_to_perspective_viewport(*, restore_navigation: bool = True) -> bool:
    """Kit 기본 Perspective — session OmniverseKit_Persp (USD Camera bind 해제)."""
    before = _active_camera_path_str()
    ok = False
    for api in _iter_viewport_apis():
        try:
            api.camera_path = Sdf.Path(_PERSP_CAMERA_PATH)
            ok = True
        except Exception:
            try:
                api.camera_path = _PERSP_CAMERA_PATH
                ok = True
            except Exception:
                pass
    after = _active_camera_path_str()
    print(
        f"{_PRINT_PREFIX} Perspective 전환 "
        f"{before!r} -> {after!r} ok={ok}",
        flush=True,
    )
    if restore_navigation:
        try:
            from .lam_viewport_top_view import ensure_active_viewport_navigation_enabled

            ensure_active_viewport_navigation_enabled()
        except Exception:
            pass
    return bool(ok)


def ensure_session_perspective_camera(
    *,
    log_label: str = "",
    restore_navigation: bool = True,
) -> bool:
    """USD Camera look-through 에서 벗어나 session Persp 로 복귀."""
    path = _active_camera_path_str() or ""
    if path in (_PERSP_CAMERA_PATH, "") or "Persp" in path:
        return True
    tag = f" ({log_label})" if log_label else ""
    print(
        f"{_PRINT_PREFIX} session Persp 복귀 필요{tag}: camera={path!r}",
        flush=True,
    )
    return switch_to_perspective_viewport(restore_navigation=restore_navigation)


def restore_kit_default_perspective(*, log_label: str = "") -> bool:
    """USD Camera look-through 해제 → session OmniverseKit_Persp (Persp prim 삭제 금지)."""
    ok = switch_to_perspective_viewport(restore_navigation=True)
    tag = f" ({log_label})" if log_label else ""
    print(f"{_PRINT_PREFIX} Kit Perspective 복구{tag} ok={ok}", flush=True)
    return bool(ok)


def _session_persp_has_authored_overrides() -> bool:
    stage = _get_stage()
    if not stage:
        return False
    session = stage.GetSessionLayer()
    if session is None:
        return False
    prim_spec = session.GetPrimAtPath(Sdf.Path(_PERSP_CAMERA_PATH))
    if prim_spec is None:
        return False
    try:
        return bool(list(prim_spec.properties))
    except Exception:
        return False


def _persp_world_up_xyz() -> Optional[Tuple[float, float, float]]:
    stage = _get_stage()
    if not stage:
        return None
    prim = stage.GetPrimAtPath(_PERSP_CAMERA_PATH)
    if not prim or not prim.IsValid():
        return None
    try:
        xfc = UsdGeom.XformCache(Usd.TimeCode.Default())
        world = xfc.GetLocalToWorldTransform(prim)
        up = world.TransformDir(Gf.Vec3d(0.0, 1.0, 0.0))
        if up.GetLength() < 1e-9:
            return None
        up.Normalize()
        return (float(up[0]), float(up[1]), float(up[2]))
    except Exception:
        return None


def _persp_view_needs_sanitize() -> bool:
    """카메라 up 이 world Z 와 크게 어긋나면(바닥 눕힘) 복구 대상."""
    up = _persp_world_up_xyz()
    if up is None:
        return False
    dot = float(Gf.Dot(Gf.Vec3d(*up), Gf.Vec3d(0.0, 0.0, 1.0)))
    return dot < 0.75


def _execute_viewport_action(action_id: str) -> bool:
    try:
        import omni.kit.actions.core as actions  # type: ignore

        act = actions.get_action_registry().get_action(
            "omni.kit.viewport.actions",
            str(action_id),
        )
        if act is not None:
            act.execute()
            return True
    except Exception:
        pass
    return False


def _default_persp_view_from_stage() -> Optional[CameraViewSnapshot]:
    stage = _get_stage()
    if not stage:
        return None
    center: Optional[Gf.Vec3d] = None
    dist = 8000.0
    try:
        from .lam_viewport_startup_focus import startup_viewport_focus_config

        enabled, focus_path = startup_viewport_focus_config()
        if enabled and focus_path:
            prim = stage.GetPrimAtPath(focus_path)
            if prim and prim.IsValid():
                cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"])
                box = cache.ComputeWorldBound(prim).GetBox()
                if not box.IsEmpty():
                    center = box.GetMidpoint()
                    dist = max(float(box.GetSize().GetLength()) * 2.5, 500.0)
    except Exception:
        pass
    if center is None:
        world_prim = stage.GetPrimAtPath("/World")
        if world_prim and world_prim.IsValid():
            try:
                cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"])
                box = cache.ComputeWorldBound(world_prim).GetBox()
                if not box.IsEmpty():
                    center = box.GetMidpoint()
                    dist = max(float(box.GetSize().GetLength()) * 2.5, 500.0)
            except Exception:
                pass
    if center is None:
        return None
    eye = center + Gf.Vec3d(0.35 * dist, -0.85 * dist, 0.55 * dist)
    return CameraViewSnapshot(
        eye_xyz=(float(eye[0]), float(eye[1]), float(eye[2])),
        target_xyz=(float(center[0]), float(center[1]), float(center[2])),
    )


_startup_sanitize_stages: set[str] = set()
_startup_sanitize_sub: Any = None


def _stop_startup_sanitize() -> None:
    global _startup_sanitize_sub
    sub = _startup_sanitize_sub
    _startup_sanitize_sub = None
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass


def sanitize_startup_perspective_view(*, log_label: str = "startup") -> bool:
    """탑뷰/Play fly 가 Persp session xform 을 오염시킨 경우 1회 복구 (RemovePrim 금지)."""
    stage = _get_stage()
    if stage is None:
        return False
    stage_key = str(stage.GetRootLayer().identifier or "")
    if stage_key and stage_key in _startup_sanitize_stages:
        return False
    try:
        from .lam_viewport_overlay_state import get_toggle_top_view

        if bool(get_toggle_top_view()):
            return False
    except Exception:
        pass
    active = str(_active_camera_path_str() or "")
    if active and not _is_session_camera_path(active):
        return False
    if not _persp_view_needs_sanitize():
        if stage_key:
            _startup_sanitize_stages.add(stage_key)
        return False

    switch_to_perspective_viewport(restore_navigation=False)
    ok = False
    for action_id in ("frame_all", "frame_all_selection"):
        if _execute_viewport_action(action_id):
            ok = True
            break
    if not ok or _persp_view_needs_sanitize():
        snap = _default_persp_view_from_stage()
        if snap is not None:
            ok = bool(
                apply_camera_view(
                    snap,
                    up_xyz=(0.0, 0.0, 1.0),
                    camera_path=_PERSP_CAMERA_PATH,
                )
            )
    if stage_key:
        _startup_sanitize_stages.add(stage_key)
    print(
        f"{_PRINT_PREFIX} startup Persp sanitize ({log_label}) "
        f"session_override={_session_persp_has_authored_overrides()} ok={ok}",
        flush=True,
    )
    return bool(ok)


def schedule_startup_perspective_sanitize(*, delay_frames: int = 16) -> None:
    """stage 준비 후 1회 — 오염된 Persp 뷰만 복구 (post_update 루프·RemovePrim 없음)."""
    _stop_startup_sanitize()
    frames_left = [max(1, int(delay_frames))]

    def _tick(_e=None) -> None:
        if frames_left[0] > 0:
            frames_left[0] -= 1
            return
        _stop_startup_sanitize()
        sanitize_startup_perspective_view(log_label="startup")

    try:
        import omni.kit.app as _app  # type: ignore

        stream = _app.get_app().get_post_update_event_stream()
        global _startup_sanitize_sub
        _startup_sanitize_sub = stream.create_subscription_to_pop(
            _tick,
            name="morph.lam_control_1.startup_persp_sanitize",
        )
    except Exception:
        pass


def camera_prim_up_xyz(prim_path: str) -> Tuple[float, float, float]:
    """USD Camera prim 월드 up 벡터."""
    path = str(prim_path or "").strip()
    stage = _get_stage()
    if not stage or not path:
        return (0.0, 0.0, 1.0)
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return (0.0, 0.0, 1.0)
    try:
        xfc = UsdGeom.XformCache(Usd.TimeCode.Default())
        world = xfc.GetLocalToWorldTransform(prim)
        up = world.TransformDir(Gf.Vec3d(0.0, 1.0, 0.0))
        if up.GetLength() < 1e-9:
            return (0.0, 0.0, 1.0)
        up.Normalize()
        return (float(up[0]), float(up[1]), float(up[2]))
    except Exception:
        return (0.0, 0.0, 1.0)


def get_up_for_play_camera_target() -> Tuple[float, float, float]:
    if play_camera_use_preset_coords():
        try:
            from .lam_viewport_overlay_config import PLAY_CAMERA_PRESET  # type: ignore

            return tuple(float(x) for x in PLAY_CAMERA_PRESET.up_xyz)
        except Exception:
            return (0.0, 0.0, 1.0)
    return camera_prim_up_xyz(play_camera_prim_path())


def get_session_fly_up_xyz(
    *,
    top_view: bool = False,
    play: bool = False,
) -> Tuple[float, float, float]:
    """session Persp fly 보간 up.

    USD Camera prim local up 은 Persp 에 적용하면 바닥이 눕는 등 orientation 깨짐.
    Camera prim 모드 fly 는 world Z-up, preset 모드만 config up 사용.
    """
    if top_view and top_view_use_preset_coords():
        try:
            from .lam_viewport_overlay_config import TOP_VIEW_PRESET  # type: ignore

            return tuple(float(x) for x in TOP_VIEW_PRESET.up_xyz)
        except Exception:
            return (0.0, 0.0, 1.0)
    if play and play_camera_use_preset_coords():
        try:
            from .lam_viewport_overlay_config import PLAY_CAMERA_PRESET  # type: ignore

            return tuple(float(x) for x in PLAY_CAMERA_PRESET.up_xyz)
        except Exception:
            return (0.0, 0.0, 1.0)
    return (0.0, 0.0, 1.0)


def get_up_for_top_view_target() -> Tuple[float, float, float]:
    """탑뷰 preset 모드 apply 용 — Camera prim 모드는 ``get_session_fly_up_xyz`` 사용."""
    if top_view_use_preset_coords():
        try:
            from .lam_viewport_overlay_config import TOP_VIEW_PRESET  # type: ignore

            return tuple(float(x) for x in TOP_VIEW_PRESET.up_xyz)
        except Exception:
            return (0.0, 0.0, 1.0)
    return (0.0, 0.0, 1.0)


def apply_session_view_to_target(
    target: CameraViewSnapshot,
    *,
    up_xyz: Optional[Tuple[float, float, float]] = None,
    log_context: str = "",
) -> bool:
    """session Persp 카메라에 target eye/target 적용 (USD Camera bind 없이 동일 시점)."""
    ensure_session_perspective_camera(
        log_label=log_context or "apply_session_view",
        restore_navigation=False,
    )
    up = up_xyz if up_xyz is not None else (0.0, 0.0, 1.0)
    ok = bool(
        apply_camera_view(
            target,
            up_xyz=up,
            camera_path=_PERSP_CAMERA_PATH,
        )
    )
    tag = f" ({log_context})" if log_context else ""
    if ok:
        print(f"{_PRINT_PREFIX} session view 적용{tag}", flush=True)
    else:
        print(f"{_PRINT_PREFIX} session view 적용 실패{tag}", flush=True)
    return ok


def snapshot_from_prim_path(prim_path: str) -> Optional[CameraViewSnapshot]:
    """지정 Camera prim 의 eye/target (월드)."""
    path = str(prim_path or "").strip()
    stage = _get_stage()
    if not stage or not path:
        return None
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return None
    try:
        xfc = UsdGeom.XformCache(Usd.TimeCode.Default())
        world = xfc.GetLocalToWorldTransform(prim)
        eye = world.ExtractTranslation()
        coi = _read_coi_local(prim)
        dist = abs(float(coi[2]))
        if dist < 1e-6:
            dist = (Gf.Vec3d(0, 0, -1) * world).GetLength()
        if dist < 1e-6:
            dist = 500.0
        forward = world.TransformDir(Gf.Vec3d(0.0, 0.0, -1.0))
        if forward.GetLength() < 1e-9:
            forward = Gf.Vec3d(0.0, 0.0, -1.0)
        else:
            forward.Normalize()
        target = eye + forward * dist
        return CameraViewSnapshot(
            eye_xyz=(float(eye[0]), float(eye[1]), float(eye[2])),
            target_xyz=(float(target[0]), float(target[1]), float(target[2])),
        )
    except Exception:
        return None


def _active_camera_path_str() -> Optional[str]:
    try:
        from omni.kit.viewport.utility import get_active_viewport_camera_string  # type: ignore

        p = get_active_viewport_camera_string()
        return str(p) if p else None
    except Exception:
        return None


def _read_coi_local(cam_prim: Usd.Prim) -> Gf.Vec3d:
    attr = cam_prim.GetAttribute(_COI_ATTR)
    if attr and attr.IsValid():
        v = attr.Get(Usd.TimeCode.Default())
        if v is not None:
            return Gf.Vec3d(float(v[0]), float(v[1]), float(v[2]))
    return Gf.Vec3d(0.0, 0.0, -500.0)


def _snapshot_from_camera_prim() -> Optional[CameraViewSnapshot]:
    """Kit session 카메라 prim + COI — apply 와 동일 규칙."""
    stage = _get_stage()
    path = _active_camera_path_str()
    if not stage or not path:
        return None
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return None
    try:
        xfc = UsdGeom.XformCache(Usd.TimeCode.Default())
        world = xfc.GetLocalToWorldTransform(prim)
        eye = world.ExtractTranslation()
        coi = _read_coi_local(prim)
        dist = abs(float(coi[2]))
        if dist < 1e-6:
            dist = (Gf.Vec3d(0, 0, -1) * world).GetLength()
        if dist < 1e-6:
            dist = 500.0
        forward = world.TransformDir(Gf.Vec3d(0.0, 0.0, -1.0))
        if forward.GetLength() < 1e-9:
            forward = Gf.Vec3d(0.0, 0.0, -1.0)
        else:
            forward.Normalize()
        target = eye + forward * dist
        return CameraViewSnapshot(
            eye_xyz=(float(eye[0]), float(eye[1]), float(eye[2])),
            target_xyz=(float(target[0]), float(target[1]), float(target[2])),
        )
    except Exception:
        return None


def _snapshot_from_viewport_state() -> Optional[CameraViewSnapshot]:
    try:
        from omni.kit.viewport.utility.camera_state import ViewportCameraState  # type: ignore

        st = ViewportCameraState()
        eye = getattr(st, "position_world", None)
        if eye is None:
            return None
        ex = (float(eye[0]), float(eye[1]), float(eye[2]))
        tgt = None
        for name in (
            "target_world",
            "center_of_interest_world",
            "pivot_world",
            "interest_world",
        ):
            raw = getattr(st, name, None)
            if raw is not None:
                tgt = (float(raw[0]), float(raw[1]), float(raw[2]))
                break
        if tgt is None:
            return None
        return CameraViewSnapshot(eye_xyz=ex, target_xyz=tgt)
    except Exception:
        return None


def capture_current_view() -> Optional[CameraViewSnapshot]:
    """캡처·적용 모두 prim+COI 우선 (일관성)."""
    snap = _snapshot_from_camera_prim()
    if snap is not None:
        return snap
    return _snapshot_from_viewport_state()


def get_play_camera_preset() -> CameraViewSnapshot:
    from .lam_viewport_overlay_config import PLAY_CAMERA_PRESET  # type: ignore

    p = PLAY_CAMERA_PRESET
    return CameraViewSnapshot(
        eye_xyz=tuple(float(x) for x in p.eye_xyz),
        target_xyz=tuple(float(x) for x in p.target_xyz),
    )


def _snapshot_from_preset_spec(spec: Any) -> Optional[CameraViewSnapshot]:
    if spec is None:
        return None
    try:
        return CameraViewSnapshot(
            eye_xyz=tuple(float(x) for x in spec.eye_xyz),
            target_xyz=tuple(float(x) for x in spec.target_xyz),
        )
    except Exception:
        return None


def play_camera_prim_view_spec() -> Optional[CameraViewSnapshot]:
    """config PLAY_CAMERA_PRIM_VIEW (None 이면 prim 현재 상태 사용)."""
    try:
        from .lam_viewport_overlay_config import PLAY_CAMERA_PRIM_VIEW  # type: ignore
    except Exception:
        return None
    return _snapshot_from_preset_spec(PLAY_CAMERA_PRIM_VIEW)


def top_view_camera_prim_view_spec() -> Optional[CameraViewSnapshot]:
    """config TOP_VIEW_CAMERA_PRIM_VIEW (None 이면 prim 현재 상태 사용)."""
    try:
        from .lam_viewport_overlay_config import TOP_VIEW_CAMERA_PRIM_VIEW  # type: ignore
    except Exception:
        return None
    return _snapshot_from_preset_spec(TOP_VIEW_CAMERA_PRIM_VIEW)


def _up_from_preset_spec(spec: Any) -> Tuple[float, float, float]:
    if spec is None:
        return (0.0, 0.0, 1.0)
    try:
        return tuple(float(x) for x in spec.up_xyz)
    except Exception:
        return (0.0, 0.0, 1.0)


def _capture_world_up_from_active_camera() -> Tuple[float, float, float]:
    """활성 카메라 prim 월드 up (config up_xyz 캡처용)."""
    stage = _get_stage()
    path = _active_camera_path_str()
    if not stage or not path:
        return (0.0, 0.0, 1.0)
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return (0.0, 0.0, 1.0)
    try:
        xfc = UsdGeom.XformCache(Usd.TimeCode.Default())
        world = xfc.GetLocalToWorldTransform(prim)
        up = world.TransformDir(Gf.Vec3d(0.0, 1.0, 0.0))
        if up.GetLength() < 1e-9:
            return (0.0, 0.0, 1.0)
        up.Normalize()
        return (float(up[0]), float(up[1]), float(up[2]))
    except Exception:
        return (0.0, 0.0, 1.0)


def apply_view_to_camera_prim(
    prim_path: str,
    snap: CameraViewSnapshot,
    *,
    up_xyz: Optional[Tuple[float, float, float]] = None,
    log_context: str = "",
) -> bool:
    """USD Camera prim 에 eye/target 뷰 기록 (COI=거리 → 줌까지 고정).

    config 의 *_CAMERA_PRIM_VIEW 스펙을 진입 시마다 강제 적용해
    이전 세션/조작에서 남은 줌 상태가 이어지지 않게 한다.
    """
    path = str(prim_path or "").strip()
    if not path or _is_session_camera_path(path):
        return False
    stage = _get_stage()
    if not stage:
        return False
    cam_prim = stage.GetPrimAtPath(path)
    if not cam_prim or not cam_prim.IsValid():
        return False
    dist = (_vec3(snap.target_xyz) - _vec3(snap.eye_xyz)).GetLength()
    if dist < 1e-6:
        return False
    up_hint = tuple(float(x) for x in up_xyz) if up_xyz is not None else (0.0, 0.0, 1.0)
    up = _resolve_camera_up_vector(snap.eye_xyz, snap.target_xyz, up_hint)
    new_local = _camera_local_matrix(cam_prim, snap.eye_xyz, snap.target_xyz, up)
    ok = _write_prim_local_xform(
        path,
        new_local,
        coi=Gf.Vec3d(0.0, 0.0, -dist),
        force_reset_ops=True,
    )
    if ok:
        _invalidate_camera_prim_baseline(path)
    tag = f" ({log_context})" if log_context else ""
    print(
        f"{_PRINT_PREFIX} Camera prim 뷰 스펙 적용{tag} path={path!r} "
        f"dist={dist:.3f} up=({up[0]:.3f},{up[1]:.3f},{up[2]:.3f}) ok={ok}",
        flush=True,
    )
    return ok


def apply_play_camera_prim_view_spec() -> bool:
    """Play prim 모드 — config 스펙이 있으면 prim 에 뷰·줌 강제 (없으면 no-op)."""
    if play_camera_use_preset_coords():
        return False
    spec = play_camera_prim_view_spec()
    if spec is None:
        return False
    try:
        from .lam_viewport_overlay_config import PLAY_CAMERA_PRIM_VIEW  # type: ignore

        up = _up_from_preset_spec(PLAY_CAMERA_PRIM_VIEW)
    except Exception:
        up = (0.0, 0.0, 1.0)
    return apply_view_to_camera_prim(
        play_camera_prim_path(),
        spec,
        up_xyz=up,
        log_context="play_prim_view",
    )


def apply_top_view_camera_prim_view_spec() -> bool:
    """탑뷰 prim 모드 — config 스펙이 있으면 prim 에 뷰·줌 강제 (없으면 no-op)."""
    if top_view_use_preset_coords():
        return False
    spec = top_view_camera_prim_view_spec()
    if spec is None:
        return False
    try:
        from .lam_viewport_overlay_config import TOP_VIEW_CAMERA_PRIM_VIEW  # type: ignore

        up = _up_from_preset_spec(TOP_VIEW_CAMERA_PRIM_VIEW)
    except Exception:
        up = (0.0, 0.0, 1.0)
    return apply_view_to_camera_prim(
        top_view_camera_prim_path(),
        spec,
        up_xyz=up,
        log_context="top_view_prim_view",
    )


def get_play_camera_target_snapshot() -> Optional[CameraViewSnapshot]:
    if play_camera_use_preset_coords():
        return get_play_camera_preset()
    path = play_camera_prim_path()
    if not path:
        return None
    spec = play_camera_prim_view_spec()
    if spec is not None:
        return spec
    return get_camera_prim_baseline_view(path)


def get_top_view_target_snapshot() -> Optional[CameraViewSnapshot]:
    if top_view_use_preset_coords():
        from .lam_viewport_top_view import get_top_view_preset_snapshot  # type: ignore

        return get_top_view_preset_snapshot()
    path = top_view_camera_prim_path()
    if not path:
        return None
    spec = top_view_camera_prim_view_spec()
    if spec is not None:
        return spec
    return get_camera_prim_baseline_view(path)


def play_camera_target_configured() -> bool:
    try:
        from .lam_viewport_overlay_config import PLAY_CAMERA_PRESET_ENABLED  # type: ignore

        if not bool(PLAY_CAMERA_PRESET_ENABLED):
            return False
    except Exception:
        return False
    if play_camera_use_preset_coords():
        return play_camera_preset_configured()
    path = play_camera_prim_path()
    if not path:
        return False
    snap = play_camera_prim_view_spec()
    if snap is None:
        snap = snapshot_from_prim_path(path)
    if snap is None:
        snap = get_camera_prim_baseline_view(path)
    if snap is None:
        return False
    eye = _vec3(snap.eye_xyz)
    tgt = _vec3(snap.target_xyz)
    return (tgt - eye).GetLength() >= 0.25


def top_view_target_configured() -> bool:
    try:
        from .lam_viewport_overlay_config import TOP_VIEW_PRESET_ENABLED  # type: ignore

        if not bool(TOP_VIEW_PRESET_ENABLED):
            return False
    except Exception:
        return False
    if top_view_use_preset_coords():
        from .lam_viewport_top_view import top_view_preset_configured  # type: ignore

        return bool(top_view_preset_configured())
    path = top_view_camera_prim_path()
    if not path:
        return False
    snap = top_view_camera_prim_view_spec()
    if snap is None:
        snap = snapshot_from_prim_path(path)
    if snap is None:
        snap = get_camera_prim_baseline_view(path)
    if snap is None:
        return False
    eye = _vec3(snap.eye_xyz)
    tgt = _vec3(snap.target_xyz)
    return (tgt - eye).GetLength() >= 0.25


def play_camera_preset_configured() -> bool:
    try:
        from .lam_viewport_overlay_config import PLAY_CAMERA_PRESET_ENABLED  # type: ignore

        if not bool(PLAY_CAMERA_PRESET_ENABLED):
            return False
    except Exception:
        return False
    preset = get_play_camera_preset()
    eye = _vec3(preset.eye_xyz)
    tgt = _vec3(preset.target_xyz)
    if (tgt - eye).GetLength() < 0.25:
        return False
    return True


def play_camera_fly_duration_sec() -> float:
    try:
        from .lam_viewport_overlay_config import PLAY_CAMERA_FLY_DURATION_SEC  # type: ignore

        return max(0.05, float(PLAY_CAMERA_FLY_DURATION_SEC))
    except Exception:
        return 0.6


def play_camera_position_epsilon_m() -> float:
    try:
        from .lam_viewport_overlay_config import PLAY_CAMERA_FLY_POSITION_EPS_M  # type: ignore

        return max(1e-6, float(PLAY_CAMERA_FLY_POSITION_EPS_M))
    except Exception:
        return 0.05


def views_are_close(
    a: CameraViewSnapshot,
    b: CameraViewSnapshot,
    *,
    pos_eps_m: Optional[float] = None,
) -> bool:
    eps = play_camera_position_epsilon_m() if pos_eps_m is None else float(pos_eps_m)
    if (_vec3(a.eye_xyz) - _vec3(b.eye_xyz)).GetLength() > eps:
        return False
    if (_vec3(a.target_xyz) - _vec3(b.target_xyz)).GetLength() > eps:
        return False
    da = _vec3(a.target_xyz) - _vec3(a.eye_xyz)
    db = _vec3(b.target_xyz) - _vec3(b.eye_xyz)
    if da.GetLength() < 1e-9 or db.GetLength() < 1e-9:
        return True
    da.Normalize()
    db.Normalize()
    dot = float(da * db)
    dot = max(-1.0, min(1.0, dot))
    try:
        from .lam_viewport_overlay_config import PLAY_CAMERA_FLY_DIRECTION_EPS_DEG  # type: ignore

        deg = float(PLAY_CAMERA_FLY_DIRECTION_EPS_DEG)
    except Exception:
        deg = 1.0
    return dot >= math.cos(math.radians(max(0.0, deg)))


def format_config_snippet(
    snap: CameraViewSnapshot,
    *,
    up_xyz: Tuple[float, float, float] = (0.0, 0.0, 1.0),
) -> str:
    e = snap.eye_xyz
    t = snap.target_xyz
    u = up_xyz
    coords = (
        f"    eye_xyz=({e[0]:.6f}, {e[1]:.6f}, {e[2]:.6f}),\n"
        f"    target_xyz=({t[0]:.6f}, {t[1]:.6f}, {t[2]:.6f}),\n"
        f"    up_xyz=({u[0]:.6f}, {u[1]:.6f}, {u[2]:.6f}),\n"
    )
    return (
        "# lam_viewport_overlay_config.py 에 붙여넣기\n"
        "PLAY_CAMERA_PRESET_ENABLED = True\n"
        "PLAY_CAMERA_PRESET = PlayCameraPresetSpec(\n"
        f"{coords}"
        ")\n\n"
        "# 탑뷰 보기 — TOP_VIEW_PRESET 에 붙여넣기\n"
        "TOP_VIEW_PRESET_ENABLED = True\n"
        "TOP_VIEW_PRESET = PlayCameraPresetSpec(\n"
        f"{coords}"
        ")\n\n"
        "# Camera prim 모드(USE_PRESET_COORDS=False) — Play 시점 줌 고정\n"
        "PLAY_CAMERA_PRIM_VIEW = PlayCameraPresetSpec(\n"
        f"{coords}"
        ")\n\n"
        "# Camera prim 모드 — 탑뷰 줌 고정 (TOP_VIEW_CAMERA_PRIM_VIEW 에 붙여넣기)\n"
        "TOP_VIEW_CAMERA_PRIM_VIEW = PlayCameraPresetSpec(\n"
        f"{coords}"
        ")\n"
    )


def log_play_camera_preset_capture() -> bool:
    """현재 뷰포트 시점 캡처 → 콘솔에 config 조각 출력 (UI 버튼용)."""
    snap = capture_current_view()
    if snap is None:
        print(
            f"{_PRINT_PREFIX} 캡처 실패 — 활성 뷰포트·카메라·stage 확인",
            flush=True,
        )
        return False
    print(
        f"{_PRINT_PREFIX} 현재 뷰 캡처 (eye→target 거리 "
        f"{(_vec3(snap.target_xyz) - _vec3(snap.eye_xyz)).GetLength():.3f} m):\n"
        f"{format_config_snippet(snap, up_xyz=_capture_world_up_from_active_camera())}",
        flush=True,
    )
    return True


def _camera_world_from_eye_target(
    eye: Tuple[float, float, float],
    target: Tuple[float, float, float],
    up: Tuple[float, float, float],
) -> Gf.Matrix4d:
    """Kit 카메라 월드 행렬 (local -Z → target).

    SetLookAt 은 forward/up 평행 시 특이 행렬을 반환할 수 있어
    항상 직교 기저를 직접 구성한다.
    """
    fwd = _vec3(target) - _vec3(eye)
    if fwd.GetLength() < 1e-9:
        return Gf.Matrix4d(1.0)
    fwd.Normalize()
    up_resolved = _resolve_camera_up_vector(eye, target, up)
    up_v = _vec3(up_resolved)
    right = Gf.Cross(fwd, up_v)
    if right.GetLength() < 1e-9:
        return Gf.Matrix4d(1.0)
    right.Normalize()
    up_c = Gf.Cross(right, fwd)
    up_c.Normalize()
    m = Gf.Matrix4d(1.0)
    m.SetRow(0, Gf.Vec4d(right[0], right[1], right[2], 0.0))
    m.SetRow(1, Gf.Vec4d(up_c[0], up_c[1], up_c[2], 0.0))
    m.SetRow(2, Gf.Vec4d(-fwd[0], -fwd[1], -fwd[2], 0.0))
    m.SetRow(3, Gf.Vec4d(eye[0], eye[1], eye[2], 1.0))
    return m


def _camera_local_matrix(
    cam_prim: Usd.Prim,
    eye: Tuple[float, float, float],
    target: Tuple[float, float, float],
    up: Tuple[float, float, float],
) -> Gf.Matrix4d:
    world = _camera_world_from_eye_target(eye, target, up)
    parent = cam_prim.GetParent()
    if parent and parent.IsValid() and not parent.IsPseudoRoot():
        px = UsdGeom.Xformable(parent).ComputeParentToWorldTransform(Usd.TimeCode.Default())
        return px.GetInverse() * world
    return world


def apply_camera_view(
    snap: CameraViewSnapshot,
    *,
    up_xyz: Optional[Tuple[float, float, float]] = None,
    camera_path: Optional[str] = None,
) -> bool:
    stage = _get_stage()
    path = str(camera_path or _active_camera_path_str() or "").strip()
    if not stage or not path:
        return False
    if not _is_session_camera_path(path):
        path = _PERSP_CAMERA_PATH
    cam_prim = stage.GetPrimAtPath(path)
    if not cam_prim or not cam_prim.IsValid():
        return False
    if up_xyz is not None:
        up = tuple(float(x) for x in up_xyz)
    else:
        try:
            from .lam_viewport_overlay_config import PLAY_CAMERA_PRESET  # type: ignore

            up = tuple(float(x) for x in PLAY_CAMERA_PRESET.up_xyz)
        except Exception:
            up = (0.0, 0.0, 1.0)
    up = _resolve_camera_up_vector(snap.eye_xyz, snap.target_xyz, up)

    dist = (_vec3(snap.target_xyz) - _vec3(snap.eye_xyz)).GetLength()
    if dist < 1e-6:
        return False
    coi_val = Gf.Vec3d(0.0, 0.0, -float(dist))
    new_local = _camera_local_matrix(cam_prim, snap.eye_xyz, snap.target_xyz, up)
    return _write_prim_local_xform(path, new_local, coi=coi_val)


def _apply_fly_frame_view(
    snap: CameraViewSnapshot,
    *,
    up_xyz: Tuple[float, float, float],
    fly_camera_path: str = "",
) -> bool:
    """fly 한 프레임 — session Persp 또는 USD Camera prim."""
    path = str(fly_camera_path or "").strip()
    if path and not _is_session_camera_path(path):
        return bool(apply_view_to_camera_prim(path, snap, up_xyz=up_xyz))
    return bool(
        apply_camera_view(
            snap,
            up_xyz=up_xyz,
            camera_path=path or _PERSP_CAMERA_PATH,
        )
    )


def _start_fly_animation(
    start: CameraViewSnapshot,
    end: CameraViewSnapshot,
    done: threading.Event,
    *,
    on_complete: Optional[Any] = None,
    up_xyz: Optional[Tuple[float, float, float]] = None,
    usd_context_name: str = "",
    fly_camera_path: str = "",
) -> None:
    """main 스레드에서만 호출 — update 구독만 걸고 즉시 반환 (메인 wait 금지)."""
    dur = play_camera_fly_duration_sec()
    t0 = time.perf_counter()
    sub_box: List[Any] = [None]
    up = up_xyz if up_xyz is not None else (0.0, 0.0, 1.0)
    ctx = str(usd_context_name or "").strip()
    fly_path = str(fly_camera_path or "").strip()

    def _finish() -> None:
        try:
            if sub_box[0] is not None:
                sub_box[0].unsubscribe()
        except Exception:
            pass
        sub_box[0] = None
        if callable(on_complete):
            try:
                on_complete()
            except Exception:
                pass
        done.set()

    def _tick(_event) -> None:
        with camera_fly_usd_context(ctx or None):
            elapsed = time.perf_counter() - t0
            u = _smoothstep01(elapsed / dur) if dur > 1e-9 else 1.0
            eye = _lerp3(start.eye_xyz, end.eye_xyz, u)
            tgt = _lerp3(start.target_xyz, end.target_xyz, u)
            _apply_fly_frame_view(
                CameraViewSnapshot(eye_xyz=eye, target_xyz=tgt),
                up_xyz=up,
                fly_camera_path=fly_path,
            )
            if u >= 1.0 - 1e-9:
                _apply_fly_frame_view(end, up_xyz=up, fly_camera_path=fly_path)
                _finish()

    try:
        import omni.kit.app as _app  # type: ignore

        stream = _app.get_app().get_update_event_stream()
        sub_box[0] = stream.create_subscription_to_pop(
            _tick,
            name="morph.lam_control_1:play_camera_fly",
        )
        with camera_fly_usd_context(ctx or None):
            _apply_fly_frame_view(
                CameraViewSnapshot(
                    eye_xyz=start.eye_xyz,
                    target_xyz=start.target_xyz,
                ),
                up_xyz=up,
                fly_camera_path=fly_path,
            )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} update subscribe failed: {exc}", flush=True)
        with camera_fly_usd_context(ctx or None):
            _apply_fly_frame_view(end, up_xyz=up, fly_camera_path=fly_path)
        _finish()


def kickoff_fly_to_target(
    target: CameraViewSnapshot,
    done: threading.Event,
    *,
    assign_prim_path: str = "",
    up_xyz: Optional[Tuple[float, float, float]] = None,
    log_context: str = "",
    viewport_api: Any = None,
    usd_context_name: str = "",
) -> bool:
    """main 스레드에서 fly 시작만 걸고 즉시 반환. 완료는 ``done``."""
    if done is None:
        raise ValueError("done event required")
    up = up_xyz if up_xyz is not None else (0.0, 0.0, 1.0)
    tag = log_context or "fly"
    assign_path = str(assign_prim_path or "").strip()
    ctx = str(usd_context_name or "").strip()
    with camera_fly_usd_context(ctx or None):
        if viewport_api is not None:
            current = capture_view_for_viewport(viewport_api, ctx)
        else:
            current = capture_current_view()
        if viewport_api is not None:
            restore_perspective_on_viewport(viewport_api, ctx)
        else:
            ensure_session_perspective_camera(log_label=tag, restore_navigation=False)
        if current is not None:
            apply_camera_view(
                current,
                up_xyz=up,
                camera_path=_PERSP_CAMERA_PATH,
            )
        if current is None:
            ok = _finish_fly_to_target(
                target,
                up_xyz=up,
                assign_prim_path=assign_prim_path,
                log_context=f"{tag}_direct",
                viewport_api=viewport_api,
                usd_context_name=ctx,
            )
            done.set()
            return ok
        if views_are_close(current, target):
            print(f"{_PRINT_PREFIX} 현재 뷰 ≈ target — fly 생략, camera/view 동기화", flush=True)
            _finish_fly_to_target(
                target,
                up_xyz=up,
                assign_prim_path=assign_prim_path,
                log_context=f"{tag}_sync",
                viewport_api=viewport_api,
                usd_context_name=ctx,
            )
            done.set()
            return True

        def _complete() -> None:
            _finish_fly_to_target(
                target,
                up_xyz=up,
                assign_prim_path=assign_prim_path,
                log_context=f"{tag}_fly_end",
                viewport_api=viewport_api,
                usd_context_name=ctx,
            )

        print(
            f"{_PRINT_PREFIX} fly 시작 ({play_camera_fly_duration_sec():.2f}s) "
            f"context={tag}"
            f"{' bind=' + assign_prim_path if assign_prim_path else ' session_persp'}",
            flush=True,
        )
        _start_fly_animation(
            current,
            target,
            done,
            on_complete=_complete,
            up_xyz=up,
            usd_context_name=ctx,
        )
        return True


def run_sync_fly_to_target(
    target: CameraViewSnapshot,
    *,
    assign_prim_path: str = "",
) -> bool:
    """background 스레드 전용 동기 fly — main(UI) 스레드에서는 사용 금지."""
    if threading.current_thread() is threading.main_thread():
        print(
            f"{_PRINT_PREFIX} run_sync_fly_to_target 는 UI main 스레드에서 "
            "호출할 수 없습니다 — kickoff_fly_to_target 사용",
            flush=True,
        )
        return False
    done = threading.Event()
    fly_started = False

    def _on_main() -> None:
        nonlocal fly_started
        fly_started = kickoff_fly_to_target(
            target,
            done,
            assign_prim_path=assign_prim_path,
        )

    try:
        from .lam_sequence_engine import _dispatch_main_wait  # type: ignore

        if not _dispatch_main_wait(_on_main, timeout=15.0):
            return False
    except Exception:
        return False
    if not fly_started:
        return False
    wait_sec = play_camera_fly_duration_sec() + 8.0
    return bool(done.wait(timeout=wait_sec))


def restore_perspective_after_play_camera_mode() -> None:
    """PLAY_CAMERA_USE_PRESET_COORDS=False 일 때 정지 후 Perspective 복귀."""
    stop_play_camera_baseline_hold()
    if play_camera_use_preset_coords():
        return

    def _go() -> None:
        restore_kit_default_perspective(log_label="play_stop_reset")
        try:
            from .lam_viewport_top_view import restore_viewport_camera_navigation

            restore_viewport_camera_navigation(schedule_frames=8)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} navigation restore after perspective: {exc}",
                flush=True,
            )

    try:
        from .lam_sequence_engine import _dispatch_main_wait  # type: ignore

        _dispatch_main_wait(_go, timeout=5.0)
    except Exception:
        try:
            switch_to_perspective_viewport()
        except Exception:
            pass


def will_run_play_camera_fly() -> bool:
    try:
        from .lam_viewport_overlay_state import get_toggle_play_camera_fly  # type: ignore
    except Exception:
        return False
    return bool(get_toggle_play_camera_fly()) and play_camera_target_configured()


def planned_camera_fly_duration_sec() -> float:
    """스케줄용 — fly 가 켜져 있으면 config duration (실제 스킵 시에도 동일)."""
    if not will_run_play_camera_fly():
        return 0.0
    return play_camera_fly_duration_sec()


def kickoff_play_camera_fly(done: threading.Event) -> bool:
    """Play worker — main 에 fly 시작만 걸고 즉시 반환. 완료는 ``done``."""
    if done is None:
        raise ValueError("done event required")
    if not will_run_play_camera_fly():
        done.set()
        return False

    target = get_play_camera_target_snapshot()
    if target is None:
        done.set()
        return False
    fly_started = False
    err: List[Optional[BaseException]] = [None]
    use_prim = not play_camera_use_preset_coords()
    prim_path = play_assign_prim_path()
    up = get_session_fly_up_xyz(play=True)

    def _kickoff_on_main() -> None:
        nonlocal fly_started
        try:
            current = capture_current_view()
            if use_prim and prim_path:
                apply_play_camera_prim_view_spec()
            ensure_session_perspective_camera(
                log_label="play_fly",
                restore_navigation=False,
            )
            if current is not None:
                apply_camera_view(
                    current,
                    up_xyz=up,
                    camera_path=_PERSP_CAMERA_PATH,
                )
            if current is None:
                print(
                    f"{_PRINT_PREFIX} 현재 뷰 읽기 실패 — target 직접 적용 시도",
                    flush=True,
                )
                if _finish_fly_to_target(
                    target,
                    up_xyz=up,
                    assign_prim_path=prim_path,
                    log_context="play_no_current",
                ):
                    fly_started = True
                    done.set()
                return
            if views_are_close(current, target):
                print(
                    f"{_PRINT_PREFIX} 현재 뷰 ≈ target — fly 생략, camera/view 동기화",
                    flush=True,
                )
                _finish_fly_to_target(
                    target,
                    up_xyz=up,
                    assign_prim_path=prim_path,
                    log_context="play_sync",
                )
                fly_started = True
                done.set()
                return
            print(
                f"{_PRINT_PREFIX} fly 시작 "
                f"({play_camera_fly_duration_sec():.2f}s)"
                f"{' bind=' + prim_path if prim_path else ' session_persp'}",
                flush=True,
            )
            fly_started = True

            def _complete() -> None:
                _finish_fly_to_target(
                    target,
                    up_xyz=up,
                    assign_prim_path=prim_path,
                    log_context="play_fly_end",
                )

            _start_fly_animation(
                current,
                target,
                done,
                on_complete=_complete,
                up_xyz=up,
            )
        except BaseException as e:
            err[0] = e
            raise
        finally:
            if not fly_started:
                done.set()

    try:
        from .lam_sequence_engine import _dispatch_main_wait  # type: ignore

        if not _dispatch_main_wait(_kickoff_on_main, timeout=5.0):
            print(f"{_PRINT_PREFIX} fly kickoff timeout", flush=True)
            done.set()
            return False
    except Exception as exc:
        print(f"{_PRINT_PREFIX} fly kickoff failed: {exc}", flush=True)
        done.set()
        return False
    if err[0] is not None:
        print(f"{_PRINT_PREFIX} fly error: {err[0]}", flush=True)
    return bool(fly_started)


def kickoff_play_camera_fly_for_screen(
    done: threading.Event,
    *,
    viewport_api: Any,
    usd_context_name: str,
) -> bool:
    """화면2+ Play — 전역 토글/active Viewport 무시, 지정 타일·context 만 fly."""
    if done is None:
        raise ValueError("done event required")
    if viewport_api is None:
        done.set()
        return False
    if not play_camera_target_configured():
        done.set()
        return False

    ctx = str(usd_context_name or "").strip()
    target = None
    with camera_fly_usd_context(ctx or None):
        target = get_play_camera_target_snapshot()
    if target is None:
        done.set()
        return False

    fly_started = False
    err: List[Optional[BaseException]] = [None]
    use_prim = not play_camera_use_preset_coords()
    prim_path = play_assign_prim_path()
    up = get_session_fly_up_xyz(play=True)

    def _kickoff_on_main() -> None:
        nonlocal fly_started
        try:
            # 화면1 과 동일: 현재 뷰를 먼저 캡처한 뒤 target 준비.
            # Camera prim 모드(화면2+): aux 에 Persp 가 없을 수 있어
            # /Camera 에 시작점을 쓰고 look-through 한 채 fly (Persp 우회).
            with camera_fly_usd_context(ctx or None):
                current = capture_view_for_viewport(viewport_api, ctx)
                if current is None:
                    print(
                        f"{_PRINT_PREFIX} screen 현재 뷰 읽기 실패 — target 직접 적용",
                        flush=True,
                    )
                    if use_prim and prim_path:
                        apply_play_camera_prim_view_spec()
                    if _finish_fly_to_target(
                        target,
                        up_xyz=up,
                        assign_prim_path=prim_path,
                        log_context="play_screen_no_current",
                        viewport_api=viewport_api,
                        usd_context_name=ctx,
                    ):
                        fly_started = True
                        done.set()
                    return
                if views_are_close(current, target):
                    print(
                        f"{_PRINT_PREFIX} screen 현재 뷰 ≈ target — fly 생략, 동기화 "
                        f"ctx={ctx!r}",
                        flush=True,
                    )
                    if use_prim and prim_path:
                        apply_play_camera_prim_view_spec()
                    _finish_fly_to_target(
                        target,
                        up_xyz=up,
                        assign_prim_path=prim_path,
                        log_context="play_screen_sync",
                        viewport_api=viewport_api,
                        usd_context_name=ctx,
                    )
                    fly_started = True
                    done.set()
                    return

                fly_path = ""
                if use_prim and prim_path:
                    # 시작점을 Camera 에 쓰고 look-through — fly 중 시야가 보간됨
                    ensure_camera_prim_baseline(prim_path)
                    apply_view_to_camera_prim(prim_path, current, up_xyz=up)
                    if not set_viewport_camera_prim_path_on_api(
                        viewport_api,
                        prim_path,
                        usd_context_name=ctx,
                    ):
                        print(
                            f"{_PRINT_PREFIX} screen Camera look-through 실패 "
                            f"path={prim_path!r} ctx={ctx!r}",
                            flush=True,
                        )
                    fly_path = prim_path
                else:
                    restore_perspective_on_viewport(viewport_api, ctx)
                    if not apply_camera_view(
                        current,
                        up_xyz=up,
                        camera_path=_PERSP_CAMERA_PATH,
                    ):
                        print(
                            f"{_PRINT_PREFIX} screen fly Persp 시작점 적용 실패 "
                            f"ctx={ctx!r}",
                            flush=True,
                        )
                    fly_path = _PERSP_CAMERA_PATH

                print(
                    f"{_PRINT_PREFIX} screen fly 시작 "
                    f"ctx={ctx!r} ({play_camera_fly_duration_sec():.2f}s)"
                    f"{' bind=' + prim_path if prim_path else ' session_persp'}",
                    flush=True,
                )
                fly_started = True

                def _complete() -> None:
                    if use_prim and prim_path:
                        apply_play_camera_prim_view_spec()
                    _finish_fly_to_target(
                        target,
                        up_xyz=up,
                        assign_prim_path=prim_path,
                        log_context="play_screen_fly_end",
                        viewport_api=viewport_api,
                        usd_context_name=ctx,
                    )

                _start_fly_animation(
                    current,
                    target,
                    done,
                    on_complete=_complete,
                    up_xyz=up,
                    usd_context_name=ctx,
                    fly_camera_path=fly_path,
                )
        except BaseException as e:
            err[0] = e
            raise
        finally:
            if not fly_started:
                done.set()

    try:
        from .lam_sequence_engine import _dispatch_main_wait  # type: ignore

        if not _dispatch_main_wait(_kickoff_on_main, timeout=5.0):
            print(f"{_PRINT_PREFIX} screen fly kickoff timeout", flush=True)
            done.set()
            return False
    except Exception as exc:
        print(f"{_PRINT_PREFIX} screen fly kickoff failed: {exc}", flush=True)
        done.set()
        return False
    if err[0] is not None:
        print(f"{_PRINT_PREFIX} screen fly error: {err[0]}", flush=True)
    return bool(fly_started)


def run_play_camera_fly_before_start() -> None:
    """Play worker — fly 완료까지 대기 (레거시·단독 호출용)."""
    if not will_run_play_camera_fly():
        return
    done = threading.Event()
    kicked = kickoff_play_camera_fly(done)
    if not kicked:
        return
    wait_sec = play_camera_fly_duration_sec() + 8.0
    if not done.wait(timeout=wait_sec):
        print(
            f"{_PRINT_PREFIX} fly wait timeout ({wait_sec:.1f}s) — 재생 계속",
            flush=True,
        )
    else:
        print(f"{_PRINT_PREFIX} fly 완료", flush=True)


__all__ = [
    "CameraPrimBaseline",
    "CameraViewSnapshot",
    "apply_camera_view",
    "apply_play_camera_prim_view_spec",
    "apply_session_view_to_target",
    "apply_top_view_camera_prim_view_spec",
    "apply_view_to_camera_prim",
    "camera_prim_up_xyz",
    "bind_viewport_to_camera_prim",
    "camera_fly_usd_context",
    "capture_current_view",
    "ensure_camera_prim_baseline",
    "format_config_snippet",
    "get_camera_prim_baseline_view",
    "get_session_fly_up_xyz",
    "get_up_for_play_camera_target",
    "get_up_for_top_view_target",
    "get_play_camera_target_snapshot",
    "get_top_view_target_snapshot",
    "log_play_camera_preset_capture",
    "play_camera_fly_duration_sec",
    "play_camera_preset_configured",
    "play_camera_prim_view_spec",
    "play_camera_target_configured",
    "play_assign_prim_path",
    "play_camera_bind_viewport_to_usd_prim",
    "play_camera_use_preset_coords",
    "kickoff_fly_to_target",
    "kickoff_play_camera_fly",
    "kickoff_play_camera_fly_for_screen",
    "planned_camera_fly_duration_sec",
    "restore_camera_prim_baseline",
    "restore_kit_default_perspective",
    "restore_perspective_after_play_camera_mode",
    "restore_perspective_on_viewport",
    "run_play_camera_fly_before_start",
    "run_sync_fly_to_target",
    "sanitize_startup_perspective_view",
    "schedule_startup_perspective_sanitize",
    "set_viewport_camera_prim_path",
    "set_viewport_camera_prim_path_on_api",
    "ensure_session_perspective_camera",
    "snapshot_from_prim_path",
    "capture_view_for_viewport",
    "stop_play_camera_baseline_hold",
    "switch_to_perspective_viewport",
    "top_view_assign_prim_path",
    "top_view_camera_prim_path",
    "top_view_camera_prim_view_spec",
    "top_view_target_configured",
    "top_view_use_preset_coords",
    "views_are_close",
    "will_run_play_camera_fly",
]
