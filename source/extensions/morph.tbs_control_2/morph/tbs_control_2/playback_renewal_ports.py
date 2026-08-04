"""
renewal JSON 포트 plan SSOT — 프리런 1회, 재생 ``plan.lookup(sim_now)`` 전용.

정책:
  - ``data/sim_sequences`` JSON 에 renewal 마커 → 포트 갱신 sim = ``t0 + json_lead + offset``
  - **proc_end / JSON 종료 / PORT_OCC_REFRESH 갱신 없음**
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .control_sim_prerun_playback import (
    _post_anim_src_from_progress_and_event,
    panel_occ_tuple_from_dict,
    predict_ports_occupancy_after_anim,
)
from .playback_schedule import PlaybackScheduledStep
from .sim_sequence_json import (
    has_renewal_marker_in_file,
    load_sim_sequence_steps,
    resolve_renewal_for_json_step,
    resolve_sim_sequence_json_path,
)

_DEFAULT_PANEL_PORTS: Tuple[str, ...] = (
    "INOUT",
    "BP1",
    "BP2",
    "BP3",
    "BP4",
    "EP1",
    "EP2",
    "EP3",
)


def json_step_linked_basename(step: PlaybackScheduledStep) -> str:
    p = step.progress_payload if isinstance(step.progress_payload, dict) else {}
    for cand in (
        step.json_basename,
        step.json_path,
        p.get("linked_anim_json"),
    ):
        cs = str(cand or "").strip()
        if cs:
            return cs.split("/")[-1].split("\\")[-1]
    return ""


def step_json_has_renewal_marker(step: PlaybackScheduledStep) -> bool:
    """renewal JSON — 스케줄 플래그 · linked · rules 매핑 · ``sim_sequences`` 파일."""
    if bool(step.has_renewal):
        return True
    p = step.progress_payload if isinstance(step.progress_payload, dict) else {}
    for cand in (
        step.json_path,
        step.json_basename,
        json_step_linked_basename(step),
        p.get("linked_anim_json"),
    ):
        cs = str(cand or "").strip()
        if cs and has_renewal_marker_in_file(cs):
            return True
    try:
        ep = step.event_payload if isinstance(step.event_payload, dict) else {}
        seq_u = str(step.event_seq or ep.get("seq") or "").strip().upper()
        if seq_u:
            from .control_window import _resolve_event_animation_entry

            mapping = dict(ep)
            mapping["seq"] = seq_u
            mapped_json, _, _, _ = _resolve_event_animation_entry(seq_u, mapping)
            if mapped_json and has_renewal_marker_in_file(str(mapped_json)):
                return True
    except Exception:
        pass
    return False


def step_renewal_json_steps(step: PlaybackScheduledStep) -> Optional[List[Any]]:
    p = step.progress_payload if isinstance(step.progress_payload, dict) else {}
    for cand in (step.json_path, step.json_basename, p.get("linked_anim_json")):
        cs = str(cand or "").strip()
        if not cs:
            continue
        steps = load_sim_sequence_steps(cs)
        if isinstance(steps, list) and steps:
            return steps
    return None


def _renewal_offset_sec_for_step(step: PlaybackScheduledStep) -> Optional[float]:
    """renewal JSON 1배속 offset — 스케줄 필드 · 파일 · duration fallback."""
    try:
        ro = step.renewal_offset_sec
        if ro is not None and float(ro) > 1e-9:
            return float(ro)
    except Exception:
        pass
    p = step.progress_payload if isinstance(step.progress_payload, dict) else {}
    parsed = step_renewal_json_steps(step)
    hr, off, _ = resolve_renewal_for_json_step(
        json_path=step.json_path,
        parsed_steps=parsed,
        json_basename=str(step.json_basename or ""),
        linked=str(p.get("linked_anim_json") or ""),
    )
    if off is not None and float(off) > 1e-9:
        return float(off)
    if isinstance(parsed, list) and parsed:
        try:
            from .json_playback_timing import renewal_offset_fallback_from_steps

            off_fb = renewal_offset_fallback_from_steps(list(parsed))
            if off_fb is not None and float(off_fb) > 1e-9:
                return float(off_fb)
        except Exception:
            pass
    if hr:
        return None
    return None


def renewal_playback_port_sync_for_step(step: PlaybackScheduledStep) -> Optional[float]:
    """renewal JSON step → 재생 sim 축 포트 갱신 시각 (없으면 None).

    JSON 내 renewal offset 으로 항상 재계산한다.
    스케줄 ``t_playback_port_sync`` 가 JSON 종료로 잘못 bake 된 경우에도
    프리런 막대·포트 마일스톤이 renewal 시각에 맞춰지도록 한다.
    """
    if not step_json_has_renewal_marker(step):
        return None

    p = step.progress_payload if isinstance(step.progress_payload, dict) else {}
    try:
        t0s = float(str(p.get("event_start_sim_time") or step.t_event or 0.0))
    except Exception:
        t0s = float(step.t_event or 0.0)

    # renewal 마커는 확정됐는데 offset 산출이 실패/0 이면 → JSON 시작(t0+lead)에 적용.
    # REMOVED 만 예외: 시작 직후 포트 EMPTY 금지 → anim/proc 구간의 집기 추정 시각 사용.
    off = _renewal_offset_sec_for_step(step)
    off_eff = 0.0 if (off is None or float(off) <= 1e-9) else float(off)
    try:
        ev_u = str(
            (step.progress_payload or {}).get("event_seq")
            or (step.event_payload or {}).get("seq")
            or step.event_seq
            or ""
        ).strip().upper()
    except Exception:
        ev_u = ""
    if ev_u == "REMOVED" and float(off_eff) <= 1e-9:
        try:
            dur = float(step.anim_sec or 0.0)
            if dur <= 1e-9:
                dur = float(step.proc_sec or 0.0)
            if dur > 1e-9:
                # 집는 모션 근처(전반~중반) — 시작(t0) 직후 clear 방지
                off_eff = max(0.05, min(float(dur) * 0.35, float(dur) - 0.05))
        except Exception:
            pass

    try:
        from .json_playback_timing import playback_port_sync_sim_time, resolve_playback_proc_anim

        proc_pb, anim_pb = resolve_playback_proc_anim(
            float(step.proc_sec or 0.0),
            float(step.anim_sec or 0.0),
            json_est_sec=float(step.json_est_sec or 0.0),
        )
        computed = playback_port_sync_sim_time(
            float(t0s),
            float(proc_pb),
            float(anim_pb),
            has_renewal=True,
            renewal_offset_sec=float(off_eff),
        )
        if computed is not None:
            return float(computed)
    except Exception:
        pass

    # offset 재계산 실패 시에만 스케줄 sync 사용 (JSON 종료 근사는 제외)
    if step.t_playback_port_sync is not None:
        try:
            sync_sched = float(step.t_playback_port_sync)
            json_end = None
            if step.t_json_end is not None:
                json_end = float(step.t_json_end)
            elif step.t_playback_json_end is not None:
                json_end = float(step.t_playback_json_end)
            else:
                json_end = float(step.t_proc_end or 0.0)
            if json_end is not None and abs(sync_sched - float(json_end)) <= 1e-3:
                return None
            return sync_sched
        except Exception:
            pass
    return None


def renewal_port_occ_pairs_for_step(
    step: PlaybackScheduledStep,
    *,
    panel_ports: Optional[List[str]] = None,
) -> Tuple[Tuple[str, str], ...]:
    pairs = step.ports_occ_panel_renewal if step.ports_occ_panel_renewal else ()
    if pairs:
        return pairs
    if step.ports_occ_after:
        return step.ports_occ_after

    ports = list(panel_ports or ["INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3"])
    p = step.progress_payload if isinstance(step.progress_payload, dict) else {}
    panel_start: Dict[str, str] = {}
    for k in ports:
        panel_start[str(k).upper()] = ""

    src = _post_anim_src_from_progress_and_event(
        p if isinstance(p, dict) else {},
        step.event_payload if isinstance(step.event_payload, dict) else None,
    )
    pred = predict_ports_occupancy_after_anim(dict(panel_start), src)
    return panel_occ_tuple_from_dict({**panel_start, **pred}, ports)


def renewal_full_panel_occ_for_step(
    step: PlaybackScheduledStep,
    *,
    base_occ: Optional[Dict[str, str]] = None,
    panel_ports: Optional[List[str]] = None,
) -> Optional[Dict[str, str]]:
    """
    renewal step 의 **전체 패널** occ — base(이전 milestone) + step SSOT.

    ``ports_occ_panel_renewal`` 이 비어 있거나 fallback predict 가 빈 panel 기준이면
    EP1 등 이전 포트가 사라지거나 INOUT/BP 갱신이 plan 에 안 들어가는 문제를 막는다.
    """
    ports = [str(p).strip().upper() for p in (panel_ports or _DEFAULT_PANEL_PORTS) if str(p).strip()]
    if not ports:
        ports = list(_DEFAULT_PANEL_PORTS)
    out: Dict[str, str] = {p: "" for p in ports}
    if isinstance(base_occ, dict):
        for k, v in base_occ.items():
            ku = str(k).strip().upper()
            if ku in out:
                out[ku] = str(v or "")

    pairs = renewal_port_occ_pairs_for_step(step, panel_ports=ports)
    if pairs:
        for k, v in pairs:
            ku = str(k).strip().upper()
            if ku in out:
                out[ku] = str(v or "")
        return dict(out)

    p = step.progress_payload if isinstance(step.progress_payload, dict) else {}
    ep = step.event_payload if isinstance(step.event_payload, dict) else {}
    src = _post_anim_src_from_progress_and_event(p, ep)
    pred = predict_ports_occupancy_after_anim(dict(out), src)
    for k, v in (pred or {}).items():
        ku = str(k).strip().upper()
        if ku in out:
            out[ku] = str(v or "")
    return dict(out)


def renewal_port_milestone_for_step(
    step: PlaybackScheduledStep,
    *,
    panel_ports: Optional[List[str]] = None,
    base_occ: Optional[Dict[str, str]] = None,
) -> Optional[Tuple[float, Dict[str, str]]]:
    """``(sync_sim_t, full_panel_occ_dict)`` — renewal JSON 전용."""
    if str(step.kind or "").strip().lower() != "json_step":
        return None
    if not step_json_has_renewal_marker(step):
        return None
    sync_t = renewal_playback_port_sync_for_step(step)
    if sync_t is None:
        return None
    occ = renewal_full_panel_occ_for_step(
        step,
        base_occ=base_occ,
        panel_ports=panel_ports,
    )
    if not occ:
        return None
    return float(sync_t), dict(occ)


def renewal_json_engine_occ_block_windows(
    schedule: Any,
) -> Tuple[Tuple[float, float], ...]:
    """renewal JSON — 엔진 occ 는 renewal plan milestone 만 (proc_end 포함 차단)."""
    out: List[Tuple[float, float]] = []
    for step in (getattr(schedule, "steps", None) or ()):
        if not isinstance(step, PlaybackScheduledStep):
            continue
        if str(step.kind or "").strip().lower() != "json_step":
            continue
        if not step_json_has_renewal_marker(step) and not bool(step.has_renewal):
            continue
        try:
            t_start = float(step.t_event or 0.0)
            t_end = float(step.t_proc_end) + 5.0
            if t_end >= t_start - 1e-6:
                out.append((float(t_start), float(t_end)))
        except Exception:
            pass
    return tuple(out)


def engine_occ_blocked_for_renewal_json(
    t_sim: float,
    windows: Tuple[Tuple[float, float], ...],
) -> bool:
    t = float(t_sim)
    for t_start, t_end in windows or ():
        if float(t_start) - 1e-6 <= t <= float(t_end) + 1e-6:
            return True
    return False


__all__ = [
    "engine_occ_blocked_for_renewal_json",
    "json_step_linked_basename",
    "renewal_full_panel_occ_for_step",
    "renewal_json_engine_occ_block_windows",
    "renewal_playback_port_sync_for_step",
    "renewal_port_milestone_for_step",
    "renewal_port_occ_pairs_for_step",
    "step_json_has_renewal_marker",
    "step_renewal_json_steps",
]
