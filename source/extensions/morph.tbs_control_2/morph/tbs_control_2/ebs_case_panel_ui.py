"""EBS CASE A/B Kit ``ui.Window`` 패널 본문."""

from __future__ import annotations

from typing import Any, Callable, List

import omni.ui as ui

from .ebs_case_models import (
    CASE_A,
    CASE_B,
    bind_case_b_ep_count_combo,
    copy_case_a_to_b,
    copy_case_b_to_a,
    get_case_model,
    get_sim_ep_count_idx_for_case,
    screen_from_case,
)


def build_ebs_case_window_panel(ext: Any, case_id: int, *, cb_style: Any) -> None:
    """``EBS제어창(CASE A|B)`` 스크롤 영역 본문."""
    from .ebs_control_panel_ui import _bind_ep_count_combo, _on_faulty_port_changed
    from .control_window import (
        _sync_ebs_control_visibility_for_case,
        _sync_ep3_port_cell_visibility_for_case,
        on_sim_ep_count_changed,
        on_sim_ep_count_changed_for_case,
        on_sim_reset_for_screen,
        on_sim_start_for_screen,
        on_sim_stop_for_screen,
    )

    cid = int(case_id)
    screen = screen_from_case(cid)
    ep_idx = int(get_sim_ep_count_idx_for_case(ext, cid))

    if cid == CASE_A:
        ep_combo_bind = lambda c: _bind_ep_count_combo(ext, c)
        on_ep_changed = lambda: on_sim_ep_count_changed(ext)
        sync_vis = lambda: _sync_ebs_control_visibility_for_case(ext, CASE_A)
        sync_ep3 = lambda: _sync_ep3_port_cell_visibility_for_case(ext, CASE_A)
    else:
        ep_combo_bind = lambda c: bind_case_b_ep_count_combo(ext, c)
        on_ep_changed = lambda: on_sim_ep_count_changed_for_case(ext, CASE_B)
        sync_vis = lambda: _sync_ebs_control_visibility_for_case(ext, CASE_B)
        sync_ep3 = lambda: _sync_ep3_port_cell_visibility_for_case(ext, CASE_B)
        if not isinstance(getattr(ext, "_ebs_b_init_ebs_rows", None), list):
            ext._ebs_b_init_ebs_rows = []
        if not isinstance(getattr(ext, "_ebs_b_fault_ebs_rows", None), list):
            ext._ebs_b_fault_ebs_rows = []
        if not isinstance(getattr(ext, "_ebs_b_init_bp4_rows", None), list):
            ext._ebs_b_init_bp4_rows = []
        if not isinstance(getattr(ext, "_ebs_b_fault_bp4_rows", None), list):
            ext._ebs_b_fault_bp4_rows = []
        if not isinstance(getattr(ext, "_ebs_b_init_ep3_rows", None), list):
            ext._ebs_b_init_ep3_rows = []
        if not isinstance(getattr(ext, "_ebs_b_fault_ep3_rows", None), list):
            ext._ebs_b_fault_ep3_rows = []

    init_rows: List[Any] = ext._sim_init_ebs_rows if cid == CASE_A else ext._ebs_b_init_ebs_rows
    fault_rows: List[Any] = ext._sim_fault_ebs_rows if cid == CASE_A else ext._ebs_b_fault_ebs_rows
    init_bp4_rows: List[Any] = ext._sim_init_bp4_rows if cid == CASE_A else ext._ebs_b_init_bp4_rows
    fault_bp4_rows: List[Any] = ext._sim_fault_bp4_rows if cid == CASE_A else ext._ebs_b_fault_bp4_rows
    init_ep3_rows: List[Any] = ext._sim_init_ep3_rows if cid == CASE_A else ext._ebs_b_init_ep3_rows
    fault_ep3_rows: List[Any] = ext._sim_fault_ep3_rows if cid == CASE_A else ext._ebs_b_fault_ep3_rows

    def _m(suffix: str) -> Any:
        return get_case_model(ext, cid, suffix)

    with ui.Frame(style={"background_color": 0xFF1E2530}):
        with ui.VStack(padding=8, spacing=6):
            tag = "CASE A · 화면1" if cid == CASE_A else "CASE B · 화면2"
            with ui.HStack(spacing=8, height=26):
                ui.Label(tag, width=120, style={"color": 0xFF7EB8DA, "font_size": 13})
                if cid == CASE_A:
                    ui.Button(
                        "A→B copy",
                        width=88,
                        height=24,
                        clicked_fn=lambda: copy_case_a_to_b(ext),
                    )
                else:
                    ui.Button(
                        "B→A copy",
                        width=88,
                        height=24,
                        clicked_fn=lambda: copy_case_b_to_a(ext),
                    )

            with ui.HStack(spacing=8, height=28):
                ui.Label("EBS 적용여부", width=100)
                ui.CheckBox(model=_m("ebs_enabled"), width=30, style=cb_style)

            with ui.HStack(spacing=8, height=28):
                ui.Label("LOT 수", width=80)
                ui.IntField(model=_m("lot_count"), width=80)
                ui.Label("EP 개수", width=55)
                ep_combo_bind(ui.ComboBox(ep_idx, "2", "3"))

            with ui.HStack(spacing=8, height=28):
                ui.Label("LOT생성간격", width=100)
                ui.FloatField(model=_m("lot_spawn_min"), width=65)
                ui.Label("~", width=10)
                ui.FloatField(model=_m("lot_spawn_max"), width=65)
                ui.Label("회수간격", width=60)
                ui.FloatField(model=_m("pickup_evt_min"), width=55)
                ui.Label("~", width=10)
                ui.FloatField(model=_m("pickup_evt_max"), width=55)

            with ui.HStack(spacing=8, height=28):
                ui.Label("FOUP공정(EP)", width=100)
                ui.FloatField(model=_m("foup_proc_min"), width=65)
                ui.Label("~", width=10)
                ui.FloatField(model=_m("foup_proc_max"), width=65)
                ui.Label("초", width=20, style={"color": 0xFF9AA4B2})

            ui.Label("초기 LOT 적재 포트 (체크 시 시작 시점에 FULL)", height=20)
            init_buffer_row = ui.HStack(spacing=8, height=26)
            init_rows.append(init_buffer_row)
            with init_buffer_row:
                ui.Label("IN/OUT", width=55)
                ui.CheckBox(model=_m("init_inout"), width=30, style=cb_style)
                ui.Label("BP1", width=30)
                ui.CheckBox(model=_m("init_bp1"), width=30, style=cb_style)
                ui.Label("BP2", width=30)
                ui.CheckBox(model=_m("init_bp2"), width=30, style=cb_style)
                ui.Label("BP3", width=30)
                ui.CheckBox(model=_m("init_bp3"), width=30, style=cb_style)
                bp4_row = ui.HStack(spacing=8, height=26)
                init_bp4_rows.append(bp4_row)
                init_rows.append(bp4_row)
                if cid == CASE_A:
                    ext._sim_init_bp4_row = bp4_row
                else:
                    ext._ebs_b_init_bp4_row = bp4_row
                with bp4_row:
                    ui.Label("BP4", width=30)
                    ui.CheckBox(model=_m("init_bp4"), width=30, style=cb_style)
            with ui.HStack(spacing=8, height=26):
                ui.Label("EP1", width=30)
                ui.CheckBox(model=_m("init_ep1"), width=30, style=cb_style)
                ui.Label("EP2", width=30)
                ui.CheckBox(model=_m("init_ep2"), width=30, style=cb_style)
                ep3_row = ui.HStack(spacing=8, height=26)
                init_ep3_rows.append(ep3_row)
                if cid == CASE_A:
                    ext._sim_init_ep3_row = ep3_row
                else:
                    ext._ebs_b_init_ep3_row = ep3_row
                with ep3_row:
                    ui.Label("EP3", width=30)
                    ui.CheckBox(model=_m("init_ep3"), width=30, style=cb_style)

            try:
                _m("init_ep3").add_value_changed_fn(lambda m: on_ep_changed())
            except Exception:
                pass
            for name in (
                "init_inout",
                "init_bp1",
                "init_bp2",
                "init_bp3",
                "init_bp4",
                "init_ep1",
                "init_ep2",
                "init_ep3",
            ):
                try:
                    _m(name).add_value_changed_fn(lambda m: sync_ep3())
                except Exception:
                    pass
            on_ep_changed()
            sync_vis()

            ui.Spacer(height=2)
            ui.Label(
                "고장(비가동) 포트 (체크 시 해당 포트는 라우팅에서 제외, 실행 중에도 즉시 반영)",
                height=20,
            )
            fault_buffer_row = ui.HStack(spacing=8, height=26)
            fault_rows.append(fault_buffer_row)
            with fault_buffer_row:
                ui.Label("IN/OUT", width=55)
                ui.CheckBox(model=_m("fault_inout"), width=30, style=cb_style)
                ui.Label("BP1", width=30)
                ui.CheckBox(model=_m("fault_bp1"), width=30, style=cb_style)
                ui.Label("BP2", width=30)
                ui.CheckBox(model=_m("fault_bp2"), width=30, style=cb_style)
                ui.Label("BP3", width=30)
                ui.CheckBox(model=_m("fault_bp3"), width=30, style=cb_style)
                f_bp4 = ui.HStack(spacing=8, height=26)
                fault_bp4_rows.append(f_bp4)
                fault_rows.append(f_bp4)
                if cid == CASE_A:
                    ext._sim_fault_bp4_row = f_bp4
                else:
                    ext._ebs_b_fault_bp4_row = f_bp4
                with f_bp4:
                    ui.Label("BP4", width=30)
                    ui.CheckBox(model=_m("fault_bp4"), width=30, style=cb_style)
            with ui.HStack(spacing=8, height=26):
                ui.Label("EP1", width=30)
                ui.CheckBox(model=_m("fault_ep1"), width=30, style=cb_style)
                ui.Label("EP2", width=30)
                ui.CheckBox(model=_m("fault_ep2"), width=30, style=cb_style)
                f_ep3 = ui.HStack(spacing=8, height=26)
                fault_ep3_rows.append(f_ep3)
                if cid == CASE_A:
                    ext._sim_fault_ep3_row = f_ep3
                else:
                    ext._ebs_b_fault_ep3_row = f_ep3
                with f_ep3:
                    ui.Label("EP3", width=30)
                    ui.CheckBox(model=_m("fault_ep3"), width=30, style=cb_style)
            for name in (
                "fault_inout",
                "fault_bp1",
                "fault_bp2",
                "fault_bp3",
                "fault_bp4",
                "fault_ep1",
                "fault_ep2",
                "fault_ep3",
            ):
                try:
                    _m(name).add_value_changed_fn(lambda m: _on_faulty_port_changed(ext, m))
                except Exception:
                    pass

            with ui.HStack(spacing=8, height=28):
                if cid == CASE_A:
                    ext._sim_oht_timing_label = ui.Label("OHT→IN/OUT/EP", width=100)
                else:
                    ext._ebs_b_oht_timing_label = ui.Label("OHT→IN/OUT/EP", width=100)
                ui.FloatField(model=_m("oht_bp1_min"), width=70)
                ui.Label("~", width=10)
                ui.FloatField(model=_m("oht_bp1_max"), width=70)
                inout_bp_block = ui.HStack(spacing=8, height=28)
                if cid == CASE_A:
                    ext._sim_timing_inout_bp_block = inout_bp_block
                else:
                    ext._ebs_b_timing_inout_bp_block = inout_bp_block
                with inout_bp_block:
                    ui.Label("IN/OUT->BP", width=60)
                    ui.FloatField(model=_m("bp1_bp_min"), width=55)
                    ui.Label("~", width=10)
                    ui.FloatField(model=_m("bp1_bp_max"), width=55)
            bp_ep_row = ui.HStack(spacing=8, height=28)
            if cid == CASE_A:
                ext._sim_timing_bp_ep_row = bp_ep_row
            else:
                ext._ebs_b_timing_bp_ep_row = bp_ep_row
            with bp_ep_row:
                ui.Label("BP->EP", width=80)
                ui.FloatField(model=_m("bp_ep_min"), width=70)
                ui.Label("~", width=10)
                ui.FloatField(model=_m("bp_ep_max"), width=70)
                ui.Label("EP->OHT", width=60)
                ui.FloatField(model=_m("ep_oht_min"), width=55)
                ui.Label("~", width=10)
                ui.FloatField(model=_m("ep_oht_max"), width=55)

            with ui.HStack(spacing=8, height=28):
                ui.CheckBox(
                    model=ext._sim_process_time_priority_model,
                    width=30,
                    style=cb_style,
                    visible=False,
                )
                ui.CheckBox(model=ext._sim_bar_preview_model, width=30, style=cb_style)
                ui.Label("결과 미리보기", width=90)
                ui.Spacer(width=8)
                ui.Button(
                    "시작",
                    width=72,
                    clicked_fn=lambda e=ext, s=screen: on_sim_start_for_screen(e, s),
                )
                ui.Button(
                    "정지",
                    width=72,
                    clicked_fn=lambda e=ext, s=screen: on_sim_stop_for_screen(e, s),
                )
                ui.Button(
                    "리셋",
                    width=72,
                    clicked_fn=lambda e=ext, s=screen: on_sim_reset_for_screen(e, s),
                )
                ui.Label(
                    f"화면{screen}만",
                    width=0,
                    style={"color": 0xFF9AA4B2, "font_size": 11},
                )
