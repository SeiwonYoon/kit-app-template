"""TBS 시뮬 시작 카메라 fly + 정지 시 Perspective 줌 복귀.

워크플로 (LAM ``lam_play_camera_fly`` 패턴 기반):
1) 「뷰 저장」 → 콘솔에 config 스니펫 (즉시 적용 없음)
2) ``SIM_CAMERA_VIEW`` / ``SIM_CAMERA_PRIM_PATH`` + ``SIM_CAMERA_MODE_ENABLED=True``
3) ``SIM_CAMERA_FLY_ENABLED=True``  → 현재 뷰 기억 → target 으로 fly → (있으면) Camera prim bind
   ``SIM_CAMERA_FLY_ENABLED=False`` → fly 없이 ``SIM_CAMERA_VIEW`` 로 즉시 이동
4) 시뮬 정지: Perspective 복귀 + 시작 전 줌/뷰 복원 (다중 프레임 안정화)

fly 구간은 Perspective 로만 진행하고 Persp aperture 를 목표 Camera FOV 로 보간한 뒤,
종료 시에만 Camera prim look-through (LAM 과 동일 — 줌 점프 방지).

UI·웹 모두 ``on_sim_start_clicked`` / ``on_sim_stop_clicked`` 를 타므로 동일 경로.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom  # type: ignore

_PRINT_PREFIX = "[TBS/SimCamera]"
_PERSP_CAMERA_PATH = "/OmniverseKit_Persp"
_COI_ATTR = "omni:kit:centerOfInterest"

# 시뮬 시작 직전 Perspective 뷰 (컨텍스트별 / 전역) — 정지 시 복원
_pre_sim_views: Dict[str, "CameraViewSnapshot"] = {}
_pre_sim_ups: Dict[str, Tuple[float, float, float]] = {}
_fly_sub: Any = None
_fly_done_evt: Optional[threading.Event] = None
_fly_active: bool = False
_stop_persp_restore_sub: Any = None


@dataclass(frozen=True)
class CameraViewSnapshot:
    eye_xyz: Tuple[float, float, float]
    target_xyz: Tuple[float, float, float]


def _vec3(t: Tuple[float, float, float]) -> Gf.Vec3d:
    return Gf.Vec3d(float(t[0]), float(t[1]), float(t[2]))


def _lerp3(
    a: Tuple[float, float, float],
    b: Tuple[float, float, float],
    u: float,
) -> Tuple[float, float, float]:
    return (
        a[0] + (b[0] - a[0]) * u,
        a[1] + (b[1] - a[1]) * u,
        a[2] + (b[2] - a[2]) * u,
    )


def _smoothstep01(t: float) -> float:
    x = max(0.0, min(1.0, float(t)))
    return x * x * (3.0 - 2.0 * x)


def _is_session_camera_path(path: str) -> bool:
    p = str(path or "").strip()
    return not p or p == _PERSP_CAMERA_PATH or "Persp" in p


def _get_stage_for_context(usd_context_name: str = "") -> Any:
    try:
        import omni.usd as ou  # type: ignore

        cn = str(usd_context_name or "").strip()
        ctx = ou.get_context(cn) if cn else ou.get_context()
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


def _resolve_viewport_context_name(viewport_api: Any) -> str:
    if viewport_api is None:
        return ""
    try:
        cn = getattr(viewport_api, "usd_context_name", None)
        if cn is not None and str(cn).strip():
            return str(cn).strip()
    except Exception:
        pass
    try:
        ctx = getattr(viewport_api, "usd_context", None)
        if ctx is not None and hasattr(ctx, "get_name"):
            return str(ctx.get_name() or "").strip()
    except Exception:
        pass
    return ""


def _iter_viewport_and_contexts(ext: Any = None) -> List[Tuple[Any, str]]:
    """모든 분할 화면(화면1, 화면2 등)의 (viewport_api, usd_context_name) 쌍."""
    results: List[Tuple[Any, str]] = []
    seen_ids: set[int] = set()

    def _add(api: Any, default_ctx: str = "") -> None:
        if api is None:
            return
        oid = id(api)
        if oid in seen_ids:
            return
        seen_ids.add(oid)
        ctx = _resolve_viewport_context_name(api) or default_ctx
        results.append((api, ctx))

    # 1) 활성 뷰포트
    act_vp = _get_active_viewport_api()
    if act_vp is not None:
        _add(act_vp, "")

    # 2) 표준 윈도우 이름 기반 조회
    win_names = ["Viewport", "TBS_SimSplit_1", "TBS_SimSplit_2", "TBS_SimSplit_3"]
    for idx, win_name in enumerate(win_names):
        try:
            from omni.kit.viewport.utility import get_viewport_from_window_name  # type: ignore

            vp = get_viewport_from_window_name(str(win_name))
            if vp is not None:
                ctx_hint = "" if idx == 0 else f"TBS_SimSplit_{idx}"
                _add(vp, ctx_hint)
        except Exception:
            pass

    # 3) ext 객체에 기록된 컨텍스트명 기반 보조 매핑
    if ext is not None:
        try:
            ctx_names = list(getattr(ext, "_sim_multi_context_names", []) or [])
            for c_idx, c_name in enumerate(ctx_names):
                try:
                    from omni.kit.viewport.utility import get_viewport_from_window_name  # type: ignore

                    wn = f"TBS_SimSplit_{c_idx + 1}"
                    vp = get_viewport_from_window_name(wn)
                    if vp is not None:
                        _add(vp, str(c_name))
                except Exception:
                    pass
        except Exception:
            pass

    return results


def _active_camera_path_str(viewport_api: Any = None) -> str:
    api = viewport_api if viewport_api is not None else _get_active_viewport_api()
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
    usd_context_name: str = "",
) -> bool:
    stage = _get_stage_for_context(usd_context_name)
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
        print(f"{_PRINT_PREFIX} USD xform set failed (ctx={usd_context_name!r}): {exc}", flush=True)
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


def _snapshot_from_camera_prim(camera_path: str = "", usd_context_name: str = "") -> Optional[CameraViewSnapshot]:
    stage = _get_stage_for_context(usd_context_name)
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


def capture_current_view(viewport_api: Any = None, usd_context_name: str = "") -> Optional[CameraViewSnapshot]:
    vp = viewport_api if viewport_api is not None else _get_active_viewport_api()
    snap = _snapshot_from_viewport_state(vp)
    if snap is not None:
        return snap
    return _snapshot_from_camera_prim(usd_context_name=usd_context_name)


def _capture_world_up(viewport_api: Any = None, usd_context_name: str = "") -> Tuple[float, float, float]:
    stage = _get_stage_for_context(usd_context_name)
    path = _active_camera_path_str(viewport_api)
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
        "SIM_CAMERA_FLY_ENABLED = True  # False → fly 없이 즉시 이동\n"
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


def set_viewport_camera_prim_path(prim_path: str, ext: Any = None) -> bool:
    path = str(prim_path or "").strip()
    if not path:
        return False
    ok = False
    for api, _ctx in _iter_viewport_and_contexts(ext):
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
    usd_context_name: str = "",
) -> bool:
    path = str(prim_path or "").strip()
    if not path or _is_session_camera_path(path):
        return False
    stage = _get_stage_for_context(usd_context_name)
    if not stage:
        return False
    cam_prim = stage.GetPrimAtPath(path)
    if not cam_prim or not cam_prim.IsValid():
        print(f"{_PRINT_PREFIX} Camera prim 없음 path={path!r} (ctx={usd_context_name!r})", flush=True)
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
        usd_context_name=usd_context_name,
    )
    return ok


def apply_view_to_perspective(
    snap: CameraViewSnapshot,
    *,
    up_xyz: Tuple[float, float, float] = (0.0, 0.0, 1.0),
    usd_context_name: str = "",
    viewport_api: Any = None,
) -> bool:
    """Perspective session 카메라에 뷰 기록 + ViewportCameraState 동기화."""
    stage = _get_stage_for_context(usd_context_name)
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
    ok = _write_prim_local_xform(
        _PERSP_CAMERA_PATH,
        new_local,
        coi=Gf.Vec3d(0.0, 0.0, -float(dist)),
        usd_context_name=usd_context_name,
    )
    # ViewportCameraState 가 사용 가능하면 실시간 내부 상태도 동기화
    if viewport_api is not None:
        try:
            from omni.kit.viewport.utility.camera_state import ViewportCameraState  # type: ignore

            st = ViewportCameraState(viewport_api)
            if st is not None:
                st.set_position_world(Gf.Vec3d(float(snap.eye_xyz[0]), float(snap.eye_xyz[1]), float(snap.eye_xyz[2])), True)
                st.set_target_world(Gf.Vec3d(float(snap.target_xyz[0]), float(snap.target_xyz[1]), float(snap.target_xyz[2])), True)
        except Exception:
            pass
    return ok


def apply_view_to_all_screens(
    snap: CameraViewSnapshot,
    *,
    up_xyz: Tuple[float, float, float] = (0.0, 0.0, 1.0),
    assign_prim_path: str = "",
    ext: Any = None,
) -> bool:
    """모든 분할 화면(화면1, 화면2 등)에 일관된 뷰 적용."""
    path = str(assign_prim_path or "").strip()
    is_prim = bool(path and not _is_session_camera_path(path))
    vps = _iter_viewport_and_contexts(ext)
    ok = False
    for api, ctx in vps:
        if is_prim:
            prim_ok = apply_view_to_camera_prim(path, snap, up_xyz=up_xyz, usd_context_name=ctx)
            ok = prim_ok or ok
        else:
            persp_ok = apply_view_to_perspective(snap, up_xyz=up_xyz, usd_context_name=ctx, viewport_api=api)
            ok = persp_ok or ok
    if is_prim:
        set_viewport_camera_prim_path(path, ext=ext)
    else:
        set_viewport_camera_prim_path(_PERSP_CAMERA_PATH, ext=ext)
    return ok


def _read_camera_aperture(
    prim_path: str,
    *,
    usd_context_name: str = "",
) -> Optional[Tuple[float, float]]:
    """Camera prim 의 (horizontal, vertical) aperture."""
    stage = _get_stage_for_context(usd_context_name)
    path = str(prim_path or "").strip()
    if not stage or not path:
        return None
    cam_prim = stage.GetPrimAtPath(path)
    if not cam_prim or not cam_prim.IsValid():
        return None
    try:
        cam = UsdGeom.Camera(cam_prim)
        h = cam.GetHorizontalApertureAttr().Get()
        v = cam.GetVerticalApertureAttr().Get()
        if h is None or v is None:
            return None
        return (float(h), float(v))
    except Exception:
        return None


def _read_camera_focal_length(
    prim_path: str,
    *,
    usd_context_name: str = "",
) -> Optional[float]:
    stage = _get_stage_for_context(usd_context_name)
    path = str(prim_path or "").strip()
    if not stage or not path:
        return None
    cam_prim = stage.GetPrimAtPath(path)
    if not cam_prim or not cam_prim.IsValid():
        return None
    try:
        f = UsdGeom.Camera(cam_prim).GetFocalLengthAttr().Get()
        return float(f) if f else None
    except Exception:
        return None


def _set_camera_aperture(
    prim_path: str,
    horizontal: float,
    vertical: float,
    *,
    usd_context_name: str = "",
) -> bool:
    stage = _get_stage_for_context(usd_context_name)
    path = str(prim_path or "").strip()
    if not stage or not path:
        return False
    cam_prim = stage.GetPrimAtPath(path)
    if not cam_prim or not cam_prim.IsValid():
        return False
    try:
        cam = UsdGeom.Camera(cam_prim)
        with Usd.EditContext(stage, Usd.EditTarget(stage.GetSessionLayer())):
            cam.GetHorizontalApertureAttr().Set(float(horizontal))
            cam.GetVerticalApertureAttr().Set(float(vertical))
        return True
    except Exception:
        return False


def _persp_aperture_matching_camera_prim(
    prim_path: str,
    *,
    usd_context_name: str = "",
) -> Optional[Tuple[float, float]]:
    """목표 Camera prim FOV 와 같은 화면이 되도록 Persp (h,v) aperture 계산."""
    prim_ap = _read_camera_aperture(prim_path, usd_context_name=usd_context_name)
    if prim_ap is None:
        return None
    persp_f = _read_camera_focal_length(_PERSP_CAMERA_PATH, usd_context_name=usd_context_name)
    prim_f = _read_camera_focal_length(prim_path, usd_context_name=usd_context_name)
    if not persp_f or not prim_f:
        return prim_ap
    scale = persp_f / prim_f
    return (prim_ap[0] * scale, prim_ap[1] * scale)


def _persp_equivalent_aperture_for_prim(
    prim_path: str,
    *,
    usd_context_name: str = "",
) -> Optional[Tuple[float, float]]:
    """Persp 현재 FOV 와 같게 보이도록 Camera prim (h,v) aperture 계산 (bind 직전)."""
    persp_ap = _read_camera_aperture(_PERSP_CAMERA_PATH, usd_context_name=usd_context_name)
    if persp_ap is None:
        return None
    persp_f = _read_camera_focal_length(_PERSP_CAMERA_PATH, usd_context_name=usd_context_name)
    prim_f = _read_camera_focal_length(prim_path, usd_context_name=usd_context_name)
    if not persp_f or not prim_f:
        return persp_ap
    scale = prim_f / persp_f
    return (persp_ap[0] * scale, persp_ap[1] * scale)


def _apply_persp_aperture_all_screens(
    hv: Tuple[float, float],
    *,
    ext: Any = None,
) -> None:
    for _api, ctx in _iter_viewport_and_contexts(ext):
        _set_camera_aperture(
            _PERSP_CAMERA_PATH,
            hv[0],
            hv[1],
            usd_context_name=ctx,
        )


def _resolve_fly_aperture_blend(
    assign_prim_path: str,
    *,
    ext: Any = None,
) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
    """fly 시작·종료 Persp aperture (첫 유효 context 기준)."""
    path = str(assign_prim_path or "").strip()
    if not path or _is_session_camera_path(path):
        return None, None
    for _api, ctx in _iter_viewport_and_contexts(ext):
        end_ap = _persp_aperture_matching_camera_prim(path, usd_context_name=ctx)
        cur_ap = _read_camera_aperture(_PERSP_CAMERA_PATH, usd_context_name=ctx)
        if end_ap is not None and cur_ap is not None:
            return cur_ap, end_ap
    return None, None


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


def _cfg_fly_duration_sec() -> float:
    try:
        from .sim_control_defaults import SIM_CAMERA_FLY_DURATION_SEC

        return max(0.05, float(SIM_CAMERA_FLY_DURATION_SEC))
    except Exception:
        return 2.0


def _cfg_camera_fly_enabled() -> bool:
    try:
        from .sim_control_defaults import SIM_CAMERA_FLY_ENABLED

        return bool(SIM_CAMERA_FLY_ENABLED)
    except Exception:
        return True


def _target_from_config() -> Tuple[Optional[CameraViewSnapshot], Tuple[float, float, float]]:
    view = _cfg_camera_view()
    up = (0.0, 0.0, 1.0)
    if view is None:
        return None, up
    try:
        snap = CameraViewSnapshot(
            eye_xyz=tuple(float(x) for x in view.eye_xyz),  # type: ignore[arg-type]
            target_xyz=tuple(float(x) for x in view.target_xyz),  # type: ignore[arg-type]
        )
        up = tuple(float(x) for x in getattr(view, "up_xyz", (0.0, 0.0, 1.0)))
        return snap, up
    except Exception:
        return None, up


def _views_are_close(
    a: CameraViewSnapshot,
    b: CameraViewSnapshot,
    *,
    pos_eps_m: float = 0.05,
) -> bool:
    de = (_vec3(a.eye_xyz) - _vec3(b.eye_xyz)).GetLength()
    dt = (_vec3(a.target_xyz) - _vec3(b.target_xyz)).GetLength()
    return de <= pos_eps_m and dt <= pos_eps_m


def is_sim_camera_flying() -> bool:
    """카메라 FLY 애니메이션이 현재 동작 중인지 여부."""
    global _fly_active
    return bool(_fly_active)


def _stop_fly_subscription() -> None:
    global _fly_sub, _fly_done_evt, _fly_active
    _fly_active = False
    try:
        if _fly_sub is not None:
            _fly_sub.unsubscribe()
    except Exception:
        pass
    _fly_sub = None
    if _fly_done_evt is not None:
        try:
            _fly_done_evt.set()
        except Exception:
            pass
    _fly_done_evt = None


def _stop_stop_perspective_restore_subscription() -> None:
    global _stop_persp_restore_sub
    try:
        if _stop_persp_restore_sub is not None:
            _stop_persp_restore_sub.unsubscribe()
    except Exception:
        pass
    _stop_persp_restore_sub = None


def _remember_pre_sim_views(ext: Any = None) -> None:
    """시뮬 시작 직전 모든 뷰포트의 시점을 기억."""
    global _pre_sim_views, _pre_sim_ups
    # 이미 fly 중이거나 이전에 기억된 상태가 있으면 덮어쓰지 않음
    if _pre_sim_views:
        return
    for api, ctx in _iter_viewport_and_contexts(ext):
        snap = capture_current_view(api, ctx)
        if snap is not None:
            _pre_sim_views[ctx] = snap
            _pre_sim_ups[ctx] = _capture_world_up(api, ctx)
            e = snap.eye_xyz
            print(
                f"{_PRINT_PREFIX} pre-sim view 저장 (ctx={ctx!r}) "
                f"eye=({e[0]:.3f},{e[1]:.3f},{e[2]:.3f})",
                flush=True,
            )


def _finish_fly_to_target(
    target: CameraViewSnapshot,
    *,
    up_xyz: Tuple[float, float, float],
    assign_prim_path: str = "",
    ext: Any = None,
) -> bool:
    global _fly_active
    _fly_active = False
    path = str(assign_prim_path or "").strip()
    use_prim = bool(path and not _is_session_camera_path(path))
    if use_prim:
        # Persp fly 종료 FOV 와 prim look-through FOV 를 맞춘 뒤 bind (줌 점프 방지)
        for _api, ctx in _iter_viewport_and_contexts(ext):
            eq_ap = _persp_equivalent_aperture_for_prim(path, usd_context_name=ctx)
            if eq_ap is not None:
                _set_camera_aperture(path, eq_ap[0], eq_ap[1], usd_context_name=ctx)
    ok = apply_view_to_all_screens(
        target,
        up_xyz=up_xyz,
        assign_prim_path=path if use_prim else "",
        ext=ext,
    )
    if use_prim:
        print(f"{_PRINT_PREFIX} fly 종료 → prim bind path={path!r} ok={ok}", flush=True)
    else:
        print(f"{_PRINT_PREFIX} fly 종료 → Perspective 뷰 고정 ok={ok}", flush=True)
    return ok


def _start_fly_animation(
    start: CameraViewSnapshot,
    end: CameraViewSnapshot,
    done: threading.Event,
    *,
    up_xyz: Tuple[float, float, float],
    assign_prim_path: str = "",
    ext: Any = None,
    on_complete: Optional[Callable[[], None]] = None,
) -> None:
    """main 스레드: Perspective + aperture 보간 fly. prim bind 는 on_complete 에서만."""
    global _fly_sub, _fly_done_evt, _fly_active
    _stop_fly_subscription()
    _fly_active = True
    _fly_done_evt = done
    dur = _cfg_fly_duration_sec()
    t0 = time.perf_counter()
    up = up_xyz
    ap_start, ap_end = _resolve_fly_aperture_blend(assign_prim_path, ext=ext)
    if ap_start is not None and ap_end is not None:
        print(
            f"{_PRINT_PREFIX} Persp aperture 보간 "
            f"h={ap_start[0]:.3f}->{ap_end[0]:.3f}",
            flush=True,
        )

    def _aperture_at(u: float) -> Optional[Tuple[float, float]]:
        if ap_start is None or ap_end is None:
            return None
        return (
            ap_start[0] + (ap_end[0] - ap_start[0]) * u,
            ap_start[1] + (ap_end[1] - ap_start[1]) * u,
        )

    def _finish() -> None:
        global _fly_sub, _fly_active
        _fly_active = False
        try:
            if _fly_sub is not None:
                _fly_sub.unsubscribe()
        except Exception:
            pass
        _fly_sub = None
        if callable(on_complete):
            try:
                on_complete()
            except Exception:
                pass
        done.set()

    def _tick(_event) -> None:
        elapsed = time.perf_counter() - t0
        u = _smoothstep01(elapsed / dur) if dur > 1e-9 else 1.0
        eye = _lerp3(start.eye_xyz, end.eye_xyz, u)
        tgt = _lerp3(start.target_xyz, end.target_xyz, u)
        cur_snap = CameraViewSnapshot(eye_xyz=eye, target_xyz=tgt)
        # fly 중: Perspective 만 (Camera prim bind 금지)
        apply_view_to_all_screens(cur_snap, up_xyz=up, assign_prim_path="", ext=ext)
        ap = _aperture_at(u)
        if ap is not None:
            _apply_persp_aperture_all_screens(ap, ext=ext)
        if u >= 1.0 - 1e-9:
            apply_view_to_all_screens(end, up_xyz=up, assign_prim_path="", ext=ext)
            ap_final = _aperture_at(1.0)
            if ap_final is not None:
                _apply_persp_aperture_all_screens(ap_final, ext=ext)
            _finish()

    try:
        import omni.kit.app as _app  # type: ignore

        set_viewport_camera_prim_path(_PERSP_CAMERA_PATH, ext=ext)
        apply_view_to_all_screens(start, up_xyz=up, assign_prim_path="", ext=ext)
        if ap_start is not None:
            _apply_persp_aperture_all_screens(ap_start, ext=ext)
        stream = _app.get_app().get_update_event_stream()
        _fly_sub = stream.create_subscription_to_pop(
            _tick,
            name="morph.tbs_control_2:sim_camera_fly",
        )
        print(f"{_PRINT_PREFIX} fly 시작 ({dur:.2f}s)", flush=True)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} fly 구독 실패: {exc}", flush=True)
        _fly_active = False
        if callable(on_complete):
            try:
                on_complete()
            except Exception:
                pass
        done.set()


def apply_sim_camera_on_start(ext: Any = None) -> bool:
    """시뮬 시작 직전 카메라 설정 및 FLY 실행."""
    _stop_stop_perspective_restore_subscription()
    if not _cfg_camera_mode_enabled():
        _stop_fly_subscription()
        ok = set_viewport_camera_prim_path(_PERSP_CAMERA_PATH, ext=ext)
        print(f"{_PRINT_PREFIX} 카메라 모드 OFF → Perspective 시작 ok={ok}", flush=True)
        return ok

    prim = _cfg_camera_prim_path()
    target, up = _target_from_config()
    set_viewport_camera_prim_path(_PERSP_CAMERA_PATH, ext=ext)
    
    # 1) 시작 전 시점 저장 (정지 시 복원용)
    _remember_pre_sim_views(ext)

    if target is None:
        if prim and not _is_session_camera_path(prim):
            ok = set_viewport_camera_prim_path(prim, ext=ext)
            print(f"{_PRINT_PREFIX} 카메라 모드 ON (VIEW 없음) → prim bind={prim!r} ok={ok}", flush=True)
            return ok
        print(f"{_PRINT_PREFIX} 카메라 모드 ON (VIEW·prim 없음)", flush=True)
        return True

    current = capture_current_view()
    if current is None:
        return _finish_fly_to_target(target, up_xyz=up, assign_prim_path=prim, ext=ext)

    # fly 비활성: 애니메이션 없이 SIM_CAMERA_VIEW 로 즉시 이동
    if not _cfg_camera_fly_enabled():
        print(f"{_PRINT_PREFIX} fly 비활성 — 즉시 이동", flush=True)
        return _finish_fly_to_target(target, up_xyz=up, assign_prim_path=prim, ext=ext)

    # 이미 동일 위치이면 fly 생략하고 바인딩
    if _views_are_close(current, target):
        return _finish_fly_to_target(target, up_xyz=up, assign_prim_path=prim, ext=ext)

    done = threading.Event()

    def _complete() -> None:
        _finish_fly_to_target(target, up_xyz=up, assign_prim_path=prim, ext=ext)

    _start_fly_animation(
        current,
        target,
        done,
        up_xyz=up,
        assign_prim_path=prim,
        ext=ext,
        on_complete=_complete,
    )
    return True


def schedule_restore_perspective_after_sim_stop(
    ext: Any = None,
    *,
    delay_frames: int = 10,
) -> None:
    """정지 후 race 대비 — 여러 프레임 동안 Perspective 및 줌 복원을 안정적으로 정착."""
    global _stop_persp_restore_sub, _pre_sim_views, _pre_sim_ups
    _stop_stop_perspective_restore_subscription()
    
    snaps = dict(_pre_sim_views)
    ups = dict(_pre_sim_ups)
    if not snaps:
        return

    frames_left = [max(1, int(delay_frames))]

    def _apply_step(is_final: bool = False) -> None:
        for api, ctx in _iter_viewport_and_contexts(ext):
            snap = snaps.get(ctx) or snaps.get("")
            up = ups.get(ctx) or (0.0, 0.0, 1.0)
            if snap is not None:
                apply_view_to_perspective(snap, up_xyz=up, usd_context_name=ctx, viewport_api=api)
        set_viewport_camera_prim_path(_PERSP_CAMERA_PATH, ext=ext)
        if is_final:
            _pre_sim_views.clear()
            _pre_sim_ups.clear()

    def _tick(_e=None) -> None:
        if frames_left[0] > 0:
            frames_left[0] -= 1
            _apply_step(is_final=False)
            return
        _stop_stop_perspective_restore_subscription()
        _apply_step(is_final=True)
        print(f"{_PRINT_PREFIX} 정지 후 Perspective 복원 완료 (다중 프레임 정착)", flush=True)

    try:
        import omni.kit.app as _app  # type: ignore

        _apply_step(is_final=False)
        stream = _app.get_app().get_update_event_stream()
        _stop_persp_restore_sub = stream.create_subscription_to_pop(
            _tick,
            name="morph.tbs_control_2:stop_perspective_restore",
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} stop perspective restore schedule failed: {exc}", flush=True)
        _apply_step(is_final=True)


def restore_sim_camera_on_stop(ext: Any = None) -> bool:
    """시뮬 정지: fly 중단 → Perspective 복귀 + 시작 전 줌 복원."""
    _stop_fly_subscription()
    schedule_restore_perspective_after_sim_stop(ext=ext, delay_frames=8)
    return True
