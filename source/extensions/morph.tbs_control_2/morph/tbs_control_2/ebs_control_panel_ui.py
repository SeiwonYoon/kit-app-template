"""EBS 제어창·Viewport HUD 공통 시뮬레이션 패널 UI."""

from __future__ import annotations

from typing import Any, List, Set

import omni.ui as ui

from .kit_chrome_visibility import KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH, apply_kit_chrome_hidden
from .sim_control_defaults import SIM_BAR_PREVIEW_DEFAULT as _SIM_BAR_PREVIEW_DEFAULT
from .sim_control_defaults import SIM_CONTROL_DEFAULTS as _SIM_DEF

# Viewport HUD 체크박스 ↔ Kit 보조 창 (model_attr, 라벨, resolver key)
_AUX_KIT_WINDOW_SPECS: tuple[tuple[str, str, str], ...] = (
    ("_ui_show_tbs_usd_model", "USD Load", "usd"),
    ("_ui_show_tbs_sequence_model", "시퀀스", "sequence"),
    ("_ui_show_tbs_timetable_model", "타임테이블", "timetable"),
    ("_ui_show_tbs_sim_monitor_model", "시뮬 모니터", "monitor"),
    ("_ui_show_tbs_fix_proc_model", "fix 공정 입력", "fix_proc"),
    ("_ui_show_ebs_control_model", "EBS 제어창", "ebs"),
)


def _set_kit_window_visible(win: Any, visible: bool) -> None:
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


def _resolve_aux_kit_windows(ext: Any, which: str) -> List[Any]:
    """체크박스 1개가 제어하는 Kit 보조 창 목록 (2분할 시 모니터·타임테이블은 화면별 N개)."""
    if which == "usd":
        w = getattr(getattr(ext, "_tbs_usd_window", None), "_window", None)
        return [w] if w is not None else []
    if which == "sequence":
        editor = getattr(getattr(ext, "_sequence_window", None), "_editor", None)
        w = getattr(editor, "_window", None)
        return [w] if w is not None else []
    if which == "timetable":
        try:
            from .control_window import _iter_sim_timetable_windows

            return list(_iter_sim_timetable_windows(ext))
        except Exception:
            w = getattr(ext, "_sim_timetable_window", None)
            return [w] if w is not None else []
    if which == "monitor":
        try:
            from .control_window import _iter_sim_monitor_windows

            return list(_iter_sim_monitor_windows(ext))
        except Exception:
            w = getattr(ext, "_sim_monitor_window", None)
            return [w] if w is not None else []
    if which == "fix_proc":
        w = getattr(ext, "_fix_proc_window", None)
        return [w] if w is not None else []
    if which == "ebs":
        wins: List[Any] = []
        for attr in ("_control_window", "_control_window_b"):
            w = getattr(ext, attr, None)
            if w is not None:
                wins.append(w)
        return wins
    return []


def _resolve_aux_kit_window(ext: Any, which: str) -> Any:
    wins = _resolve_aux_kit_windows(ext, which)
    return wins[0] if wins else None


def sync_aux_kit_window_visibility(ext: Any) -> None:
    """HUD 체크박스 모델 상태를 각 Kit 보조 창 visible 에 반영."""
    for model_attr, _label, which in _AUX_KIT_WINDOW_SPECS:
        mdl = getattr(ext, model_attr, None)
        if mdl is None:
            continue
        try:
            visible = bool(mdl.as_bool)
        except Exception:
            visible = True
        for win in _resolve_aux_kit_windows(ext, which):
            _set_kit_window_visible(win, visible)


def _on_aux_kit_window_visibility_changed(ext: Any, which: str, _model: Any = None) -> None:
    for model_attr, _label, key in _AUX_KIT_WINDOW_SPECS:
        if key != which:
            continue
        mdl = getattr(ext, model_attr, None)
        if mdl is None:
            return
        try:
            visible = bool(mdl.as_bool)
        except Exception:
            visible = True
        for win in _resolve_aux_kit_windows(ext, which):
            _set_kit_window_visible(win, visible)
        return


