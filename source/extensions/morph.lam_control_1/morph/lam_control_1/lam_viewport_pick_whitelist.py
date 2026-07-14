"""Viewport 클릭 선택 제한(화이트리스트) (기능 #4) — v1.

목표:
- Stage 트리 선택은 건드리지 않고, viewport 클릭으로 인해 발생한 selection만 필터링.
- 허용 루트 하위 클릭 시 루트로 선택 치환, 그 외는 selection clear.
"""

from __future__ import annotations

import time
from typing import Any, Optional, Sequence

from .lam_viewport_overlay_config import VIEWPORT_PICK_WHITELIST_ROOTS
from .lam_viewport_overlay_state import get_toggle_pick_whitelist

_PRINT_PREFIX = "[LAM/PickWhitelist]"

_last_viewport_click_monotonic: float = 0.0
_selection_sub: Any = None
_mouse_hooked: bool = False


def _normalize(p: str) -> str:
    s = (p or "").strip().rstrip("/")
    return s


def _is_under(root: str, path: str) -> bool:
    r = _normalize(root)
    p = _normalize(path)
    if not r or not p:
        return False
    if p == r:
        return True
    return p.startswith(r + "/")


def _find_root_for_path(roots: Sequence[str], path: str) -> Optional[str]:
    # 가장 긴 매칭 루트 선택
    p = _normalize(path)
    best = ""
    for r0 in roots:
        r = _normalize(r0)
        if not r:
            continue
        if _is_under(r, p) and len(r) > len(best):
            best = r
    return best or None


def _note_viewport_click(*_args, **_kwargs) -> None:
    global _last_viewport_click_monotonic
    _last_viewport_click_monotonic = time.monotonic()


def _selection_change_handler(_e=None) -> None:
    if not get_toggle_pick_whitelist():
        return
    # viewport 클릭 직후의 selection change만 필터링
    if time.monotonic() - float(_last_viewport_click_monotonic) > 0.25:
        return
    roots = list(VIEWPORT_PICK_WHITELIST_ROOTS or [])
    try:
        import omni.usd as ou  # type: ignore

        ctx = ou.get_context("")
        if ctx is None:
            return
        sel = ctx.get_selection()
        paths = list(sel.get_selected_prim_paths() or [])
        if not paths:
            return
        # whitelist가 비어 있으면 "아무것도 선택되지 않게" 강제
        if not roots:
            sel.clear_selected_prim_paths()
            return
        # 단일 선택만(v1)
        p0 = str(paths[0])
        root = _find_root_for_path(roots, p0)
        if root:
            if _normalize(p0) != _normalize(root):
                sel.set_selected_prim_paths([root], True)
        else:
            sel.clear_selected_prim_paths()
    except Exception:
        return


def _hook_viewport_mouse_once() -> None:
    global _mouse_hooked
    if _mouse_hooked:
        return
    # 활성 viewport window의 frame에 mouse_pressed_fn을 걸어 "viewport 클릭"을 감지한다.
    try:
        from omni.kit.viewport.utility import get_active_viewport_window  # type: ignore

        vw = get_active_viewport_window()
        if vw is None:
            return
        frame = getattr(vw, "frame", None)
        if frame is None:
            return
        fn = getattr(frame, "set_mouse_pressed_fn", None)
        if callable(fn):
            fn(lambda *a, **k: _note_viewport_click(*a, **k))
            _mouse_hooked = True
            return
    except Exception:
        pass


def enable_pick_whitelist() -> None:
    """Whitelist 모드 활성화: selection 변경 구독 + viewport 클릭 감지."""
    global _selection_sub
    if _selection_sub is not None:
        return
    _hook_viewport_mouse_once()
    try:
        import omni.usd as ou  # type: ignore
        from carb.eventdispatcher import get_eventdispatcher  # type: ignore

        ctx = ou.get_context("")
        if ctx is None:
            return
        ed = get_eventdispatcher()
        event_name = ctx.stage_event_name(ou.StageEventType.SELECTION_CHANGED)
        _selection_sub = ed.observe_event(
            observer_name="morph.lam_control_1:PickWhitelist",
            event_name=event_name,
            on_event=lambda _e: _selection_change_handler(_e),
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} enable failed: {exc}", flush=True)


def disable_pick_whitelist() -> None:
    global _selection_sub
    sub = _selection_sub
    _selection_sub = None
    if sub is None:
        return
    try:
        if hasattr(sub, "release"):
            sub.release()
        elif hasattr(sub, "unsubscribe"):
            sub.unsubscribe()
    except Exception:
        pass


__all__ = ["enable_pick_whitelist", "disable_pick_whitelist"]

