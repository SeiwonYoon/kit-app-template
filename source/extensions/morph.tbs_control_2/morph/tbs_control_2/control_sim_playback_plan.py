"""
프리런 ``PlaybackPlanSnapshot`` → 재생 UI (포트·막대) display-only replay.

════════════════════════════════════════════════════════════════════════
재생 공정 경계 SSOT (필수 불변식)
════════════════════════════════════════════════════════════════════════

한 화면에서 gated JSON(ARRIVED/MOVE/REMOVED …)이 재생 중일 때
(``json_wall_busy`` **또는** ``proc_gate`` / proc_wait):

  · ``sim_now`` 는 단조 전진 — 진행률·plan 은 ``playback_sync_sim_t`` / ``playback_plan_lookup_sim_t`` (표시·lookup 전용).

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

포트·plan·progress·renewal:
  재생 sim 축 — ``sim_now`` 단조 전진, plan ``playback_plan_lookup_sim_t(sim_now)``.
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


def reset_playback_renewal_runtime(ext: Any, screen: int) -> None:
    """다음 gated JSON event 시작·재생 종료 — renewal applied/hold/panel 잔상 제거."""
    scr = int(screen)
    clear_renewal_occ_hold(ext, scr)
    sk = str(scr)
    try:
        by = getattr(ext, "_sim_playback_renewal_applied_by_screen", None)
        if isinstance(by, dict):
            by.pop(sk, None)
    except Exception:
        pass
    try:
        pan = getattr(ext, "_sim_playback_renewal_panel_by_screen", None)
        if isinstance(pan, dict):
            pan.pop(sk, None)
    except Exception:
        pass


def _proc_gate_plan_cap_sim(ext: Any, screen: int) -> Optional[float]:
    """
    ``proc_gate`` — 미emit 다음 gated event 전까지 plan lookup 상한.

    ``sim_now >= t_proc_end`` 여도 cap 을 유지해야 heartbeat 가
    다음 JSON 마일스톤 포트를 잠깐 그리는 깜빡임이 없다.
    """
    scr = int(screen)
    caps: List[float] = []
    try:
        from .control_sim_playback_gate import get_proc_gate_end

        parallel = False
        try:
            from .sim_parallel_rails import parallel_moves_enabled

            parallel = bool(parallel_moves_enabled())
        except Exception:
            parallel = False

        if parallel:
            for rail in ("oht", "move"):
                pe = get_proc_gate_end(ext, scr, rail=rail)
                if pe is not None and float(pe) > 1e-9:
                    caps.append(float(pe))
        else:
            pe = get_proc_gate_end(ext, scr)
            if pe is not None and float(pe) > 1e-9:
                caps.append(float(pe))
    except Exception:
        pass
    if not caps:
        return None
    return float(min(caps))


def _playback_gated_plan_cap_sim(ext: Any, screen: int) -> Optional[float]:
    """JSON wall·proc_gate·active job — plan lookup 최소 상한."""
    caps: List[float] = []
    try:
        pe = _proc_gate_plan_cap_sim(ext, int(screen))
        if pe is not None:
            caps.append(float(pe))
    except Exception:
        pass
    try:
        jc = _active_json_process_cap_sim(ext, int(screen))
        if jc is not None:
            caps.append(float(jc))
    except Exception:
        pass
    if not caps:
        return None
    return float(min(caps))


def _active_json_process_cap_sim(ext: Any, screen: int) -> Optional[float]:
    """
    JSON wall active — plan lookup 이 **현재 job 공정 종료**를 넘지 않게 (미래 포트 차단).

    frontier 가 sim_now>=t_end 일 때 후보에서 빠져도 cap 은 유지한다.
    """
    scr = int(screen)
    caps: List[float] = []
    try:
        from .control_sim_playback_gate import is_json_wall_busy

        parallel = False
        try:
            from .sim_parallel_rails import parallel_moves_enabled

            parallel = bool(parallel_moves_enabled())
        except Exception:
            parallel = False

        rails: Tuple[Optional[str], ...] = ("oht", "move") if parallel else (None,)
        for rail in rails:
            r = str(rail).strip().lower() if rail else None
            if r and not is_json_wall_busy(ext, scr, rail=r):
                continue
            if not r and not is_json_wall_busy(ext, scr):
                continue
            act = _active_gated_event_src(ext, scr, rail=r if r else None)
            if not isinstance(act, dict) or not act:
                continue
            end: Optional[float] = None
            try:
                sched = get_playback_schedule_for_screen(ext, scr)
                end = _step_process_end_sim(_find_gated_event_step(sched, dict(act)))
            except Exception:
                end = None
            if end is None:
                try:
                    t0 = float(
                        str(
                            act.get("_event_start_sim")
                            or act.get("event_start_sim_time")
                            or act.get("t")
                            or act.get("sim_time")
                            or "0"
                        ).strip()
                        or "0"
                    )
                    proc = float(str(act.get("proc_sec") or "0").strip() or "0")
                    if proc > 1e-9:
                        end = float(t0) + float(proc)
                except Exception:
                    end = None
            if end is not None and float(end) > 1e-9:
                caps.append(float(end))
    except Exception:
        pass
    if not caps:
        return None
    return float(min(caps))


def playback_plan_lookup_sim_t(ext: Any, screen: int, t_sim: float) -> float:
    """plan·막대 lookup — wall 매핑 + gated cap(proc_gate·active job) + renewal cap."""
    t = float(t_sim)
    if ext is None or not bool(getattr(ext, "_sim_playback_started", False)):
        return t
    try:
        from .json_playback_timing import playback_sync_sim_t

        t = float(playback_sync_sim_t(ext, int(screen), t))
    except Exception:
        pass
    try:
        cap = _playback_gated_plan_cap_sim(ext, int(screen))
        if cap is not None and t > float(cap) + 1e-9:
            t = float(cap)
    except Exception:
        pass
    try:
        t = float(_renewal_plan_lookup_adjust(ext, int(screen), t))
    except Exception:
        pass
    return float(t)


def _renewal_plan_lookup_adjust(ext: Any, screen: int, t_lookup: float) -> float:
    """
    renewal plan lookup.

    · applied 후: sync_t floor — JSON wall 해제·공정 대기 중에도 heartbeat 가 pre-renewal 로
      되돌리지 않게 (wall 해제 시 reset 하지 않음, 다음 gated event 시작 시 reset).
    · wall 전: sync_t 미만 cap (renewal 조기 표시 방지)
    """
    t = float(t_lookup)
    scr = int(screen)

    if _renewal_wall_applied_for_screen(ext, scr):
        sync_t = _renewal_stored_sync_t(ext, scr)
        if sync_t is not None and float(sync_t) > 1e-9:
            if t + 1e-6 < float(sync_t):
                return max(t, float(sync_t))
            return t

    try:
        from .control_sim_playback_gate import is_json_wall_busy

        if not is_json_wall_busy(ext, scr):
            return t

        act = _active_gated_event_src(ext, scr)
        if not isinstance(act, dict) or not act:
            return t

        has_r = bool(act.get("has_renewal"))
        if not has_r:
            try:
                parsed = act.get("parsed")
                if isinstance(parsed, list) and parsed:
                    from .json_playback_timing import renewal_info_from_steps

                    has_r, _ = renewal_info_from_steps(list(parsed))
            except Exception:
                has_r = False
        if not has_r:
            return t

        sync_t = _resolve_renewal_sync_t_for_playback(ext, scr, dict(act))
        if sync_t is None or float(sync_t) <= 1e-9:
            return t

        if t > float(sync_t) - 1e-4:
            return max(0.0, float(sync_t) - 1e-4)
    except Exception:
        pass
    return max(0.0, float(t))


def _renewal_apply_dedupe(src: Dict[str, Any]) -> str:
    if not isinstance(src, dict):
        return ""
    try:
        t0 = float(
            str(
                src.get("_event_start_sim")
                or src.get("event_start_sim_time")
                or src.get("t")
                or src.get("sim_time")
                or "0"
            ).strip()
            or "0"
        )
    except Exception:
        t0 = 0.0
    ev = str(src.get("event") or src.get("event_seq") or src.get("seq") or "").strip().upper()
    bn = ""
    for cand in (src.get("file"), src.get("path")):
        cs = str(cand or "").strip()
        if cs:
            bn = cs.split("/")[-1].split("\\")[-1].strip().lower()
            break
    return f"{ev}|{t0:.4f}|{bn}"


def mark_playback_renewal_wall_applied(
    ext: Any,
    screen: int,
    src: Dict[str, Any],
    *,
    sync_t: Optional[float] = None,
    occ: Optional[Dict[str, str]] = None,
) -> None:
    """renewal wall 콜백 — 현재 job dedupe + 패널 occ SSOT 기록."""
    if not isinstance(src, dict):
        return
    sk = str(int(screen))
    dedupe = _renewal_apply_dedupe(dict(src))
    try:
        by = getattr(ext, "_sim_playback_renewal_applied_by_screen", None)
        if not isinstance(by, dict):
            by = {}
            ext._sim_playback_renewal_applied_by_screen = by
        by[sk] = dedupe
    except Exception:
        pass
    if isinstance(occ, dict) and occ:
        try:
            pan = getattr(ext, "_sim_playback_renewal_panel_by_screen", None)
            if not isinstance(pan, dict):
                pan = {}
                ext._sim_playback_renewal_panel_by_screen = pan
            pan[sk] = {
                "dedupe": dedupe,
                "sync_t": float(sync_t) if sync_t is not None else 0.0,
                "occ": _ensure_panel_occ_keys(dict(occ)),
            }
        except Exception:
            pass


def _renewal_wall_applied_for_src(ext: Any, screen: int, src: Dict[str, Any]) -> bool:
    try:
        by = getattr(ext, "_sim_playback_renewal_applied_by_screen", None)
        if not isinstance(by, dict):
            return False
        return str(by.get(str(int(screen)), "") or "") == _renewal_apply_dedupe(dict(src))
    except Exception:
        return False


def _renewal_wall_applied_for_screen(ext: Any, screen: int) -> bool:
    """renewal wall 콜백이 적용됐는지 — active job dedupe 재검증 없이 화면 단위."""
    try:
        by = getattr(ext, "_sim_playback_renewal_applied_by_screen", None)
        if not isinstance(by, dict):
            return False
        return bool(str(by.get(str(int(screen)), "") or "").strip())
    except Exception:
        return False


def _renewal_panel_record(ext: Any, screen: int) -> Optional[Dict[str, Any]]:
    try:
        pan = getattr(ext, "_sim_playback_renewal_panel_by_screen", None)
        if not isinstance(pan, dict):
            return None
        rec = pan.get(str(int(screen)))
        return rec if isinstance(rec, dict) else None
    except Exception:
        return None


def _renewal_stored_sync_t(ext: Any, screen: int) -> Optional[float]:
    rec = _renewal_panel_record(ext, int(screen))
    if isinstance(rec, dict):
        try:
            st = rec.get("sync_t")
            if st is not None and float(st) > 1e-9:
                return float(st)
        except Exception:
            pass
    try:
        by = getattr(ext, "_sim_playback_renewal_applied_by_screen", None)
        dedupe = str(by.get(str(int(screen)), "") or "") if isinstance(by, dict) else ""
        if not dedupe:
            return None
        act = _active_gated_event_src(ext, int(screen))
        if isinstance(act, dict) and _renewal_apply_dedupe(dict(act)) == dedupe:
            return _resolve_renewal_sync_t_for_playback(ext, int(screen), dict(act))
    except Exception:
        pass
    return None


def _collect_playback_frontier_candidates(ext: Any, screen: int) -> List[float]:
    """gated JSON·proc_gate·hold_t 후보 sim 경계 목록."""
    scr = int(screen)
    candidates: List[float] = []
    t_now = float(_sim_now_for_screen(ext, scr, None))
    parallel = False
    try:
        from .sim_parallel_rails import parallel_moves_enabled

        parallel = bool(parallel_moves_enabled())
    except Exception:
        parallel = False

    def _append_proc_end(pe: Any) -> None:
        try:
            if pe is not None and float(pe) > 1e-9:
                candidates.append(float(pe))
        except Exception:
            pass

    def _append_from_active_src(src: Optional[Dict[str, Any]]) -> None:
        if not isinstance(src, dict) or not src:
            return
        end: Optional[float] = None
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
                        str(src.get("anim_sec") or src.get("est_total") or "0").strip()
                        or "0"
                    )
                    if anim > 1e-9:
                        end = float(t0) + float(anim)
            except Exception:
                end = None
        if end is not None and float(end) > 1e-9:
            candidates.append(float(end))

    try:
        from .control_sim_playback_gate import (
            get_proc_gate_end,
            is_json_wall_busy,
        )

        if parallel:
            for rail in ("oht", "move"):
                _append_proc_end(get_proc_gate_end(ext, scr, rail=rail))
                if is_json_wall_busy(ext, scr, rail=rail):
                    _append_from_active_src(_active_gated_event_src(ext, scr, rail=rail))
        else:
            _append_proc_end(get_proc_gate_end(ext, scr))
            if is_json_wall_busy(ext, scr):
                _append_from_active_src(_active_gated_event_src(ext, scr))
    except Exception:
        pass

    if not parallel:
        try:
            from .control_sim_multi_playback import get_sim_playback_player

            pl = get_sim_playback_player(ext, scr)
            hold_t = None
            if pl is not None and hasattr(pl, "pending_gated_emit_hold_t"):
                hold_t = pl.pending_gated_emit_hold_t(scr)
            if hold_t is not None and float(hold_t) > 1e-9:
                candidates.append(float(hold_t))
        except Exception:
            pass
    return candidates


def playback_plan_frontier_sim(ext: Any, screen: int) -> Optional[float]:
    """
    plan·막대 lookup 상한 — busy 레일 **min** 공정 경계.

    ``sim_now`` 가 미emit ARRIVED milestone 을 넘어 INOUT 등이 먼저 차는 것을 막는다.
    """
    candidates = _collect_playback_frontier_candidates(ext, int(screen))
    if not candidates:
        return None
    return float(min(candidates))


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
    milestone_occ: Optional[Dict[str, str]] = None,
    wall_applied: bool = False,
) -> None:
    """
    renewal hold — **이벤트 delta 만** 저장 (전체 panel 금지).

    병렬 A∥B 동시 renewal 시 **키별 merge** (덮어쓰기로 EP/BP delta 가
    사라지며 INOUT만 남는 버그 방지).

    과거: plan milestone 전체 occ 를 hold 하면 sync_t 시점의 **다른 포트 미래 LOT** 까지
    sim_now 이전에 패널에 떠 LOT 깜빡임이 난다.
    """
    try:
        by = getattr(ext, "_sim_playback_renewal_occ_hold_by_screen", None)
        if not isinstance(by, dict):
            by = {}
            ext._sim_playback_renewal_occ_hold_by_screen = by
        dlt: Dict[str, str] = {}
        prev = by.get(str(int(screen)))
        if isinstance(prev, dict) and isinstance(prev.get("delta"), dict):
            for k, v in prev["delta"].items():
                ku = str(k).strip().upper()
                if ku:
                    dlt[ku] = str(v or "")
        if isinstance(delta, dict) and delta:
            for k, v in delta.items():
                ku = str(k).strip().upper()
                if ku:
                    dlt[ku] = str(v or "")
        sync_keep = float(sync_t)
        try:
            if isinstance(prev, dict) and prev.get("sync_t") is not None:
                sync_keep = max(float(prev.get("sync_t")), float(sync_t))
        except Exception:
            sync_keep = float(sync_t)
        entry: Dict[str, Any] = {
            "delta": dict(dlt),
            # 레거시 키 — 읽기 시 delta 우선
            "occ": dict(dlt) if dlt else dict(occ or {}),
            "sync_t": float(sync_keep),
            "dedupe": str(dedupe or ""),
            "wall_applied": bool(wall_applied)
            or bool(isinstance(prev, dict) and prev.get("wall_applied")),
        }
        if isinstance(milestone_occ, dict) and milestone_occ:
            entry["milestone_occ"] = {
                str(k).strip().upper(): str(v or "")
                for k, v in milestone_occ.items()
                if str(k or "").strip()
            }
        elif isinstance(prev, dict) and isinstance(prev.get("milestone_occ"), dict):
            entry["milestone_occ"] = dict(prev["milestone_occ"])
        by[str(int(screen))] = entry
    except Exception:
        pass


def _occ_delta_for_anim_src(src: Dict[str, Any]) -> Dict[str, str]:
    """ARRIVED/MOVE/REMOVED 가 바꾸는 포트만 (전체 패널 스냅샷 아님)."""
    try:
        from .control_sim_prerun_playback import (
            _canonical_sim_port_key,
            _normalize_anim_event_seq,
            _s_val,
            repair_anim_src_ports,
        )
    except Exception:
        return {}
    src_f = repair_anim_src_ports(dict(src or {}))
    ev = _normalize_anim_event_seq(
        _s_val(src_f.get("event") or src_f.get("event_seq") or src_f.get("seq"))
    )
    lot = _s_val(src_f.get("lot_id"))
    fr = _canonical_sim_port_key(_s_val(src_f.get("from_port_id")))
    to = _canonical_sim_port_key(_s_val(src_f.get("to_port_id")))
    port = _canonical_sim_port_key(_s_val(src_f.get("port_id") or src_f.get("event_port_id")))
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
    # BP→EP delta 가 EP 에 lot 을 올렸으면 동일 lot INOUT 잔상 제거
    try:
        for k, v in (delta or {}).items():
            ku = str(k).strip().upper()
            lot = str(v or "").strip()
            if ku.startswith("EP") and lot and str(out.get("INOUT") or "").strip() == lot:
                out["INOUT"] = ""
    except Exception:
        pass
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


def _resolve_renewal_panel_occ_for_wall(
    ext: Any,
    screen: int,
    snap: PlaybackPlanSnapshot,
    src: Dict[str, Any],
    sim_ref: float,
) -> Tuple[Optional[float], Optional[Dict[str, str]]]:
    """
    renewal wall 패널 occ — schedule step ``renewal_full_panel_occ`` 우선.

    milestone search 만 쓰면 EP1 lot 유지·EP2 오염 등 partial occ 로 깜빡일 수 있다.
    """
    scr = int(screen)
    src_r = dict(src)
    sync_t: Optional[float] = None
    occ: Optional[Dict[str, str]] = None

    sched = get_stored_playback_schedule_for_screen(ext, scr)
    step = _find_wall_renewal_step(sched, src_r)
    if step is not None:
        try:
            from .playback_renewal_ports import (
                renewal_full_panel_occ_for_step,
                renewal_playback_port_sync_for_step,
            )

            st = step.t_playback_port_sync
            if st is None:
                st = renewal_playback_port_sync_for_step(step)
            if st is not None and float(st) > 1e-9:
                sync_t = float(st)

            base_occ: Optional[Dict[str, str]] = None
            try:
                t_evt = float(step.t_event or 0.0)
                if t_evt > 1e-9:
                    base_occ = dict(snap.ports_at(max(0.0, t_evt - 1e-4)))
            except Exception:
                base_occ = None

            occ_step = renewal_full_panel_occ_for_step(step, base_occ=base_occ)
            if isinstance(occ_step, dict) and occ_step:
                occ = _ensure_panel_occ_keys(dict(occ_step))
        except Exception:
            sync_t = None
            occ = None

    if occ is None:
        mile = _find_plan_renewal_milestone_for_event(snap, src_r, float(sim_ref))
        if mile is not None:
            try:
                if sync_t is None or float(sync_t) <= 1e-9:
                    sync_t = float(mile[0])
                occ = _ensure_panel_occ_keys(dict(mile[1]))
            except Exception:
                pass

    if sync_t is None or float(sync_t) <= 1e-9:
        try:
            sync_t = _resolve_renewal_sync_t_for_playback(ext, scr, src_r)
        except Exception:
            sync_t = None

    return sync_t, occ


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
    """레거시 no-op — renewal 포트는 plan lookup adjust + wall 직접 apply 만."""
    del ext, screen
    return _ensure_panel_occ_keys(dict(plan_occ)), float(sim_now)


def _dedupe_panel_lot_ghosts(occ: Dict[str, str]) -> Dict[str, str]:
    """같은 LOT 이 여러 포트에 보이면 EP > BP > INOUT 우선으로 하나만 남긴다."""
    out = dict(occ or {})
    by_lot: Dict[str, List[str]] = {}
    for k, v in out.items():
        lid = str(v or "").strip()
        ku = str(k or "").strip().upper()
        if not lid or not ku:
            continue
        by_lot.setdefault(lid, []).append(ku)
    for lid, ports in by_lot.items():
        if len(ports) <= 1:
            continue
        best = ""
        for p in ports:
            if p.startswith("EP"):
                best = p
                break
        if not best:
            for p in ports:
                if p.startswith("BP"):
                    best = p
                    break
        if not best:
            best = ports[0]
        for p in ports:
            if p != best:
                out[p] = ""
    return out


def _renewal_panel_occ_if_applied(ext: Any, screen: int) -> Optional[Dict[str, str]]:
    """
    renewal wall 적용 후 패널 occ SSOT.

    active job dedupe 가 heartbeat 마다 어긋나도 화면 applied + panel store 를 우선한다.
    JSON wall 해제 후 공정 대기까지 유지 (다음 gated event 시작 시 reset).
    """
    scr = int(screen)
    if not _renewal_wall_applied_for_screen(ext, scr):
        return None
    rec = _renewal_panel_record(ext, scr)
    if not isinstance(rec, dict):
        return None
    try:
        by = getattr(ext, "_sim_playback_renewal_applied_by_screen", None)
        if isinstance(by, dict):
            applied = str(by.get(str(scr), "") or "")
            stored = str(rec.get("dedupe", "") or "")
            if applied and stored and applied != stored:
                return None
    except Exception:
        pass
    occ = rec.get("occ")
    if isinstance(occ, dict) and occ:
        return _ensure_panel_occ_keys(dict(occ))
    return None


def _playback_ports_at_sim(
    ext: Any,
    snap: PlaybackPlanSnapshot,
    screen: int,
    t_sim: float,
) -> Dict[str, str]:
    """재생 포트 SSOT = plan ``ports_at(t)`` + renewal 적용 후 pinned occ."""
    pinned = _renewal_panel_occ_if_applied(ext, int(screen))
    if pinned is not None:
        return dict(pinned)
    return _ensure_panel_occ_keys(dict(snap.ports_at(float(t_sim))))


def patch_parallel_rail_port_hold_pre(
    ext: Any,
    screen: int,
    delta: Dict[str, str],
    *,
    prefer_rail: str = "",
) -> None:
    """renewal delta 키를 활성 rail hold ``pre`` 에 반영 — hold 가 delta 를 되돌리지 않게."""
    try:
        from .control_sim_playback_gate import is_json_wall_busy
        from .sim_parallel_rails import parallel_moves_enabled, rail_queue_key

        if not parallel_moves_enabled():
            return
        if not isinstance(delta, dict) or not delta:
            return
        by = _parallel_port_hold_map(ext)
        scr = int(screen)
        rails = []
        pr = str(prefer_rail or "").strip().lower()
        if pr in ("oht", "move"):
            rails.append(pr)
        for r in ("oht", "move"):
            if r not in rails:
                rails.append(r)
        for r in rails:
            if not is_json_wall_busy(ext, scr, rail=r):
                continue
            hold = by.get(rail_queue_key(scr, r))
            if not isinstance(hold, dict):
                continue
            pre = hold.get("pre")
            if not isinstance(pre, dict):
                continue
            patched = False
            for k, v in delta.items():
                ku = str(k or "").strip().upper()
                if not ku:
                    continue
                if ku in pre:
                    pre[ku] = str(v or "")
                    patched = True
            if patched:
                hold["pre"] = dict(pre)
                by[rail_queue_key(scr, r)] = hold
                break
    except Exception:
        pass


def _parallel_port_hold_map(ext: Any) -> Dict[str, Dict[str, Any]]:
    by = getattr(ext, "_sim_playback_parallel_port_hold_by_rail", None)
    if not isinstance(by, dict):
        by = {}
        try:
            ext._sim_playback_parallel_port_hold_by_rail = by
        except Exception:
            pass
    return by


def set_parallel_rail_port_hold(
    ext: Any,
    screen: int,
    rail: str,
    pre_ports: Dict[str, str],
    *,
    event_seq: str = "",
) -> None:
    """
    병렬 전용: JSON wall 동안 plan 이 앞서 가도 **이 이벤트 delta 포트**는
    emit 직전 값(pre)으로 패널에 고정한다. False 경로에서는 호출하지 않는다.
    """
    try:
        from .sim_parallel_rails import parallel_moves_enabled, rail_queue_key

        if not parallel_moves_enabled():
            return
        r = str(rail or "").strip().lower()
        if r not in ("oht", "move"):
            return
        pre: Dict[str, str] = {}
        for k, v in (pre_ports or {}).items():
            ku = str(k or "").strip().upper()
            if ku:
                pre[ku] = str(v or "")
        if not pre:
            return
        _parallel_port_hold_map(ext)[rail_queue_key(int(screen), r)] = {
            "pre": dict(pre),
            "rail": r,
            "event_seq": str(event_seq or ""),
            "screen": int(screen),
        }
    except Exception:
        pass


def clear_parallel_rail_port_hold(
    ext: Any, screen: int, rail: Optional[str] = None
) -> None:
    try:
        from .sim_parallel_rails import parallel_moves_enabled, rail_queue_key

        if not parallel_moves_enabled():
            return
        by = _parallel_port_hold_map(ext)
        scr = int(screen)
        if rail:
            by.pop(rail_queue_key(scr, str(rail)), None)
            return
        for r in ("oht", "move"):
            by.pop(rail_queue_key(scr, r), None)
    except Exception:
        pass


def apply_parallel_rail_port_holds(
    ext: Any, screen: int, occ: Dict[str, str]
) -> Dict[str, str]:
    """wall busy 인 레일의 hold pre 로 occ 키를 덮어쓴다."""
    out = dict(occ or {})
    try:
        from .control_sim_playback_gate import is_json_wall_busy
        from .sim_parallel_rails import parallel_moves_enabled, rail_queue_key

        if not parallel_moves_enabled():
            return out
        by = _parallel_port_hold_map(ext)
        scr = int(screen)
        for r in ("oht", "move"):
            if not is_json_wall_busy(ext, scr, rail=r):
                continue
            hold = by.get(rail_queue_key(scr, r))
            if not isinstance(hold, dict):
                continue
            pre = hold.get("pre")
            if not isinstance(pre, dict):
                continue
            for k, v in pre.items():
                ku = str(k or "").strip().upper()
                if ku:
                    out[ku] = str(v or "")
        # MOVE wall: BP 에 LOT 이 잡혀 있는데 INOUT 에 같은 LOT 잔상 → INOUT 비움
        # (ARRIVED∥MOVE 병렬 중 포트가 BP→INOUT 으로 "점프"해 보이는 것 방지)
        if is_json_wall_busy(ext, scr, rail="move"):
            hold_m = by.get(rail_queue_key(scr, "move"))
            pre_m = hold_m.get("pre") if isinstance(hold_m, dict) else None
            if isinstance(pre_m, dict):
                for bk in ("BP1", "BP2", "BP3", "BP4"):
                    lot_bp = str(pre_m.get(bk) or out.get(bk) or "").strip()
                    if lot_bp and str(out.get("INOUT") or "").strip() == lot_bp:
                        out["INOUT"] = ""
                        break
    except Exception:
        pass
    return out


def capture_pre_ports_for_event_delta(
    ext: Any, screen: int, event_payload: Dict[str, Any]
) -> Dict[str, str]:
    """emit 직전 — 이 이벤트가 바꿀 포트의 현재 패널/스냅샷 값."""
    try:
        delta = _occ_delta_for_anim_src(dict(event_payload or {}))
    except Exception:
        delta = {}
    if not delta:
        return {}
    last: Dict[str, str] = {}
    try:
        last_by = getattr(ext, "_sim_last_ports_occupancy_by_screen", None)
        raw = last_by.get(str(int(screen))) if isinstance(last_by, dict) else None
        if isinstance(raw, dict):
            last = {str(k).strip().upper(): str(v or "") for k, v in raw.items()}
    except Exception:
        last = {}
    # plan SSOT 우선 — last 패널에 병렬 잔상이 있으면 emit hold 가 고착화됨
    plan: Dict[str, str] = {}
    try:
        snap = get_plan_snapshot(ext, int(screen))
        if snap is not None:
            t_now = _sim_now_for_screen(ext, int(screen), None)
            # rail/renewal 없이 plan raw — hold 순환 방지
            plan = {
                str(k).strip().upper(): str(v or "")
                for k, v in dict(snap.ports_at(float(t_now))).items()
            }
    except Exception:
        plan = {}
    pre: Dict[str, str] = {}
    for k in delta.keys():
        ku = str(k).strip().upper()
        if not ku:
            continue
        if ku in plan:
            pre[ku] = str(plan.get(ku, "") or "")
        else:
            pre[ku] = str(last.get(ku, "") or "")
    return pre


# ── REMOVED JSON: 포트 패널은 renewal, 3D prim 숨김만 JSON 종료까지 보류 ─────

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


def _ensure_removed_prim_hide_holds_from_schedule(ext: Any, screen: int) -> None:
    """
    재생 plan EMPTY(renewal sync) 가 wall hold 등록보다 먼저 와도
    prim 이 한 프레임 꺼지지 않도록, 활성 REMOVED step 에 대해 hold 를 선등록한다.

    hold 구간: ``t_event <= sim_now < t_json_end`` (숨김 = JSON 종료).
    """
    if not bool(getattr(ext, "_sim_playback_started", False)):
        return
    sched = get_stored_playback_schedule_for_screen(ext, int(screen))
    if sched is None:
        return
    try:
        from .control_sim_prerun_playback import _normalize_anim_event_seq, _s_val
    except Exception:
        return
    try:
        sim_now = float(_sim_now_for_screen(ext, int(screen), None))
    except Exception:
        return
    holds = _removed_prim_hide_holds(ext, int(screen))
    for step in list(getattr(sched, "steps", None) or ()):
        try:
            if str(getattr(step, "kind", "") or "").strip().lower() != "json_step":
                continue
            p = getattr(step, "progress_payload", None)
            if not isinstance(p, dict):
                p = {}
            ev = _normalize_anim_event_seq(
                _s_val(
                    p.get("event")
                    or p.get("event_seq")
                    or getattr(step, "event_seq", None)
                    or ""
                )
            )
            if ev != "REMOVED":
                continue
            t0 = float(getattr(step, "t_event", 0.0) or 0.0)
            hide_end = 0.0
            for attr in ("t_json_end", "t_anim_end", "t_proc_end", "t_playback_json_end"):
                try:
                    v = float(getattr(step, attr, 0.0) or 0.0)
                except Exception:
                    v = 0.0
                if v > 1e-9:
                    hide_end = v
                    break
            if hide_end <= 1e-9:
                continue
            # JSON 시작 전부터 선등록하면 조기 EMPTY 에도 버팀. JSON 끝나면 만료.
            if float(sim_now) + 1e-6 >= float(hide_end):
                continue
            if float(sim_now) + 0.05 < float(t0):
                # 아직 해당 REMOVED 이벤트 전이면 스킵 (너무 이른 선등록 방지)
                continue
            port = _canon_port(
                p.get("port_id")
                or p.get("event_port_id")
                or p.get("to_port_id")
                or getattr(step, "port_id", None)
            )
            lot = str(p.get("lot_id") or getattr(step, "lot_id", "") or "").strip()
            if not port or not lot:
                continue
            prev = holds.get(str(port))
            # 더 긴(정확한) hide_end 로 갱신
            if isinstance(prev, dict):
                try:
                    prev_end = float(prev.get("proc_end_t", 0.0) or 0.0)
                except Exception:
                    prev_end = 0.0
                if prev_end + 1e-6 >= float(hide_end) and str(prev.get("lot") or "") == lot:
                    continue
            holds[str(port)] = {"lot": lot, "proc_end_t": float(hide_end)}
        except Exception:
            continue


def _ensure_removed_prim_hide_hold_from_active_job(ext: Any, screen: int) -> None:
    """라이브·재생 공통 — 화면 active REMOVED JSON 이 있으면 hold 선등록."""
    try:
        from .control_window import _screen_active_json_job
    except Exception:
        return
    try:
        job = _screen_active_json_job(ext, int(screen))
    except Exception:
        job = None
    if not isinstance(job, dict):
        return
    try:
        from .control_sim_prerun_playback import _normalize_anim_event_seq, _s_val

        ev = _normalize_anim_event_seq(
            _s_val(job.get("event") or job.get("event_seq") or job.get("seq") or "")
        )
    except Exception:
        ev = str(job.get("event") or job.get("event_seq") or "").strip().upper()
    if ev != "REMOVED":
        return
    sched = get_stored_playback_schedule_for_screen(ext, int(screen))
    src = dict(job)
    if src.get("event_start_sim_time") in (None, ""):
        t0 = src.get("_event_start_sim") or src.get("t") or src.get("sim_time")
        if t0 is not None:
            src["event_start_sim_time"] = str(t0)
    _register_removed_prim_hide_hold_for_renewal(ext, int(screen), src, sched)


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

    plan EMPTY(renewal) 가 hold 등록보다 먼저 와도 깜빡이지 않도록
    schedule/active-job 에서 hold 를 먼저 확보한다.
    """
    try:
        _ensure_removed_prim_hide_holds_from_schedule(ext, int(screen))
    except Exception:
        pass
    try:
        _ensure_removed_prim_hide_hold_from_active_job(ext, int(screen))
    except Exception:
        pass
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


