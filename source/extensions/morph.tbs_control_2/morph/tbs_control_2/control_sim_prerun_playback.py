from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .sim_lot_fix_proc import format_fix_meta_block, format_lot_id_display


@dataclass(frozen=True)
class SimTimelineItem:
    """프리런 결과의 단일 아이템(시뮬 시간 기준)."""

    t: float
    kind: str  # "log" | "event" | "progress"
    payload: Any


@dataclass(frozen=True)
class SimPreRunResult:
    """프리런 결과(화면 1개).

    ``final_sim_time`` / ``total_est_sec`` 는 프리런 완료 후 동일 값(실제 env.now).
    (시작 전 엔진 추정치와 혼동하지 말 것 — 웹·모니터 총 공정시간 SSOT.)
    """

    screen: int
    final_sim_time: float
    total_est_sec: float
    items: Tuple[SimTimelineItem, ...]



@dataclass(frozen=True)
class TimetableRowMeta:
    """타임테이블 UI 한 줄(클릭 단위) 메타."""

    row_index: int
    t: float
    kind: str  # "event" | "step"
    json_obj: Dict[str, Any]
    display_line: str
    through_item_index: int  # Fast-apply 시 items[0..through_item_index] 포함


@dataclass
class SeekSnapshot:
    """
    프리런 타임라인 ``items[0 .. item_index-1]`` 적용 후 상태.

    ``play_cursor == item_index`` 일 때 seek 가 이 스냅샷을 사용한다.
    """

    item_index: int
    apply_payload: Dict[str, Any]
    progress_last_payload: Optional[Dict[str, Any]] = None
    foup_by_ep: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    needs_state_apply: bool = False


def _merge_foup_active_ep_from_payload(current: str, payload: Dict[str, Any]) -> str:
    """``_remember_foup_active_ep`` 와 동일 규칙(확장 상태 없이 순수 병합)."""
    ep = str(payload.get("foup_proc_active_ep", "") or "").strip().upper()
    if ep:
        return ep
    if "foup_proc_active_ep" in payload:
        return ""
    return str(current or "").strip().upper()


def _extract_ep_id_from_foup_payload(payload: Dict[str, Any]) -> str:
    ep_id = str(payload.get("port_id", "") or "").strip().upper()
    if ep_id:
        return ep_id
    try:
        import re as _re

        src_txt = (str(payload.get("label", "") or "") + " " + str(payload.get("detail", "") or "")).upper()
        m = _re.search(r"\bEP(\d+)\b", src_txt)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 3:
                return f"EP{n}"
    except Exception:
        pass
    return ""


def build_seek_snapshots_by_item_index(items: Tuple[SimTimelineItem, ...]) -> List[SeekSnapshot]:
    """
    프리런 ``items`` 를 한 번 훑어 item_index 별 seek 스냅샷을 만든다.

    반환 길이 ``len(items)+1`` — ``snapshots[play_cursor]`` 가
    ``_fast_apply_prerun_seek`` 의 ``range(play_cursor)`` 루프 결과와 동일해야 한다.
    """
    snapshots: List[SeekSnapshot] = []
    occ: Dict[str, str] = {}
    foup_active = ""
    last_progress: Optional[Dict[str, Any]] = None
    foup_by_ep: Dict[str, Dict[str, Any]] = {}
    needs_apply = False

    def _append_snapshot(item_index: int) -> None:
        sim_t = "0.00"
        if isinstance(last_progress, dict):
            sim_t = str(last_progress.get("sim_time", "") or "0.00")
        snapshots.append(
            SeekSnapshot(
                item_index=int(item_index),
                apply_payload={
                    "ports_occupancy": dict(occ),
                    "sim_time": sim_t,
                    "foup_proc_active_ep": str(foup_active or ""),
                },
                progress_last_payload=dict(last_progress) if isinstance(last_progress, dict) else None,
                foup_by_ep={k: dict(v) for k, v in foup_by_ep.items()},
                needs_state_apply=bool(needs_apply),
            )
        )

    _append_snapshot(0)
    for it in items:
        kind = str(it.kind or "").strip().lower()
        p = it.payload
        if kind == "event" and isinstance(p, dict):
            pd = dict(p)
            o = pd.get("ports_occupancy", {})
            if isinstance(o, dict) and o:
                occ = {str(k).strip().upper(): str(v or "") for k, v in o.items()}
            foup_active = _merge_foup_active_ep_from_payload(foup_active, pd)
            needs_apply = True
        elif kind == "progress" and isinstance(p, dict):
            pd = dict(p)
            last_progress = pd
            o = pd.get("ports_occupancy", {})
            if isinstance(o, dict) and o:
                occ = {str(k).strip().upper(): str(v or "") for k, v in o.items()}
                needs_apply = True
            foup_active = _merge_foup_active_ep_from_payload(foup_active, pd)
            ev_seq = str(pd.get("event_seq") or pd.get("sequence_name") or "").strip().upper()
            if ev_seq == "FOUP_PROCESS":
                ep_id = _extract_ep_id_from_foup_payload(pd)
                if ep_id:
                    foup_by_ep[ep_id] = dict(pd)
        _append_snapshot(len(snapshots))
    return snapshots


def _f_val(x: Any, default: float = 0.0) -> float:
    try:
        return float(str(x).strip() or default)
    except Exception:
        return float(default)


def _s_val(v: Any) -> str:
    try:
        return str(v).strip() if v is not None else ""
    except Exception:
        return ""


def _format_timetable_proc_line_ko(proc_sec: float, anim_sec: float, *, process_time_priority: bool) -> str:
    p = max(0.0, float(proc_sec))
    a = max(0.0, float(anim_sec))
    if process_time_priority:
        return f"공정시간 우선: {p:.1f}s (공정 {p:.1f}s)"
    return f"공정시간: {p:.1f}s"


def _format_timetable_anim_line_ko(anim: str, anim_sec: float) -> str:
    name = _s_val(anim)
    if not name:
        return "애니메이션: 없음"
    bn = name.replace("\\", "/").rsplit("/", 1)[-1]
    a = max(0.0, float(anim_sec))
    if a > 1e-9:
        return f"애니메이션: {bn} (추정 {a:.1f}s)"
    return f"애니메이션: {bn}"


