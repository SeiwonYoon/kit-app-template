"""
프리런 막대그래프 — 5상태(load/proc/unload/empty/down) 사전 계산·웹 export.

시뮬 엔진·재생 tick 은 건드리지 않는다. 타임라인 items 만 읽어 막대를 만든다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .ebs_case_models import ep_count_from_snapshot
from .control_sim_prerun_playback import (
    SimPreRunResult,
    SimTimelineItem,
    TimetableRowMeta,
    _ANIM_PORT_UPDATE_SEQS,
    _canonical_sim_port_key,
    _f_val,
    _normalize_anim_event_seq,
    _post_anim_src_from_progress,
    _post_anim_src_from_progress_and_event,
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

PRERUN_EXPORT_VERSION = 2


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


def normalize_bar_graph_row_order(row_order: List[str]) -> List[str]:
    """ALL_EP 를 항상 최상단(첫 행)으로 둔다. 나머지 행의 상대 순서는 유지."""
    rows = [str(r).strip() for r in (row_order or []) if str(r).strip()]
    if "ALL_EP" not in rows:
        return rows
    return ["ALL_EP"] + [r for r in rows if r != "ALL_EP"]


def bar_graph_row_order(ep_count_idx: int, *, ebs_enabled: bool = True) -> List[str]:
    """EP 개수·EBS 적용 여부에 따른 막대 행 순서 (ALL_EP 최상단)."""
    idx = 1 if int(ep_count_idx) else 0
    eps = ["EP1", "EP2"] + (["EP3"] if idx else [])
    if not bool(ebs_enabled):
        return ["ALL_EP"] + list(eps)
    bps = ["BP1", "BP2", "BP3"] + (["BP4"] if idx else [])
    return ["ALL_EP"] + list(eps) + ["INOUT"] + bps


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
    """EP 포트 상태 중 최우선 1개를 ALL_EP 행에 반영 (proc > load > unload > empty > down)."""
    if not ep_states:
        return BAR_STATE_EMPTY
    if BAR_STATE_PROC in ep_states:
        return BAR_STATE_PROC
    if BAR_STATE_LOAD in ep_states:
        return BAR_STATE_LOAD
    if BAR_STATE_UNLOAD in ep_states:
        return BAR_STATE_UNLOAD
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
    # ALL_EP 가 row_order 최상단이어도 EP 상태를 먼저 모아 집계한다.
    ep_states: List[str] = [
        _resolve_port_bar_state(ep, occ, foup_phase, fault_ports) for ep in ep_list
    ]
    all_ep_st = _aggregate_all_ep_state(ep_states)
    for row_name in row_order:
        if row_name not in rows:
            rows[row_name] = []
        if row_name == "ALL_EP":
            st = all_ep_st
        else:
            st = _resolve_port_bar_state(row_name, occ, foup_phase, fault_ports)
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


_BAR_STATE_SUMMARY_LABEL: Dict[str, str] = {
    BAR_STATE_EMPTY: "empty",
    BAR_STATE_LOAD: "load",
    BAR_STATE_PROC: "proc",
    BAR_STATE_UNLOAD: "unload",
    BAR_STATE_DOWN: "down",
}


def format_row_state_duration_summary(segs: List[Dict[str, Any]]) -> str:
    """막대 1행 — 5상태별 누적 초 (0초 상태는 생략). 예: ``empty:12s proc:5s load:3s``."""
    acc = {s: 0.0 for s in BAR_STATES}
    for seg in segs or ():
        if not isinstance(seg, dict):
            continue
        st = bar_state_from_seg(seg)
        try:
            acc[st] = float(acc.get(st, 0.0)) + float(seg.get("dur", 0.0))
        except Exception:
            pass
    parts: List[str] = []
    for st in BAR_STATES:
        sec = float(acc.get(st, 0.0) or 0.0)
        if sec <= 0.05:
            continue
        lbl = _BAR_STATE_SUMMARY_LABEL.get(st, st)
        if abs(sec - round(sec)) < 0.05:
            parts.append(f"{lbl}:{int(round(sec))}s")
        else:
            parts.append(f"{lbl}:{sec:.1f}s")
    return " ".join(parts) if parts else "empty:0s"


def _json_end_sim_time_from_progress(p: Dict[str, Any], *, fallback_t: float = 0.0) -> Optional[float]:
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


def overlay_bar_rows_tip_from_occ(
    rows_state: Dict[str, List[Dict[str, Any]]],
    row_order: List[str],
    ep_list: List[str],
    occ: Dict[str, str],
    *,
    fault_ports: Optional[Set[str]] = None,
    foup_active_ep: str = "",
) -> None:
    """
    renewal lead 등 ``sim_now < plan sync`` 구간 — 막대 끝 색만 plan occ 로 보정 (시간축은 sim_now).
    """
    faults = fault_ports or set()
    foup_phase: Dict[str, str] = {}
    ep_active = str(foup_active_ep or "").strip().upper()
    if ep_active.startswith("EP"):
        foup_phase[ep_active] = BAR_STATE_PROC
    ep_states = [
        _resolve_port_bar_state(ep, occ, foup_phase, faults) for ep in ep_list
    ]
    all_ep_st = _aggregate_all_ep_state(ep_states)
    tip_dur = 1e-6
    for row_name in row_order:
        want = all_ep_st if row_name == "ALL_EP" else _resolve_port_bar_state(
            row_name, occ, foup_phase, faults
        )
        segs = rows_state.get(row_name)
        if not isinstance(segs, list):
            segs = []
            rows_state[row_name] = segs
        if not segs:
            if want != BAR_STATE_EMPTY:
                segs.append({"state": want, "dur": tip_dur})
            continue
        if bar_state_from_seg(segs[-1]) != want:
            segs.append({"state": want, "dur": tip_dur})


def _collect_foup_milestones_from_items(
    sorted_items: Tuple[SimTimelineItem, ...],
    seq_start: int = 0,
) -> List[Tuple[float, int, str, Any]]:
    """FOUP START/END 마일스톤 (event sim 시각)."""
    out: List[Tuple[float, int, str, Any]] = []
    seq_i = int(seq_start)
    for it in sorted_items or ():
        kind = str(it.kind or "").strip().lower()
        if kind != "event" or not isinstance(it.payload, dict):
            continue
        try:
            t_ev = float(getattr(it, "t", 0.0) or 0.0)
        except Exception:
            t_ev = 0.0
        p = dict(it.payload)
        seq_u = _s_val(p.get("seq")).upper()
        port = _canonical_sim_port_key(_s_val(p.get("port_id")))
        if seq_u == "FOUP_PROCESS_START" and port.startswith("EP"):
            out.append((float(t_ev), seq_i, "foup_start", port))
            seq_i += 1
        elif seq_u == "FOUP_PROCESS_END" and port.startswith("EP"):
            out.append((float(t_ev), seq_i, "foup_end", port))
            seq_i += 1
    return out


def _collect_port_occ_snap_milestones_from_items(
    sorted_items: Tuple[SimTimelineItem, ...],
    seq_start: int = 0,
) -> List[Tuple[float, int, str, Any]]:
    """
    progress/event 의 ``ports_occupancy`` 를 해당 sim 시각 t 에 즉시 반영.
    JSON 종료 시각(occ)과 별도 — EP2·INOUT·BP 등 비-애니 이벤트 점유도 막대에 반영.
    """
    out: List[Tuple[float, int, str, Any]] = []
    seq_i = int(seq_start)
    last_sig = ""
    for it in sorted_items or ():
        kind = str(it.kind or "").strip().lower()
        if kind not in ("event", "progress") or not isinstance(it.payload, dict):
            continue
        try:
            t_ev = float(getattr(it, "t", 0.0) or 0.0)
        except Exception:
            t_ev = 0.0
        p = dict(it.payload)
        if kind == "progress":
            st = _s_val(p.get("status")).upper()
            el = _f_val(p.get("elapsed"), 0.0)
            if st != "RUNNING" or abs(el) > 1e-9:
                continue
        po = p.get("ports_occupancy")
        if not isinstance(po, dict) or not po:
            continue
        occ_d = {
            str(k).strip().upper(): str(v or "")
            for k, v in po.items()
            if str(k).strip()
        }
        if not occ_d:
            continue
        sig = f"{t_ev:.4f}|{sorted(occ_d.items())}"
        if sig == last_sig:
            continue
        last_sig = sig
        out.append((float(t_ev), seq_i, "occ_snap", dict(occ_d)))
        seq_i += 1
    return out


def _port_ui_milestones_from_tuples(
    milestones: List[Tuple[float, int, str, Any]],
) -> Tuple[Any, ...]:
    """finalize 튜플 → ``PlaybackUIMilestone`` (occ replay SSOT)."""
    try:
        from .playback_plan import PlaybackUIMilestone

        ms_list: List[Any] = []
        for t, o, k, d in milestones or ():
            kk = str(k)
            if kk == "occ_full":
                ms_list.append(
                    PlaybackUIMilestone(t_sim=float(t), order=int(o), kind="occ_full", data=d)
                )
            elif kk == "occ_snap":
                ms_list.append(
                    PlaybackUIMilestone(t_sim=float(t), order=int(o), kind="occ_snap", data=d)
                )
            elif kk == "occ_plan":
                ms_list.append(
                    PlaybackUIMilestone(t_sim=float(t), order=int(o), kind="occ_plan", data=d)
                )
            elif kk == "occ" and isinstance(d, tuple) and len(d) >= 2:
                src, panel_occ = d[0], d[1]
                if isinstance(src, dict):
                    occ_pred = predict_ports_occupancy_after_anim(
                        dict(panel_occ or {}) if isinstance(panel_occ, dict) else {},
                        dict(src),
                    )
                    ms_list.append(
                        PlaybackUIMilestone(
                            t_sim=float(t),
                            order=int(o),
                            kind="occ_plan",
                            data=(dict(src), dict(occ_pred)),
                        )
                    )
        return tuple(ms_list)
    except Exception:
        return ()


def replay_bar_rows_at_t(
    milestones: List[Tuple[float, int, str, Any]],
    *,
    sorted_items: Tuple[SimTimelineItem, ...],
    t_cut: float,
    row_order: List[str],
    ep_list: List[str],
    all_ports: List[str],
    faults: Set[str],
    plan_ports_at_t: Optional[Dict[str, str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    plan UI 마일스톤 + ``plan_ports_at_t`` SSOT → 막대 rows @ ``t_cut``.

    포트 ``snap.ports_at(t_plan)`` 과 동일 occ 를 막대 끝(tip)에 맞춘다.
    truncate(precomputed) 대신 재생 중 이 함수만 사용하면 포트·막대 불일치가 사라진다.
    """
    ms_sorted = sorted(
        list(milestones or ()),
        key=lambda m: (float(m[0]), int(m[1])),
    )
    rows: Dict[str, List[Dict[str, Any]]] = {r: [] for r in row_order}
    init_occ = _initial_bar_occ_at_t0(sorted_items, all_ports)
    bar_occ = dict(init_occ)
    foup_phase: Dict[str, str] = {}
    t_cur = 0.0
    t_final = max(0.0, float(t_cut))
    port_ui_ms = _port_ui_milestones_from_tuples(ms_sorted)

    def _sync_bar_occ_from_plan(t_sim: float, *, at_final: bool = False) -> None:
        nonlocal bar_occ
        if at_final and plan_ports_at_t is not None:
            for p in all_ports:
                if p in plan_ports_at_t:
                    bar_occ[p] = str(plan_ports_at_t.get(p, "") or "")
            for ep in ep_list:
                if not str(bar_occ.get(ep, "") or "").strip():
                    foup_phase.pop(ep, None)
            return
        if not port_ui_ms:
            return
        try:
            from .playback_plan import replay_ports_occ_at_t

            replayed = replay_ports_occ_at_t(
                port_ui_ms,
                t_sim=float(t_sim),
                all_ports=all_ports,
                initial_occ=init_occ,
            )
            if isinstance(replayed, dict) and replayed:
                bar_occ = dict(replayed)
        except Exception:
            pass
        for ep in ep_list:
            if not str(bar_occ.get(ep, "") or "").strip():
                foup_phase.pop(ep, None)

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

    for t_m, _ord, kind, data in ms_sorted:
        if float(t_m) > float(t_final) + 1e-9:
            break
        _apply_interval(float(t_m))
        if kind == "foup_start":
            ep = str(data or "").strip().upper()
            if ep:
                foup_phase[ep] = BAR_STATE_PROC
        elif kind == "foup_end":
            ep = str(data or "").strip().upper()
            if ep:
                foup_phase[ep] = BAR_STATE_UNLOAD
        elif kind == "occ_full":
            _sync_bar_occ_from_plan(float(t_m), at_final=False)
        elif kind == "occ_snap":
            _sync_bar_occ_from_plan(float(t_m), at_final=False)
        elif kind in ("occ", "occ_plan"):
            _sync_bar_occ_from_plan(float(t_m), at_final=False)
            src = data[0] if isinstance(data, tuple) and len(data) >= 1 else {}
            if isinstance(src, dict):
                ev = _normalize_anim_event_seq(_s_val(src.get("event") or src.get("event_seq")))
                if ev == "REMOVED":
                    port = _canonical_sim_port_key(_s_val(src.get("port_id")))
                    if port.startswith("EP"):
                        foup_phase.pop(port, None)

    _sync_bar_occ_from_plan(float(t_final), at_final=True)
    if float(t_final) > float(t_cur) + 1e-9:
        _push_bar_rows_for_interval(
            rows,
            row_order,
            ep_list,
            bar_occ,
            foup_phase,
            faults,
            float(t_final) - float(t_cur),
            cap_segments=None,
        )
    elif plan_ports_at_t is not None:
        _push_bar_rows_for_interval(
            rows,
            row_order,
            ep_list,
            bar_occ,
            foup_phase,
            faults,
            1e-6,
            cap_segments=None,
        )

    for r in row_order:
        rows[r] = merge_bar_row_segments(rows.get(r, []))
    return rows


