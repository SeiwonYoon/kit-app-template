"""Kit 메인(UI) 스레드로 callable 마샬링 — HTTP·HyView 메시징 공용."""

from __future__ import annotations

import threading
from collections import deque
from concurrent.futures import Future
from typing import Any, Callable, Deque, Optional, Tuple

import omni.kit.app as kit_app

_pending_main: Deque[Tuple[Optional[Future], Callable[[], Any]]] = deque()
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


def schedule_on_main_thread(
    fn: Callable[[], Any],
    *,
    on_done: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[BaseException], None]] = None,
) -> None:
    """UI 스레드에서 fn 실행. 완료까지 호출 스레드를 block 하지 않는다."""
    ensure_kit_main_dispatch()

    def _wrap() -> None:
        try:
            result = fn()
        except BaseException as exc:
            if on_error is not None:
                on_error(exc)
            return
        if on_done is not None:
            try:
                on_done(result)
            except Exception:
                pass

    with _pending_lock:
        _pending_main.append((None, _wrap))


def run_on_main_thread(fn: Callable[[], Any], *, timeout: float = 120.0) -> Any:
    """UI 스레드에서 fn 실행 후 결과 반환 (동기 대기)."""
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
