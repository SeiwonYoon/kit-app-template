"""
프리런 ``PlaybackPlanSnapshot`` → 재생 UI (포트·막대) display-only replay.

재생 SSOT (단일 lookup):

  ``resolve_playback_ui_at_sim(ext, screen, t)`` → ``PlaybackUIState``
  ``refresh_playback_display_at_sim`` — 포트·막대 공통 갱신 진입점

  ``sim_now`` (또는 Seek 시 explicit ``t_sim``) 하나로 plan lookup.
  LAM renewal wall 은 3D 애니만 — 포트·막대는 heartbeat/tick 이 plan 을 따른다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


def _renewal_debug_on(ext: Any) -> bool:
    # 1) ext 별 명시적 OFF/ON 우선
    flag = getattr(ext, "_sim_renewal_debug", None)
    if flag is not None:
        return bool(flag)
    # 2) 환경변수
    env = str(os.environ.get("TBS_RENEWAL_DEBUG", "") or "").strip()
    if env not in ("", "0", "false", "False"):
        return True
    if env in ("0", "false", "False"):
        return False
    # 3) 기본값 — sim_control_defaults.SIM_RENEWAL_DEBUG (상시 ON)
    try:
        from .sim_control_defaults import SIM_RENEWAL_DEBUG

        return bool(SIM_RENEWAL_DEBUG)
    except Exception:
        return False

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


def clear_renewal_occ_hold(ext: Any, screen: int) -> None:
    """renewal JSON 종료·재생 정지 시 hold 해제."""
    _clear_renewal_occ_hold(ext, int(screen))


def _resolve_renewal_sync_t_for_playback(
    ext: Any,
    screen: int,
    src: Dict[str, Any],
) -> Optional[float]:
    """renewal 포트 sync sim 시각 — 프리런 스케줄 SSOT 우선, 없으면 runtime 계산."""
    if not isinstance(src, dict):
        return None
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
    except Exception:
        t0 = 0.0
    ev = str(src.get("event") or src.get("event_seq") or src.get("seq") or "").strip().upper()
    file_bn = str(src.get("file") or "").strip().lower()

    sched = get_stored_playback_schedule_for_screen(ext, int(screen))
    if sched is not None:
        try:
            from .playback_renewal_ports import (
                renewal_playback_port_sync_for_step,
                step_json_has_renewal_marker,
            )

            for step in sched.steps or ():
                if str(step.kind or "").strip().lower() != "json_step":
                    continue
                if not bool(step.has_renewal) and not step_json_has_renewal_marker(step):
                    continue
                if abs(float(step.t_event or 0.0) - float(t0)) > 0.25:
                    continue
                if ev and str(step.event_seq or "").strip().upper() not in ("", ev):
                    continue
                step_bn = str(step.json_basename or "").strip().lower()
                if file_bn and step_bn and file_bn != step_bn:
                    continue
                st = step.t_playback_port_sync
                if st is None:
                    st = renewal_playback_port_sync_for_step(step)
                if st is not None and float(st) > 1e-9:
                    return float(st)
        except Exception:
            pass

    try:
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
            return playback_port_sync_sim_time(
                float(t0),
                float(proc_pb),
                float(anim_pb),
                has_renewal=True,
                renewal_offset_sec=float(off),
            )
    except Exception:
        pass
    return None


_MOVE_FAMILY: Tuple[str, ...] = ("MOVE", "MOVE_TRANSFERING", "MOVE_REQ")


def _canon_port(p: Any) -> str:
    o = str(p or "").strip().upper()
    if o in ("IN/OUT", "INOUT"):
        return "INOUT"
    return o


def _find_wall_renewal_step(sched: Any, src: Dict[str, Any]) -> Optional[Any]:
    """
    renewal wall 이 가리키는 "이 이벤트의 JSON step" 을 robust 하게 찾는다.

    ``find_scheduled_step_for_anim_src`` 와 달리 ``t_playback_port_sync`` 유무에 의존하지 않고,
    MOVE 계열(MOVE/MOVE_TRANSFERING/MOVE_REQ)을 한 family 로 취급한다. event+t0+to/port+lot+파일명
    으로 매칭해 renewal step 만 반환한다.
    """
    if sched is None or not isinstance(src, dict):
        return None
    try:
        from .control_sim_prerun_playback import _normalize_anim_event_seq, _s_val
        from .playback_renewal_ports import step_json_has_renewal_marker
        from .playback_schedule import PlaybackScheduledStep
    except Exception:
        return None

    ev = _normalize_anim_event_seq(
        _s_val(src.get("event") or src.get("event_seq") or src.get("seq"))
    )
    if not ev:
        return None
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
    except Exception:
        t0 = 0.0
    src_to = _canon_port(src.get("to_port_id") or src.get("port_id"))
    src_lot = str(src.get("lot_id") or "").strip()
    src_bn = ""
    for cand in (src.get("path"), src.get("file")):
        cs = str(cand or "").strip()
        if cs:
            src_bn = cs.split("/")[-1].split("\\")[-1].strip().lower()
            break

    ev_is_move = ev in _MOVE_FAMILY
    best: Optional[Any] = None
    best_dt = 1e18
    for step in getattr(sched, "steps", None) or ():
        if not isinstance(step, PlaybackScheduledStep):
            continue
        if str(step.kind or "").strip().lower() != "json_step":
            continue
        if not (bool(step.has_renewal) or step_json_has_renewal_marker(step)):
            continue
        p = step.progress_payload if isinstance(step.progress_payload, dict) else {}
        step_ev = _normalize_anim_event_seq(
            _s_val(p.get("event_seq") or p.get("sequence_name") or step.event_seq)
        )
        ev_ok = (step_ev == ev) or (ev_is_move and step_ev in _MOVE_FAMILY)
        if not ev_ok:
            continue
        try:
            step_t0 = float(
                str(
                    p.get("event_start_sim_time")
                    or p.get("sim_time")
                    or step.t_event
                    or "0"
                ).strip()
                or "0"
            )
        except Exception:
            step_t0 = float(step.t_event or 0.0)
        dt = abs(step_t0 - t0)
        if dt > 0.3:
            continue
        ep = step.event_payload if isinstance(step.event_payload, dict) else {}
        step_to = _canon_port(ep.get("to_port_id") or ep.get("port_id"))
        if src_to and step_to and src_to != step_to:
            continue
        step_lot = str(ep.get("lot_id") or p.get("lot_id") or "").strip()
        if src_lot and step_lot and src_lot != step_lot:
            continue
        step_bn = str(step.json_basename or "").strip().lower()
        if src_bn and step_bn and src_bn != step_bn:
            continue
        if dt < best_dt:
            best_dt = dt
            best = step
    return best


def _renewal_occ_for_playback_sync(
    ext: Any,
    screen: int,
    sim_now: float,
    plan_occ: Dict[str, str],
) -> Tuple[Dict[str, str], float]:
    """
    wall 이 sim 보다 앞서 renewal 을 적용한 뒤 — sim 이 sync_t 에 도달할 때까지 hold occ 유지.

    heartbeat 가 ``ports_at(sim_now)`` 로 pre-renewal 상태를 덮어쓰며 깜빡이는 것을 막는다.
    """
    hold = _get_renewal_occ_hold(ext, int(screen))
    if not hold:
        return _ensure_panel_occ_keys(dict(plan_occ)), float(sim_now)
    sync_t = float(hold.get("sync_t", 0.0) or 0.0)
    held_occ = _ensure_panel_occ_keys(dict(hold.get("occ") or {}))
    if float(sim_now) + 1e-6 < sync_t:
        # 아직 sim 이 renewal 시점에 도달하지 않음 — wall 이 적용한 renewal occ 유지(깜빡임 방지).
        return dict(held_occ), float(sim_now)
    # sim 이 sync_t 를 지났다 → plan ports_at(sim_now) 가 이미 renewal milestone 을 포함하므로
    # 그대로 사용한다(과거 occ merge 금지: 회수·이동으로 비워진 포트가 되살아나는 버그 방지).
    _clear_renewal_occ_hold(ext, int(screen))
    return _ensure_panel_occ_keys(dict(plan_occ)), float(sim_now)


def _playback_ports_at_sim(
    ext: Any,
    snap: PlaybackPlanSnapshot,
    screen: int,
    t_sim: float,
) -> Dict[str, str]:
    """plan.lookup(t) + renewal hold (포트·막대 공통)."""
    occ = _ensure_panel_occ_keys(dict(snap.ports_at(float(t_sim))))
    occ, _ = _renewal_occ_for_playback_sync(ext, int(screen), float(t_sim), occ)
    return _ensure_panel_occ_keys(dict(occ))


# ── REMOVED JSON: 포트 패널은 renewal, 3D prim 숨김만 proc_end 까지 보류 ─────

def _removed_prim_hide_holds(ext: Any, screen: int) -> Dict[str, Dict[str, Any]]:
    by = getattr(ext, "_sim_playback_removed_prim_hold_by_screen", None)
    if not isinstance(by, dict):
        by = {}
        ext._sim_playback_removed_prim_hold_by_screen = by
    sk = str(int(screen))
    holds = by.get(sk)
    if not isinstance(holds, dict):
        holds = {}
        by[sk] = holds
    return holds


def _register_removed_prim_hide_hold_for_renewal(
    ext: Any,
    screen: int,
    src: Dict[str, Any],
    sched: Any,
) -> None:
    """
    REMOVED renewal — 패널은 비우되 FOUP prim 은 공정 종료(proc_end) 까지 보이게 hold 등록.
    """
    if not bool(getattr(ext, "_sim_playback_started", False)):
        return
    try:
        from .control_sim_prerun_playback import _normalize_anim_event_seq, _s_val
    except Exception:
        return
    ev = _normalize_anim_event_seq(_s_val(src.get("event") or src.get("event_seq") or src.get("seq")))
    if ev != "REMOVED":
        return
    port = _canon_port(src.get("port_id") or src.get("event_port_id") or src.get("to_port_id"))
    lot = str(src.get("lot_id") or "").strip()
    if not port or not lot:
        return
    proc_end: Optional[float] = None
    if sched is not None:
        try:
            from .playback_schedule import find_scheduled_step_for_anim_src

            step = find_scheduled_step_for_anim_src(sched, dict(src))
            if step is not None:
                proc_end = float(step.t_proc_end or 0.0)
        except Exception:
            proc_end = None
    if proc_end is None or proc_end <= 1e-9:
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
            if proc > 1e-9:
                proc_end = float(t0) + float(proc)
        except Exception:
            proc_end = None
    if proc_end is None or proc_end <= 1e-9:
        return
    holds = _removed_prim_hide_holds(ext, int(screen))
    holds[str(port).strip().upper()] = {"lot": str(lot), "proc_end_t": float(proc_end)}


def prim_occ_for_playback_visibility(
    ext: Any,
    screen: int,
    panel_occ: Dict[str, str],
) -> Dict[str, str]:
    """
    재생 중 3D prim 가시성용 occ — REMOVED hold 가 있으면 해당 포트 lot 을 유지(보임).
    패널 occ(``panel_occ``) 와 분리해서 쓴다.
    """
    if not bool(getattr(ext, "_sim_playback_started", False)):
        return _ensure_panel_occ_keys(dict(panel_occ))
    out = _ensure_panel_occ_keys(dict(panel_occ))
    holds = _removed_prim_hide_holds(ext, int(screen))
    if not holds:
        return out
    try:
        sim_now = float(_sim_now_for_screen(ext, int(screen), None))
    except Exception:
        sim_now = 0.0
    expired: List[str] = []
    for port, hold in list(holds.items()):
        if not isinstance(hold, dict):
            expired.append(str(port))
            continue
        lot = str(hold.get("lot") or "").strip()
        try:
            proc_end = float(hold.get("proc_end_t", 0.0) or 0.0)
        except Exception:
            proc_end = 0.0
        pu = str(port).strip().upper()
        if not lot or proc_end <= 1e-9:
            expired.append(pu)
            continue
        if float(sim_now) + 1e-6 < proc_end:
            if pu in out:
                out[pu] = lot
        else:
            expired.append(pu)
    for pu in expired:
        holds.pop(pu, None)
    return out


def clear_removed_prim_hide_holds(ext: Any, screen: Optional[int] = None) -> None:
    try:
        by = getattr(ext, "_sim_playback_removed_prim_hold_by_screen", None)
        if not isinstance(by, dict):
            return
        if screen is None:
            by.clear()
        else:
            by.pop(str(int(screen)), None)
    except Exception:
        pass


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
    return _playback_ports_at_sim(ext, snap, int(screen), float(t_lookup))


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
    occ = _playback_ports_at_sim(ext, snap, int(screen), float(t_lookup))
    prim_occ = prim_occ_for_playback_visibility(ext, int(screen), dict(occ))

    last_by = getattr(ext, "_sim_last_ports_occupancy_by_screen", None)
    last = last_by.get(sk) if isinstance(last_by, dict) else None
    last_prim_by = getattr(ext, "_sim_last_prim_ports_occupancy_by_screen", None)
    last_prim = last_prim_by.get(sk) if isinstance(last_prim_by, dict) else None
    if (not force) and isinstance(last, dict) and _occ_dicts_equal(dict(last), occ):
        # REMOVED prim hide hold: 패널 occ 는 같아도 proc_end 이후 prim occ 가 바뀌면 갱신.
        if isinstance(last_prim, dict) and _occ_dicts_equal(last_prim, prim_occ):
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
    clear_removed_prim_hide_holds(ext)


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
    ports = _playback_ports_at_sim(ext, snap, scr, float(t_lookup))

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


def _find_plan_renewal_milestone_for_event(
    snap: PlaybackPlanSnapshot,
    src: Dict[str, Any],
    sim_now: float,
) -> Optional[Tuple[float, Dict[str, str]]]:
    """
    이 이벤트가 만들어내는 **plan occ milestone** 을 직접 찾는다 (sync_t 재계산 금지).

    wall 이 자체 계산한 sync_t 가 plan milestone 시각과 어긋나면(예: resolve fallback)
    hold 가 즉시 해제되어 이전 상태가 보이는 버그가 난다. plan milestone 은 이미 정확하므로
    이벤트 결과(ARRIVED/MOVE → ``to==lot``, REMOVED → ``port==''``)와 일치하고 ``sim_now`` 에
    가장 가까운(>= sim_now - 1.0) milestone 을 그대로 채택한다.
    """
    try:
        from .control_sim_prerun_playback import _normalize_anim_event_seq, _s_val
    except Exception:
        return None

    ev = _normalize_anim_event_seq(
        _s_val(src.get("event") or src.get("event_seq") or src.get("seq"))
    )
    if not ev:
        return None
    lot = str(src.get("lot_id") or "").strip()
    to = _canon_port(src.get("to_port_id") or src.get("port_id"))
    port = _canon_port(src.get("port_id") or src.get("event_port_id") or src.get("to_port_id"))

    def _matches(occ: Dict[str, str]) -> bool:
        if ev in _MOVE_FAMILY:
            if not to:
                return False
            return str(occ.get(to, "") or "").strip() == lot and lot != ""
        if ev == "ARRIVED":
            dest = to or port
            if not dest:
                return False
            return str(occ.get(dest, "") or "").strip() == lot and lot != ""
        if ev == "REMOVED":
            tgt = port or to
            if not tgt:
                return False
            return str(occ.get(tgt, "") or "").strip() == ""
        return False

    lo = float(sim_now) - 1.0
    best: Optional[Tuple[float, Dict[str, str]]] = None
    for m in snap.milestones or ():
        if str(getattr(m, "kind", "")) != "occ_full":
            continue
        t_ms = float(getattr(m, "t_sim", 0.0) or 0.0)
        if t_ms < lo:
            continue
        data = getattr(m, "data", None)
        if not isinstance(data, dict):
            continue
        if not _matches(data):
            continue
        if best is None or t_ms < best[0]:
            best = (t_ms, _ensure_panel_occ_keys(dict(data)))
    return best


def _dump_plan_milestones_once(ext: Any, screen: int, snap: PlaybackPlanSnapshot) -> None:
    """디버그 — 화면별 plan occ 마일스톤을 1회만 콘솔에 덤프."""
    try:
        done = getattr(ext, "_sim_renewal_dbg_dumped_screens", None)
        if not isinstance(done, set):
            done = set()
            ext._sim_renewal_dbg_dumped_screens = done
        if int(screen) in done:
            return
        done.add(int(screen))
        print(f"[RENEWAL_DBG] --- plan occ milestones (scr={int(screen)}) ---", flush=True)
        for m in snap.milestones or ():
            if str(getattr(m, "kind", "")) not in ("occ_full", "occ_snap", "occ_plan"):
                continue
            data = getattr(m, "data", None)
            if isinstance(data, dict):
                nz = {k: v for k, v in data.items() if str(v or "").strip()}
            else:
                nz = data
            print(
                f"[RENEWAL_DBG]   t={float(getattr(m, 't_sim', 0.0)):7.2f} "
                f"ord={getattr(m, 'order', 0)} {getattr(m, 'kind', '')}: {nz}",
                flush=True,
            )
        print("[RENEWAL_DBG] --- end milestones ---", flush=True)
    except Exception:
        pass


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

    sched = get_stored_playback_schedule_for_screen(ext, scr)

    try:
        sim_now_match = _sim_now_for_screen(ext, scr, None)
    except Exception:
        sim_now_match = 0.0

    sync_t: Optional[float] = None
    occ: Optional[Dict[str, str]] = None
    occ_src = "none"
    matched = "none"

    # ── 1순위(SSOT 직결): 이 이벤트가 만드는 plan occ milestone 을 그대로 채택. ──
    # wall 이 sync_t 를 재계산하지 않고 plan 과 "동일한 milestone(t_sim, occ)" 을 쓰므로
    # wall sync_t < sim_now < plan milestone 으로 hold 가 조기 해제되던 버그(MOVE/REMOVED
    # 갱신 안됨·이전 것 적용)가 원천 차단된다.
    ms = _find_plan_renewal_milestone_for_event(snap, dict(src), float(sim_now_match))
    if ms is not None:
        sync_t, occ = float(ms[0]), _ensure_panel_occ_keys(dict(ms[1]))
        matched = "plan_milestone"
        occ_src = "plan_milestone"

    # 2순위: step 기반 sync_t (renewal_playback_port_sync_for_step) — plan 빌드와 동일 함수.
    if sync_t is None:
        step = _find_wall_renewal_step(sched, dict(src))
        if step is None and sched is not None:
            try:
                from .playback_schedule import find_scheduled_step_for_anim_src

                step = find_scheduled_step_for_anim_src(sched, dict(src))
            except Exception:
                step = None
        if step is not None:
            try:
                from .playback_renewal_ports import (
                    renewal_full_panel_occ_for_step,
                    renewal_playback_port_sync_for_step,
                )

                st_t = renewal_playback_port_sync_for_step(step)
                if st_t is not None:
                    sync_t = float(st_t)
                    matched = "step_milestone"
                    base_occ = _ensure_panel_occ_keys(
                        dict(snap.ports_at(max(0.0, float(sync_t) - 0.01)))
                    )
                    occ_step = renewal_full_panel_occ_for_step(
                        step,
                        base_occ=dict(base_occ),
                        panel_ports=list(_PANEL_OCC_KEYS),
                    )
                    if isinstance(occ_step, dict) and occ_step:
                        occ = _ensure_panel_occ_keys(dict(occ_step))
                        occ_src = "step_full"
            except Exception:
                sync_t = None

    # 3순위: src 단독 runtime 계산.
    if sync_t is None:
        sync_t = _resolve_renewal_sync_t_for_playback(ext, scr, src)
        if sync_t is not None:
            matched = "runtime"

    if sync_t is None:
        if _renewal_debug_on(ext):
            print(
                f"[RENEWAL_DBG] scr={scr} NO_SYNC ev={src.get('event')} "
                f"to={src.get('to_port_id') or src.get('port_id')} lot={src.get('lot_id')} "
                f"path={src.get('path') or src.get('file')}",
                flush=True,
            )
        return False

    if occ is None:
        occ = _ensure_panel_occ_keys(dict(snap.ports_at(float(sync_t))))
        occ_src = "ports_at"

    if _renewal_debug_on(ext):
        try:
            sim_now = _sim_now_for_screen(ext, scr, None)
        except Exception:
            sim_now = -1.0
        nz = {k: v for k, v in occ.items() if str(v or "").strip()}
        nz_plan = {k: v for k, v in snap.ports_at(float(sync_t)).items() if str(v or "").strip()}
        print(
            f"[RENEWAL_DBG] scr={scr} ev={src.get('event')} "
            f"to={src.get('to_port_id') or src.get('port_id')} lot={src.get('lot_id')} "
            f"match={matched} occ_src={occ_src} sync_t={float(sync_t):.2f} sim_now={float(sim_now):.2f} "
            f"occ={nz} plan_at_sync={nz_plan}",
            flush=True,
        )
        _dump_plan_milestones_once(ext, scr, snap)

    # ── 단일 writer 원칙 ──────────────────────────────────────────────
    # wall 은 패널에 직접 쓰지 않는다. renewal occ 를 hold 에만 넣고, 패널 반영은
    # heartbeat 경로(refresh_playback_display_at_sim → _playback_ports_at_sim →
    # _renewal_occ_for_playback_sync) 단 하나로만 한다.
    _register_removed_prim_hide_hold_for_renewal(ext, scr, dict(src), sched)
    _set_renewal_occ_hold(ext, scr, dict(occ), float(sync_t))

    refresh_playback_display_at_sim(ext, scr, force=True)
    return True


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
    "clear_removed_prim_hide_holds",
    "clear_renewal_occ_hold",
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
    "prim_occ_for_playback_visibility",
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
