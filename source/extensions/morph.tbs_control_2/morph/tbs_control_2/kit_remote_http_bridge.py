# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
Kit 내 HTTP 브리지 — 브라우저에서 TBS 제어창·USD Load 와 동일 동작을 호출한다.

사용:
  확장 로드 시 기본으로 HTTP 브리지가 켜진다. 끄려면 TBS_REMOTE_UI=0 (또는 false, no, off).
  브라우저에서 http://127.0.0.1:<포트>/ 접속 (포트 기본 8720).

정적 파일: 확장 루트 web/tbs_kit_remote/
포트: TBS_REMOTE_UI_PORT (기본 8720)
바인드 주소: TBS_REMOTE_UI_BIND (기본 127.0.0.1 = 로컬만). 원격 브라우저는 0.0.0.0 등.

모든 ext / omni.ui 접근은 메인 스레드(업데이트 스트림)에서만 수행한다.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections import deque
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import omni.kit.app as app

from . import sim_multi_view
from .kit_chrome_visibility import apply_kit_chrome_hidden, is_kit_chrome_hidden
from .control_window import (
    _close_sim_gate_dialog,
    _ep_count_idx_for_port_panel,
    _on_save_sim_settings_to_screen,
    on_copy_sim_progress,
    on_sim_ep_count_changed,
    on_sim_reset_clicked,
    on_sim_start_clicked,
    on_sim_stop_clicked,
    on_xml_ok_clicked,
    on_xml_run_clicked,
    on_xml_seq_changed,
    refresh_object_list,
)
from .control_sim_bar_graph import (
    BAR_STATES,
    bar_graph_row_order,
    bar_state_from_seg,
    compute_duration_sec_by_row,
    get_bar_state_colors_hex,
)
from .sim_control_defaults import SIM_CONTROL_DEFAULTS as _SIM_DEF
from .tbs_data_paths import resolve_local_data_path
from .tbs_usd_window import default_load_usd_path
from .usd_loader_utils import get_resource_usd_list, path_has_supported_stage_extension

_WEB_ROOT = Path(__file__).resolve().parent.parent.parent / "web" / "tbs_kit_remote"

_server: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_update_sub: Any = None
_ext_ref: Any = None
_pending_main: Deque[Tuple[Future, Callable[[], Any]]] = deque()
_pending_lock = threading.Lock()
_DEFAULT_PORT = 8720


def _run_on_main(fn: Callable[[], Any]) -> Any:
    fut: Future = Future()

    def _wrap() -> None:
        try:
            fut.set_result(fn())
        except Exception as e:
            fut.set_exception(e)

    with _pending_lock:
        _pending_main.append((fut, _wrap))
    return fut.result(timeout=120.0)


def _pump_main_queue(_e: Any) -> None:
    while True:
        with _pending_lock:
            if not _pending_main:
                break
            _, run = _pending_main.popleft()
        try:
            run()
        except Exception:
            pass


def _prerun_export_by_screen_for_api(ext: Any) -> Dict[str, Any]:
    raw = getattr(ext, "_sim_prerun_export_json_by_screen", None)
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            out[str(k)] = v
    return out