def _finalize_ep_bar_from_milestones(
    milestones: List[Tuple[float, int, str, Any]],
    *,
    sorted_items: Tuple[SimTimelineItem, ...],
    final_sim_time: float,
    total_est: float,
    row_order: List[str],
    ep_list: List[str],
    all_ports: List[str],
    buffer_ports: Tuple[str, ...],
    faults: Set[str],
) -> EpBarPrecomputed:
    t_final = max(0.0, float(final_sim_time))
    plan_occ: Optional[Dict[str, str]] = None
    try:
        from .playback_plan import replay_ports_occ_at_t

        port_ui_ms = _port_ui_milestones_from_tuples(list(milestones or ()))
        if port_ui_ms:
            plan_occ = replay_ports_occ_at_t(
                port_ui_ms,
                t_sim=float(t_final),
                all_ports=all_ports,
                initial_occ=_initial_bar_occ_at_t0(sorted_items, all_ports),
            )
    except Exception:
        plan_occ = None

    rows = replay_bar_rows_at_t(
        list(milestones or ()),
        sorted_items=sorted_items,
        t_cut=float(t_final),
        row_order=row_order,
        ep_list=ep_list,
        all_ports=all_ports,
        faults=faults,
        plan_ports_at_t=plan_occ,
    )

    te = float(total_est)
    if te <= 0.0:
        te = max(30.0, t_final)

    dur_by = compute_duration_sec_by_row(rows)
    return EpBarPrecomputed(
        total_est=float(te),
        rows=rows,
        ep_ports=tuple(ep_list),
        buffer_ports=buffer_ports,
        row_order=tuple(row_order),
        duration_sec_by_row=dur_by,
        fault_ports=tuple(sorted(faults)),
    )