def get_sim_ep_count_idx(ext: Any) -> int:
    try:
        m = getattr(ext, "_sim_ep_count_idx_model", None)
        if m is not None:
            return int(m.get_value_as_int())
    except Exception:
        pass
    try:
        combo = getattr(ext, "_sim_ep_count_combo", None)
        if combo is not None:
            return int(combo.model.get_item_value_model().as_int)
    except Exception:
        pass
    return int(_SIM_DEF.ep_count_idx)


def get_sim_ebs_enabled(ext: Any) -> bool:
    try:
        m = getattr(ext, "_sim_ebs_enabled_model", None)
        if m is not None:
            return bool(m.get_value_as_bool())
    except Exception:
        pass
    return True


def _sync_ep_count_combo_widgets(ext: Any, idx: int) -> None:
    """창·HUD 등 여러 EP 콤보 선택을 동일 인덱스로 맞춘다."""
    try:
        ext._sim_ep_count_idx_model.set_value(int(idx))
    except Exception:
        pass
    for combo in list(getattr(ext, "_sim_ep_count_combos", None) or []):
        if combo is None:
            continue
        try:
            combo.model.get_item_value_model().set_value(int(idx))
        except Exception:
            pass


def _bind_ep_count_combo(ext: Any, combo: Any) -> None:
    from .control_window import on_sim_ep_count_changed

    def _on_combo(_m: Any, *_a: Any) -> None:
        try:
            idx = int(_m.get_item_value_model().as_int)
        except Exception:
            idx = 0
        _sync_ep_count_combo_widgets(ext, int(idx))
        on_sim_ep_count_changed(ext)

    try:
        combo.model.add_item_changed_fn(_on_combo)
    except Exception:
        pass
    combos = getattr(ext, "_sim_ep_count_combos", None)
    if not isinstance(combos, list):
        combos = []
        ext._sim_ep_count_combos = combos
    combos.append(combo)
    if getattr(ext, "_sim_ep_count_combo", None) is None:
        ext._sim_ep_count_combo = combo