def _serialize_ep_timeline_for_screen(ext: Any, scr_key: str) -> Dict[str, Any]:
    """포트 아래 EP 타임라인(막대) 상태 — Kit ``_update_ep_timeline_under_port_state`` rows 와 동일."""
    sk = str(scr_key)
    st_by = getattr(ext, "_sim_ep_occ_timeline_state_by_screen", None)
    st = st_by.get(sk) if isinstance(st_by, dict) else None
    try:
        si = int(sk.strip() or "1")
    except Exception:
        si = 1
    ep_idx = int(_ep_count_idx_for_port_panel(ext, si))
    row_order = bar_graph_row_order(ep_idx)
    hex_colors = get_bar_state_colors_hex()
    base = {
        "t_now": 0.0,
        "total_est": 30.0,
        "rows": {},
        "empty_acc": {k: 0.0 for k in row_order},
        "row_order": row_order,
        "states": list(BAR_STATES),
        "colors": dict(hex_colors),
        "duration_sec_by_row": {k: {} for k in row_order},
        "has_prerun": False,
    }
    if not isinstance(st, dict):
        return base
    t_last = st.get("t_last")
    try:
        t_now = float(t_last) if t_last is not None else 0.0
    except Exception:
        t_now = 0.0
    total_est = st.get("total_est_fixed")
    try:
        total_est_f = float(total_est) if total_est is not None else 0.0
    except Exception:
        total_est_f = 0.0
    if total_est_f <= 0.0:
        total_est_f = max(30.0, t_now * 1.2)
    rows_state = st.get("rows", {})
    rows_out: Dict[str, List[Dict[str, Any]]] = {}
    if isinstance(rows_state, dict):
        for rk in row_order:
            segs = rows_state.get(rk, [])
            if not isinstance(segs, list):
                rows_out[rk] = []
                continue
            row_segs: List[Dict[str, Any]] = []
            for x in segs:
                if not isinstance(x, dict):
                    continue
                try:
                    dur = float(x.get("dur", 0.0))
                except Exception:
                    dur = 0.0
                if dur <= 1e-9:
                    continue
                st_name = bar_state_from_seg(x)
                row_segs.append(
                    {
                        "state": st_name,
                        "dur_sec": dur,
                        "dur": dur,
                        "empty": st_name == "empty",
                        "color": hex_colors.get(st_name, "#888888"),
                    }
                )
            rows_out[rk] = row_segs
    duration_sec_by_row = (
        compute_duration_sec_by_row(rows_state) if isinstance(rows_state, dict) else {}
    )
    empty_acc: Dict[str, float] = {}
    for rk in row_order:
        try:
            empty_acc[rk] = float((duration_sec_by_row.get(rk) or {}).get("empty", 0.0))
        except Exception:
            empty_acc[rk] = 0.0
    export_by = getattr(ext, "_sim_prerun_export_json_by_screen", None)
    has_prerun = isinstance(export_by, dict) and isinstance(export_by.get(sk), dict)
    return {
        "t_now": float(t_now),
        "total_est": float(total_est_f),
        "rows": rows_out,
        "empty_acc": empty_acc,
        "row_order": row_order,
        "states": list(BAR_STATES),
        "colors": dict(hex_colors),
        "duration_sec_by_row": duration_sec_by_row,
        "has_prerun": bool(has_prerun),
    }


def _channel_snapshot_from_ch(ext: Any, ch: Dict[str, Any]) -> Dict[str, Any]:
    si = int(ch.get("screen", 1) or 1)
    sk = str(si)
    port_header = "[포트상태]"
    try:
        ph = ch.get("port_header")
        if ph is not None:
            port_header = str(ph.text or port_header)
    except Exception:
        pass
    ports: Dict[str, str] = {}
    cells = ch.get("port_cells") or {}
    for name in ("INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3"):
        lbl = cells.get(name)
        try:
            if lbl is not None:
                raw = (lbl.text or "").strip()
                if ":" in raw:
                    ports[name] = raw.split(":", 1)[-1].strip() or "-"
                else:
                    ports[name] = raw or "-"
            else:
                ports[name] = "-"
        except Exception:
            ports[name] = "-"
    ep3_visible = True
    try:
        c = ch.get("port_ep3_cell_container")
        if c is not None:
            ep3_visible = bool(c.visible)
    except Exception:
        pass
    bp4_visible = True
    try:
        c = ch.get("port_bp4_cell_container")
        if c is not None:
            bp4_visible = bool(c.visible)
    except Exception:
        pass
    progress = ""
    try:
        pl = ch.get("progress_label")
        if pl is not None:
            progress = str(pl.text or "")
    except Exception:
        pass
    history = ""
    try:
        hl = ch.get("history_label")
        if hl is not None:
            history = str(hl.text or "")
    except Exception:
        pass
    return {
        "screen": si,
        "port_header": port_header,
        "ports": ports,
        "ep3_visible": ep3_visible,
        "bp4_visible": bp4_visible,
        "progress": progress,
        "history": history,
        "ep_timeline": _serialize_ep_timeline_for_screen(ext, sk),
    }