def build_ep_bar_from_playback_schedule(
    schedule: Any,
    items: Tuple[SimTimelineItem, ...],
    *,
    final_sim_time: float,
    ep_ports: Optional[List[str]] = None,
    ep_count_idx: int = 0,
    ebs_enabled: bool = True,
    fault_ports: Optional[Set[str]] = None,
) -> EpBarPrecomputed:
    """
    프리런 ``PlaybackSchedule.ui_milestones`` SSOT → 5상태 막대 (포트와 동일 마일스톤).
    """
    total_est = max(0.0, float(final_sim_time))
    faults = {str(p).strip().upper() for p in (fault_ports or set()) if str(p).strip()}
    row_order = bar_graph_row_order(int(ep_count_idx), ebs_enabled=bool(ebs_enabled))
    ep_list = [r for r in row_order if r.startswith("EP")]
    if isinstance(ep_ports, list) and ep_ports:
        ep_list = [str(x).strip().upper() for x in ep_ports if str(x).strip().upper().startswith("EP")]
    if not ep_list:
        ep_list = ["EP1", "EP2"] + (["EP3"] if int(ep_count_idx) else [])
    buffer_ports = tuple(r for r in row_order if r.startswith("BP"))
    all_ports = [r for r in row_order if r != "ALL_EP"]

    sorted_items = _sorted_timeline_items_for_bar(items)

    milestones: List[Tuple[float, int, str, Any]] = []
    ui_ms = getattr(schedule, "ui_milestones", None) or ()
    if ui_ms:
        try:
            from .playback_plan import milestones_to_finalize_tuples

            milestones = milestones_to_finalize_tuples(ui_ms)
        except Exception:
            milestones = []
    if not milestones:
        return build_ep_bar_from_timeline_replay(
            items,
            final_sim_time=float(final_sim_time),
            ep_ports=ep_ports,
            ep_count_idx=int(ep_count_idx),
            ebs_enabled=bool(ebs_enabled),
            fault_ports=fault_ports,
        )

    return _finalize_ep_bar_from_milestones(
        milestones,
        sorted_items=sorted_items,
        final_sim_time=float(final_sim_time),
        total_est=float(total_est),
        row_order=row_order,
        ep_list=ep_list,
        all_ports=all_ports,
        buffer_ports=buffer_ports,
        faults=faults,
    )


