"""프리런 재생 — 화면 공통 라이브 배속.

sim_now 는 화면별로 잠그지 않는다. UI 배속을 바꾸면 화면 1·2 가
같은 벽시계에 같은 배속을 곱해 같이 흐른다.
"""

from __future__ import annotations

from typing import Any, Callable, Dict


def get_ui_sim_speed(ext: Any) -> float:
    try:
        m = getattr(ext, "_sim_speed_model", None)
        if m is not None:
            return max(0.1, float(m.get_value_as_float()))
    except Exception:
        pass
    return 1.0


def _lock_map(ext: Any) -> Dict[str, float]:
    by = getattr(ext, "_sim_playback_step_speed_by_screen", None)
    if not isinstance(by, dict):
        by = {}
        try:
            ext._sim_playback_step_speed_by_screen = by
        except Exception:
            pass
    return by


def is_playback_step_speed_locked(ext: Any, screen: int) -> bool:
    del screen
    return False


def lock_playback_step_speed(ext: Any, screen: int) -> float:
    """호환용. 시계는 화면 고정 배속을 쓰지 않는다."""
    del screen
    return float(get_ui_sim_speed(ext))


def unlock_playback_step_speed(ext: Any, screen: int) -> None:
    key = str(max(1, int(screen)))
    try:
        _lock_map(ext).pop(key, None)
    except Exception:
        pass


def clear_playback_step_speed_locks(ext: Any) -> None:
    try:
        ext._sim_playback_step_speed_by_screen = {}
    except Exception:
        pass


def get_playback_advance_speed(ext: Any, screen: int) -> float:
    """``sim_now`` 전진 — 화면과 무관하게 현재 UI 배속."""
    del screen
    return max(0.05, float(get_ui_sim_speed(ext)))


def ensure_step_speed_locked(ext: Any, screen: int) -> float:
    """호환용. JSON/표시도 라이브 UI 배속."""
    del screen
    return float(get_ui_sim_speed(ext))


def make_playback_speed_supplier(ext: Any, screen: int) -> Callable[[], float]:
    del screen

    def _sup() -> float:
        return get_playback_advance_speed(ext, 1)

    return _sup