def _apply_per_screen_snapshot(ext: Any, snap: Dict[str, Any]) -> None:
    """멀티 화면 스냅샷 dict → 제어창 모델(``_apply_web_fields`` 키 규격)."""
    if not isinstance(snap, dict) or not snap:
        return
    try:
        ep_idx = int(snap.get("ep_count_idx", _SIM_DEF.ep_count_idx) or _SIM_DEF.ep_count_idx)
    except Exception:
        ep_idx = int(_SIM_DEF.ep_count_idx)
    try:
        lc = max(1, int(snap.get("lot_count", _SIM_DEF.lot_count) or _SIM_DEF.lot_count))
    except Exception:
        lc = int(_SIM_DEF.lot_count)
    try:
        smin = max(0.1, float(snap.get("spawn_min", _SIM_DEF.lot_spawn_min)))
        smax = max(0.1, float(snap.get("spawn_max", _SIM_DEF.lot_spawn_max)))
    except Exception:
        smin, smax = float(_SIM_DEF.lot_spawn_min), float(_SIM_DEF.lot_spawn_max)
    if smin > smax:
        smin, smax = smax, smin
    try:
        pmin = max(0.1, float(snap.get("pue_min", _SIM_DEF.pickup_min)))
        pmax = max(0.1, float(snap.get("pue_max", _SIM_DEF.pickup_max)))
    except Exception:
        pmin, pmax = float(_SIM_DEF.pickup_min), float(_SIM_DEF.pickup_max)
    if pmin > pmax:
        pmin, pmax = pmax, pmin

    def _g(key: str, default: float = 5.0) -> float:
        try:
            return max(0.1, float(snap.get(key, default)))
        except Exception:
            return default

    f: Dict[str, Any] = {
        "lot_count": lc,
        "ep_count_index": ep_idx,
        "lot_spawn_min": smin,
        "lot_spawn_max": smax,
        "pickup_min": pmin,
        "pickup_max": pmax,
        "oht_min": _g("oht_bp1_min", float(_SIM_DEF.oht_to_bp1_min)),
        "oht_max": _g("oht_bp1_max", float(_SIM_DEF.oht_to_bp1_max)),
        "bp1_bp_min": _g("bp1_bp_min", float(_SIM_DEF.bp1_to_bp_min)),
        "bp1_bp_max": _g("bp1_bp_max", float(_SIM_DEF.bp1_to_bp_max)),
        "bp_ep_min": _g("bp_ep_min", float(_SIM_DEF.bp_to_ep_min)),
        "bp_ep_max": _g("bp_ep_max", float(_SIM_DEF.bp_to_ep_max)),
        "ep_oht_min": _g("ep_oht_min", float(_SIM_DEF.ep_to_oht_min)),
        "ep_oht_max": _g("ep_oht_max", float(_SIM_DEF.ep_to_oht_max)),
        "init_inout": bool(snap.get("init_inout")),
        "init_bp1": bool(snap.get("init_bp1")),
        "init_bp2": bool(snap.get("init_bp2")),
        "init_bp3": bool(snap.get("init_bp3")),
        "init_bp4": bool(snap.get("init_bp4")),
        "init_ep1": bool(snap.get("init_ep1")),
        "init_ep2": bool(snap.get("init_ep2")),
        "init_ep3": bool(snap.get("init_ep3")),
        "fault_inout": bool(snap.get("fault_inout")),
        "fault_bp1": bool(snap.get("fault_bp1")),
        "fault_bp2": bool(snap.get("fault_bp2")),
        "fault_bp3": bool(snap.get("fault_bp3")),
        "fault_bp4": bool(snap.get("fault_bp4")),
        "fault_ep1": bool(snap.get("fault_ep1")),
        "fault_ep2": bool(snap.get("fault_ep2")),
        "fault_ep3": bool(snap.get("fault_ep3")),
        "foup_proc_min": _g("foup_proc_min", float(_SIM_DEF.foup_process_min)),
        "foup_proc_max": _g("foup_proc_max", float(_SIM_DEF.foup_process_max)),
    }
    _apply_web_fields(ext, f)