def _active_gated_event_src(
    ext: Any, screen: int, rail: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """현재 화면(·레일)에서 JSON wall 로 묶인 gated 이벤트 src/job."""
    scr = int(screen)
    try:
        bya = getattr(ext, "_sim_anim_active_by_screen", None)
        if isinstance(bya, dict):
            from .sim_parallel_rails import (
                anim_state_key,
                parallel_moves_enabled,
            )

            keys: List[str] = []
            if parallel_moves_enabled():
                r = str(rail or "").strip().lower()
                if r in ("oht", "move"):
                    keys.append(anim_state_key(scr, r))
                else:
                    keys.extend(
                        [
                            anim_state_key(scr, "oht"),
                            anim_state_key(scr, "move"),
                            str(scr),
                        ]
                    )
            else:
                keys.append(str(scr))
            for k in keys:
                cand = bya.get(k)
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
    화면별 **공정 경계** sim 시각 (진행률·시계 cap SSOT).

    - wall/proc_gate / 미emit gated 의 ``t0+proc``
    - 병렬: 레일 경계 **max** (A∥B 시계 공유)
    - 직렬: **min** + hold_t

    plan 포트 lookup 은 ``playback_plan_frontier_sim`` (항상 min) 을 사용한다.
    """
    candidates = _collect_playback_frontier_candidates(ext, int(screen))
    if not candidates:
        return None
    parallel = False
    try:
        from .sim_parallel_rails import parallel_moves_enabled

        parallel = bool(parallel_moves_enabled())
    except Exception:
        parallel = False
    if parallel:
        return float(max(candidates))
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


# 레거시 이름 — 공정 경계 SSOT (plan 은 ``playback_plan_frontier_sim``)
def _active_json_plan_cap_sim(ext: Any, screen: int) -> Optional[float]:
    return playback_plan_frontier_sim(ext, int(screen))


def resolve_playback_ui_axes(
    ext: Any,
    screen: int,
    t_sim: Optional[float] = None,
    *,
    explicit: bool = False,
) -> PlaybackUIAxes:
    """
    재생 UI 시각 축 — ``t_display``·``t_plan`` raw ``sim_now`` (Seek explicit 제외).

    plan 포트 lookup 은 ``resolve_playback_ui_at_sim`` 에서 ``playback_plan_lookup_sim_t`` 로 cap.
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
        axes = resolve_playback_ui_axes(ext, int(screen), float(t_sim))
        t_lookup = playback_plan_lookup_sim_t(ext, int(screen), float(axes.t_plan))
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
    t_lookup = playback_plan_lookup_sim_t(ext, int(screen), float(axes.t_plan))
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
    try:
        ext._sim_playback_parallel_port_hold_by_rail = {}
    except Exception:
        pass
    try:
        applied_by = getattr(ext, "_sim_playback_renewal_applied_by_screen", None)
        if isinstance(applied_by, dict):
            applied_by.clear()
    except Exception:
        pass
    try:
        pan_by = getattr(ext, "_sim_playback_renewal_panel_by_screen", None)
        if isinstance(pan_by, dict):
            pan_by.clear()
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
    if bool(explicit):
        t_lookup = float(axes.t_plan)
    else:
        t_lookup = playback_plan_lookup_sim_t(ext, scr, float(axes.t_plan))
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
    재생 — LAM renewal wall 콜백.

    plan milestone occ 를 패널에 1회 적용 + applied 기록 (현재 job dedupe).
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

    if _renewal_debug_on(ext):
        try:
            sim_now = _sim_now_for_screen(ext, scr, None)
        except Exception:
            sim_now = -1.0
        print(
            f"[RENEWAL_DBG] scr={scr} PLAN_ONLY ev={src.get('event')} "
            f"to={src.get('to_port_id') or src.get('port_id')} lot={src.get('lot_id')} "
            f"sim_now={float(sim_now):.2f} (no live delta hold)",
            flush=True,
        )
        _dump_plan_milestones_once(ext, scr, snap)

    # REMOVED: 패널 EMPTY 는 plan renewal milestone, 3D 숨김은 JSON 종료까지
    try:
        _ensure_removed_prim_hide_holds_from_schedule(ext, scr)
    except Exception:
        pass
    _register_removed_prim_hide_hold_for_renewal(ext, scr, dict(src), sched)

    src_r = dict(src)
    sim_ref = float(_sim_now_for_screen(ext, scr, None))

    sync_t, occ_panel = _resolve_renewal_panel_occ_for_wall(ext, scr, snap, src_r, sim_ref)

    delta = _occ_delta_for_anim_src(src_r)
    del delta  # plan milestone SSOT — delta hold 미사용

    mark_playback_renewal_wall_applied(
        ext,
        scr,
        dict(src),
        sync_t=sync_t,
        occ=occ_panel if isinstance(occ_panel, dict) else None,
    )

    if isinstance(occ_panel, dict) and occ_panel:
        try:
            _apply_plan_ports_to_panel(
                ext,
                int(scr),
                dict(occ_panel),
                t_display=float(sim_ref),
            )
        except Exception:
            pass

    t_refresh: Optional[float] = (
        float(sync_t) if sync_t is not None and float(sync_t) > 1e-9 else None
    )
    refresh_playback_display_at_sim(
        ext,
        scr,
        t_refresh,
        force=True,
        explicit=bool(t_refresh is not None and float(t_refresh) > 1e-9),
    )
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
        sk = str(int(scr))
        occ = dict(state.ports or {})
        last_by = getattr(ext, "_sim_last_ports_occupancy_by_screen", None)
        last = last_by.get(sk) if isinstance(last_by, dict) else None
        if bool(force) or not (isinstance(last, dict) and _occ_dicts_equal(dict(last), occ)):
            try:
                _apply_plan_ports_to_panel(
                    ext,
                    int(scr),
                    occ,
                    t_display=float(state.axes.t_display),
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
    "apply_parallel_rail_port_holds",
    "patch_parallel_rail_port_hold_pre",
    "apply_playback_frontier",
    "apply_playback_renewal_from_wall",
    "capture_pre_ports_for_event_delta",
    "clear_parallel_rail_port_hold",
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
    "playback_plan_frontier_sim",
    "playback_plan_lookup_sim_t",
    "playback_process_frontier_sim",
    "prim_occ_for_playback_visibility",
    "refresh_playback_display_at_sim",
    "rebuild_plan_snapshot_for_screen",
    "reset_plan_replay_floor",
    "reset_playback_renewal_runtime",
    "resolve_playback_ui_at_sim",
    "resolve_playback_ui_axes",
    "seek_playback_ui_at_sim",
    "set_parallel_rail_port_hold",
    "set_plan_replay_floor",
    "sync_playback_ui_at_renewal",
    "sync_playback_ui_at_sim",
]
