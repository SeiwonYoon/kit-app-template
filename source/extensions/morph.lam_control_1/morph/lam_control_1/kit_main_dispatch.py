"""Kit 메인 스레드 디스패치 — TBS ``kit_main_dispatch`` 와 동일 패턴."""

from __future__ import annotations

from typing import Any, Callable, Optional

_PRINT_PREFIX = "[LAM/kit-main]"

_dispatch_ready: bool = False


def ensure_kit_main_dispatch() -> bool:
    """``omni.kit.app`` 메인 루프에 디스패치가 가능한지 확인."""
    global _dispatch_ready
    if _dispatch_ready:
        return True
    try:
        import omni.kit.app

        app = omni.kit.app.get_app()
        if app is None:
            return False
        _dispatch_ready = True
        return True
    except Exception:
        return False


def schedule_on_main_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> bool:
    """콜백을 Kit 메인 스레드에서 실행."""
    if not ensure_kit_main_dispatch():
        try:
            fn(*args, **kwargs)
            return True
        except Exception as exc:
            print(f"{_PRINT_PREFIX} direct call failed: {exc}", flush=True)
            return False
    try:
        import omni.kit.app

        app = omni.kit.app.get_app()
        if app is None:
            fn(*args, **kwargs)
            return True

        def _run() -> None:
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                print(f"{_PRINT_PREFIX} callback failed: {exc}", flush=True)

        app.post_update(_run)
        return True
    except Exception as exc:
        print(f"{_PRINT_PREFIX} post_update failed: {exc}", flush=True)
        try:
            fn(*args, **kwargs)
            return True
        except Exception:
            return False


def get_main_update_event_stream() -> Optional[Any]:
    try:
        import omni.kit.app

        app = omni.kit.app.get_app()
        return app.get_update_event_stream() if app is not None else None
    except Exception:
        return None
