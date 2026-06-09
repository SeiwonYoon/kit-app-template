"""Stub no-op replacements for LAM-only hooks referenced from ported sequence/anim modules."""

from __future__ import annotations


def sync_csv_play_live_speed_from_ui(*_a, **_k) -> None:
    return None


def csv_play_session_active() -> bool:
    return False


def get_csv_play_anim_dt_scale(speed_ref: float) -> float:
    """MOVE/ROTATE 프레임 dt 배율. TBS 시퀀스 에디터는 CSV Play 없음 → 항상 1.0."""
    _ = float(max(0.01, speed_ref or 1.0))
    return 1.0


__all__ = [
    "sync_csv_play_live_speed_from_ui",
    "csv_play_session_active",
    "get_csv_play_anim_dt_scale",
]