def _snapshot(ext: Any) -> Dict[str, Any]:
    def _txt(get_lbl, get_model):
        try:
            if get_lbl() is not None:
                t = get_lbl().text
                if t:
                    return t
        except Exception:
            pass
        try:
            if get_model() is not None:
                return get_model().as_string or ""
        except Exception:
            pass
        return ""

    usd_status = ""
    try:
        win = getattr(ext, "_tbs_usd_window", None)
        if win is not None:
            lbl = getattr(win, "_log_label", None)
            if lbl is not None:
                t = (lbl.text or "").strip()
                if t and t != "(no log yet)":
                    usd_status = t
            if not usd_status:
                master = getattr(win, "_master", None)
                mp = str(getattr(master, "master_path", "") or "").strip() if master else ""
                if mp:
                    usd_status = f"Master: {mp}"
            if not usd_status:
                mdl = getattr(win, "_master_path_model", None)
                if mdl is not None:
                    usd_status = mdl.get_value_as_string() or ""
    except Exception:
        pass

    progress = _txt(lambda: getattr(ext, "_sim_progress_label", None), lambda: getattr(ext, "_sim_progress_text", None))
    history = _txt(lambda: getattr(ext, "_sim_history_label", None), lambda: getattr(ext, "_sim_history_text", None))
    sim_line = _txt(lambda: getattr(ext, "_sim_history_label", None), lambda: getattr(ext, "_sim_history_text", None))

    port_header = "[포트상태]"
    try:
        if getattr(ext, "_sim_port_state_header_label", None) is not None:
            port_header = ext._sim_port_state_header_label.text or port_header
    except Exception:
        pass

    ports: Dict[str, str] = {}
    cells = getattr(ext, "_sim_port_cells", None) or {}
    for name in ("INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3"):
        lbl = cells.get(name)
        try:
            if lbl is not None:
                raw = (lbl.text or "").strip()
                if ":" in raw:
                    ports[name] = raw.split(":", 1)[-1].strip() or "-"
                else:
                    ports[name] = raw or "-"
            else:
                ports[name] = "-"
        except Exception:
            ports[name] = "-"

    ep3_visible = True
    try:
        c = getattr(ext, "_sim_port_ep3_cell_container", None)
        if c is not None:
            ep3_visible = bool(c.visible)
    except Exception:
        pass

    bp4_visible = True
    try:
        c = getattr(ext, "_sim_port_bp4_cell_container", None)
        if c is not None:
            bp4_visible = bool(c.visible)
    except Exception:
        pass

    kit_app = ""
    try:
        kit_app = app.get_app().get_name() or ""
    except Exception:
        pass

    try:
        n_split = max(1, min(4, int(getattr(ext, "_sim_viewport_split_count", 1) or 1)))
    except Exception:
        n_split = 1
    split_row_visible = False
    try:
        row = getattr(ext, "_sim_multi_split_row", None)
        if row is not None:
            split_row_visible = bool(row.visible)
    except Exception:
        pass

    # 화면1 EP 막대(포트 점유 타임라인): USD 미로드·단일 모니터여도 시뮬 중 웹이 그릴 수 있게 항상 내려준다.
    ep_timeline_root = _serialize_ep_timeline_for_screen(ext, "1")

    # 멀티 모니터 스냅샷은 USD 로드 후 분할 행이 켜진 경우에만 내려준다(웹이 레거시 패널과 이중 표시되지 않도록).
    channels: List[Dict[str, Any]] = []
    chans = getattr(ext, "_sim_monitor_channels", None)
    if split_row_visible:
        if isinstance(chans, list) and chans:
            for ch in chans:
                if isinstance(ch, dict):
                    channels.append(_channel_snapshot_from_ch(ext, ch))
        else:
            channels.append(
                {
                    "screen": 1,
                    "port_header": port_header,
                    "ports": dict(ports),
                    "ep3_visible": ep3_visible,
                    "bp4_visible": bp4_visible,
                    "progress": progress,
                    "history": history,
                    "ep_timeline": ep_timeline_root,
                }
            )

    snaps = list(getattr(ext, "_sim_per_screen_snapshots", None) or [])
    while len(snaps) < 4:
        snaps.append(None)
    snaps = snaps[:4]
    per_screen_snapshots: List[Optional[Dict[str, Any]]] = []
    for s in snaps:
        if isinstance(s, dict):
            per_screen_snapshots.append(dict(s))
        else:
            per_screen_snapshots.append(None)

    gate_pending = getattr(ext, "_sim_web_gate_pending", None)
    if not isinstance(gate_pending, dict):
        gate_pending = None

    return {
        "usd_status": usd_status,
        "sim_line": sim_line,
        "progress": progress,
        "history": history,
        "port_header": port_header,
        "ports": ports,
        "ep3_visible": ep3_visible,
        "bp4_visible": bp4_visible,
        "kit_app": kit_app,
        "kit_chrome_hidden": is_kit_chrome_hidden(ext),
        "viewport_split_count": int(n_split),
        "sim_multi_split_row_visible": bool(split_row_visible),
        "channels": channels,
        "ep_timeline": ep_timeline_root,
        "prerun_export_by_screen": _prerun_export_by_screen_for_api(ext),
        "per_screen_snapshots": per_screen_snapshots,
        "gate_pending": gate_pending,
    }


