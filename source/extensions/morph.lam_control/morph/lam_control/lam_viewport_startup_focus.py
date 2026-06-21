"""Kit 시작 시 Viewport 오빗 pivot(회전 중심) 자동 적용 — 카메라 줌/이동·선택 없음.

설정: ``lam_viewport_overlay_config`` 의
``STARTUP_VIEWPORT_FOCUS_PRIM_ENABLED`` / ``STARTUP_VIEWPORT_FOCUS_PRIM_PATH``.
경로가 비어 있거나 enabled=False 이면 아무 것도 하지 않는다.

지정 prim 을 **선택하지 않고**, 카메라 eye/줌은 유지한 채 orbit pivot(COI) 을 맞춘다.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom  # type: ignore

_PRINT_PREFIX = "[LAM/ViewFocus]"
_COI_ATTR = "omni:kit:centerOfInterest"
_SUBS: dict[str, Any] = {
    "focus": None,
    "give_up": False,
    "logged_no_manip": False,
}


def startup_viewport_focus_config() -> Tuple[bool, str]:
    try:
        from .lam_viewport_overlay_config import (
            STARTUP_VIEWPORT_FOCUS_PRIM_ENABLED,
            STARTUP_VIEWPORT_FOCUS_PRIM_PATH,
        )

        enabled = bool(STARTUP_VIEWPORT_FOCUS_PRIM_ENABLED)
        path = str(STARTUP_VIEWPORT_FOCUS_PRIM_PATH or "").strip()
    except Exception:
        return False, ""
    if not path:
        enabled = False
    return enabled, path


def _prim_exists_on_stage(path: str) -> bool:
    p = str(path or "").strip()
    if not p.startswith("/"):
        return False
    try:
        import omni.usd as ou  # type: ignore

        stage = ou.get_context().get_stage()
        if stage is None:
            return False
        prim = stage.GetPrimAtPath(p)
        return bool(prim and prim.IsValid())
    except Exception:
        return False


def _get_active_viewport_api() -> Any:
    try:
        from omni.kit.viewport.utility import get_active_viewport  # type: ignore

        viewport_api = get_active_viewport()
        if viewport_api is not None:
            return viewport_api
    except Exception:
        pass
    try:
        from omni.kit.viewport.utility import get_active_viewport_window  # type: ignore

        win = get_active_viewport_window()
        if win is not None:
            return getattr(win, "viewport_api", None)
    except Exception:
        pass
    return None


def _resolve_usd_context_name(viewport_api: Any) -> str:
    if viewport_api is not None:
        try:
            name = str(getattr(viewport_api, "usd_context_name", "") or "")
            if name:
                return name
        except Exception:
            pass
    return ""


def _get_stage(viewport_api: Any = None) -> Optional[Usd.Stage]:
    try:
        import omni.usd as ou  # type: ignore

        ctx_name = _resolve_usd_context_name(viewport_api)
        ctx = ou.get_context(ctx_name) if ctx_name else ou.get_context("")
        if ctx is None:
            ctx = ou.get_context()
        if ctx is None:
            return None
        return ctx.get_stage()
    except Exception:
        return None


def _active_camera_path_str(viewport_api: Any = None) -> Optional[str]:
    try:
        from omni.kit.viewport.utility import get_active_viewport_camera_string  # type: ignore

        p = get_active_viewport_camera_string()
        return str(p) if p else None
    except Exception:
        pass
    if viewport_api is not None:
        try:
            cam_path = getattr(viewport_api, "camera_path", None)
            if cam_path is not None:
                return str(cam_path)
        except Exception:
            pass
    return None


def _camera_eye_world(viewport_api: Any = None) -> Optional[Gf.Vec3d]:
    try:
        from omni.kit.viewport.utility.camera_state import ViewportCameraState  # type: ignore

        for args in ((viewport_api,), ()):
            try:
                st = ViewportCameraState(*args)
            except Exception:
                continue
            eye = getattr(st, "position_world", None)
            if eye is not None:
                return Gf.Vec3d(float(eye[0]), float(eye[1]), float(eye[2]))
    except Exception:
        pass
    stage = _get_stage(viewport_api)
    cam_path = _active_camera_path_str(viewport_api)
    if not stage or not cam_path:
        return None
    prim = stage.GetPrimAtPath(cam_path)
    if not prim or not prim.IsValid():
        return None
    try:
        xfc = UsdGeom.XformCache(Usd.TimeCode.Default())
        return xfc.GetLocalToWorldTransform(prim).ExtractTranslation()
    except Exception:
        return None


def _prim_world_pivot(path: str, stage: Usd.Stage) -> Optional[Gf.Vec3d]:
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return None
    try:
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        bbox = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        center = bbox.GetMidpoint()
        return Gf.Vec3d(float(center[0]), float(center[1]), float(center[2]))
    except Exception:
        pass
    try:
        xform = UsdGeom.Xformable(prim)
        if xform:
            xfc = UsdGeom.XformCache(Usd.TimeCode.Default())
            t = xfc.GetLocalToWorldTransform(prim).ExtractTranslation()
            return Gf.Vec3d(float(t[0]), float(t[1]), float(t[2]))
    except Exception:
        pass
    return None


def _is_viewport_camera_manipulator_model(obj: Any) -> bool:
    """``omni.ui.scene.CameraModel``(view/projection 전용) 은 제외."""
    if obj is None:
        return False
    if type(obj).__name__ == "CameraModel":
        return False
    get_as_floats = getattr(obj, "get_as_floats", None)
    set_floats = getattr(obj, "set_floats", None)
    if not callable(get_as_floats) or not callable(set_floats):
        return False
    try:
        transform = get_as_floats("transform")
        return bool(transform and len(transform) >= 16)
    except Exception:
        return False


def _collect_camera_manipulator_models(viewport_api: Any) -> List[Any]:
    """깊은 BFS 없이 viewport/camera manipulator 직접 경로만 탐색."""
    models: List[Any] = []
    seen: set[int] = set()

    def _try_add(obj: Any) -> None:
        if obj is None or not _is_viewport_camera_manipulator_model(obj):
            return
        oid = id(obj)
        if oid in seen:
            return
        seen.add(oid)
        models.append(obj)

    candidates: List[Any] = []
    if viewport_api is not None:
        candidates.append(viewport_api)
        for attr in (
            "camera_manipulator",
            "_camera_manipulator",
            "manipulator",
            "camera_model",
            "_camera_model",
        ):
            candidates.append(getattr(viewport_api, attr, None))

    try:
        from omni.kit.viewport.utility import get_active_viewport_window  # type: ignore

        win = get_active_viewport_window()
        if win is not None:
            candidates.append(win)
            for attr in (
                "camera_manipulator",
                "_camera_manipulator",
                "manipulator",
                "viewport_widget",
                "_viewport_widget",
            ):
                w = getattr(win, attr, None)
                candidates.append(w)
                if w is not None:
                    candidates.append(getattr(w, "model", None))
                    candidates.append(getattr(w, "camera_manipulator", None))
                    cm = getattr(w, "camera_manipulator", None)
                    if cm is not None:
                        candidates.append(getattr(cm, "model", None))
    except Exception:
        pass

    for obj in candidates:
        _try_add(obj)
        _try_add(getattr(obj, "model", None))

    return models


def _matrix_from_model_floats(model: Any) -> Optional[Gf.Matrix4d]:
    try:
        floats = model.get_as_floats("transform")
        if not floats or len(floats) < 16:
            return None
        return Gf.Matrix4d(*[float(v) for v in floats[:16]])
    except Exception:
        return None


def _coi_local_from_pivot(cam_world: Gf.Matrix4d, pivot_world: Gf.Vec3d) -> Gf.Vec3d:
    return cam_world.GetInverse().Transform(pivot_world)


def _set_model_coi_local(model: Any, coi_local: Gf.Vec3d) -> bool:
    vals = [float(coi_local[0]), float(coi_local[1]), float(coi_local[2])]
    try:
        model.set_ints("disable_undo", [1])
    except Exception:
        pass
    try:
        model.set_floats("center_of_interest", vals)
        return True
    except Exception:
        return False


def _camera_world_from_usd(viewport_api: Any) -> Optional[Gf.Matrix4d]:
    stage = _get_stage(viewport_api)
    cam_path = _active_camera_path_str(viewport_api)
    if not stage or not cam_path:
        return None
    prim = stage.GetPrimAtPath(cam_path)
    if not prim or not prim.IsValid():
        return None
    try:
        xfc = UsdGeom.XformCache(Usd.TimeCode.Default())
        return xfc.GetLocalToWorldTransform(prim)
    except Exception:
        return None


def _set_usd_coi_local_quiet(
    coi_local: Gf.Vec3d,
    *,
    viewport_api: Any = None,
) -> bool:
    """session layer 에 COI 만 조용히 기록 (ChangePropertyCommand/선택 없음)."""
    stage = _get_stage(viewport_api)
    cam_path = _active_camera_path_str(viewport_api)
    if not stage or not cam_path:
        return False
    cam_prim = stage.GetPrimAtPath(cam_path)
    if not cam_prim or not cam_prim.IsValid():
        return False
    try:
        edit = Usd.EditContext(stage, Usd.EditTarget(stage.GetSessionLayer()))
        with edit:
            attr = cam_prim.GetAttribute(_COI_ATTR)
            if not attr or not attr.IsValid():
                cam_prim.CreateAttribute(
                    _COI_ATTR, Sdf.ValueTypeNames.Vector3d, custom=True
                ).Set(coi_local)
            else:
                attr.Set(coi_local)
        return True
    except Exception:
        return False


def _apply_orbit_pivot_only(viewport_api: Any, pivot_world: Gf.Vec3d) -> bool:
    models = _collect_camera_manipulator_models(viewport_api)
    model_ok = False

    for model in models:
        cam_world = _matrix_from_model_floats(model)
        if cam_world is None:
            continue
        local = _coi_local_from_pivot(cam_world, pivot_world)
        if _set_model_coi_local(model, local):
            model_ok = True

    if model_ok:
        return True

    cam_world = _camera_world_from_usd(viewport_api)
    if cam_world is None:
        return False
    coi_local = _coi_local_from_pivot(cam_world, pivot_world)
    return _set_usd_coi_local_quiet(coi_local, viewport_api=viewport_api)


def _mark_orbit_pivot_give_up(reason: str) -> None:
    if not _SUBS.get("logged_no_manip"):
        print(f"{_PRINT_PREFIX} {reason}", flush=True)
        _SUBS["logged_no_manip"] = True
    _SUBS["give_up"] = True
    _stop_focus_retry_subscription()


def apply_startup_viewport_focus_prim() -> bool:
    """지정 prim 기준 오빗 pivot(COI)만 설정 — 선택·줌·카메라 이동 없음."""
    if _SUBS.get("give_up"):
        return False

    enabled, path = startup_viewport_focus_config()
    if not enabled or not path:
        return False
    if not _prim_exists_on_stage(path):
        return False

    viewport_api = _get_active_viewport_api()
    if viewport_api is None:
        return False

    stage = _get_stage(viewport_api)
    if stage is None:
        return False
    pivot_world = _prim_world_pivot(path, stage)
    if pivot_world is None:
        return False

    eye_before = _camera_eye_world(viewport_api)

    if not _apply_orbit_pivot_only(viewport_api, pivot_world):
        _mark_orbit_pivot_give_up(
            f"orbit pivot unavailable prim={path} — camera unchanged (no retry)"
        )
        return False

    eye_after = _camera_eye_world(viewport_api)
    if eye_before is not None and eye_after is not None:
        drift = (eye_after - eye_before).GetLength()
        if drift > 1e-3:
            print(
                f"{_PRINT_PREFIX} warning: camera eye moved {drift:.4f} m while setting pivot",
                flush=True,
            )

    print(
        f"{_PRINT_PREFIX} startup orbit pivot OK prim={path} (no selection) "
        f"pivot=({pivot_world[0]:.3f}, {pivot_world[1]:.3f}, {pivot_world[2]:.3f})",
        flush=True,
    )
    return True


def _stop_focus_retry_subscription() -> None:
    sub = _SUBS.get("focus")
    if sub is None:
        return
    try:
        sub.unsubscribe()
    except Exception:
        pass
    _SUBS["focus"] = None


def schedule_startup_viewport_focus_after_stage_ready(
    *,
    delay_frames: int = 24,
    max_attempts: int = 30,
) -> None:
    """Master USD·stage prim 준비 후 오빗 pivot(COI) 설정을 post_update 로 재시도."""
    if _SUBS.get("give_up"):
        return

    enabled, path = startup_viewport_focus_config()
    if not enabled or not path:
        _stop_focus_retry_subscription()
        return

    _stop_focus_retry_subscription()

    frames_until_start = [max(0, int(delay_frames))]
    attempts_left = [max(1, int(max_attempts))]

    def _finish() -> None:
        _stop_focus_retry_subscription()

    def _tick(_e=None) -> None:
        if _SUBS.get("give_up"):
            _finish()
            return
        enabled_now, path_now = startup_viewport_focus_config()
        if not enabled_now or not path_now:
            _finish()
            return
        if frames_until_start[0] > 0:
            frames_until_start[0] -= 1
            return
        if apply_startup_viewport_focus_prim():
            _finish()
            return
        if _SUBS.get("give_up"):
            _finish()
            return
        attempts_left[0] -= 1
        if attempts_left[0] <= 0:
            _mark_orbit_pivot_give_up(
                f"startup orbit pivot gave up (prim/viewport not ready?): {path_now}"
            )
            _finish()

    try:
        import omni.kit.app as _app  # type: ignore

        stream = _app.get_app().get_post_update_event_stream()
        _SUBS["focus"] = stream.create_subscription_to_pop(
            _tick,
            name="morph.lam_control.viewport_startup_focus",
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} schedule failed: {exc} (immediate try)", flush=True)
        apply_startup_viewport_focus_prim()


__all__ = [
    "startup_viewport_focus_config",
    "apply_startup_viewport_focus_prim",
    "schedule_startup_viewport_focus_after_stage_ready",
]
