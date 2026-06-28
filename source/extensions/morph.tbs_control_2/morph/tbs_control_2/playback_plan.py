"""
프리런 PlaybackSchedule + 타임라인 → UI 마일스톤 SSOT (포트·막대 공통).

프리런 1회: 엔진 ``ports_occupancy`` 타임라인 + 스케줄 step panel occ 를 merge 해 ``occ_full`` 확정.
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

    - anim 포트 이벤트(ARRIVED/MOVE/…): ``playback_schedule`` milestone 이 SSOT.
    - renewal JSON 실행~공정 종료 구간: 엔진 occ 전부 스킵 — renewal sim 마일스톤만.
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
    out: List[Tuple[float, int, Dict[str, str]]] = []
    seq_i = 0
    last_sig = ""
    for it in sorted_items or ():
        kind = str(it.kind or "").strip().lower()
        if kind not in ("event", "progress") or not isinstance(it.payload, dict):
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
        if ev == "PORT_OCC_REFRESH":
            continue
        if ev in _ANIM_PORT_UPDATE_SEQS:
            continue
        if kind == "progress":
            st = _s_val(p.get("status")).upper()
            if st not in ("RUNNING", "DONE"):
                continue
            if st == "DONE" and ev in _ANIM_PORT_UPDATE_SEQS:
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


def _collect_schedule_port_occ_points(
    schedule: PlaybackSchedule,
) -> List[Tuple[float, int, Dict[str, str]]]:
    """
    스케줄 step panel occ → occ_full 마일스톤.

    - renewal JSON: ``playback_renewal_ports`` SSOT (renewal sim 1회, proc_end 없음).
    - 그 외 anim JSON: 공정 종료 1회.
    """
    from .playback_renewal_ports import (
        renewal_full_panel_occ_for_step,
        renewal_playback_port_sync_for_step,
        renewal_port_milestone_for_step,
        step_json_has_renewal_marker,
    )

    panel_ports = ["INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3"]
    running: Dict[str, str] = {p: "" for p in panel_ports}

    out: List[Tuple[float, int, Dict[str, str]]] = []
    for step in schedule.steps or ():
        if not isinstance(step, PlaybackScheduledStep):
            continue

        if str(step.kind or "").strip().lower() == "occ_refresh":
            continue

        is_renewal_json = bool(step.has_renewal) or step_json_has_renewal_marker(step)
        if is_renewal_json:
            sync_t = renewal_playback_port_sync_for_step(step)
            if sync_t is None:
                sync_t = step.t_playback_port_sync
            occ_r: Optional[Dict[str, str]] = None
            if sync_t is not None:
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
            if sync_t is not None and occ_r:
                out.append((float(sync_t), 50000 + int(step.index), dict(occ_r)))
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
        sync_t = step.t_playback_port_sync
        if sync_t is None:
            if step.t_playback_json_end is not None:
                sync_t = float(step.t_playback_json_end)
            else:
                kind = str(step.kind or "").strip().lower()
                if kind in ("occ_refresh", "foup", "foup_process", "event_only"):
                    sync_t = float(step.t_event)
                else:
                    sync_t = float(step.t_proc_end)
        occ_d = _occ_tuple_to_dict(panel_pairs)
        if not occ_d:
            continue
        out.append((float(sync_t), 10000 + int(step.index), dict(occ_d)))
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

    1) 엔진 ``ports_occupancy`` (event·progress)
    2) 스케줄 step ``ports_occ_panel`` / renewal·proc_end 분리
    → ``occ_full`` 마일스톤만 저장.
    """
    from .control_sim_bar_graph import _collect_foup_milestones_from_items

    keys = [str(p).strip().upper() for p in (port_keys or ()) if str(p).strip()]
    sorted_items = _sorted_timeline_items(items)
    raw: List[Tuple[float, int, str, Any]] = []
    raw.extend(_collect_foup_milestones_from_items(sorted_items, seq_start=0))

    init = dict(initial_occ or {})
    engine_changes = _collect_engine_port_occ_changes(sorted_items, keys, schedule=schedule)
    schedule_points = _collect_schedule_port_occ_points(schedule)
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
