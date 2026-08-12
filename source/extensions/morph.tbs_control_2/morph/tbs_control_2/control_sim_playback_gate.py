"""프리런 재생 — JSON eff_sp + 타임라인 event emit 게이트 (1·2화면 공통).

직렬: 화면당 wall/proc/runner 1개 (기존).
병렬(``SIM_PARALLEL_NONCONFLICTING_MOVES``): 레일 ``oht``|``move`` 별 게이트 → A∥B emit 가능.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


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


def _gate_key(screen: int, rail: Optional[str] = None) -> str:
    scr = max(1, int(screen))
    try:
        from .sim_parallel_rails import parallel_moves_enabled, rail_queue_key

        if parallel_moves_enabled() and rail:
            return rail_queue_key(scr, str(rail))
    except Exception:
        pass
    return str(scr)


def set_json_wall_busy(
    ext: Any, screen: int, busy: bool, rail: Optional[str] = None
) -> None:
    """JSON 한 건이 시작~완료될 때까지 True — 타임라인 다음 event 차단."""
    scr = max(1, int(screen))
    by = _json_busy_map(ext)
    # 병렬에서 rail 없이 False → 해당 화면 모든 레일·레거시 키 해제
    if (not busy) and (rail is None):
        try:
            from .sim_parallel_rails import parallel_moves_enabled, rail_queue_key

            if parallel_moves_enabled():
                by[str(scr)] = False
                by[rail_queue_key(scr, "oht")] = False
                by[rail_queue_key(scr, "move")] = False
                return
        except Exception:
            pass
    by[_gate_key(scr, rail)] = bool(busy)


def is_json_wall_busy(ext: Any, screen: int, rail: Optional[str] = None) -> bool:
    by = _json_busy_map(ext)
    scr = max(1, int(screen))
    try:
        from .sim_parallel_rails import parallel_moves_enabled, rail_queue_key

        if parallel_moves_enabled():
            if rail:
                return bool(by.get(rail_queue_key(scr, str(rail)), False))
            return (
                bool(by.get(rail_queue_key(scr, "oht"), False))
                or bool(by.get(rail_queue_key(scr, "move"), False))
                or bool(by.get(str(scr), False))
            )
    except Exception:
        pass
    return bool(by.get(str(scr), False))


def _proc_gate_map(ext: Any) -> dict:
    by = getattr(ext, "_sim_playback_proc_gate_by_screen", None)
    if not isinstance(by, dict):
        by = {}
        try:
            ext._sim_playback_proc_gate_by_screen = by
        except Exception:
            pass
    return by


def set_proc_gate_end(
    ext: Any, screen: int, t_proc_end: float, rail: Optional[str] = None
) -> None:
    """직전 gated 이벤트의 공정 종료 sim 시각 (레일별 가능)."""
    try:
        te = float(t_proc_end)
    except Exception:
        return
    if te <= 1e-9:
        return
    _proc_gate_map(ext)[_gate_key(screen, rail)] = float(te)


def clear_proc_gate_end(ext: Any, screen: int, rail: Optional[str] = None) -> None:
    try:
        _proc_gate_map(ext).pop(_gate_key(screen, rail), None)
    except Exception:
        pass


def get_proc_gate_end(
    ext: Any, screen: int, rail: Optional[str] = None
) -> Optional[float]:
    try:
        v = _proc_gate_map(ext).get(_gate_key(screen, rail))
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


def is_proc_wait_blocking(
    ext: Any, screen: int, rail: Optional[str] = None
) -> bool:
    """``sim_now`` 가 직전 gated 이벤트 공정 종료 이전이면 True."""
    pe = get_proc_gate_end(ext, int(screen), rail=rail)
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


def is_screen_runner_busy(
    ext: Any, screen: int, rail: Optional[str] = None
) -> bool:
    """SequenceRunner 가 LAM/drain/legacy tick 중이면 True."""
    try:
        from .sim_parallel_rails import parallel_moves_enabled, rail_queue_key

        if parallel_moves_enabled():
            runners = getattr(ext, "_sim_runners_by_screen_rail", None)
            if isinstance(runners, dict):
                if rail:
                    rr = runners.get(rail_queue_key(max(1, int(screen)), str(rail)))
                    if rr is not None:
                        return bool(getattr(rr, "is_running", lambda: False)())
                    return False
                # rail 미지정: 해당 화면 어느 레일이든 busy 이면 True
                scr = max(1, int(screen))
                for rk in (
                    rail_queue_key(scr, "oht"),
                    rail_queue_key(scr, "move"),
                ):
                    rr = runners.get(rk)
                    if rr is not None and bool(
                        getattr(rr, "is_running", lambda: False)()
                    ):
                        return True
    except Exception:
        pass
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


def _json_hold_reason_active_or_queued(
    ext: Any, screen: int, rail: Optional[str] = None
) -> bool:
    """lead 대기·실행중 active 또는 멀티 큐 대기면 wall 유지."""
    scr = max(1, int(screen))
    try:
        active_by = getattr(ext, "_sim_anim_active_by_screen", None)
        if isinstance(active_by, dict):
            from .sim_parallel_rails import (
                anim_state_key,
                parallel_moves_enabled,
                screen_from_state_key,
            )

            keys = []
            if parallel_moves_enabled():
                if rail:
                    keys.append(anim_state_key(scr, str(rail)))
                else:
                    keys.extend(
                        [
                            anim_state_key(scr, "oht"),
                            anim_state_key(scr, "move"),
                            str(scr),
                        ]
                    )
            else:
                keys.append(str(scr))
            for k in keys:
                act = active_by.get(k)
                if not isinstance(act, dict) or not act:
                    continue
                try:
                    if screen_from_state_key(k) != scr and str(
                        act.get("tbs_sim_screen") or ""
                    ).strip() not in ("", str(scr)):
                        continue
                except Exception:
                    pass
                # lead 대기 또는 JSON 시퀀스 실행 중 — wall 유지
                if bool(act.get("_json_pending_sim_start")) and not bool(
                    act.get("_json_sequence_started")
                ):
                    return True
                if bool(act.get("_json_sequence_started")):
                    return True
    except Exception:
        pass
    try:
        by = getattr(ext, "_sim_playback_json_jobs_by_screen", None)
        q = by.get(str(scr)) if isinstance(by, dict) else None
        if q is None:
            return False
        if not rail:
            return len(q) > 0
        from .sim_parallel_rails import rail_from_job_or_payload

        for job in list(q):
            if not isinstance(job, dict):
                continue
            if str(rail_from_job_or_payload(job) or "").lower() == str(rail).lower():
                return True
    except Exception:
        pass
    return False


def try_release_json_wall_when_idle(
    ext: Any, screen: int, rail: Optional[str] = None
) -> bool:
    """
    runner·motion 모두 idle 이면 ``json_wall_busy`` 해제.

    Returns: 해제 후(또는 원래 idle) True — 여전히 busy 면 False.
    """
    scr = max(1, int(screen))
    try:
        from .sim_parallel_rails import parallel_moves_enabled

        par = bool(parallel_moves_enabled())
    except Exception:
        par = False

    # 병렬 + rail 미지정: 레일별로 각각 해제 시도
    if par and rail is None:
        ok_oht = try_release_json_wall_when_idle(ext, scr, rail="oht")
        ok_move = try_release_json_wall_when_idle(ext, scr, rail="move")
        # 레거시 키
        if bool(_json_busy_map(ext).get(str(scr), False)):
            if (not is_screen_runner_busy(ext, scr)) and (
                not _json_hold_reason_active_or_queued(ext, scr)
            ):
                _json_busy_map(ext)[str(scr)] = False
        return bool(ok_oht and ok_move)

    if not is_json_wall_busy(ext, scr, rail=rail):
        return True
    if _json_hold_reason_active_or_queued(ext, scr, rail=rail):
        return False
    if is_screen_runner_busy(ext, scr, rail=rail):
        return False
    try:
        # 재생: runner idle 이면 wall 해제 — FOUP translate 등 비-JSON motion 과 분리
        if not bool(getattr(ext, "_sim_playback_started", False)):
            if is_screen_channel_motion_busy(ext, scr):
                return False
    except Exception:
        if is_screen_channel_motion_busy(ext, scr):
            return False
    set_json_wall_busy(ext, scr, False, rail=rail)
    try:
        from .control_sim_playback_plan import clear_parallel_rail_port_hold

        clear_parallel_rail_port_hold(ext, scr, rail=rail)
    except Exception:
        pass
    try:
        from .control_sim_playback_plan import refresh_playback_display_at_sim

        refresh_playback_display_at_sim(ext, scr, force=True)
    except Exception:
        pass
    return True


def can_emit_timeline_event(
    ext: Any, screen: int, rail: Optional[str] = None
) -> bool:
    """
    타임라인 ``kind=event`` emit 허용.

    - ``json_wall_busy`` / runner / (직렬만) motion busy → 금지
    - **proc_wait**: 직전 gated 이벤트 ``t_proc_end`` 전 → 금지
    - 병렬: ``rail`` 단위 게이트로 A∥B 동시 emit
    - 동일 EPn 은 ``rail_ep_conflict_blocks_emit`` / ``make_playback_event_gate`` 에서 추가 차단
    """
    if not bool(getattr(ext, "_sim_playback_started", False)):
        return True
    scr = max(1, int(screen))
    if is_json_wall_busy(ext, scr, rail=rail):
        return False
    if is_proc_wait_blocking(ext, scr, rail=rail):
        return False
    if is_screen_runner_busy(ext, scr, rail=rail):
        return False
    try:
        from .sim_parallel_rails import parallel_moves_enabled

        if not parallel_moves_enabled():
            if is_screen_channel_motion_busy(ext, scr):
                return False
    except Exception:
        if is_screen_channel_motion_busy(ext, scr):
            return False
    return True


def is_rail_json_occupying(ext: Any, screen: int, rail: str) -> bool:
    """병렬 레일이 JSON wall·lead·runner 로 점유 중이면 True (채널 stop 억제용)."""
    r = str(rail or "").strip().lower()
    if r not in ("oht", "move"):
        return False
    scr = max(1, int(screen))
    if is_json_wall_busy(ext, scr, rail=r):
        return True
    if is_screen_runner_busy(ext, scr, rail=r):
        return True
    try:
        from .sim_parallel_rails import anim_state_key

        active_by = getattr(ext, "_sim_anim_active_by_screen", None)
        if isinstance(active_by, dict):
            act = active_by.get(anim_state_key(scr, r))
            if isinstance(act, dict) and act:
                return True
    except Exception:
        pass
    return False


def is_twin_rail_occupying(ext: Any, screen: int, rail: Optional[str]) -> bool:
    """병렬 ON 이고 상대 레일이 JSON 점유 중이면 True."""
    try:
        from .sim_parallel_rails import parallel_moves_enabled, twin_rail

        if not parallel_moves_enabled():
            return False
        twin = twin_rail(str(rail or ""))
        if not twin:
            return False
        return bool(is_rail_json_occupying(ext, int(screen), twin))
    except Exception:
        return False


def rail_ep_conflict_blocks_emit(
    ext: Any,
    screen: int,
    rail: str,
    payload: Optional[Dict[str, Any]],
) -> bool:
    """병렬: 타 레일 JSON 이 같은 EPn 점유면 True (emit 보류)."""
    try:
        from .sim_parallel_rails import (
            anim_state_key,
            ep_target_from_payload,
            ep_targets_conflict,
            parallel_moves_enabled,
            twin_rail,
        )

        if not parallel_moves_enabled():
            return False
        r = str(rail or "").strip().lower()
        twin = twin_rail(r)
        if not twin:
            return False
        my_ep = ep_target_from_payload(payload if isinstance(payload, dict) else {})
        if not my_ep:
            return False
        if not is_rail_json_occupying(ext, int(screen), twin):
            return False
        active_by = getattr(ext, "_sim_anim_active_by_screen", None)
        other = None
        if isinstance(active_by, dict):
            other = active_by.get(anim_state_key(int(screen), twin))
        if not isinstance(other, dict) or not other:
            # wall 만 남고 active 가 비었으면 EP 를 알 수 없어 보수적으로 차단하지 않음
            # (타 레일 runner busy 는 occupying 에 포함되나 EP 미상이면 A∥B 허용)
            return False
        other_ep = ep_target_from_payload(other)
        return bool(ep_targets_conflict(my_ep, other_ep))
    except Exception:
        return False


def make_playback_event_gate(ext: Any):
    """``(screen, rail=None, payload=None)`` — 병렬 레일·동일 EP 가드 포함."""

    def _gate(scr, rail=None, payload=None):  # noqa: ANN001
        if not can_emit_timeline_event(ext, int(scr), rail=rail):
            return False
        if rail and isinstance(payload, dict):
            if rail_ep_conflict_blocks_emit(ext, int(scr), str(rail), payload):
                return False
        return True

    return _gate


def clear_playback_gate_state(ext: Any) -> None:
    try:
        ext._sim_json_wall_busy_by_screen = {}
        ext._sim_playback_proc_gate_by_screen = {}
    except Exception:
        pass
    try:
        ext._sim_playback_parallel_port_hold_by_rail = {}
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
    "is_rail_json_occupying",
    "is_screen_channel_motion_busy",
    "is_screen_runner_busy",
    "is_twin_rail_occupying",
    "json_wall_duration_sec",
    "make_playback_event_gate",
    "rail_ep_conflict_blocks_emit",
    "set_json_wall_busy",
    "set_proc_gate_end",
    "try_release_json_wall_when_idle",
]
