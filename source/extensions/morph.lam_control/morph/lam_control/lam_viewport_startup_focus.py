"""Kit 시작 시 Viewport F(선택 prim 기준 오빗 pivot) 자동 적용.

설정: ``lam_viewport_overlay_config`` 의
``STARTUP_VIEWPORT_FOCUS_PRIM_ENABLED`` / ``STARTUP_VIEWPORT_FOCUS_PRIM_PATH``.
경로가 비어 있거나 enabled=False 이면 아무 것도 하지 않는다.
"""

from __future__ import annotations

from typing import Any, Tuple

_PRINT_PREFIX = "[LAM/ViewFocus]"
_focus_retry_sub: Any = None


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


def apply_startup_viewport_focus_prim() -> bool:
    """선택 + ``frame_viewport_prims`` — Kit F 와 동일한 오빗 기준 설정."""
    enabled, path = startup_viewport_focus_config()
    if not enabled or not path:
        return False
    if not _prim_exists_on_stage(path):
        return False

    try:
        import omni.usd as ou  # type: ignore

        ctx = ou.get_context()
        if ctx is not None:
            sel = ctx.get_selection()
            if sel is not None:
                sel.set_selected_prim_paths([path], True)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} selection failed: {exc}", flush=True)
        return False

    try:
        from omni.kit.viewport.utility import (  # type: ignore
            frame_viewport_prims,
            get_active_viewport,
        )

        viewport_api = get_active_viewport()
        if viewport_api is None:
            try:
                from omni.kit.viewport.utility import get_active_viewport_window  # type: ignore

                win = get_active_viewport_window()
                viewport_api = win.viewport_api if win is not None else None
            except Exception:
                viewport_api = None
        if viewport_api is None:
            return False
        frame_viewport_prims(viewport_api, prims=[path])
        print(f"{_PRINT_PREFIX} startup focus OK prim={path}", flush=True)
        return True
    except Exception as exc:
        print(f"{_PRINT_PREFIX} frame_viewport_prims failed: {exc}", flush=True)
        return False


def _stop_focus_retry_subscription() -> None:
    global _focus_retry_sub
    if _focus_retry_sub is None:
        return
    try:
        _focus_retry_sub.unsubscribe()
    except Exception:
        pass
    _focus_retry_sub = None


def schedule_startup_viewport_focus_after_stage_ready(
    *,
    delay_frames: int = 24,
    max_attempts: int = 180,
) -> None:
    """Master USD·stage prim 준비 후 F 동등 동작을 post_update 로 재시도."""
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
        attempts_left[0] -= 1
        if attempts_left[0] <= 0:
            print(
                f"{_PRINT_PREFIX} startup focus gave up (prim not ready?): {path_now}",
                flush=True,
            )
            _finish()

    try:
        import omni.kit.app as _app  # type: ignore

        stream = _app.get_app().get_post_update_event_stream()
        global _focus_retry_sub
        _focus_retry_sub = stream.create_subscription_to_pop(
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
