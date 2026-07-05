"""Kit 메인(UI) 스레드로 callable 마샬링 — HTTP·HyView 메시징 공용."""

from __future__ import annotations

import threading
from collections import deque
from concurrent.futures import Future
from typing import Any, Callable, Deque, Optional, Tuple

import omni.kit.app as kit_app

_pending_main: Deque[Tuple[Future, Callable[[], Any]]] = deque()
_pending_lock = threading.Lock()
_update_sub: Any = None


def _pump_main_queue(_e: Any) -> None:
    while True:
        with _pending_lock:
            if not _pending_main:
                break
            _, run = _pending_main.popleft()
        try:
            run()
        except Exception:
            pass


def ensure_kit_main_dispatch() -> None:
    global _update_sub
    if _update_sub is not None:
        return
    try:
        _update_sub = kit_app.get_app().get_update_event_stream().create_subscription_to_pop(
            _pump_main_queue,
            name="morph.tbs_control_2:kit_main_dispatch",
        )
    except Exception:
        _update_sub = None


def run_on_main_thread(fn: Callable[[], Any], *, timeout: float = 120.0) -> Any:
    """UI 스레드에서 fn 실행 후 결과 반환."""
    ensure_kit_main_dispatch()
    fut: Future = Future()

    def _wrap() -> None:
        try:
            fut.set_result(fn())
        except Exception as exc:
            fut.set_exception(exc)

    with _pending_lock:
        _pending_main.append((fut, _wrap))
    return fut.result(timeout=float(timeout))


def shutdown_kit_main_dispatch() -> None:
    global _update_sub
    if _update_sub is not None:
        try:
            _update_sub.unsubscribe()
        except Exception:
            pass
        _update_sub = None
    with _pending_lock:
        _pending_main.clear()
