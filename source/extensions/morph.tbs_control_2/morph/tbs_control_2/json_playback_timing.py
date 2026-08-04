"""
JSON 재생 sim 축 타이밍 — back-align · renewal 포트 동기 시각.

두 축:
  **back-align (``port_sync_sim_time``)** — 스케줄·Seek fail-safe·``effective_ports_occupancy_at_t``
    - t_json_start = t0 + max(0, proc - anim)
    - renewal: t_json_start + renewal_offset
    - 그 외: t_json_end = t0 + proc

  **재생 (``playback_port_sync_sim_time``)** — ``SimTimelinePlayer.sim_now`` · 프리런 막대 truncate
    - JSON 러너는 sim ``t0 + json_lead`` 에 시작 (``_poll_playback_sim_aligned_json_starts``)
    - renewal: t0 + json_lead + renewal_offset (포트 ``on_renewal_step`` 와 동일 sim 위치)
    - 그 외: t0 + json_lead + min(anim, proc − json_lead) (= t0 + proc when anim ≤ proc)
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
        off = 0.0 if renewal_offset_sec is None else float(renewal_offset_sec)
        return float(t0) + float(lead) + max(0.0, off)
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
