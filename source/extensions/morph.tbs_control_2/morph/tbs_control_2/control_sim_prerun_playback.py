from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SimTimelineItem:
    """프리런 결과의 단일 아이템(시뮬 시간 기준)."""

    t: float
    kind: str  # "log" | "event" | "progress"
    payload: Any


@dataclass(frozen=True)
class SimPreRunResult:
    """프리런 결과(화면 1개)."""

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

    omit_keys = frozenset({"kind", "detail", "proc_sec", "anim_sec"})
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
        disp[k] = v

    parts: List[str] = []
    try:
        parts.append(json.dumps(disp, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        parts.append(str(disp))

    if kind == "step":
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


def _post_anim_src_from_progress(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event": _normalize_anim_event_seq(_s_val(p.get("event_seq") or p.get("sequence_name"))),
        "lot_id": _s_val(p.get("lot_id")),
        "from_port_id": _s_val(p.get("from_port_id")),
        "to_port_id": _s_val(p.get("to_port_id")),
        "port_id": _s_val(p.get("port_id") or p.get("event_port_id")),
    }


def predict_ports_occupancy_after_anim(occ_base: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    """JSON(이동·안착·회수) 종료 직후 기대되는 ports_occupancy."""
    occ_pred = dict(occ_base or {})
    ev = _normalize_anim_event_seq(_s_val(src.get("event") or src.get("event_seq") or src.get("seq")))
    lot_id = _s_val(src.get("lot_id"))
    fr = _canonical_sim_port_key(_s_val(src.get("from_port_id")))
    to = _canonical_sim_port_key(_s_val(src.get("to_port_id")))
    port = _canonical_sim_port_key(_s_val(src.get("port_id") or src.get("event_port_id")))
    if ev in ("MOVE_TRANSFERING", "MOVE_REQ", "MOVE"):
        if fr:
            occ_pred[fr] = ""
        if to and lot_id:
            occ_pred[to] = lot_id
    elif ev == "ARRIVED":
        dest = port or to
        if dest and lot_id:
            occ_pred[dest] = lot_id
    elif ev == "REMOVED":
        if port:
            occ_pred[port] = ""
    return occ_pred


def anim_json_end_sim_time(progress_p: Dict[str, Any]) -> Optional[float]:
    """애니 포트 이벤트 RUNNING progress 의 JSON 종료 sim 시각."""
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
    return _json_end_sim_time_from_progress(progress_p, fallback_t=float(t0))


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
    anim_end = anim_json_end_sim_time(progress_p)
    if anim_end is None:
        return occ
    if float(at_t) + 1e-9 < float(anim_end):
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
    anim_end = anim_json_end_sim_time(progress_p)
    if anim_end is None or anim_end <= float(t0) + 1e-9 or anim_end >= float(t1) - 1e-9:
        return [(dt_total, effective_ports_occupancy_at_t(occ0, progress_p, t1))]
    src = _post_anim_src_from_progress(progress_p)
    occ_after = predict_ports_occupancy_after_anim(dict(occ0), src)
    return [
        (float(anim_end) - float(t0), dict(occ0)),
        (float(t1) - float(anim_end), occ_after),
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
    anim_end = anim_json_end_sim_time(progress_p)
    if anim_end is None or float(t_end) + 1e-9 < float(anim_end):
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
    """step progress 기준 JSON 종료 sim 시각."""
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
            for k in ("port_id", "from_port_id", "to_port_id", "lot_id", "foup_id", "lot_seq"):
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

    def start(self) -> None:
        with self._lock:
            self._playing = True
            now_wall = time.perf_counter()
            self._cursor_by_screen = {scr: 0 for scr in self._results.keys()}
            self._sim_now_by_screen = {scr: 0.0 for scr in self._results.keys()}
            self._last_wall_by_screen = {scr: now_wall for scr in self._results.keys()}

    def stop(self) -> None:
        with self._lock:
            self._playing = False

    def is_playing(self) -> bool:
        with self._lock:
            return bool(self._playing)

    def sim_now(self, screen: int) -> float:
        with self._lock:
            return float(self._sim_now_by_screen.get(int(screen), 0.0))

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

    def emit_due_items(self, *, max_emits: int = 24) -> int:
        """``sim_now`` 이하 타임라인 항목 emit (프레임당 상한)."""
        emitted = 0
        max_n = max(1, int(max_emits))
        for scr, res in self._results.items():
            t_sim = self.sim_now(scr)
            i = 0
            with self._lock:
                i = int(self._cursor_by_screen.get(scr, 0))
            items = res.items
            event_emitted_this_tick = False
            while i < len(items) and float(items[i].t) <= float(t_sim) + 1e-9 and emitted < max_n:
                it = items[i]
                if str(it.kind) == "event":
                    if event_emitted_this_tick:
                        break
                    if self._event_emit_allowed is not None:
                        try:
                            if not bool(self._event_emit_allowed(int(scr))):
                                break
                        except Exception:
                            break
                    try:
                        self._emit(it.kind, it.payload, int(scr))
                    except Exception:
                        pass
                    emitted += 1
                    i += 1
                    event_emitted_this_tick = True
                    # 동일 sim_time 의 progress(공정 단계)는 같은 틱에 함께 emit — 연계 JSON 표시 어긋남 방지
                    if (
                        i < len(items)
                        and str(items[i].kind) == "progress"
                        and abs(float(items[i].t) - float(it.t)) <= 1e-9
                        and emitted < max_n
                    ):
                        it_p = items[i]
                        try:
                            self._emit(it_p.kind, it_p.payload, int(scr))
                        except Exception:
                            pass
                        emitted += 1
                        i += 1
                    break
                try:
                    self._emit(it.kind, it.payload, int(scr))
                except Exception:
                    pass
                emitted += 1
                i += 1
            with self._lock:
                self._cursor_by_screen[scr] = int(i)
        return int(emitted)

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

    try:
        final_sim = float(getattr(engine.env, "now", 0.0) or 0.0) if getattr(engine, "env", None) is not None else 0.0
    except Exception:
        final_sim = 0.0
    try:
        te = float(getattr(engine, "_sim_total_est_sec", 0.0) or 0.0)
    except Exception:
        te = 0.0

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
