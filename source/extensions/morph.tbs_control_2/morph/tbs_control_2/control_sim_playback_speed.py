"""프리런 재생 — 단계(이벤트) 경계 라이브 배속 (진행 중 단계는 시작 배속 유지)."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


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
    return str(max(1, int(screen))) in _lock_map(ext)


def lock_playback_step_speed(ext: Any, screen: int) -> float:
    """현재 UI 배속을 이 화면의 진행 중 단계에 고정 (이미 고정돼 있으면 유지)."""
    key = str(max(1, int(screen)))
    mp = _lock_map(ext)
    if key not in mp:
        mp[key] = float(get_ui_sim_speed(ext))
    return float(mp[key])


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
    """``sim_now`` 전진용 — 단계 고정 중이면 고정 배속, 아니면 UI 라이브 배속."""
    key = str(max(1, int(screen)))
    mp = _lock_map(ext)
    if key in mp:
        try:
            return max(0.05, float(mp[key]))
        except Exception:
            pass
    return max(0.05, float(get_ui_sim_speed(ext)))


def ensure_step_speed_locked(ext: Any, screen: int) -> float:
    """JSON job 등 단계 시작 직전 — 아직 고정 없으면 지금 UI 배속으로 고정."""
    return lock_playback_step_speed(ext, screen)


def make_playback_speed_supplier(ext: Any, screen: int) -> Callable[[], float]:
    scr = int(screen)

    def _sup() -> float:
        return get_playback_advance_speed(ext, scr)

    return _sup