def _sorted_timeline_items_for_bar(items: Tuple[SimTimelineItem, ...]) -> Tuple[SimTimelineItem, ...]:
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


def build_ep_bar_from_timeline_replay(
    items: Tuple[SimTimelineItem, ...],
    *,
    final_sim_time: float,
    ep_ports: Optional[List[str]] = None,
    ep_count_idx: int = 0,
    ebs_enabled: bool = True,
    fault_ports: Optional[Set[str]] = None,
) -> EpBarPrecomputed:
    """
    프리런 타임라인 → 5상태 막대 사전 계산 (EP + ALL_EP + INOUT + BP).

    occ 마일스톤: ``occ_snap`` (이벤트 sim 시각 ports_occupancy),
    ``playback_port_sync_sim_time_from_progress`` (JSON 종료·renewal 축).
    FOUP START/END 는 event sim 시각 그대로.
    """
    total_est = max(0.0, float(final_sim_time))
    faults = {str(p).strip().upper() for p in (fault_ports or set()) if str(p).strip()}
    row_order = bar_graph_row_order(int(ep_count_idx), ebs_enabled=bool(ebs_enabled))
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

    event_by_t: Dict[float, Dict[str, Any]] = {}
    for it in sorted_items:
        if str(it.kind or "").strip().lower() != "event" or not isinstance(it.payload, dict):
            continue
        try:
            t_e = float(getattr(it, "t", 0.0) or 0.0)
        except Exception:
            t_e = 0.0
        event_by_t[float(t_e)] = dict(it.payload)

    def _event_payload_at_t(t_sim: float) -> Optional[Dict[str, Any]]:
        ep = event_by_t.get(float(t_sim))
        if isinstance(ep, dict):
            return ep
        best: Optional[Dict[str, Any]] = None
        best_d = 1e9
        for t_k, cand in event_by_t.items():
            d = abs(float(t_k) - float(t_sim))
            if d <= 1e-4 and d < best_d:
                best_d = d
                best = cand
        return best

    Milestone = Tuple[float, int, str, Any]
    milestones: List[Milestone] = []
    milestones.extend(_collect_foup_milestones_from_items(sorted_items, seq_start=0))
    seq_i = len(milestones)
    milestones.extend(_collect_port_occ_snap_milestones_from_items(sorted_items, seq_start=seq_i))
    seq_i = len(milestones)

    panel_occ: Dict[str, str] = {}
    for it in sorted_items:
        kind = str(it.kind or "").strip().lower()
        try:
            t_ev = float(getattr(it, "t", 0.0) or 0.0)
        except Exception:
            t_ev = 0.0
        if kind == "progress" and isinstance(it.payload, dict):
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
            json_path: Optional[str] = None
            try:
                from .playback_schedule import resolve_json_path_for_timeline_event

                event_p = _event_payload_at_t(float(t_ev))
                _, jp, _ = resolve_json_path_for_timeline_event(
                    ev,
                    event_p,
                    _s_val(p.get("linked_anim_json")),
                )
                if jp:
                    json_path = str(jp)
            except Exception:
                json_path = None
            try:
                from .json_playback_timing import playback_port_sync_sim_time_from_progress

                t_port_sync = playback_port_sync_sim_time_from_progress(
                    p,
                    fallback_t=_f_val(p.get("sim_time", it.t), t_ev),
                    json_path=json_path,
                )
            except Exception:
                t_port_sync = _json_end_sim_time_from_progress(
                    p,
                    fallback_t=_f_val(p.get("sim_time", it.t), t_ev),
                )
            if t_port_sync is None:
                continue
            milestones.append(
                (
                    float(t_port_sync),
                    seq_i,
                    "occ",
                    (
                        _post_anim_src_from_progress_and_event(
                            p, _event_payload_at_t(float(t_ev))
                        ),
                        dict(panel_occ),
                    ),
                )
            )
            seq_i += 1

    milestones.sort(key=lambda m: (float(m[0]), int(m[1])))

    return _finalize_ep_bar_from_milestones(
        milestones,
        sorted_items=sorted_items,
        final_sim_time=float(final_sim_time),
        total_est=float(total_est),
        row_order=row_order,
        ep_list=ep_list,
        all_ports=all_ports,
        buffer_ports=buffer_ports,
        faults=faults,
    )