def _apply_web_fields(ext: Any, f: Dict[str, Any]) -> None:
    if not f:
        return

    def _set_bool_model(m: Any, v: bool) -> None:
        if m is None:
            return
        try:
            if hasattr(m, "set_value_as_bool"):
                m.set_value_as_bool(bool(v))
                return
        except Exception:
            pass
        try:
            if hasattr(m, "set_value"):
                m.set_value(bool(v))
                return
        except Exception:
            pass

    def _set_int_model(m: Any, v: int) -> None:
        if m is None:
            return
        try:
            if hasattr(m, "set_value_as_int"):
                m.set_value_as_int(int(v))
                return
        except Exception:
            pass
        try:
            if hasattr(m, "set_value"):
                m.set_value(int(v))
                return
        except Exception:
            pass

    def _set_float_model(m: Any, v: float) -> None:
        if m is None:
            return
        try:
            if hasattr(m, "set_value_as_float"):
                m.set_value_as_float(float(v))
                return
        except Exception:
            pass
        try:
            if hasattr(m, "set_value"):
                m.set_value(float(v))
                return
        except Exception:
            pass

    def _i(key: str, default: int = 0) -> int:
        try:
            return int(f.get(key, default))
        except Exception:
            return default

    def _f(key: str, default: float = 0.0) -> float:
        try:
            return float(f.get(key, default))
        except Exception:
            return default

    def _b(key: str) -> bool:
        return bool(f.get(key))

    try:
        _set_int_model(getattr(ext, "_sim_lot_count_model", None), max(1, _i("lot_count", int(_SIM_DEF.lot_count))))
    except Exception:
        pass
    try:
        ext._sim_ep_count_combo.model.get_item_value_model().set_value(
            0 if _i("ep_count_index", int(_SIM_DEF.ep_count_idx)) == 0 else 1
        )
        on_sim_ep_count_changed(ext)
    except Exception:
        pass
    # NOTE: 어떤 1개 모델 접근이 실패해도 speed/log 등 핵심 값 적용이 누락되지 않게 개별 적용한다.
    try:
        _set_float_model(
            getattr(ext, "_sim_lot_spawn_min_model", None), max(0.1, _f("lot_spawn_min", float(_SIM_DEF.lot_spawn_min)))
        )
    except Exception:
        pass
    try:
        _set_float_model(
            getattr(ext, "_sim_lot_spawn_max_model", None), max(0.1, _f("lot_spawn_max", float(_SIM_DEF.lot_spawn_max)))
        )
    except Exception:
        pass
    try:
        _set_float_model(
            getattr(ext, "_sim_pickup_evt_min_model", None), max(0.1, _f("pickup_min", float(_SIM_DEF.pickup_min)))
        )
    except Exception:
        pass
    try:
        _set_float_model(
            getattr(ext, "_sim_pickup_evt_max_model", None), max(0.1, _f("pickup_max", float(_SIM_DEF.pickup_max)))
        )
    except Exception:
        pass
    try:
        _set_float_model(getattr(ext, "_sim_speed_model", None), max(0.1, _f("speed", float(_SIM_DEF.sim_speed))))
    except Exception:
        pass
    try:
        _set_float_model(
            getattr(ext, "_sim_log_interval_model", None), max(0.0, _f("log_interval", float(_SIM_DEF.log_interval_sec)))
        )
    except Exception:
        pass
    try:
        _set_bool_model(getattr(ext, "_sim_confirm_each_step_model", None), _b("confirm_each"))
    except Exception:
        pass
    try:
        _set_bool_model(getattr(ext, "_sim_process_time_priority_model", None), _b("process_time_priority"))
    except Exception:
        pass
    try:
        _set_float_model(
            getattr(ext, "_sim_oht_bp1_min_model", None),
            max(0.1, _f("oht_min", float(_SIM_DEF.oht_to_bp1_min))),
        )
    except Exception:
        pass
    try:
        _set_float_model(
            getattr(ext, "_sim_oht_bp1_max_model", None),
            max(0.1, _f("oht_max", float(_SIM_DEF.oht_to_bp1_max))),
        )
    except Exception:
        pass
    try:
        _set_float_model(
            getattr(ext, "_sim_bp1_bp_min_model", None),
            max(0.1, _f("bp1_bp_min", float(_SIM_DEF.bp1_to_bp_min))),
        )
    except Exception:
        pass
    try:
        _set_float_model(
            getattr(ext, "_sim_bp1_bp_max_model", None),
            max(0.1, _f("bp1_bp_max", float(_SIM_DEF.bp1_to_bp_max))),
        )
    except Exception:
        pass
    try:
        _set_float_model(
            getattr(ext, "_sim_bp_ep_min_model", None),
            max(0.1, _f("bp_ep_min", float(_SIM_DEF.bp_to_ep_min))),
        )
    except Exception:
        pass
    try:
        _set_float_model(
            getattr(ext, "_sim_bp_ep_max_model", None),
            max(0.1, _f("bp_ep_max", float(_SIM_DEF.bp_to_ep_max))),
        )
    except Exception:
        pass
    try:
        _set_float_model(
            getattr(ext, "_sim_ep_oht_min_model", None),
            max(0.1, _f("ep_oht_min", float(_SIM_DEF.ep_to_oht_min))),
        )
    except Exception:
        pass
    try:
        _set_float_model(
            getattr(ext, "_sim_ep_oht_max_model", None),
            max(0.1, _f("ep_oht_max", float(_SIM_DEF.ep_to_oht_max))),
        )
    except Exception:
        pass
    try:
        _set_float_model(
            getattr(ext, "_sim_foup_proc_min_model", None), max(0.1, _f("foup_proc_min", float(_SIM_DEF.foup_process_min)))
        )
    except Exception:
        pass
    try:
        _set_float_model(
            getattr(ext, "_sim_foup_proc_max_model", None), max(0.1, _f("foup_proc_max", float(_SIM_DEF.foup_process_max)))
        )
    except Exception:
        pass
    try:
        if getattr(ext, "_sim_init_inout_model", None) is not None:
            ext._sim_init_inout_model.set_value_as_bool(_b("init_inout"))
        ext._sim_init_bp1_model.set_value_as_bool(_b("init_bp1"))
        ext._sim_init_bp2_model.set_value_as_bool(_b("init_bp2"))
        ext._sim_init_bp3_model.set_value_as_bool(_b("init_bp3"))
        ext._sim_init_bp4_model.set_value_as_bool(_b("init_bp4"))
        ext._sim_init_ep1_model.set_value_as_bool(_b("init_ep1"))
        ext._sim_init_ep2_model.set_value_as_bool(_b("init_ep2"))
        ext._sim_init_ep3_model.set_value_as_bool(_b("init_ep3"))
    except Exception:
        pass
    try:
        # 고장(비가동) 포트
        if getattr(ext, "_sim_fault_inout_model", None) is not None:
            ext._sim_fault_inout_model.set_value_as_bool(_b("fault_inout"))
        if getattr(ext, "_sim_fault_bp1_model", None) is not None:
            ext._sim_fault_bp1_model.set_value_as_bool(_b("fault_bp1"))
        if getattr(ext, "_sim_fault_bp2_model", None) is not None:
            ext._sim_fault_bp2_model.set_value_as_bool(_b("fault_bp2"))
        if getattr(ext, "_sim_fault_bp3_model", None) is not None:
            ext._sim_fault_bp3_model.set_value_as_bool(_b("fault_bp3"))
        if getattr(ext, "_sim_fault_bp4_model", None) is not None:
            ext._sim_fault_bp4_model.set_value_as_bool(_b("fault_bp4"))
        if getattr(ext, "_sim_fault_ep1_model", None) is not None:
            ext._sim_fault_ep1_model.set_value_as_bool(_b("fault_ep1"))
        if getattr(ext, "_sim_fault_ep2_model", None) is not None:
            ext._sim_fault_ep2_model.set_value_as_bool(_b("fault_ep2"))
        if getattr(ext, "_sim_fault_ep3_model", None) is not None:
            ext._sim_fault_ep3_model.set_value_as_bool(_b("fault_ep3"))
    except Exception:
        pass
    try:
        ext._priority_prefix_model.set_value_as_string(str(f.get("priority_prefix", "") or ""))
    except Exception:
        pass
    try:
        idx = max(0, min(6, _i("xml_seq_index", 0)))
        ext._xml_seq_combo.model.get_item_value_model().set_value(idx)
        on_xml_seq_changed(ext)
    except Exception:
        pass
    try:
        ext._xml_from_port_model.set_value_as_int(_i("xml_from", 1))
        ext._xml_to_port_model.set_value_as_int(_i("xml_to", 6))
        ext._xml_port_id_model.set_value_as_int(_i("xml_port_id", 1))
    except Exception:
        pass


