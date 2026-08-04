"""
프리런 ``PlaybackPlanSnapshot`` → 재생 UI (포트·막대) display-only replay.

════════════════════════════════════════════════════════════════════════
재생 공정 경계 SSOT (필수 불변식)
════════════════════════════════════════════════════════════════════════

한 화면에서 gated JSON(ARRIVED/MOVE/REMOVED …)이 재생 중일 때
(``json_wall_busy`` **또는** ``proc_gate`` / proc_wait):

  · emit 커서(다음 gated 이벤트) · ``sim_now`` · plan lookup(포트/막대)
    는 **같은 공정 경계**를 넘지 않는다.

  · 경계 = 현재/직전 gated 이벤트의 공정 종료 sim 시각
    (``t_proc_end`` / ``t0+proc`` / ``t_playback_json_end``).

이유: emit 만 게이트로 막고 ``sim_now`` 가 앞서면 plan 이 미emit 공정의
점유(예: IN/OUT LOT)를 먼저 보여 ``ARRIVED INOUT`` 이 생략된 것처럼 보인다.
JSON 만 먼저 끝나 wall 이 풀려도 공정 진행 중이면 동일하다.

API: ``playback_process_frontier_sim`` → ``resolve_playback_ui_axes`` · clock clamp · ``can_emit``(proc_wait).
════════════════════════════════════════════════════════════════════════

재생 SSOT (단일 lookup):

  ``resolve_playback_ui_at_sim(ext, screen, t)`` → ``PlaybackUIState``
  ``refresh_playback_display_at_sim`` — 포트·막대 공통 갱신 진입점
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple


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
_MOVE_FAMILY: Tuple[str, ...] = ("MOVE", "MOVE_TRANSFERING", "MOVE_REQ")


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
            if isinstance(hold, dict) and (
                isinstance(hold.get("delta"), dict) or isinstance(hold.get("occ"), dict)
            ):
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
    delta: Optional[Dict[str, str]] = None,
) -> None:
    """
    renewal hold — **이벤트 delta 만** 저장 (전체 panel 금지).

    과거: plan milestone 전체 occ 를 hold 하면 sync_t 시점의 **다른 포트 미래 LOT** 까지
    sim_now 이전에 패널에 떠 LOT 깜빡임이 난다.
    """
    try:
        by = getattr(ext, "_sim_playback_renewal_occ_hold_by_screen", None)
        if not isinstance(by, dict):
            by = {}
            ext._sim_playback_renewal_occ_hold_by_screen = by
        dlt: Dict[str, str] = {}
        if isinstance(delta, dict) and delta:
            for k, v in delta.items():
                ku = str(k).strip().upper()
                if ku:
                    dlt[ku] = str(v or "")
        by[str(int(screen))] = {
            "delta": dict(dlt),
            # 레거시 키 — 읽기 시 delta 우선
            "occ": dict(dlt) if dlt else dict(occ or {}),
            "sync_t": float(sync_t),
            "dedupe": str(dedupe or ""),
        }
    except Exception:
        pass


def _occ_delta_for_anim_src(src: Dict[str, Any]) -> Dict[str, str]:
    """ARRIVED/MOVE/REMOVED 가 바꾸는 포트만 (전체 패널 스냅샷 아님)."""
    try:
        from .control_sim_prerun_playback import (
            _canonical_sim_port_key,
            _normalize_anim_event_seq,
            _s_val,
        )
    except Exception:
        return {}
    ev = _normalize_anim_event_seq(
        _s_val(src.get("event") or src.get("event_seq") or src.get("seq"))
    )
    lot = _s_val(src.get("lot_id"))
    fr = _canonical_sim_port_key(_s_val(src.get("from_port_id")))
    to = _canonical_sim_port_key(_s_val(src.get("to_port_id")))
    port = _canonical_sim_port_key(_s_val(src.get("port_id") or src.get("event_port_id")))
    out: Dict[str, str] = {}
    if ev in _MOVE_FAMILY:
        if fr:
            out[fr] = ""
        if to and lot:
            out[to] = lot
    elif ev == "ARRIVED":
        dest = port or to
        if dest and lot:
            out[dest] = lot
    elif ev == "REMOVED":
        tgt = port or to
        if tgt:
            out[tgt] = ""
    return out


def _merge_renewal_delta_onto_plan(
    plan_occ: Dict[str, str],
    delta: Mapping[str, str],
) -> Dict[str, str]:
    out = _ensure_panel_occ_keys(dict(plan_occ))
    for k, v in (delta or {}).items():
        ku = str(k).strip().upper()
        if ku in out or ku:
            out[ku] = str(v or "")
    return _ensure_panel_occ_keys(dict(out))


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


def clear_runtime_bar_rows(ext: Any, *, screen: Optional[int] = None) -> None:
    """레거시 no-op — 막대는 프리런 ``bar_pre`` SSOT 만 사용 (재생 중 재패치 없음)."""
    del ext, screen
    return


def _renewal_occ_for_playback_sync(
    ext: Any,
    screen: int,
    sim_now: float,
    plan_occ: Dict[str, str],
) -> Tuple[Dict[str, str], float]:
    """
    wall renewal — **이벤트 delta** 를 ``plan.ports_at(sim_now)`` 위에 덮는다.

    전체 milestone occ hold 금지: sync_t 스냅샷의 다른 포트 미래 LOT 이
    sim 이전에 패널에 잠깐 뜨는 버그(예: LOT_005 깜빡임)를 막는다.
    """
    hold = _get_renewal_occ_hold(ext, int(screen))
    if not hold:
        return _ensure_panel_occ_keys(dict(plan_occ)), float(sim_now)
    sync_t = float(hold.get("sync_t", 0.0) or 0.0)
    delta = hold.get("delta")
    if not isinstance(delta, dict):
        delta = {}
    # 레거시 full-occ hold 는 미래 LOT 깜빡임 원인이므로 무시하고 plan 만 사용
    if float(sim_now) + 1e-6 < sync_t:
        if delta:
            return _merge_renewal_delta_onto_plan(dict(plan_occ), delta), float(sim_now)
        return _ensure_panel_occ_keys(dict(plan_occ)), float(sim_now)
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
    REMOVED renewal — 패널은 비우되 FOUP prim 은 **JSON 종료** 까지 보이게 hold 등록.
    재생·라이브 공통.
    """
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
    hide_end: Optional[float] = None
    if sched is not None:
        try:
            from .playback_schedule import find_scheduled_step_for_anim_src

            step = find_scheduled_step_for_anim_src(sched, dict(src))
            if step is not None:
                # JSON 종료 우선 (요구: 객체 숨김 = json end)
                for attr in ("t_json_end", "t_anim_end", "t_proc_end"):
                    try:
                        v = float(getattr(step, attr, 0.0) or 0.0)
                    except Exception:
                        v = 0.0
                    if v > 1e-9:
                        hide_end = v
                        break
        except Exception:
            hide_end = None
    if hide_end is None or hide_end <= 1e-9:
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
            anim = float(str(src.get("anim_sec") or "0").strip() or "0")
            proc = float(str(src.get("proc_sec") or "0").strip() or "0")
            # json/애니 길이 우선, 없으면 공정 길이
            dur = anim if anim > 1e-9 else proc
            if dur > 1e-9:
                hide_end = float(t0) + float(dur)
        except Exception:
            hide_end = None
    if hide_end is None or hide_end <= 1e-9:
        # fallback: 아주 짧게라도 hold (renewal 직후 즉시 hide 방지)
        try:
            t0 = float(str(src.get("event_start_sim_time") or src.get("t") or "0").strip() or "0")
            hide_end = float(t0) + 1.0
        except Exception:
            return
    holds = _removed_prim_hide_holds(ext, int(screen))
    holds[str(port).strip().upper()] = {"lot": str(lot), "proc_end_t": float(hide_end)}