def build_ep_bar_from_progress_items(
    items: Tuple[SimTimelineItem, ...],
    *,
    final_sim_time: float,
    ep_ports: Optional[List[str]] = None,
    ep_count_idx: int = 0,
    ebs_enabled: bool = True,
    fault_ports: Optional[Set[str]] = None,
) -> EpBarPrecomputed:
    return build_ep_bar_from_timeline_replay(
        items,
        final_sim_time=float(final_sim_time),
        ep_ports=ep_ports,
        ep_count_idx=int(ep_count_idx),
        ebs_enabled=bool(ebs_enabled),
        fault_ports=fault_ports,
    )


def _bar_copy_round_sec(sec: float) -> float:
    s = float(sec)
    if abs(s - round(s)) < 0.05:
        return float(int(round(s)))
    return round(s, 1)


def _bar_copy_pct(part: float, whole: float) -> float:
    if float(whole) <= 1e-9:
        return 0.0
    return round(100.0 * float(part) / float(whole), 1)


def build_bar_graph_copy_document(
    *,
    screen: int,
    bar: EpBarPrecomputed,
    sim_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """클립보드 복사용 막대그래프 JSON — 행별 시간 구간·상태별 합계(초·%)."""
    _ = sim_snapshot  # API 호환용 (현재 export 에 메타 미포함)
    row_order = (
        normalize_bar_graph_row_order(list(bar.row_order))
        if bar.row_order
        else normalize_bar_graph_row_order([str(k) for k in (bar.rows or {}).keys()])
    )
    try:
        total_est = float(bar.total_est)
    except Exception:
        total_est = 0.0

    rows_out: Dict[str, Any] = {}
    for row_name in row_order:
        rk = str(row_name)
        segs = bar.rows.get(rk, []) if isinstance(bar.rows, dict) else []
        seg_list = segs if isinstance(segs, list) else []

        row_total = 0.0
        for s in seg_list:
            if not isinstance(s, dict):
                continue
            try:
                row_total += float(s.get("dur", 0.0))
            except Exception:
                pass
        if row_total <= 1e-9:
            row_total = float(total_est)

        segments_out: List[Dict[str, Any]] = []
        acc_by_state: Dict[str, float] = {st: 0.0 for st in BAR_STATES}
        t_cur = 0.0
        for s in seg_list:
            if not isinstance(s, dict):
                continue
            try:
                dur = float(s.get("dur", 0.0))
            except Exception:
                dur = 0.0
            if dur <= 1e-9:
                continue
            st = bar_state_from_seg(s)
            t_end = float(t_cur) + float(dur)
            segments_out.append(
                {
                    "from_sec": _bar_copy_round_sec(t_cur),
                    "to_sec": _bar_copy_round_sec(t_end),
                    "state": st,
                    "pct": _bar_copy_pct(dur, row_total),
                }
            )
            acc_by_state[st] = float(acc_by_state.get(st, 0.0)) + float(dur)
            t_cur = float(t_end)

        by_state: Dict[str, Dict[str, float]] = {}
        for st in BAR_STATES:
            sec = float(acc_by_state.get(st, 0.0) or 0.0)
            if sec <= 0.05:
                continue
            by_state[st] = {
                "sec": _bar_copy_round_sec(sec),
                "pct": _bar_copy_pct(sec, row_total),
            }

        rows_out[rk] = {
            "total_sec": _bar_copy_round_sec(row_total),
            "segments": segments_out,
            "by_state": by_state,
        }

    return {
        "screen": int(screen),
        "total_sec": _bar_copy_round_sec(total_est),
        "row_order": [str(r) for r in row_order],
        "rows": rows_out,
    }


def _timetable_meta_to_dict(m: TimetableRowMeta) -> Dict[str, Any]:
    return {
        "row_index": int(m.row_index),
        "t": float(m.t),
        "kind": str(m.kind),
        "display_line": str(m.display_line),
        "through_item_index": int(m.through_item_index),
        "json_obj": dict(m.json_obj or {}),
    }


def _empty_pct_key(row_name: str) -> str:
    """막대 행 이름 → empty 비율 키. 예: ``ALL_EP`` → ``all_ep_empty_pct``."""
    return f"{str(row_name or '').strip().lower()}_empty_pct"


def compute_bar_graph_empty_pct(bar: EpBarPrecomputed, row_order: List[str]) -> Dict[str, float]:
    """막대별 empty 상태 비율(%) — 행 총 시간 대비 empty 누적 초.

    ``bar_graph.empty_pct`` 로 export 된다. 예:
    ``{"all_ep_empty_pct": 32.5, "ep1_empty_pct": 40.0, ...}``
    """
    out: Dict[str, float] = {}
    durs_by_row = bar.duration_sec_by_row or {}
    for row_name in row_order or []:
        durs = durs_by_row.get(row_name)
        if not isinstance(durs, dict):
            out[_empty_pct_key(row_name)] = 0.0
            continue
        row_total = sum(float(v or 0.0) for v in durs.values())
        empty_sec = float(durs.get(BAR_STATE_EMPTY, 0.0) or 0.0)
        pct = (empty_sec / row_total * 100.0) if row_total > 1e-9 else 0.0
        out[_empty_pct_key(row_name)] = round(pct, 2)
    return out


def compute_cumulative_empty_pct(segs: List[Dict[str, Any]], t: float) -> float:
    """막대 세그먼트를 ``t`` 초까지 잘라, 진행 시간 대비 empty 누적 비율(%).

    timetable 행별 ``all_ep_empty_pct`` 계산에 사용한다 (t<=0 이면 0.0).
    """
    t_end = float(t or 0.0)
    if t_end <= 1e-9:
        return 0.0
    cursor = 0.0
    empty_acc = 0.0
    for seg in segs or []:
        if not isinstance(seg, dict):
            continue
        dur = max(0.0, float(seg.get("dur", 0.0) or 0.0))
        if dur <= 0.0:
            continue
        seg_start = cursor
        seg_end = cursor + dur
        cursor = seg_end
        overlap = min(seg_end, t_end) - seg_start
        if overlap <= 0.0:
            break
        if bar_state_from_seg(seg) == BAR_STATE_EMPTY:
            empty_acc += overlap
        if seg_end >= t_end:
            break
    return round(min(100.0, empty_acc / t_end * 100.0), 2)


def build_prerun_export_document(
    *,
    screen: int,
    result: SimPreRunResult,
    bar: EpBarPrecomputed,
    timetable_metas: Optional[List[TimetableRowMeta]] = None,
    seek_snapshots_count: int = 0,
    sim_snapshot: Optional[Dict[str, Any]] = None,
    sim_speed: Optional[float] = None,
) -> Dict[str, Any]:
    """웹·외부 연동용 프리런 통합 JSON (막대 세그먼트 + 상태별 누적 초 포함)."""
    snap = dict(sim_snapshot or {})
    ep_count = ep_count_from_snapshot(snap, default=2)
    ep_count_idx = 1 if ep_count >= 3 else 0
    ebs_enable = bool(snap.get("ebs_enabled", True))
    try:
        speed = max(0.1, float(sim_speed if sim_speed is not None else snap.get("speed", 1.0) or 1.0))
    except Exception:
        speed = 1.0
    row_order = (
        normalize_bar_graph_row_order(list(bar.row_order))
        if bar.row_order
        else bar_graph_row_order(ep_count_idx)
    )

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

    # timetable 각 행 — 해당 행 t 시점까지 ALL_EP 진행 시간 대비 empty 누적 % 를 동봉.
    all_ep_segs = bar.rows.get("ALL_EP", []) if isinstance(bar.rows, dict) else []
    tt_rows = []
    for m in timetable_metas or []:
        if isinstance(m, TimetableRowMeta):
            row = _timetable_meta_to_dict(m)
            jo = row.get("json_obj")
            if isinstance(jo, dict):
                jo["all_ep_empty_pct"] = compute_cumulative_empty_pct(
                    all_ep_segs, float(m.t)
                )
            tt_rows.append(row)

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
        "case": max(0, int(screen) - 1),
        "sim": {
            "ep_count_idx": ep_count_idx,
            "ep_count": ep_count,
            "ebs_enable": ebs_enable,
            "speed": round(float(speed), 4),
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
            "empty_pct": compute_bar_graph_empty_pct(bar, row_order),
        },
    }


