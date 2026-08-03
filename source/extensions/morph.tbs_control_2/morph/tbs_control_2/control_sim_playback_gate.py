"""프리런 재생 — JSON eff_sp + 타임라인 event emit 게이트 (1·2화면 공통)."""

from __future__ import annotations

from typing import Any, Optional


def compute_json_effective_speed(user_sp: float, proc_sec: float, est_total: float) -> float:
    """
    사용자 배속 위에, JSON(1배속 길이)이 공정시간보다 길면 압축 배속을 곱한다.

    wall JSON 시간 = est_total / eff_sp = proc_sec / user_sp  (proc < est 일 때)
    """
    sp = max(0.1, float(user_sp))
    proc = max(0.0, float(proc_sec))
    est = max(0.0, float(est_total))
    if proc > 1e-9 and est > proc + 1e-6:
        return max(0.1, sp * (est / proc))
    return sp


def json_wall_duration_sec(est_total: float, eff_sp: float) -> float:
    est = max(0.0, float(est_total))
    sp = max(0.1, float(eff_sp))
    if est <= 0.0:
        return 0.0
    return float(est) / sp


def _json_busy_map(ext: Any) -> dict:
    by = getattr(ext, "_sim_json_wall_busy_by_screen", None)
    if not isinstance(by, dict):
        by = {}
        try:
            ext._sim_json_wall_busy_by_screen = by
        except Exception:
            pass
    return by


def set_json_wall_busy(ext: Any, screen: int, busy: bool) -> None:
    """JSON 한 건이 시작~완료(포트 반영) 될 때까지 True — 타임라인 다음 event 차단."""
    _json_busy_map(ext)[str(max(1, int(screen)))] = bool(busy)


def is_json_wall_busy(ext: Any, screen: int) -> bool:
    return bool(_json_busy_map(ext).get(str(max(1, int(screen))), False))


def _proc_gate_map(ext: Any) -> dict:
    by = getattr(ext, "_sim_playback_proc_gate_by_screen", None)
    if not isinstance(by, dict):
        by = {}
        try:
            ext._sim_playback_proc_gate_by_screen = by
        except Exception:
            pass
    return by


def set_proc_gate_end(ext: Any, screen: int, t_proc_end: float) -> None:
    """
    직전 gated 이벤트의 공정 종료 sim 시각.

    JSON wall 이 먼저 풀려도 ``sim_now < t_proc_end`` 이면 다음 gated emit 금지
    (진행률이 아직 해당 ARRIVED/MOVE 인데 다음 포트 공정이 먼저 나가는 것 방지).
    """
    try:
        te = float(t_proc_end)
    except Exception:
        return
    if te <= 1e-9:
        return
    _proc_gate_map(ext)[str(max(1, int(screen)))] = float(te)


def clear_proc_gate_end(ext: Any, screen: int) -> None:
    try:
        _proc_gate_map(ext).pop(str(max(1, int(screen))), None)
    except Exception:
        pass


def get_proc_gate_end(ext: Any, screen: int) -> Optional[float]:
    try:
        v = _proc_gate_map(ext).get(str(max(1, int(screen))))
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _sim_now_for_gate(ext: Any, screen: int) -> float:
    try:
        from .control_sim_screen_playback import get_sim_playback_player

        pl = get_sim_playback_player(ext, int(screen))
        if pl is not None:
            return float(pl.sim_now(int(screen)))
    except Exception:
        pass
    return 0.0


def is_proc_wait_blocking(ext: Any, screen: int) -> bool:
    """``sim_now`` 가 직전 gated 이벤트 공정 종료 이전이면 True."""
    pe = get_proc_gate_end(ext, int(screen))
    if pe is None:
        return False
    try:
        if float(_sim_now_for_gate(ext, int(screen))) + 1e-6 < float(pe):
            return True
    except Exception:
        return False
    return False


def _runner_for_screen(ext: Any, screen: int) -> Any:
    key = str(max(1, int(screen)))
    try:
        runners = getattr(ext, "_sim_runners_by_screen", None)
        rr = runners.get(key) if isinstance(runners, dict) else None
        if rr is not None:
            return rr
    except Exception:
        pass
    if int(screen) == 1:
        return getattr(ext, "_sim_runner", None)
    return None


