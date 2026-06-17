"""EBS 제어창·Viewport HUD 공통 시뮬레이션 패널 UI."""

from __future__ import annotations

from typing import Any, List, Set

import omni.ui as ui

from .kit_chrome_visibility import KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH, apply_kit_chrome_hidden
from .sim_control_defaults import SIM_CONTROL_DEFAULTS as _SIM_DEF

# Viewport HUD 체크박스 ↔ Kit 보조 창 (model_attr, 라벨, resolver key)
_AUX_KIT_WINDOW_SPECS: tuple[tuple[str, str, str], ...] = (
    ("_ui_show_tbs_usd_model", "USD Load", "usd"),
    ("_ui_show_tbs_sequence_model", "시퀀스", "sequence"),
    ("_ui_show_tbs_timetable_model", "타임테이블", "timetable"),
    ("_ui_show_tbs_sim_monitor_model", "시뮬 모니터", "monitor"),
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


def _resolve_aux_kit_window(ext: Any, which: str) -> Any:
    if which == "usd":
        return getattr(getattr(ext, "_tbs_usd_window", None), "_window", None)
    if which == "sequence":
        editor = getattr(getattr(ext, "_sequence_window", None), "_editor", None)
        return getattr(editor, "_window", None)
    if which == "timetable":
        return getattr(ext, "_sim_timetable_window", None)
    if which == "monitor":
        return getattr(ext, "_sim_monitor_window", None)
    if which == "ebs":
        return getattr(ext, "_control_window", None)
    return None


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
        _set_kit_window_visible(_resolve_aux_kit_window(ext, which), visible)


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
        _set_kit_window_visible(_resolve_aux_kit_window(ext, which), visible)
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
    ext._sim_log_interval_model = ui.SimpleFloatModel(float(_SIM_DEF.log_interval_sec))
    ext._sim_confirm_each_step_model = ui.SimpleBoolModel(False)
    ext._sim_bar_preview_model = ui.SimpleBoolModel(False)
    try:
        ext._sim_bar_preview_model.add_value_changed_fn(lambda *_a: _on_sim_bar_preview_toggled(ext))
    except Exception:
        pass
    ext._sim_process_time_priority_model = ui.SimpleBoolModel(False)
    ext._sim_oht_bp1_min_model = ui.SimpleFloatModel(float(_SIM_DEF.oht_to_bp1_min))
    ext._sim_oht_bp1_max_model = ui.SimpleFloatModel(float(_SIM_DEF.oht_to_bp1_max))
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
    ext._sim_per_screen_snapshots = [None, None, None, None]
    ext._sim_snapshot_sync_guard = False
    ext._sim_per_screen_block = None
    ext._sim_per_screen_row_hstacks = []
    ext._sim_per_screen_status_labels = []
    ext._sync_sim_per_screen_rows_fn = None
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
    ext._sim_monitor_split_host = None
    ext._sim_monitor_split_inner = None
    ext._sim_timetable_window = None
    ext._sim_timetable_user_dismissed = False
    ext._sim_timetable_split_host = None
    ext._sim_timetable_split_inner = None
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
        setattr(ext, model_attr, ui.SimpleBoolModel(True))
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


def build_ebs_control_panel_content(ext: Any, *, compact: bool = False) -> None:
    """
    EBS 시뮬레이션 제어 패널(주석 처리된 XML·prim 목록 등 제외).

    ``compact=True`` 는 Viewport 좌측 HUD(~300px)용 세로형 레이아웃.
    """
    from .control_window import (
        CHECKBOX_WHITE_STYLE,
        _on_save_sim_settings_to_screen,
        _on_sim_split_choice_changed,
        _refresh_sim_per_screen_rows,
        _sync_ep3_port_cell_visibility,
        _sync_sim_split_checkboxes_from_ext_count,
        on_sim_ep_count_changed,
        on_sim_reset_clicked,
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
            _sync_ep3_port_cell_visibility=_sync_ep3_port_cell_visibility,
        )
        return

    cb_style = CHECKBOX_WHITE_STYLE
    with ui.Frame(style={"background_color": 0xFF1E2530}):
        with ui.VStack(padding=8, spacing=6):
            with ui.HStack(spacing=10, height=28):
                ui.Label(
                    "시뮬레이션 (simpy)",
                    width=150,
                    height=24,
                    style={"color": 0xFFDDDDDD},
                )
                ext._sim_multi_split_row = ui.HStack(spacing=8, height=26)
                ext._sim_multi_split_row.visible = False
                with ext._sim_multi_split_row:
                    ui.Label(
                        "시뮬 화면(USD 로드 시)",
                        width=130,
                        height=22,
                        style={"color": 0xFF9AA4B2},
                    )
                    ext._sim_split_cb_models = []
                    for i in range(1, 5):
                        m = ui.SimpleBoolModel(i == 1)
                        ext._sim_split_cb_models.append(m)
                        ui.Label(f"{i}화면", width=40, height=22, style={"color": 0xFFDDDDDD})
                        ui.CheckBox(model=m, width=22, style=cb_style)
                        try:
                            m.add_value_changed_fn(lambda md, ii=i: _on_sim_split_choice_changed(ext, ii, md))
                        except Exception:
                            pass
                    ext._sync_sim_multi_split_ui_fn = lambda: _sync_sim_split_checkboxes_from_ext_count(ext)

            with ui.HStack(spacing=8, height=28):
                ui.Label("LOT 수", width=80)
                ui.IntField(model=ext._sim_lot_count_model, width=80)
                ui.Label("EP 개수", width=55)
                _bind_ep_count_combo(ext, ui.ComboBox(int(get_sim_ep_count_idx(ext)), "2", "3"))

            with ui.HStack(spacing=8, height=28):
                ui.Label("LOT생성간격", width=100)
                ui.FloatField(model=ext._sim_lot_spawn_min_model, width=65)
                ui.Label("~", width=10)
                ui.FloatField(model=ext._sim_lot_spawn_max_model, width=65)
                ui.Label("회수간격", width=60)
                ui.FloatField(model=ext._sim_pickup_evt_min_model, width=55)
                ui.Label("~", width=10)
                ui.FloatField(model=ext._sim_pickup_evt_max_model, width=55)

            with ui.HStack(spacing=8, height=28):
                ui.Label("FOUP공정(EP)", width=100)
                ui.FloatField(model=ext._sim_foup_proc_min_model, width=65)
                ui.Label("~", width=10)
                ui.FloatField(model=ext._sim_foup_proc_max_model, width=65)
                ui.Label("초", width=20, style={"color": 0xFF9AA4B2})

            ui.Label("초기 LOT 적재 포트 (체크 시 시작 시점에 FULL)", height=20)
            with ui.HStack(spacing=8, height=26):
                ui.Label("IN/OUT", width=55)
                ui.CheckBox(model=ext._sim_init_inout_model, width=30, style=cb_style)
                ui.Label("BP1", width=30)
                ui.CheckBox(model=ext._sim_init_bp1_model, width=30, style=cb_style)
                ui.Label("BP2", width=30)
                ui.CheckBox(model=ext._sim_init_bp2_model, width=30, style=cb_style)
                ui.Label("BP3", width=30)
                ui.CheckBox(model=ext._sim_init_bp3_model, width=30, style=cb_style)
                bp4_row = ui.HStack(spacing=8, height=26)
                ext._sim_init_bp4_rows.append(bp4_row)
                ext._sim_init_bp4_row = bp4_row
                with bp4_row:
                    ui.Label("BP4", width=30)
                    ui.CheckBox(model=ext._sim_init_bp4_model, width=30, style=cb_style)
            with ui.HStack(spacing=8, height=26):
                ui.Label("EP1", width=30)
                ui.CheckBox(model=ext._sim_init_ep1_model, width=30, style=cb_style)
                ui.Label("EP2", width=30)
                ui.CheckBox(model=ext._sim_init_ep2_model, width=30, style=cb_style)
                ep3_row = ui.HStack(spacing=8, height=26)
                ext._sim_init_ep3_rows.append(ep3_row)
                ext._sim_init_ep3_row = ep3_row
                with ep3_row:
                    ui.Label("EP3", width=30)
                    ui.CheckBox(model=ext._sim_init_ep3_model, width=30, style=cb_style)

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

            ui.Spacer(height=2)
            ui.Label(
                "고장(비가동) 포트 (체크 시 해당 포트는 라우팅에서 제외, 실행 중에도 즉시 반영)",
                height=20,
            )
            with ui.HStack(spacing=8, height=26):
                ui.Label("IN/OUT", width=55)
                ui.CheckBox(model=ext._sim_fault_inout_model, width=30, style=cb_style)
                ui.Label("BP1", width=30)
                ui.CheckBox(model=ext._sim_fault_bp1_model, width=30, style=cb_style)
                ui.Label("BP2", width=30)
                ui.CheckBox(model=ext._sim_fault_bp2_model, width=30, style=cb_style)
                ui.Label("BP3", width=30)
                ui.CheckBox(model=ext._sim_fault_bp3_model, width=30, style=cb_style)
                f_bp4 = ui.HStack(spacing=8, height=26)
                ext._sim_fault_bp4_rows.append(f_bp4)
                ext._sim_fault_bp4_row = f_bp4
                with f_bp4:
                    ui.Label("BP4", width=30)
                    ui.CheckBox(model=ext._sim_fault_bp4_model, width=30, style=cb_style)
            with ui.HStack(spacing=8, height=26):
                ui.Label("EP1", width=30)
                ui.CheckBox(model=ext._sim_fault_ep1_model, width=30, style=cb_style)
                ui.Label("EP2", width=30)
                ui.CheckBox(model=ext._sim_fault_ep2_model, width=30, style=cb_style)
                f_ep3 = ui.HStack(spacing=8, height=26)
                ext._sim_fault_ep3_rows.append(f_ep3)
                ext._sim_fault_ep3_row = f_ep3
                with f_ep3:
                    ui.Label("EP3", width=30)
                    ui.CheckBox(model=ext._sim_fault_ep3_model, width=30, style=cb_style)
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

            with ui.HStack(spacing=8, height=28):
                ui.Label("OHT→IN/OUT/EP", width=100)
                ui.FloatField(model=ext._sim_oht_bp1_min_model, width=70)
                ui.Label("~", width=10)
                ui.FloatField(model=ext._sim_oht_bp1_max_model, width=70)
                ui.Label("IN/OUT->BP", width=60)
                ui.FloatField(model=ext._sim_bp1_bp_min_model, width=55)
                ui.Label("~", width=10)
                ui.FloatField(model=ext._sim_bp1_bp_max_model, width=55)
            with ui.HStack(spacing=8, height=28):
                ui.Label("BP->EP", width=80)
                ui.FloatField(model=ext._sim_bp_ep_min_model, width=70)
                ui.Label("~", width=10)
                ui.FloatField(model=ext._sim_bp_ep_max_model, width=70)
                ui.Label("EP->OHT", width=60)
                ui.FloatField(model=ext._sim_ep_oht_min_model, width=55)
                ui.Label("~", width=10)
                ui.FloatField(model=ext._sim_ep_oht_max_model, width=55)
            with ui.HStack(spacing=8, height=28):
                ui.Label("시뮬 속도배율", width=100)
                ui.FloatField(model=ext._sim_speed_model, width=80)
                ui.Label("로그주기(s)", width=70)
                ui.FloatField(model=ext._sim_log_interval_model, width=70)
            with ui.HStack(spacing=8, height=28):
                ui.CheckBox(
                    model=ext._sim_process_time_priority_model,
                    width=30,
                    style=cb_style,
                    visible=False,
                )
                ui.Label("공정설정 시간 우선", width=120, visible=False)
                ui.CheckBox(model=ext._sim_bar_preview_model, width=30, style=cb_style)
                ui.Label("결과 미리보기", width=90)
                ui.Spacer(width=8)
                start_btn = ui.Button("시작", width=72, clicked_fn=lambda: on_sim_start_clicked(ext))
                ext._sim_start_buttons.append(start_btn)
                ext._sim_start_button = start_btn
                ui.Button("정지", width=72, clicked_fn=lambda: on_sim_stop_clicked(ext))
                ui.Button("리셋", width=72, clicked_fn=lambda: on_sim_reset_clicked(ext))

            ext._sim_per_screen_block = ui.VStack(spacing=4, visible=False)
            with ext._sim_per_screen_block:
                ui.Label(
                    "2~4분할 시: 화면별로 아래에서 「현재 설정 저장」을 누르면 LOT·간격·적재/고장·시간 값이 "
                    "해당 화면에 고정됩니다. 저장 안 한 화면은 시뮬 시작 시 제어창 값으로 자동 채웁니다.",
                    word_wrap=True,
                    width=0,
                    style={"color": 0xFF9AA4B2},
                )
                ext._sim_per_screen_row_hstacks = []
                ext._sim_per_screen_status_labels = []
                for si in range(1, 5):
                    row = ui.HStack(spacing=8, height=26, visible=False)
                    ext._sim_per_screen_row_hstacks.append(row)
                    with row:
                        ui.Label(f"화면{si}", width=44, height=22, style={"color": 0xFFDDDDDD})
                        st = ui.Label("(미저장)", width=64, height=22, style={"color": 0xFF9AA4B2})
                        ext._sim_per_screen_status_labels.append(st)
                        ui.Button(
                            "현재 설정 저장",
                            width=110,
                            height=24,
                            clicked_fn=lambda e=ext, k=si: _on_save_sim_settings_to_screen(e, k),
                        )
            ext._sync_sim_per_screen_rows_fn = lambda: _refresh_sim_per_screen_rows(ext)


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
    _sync_ep3_port_cell_visibility: Any,
) -> None:
    _purge_hud_rows_from_lists(ext)
    lw, fw, sw = 72, 54, 48
    with ui.VStack(padding=0, spacing=5):
        ui.Label("EBS (Viewport)", height=18, style={"font_size": 13, "color": 0xFFFFFFFF})
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
        with ui.HStack(spacing=4, height=24):
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
        with ui.HStack(spacing=4, height=24):
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
                ("IN→BP", ext._sim_bp1_bp_min_model, ext._sim_bp1_bp_max_model),
                ("BP→EP", ext._sim_bp_ep_min_model, ext._sim_bp_ep_max_model),
                ("EP→OHT", ext._sim_ep_oht_min_model, ext._sim_ep_oht_max_model),
            ):
                with ui.HStack(spacing=4, height=26):
                    ui.Label(lbl, width=lw)
                    ui.FloatField(model=mn, width=sw)
                    ui.Label("~", width=8)
                    ui.FloatField(model=mx, width=sw)
        with ui.HStack(spacing=4, height=26):
            ui.Label("배율", width=36)
            ui.FloatField(model=ext._sim_speed_model, width=fw)
            ui.Label("로그(s)", width=44)
            ui.FloatField(model=ext._sim_log_interval_model, width=fw)
        with ui.HStack(spacing=4, height=28):
            ui.CheckBox(model=ext._sim_bar_preview_model, width=24, style=cb_style)
            ui.Label("미리보기", width=52)
            ext._sim_start_buttons.append(
                ui.Button("시작", width=60, clicked_fn=lambda: on_sim_start_clicked(ext))
            )
            ui.Button("정지", width=60, clicked_fn=lambda: on_sim_stop_clicked(ext))
            ui.Button("리셋", width=60, clicked_fn=lambda: on_sim_reset_clicked(ext))
        ui.Label("창 표시", height=16, style={"color": 0xFF9AA4B2, "font_size": 11})
        for model_attr, label, _which in _AUX_KIT_WINDOW_SPECS:
            with ui.HStack(spacing=4, height=22):
                ui.CheckBox(model=getattr(ext, model_attr), width=24, style=cb_style)
                ui.Label(label, width=0)