def format_timetable_display_line(row: Dict[str, Any]) -> str:
    """
    타임테이블 UI 한 줄.

    - 앞부분: ``t``·``screen``·``event`` 등 핵심 필드만 담은 짧은 JSON (kind/detail/proc_sec/anim_sec 키 제외)
    - 뒷부분: 엔진 로그와 동일 톤의 ``공정시간: …`` · ``애니메이션: …`` 한글 문구
    """
    kind = _s_val(row.get("kind")).lower()
    proc_sec = _f_val(row.get("proc_sec", 0.0), 0.0)
    anim_sec = _f_val(row.get("anim_sec", 0.0), 0.0)
    anim_file = _s_val(row.get("anim"))
    ptp = _s_val(row.get("process_time_priority")).lower() in ("1", "true", "on", "yes")

    omit_keys = frozenset({"kind", "detail", "proc_sec", "anim_sec", "lot_fix_label"})
    disp: Dict[str, Any] = {}
    for k, v in row.items():
        if k in omit_keys:
            continue
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if k == "anim":
            disp[k] = anim_file.replace("\\", "/").rsplit("/", 1)[-1] if anim_file else ""
            continue
        if k == "lot_id":
            lid = str(row.get("lot_id_display") or "").strip() or format_lot_id_display(
                str(v), str(row.get("lot_fix_label") or "")
            )
            if lid:
                disp["lot_id"] = lid
            continue
        if k in ("fix_oht_ep", "fix_ep_oht", "lot_id_display"):
            continue
        disp[k] = v

    parts: List[str] = []
    try:
        parts.append(json.dumps(disp, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        parts.append(str(disp))

    if kind == "step":
        fix_oht = row.get("fix_oht_ep")
        fix_ep = row.get("fix_ep_oht")
        fix_block = ""
        if fix_oht is not None and str(fix_oht).strip():
            try:
                fix_block = format_fix_meta_block(
                    lot_id_display=str(disp.get("lot_id") or ""),
                    fix_oht_ep=float(fix_oht),
                )
            except Exception:
                fix_block = format_fix_meta_block(
                    lot_id_display=str(disp.get("lot_id") or ""),
                    fix_oht_ep=fix_oht,
                )
        elif fix_ep is not None and str(fix_ep).strip():
            try:
                fix_block = format_fix_meta_block(
                    lot_id_display=str(disp.get("lot_id") or ""),
                    fix_ep_oht=float(fix_ep),
                )
            except Exception:
                fix_block = format_fix_meta_block(
                    lot_id_display=str(disp.get("lot_id") or ""),
                    fix_ep_oht=fix_ep,
                )
        if fix_block:
            parts.append(fix_block)
        parts.append(_format_timetable_proc_line_ko(proc_sec, anim_sec, process_time_priority=ptp))
        parts.append(_format_timetable_anim_line_ko(anim_file, anim_sec))

    return "  ".join(p for p in parts if str(p).strip())


def _push_bar_seg(
    segs: List[Dict[str, Any]],
    empty: bool,
    dur: float,
    *,
    cap_segments: Optional[int] = 220,
) -> None:
    if dur <= 1e-9:
        return
    if segs and isinstance(segs[-1], dict) and bool(segs[-1].get("empty")) == bool(empty):
        segs[-1]["dur"] = float(segs[-1].get("dur", 0.0)) + float(dur)
    else:
        segs.append({"empty": bool(empty), "dur": float(dur)})
    if cap_segments is not None and len(segs) > int(cap_segments):
        del segs[: -int(cap_segments)]


_EP_BAR_KEYS = ("EP1", "EP2", "EP3")


def _blank_ep_bar_occ(ep_list: Optional[List[str]] = None) -> Dict[str, str]:
    keys = list(ep_list) if ep_list else list(_EP_BAR_KEYS)
    return {str(ep).strip().upper(): "" for ep in keys if str(ep).strip().upper().startswith("EP")}


def mask_ep_ports_for_bar(occ: Dict[str, Any]) -> Dict[str, Any]:
    """EP 막대: 포트 패널 갱신 전 initial_full_ports 등 EP 점유를 숨긴다."""
    out = dict(occ or {})
    for ep in _EP_BAR_KEYS:
        out[ep] = ""
    return out


_ANIM_PORT_UPDATE_SEQS = frozenset({
    "ARRIVED",
    "MOVE_TRANSFERING",
    "MOVE_REQ",
    "MOVE",
    "REMOVED",
})


def _normalize_anim_event_seq(ev: str) -> str:
    """짧은 이름 또는 EAPEIS 정식명 → 짧은 이름."""
    e = str(ev or "").strip().upper()
    if not e:
        return ""
    if e in _ANIM_PORT_UPDATE_SEQS:
        return e
    try:
        from . import xml_generator

        mapping = {
            str(xml_generator.SEQ_ARRIVED).strip().upper(): "ARRIVED",
            str(xml_generator.SEQ_MOVE_TRANSFERING).strip().upper(): "MOVE_TRANSFERING",
            str(xml_generator.SEQ_MOVE_REQ).strip().upper(): "MOVE_REQ",
            str(xml_generator.SEQ_MOVE).strip().upper(): "MOVE",
            str(xml_generator.SEQ_REMOVED).strip().upper(): "REMOVED",
        }
        mapped = mapping.get(e)
        if mapped:
            return mapped
    except Exception:
        pass
    return e


def _canonical_sim_port_key(port: str) -> str:
    o = str(port or "").strip().upper()
    if not o:
        return ""
    if o in ("IN/OUT", "INOUT"):
        return "INOUT"
    if o.startswith("BP"):
        try:
            n = int(o.replace("BP", ""))
            if 1 <= n <= 4:
                return f"BP{n}"
        except Exception:
            pass
    if o.startswith("EP"):
        try:
            n = int(o.replace("EP", ""))
            if 1 <= n <= 3:
                return f"EP{n}"
        except Exception:
            pass
    return o


def _anim_basename_from_src(src: Mapping[str, Any]) -> str:
    for k in ("file", "path", "linked_anim_json", "json_basename"):
        v = str((src or {}).get(k) or "").strip().replace("\\", "/")
        if v:
            return v.rsplit("/", 1)[-1]
    return ""


def repair_anim_src_ports(src: Dict[str, Any]) -> Dict[str, Any]:
    """JSON 파일명으로 from/to 보강 — BP→EP 가 INOUT 으로 잘못 찍히는 잔상 방지.

    예: ``move_bp1_ep1.json`` + to=INOUT(오염) → BP1→EP1 복구.
    """
    import re

    out = dict(src or {})
    ev = _normalize_anim_event_seq(
        _s_val(out.get("event") or out.get("event_seq") or out.get("seq"))
    )
    bn = _anim_basename_from_src(out).lower()
    if not bn:
        return out

    m_bp_ep = re.search(r"move_(bp[1-4])_(ep[1-3])", bn)
    if m_bp_ep and ev in ("", "MOVE", "MOVE_REQ", "MOVE_TRANSFERING"):
        # MOVE_REQ / move_bp*_ep* 는 파일명이 SSOT (병렬 시 to 오염 대응)
        if ev in ("", "MOVE", "MOVE_REQ") or "move_bp" in bn:
            out["from_port_id"] = str(m_bp_ep.group(1)).upper()
            out["to_port_id"] = str(m_bp_ep.group(2)).upper()
            if not ev:
                out["event"] = "MOVE_REQ"

    m_inout_bp = re.search(r"move_inout_(bp[1-4])", bn)
    if m_inout_bp and ev in ("", "MOVE_TRANSFERING", "MOVE"):
        out["from_port_id"] = "INOUT"
        out["to_port_id"] = str(m_inout_bp.group(1)).upper()
        if not ev:
            out["event"] = "MOVE_TRANSFERING"

    if "arrived_inout" in bn and ev in ("", "ARRIVED"):
        out["port_id"] = "INOUT"
        out["to_port_id"] = out.get("to_port_id") or "INOUT"
        out["from_port_id"] = out.get("from_port_id") or "OHT"
        if not ev:
            out["event"] = "ARRIVED"

    m_arr_ep = re.search(r"arrived_(ep[1-3])", bn)
    if m_arr_ep and ev in ("", "ARRIVED"):
        ep = str(m_arr_ep.group(1)).upper()
        out["port_id"] = ep
        out["to_port_id"] = ep
        out["from_port_id"] = out.get("from_port_id") or "OHT"
        if not ev:
            out["event"] = "ARRIVED"

    # MOVE_REQ 가 to=INOUT 이면 파일명으로 강제 교정
    to_now = _canonical_sim_port_key(_s_val(out.get("to_port_id")))
    if m_bp_ep and (ev in ("MOVE_REQ", "MOVE") or "move_bp" in bn) and to_now in ("", "INOUT"):
        out["from_port_id"] = str(m_bp_ep.group(1)).upper()
        out["to_port_id"] = str(m_bp_ep.group(2)).upper()
    return out


def _clear_lot_elsewhere(occ: Dict[str, Any], lot_id: str, keep: Tuple[str, ...]) -> None:
    """같은 LOT 이 keep 외 포트에 남아 있으면 비움 (BP→EP 후 INOUT 잔상 등)."""
    lid = str(lot_id or "").strip()
    if not lid:
        return
    keep_u = {str(k).strip().upper() for k in (keep or ()) if str(k).strip()}
    for k in list(occ.keys()):
        ku = str(k).strip().upper()
        if ku in keep_u:
            continue
        if str(occ.get(k) or "").strip() == lid:
            occ[k] = ""


def _post_anim_src_from_progress(p: Dict[str, Any]) -> Dict[str, Any]:
    return repair_anim_src_ports(
        {
            "event": _normalize_anim_event_seq(_s_val(p.get("event_seq") or p.get("sequence_name"))),
            "lot_id": _s_val(p.get("lot_id")),
            "from_port_id": _s_val(p.get("from_port_id")),
            "to_port_id": _s_val(p.get("to_port_id")),
            "port_id": _s_val(p.get("port_id") or p.get("event_port_id")),
            "file": _s_val(p.get("linked_anim_json") or p.get("file") or p.get("path")),
            "linked_anim_json": _s_val(p.get("linked_anim_json")),
            "path": _s_val(p.get("path")),
        }
    )


def _post_anim_src_from_progress_and_event(
    progress_p: Dict[str, Any],
    event_p: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """progress + event payload 병합 — INOUT/BP 이동 lot·port 누락 방지."""
    src = dict(_post_anim_src_from_progress(progress_p if isinstance(progress_p, dict) else {}))
    ev = dict(event_p or {}) if isinstance(event_p, dict) else {}
    if ev:
        if not _s_val(src.get("event") or src.get("event_seq")):
            src["event"] = _normalize_anim_event_seq(
                _s_val(ev.get("seq") or ev.get("event_seq") or ev.get("sequence_name"))
            )
        for key in ("lot_id", "from_port_id", "to_port_id", "port_id", "event_port_id"):
            if not _s_val(src.get(key)) and _s_val(ev.get(key)):
                src[key] = _s_val(ev.get(key))
        for key in ("file", "path", "linked_anim_json"):
            if not _s_val(src.get(key)) and _s_val(ev.get(key)):
                src[key] = _s_val(ev.get(key))
    return repair_anim_src_ports(src)


def predict_ports_occupancy_after_anim(occ_base: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    """JSON(이동·안착·회수) 종료 직후 기대되는 ports_occupancy."""
    occ_pred = dict(occ_base or {})
    src_f = repair_anim_src_ports(dict(src or {}))
    ev = _normalize_anim_event_seq(
        _s_val(src_f.get("event") or src_f.get("event_seq") or src_f.get("seq"))
    )
    lot_id = _s_val(src_f.get("lot_id"))
    fr = _canonical_sim_port_key(_s_val(src_f.get("from_port_id")))
    to = _canonical_sim_port_key(_s_val(src_f.get("to_port_id")))
    port = _canonical_sim_port_key(_s_val(src_f.get("port_id") or src_f.get("event_port_id")))
    if ev in ("MOVE_TRANSFERING", "MOVE_REQ", "MOVE"):
        if fr:
            occ_pred[fr] = ""
        if to and lot_id:
            occ_pred[to] = lot_id
        # BP→EP: 같은 LOT 이 INOUT 에 남아 있으면 잔상 제거
        if fr.startswith("BP") and to.startswith("EP") and lot_id:
            _clear_lot_elsewhere(occ_pred, lot_id, keep=(to,))
    elif ev == "ARRIVED":
        dest = port or to
        if dest and lot_id:
            occ_pred[dest] = lot_id
            # 주의: INOUT ARRIVED 에서 같은 lot 을 BP 에서 지우면
            # (lot_id 오염 시) BP→INOUT 점프처럼 보인다. ARRIVED 는 dest 만 채움.
    elif ev == "REMOVED":
        if port:
            occ_pred[port] = ""
    return occ_pred


def predict_ports_occupancy_at_playback_sync(
    occ_base: Dict[str, Any],
    progress_p: Dict[str, Any],
    event_p: Optional[Dict[str, Any]] = None,
    *,
    at_t: float,
    parsed_steps: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    재생 sim 축 ``at_t`` 시점 panel occ (renewal wall 포함).

    ``at_t >= playback_port_sync`` 이면 JSON 이벤트 post-anim predict 적용.
    INOUT·EP 공통 — renewal wall 에서 ARRIVED/MOVE 반영.
    """
    occ = dict(occ_base or {})
    src = _post_anim_src_from_progress_and_event(
        progress_p if isinstance(progress_p, dict) else {},
        event_p if isinstance(event_p, dict) else None,
    )
    try:
        from .json_playback_timing import playback_port_sync_sim_time_from_progress

        t_sync = playback_port_sync_sim_time_from_progress(
            progress_p if isinstance(progress_p, dict) else {},
            steps=parsed_steps,
        )
    except Exception:
        t_sync = None
    if t_sync is None:
        # sync 시각을 못 구하면 조기 EMPTY(REMOVED start) 예측 금지 — base 유지
        return occ
    if float(at_t) + 1e-9 >= float(t_sync):
        return predict_ports_occupancy_after_anim(occ, src)
    return occ


def panel_occ_tuple_from_dict(
    occ: Mapping[str, str],
    panel_ports: Sequence[str],
) -> Tuple[Tuple[str, str], ...]:
    return tuple(
        (str(k).strip().upper(), str(occ.get(k, "") or ""))
        for k in panel_ports
        if str(k).strip()
    )


def anim_json_end_sim_time(progress_p: Dict[str, Any]) -> Optional[float]:
    """애니 포트 이벤트 RUNNING progress 의 JSON 종료 sim 시각 (back-align)."""
    if not isinstance(progress_p, dict):
        return None
    if _s_val(progress_p.get("status")).upper() != "RUNNING":
        return None
    ev = _normalize_anim_event_seq(_s_val(progress_p.get("event_seq") or progress_p.get("sequence_name")))
    if ev not in _ANIM_PORT_UPDATE_SEQS:
        return None
    t0 = _f_val(progress_p.get("event_start_sim_time"), -1.0)
    if t0 < 0.0:
        return None
    try:
        from .json_playback_timing import json_end_sim_time_from_progress

        return json_end_sim_time_from_progress(progress_p, fallback_t=float(t0))
    except Exception:
        return _json_end_sim_time_from_progress(progress_p, fallback_t=float(t0))


def anim_json_port_sync_sim_time(progress_p: Dict[str, Any]) -> Optional[float]:
    """포트·막대 갱신 sim 시각 — 재생 축 ``playback_port_sync`` (renewal 우선)."""
    if not isinstance(progress_p, dict):
        return None
    if _s_val(progress_p.get("status")).upper() != "RUNNING":
        return None
    ev = _normalize_anim_event_seq(_s_val(progress_p.get("event_seq") or progress_p.get("sequence_name")))
    if ev not in _ANIM_PORT_UPDATE_SEQS:
        return None
    t0 = _f_val(progress_p.get("event_start_sim_time"), -1.0)
    if t0 < 0.0:
        return None
    json_path: Optional[str] = None
    steps: Optional[List[Any]] = None
    linked = str(progress_p.get("linked_anim_json") or "").strip()
    if linked:
        try:
            from .sim_sequence_json import load_sim_sequence_steps, resolve_sim_sequence_json_path

            jp = resolve_sim_sequence_json_path(linked)
            if jp is not None:
                json_path = str(jp)
                steps = load_sim_sequence_steps(str(jp))
        except Exception:
            pass
    try:
        from .json_playback_timing import playback_port_sync_sim_time_from_progress

        return playback_port_sync_sim_time_from_progress(
            progress_p,
            fallback_t=float(t0),
            json_path=json_path,
            steps=steps if isinstance(steps, list) else None,
        )
    except Exception:
        return anim_json_end_sim_time(progress_p)


def effective_ports_occupancy_at_t(
    occ_base: Dict[str, Any],
    progress_p: Optional[Dict[str, Any]],
    at_t: float,
) -> Dict[str, Any]:
    """
    EP 막대용 ports_occupancy.

    엔진은 공정 종료 후에만 occ 를 바꾸므로, JSON 종료(anim_sec) 이후 구간은 post-anim 예측을 쓴다.
    """
    occ = dict(occ_base or {})
    if not isinstance(progress_p, dict):
        return occ
    sync_t = anim_json_port_sync_sim_time(progress_p)
    if sync_t is None:
        return occ
    if float(at_t) + 1e-9 < float(sync_t):
        return occ
    return predict_ports_occupancy_after_anim(occ, _post_anim_src_from_progress(progress_p))


def interval_occ_parts(
    occ_engine: Dict[str, Any],
    progress_p: Dict[str, Any],
    t0: float,
    t1: float,
) -> List[Tuple[float, Dict[str, Any]]]:
    """[t0,t1] 구간을 JSON 종료 시각 기준으로 나눠 (dt, occ) 목록을 반환."""
    dt_total = max(0.0, float(t1) - float(t0))
    if dt_total <= 1e-9:
        return []
    if _s_val(progress_p.get("status")).upper() == "DONE":
        po = progress_p.get("ports_occupancy")
        if isinstance(po, dict) and po:
            return [(dt_total, {str(k): str(v or "") for k, v in po.items()})]
    occ0 = dict(occ_engine or {})
    sync_t = anim_json_port_sync_sim_time(progress_p)
    if sync_t is None or sync_t <= float(t0) + 1e-9 or sync_t >= float(t1) - 1e-9:
        return [(dt_total, effective_ports_occupancy_at_t(occ0, progress_p, t1))]
    src = _post_anim_src_from_progress(progress_p)
    occ_after = predict_ports_occupancy_after_anim(dict(occ0), src)
    return [
        (float(sync_t) - float(t0), dict(occ0)),
        (float(t1) - float(sync_t), occ_after),
    ]


def _progress_event_affects_ep(p: Dict[str, Any]) -> bool:
    ev = _normalize_anim_event_seq(_s_val(p.get("event_seq") or p.get("sequence_name")))
    if ev not in _ANIM_PORT_UPDATE_SEQS:
        return False
    src = _post_anim_src_from_progress(p)
    for key in ("from_port_id", "to_port_id", "port_id"):
        port = _canonical_sim_port_key(_s_val(src.get(key)))
        if port.startswith("EP"):
            return True
    return False


def bar_display_occ_for_ep(
    bar_ep_occ: Dict[str, str],
    ep_list: List[str],
    occ_eff: Dict[str, Any],
) -> Dict[str, Any]:
    """EP 막대 표시용: 비-EP 이벤트 구간에서도 EP 점유를 유지한다."""
    out = dict(occ_eff or {})
    for ep in ep_list:
        out[ep] = str(bar_ep_occ.get(ep, "") or "")
    return out


def commit_bar_ep_occ_from_interval(
    bar_ep_occ: Dict[str, str],
    ep_list: List[str],
    progress_p: Dict[str, Any],
    t_end: float,
    occ_eff: Dict[str, Any],
) -> None:
    """EP 관련 이벤트(JSON 종료 또는 DONE)에서만 bar_ep_occ 를 갱신한다."""
    if not _progress_event_affects_ep(progress_p):
        return
    st = _s_val(progress_p.get("status")).upper()
    if st == "DONE":
        for ep in ep_list:
            bar_ep_occ[ep] = str(occ_eff.get(ep, "") or "")
        return
    sync_t = anim_json_port_sync_sim_time(progress_p)
    if sync_t is None or float(t_end) + 1e-9 < float(sync_t):
        return
    ev = _normalize_anim_event_seq(_s_val(progress_p.get("event_seq") or progress_p.get("sequence_name")))
    src = _post_anim_src_from_progress(progress_p)
    if ev == "REMOVED":
        port = _canonical_sim_port_key(_s_val(src.get("port_id")))
        if port in bar_ep_occ:
            bar_ep_occ[port] = ""
        return
    for ep in ep_list:
        v = str(occ_eff.get(ep, "") or "")
        if v:
            bar_ep_occ[ep] = v


def _resolve_ep_list_for_bar(
    eps: List[str],
    p: Dict[str, Any],
    ep_occ: Dict[str, Any],
) -> List[str]:
    ep_list = list(eps)
    if not ep_list:
        ep_ports_raw = p.get("ep_ports", [])
        if isinstance(ep_ports_raw, list) and ep_ports_raw:
            ep_list = [str(x).strip().upper() for x in ep_ports_raw if str(x).strip().upper().startswith("EP")]
    if not ep_list:
        ep_list = sorted(
            [str(k).strip().upper() for k in ep_occ.keys() if str(k).strip().upper().startswith("EP")],
            key=lambda x: int(str(x).replace("EP", "") or "0"),
        )
    if not ep_list:
        ep_list = ["EP1", "EP2"]
    return ep_list


def _push_bar_rows_from_occ(
    rows: Dict[str, List[Dict[str, Any]]],
    ep_list: List[str],
    occ: Dict[str, Any],
    dt: float,
    *,
    cap_segments: Optional[int] = 220,
) -> None:
    if dt <= 1e-9:
        return
    all_empty = True
    for ep in ep_list:
        if ep not in rows:
            rows[ep] = []
        empty = not bool(str(occ.get(ep, "") or "").strip())
        if not empty:
            all_empty = False
        _push_bar_seg(rows[ep], empty=empty, dur=dt, cap_segments=cap_segments)
    if "ALL_EP" not in rows:
        rows["ALL_EP"] = []
    _push_bar_seg(rows["ALL_EP"], empty=all_empty, dur=dt, cap_segments=cap_segments)


def _json_end_sim_time_from_progress(p: Dict[str, Any], *, fallback_t: float = 0.0) -> Optional[float]:
    """step progress 기준 JSON 종료 sim 시각 (back-align)."""
    try:
        from .json_playback_timing import json_end_sim_time_from_progress

        return json_end_sim_time_from_progress(p, fallback_t=float(fallback_t))
    except Exception:
        pass
    try:
        t0 = float(str(p.get("event_start_sim_time", "")).strip() or "0.0")
    except Exception:
        t0 = float(fallback_t)
    asec = _f_val(p.get("anim_sec", 0.0), 0.0)
    psec = _f_val(p.get("proc_sec", 0.0), 0.0)
    if asec <= 1e-9 and psec <= 1e-9:
        return None
    if asec <= 1e-9:
        return float(t0)
    eff_anim = min(max(0.0, asec), max(0.0, psec)) if psec > 1e-9 else max(0.0, asec)
    return float(t0) + float(eff_anim)


def _initial_bar_ep_at_t0(
    sorted_items: Tuple[SimTimelineItem, ...],
    eps: List[str],
) -> Dict[str, str]:
    """
    막대 초기 EP 점유 — ``initial_full_ports`` 만 반영.

    엔진 ``ports_occupancy`` 는 JSON 종료 전에도 EP LOT 이 들어 있을 수 있어
    막대 초기값으로 쓰면 안 된다. t≈0 스냅샷에서 애니 이벤트 없이 차 있는 EP 만 초록 시작.
    """
    out = {ep: "" for ep in eps}
    for it in sorted_items or ():
        try:
            t = float(getattr(it, "t", 0.0) or 0.0)
        except Exception:
            t = 0.0
        if t > 1e-3:
            break
        if str(it.kind or "").strip().lower() != "progress":
            continue
        try:
            p = dict(it.payload) if isinstance(it.payload, dict) else {}
        except Exception:
            p = {}
        if _s_val(p.get("status")).upper() != "RUNNING":
            continue
        if abs(_f_val(p.get("elapsed"), 0.0)) > 1e-9:
            continue
        if _progress_event_affects_ep(p):
            return out
    for it in sorted_items or ():
        try:
            t = float(getattr(it, "t", 0.0) or 0.0)
        except Exception:
            t = 0.0
        if t > 1e-3:
            break
        try:
            p = dict(it.payload) if isinstance(it.payload, dict) else {}
        except Exception:
            p = {}
        po = p.get("ports_occupancy")
        if not isinstance(po, dict) or not po:
            continue
        for ep in eps:
            v = str(po.get(ep, "") or "").strip()
            if v:
                out[ep] = v
        break
    return out


def build_timetable_row_metas(res: SimPreRunResult) -> List[TimetableRowMeta]:
    """
    ``_build_prerun_timetable_text`` 와 동일 필터·정렬로 UI 행 메타를 만든다.
    각 행은 ``through_item_index`` 로 Fast-apply 범위를 지정한다.
    """
    si = int(res.screen)
    items = res.items
    item_by_key: Dict[Tuple[float, str, str], int] = {}
    for idx, it in enumerate(items):
        kind = str(it.kind or "").strip().lower()
        p = it.payload
        if kind == "event" and isinstance(p, dict):
            seq = _s_val(p.get("seq")).upper()
            if seq:
                item_by_key[(round(float(it.t), 4), "event", seq)] = idx
        elif kind == "progress" and isinstance(p, dict):
            st = _s_val(p.get("status")).upper()
            el = _f_val(p.get("elapsed", 0.0), 0.0)
            if st == "RUNNING" and abs(el) <= 1e-9:
                ev = _s_val(p.get("event_seq") or p.get("sequence_name")).upper()
                if ev:
                    item_by_key[(round(float(it.t), 4), "step", ev)] = idx

    rows_data: List[Dict[str, Any]] = []
    for it in items:
        kind = str(it.kind or "").strip().lower()
        p = it.payload
        t_val = round(_f_val(it.t, 0.0), 2)
        if kind == "event" and isinstance(p, dict):
            seq = _s_val(p.get("seq")).upper()
            if not seq:
                continue
            row: Dict[str, Any] = {"t": t_val, "screen": si, "kind": "event", "event": seq}
            for k in (
                "port_id",
                "from_port_id",
                "to_port_id",
                "lot_id",
                "lot_id_display",
                "lot_fix_label",
                "foup_id",
                "lot_seq",
                "fix_oht_ep",
                "fix_ep_oht",
            ):
                v = _s_val(p.get(k))
                if v:
                    row[k] = v
            rows_data.append(row)
        elif kind == "progress" and isinstance(p, dict):
            st = _s_val(p.get("status")).upper()
            el = _f_val(p.get("elapsed", 0.0), 0.0)
            if st != "RUNNING" or abs(el) > 1e-9:
                continue
            ev = _s_val(p.get("event_seq") or p.get("sequence_name")).upper()
            if not ev:
                continue
            row = {"t": t_val, "screen": si, "kind": "step", "event": ev}
            lid = _s_val(p.get("lot_id"))
            if lid:
                row["lot_id"] = lid
            pid = _s_val(p.get("port_id"))
            if pid:
                row["port_id"] = pid
            label = _s_val(p.get("label"))
            if label:
                row["label"] = label
            row["anim"] = _s_val(p.get("linked_anim_json"))
            row["proc_sec"] = round(_f_val(p.get("proc_sec", 0.0), 0.0), 2)
            row["anim_sec"] = round(_f_val(p.get("anim_sec", 0.0), 0.0), 2)
            detail = _s_val(p.get("detail"))
            if detail:
                row["detail"] = detail
            ptp = _s_val(p.get("process_time_priority"))
            if ptp:
                row["process_time_priority"] = ptp
            for fk in ("lot_id_display", "lot_fix_label", "fix_oht_ep", "fix_ep_oht"):
                fv = _s_val(p.get(fk))
                if fv:
                    row[fk] = fv
            rows_data.append(row)

    if not rows_data:
        return []

    kind_prio = {"event": 0, "step": 1}
    rows_data.sort(
        key=lambda r: (
            float(r.get("t", 0.0)),
            int(kind_prio.get(str(r.get("kind", "")), 9)),
        )
    )

    metas: List[TimetableRowMeta] = []
    for ri, r in enumerate(rows_data):
        t_val = float(r.get("t", 0.0))
        kind = str(r.get("kind", ""))
        ev = _s_val(r.get("event")).upper()
        key = (round(t_val, 4), kind, ev)
        through = int(item_by_key.get(key, -1))
        if through < 0:
            through = _find_through_item_index(items, t_val, kind, ev, ri, rows_data)
        metas.append(
            TimetableRowMeta(
                row_index=int(ri),
                t=t_val,
                kind=kind,
                json_obj=dict(r),
                display_line=format_timetable_display_line(r),
                through_item_index=int(through),
            )
        )
    return metas


def _find_through_item_index(
    items: Tuple[SimTimelineItem, ...],
    t_val: float,
    kind: str,
    ev: str,
    row_index: int,
    rows_data: List[Dict[str, Any]],
) -> int:
    """item_by_key 미스 시 행 순서 기준으로 through 인덱스 추정."""
    best = -1
    for idx, it in enumerate(items):
        if float(it.t) > float(t_val) + 1e-6:
            break
        ik = str(it.kind or "").strip().lower()
        p = it.payload
        if ik == "event" and isinstance(p, dict) and kind == "event":
            if _s_val(p.get("seq")).upper() == ev and abs(float(it.t) - t_val) <= 1e-3:
                best = idx
        elif ik == "progress" and isinstance(p, dict) and kind == "step":
            st = _s_val(p.get("status")).upper()
            el = _f_val(p.get("elapsed", 0.0), 0.0)
            if st == "RUNNING" and abs(el) <= 1e-9:
                ev2 = _s_val(p.get("event_seq") or p.get("sequence_name")).upper()
                if ev2 == ev and abs(float(it.t) - t_val) <= 1e-3:
                    best = idx
    if best >= 0:
        return best
    for idx, it in enumerate(items):
        if float(it.t) <= float(t_val) + 1e-6:
            best = idx
    return max(0, best)


def resolve_seek_through_index(
    metas: List[TimetableRowMeta],
    clicked_row_index: int,
) -> Tuple[float, int]:
    """
    클릭 행 기준 seek 목표 (t, through_item_index).
    동일 t 의 상단 행들이 모두 포함되도록 through 를 상향 조정한다.
    """
    if not metas:
        return 0.0, 0
    ri = max(0, min(int(clicked_row_index), len(metas) - 1))
    clicked = metas[ri]
    t_target = float(clicked.t)
    through = int(clicked.through_item_index)
    for m in metas[: ri + 1]:
        if abs(float(m.t) - t_target) <= 1e-6:
            through = max(through, int(m.through_item_index))
    return t_target, through


class PlaybackEnv:
    def __init__(self) -> None:
        self.now: float = 0.0


class PlaybackEngine:
    """UI 그래프 동기화용 최소 엔진( env.now 제공 )."""

    def __init__(self, final_sim_time: float) -> None:
        self.env = PlaybackEnv()
        self._final = float(final_sim_time)
        self._running = True
        self._done = False

    @property
    def is_done(self) -> bool:
        return bool(self._done)

    @property
    def is_running(self) -> bool:
        return bool(self._running and (not self._done))

    def stop(self) -> None:
        self._running = False
        self._done = True

    def _set_now(self, t: float) -> None:
        self.env.now = max(0.0, float(t))
        if self.env.now >= self._final - 1e-9:
            self._done = True


class SimTimelinePlayer:
    """
    프리런 타임라인을 wall-clock에 맞춰 재생한다.
    - emit_fn(kind, payload, screen)
    - speed_supplier() -> float
    """

    def __init__(
        self,
        results_by_screen: Dict[int, SimPreRunResult],
        emit_fn: Callable[[str, Any, int], None],
        speed_supplier: Callable[[], float],
        event_emit_allowed: Optional[Callable[[int], bool]] = None,
    ) -> None:
        self._results = dict(results_by_screen or {})
        self._emit = emit_fn
        self._speed = speed_supplier
        self._event_emit_allowed = event_emit_allowed
        self._lock = threading.Lock()
        self._playing = False
        self._sim_now_by_screen: Dict[int, float] = {}
        self._cursor_by_screen: Dict[int, int] = {}
        self._last_wall_by_screen: Dict[int, float] = {}
        # gated 이벤트(JSON dispatch)가 막혀 커서가 고정된 동안, 그 뒤에서 먼저 내보낸
        # non-gated 이벤트(FOUP_PROCESS_*/PORT_OCC_REFRESH) 인덱스. 커서가 따라오면 건너뛴다.
        self._skipped_by_screen: Dict[int, set] = {}

    def start(self) -> None:
        with self._lock:
            self._playing = True
            now_wall = time.perf_counter()
            self._cursor_by_screen = {scr: 0 for scr in self._results.keys()}
            self._sim_now_by_screen = {scr: 0.0 for scr in self._results.keys()}
            self._last_wall_by_screen = {scr: now_wall for scr in self._results.keys()}
            self._skipped_by_screen = {scr: set() for scr in self._results.keys()}

    def stop(self) -> None:
        with self._lock:
            self._playing = False

    def is_playing(self) -> bool:
        with self._lock:
            return bool(self._playing)

    def sim_now(self, screen: int) -> float:
        with self._lock:
            return float(self._sim_now_by_screen.get(int(screen), 0.0))

    def clamp_sim_now_max(self, screen: int, t_max: float) -> None:
        """``playback_process_frontier_sim`` 등 — ``sim_now`` 를 공정 경계 이하로."""
        scr = int(screen)
        try:
            cap = float(t_max)
        except Exception:
            return
        with self._lock:
            cur = float(self._sim_now_by_screen.get(scr, 0.0))
            if cur > cap + 1e-9:
                self._sim_now_by_screen[scr] = float(cap)

    def cursor(self, screen: int) -> int:
        with self._lock:
            return int(self._cursor_by_screen.get(int(screen), 0))

    def seek(self, screen: int, *, target_t: float, item_cursor: int) -> None:
        """재생 커서를 ``target_t`` / ``item_cursor`` 로 옮기고 wall-clock 기준을 재설정."""
        scr = int(screen)
        t = max(0.0, float(target_t))
        ic = max(0, int(item_cursor))
        with self._lock:
            res = self._results.get(scr)
            if res is not None:
                t = min(float(res.final_sim_time), t)
                ic = min(ic, len(res.items))
            self._sim_now_by_screen[scr] = t
            self._cursor_by_screen[scr] = ic
            self._last_wall_by_screen[scr] = time.perf_counter()
            self._skipped_by_screen[scr] = set()
            self._playing = True

    def advance_sim_clock(self) -> None:
        """wall-clock 기준으로 ``sim_now`` 만 전진 (emit 없음)."""
        now_wall = time.perf_counter()
        sp = 1.0
        try:
            sp = max(0.05, float(self._speed()))
        except Exception:
            sp = 1.0
        with self._lock:
            if not self._playing:
                return
            for scr, res in self._results.items():
                last_w = float(self._last_wall_by_screen.get(scr, now_wall))
                dt = max(0.0, now_wall - last_w)
                t_sim = float(self._sim_now_by_screen.get(scr, 0.0)) + float(dt) * float(sp)
                t_sim = min(float(res.final_sim_time), float(t_sim))
                self._sim_now_by_screen[scr] = float(t_sim)
                self._last_wall_by_screen[scr] = float(now_wall)

    def apply_playback_sync(self, ext: Any, screen: int) -> None:
        """레거시 no-op — sim_now 는 tick clamp 하지 않음 (FOUP·다중 공정 동시 진행)."""
        del ext, screen
        return

    @staticmethod
    def _event_needs_json_gate(payload: Any) -> bool:
        """JSON 시퀀스를 dispatch 하는 이벤트만 emit 게이트(러너 busy)를 받는다.

        FOUP_PROCESS_START/END·PORT_OCC_REFRESH·READYTOLOAD/UNLOAD 는
        SequenceRunner 를 거치지 않으므로 게이트와 무관하게 자기 sim 시각에 내보낸다.
        READY* 를 gated 로 두면 oht wall 이 잡혀 MOVE 중 REMOVED 가 막힐 수 있다.
        """
        if not isinstance(payload, dict):
            return True
        seq = str(payload.get("seq") or payload.get("event") or "").strip().upper()
        if not seq:
            return False
        if seq in (
            "PORT_OCC_REFRESH",
            "FOUP_PROCESS_START",
            "FOUP_PROCESS_END",
            "READYTOLOAD",
            "READYTOUNLOAD",
            "EAPEIS_PORT_READYTOLOAD",
            "EAPEIS_PORT_READYTOUNLOAD",
        ):
            return False
        return True

    def _safe_emit(self, item: Any, scr: int) -> None:
        try:
            self._emit(item.kind, item.payload, int(scr))
        except Exception:
            pass

    def _gate_open(self, scr: int, payload: Any = None) -> bool:
        if self._event_emit_allowed is None:
            return True
        try:
            return bool(self._event_emit_allowed(int(scr), payload=payload))  # type: ignore[call-arg]
        except TypeError:
            try:
                return bool(self._event_emit_allowed(int(scr)))
            except Exception:
                return False
        except Exception:
            return False

    def emit_due_items(self, *, max_emits: int = 24) -> int:
        """``sim_now`` 이하 타임라인 항목 emit (프레임당 상한).

        gated 이벤트(JSON dispatch)가 러너 busy 로 막히면 커서를 고정하되, 그 뒤의
        non-gated 이벤트(FOUP_PROCESS_*/PORT_OCC_REFRESH)는 **같은 t 이하** 만 허용.

        병렬 모드: oht/move 레일별로 gated emit 1개까지 동일 tick 허용 (A∥B).
        **금지:** gate 로 막힌 gated 보다 **뒤 인덱스** 의 다른 레일 gated
        (ARRIVED INOUT 미emit 인데 MOVE INOUT→BP 선행) · 앞선 FOUP 로 EP 공정 꼬임.
        """
        emitted = 0
        max_n = max(1, int(max_emits))
        try:
            from .sim_parallel_rails import classify_sim_rail, parallel_moves_enabled

            parallel = bool(parallel_moves_enabled())
        except Exception:
            classify_sim_rail = None  # type: ignore
            parallel = False

        def _skip_same_t_progress(items_local, j0: int, t0: float) -> int:
            jj = int(j0)
            while (
                jj < len(items_local)
                and str(items_local[jj].kind) == "progress"
                and abs(float(items_local[jj].t) - float(t0)) <= 1e-9
            ):
                jj += 1
            return jj

        for scr, res in self._results.items():
            t_sim = self.sim_now(scr)
            with self._lock:
                i = int(self._cursor_by_screen.get(scr, 0))
                skipped = self._skipped_by_screen.get(scr)
                if skipped is None:
                    skipped = set()
                    self._skipped_by_screen[scr] = skipped
            items = res.items
            gated_emitted_rails: set = set()
            event_emitted_this_tick = False
            cursor_frozen = False
            freeze_at: Optional[int] = None
            freeze_t: Optional[float] = None
            j = i
            while j < len(items) and float(items[j].t) <= float(t_sim) + 1e-9 and emitted < max_n:
                it = items[j]
                if j in skipped:
                    if not cursor_frozen and freeze_at is None:
                        skipped.discard(j)
                        i = j + 1
                    j += 1
                    continue
                kind = str(it.kind)
                if kind == "event":
                    needs_gate = self._event_needs_json_gate(it.payload)
                    rail = None
                    if parallel and classify_sim_rail is not None and isinstance(it.payload, dict):
                        rail = classify_sim_rail(
                            str(it.payload.get("sim_rail") or it.payload.get("seq") or it.payload.get("event") or "")
                        )
                        if rail is None and needs_gate:
                            rail = "oht"
                    if needs_gate:
                        if not (parallel and rail):
                            # 직렬: 미emit gated 뒤 인덱스 gated 선행 금지
                            if freeze_at is not None and j > int(freeze_at):
                                break
                        if parallel and rail:
                            rail_blocked = False
                            if rail in gated_emitted_rails:
                                rail_blocked = True
                            elif not self._gate_open_rail(int(scr), rail, it.payload):
                                rail_blocked = True
                            if rail_blocked:
                                # 이 레일만 보류 — 다른 레일 gated(REMOVED∥MOVE) 는 계속 스캔
                                if freeze_at is None:
                                    freeze_at = j
                                    try:
                                        freeze_t = float(it.t)
                                    except Exception:
                                        freeze_t = None
                                cursor_frozen = True
                                j = _skip_same_t_progress(items, j + 1, float(it.t))
                                continue
                            # 같은 레일의 앞선 freeze 이면 중단(후순위 같은 레일 A/B 직렬)
                            if freeze_at is not None and j > int(freeze_at):
                                try:
                                    fr_it = items[int(freeze_at)]
                                    fr_rail = None
                                    if classify_sim_rail is not None and isinstance(
                                        fr_it.payload, dict
                                    ):
                                        fr_rail = classify_sim_rail(
                                            str(
                                                fr_it.payload.get("sim_rail")
                                                or fr_it.payload.get("seq")
                                                or ""
                                            )
                                        )
                                except Exception:
                                    fr_rail = None
                                if fr_rail and str(fr_rail) == str(rail):
                                    break
                        else:
                            if cursor_frozen or event_emitted_this_tick:
                                break
                            if not self._gate_open(int(scr), it.payload):
                                cursor_frozen = True
                                if freeze_at is None:
                                    freeze_at = j
                                    try:
                                        freeze_t = float(it.t)
                                    except Exception:
                                        freeze_t = None
                                j = _skip_same_t_progress(items, j + 1, float(it.t))
                                continue
                        evt_idx = j
                        evt_t = float(it.t)
                        self._safe_emit(it, int(scr))
                        emitted += 1
                        if parallel and rail:
                            gated_emitted_rails.add(rail)
                        else:
                            event_emitted_this_tick = True
                        j += 1
                        if (
                            j < len(items)
                            and str(items[j].kind) == "progress"
                            and abs(float(items[j].t) - evt_t) <= 1e-9
                            and emitted < max_n
                            and j not in skipped
                        ):
                            self._safe_emit(items[j], int(scr))
                            emitted += 1
                            j += 1
                        i = j
                        if not parallel:
                            break
                        continue
                    # non-gated 이벤트 (FOUP_*/READY*/PORT_OCC_REFRESH)
                    # freeze 중이면 막힌 gated 시각을 넘는 FOUP 로 EP 공정 UI 가 꼬이지 않게 차단
                    if freeze_at is not None and freeze_t is not None:
                        try:
                            if float(it.t) > float(freeze_t) + 1e-9:
                                break
                        except Exception:
                            break
                        if j > int(freeze_at):
                            # 같은 t 의 FOUP 만 허용 (뒷 인덱스·뒷 공정 금지)
                            try:
                                if abs(float(it.t) - float(freeze_t)) > 1e-9:
                                    break
                            except Exception:
                                break
                    self._safe_emit(it, int(scr))
                    emitted += 1
                    if cursor_frozen or freeze_at is not None:
                        skipped.add(j)
                    else:
                        i = j + 1
                    cur_t = float(it.t)
                    j += 1
                    if (
                        j < len(items)
                        and str(items[j].kind) == "progress"
                        and abs(float(items[j].t) - cur_t) <= 1e-9
                        and emitted < max_n
                        and j not in skipped
                    ):
                        self._safe_emit(items[j], int(scr))
                        emitted += 1
                        if cursor_frozen or freeze_at is not None:
                            skipped.add(j)
                        else:
                            i = j + 1
                        j += 1
                    continue
                # log / progress
                if freeze_at is not None and freeze_t is not None:
                    try:
                        if float(it.t) > float(freeze_t) + 1e-9:
                            break
                    except Exception:
                        break
                    if j > int(freeze_at) and kind == "progress":
                        # 막힌 gated 자신의 progress 는 이미 스킵됨.
                        # 이후 progress 는 시계만 올리는 착시를 내므로 중단.
                        break
                self._safe_emit(it, int(scr))
                emitted += 1
                if cursor_frozen or freeze_at is not None:
                    skipped.add(j)
                else:
                    i = j + 1
                j += 1
            with self._lock:
                if freeze_at is not None:
                    self._cursor_by_screen[scr] = int(freeze_at)
                else:
                    self._cursor_by_screen[scr] = int(i)
            # frontier: 미emit gated 시각을 시계/plan 캡으로 노출
            try:
                hold_by = getattr(self, "_emit_hold_t_by_screen", None)
                if not isinstance(hold_by, dict):
                    hold_by = {}
                    self._emit_hold_t_by_screen = hold_by
                if freeze_at is not None and freeze_t is not None:
                    hold_by[int(scr)] = float(freeze_t)
                else:
                    hold_by.pop(int(scr), None)
            except Exception:
                pass
        return int(emitted)

    def pending_gated_emit_hold_t(self, screen: int) -> Optional[float]:
        """미emit gated 로 커서 freeze 된 이벤트의 sim t (없으면 None)."""
        try:
            hold_by = getattr(self, "_emit_hold_t_by_screen", None)
            if not isinstance(hold_by, dict):
                return None
            v = hold_by.get(int(screen))
            if v is None:
                return None
            return float(v)
        except Exception:
            return None

    def _gate_open_rail(self, scr: int, rail: str, payload: Any = None) -> bool:
        if self._event_emit_allowed is None:
            return True
        try:
            # (screen, rail=..., payload=...) — 실패 시 단계적으로 축소
            return bool(
                self._event_emit_allowed(int(scr), rail=str(rail), payload=payload)  # type: ignore[call-arg]
            )
        except TypeError:
            try:
                return bool(self._event_emit_allowed(int(scr), rail=str(rail)))  # type: ignore[call-arg]
            except TypeError:
                try:
                    return bool(self._event_emit_allowed(int(scr)))
                except Exception:
                    return False
            except Exception:
                return False
        except Exception:
            return False

    def tick(self) -> None:
        """레거시 — ``advance_sim_clock`` + ``emit_due_items``."""
        self.advance_sim_clock()
        self.emit_due_items()


def prerun_engine_to_timeline(
    *,
    screen: int,
    engine: Any,
    max_tick_steps: int = 2000000,
) -> SimPreRunResult:
    """
    주어진 TBSSimulationEngine 인스턴스를 가능한 빠르게 끝까지 tick() 하며,
    on_log/on_event/on_progress로 올라오는 payload를 시뮬 시간 기준으로 수집한다.
    """
    items: List[SimTimelineItem] = []

    def _t_from_payload(payload: Any) -> float:
        if isinstance(payload, dict):
            try:
                return float(str(payload.get("sim_time", "")).strip() or "0.0")
            except Exception:
                return 0.0
        return 0.0

    def on_log(line: str) -> None:
        try:
            items.append(SimTimelineItem(t=float(getattr(engine.env, "now", 0.0) or 0.0), kind="log", payload=str(line)))
        except Exception:
            items.append(SimTimelineItem(t=0.0, kind="log", payload=str(line)))

    def on_event(payload: Dict[str, Any]) -> None:
        items.append(SimTimelineItem(t=_t_from_payload(payload), kind="event", payload=dict(payload)))

    def on_progress(payload: Dict[str, Any]) -> None:
        items.append(SimTimelineItem(t=_t_from_payload(payload), kind="progress", payload=dict(payload)))

    try:
        engine._on_log = on_log  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        engine._on_event = on_event  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        engine._on_progress = on_progress  # type: ignore[attr-defined]
    except Exception:
        pass

    # start() 는 프리런 수집 콜백 부착 **전**에 이미 호출된다(noop on_event).
    # 그때 보낸 초기 적재 PORT_OCC_REFRESH 는 타임라인에 없어 t=0 plan/패널이 비게 된다.
    # 수집 직전에 점유가 남아 있으면 refresh 를 1회 재emit 한다.
    try:
        ports_map = getattr(engine, "ports", None)
        all_ports = list(getattr(engine, "_all_ports", None) or ())
        has_occ = False
        if isinstance(ports_map, dict) and all_ports:
            for pk in all_ports:
                if ports_map.get(pk) is not None:
                    has_occ = True
                    break
        if has_occ and hasattr(engine, "_emit_port_occ_refresh"):
            engine._emit_port_occ_refresh("초기 적재 포트 표시(프리런 수집 재동기화)")
        elif has_occ:
            # fallback: 콜백만 붙어 있으면 수동 스냅샷 아이템
            occ: Dict[str, str] = {}
            for pk in all_ports:
                lot = ports_map.get(pk) if isinstance(ports_map, dict) else None
                occ[str(pk)] = str(getattr(lot, "lot_id", "") or "") if lot is not None else ""
            try:
                t_now = float(getattr(getattr(engine, "env", None), "now", 0.0) or 0.0)
            except Exception:
                t_now = 0.0
            items.append(
                SimTimelineItem(
                    t=float(t_now),
                    kind="event",
                    payload={
                        "seq": "PORT_OCC_REFRESH",
                        "sim_time": f"{float(t_now):.2f}",
                        "ports_occupancy": dict(occ),
                    },
                )
            )
    except Exception:
        pass

    # 프리런 동안 콘솔 print 끄기(기본). on_log 수집은 유지되어 재생 로그 패널은 그대로.
    # LOT 수·공정시간이 크면 줄 단위 print(flush) 가 시작을 크게 지연시키므로,
    # sim_control_defaults.SIM_PRERUN_CONSOLE_LOG 가 False 면 프리런 구간만 콘솔을 끈다.
    _prev_console_log: Optional[bool] = None
    try:
        from .sim_control_defaults import SIM_PRERUN_CONSOLE_LOG

        if not bool(SIM_PRERUN_CONSOLE_LOG):
            _prev_console_log = bool(getattr(engine, "_print_to_console", True))
            if hasattr(engine, "set_console_logging_enabled"):
                engine.set_console_logging_enabled(False)
            else:
                engine._print_to_console = False  # type: ignore[attr-defined]
    except Exception:
        _prev_console_log = None

    steps = 0
    while True:
        try:
            if getattr(engine, "is_done", False):
                break
            if not getattr(engine, "is_running", False):
                break
        except Exception:
            break
        try:
            engine.tick(1e6)
        except Exception:
            break
        steps += 1
        if steps >= int(max_tick_steps):
            break

    # 프리런 종료 — 콘솔 로그 설정 원복(끈 경우에만).
    if _prev_console_log is not None:
        try:
            if hasattr(engine, "set_console_logging_enabled"):
                engine.set_console_logging_enabled(bool(_prev_console_log))
            else:
                engine._print_to_console = bool(_prev_console_log)  # type: ignore[attr-defined]
        except Exception:
            pass

    try:
        final_sim = float(getattr(engine.env, "now", 0.0) or 0.0) if getattr(engine, "env", None) is not None else 0.0
    except Exception:
        final_sim = 0.0
    # SSOT: 프리런 완료 후 「총 공정시간」은 실제 env.now (= final_sim).
    # 사전샘플 합(_sim_total_est_sec)은 시작 전 스케일용 추정치라 실제와 어긋날 수 있다.
    # 모니터·막대·웹 export 가 서로 다른 값을 쓰지 않도록 동기화한다.
    try:
        engine._sim_total_est_sec = float(final_sim)  # type: ignore[attr-defined]
    except Exception:
        pass
    te = float(final_sim)

    kind_prio = {"log": 0, "event": 1, "progress": 2}
    try:
        items.sort(key=lambda it: (float(it.t), int(kind_prio.get(str(it.kind), 9))))
    except Exception:
        pass

    return SimPreRunResult(
        screen=int(screen),
        final_sim_time=float(final_sim),
        total_est_sec=float(te),
        items=tuple(items),
    )
