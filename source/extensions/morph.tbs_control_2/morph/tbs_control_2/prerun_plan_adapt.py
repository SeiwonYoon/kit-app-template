"""
``PrerunPlan`` → ``SimPreRunResult`` 어댑터.

기존 ``build_playback_schedule`` / ``SimTimelinePlayer`` 가 그대로 소비할 수 있게
event + RUNNING progress 쌍을 합성한다.

포트 점유는 **키프레임 ``PORT_OCC_REFRESH`` 만** 실어 보낸다.
(공정 시작 event/RUNNING 에 빈 occ 를 넣으면 병렬 공정이 패널·막대를 덮어쓴다.)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .control_sim_prerun_playback import SimPreRunResult, SimTimelineItem
from .prerun_plan_ssot import (
    KIND_BP_TO_EP,
    KIND_FOUP,
    KIND_INOUT_TO_BP,
    KIND_OHT_TO_EP,
    KIND_OHT_TO_INOUT,
    KIND_REMOVE,
    PlanProcess,
    PrerunPlan,
    PrerunPlanConfig,
    build_prerun_plan,
)


def _mid(a: float, b: float) -> float:
    return max(0.01, (float(a) + float(b)) * 0.5)


def _pool_or_mid(engine: Any, key: str, a: float, b: float) -> float:
    """엔진 pre_pool 첫 샘플이 있으면 사용, 없으면 중앙값."""
    try:
        pool = getattr(engine, "_pre_pool", None) or {}
        arr = pool.get(key) if isinstance(pool, dict) else None
        if isinstance(arr, list) and arr:
            return max(0.01, float(arr[0]))
    except Exception:
        pass
    return _mid(a, b)


def _kind_to_seq(kind: str) -> str:
    k = str(kind or "").strip().upper()
    if k in (KIND_OHT_TO_EP, KIND_OHT_TO_INOUT):
        return "ARRIVED"
    if k == KIND_INOUT_TO_BP:
        return "MOVE_TRANSFERING"
    if k == KIND_BP_TO_EP:
        return "MOVE_REQ"
    if k == KIND_REMOVE:
        return "REMOVED"
    if k == KIND_FOUP:
        return "FOUP_PROCESS"
    return k


def prerun_config_from_engine(engine: Any) -> PrerunPlanConfig:
    """엔진 timing/init 스냅샷으로 플래너 설정 생성."""
    timing = getattr(engine, "_timing", None)
    init = getattr(engine, "_init_cfg", None)
    ep_ports = list(getattr(engine, "_ep_ports", None) or ("EP1", "EP2"))
    ep_count = max(2, min(3, len(ep_ports)))
    try:
        lot_count = int(getattr(engine, "_max_oht_lots", 0) or 0)
    except Exception:
        lot_count = 0
    if lot_count <= 0:
        try:
            lot_count = int(getattr(init, "max_oht_lots", 0) or 0)
        except Exception:
            lot_count = 3
    ebs_on = True
    try:
        ebs_on = bool(getattr(engine, "_ebs_enabled", getattr(init, "ebs_enabled", True)))
    except Exception:
        ebs_on = True
    if timing is None:
        return PrerunPlanConfig(lot_count=lot_count, ep_count=ep_count, ebs_on=ebs_on)

    oht_ep = _pool_or_mid(engine, "oht_to_bp1", timing.oht_to_bp1_min, timing.oht_to_bp1_max)
    oht_in = _pool_or_mid(engine, "oht_to_inout", timing.oht_to_inout_min, timing.oht_to_inout_max)
    # UI 에 OHT→INOUT 가 비어 기본(5~10)으로 남은 경우 EP 이송 시간과 맞춤
    try:
        lo_i, hi_i = float(timing.oht_to_inout_min), float(timing.oht_to_inout_max)
        lo_e, hi_e = float(timing.oht_to_bp1_min), float(timing.oht_to_bp1_max)
        if hi_i <= 12.0 and lo_e >= 20.0:
            oht_in = oht_ep
    except Exception:
        pass

    return PrerunPlanConfig(
        lot_count=int(lot_count),
        ep_count=int(ep_count),
        ebs_on=bool(ebs_on),
        proc_oht_to_ep=float(oht_ep),
        proc_oht_to_inout=float(oht_in),
        proc_inout_to_bp=_pool_or_mid(engine, "bp1_to_bp", timing.bp1_to_bp_min, timing.bp1_to_bp_max),
        proc_bp_to_ep=_pool_or_mid(engine, "bp_to_ep", timing.bp_to_ep_min, timing.bp_to_ep_max),
        proc_remove=_pool_or_mid(engine, "ep_to_oht", timing.ep_to_oht_min, timing.ep_to_oht_max),
        proc_foup=_pool_or_mid(engine, "foup_process", timing.foup_process_min, timing.foup_process_max),
        anim_from_json=True,
        anim_sec_fallback=10.0,
        foup_global_serial=True,
    )


def _process_to_items(p: PlanProcess) -> List[SimTimelineItem]:
    """공정 이벤트/progress — ports_occupancy 없음 (키프레임 REFRESH 가 SSOT)."""
    items: List[SimTimelineItem] = []
    t0 = float(p.t_start)
    seq = _kind_to_seq(p.kind)

    if p.kind == KIND_FOUP:
        items.append(
            SimTimelineItem(
                t=t0,
                kind="event",
                payload={
                    "seq": "FOUP_PROCESS_START",
                    "sim_time": f"{t0:.2f}",
                    "port_id": p.port,
                    "lot_id": p.lot_id,
                    "proc_sec": f"{float(p.proc_sec):.2f}",
                    "foup_proc_active_ep": p.port,
                },
            )
        )
        items.append(
            SimTimelineItem(
                t=t0,
                kind="progress",
                payload={
                    "status": "RUNNING",
                    "elapsed": "0.0",
                    "event_seq": "FOUP_PROCESS",
                    "sequence_name": "FOUP_PROCESS",
                    "event_start_sim_time": f"{t0:.2f}",
                    "sim_time": f"{t0:.2f}",
                    "proc_sec": f"{float(p.proc_sec):.2f}",
                    "anim_sec": "0.0",
                    "port_id": p.port,
                    "lot_id": p.lot_id,
                    "label": f"FOUP {p.port}",
                    "foup_proc_active_ep": p.port,
                },
            )
        )
        t1 = float(p.t_wall_end if p.t_wall_end is not None else t0 + p.proc_sec)
        items.append(
            SimTimelineItem(
                t=t1,
                kind="event",
                payload={
                    "seq": "FOUP_PROCESS_END",
                    "sim_time": f"{t1:.2f}",
                    "port_id": p.port,
                    "lot_id": p.lot_id,
                    "foup_proc_active_ep": "",
                },
            )
        )
        items.append(
            SimTimelineItem(
                t=t1,
                kind="progress",
                payload={
                    "status": "DONE",
                    "elapsed": f"{float(p.proc_sec):.2f}",
                    "event_seq": "FOUP_PROCESS",
                    "event_start_sim_time": f"{t0:.2f}",
                    "sim_time": f"{t1:.2f}",
                    "proc_sec": f"{float(p.proc_sec):.2f}",
                    "anim_sec": "0.0",
                    "port_id": p.port,
                    "lot_id": p.lot_id,
                    "foup_proc_active_ep": "",
                },
            )
        )
        return items

    wall = float(p.t_wall_end if p.t_wall_end is not None else t0 + p.proc_sec)
    anim_q = 0.0
    if p.t_anim_start is not None:
        anim_q = max(0.0, float(p.t_anim_start) - float(p.t_anim_ready))
    linked = str(p.linked_json or "").strip()
    if not linked and p.needs_anim:
        try:
            from .sim_sequence_duration import linked_json_for_process

            linked = linked_json_for_process(
                kind=p.kind, from_port=p.from_port, to_port=p.to_port, port=p.port
            )
        except Exception:
            linked = ""

    anim_s = f"{float(p.anim_sec):.2f}"
    proc_s = f"{float(p.proc_sec):.2f}"
    # renewal / port_sync — 프리런에서 확정 (재생 중 JSON 재파싱 금지)
    has_renewal = False
    renewal_off: Optional[float] = None
    if (
        p.t_anim_start is not None
        and p.t_port_sync is not None
        and p.t_anim_end is not None
        and float(p.t_port_sync) + 1e-6 < float(p.t_anim_end)
    ):
        has_renewal = True
        renewal_off = max(0.0, float(p.t_port_sync) - float(p.t_anim_start))
    sync_fields: Dict[str, Any] = {}
    if p.t_port_sync is not None:
        sync_fields["port_sync_sim_time"] = f"{float(p.t_port_sync):.2f}"
    if has_renewal:
        sync_fields["has_renewal"] = True
        if renewal_off is not None:
            sync_fields["renewal_offset_sec"] = f"{float(renewal_off):.2f}"
    play_fields: Dict[str, Any] = {}
    if p.t_anim_start is not None:
        play_fields["anim_play_start_sim_time"] = f"{float(p.t_anim_start):.2f}"
    if p.t_anim_end is not None:
        play_fields["anim_play_end_sim_time"] = f"{float(p.t_anim_end):.2f}"
    items.append(
        SimTimelineItem(
            t=t0,
            kind="event",
            payload={
                "seq": seq,
                "sim_time": f"{t0:.2f}",
                "event_start_sim_time": f"{t0:.2f}",
                "from_port_id": p.from_port,
                "to_port_id": p.to_port,
                "port_id": p.port,
                "lot_id": p.lot_id,
                "proc_sec": proc_s,
                "anim_sec": anim_s,
                "linked_anim_json": linked,
                # wall 연장 구간 — emit 시 proc_gate 가 t0+proc 로 잘리지 않게
                "wall_sec": f"{max(0.0, wall - t0):.2f}",
                "event_end_sim_time": f"{wall:.2f}",
                "anim_queue_delay_sec": f"{anim_q:.2f}",
                **play_fields,
                **sync_fields,
            },
        )
    )
    prog: Dict[str, Any] = {
        "status": "RUNNING",
        "elapsed": "0.0",
        "event_seq": seq,
        "sequence_name": seq,
        "event_start_sim_time": f"{t0:.2f}",
        "sim_time": f"{t0:.2f}",
        "proc_sec": proc_s,
        "anim_sec": anim_s,
        "linked_anim_json": linked,
        "from_port_id": p.from_port,
        "to_port_id": p.to_port,
        "port_id": p.port,
        "event_port_id": p.port,
        "lot_id": p.lot_id,
        "label": f"{p.kind} {p.lot_id}",
        "wall_sec": f"{max(0.0, wall - t0):.2f}",
        "anim_queue_delay_sec": f"{anim_q:.2f}",
        "event_end_sim_time": f"{wall:.2f}",
        **play_fields,
        **sync_fields,
    }
    items.append(SimTimelineItem(t=t0, kind="progress", payload=prog))

    t_done = float(wall)
    if p.t_anim_end is not None:
        t_done = max(float(wall), float(p.t_anim_end))
    items.append(
        SimTimelineItem(
            t=t_done,
            kind="progress",
            payload={
                "status": "DONE",
                "elapsed": f"{max(0.0, wall - t0):.2f}",
                "event_seq": seq,
                "event_start_sim_time": f"{t0:.2f}",
                "sim_time": f"{t_done:.2f}",
                "proc_sec": proc_s,
                "anim_sec": anim_s,
                "linked_anim_json": linked,
                "from_port_id": p.from_port,
                "to_port_id": p.to_port,
                "port_id": p.port,
                "lot_id": p.lot_id,
                "wall_sec": f"{max(0.0, wall - t0):.2f}",
                "anim_queue_delay_sec": f"{anim_q:.2f}",
                "event_end_sim_time": f"{wall:.2f}",
            },
        )
    )
    return items


def prerun_plan_to_timeline(*, screen: int, plan: PrerunPlan) -> SimPreRunResult:
    """오프라인 플랜 → 재생용 SimPreRunResult."""
    items: List[SimTimelineItem] = []

    # 포트 SSOT: 플래너 키프레임만
    last_sig = ""
    for kf in plan.port_keyframes:
        occ = {str(k).strip().upper(): str(v or "") for k, v in dict(kf.ports or {}).items()}
        sig = f"{float(kf.t):.4f}|{sorted(occ.items())}"
        if sig == last_sig:
            continue
        last_sig = sig
        items.append(
            SimTimelineItem(
                t=float(kf.t),
                kind="event",
                payload={
                    "seq": "PORT_OCC_REFRESH",
                    "sim_time": f"{float(kf.t):.2f}",
                    "ports_occupancy": dict(occ),
                    "note": f"prerun_plan_ssot:{kf.note}",
                },
            )
        )

    for p in plan.processes:
        items.extend(_process_to_items(p))

    def _prio(it: SimTimelineItem) -> Tuple[float, int, str]:
        k = str(it.kind or "").lower()
        # 같은 t 에서 REFRESH 를 공정 event 보다 먼저 적용
        seq = ""
        if isinstance(it.payload, dict):
            seq = str(it.payload.get("seq") or it.payload.get("event_seq") or "")
        if k == "event" and seq == "PORT_OCC_REFRESH":
            pr = 0
        elif k == "event":
            pr = 1
        elif k == "progress":
            pr = 2
        else:
            pr = 3
        return (float(it.t), pr, seq)

    items.sort(key=_prio)
    final_t = float(plan.final_sim_time)
    return SimPreRunResult(
        screen=int(screen),
        final_sim_time=final_t,
        total_est_sec=final_t,
        items=tuple(items),
    )


def prerun_ssot_to_timeline(*, screen: int, engine: Any) -> SimPreRunResult:
    """엔진 설정 → 오프라인 플랜 → SimPreRunResult (프리런 스레드 진입점)."""
    try:
        fill = getattr(engine, "_presample_fill", None)
        if callable(fill):
            fill()
    except Exception:
        pass

    cfg = prerun_config_from_engine(engine)
    plan = build_prerun_plan(cfg)
    try:
        engine._prerun_plan_ssot = plan  # type: ignore[attr-defined]
    except Exception:
        pass
    return prerun_plan_to_timeline(screen=screen, plan=plan)


def store_prerun_plan_on_ext(ext: Any, screen: int, plan: Any) -> None:
    """재생 중 동시공정/애니큐 표시용으로 플랜을 ext 에 보관."""
    if ext is None or plan is None:
        return
    try:
        by = getattr(ext, "_sim_prerun_plan_by_screen", None)
        if not isinstance(by, dict):
            by = {}
            ext._sim_prerun_plan_by_screen = by
        by[int(screen)] = plan
    except Exception:
        pass


def get_prerun_plan_for_screen(ext: Any, screen: int) -> Optional[Any]:
    try:
        by = getattr(ext, "_sim_prerun_plan_by_screen", None)
        if isinstance(by, dict):
            p = by.get(int(screen))
            if p is not None:
                return p
    except Exception:
        pass
    try:
        engines = getattr(ext, "_sim_engines_by_screen", None)
        if isinstance(engines, dict):
            eng = engines.get(int(screen))
            if eng is not None:
                return getattr(eng, "_prerun_plan_ssot", None)
    except Exception:
        pass
    return None


def enrich_ssot_playback_progress(
    ext: Any,
    screen: int,
    payload: Dict[str, Any],
    tnow: float,
) -> None:
    """진행현황 — 프리런 플랜만으로 본문·동시공정·대기큐 채움 (live 재추정 금지)."""
    if not isinstance(payload, dict):
        return
    try:
        from .sim_control_defaults import SIM_PRERUN_PLAN_SSOT

        if not bool(SIM_PRERUN_PLAN_SSOT):
            return
    except Exception:
        return

    plan = get_prerun_plan_for_screen(ext, int(screen))
    if plan is None:
        return

    t = float(tnow)
    # 표시용: 실제 재생 파일명 (스케줄 재계산에는 쓰지 않음)
    playing_bn = ""
    try:
        from .progress_step_state import get_anim_runtime, sync_anim_runtime_from_ext, _basename_json

        sync_anim_runtime_from_ext(ext, int(screen))
        ar = get_anim_runtime(ext, int(screen))
        if str(getattr(ar, "phase", "") or "") == "playing":
            playing_bn = _basename_json(str(getattr(ar, "current_file", "") or ""))
    except Exception:
        playing_bn = ""

    # 플랜 시각 창만으로 재생 중 공정 결정 (live 파일명으로 스케줄 덮지 않음)
    playing_proc = None
    waiting: List[str] = []
    try:
        anims = sorted(
            list(getattr(plan, "anims", None) or []),
            key=lambda a: float(getattr(a, "t_start", 0.0) or 0.0),
        )
        procs_by_uid = {
            str(getattr(p, "uid", "") or ""): p
            for p in list(getattr(plan, "processes", None) or [])
        }
        for a in anims:
            a0 = float(getattr(a, "t_start", 0.0) or 0.0)
            a1 = float(getattr(a, "t_end", 0.0) or 0.0)
            uid = str(getattr(a, "process_uid", "") or "")
            p = procs_by_uid.get(uid)
            linked = str(getattr(p, "linked_json", "") or "").strip() if p is not None else ""
            label = linked or f"{getattr(a, 'kind', '')}_{getattr(a, 'port', '')}"
            if playing_proc is None and a0 - 1e-9 <= t < a1 - 1e-12:
                playing_proc = p
            if t + 1e-9 < a0:
                waiting.append(f"{label} @{a0:.1f}s")
        waiting = waiting[:8]
        if playing_proc is None:
            for p in procs_by_uid.values():
                t0 = float(getattr(p, "t_start", 0.0) or 0.0)
                we = getattr(p, "t_wall_end", None)
                if we is None:
                    we = t0 + float(getattr(p, "proc_sec", 0.0) or 0.0)
                if t0 - 1e-6 <= t < float(we) - 1e-12:
                    playing_proc = p
                    break
    except Exception:
        waiting = []

    # 본문 = 플랜 공정 (옛 ARRIVED DONE 고착 방지)
    if playing_proc is not None:
        try:
            p = playing_proc
            t0 = float(getattr(p, "t_start", 0.0) or 0.0)
            we = getattr(p, "t_wall_end", None)
            if we is None:
                we = t0 + float(getattr(p, "proc_sec", 0.0) or 0.0)
            we = float(we)
            proc = max(1e-6, float(getattr(p, "proc_sec", 0.0) or 0.0))
            wall_span = max(proc, max(0.0, we - t0))
            el = max(0.0, min(wall_span, t - t0))
            pct = int(min(100.0, 100.0 * el / wall_span))
            kind = str(getattr(p, "kind", "") or "")
            seq = _kind_to_seq(kind)
            lot = str(getattr(p, "lot_id", "") or "")
            fr = str(getattr(p, "from_port", "") or "")
            to = str(getattr(p, "to_port", "") or getattr(p, "port", "") or "")
            linked = str(getattr(p, "linked_json", "") or "").strip()
            anim_s = float(getattr(p, "anim_sec", 0.0) or 0.0)
            payload["status"] = "RUNNING" if t + 1e-9 < we else "DONE"
            payload["event_seq"] = seq
            payload["sequence_name"] = seq
            payload["label"] = f"{kind} {lot}".strip()
            payload["detail"] = f"{fr}->{to} (port={getattr(p, 'port', '')} | lot={lot})"
            payload["lot_id"] = lot
            payload["from_port_id"] = fr
            payload["to_port_id"] = to
            payload["port_id"] = str(getattr(p, "port", "") or "")
            payload["linked_anim_json"] = linked or playing_bn
            payload["event_start_sim_time"] = f"{t0:.2f}"
            payload["event_end_sim_time"] = f"{we:.2f}"
            payload["wall_sec"] = f"{wall_span:.2f}"
            payload["proc_sec"] = f"{proc:.2f}"
            if anim_s > 1e-9:
                payload["anim_sec"] = f"{anim_s:.2f}"
            if getattr(p, "t_anim_start", None) is not None:
                payload["anim_play_start_sim_time"] = f"{float(p.t_anim_start):.2f}"
            if getattr(p, "t_anim_end", None) is not None:
                payload["anim_play_end_sim_time"] = f"{float(p.t_anim_end):.2f}"
            payload["elapsed"] = f"{el:.1f}"
            payload["total"] = f"{wall_span:.1f}"
            payload["percent"] = str(pct if t + 1e-9 < we else 100)
            # ProgressStepState 도 같이 갱신
            try:
                from .progress_step_state import get_progress_step

                st = get_progress_step(ext, int(screen))
                new_hint = (seq, linked or playing_bn, f"{t0:.2f}")
                old_hint = (st.event_seq, st.linked_anim_json, st.event_start_sim_time)
                if new_hint != old_hint:
                    st.step_id += 1
                    st.display_rev += 1
                st.status = str(payload["status"])
                st.event_seq = seq
                st.label = str(payload["label"])
                st.detail = str(payload["detail"])
                st.linked_anim_json = linked or playing_bn
                st.event_start_sim_time = f"{t0:.2f}"
                st.proc_sec = f"{proc:.2f}"
                st.anim_sec = str(payload.get("anim_sec") or st.anim_sec)
                st.elapsed = str(payload["elapsed"])
                st.total = str(payload["total"])
                st.percent = str(payload["percent"])
                st.sim_time = f"{t:.2f}"
                snap = dict(st.payload_snapshot) if isinstance(st.payload_snapshot, dict) else {}
                snap.update({k: payload[k] for k in (
                    "event_end_sim_time", "wall_sec", "lot_id", "from_port_id",
                    "to_port_id", "port_id", "linked_anim_json", "event_seq",
                    "proc_sec", "anim_sec", "status", "label", "detail",
                    "anim_play_start_sim_time", "anim_play_end_sim_time",
                ) if k in payload})
                st.payload_snapshot = snap
            except Exception:
                pass
        except Exception:
            pass

    conc_lines: List[str] = []
    try:
        for p in list(getattr(plan, "processes", None) or []):
            t0 = float(getattr(p, "t_start", 0.0) or 0.0)
            we = getattr(p, "t_wall_end", None)
            if we is None:
                we = t0 + float(getattr(p, "proc_sec", 0.0) or 0.0)
            we = float(we)
            if t + 1e-9 < t0 or t >= we - 1e-12:
                continue
            span = max(1e-6, we - t0)
            el = max(0.0, min(span, t - t0))
            pct = int(min(100.0, 100.0 * el / span))
            kind = str(getattr(p, "kind", "") or "")
            lot = str(getattr(p, "lot_id", "") or "")
            fr = str(getattr(p, "from_port", "") or "")
            to = str(getattr(p, "to_port", "") or getattr(p, "port", "") or "")
            route = f"{fr}->{to}" if fr or to else str(getattr(p, "port", "") or "")
            conc_lines.append(f"· {kind} {lot} {route} {pct}% ({el:.1f}/{span:.1f}s)")
    except Exception:
        conc_lines = []

    if conc_lines:
        payload["concurrent_summary"] = "\n".join(conc_lines)
        payload["concurrent_count"] = str(len(conc_lines))
    if playing_bn or (playing_proc is not None and getattr(playing_proc, "linked_json", "")):
        payload["anim_playing_json"] = playing_bn or str(getattr(playing_proc, "linked_json", "") or "")
    if waiting:
        payload["anim_queue_waiting"] = " | ".join(waiting)
        qdel = str(payload.get("anim_queue_delay_sec") or "").strip()
        if not qdel or qdel in ("0", "0.0", "0.00"):
            payload["anim_queue_delay_sec"] = ""
    # anim_sec 는 플랜만 — JSON 파일 재추정 금지


__all__ = [
    "prerun_config_from_engine",
    "prerun_plan_to_timeline",
    "prerun_ssot_to_timeline",
    "store_prerun_plan_on_ext",
    "get_prerun_plan_for_screen",
    "enrich_ssot_playback_progress",
]