def is_screen_runner_busy(ext: Any, screen: int) -> bool:
    """SequenceRunner 가 LAM/drain/legacy tick 중이면 True."""
    rr = _runner_for_screen(ext, screen)
    if rr is None:
        return False
    try:
        return bool(getattr(rr, "is_running", lambda: False)())
    except Exception:
        return False


def _usd_context_for_screen(ext: Any, screen: int) -> Any:
    scr = max(1, int(screen))
    if scr <= 1:
        return None
    try:
        names = list(getattr(ext, "_sim_multi_context_names", []) or [])
        if len(names) >= scr - 1:
            nm = str(names[scr - 2] or "").strip()
            if nm:
                return nm
    except Exception:
        pass
    return f"morph_tbs_split_aux_{scr - 1}"


def _registry_for_screen(ext: Any, screen: int) -> Any:
    try:
        from .tbs_split_composed_loader import get_split_runtime_for_screen

        rt = get_split_runtime_for_screen(ext, int(screen))
        if rt is not None:
            return rt.registry
    except Exception:
        pass
    return None


def is_screen_channel_motion_busy(ext: Any, screen: int) -> bool:
    """해당 화면 USD 컨텍스트에서 MOVE/ROTATE/replay 가 진행 중이면 True."""
    try:
        from .sim_channel_scope import is_channel_motion_busy

        return bool(
            is_channel_motion_busy(
                _usd_context_for_screen(ext, int(screen)),
                _registry_for_screen(ext, int(screen)),
            )
        )
    except Exception:
        return False


def try_release_json_wall_when_idle(ext: Any, screen: int) -> bool:
    """
    runner·motion 모두 idle 이면 ``json_wall_busy`` 해제.

    Returns: 해제 후(또는 원래 idle) True — 여전히 busy 면 False.
    """
    scr = max(1, int(screen))
    if not is_json_wall_busy(ext, scr):
        return True
    if is_screen_runner_busy(ext, scr):
        return False
    if is_screen_channel_motion_busy(ext, scr):
        return False
    set_json_wall_busy(ext, scr, False)
    return True


def can_emit_timeline_event(ext: Any, screen: int) -> bool:
    """
    타임라인 ``kind=event`` emit 허용.

    - ``json_wall_busy`` / runner / motion busy → 금지
    - **proc_wait**: 직전 gated 이벤트 ``t_proc_end`` 전 → 금지
      (JSON 만 먼저 끝나 wall 이 풀려도 공정 진행 중 다음 ARRIVED/MOVE 차단)

    progress/log/FOUP(non-gated)·sim_now 는 SimTimelinePlayer 가 계속 처리한다.
    """
    if not bool(getattr(ext, "_sim_playback_started", False)):
        return True
    scr = max(1, int(screen))
    if is_json_wall_busy(ext, scr):
        return False
    if is_proc_wait_blocking(ext, scr):
        return False
    if is_screen_runner_busy(ext, scr):
        return False
    if is_screen_channel_motion_busy(ext, scr):
        return False
    return True


def clear_playback_gate_state(ext: Any) -> None:
    try:
        ext._sim_json_wall_busy_by_screen = {}
        ext._sim_playback_proc_gate_by_screen = {}
    except Exception:
        pass
    try:
        from .control_sim_playback_speed import clear_playback_step_speed_locks

        clear_playback_step_speed_locks(ext)
    except Exception:
        pass


def clear_proc_gates(ext: Any) -> None:
    clear_playback_gate_state(ext)


__all__ = [
    "can_emit_timeline_event",
    "clear_playback_gate_state",
    "clear_proc_gate_end",
    "clear_proc_gates",
    "compute_json_effective_speed",
    "get_proc_gate_end",
    "is_json_wall_busy",
    "is_proc_wait_blocking",
    "is_screen_channel_motion_busy",
    "is_screen_runner_busy",
    "json_wall_duration_sec",
    "set_json_wall_busy",
    "set_proc_gate_end",
    "try_release_json_wall_when_idle",
]