def prim_occ_for_playback_visibility(
    ext: Any,
    screen: int,
    panel_occ: Dict[str, str],
) -> Dict[str, str]:
    """
    3D prim 가시성용 occ — REMOVED hold 가 있으면 해당 포트 lot 을 유지(보임).
    패널 occ(``panel_occ``) 와 분리. 재생·라이브 renewal 공통.
    """
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


def _active_gated_event_src(ext: Any, screen: int) -> Optional[Dict[str, Any]]:
    """현재 화면에서 JSON wall 로 묶인 gated 이벤트 src/job."""
    scr = int(screen)
    try:
        bya = getattr(ext, "_sim_anim_active_by_screen", None)
        if isinstance(bya, dict):
            cand = bya.get(str(scr))
            if isinstance(cand, dict) and cand:
                return dict(cand)
    except Exception:
        pass
    try:
        by = getattr(ext, "_sim_post_anim_src_by_screen", None)
        if isinstance(by, dict):
            cand = by.get(str(scr))
            if isinstance(cand, dict) and cand:
                return dict(cand)
    except Exception:
        pass
    return None


def _step_process_end_sim(step: Any) -> Optional[float]:
    """스케줄 step → 공정 종료 sim (port sync 아님)."""
    if step is None:
        return None
    for attr in ("t_proc_end", "t_playback_json_end"):
        try:
            v = getattr(step, attr, None)
            if v is not None and float(v) > 1e-9:
                return float(v)
        except Exception:
            continue
    try:
        t_ev = float(getattr(step, "t_event", 0.0) or 0.0)
        proc = float(getattr(step, "proc_sec", 0.0) or 0.0)
        if proc > 1e-9:
            return float(t_ev) + float(proc)
    except Exception:
        pass
    return None


