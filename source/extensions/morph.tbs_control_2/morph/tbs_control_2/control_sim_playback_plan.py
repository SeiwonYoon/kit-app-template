"""
프리런 ``PlaybackPlanSnapshot`` → 재생 UI (포트·막대) display-only replay.

재생 SSOT (단일 lookup):

  ``resolve_playback_ui_at_sim(ext, screen, t)`` → ``PlaybackUIState``
  ``refresh_playback_display_at_sim`` — 포트·막대 공통 갱신 진입점

  ``sim_now`` (또는 Seek 시 explicit ``t_sim``) 하나로 plan lookup.
  LAM renewal wall 은 3D 애니만 — 포트·막대는 heartbeat/tick 이 plan 을 따른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .playback_plan import PlaybackPlanSnapshot
from .playback_schedule import PlaybackSchedule

_PANEL_OCC_KEYS: Tuple[str, ...] = ("INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3")


@dataclass(frozen=True)
class PlaybackUIAxes:
    """재생 UI 시각 축 — ``t_display`` 와 ``t_plan`` 은 재생 중 항상 동일 (Seek explicit 제외)."""

    t_display: float
    t_plan: float


@dataclass(frozen=True)
class PlaybackUIState:
    """재생 UI SSOT — ``resolve_playback_ui_at_sim`` 결과."""

    screen: int
    axes: PlaybackUIAxes
    ports: Dict[str, str]
    bar_rows: Dict[str, List[Dict[str, Any]]]
    bar_total_est: float
    row_order: Tuple[str, ...]
    preview_full: bool


def _ensure_panel_occ_keys(occ: Optional[Dict[str, str]]) -> Dict[str, str]:
    out = {k: "" for k in _PANEL_OCC_KEYS}
    if isinstance(occ, dict):
        for k, v in occ.items():
            ku = str(k).strip().upper()
            if ku in out:
                out[ku] = str(v or "")
    return out


def _renewal_plan_floor(ext: Any, screen: int) -> float:
    """Deprecated — per-event hold 사용."""
    return 0.0


def _set_renewal_plan_floor(ext: Any, screen: int, t_floor: float) -> None:
    """Deprecated — no-op."""
    return


def _get_renewal_occ_hold(ext: Any, screen: int) -> Optional[Dict[str, Any]]:
    try:
        by = getattr(ext, "_sim_playback_renewal_occ_hold_by_screen", None)
        if isinstance(by, dict):
            hold = by.get(str(int(screen)))
            if isinstance(hold, dict) and isinstance(hold.get("occ"), dict):
                return hold
    except Exception:
        pass
    return None


def _set_renewal_occ_hold(
    ext: Any,
    screen: int,
    occ: Dict[str, str],
    sync_t: float,
    *,
    dedupe: str = "",
) -> None:
    try:
        by = getattr(ext, "_sim_playback_renewal_occ_hold_by_screen", None)
        if not isinstance(by, dict):
            by = {}
            ext._sim_playback_renewal_occ_hold_by_screen = by
        by[str(int(screen))] = {
            "occ": dict(occ),
            "sync_t": float(sync_t),
            "dedupe": str(dedupe or ""),
        }
    except Exception:
        pass


def _clear_renewal_occ_hold(ext: Any, screen: int) -> None:
    try:
        by = getattr(ext, "_sim_playback_renewal_occ_hold_by_screen", None)
        if isinstance(by, dict):
            by.pop(str(int(screen)), None)
    except Exception:
        pass


def _renewal_occ_for_playback_sync(
    ext: Any,
    screen: int,
    sim_now: float,
    plan_occ: Dict[str, str],
) -> Tuple[Dict[str, str], float]:
    """
    wall 이 sim 보다 앞서 renewal 을 적용한 뒤 — sim 이 sync_t 에 도달할 때까지 hold occ 유지.
    """
    hold = _get_renewal_occ_hold(ext, int(screen))
    if not hold:
        return dict(plan_occ), float(sim_now)
    sync_t = float(hold.get("sync_t", 0.0) or 0.0)
    if float(sim_now) + 1e-6 >= sync_t:
        _clear_renewal_occ_hold(ext, int(screen))
        return dict(plan_occ), float(sim_now)
    occ_h = _ensure_panel_occ_keys(dict(hold.get("occ") or {}))
    return dict(occ_h), max(float(sim_now), sync_t)


def get_stored_playback_schedule_for_screen(ext: Any, screen: int) -> Optional[PlaybackSchedule]:
    try:
        by = getattr(ext, "_sim_playback_schedule_by_screen", None)
        if not isinstance(by, dict):
            return None
        sched = by.get(str(int(screen))) or by.get(int(screen))
        return sched if isinstance(sched, PlaybackSchedule) else None
    except Exception:
        return None


def get_playback_schedule_for_screen(ext: Any, screen: int) -> Optional[PlaybackSchedule]:
    if not bool(getattr(ext, "_sim_playback_started", False)):
        return None
    return get_stored_playback_schedule_for_screen(ext, int(screen))


def get_plan_snapshot(ext: Any, screen: int) -> Optional[PlaybackPlanSnapshot]:
    try:
        by = getattr(ext, "_sim_playback_plan_by_screen", None)
        if not isinstance(by, dict):
            return None
        snap = by.get(str(int(screen))) or by.get(int(screen))
        return snap if isinstance(snap, PlaybackPlanSnapshot) else None
    except Exception:
        return None


def _sim_now_for_screen(ext: Any, screen: int, t_sim: Optional[float] = None) -> float:
    scr = int(screen)
    try:
        from .control_sim_screen_playback import get_sim_playback_player

        pl = get_sim_playback_player(ext, scr)
        if pl is not None:
            return float(pl.sim_now(scr))
    except Exception:
        pass
    if t_sim is not None:
        try:
            return float(t_sim)
        except Exception:
            pass
    return 0.0


def resolve_playback_ui_axes(
    ext: Any,
    screen: int,
    t_sim: Optional[float] = None,
    *,
    explicit: bool = False,
) -> PlaybackUIAxes:
    """
    재생 UI 시각 축.

    - 재생: ``sim_now`` 단일 축 (포트·막대 공통).
    - ``explicit=True`` (Seek): ``t_sim`` 을 display·plan 공통 축으로 사용.
    """
    if explicit and t_sim is not None:
        try:
            t = float(t_sim)
        except Exception:
            t = 0.0
        return PlaybackUIAxes(t_display=float(t), t_plan=float(t))

    t = _sim_now_for_screen(ext, int(screen), t_sim)
    return PlaybackUIAxes(t_display=float(t), t_plan=float(t))


def plan_lookup_sim_t(
    ext: Any,
    screen: int,
    t_sim: float,
    *,
    honor_explicit: bool = False,
) -> float:
    """레거시 — ``resolve_playback_ui_axes(...).t_plan`` 과 동일."""
    if honor_explicit and float(t_sim) > 1e-9:
        return float(t_sim)
    return resolve_playback_ui_axes(ext, int(screen), float(t_sim)).t_plan


def playback_plan_active(ext: Any, screen: int) -> bool:
    if not bool(getattr(ext, "_sim_playback_started", False)):
        return False
    snap = get_plan_snapshot(ext, int(screen))
    if snap is None:
        snap = ensure_plan_snapshot(ext, int(screen))
    return snap is not None


def get_plan_ports_at_sim(
    ext: Any,
    screen: int,
    t_sim: float,
    *,
    honor_explicit: bool = False,
) -> Optional[Dict[str, str]]:
    snap = ensure_plan_snapshot(ext, int(screen))
    if snap is None:
        return None
    if honor_explicit and float(t_sim) > 1e-9:
        t_lookup = float(t_sim)
    else:
        t_lookup = resolve_playback_ui_axes(ext, int(screen), float(t_sim)).t_plan
    return dict(snap.ports_at(float(t_lookup)))


def _occ_dicts_equal(a: Dict[str, str], b: Dict[str, str]) -> bool:
    if set(a.keys()) != set(b.keys()):
        return False
    for k in a:
        if str(a.get(k, "") or "") != str(b.get(k, "") or ""):
            return False
    return True


def _apply_plan_ports_to_panel(
    ext: Any,
    screen: int,
    occ: Dict[str, str],
    *,
    t_display: float,
) -> bool:
    try:
        from .control_window import _apply_sim_event_state_only

        payload: Dict[str, Any] = {
            "ports_occupancy": dict(occ),
            "sim_time": f"{float(t_display):.2f}",
            "_from_playback_plan": True,
        }
        try:
            ext._sim_playback_plan_panel_apply = True
            _apply_sim_event_state_only(ext, payload, screen=int(screen))
        finally:
            try:
                ext._sim_playback_plan_panel_apply = False
            except Exception:
                pass
    except Exception:
        return False
    return True


def sync_playback_ui_at_sim(ext: Any, screen: int, t_sim: float, *, force: bool = False) -> bool:
    """``sim_now`` 에 맞춰 plan occ → 포트 패널."""
    if not bool(getattr(ext, "_sim_playback_started", False)):
        return False
    snap = get_plan_snapshot(ext, int(screen))
    if snap is None:
        snap = rebuild_plan_snapshot_for_screen(ext, int(screen))
    if snap is None:
        return False

    sk = str(int(screen))
    axes = resolve_playback_ui_axes(ext, int(screen), float(t_sim))
    t_lookup = float(axes.t_plan)
    occ = _ensure_panel_occ_keys(dict(snap.ports_at(t_lookup)))

    # renewal wall 이 sim 보다 앞서 적용된 경우 — sim 이 sync_t 에 도달할 때까지 held occ 유지
    # (JSON wall 동안 sim_now 가 멈춰 있어 plan ports_at(sim_now) 가 pre-renewal 로 되돌리는 깜빡임 방지).
    occ, _ = _renewal_occ_for_playback_sync(ext, int(screen), float(t_lookup), occ)
    occ = _ensure_panel_occ_keys(dict(occ))

    last_by = getattr(ext, "_sim_last_ports_occupancy_by_screen", None)
    last = last_by.get(sk) if isinstance(last_by, dict) else None
    if (not force) and isinstance(last, dict) and _occ_dicts_equal(dict(last), occ):
        return False

    return _apply_plan_ports_to_panel(
        ext,
        int(screen),
        occ,
        t_display=float(axes.t_display),
    )


def seek_playback_ui_at_sim(ext: Any, screen: int, t_sim: float) -> bool:
    """Seek 직후 — 목표 sim 시각으로 plan lookup 후 UI 반영."""
    refresh_playback_display_at_sim(
        ext,
        int(screen),
        float(t_sim),
        force=True,
        explicit=True,
    )
    return True


def clear_playback_plan_runtime_state(ext: Any) -> None:
    """재생 종료·취소 시 런타임 정리 (프리런 snapshot 은 유지)."""
    try:
        by = getattr(ext, "_sim_playback_plan_replay_floor_by_screen", None)
        if isinstance(by, dict):
            by.clear()
    except Exception:
        pass
    try:
        hold_by = getattr(ext, "_sim_playback_renewal_occ_hold_by_screen", None)
        if isinstance(hold_by, dict):
            hold_by.clear()
    except Exception:
        pass


def rebuild_plan_snapshot_for_screen(ext: Any, screen: int) -> Optional[PlaybackPlanSnapshot]:
    sched = get_stored_playback_schedule_for_screen(ext, int(screen))
    if sched is None:
        return None
    try:
        results = getattr(ext, "_sim_prerun_results_by_screen", None)
        res = results.get(int(screen)) if isinstance(results, dict) else None
        if res is None:
            return None
        init_by = getattr(ext, "_sim_playback_plan_initial_occ_by_screen", None)
        sk = str(int(screen))
        init = init_by.get(sk) if isinstance(init_by, dict) else {}
        from .control_sim_bar_graph import bar_graph_row_order, _initial_bar_occ_at_t0

        snap_cfg = None
        try:
            from .control_window import _sim_snapshot_for_screen, _ep_count_idx_for_port_panel

            snap_cfg = _sim_snapshot_for_screen(ext, int(screen))
            ep_idx = int(
                snap_cfg.get("ep_count_idx", _ep_count_idx_for_port_panel(ext, int(screen))) or 0
            )
            ebs_on = bool(snap_cfg.get("ebs_enabled", True)) if snap_cfg else True
        except Exception:
            ep_idx = 0
            ebs_on = True
        row_o = bar_graph_row_order(int(ep_idx), ebs_enabled=bool(ebs_on))
        ports = [r for r in row_o if r != "ALL_EP"]
        if not isinstance(init, dict) or not init:
            init = dict(_initial_bar_occ_at_t0(res.items, ports))
        from .playback_plan import build_playback_plan_snapshot

        plan = build_playback_plan_snapshot(
            int(screen),
            sched,
            tuple(getattr(res, "items", ()) or ()),
            initial_occ=dict(init),
            port_keys=ports,
            final_sim_time=float(getattr(res, "final_sim_time", 0.0) or 0.0),
        )
        by = getattr(ext, "_sim_playback_plan_by_screen", None)
        if not isinstance(by, dict):
            by = {}
            ext._sim_playback_plan_by_screen = by
        by[sk] = plan
        return plan
    except Exception:
        return None


def ensure_plan_snapshot(ext: Any, screen: int) -> Optional[PlaybackPlanSnapshot]:
    snap = get_plan_snapshot(ext, int(screen))
    if snap is not None:
        return snap
    return rebuild_plan_snapshot_for_screen(ext, int(screen))


def ensure_playback_plans_for_results(ext: Any, results: Dict[int, Any]) -> bool:
    ok_all = True
    for scr_k in (results or {}):
        try:
            scr = int(scr_k)
        except Exception:
            continue
        snap = ensure_plan_snapshot(ext, scr)
        if snap is None or not snap.has_port_milestones():
            ok_all = False
            print(
                f"[SIM] 재생 plan 없음(화면{scr}): Start 버튼으로 시뮬을 다시 시작하세요 "
                f"(포트·renewal 갱신 불가)",
                flush=True,
            )
    return ok_all


def resolve_playback_ui_at_sim(
    ext: Any,
    screen: int,
    t_sim: Optional[float] = None,
    *,
    explicit: bool = False,
) -> Optional[PlaybackUIState]:
    """
    재생 UI SSOT — ``plan.lookup(sim_now)`` 로 포트·막대를 한 번에 resolve.
    """
    if not bool(getattr(ext, "_sim_playback_started", False)):
        return None
    if not playback_plan_active(ext, int(screen)):
        return None
    snap = ensure_plan_snapshot(ext, int(screen))
    if snap is None:
        return None

    scr = int(screen)
    axes = resolve_playback_ui_axes(ext, scr, t_sim, explicit=bool(explicit))
    t_lookup = float(axes.t_plan)
    ports = _ensure_panel_occ_keys(dict(snap.ports_at(t_lookup)))

    preview_full = False
    try:
        pm = getattr(ext, "_sim_bar_preview_model", None)
        preview_full = bool(pm.get_value_as_bool()) if pm is not None else False
    except Exception:
        preview_full = False

    bar_total = float(getattr(snap, "final_sim_time", 0.0) or 0.0)
    pre_by = getattr(ext, "_sim_ep_bar_prerun_by_screen", None)
    bar_pre = pre_by.get(str(scr)) if isinstance(pre_by, dict) else None

    row_order: Tuple[str, ...] = ()
    if bar_pre is not None and bar_pre.row_order:
        row_order = tuple(str(r) for r in bar_pre.row_order)
    if not row_order:
        row_order = (
            "ALL_EP",
            "EP1",
            "EP2",
            "EP3",
            "INOUT",
            "BP1",
            "BP2",
            "BP3",
            "BP4",
        )
        try:
            from .control_sim_bar_graph import bar_graph_row_order

            cfg_by = getattr(ext, "_sim_snapshot_by_screen", None)
            ep_idx = 0
            ebs_on = True
            if isinstance(cfg_by, dict):
                snap_cfg = cfg_by.get(str(scr)) or cfg_by.get(int(scr))
                if isinstance(snap_cfg, dict):
                    ep_idx = int(snap_cfg.get("ep_count_idx", 0) or 0)
                    ebs_on = bool(snap_cfg.get("ebs_enabled", True))
            row_order = tuple(
                str(r) for r in bar_graph_row_order(int(ep_idx), ebs_enabled=bool(ebs_on))
            )
        except Exception:
            pass

    if bar_pre is not None and float(getattr(bar_pre, "total_est", 0.0) or 0.0) > 0.0:
        bar_total = float(bar_pre.total_est)

    if preview_full and bar_pre is not None and isinstance(bar_pre.rows, dict):
        bar_rows = {str(k): list(v) for k, v in bar_pre.rows.items()}
    else:
        from .control_sim_bar_graph import (
            overlay_bar_rows_tip_from_occ,
            replay_bar_rows_at_t,
            truncate_bar_rows_at_t,
        )
        from .playback_plan import milestones_to_finalize_tuples

        t_bar = float(t_lookup)
        bar_rows: Dict[str, List[Dict[str, Any]]] = {}

        if bar_pre is not None and isinstance(bar_pre.rows, dict) and bar_pre.rows:
            bar_rows = truncate_bar_rows_at_t(bar_pre.rows, t_bar)
            if not bar_rows or not any(bar_rows.values()):
                bar_rows = {str(k): list(v) for k, v in bar_pre.rows.items()}
        else:
            milestones = milestones_to_finalize_tuples(snap.milestones)
            sorted_items: Tuple[Any, ...] = ()
            try:
                results = getattr(ext, "_sim_prerun_results_by_screen", None)
                res = results.get(scr) if isinstance(results, dict) else None
                if res is not None:
                    sorted_items = tuple(getattr(res, "items", ()) or ())
            except Exception:
                sorted_items = ()
            ep_list = [r for r in row_order if str(r).startswith("EP")]
            all_ports = [r for r in row_order if r != "ALL_EP"]
            if not all_ports:
                all_ports = list(_PANEL_OCC_KEYS)
            faults: set = set()
            if bar_pre is not None:
                faults = {
                    str(p).strip().upper()
                    for p in (bar_pre.fault_ports or ())
                    if str(p).strip()
                }
            try:
                bar_rows = replay_bar_rows_at_t(
                    list(milestones or []),
                    sorted_items=sorted_items,
                    t_cut=t_bar,
                    row_order=list(row_order),
                    ep_list=ep_list,
                    all_ports=all_ports,
                    faults=faults,
                    plan_ports_at_t=dict(ports),
                )
            except Exception:
                bar_rows = {}

        if row_order:
            ep_rows = [x for x in row_order if str(x).startswith("EP")]
            faults_overlay: set = set()
            if bar_pre is not None:
                faults_overlay = {
                    str(p).strip().upper()
                    for p in (bar_pre.fault_ports or ())
                    if str(p).strip()
                }
            try:
                overlay_bar_rows_tip_from_occ(
                    bar_rows,
                    list(row_order),
                    ep_rows,
                    dict(ports),
                    fault_ports=faults_overlay,
                    foup_active_ep="",
                )
            except Exception:
                pass
            for r in row_order:
                rk = str(r)
                if rk not in bar_rows:
                    bar_rows[rk] = []
            if not any(
                isinstance(bar_rows.get(str(r)), list) and bar_rows.get(str(r)) for r in row_order
            ):
                seed_dur = max(1e-6, float(t_bar))
                for r in row_order:
                    bar_rows[str(r)] = [{"state": "empty", "dur": float(seed_dur)}]

    return PlaybackUIState(
        screen=int(scr),
        axes=axes,
        ports=dict(ports),
        bar_rows=bar_rows,
        bar_total_est=float(bar_total),
        row_order=row_order,
        preview_full=bool(preview_full),
    )


def apply_playback_renewal_from_wall(ext: Any, screen: int, src: Dict[str, Any]) -> bool:
    """
    재생 — LAM renewal wall 시 plan milestone(renewal sync sim) 을 패널에 즉시 반영.

    sim_now 가 JSON wall 동안 멈춰 있어도 renewal 3D 와 포트 패널이 같은 시점에 맞춰진다.
    """
    if not bool(getattr(ext, "_sim_playback_started", False)):
        return False
    if not isinstance(src, dict):
        return False

    scr = int(screen)
    snap = ensure_plan_snapshot(ext, scr)
    if snap is None:
        snap = rebuild_plan_snapshot_for_screen(ext, scr)
    if snap is None:
        return False

    sync_t: Optional[float] = None
    try:
        t0 = float(
            str(
                src.get("event_start_sim_time")
                or src.get("_event_start_sim")
                or src.get("t")
                or src.get("sim_time")
                or "0"
            ).strip()
            or "0"
        )
        proc = float(str(src.get("proc_sec") or "0").strip() or "0")
        anim = float(str(src.get("anim_sec") or "0").strip() or "0")
        est = float(str(src.get("est_total") or src.get("json_est_sec") or "0").strip() or "0")
        off = src.get("renewal_offset_sec")
        if off is None:
            parsed = src.get("parsed")
            if isinstance(parsed, list) and parsed:
                from .json_playback_timing import renewal_info_from_steps

                _hr, off = renewal_info_from_steps(list(parsed))
        from .json_playback_timing import playback_port_sync_sim_time, resolve_playback_proc_anim

        proc_pb, anim_pb = resolve_playback_proc_anim(proc, anim, json_est_sec=float(est))
        if off is not None and float(off) > 1e-9:
            sync_t = playback_port_sync_sim_time(
                float(t0),
                float(proc_pb),
                float(anim_pb),
                has_renewal=True,
                renewal_offset_sec=float(off),
            )
    except Exception:
        sync_t = None

    if sync_t is None:
        return False

    occ = _ensure_panel_occ_keys(dict(snap.ports_at(float(sync_t))))
    axes = resolve_playback_ui_axes(ext, scr)

    # JSON wall 동안 sim_now 는 멈춰 있다 → renewal 적용 후 sim 이 sync_t 에 도달할 때까지
    # heartbeat(plan ports_at(sim_now)) 가 pre-renewal 로 되돌리지 못하게 hold 를 건다.
    try:
        if float(axes.t_plan) + 1e-6 < float(sync_t):
            _set_renewal_occ_hold(ext, scr, dict(occ), float(sync_t))
    except Exception:
        pass

    return _apply_plan_ports_to_panel(
        ext,
        scr,
        occ,
        t_display=float(axes.t_display),
    )


def refresh_playback_display_at_sim(
    ext: Any,
    screen: int,
    t_sim: Optional[float] = None,
    *,
    force: bool = False,
    explicit: bool = False,
) -> None:
    """
    재생 UI 단일 갱신 — ``resolve_playback_ui_at_sim`` → 포트 + 막대.
    """
    if not bool(getattr(ext, "_sim_playback_started", False)):
        return
    scr = int(screen)
    tnow = _sim_now_for_screen(ext, scr, t_sim)

    state = resolve_playback_ui_at_sim(
        ext,
        scr,
        float(tnow),
        explicit=bool(explicit),
    )

    if state is not None:
        try:
            sync_playback_ui_at_sim(
                ext,
                scr,
                float(state.axes.t_display),
                force=bool(force),
            )
        except Exception:
            pass

    try:
        from .control_window import _apply_playback_bar_to_channel, _render_ep_bar_prerun_at_t

        _apply_playback_bar_to_channel(
            ext,
            scr,
            state,
            t_fallback=float(tnow),
        )
        chans = getattr(ext, "_sim_monitor_channels", None)
        if isinstance(chans, list) and 0 < int(scr) <= len(chans):
            ch = chans[int(scr) - 1]
            if isinstance(ch, dict) and ch.get("ep_timeline_widget") is None:
                _render_ep_bar_prerun_at_t(ext, ch, float(tnow))
    except Exception:
        pass


def handle_playback_renewal_step(
    ext: Any,
    screen: int,
    _idx: int,
    _step: dict,
) -> None:
    """재생 — ``apply_playback_renewal_from_wall`` (호출측에서 main dispatch)."""
    return


def install_playback_renewal_handlers(ext: Any) -> None:
    """재생 SSOT — renewal 핸들러 등록 불필요 (LAM 3D + wall 시 plan apply)."""
    return


# 레거시 import 호환 (no-op)
def get_plan_replay_floor(ext: Any, screen: int) -> float:
    return 0.0


def set_plan_replay_floor(ext: Any, screen: int, t_sim: float) -> None:
    return


def reset_plan_replay_floor(ext: Any, screen: int, t_sim: float) -> None:
    return


def clear_plan_replay_floors(ext: Any, *, screen: Optional[int] = None) -> None:
    return


def sync_playback_ui_at_renewal(*_args: Any, **_kwargs: Any) -> bool:
    """Deprecated — 재생 UI 는 ``refresh_playback_display_at_sim`` 만 사용."""
    return False


__all__ = [
    "PlaybackUIAxes",
    "PlaybackUIState",
    "apply_playback_renewal_from_wall",
    "clear_plan_replay_floors",
    "clear_playback_plan_runtime_state",
    "ensure_plan_snapshot",
    "ensure_playback_plans_for_results",
    "get_plan_ports_at_sim",
    "get_plan_replay_floor",
    "get_plan_snapshot",
    "get_playback_schedule_for_screen",
    "get_stored_playback_schedule_for_screen",
    "handle_playback_renewal_step",
    "install_playback_renewal_handlers",
    "plan_lookup_sim_t",
    "playback_plan_active",
    "refresh_playback_display_at_sim",
    "rebuild_plan_snapshot_for_screen",
    "reset_plan_replay_floor",
    "resolve_playback_ui_at_sim",
    "resolve_playback_ui_axes",
    "seek_playback_ui_at_sim",
    "set_plan_replay_floor",
    "sync_playback_ui_at_renewal",
    "sync_playback_ui_at_sim",
]