def init_ebs_control_models(ext: Any) -> None:
    """시뮬 UI 모델·런타임 상태 초기화 (창/HUD 공유)."""
    from .control_window import _on_sim_bar_preview_toggled
    from .sequence_engine import SequenceRunner
    import threading

    ext._xml_from_port_model = ui.SimpleIntModel(1)
    ext._xml_to_port_model = ui.SimpleIntModel(6)
    ext._xml_port_id_model = ui.SimpleIntModel(1)
    ext._last_generated_xml = ""
    ext._priority_prefix_model = ui.SimpleStringModel("Mesh_")
    ext._sim_lot_count_model = ui.SimpleIntModel(int(_SIM_DEF.lot_count))
    ext._sim_lot_spawn_min_model = ui.SimpleFloatModel(float(_SIM_DEF.lot_spawn_min))
    ext._sim_lot_spawn_max_model = ui.SimpleFloatModel(float(_SIM_DEF.lot_spawn_max))
    ext._sim_pickup_evt_min_model = ui.SimpleFloatModel(float(_SIM_DEF.pickup_min))
    ext._sim_pickup_evt_max_model = ui.SimpleFloatModel(float(_SIM_DEF.pickup_max))
    ext._sim_speed_model = ui.SimpleFloatModel(float(_SIM_DEF.sim_speed))
    ext._sim_confirm_each_step_model = ui.SimpleBoolModel(False)
    ext._sim_bar_preview_model = ui.SimpleBoolModel(bool(_SIM_BAR_PREVIEW_DEFAULT))
    try:
        ext._sim_bar_preview_model.add_value_changed_fn(lambda *_a: _on_sim_bar_preview_toggled(ext))
    except Exception:
        pass
    ext._sim_process_time_priority_model = ui.SimpleBoolModel(False)
    ext._sim_oht_bp1_min_model = ui.SimpleFloatModel(float(_SIM_DEF.oht_to_bp1_min))
    ext._sim_oht_bp1_max_model = ui.SimpleFloatModel(float(_SIM_DEF.oht_to_bp1_max))
    ext._sim_oht_inout_min_model = ui.SimpleFloatModel(float(_SIM_DEF.oht_to_inout_min))
    ext._sim_oht_inout_max_model = ui.SimpleFloatModel(float(_SIM_DEF.oht_to_inout_max))
    ext._sim_bp1_bp_min_model = ui.SimpleFloatModel(float(_SIM_DEF.bp1_to_bp_min))
    ext._sim_bp1_bp_max_model = ui.SimpleFloatModel(float(_SIM_DEF.bp1_to_bp_max))
    ext._sim_bp_ep_min_model = ui.SimpleFloatModel(float(_SIM_DEF.bp_to_ep_min))
    ext._sim_bp_ep_max_model = ui.SimpleFloatModel(float(_SIM_DEF.bp_to_ep_max))
    ext._sim_ep_oht_min_model = ui.SimpleFloatModel(float(_SIM_DEF.ep_to_oht_min))
    ext._sim_ep_oht_max_model = ui.SimpleFloatModel(float(_SIM_DEF.ep_to_oht_max))
    ext._sim_foup_proc_min_model = ui.SimpleFloatModel(float(_SIM_DEF.foup_process_min))
    ext._sim_foup_proc_max_model = ui.SimpleFloatModel(float(_SIM_DEF.foup_process_max))
    ext._sim_ep_count_idx_model = ui.SimpleIntModel(int(_SIM_DEF.ep_count_idx))
    ext._sim_ep_count_combo = None
    ext._sim_ep_count_combos = []
    ext._sim_ebs_enabled_model = ui.SimpleBoolModel(True)
    ext._sim_ebs_enabled_checkboxes: List[Any] = []
    ext._sim_init_buffer_row = None
    ext._sim_init_ebs_rows: List[Any] = []
    ext._sim_fault_buffer_row = None
    ext._sim_fault_ebs_rows: List[Any] = []
    ext._sim_timing_inout_bp_block = None
    ext._sim_timing_bp_ep_row = None
    ext._sim_oht_timing_label = None
    ext._sim_timing_oht_inout_rows: List[Any] = []
    ext._sim_timing_ebs_compact_rows: List[Any] = []
    try:

        def _on_ebs_enabled_model_changed(_m: Any) -> None:
            from .tbs_ep_port_visibility import on_sim_ebs_enabled_changed

            on_sim_ebs_enabled_changed(ext)

        ext._sim_ebs_enabled_model.add_value_changed_fn(_on_ebs_enabled_model_changed)
    except Exception:
        pass
    ext._sim_start_buttons: List[Any] = []
    ext._sim_init_inout_model = ui.SimpleBoolModel(False)
    ext._sim_init_bp1_model = ui.SimpleBoolModel(False)
    ext._sim_init_bp2_model = ui.SimpleBoolModel(False)
    ext._sim_init_bp3_model = ui.SimpleBoolModel(False)
    ext._sim_init_bp4_model = ui.SimpleBoolModel(False)
    ext._sim_init_ep1_model = ui.SimpleBoolModel(False)
    ext._sim_init_ep2_model = ui.SimpleBoolModel(False)
    ext._sim_init_ep3_model = ui.SimpleBoolModel(False)
    ext._sim_init_bp4_rows: List[Any] = []
    ext._sim_init_ep3_rows: List[Any] = []
    ext._sim_init_bp4_row = None
    ext._sim_init_ep3_row = None
    ext._sim_fault_inout_model = ui.SimpleBoolModel(False)
    ext._sim_fault_bp1_model = ui.SimpleBoolModel(False)
    ext._sim_fault_bp2_model = ui.SimpleBoolModel(False)
    ext._sim_fault_bp3_model = ui.SimpleBoolModel(False)
    ext._sim_fault_bp4_model = ui.SimpleBoolModel(False)
    ext._sim_fault_ep1_model = ui.SimpleBoolModel(False)
    ext._sim_fault_ep2_model = ui.SimpleBoolModel(False)
    ext._sim_fault_ep3_model = ui.SimpleBoolModel(False)
    ext._sim_fault_bp4_rows: List[Any] = []
    ext._sim_fault_ep3_rows: List[Any] = []
    ext._sim_fault_bp4_row = None
    ext._sim_fault_ep3_row = None
    try:
        from .control_sim_fix_proc_hud import ensure_fix_proc_file_hud_models

        ensure_fix_proc_file_hud_models(ext)
    except Exception:
        pass
    ext._sim_log_text = ui.SimpleStringModel("[SIM] 대기 중")
    ext._sim_history_text = ui.SimpleStringModel("[SIM] 대기 중")
    ext._sim_progress_text = ui.SimpleStringModel("[진행현황] 없음")
    ext._sim_port_state_text = ui.SimpleStringModel("[포트상태] 대기 중")
    ext._sim_recent_story_blocks = []
    ext._sim_progress_rows = {}
    ext._sim_progress_history = []
    ext._sim_progress_start_times = {}
    ext._sim_progress_last_key = {}
    ext._sim_engine = None
    ext._sim_engines = []
    from .sim_control_defaults import default_viewport_split_count

    ext._sim_viewport_split_target = int(default_viewport_split_count())
    ext._sim_viewport_split_count = 1
    ext._sync_sim_multi_split_ui_fn = None
    ext._tbs_multi_split_usd_ready = False
    ext._tbs_last_loaded_usd_path = ""
    ext._sim_multi_view_apply_token = 0
    ext._sim_multi_viewport_entries = []
    ext._tbs_sim_snapshot_hud_windows = []
    ext._tbs_sim_snapshot_hud_roots = {}
    ext._tbs_split_main_viewport_window = None
    ext._tbs_sim_hud_screen1_label = None
    ext._tbs_sim_hud_screen1_live_sub = None
    ext._tbs_sim_hud_live_ctr = 0
    ext._sim_multi_context_names = []
    ext._tbs_split_session_layer_paths = []
    ext._tbs_mdl_https_texture_hint_done = False
    ext._sim_split_mutate_guard = False
    ext._sim_split_cb_models = []
    ext._sim_split_stage_sub = None
    ext._sim_multi_split_row = None
    from .ebs_case_models import init_ebs_case_b_models

    init_ebs_case_b_models(ext)
    ext._ebs_b_init_ebs_rows = []
    ext._ebs_b_fault_ebs_rows = []
    ext._ebs_b_init_bp4_rows = []
    ext._ebs_b_fault_bp4_rows = []
    ext._ebs_b_init_ep3_rows = []
    ext._ebs_b_fault_ep3_rows = []
    ext._control_window_b = None
    ext._sim_per_screen_snapshots = [None, None, None, None]
    ext._sim_snapshot_sync_guard = False
    ext._sync_sim_per_screen_rows_fn = lambda *a, **k: None
    ext._sim_update_sub = None
    ext._sim_thread = None
    ext._sim_tick_threads = []
    ext._sim_thread_stop = None
    ext._sim_log_queue = None
    ext._sim_log_ui_sub = None
    ext._sim_log_view_combo = None
    ext._sim_progress_frame = None
    ext._sim_history_frame = None
    ext._sim_anim_history_frame = None
    ext._sim_monitor_window = None
    ext._sim_monitor_windows_by_screen = {}
    ext._sim_monitor_split_host = None
    ext._sim_monitor_split_host_by_screen = {}
    ext._sim_monitor_split_inner = None
    ext._sim_monitor_split_inner_by_screen = {}
    ext._sim_timetable_window = None
    ext._sim_timetable_windows_by_screen = {}
    ext._fix_proc_window = None
    ext._sim_timetable_user_dismissed = False
    ext._sim_timetable_split_host = None
    ext._sim_timetable_split_host_by_screen = {}
    ext._sim_timetable_split_inner = None
    ext._sim_timetable_split_inner_by_screen = {}
    ext._sim_foup_outer_host_by_screen = {}
    ext._sim_foup_inner_stack_by_screen = {}
    ext._sim_timetable_layout_n = 1
    ext._sim_monitor_channels = []
    ext._sim_monitor_layout_n = 1
    ext._rebuild_sim_monitor_split_ui_fn = None
    ext._rebuild_sim_timetable_split_ui_fn = None
    ext._sim_port_state_frame = None
    ext._sim_port_state_header_label = None
    ext._sim_port_cells = {}
    ext._sim_port_cell_boxes = {}
    ext._sim_port_bp4_cell_container = None
    ext._sim_port_ep3_cell = None
    ext._sim_port_ep3_cell_container = None
    ext._sim_progress_label = None
    ext._sim_history_label = None
    ext._sim_anim_history_label = None
    ext._sim_port_state_label = None
    ext._sim_runner = SequenceRunner(
        registry=getattr(ext, "_tbs_registry", None),
        scheduler=getattr(ext, "_tbs_scheduler", None),
        evaluator=getattr(ext, "_tbs_evaluator", None),
    )
    ext._sim_anim_active = {}
    ext._sim_anim_pending = []
    ext._sim_tick_pause_event = threading.Event()
    ext._sim_gate_pause_event = threading.Event()
    ext._sim_tick_pause_until_wall = None
    ext._sim_gate_dialog = None
    ext._kit_chrome_hide_model = ui.SimpleBoolModel(KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH)
    for model_attr, _label, which in _AUX_KIT_WINDOW_SPECS:
        setattr(ext, model_attr, ui.SimpleBoolModel(False))
        try:
            getattr(ext, model_attr).add_value_changed_fn(
                lambda m, w=which: _on_aux_kit_window_visibility_changed(ext, w, m)
            )
        except Exception:
            pass

    def _on_kit_chrome_toggle(model: Any) -> None:
        try:
            apply_kit_chrome_hidden(ext, bool(model.as_bool))
        except Exception:
            pass

    try:
        ext._kit_chrome_hide_model.add_value_changed_fn(_on_kit_chrome_toggle)
    except Exception:
        pass

    from .control_window import _capture_per_screen_sim_settings

    ext._capture_sim_settings_dict_for_hud_fn = lambda: _capture_per_screen_sim_settings(ext)


