"""Viewport 「탑뷰 보기」 — preset 카메라 고정 + 네비게이션 입력 차단.

설정: ``lam_viewport_overlay_config`` 의 ``TOP_VIEW_PRESET*``.
「뷰저장」 버튼(Play preset 과 공유)으로 캡처한 eye/target 을 config 에 붙여넣는다.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

_PRINT_PREFIX = "[LAM/TopView]"
_CAMERA_BINDINGS_PATH = "/exts/omni.kit.viewport.window/bindings/camera"
_DISABLE_KEYS = (
    "disable_pan",
    "disable_zoom",
    "disable_tumble",
    "disable_look",
    "disable_move",
    "disable_fly",
)
_state: dict[str, Any] = {
    "hold_sub": None,
    "active": False,
    "saved_camera_bindings": None,
    "locked_models": [],
}


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


def _get_active_viewport_window() -> Any:
    try:
        from omni.kit.viewport.utility import get_active_viewport_window  # type: ignore

        return get_active_viewport_window()
    except Exception:
        return None


def _is_viewport_camera_manipulator_model(obj: Any) -> bool:
    if obj is None or type(obj).__name__ == "CameraModel":
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


def _try_add_model(models: List[Any], seen: set[int], obj: Any) -> None:
    if obj is None or not _is_viewport_camera_manipulator_model(obj):
        return
    oid = id(obj)
    if oid in seen:
        return
    seen.add(oid)
    models.append(obj)


def _collect_camera_manipulator_models(viewport_api: Any) -> List[Any]:
    """SceneView.model 포함 — 실제 입력에 연결된 manipulator model 탐색."""
    models: List[Any] = []
    seen: set[int] = set()

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

    win = _get_active_viewport_window()
    if win is not None:
        candidates.append(win)
        for attr in (
            "camera_manipulator",
            "_camera_manipulator",
            "manipulator",
            "viewport_widget",
            "_viewport_widget",
            "viewport_frame",
            "_viewport_frame",
        ):
            w = getattr(win, attr, None)
            candidates.append(w)
            if w is None:
                continue
            candidates.append(getattr(w, "model", None))
            candidates.append(getattr(w, "camera_manipulator", None))
            for sv_attr in ("scene_view", "_scene_view"):
                sv = getattr(w, sv_attr, None)
                if sv is not None:
                    candidates.append(getattr(sv, "model", None))
                    candidates.append(sv)
            cm = getattr(w, "camera_manipulator", None)
            if cm is not None:
                candidates.append(getattr(cm, "model", None))

    for obj in candidates:
        _try_add_model(models, seen, obj)
        _try_add_model(models, seen, getattr(obj, "model", None))

    return models


def _zero_manipulator_speeds(model: Any) -> None:
    for key, vals in (
        ("world_speed", [0.0, 0.0, 0.0]),
        ("rotation_speed", [0.0]),
        ("tumble_speed", [0.0]),
        ("look_speed", [0.0]),
        ("fly_speed", [0.0]),
    ):
        try:
            model.set_floats(key, vals)
        except Exception:
            pass


def _set_model_navigation_enabled(model: Any, enabled: bool) -> None:
    flag = 0 if enabled else 1
    try:
        model.set_ints("disable_undo", [1])
    except Exception:
        pass
    for key in _DISABLE_KEYS:
        try:
            model.set_ints(key, [flag])
        except Exception:
            pass
    if not enabled:
        _zero_manipulator_speeds(model)


def _set_manipulator_navigation_enabled(viewport_api: Any, enabled: bool) -> List[Any]:
    models = _collect_camera_manipulator_models(viewport_api)
    for model in models:
        _set_model_navigation_enabled(model, enabled)
    return models


def _save_camera_bindings() -> Any:
    try:
        import carb.settings  # type: ignore

        settings = carb.settings.get_settings()
        return settings.get(_CAMERA_BINDINGS_PATH)
    except Exception:
        return None


def _apply_camera_bindings(bindings: Any) -> None:
    try:
        import carb.settings  # type: ignore

        settings = carb.settings.get_settings()
        settings.set(_CAMERA_BINDINGS_PATH, bindings if bindings is not None else {})
    except Exception:
        pass


def _block_camera_bindings() -> None:
    if _state.get("saved_camera_bindings") is None:
        _state["saved_camera_bindings"] = _save_camera_bindings()
    _apply_camera_bindings({})


def _restore_camera_bindings() -> None:
    saved = _state.get("saved_camera_bindings")
    if saved is None:
        return
    _apply_camera_bindings(saved)
    _state["saved_camera_bindings"] = None


def _reassert_navigation_lock() -> None:
    """입력 차단만 재적용 — 카메라 위치는 건드리지 않음 (snap-back 방지)."""
    if not _state.get("active"):
        return
    try:
        import carb.settings  # type: ignore

        settings = carb.settings.get_settings()
        current = settings.get(_CAMERA_BINDINGS_PATH)
        if current not in (None, {}):
            settings.set(_CAMERA_BINDINGS_PATH, {})
    except Exception:
        pass

    viewport_api = _get_active_viewport_api()
    if viewport_api is None:
        return

    locked: List[Any] = list(_state.get("locked_models") or [])
    seen: set[int] = {id(m) for m in locked}
    for model in _collect_camera_manipulator_models(viewport_api):
        if id(model) not in seen:
            locked.append(model)
            seen.add(id(model))
        _set_model_navigation_enabled(model, False)
    _state["locked_models"] = locked


def get_top_view_preset_snapshot() -> "CameraViewSnapshot":
    from .lam_play_camera_fly import CameraViewSnapshot

    from .lam_viewport_overlay_config import TOP_VIEW_PRESET  # type: ignore

    p = TOP_VIEW_PRESET
    return CameraViewSnapshot(
        eye_xyz=tuple(float(x) for x in p.eye_xyz),
        target_xyz=tuple(float(x) for x in p.target_xyz),
    )


def top_view_preset_configured() -> bool:
    try:
        from .lam_viewport_overlay_config import TOP_VIEW_PRESET_ENABLED  # type: ignore

        if not bool(TOP_VIEW_PRESET_ENABLED):
            return False
    except Exception:
        return False
    try:
        preset = get_top_view_preset_snapshot()
        eye = preset.eye_xyz
        tgt = preset.target_xyz
        dist = (
            (float(tgt[0]) - float(eye[0])) ** 2
            + (float(tgt[1]) - float(eye[1])) ** 2
            + (float(tgt[2]) - float(eye[2])) ** 2
        ) ** 0.5
        return dist >= 0.25
    except Exception:
        return False


def apply_top_view_preset() -> bool:
    from .lam_play_camera_fly import apply_camera_view

    try:
        from .lam_viewport_overlay_config import TOP_VIEW_PRESET  # type: ignore

        up = tuple(float(x) for x in TOP_VIEW_PRESET.up_xyz)
    except Exception:
        up = (0.0, 0.0, 1.0)
    snap = get_top_view_preset_snapshot()
    return bool(apply_camera_view(snap, up_xyz=up))


def _stop_hold_subscription() -> None:
    sub = _state.get("hold_sub")
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
    _state["hold_sub"] = None


def _start_hold_subscription() -> None:
    """카메라 위치 복원이 아니라 입력 잠금만 주기적으로 재적용."""
    _stop_hold_subscription()

    def _tick(_e=None) -> None:
        if not _state.get("active"):
            _stop_hold_subscription()
            return
        _reassert_navigation_lock()

    try:
        import omni.kit.app as _app  # type: ignore

        stream = _app.get_app().get_post_update_event_stream()
        _state["hold_sub"] = stream.create_subscription_to_pop(
            _tick,
            name="morph.lam_control.viewport_top_view_lock",
        )
    except Exception:
        _state["hold_sub"] = None


def _acquire_input_lock(viewport_api: Any) -> None:
    _block_camera_bindings()
    models = _set_manipulator_navigation_enabled(viewport_api, False)
    _state["locked_models"] = models
    _reassert_navigation_lock()


def _release_input_lock(viewport_api: Any) -> None:
    _restore_camera_bindings()
    if viewport_api is not None:
        _set_manipulator_navigation_enabled(viewport_api, True)
    _state["locked_models"] = []


def enable_top_view_mode() -> bool:
    """탑뷰 preset 적용 + 뷰포트 카메라 네비게이션 입력 차단."""
    if _state.get("active"):
        apply_top_view_preset()
        _reassert_navigation_lock()
        return True
    if not top_view_preset_configured():
        print(
            f"{_PRINT_PREFIX} TOP_VIEW_PRESET 미설정 — "
            "뷰저장 후 lam_viewport_overlay_config TOP_VIEW_PRESET 붙여넣기",
            flush=True,
        )
        return False

    viewport_api = _get_active_viewport_api()
    if viewport_api is None:
        print(f"{_PRINT_PREFIX} active viewport 없음", flush=True)
        return False

    if not apply_top_view_preset():
        print(f"{_PRINT_PREFIX} 탑뷰 카메라 적용 실패", flush=True)
        return False

    _acquire_input_lock(viewport_api)
    _state["active"] = True
    _start_hold_subscription()
    print(
        f"{_PRINT_PREFIX} 탑뷰 고정 ON — 카메라 조작 차단 "
        f"(models={len(_state.get('locked_models') or [])})",
        flush=True,
    )
    return True


def disable_top_view_mode() -> None:
    """카메라 조작 잠금 해제 (시점은 유지)."""
    if not _state.get("active") and _state.get("hold_sub") is None:
        return
    viewport_api = _get_active_viewport_api()
    _state["active"] = False
    _stop_hold_subscription()
    _release_input_lock(viewport_api)
    print(f"{_PRINT_PREFIX} 탑뷰 고정 OFF — 카메라 조작 해제", flush=True)


def is_top_view_mode_active() -> bool:
    return bool(_state.get("active"))


def schedule_top_view_after_stage_ready(*, delay_frames: int = 12) -> None:
    """stage 준비 후 STARTUP_CHECK_TOP_VIEW 가 True 이면 탑뷰 모드 적용."""
    try:
        from .lam_viewport_overlay_state import get_toggle_top_view

        if not get_toggle_top_view():
            return
    except Exception:
        return

    frames_left = [max(0, int(delay_frames))]

    def _tick(_e=None) -> None:
        if frames_left[0] > 0:
            frames_left[0] -= 1
            return
        try:
            sub = _state.get("_startup_sub")
            if sub is not None:
                sub.unsubscribe()
                _state["_startup_sub"] = None
        except Exception:
            pass
        enable_top_view_mode()

    try:
        import omni.kit.app as _app  # type: ignore

        stream = _app.get_app().get_post_update_event_stream()
        _state["_startup_sub"] = stream.create_subscription_to_pop(
            _tick,
            name="morph.lam_control.viewport_top_view_startup",
        )
    except Exception:
        pass


__all__ = [
    "apply_top_view_preset",
    "disable_top_view_mode",
    "enable_top_view_mode",
    "get_top_view_preset_snapshot",
    "is_top_view_mode_active",
    "schedule_top_view_after_stage_ready",
    "top_view_preset_configured",
]
