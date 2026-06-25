"""
프리런 타임라인 → 재생 스케줄 (화면당 sim 시간축 SSOT).

프리런에 MOVE/회전 pose 를 녹화하지 않고, **언제·어떤 JSON·몇 초·어떤 배속**인지를
미리 확정해 둔다. 재생기는 이 스케줄만 따라 실행하면 된다(Phase 2+).

Phase 0: 빌드만 수행 — 기존 재생 경로는 변경하지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .control_sim_playback_gate import (
    compute_json_effective_speed,
    json_wall_duration_sec,
)
from .control_sim_prerun_playback import SimPreRunResult, SimTimelineItem

_STEP_KIND_JSON = "json_step"
_STEP_KIND_FOUP = "foup"
_STEP_KIND_OCC = "occ_refresh"
_STEP_KIND_EVENT_ONLY = "event_only"
_STEP_KIND_FOUP_PROC = "foup_process"


@dataclass(frozen=True)
class PlaybackScheduledStep:
    """화면 sim 축 위 한 공정 단계 — JSON·진행률·FOUP 가 같은 t0~t1 구간을 공유."""

    screen: int
    index: int
    kind: str
    t_event: float
    t_proc_end: float
    event_seq: str
    event_payload: Dict[str, Any]
    progress_payload: Dict[str, Any]
    json_basename: str
    json_path: Optional[str]
    json_est_sec: float
    proc_sec: float
    anim_sec: float
    eff_sp_at_1x: float
    json_wall_sec_at_1x: float
    needs_json_gate: bool
    mapping_source: str
    t_json_start: float = 0.0
    t_json_end: float = 0.0
    t_port_sync: Optional[float] = None
    has_renewal: bool = False
    renewal_offset_sec: Optional[float] = None
    json_lead_sec: float = 0.0


@dataclass
class PlaybackSchedule:
    screen: int
    final_sim_time: float
    steps: Tuple[PlaybackScheduledStep, ...] = ()
    built_at_user_sp: float = 1.0

    def eff_sp_for_step(self, step: PlaybackScheduledStep, user_sp: float) -> float:
        return compute_json_effective_speed(float(user_sp), step.proc_sec, step.json_est_sec)

    def json_wall_for_step(self, step: PlaybackScheduledStep, user_sp: float) -> float:
        eff = self.eff_sp_for_step(step, user_sp)
        return json_wall_duration_sec(step.json_est_sec, eff)


def _f(payload: Dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(str(payload.get(key, "") or default).strip() or default)
    except Exception:
        return float(default)


def _s(payload: Dict[str, Any], key: str) -> str:
    try:
        return str(payload.get(key, "") or "").strip()
    except Exception:
        return ""


def _needs_json_gate(seq_u: str) -> bool:
    if not seq_u:
        return False
    if seq_u in ("PORT_OCC_REFRESH", "FOUP_PROCESS_START", "FOUP_PROCESS_END"):
        return False
    if seq_u == "FOUP_PROCESS":
        return False
    return True


def _estimate_json_sec(json_path: Optional[str]) -> float:
    if not json_path:
        return 0.0
    p = Path(str(json_path))
    if not p.is_file():
        return 0.0
    try:
        from .control_window import _estimate_sequence_total_duration_sec_for_log

        parsed = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(parsed, list):
            return 0.0
        est = _estimate_sequence_total_duration_sec_for_log(parsed, speed_scale=1.0)
        return float(est) if isinstance(est, (int, float)) else 0.0
    except Exception:
        return 0.0


def _resolve_json_path(seq_u: str, event_payload: Dict[str, Any], linked_basename: str) -> Tuple[str, str, Optional[str]]:
    """반환: (basename, absolute_path|\"\", mapping_source)."""
    bn = str(linked_basename or "").strip()
    if bn:
        try:
            from .control_window import _normalize_json_path

            p = _normalize_json_path(bn)
            if p.is_file():
                return p.name, str(p), "engine_linked_anim_json"
        except Exception:
            pass
    try:
        from .control_window import _normalize_json_path, _resolve_event_animation_entry

        mapping = dict(event_payload or {})
        mapping["seq"] = seq_u
        j, _meta, _rule, src = _resolve_event_animation_entry(seq_u, mapping)
        if j:
            p = _normalize_json_path(str(j))
            if p.is_file():
                return p.name, str(p), str(src or "resolve")
    except Exception:
        pass
    return bn, None, ""


def _classify_event(seq_u: str) -> str:
    if seq_u in ("FOUP_PROCESS_START", "FOUP_PROCESS_END"):
        return _STEP_KIND_FOUP
    if seq_u == "PORT_OCC_REFRESH":
        return _STEP_KIND_OCC
    if seq_u == "FOUP_PROCESS":
        return _STEP_KIND_FOUP_PROC
    if seq_u in ("READYTOLOAD", "READYTOUNLOAD"):
        return _STEP_KIND_EVENT_ONLY
    return _STEP_KIND_JSON


def build_playback_schedule(
    result: SimPreRunResult,
    *,
    user_sp: float = 1.0,
) -> PlaybackSchedule:
    """
    ``SimPreRunResult.items`` 를 훑어 화면별 재생 스케줄을 만든다.

    - event 와 같은 sim_t 의 progress(RUNNING, elapsed≈0) 를 한 쌍으로 본다.
    - proc_end = t0 + proc_sec (엔진 ``_wait_with_progress`` 와 동일 축).
    - JSON wall/eff_sp 는 user_sp=1 기준으로 저장; 재생 시 ``eff_sp_for_step`` 으로 재계산.
    """
    screen = int(result.screen)
    items = tuple(result.items or ())
    steps: List[PlaybackScheduledStep] = []
    idx = 0
    step_i = 0
    sp1 = max(0.1, float(user_sp))

    while idx < len(items):
        it = items[idx]
        kind = str(it.kind or "").strip().lower()
        if kind != "event" or not isinstance(it.payload, dict):
            idx += 1
            continue

        event_p = dict(it.payload)
        seq_u = _s(event_p, "seq").upper()
        if not seq_u:
            idx += 1
            continue

        t_ev = float(it.t)
        progress_p: Dict[str, Any] = {}
        j = idx + 1
        if j < len(items):
            nxt = items[j]
            if str(nxt.kind or "").strip().lower() == "progress" and isinstance(nxt.payload, dict):
                pp = dict(nxt.payload)
                if abs(float(nxt.t) - t_ev) <= 1e-4 and _s(pp, "status").upper() == "RUNNING":
                    if abs(_f(pp, "elapsed", 0.0)) <= 1e-6:
                        progress_p = pp
                        idx = j + 1
                    else:
                        idx += 1
                else:
                    idx += 1
            else:
                idx += 1
        else:
            idx += 1

        if not progress_p:
            progress_p = {
                "event_seq": seq_u,
                "event_start_sim_time": f"{t_ev:.2f}",
                "proc_sec": _s(event_p, "proc_sec") or "0.0",
                "anim_sec": "0.0",
                "status": "RUNNING",
                "elapsed": "0.0",
            }

        t0 = _f(progress_p, "event_start_sim_time", t_ev)
        if t0 <= 1e-9:
            t0 = t_ev
        proc = _f(progress_p, "proc_sec", _f(event_p, "proc_sec", 0.0))
        anim = _f(progress_p, "anim_sec", 0.0)
        if proc <= 1e-9 and anim > 1e-9:
            proc = anim
        proc_end = t0 + max(0.01, proc) if proc > 1e-9 else t0

        linked = _s(progress_p, "linked_anim_json")
        step_kind = _classify_event(seq_u)
        json_bn, json_path, map_src = ("", None, "")
        if step_kind == _STEP_KIND_JSON:
            json_bn, json_path, map_src = _resolve_json_path(seq_u, event_p, linked)
        json_est = _estimate_json_sec(json_path) if json_path else anim
        eff = compute_json_effective_speed(sp1, proc, json_est)
        wall = json_wall_duration_sec(json_est, eff)

        t_json_start = t0
        t_json_end = proc_end
        t_port_sync: Optional[float] = None
        has_renewal = False
        renewal_off: Optional[float] = None
        lead = 0.0
        if step_kind == _STEP_KIND_JSON and (json_path or json_est > 1e-9):
            try:
                from .json_playback_timing import renewal_info_from_json_path, timing_from_progress

                if json_path:
                    has_renewal, renewal_off = renewal_info_from_json_path(json_path)
                tm = timing_from_progress(
                    progress_p,
                    json_path=json_path,
                )
                t_json_start = float(tm.get("t_json_start", t0))
                t_json_end = float(tm.get("t_json_end", proc_end))
                t_port_sync = tm.get("t_port_sync")
                has_renewal = bool(tm.get("has_renewal", has_renewal))
                renewal_off = tm.get("renewal_offset_sec", renewal_off)
                lead = float(tm.get("json_lead_sec", 0.0))
            except Exception:
                pass

        steps.append(
            PlaybackScheduledStep(
                screen=screen,
                index=step_i,
                kind=step_kind,
                t_event=float(t_ev),
                t_proc_end=float(proc_end),
                event_seq=seq_u,
                event_payload=event_p,
                progress_payload=progress_p,
                json_basename=str(json_bn or linked or ""),
                json_path=json_path,
                json_est_sec=float(json_est),
                proc_sec=float(proc),
                anim_sec=float(anim),
                eff_sp_at_1x=float(eff),
                json_wall_sec_at_1x=float(wall),
                needs_json_gate=_needs_json_gate(seq_u) and bool(json_path or linked),
                mapping_source=str(map_src or ""),
                t_json_start=float(t_json_start),
                t_json_end=float(t_json_end),
                t_port_sync=t_port_sync,
                has_renewal=bool(has_renewal),
                renewal_offset_sec=renewal_off,
                json_lead_sec=float(lead),
            )
        )
        step_i += 1

    return PlaybackSchedule(
        screen=screen,
        final_sim_time=float(result.final_sim_time),
        steps=tuple(steps),
        built_at_user_sp=float(sp1),
    )


def build_schedules_by_screen(
    results: Dict[int, SimPreRunResult],
    *,
    user_sp: float = 1.0,
) -> Dict[int, PlaybackSchedule]:
    out: Dict[int, PlaybackSchedule] = {}
    for sk, res in (results or {}).items():
        if res is None:
            continue
        try:
            si = int(sk)
        except Exception:
            continue
        out[si] = build_playback_schedule(res, user_sp=float(user_sp))
    return out


__all__ = [
    "PlaybackSchedule",
    "PlaybackScheduledStep",
    "build_playback_schedule",
    "build_schedules_by_screen",
]