def _on_faulty_port_changed(ext: Any, _m: Any = None) -> None:
    for eng in list(getattr(ext, "_sim_engines", None) or []):
        if eng is not None:
            try:
                eng.kick_serial_flow()
            except Exception:
                pass
    sim = getattr(ext, "_sim_engine", None)
    if sim is not None:
        try:
            sim.kick_serial_flow()
        except Exception:
            pass


def build_ebs_control_panel_content(ext: Any, *, compact: bool = False, case_id: int = 1) -> None:
    """
    EBS 시뮬레이션 제어 패널.

    ``compact=True``  → Viewport HUD (CASE A 모델, 전역 시작/정지).
    ``compact=False`` → ``EBS제어창(CASE A|B)`` 본문.
    """
    from .control_window import (
        CHECKBOX_WHITE_STYLE,
        _on_sim_split_choice_changed,
        _sync_ep3_port_cell_visibility,
        _sync_sim_split_checkboxes_from_ext_count,
        on_sim_ep_count_changed,
        on_sim_reset_clicked,
        on_sim_restart_clicked,
        on_sim_start_clicked,
        on_sim_stop_clicked,
    )

    if compact:
        _build_ebs_control_panel_compact(
            ext,
            cb_style=CHECKBOX_WHITE_STYLE,
            on_sim_ep_count_changed=on_sim_ep_count_changed,
            on_sim_start_clicked=on_sim_start_clicked,
            on_sim_stop_clicked=on_sim_stop_clicked,
            on_sim_reset_clicked=on_sim_reset_clicked,
            on_sim_restart_clicked=on_sim_restart_clicked,
            _sync_ep3_port_cell_visibility=_sync_ep3_port_cell_visibility,
            _on_sim_split_choice_changed=_on_sim_split_choice_changed,
            _sync_sim_split_checkboxes_from_ext_count=_sync_sim_split_checkboxes_from_ext_count,
        )
        return

    from .ebs_case_panel_ui import build_ebs_case_window_panel

    build_ebs_case_window_panel(ext, int(case_id), cb_style=CHECKBOX_WHITE_STYLE)