def write_prerun_export_json(path: str, doc: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)


def build_prerun_export_document_web_slim(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Web 전송용 프리런 export JSON 간소화.

    NOTE:
    - 원본 doc(Kit 내부용/디스크 저장용)는 그대로 두고, 웹/슬림 파일에만 적용한다.
    - 규칙은 문서 `docs/tbs_control_2_prerun_web_payload_slimming_plan_ko.md`를 따른다.
    """
    out: Dict[str, Any] = dict(doc or {})

    # 7.0.1 Top-level 제거
    out.pop("version", None)
    sim = out.get("sim")
    if isinstance(sim, dict):
        sim2 = dict(sim)
        sim2.pop("ep_count_idx", None)
        out["sim"] = sim2

    # 7.0.2 timeline 제거 + 7.0.3 timetable_rows 축소
    tl = out.get("timeline")
    if isinstance(tl, dict):
        tl2 = dict(tl)
        for k in ("item_count", "final_sim_time_sec", "total_est_sec", "seek_snapshots_count"):
            tl2.pop(k, None)

        # timetable_rows: meta row list -> filtered json_obj -> drop fields
        # (string 화하지 않고 object 그대로 둔다 — 웹은 t 배열만 받고,
        #  개별 행은 T2V_request_time_table 로 이 object 를 그대로 조회한다.)
        rows_in = tl2.get("timetable_rows")
        rows_out: List[Dict[str, Any]] = []
        if isinstance(rows_in, list):
            for row in rows_in:
                if not isinstance(row, dict):
                    continue
                jo = row.get("json_obj")
                if not isinstance(jo, dict):
                    continue
                kind = str(jo.get("kind", "") or "").strip().lower()
                ev = str(jo.get("event", "") or "").strip()
                keep = False
                if kind == "event" and ev in ("FOUP_PROCESS_START", "FOUP_PROCESS_END"):
                    keep = True
                elif kind == "step" and bool(str(jo.get("anim", "") or "").strip()):
                    keep = True
                if not keep:
                    continue
                jo2 = dict(jo)
                # 필드 삭제: screen/kind/process_time_priority
                for k in ("screen", "kind", "process_time_priority"):
                    jo2.pop(k, None)
                rows_out.append(jo2)
        tl2["timetable_rows"] = rows_out
        out["timeline"] = tl2

    # 7.0.4 bar_graph.segments 축소: dict -> [state, dur_sec]
    bg = out.get("bar_graph")
    if isinstance(bg, dict):
        bg2 = dict(bg)
        segs = bg2.get("segments")
        if isinstance(segs, dict):
            segs2: Dict[str, Any] = {}
            for row_name, seg_list in segs.items():
                if not isinstance(seg_list, list):
                    continue
                new_list: List[Any] = []
                for s in seg_list:
                    if not isinstance(s, dict):
                        continue
                    st = s.get("state")
                    dur = s.get("dur_sec")
                    if st is None or dur is None:
                        continue
                    new_list.append([st, dur])
                segs2[str(row_name)] = new_list
            bg2["segments"] = segs2
        bg = bg2
        out["bar_graph"] = bg

    return out


__all__ = [
    "BAR_STATES",
    "BAR_STATE_COLORS",
    "BAR_STATE_COLORS_HEX",
    "EpBarPrecomputed",
    "allocate_bar_segment_pixels",
    "bar_graph_row_order",
    "normalize_bar_graph_row_order",
    "get_bar_state_colors_hex",
    "get_bar_state_colors_kit",
    "hex_to_kit_ui_color",
    "bar_state_color",
    "bar_state_from_seg",
    "build_ep_bar_from_progress_items",
    "build_ep_bar_from_playback_schedule",
    "build_ep_bar_from_timeline_replay",
    "build_bar_graph_copy_document",
    "build_prerun_export_document",
    "build_prerun_export_document_web_slim",
    "compute_bar_graph_empty_pct",
    "compute_cumulative_empty_pct",
    "compute_duration_sec_by_row",
    "format_row_state_duration_summary",
    "merge_bar_row_segments",
    "replay_bar_rows_at_t",
    "truncate_bar_rows_at_t",
    "overlay_bar_rows_tip_from_occ",
    "write_prerun_export_json",
]