def _dispatch_command(ext: Any, data: Dict[str, Any]) -> Dict[str, Any]:
    cmd = str(data.get("cmd", "") or "").strip()
    if cmd == "load_usd":
        path = str(data.get("path", "") or "").strip()
        if not path:
            path = resolve_local_data_path(default_load_usd_path) or str(
                default_load_usd_path or ""
            )
        if not path or not path_has_supported_stage_extension(path):
            return {"ok": False, "error": "invalid usd path"}

        def _open_master() -> bool:
            win = getattr(ext, "_tbs_usd_window", None)
            if win is None:
                return False
            resolved = resolve_local_data_path(path) or path
            return bool(win.open_master_at_path(resolved, log_prefix="HTTP load_usd"))

        ok = bool(_run_on_main(_open_master))
        return {"ok": ok}

    if cmd == "apply_fields":
        fields = data.get("fields")
        if isinstance(fields, dict):
            _apply_web_fields(ext, fields)
        return {"ok": True}

    if cmd == "sim_start":
        fields = data.get("fields")
        if isinstance(fields, dict):
            _apply_web_fields(ext, fields)
        on_sim_start_clicked(ext)
        return {"ok": True}

    if cmd == "sim_stop":
        on_sim_stop_clicked(ext)
        return {"ok": True}

    if cmd == "sim_reset":
        on_sim_reset_clicked(ext)
        return {"ok": True}

    if cmd == "prim_refresh":
        refresh_object_list(ext)
        return {"ok": True}

    if cmd == "log_mode":
        # 표시모드 제거(항상 둘다). 과거 클라이언트 호환을 위해 ok만 반환.
        return {"ok": True}

    if cmd == "copy_progress":
        on_copy_sim_progress(ext)
        return {"ok": True}

    if cmd == "xml_ok":
        fields = data.get("fields")
        if isinstance(fields, dict):
            _apply_web_fields(ext, fields)
        on_xml_ok_clicked(ext)
        return {"ok": True}

    if cmd == "xml_run":
        on_xml_run_clicked(ext)
        return {"ok": True}

    if cmd == "kit_chrome_hide":
        hidden = bool(data.get("hidden", False))
        apply_kit_chrome_hidden(ext, hidden)
        try:
            m = getattr(ext, "_kit_chrome_hide_model", None)
            if m is not None:
                if hasattr(m, "set_value"):
                    m.set_value(hidden)
                elif hasattr(m, "set_value_as_bool"):
                    m.set_value_as_bool(hidden)
        except Exception:
            pass
        return {"ok": True}

    if cmd == "sim_viewport_split":
        try:
            n = int(data.get("count", data.get("split_n", 1)) or 1)
        except Exception:
            n = 1
        n = max(1, min(4, n))
        sim_multi_view.apply_sim_viewport_split_layout(ext, n)
        return {"ok": True, "count": n}

    if cmd == "save_sim_screen":
        try:
            si = int(data.get("screen", 1) or 1)
        except Exception:
            si = 1
        _on_save_sim_settings_to_screen(ext, si)
        return {"ok": True, "screen": si}

    if cmd == "apply_per_screen_snapshot":
        snap = data.get("snapshot")
        if isinstance(snap, dict):
            _apply_per_screen_snapshot(ext, snap)
            try:
                on_sim_ep_count_changed(ext)
            except Exception:
                pass
        return {"ok": True}

    if cmd == "gate_confirm":
        done = getattr(ext, "_sim_web_gate_done_event", None)
        _close_sim_gate_dialog(ext, done)
        return {"ok": True}

    if cmd == "ui_windows":
        """
        스트리밍 화면에서만 보이게 하기 위한 UI 토글:
        - hide=True  → Kit 내부의 TBS 제어창/시퀀스 편집기 창을 숨김(visible=False)
        - hide=False → 다시 표시(visible=True)
        """
        hide = bool(data.get("hide", False))

        def _set_visible(win: Any, visible: bool) -> None:
            if win is None:
                return
            try:
                win.visible = bool(visible)
                return
            except Exception:
                pass
            try:
                if hasattr(win, "set_visible"):
                    win.set_visible(bool(visible))
            except Exception:
                pass

        # 1) TBS 제어창
        try:
            _set_visible(getattr(ext, "_control_window", None), not hide)
        except Exception:
            pass

        # 1b) 시뮬 모니터 창
        try:
            _set_visible(getattr(ext, "_sim_monitor_window", None), not hide)
        except Exception:
            pass

        # 1c) 타임테이블 창
        try:
            _set_visible(getattr(ext, "_sim_timetable_window", None), not hide)
        except Exception:
            pass

        # 2) 시퀀스 편집기(SequenceEditorWindow 내부 _window)
        try:
            sw = getattr(ext, "_sequence_window", None)
            _set_visible(getattr(sw, "_window", None) if sw is not None else None, not hide)
        except Exception:
            pass

        return {"ok": True, "hide": hide}

    return {"ok": False, "error": f"unknown cmd: {cmd}"}


