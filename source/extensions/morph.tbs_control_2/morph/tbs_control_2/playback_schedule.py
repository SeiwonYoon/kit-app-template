"""
프리런 타임라인 → 재생 스케줄 + UI 마일스톤 (화면당 sim 시간축 SSOT).

프리런 1회: 이벤트·JSON·proc/anim/eff_sp·renewal·``ui_milestones``(포트·막대 공통) 확정.
재생: 스케줄(JSON 실행) + ui_milestones replay (포트·막대 truncate).
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
from .control_sim_prerun_playback import (
    SimPreRunResult,
    SimTimelineItem,
    _ANIM_PORT_UPDATE_SEQS,
    _normalize_anim_event_seq,
    _post_anim_src_from_progress,
    _post_anim_src_from_progress_and_event,
    _s_val,
    predict_ports_occupancy_after_anim,
)
from .sim_sequence_json import (
    has_renewal_marker_in_file,
    load_sim_sequence_steps,
    resolve_renewal_for_json_step,
    resolve_sim_sequence_json_path,
)

_STEP_KIND_JSON = "json_step"
_STEP_KIND_FOUP = "foup"
_STEP_KIND_OCC = "occ_refresh"
_STEP_KIND_EVENT_ONLY = "event_only"
_STEP_KIND_FOUP_PROC = "foup_process"


def _resolve_json_path_for_schedule(
    seq_u: str,
    event_p: Dict[str, Any],
    linked: str,
    json_path: Optional[str],
    json_bn: str,
) -> Tuple[Optional[str], str]:
    jp = str(json_path or "").strip() or None
    bn = str(json_bn or linked or "").strip()
    if not jp and bn:
        rp = resolve_sim_sequence_json_path(bn)
        if rp is not None:
            jp = str(rp)
            bn = rp.name
    if not jp and linked:
        rp = resolve_sim_sequence_json_path(str(linked))
        if rp is not None:
            jp = str(rp)
            if not bn:
                bn = rp.name
    if not jp and seq_u:
        try:
            from .control_window import _resolve_event_animation_entry

            mapping = dict(event_p or {})
            mapping["seq"] = seq_u
            j, _, _, _ = _resolve_event_animation_entry(seq_u, mapping)
            if j:
                rp = resolve_sim_sequence_json_path(str(j))
                if rp is not None:
                    jp = str(rp)
                    bn = rp.name
        except Exception:
            pass
    return jp, bn


def _apply_renewal_fields_for_json_step(
    *,
    step_kind: str,
    json_path: Optional[str],
    json_bn: str,
    linked: str,
    parsed_steps: Optional[List[Any]],
    t0: float,
    proc: float,
    anim: float,
    has_renewal: bool,
    renewal_off: Optional[float],
    t_playback_sync: Optional[float],
    ports_panel_renewal: Tuple[Tuple[str, str], ...],
    ports_after: Tuple[Tuple[str, str], ...],
    t_playback_json_end: Optional[float],
    ports_panel_json_end: Tuple[Tuple[str, str], ...],
    panel_at_step_start: Dict[str, str],
    src_ev: Dict[str, Any],
    panel_ports: List[str],
) -> Tuple[
    bool,
    Optional[float],
    Optional[float],
    Tuple[Tuple[str, str], ...],
    Tuple[Tuple[str, str], ...],
    Optional[float],
    Tuple[Tuple[str, str], ...],
    Optional[str],
]:
    if step_kind != _STEP_KIND_JSON:
        return (
            has_renewal,
            renewal_off,
            t_playback_sync,
            ports_panel_renewal,
            ports_after,
            t_playback_json_end,
            ports_panel_json_end,
            json_path,
        )

    jp = str(json_path or "").strip() or None
    hr_res, ro_res, jp_res = resolve_renewal_for_json_step(
        json_path=jp,
        parsed_steps=parsed_steps,
        json_basename=str(json_bn or ""),
        linked=str(linked or ""),
    )
    file_renewal = bool(hr_res)
    for cand in (jp, json_bn, linked):
        if cand and has_renewal_marker_in_file(str(cand)):
            file_renewal = True
            break

    if hr_res:
        has_renewal = True
        if ro_res is not None:
            renewal_off = ro_res
        if jp_res:
            jp = str(jp_res)
            json_path = jp
    elif file_renewal:
        has_renewal = True
    elif bool(has_renewal) and renewal_off is None:
        has_renewal = False

    if not bool(has_renewal):
        return (
            has_renewal,
            renewal_off,
            t_playback_sync,
            ports_panel_renewal,
            ports_after,
            t_playback_json_end,
            ports_panel_json_end,
            jp,
        )

    try:
        from .json_playback_timing import playback_port_sync_sim_time
        from .control_sim_prerun_playback import panel_occ_tuple_from_dict

        if renewal_off is None:
            steps_fb = parsed_steps
            if not isinstance(steps_fb, list) or not steps_fb:
                for cand in (jp, json_bn, linked):
                    cs = str(cand or "").strip()
                    if not cs:
                        continue
                    steps_fb = load_sim_sequence_steps(cs)
                    if isinstance(steps_fb, list) and steps_fb:
                        break
            if isinstance(steps_fb, list) and steps_fb:
                try:
                    from .json_playback_timing import renewal_offset_fallback_from_steps

                    off_fb = renewal_offset_fallback_from_steps(list(steps_fb))
                    if off_fb is not None and float(off_fb) > 1e-9:
                        renewal_off = float(off_fb)
                except Exception:
                    pass

        if renewal_off is None:
            return (
                True,
                None,
                None,
                ports_panel_renewal,
                ports_after,
                None,
                (),
                jp,
            )

        off = float(renewal_off)
        t_playback_sync = playback_port_sync_sim_time(
            float(t0),
            float(proc),
            float(anim),
            has_renewal=True,
            renewal_offset_sec=off,
        )
        t_playback_json_end = None
        ports_panel_json_end = ()
        if not ports_panel_renewal:
            pred = predict_ports_occupancy_after_anim(dict(panel_at_step_start), src_ev)
            ports_panel_renewal = panel_occ_tuple_from_dict(
                {**panel_at_step_start, **pred},
                panel_ports,
            )
            ports_after = tuple(
                (str(k).strip().upper(), str(v or ""))
                for k, v in (pred or {}).items()
                if str(k).strip()
            )
    except Exception:
        pass

    return (
        True,
        renewal_off,
        t_playback_sync,
        ports_panel_renewal,
        ports_after,
        t_playback_json_end,
        ports_panel_json_end,
        jp,
    )


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
    t_json_run_start_sim: float = 0.0
    t_playback_port_sync: Optional[float] = None
    t_playback_json_end: Optional[float] = None
    ports_occ_after: Tuple[Tuple[str, str], ...] = ()
    ports_occ_panel: Tuple[Tuple[str, str], ...] = ()
    ports_occ_panel_renewal: Tuple[Tuple[str, str], ...] = ()
    ports_occ_panel_json_end: Tuple[Tuple[str, str], ...] = ()


@dataclass
class PlaybackSchedule:
    screen: int
    final_sim_time: float
    steps: Tuple[PlaybackScheduledStep, ...] = ()
    built_at_user_sp: float = 1.0
    ui_milestones: Tuple[Any, ...] = ()  # PlaybackUIMilestone — 순환 import 방지

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


def resolve_json_path_for_timeline_event(
    seq_u: str,
    event_payload: Optional[Dict[str, Any]],
    linked_basename: str,
) -> Tuple[str, Optional[str], str]:
    """타임라인 event+progress 쌍에서 JSON 절대 경로를 해석 (재생 job·막대 빌드 공통)."""
    return _resolve_json_path(str(seq_u or "").strip().upper(), dict(event_payload or {}), str(linked_basename or "").strip())


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
            lp = resolve_sim_sequence_json_path(bn)
            if lp is not None and lp.is_file():
                return lp.name, str(lp), "sim_sequence_linked"
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
            try:
                lp = resolve_sim_sequence_json_path(str(j))
                if lp is not None and lp.is_file():
                    return lp.name, str(lp), str(src or "sim_sequence_resolve")
            except Exception:
                pass
    except Exception:
        pass
    return bn, None, ""


def _initial_panel_occ_from_items(items: Tuple[SimTimelineItem, ...], ports: List[str]) -> Dict[str, str]:
    """타임라인 t≈0 progress/event 에서 초기 panel occ."""
    try:
        from .control_sim_bar_graph import _initial_bar_occ_at_t0

        return dict(_initial_bar_occ_at_t0(items, list(ports)))
    except Exception:
        out = {str(p).upper(): "" for p in (ports or [])}
        return out


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
    panel_ports = ["INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3"]
    panel_occ: Dict[str, str] = _initial_panel_occ_from_items(items, panel_ports)
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
            json_bn, json_path, map_src = resolve_json_path_for_timeline_event(seq_u, event_p, linked)
        json_est = _estimate_json_sec(json_path) if json_path else anim
        eff = compute_json_effective_speed(sp1, proc, json_est)
        wall = json_wall_duration_sec(json_est, eff)

        t_json_start = t0
        t_json_end = proc_end
        t_port_sync: Optional[float] = None
        has_renewal = False
        renewal_off: Optional[float] = None
        lead = 0.0
        t_playback_sync: Optional[float] = None
        t_playback_json_end: Optional[float] = None
        ports_after: Tuple[Tuple[str, str], ...] = ()
        ports_panel: Tuple[Tuple[str, str], ...] = ()
        ports_panel_renewal: Tuple[Tuple[str, str], ...] = ()
        ports_panel_json_end: Tuple[Tuple[str, str], ...] = ()

        panel_at_step_start = dict(panel_occ)

        # anim JSON — 엔진 occ 는 RUNNING(t0)부터 종료 후 값을 실어 보냄. 누적 panel_occ 에 섞지 않음.
        if step_kind != _STEP_KIND_JSON:
            po_ev = event_p.get("ports_occupancy")
            po_pr = progress_p.get("ports_occupancy")
            for po in (po_pr, po_ev):
                if isinstance(po, dict) and po:
                    for k, v in po.items():
                        ku = str(k).strip().upper()
                        if ku:
                            panel_occ[ku] = str(v or "")
            panel_at_step_start = dict(panel_occ)
        parsed_steps: Optional[List[Any]] = None
        if step_kind == _STEP_KIND_JSON and (json_path or json_est > 1e-9 or linked):
            if json_path:
                try:
                    parsed = json.loads(Path(str(json_path)).read_text(encoding="utf-8"))
                    if isinstance(parsed, list):
                        parsed_steps = parsed
                except Exception:
                    parsed_steps = None
            elif linked:
                try:
                    lp = resolve_sim_sequence_json_path(str(linked))
                    if lp is not None and lp.is_file():
                        json_path = str(lp)
                        json_bn = lp.name
                        parsed = json.loads(lp.read_text(encoding="utf-8"))
                        if isinstance(parsed, list):
                            parsed_steps = parsed
                except Exception:
                    pass
            try:
                from .json_playback_timing import (
                    playback_port_sync_sim_time,
                    playback_port_sync_sim_time_from_progress,
                    renewal_info_from_json_path,
                    renewal_info_from_steps,
                    resolve_playback_proc_anim,
                    timing_from_progress,
                )

                if json_path:
                    has_renewal, renewal_off = renewal_info_from_json_path(json_path)
                if parsed_steps is not None:
                    hr, ro = renewal_info_from_steps(parsed_steps)
                    if hr:
                        has_renewal = True
                        renewal_off = ro
                proc_pb, anim_pb = resolve_playback_proc_anim(
                    proc, anim, json_est_sec=float(json_est)
                )
                tm = timing_from_progress(
                    progress_p,
                    json_path=json_path,
                    steps=parsed_steps,
                    json_est_sec=float(json_est),
                )
                t_json_start = float(tm.get("t_json_start", t0))
                t_json_end = float(tm.get("t_json_end", proc_end))
                t_port_sync = tm.get("t_port_sync")
                has_renewal = bool(tm.get("has_renewal", has_renewal))
                renewal_off = tm.get("renewal_offset_sec", renewal_off)
                lead = float(tm.get("json_lead_sec", 0.0))
                proc = float(proc_pb)
                anim = float(anim_pb)
                if float(t_json_end) > float(t0) + 1e-9:
                    proc_end = float(t_json_end)
            except Exception:
                pass

            ev_u = _normalize_anim_event_seq(_s(progress_p, "event_seq") or _s(progress_p, "sequence_name") or seq_u)
            src_ev = _post_anim_src_from_progress_and_event(progress_p, event_p)
            try:
                from .json_playback_timing import (
                    playback_port_sync_sim_time,
                    playback_port_sync_sim_time_from_progress,
                )
                from .control_sim_prerun_playback import panel_occ_tuple_from_dict

                if bool(has_renewal):
                    # renewal sim·occ — step append 직전 _apply_renewal_fields_for_json_step 에서 확정
                    t_playback_sync = None
                    t_playback_json_end = None
                    ports_panel_json_end = ()
                elif ev_u in _ANIM_PORT_UPDATE_SEQS:
                    t_playback_sync = playback_port_sync_sim_time_from_progress(
                        progress_p,
                        fallback_t=float(t0),
                        json_path=json_path,
                        steps=parsed_steps,
                        json_est_sec=float(json_est),
                    )
                    t_playback_json_end = playback_port_sync_sim_time(
                        float(t0),
                        float(proc),
                        float(anim),
                        has_renewal=False,
                        renewal_offset_sec=None,
                    )
                    if t_playback_json_end is not None and (
                        t_playback_sync is None or float(t_playback_json_end) > float(t_playback_sync) + 1e-6
                    ):
                        t_playback_sync = float(t_playback_json_end)
                    pred_full = predict_ports_occupancy_after_anim(
                        dict(panel_at_step_start),
                        src_ev,
                    )
                    ports_panel = panel_occ_tuple_from_dict(
                        {**panel_at_step_start, **pred_full},
                        panel_ports,
                    )
                    ports_panel_renewal = ()
                    ports_after = tuple(
                        (str(k).strip().upper(), str(v or ""))
                        for k, v in (pred_full or {}).items()
                        if str(k).strip()
                    )
                    if t_playback_json_end is not None:
                        ports_panel_json_end = panel_occ_tuple_from_dict(
                            {**panel_at_step_start, **pred_full},
                            panel_ports,
                        )
                    for k, v in ports_after:
                        panel_occ[str(k)] = str(v or "")
            except Exception:
                t_playback_sync = None
                t_playback_json_end = None

        if step_kind == _STEP_KIND_JSON and not bool(has_renewal):
            try:
                hr_r, ro_r, jp_r = resolve_renewal_for_json_step(
                    json_path=json_path,
                    parsed_steps=parsed_steps,
                    json_basename=str(json_bn or ""),
                    linked=linked,
                )
                if hr_r:
                    has_renewal = True
                    renewal_off = ro_r
                    if jp_r:
                        json_path = str(jp_r)
            except Exception:
                pass

        if step_kind == _STEP_KIND_JSON and t_playback_sync is None and not bool(has_renewal):
            file_ren = False
            for cand in (json_path, json_bn, linked):
                if cand and has_renewal_marker_in_file(str(cand)):
                    file_ren = True
                    break
            if not file_ren:
                try:
                    hr_fb, ro_fb, jp_fb = resolve_renewal_for_json_step(
                        json_path=json_path,
                        parsed_steps=parsed_steps,
                        json_basename=str(json_bn or ""),
                        linked=linked,
                    )
                    if hr_fb:
                        has_renewal = True
                        renewal_off = ro_fb
                        if jp_fb:
                            json_path = str(jp_fb)
                        file_ren = True
                except Exception:
                    pass
            if not file_ren:
                try:
                    t_playback_sync = float(t_json_end if t_json_end > t0 else proc_end)
                    if not ports_after:
                        pred = predict_ports_occupancy_after_anim(
                            dict(panel_occ),
                            _post_anim_src_from_progress_and_event(progress_p, event_p),
                        )
                        ports_after = tuple(
                            (str(k).strip().upper(), str(v or ""))
                            for k, v in (pred or {}).items()
                            if str(k).strip()
                        )
                        for k, v in ports_after:
                            panel_occ[str(k)] = str(v or "")
                except Exception:
                    pass

        if t_playback_sync is None and step_kind == _STEP_KIND_OCC:
            t_playback_sync = float(t_ev)

        if not bool(has_renewal):
            ports_panel = tuple(
                (str(k).strip().upper(), str(panel_occ.get(k, "") or ""))
                for k in panel_ports
            )
        else:
            ports_panel = ()

        t_json_run_start_sim = float(t0) + float(lead)

        src_ev_final = _post_anim_src_from_progress_and_event(progress_p, event_p)
        json_path, json_bn = _resolve_json_path_for_schedule(
            seq_u, event_p, linked, json_path, str(json_bn or "")
        )
        (
            has_renewal,
            renewal_off,
            t_playback_sync,
            ports_panel_renewal,
            ports_after,
            t_playback_json_end,
            ports_panel_json_end,
            json_path,
        ) = _apply_renewal_fields_for_json_step(
            step_kind=step_kind,
            json_path=json_path,
            json_bn=str(json_bn or linked or ""),
            linked=linked,
            parsed_steps=parsed_steps,
            t0=float(t0),
            proc=float(proc),
            anim=float(anim),
            has_renewal=bool(has_renewal),
            renewal_off=renewal_off,
            t_playback_sync=t_playback_sync,
            ports_panel_renewal=ports_panel_renewal,
            ports_after=ports_after,
            t_playback_json_end=t_playback_json_end,
            ports_panel_json_end=ports_panel_json_end,
            panel_at_step_start=dict(panel_at_step_start),
            src_ev=src_ev_final,
            panel_ports=panel_ports,
        )

        if ports_after:
            for k, v in ports_after:
                ku = str(k).strip().upper()
                if ku:
                    panel_occ[ku] = str(v or "")

        if step_kind == _STEP_KIND_JSON:
            for cand in (json_path, json_bn, linked):
                if cand and has_renewal_marker_in_file(str(cand)):
                    has_renewal = True
                    ports_panel = ()
                    t_playback_json_end = None
                    ports_panel_json_end = ()
                    break

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
                t_json_run_start_sim=float(t_json_run_start_sim),
                t_playback_port_sync=t_playback_sync,
                t_playback_json_end=t_playback_json_end,
                ports_occ_after=ports_after,
                ports_occ_panel=ports_panel,
                ports_occ_panel_renewal=ports_panel_renewal,
                ports_occ_panel_json_end=ports_panel_json_end,
            )
        )
        step_i += 1

    sched = PlaybackSchedule(
        screen=screen,
        final_sim_time=float(result.final_sim_time),
        steps=tuple(steps),
        built_at_user_sp=float(sp1),
    )
    try:
        from .playback_plan import build_playback_ui_milestones

        ui_ms = build_playback_ui_milestones(
            sched,
            items,
            port_keys=panel_ports,
            initial_occ=_initial_panel_occ_from_items(items, panel_ports),
        )
        sched.ui_milestones = tuple(ui_ms)
    except Exception:
        sched.ui_milestones = ()
    return sched


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


def _f_src(src: Dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(str(src.get(key, "") or default).strip() or default)
    except Exception:
        return float(default)


def _json_basename_from_src(src: Dict[str, Any]) -> str:
    src_path = str(src.get("path") or "").strip()
    if src_path:
        try:
            return Path(src_path).name.strip().lower()
        except Exception:
            pass
    return str(src.get("file") or "").strip().lower()


def find_port_sync_step_for_active_job(
    schedule: PlaybackSchedule,
    src: Dict[str, Any],
    *,
    min_sync_t: float = 0.0,
) -> Optional[PlaybackScheduledStep]:
    """
    현재 JSON(파일명) + ``min_sync_t`` 보다 큰 다음 포트 sync step.

    renewal·JSON 종료 공통 (INOUT/BP·EP 전체).
    """
    src_json = _json_basename_from_src(src)
    min_t = max(0.0, float(min_sync_t))
    best: Optional[PlaybackScheduledStep] = None
    best_sync = 1e18
    for step in schedule.steps or ():
        if not isinstance(step, PlaybackScheduledStep):
            continue
        if step.t_playback_port_sync is None:
            continue
        if (
            not step.ports_occ_after
            and not step.ports_occ_panel
            and not step.ports_occ_panel_renewal
        ):
            continue
        st = float(step.t_playback_port_sync)
        if st <= min_t + 1e-6:
            continue
        step_json = str(step.json_basename or "").strip().lower()
        if src_json and step_json and src_json != step_json:
            continue
        if st < best_sync:
            best_sync = st
            best = step
    return best


def find_renewal_step_for_active_job(
    schedule: PlaybackSchedule,
    src: Dict[str, Any],
    *,
    min_sync_t: float = 0.0,
) -> Optional[PlaybackScheduledStep]:
    """renewal wall step — ``find_port_sync_step`` 중 ``has_renewal`` 만."""
    step = find_port_sync_step_for_active_job(schedule, src, min_sync_t=float(min_sync_t))
    if step is not None and bool(step.has_renewal):
        return step
    return None


def find_scheduled_step_for_anim_src(
    schedule: PlaybackSchedule,
    src: Dict[str, Any],
    *,
    min_sync_t: float = 0.0,
) -> Optional[PlaybackScheduledStep]:
    """JSON anim src 와 일치하는 스케줄 step (포트 sync 시각 조회용)."""
    from .control_sim_prerun_playback import _normalize_anim_event_seq, _s_val

    ev = _normalize_anim_event_seq(str(src.get("event") or src.get("event_seq") or src.get("seq") or ""))
    t0 = _f_src(src, "event_start_sim_time", _f_src(src, "_event_start_sim", _f_src(src, "t", _f_src(src, "sim_time", 0.0))))
    if not ev:
        return None
    src_path = str(src.get("path") or "").strip()
    src_json = ""
    if src_path:
        try:
            from pathlib import Path

            src_json = Path(src_path).name.strip().lower()
        except Exception:
            src_json = ""
    if not src_json:
        src_json = str(src.get("file") or "").strip().lower()
    src_lot = str(src.get("lot_id") or "").strip()
    src_to = str(src.get("to_port_id") or src.get("port_id") or "").strip().upper()
    min_t = max(0.0, float(min_sync_t))

    candidates: List[Tuple[float, PlaybackScheduledStep]] = []
    for step in schedule.steps or ():
        if not isinstance(step, PlaybackScheduledStep):
            continue
        p = step.progress_payload if isinstance(step.progress_payload, dict) else {}
        step_ev = _normalize_anim_event_seq(_s_val(p.get("event_seq") or p.get("sequence_name") or step.event_seq))
        if step_ev != ev:
            continue
        step_t0 = _f_src(p, "event_start_sim_time", _f_src(p, "sim_time", float(step.t_event)))
        if step_t0 <= 1e-9:
            step_t0 = float(step.t_event)
        dt = abs(float(step_t0) - float(t0))
        if dt > 0.05:
            continue
        step_json = str(step.json_basename or "").strip().lower()
        if src_json and step_json and src_json != step_json:
            continue
        ep = step.event_payload if isinstance(step.event_payload, dict) else {}
        if src_to:
            to_p = str(ep.get("to_port_id") or ep.get("port_id") or "").strip().upper()
            if not to_p or to_p != src_to:
                continue
        if src_lot:
            lot = str(ep.get("lot_id") or p.get("lot_id") or "").strip()
            if lot and lot != src_lot:
                continue
        sync_t = step.t_playback_port_sync
        if sync_t is None:
            continue
        score = float(dt)
        if src_json and step_json and src_json == step_json:
            score -= 1000.0
        candidates.append((score, step))

    if not candidates:
        return None
    pool = list(candidates)
    pool.sort(key=lambda x: (float(x[0]), float(x[1].t_playback_port_sync or 0.0)))
    return pool[0][1]


def resolve_playback_port_sync_for_renewal(
    schedule: Optional[PlaybackSchedule],
    src: Dict[str, Any],
    *,
    min_sync_t: float = 0.0,
) -> Tuple[Optional[float], Optional[PlaybackScheduledStep]]:
    """
    renewal wall 시 plan 포트 sync sim 시각 + 매칭 step.

    **현재 JSON anim(src) + has_renewal 만** — 파일명·floor 기준 다음 step 조회는 사용하지 않는다.
    (동일 JSON 다회 사용 시 3번째부터 proc_end step 으로 잘못 떨어지는 버그 방지)
    """
    _ = float(min_sync_t)  # legacy — anim src 매칭만 사용
    if schedule is not None:
        step = find_scheduled_step_for_anim_src(schedule, src, min_sync_t=0.0)
        if (
            step is not None
            and bool(step.has_renewal)
            and step.t_playback_port_sync is not None
        ):
            return float(step.t_playback_port_sync), step
    try:
        from .json_playback_timing import playback_port_sync_sim_time, renewal_info_from_steps

        t0 = _f_src(
            src,
            "event_start_sim_time",
            _f_src(src, "_event_start_sim", _f_src(src, "t", _f_src(src, "sim_time", 0.0))),
        )
        proc = _f_src(src, "proc_sec", 0.0)
        anim = _f_src(src, "anim_sec", _f_src(src, "est_total", 0.0))
        if proc <= 1e-9 and anim > 1e-9:
            proc = anim
        has_r = bool(src.get("has_renewal"))
        ro = src.get("renewal_offset_sec")
        if not has_r or ro is None:
            parsed = src.get("parsed")
            if isinstance(parsed, list) and parsed:
                has_r, ro = renewal_info_from_steps(parsed)
        if not has_r:
            jp = str(src.get("path") or "").strip()
            if jp:
                from .json_playback_timing import renewal_info_from_json_path

                has_r, ro = renewal_info_from_json_path(jp)
        if not has_r:
            return None, None
        st = playback_port_sync_sim_time(
            float(t0),
            float(proc),
            float(anim),
            has_renewal=True,
            renewal_offset_sec=ro,
        )
        if st is not None:
            return float(st), None
    except Exception:
        pass
    return None, None


__all__ = [
    "PlaybackSchedule",
    "PlaybackScheduledStep",
    "build_playback_schedule",
    "build_schedules_by_screen",
    "find_port_sync_step_for_active_job",
    "find_renewal_step_for_active_job",
    "find_scheduled_step_for_anim_src",
    "resolve_playback_port_sync_for_renewal",
    "resolve_json_path_for_timeline_event",
]
