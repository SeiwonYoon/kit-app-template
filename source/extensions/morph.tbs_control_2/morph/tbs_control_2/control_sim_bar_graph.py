"""
프리런 막대그래프 — 5상태(load/proc/unload/empty/down) 사전 계산·웹 export.

시뮬 엔진·재생 tick 은 건드리지 않는다. 타임라인 items 만 읽어 막대를 만든다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .control_sim_prerun_playback import (
    SimPreRunResult,
    SimTimelineItem,
    TimetableRowMeta,
    _ANIM_PORT_UPDATE_SEQS,
    _canonical_sim_port_key,
    _f_val,
    _normalize_anim_event_seq,
    _post_anim_src_from_progress,
    _progress_event_affects_ep,
    _s_val,
    predict_ports_occupancy_after_anim,
)

BAR_STATE_EMPTY = "empty"
BAR_STATE_LOAD = "load"
BAR_STATE_PROC = "proc"
BAR_STATE_UNLOAD = "unload"
BAR_STATE_DOWN = "down"

BAR_STATES: Tuple[str, ...] = (
    BAR_STATE_EMPTY,
    BAR_STATE_LOAD,
    BAR_STATE_PROC,
    BAR_STATE_UNLOAD,
    BAR_STATE_DOWN,
)

# 기본값 — config/bar_graph_colors.json 없거나 키 누락 시 폴백 (#RRGGBB)
_DEFAULT_BAR_STATE_COLORS_HEX: Dict[str, str] = {
    BAR_STATE_EMPTY: "#FFFF00",
    BAR_STATE_LOAD: "#00FF00",
    BAR_STATE_PROC: "#3399FF",
    BAR_STATE_UNLOAD: "#CC66FF",
    BAR_STATE_DOWN: "#FF0000",
}

_BAR_COLORS_KIT_CACHE: Optional[Dict[str, int]] = None
_BAR_COLORS_HEX_CACHE: Optional[Dict[str, str]] = None
_BAR_COLORS_MTIME: Optional[float] = None

# 하위 호환 — get_bar_state_colors_*() 가 mtime 변경 시 갱신한다.
BAR_STATE_COLORS: Dict[str, int] = {}
BAR_STATE_COLORS_HEX: Dict[str, str] = {}


def _extension_root_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _bar_graph_colors_path() -> Path:
    return _extension_root_dir() / "config" / "bar_graph_colors.json"


def _normalize_hex_color(raw: str) -> str:
    s = str(raw or "").strip().upper()
    if not s:
        return ""
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6:
        return ""
    try:
        int(s, 16)
    except ValueError:
        return ""
    return f"#{s}"


def hex_to_kit_ui_color(hex_rgb: str) -> int:
    """#RRGGBB → omni.ui ``0xAABBGGRR``."""
    norm = _normalize_hex_color(hex_rgb)
    if not norm:
        return 0xFF808080
    s = norm[1:]
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    return int((0xFF << 24) | (b << 16) | (g << 8) | r)


def _parse_bar_colors_file(data: Any) -> Dict[str, str]:
    out: Dict[str, str] = dict(_DEFAULT_BAR_STATE_COLORS_HEX)
    if not isinstance(data, dict):
        return out
    for st in BAR_STATES:
        raw = data.get(st)
        if raw is None:
            continue
        norm = _normalize_hex_color(str(raw))
        if norm:
            out[st] = norm
    return out


def _refresh_bar_color_caches(kit: Dict[str, int], hex_map: Dict[str, str]) -> None:
    global BAR_STATE_COLORS, BAR_STATE_COLORS_HEX
    BAR_STATE_COLORS = dict(kit)
    BAR_STATE_COLORS_HEX = dict(hex_map)


def get_bar_state_colors_hex() -> Dict[str, str]:
    """config/bar_graph_colors.json 기준 #RRGGBB (mtime 캐시)."""
    global _BAR_COLORS_HEX_CACHE, _BAR_COLORS_KIT_CACHE, _BAR_COLORS_MTIME
    p = _bar_graph_colors_path()
    mtime: Optional[float] = None
    if p.is_file():
        try:
            mtime = float(p.stat().st_mtime)
        except Exception:
            mtime = None
    if _BAR_COLORS_HEX_CACHE is not None and _BAR_COLORS_MTIME == mtime:
        return dict(_BAR_COLORS_HEX_CACHE)
    hex_map = dict(_DEFAULT_BAR_STATE_COLORS_HEX)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            hex_map = _parse_bar_colors_file(data)
        except Exception as ex:
            print(f"[BAR] 색상 파일 로드 실패: {p} err={ex}", flush=True)
    kit_map = {st: hex_to_kit_ui_color(hx) for st, hx in hex_map.items()}
    _BAR_COLORS_HEX_CACHE = dict(hex_map)
    _BAR_COLORS_KIT_CACHE = dict(kit_map)
    _BAR_COLORS_MTIME = mtime
    _refresh_bar_color_caches(kit_map, hex_map)
    return dict(hex_map)