def _resources_json() -> Dict[str, Any]:
    items: List[Dict[str, str]] = []
    try:
        for name, path in get_resource_usd_list():
            items.append({"name": name, "path": path})
    except Exception:
        pass
    return {"items": items}


class _TbsRemoteHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str, *, cors: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        if self.path.split("?", 1)[0].rstrip("/").startswith("/api"):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Connection", "close")
            self.end_headers()
        else:
            self.send_error(404)

    def do_GET(self) -> None:
        global _ext_ref
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/api/state":
            if _ext_ref is None:
                self._send(503, b'{"error":"ext not ready"}', "application/json; charset=utf-8", cors=True)
                return
            try:
                snap = _run_on_main(lambda: _snapshot(_ext_ref))
                body = json.dumps(snap, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8", cors=True)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}).encode("utf-8"), "application/json; charset=utf-8", cors=True)
            return
        if path == "/api/prerun":
            if _ext_ref is None:
                self._send(503, b'{"error":"ext not ready"}', "application/json; charset=utf-8", cors=True)
                return
            try:
                q = self.path.split("?", 1)
                screen_q = ""
                if len(q) > 1:
                    for part in q[1].split("&"):
                        if part.startswith("screen="):
                            screen_q = part.split("=", 1)[-1].strip()
                            break
                export_all = _run_on_main(lambda: _prerun_export_by_screen_for_api(_ext_ref))
                if screen_q:
                    doc = export_all.get(str(screen_q))
                    if not isinstance(doc, dict):
                        self._send(404, json.dumps({"error": "no prerun for screen"}).encode("utf-8"), "application/json; charset=utf-8", cors=True)
                        return
                    body = json.dumps(doc, ensure_ascii=False).encode("utf-8")
                else:
                    body = json.dumps(export_all, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8", cors=True)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}).encode("utf-8"), "application/json; charset=utf-8", cors=True)
            return
        if path == "/api/resources":
            body = json.dumps(_resources_json(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8", cors=True)
            return

        if path == "/":
            path = "/index.html"
        rel = path.lstrip("/").replace("..", "")
        fp = _WEB_ROOT / rel
        if not fp.is_file():
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        data = fp.read_bytes()
        ct = "application/octet-stream"
        if fp.suffix.lower() == ".html":
            ct = "text/html; charset=utf-8"
        elif fp.suffix.lower() == ".css":
            ct = "text/css; charset=utf-8"
        elif fp.suffix.lower() == ".js":
            ct = "application/javascript; charset=utf-8"
        self._send(200, data, ct)

    def do_POST(self) -> None:
        global _ext_ref
        if self.path.split("?", 1)[0].rstrip("/") != "/api/command":
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        if _ext_ref is None:
            self._send(503, b'{"error":"ext not ready"}', "application/json; charset=utf-8", cors=True)
            return
        try:
            ln = int(self.headers.get("Content-Length", "0") or "0")
        except Exception:
            ln = 0
        raw = self.rfile.read(ln) if ln > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, b'{"error":"invalid json"}', "application/json; charset=utf-8", cors=True)
            return
        if not isinstance(data, dict):
            self._send(400, b'{"error":"body must be object"}', "application/json; charset=utf-8", cors=True)
            return
        try:
            result = _run_on_main(lambda: _dispatch_command(_ext_ref, data))
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8", cors=True)
        except Exception as e:
            self._send(500, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"), "application/json; charset=utf-8", cors=True)


def start_tbs_remote_http_bridge(ext: Any) -> None:
    global _server, _server_thread, _update_sub, _ext_ref
    _ext_ref = ext
    if _WEB_ROOT.is_dir():
        pass
    else:
        try:
            print(f"[TBS Remote UI] web 폴더 없음: {_WEB_ROOT}", flush=True)
        except Exception:
            pass

    try:
        _update_sub = app.get_app().get_update_event_stream().create_subscription_to_pop(
            _pump_main_queue,
            name="morph.tbs_control_2:tbs_remote_main_queue",
        )
    except Exception as e:
        try:
            print(f"[TBS Remote UI] 업데이트 구독 실패: {e}", flush=True)
        except Exception:
            pass
        return

    port = _DEFAULT_PORT
    try:
        port = int(os.environ.get("TBS_REMOTE_UI_PORT", str(_DEFAULT_PORT)).strip())
    except Exception:
        port = _DEFAULT_PORT

    bind = (os.environ.get("TBS_REMOTE_UI_BIND", "127.0.0.1") or "127.0.0.1").strip()
    if bind in ("*", "all", "ANY"):
        bind = "0.0.0.0"

    try:
        _server = ThreadingHTTPServer((bind, port), _TbsRemoteHandler)
    except OSError as e:
        try:
            print(f"[TBS Remote UI] 바인드 실패 {bind}:{port} — {e}", flush=True)
        except Exception:
            pass
        return

    def _serve() -> None:
        try:
            _server.serve_forever(poll_interval=0.5)
        except Exception:
            pass

    _server_thread = threading.Thread(target=_serve, name="tbs_remote_http", daemon=True)
    _server_thread.start()
    try:
        if bind == "0.0.0.0":
            print(
                f"[TBS Remote UI] listen {bind}:{port} — 로컬: http://127.0.0.1:{port}/ | "
                f"원격 PC 브라우저: http://<이-Kit-PC의-LAN-IP>:{port}/",
                flush=True,
            )
        else:
            print(f"[TBS Remote UI] http://{bind}:{port}/  (정적+API)", flush=True)
    except Exception:
        pass


def stop_tbs_remote_http_bridge() -> None:
    global _server, _server_thread, _update_sub, _ext_ref
    _ext_ref = None
    if _update_sub is not None:
        try:
            _update_sub.unsubscribe()
        except Exception:
            pass
        _update_sub = None
    if _server is not None:
        try:
            _server.shutdown()
        except Exception:
            pass
        try:
            _server.server_close()
        except Exception:
            pass
        _server = None
    _server_thread = None
