"""TEMP — Federation Load HUD ``I`` 단축키 토글 (테스트용).

삭제 절차 (테스트 완료 후):
  1) 이 파일 삭제
  2) ``lam_sim_control_defaults.TEMP_FEDERATION_LOAD_HUD_I_HOTKEY`` 및 ``__all__`` 항목 삭제
  3) ``extension.py`` 의 TEMP install/uninstall 블록 삭제
  4) ``lam_federation_load_hud.py`` 의 ``BEGIN/END TEMP: I-hotkey`` 블록 삭제
"""

from __future__ import annotations

from typing import Any, Optional

_PRINT_PREFIX = "[LAM/FedLoadHUD/I-TEMP]"
_input_iface: Any = None
_input_sub_id: Any = None


def _temp_flag_enabled() -> bool:
    try:
        from .lam_sim_control_defaults import TEMP_FEDERATION_LOAD_HUD_I_HOTKEY

        return bool(TEMP_FEDERATION_LOAD_HUD_I_HOTKEY)
    except Exception:
        return False


def _window_is_focused(win: Any) -> bool:
    if win is None:
        return False
    try:
        if bool(getattr(win, "focused", False)):
            return True
    except Exception:
        pass
    try:
        if bool(getattr(win, "selected", False)):
            return True
    except Exception:
        pass
    for attr in ("window", "_window", "ui_window", "viewport_window"):
        try:
            inner = getattr(win, attr, None)
        except Exception:
            inner = None
        if inner is not None and inner is not win and _window_is_focused(inner):
            return True
    return False


def _is_viewport_focused() -> bool:
    names = ("Viewport", "LAM_SimSplit_1", "LAM_SimSplit_2", "TBS_SimSplit_1")
    try:
        import omni.ui as ui  # type: ignore

        get_win = getattr(ui.Workspace, "get_window", None)
        if callable(get_win):
            for nm in names:
                try:
                    if _window_is_focused(get_win(nm)):
                        return True
                except Exception:
                    continue
    except Exception:
        pass
    try:
        from omni.kit.viewport.utility import get_active_viewport_window  # type: ignore

        active = get_active_viewport_window()
        if _window_is_focused(active):
            return True
        # TEMP: Kit 에서 Viewport 클릭 후에도 focused=False 인 경우가 많음.
        # active viewport 창이 있으면 Viewport 조작 중으로 간주.
        if active is not None:
            return True
    except Exception:
        pass
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name  # type: ignore

        api = get_viewport_from_window_name("Viewport")
        for attr in ("viewport_window", "window", "_viewport_window", "_window"):
            cand = getattr(api, attr, None) if api is not None else None
            if _window_is_focused(cand):
                return True
        if _window_is_focused(api):
            return True
    except Exception:
        pass
    return False


def _keyboard_input_is_i(ke: Any, KeyboardInput: Any) -> bool:
    key = getattr(ke, "input", None)
    try:
        if key == KeyboardInput.I:
            return True
    except Exception:
        pass
    # 일부 빌드/바인딩에서 enum 비교가 깨질 때 대비
    try:
        name = str(getattr(key, "name", "") or "")
        if name in ("I", "KEY_I", "KeyboardInput.I"):
            return True
    except Exception:
        pass
    try:
        if int(key) == int(KeyboardInput.I):
            return True
    except Exception:
        pass
    return False


def install_federation_load_hud_i_hotkey() -> None:
    """Viewport 포커스 + ``I`` → Federation Load HUD(배경 포함) 토글."""
    uninstall_federation_load_hud_i_hotkey()
    if not _temp_flag_enabled():
        return
    try:
        import carb.input  # type: ignore
        from carb.input import (  # type: ignore
            DeviceType,
            KeyboardEventType,
            KeyboardInput,
        )

        iface = carb.input.acquire_input_interface()
    except Exception as exc:
        print(f"{_PRINT_PREFIX} subscribe skipped: {exc}", flush=True)
        return

    def _on_input_event(event: Any, *_args: Any, **_kwargs: Any) -> bool:
        try:
            if getattr(event, "deviceType", None) != DeviceType.KEYBOARD:
                return True
            ke = getattr(event, "event", None)
            if ke is None:
                return True
            et = getattr(ke, "type", None)
            if et not in (
                KeyboardEventType.KEY_RELEASE,
                getattr(KeyboardEventType, "KEY_PRESS", None),
            ):
                return True
            # KEY_PRESS + KEY_RELEASE 모두 오면 한 번만 처리 (RELEASE 우선)
            if et != KeyboardEventType.KEY_RELEASE:
                return True
            if not _keyboard_input_is_i(ke, KeyboardInput):
                return True
            try:
                mods = int(getattr(ke, "modifiers", 0) or 0)
                if mods:
                    print(f"{_PRINT_PREFIX} I ignored (modifiers={mods})", flush=True)
                    return True
            except Exception:
                pass
            focused = _is_viewport_focused()
            if not focused:
                print(
                    f"{_PRINT_PREFIX} I ignored (Viewport not focused — click Viewport first)",
                    flush=True,
                )
                return True
            print(f"{_PRINT_PREFIX} I → toggle Federation Load HUD", flush=True)
            from .lam_federation_load_hud import (
                toggle_federation_load_hud_user_overlay_visible,
            )

            toggle_federation_load_hud_user_overlay_visible()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} I handler error: {exc}", flush=True)
        return True

    global _input_iface, _input_sub_id
    try:
        _input_iface = iface
        _input_sub_id = iface.subscribe_to_input_events(_on_input_event, order=0)
        print(f"{_PRINT_PREFIX} I hotkey armed (Viewport focus only)", flush=True)
    except Exception as exc:
        _input_iface = None
        _input_sub_id = None
        print(f"{_PRINT_PREFIX} subscribe failed: {exc}", flush=True)


def uninstall_federation_load_hud_i_hotkey() -> None:
    global _input_iface, _input_sub_id
    iface = _input_iface
    sub = _input_sub_id
    _input_iface = None
    _input_sub_id = None
    if iface is None or sub is None:
        return
    try:
        iface.unsubscribe_to_input_events(sub)
    except Exception:
        pass


__all__ = [
    "install_federation_load_hud_i_hotkey",
    "uninstall_federation_load_hud_i_hotkey",
]
