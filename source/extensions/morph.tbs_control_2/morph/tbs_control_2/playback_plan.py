"""
프리런 PlaybackSchedule + 타임라인 → UI 마일스톤 SSOT (포트·막대 공통).

프리런 1회: 스케줄 step panel occ 를 중심으로 ``occ_full`` 확정.
FOUP progress 등 점유 비변경 틱의 엔진 스냅샷은 넣지 않는다(미래 LOT 깜빡임 방지).
재생: ``sim_now`` lookup 만.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .control_sim_prerun_playback import (
    SimTimelineItem,
    _ANIM_PORT_UPDATE_SEQS,
    _f_val,
    _s_val,
)
from .playback_schedule import PlaybackSchedule, PlaybackScheduledStep


@dataclass(frozen=True)
class PlaybackUIMilestone:
    t_sim: float
    order: int
    kind: str  # foup_start | foup_end | occ_full | occ_snap | occ_plan (legacy)
    data: Any


def _occ_tuple_to_dict(pairs: Tuple[Tuple[str, str], ...]) -> Dict[str, str]:
    return {str(k).strip().upper(): str(v or "") for k, v in (pairs or ()) if str(k).strip()}


def _sorted_timeline_items(items: Tuple[SimTimelineItem, ...]) -> Tuple[SimTimelineItem, ...]:
    kind_prio = {"log": 0, "event": 1, "progress": 2}
    try:
        return tuple(
            sorted(
                (it for it in (items or ()) if isinstance(it, SimTimelineItem)),
                key=lambda it: (float(getattr(it, "t", 0.0) or 0.0), int(kind_prio.get(str(it.kind), 9))),
            )
        )
    except Exception:
        return ()


def _normalize_occ_payload(po: Any, port_keys: Sequence[str]) -> Dict[str, str]:
    if not isinstance(po, dict) or not po:
        return {}
    keys = {str(p).strip().upper() for p in (port_keys or ()) if str(p).strip()}
    return {
        str(k).strip().upper(): str(v or "")
        for k, v in po.items()
        if str(k).strip() and str(k).strip().upper() in keys
    }


def _schedule_anim_json_occ_block_windows(
    schedule: Optional[PlaybackSchedule],
) -> Tuple[Tuple[float, float], ...]:
    """
    JSON step — **이벤트 시작~공정 종료(+버퍼)** 구간 엔진 occ 차단.

    엔진 progress 는 RUNNING(t0)부터 공정 종료 후 occ 를 실어 보낸다.
    JSON step 의 포트 갱신 시각은 스케줄 milestone(renewal sim / json-end) 이 **유일한 SSOT** 이므로,
    이벤트 종류·renewal 감지 여부와 무관하게 이 구간의 엔진 occ 는 전부 무시한다.
    (renewal 감지가 런타임에서 실패해도 proc_end 엔진 occ 가 새지 않도록 한다.)
    """
    out: List[Tuple[float, float]] = []
    for step in (schedule.steps or ()) if schedule is not None else ():
        if not isinstance(step, PlaybackScheduledStep):
            continue
        if str(step.kind or "").strip().lower() != "json_step":
            continue
        try:
            t_start = float(step.t_event or 0.0)
            # proc_end 직후 DONE occ 까지 포함 (엔진 occ 가 약간 늦게 실릴 수 있음).
            t_end = float(step.t_proc_end) + 5.0
            if t_end >= t_start - 1e-6:
                out.append((float(t_start), float(t_end)))
        except Exception:
            pass
    return tuple(out)


def _engine_occ_blocked_in_schedule_anim_json(
    t_sim: float,
    windows: Sequence[Tuple[float, float]],
) -> bool:
    t = float(t_sim)
    for t_start, t_end in windows or ():
        if float(t_start) - 1e-6 <= t <= float(t_end) + 1e-6:
            return True
    return False


def _step_json_has_renewal_marker(step: PlaybackScheduledStep) -> bool:
    """스케줄 step — renewal 마커 (``playback_renewal_ports`` SSOT)."""
    try:
        from .playback_renewal_ports import step_json_has_renewal_marker

        return bool(step_json_has_renewal_marker(step))
    except Exception:
        return bool(step.has_renewal)


def _collect_engine_port_occ_changes(
    sorted_items: Tuple[SimTimelineItem, ...],
    port_keys: Sequence[str],
    *,
    schedule: Optional[PlaybackSchedule] = None,
) -> List[Tuple[float, int, Dict[str, str]]]:
    """
    프리런 타임라인 — event·progress 의 ``ports_occupancy``.

    - anim 포트 이벤트(ARRIVED/MOVE/…): ``playback_schedule`` milestone 이 SSOT → 스킵.
    - renewal JSON 실행~공정 종료 구간: 엔진 occ 전부 스킵.
    - FOUP·READYTO*·progress 틱: 스킵.
      (FOUP progress 는 공정 중에도 엔진 전체 스냅샷을 실어, 다른 포트/미래 LOT 이
       포트 패널에 잠깐 뜨는 버그의 원인이 됨. 점유 변경은 JSON step milestone 만.)
    """
    from .control_sim_prerun_playback import _normalize_anim_event_seq

    anim_json_windows = _schedule_anim_json_occ_block_windows(schedule)
    try:
        from .playback_renewal_ports import (
            engine_occ_blocked_for_renewal_json,
            renewal_json_engine_occ_block_windows,
        )

        renewal_windows = renewal_json_engine_occ_block_windows(schedule)
    except Exception:
        renewal_windows = ()

    # 포트 점유를 바꾸지 않거나, JSON SSOT 와 충돌하는 시퀀스
    _ignore_seqs = frozenset(
        {
            "PORT_OCC_REFRESH",
            "FOUP_PROCESS",
            "FOUP_PROCESS_START",
            "FOUP_PROCESS_END",
            "READYTOLOAD",
            "READYTOUNLOAD",
        }
    )

    out: List[Tuple[float, int, Dict[str, str]]] = []
    seq_i = 0
    last_sig = ""
    for it in sorted_items or ():
        kind = str(it.kind or "").strip().lower()
        # progress 틱(FOUP RUNNING 등)의 ports_occupancy 는 패널 SSOT 에 넣지 않음
        if kind != "event" or not isinstance(it.payload, dict):
            continue
        try:
            t_ev = float(getattr(it, "t", 0.0) or 0.0)
        except Exception:
            t_ev = 0.0
        if _engine_occ_blocked_in_schedule_anim_json(t_ev, anim_json_windows):
            continue
        if renewal_windows and engine_occ_blocked_for_renewal_json(t_ev, renewal_windows):
            continue
        p = dict(it.payload)
        ev = _normalize_anim_event_seq(
            _s_val(p.get("event_seq") or p.get("sequence_name") or p.get("seq"))
        )
        if ev in _ignore_seqs:
            continue
        if ev in _ANIM_PORT_UPDATE_SEQS:
            continue
        occ_d = _normalize_occ_payload(p.get("ports_occupancy"), port_keys)
        if not occ_d:
            continue
        sig = f"{t_ev:.4f}|{sorted(occ_d.items())}"
        if sig == last_sig:
            continue
        last_sig = sig
        out.append((float(t_ev), int(seq_i), dict(occ_d)))
        seq_i += 1
    return out


def _step_playback_sync_t(
    step: PlaybackScheduledStep,
    *,
    is_renewal: bool,
) -> Optional[float]:
    """step 의 포트 갱신 sim 시각 — renewal 이면 offset 재계산, 아니면 JSON-end/proc_end."""
    from .playback_renewal_ports import (
        renewal_playback_port_sync_for_step,
        step_json_has_renewal_marker,
    )

    if is_renewal or step_json_has_renewal_marker(step):
        sync_t = renewal_playback_port_sync_for_step(step)
        if sync_t is not None:
            return float(sync_t)
        # offset/proc 재계산 실패 시에도 JSON-end 로 밀지 말고 lead+offset 으로 보정
        try:
            from .json_playback_timing import json_lead_sec, resolve_playback_proc_anim

            t0s = float(step.t_event or 0.0)
            p = step.progress_payload if isinstance(step.progress_payload, dict) else {}
            try:
                t0s = float(str(p.get("event_start_sim_time") or t0s))
            except Exception:
                pass
            proc_pb, anim_pb = resolve_playback_proc_anim(
                float(step.proc_sec or 0.0),
                float(step.anim_sec or 0.0),
                json_est_sec=float(step.json_est_sec or 0.0),
            )
            lead = float(json_lead_sec(float(proc_pb), float(anim_pb)))
            off = 0.0
            try:
                if step.renewal_offset_sec is not None and float(step.renewal_offset_sec) > 1e-9:
                    off = float(step.renewal_offset_sec)
            except Exception:
                off = 0.0
            run_start = step.t_json_run_start_sim
            if run_start is not None and float(run_start) > 1e-9:
                return float(run_start) + float(off)
            return float(t0s) + float(lead) + float(off)
        except Exception:
            return None

    sync_t = step.t_playback_port_sync
    if sync_t is not None:
        return float(sync_t)
    if step.t_playback_json_end is not None:
        return float(step.t_playback_json_end)
    try:
        return float(step.t_proc_end)
    except Exception:
        return None


def _collect_schedule_port_occ_points_parallel(
    schedule: PlaybackSchedule,
    *,
    initial_occ: Optional[Mapping[str, str]] = None,
) -> List[Tuple[float, int, Dict[str, str]]]:
    """
    병렬 모드 전용: sync_t 시각 순으로 running+predict 재 bake.

    schedule 1차 패스(시작 순)의 ``ports_occ_panel*`` 는 A∥B overlapping 에
    오염될 수 있으므로 무시하고, 이미 반영된 sync 만 누적한 ``running`` 위에서
    각 JSON 의 post-anim predict 를 다시 적용한다.
    """
    from .control_sim_prerun_playback import (
        _post_anim_src_from_progress_and_event,
        predict_ports_occupancy_after_anim,
    )
    from .playback_renewal_ports import (
        renewal_full_panel_occ_for_step,
        step_json_has_renewal_marker,
    )

    panel_ports = ["INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3"]
    running: Dict[str, str] = {p: "" for p in panel_ports}
    if isinstance(initial_occ, Mapping):
        for k, v in initial_occ.items():
            ku = str(k).strip().upper()
            if ku in running:
                running[ku] = str(v or "")

    candidates: List[Tuple[float, int, PlaybackScheduledStep, bool]] = []
    for step in schedule.steps or ():
        if not isinstance(step, PlaybackScheduledStep):
            continue
        kind = str(step.kind or "").strip().lower()
        if kind in ("occ_refresh", "foup", "foup_process", "event_only"):
            continue
        if kind != "json_step":
            continue
        is_renewal = bool(step.has_renewal) or step_json_has_renewal_marker(step)
        # non-renewal: 포트 갱신 대상이 아니면 스킵
        if not is_renewal:
            panel_pairs = step.ports_occ_panel if step.ports_occ_panel else step.ports_occ_after
            ev_u = str(
                (step.progress_payload or {}).get("event_seq")
                or (step.event_payload or {}).get("seq")
                or step.event_seq
                or ""
            ).strip().upper()
            if not panel_pairs and ev_u not in _ANIM_PORT_UPDATE_SEQS:
                continue
        sync_t = _step_playback_sync_t(step, is_renewal=is_renewal)
        if sync_t is None:
            continue
        try:
            t_ev = float(step.t_event or 0.0)
        except Exception:
            t_ev = 0.0
        sync_f = max(float(sync_t), float(t_ev))
        order = (50000 if is_renewal else 10000) + int(step.index)
        candidates.append((sync_f, order, step, is_renewal))

    candidates.sort(key=lambda x: (float(x[0]), int(x[1])))

    out: List[Tuple[float, int, Dict[str, str]]] = []
    last_sync_t = -1.0
    for sync_f0, order, step, is_renewal in candidates:
        sync_f = float(sync_f0)
        # renewal 은 이전 MOVE json-end 시각으로 끌어올리지 않음 (ARRIVED→JSON-end 착시)
        if (not is_renewal) and sync_f + 1e-9 < float(last_sync_t):
            sync_f = float(last_sync_t) + 1e-4

        if is_renewal:
            occ_r = renewal_full_panel_occ_for_step(
                step,
                base_occ=dict(running),
                panel_ports=panel_ports,
                ignore_baked_pairs=True,
            )
        else:
            p = step.progress_payload if isinstance(step.progress_payload, dict) else {}
            ep = step.event_payload if isinstance(step.event_payload, dict) else {}
            src = _post_anim_src_from_progress_and_event(p, ep)
            try:
                bn = str(getattr(step, "json_basename", "") or "").strip()
                if bn and not src.get("file"):
                    src["file"] = bn
                jp = str(getattr(step, "json_path", "") or "").strip()
                if jp and not src.get("path"):
                    src["path"] = jp
            except Exception:
                pass
            pred = predict_ports_occupancy_after_anim(dict(running), src) or {}
            occ_r = {k: str(running.get(k, "") or "") for k in panel_ports}
            for k, v in (pred or {}).items():
                ku = str(k).strip().upper()
                if ku in occ_r:
                    occ_r[ku] = str(v or "")

        if not occ_r:
            continue
        out.append((sync_f, int(order), dict(occ_r)))
        last_sync_t = float(sync_f)
        for k in panel_ports:
            running[k] = str(occ_r.get(k, "") or "")
    return out


def _collect_schedule_port_occ_points(
    schedule: PlaybackSchedule,
    *,
    initial_occ: Optional[Mapping[str, str]] = None,
) -> List[Tuple[float, int, Dict[str, str]]]:
    """
    스케줄 step panel occ → occ_full 마일스톤.

    - renewal JSON: ``playback_renewal_ports`` SSOT (renewal sim 1회, proc_end 없음).
    - 그 외 anim JSON: 공정 종료 1회.
    - FOUP/event_only: 포트 점유 변경 없음 → 마일스톤 미생성.
    - sync_t 는 이벤트 순서·t_event 대비 역행하지 않게 clamp (미래 LOT 조기 표시 방지).

    병렬(``SIM_PARALLEL_NONCONFLICTING_MOVES``): sync_t 순 · running+predict 재 bake
    (시작 순에 오염된 ports_occ_panel* 무시). 직렬은 기존 경로 유지.
    """
    try:
        from .sim_parallel_rails import parallel_moves_enabled

        if parallel_moves_enabled():
            return _collect_schedule_port_occ_points_parallel(
                schedule, initial_occ=initial_occ
            )
    except Exception:
        pass

    from .playback_renewal_ports import (
        renewal_full_panel_occ_for_step,
        renewal_playback_port_sync_for_step,
        renewal_port_milestone_for_step,
        step_json_has_renewal_marker,
    )

    panel_ports = ["INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3"]
    running: Dict[str, str] = {p: "" for p in panel_ports}
    if isinstance(initial_occ, Mapping):
        for k, v in initial_occ.items():
            ku = str(k).strip().upper()
            if ku in running:
                running[ku] = str(v or "")

    out: List[Tuple[float, int, Dict[str, str]]] = []
    last_sync_t = -1.0
    for step in schedule.steps or ():
        if not isinstance(step, PlaybackScheduledStep):
            continue

        kind = str(step.kind or "").strip().lower()
        if kind in ("occ_refresh", "foup", "foup_process", "event_only"):
            continue

        is_renewal_json = bool(step.has_renewal) or step_json_has_renewal_marker(step)
        if is_renewal_json:
            sync_t = _step_playback_sync_t(step, is_renewal=True)
            if sync_t is None:
                if step.ports_occ_panel_renewal or step.ports_occ_after:
                    occ_r_fb = renewal_full_panel_occ_for_step(
                        step,
                        base_occ=dict(running),
                        panel_ports=panel_ports,
                    )
                    if occ_r_fb:
                        for k in panel_ports:
                            running[k] = str(occ_r_fb.get(k, "") or "")
                continue
            occ_r: Optional[Dict[str, str]] = None
            ms = renewal_port_milestone_for_step(
                step,
                panel_ports=panel_ports,
                base_occ=dict(running),
            )
            if ms is not None:
                _, occ_r = ms
            else:
                occ_r = renewal_full_panel_occ_for_step(
                    step,
                    base_occ=dict(running),
                    panel_ports=panel_ports,
                )
            if occ_r:
                try:
                    t_ev = float(step.t_event or 0.0)
                except Exception:
                    t_ev = 0.0
                # renewal 은 last_sync_t 로 끌어올리지 않음 (이전 JSON-end bake 잔여 방지)
                sync_f = max(float(sync_t), float(t_ev))
                if sync_f + 1e-9 < float(last_sync_t):
                    # 완전 역행만 소량 보정 (동일 시각 허용)
                    sync_f = float(last_sync_t) + 1e-4
                out.append((sync_f, 50000 + int(step.index), dict(occ_r)))
                last_sync_t = float(sync_f)
                for k in panel_ports:
                    running[k] = str(occ_r.get(k, "") or "")
            elif step.ports_occ_after:
                for k, v in step.ports_occ_after:
                    ku = str(k).strip().upper()
                    if ku in running:
                        running[ku] = str(v or "")
            continue

        panel_pairs = step.ports_occ_panel if step.ports_occ_panel else step.ports_occ_after
        if not panel_pairs:
            continue
        # non-renewal 이라도 파일 마커가 늦게 잡히면 renewal 경로로
        if step_json_has_renewal_marker(step):
            sync_t_rn = renewal_playback_port_sync_for_step(step)
            if sync_t_rn is not None:
                occ_rn = renewal_full_panel_occ_for_step(
                    step,
                    base_occ=dict(running),
                    panel_ports=panel_ports,
                )
                if occ_rn:
                    try:
                        t_ev = float(step.t_event or 0.0)
                    except Exception:
                        t_ev = 0.0
                    sync_f = max(float(sync_t_rn), float(t_ev))
                    if sync_f + 1e-9 < float(last_sync_t):
                        sync_f = float(last_sync_t) + 1e-4
                    out.append((sync_f, 50000 + int(step.index), dict(occ_rn)))
                    last_sync_t = float(sync_f)
                    for k in panel_ports:
                        running[k] = str(occ_rn.get(k, "") or "")
                    continue
        sync_t = step.t_playback_port_sync
        if sync_t is None:
            if step.t_playback_json_end is not None:
                sync_t = float(step.t_playback_json_end)
            else:
                sync_t = float(step.t_proc_end)
        occ_d = _occ_tuple_to_dict(panel_pairs)
        if not occ_d:
            continue
        try:
            t_ev = float(step.t_event or 0.0)
        except Exception:
            t_ev = 0.0
        sync_f = max(float(sync_t), float(t_ev), float(last_sync_t))
        out.append((sync_f, 10000 + int(step.index), dict(occ_d)))
        last_sync_t = float(sync_f)
        for k in panel_ports:
            if k in occ_d:
                running[k] = str(occ_d.get(k, "") or "")
    return out


def _merge_occ_timeline_to_full_milestones(
    *,
    port_keys: Sequence[str],
    initial_occ: Mapping[str, str],
    engine_changes: List[Tuple[float, int, Dict[str, str]]],
    schedule_points: List[Tuple[float, int, Dict[str, str]]],
) -> List[Tuple[float, int, str, Dict[str, str]]]:
    """시간순 merge → 각 시점의 **전체** panel occ (occ_full)."""
    keys = [str(p).strip().upper() for p in (port_keys or ()) if str(p).strip()]
    occ: Dict[str, str] = {k: "" for k in keys}
    for k, v in (initial_occ or {}).items():
        ku = str(k).strip().upper()
        if ku in occ:
            occ[ku] = str(v or "")

    merged_in: List[Tuple[float, int, str, Dict[str, str]]] = []
    merged_in.extend((float(t), int(o), "engine", dict(d)) for t, o, d in engine_changes)
    merged_in.extend((float(t), int(o), "schedule", dict(d)) for t, o, d in schedule_points)
    merged_in.sort(key=lambda x: (float(x[0]), int(x[1])))

    raw: List[Tuple[float, int, str, Dict[str, str]]] = []
    if keys:
        raw.append((0.0, -1, "occ_full", dict(occ)))

    last_sig = ""
    for t_sim, _order, src, data in merged_in:
        if src == "schedule":
            for k in keys:
                if k in data:
                    occ[k] = str(data.get(k, "") or "")
        else:
            for k, v in data.items():
                if k in occ:
                    occ[k] = str(v or "")
        sig = "|".join(f"{k}={occ.get(k, '')}" for k in keys)
        if sig == last_sig:
            continue
        last_sig = sig
        raw.append((float(t_sim), int(_order), "occ_full", dict(occ)))
    return raw


def build_playback_ui_milestones(
    schedule: PlaybackSchedule,
    items: Tuple[SimTimelineItem, ...],
    *,
    port_keys: Optional[Sequence[str]] = None,
    initial_occ: Optional[Mapping[str, str]] = None,
) -> Tuple[PlaybackUIMilestone, ...]:
    """
    프리런 1회 UI SSOT.

    1) 스케줄 step ``ports_occ_panel`` / renewal·json-end (점유 변경 SSOT)
    2) 엔진 event occ 는 anim/FOUP/progress 제외한 잔여만 (실질적으로 거의 없음)
    → ``occ_full`` 마일스톤만 저장.
    """
    from .control_sim_bar_graph import _collect_foup_milestones_from_items

    keys = [str(p).strip().upper() for p in (port_keys or ()) if str(p).strip()]
    sorted_items = _sorted_timeline_items(items)
    raw: List[Tuple[float, int, str, Any]] = []
    raw.extend(_collect_foup_milestones_from_items(sorted_items, seq_start=0))

    init = dict(initial_occ or {})
    engine_changes = _collect_engine_port_occ_changes(sorted_items, keys, schedule=schedule)
    schedule_points = _collect_schedule_port_occ_points(schedule, initial_occ=init)
    occ_full = _merge_occ_timeline_to_full_milestones(
        port_keys=keys,
        initial_occ=init,
        engine_changes=engine_changes,
        schedule_points=schedule_points,
    )
    order_base = len(raw)
    for i, (t, o, k, d) in enumerate(occ_full):
        raw.append((float(t), int(order_base + i), str(k), dict(d)))

    raw.sort(key=lambda m: (float(m[0]), int(m[1])))
    return tuple(
        PlaybackUIMilestone(t_sim=float(t), order=int(o), kind=str(k), data=d)
        for t, o, k, d in raw
    )


def _apply_milestone_to_occ(
    bar_occ: Dict[str, str],
    all_ports: Sequence[str],
    kind: str,
    data: Any,
) -> None:
    if kind == "occ_full":
        occ_d = dict(data or {})
        for port in all_ports:
            if port in occ_d:
                bar_occ[port] = str(occ_d.get(port, "") or "")
        return
    if kind == "occ_snap":
        occ_d = dict(data or {})
        for port in all_ports:
            if port in occ_d:
                bar_occ[port] = str(occ_d.get(port, "") or "")
        return
    if kind == "occ_plan":
        occ_pred = dict((data or (None, {}))[1] or {})
        for port in all_ports:
            if port in occ_pred:
                bar_occ[port] = str(occ_pred.get(port, "") or "")


def replay_ports_occ_at_t(
    milestones: Sequence[PlaybackUIMilestone],
    *,
    t_sim: float,
    all_ports: Sequence[str],
    initial_occ: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    ports = [str(p).strip().upper() for p in (all_ports or []) if str(p).strip()]
    occ: Dict[str, str] = {p: "" for p in ports}
    if isinstance(initial_occ, dict):
        for k, v in initial_occ.items():
            ku = str(k).strip().upper()
            if ku in occ:
                occ[ku] = str(v or "")
    t_cut = float(t_sim)
    for m in milestones or ():
        if float(m.t_sim) > t_cut + 1e-9:
            break
        if str(m.kind) == "occ_full":
            occ_d = dict(m.data or {})
            for port in ports:
                if port in occ_d:
                    occ[port] = str(occ_d.get(port, "") or "")
            continue
        if m.kind == "occ_snap":
            _apply_milestone_to_occ(occ, ports, "occ_snap", m.data)
        elif m.kind == "occ_plan":
            _apply_milestone_to_occ(occ, ports, "occ_plan", m.data)
    return dict(occ)


def milestone_index_at_sim(
    milestones: Sequence[PlaybackUIMilestone],
    t_sim: float,
) -> int:
    last = -1
    t_cut = float(t_sim)
    for i, m in enumerate(milestones or ()):
        if m.kind not in ("occ_full", "occ_snap", "occ_plan"):
            continue
        if float(m.t_sim) <= t_cut + 1e-9:
            last = int(i)
        else:
            break
    return int(last)


def milestones_to_finalize_tuples(
    milestones: Sequence[PlaybackUIMilestone],
) -> List[Tuple[float, int, str, Any]]:
    return [(float(m.t_sim), int(m.order), str(m.kind), m.data) for m in (milestones or ())]


def _freeze_occ_dict(occ: Mapping[str, str]) -> Tuple[Tuple[str, str], ...]:
    pairs: List[Tuple[str, str]] = []
    for k, v in (occ or {}).items():
        ku = str(k).strip().upper()
        if ku:
            pairs.append((ku, str(v or "")))
    pairs.sort(key=lambda p: p[0])
    return tuple(pairs)


def _port_keys_from_milestones(
    milestones: Sequence[PlaybackUIMilestone],
    initial_occ: Mapping[str, str],
) -> Tuple[str, ...]:
    keys: set = set()
    for k in initial_occ:
        ku = str(k).strip().upper()
        if ku:
            keys.add(ku)
    for m in milestones or ():
        if m.kind in ("occ_full", "occ_snap") and isinstance(m.data, dict):
            keys.update(str(k).strip().upper() for k in m.data if str(k).strip())
        elif m.kind == "occ_plan" and isinstance(m.data, tuple) and len(m.data) >= 2:
            occ_d = m.data[1]
            if isinstance(occ_d, dict):
                keys.update(str(k).strip().upper() for k in occ_d if str(k).strip())
    return tuple(sorted(keys))


@dataclass(frozen=True)
class PlaybackPlanSnapshot:
    screen: int
    final_sim_time: float
    initial_occ: Tuple[Tuple[str, str], ...]
    port_keys: Tuple[str, ...]
    milestones: Tuple[PlaybackUIMilestone, ...]

    def initial_occ_dict(self) -> Dict[str, str]:
        return {str(k): str(v or "") for k, v in (self.initial_occ or ())}

    def ports_at(self, t_sim: float) -> Dict[str, str]:
        return replay_ports_occ_at_t(
            self.milestones,
            t_sim=float(t_sim),
            all_ports=self.port_keys,
            initial_occ=self.initial_occ_dict(),
        )

    def has_port_milestones(self) -> bool:
        return any(m.kind in ("occ_full", "occ_snap", "occ_plan") for m in (self.milestones or ()))


def build_playback_plan_snapshot(
    screen: int,
    schedule: PlaybackSchedule,
    items: Tuple[SimTimelineItem, ...],
    *,
    initial_occ: Mapping[str, str],
    port_keys: Sequence[str],
    final_sim_time: float,
) -> PlaybackPlanSnapshot:
    keys = tuple(
        sorted({str(p).strip().upper() for p in (port_keys or ()) if str(p).strip()})
    )
    init = dict(initial_occ or {})
    ui_ms = build_playback_ui_milestones(
        schedule,
        items,
        port_keys=keys,
        initial_occ=init,
    )
    try:
        schedule.ui_milestones = tuple(ui_ms)
    except Exception:
        pass
    if not keys:
        keys = _port_keys_from_milestones(ui_ms, init)
    if keys:
        init = replay_ports_occ_at_t(ui_ms, t_sim=0.0, all_ports=keys, initial_occ=init)
    return PlaybackPlanSnapshot(
        screen=int(screen),
        final_sim_time=float(final_sim_time),
        initial_occ=_freeze_occ_dict(init),
        port_keys=keys,
        milestones=tuple(ui_ms),
    )


__all__ = [
    "PlaybackPlanSnapshot",
    "PlaybackUIMilestone",
    "build_playback_plan_snapshot",
    "build_playback_ui_milestones",
    "milestone_index_at_sim",
    "milestones_to_finalize_tuples",
    "replay_ports_occ_at_t",
]
