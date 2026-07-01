"""프리런 재생 — ``control_sim_screen_playback`` 런타임 위임 (레거시 import 경로 유지)."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .control_sim_prerun_playback import SimPreRunResult, SimTimelinePlayer
from .control_sim_screen_playback import (
    SimPlaybackRuntime,
    add_playback_sessions_after_prerun,
    bootstrap_playback_after_prerun,
    get_playback_runtime,
    get_sim_playback_player,
    is_multi_playback_instances,
    iter_sim_playback_players,
    set_sim_playback_active,
    sim_log_ui_drain_limit,
    sim_log_ui_history_drain_limit,
    stop_playback_for_screen,
    stop_playback_runtime,
)


def start_multi_playback_instances(
    ext: Any,
    results: Dict[int, SimPreRunResult],
    emit_fn: Callable[[str, Any, int], None],
    speed_supplier: Callable[[], float],
    event_emit_allowed: Optional[Callable[[int], bool]] = None,
) -> SimPlaybackRuntime:
    """레거시 이름 — 1·N 화면 공통 ``SimPlaybackRuntime.start``."""
    return bootstrap_playback_after_prerun(
        ext, results, emit_fn, speed_supplier, event_emit_allowed
    )


def stop_multi_playback_instances(ext: Any) -> None:
    stop_playback_runtime(ext)


def tick_multi_playback(ext: Any) -> None:
    from .control_window import _tick_playback_timeline

    _tick_playback_timeline(ext)


__all__ = [
    "add_playback_sessions_after_prerun",
    "bootstrap_playback_after_prerun",
    "get_playback_runtime",
    "get_sim_playback_player",
    "is_multi_playback_instances",
    "iter_sim_playback_players",
    "start_multi_playback_instances",
    "stop_multi_playback_instances",
    "stop_playback_for_screen",
    "tick_multi_playback",
]
