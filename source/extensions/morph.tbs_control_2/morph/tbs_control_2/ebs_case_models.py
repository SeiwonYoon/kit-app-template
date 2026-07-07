"""EBS CASE A/B — 화면별 시뮬 설정 UI 모델·캡처·복사."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

import omni.ui as ui

from .sim_control_defaults import SIM_CONTROL_DEFAULTS as _SIM_DEF

# CASE 1 = 화면1 (ext._sim_* , HUD 와 공유) / CASE 2 = 화면2 (ext._ebs_b_*)
CASE_A = 1
CASE_B = 2

_BOOL_FIELDS: Tuple[str, ...] = (
    "init_inout",
    "init_bp1",
    "init_bp2",
    "init_bp3",
    "init_bp4",
    "init_ep1",
    "init_ep2",
    "init_ep3",
    "fault_inout",
    "fault_bp1",
    "fault_bp2",
    "fault_bp3",
    "fault_bp4",
    "fault_ep1",
    "fault_ep2",
    "fault_ep3",
)

_FLOAT_FIELDS: Tuple[str, ...] = (
    "lot_spawn_min",
    "lot_spawn_max",
    "pickup_min",
    "pickup_max",
    "oht_bp1_min",
    "oht_bp1_max",
    "bp1_bp_min",
    "bp1_bp_max",
    "bp_ep_min",
    "bp_ep_max",
    "ep_oht_min",
    "ep_oht_max",
    "foup_proc_min",
    "foup_proc_max",
)

# dict 키 → 모델 접미사 (CASE A: _sim_{suffix}_model, CASE B: _ebs_b_{suffix}_model)
_FIELD_TO_SUFFIX: Dict[str, str] = {
    "lot_count": "lot_count",
    "lot_spawn_min": "lot_spawn_min",
    "lot_spawn_max": "lot_spawn_max",
    "pickup_min": "pickup_evt_min",
    "pickup_max": "pickup_evt_max",
    "spawn_min": "lot_spawn_min",
    "spawn_max": "lot_spawn_max",
    "pue_min": "pickup_evt_min",
    "pue_max": "pickup_evt_max",
    "oht_bp1_min": "oht_bp1_min",
    "oht_bp1_max": "oht_bp1_max",
    "bp1_bp_min": "bp1_bp_min",
    "bp1_bp_max": "bp1_bp_max",
    "bp_ep_min": "bp_ep_min",
    "bp_ep_max": "bp_ep_max",
    "ep_oht_min": "ep_oht_min",
    "ep_oht_max": "ep_oht_max",
    "foup_proc_min": "foup_proc_min",
    "foup_proc_max": "foup_proc_max",
    "init_inout": "init_inout",
    "init_bp1": "init_bp1",
    "init_bp2": "init_bp2",
    "init_bp3": "init_bp3",
    "init_bp4": "init_bp4",
    "init_ep1": "init_ep1",
    "init_ep2": "init_ep2",
    "init_ep3": "init_ep3",
    "fault_inout": "fault_inout",
    "fault_bp1": "fault_bp1",
    "fault_bp2": "fault_bp2",
    "fault_bp3": "fault_bp3",
    "fault_bp4": "fault_bp4",
    "fault_ep1": "fault_ep1",
    "fault_ep2": "fault_ep2",
    "fault_ep3": "fault_ep3",
}


def case_from_screen(screen_1based: int) -> int:
    return CASE_A if int(screen_1based) <= 1 else CASE_B


def screen_from_case(case_id: int) -> int:
    return 1 if int(case_id) == CASE_A else 2


def _model_attr(case_id: int, suffix: str) -> str:
    if int(case_id) == CASE_A:
        return f"_sim_{suffix}_model"
    return f"_ebs_b_{suffix}_model"


def get_case_model(ext: Any, case_id: int, suffix: str) -> Any:
    return getattr(ext, _model_attr(int(case_id), suffix), None)


def init_ebs_case_b_models(ext: Any) -> None:
    """CASE B(화면2) 전용 UI 모델 — CASE A 는 기존 ext._sim_* (HUD 와 공유)."""
    ext._ebs_b_lot_count_model = ui.SimpleIntModel(int(_SIM_DEF.lot_count))
    ext._ebs_b_lot_spawn_min_model = ui.SimpleFloatModel(float(_SIM_DEF.lot_spawn_min))
    ext._ebs_b_lot_spawn_max_model = ui.SimpleFloatModel(float(_SIM_DEF.lot_spawn_max))
    ext._ebs_b_pickup_evt_min_model = ui.SimpleFloatModel(float(_SIM_DEF.pickup_min))
    ext._ebs_b_pickup_evt_max_model = ui.SimpleFloatModel(float(_SIM_DEF.pickup_max))
    ext._ebs_b_oht_bp1_min_model = ui.SimpleFloatModel(float(_SIM_DEF.oht_to_bp1_min))
    ext._ebs_b_oht_bp1_max_model = ui.SimpleFloatModel(float(_SIM_DEF.oht_to_bp1_max))
    ext._ebs_b_bp1_bp_min_model = ui.SimpleFloatModel(float(_SIM_DEF.bp1_to_bp_min))
    ext._ebs_b_bp1_bp_max_model = ui.SimpleFloatModel(float(_SIM_DEF.bp1_to_bp_max))
    ext._ebs_b_bp_ep_min_model = ui.SimpleFloatModel(float(_SIM_DEF.bp_to_ep_min))
    ext._ebs_b_bp_ep_max_model = ui.SimpleFloatModel(float(_SIM_DEF.bp_to_ep_max))
    ext._ebs_b_ep_oht_min_model = ui.SimpleFloatModel(float(_SIM_DEF.ep_to_oht_min))
    ext._ebs_b_ep_oht_max_model = ui.SimpleFloatModel(float(_SIM_DEF.ep_to_oht_max))
    ext._ebs_b_foup_proc_min_model = ui.SimpleFloatModel(float(_SIM_DEF.foup_process_min))
    ext._ebs_b_foup_proc_max_model = ui.SimpleFloatModel(float(_SIM_DEF.foup_process_max))
    ext._ebs_b_ep_count_idx_model = ui.SimpleIntModel(int(_SIM_DEF.ep_count_idx))
    ext._ebs_b_ep_count_combo = None
    ext._ebs_b_ep_count_combos: List[Any] = []
    ext._ebs_b_ebs_enabled_model = ui.SimpleBoolModel(True)
    for name in _BOOL_FIELDS:
        setattr(ext, _model_attr(CASE_B, name), ui.SimpleBoolModel(False))
    try:

        def _on_b_ebs_changed(_m: Any) -> None:
            from .control_window import on_sim_ebs_enabled_changed_for_case

            on_sim_ebs_enabled_changed_for_case(ext, CASE_B)

        ext._ebs_b_ebs_enabled_model.add_value_changed_fn(_on_b_ebs_changed)
    except Exception:
        pass


def get_sim_ep_count_idx_for_case(ext: Any, case_id: int) -> int:
    cid = int(case_id)
    if cid == CASE_A:
        from .ebs_control_panel_ui import get_sim_ep_count_idx

        return int(get_sim_ep_count_idx(ext))
    try:
        m = getattr(ext, "_ebs_b_ep_count_idx_model", None)
        if m is not None:
            return int(m.get_value_as_int())
    except Exception:
        pass
    try:
        combo = getattr(ext, "_ebs_b_ep_count_combo", None)
        if combo is not None:
            return int(combo.model.get_item_value_model().as_int)
    except Exception:
        pass
    return int(_SIM_DEF.ep_count_idx)


def get_sim_ebs_enabled_for_case(ext: Any, case_id: int) -> bool:
    cid = int(case_id)
    if cid == CASE_A:
        from .ebs_control_panel_ui import get_sim_ebs_enabled

        return bool(get_sim_ebs_enabled(ext))
    try:
        m = getattr(ext, "_ebs_b_ebs_enabled_model", None)
        if m is not None:
            return bool(m.get_value_as_bool())
    except Exception:
        pass
    return True


def _sync_case_b_ep_count_combo_widgets(ext: Any, idx: int) -> None:
    try:
        ext._ebs_b_ep_count_idx_model.set_value(int(idx))
    except Exception:
        pass
    for combo in list(getattr(ext, "_ebs_b_ep_count_combos", None) or []):
        if combo is None:
            continue
        try:
            combo.model.get_item_value_model().set_value(int(idx))
        except Exception:
            pass


def bind_case_b_ep_count_combo(ext: Any, combo: Any) -> None:
    from .control_window import on_sim_ep_count_changed_for_case

    def _on_combo(_m: Any, *_a: Any) -> None:
        try:
            idx = int(_m.get_item_value_model().as_int)
        except Exception:
            idx = 0
        _sync_case_b_ep_count_combo_widgets(ext, int(idx))
        on_sim_ep_count_changed_for_case(ext, CASE_B)

    try:
        combo.model.add_item_changed_fn(_on_combo)
    except Exception:
        pass
    combos = getattr(ext, "_ebs_b_ep_count_combos", None)
    if not isinstance(combos, list):
        combos = []
        ext._ebs_b_ep_count_combos = combos
    combos.append(combo)
    if getattr(ext, "_ebs_b_ep_count_combo", None) is None:
        ext._ebs_b_ep_count_combo = combo


def _ep_count_from_snap_value(raw: Any, *, default: int = 2) -> int:
    try:
        return 3 if int(raw) >= 3 else 2
    except Exception:
        return 3 if int(default) >= 3 else 2


def capture_case_sim_settings(ext: Any, case_id: int) -> Dict[str, Any]:
    """CASE 실시간 UI → 시뮬 엔진용 dict (화면별 전체 설정)."""
    cid = int(case_id)
    d: Dict[str, Any] = {}
    try:
        idx = int(get_sim_ep_count_idx_for_case(ext, cid))
    except Exception:
        idx = int(_SIM_DEF.ep_count_idx)
    d["ep_count"] = _ep_count_from_snap_value(3 if idx == 1 else 2)
    d["ebs_enabled"] = bool(get_sim_ebs_enabled_for_case(ext, cid))
    try:
        m = get_case_model(ext, cid, "lot_count")
        d["lot_count"] = max(1, int(m.get_value_as_int())) if m is not None else int(_SIM_DEF.lot_count)
    except Exception:
        d["lot_count"] = int(_SIM_DEF.lot_count)
    for out_key, suffix in (
        ("spawn_min", "lot_spawn_min"),
        ("spawn_max", "lot_spawn_max"),
        ("pue_min", "pickup_evt_min"),
        ("pue_max", "pickup_evt_max"),
    ):
        try:
            m = get_case_model(ext, cid, suffix)
            d[out_key] = float(m.get_value_as_float()) if m is not None else float(getattr(_SIM_DEF, suffix.replace("pickup_evt", "pickup"), 1.0))
        except Exception:
            d[out_key] = 1.0
    for key in (
        "oht_bp1_min",
        "oht_bp1_max",
        "bp1_bp_min",
        "bp1_bp_max",
        "bp_ep_min",
        "bp_ep_max",
        "ep_oht_min",
        "ep_oht_max",
        "foup_proc_min",
        "foup_proc_max",
    ):
        try:
            m = get_case_model(ext, cid, key)
            d[key] = float(m.get_value_as_float()) if m is not None else 1.0
        except Exception:
            d[key] = 1.0
    for key in _BOOL_FIELDS:
        try:
            m = get_case_model(ext, cid, key)
            d[key] = bool(m.get_value_as_bool()) if m is not None else False
        except Exception:
            d[key] = False
    return d


def capture_case_sim_settings_for_screen(ext: Any, screen_1based: int) -> Dict[str, Any]:
    return capture_case_sim_settings(ext, case_from_screen(screen_1based))


def apply_case_sim_settings(ext: Any, case_id: int, snap: Dict[str, Any]) -> None:
    """dict → CASE UI 모델 (웹 API ``_apply_per_screen_snapshot`` 와 동일 키)."""
    if not isinstance(snap, dict) or not snap:
        return
    cid = int(case_id)
    ep_in_snap = "ep_count" in snap
    if ep_in_snap:
        idx = 1 if _ep_count_from_snap_value(snap.get("ep_count")) >= 3 else 0
        if cid == CASE_A:
            from .ebs_control_panel_ui import _sync_ep_count_combo_widgets

            _sync_ep_count_combo_widgets(ext, idx)
        else:
            _sync_case_b_ep_count_combo_widgets(ext, idx)
    try:
        m = get_case_model(ext, cid, "ebs_enabled")
        if m is not None and "ebs_enabled" in snap:
            m.set_value(bool(snap.get("ebs_enabled")))
    except Exception:
        pass
    try:
        m = get_case_model(ext, cid, "lot_count")
        if m is not None:
            m.set_value(max(1, int(snap.get("lot_count", _SIM_DEF.lot_count) or _SIM_DEF.lot_count)))
    except Exception:
        pass
    for out_key, suffix in (
        ("spawn_min", "lot_spawn_min"),
        ("spawn_max", "lot_spawn_max"),
        ("pue_min", "pickup_evt_min"),
        ("pue_max", "pickup_evt_max"),
    ):
        try:
            m = get_case_model(ext, cid, suffix)
            if m is not None and out_key in snap:
                m.set_value(float(snap[out_key]))
        except Exception:
            pass
    for key in (
        "oht_bp1_min",
        "oht_bp1_max",
        "bp1_bp_min",
        "bp1_bp_max",
        "bp_ep_min",
        "bp_ep_max",
        "ep_oht_min",
        "ep_oht_max",
        "foup_proc_min",
        "foup_proc_max",
    ):
        try:
            m = get_case_model(ext, cid, key)
            if m is not None and key in snap:
                m.set_value(float(snap[key]))
        except Exception:
            pass
    for key in _BOOL_FIELDS:
        try:
            m = get_case_model(ext, cid, key)
            if m is not None and key in snap:
                m.set_value(bool(snap[key]))
        except Exception:
            pass
    if cid == CASE_A:
        if ep_in_snap:
            try:
                from .control_window import on_sim_ep_count_changed

                on_sim_ep_count_changed(ext)
            except Exception:
                pass
    else:
        if ep_in_snap:
            try:
                from .control_window import on_sim_ep_count_changed_for_case

                on_sim_ep_count_changed_for_case(ext, CASE_B)
            except Exception:
                pass


def copy_case_sim_settings(ext: Any, from_case: int, to_case: int) -> None:
    snap = capture_case_sim_settings(ext, int(from_case))
    apply_case_sim_settings(ext, int(to_case), snap)


def copy_case_a_to_b(ext: Any) -> None:
    copy_case_sim_settings(ext, CASE_A, CASE_B)


def copy_case_b_to_a(ext: Any) -> None:
    copy_case_sim_settings(ext, CASE_B, CASE_A)


def snapshots_for_startup_channels(ext: Any, n_ch: int) -> List[Dict[str, Any]]:
    """HUD 전역 시작: 화면 i → CASE i 실시간 값."""
    n = max(1, min(4, int(n_ch)))
    out: List[Dict[str, Any]] = []
    for i in range(n):
        screen = i + 1
        out.append(copy.deepcopy(capture_case_sim_settings_for_screen(ext, screen)))
    return out