def get_bar_state_colors_kit() -> Dict[str, int]:
    get_bar_state_colors_hex()
    if _BAR_COLORS_KIT_CACHE is None:
        hex_map = get_bar_state_colors_hex()
        return {st: hex_to_kit_ui_color(hx) for st, hx in hex_map.items()}
    return dict(_BAR_COLORS_KIT_CACHE)


# 모듈 import 시 1회 로드
get_bar_state_colors_hex()

PRERUN_EXPORT_VERSION = 1


@dataclass
class EpBarPrecomputed:
    """프리런 타임라인으로 미리 계산한 막대 rows + 상태별 누적 초."""

    total_est: float
    rows: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    ep_ports: Tuple[str, ...] = ()
    buffer_ports: Tuple[str, ...] = ()
    row_order: Tuple[str, ...] = ()
    duration_sec_by_row: Dict[str, Dict[str, float]] = field(default_factory=dict)
    fault_ports: Tuple[str, ...] = ()


def bar_graph_row_order(ep_count_idx: int) -> List[str]:
    """EP 개수에 따른 막대 행 순서 (EP 블록 → ALL_EP → INOUT → BP)."""
    idx = 1 if int(ep_count_idx) else 0
    eps = ["EP1", "EP2"] + (["EP3"] if idx else [])
    bps = ["BP1", "BP2", "BP3"] + (["BP4"] if idx else [])
    return list(eps) + ["ALL_EP", "INOUT"] + bps


def bar_state_from_seg(seg: Dict[str, Any]) -> str:
    st = str(seg.get("state", "") or "").strip().lower()
    if st in BAR_STATES:
        return st
    return BAR_STATE_EMPTY if bool(seg.get("empty", False)) else BAR_STATE_LOAD


def bar_state_color(state: str) -> int:
    kit = get_bar_state_colors_kit()
    st = str(state or "").strip().lower()
    return int(kit.get(st, kit.get(BAR_STATE_EMPTY, hex_to_kit_ui_color("#FFFF00"))))


