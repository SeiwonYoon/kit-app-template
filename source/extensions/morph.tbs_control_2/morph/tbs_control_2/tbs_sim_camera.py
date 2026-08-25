"""TBS 시뮬 시작 카메라 — ``sim_control_defaults`` + EBS HUD「뷰 저장」.

워크플로 (LAM play-camera 와 동일):
1) 「뷰 저장」 → 현재 뷰를 콘솔에 config 스니펫으로 출력 (즉시 적용 아님)
2) ``SIM_CAMERA_VIEW`` / ``SIM_CAMERA_PRIM_PATH`` 에 붙여넣고
   ``SIM_CAMERA_MODE_ENABLED = True``
3) 시뮬 시작 시 해당 카메라·뷰로 맞춘 뒤 시뮬 진행
   (False 면 Perspective 로 시작)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom  # type: ignore

_PRINT_PREFIX = "[TBS/SimCamera]"
_PERSP_CAMERA_PATH = "/OmniverseKit_Persp"
_COI_ATTR = "omni:kit:centerOfInterest"


@dataclass(frozen=True)
class CameraViewSnapshot:
    eye_xyz: Tuple[float, float, float]
    target_xyz: Tuple[float, float, float]


def _vec3(t: Tuple[float, float, float]) -> Gf.Vec3d:
    return Gf.Vec3d(float(t[0]), float(t[1]), float(t[2]))


def _is_session_camera_path(path: str) -> bool:
    p = str(path or "").strip()
    return not p or p == _PERSP_CAMERA_PATH or "Persp" in p


def _get_stage() -> Any:
    try:
        import omni.usd as ou  # type: ignore

        ctx = ou.get_context()
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
    for win_name in ("Viewport", "TBS_SimSplit_1", "TBS_SimSplit_2", "TBS_SimSplit_3"):
        try:
            from omni.kit.viewport.utility import get_viewport_from_window_name  # type: ignore

            _add(get_viewport_from_window_name(str(win_name)))
        except Exception:
            pass
    return apis


def _active_camera_path_str() -> str:
    api = _get_active_viewport_api()
    if api is None:
        return ""
    try:
        p = getattr(api, "camera_path", None)
        if p is None:
            return ""
        return str(p).strip()
    except Exception:
        return ""


def _resolve_camera_up_vector(
    eye: Tuple[float, float, float],
    target: Tuple[float, float, float],
    up_hint: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    fwd = _vec3(target) - _vec3(eye)
    if fwd.GetLength() < 1e-9:
        return (0.0, 0.0, 1.0)
    fwd.Normalize()
    up_v = _vec3(up_hint)
    if up_v.GetLength() < 1e-9:
        up_v = Gf.Vec3d(0.0, 0.0, 1.0)
    else:
        up_v.Normalize()
    if abs(Gf.Dot(fwd, up_v)) > 0.999:
        # forward 와 평행하면 대체 up
        alt = Gf.Vec3d(0.0, 0.0, 1.0)
        if abs(Gf.Dot(fwd, alt)) > 0.999:
            alt = Gf.Vec3d(0.0, 1.0, 0.0)
        up_v = alt
    return (float(up_v[0]), float(up_v[1]), float(up_v[2]))


def _camera_world_from_eye_target(
    eye: Tuple[float, float, float],
    target: Tuple[float, float, float],
    up: Tuple[float, float, float],
) -> Gf.Matrix4d:
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
    m.SetRow(3, Gf.Vec4d(float(eye[0]), float(eye[1]), float(eye[2]), 1.0))
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
        px = UsdGeom.Xformable(parent).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        return px.GetInverse() * world
    return world


def _write_prim_local_xform(
    prim_path: str,
    local_matrix: Gf.Matrix4d,
    *,
    coi: Optional[Gf.Vec3d] = None,
) -> bool:
    stage = _get_stage()
    path = str(prim_path or "").strip()
    if not stage or not path:
        return False
    cam_prim = stage.GetPrimAtPath(path)
    if not cam_prim or not cam_prim.IsValid():
        return False
    try:
        edit = Usd.EditContext(stage, Usd.EditTarget(stage.GetSessionLayer()))
        with edit:
            xformable = UsdGeom.Xformable(cam_prim)
            xformable.ClearXformOpOrder()
            op = xformable.AddTransformOp()
            op.Set(local_matrix)
            if coi is not None:
                coi_attr = cam_prim.GetAttribute(_COI_ATTR)
                if not coi_attr or not coi_attr.IsValid():
                    cam_prim.CreateAttribute(
                        _COI_ATTR, Sdf.ValueTypeNames.Vector3d, custom=True
                    ).Set(coi)
                else:
                    coi_attr.Set(coi)
        return True
    except Exception as exc:
        print(f"{_PRINT_PREFIX} USD xform set failed: {exc}", flush=True)
        return False


def _read_coi_local(prim: Usd.Prim) -> Gf.Vec3d:
    try:
        attr = prim.GetAttribute(_COI_ATTR)
        if attr and attr.IsValid():
            v = attr.Get()
            if v is not None:
                return Gf.Vec3d(float(v[0]), float(v[1]), float(v[2]))
    except Exception:
        pass
    return Gf.Vec3d(0.0, 0.0, -500.0)


def _snapshot_from_camera_prim(camera_path: str = "") -> Optional[CameraViewSnapshot]:
    stage = _get_stage()
    path = str(camera_path or "").strip() or _active_camera_path_str()
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


def _snapshot_from_viewport_state(viewport_api: Any = None) -> Optional[CameraViewSnapshot]:
    try:
        from omni.kit.viewport.utility.camera_state import ViewportCameraState  # type: ignore

        st = None
        if viewport_api is not None:
            try:
                st = ViewportCameraState(viewport_api)
            except Exception:
                st = None
        if st is None:
            st = ViewportCameraState()
        if st is None:
            return None
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
    snap = _snapshot_from_viewport_state(_get_active_viewport_api())
    if snap is not None:
        return snap
    return _snapshot_from_camera_prim()


def _capture_world_up() -> Tuple[float, float, float]:
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


def format_config_snippet(
    snap: CameraViewSnapshot,
    *,
    up_xyz: Tuple[float, float, float] = (0.0, 0.0, 1.0),
) -> str:
    e = snap.eye_xyz
    t = snap.target_xyz
    u = up_xyz
    return (
        "# sim_control_defaults.py 에 붙여넣기\n"
        "SIM_CAMERA_MODE_ENABLED = True\n"
        "# SIM_CAMERA_PRIM_PATH = \"/Camera\"  # stage 실제 Camera prim 경로\n"
        "SIM_CAMERA_VIEW = SimCameraViewSpec(\n"
        f"    eye_xyz=({e[0]:.6f}, {e[1]:.6f}, {e[2]:.6f}),\n"
        f"    target_xyz=({t[0]:.6f}, {t[1]:.6f}, {t[2]:.6f}),\n"
        f"    up_xyz=({u[0]:.6f}, {u[1]:.6f}, {u[2]:.6f}),\n"
        ")\n"
    )


def log_sim_camera_view_capture() -> bool:
    """현재 뷰포트 시점 캡처 → 콘솔에 config 조각 출력 (즉시 적용 없음)."""
    snap = capture_current_view()
    if snap is None:
        print(f"{_PRINT_PREFIX} 캡처 실패 — 활성 뷰포트·카메라·stage 확인", flush=True)
        return False
    dist = (_vec3(snap.target_xyz) - _vec3(snap.eye_xyz)).GetLength()
    print(
        f"{_PRINT_PREFIX} 현재 뷰 캡처 (eye→target 거리 {dist:.3f}):\n"
        f"{format_config_snippet(snap, up_xyz=_capture_world_up())}",
        flush=True,
    )
    return True


def set_viewport_camera_prim_path(prim_path: str) -> bool:
    path = str(prim_path or "").strip()
    if not path:
        return False
    ok = False
    for api in _iter_viewport_apis():
        try:
            api.camera_path = Sdf.Path(path)
            ok = True
        except Exception:
            try:
                api.camera_path = path
                ok = True
            except Exception:
                pass
    return ok


def apply_view_to_camera_prim(
    prim_path: str,
    snap: CameraViewSnapshot,
    *,
    up_xyz: Tuple[float, float, float] = (0.0, 0.0, 1.0),
) -> bool:
    path = str(prim_path or "").strip()
    if not path or _is_session_camera_path(path):
        return False
    stage = _get_stage()
    if not stage:
        return False
    cam_prim = stage.GetPrimAtPath(path)
    if not cam_prim or not cam_prim.IsValid():
        print(f"{_PRINT_PREFIX} Camera prim 없음 path={path!r}", flush=True)
        return False
    dist = (_vec3(snap.target_xyz) - _vec3(snap.eye_xyz)).GetLength()
    if dist < 1e-6:
        return False
    up = _resolve_camera_up_vector(snap.eye_xyz, snap.target_xyz, up_xyz)
    new_local = _camera_local_matrix(cam_prim, snap.eye_xyz, snap.target_xyz, up)
    ok = _write_prim_local_xform(
        path,
        new_local,
        coi=Gf.Vec3d(0.0, 0.0, -float(dist)),
    )
    print(
        f"{_PRINT_PREFIX} Camera prim 뷰 적용 path={path!r} dist={dist:.3f} ok={ok}",
        flush=True,
    )
    return ok


def apply_view_to_perspective(
    snap: CameraViewSnapshot,
    *,
    up_xyz: Tuple[float, float, float] = (0.0, 0.0, 1.0),
) -> bool:
    """Perspective session 카메라에 뷰 기록."""
    stage = _get_stage()
    if not stage:
        return False
    cam_prim = stage.GetPrimAtPath(_PERSP_CAMERA_PATH)
    if not cam_prim or not cam_prim.IsValid():
        return False
    dist = (_vec3(snap.target_xyz) - _vec3(snap.eye_xyz)).GetLength()
    if dist < 1e-6:
        return False
    up = _resolve_camera_up_vector(snap.eye_xyz, snap.target_xyz, up_xyz)
    new_local = _camera_local_matrix(cam_prim, snap.eye_xyz, snap.target_xyz, up)
    return _write_prim_local_xform(
        _PERSP_CAMERA_PATH,
        new_local,
        coi=Gf.Vec3d(0.0, 0.0, -float(dist)),
    )


def _cfg_camera_mode_enabled() -> bool:
    try:
        from .sim_control_defaults import SIM_CAMERA_MODE_ENABLED

        return bool(SIM_CAMERA_MODE_ENABLED)
    except Exception:
        return False


def _cfg_camera_prim_path() -> str:
    try:
        from .sim_control_defaults import SIM_CAMERA_PRIM_PATH

        return str(SIM_CAMERA_PRIM_PATH or "").strip()
    except Exception:
        return ""


def _cfg_camera_view() -> Any:
    try:
        from .sim_control_defaults import SIM_CAMERA_VIEW

        return SIM_CAMERA_VIEW
    except Exception:
        return None


def apply_sim_camera_on_start() -> bool:
    """시뮬 시작 직전 카메라 적용.

    - ``SIM_CAMERA_MODE_ENABLED=False`` → Perspective bind (기존과 동일)
    - ``True`` → prim 경로 bind + ``SIM_CAMERA_VIEW`` 가 있으면 그 뷰로 맞춤
    """
    if not _cfg_camera_mode_enabled():
        ok = set_viewport_camera_prim_path(_PERSP_CAMERA_PATH)
        print(
            f"{_PRINT_PREFIX} 카메라 모드 OFF → Perspective 시작 ok={ok}",
            flush=True,
        )
        return ok

    prim = _cfg_camera_prim_path()
    view = _cfg_camera_view()
    snap: Optional[CameraViewSnapshot] = None
    up = (0.0, 0.0, 1.0)
    if view is not None:
        try:
            snap = CameraViewSnapshot(
                eye_xyz=tuple(float(x) for x in view.eye_xyz),  # type: ignore[arg-type]
                target_xyz=tuple(float(x) for x in view.target_xyz),  # type: ignore[arg-type]
            )
            up = tuple(float(x) for x in getattr(view, "up_xyz", (0.0, 0.0, 1.0)))
        except Exception:
            snap = None

    if prim and not _is_session_camera_path(prim):
        if snap is not None:
            apply_view_to_camera_prim(prim, snap, up_xyz=up)
        ok = set_viewport_camera_prim_path(prim)
        print(
            f"{_PRINT_PREFIX} 카메라 모드 ON → prim={prim!r} "
            f"view={'set' if snap else 'prim-as-is'} bind_ok={ok}",
            flush=True,
        )
        return ok

    # prim 경로 없으면 Perspective 에 뷰만 적용
    if snap is not None:
        apply_view_to_perspective(snap, up_xyz=up)
    ok = set_viewport_camera_prim_path(_PERSP_CAMERA_PATH)
    print(
        f"{_PRINT_PREFIX} 카메라 모드 ON (prim 경로 없음) → Perspective+VIEW ok={ok}",
        flush=True,
    )
    return ok
