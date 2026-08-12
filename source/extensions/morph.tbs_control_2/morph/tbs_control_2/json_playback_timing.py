"""
JSON 재생 sim 축 타이밍 — back-align · renewal 포트 동기 시각.

두 축:
  **back-align (``port_sync_sim_time``)** — 스케줄·Seek fail-safe·``effective_ports_occupancy_at_t``
    - t_json_start = t0 + max(0, proc - anim)
    - renewal: t_json_start + renewal_offset
    - 그 외: t_json_end = t0 + proc

  **재생 (``playback_sync_sim_t``)** — 진행률·plan lookup 표시 전용 (sim_now clamp 금지)
    - JSON wall: wall ``est/eff_sp`` ↔ sim ``[t0,t0+proc]``
    - sim_now: wall×user_sp 단조 전진 + emit/json_wall 게이트
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .sequence_renewal import find_first_renewal_index, is_renewal_marker, is_renewal_marker


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(str(x).strip() or default)
    except Exception:
        return float(default)


def compute_json_sim_window(t0: float, proc_sec: float, anim_sec: float) -> Tuple[float, float]:
    """sim 축 JSON 재생 구간 ``(t_json_start, t_json_end)``."""
    t0 = float(t0)
    proc = max(0.0, float(proc_sec))
    anim = max(0.0, float(anim_sec))
    if proc <= 1e-9 and anim <= 1e-9:
        return t0, t0
    lead = max(0.0, proc - anim) if proc > 1e-9 else 0.0
    t_start = t0 + lead
    t_end = t0 + proc if proc > 1e-9 else t_start + anim
    return float(t_start), float(t_end)


def json_lead_sec(proc_sec: float, anim_sec: float) -> float:
    """back-align 앞 대기 sim 초."""
    proc = max(0.0, float(proc_sec))
    anim = max(0.0, float(anim_sec))
    if proc <= 1e-9:
        return 0.0
    return max(0.0, proc - anim)


def resolve_playback_anim_sec(
    proc_sec: float,
    anim_sec: float,
    *,
    json_est_sec: float = 0.0,
) -> float:
    """
    재생 sim 축 JSON 길이 — progress ``anim_sec`` 가 0 이면 프리런 JSON 추정치 사용.

    ``anim_sec=0`` + ``proc>0`` 이면 ``lead=proc`` 가 되어 renewal sync 가 공정 종료 *이후*로
    밀리는 버그를 막는다.
    """
    anim = max(0.0, float(anim_sec))
    est = max(0.0, float(json_est_sec))
    if anim <= 1e-9 and est > 1e-9:
        anim = float(est)
    return float(anim)


def resolve_playback_proc_anim(
    proc_sec: float,
    anim_sec: float,
    *,
    json_est_sec: float = 0.0,
) -> Tuple[float, float]:
    """재생 sim 축 ``(proc, anim)`` — anim 추정 후 proc 보정."""
    anim = resolve_playback_anim_sec(proc_sec, anim_sec, json_est_sec=json_est_sec)
    proc = max(0.0, float(proc_sec))
    if proc <= 1e-9 and anim > 1e-9:
        proc = float(anim)
    return float(proc), float(anim)


def estimate_prefix_duration_sec(steps: List[Any], end_exclusive: int) -> float:
    """``steps[0:end_exclusive]`` 1배속 예상 길이 (엔진 그룹 규칙과 동일)."""
    if end_exclusive <= 0 or not steps:
        return 0.0
    try:
        from .control_window import _estimate_step_duration_sec_for_log
        from .tbs_lam_sequence_engine import _group_end_index
    except Exception:
        try:
            from .control_window import _estimate_step_duration_sec_for_log
            from .sequence_engine_legacy import _group_end_index
        except Exception:
            return 0.0

    end_exclusive = min(int(end_exclusive), len(steps))
    try:
        t_cursor = max(0.0, int((steps[0] or {}).get("step_delay_ms", 0)) / 1000.0)
    except Exception:
        t_cursor = 0.0

    i = 0
    while i < end_exclusive:
        try:
            g_end = min(_group_end_index(steps, i), end_exclusive - 1)
        except Exception:
            g_end = i
        t0 = t_cursor
        group_finish = t0
        for j in range(i, g_end + 1):
            st = steps[j] if isinstance(steps[j], dict) else {}
            off = 0.0
            if j != i:
                try:
                    off = max(0.0, int((st or {}).get("step_delay_ms", 0)) / 1000.0)
                except Exception:
                    off = 0.0
            dur = _estimate_step_duration_sec_for_log(st, speed_scale=1.0)
            if dur is None:
                dur = 0.0
            group_finish = max(group_finish, t0 + off + float(dur))
        if g_end + 1 >= end_exclusive:
            return max(0.0, float(group_finish))
        anchor_step = steps[g_end] if isinstance(steps[g_end], dict) else {}
        anchor_off = 0.0
        if g_end > i:
            try:
                anchor_off = max(0.0, int((anchor_step or {}).get("step_delay_ms", 0)) / 1000.0)
            except Exception:
                anchor_off = 0.0
        anchor_dur = _estimate_step_duration_sec_for_log(anchor_step, speed_scale=1.0)
        if anchor_dur is None:
            anchor_dur = 0.0
        anchor_end = t0 + anchor_off + float(anchor_dur)
        next_idx = g_end + 1
        try:
            delay_next = int((steps[next_idx] or {}).get("step_delay_ms", 0)) / 1000.0
        except Exception:
            delay_next = 0.0
        t_cursor = max(t0, anchor_end + float(delay_next))
        i = next_idx
    return max(0.0, float(t_cursor))


def renewal_offset_fallback_from_steps(steps: List[Any]) -> Optional[float]:
    """``control_window`` 없이 JSON step duration 만으로 renewal 전 1배속 경과."""
    ri = find_first_renewal_index(list(steps or []))
    if ri is None or int(ri) <= 0:
        return None
    total = 0.0
    for i in range(int(ri)):
        st = steps[i] if isinstance(steps[i], dict) else {}
        if is_renewal_marker(st):
            continue
        dur = 0.0
        for key in ("_runtime_duration", "duration"):
            try:
                v = float(st.get(key) or 0.0)
                if v > 1e-9:
                    dur = float(v)
                    break
            except Exception:
                pass
        if dur <= 1e-9:
            try:
                t_u = str(st.get("type") or "").strip().upper()
                if t_u in ("USD_TIMELINE", "TIMESAMPLES_REPLAY"):
                    sf = int(st.get("start_frame") or 0)
                    ef = int(st.get("end_frame") or 0)
                    if ef > sf:
                        sp = float(st.get("speed_scale") or 1.0)
                        dur = max(0.0, float(ef - sf) / 30.0 / max(0.01, sp))
            except Exception:
                pass
        try:
            if i > 0:
                dur += max(0.0, int(st.get("step_delay_ms", 0)) / 1000.0)
        except Exception:
            pass
        total += max(0.0, float(dur))
    return float(total) if total > 1e-6 else None


def renewal_info_from_steps(steps: List[Any]) -> Tuple[bool, Optional[float]]:
    """``(has_renewal, offset_sec_1x)`` — renewal 전까지 JSON 내 1배속 경과."""
    ri = find_first_renewal_index(list(steps or []))
    if ri is None:
        return False, None
    off = estimate_prefix_duration_sec(list(steps or []), int(ri))
    if off is None or float(off) <= 1e-9:
        off_fb = renewal_offset_fallback_from_steps(list(steps or []))
        if off_fb is not None:
            off = float(off_fb)
    if off is None or float(off) <= 1e-9:
        return True, None
    return True, float(off)


def renewal_info_from_json_path(json_path: Optional[str]) -> Tuple[bool, Optional[float]]:
    if not json_path:
        return False, None
    p = Path(str(json_path))
    if not p.is_file():
        return False, None
    try:
        parsed = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(parsed, list):
            return False, None
        return renewal_info_from_steps(parsed)
    except Exception:
        return False, None


def playback_port_sync_sim_time(
    t0: float,
    proc_sec: float,
    anim_sec: float,
    *,
    has_renewal: bool = False,
    renewal_offset_sec: Optional[float] = None,
) -> Optional[float]:
    """
    재생 sim 축 occ 마일스톤 — ``SimTimelinePlayer.sim_now`` truncate 와 동일.

    JSON 러너는 ``t0 + json_lead`` 에 시작; renewal 은 그 이후 JSON 내 offset.

    ``anim > proc`` 이면 wall 의 ``eff_sp = anim/proc`` 과 같게 offset 을 sim 축으로
    압축한다 (``off_sim = off * proc/anim``). 1배속 offset 을 그대로 더하면
    sync ≥ proc_end 가 되어 포트가 JSON/공정 종료에야 갱신되는 것처럼 보인다.
    """
    t0 = float(t0)
    proc = max(0.0, float(proc_sec))
    anim = max(0.0, float(anim_sec))
    if proc <= 1e-9 and anim <= 1e-9:
        return None
    if proc <= 1e-9 and anim > 1e-9:
        proc = anim
    lead = json_lead_sec(proc, anim)
    if has_renewal:
        off = 0.0 if renewal_offset_sec is None else max(0.0, float(renewal_offset_sec))
        # wall: renewal_wall = run_start + off/eff_sp, eff_sp≈anim/proc (anim>proc)
        # sim: JSON 창 길이 = proc-lead → off 를 그 비로 압축
        span = max(0.0, float(proc) - float(lead))
        if anim > 1e-9 and float(anim) > float(span) + 1e-9 and span > 1e-9:
            off_sim = float(off) * (float(span) / float(anim))
        else:
            off_sim = float(off)
        sync = float(t0) + float(lead) + float(off_sim)
        # 공정 창을 넘기지 않음 (JSON-end 로 오인되는 pe clamp 회피)
        if proc > 1e-9:
            sync = min(float(sync), float(t0) + float(proc) - 1e-4)
        return float(max(float(t0) + float(lead), sync))
    if proc > 1e-9:
        tail = float(anim) if anim > 1e-9 else float(proc)
        if anim > 1e-9:
            tail = min(float(anim), max(0.0, float(proc) - float(lead)))
        return float(t0) + float(lead) + float(tail)
    if anim > 1e-9:
        return float(t0) + float(anim)
    return None


def port_sync_sim_time(
    t0: float,
    proc_sec: float,
    anim_sec: float,
    *,
    has_renewal: bool = False,
    renewal_offset_sec: Optional[float] = None,
) -> Optional[float]:
    """back-align 스케줄 축 포트·Seek fail-safe sim 시각."""
    t_start, t_end = compute_json_sim_window(t0, proc_sec, anim_sec)
    if has_renewal:
        off = 0.0 if renewal_offset_sec is None else float(renewal_offset_sec)
        return float(t_start) + max(0.0, off)
    if t_end <= t_start + 1e-9 and anim_sec <= 1e-9:
        return None
    return float(t_end)


def timing_from_progress(
    progress_p: Dict[str, Any],
    *,
    json_path: Optional[str] = None,
    steps: Optional[List[Any]] = None,
    json_est_sec: float = 0.0,
) -> Dict[str, Any]:
    """
    progress payload + JSON → 타이밍 dict.

    Keys: t0, proc, anim, t_json_start, t_json_end, t_port_sync,
          has_renewal, renewal_offset_sec, json_lead_sec
    """
    p = dict(progress_p or {})
    t0 = _f(p.get("event_start_sim_time"), _f(p.get("sim_time"), _f(p.get("t"), 0.0)))
    proc = _f(p.get("proc_sec"), 0.0)
    anim = _f(p.get("anim_sec"), 0.0)
    proc, anim = resolve_playback_proc_anim(proc, anim, json_est_sec=float(json_est_sec))
    t_start, t_end = compute_json_sim_window(t0, proc, anim)
    lead = json_lead_sec(proc, anim)

    has_renewal = False
    renewal_off: Optional[float] = None
    if steps is not None:
        has_renewal, renewal_off = renewal_info_from_steps(steps)
    elif json_path:
        has_renewal, renewal_off = renewal_info_from_json_path(json_path)
    else:
        linked = str(p.get("linked_anim_json") or "").strip()
        if linked:
            try:
                from .control_window import _normalize_json_path

                jp = _normalize_json_path(linked)
                if jp.is_file():
                    has_renewal, renewal_off = renewal_info_from_json_path(str(jp))
            except Exception:
                pass

    t_sync = port_sync_sim_time(
        t0,
        proc,
        anim,
        has_renewal=has_renewal,
        renewal_offset_sec=renewal_off,
    )
    return {
        "t0": float(t0),
        "proc": float(proc),
        "anim": float(anim),
        "t_json_start": float(t_start),
        "t_json_end": float(t_end),
        "t_port_sync": t_sync,
        "has_renewal": bool(has_renewal),
        "renewal_offset_sec": renewal_off,
        "json_lead_sec": float(lead),
    }


def json_end_sim_time_from_progress(
    progress_p: Dict[str, Any],
    *,
    fallback_t: float = 0.0,
    json_path: Optional[str] = None,
) -> Optional[float]:
    """back-align 반영 JSON 종료 sim 시각."""
    p = dict(progress_p or {})
    if _f(p.get("anim_sec"), 0.0) <= 1e-9 and _f(p.get("proc_sec"), 0.0) <= 1e-9:
        return None
    t0 = _f(p.get("event_start_sim_time"), float(fallback_t))
    if t0 < 0.0:
        t0 = float(fallback_t)
    _, t_end = compute_json_sim_window(t0, _f(p.get("proc_sec"), 0.0), _f(p.get("anim_sec"), 0.0))
    return float(t_end)


def port_sync_sim_time_from_progress(
    progress_p: Dict[str, Any],
    *,
    fallback_t: float = 0.0,
    json_path: Optional[str] = None,
) -> Optional[float]:
    """back-align 축 — renewal 우선 (Seek·fail-safe)."""
    p = dict(progress_p or {})
    ev_status = str(p.get("status") or "").strip().upper()
    if ev_status and ev_status != "RUNNING":
        return json_end_sim_time_from_progress(p, fallback_t=fallback_t, json_path=json_path)
    t0 = _f(p.get("event_start_sim_time"), float(fallback_t))
    if t0 < 0.0:
        t0 = float(fallback_t)
    info = timing_from_progress(p, json_path=json_path)
    return info.get("t_port_sync")


def playback_foup_heartbeat_elapsed(
    ext: Any,
    screen: int,
    payload: Dict[str, Any],
    tnow: float,
) -> float:
    """
    FOUP 공정 heartbeat elapsed — JSON frontier 와 무관하게 wall-clock 으로 부드럽게 증가.

    sim_now 가 JSON wall·공정 경계에 묶이면 FOUP %·초가 멈췄다가 점프하는 문제를 막는다.
    """
    t0 = _f(payload.get("event_start_sim_time"), 0.0)
    proc = _f(payload.get("proc_sec"), 0.0)
    if proc <= 1e-9:
        proc = _f(payload.get("total"), 0.0)
    if proc <= 1e-9:
        return 0.0

    if ext is None or not bool(getattr(ext, "_sim_playback_started", False)):
        return max(0.0, min(float(proc), float(tnow) - float(t0)))

    import time as _time

    wall_start = payload.get("_foup_phase_wall_start")
    try:
        ws = float(wall_start) if wall_start is not None else None
    except Exception:
        ws = None
    if ws is None:
        ws = float(_time.monotonic())

    sp = 1.0
    try:
        from .control_sim_playback_speed import ensure_step_speed_locked

        sp = max(0.1, float(ensure_step_speed_locked(ext, max(1, int(screen)))))
    except Exception:
        pass

    el = max(0.0, min(float(proc), (float(_time.monotonic()) - float(ws)) * float(sp)))

    # EP·phase 별 단조 증가 (heartbeat 역행 방지)
    ep = str(payload.get("port_id") or "").strip().upper()
    phase_id = str(payload.get("_foup_phase_id") or "").strip()
    store = getattr(ext, "_playback_foup_el_mono", None)
    if not isinstance(store, dict):
        store = {}
        ext._playback_foup_el_mono = store
    mk = f"{max(1, int(screen))}|{ep}|{phase_id}"
    prev = float(store.get(mk, 0.0) or 0.0)
    el = max(prev, float(el))
    el = min(float(proc), el)
    store[mk] = float(el)
    return float(el)


def foup_phase_identity(payload: Dict[str, Any]) -> str:
    """FOUP 단계(+Y/공정/-Y) 변경 감지 키."""
    p = dict(payload or {})
    return "|".join(
        (
            str(p.get("event_start_sim_time") or "").strip(),
            str(p.get("proc_sec") or p.get("total") or "").strip(),
            str(p.get("label") or "").strip(),
            str(p.get("port_id") or "").strip(),
        )
    )


def _wall_mapped_sim_for_active_job(
    ext: Any,
    screen: int,
    active: Dict[str, Any],
) -> Optional[float]:
    """JSON wall active job — wall-clock → sim ``[t0, t0+proc]`` (back-align · eff_sp)."""
    import time as _time

    try:
        t0_sim = _f(
            active.get("_event_start_sim")
            or active.get("event_start_sim_time")
            or active.get("t")
            or active.get("sim_time"),
            0.0,
        )
        proc_raw = _f(active.get("proc_sec"), 0.0)
        est_total = max(0.0, _f(active.get("anim_sec") or active.get("est_total"), 0.0))
        proc_sec, anim_pb = resolve_playback_proc_anim(
            proc_raw, est_total, json_est_sec=float(est_total)
        )
        if proc_sec <= 1e-9:
            return None

        json_lead = max(0.0, _f(active.get("json_lead_sec"), json_lead_sec(proc_sec, anim_pb)))
        lead_wall = max(0.0, _f(active.get("_json_lead_wall_sec"), 0.0))
        if lead_wall <= 1e-9 and json_lead > 1e-9:
            try:
                from .control_sim_playback_speed import ensure_step_speed_locked

                user_sp = max(0.1, float(ensure_step_speed_locked(ext, max(1, int(screen)))))
                lead_wall = float(json_lead) / float(user_sp)
            except Exception:
                lead_wall = float(json_lead)

        started_wall = active.get("_started_wall")
        if started_wall is None:
            started_wall = active.get("_json_run_start_wall")
        eff_sp = max(0.1, _f(active.get("_eff_sp"), 1.0))
        if est_total <= 1e-9:
            est_total = float(anim_pb)
        if started_wall is None:
            return None

        t_cap = t0_sim + proc_sec
        scr = max(1, int(screen))
        playback = bool(getattr(ext, "_sim_playback_started", False))
        seq_started = bool(active.get("_json_sequence_started"))
        pending_sim = bool(active.get("_json_pending_sim_start")) and not seq_started

        # 재생 back-align lead — sim_now SSOT (wall lead 와 어긋나면 포트·진행률이 튐)
        if playback and (pending_sim or (json_lead > 1e-9 and not seq_started)):
            try:
                from .control_sim_multi_playback import get_sim_playback_player

                pl = get_sim_playback_player(ext, scr)
                if pl is not None:
                    sim_now = float(pl.sim_now(scr))
                    if pending_sim or sim_now + 1e-6 < t0_sim + json_lead:
                        return min(t_cap, max(t0_sim, min(sim_now, t0_sim + json_lead)))
            except Exception:
                pass

        now_wall = float(_time.monotonic())
        sw = float(started_wall)

        if json_lead > 1e-9 and not seq_started and lead_wall > 1e-9 and now_wall < sw + lead_wall:
            user_sp = 1.0
            try:
                from .control_sim_playback_speed import ensure_step_speed_locked

                user_sp = max(0.1, float(ensure_step_speed_locked(ext, scr)))
            except Exception:
                pass
            wall_el = max(0.0, (now_wall - sw) * user_sp)
            return min(t_cap, t0_sim + min(json_lead, wall_el))

        json_run_wall = active.get("_json_run_start_wall")
        if json_run_wall is not None:
            json_run_wall = float(json_run_wall)
        else:
            json_run_wall = sw + lead_wall

        json_wall_sec = (
            est_total / eff_sp if est_total > 1e-9 else max(0.01, proc_sec - json_lead)
        )
        json_el = max(0.0, now_wall - float(json_run_wall))
        json_frac = min(1.0, json_el / max(0.01, json_wall_sec))
        sim_tail = max(0.0, proc_sec - json_lead)
        return min(t_cap, max(t0_sim, t0_sim + json_lead + json_frac * sim_tail))
    except Exception:
        return None


def playback_sync_sim_t(ext: Any, screen: int, raw_t: float) -> float:
    """
    진행률·plan lookup 보조 sim 시각 (표시 전용 — ``sim_now`` 는 clamp 하지 않음).

    JSON wall active job: wall-clock → ``[t0,t0+proc]``. 그 외 plan frontier cap.
    """
    t_raw = float(raw_t)
    if ext is None or not bool(getattr(ext, "_sim_playback_started", False)):
        return t_raw

    scr = max(1, int(screen))

    try:
        from .control_sim_playback_gate import is_json_wall_busy
        from .control_sim_playback_plan import _active_gated_event_src
        from .sim_parallel_rails import parallel_moves_enabled

        parallel = bool(parallel_moves_enabled())
        mapped: Optional[float] = None
        if parallel:
            for rail in ("oht", "move"):
                if not is_json_wall_busy(ext, scr, rail=rail):
                    continue
                act = _active_gated_event_src(ext, scr, rail=rail)
                if isinstance(act, dict) and act:
                    m = _wall_mapped_sim_for_active_job(ext, scr, act)
                    if m is not None:
                        mapped = float(m) if mapped is None else min(mapped, float(m))
        elif is_json_wall_busy(ext, scr):
            act = _active_gated_event_src(ext, scr)
            if isinstance(act, dict) and act:
                m = _wall_mapped_sim_for_active_job(ext, scr, act)
                if m is not None:
                    mapped = float(m)
        if mapped is not None:
            t_raw = float(mapped)
        elif is_json_wall_busy(ext, scr) or (
            parallel and any(is_json_wall_busy(ext, scr, rail=r) for r in ("oht", "move"))
        ):
            try:
                from .control_sim_playback_plan import _playback_gated_plan_cap_sim

                cap = _playback_gated_plan_cap_sim(ext, scr)
                if cap is not None and t_raw > float(cap) + 1e-9:
                    t_raw = float(cap)
            except Exception:
                pass
    except Exception:
        pass

    try:
        from .control_sim_playback_plan import playback_plan_frontier_sim

        fr = playback_plan_frontier_sim(ext, scr)
        if fr is not None and t_raw > float(fr) + 1e-9:
            t_raw = float(fr)
    except Exception:
        pass
    try:
        from .control_sim_playback_plan import _playback_gated_plan_cap_sim

        cap = _playback_gated_plan_cap_sim(ext, scr)
        if cap is not None and t_raw > float(cap) + 1e-9:
            t_raw = float(cap)
    except Exception:
        pass
    return max(0.0, float(t_raw))


def playback_progress_sim_t(
    ext: Any,
    screen: int,
    tnow: float,
    *,
    t0: float = 0.0,
    proc: float = 0.0,
    active: Optional[Dict[str, Any]] = None,
) -> float:
    """
    진행률 heartbeat 전용 sim 시각 — active JSON wall 의 wall-clock 매핑만.

    plan frontier cap 은 적용하지 않는다 (타 레일 proc_gate 가 현재 줄 진행을
    끊는 stutter 방지).
    """
    t_raw = float(tnow)
    if ext is None or not bool(getattr(ext, "_sim_playback_started", False)):
        return t_raw

    scr = max(1, int(screen))
    candidates: List[Dict[str, Any]] = []

    if isinstance(active, dict) and active:
        candidates.append(dict(active))

    try:
        from .control_sim_playback_gate import is_json_wall_busy
        from .control_sim_playback_plan import _active_gated_event_src
        from .sim_parallel_rails import parallel_moves_enabled

        parallel = bool(parallel_moves_enabled())
        if parallel:
            for rail in ("move", "oht"):
                if not is_json_wall_busy(ext, scr, rail=rail):
                    continue
                act_r = _active_gated_event_src(ext, scr, rail=rail)
                if isinstance(act_r, dict) and act_r:
                    candidates.append(dict(act_r))
        elif is_json_wall_busy(ext, scr):
            act_s = _active_gated_event_src(ext, scr)
            if isinstance(act_s, dict) and act_s:
                candidates.append(dict(act_s))
    except Exception:
        pass

    if t0 > 1e-9 and candidates:
        matched: List[Dict[str, Any]] = []
        for act_c in candidates:
            try:
                at = _f(
                    act_c.get("_event_start_sim")
                    or act_c.get("event_start_sim_time")
                    or act_c.get("t")
                    or act_c.get("sim_time"),
                    0.0,
                )
            except Exception:
                at = 0.0
            if at > 1e-9 and abs(float(at) - float(t0)) <= 0.35:
                matched.append(act_c)
        if matched:
            candidates = matched

    mapped: Optional[float] = None
    for act_c in candidates:
        m = _wall_mapped_sim_for_active_job(ext, scr, act_c)
        if m is None:
            continue
        mapped = float(m) if mapped is None else min(mapped, float(m))

    if mapped is not None:
        if proc > 1e-9 and t0 > 1e-9:
            mapped = min(float(mapped), float(t0) + float(proc))
        return max(float(t0) if t0 > 1e-9 else 0.0, float(mapped))
    return t_raw


def playback_heartbeat_progress_sim_t(
    ext: Any,
    screen: int,
    tnow: float,
    *,
    t0: float = 0.0,
    proc: float = 0.0,
    active: Optional[Dict[str, Any]] = None,
) -> float:
    """진행률 heartbeat — ``playback_progress_sim_t`` (frontier cap 없음)."""
    return playback_progress_sim_t(
        ext,
        int(screen),
        float(tnow),
        t0=float(t0),
        proc=float(proc),
        active=active,
    )


def playback_heartbeat_monotonic_elapsed(
    ext: Any,
    screen: int,
    step_id: int,
    elapsed: float,
    proc: float,
) -> float:
    """heartbeat 보간 elapsed 가 뒤로 가거나 proc 을 넘지 않게."""
    el = max(0.0, min(float(proc), float(elapsed)))
    if ext is None or proc <= 1e-9:
        return el
    store = getattr(ext, "_playback_prog_el_mono", None)
    if not isinstance(store, dict):
        store = {}
        ext._playback_prog_el_mono = store
    sk = str(max(1, int(screen)))
    rec = store.get(sk)
    sid = int(step_id)
    if not isinstance(rec, dict) or int(rec.get("step_id", -1)) != sid:
        rec = {"step_id": sid, "el": 0.0}
    el2 = max(float(rec.get("el", 0.0)), el)
    el2 = min(float(proc), el2)
    rec["el"] = el2
    store[sk] = rec
    return el2


def playback_port_sync_sim_time_from_progress(
    progress_p: Dict[str, Any],
    *,
    fallback_t: float = 0.0,
    json_path: Optional[str] = None,
    steps: Optional[List[Any]] = None,
    json_est_sec: float = 0.0,
) -> Optional[float]:
    """재생 sim 축 — 프리런 막대 occ 마일스톤 (``sim_now`` truncate 와 동일)."""
    p = dict(progress_p or {})
    t0 = _f(p.get("event_start_sim_time"), _f(p.get("sim_time"), float(fallback_t)))
    if t0 < 0.0:
        t0 = float(fallback_t)
    est = float(json_est_sec)
    if est <= 1e-9 and json_path:
        try:
            from .playback_schedule import _estimate_json_sec

            est = float(_estimate_json_sec(json_path))
        except Exception:
            est = 0.0
    info = timing_from_progress(p, json_path=json_path, steps=steps, json_est_sec=float(est))
    return playback_port_sync_sim_time(
        t0,
        float(info.get("proc", 0.0)),
        float(info.get("anim", 0.0)),
        has_renewal=bool(info.get("has_renewal")),
        renewal_offset_sec=info.get("renewal_offset_sec"),
    )


__all__ = [
    "compute_json_sim_window",
    "estimate_prefix_duration_sec",
    "json_end_sim_time_from_progress",
    "json_lead_sec",
    "playback_foup_heartbeat_elapsed",
    "playback_sync_sim_t",
    "playback_progress_sim_t",
    "playback_heartbeat_progress_sim_t",
    "foup_phase_identity",
    "playback_port_sync_sim_time",
    "playback_port_sync_sim_time_from_progress",
    "port_sync_sim_time",
    "port_sync_sim_time_from_progress",
    "resolve_playback_anim_sec",
    "resolve_playback_proc_anim",
    "renewal_info_from_json_path",
    "renewal_info_from_steps",
    "timing_from_progress",
]
