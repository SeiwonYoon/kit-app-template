"""화면1·2 표시 상태 — 두 런타임(USD/context)은 유지하고 Viewport Dock 창만 전환.

lam_control_1 ``lam_screen_visibility`` 와 동일 계열.
웹 ``T2V_request_screen_visibility`` · 제어창 체크박스가 이 모듈을 SSOT 로 사용한다.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional, Tuple

_PRINT_PREFIX = "[TBS/ScreenVisibility]"


def _read_bool(model: Any) -> bool:
    if model is None:
        return False
    for name in ("get_value_as_bool", "get_value"):
        try:
            fn = getattr(model, name, None)
            if callable(fn):
                return bool(fn())
        except Exception:
            pass
    try:
        return bool(model.as_bool)
    except Exception:
        return False


def visible_screens(ext: Any) -> Tuple[bool, bool]:
    """현재 요청된 화면 표시 상태."""
    if ext is None:
        return True, True
    m1 = getattr(ext, "_tbs_show_screen_1_model", None)
    m2 = getattr(ext, "_tbs_show_screen_2_model", None)
    if m1 is not None and m2 is not None:
        show_1, show_2 = _read_bool(m1), _read_bool(m2)
    else:
        try:
            from .sim_control_defaults import default_visible_screens

            show_1, show_2 = default_visible_screens()
        except Exception:
            show_1, show_2 = True, True
    if not show_1 and not show_2:
        show_1 = True
    return bool(show_1), bool(show_2)


def init_screen_visibility_models(ext: Any) -> None:
    """Extension 수명의 화면 표시 모델 생성 및 콜백 연결."""
    if ext is None or getattr(ext, "_tbs_screen_visibility_models_ready", False):
        return
    try:
        from omni.ui import SimpleBoolModel  # type: ignore
    except Exception:
        return
    show_1, show_2 = visible_screens(ext)
    ext._tbs_show_screen_1_model = SimpleBoolModel(show_1)
    ext._tbs_show_screen_2_model = SimpleBoolModel(show_2)
    ext._tbs_screen_visibility_syncing = False
    ext._tbs_screen_visibility_models_ready = True
    ext._tbs_screen_visibility_applied = None
    ext._tbs_screen_visibility_generation = 0

    def _changed(changed_screen: int) -> Callable[..., None]:
        def _on_changed(*_args: Any) -> None:
            if bool(getattr(ext, "_tbs_screen_visibility_syncing", False)):
                return
            show_a = _read_bool(ext._tbs_show_screen_1_model)
            show_b = _read_bool(ext._tbs_show_screen_2_model)
            if not show_a and not show_b:
                try:
                    ext._tbs_screen_visibility_syncing = True
                    model = (
                        ext._tbs_show_screen_1_model
                        if int(changed_screen) == 1
                        else ext._tbs_show_screen_2_model
                    )
                    model.set_value(True)
                finally:
                    ext._tbs_screen_visibility_syncing = False
                print(
                    f"{_PRINT_PREFIX} 둘 다 OFF 거부 — 화면{changed_screen} ON 복원",
                    flush=True,
                )
                return
            request_screen_visibility(ext, show_a, show_b)

        return _on_changed

    for screen, model in (
        (1, ext._tbs_show_screen_1_model),
        (2, ext._tbs_show_screen_2_model),
    ):
        for hook in ("add_value_changed_fn", "add_item_changed_fn"):
            try:
                fn = getattr(model, hook, None)
                if callable(fn):
                    fn(_changed(screen))
                    break
            except Exception:
                pass


def mount_screen_visibility_checkboxes(
    ext: Any,
    ui: Any,
    *,
    row_height: int = 22,
) -> None:
    """제어창·HUD 의 화면1·화면2 표시 체크박스."""
    init_screen_visibility_models(ext)
    m1 = getattr(ext, "_tbs_show_screen_1_model", None)
    m2 = getattr(ext, "_tbs_show_screen_2_model", None)
    if m1 is None or m2 is None:
        return
    with ui.HStack(spacing=6, height=int(row_height)):
        ui.Label("화면 표시", width=56, height=int(row_height))
        ui.Label("화면1", width=38, height=int(row_height))
        ui.CheckBox(
            model=m1,
            width=20,
            height=int(row_height),
            tooltip="화면1 표시 (마지막 화면은 끌 수 없음)",
        )
        ui.Label("화면2", width=38, height=int(row_height))
        ui.CheckBox(
            model=m2,
            width=20,
            height=int(row_height),
            tooltip="화면2 표시 (마지막 화면은 끌 수 없음)",
        )
        ui.Spacer()


def _set_models(ext: Any, show_1: bool, show_2: bool) -> None:
    try:
        ext._tbs_screen_visibility_syncing = True
        ext._tbs_show_screen_1_model.set_value(bool(show_1))
        ext._tbs_show_screen_2_model.set_value(bool(show_2))
    finally:
        ext._tbs_screen_visibility_syncing = False


def request_screen_visibility(
    ext: Any,
    show_1: bool,
    show_2: bool,
    *,
    startup: bool = False,
    on_complete: Optional[Callable[[], None]] = None,
) -> None:
    """표시 전환 요청. 두 stage/runtime 은 유지하고 Dock 창만 전환한다."""
    if ext is None:
        if callable(on_complete):
            try:
                on_complete()
            except Exception:
                pass
        return
    init_screen_visibility_models(ext)
    show_1, show_2 = bool(show_1), bool(show_2)
    if not show_1 and not show_2:
        current = visible_screens(ext)
        show_1, show_2 = current if any(current) else (True, False)
    _set_models(ext, show_1, show_2)

    desired = (show_1, show_2)
    previous = getattr(ext, "_tbs_screen_visibility_applied", None)

    def _notify_done() -> None:
        if not callable(on_complete):
            return
        try:
            on_complete()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} on_complete 실패: {exc}", flush=True)

    if (not startup) and previous == desired:
        _notify_done()
        return

    gen = int(getattr(ext, "_tbs_screen_visibility_generation", 0) or 0) + 1
    ext._tbs_screen_visibility_generation = gen

    async def _run_layout_then_notify() -> None:
        try:
            await _apply_layout_async(ext, desired, gen)
        finally:
            if int(getattr(ext, "_tbs_screen_visibility_generation", 0) or 0) == gen:
                _notify_done()

    try:
        asyncio.ensure_future(_run_layout_then_notify())
    except Exception as exc:
        print(f"{_PRINT_PREFIX} layout schedule 실패: {exc}", flush=True)
        _notify_done()


def _revive_viewport_window(win_name: str) -> None:
    wn = str(win_name or "").strip()
    if not wn:
        return
    try:
        from .sim_multi_view import (
            _ensure_viewport_camera_navigation_enabled,
            _split_viewport_api,
            _sync_viewport_resolution_from_workspace_window,
            _workspace_show_named_window,
        )

        _workspace_show_named_window(wn, True)
        api = _split_viewport_api(wn)
        if api is not None:
            for attr, value in (
                ("fill_frame", True),
                ("updates_enabled", True),
                ("enabled", True),
                ("enable_input", True),
                ("inputs_enabled", True),
            ):
                if hasattr(api, attr):
                    try:
                        setattr(api, attr, value)
                    except Exception:
                        pass
        _sync_viewport_resolution_from_workspace_window(wn)
        _ensure_viewport_camera_navigation_enabled(wn)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} revive 실패 win={wn!r}: {exc}", flush=True)


async def _wake_visible_viewports_after_layout(
    ext: Any,
    show_1: bool,
    show_2: bool,
    generation: int,
) -> None:
    try:
        import omni.kit.app as kit_app  # type: ignore
    except Exception:
        kit_app = None
    from .sim_multi_view import (
        _ensure_viewport_camera_navigation_enabled,
        _schedule_split_viewport_input_ready,
        set_viewport_fill_frame_for_split_count,
        wake_main_viewport_after_master_open,
    )

    aux_name = "TBS_SimSplit_1"
    if kit_app is not None:
        for _ in range(4):
            if int(getattr(ext, "_tbs_screen_visibility_generation", 0) or 0) != generation:
                return
            await kit_app.get_app().next_update_async()

    set_viewport_fill_frame_for_split_count(2, True)
    if show_1:
        try:
            await wake_main_viewport_after_master_open(ext, 2)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} wake_main 실패: {exc}", flush=True)
        _revive_viewport_window("Viewport")
        _schedule_split_viewport_input_ready("Viewport", frames=12)
    if show_2:
        _revive_viewport_window(aux_name)
        _schedule_split_viewport_input_ready(aux_name, frames=12)

    if show_1 and show_2:
        _ensure_viewport_camera_navigation_enabled("Viewport")
        _ensure_viewport_camera_navigation_enabled(aux_name)

    if kit_app is not None:
        for _ in range(6):
            if int(getattr(ext, "_tbs_screen_visibility_generation", 0) or 0) != generation:
                return
            await kit_app.get_app().next_update_async()
        if show_1:
            _revive_viewport_window("Viewport")
        if show_2:
            _revive_viewport_window(aux_name)


async def _apply_layout_async(
    ext: Any,
    desired: Tuple[bool, bool],
    generation: int,
) -> None:
    """Dock 창 가시성과 50:50/100% 레이아웃 적용."""
    try:
        import omni.kit.app as kit_app  # type: ignore
    except Exception:
        kit_app = None
    show_1, show_2 = desired
    if int(getattr(ext, "_tbs_screen_visibility_generation", 0) or 0) != generation:
        return
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active

        if is_split_widget_layout_active(ext):
            print(
                f"{_PRINT_PREFIX} Widget 표시 전환은 지원하지 않음 — Dock 모드 필요",
                flush=True,
            )
            return
    except Exception:
        pass

    from .sim_multi_view import (
        _apply_split_dock_layout,
        _sync_viewport_resolution_from_workspace_window,
        _workspace_show_named_window,
        apply_viewport_split_user_resize_lock,
        set_viewport_fill_frame_for_split_count,
        teardown_viewport_split_resize_lock,
    )

    aux_name = "TBS_SimSplit_1"
    if show_2:
        _workspace_show_named_window(aux_name, True)
    if show_1:
        _workspace_show_named_window("Viewport", True)
    if kit_app is not None:
        await kit_app.get_app().next_update_async()

    if show_1 and show_2:
        token = int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0)
        await _apply_split_dock_layout(ext, token, 2, warn_on_dock_miss=False)
        _workspace_show_named_window("Viewport", True)
        _workspace_show_named_window(aux_name, True)
        apply_viewport_split_user_resize_lock(ext)
    else:
        teardown_viewport_split_resize_lock(ext)
        _workspace_show_named_window("Viewport", show_1)
        _workspace_show_named_window(aux_name, show_2)

    if kit_app is not None:
        for _ in range(3):
            await kit_app.get_app().next_update_async()
    if int(getattr(ext, "_tbs_screen_visibility_generation", 0) or 0) != generation:
        return

    set_viewport_fill_frame_for_split_count(2, True)
    if show_1:
        _sync_viewport_resolution_from_workspace_window("Viewport")
    if show_2:
        _sync_viewport_resolution_from_workspace_window(aux_name)

    await _wake_visible_viewports_after_layout(ext, show_1, show_2, generation)
    if int(getattr(ext, "_tbs_screen_visibility_generation", 0) or 0) != generation:
        return

    ext._tbs_screen_visibility_applied = desired

    # HUD 를 보이는 화면에 재부착
    try:
        hud = getattr(ext, "_tbs_viewport_control_hud", None)
        if hud is not None:
            hud.sync_layers(delay_frames=2, force=True)
    except Exception:
        pass

    mode = "both" if show_1 and show_2 else ("screen1" if show_1 else "screen2")
    print(f"{_PRINT_PREFIX} 적용 완료 mode={mode}", flush=True)


def apply_startup_screen_visibility(ext: Any) -> None:
    """두 화면 Dock 준비 후 defaults 의 초기 표시 상태 적용."""
    if bool(getattr(ext, "_tbs_startup_screen_visibility_requested", False)):
        return
    ext._tbs_startup_screen_visibility_requested = True
    init_screen_visibility_models(ext)
    show_1, show_2 = visible_screens(ext)
    request_screen_visibility(ext, show_1, show_2, startup=True)


__all__ = [
    "apply_startup_screen_visibility",
    "init_screen_visibility_models",
    "mount_screen_visibility_checkboxes",
    "request_screen_visibility",
    "visible_screens",
]