def _find_gated_event_step(sched: Any, src: Dict[str, Any]) -> Optional[Any]:
    """
    현재 gated anim src → 스케줄 step.

    ``find_scheduled_step_for_anim_src`` 는 port sync 필수라 ARRIVED 등에서도
    sync 없는 step 을 놓칠 수 있어, sync 없이 event+t0+port+lot 로 매칭한다.
    """
    if sched is None or not isinstance(src, dict):
        return None
    try:
        from .control_sim_prerun_playback import _normalize_anim_event_seq, _s_val
        from .playback_schedule import PlaybackScheduledStep, find_scheduled_step_for_anim_src
    except Exception:
        return None

    # sync 있는 경우 기존 matcher 우선
    try:
        step = find_scheduled_step_for_anim_src(sched, dict(src))
        if step is not None:
            return step
    except Exception:
        pass

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
        kind = str(getattr(step, "kind", "") or "").strip().lower()
        if kind and kind not in ("json_step", "event", ""):
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
        if src_bn:
            step_bn = str(getattr(step, "json_basename", "") or "").strip().lower()
            if step_bn and step_bn != src_bn:
                continue
        if dt < best_dt:
            best_dt = dt
            best = step
    return best


def playback_process_frontier_sim(ext: Any, screen: int) -> Optional[float]:
    """
    화면별 **공정 경계** sim 시각 (재생 SSOT).

    경계가 있으면 emit·``sim_now``·plan/display 가 그 시각을 넘지 않는다.

    후보 (유효한 것만, 최소값):
      1) ``proc_gate`` (직전 gated 이벤트 ``t0+proc`` — wall 해제 후에도 유지)
      2) ``json_wall_busy`` 중 active job / 스케줄 step 의 ``t_proc_end``

    wall 만 보고 clamp 하면 JSON 조기 종료 후 시계·다음 ARRIVED 가 앞서
    포트/진행이 어긋난다 → proc_wait 와 동일 SSOT.
    """
    scr = int(screen)
    candidates: List[float] = []

    try:
        from .control_sim_playback_gate import get_proc_gate_end

        pe = get_proc_gate_end(ext, scr)
        if pe is not None and float(pe) > 1e-9:
            t_now = float(_sim_now_for_screen(ext, scr, None))
            # 이미 공정 종료를 지났으면 gate 는 emit 쪽에서 열림 — frontier 없음
            if t_now + 1e-6 < float(pe):
                candidates.append(float(pe))
    except Exception:
        pass

    wall_busy = False
    try:
        from .control_sim_playback_gate import is_json_wall_busy

        wall_busy = bool(is_json_wall_busy(ext, scr))
    except Exception:
        wall_busy = False

    if wall_busy:
        src = _active_gated_event_src(ext, scr)
        end: Optional[float] = None
        if isinstance(src, dict) and src:
            try:
                sched = get_playback_schedule_for_screen(ext, scr)
                end = _step_process_end_sim(_find_gated_event_step(sched, dict(src)))
            except Exception:
                end = None
            if end is None:
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
                        end = float(t0) + float(proc)
                    else:
                        anim = float(
                            str(src.get("anim_sec") or src.get("est_total") or "0").strip() or "0"
                        )
                        if anim > 1e-9:
                            end = float(t0) + float(anim)
                except Exception:
                    end = None
        if end is not None and float(end) > 1e-9:
            candidates.append(float(end))

    if not candidates:
        return None
    return float(min(candidates))