def merge_bar_row_segments(segs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in segs or []:
        if not isinstance(s, dict):
            continue
        try:
            dur = float(s.get("dur", 0.0))
        except Exception:
            dur = 0.0
        if dur <= 1e-9:
            continue
        st = bar_state_from_seg(s)
        if out and bar_state_from_seg(out[-1]) == st:
            out[-1]["dur"] = float(out[-1]["dur"]) + dur
        else:
            out.append({"state": st, "dur": dur})
    return out


def _push_bar_seg_state(
    segs: List[Dict[str, Any]],
    state: str,
    dur: float,
    *,
    cap_segments: Optional[int] = 220,
) -> None:
    st = str(state or BAR_STATE_EMPTY).strip().lower()
    if st not in BAR_STATES:
        st = BAR_STATE_EMPTY
    if dur <= 1e-9:
        return
    if segs and isinstance(segs[-1], dict) and bar_state_from_seg(segs[-1]) == st:
        segs[-1]["dur"] = float(segs[-1].get("dur", 0.0)) + float(dur)
    else:
        segs.append({"state": st, "dur": float(dur)})
    if cap_segments is not None and len(segs) > int(cap_segments):
        del segs[: -int(cap_segments)]


def _resolve_port_bar_state(
    port: str,
    occ: Dict[str, str],
    foup_phase: Dict[str, str],
    fault_ports: Set[str],
) -> str:
    p = str(port or "").strip().upper()
    if p in fault_ports:
        return BAR_STATE_DOWN
    has_lot = bool(str(occ.get(p, "") or "").strip())
    if p.startswith("EP"):
        if not has_lot:
            return BAR_STATE_EMPTY
        ph = str(foup_phase.get(p, "") or "").strip().lower()
        if ph == BAR_STATE_PROC:
            return BAR_STATE_PROC
        if ph == BAR_STATE_UNLOAD:
            return BAR_STATE_UNLOAD
        return BAR_STATE_LOAD
    if not has_lot:
        return BAR_STATE_EMPTY
    return BAR_STATE_LOAD


def _aggregate_all_ep_state(ep_states: List[str]) -> str:
    if not ep_states:
        return BAR_STATE_EMPTY
    if BAR_STATE_PROC in ep_states:
        return BAR_STATE_PROC
    if BAR_STATE_UNLOAD in ep_states:
        return BAR_STATE_UNLOAD
    if BAR_STATE_LOAD in ep_states:
        return BAR_STATE_LOAD
    if all(s == BAR_STATE_DOWN for s in ep_states):
        return BAR_STATE_DOWN
    return BAR_STATE_EMPTY


def _push_bar_rows_for_interval(
    rows: Dict[str, List[Dict[str, Any]]],
    row_order: List[str],
    ep_list: List[str],
    occ: Dict[str, str],
    foup_phase: Dict[str, str],
    fault_ports: Set[str],
    dt: float,
    *,
    cap_segments: Optional[int] = 220,
) -> None:
    if dt <= 1e-9:
        return
    ep_states: List[str] = []
    for row_name in row_order:
        if row_name not in rows:
            rows[row_name] = []
        if row_name == "ALL_EP":
            st = _aggregate_all_ep_state(ep_states)
        else:
            st = _resolve_port_bar_state(row_name, occ, foup_phase, fault_ports)
            if row_name in ep_list:
                ep_states.append(st)
        _push_bar_seg_state(rows[row_name], st, dt, cap_segments=cap_segments)


def compute_duration_sec_by_row(rows: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for row_name, segs in (rows or {}).items():
        acc = {s: 0.0 for s in BAR_STATES}
        for seg in segs or []:
            if not isinstance(seg, dict):
                continue
            st = bar_state_from_seg(seg)
            try:
                acc[st] = float(acc.get(st, 0.0)) + float(seg.get("dur", 0.0))
            except Exception:
                pass
        out[str(row_name)] = {k: round(float(v), 4) for k, v in acc.items() if float(v) > 1e-9}
    return out


def _json_end_sim_time_from_progress(p: Dict[str, Any], *, fallback_t: float = 0.0) -> Optional[float]:
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


def _initial_bar_occ_at_t0(
    sorted_items: Tuple[SimTimelineItem, ...],
    ports: List[str],
) -> Dict[str, str]:
    out = {p: "" for p in ports}
    for it in sorted_items or ():
        try:
            t = float(getattr(it, "t", 0.0) or 0.0)
        except Exception:
            t = 0.0
        if t > 1e-3:
            break
        if str(it.kind or "").strip().lower() != "progress":
            continue
        p = dict(it.payload) if isinstance(it.payload, dict) else {}
        if _s_val(p.get("status")).upper() != "RUNNING":
            continue
        if abs(_f_val(p.get("elapsed"), 0.0)) > 1e-9:
            continue
        ev = _normalize_anim_event_seq(_s_val(p.get("event_seq") or p.get("sequence_name")))
        if ev in _ANIM_PORT_UPDATE_SEQS:
            return out
    for it in sorted_items or ():
        try:
            t = float(getattr(it, "t", 0.0) or 0.0)
        except Exception:
            t = 0.0
        if t > 1e-3:
            break
        p = dict(it.payload) if isinstance(it.payload, dict) else {}
        po = p.get("ports_occupancy")
        if not isinstance(po, dict) or not po:
            continue
        for port in ports:
            v = str(po.get(port, "") or "").strip()
            if v:
                out[port] = v
        break
    return out


def allocate_bar_segment_pixels(
    segs: List[Dict[str, Any]],
    *,
    total_est: float,
    bar_w: int,
    t_cover: Optional[float] = None,
) -> List[Tuple[int, str]]:
    merged = merge_bar_row_segments(segs)
    if total_est <= 1e-9 or bar_w <= 0 or not merged:
        return []
    dur_sum = sum(float(s.get("dur", 0.0)) for s in merged)
    if dur_sum <= 1e-9:
        return []
    if t_cover is None:
        t_cover = dur_sum
    target_px = int(round((float(t_cover) / float(total_est)) * float(bar_w)))
    target_px = max(0, min(int(bar_w), target_px))
    if target_px <= 0:
        return []
    weights = [float(s.get("dur", 0.0)) for s in merged]
    wsum = sum(weights)
    if wsum <= 1e-9:
        return []
    raw = [(target_px * w / wsum) for w in weights]
    widths = [int(f) for f in raw]
    slack = target_px - sum(widths)
    if slack > 0:
        order = sorted(range(len(raw)), key=lambda i: (raw[i] - widths[i]), reverse=True)
        for i in range(slack):
            widths[order[i % len(order)]] += 1
    out: List[Tuple[int, str]] = []
    for i, s in enumerate(merged):
        if widths[i] > 0:
            out.append((int(widths[i]), bar_state_from_seg(s)))
    return out


def truncate_bar_rows_at_t(rows: Dict[str, List[Dict[str, Any]]], t_cut: float) -> Dict[str, List[Dict[str, Any]]]:
    t_cut = max(0.0, float(t_cut))
    out: Dict[str, List[Dict[str, Any]]] = {}
    for row_name, segs in (rows or {}).items():
        acc = 0.0
        clipped: List[Dict[str, Any]] = []
        for seg in segs or []:
            if not isinstance(seg, dict):
                continue
            dur = float(seg.get("dur", 0.0))
            st = bar_state_from_seg(seg)
            if acc + dur <= t_cut + 1e-9:
                clipped.append({"state": st, "dur": dur})
                acc += dur
            elif acc < t_cut - 1e-9:
                rem = t_cut - acc
                clipped.append({"state": st, "dur": rem})
                acc = t_cut
                break
            else:
                break
        out[str(row_name)] = clipped
    return out


def build_ep_bar_from_timeline_replay(
    items: Tuple[SimTimelineItem, ...],
    *,
    final_sim_time: float,
    ep_ports: Optional[List[str]] = None,
    ep_count_idx: int = 0,
    fault_ports: Optional[Set[str]] = None,
) -> EpBarPrecomputed:
    """
    프리런 타임라인 → 5상태 막대 사전 계산 (EP + ALL_EP + INOUT + BP).

  JSON 종료 시점에 ports_occupancy 반영, FOUP START/END 로 proc/unload 구간 표시.
    """
    total_est = max(0.0, float(final_sim_time))
    faults = {str(p).strip().upper() for p in (fault_ports or set()) if str(p).strip()}
    row_order = bar_graph_row_order(int(ep_count_idx))
    ep_list = [r for r in row_order if r.startswith("EP")]
    if isinstance(ep_ports, list) and ep_ports:
        ep_list = [str(x).strip().upper() for x in ep_ports if str(x).strip().upper().startswith("EP")]
    if not ep_list:
        ep_list = ["EP1", "EP2"] + (["EP3"] if int(ep_count_idx) else [])
    buffer_ports = tuple(r for r in row_order if r.startswith("BP"))
    all_ports = [r for r in row_order if r != "ALL_EP"]

    kind_prio = {"log": 0, "event": 1, "progress": 2}
    try:
        sorted_items = tuple(
            sorted(
                (it for it in (items or ()) if isinstance(it, SimTimelineItem)),
                key=lambda it: (float(getattr(it, "t", 0.0) or 0.0), int(kind_prio.get(str(it.kind), 9))),
            )
        )
    except Exception:
        sorted_items = ()

    Milestone = Tuple[float, int, str, Any]
    milestones: List[Milestone] = []
    seq_i = 0

    panel_occ: Dict[str, str] = {}
    for it in sorted_items:
        kind = str(it.kind or "").strip().lower()
        try:
            t_ev = float(getattr(it, "t", 0.0) or 0.0)
        except Exception:
            t_ev = 0.0
        if kind == "event" and isinstance(it.payload, dict):
            p = dict(it.payload)
            seq_u = _s_val(p.get("seq")).upper()
            port = _canonical_sim_port_key(_s_val(p.get("port_id")))
            if seq_u == "FOUP_PROCESS_START" and port.startswith("EP"):
                milestones.append((t_ev, seq_i, "foup_start", port))
                seq_i += 1
            elif seq_u == "FOUP_PROCESS_END" and port.startswith("EP"):
                milestones.append((t_ev, seq_i, "foup_end", port))
                seq_i += 1
        elif kind == "progress" and isinstance(it.payload, dict):
            p = dict(it.payload)
            po = p.get("ports_occupancy")
            if isinstance(po, dict) and po:
                panel_occ = {str(k).strip().upper(): str(v or "") for k, v in po.items()}
            st = _s_val(p.get("status")).upper()
            el = _f_val(p.get("elapsed"), 0.0)
            if st != "RUNNING" or abs(el) > 1e-9:
                continue
            ev = _normalize_anim_event_seq(_s_val(p.get("event_seq") or p.get("sequence_name")))
            if ev not in _ANIM_PORT_UPDATE_SEQS:
                continue
            t_json_end = _json_end_sim_time_from_progress(
                p,
                fallback_t=_f_val(p.get("sim_time", it.t), t_ev),
            )
            if t_json_end is None:
                continue
            milestones.append(
                (
                    float(t_json_end),
                    seq_i,
                    "occ",
                    (_post_anim_src_from_progress(p), dict(panel_occ)),
                )
            )
            seq_i += 1

    milestones.sort(key=lambda m: (float(m[0]), int(m[1])))

    rows: Dict[str, List[Dict[str, Any]]] = {r: [] for r in row_order}
    bar_occ = _initial_bar_occ_at_t0(sorted_items, all_ports)
    foup_phase: Dict[str, str] = {}
    t_cur = 0.0
    t_final = max(0.0, float(final_sim_time))

    def _apply_interval(t_end: float) -> None:
        nonlocal t_cur
        t_apply = min(float(t_end), float(t_final))
        if t_apply > t_cur + 1e-9:
            _push_bar_rows_for_interval(
                rows,
                row_order,
                ep_list,
                bar_occ,
                foup_phase,
                faults,
                t_apply - t_cur,
                cap_segments=None,
            )
            t_cur = t_apply

    for t_m, _ord, kind, data in milestones:
        _apply_interval(float(t_m))
        if kind == "foup_start":
            ep = str(data or "").strip().upper()
            if ep:
                foup_phase[ep] = BAR_STATE_PROC
        elif kind == "foup_end":
            ep = str(data or "").strip().upper()
            if ep:
                foup_phase[ep] = BAR_STATE_UNLOAD
        elif kind == "occ":
            src, occ_snap = data
            occ_pred = predict_ports_occupancy_after_anim(dict(occ_snap), dict(src))
            for port in all_ports:
                if port in occ_pred:
                    bar_occ[port] = str(occ_pred.get(port, "") or "")
                elif port in occ_snap:
                    bar_occ[port] = str(occ_pred.get(port, bar_occ.get(port, "")) or "")
            ev = _normalize_anim_event_seq(_s_val(src.get("event") or src.get("event_seq")))
            if ev == "REMOVED":
                port = _canonical_sim_port_key(_s_val(src.get("port_id")))
                if port.startswith("EP"):
                    foup_phase.pop(port, None)
            for ep in ep_list:
                if not str(bar_occ.get(ep, "") or "").strip():
                    foup_phase.pop(ep, None)

    if t_final > t_cur + 1e-9:
        _push_bar_rows_for_interval(
            rows,
            row_order,
            ep_list,
            bar_occ,
            foup_phase,
            faults,
            t_final - t_cur,
            cap_segments=None,
        )

    for r in row_order:
        rows[r] = merge_bar_row_segments(rows.get(r, []))

    if total_est <= 0.0:
        total_est = max(30.0, t_final)

    dur_by = compute_duration_sec_by_row(rows)
    return EpBarPrecomputed(
        total_est=float(total_est),
        rows=rows,
        ep_ports=tuple(ep_list),
        buffer_ports=buffer_ports,
        row_order=tuple(row_order),
        duration_sec_by_row=dur_by,
        fault_ports=tuple(sorted(faults)),
    )


def build_ep_bar_from_progress_items(
    items: Tuple[SimTimelineItem, ...],
    *,
    final_sim_time: float,
    ep_ports: Optional[List[str]] = None,
    ep_count_idx: int = 0,
    fault_ports: Optional[Set[str]] = None,
) -> EpBarPrecomputed:
    return build_ep_bar_from_timeline_replay(
        items,
        final_sim_time=float(final_sim_time),
        ep_ports=ep_ports,
        ep_count_idx=int(ep_count_idx),
        fault_ports=fault_ports,
    )


def _timetable_meta_to_dict(m: TimetableRowMeta) -> Dict[str, Any]:
    return {
        "row_index": int(m.row_index),
        "t": float(m.t),
        "kind": str(m.kind),
        "display_line": str(m.display_line),
        "through_item_index": int(m.through_item_index),
        "json_obj": dict(m.json_obj or {}),
    }


def build_prerun_export_document(
    *,
    screen: int,
    result: SimPreRunResult,
    bar: EpBarPrecomputed,
    timetable_metas: Optional[List[TimetableRowMeta]] = None,
    seek_snapshots_count: int = 0,
    sim_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """웹·외부 연동용 프리런 통합 JSON (막대 세그먼트 + 상태별 누적 초 포함)."""
    snap = dict(sim_snapshot or {})
    ep_count_idx = int(snap.get("ep_count_idx", 0) or 0)
    ep_count = 3 if ep_count_idx else 2
    row_order = list(bar.row_order) if bar.row_order else bar_graph_row_order(ep_count_idx)

    hex_colors = get_bar_state_colors_hex()
    segments_out: Dict[str, List[Dict[str, Any]]] = {}
    for row_name in row_order:
        segs = bar.rows.get(row_name, []) if isinstance(bar.rows, dict) else []
        segments_out[row_name] = [
            {
                "state": bar_state_from_seg(s),
                "dur_sec": round(float(s.get("dur", 0.0)), 4),
                "color": hex_colors.get(bar_state_from_seg(s), "#888888"),
            }
            for s in (segs or [])
            if isinstance(s, dict) and float(s.get("dur", 0.0)) > 1e-9
        ]

    tt_rows = []
    for m in timetable_metas or []:
        if isinstance(m, TimetableRowMeta):
            tt_rows.append(_timetable_meta_to_dict(m))

    timeline_summary = {
        "item_count": len(result.items or ()),
        "final_sim_time_sec": round(float(result.final_sim_time), 4),
        "total_est_sec": round(float(result.total_est_sec or result.final_sim_time), 4),
    }

    duration_sec_totals: Dict[str, float] = {s: 0.0 for s in BAR_STATES}
    for row_durs in (bar.duration_sec_by_row or {}).values():
        if not isinstance(row_durs, dict):
            continue
        for st_key, sec_val in row_durs.items():
            sk = str(st_key or "").strip().lower()
            if sk in duration_sec_totals:
                duration_sec_totals[sk] = float(duration_sec_totals[sk]) + float(sec_val)
    duration_sec_totals = {
        k: round(float(v), 4) for k, v in duration_sec_totals.items() if float(v) > 1e-9
    }

    return {
        "version": PRERUN_EXPORT_VERSION,
        "screen": int(screen),
        "sim": {
            "ep_count_idx": ep_count_idx,
            "ep_count": ep_count,
            "buffer_ports": list(bar.buffer_ports or bar_graph_row_order(ep_count_idx)[- (4 if ep_count_idx else 3) :]),
            "fault_ports": list(bar.fault_ports or ()),
            "final_sim_time_sec": timeline_summary["final_sim_time_sec"],
            "total_est_sec": timeline_summary["total_est_sec"],
            "settings_snapshot": snap,
        },
        "timeline": {
            **timeline_summary,
            "timetable_rows": tt_rows,
            "seek_snapshots_count": int(seek_snapshots_count),
        },
        "bar_graph": {
            "states": list(BAR_STATES),
            "colors": dict(hex_colors),
            "row_order": row_order,
            "segments": segments_out,
            "duration_sec_by_row": dict(bar.duration_sec_by_row or {}),
            "duration_sec_totals": duration_sec_totals,
        },
    }


def write_prerun_export_json(path: str, doc: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)


__all__ = [
    "BAR_STATES",
    "BAR_STATE_COLORS",
    "BAR_STATE_COLORS_HEX",
    "EpBarPrecomputed",
    "allocate_bar_segment_pixels",
    "bar_graph_row_order",
    "get_bar_state_colors_hex",
    "get_bar_state_colors_kit",
    "hex_to_kit_ui_color",
    "bar_state_color",
    "bar_state_from_seg",
    "build_ep_bar_from_progress_items",
    "build_ep_bar_from_timeline_replay",
    "build_prerun_export_document",
    "compute_duration_sec_by_row",
    "merge_bar_row_segments",
    "truncate_bar_rows_at_t",
    "write_prerun_export_json",
]