def _purge_hud_rows_from_lists(ext: Any) -> None:
    """HUD 재마운트 시 이전 HUD 위젯 참조만 목록에서 제거."""
    win_bp4 = getattr(ext, "_sim_init_bp4_row", None)
    win_ep3 = getattr(ext, "_sim_init_ep3_row", None)
    win_fbp4 = getattr(ext, "_sim_fault_bp4_row", None)
    win_fep3 = getattr(ext, "_sim_fault_ep3_row", None)
    win_start = getattr(ext, "_sim_start_button", None)
    win_combo = getattr(ext, "_sim_ep_count_combo", None)
    ext._sim_init_bp4_rows = [win_bp4] if win_bp4 is not None else []
    ext._sim_init_ep3_rows = [win_ep3] if win_ep3 is not None else []
    ext._sim_fault_bp4_rows = [win_fbp4] if win_fbp4 is not None else []
    ext._sim_fault_ep3_rows = [win_fep3] if win_fep3 is not None else []
    ext._sim_start_buttons = [win_start] if win_start is not None else []
    ext._sim_ep_count_combos = [win_combo] if win_combo is not None else []


def _build_ebs_control_panel_compact(
    ext: Any,
    *,
    cb_style: Any,
    on_sim_ep_count_changed: Any,
    on_sim_start_clicked: Any,
    on_sim_stop_clicked: Any,
    on_sim_reset_clicked: Any,
    on_sim_restart_clicked: Any,
    _sync_ep3_port_cell_visibility: Any,
    _on_sim_split_choice_changed: Any,
    _sync_sim_split_checkboxes_from_ext_count: Any,
) -> None:
    _purge_hud_rows_from_lists(ext)
    lw, fw, sw = 72, 54, 48
    with ui.VStack(padding=0, spacing=5):
        ui.Label("EBS (Viewport · CASE A)", height=18, style={"font_size": 13, "color": 0xFFFFFFFF})
        try:
            from .control_sim_fix_proc_hud import build_fix_proc_file_ebs_rows

            build_fix_proc_file_ebs_rows(ext, lw=lw, cb_style=cb_style)
        except Exception:
            pass
        ext._sim_multi_split_row = ui.HStack(spacing=4, height=22)
        ext._sim_multi_split_row.visible = False
        with ext._sim_multi_split_row:
            ui.Label("분할", width=28, style={"color": 0xFF9AA4B2, "font_size": 11})
            ext._sim_split_cb_models = []
            try:
                from .sim_control_defaults import MAX_VIEWPORT_SPLIT_COUNT, default_viewport_split_count

                cap = max(1, int(MAX_VIEWPORT_SPLIT_COUNT))
            except Exception:
                cap = 2
            try:
                initial_n = max(1, min(cap, int(default_viewport_split_count())))
            except Exception:
                initial_n = 1
            for i in range(1, cap + 1):
                m = ui.SimpleBoolModel(i == initial_n)
                ext._sim_split_cb_models.append(m)
                ui.Label(f"{i}", width=14, style={"color": 0xFFDDDDDD, "font_size": 11})
                ui.CheckBox(model=m, width=20, style=cb_style)
                try:
                    m.add_value_changed_fn(lambda md, ii=i: _on_sim_split_choice_changed(ext, ii, md))
                except Exception:
                    pass
            ext._sync_sim_multi_split_ui_fn = lambda: _sync_sim_split_checkboxes_from_ext_count(ext)
            _sync_sim_split_checkboxes_from_ext_count(ext)
        with ui.HStack(spacing=4, height=26):
            ui.Label("EBS 적용", width=lw)
            ui.CheckBox(model=ext._sim_ebs_enabled_model, width=24, style=cb_style)
        with ui.HStack(spacing=4, height=26):
            ui.Label("LOT 수", width=lw)
            ui.IntField(model=ext._sim_lot_count_model, width=fw)
            ui.Label("EP 개수", width=44)
            _bind_ep_count_combo(ext, ui.ComboBox(int(get_sim_ep_count_idx(ext)), "2", "3"))
        with ui.HStack(spacing=4, height=26):
            ui.Label("LOT생성", width=lw)
            ui.FloatField(model=ext._sim_lot_spawn_min_model, width=sw)
            ui.Label("~", width=8)
            ui.FloatField(model=ext._sim_lot_spawn_max_model, width=sw)
        with ui.HStack(spacing=4, height=26):
            ui.Label("회수", width=lw)
            ui.FloatField(model=ext._sim_pickup_evt_min_model, width=sw)
            ui.Label("~", width=8)
            ui.FloatField(model=ext._sim_pickup_evt_max_model, width=sw)
        with ui.HStack(spacing=4, height=26):
            ui.Label("FOUP공정", width=lw)
            ui.FloatField(model=ext._sim_foup_proc_min_model, width=sw)
            ui.Label("~", width=8)
            ui.FloatField(model=ext._sim_foup_proc_max_model, width=sw)
        ui.Label("초기 LOT 적재", height=16, style={"color": 0xFF9AA4B2, "font_size": 11})
        hud_init_buf = ui.HStack(spacing=4, height=24)
        ext._sim_init_ebs_rows.append(hud_init_buf)
        with hud_init_buf:
            ui.Label("IN", width=22)
            ui.CheckBox(model=ext._sim_init_inout_model, width=24, style=cb_style)
            ui.Label("BP1", width=26)
            ui.CheckBox(model=ext._sim_init_bp1_model, width=24, style=cb_style)
            ui.Label("BP2", width=26)
            ui.CheckBox(model=ext._sim_init_bp2_model, width=24, style=cb_style)
            ui.Label("BP3", width=26)
            ui.CheckBox(model=ext._sim_init_bp3_model, width=24, style=cb_style)
        bp4_row = ui.HStack(spacing=4, height=24)
        ext._sim_init_bp4_rows.append(bp4_row)
        ext._sim_init_ebs_rows.append(bp4_row)
        with bp4_row:
            ui.Label("BP4", width=26)
            ui.CheckBox(model=ext._sim_init_bp4_model, width=24, style=cb_style)
        with ui.HStack(spacing=4, height=24):
            ui.Label("EP1", width=26)
            ui.CheckBox(model=ext._sim_init_ep1_model, width=24, style=cb_style)
            ui.Label("EP2", width=26)
            ui.CheckBox(model=ext._sim_init_ep2_model, width=24, style=cb_style)
            ep3_row = ui.HStack(spacing=4, height=24)
            ext._sim_init_ep3_rows.append(ep3_row)
            with ep3_row:
                ui.Label("EP3", width=26)
                ui.CheckBox(model=ext._sim_init_ep3_model, width=24, style=cb_style)
        try:
            ext._sim_init_ep3_model.add_value_changed_fn(lambda m: on_sim_ep_count_changed(ext))
        except Exception:
            pass
        for mdl in (
            ext._sim_init_inout_model,
            ext._sim_init_bp1_model,
            ext._sim_init_bp2_model,
            ext._sim_init_bp3_model,
            ext._sim_init_bp4_model,
            ext._sim_init_ep1_model,
            ext._sim_init_ep2_model,
            ext._sim_init_ep3_model,
        ):
            try:
                mdl.add_value_changed_fn(lambda m: _sync_ep3_port_cell_visibility(ext))
            except Exception:
                pass
        on_sim_ep_count_changed(ext)
        ui.Label("고장 포트", height=16, style={"color": 0xFF9AA4B2, "font_size": 11})
        hud_fault_buf = ui.HStack(spacing=4, height=24)
        ext._sim_fault_ebs_rows.append(hud_fault_buf)
        with hud_fault_buf:
            ui.Label("IN", width=22)
            ui.CheckBox(model=ext._sim_fault_inout_model, width=24, style=cb_style)
            ui.Label("BP1", width=26)
            ui.CheckBox(model=ext._sim_fault_bp1_model, width=24, style=cb_style)
            ui.Label("BP2", width=26)
            ui.CheckBox(model=ext._sim_fault_bp2_model, width=24, style=cb_style)
            ui.Label("BP3", width=26)
            ui.CheckBox(model=ext._sim_fault_bp3_model, width=24, style=cb_style)
        f_bp4 = ui.HStack(spacing=4, height=24)
        ext._sim_fault_bp4_rows.append(f_bp4)
        ext._sim_fault_ebs_rows.append(f_bp4)
        with f_bp4:
            ui.Label("BP4", width=26)
            ui.CheckBox(model=ext._sim_fault_bp4_model, width=24, style=cb_style)
        with ui.HStack(spacing=4, height=24):
            ui.Label("EP1", width=26)
            ui.CheckBox(model=ext._sim_fault_ep1_model, width=24, style=cb_style)
            ui.Label("EP2", width=26)
            ui.CheckBox(model=ext._sim_fault_ep2_model, width=24, style=cb_style)
            f_ep3 = ui.HStack(spacing=4, height=24)
            ext._sim_fault_ep3_rows.append(f_ep3)
            with f_ep3:
                ui.Label("EP3", width=26)
                ui.CheckBox(model=ext._sim_fault_ep3_model, width=24, style=cb_style)
        for mdl in (
            ext._sim_fault_inout_model,
            ext._sim_fault_bp1_model,
            ext._sim_fault_bp2_model,
            ext._sim_fault_bp3_model,
            ext._sim_fault_bp4_model,
            ext._sim_fault_ep1_model,
            ext._sim_fault_ep2_model,
            ext._sim_fault_ep3_model,
        ):
            try:
                mdl.add_value_changed_fn(lambda m: _on_faulty_port_changed(ext, m))
            except Exception:
                pass
        with ui.VStack(spacing=3):
            for lbl, mn, mx in (
                ("OHT→EP", ext._sim_oht_bp1_min_model, ext._sim_oht_bp1_max_model),
                ("OHT→IN/OUT", ext._sim_oht_inout_min_model, ext._sim_oht_inout_max_model),
                ("IN→BP", ext._sim_bp1_bp_min_model, ext._sim_bp1_bp_max_model),
                ("BP→EP", ext._sim_bp_ep_min_model, ext._sim_bp_ep_max_model),
                ("EP→OHT", ext._sim_ep_oht_min_model, ext._sim_ep_oht_max_model),
            ):
                row = ui.HStack(spacing=4, height=26)
                if lbl in ("OHT→IN/OUT", "IN→BP", "BP→EP"):
                    ext._sim_timing_ebs_compact_rows.append(row)
                with row:
                    ui.Label(lbl, width=lw)
                    ui.FloatField(model=mn, width=sw)
                    ui.Label("~", width=8)
                    ui.FloatField(model=mx, width=sw)
        with ui.HStack(spacing=4, height=26):
            ui.Label("배율", width=36)
            ui.FloatField(model=ext._sim_speed_model, width=fw)
        with ui.HStack(spacing=4, height=28):
            ui.CheckBox(model=ext._sim_bar_preview_model, width=24, style=cb_style)
            ui.Label("미리보기", width=52)
            ext._sim_start_buttons.append(
                ui.Button("시작", width=60, clicked_fn=lambda: on_sim_start_clicked(ext))
            )
            ui.Button("재시작", width=60, clicked_fn=lambda: on_sim_restart_clicked(ext))
            ui.Button("정지", width=60, clicked_fn=lambda: on_sim_stop_clicked(ext))
            ui.Button("리셋", width=60, clicked_fn=lambda: on_sim_reset_clicked(ext))
            ui.Label(
                "두 화면 동시 적용",
                width=0,
                style={"color": 0xFF7EB8DA, "font_size": 11},
            )
        try:
            from .tbs_screen_visibility import mount_screen_visibility_checkboxes

            mount_screen_visibility_checkboxes(ext, ui, row_height=22)
        except Exception:
            pass
        try:
            from .control_window import _sync_ebs_control_visibility

            _sync_ebs_control_visibility(ext)
        except Exception:
            pass
        ui.Label("창 표시", height=16, style={"color": 0xFF9AA4B2, "font_size": 11})
        for model_attr, label, _which in _AUX_KIT_WINDOW_SPECS:
            with ui.HStack(spacing=4, height=22):
                ui.CheckBox(model=getattr(ext, model_attr), width=24, style=cb_style)
                ui.Label(label, width=0)
