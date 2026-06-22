"""Kit main-thread USD dispatch — FIFO 큐 + 컨텍스트별 대기 건수.

멀티 뷰 BG 스레드의 ``_dispatch_main_wait`` 를 단일 FIFO 로 직렬화한다.
``pending`` 은 **아직 main 에서 시작되지 않은** 큐 항목 수만 센다(실행 중인 항목 제외).
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

_lock = threading.Lock()
_queue: Deque[Tuple[Optional[str], Callable[[], None], threading.Event, List[Optional[BaseException]]]] = (
    deque()
)
_pending_by_ctx: Dict[str, int] = {}
_sub_box: Dict[str, Any] = {"sub": None}
_MAX_PER_TICK = 32


def _ctx_key(usd_context_name: Optional[str]) -> str:
    return str(usd_context_name or "").strip()


def get_pending_dispatch_count(usd_context_name: Optional[str]) -> int:
    """main 에서 아직 시작되지 않은 dispatch 건수(BG thread 에서 호출 가능)."""
    key = _ctx_key(usd_context_name)
    with _lock:
        return int(_pending_by_ctx.get(key, 0))


def has_any_pending_dispatch() -> bool:
    with _lock:
        return sum(int(v) for v in _pending_by_ctx.values()) > 0


def _incr_pending(ctx: Optional[str]) -> None:
    key = _ctx_key(ctx)
    with _lock:
        _pending_by_ctx[key] = int(_pending_by_ctx.get(key, 0)) + 1


def _decr_pending(ctx: Optional[str]) -> None:
    key = _ctx_key(ctx)
    with _lock:
        n = int(_pending_by_ctx.get(key, 0)) - 1
        if n <= 0:
            _pending_by_ctx.pop(key, None)
        else:
            _pending_by_ctx[key] = n


def _ensure_subscription() -> None:
    if _sub_box.get("sub") is not None:
        return
    try:
        import omni.kit.app as _kapp

        def _on_update(_e=None) -> None:
            batch: List[
                Tuple[Optional[str], Callable[[], None], threading.Event, List[Optional[BaseException]]]
            ] = []
            with _lock:
                while _queue and len(batch) < _MAX_PER_TICK:
                    batch.append(_queue.popleft())
            if not batch:
                return
            from .tbs_usd_stage_context import pop_usd_context_name, push_usd_context_name

            for ctx, fn, done_evt, err_holder in batch:
                # 실행 시작 직전 pending 감소 — fn() 안 idle probe 가 자기 자신을 busy 로 보지 않게.
                _decr_pending(ctx)
                prev = push_usd_context_name(ctx)
                try:
                    fn()
                except BaseException as exc:
                    err_holder[0] = exc
                finally:
                    pop_usd_context_name(prev)
                    done_evt.set()

        _sub_box["sub"] = _kapp.get_app().get_update_event_stream().create_subscription_to_pop(
            _on_update,
            name="morph.tbs_control_2.main_dispatch_fifo",
        )
    except Exception:
        pass


def _run_on_main_direct(captured_ctx: Optional[str], fn: Callable[[], None]) -> None:
    prev = None
    try:
        from .tbs_usd_stage_context import pop_usd_context_name, push_usd_context_name

        prev = push_usd_context_name(captured_ctx)
        fn()
    finally:
        try:
            from .tbs_usd_stage_context import pop_usd_context_name

            pop_usd_context_name(prev)
        except Exception:
            pass


def dispatch_main(fn: Callable[[], None]) -> None:
    """다음 main update 에서 FIFO 순으로 ``fn`` 실행 (fire-and-forget)."""
    from .tbs_usd_stage_context import get_current_usd_context_name

    captured_ctx = get_current_usd_context_name()
    done_evt = threading.Event()
    err_holder: List[Optional[BaseException]] = [None]
    _incr_pending(captured_ctx)
    with _lock:
        _queue.append((captured_ctx, fn, done_evt, err_holder))
    _ensure_subscription()
    if _sub_box.get("sub") is None:
        _decr_pending(captured_ctx)
        try:
            _run_on_main_direct(captured_ctx, fn)
        except BaseException as exc:
            err_holder[0] = exc
        finally:
            done_evt.set()


def dispatch_main_wait(fn: Callable[[], None], *, timeout: float = 15.0) -> bool:
    """FIFO 에 넣고 main 에서 실행 완료될 때까지 대기."""
    from .tbs_usd_stage_context import get_current_usd_context_name

    captured_ctx = get_current_usd_context_name()
    done_evt = threading.Event()
    err_holder: List[Optional[BaseException]] = [None]

    def wrapped() -> None:
        try:
            fn()
        except BaseException as exc:
            err_holder[0] = exc
        finally:
            done_evt.set()

    _incr_pending(captured_ctx)
    with _lock:
        _queue.append((captured_ctx, wrapped, done_evt, err_holder))
    _ensure_subscription()
    if _sub_box.get("sub") is None:
        _decr_pending(captured_ctx)
        try:
            wrapped()
        except BaseException as exc:
            err_holder[0] = exc
        return err_holder[0] is None

    ok = done_evt.wait(timeout=float(timeout))
    if not ok:
        return False
    if err_holder[0] is not None:
        raise err_holder[0]
    return True


def wait_context_dispatch_idle(
    usd_context_name: Optional[str],
    *,
    max_sec: float = 15.0,
) -> bool:
    """해당 컨텍스트 main dispatch 큐가 비워질 때까지 대기(BG thread 안전)."""
    import time

    deadline = time.monotonic() + max(0.1, float(max_sec))
    while time.monotonic() < deadline:
        if get_pending_dispatch_count(usd_context_name) <= 0:
            return True
        time.sleep(0.016)
    return get_pending_dispatch_count(usd_context_name) <= 0


__all__ = [
    "dispatch_main",
    "dispatch_main_wait",
    "get_pending_dispatch_count",
    "has_any_pending_dispatch",
    "wait_context_dispatch_idle",
]
