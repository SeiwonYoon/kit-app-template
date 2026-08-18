"""Master USD 오픈·Extract 동안 Viewport/Hydra 그리기를 끄고, 끝난 뒤에 켠다."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, List, Optional

_PRINT_PREFIX = "[LAM/OpenDraw]"
_RTX_MAX_SAMPLES_DURING_OPEN = 1
_RTX_MAX_SAMPLES_AFTER_OPEN = 1024

_saved_max_samples: Optional[int] = None


def _viewport_window_names() -> List[str]:
    names = ["Viewport"]
    try:
        from .lam_sim_control_defaults import default_viewport_split_count

        n = int(default_viewport_split_count())
    except Exception:
        n = 2
    for i in range(1, max(1, n)):
        names.append(f"LAM_SimSplit_{i}")
    return names


def _set_viewport_updates(enabled: bool) -> None:
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name  # type: ignore
    except Exception:
        return
    for wn in _viewport_window_names():
        try:
            api = get_viewport_from_window_name(wn)
        except Exception:
            api = None
        if api is None:
            continue
        for attr in ("updates_enabled", "enabled"):
            if hasattr(api, attr):
                try:
                    setattr(api, attr, bool(enabled))
                except Exception:
                    pass
        if not enabled:
            try:
                blur = getattr(api, "blur", None)
                if callable(blur):
                    blur()
            except Exception:
                pass


def _set_rtx_max_samples(value: int) -> Optional[int]:
    prev: Optional[int] = None
    try:
        import carb.settings  # type: ignore

        settings = carb.settings.get_settings()
        try:
            prev = int(settings.get("/rtx/sampling/maxSamples") or value)
        except Exception:
            prev = None
        settings.set("/rtx/sampling/maxSamples", int(value))
    except Exception:
        pass
    return prev


def suspend_viewport_draw() -> None:
    """USD 오픈·Extract 전 — 라이브 뷰포트가 로드를 방해하지 않게 한다."""
    global _saved_max_samples
    _saved_max_samples = _set_rtx_max_samples(_RTX_MAX_SAMPLES_DURING_OPEN)
    _set_viewport_updates(False)
    print(
        f"{_PRINT_PREFIX} viewport draw OFF (open/extract) maxSamples="
        f"{_RTX_MAX_SAMPLES_DURING_OPEN}",
        flush=True,
    )


def resume_viewport_draw() -> None:
    """로드 완료 후 그리기 재개 — 화질은 기동 시와 동일(maxSamples=1024)."""
    global _saved_max_samples
    restore = (
        int(_saved_max_samples)
        if _saved_max_samples is not None
        else _RTX_MAX_SAMPLES_AFTER_OPEN
    )
    if restore < _RTX_MAX_SAMPLES_AFTER_OPEN:
        restore = _RTX_MAX_SAMPLES_AFTER_OPEN
    _set_rtx_max_samples(restore)
    _saved_max_samples = None
    _set_viewport_updates(True)
    print(
        f"{_PRINT_PREFIX} viewport draw ON maxSamples={restore}",
        flush=True,
    )


@contextmanager
def viewport_draw_after_usd_open() -> Iterator[None]:
    suspend_viewport_draw()
    try:
        yield
    finally:
        resume_viewport_draw()


__all__ = [
    "resume_viewport_draw",
    "suspend_viewport_draw",
    "viewport_draw_after_usd_open",
]
