"""화면1·2 표시 상태 — 두 런타임은 유지하고 Dock 창만 전환."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional, Tuple

_PRINT_PREFIX = "[LAM/ScreenVisibility]"


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
    m1 = getattr(ext, "_lam_show_screen_1_model", None)
    m2 = getattr(ext, "_lam_show_screen_2_model", None)
    if m1 is not None and m2 is not None:
        show_1, show_2 = _read_bool(m1), _read_bool(m2)
    else:
        try:
            from .lam_sim_control_defaults import default_visible_screens

            show_1, show_2 = default_visible_screens()
        except Exception:
            show_1, show_2 = True, True
    if not show_1 and not show_2:
        show_1 = True
    return bool(show_1), bool(show_2)


def init_screen_visibility_models(ext: Any) -> None:
    """Extension 수명의 화면 표시 모델 생성 및 콜백 연결."""
    if ext is None or getattr(ext, "_lam_screen_visibility_models_ready", False):
        return
    try:
        from omni.ui import SimpleBoolModel  # type: ignore
    except Exception:
        return
    show_1, show_2 = visible_screens(ext)
    ext._lam_show_screen_1_model = SimpleBoolModel(show_1)
    ext._lam_show_screen_2_model = SimpleBoolModel(show_2)
    ext._lam_screen_visibility_syncing = False
    ext._lam_screen_visibility_models_ready = True
    ext._lam_screen_visibility_applied = None
    ext._lam_screen_visibility_generation = 0

    def _changed(changed_screen: int) -> Callable[..., None]:
        def _on_changed(*_args: Any) -> None:
            if bool(getattr(ext, "_lam_screen_visibility_syncing", False)):
                return
            show_a = _read_bool(ext._lam_show_screen_1_model)
            show_b = _read_bool(ext._lam_show_screen_2_model)
            if not show_a and not show_b:
                # 마지막 화면 OFF 금지: 사용자가 방금 끈 화면을 즉시 복원.
                try:
                    ext._lam_screen_visibility_syncing = True
                    model = (
                        ext._lam_show_screen_1_model
                        if int(changed_screen) == 1
                        else ext._lam_show_screen_2_model
                    )
                    model.set_value(True)
                finally:
                    ext._lam_screen_visibility_syncing = False
                print(
                    f"{_PRINT_PREFIX} 둘 다 OFF 거부 — 화면{changed_screen} ON 복원",
                    flush=True,
                )
                return
            request_screen_visibility(ext, show_a, show_b)

        return _on_changed

    for screen, model in (
        (1, ext._lam_show_screen_1_model),
        (2, ext._lam_show_screen_2_model),
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
    """우측 상단 HUD의 화면1·화면2 표시 체크박스."""
    init_screen_visibility_models(ext)
    m1 = getattr(ext, "_lam_show_screen_1_model", None)
    m2 = getattr(ext, "_lam_show_screen_2_model", None)
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


def hud_viewport_window(ext: Any) -> Any:
    """HUD를 현재 보이는 화면에 붙인다. 화면1 우선, 화면2 단독이면 aux."""
    if ext is None:
        return None
    show_1, show_2 = visible_screens(ext)
    try:
        from .lam_csv_play_screen import resolve_viewport_window_for_screen

        target = 1 if show_1 else 2
        win = getattr(ext, "_lam_window", None)
        main_vp = getattr(win, "_viewport", None) if win is not None else None
        return resolve_viewport_window_for_screen(
            ext,
            target,
            main_viewport=main_vp,
        )
    except Exception:
        return None


def _set_models(ext: Any, show_1: bool, show_2: bool) -> None:
    try:
        ext._lam_screen_visibility_syncing = True
        ext._lam_show_screen_1_model.set_value(bool(show_1))
        ext._lam_show_screen_2_model.set_value(bool(show_2))
    finally:
        ext._lam_screen_visibility_syncing = False


def request_screen_visibility(
    ext: Any,
    show_1: bool,
    show_2: bool,
    *,
    startup: bool = False,
    on_complete: Optional[Callable[[], None]] = None,
) -> None:
    """표시 전환 요청. 새로 보일 화면을 정지·초기화한 뒤 레이아웃을 바꾼다.

    ``on_complete`` — 레이아웃·wake 적용이 끝난 뒤(메인/async 컨텍스트) 1회 호출.
    이미 동일 표시 상태면 레이아웃을 다시 돌리지 않고 즉시 호출한다.
    """
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
    previous = getattr(ext, "_lam_screen_visibility_applied", None)

    def _notify_done() -> None:
        if not callable(on_complete):
            return
        try:
            on_complete()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} on_complete 실패: {exc}", flush=True)

    # 이미 적용된 표시면 초기화/레이아웃 경합 없이 바로 진행 (파싱·시뮬 camera fly 보호)
    if (not startup) and previous == desired:
        _notify_done()
        return

    gen = int(getattr(ext, "_lam_screen_visibility_generation", 0) or 0) + 1
    ext._lam_screen_visibility_generation = gen
    if startup or previous is None:
        reset_screens = [si for si, shown in enumerate(desired, start=1) if shown]
    else:
        reset_screens = [
            si
            for si, shown in enumerate(desired, start=1)
            if shown and not bool(previous[si - 1])
        ]

    def _apply_if_current() -> None:
        if int(getattr(ext, "_lam_screen_visibility_generation", 0) or 0) != gen:
            return

        async def _run_layout_then_notify() -> None:
            try:
                await _apply_layout_async(ext, desired, gen)
            finally:
                if int(getattr(ext, "_lam_screen_visibility_generation", 0) or 0) == gen:
                    _notify_done()

        try:
            asyncio.ensure_future(_run_layout_then_notify())
        except Exception as exc:
            print(f"{_PRINT_PREFIX} layout schedule 실패: {exc}", flush=True)
            _notify_done()

    if not reset_screens:
        _apply_if_current()
        return

    remaining = {"count": len(reset_screens)}

    def _one_reset_done() -> None:
        if int(getattr(ext, "_lam_screen_visibility_generation", 0) or 0) != gen:
            return
        remaining["count"] -= 1
        if remaining["count"] <= 0:
            _apply_if_current()

    lam = getattr(ext, "_lam_window", None)
    for screen in reset_screens:
        try:
            csv_win = lam._ensure_csv_sim_play_window(screen) if lam is not None else None
            if csv_win is None:
                _one_reset_done()
            else:
                csv_win._on_csv_stop_reset_clicked(on_complete=_one_reset_done)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} 화면{screen} 표시 전 초기화 실패: {exc}",
                flush=True,
            )
            _one_reset_done()


def _revive_viewport_window(win_name: str) -> None:
    """숨김 후 재표시된 Viewport의 fill·입력·네비게이션을 복구한다."""
    wn = str(win_name or "").strip()
    if not wn:
        return
    try:
        from .lam_multi_viewport import (
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
    """표시 전환 후 검정/조작 불가 방지 — Hydra wake + 카메라 입력 복구."""
    try:
        import omni.kit.app as kit_app  # type: ignore
    except Exception:
        kit_app = None
    from .lam_multi_viewport import (
        _apply_split_navigation_to_aux,
        _ensure_viewport_camera_navigation_enabled,
        _schedule_split_viewport_input_ready,
        set_viewport_fill_frame_for_split_count,
        wake_main_viewport_after_master_open,
    )

    aux_name = "LAM_SimSplit_1"
    if kit_app is not None:
        for _ in range(4):
            if int(getattr(ext, "_lam_screen_visibility_generation", 0) or 0) != generation:
                return
            await kit_app.get_app().next_update_async()

    set_viewport_fill_frame_for_split_count(2, True)
    if show_1:
        # Viewport 를 show_window(False) 했다가 다시 켜면 Hydra/네비가 죽은 채로
        # 남을 수 있다. Master open 과 동일한 wake 경로로 복구한다.
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
        token = int(getattr(ext, "_lam_multi_viewport_apply_token", 0) or 0)
        try:
            _apply_split_navigation_to_aux(ext, 2, token, hold_ticks=48)
        except Exception:
            pass
        _ensure_viewport_camera_navigation_enabled("Viewport")
        _ensure_viewport_camera_navigation_enabled(aux_name)

    if kit_app is not None:
        for _ in range(6):
            if int(getattr(ext, "_lam_screen_visibility_generation", 0) or 0) != generation:
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
    if int(getattr(ext, "_lam_screen_visibility_generation", 0) or 0) != generation:
        return
    try:
        from .lam_multi_viewport_widget import is_split_widget_layout_active

        if is_split_widget_layout_active(ext):
            print(
                f"{_PRINT_PREFIX} Widget 표시 전환은 지원하지 않음 — Dock 모드 필요",
                flush=True,
            )
            return
    except Exception:
        pass

    from .lam_multi_viewport import (
        _apply_split_dock_layout,
        _sync_viewport_resolution_from_workspace_window,
        _workspace_show_named_window,
        apply_viewport_split_user_resize_lock,
        set_viewport_fill_frame_for_split_count,
        teardown_viewport_split_resize_lock,
    )

    aux_name = "LAM_SimSplit_1"
    # 새 대상부터 표시하고 기존 화면을 숨겨 빈 프레임 노출을 줄인다.
    if show_2:
        _workspace_show_named_window(aux_name, True)
    if show_1:
        _workspace_show_named_window("Viewport", True)
    if kit_app is not None:
        await kit_app.get_app().next_update_async()

    if show_1 and show_2:
        token = int(getattr(ext, "_lam_multi_viewport_apply_token", 0) or 0)
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
    if int(getattr(ext, "_lam_screen_visibility_generation", 0) or 0) != generation:
        return

    set_viewport_fill_frame_for_split_count(2, True)
    if show_1:
        _sync_viewport_resolution_from_workspace_window("Viewport")
    if show_2:
        _sync_viewport_resolution_from_workspace_window(aux_name)

    # 숨김→표시 후 검정 배경·회전/줌 불능 방지.
    await _wake_visible_viewports_after_layout(ext, show_1, show_2, generation)
    if int(getattr(ext, "_lam_screen_visibility_generation", 0) or 0) != generation:
        return

    ext._lam_screen_visibility_applied = desired

    # HUD를 현재 보이는 Viewport로 이동하고 화면별 overlay를 재동기화.
    lam = getattr(ext, "_lam_window", None)
    if lam is not None:
        hud = getattr(lam, "_csv_viewport_hud", None)
        if hud is not None:
            try:
                hud.sync_layers(delay_frames=2)
            except Exception:
                pass
        for screen, shown in ((1, show_1), (2, show_2)):
            if shown:
                try:
                    lam.sync_csv_viewport_overlays_for_screen(screen)
                except Exception:
                    pass
    mode = "both" if show_1 and show_2 else ("screen1" if show_1 else "screen2")
    print(f"{_PRINT_PREFIX} 적용 완료 mode={mode}", flush=True)


def apply_startup_screen_visibility(ext: Any) -> None:
    """두 화면 hydrate 완료 후 defaults의 초기 표시 상태 적용."""
    if bool(getattr(ext, "_lam_startup_screen_visibility_requested", False)):
        return
    ext._lam_startup_screen_visibility_requested = True
    init_screen_visibility_models(ext)
    show_1, show_2 = visible_screens(ext)
    request_screen_visibility(ext, show_1, show_2, startup=True)


__all__ = [
    "apply_startup_screen_visibility",
    "hud_viewport_window",
    "init_screen_visibility_models",
    "mount_screen_visibility_checkboxes",
    "request_screen_visibility",
    "visible_screens",
]
