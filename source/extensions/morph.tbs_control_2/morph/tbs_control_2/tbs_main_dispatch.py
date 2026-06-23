"""Kit main-thread USD dispatch.

- **1화면**: 단일 FIFO (기존 동작).
- **2화면 멀티 인스턴스 재생**: 컨텍스트별 독립 큐, 프레임마다 컨텍스트당 최대 N건.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

_lock = threading.Lock()
_multi_instance_mode = False

_legacy_queue: Deque[Tuple[Optional[str], Callable[[], None], threading.Event, List[Optional[BaseException]]]] = deque()
_legacy_pending = 0

_ctx_queues: Dict[str, Deque[Tuple[Callable[[], None], threading.Event, List[Optional[BaseException]]]]] = {}
_ctx_keys: List[str] = []
_pending_by_ctx: Dict[str, int] = {}

_sub_box: Dict[str, Any] = {"sub": None}
_MAX_PER_TICK = 32
_MAX_PER_CTX_PER_TICK = 32

_QueueItem = Tuple[Callable[[], None], threading.Event, List[Optional[BaseException]]]
_BatchItem = Tuple[Optional[str], Callable[[], None], threading.Event, List[Optional[BaseException]]]


def set_multi_instance_dispatch_mode(enabled: bool) -> None:
    global _multi_instance_mode, _legacy_pending
    with _lock:
        want = bool(enabled)
        if _multi_instance_mode and not want:
            for key in list(_ctx_keys):
                q = _ctx_queues.get(key)
                if not q:
                    continue
                ctx = _ctx_from_key(key)
                while q:
                    fn, done_evt, err_holder = q.popleft()
                    _legacy_queue.append((ctx, fn, done_evt, err_holder))
            _ctx_queues.clear()
            _ctx_keys.clear()
            try:
                _legacy_pending += sum(int(v) for v in _pending_by_ctx.values())
            except Exception:
                pass
            _pending_by_ctx.clear()
        _multi_instance_mode = want


def is_multi_instance_dispatch_mode() -> bool:
    with _lock:
        return bool(_multi_instance_mode)


def _ctx_key(usd_context_name: Optional[str]) -> str:
    return str(usd_context_name or "").strip()


def _ctx_from_key(ctx_key: str) -> Optional[str]:
    k = str(ctx_key or "").strip()
    return k if k else None


def get_pending_dispatch_count(usd_context_name: Optional[str]) -> int:
    key = _ctx_key(usd_context_name)
    with _lock:
        if _multi_instance_mode:
            return int(_pending_by_ctx.get(key, 0))
        return int(_legacy_pending)


def has_any_pending_dispatch() -> bool:
    with _lock:
        if _multi_instance_mode:
            return sum(int(v) for v in _pending_by_ctx.values()) > 0
        return int(_legacy_pending) > 0


def _incr_pending_legacy() -> None:
    global _legacy_pending
    _legacy_pending += 1


def _decr_pending_legacy() -> None:
    global _legacy_pending
    _legacy_pending = max(0, int(_legacy_pending) - 1)


def _incr_pending_ctx(ctx: Optional[str]) -> None:
    key = _ctx_key(ctx)
    _pending_by_ctx[key] = int(_pending_by_ctx.get(key, 0)) + 1


def _decr_pending_ctx(ctx: Optional[str]) -> None:
    key = _ctx_key(ctx)
    n = int(_pending_by_ctx.get(key, 0)) - 1
    if n <= 0:
        _pending_by_ctx.pop(key, None)
    else:
        _pending_by_ctx[key] = n


def _enqueue_ctx_locked(ctx_key: str, item: _QueueItem) -> None:
    if ctx_key not in _ctx_queues:
        _ctx_queues[ctx_key] = deque()
        _ctx_keys.append(ctx_key)
    _ctx_queues[ctx_key].append(item)


def _dequeue_legacy_batch(max_n: int) -> List[_BatchItem]:
    batch: List[_BatchItem] = []
    with _lock:
        while _legacy_queue and len(batch) < max_n:
            batch.append(_legacy_queue.popleft())
    return batch


def _dequeue_isolated_per_ctx(max_per_ctx: int) -> List[_BatchItem]:
    batch: List[_BatchItem] = []
    with _lock:
        for key in list(_ctx_keys):
            q = _ctx_queues.get(key)
            if not q:
                continue
            n_take = min(len(q), max(1, int(max_per_ctx)))
            for _ in range(n_take):
                fn, done_evt, err_holder = q.popleft()
                batch.append((_ctx_from_key(key), fn, done_evt, err_holder))
    return batch


def _ensure_subscription() -> None:
    if _sub_box.get("sub") is not None:
        return
    try:
        import omni.kit.app as _kapp

        def _on_update(_e=None) -> None:
            if is_multi_instance_dispatch_mode():
                batch = _dequeue_isolated_per_ctx(_MAX_PER_CTX_PER_TICK)
            else:
                batch = _dequeue_legacy_batch(_MAX_PER_TICK)
            if not batch:
                return
            from .tbs_usd_stage_context import pop_usd_context_name, push_usd_context_name

            for ctx, fn, done_evt, err_holder in batch:
                if is_multi_instance_dispatch_mode():
                    _decr_pending_ctx(ctx)
                else:
                    _decr_pending_legacy()
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
    from .tbs_usd_stage_context import get_current_usd_context_name

    captured_ctx = get_current_usd_context_name()
    done_evt = threading.Event()
    err_holder: List[Optional[BaseException]] = [None]

    with _lock:
        multi = bool(_multi_instance_mode)
    if multi:
        ctx_key = _ctx_key(captured_ctx)
        _incr_pending_ctx(captured_ctx)
        with _lock:
            _enqueue_ctx_locked(ctx_key, (fn, done_evt, err_holder))
    else:
        _incr_pending_legacy()
        with _lock:
            _legacy_queue.append((captured_ctx, fn, done_evt, err_holder))

    _ensure_subscription()
    if _sub_box.get("sub") is None:
        if multi:
            _decr_pending_ctx(captured_ctx)
        else:
            _decr_pending_legacy()
        try:
            _run_on_main_direct(captured_ctx, fn)
        except BaseException as exc:
            err_holder[0] = exc
        finally:
            done_evt.set()


def dispatch_main_wait(fn: Callable[[], None], *, timeout: float = 15.0) -> bool:
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

    with _lock:
        multi = bool(_multi_instance_mode)
    if multi:
        ctx_key = _ctx_key(captured_ctx)
        _incr_pending_ctx(captured_ctx)
        with _lock:
            _enqueue_ctx_locked(ctx_key, (wrapped, done_evt, err_holder))
    else:
        _incr_pending_legacy()
        with _lock:
            _legacy_queue.append((captured_ctx, wrapped, done_evt, err_holder))

    _ensure_subscription()
    if _sub_box.get("sub") is None:
        if multi:
            _decr_pending_ctx(captured_ctx)
        else:
            _decr_pending_legacy()
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
    "is_multi_instance_dispatch_mode",
    "set_multi_instance_dispatch_mode",
    "wait_context_dispatch_idle",
]
