"""Viewport 「탑뷰 보기」 — preset 카메라 고정 + 네비게이션 입력 차단.

설정: ``lam_viewport_overlay_config`` 의 ``TOP_VIEW_PRESET*``.
「뷰저장」 버튼(Play preset 과 공유)으로 캡처한 eye/target 을 config 에 붙여넣는다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

_PRINT_PREFIX = "[LAM/TopView]"
_CAMERA_BINDINGS_PATH = "/exts/omni.kit.viewport.window/bindings/camera"
# Kit omni.kit.viewport.window 기본값 (문서 fallback)
_KIT_DEFAULT_CAMERA_BINDINGS: Dict[str, str] = {
    "PanGesture": "Any MiddleButton",
    "TumbleGesture": "Alt LeftButton",
    "ZoomGesture": "Alt RightButton",
    "LookGesture": "RightButton",
    "ZoomScrollGesture": "Any",
    "FlightSpeedGesture": "RightButton",
    "FlightMode": "RightButton",
}
_VIEWPORT_WINDOW_CANDIDATES: Tuple[str, ...] = (
    "Viewport",
    "LAM Viewport",
    "Scene View",
)
_runtime_default_bindings_cache: Optional[Dict[str, str]] = None
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
    "fly_wait_sub": None,
    "fly_pending": False,
    "nav_restore_sub": None,
    "active": False,
    "lock_snapshot": None,
    "bindings_blocked": False,
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


def _collect_camera_manipulator_models_for_window_name(win_name: str) -> List[Any]:
    """Workspace 창 이름으로 manipulator model 수집 (active viewport 의존 최소화)."""
    models: List[Any] = []
    seen: set[int] = set()

    def _try_add(obj: Any) -> None:
        _try_add_model(models, seen, obj)
        _try_add_model(models, seen, getattr(obj, "model", None))

    api: Any = None
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name  # type: ignore

        api = get_viewport_from_window_name(str(win_name))
    except Exception:
        api = None
    if api is not None:
        for attr in (
            "camera_manipulator",
            "_camera_manipulator",
            "manipulator",
            "camera_model",
            "_camera_model",
        ):
            _try_add(getattr(api, attr, None))
    vp_win: Any = None
    if api is not None:
        for attr in ("viewport_window", "window", "_viewport_window", "_window"):
            cand = getattr(api, attr, None)
            if cand is not None and callable(getattr(cand, "get_frame", None)):
                vp_win = cand
                break
    if vp_win is None:
        try:
            import omni.ui as ui  # type: ignore

            w = ui.Workspace.get_window(str(win_name))
        except Exception:
            w = None
        if w is not None:
            for attr in ("viewport_window", "viewport", "_viewport_window"):
                cand = getattr(w, attr, None)
                if cand is not None:
                    vp_win = cand
                    break
    if vp_win is not None:
        for attr in (
            "camera_manipulator",
            "_camera_manipulator",
            "manipulator",
            "viewport_widget",
            "_viewport_widget",
            "viewport_frame",
            "_viewport_frame",
        ):
            w = getattr(vp_win, attr, None)
            _try_add(w)
            if w is not None:
                _try_add(getattr(w, "camera_manipulator", None))
                cm = getattr(w, "camera_manipulator", None)
                if cm is not None:
                    _try_add(getattr(cm, "model", None))
                for sv_attr in ("scene_view", "_scene_view"):
                    sv = getattr(w, sv_attr, None)
                    if sv is not None:
                        _try_add(getattr(sv, "model", None))
    return models


def _collect_camera_manipulator_models(
    viewport_api: Any,
    *,
    active_only: bool = False,
) -> List[Any]:
    """SceneView.model 포함 — 실제 입력에 연결된 manipulator model 탐색.

    ``active_only=True`` 이면 활성 viewport 만 (탑뷰 잠금·복구용).
    비활성 Persp manipulator 에 disable 플래그를 걸면 OFF 후 조작 불가가 된다.
    """
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

    if not active_only:
        for win_name in _VIEWPORT_WINDOW_CANDIDATES:
            for model in _collect_camera_manipulator_models_for_window_name(win_name):
                oid = id(model)
                if oid not in seen:
                    seen.add(oid)
                    models.append(model)

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
        model.set_ints("disable_undo", [0 if enabled else 1])
    except Exception:
        pass
    for key in _DISABLE_KEYS:
        try:
            model.set_ints(key, [flag])
        except Exception:
            pass
    if not enabled:
        _zero_manipulator_speeds(model)


def _set_manipulator_navigation_enabled(
    viewport_api: Any,
    enabled: bool,
    *,
    active_only: bool = False,
) -> List[Any]:
    models = _collect_camera_manipulator_models(
        viewport_api,
        active_only=active_only,
    )
    for model in models:
        _set_model_navigation_enabled(model, enabled)
    return models


def _set_viewport_input_enabled(viewport_api: Any, enabled: bool) -> None:
    """viewport / window / widget 의 마우스·키 입력 허용 여부."""
    targets: List[Any] = []
    if viewport_api is not None:
        targets.append(viewport_api)
    win = _get_active_viewport_window()
    if win is not None:
        targets.append(win)
        for attr in ("viewport_widget", "_viewport_widget"):
            widget = getattr(win, attr, None)
            if widget is not None:
                targets.append(widget)
    for obj in targets:
        for attr in ("enable_input", "inputs_enabled", "enabled"):
            if hasattr(obj, attr):
                try:
                    setattr(obj, attr, bool(enabled))
                except Exception:
                    pass


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


def _capture_runtime_default_bindings() -> Dict[str, str]:
    """Kit 기본 camera bindings — 잠금 전에 캡처하거나 문서 fallback 사용."""
    global _runtime_default_bindings_cache
    if _runtime_default_bindings_cache is not None:
        return dict(_runtime_default_bindings_cache)
    current = _save_camera_bindings()
    if isinstance(current, dict) and current:
        _runtime_default_bindings_cache = dict(current)
    else:
        _runtime_default_bindings_cache = dict(_KIT_DEFAULT_CAMERA_BINDINGS)
    return dict(_runtime_default_bindings_cache)


def warmup_camera_bindings_defaults() -> None:
    """stage 준비 직후 — bindings 캡처 + 오염된 Persp 뷰 1회 sanitize 예약."""
    _capture_runtime_default_bindings()
    _force_recover_camera_bindings(quiet=True)
    try:
        from .lam_play_camera_fly import schedule_startup_perspective_sanitize

        schedule_startup_perspective_sanitize(delay_frames=16)
    except Exception:
        pass


def _bindings_to_restore(saved: Any) -> Dict[str, str]:
    if isinstance(saved, dict) and saved:
        return dict(saved)
    return dict(_capture_runtime_default_bindings())


def _block_camera_bindings() -> None:
    """레거시 — 더 이상 탑뷰 잠금에 사용하지 않음 ({} 설정 시 Kit 에서 복구 불가)."""
    pass


def _force_recover_camera_bindings(*, quiet: bool = False) -> None:
    """carb bindings 가 {} 이거나 깨진 경우 Kit 기본 gesture 로 강제 복구."""
    current = _save_camera_bindings()
    needs_fix = (
        _state.get("bindings_blocked")
        or current is None
        or current == {}
    )
    if not needs_fix:
        return
    restore_to = dict(_capture_runtime_default_bindings())
    paths = (
        _CAMERA_BINDINGS_PATH,
        "/persistent/exts/omni.kit.viewport.window/bindings/camera",
    )
    try:
        import carb.settings  # type: ignore

        settings = carb.settings.get_settings()
        for path in paths:
            try:
                settings.destroy(path)
            except Exception:
                pass
            try:
                settings.set(path, restore_to)
            except Exception:
                pass
        _state["bindings_blocked"] = False
        _state["saved_camera_bindings"] = None
        if not quiet:
            print(
                f"{_PRINT_PREFIX} camera bindings 강제 복구 "
                f"({len(restore_to)} gestures)",
                flush=True,
            )
    except Exception as exc:
        if not quiet:
            print(
                f"{_PRINT_PREFIX} camera bindings 강제 복구 실패: {exc}",
                flush=True,
            )


def _restore_camera_bindings() -> None:
    _force_recover_camera_bindings()


def ensure_active_viewport_navigation_enabled() -> None:
    """orbit/pan/zoom — manipulator·viewport input 재활성화 (활성 viewport 만)."""
    viewport_api = _get_active_viewport_api()
    for model in list(_state.get("locked_models") or []):
        _set_model_navigation_enabled(model, True)
    models = _collect_camera_manipulator_models(viewport_api, active_only=True)
    for model in models:
        _set_model_navigation_enabled(model, True)
    _set_viewport_input_enabled(viewport_api, True)
    if viewport_api is not None:
        _set_manipulator_navigation_enabled(viewport_api, True, active_only=True)
    _state["locked_models"] = []


def _stop_nav_restore_subscription() -> None:
    sub = _state.get("nav_restore_sub")
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
    _state["nav_restore_sub"] = None


def schedule_restore_viewport_navigation(*, delay_frames: int = 8) -> None:
    """Camera prim / Perspective 전환 직후 manipulator 재부착 대기."""
    _stop_nav_restore_subscription()
    frames_left = [max(1, int(delay_frames))]

    def _tick(_e=None) -> None:
        if frames_left[0] > 0:
            frames_left[0] -= 1
            return
        _stop_nav_restore_subscription()
        _force_recover_camera_bindings(quiet=True)
        ensure_active_viewport_navigation_enabled()

    try:
        import omni.kit.app as _app  # type: ignore

        stream = _app.get_app().get_post_update_event_stream()
        _state["nav_restore_sub"] = stream.create_subscription_to_pop(
            _tick,
            name="morph.lam_control_1.viewport_nav_restore",
        )
    except Exception:
        _force_recover_camera_bindings(quiet=True)
        ensure_active_viewport_navigation_enabled()


def restore_viewport_camera_navigation(*, schedule_frames: int = 8) -> None:
    """탑뷰 잠금·Camera prim 전환 후 화면 조작 복구."""
    _force_recover_camera_bindings()
    ensure_active_viewport_navigation_enabled()
    if int(schedule_frames) > 0:
        schedule_restore_viewport_navigation(delay_frames=int(schedule_frames))


def _capture_lock_snapshot() -> Optional["CameraViewSnapshot"]:
    from .lam_play_camera_fly import (
        CameraViewSnapshot,
        capture_current_view,
        ensure_camera_prim_baseline,
        get_camera_prim_baseline_view,
        get_top_view_target_snapshot,
        top_view_assign_prim_path,
        top_view_use_preset_coords,
    )

    if not top_view_use_preset_coords():
        path = top_view_assign_prim_path()
        if path:
            ensure_camera_prim_baseline(path)
            view = get_camera_prim_baseline_view(path)
            if view is not None:
                return view
    snap = capture_current_view()
    if snap is not None:
        return snap
    return get_top_view_target_snapshot()


def _apply_lock_snapshot(snap: "CameraViewSnapshot") -> None:
    from .lam_play_camera_fly import (
        apply_session_view_to_target,
        get_up_for_top_view_target,
        set_viewport_camera_prim_path,
        top_view_assign_prim_path,
        top_view_use_preset_coords,
    )

    if top_view_use_preset_coords():
        apply_session_view_to_target(
            snap,
            up_xyz=get_up_for_top_view_target(),
            log_context="top_view_lock",
        )
        return
    path = top_view_assign_prim_path()
    if path:
        set_viewport_camera_prim_path(path)


def _reassert_navigation_lock() -> None:
    """탑뷰 고정 — Camera prim: bind 유지만. preset: 시점 스냅백."""
    if not _state.get("active"):
        return
    from .lam_play_camera_fly import (
        capture_current_view,
        set_viewport_camera_prim_path,
        top_view_assign_prim_path,
        top_view_use_preset_coords,
        views_are_close,
    )

    if not top_view_use_preset_coords():
        path = top_view_assign_prim_path()
        if path:
            try:
                from .lam_play_camera_fly import _active_camera_path_str

                active = str(_active_camera_path_str() or "")
            except Exception:
                active = ""
            if active != path:
                set_viewport_camera_prim_path(path)
    else:
        snap = _state.get("lock_snapshot")
        if snap is not None:
            current = capture_current_view()
            if current is not None and not views_are_close(current, snap):
                _apply_lock_snapshot(snap)

    viewport_api = _get_active_viewport_api()
    if viewport_api is None:
        return

    locked: List[Any] = list(_state.get("locked_models") or [])
    seen: set[int] = {id(m) for m in locked}
    for model in _collect_camera_manipulator_models(viewport_api, active_only=True):
        if id(model) not in seen:
            locked.append(model)
            seen.add(id(model))
        _set_model_navigation_enabled(model, False)
    _state["locked_models"] = locked
    _set_viewport_input_enabled(viewport_api, False)


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


def apply_top_view_target() -> bool:
    """탑뷰 시점 적용 — camera prim 모드는 즉시 bind, preset 모드만 Persp 좌표 적용."""
    from .lam_play_camera_fly import (
        _finish_fly_to_target,
        apply_top_view_camera_prim_view_spec,
        ensure_camera_prim_baseline,
        ensure_session_perspective_camera,
        get_session_fly_up_xyz,
        get_top_view_target_snapshot,
        top_view_assign_prim_path,
        top_view_camera_prim_path,
        top_view_use_preset_coords,
    )

    if top_view_use_preset_coords():
        return apply_top_view_preset()
    prim_path = top_view_assign_prim_path()
    if prim_path:
        apply_top_view_camera_prim_view_spec()
        ensure_camera_prim_baseline(prim_path)
    ensure_session_perspective_camera(
        log_label="top_view_apply",
        restore_navigation=False,
    )
    target = get_top_view_target_snapshot()
    if target is None:
        print(
            f"{_PRINT_PREFIX} TOP_VIEW camera prim snapshot 실패 — "
            f"path={top_view_camera_prim_path()!r}",
            flush=True,
        )
        return False
    up = get_session_fly_up_xyz(top_view=True)
    return _finish_fly_to_target(
        target,
        up_xyz=up,
        assign_prim_path=top_view_assign_prim_path(),
        log_context="top_view_bind",
    )


def _stop_fly_wait_subscription() -> None:
    sub = _state.get("fly_wait_sub")
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
    _state["fly_wait_sub"] = None


def _cancel_top_view_fly() -> None:
    _state["fly_pending"] = False
    _stop_fly_wait_subscription()
    timer = _state.pop("fly_timeout_timer", None)
    if timer is not None:
        try:
            timer.cancel()
        except Exception:
            pass


def _finish_enable_top_view(viewport_api: Any) -> None:
    _acquire_input_lock(viewport_api)
    _state["active"] = True
    _start_hold_subscription()
    models = len(_collect_camera_manipulator_models(viewport_api, active_only=True))
    print(
        f"{_PRINT_PREFIX} 탑뷰 고정 ON — 카메라 조작 차단 (models={models})",
        flush=True,
    )


def _revert_top_view_toggle() -> None:
    try:
        from .lam_viewport_overlay_state import set_toggle_top_view

        set_toggle_top_view(False, from_ui_model=True)
    except Exception:
        pass


def _start_camera_prim_fly_async(viewport_api: Any) -> bool:
    """Camera prim 모드 — UI main 스레드에서 비동기 fly 후 잠금."""
    import threading

    from .lam_play_camera_fly import (
        _PERSP_CAMERA_PATH,
        apply_camera_view,
        apply_top_view_camera_prim_view_spec,
        capture_current_view,
        ensure_camera_prim_baseline,
        ensure_session_perspective_camera,
        get_session_fly_up_xyz,
        get_top_view_target_snapshot,
        kickoff_fly_to_target,
        top_view_assign_prim_path,
        top_view_camera_prim_path,
    )

    prim = top_view_assign_prim_path()
    current = capture_current_view()
    if current is None:
        return False
    up = get_session_fly_up_xyz(top_view=True)
    if prim:
        apply_top_view_camera_prim_view_spec()
        ensure_camera_prim_baseline(prim)
    ensure_session_perspective_camera(
        log_label="top_view_fly",
        restore_navigation=False,
    )
    if current is not None:
        apply_camera_view(
            current,
            up_xyz=up,
            camera_path=_PERSP_CAMERA_PATH,
        )
    _set_viewport_input_enabled(viewport_api, False)
    target = get_top_view_target_snapshot()
    if target is None:
        print(
            f"{_PRINT_PREFIX} TOP_VIEW camera prim snapshot 실패 — "
            f"path={top_view_camera_prim_path()!r}",
            flush=True,
        )
        return False
    done = threading.Event()
    _cancel_top_view_fly()
    _state["fly_pending"] = True

    if not kickoff_fly_to_target(
        target,
        done,
        assign_prim_path=prim,
        up_xyz=up,
        log_context="top_view",
    ):
        _state["fly_pending"] = False
        return False

    def _poll(_e=None) -> None:
        if not done.is_set():
            return
        _stop_fly_wait_subscription()
        timer = _state.pop("fly_timeout_timer", None)
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
        if not _state.get("fly_pending"):
            return
        _state["fly_pending"] = False
        _finish_enable_top_view(viewport_api)

    def _poll_timeout() -> None:
        if done.is_set():
            return
        _stop_fly_wait_subscription()
        if not _state.get("fly_pending"):
            return
        _state["fly_pending"] = False
        print(f"{_PRINT_PREFIX} 탑뷰 fly 타임아웃", flush=True)
        _revert_top_view_toggle()

    try:
        import omni.kit.app as _app  # type: ignore

        stream = _app.get_app().get_update_event_stream()
        _state["fly_wait_sub"] = stream.create_subscription_to_pop(
            _poll,
            name="morph.lam_control_1.viewport_top_view_fly",
        )
    except Exception:
        _state["fly_pending"] = False
        return False

    wait_sec = 0.0
    try:
        from .lam_play_camera_fly import play_camera_fly_duration_sec

        wait_sec = float(play_camera_fly_duration_sec()) + 8.0
    except Exception:
        wait_sec = 10.0
    timer = threading.Timer(wait_sec, _poll_timeout)
    timer.daemon = True
    _state["fly_timeout_timer"] = timer
    timer.start()
    return True


def _stop_hold_subscription() -> None:
    sub = _state.get("hold_sub")
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
    _state["hold_sub"] = None


def _start_hold_subscription() -> None:
    """탑뷰 시점 유지 — 이탈 시 스냅백."""
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
            name="morph.lam_control_1.viewport_top_view_lock",
        )
    except Exception:
        _state["hold_sub"] = None


def _acquire_input_lock(viewport_api: Any) -> None:
    _state["lock_snapshot"] = _capture_lock_snapshot()
    _set_viewport_input_enabled(viewport_api, False)
    models = _set_manipulator_navigation_enabled(
        viewport_api,
        False,
        active_only=True,
    )
    _state["locked_models"] = models


def _release_input_lock(viewport_api: Any) -> None:
    """잠금 상태만 해제 — 조작 복구는 Perspective 전환 후 schedule_restore 에서."""
    _state["lock_snapshot"] = None
    _state["locked_models"] = []


def enable_top_view_mode() -> bool:
    """탑뷰 시점 적용 + 뷰포트 카메라 네비게이션 입력 차단."""
    from .lam_play_camera_fly import (
        top_view_camera_prim_path,
        top_view_target_configured,
        top_view_use_preset_coords,
    )

    if _state.get("fly_pending"):
        return True
    if _state.get("active"):
        if apply_top_view_target():
            _state["lock_snapshot"] = _capture_lock_snapshot()
            _reassert_navigation_lock()
            return True
        return False
    if not top_view_target_configured():
        if top_view_use_preset_coords():
            print(
                f"{_PRINT_PREFIX} TOP_VIEW_PRESET 미설정 — "
                "뷰저장 후 lam_viewport_overlay_config TOP_VIEW_PRESET 붙여넣기",
                flush=True,
            )
        else:
            print(
                f"{_PRINT_PREFIX} TOP_VIEW_CAMERA_PRIM_PATH 미설정 또는 "
                "Camera prim 읽기 실패 — lam_viewport_overlay_config 확인",
                flush=True,
            )
        return False

    viewport_api = _get_active_viewport_api()
    if viewport_api is None:
        print(f"{_PRINT_PREFIX} active viewport 없음", flush=True)
        return False

    if not top_view_use_preset_coords():
        from .lam_play_camera_fly import ensure_session_perspective_camera

        ensure_session_perspective_camera(
            log_label="top_view_enable",
            restore_navigation=False,
        )
        _set_viewport_input_enabled(viewport_api, False)

    if apply_top_view_target():
        _finish_enable_top_view(viewport_api)
        return True

    if top_view_use_preset_coords():
        print(f"{_PRINT_PREFIX} 탑뷰 카메라 적용 실패", flush=True)
        return False

    print(
        f"{_PRINT_PREFIX} 탑뷰 Camera prim bind 실패 — "
        f"path={top_view_camera_prim_path()!r} 확인",
        flush=True,
    )
    return False


def disable_top_view_mode() -> None:
    """카메라 조작 잠금 해제. camera 모드면 Perspective 복귀."""
    from .lam_play_camera_fly import (
        ensure_session_perspective_camera,
        top_view_use_preset_coords,
    )

    fly_was_pending = bool(_state.get("fly_pending"))
    _cancel_top_view_fly()
    timer = _state.pop("fly_timeout_timer", None)
    if timer is not None:
        try:
            timer.cancel()
        except Exception:
            pass
    if not _state.get("active") and _state.get("hold_sub") is None and not fly_was_pending:
        return
    viewport_api = _get_active_viewport_api()
    _state["active"] = False
    _stop_hold_subscription()
    _release_input_lock(viewport_api)
    if not top_view_use_preset_coords():
        from .lam_play_camera_fly import restore_kit_default_perspective

        restore_kit_default_perspective(log_label="top_view_off")
    else:
        ensure_session_perspective_camera(
            log_label="top_view_off",
            restore_navigation=False,
        )
    if not top_view_use_preset_coords():
        print(
            f"{_PRINT_PREFIX} 탑뷰 고정 OFF — Perspective 복귀 + 카메라 조작 해제",
            flush=True,
        )
    else:
        print(f"{_PRINT_PREFIX} 탑뷰 고정 OFF — 카메라 조작 해제", flush=True)
    schedule_restore_viewport_navigation(delay_frames=12)


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
            name="morph.lam_control_1.viewport_top_view_startup",
        )
    except Exception:
        pass


_per_viewport_states: Dict[int, dict] = {}


def _vp_top_view_state(viewport_api: Any) -> dict:
    key = id(viewport_api)
    st = _per_viewport_states.get(key)
    if st is None:
        st = {"active": False, "locked_models": []}
        _per_viewport_states[key] = st
    return st


def _acquire_input_lock_for_viewport(viewport_api: Any, st: dict) -> None:
    models = _set_manipulator_navigation_enabled(
        viewport_api,
        False,
        active_only=True,
    )
    st["locked_models"] = models


def _release_input_lock_for_viewport(st: dict) -> None:
    st["locked_models"] = []


def sync_top_view_on_viewport(
    viewport_api: Any,
    *,
    enabled: bool,
    usd_context_name: str = "",
    screen: int = 0,
) -> bool:
    """레거시 API — ``lam_csv_screen_runtime.apply_top_view_for_screen`` 로 위임."""
    if viewport_api is None:
        return False
    si = max(0, int(screen))
    if si <= 1 and not str(usd_context_name or "").strip():
        if enabled:
            return bool(enable_top_view_mode())
        disable_top_view_mode()
        return True
    from .lam_csv_screen_runtime import CsvScreenRuntime, apply_top_view_for_screen

    runtime = CsvScreenRuntime(
        screen=max(2, si) if si > 0 else 2,
        context_name=str(usd_context_name or "").strip() or None,
        stage=None,
        registry=None,
        scheduler=None,
        master=None,
        viewport_window=None,
        viewport_api=viewport_api,
        csv_window=None,
        lam_window=None,
    )
    return apply_top_view_for_screen(runtime, enabled=enabled)


__all__ = [
    "apply_top_view_preset",
    "apply_top_view_target",
    "disable_top_view_mode",
    "enable_top_view_mode",
    "ensure_active_viewport_navigation_enabled",
    "get_top_view_preset_snapshot",
    "is_top_view_mode_active",
    "restore_viewport_camera_navigation",
    "schedule_restore_viewport_navigation",
    "schedule_top_view_after_stage_ready",
    "sync_top_view_on_viewport",
    "top_view_preset_configured",
    "warmup_camera_bindings_defaults",
]
