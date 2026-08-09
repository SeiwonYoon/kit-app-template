"""
진행현황 패널 — 화면별 단일 표시 상태(ProgressStepState + AnimRuntimeState).

엔진 progress payload, JSON 러너, heartbeat 가 각자 캐시를 갱신하며 어긋나는 문제를
한 곳에서 조립한다. 시뮬 엔진·JSON 실행 로직은 건드리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

_PLAYBACK_TICK_STRIP_KEYS = (
    "ports_occupancy",
    "ep_occ",
    "all_ep_empty",
    "foup_proc_active_ep",
)


@dataclass
class ProgressStepState:
    """진행현황 '현재 공정 단계' — UI 첫 줄(이벤트 연계 JSON)의 유일한 출처."""

    step_id: int = 0
    display_rev: int = 0
    screen: int = 1
    event_seq: str = ""
    linked_anim_json: str = ""
    label: str = ""
    detail: str = ""
    status: str = "RUNNING"
    sim_time: str = "0.00"
    elapsed: str = "0.0"
    total: str = "0.0"
    percent: str = "0"
    proc_sec: str = ""
    anim_sec: str = ""
    process_time_priority: str = ""
    event_start_sim_time: str = ""
    sim_total_est_sec: str = ""
    payload_snapshot: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AnimRuntimeState:
    """JSON 러너 보조 표시(두 번째 줄) — linked_anim_json 을 덮어쓰지 않는다."""

    phase: str = "idle"  # idle | queued | playing
    current_file: str = ""
    queue_len: int = 0
    next_file: str = ""


def _screen_key(screen: int) -> str:
    return str(max(1, int(screen)))


def _steps_map(ext: Any) -> Dict[str, ProgressStepState]:
    raw = getattr(ext, "_sim_progress_step_by_screen", None)
    if not isinstance(raw, dict):
        raw = {}
        try:
            ext._sim_progress_step_by_screen = raw
        except Exception:
            pass
    return raw


def _anim_map(ext: Any) -> Dict[str, AnimRuntimeState]:
    raw = getattr(ext, "_sim_anim_runtime_by_screen", None)
    if not isinstance(raw, dict):
        raw = {}
        try:
            ext._sim_anim_runtime_by_screen = raw
        except Exception:
            pass
    return raw


def clear_progress_step_state(ext: Any) -> None:
    try:
        ext._sim_progress_step_by_screen = {}
        ext._sim_progress_step_secondary_by_screen = {}
        ext._sim_anim_runtime_by_screen = {}
    except Exception:
        pass


def get_progress_step(ext: Any, screen: int) -> ProgressStepState:
    sk = _screen_key(screen)
    st = _steps_map(ext).get(sk)
    if isinstance(st, ProgressStepState):
        return st
    st = ProgressStepState(screen=int(sk))
    _steps_map(ext)[sk] = st
    return st


def get_anim_runtime(ext: Any, screen: int) -> AnimRuntimeState:
    sk = _screen_key(screen)
    ar = _anim_map(ext).get(sk)
    if isinstance(ar, AnimRuntimeState):
        return ar
    ar = AnimRuntimeState()
    _anim_map(ext)[sk] = ar
    return ar


def _basename_json(path_or_name: str) -> str:
    s = str(path_or_name or "").strip().replace("\\", "/")
    if not s:
        return ""
    return Path(s).name


def _step_identity_tuple(st: ProgressStepState) -> Tuple[str, str, str, str]:
    return (
        str(st.event_seq or "").strip().upper(),
        str(st.linked_anim_json or "").strip().lower(),
        str(st.label or "").strip(),
        str(st.event_start_sim_time or "").strip(),
    )


def _payload_to_step_fields(payload: Dict[str, Any]) -> Dict[str, str]:
    p = payload if isinstance(payload, dict) else {}
    return {
        "event_seq": str(p.get("event_seq") or p.get("sequence_name") or "").strip(),
        "linked_anim_json": str(p.get("linked_anim_json") or "").strip(),
        "label": str(p.get("label", "") or "").strip(),
        "detail": str(p.get("detail", "") or "").strip(),
        "status": str(p.get("status", "RUNNING") or "RUNNING").strip(),
        "sim_time": str(p.get("sim_time", "0.00") or "0.00").strip(),
        "elapsed": str(p.get("elapsed", "0.0") or "0.0").strip(),
        "total": str(p.get("total", "0.0") or "0.0").strip(),
        "percent": str(p.get("percent", "0") or "0").strip(),
        "proc_sec": str(p.get("proc_sec", "") or "").strip(),
        "anim_sec": str(p.get("anim_sec", "") or "").strip(),
        "process_time_priority": str(p.get("process_time_priority", "") or "").strip(),
        "event_start_sim_time": str(p.get("event_start_sim_time") or "").strip(),
        "sim_total_est_sec": str(p.get("sim_total_est_sec", "") or "").strip(),
    }


def _merge_snapshot(st: ProgressStepState, payload: Dict[str, Any]) -> None:
    snap = dict(st.payload_snapshot) if isinstance(st.payload_snapshot, dict) else {}
    if isinstance(payload, dict):
        snap.update(payload)
    if st.linked_anim_json:
        snap["linked_anim_json"] = st.linked_anim_json
    if st.event_seq:
        snap["event_seq"] = st.event_seq
    st.payload_snapshot = snap


def _steps_map_secondary(ext: Any) -> Dict[str, ProgressStepState]:
    by = getattr(ext, "_sim_progress_step_secondary_by_screen", None)
    if not isinstance(by, dict):
        by = {}
        try:
            ext._sim_progress_step_secondary_by_screen = by
        except Exception:
            pass
    return by


def clear_progress_step_secondary(ext: Any, screen: int) -> None:
    """보조(MOVE) ProgressStepState 를 idle 로 비운다."""
    try:
        sec = get_progress_step_secondary(ext, int(screen))
        sec.label = ""
        sec.detail = ""
        sec.event_seq = ""
        sec.linked_anim_json = ""
        sec.percent = ""
        sec.elapsed = ""
        sec.total = ""
        sec.proc_sec = ""
        sec.anim_sec = ""
        sec.event_start_sim_time = ""
        sec.status = "RUNNING"
        sec.payload_snapshot = {}
        sec.display_rev += 1
    except Exception:
        pass


def get_progress_step_secondary(ext: Any, screen: int) -> ProgressStepState:
    m = _steps_map_secondary(ext)
    sk = _screen_key(screen)
    st = m.get(sk)
    if isinstance(st, ProgressStepState):
        return st
    st = ProgressStepState(screen=int(sk))
    m[sk] = st
    return st


def apply_engine_progress_payload(ext: Any, screen: int, payload: Dict[str, Any]) -> bool:
    """
    엔진/타임라인 progress → ProgressStepState 갱신.

    FOUP_PROCESS·timeline_only·playback_time_tick 은 False (메인 단계 미변경).
    병렬 모드: sim_rail=move 는 보조(주=OHT ARRIVED/REMOVED) ProgressStepState 에 기록.
    """
    p = payload if isinstance(payload, dict) else {}
    if str(p.get("timeline_only", "")).strip().lower() in ("1", "true", "on"):
        return False
    if str(p.get("playback_time_tick", "")).strip().lower() in ("1", "true", "on"):
        return False
    ev_u = str(p.get("event_seq") or p.get("sequence_name") or "").strip().upper()
    if ev_u == "FOUP_PROCESS":
        return False
    label = str(p.get("label", "") or "").strip()
    if not label:
        return False

    use_secondary = False
    try:
        from .sim_parallel_rails import classify_sim_rail, parallel_moves_enabled

        if parallel_moves_enabled():
            rail = str(p.get("sim_rail") or "").strip().lower()
            if not rail:
                rail = classify_sim_rail(ev_u) or ""
            use_secondary = rail == "move"
    except Exception:
        use_secondary = False

    st = (
        get_progress_step_secondary(ext, int(screen))
        if use_secondary
        else get_progress_step(ext, int(screen))
    )
    fields = _payload_to_step_fields(p)
    new_id = _step_identity_tuple(
        ProgressStepState(
            event_seq=fields["event_seq"],
            linked_anim_json=fields["linked_anim_json"],
            label=fields["label"],
            event_start_sim_time=fields["event_start_sim_time"],
        )
    )
    old_id = _step_identity_tuple(st)
    if new_id != old_id:
        st.step_id += 1
    if fields["linked_anim_json"] and fields["linked_anim_json"] != st.linked_anim_json:
        st.display_rev += 1

    for k, v in fields.items():
        setattr(st, k, v)
    _merge_snapshot(st, p)
    return True


def bind_linked_anim_on_dispatch(
    ext: Any,
    screen: int,
    file_name: str,
    *,
    event_seq: str = "",
    sim_time: str = "",
    sim_rail: str = "",
) -> None:
    """JSON 매핑·큐 적재·시작 시점 — progress emit 전에도 연계 파일명을 고정한다.

    병렬: MOVE 레일은 secondary ProgressStepState 에 bind (주 슬롯 오염 방지).
    """
    bn = _basename_json(file_name)
    if not bn:
        return
    use_secondary = False
    try:
        from .sim_parallel_rails import classify_sim_rail, parallel_moves_enabled

        if parallel_moves_enabled():
            rail = str(sim_rail or "").strip().lower()
            if rail not in ("oht", "move"):
                rail = classify_sim_rail(str(event_seq or "")) or ""
            use_secondary = rail == "move"
    except Exception:
        use_secondary = False
    st = (
        get_progress_step_secondary(ext, int(screen))
        if use_secondary
        else get_progress_step(ext, int(screen))
    )
    ev = str(event_seq or st.event_seq or "").strip()
    if ev and ev.upper() != str(st.event_seq or "").strip().upper():
        st.event_seq = ev
    if bn.lower() != str(st.linked_anim_json or "").strip().lower():
        st.linked_anim_json = bn
        st.display_rev += 1
        st.step_id += 1
    if sim_time:
        st.sim_time = str(sim_time)
    _merge_snapshot(st, st.payload_snapshot)
    if isinstance(st.payload_snapshot, dict):
        st.payload_snapshot["linked_anim_json"] = bn
        if ev:
            st.payload_snapshot["event_seq"] = ev


def sync_anim_runtime_from_ext(ext: Any, screen: int) -> AnimRuntimeState:
    """SequenceRunner / pending 큐 → AnimRuntimeState (표시 보조만)."""
    sk = _screen_key(screen)
    ar = get_anim_runtime(ext, int(screen))
    running = False
    cur_file = ""
    q = 0
    next_f = ""
    _par = False
    try:
        from .sim_parallel_rails import (
            anim_state_key,
            parallel_moves_enabled,
            rail_queue_key,
        )

        _par = bool(parallel_moves_enabled())
        if _par:
            runners = getattr(ext, "_sim_runners_by_screen_rail", None)
            if isinstance(runners, dict):
                for rk in (
                    rail_queue_key(int(screen), "oht"),
                    rail_queue_key(int(screen), "move"),
                ):
                    rr = runners.get(rk)
                    if rr is not None and bool(
                        getattr(rr, "is_running", lambda: False)()
                    ):
                        running = True
                        break
        if not running:
            runners = getattr(ext, "_sim_runners_by_screen", None)
            rr = runners.get(sk) if isinstance(runners, dict) else None
            if rr is None and int(screen) == 1:
                rr = getattr(ext, "_sim_runner", None)
            if rr is not None:
                running = bool(getattr(rr, "is_running", lambda: False)())
    except Exception:
        running = False
    active_by = getattr(ext, "_sim_anim_active_by_screen", None)
    act = None
    if isinstance(active_by, dict):
        if _par:
            try:
                from .sim_parallel_rails import anim_state_key

                for rk in (
                    anim_state_key(int(screen), "oht"),
                    anim_state_key(int(screen), "move"),
                    sk,
                ):
                    cand = active_by.get(rk)
                    if isinstance(cand, dict) and cand:
                        act = cand
                        break
            except Exception:
                act = active_by.get(sk)
        else:
            act = active_by.get(sk)
    if not isinstance(act, dict) and int(screen) == 1:
        leg = getattr(ext, "_sim_anim_active", None)
        act = leg if isinstance(leg, dict) else None
    if isinstance(act, dict):
        cur_file = _basename_json(str(act.get("file", "") or ""))
    pend_by = getattr(ext, "_sim_anim_pending_by_screen", None)
    if isinstance(pend_by, dict) and _par:
        try:
            from .sim_parallel_rails import anim_state_key

            first = None
            for rk in (
                anim_state_key(int(screen), "oht"),
                anim_state_key(int(screen), "move"),
                sk,
            ):
                pl = pend_by.get(rk)
                if isinstance(pl, list):
                    q += len(pl)
                    if first is None and pl and isinstance(pl[0], dict):
                        first = pl[0]
            if first is not None:
                next_f = _basename_json(str(first.get("file", "") or ""))
        except Exception:
            pass
    else:
        plist = pend_by.get(sk) if isinstance(pend_by, dict) else None
        if not isinstance(plist, list):
            pend = getattr(ext, "_sim_anim_pending", None)
            plist = pend if isinstance(pend, list) else []
        if isinstance(plist, list):
            q = len(plist)
            if plist and isinstance(plist[0], dict):
                next_f = _basename_json(str(plist[0].get("file", "") or ""))
    if running and cur_file:
        ar.phase = "playing"
        ar.current_file = cur_file
    elif q > 0 and next_f:
        ar.phase = "queued"
        ar.current_file = cur_file
        ar.next_file = next_f
    else:
        ar.phase = "idle"
        ar.current_file = cur_file
    ar.queue_len = int(q)
    if not ar.next_file:
        ar.next_file = next_f
    return ar


def notify_anim_queued(
    ext: Any,
    screen: int,
    file_name: str,
    queue_len: int,
    next_file: str = "",
    *,
    sim_rail: str = "",
    event_seq: str = "",
) -> None:
    bind_linked_anim_on_dispatch(
        ext, screen, file_name, sim_rail=sim_rail, event_seq=event_seq
    )
    ar = get_anim_runtime(ext, int(screen))
    ar.phase = "queued"
    ar.queue_len = max(0, int(queue_len))
    ar.next_file = _basename_json(next_file or file_name)
    sync_anim_runtime_from_ext(ext, screen)


def notify_anim_started(
    ext: Any,
    screen: int,
    file_name: str,
    *,
    sim_rail: str = "",
    event_seq: str = "",
) -> None:
    rail = str(sim_rail or "").strip().lower()
    if not rail:
        try:
            from .sim_parallel_rails import classify_sim_rail, parallel_moves_enabled

            if parallel_moves_enabled():
                bn = _basename_json(file_name).lower()
                if "move" in bn:
                    rail = "move"
                elif "arrived" in bn or "removed" in bn:
                    rail = "oht"
                else:
                    rail = classify_sim_rail(event_seq) or ""
        except Exception:
            rail = ""
    bind_linked_anim_on_dispatch(
        ext, screen, file_name, sim_rail=rail, event_seq=event_seq
    )
    ar = get_anim_runtime(ext, int(screen))
    ar.phase = "playing"
    ar.current_file = _basename_json(file_name)
    sync_anim_runtime_from_ext(ext, screen)


def notify_anim_finished(ext: Any, screen: int) -> None:
    sync_anim_runtime_from_ext(ext, screen)
    ar = get_anim_runtime(ext, int(screen))
    if ar.queue_len <= 0:
        ar.phase = "idle"
        if not ar.next_file:
            ar.current_file = ""


def build_payload_from_step(ext: Any, screen: int) -> Optional[Dict[str, Any]]:
    st = get_progress_step(ext, int(screen))
    if not str(st.label or "").strip():
        return None
    p = dict(st.payload_snapshot) if isinstance(st.payload_snapshot, dict) else {}
    p["tbs_sim_screen"] = _screen_key(screen)
    p["label"] = st.label
    p["detail"] = st.detail
    p["status"] = st.status
    p["sim_time"] = st.sim_time
    p["elapsed"] = st.elapsed
    p["total"] = st.total
    p["percent"] = st.percent
    p["event_seq"] = st.event_seq
    p["linked_anim_json"] = st.linked_anim_json
    p["proc_sec"] = st.proc_sec
    p["anim_sec"] = st.anim_sec
    p["process_time_priority"] = st.process_time_priority
    p["event_start_sim_time"] = st.event_start_sim_time
    if st.sim_total_est_sec:
        p["sim_total_est_sec"] = st.sim_total_est_sec
    p["_progress_step_id"] = int(st.step_id)
    p["_progress_display_rev"] = int(st.display_rev)
    # 병렬: 주(OHT) + 보조(MOVE) 줄
    try:
        from .sim_parallel_rails import parallel_moves_enabled

        if parallel_moves_enabled():
            sec = get_progress_step_secondary(ext, int(screen))
            if str(sec.label or "").strip():
                p["secondary_label"] = str(sec.label)
                p["secondary_percent"] = str(sec.percent or "")
                p["secondary_detail"] = str(sec.detail or "")
                p["secondary_linked_anim_json"] = str(sec.linked_anim_json or "")
                det0 = str(p.get("detail") or "").rstrip()
                sec_line = (
                    f"[보조/MOVE] {sec.label} | {sec.percent}% | {sec.linked_anim_json or '-'}"
                )
                p["detail"] = (det0 + "\n" + sec_line) if det0 else sec_line
    except Exception:
        pass
    return p


def interpolate_playback_fields(
    p: Dict[str, Any],
    tnow: float,
    *,
    ext: Any = None,
    screen: int = 1,
    apply_step_progress: Optional[Callable[..., None]] = None,
) -> None:
    """heartbeat — 현재 step_id 고정, sim_time·elapsed·percent 만 보간."""
    if apply_step_progress is not None:
        try:
            apply_step_progress(p, float(tnow), ext=ext, screen=int(screen))
        except Exception:
            pass


def _refresh_secondary_move_step_progress(ext: Any, screen: int, tnow: float) -> None:
    """병렬 MOVE(보조) ProgressStepState 의 elapsed/percent 를 sim_now 로 갱신."""
    try:
        from .control_sim_playback_gate import is_rail_json_occupying
        from .sim_parallel_rails import anim_state_key, parallel_moves_enabled

        if not parallel_moves_enabled():
            return
        # MOVE 레일 idle 이면 보조 줄 제거 (stale 표시 방지)
        if not is_rail_json_occupying(ext, int(screen), "move"):
            clear_progress_step_secondary(ext, int(screen))
            return
        sec = get_progress_step_secondary(ext, int(screen))
        if not (
            str(sec.label or "").strip() or str(sec.linked_anim_json or "").strip()
        ):
            return
        t0 = 0.0
        proc = 0.0
        try:
            t0 = float(str(sec.event_start_sim_time or "").strip() or "0")
        except Exception:
            t0 = 0.0
        try:
            proc = float(str(sec.proc_sec or "").strip() or "0")
        except Exception:
            proc = 0.0
        if proc <= 1e-9:
            try:
                proc = float(str(sec.total or "").strip() or "0")
            except Exception:
                proc = 0.0
        active_by = getattr(ext, "_sim_anim_active_by_screen", None)
        if isinstance(active_by, dict):
            act = active_by.get(anim_state_key(int(screen), "move"))
            if isinstance(act, dict) and act:
                try:
                    at = float(str(act.get("t") or "").strip() or "0")
                except Exception:
                    at = 0.0
                try:
                    ap = float(str(act.get("proc_sec") or "").strip() or "0")
                except Exception:
                    ap = 0.0
                if at > 1e-9:
                    t0 = at
                    sec.event_start_sim_time = f"{at:.4f}"
                if ap > 1e-9:
                    proc = ap
                    sec.proc_sec = f"{ap:.1f}"
                lab = str(act.get("action") or act.get("label") or "").strip()
                if lab:
                    sec.label = lab
                jf = str(act.get("file") or "").strip()
                if jf:
                    sec.linked_anim_json = Path(jf).name if ("/" in jf or "\\" in jf) else jf
                ev = str(act.get("event") or act.get("event_seq") or "").strip()
                if ev:
                    sec.event_seq = ev
        if proc <= 1e-9:
            return
        el = max(0.0, min(float(proc), float(tnow) - float(t0)))
        pct = min(100.0, 100.0 * el / float(proc))
        sec.elapsed = f"{el:.1f}"
        sec.total = f"{float(proc):.1f}"
        sec.percent = str(int(pct))
        sec.sim_time = f"{float(tnow):.2f}"
    except Exception:
        pass


def build_playback_tick_payload(
    ext: Any,
    screen: int,
    tnow: float,
    *,
    final_sim_time: Optional[float] = None,
    apply_step_progress: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    """프리런 재생 heartbeat — lp 복사 대신 ProgressStepState 기준 보간."""
    scr = max(1, int(screen))
    st = get_progress_step(ext, scr)
    base = build_payload_from_step(ext, scr)
    if not isinstance(base, dict):
        base = {
            "tbs_sim_screen": _screen_key(scr),
            "label": "대기",
            "detail": "",
            "status": "RUNNING",
            "elapsed": "0.0",
            "total": "0.0",
            "percent": "0",
        }
    p3: Dict[str, Any] = dict(base)
    p3["sim_time"] = f"{float(tnow):.2f}"
    p3["playback_time_tick"] = "1"
    if isinstance(final_sim_time, (float, int)) and float(final_sim_time) > 0.0:
        p3["sim_total_est_sec"] = f"{float(final_sim_time):.2f}"
        st.sim_total_est_sec = p3["sim_total_est_sec"]
    interpolate_playback_fields(
        p3,
        float(tnow),
        ext=ext,
        screen=scr,
        apply_step_progress=apply_step_progress,
    )
    st.sim_time = str(p3.get("sim_time", st.sim_time))
    st.elapsed = str(p3.get("elapsed", st.elapsed))
    st.total = str(p3.get("total", st.total))
    st.percent = str(p3.get("percent", st.percent))
    # 병렬 보조(MOVE) % — 직렬형 본문 + 하단에 JSON|%만
    try:
        from .sim_parallel_rails import parallel_moves_enabled

        if parallel_moves_enabled():
            _refresh_secondary_move_step_progress(ext, scr, float(tnow))
            sec = get_progress_step_secondary(ext, scr)
            # 본문 detail 에 긴 [보조/MOVE] 줄을 넣지 않음 (UI 하단 한 줄만)
            det0 = str(p3.get("detail") or "").split("\n[보조/MOVE]")[0].rstrip()
            p3["detail"] = det0
            if str(sec.label or "").strip() or str(sec.linked_anim_json or "").strip():
                p3["secondary_label"] = str(sec.label or "")
                p3["secondary_percent"] = str(sec.percent or "")
                p3["secondary_detail"] = str(sec.detail or "")
                p3["secondary_linked_anim_json"] = str(sec.linked_anim_json or "")
                p3["secondary_elapsed"] = str(sec.elapsed or "")
                p3["secondary_total"] = str(sec.total or "")
            else:
                p3.pop("secondary_label", None)
                p3.pop("secondary_percent", None)
                p3.pop("secondary_detail", None)
                p3.pop("secondary_linked_anim_json", None)
                p3.pop("secondary_elapsed", None)
                p3.pop("secondary_total", None)
    except Exception:
        pass
    for k in _PLAYBACK_TICK_STRIP_KEYS:
        p3.pop(k, None)
    p3["_progress_step_id"] = int(st.step_id)
    p3["_progress_display_rev"] = int(st.display_rev)
    return p3


def _linked_json_exists_label(hint: str) -> str:
    bn = _basename_json(hint)
    if not bn:
        return "?"
    try:
        ext_root = Path(__file__).resolve().parents[2]
        for sub in (
            Path("data/sim_sequences") / bn,
            Path("data") / "sim_sequences" / bn,
            Path(bn),
        ):
            if (ext_root / sub).is_file():
                return "존재"
        return "없음"
    except Exception:
        return "?"


def format_progress_anim_footer(ext: Any, screen: int) -> str:
    """이벤트 연계 JSON + 애니 런타임 보조 줄 — ProgressStepState 단일 출처."""
    sync_anim_runtime_from_ext(ext, int(screen))
    st = get_progress_step(ext, int(screen))
    ar = get_anim_runtime(ext, int(screen))
    hint = _basename_json(st.linked_anim_json)
    if not hint:
        hint = _basename_json(
            str((st.payload_snapshot or {}).get("linked_anim_json", "") or "")
        )
    if not hint:
        if ar.phase == "playing" and ar.current_file:
            lines = [f"애니메이션 파일(재생 중): {ar.current_file}"]
            if ar.queue_len > 0 and ar.next_file:
                lines.append(f"대기열: {ar.queue_len}건 (다음 {ar.next_file})")
            return "\n".join(lines)
        if ar.queue_len > 0 and ar.next_file:
            return "애니메이션: 대기 — 다음 " + ar.next_file + (
                f" (큐 {ar.queue_len}건)" if ar.queue_len > 1 else ""
            )
        return "애니메이션 파일: 재생 없음"

    parts: list[str] = []
    ex_lbl = _linked_json_exists_label(hint)
    parts.append(f"이벤트 연계 JSON: {hint} ({ex_lbl})")

    hint_key = hint.lower()
    cur_key = ar.current_file.lower() if ar.current_file else ""
    next_key = ar.next_file.lower() if ar.next_file else ""

    if cur_key == hint_key and ar.phase == "playing":
        parts.append(f"애니메이션 파일(재생 중): {ar.current_file}")
        if ar.queue_len > 0 and ar.next_file:
            parts.append(f"대기열: {ar.queue_len}건 (다음 {ar.next_file})")
    elif ar.queue_len > 0 and next_key == hint_key:
        parts.append(
            "애니메이션: 대기 — 다음 "
            + ar.next_file
            + (f" (큐 {ar.queue_len}건)" if ar.queue_len > 1 else "")
        )
    elif ar.phase == "playing" and ar.current_file and cur_key != hint_key:
        parts.append(f"애니메이션 파일(재생 중): {ar.current_file}")
        if ar.queue_len > 0 and ar.next_file:
            parts.append(f"대기열: {ar.queue_len}건 (다음 {ar.next_file})")

    return "\n".join(parts)


def progress_dedupe_extra(payload: Dict[str, Any]) -> Tuple[int, int]:
    try:
        return (
            int(payload.get("_progress_step_id", 0) or 0),
            int(payload.get("_progress_display_rev", 0) or 0),
        )
    except Exception:
        return (0, 0)


__all__ = [
    "AnimRuntimeState",
    "ProgressStepState",
    "apply_engine_progress_payload",
    "bind_linked_anim_on_dispatch",
    "build_payload_from_step",
    "build_playback_tick_payload",
    "clear_progress_step_secondary",
    "clear_progress_step_state",
    "format_progress_anim_footer",
    "get_anim_runtime",
    "get_progress_step",
    "get_progress_step_secondary",
    "notify_anim_finished",
    "notify_anim_queued",
    "notify_anim_started",
    "progress_dedupe_extra",
    "sync_anim_runtime_from_ext",
]