def apply_playback_frontier(ext: Any, screen: int, t_sim: float) -> float:
    """``t_sim`` 을 공정 경계 이하로 자른다."""
    t = float(t_sim)
    fr = playback_process_frontier_sim(ext, int(screen))
    if fr is None:
        return t
    try:
        if t > float(fr) + 1e-9:
            return float(fr)
    except Exception:
        pass
    return t


# 레거시 이름 — 외부 import 호환 (공정 경계 SSOT 로 위임)
def _active_json_plan_cap_sim(ext: Any, screen: int) -> Optional[float]:
    return playback_process_frontier_sim(ext, int(screen))


def resolve_playback_ui_axes(
    ext: Any,
    screen: int,
    t_sim: Optional[float] = None,
    *,
    explicit: bool = False,
) -> PlaybackUIAxes:
    """
    재생 UI 시각 축 — **display = plan = 공정 경계가 적용된 sim**.

    - Seek(``explicit``): 요청 ``t_sim`` 그대로.
    - 그 외: ``sim_now`` 에 ``playback_process_frontier_sim`` 적용.
    """
    if explicit and t_sim is not None:
        try:
            t = float(t_sim)
        except Exception:
            t = 0.0
        return PlaybackUIAxes(t_display=float(t), t_plan=float(t))

    t = _sim_now_for_screen(ext, int(screen), t_sim)
    t = apply_playback_frontier(ext, int(screen), float(t))
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
    clear_runtime_bar_rows(ext)


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
            # 막대 색은 프리런 bar_pre SSOT. tip 은 재생 resolve fallback(비-프리런) 보정용만.
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
    재생 — LAM renewal wall 시 **이 이벤트가 바꾸는 포트만** hold.

    sync_t 는 스케줄/plan 에서 가져오되, occ 는 milestone 전체 스냅샷을 쓰지 않는다.
    (미래 다른 포트 LOT 이 함께 뜨는 깜빡임 방지)
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
    matched = "none"
    delta = _occ_delta_for_anim_src(dict(src))

    # sync_t: plan milestone 시각(이 이벤트 dest/lot 일치) — occ 내용은 쓰지 않음
    ms = _find_plan_renewal_milestone_for_event(snap, dict(src), float(sim_now_match))
    if ms is not None:
        sync_t = float(ms[0])
        matched = "plan_milestone"

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
                from .playback_renewal_ports import renewal_playback_port_sync_for_step

                st_t = renewal_playback_port_sync_for_step(step)
                if st_t is not None:
                    sync_t = float(st_t)
                    matched = "step_milestone"
            except Exception:
                sync_t = None

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

    if not delta:
        # delta 없으면 hold 의미 없음 — sync 만으로는 패널을 바꾸지 않음
        if _renewal_debug_on(ext):
            print(
                f"[RENEWAL_DBG] scr={scr} NO_DELTA ev={src.get('event')} "
                f"lot={src.get('lot_id')} sync_t={float(sync_t):.2f}",
                flush=True,
            )
        return False

    if _renewal_debug_on(ext):
        try:
            sim_now = _sim_now_for_screen(ext, scr, None)
        except Exception:
            sim_now = -1.0
        print(
            f"[RENEWAL_DBG] scr={scr} ev={src.get('event')} "
            f"to={src.get('to_port_id') or src.get('port_id')} lot={src.get('lot_id')} "
            f"match={matched} occ_src=event_delta sync_t={float(sync_t):.2f} "
            f"sim_now={float(sim_now):.2f} delta={delta}",
            flush=True,
        )
        _dump_plan_milestones_once(ext, scr, snap)

    _register_removed_prim_hide_hold_for_renewal(ext, scr, dict(src), sched)
    _set_renewal_occ_hold(ext, scr, dict(delta), float(sync_t), delta=dict(delta))

    # 막대는 프리런 bar_pre(renewal bake) SSOT — 재생 중 재그리기/패치 없음
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
    "apply_playback_frontier",
    "apply_playback_renewal_from_wall",
    "clear_plan_replay_floors",
    "clear_playback_plan_runtime_state",
    "clear_removed_prim_hide_holds",
    "clear_renewal_occ_hold",
    "clear_runtime_bar_rows",
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
    "playback_process_frontier_sim",
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
