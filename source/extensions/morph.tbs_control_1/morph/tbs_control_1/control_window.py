# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
control_window.py — TBS 제어창 UI 및 이벤트 핸들러

【역할】
- build_control_window(ext): "TBS 제어창" 창. USD 타임라인(수동/자동), 가상 시그널 샘플,
  XML 제너레이터(6종 시퀀스 콤보·입력 필드), 우선 표시 접두사, prim 목록.
- refresh_object_list(ext): 드롭다운/목록 갱신.
- on_play_usd_animation / on_play_generator_sample / on_refresh_prim_list 등 버튼·콤보 핸들러.

【수정 포인트】
- USD 재생 UI: build_control_window() 상단 ~ "USD 애니메이션 정지" 버튼 근처.
- XML 제너레이터 UI: "XML 제너레이터 생성기" Frame 블록.
  · 콤보 항목 추가/순서 변경: ext._xml_seq_combo = ui.ComboBox(0, ...) 인자 목록.
  · 하단 입력 전환: on_xml_seq_changed — FROM/TO 보일지(ext._xml_ab_inputs_frame), PORT만 보일지(ext._xml_port_inputs_frame).
    → xml_generator.FROM_TO_SEQS / PORT_ID_ONLY_SEQS 와 동일한 규칙 유지.
  · OK/역파싱: on_xml_ok_clicked, on_xml_run_clicked — 내부 seqs 리스트는 콤보 순서와 반드시 일치.
- 애니메이션 버튼(예: 이동/포물선/회전): 파일 하단 on_* 및 SAMPLE_GENERATOR_JSON.

【XML 6종류와 UI 필드】 (로직·상수는 xml_generator.py)
- FROM_PORT_ID + TO_PORT_ID: MOVE_TRANSFERING, MOVE
- PORT_ID만: READYTOLOAD, ARRIVED, READYTOUNLOAD, REMOVED
새 종류 추가 시: xml_generator 수정 + 이 파일의 ComboBox·seqs 3곳 + 필요 시 IntField/모델 추가.

사용처: extension.py on_startup → build_control_window(self)
"""

from typing import Any, List

import omni.ui as ui
from pxr import Gf

from . import usd_animation_control
from . import xml_generator
from .curve_animation import make_parabolic_path, run_prim_curve_animation, stop_prim_curve_animation
from .prim_info import get_prim_display_name, safe_str
from .prim_utils import (
    collect_prim_paths_safe,
    find_all_prim_paths_by_name,
    get_prim_local_translate,
    get_stage,
    set_prim_translate_only,
)
from .rotate_animation import run_prim_rotate_animation, stop_prim_rotate_animation
from .selection_overlay import show_prim_info_in_viewport
from .signal_parser import parse_signal
from .translate_animation import run_prim_translate_animation, stop_prim_translate_animation

MAX_PRIMS_DISPLAY = 80
DEFAULT_PRIORITY_NAME_PREFIX = "Mesh_"
SAMPLE_GENERATOR_JSON = """{
  "objects": ["Mesh_308", "Mesh_561", "WalkwayEndA_01"],
  "animation": {
    "segments": [
      {"duration": 1.0, "delta": [100, 0, 0]},
      {"duration": 1.0, "delta": [0, 100, 0]},
      {"duration": 2.0, "delta": [-100, -100, 0]}
    ]
  }
}"""


def build_control_window(ext: Any) -> None:
    """TBS 제어창을 만들고 ext에 위젯/모델 참조를 저장."""
    ext._usd_anim_start_frame = ui.SimpleIntModel(200)
    ext._usd_anim_end_frame = ui.SimpleIntModel(300)
    ext._usd_anim_loop = ui.SimpleBoolModel(False)
    ext._usd_anim_auto_range_text = ui.SimpleStringModel("AUTO RANGE: (미확인)")
    ext._xml_from_port_model = ui.SimpleIntModel(1)
    ext._xml_to_port_model = ui.SimpleIntModel(6)
    ext._xml_port_id_model = ui.SimpleIntModel(1)
    ext._last_generated_xml = ""
    ext._priority_prefix_model = ui.SimpleStringModel(DEFAULT_PRIORITY_NAME_PREFIX)

    ext._control_window = ui.Window("TBS 제어창", width=460, height=640)
    with ext._control_window.frame:
        with ui.VStack(spacing=0):
            ui.Label("USD 파일 애니메이션 (타임라인)", height=0)
            with ui.HStack(spacing=8, height=28):
                ui.Label("범위", width=50, height=28)
                ext._usd_anim_mode_combo = ui.ComboBox(0, "수동", "자동")
                ext._usd_anim_mode_combo.model.add_item_changed_fn(lambda m, *a: on_usd_anim_mode_changed(ext))
                ui.Label("", width=0)
            ext._usd_anim_manual_frame_row = ui.HStack(spacing=8, height=30)
            with ext._usd_anim_manual_frame_row:
                ui.Label("시작 프레임", width=70, height=30)
                ui.IntField(model=ext._usd_anim_start_frame, width=60, height=30)
                ui.Label("끝 프레임", width=70, height=30)
                ui.IntField(model=ext._usd_anim_end_frame, width=60, height=30)
            ext._usd_anim_auto_range_row = ui.HStack(spacing=8, height=22)
            with ext._usd_anim_auto_range_row:
                ui.Label("AUTO", width=50, height=22)
                ui.Label("", model=ext._usd_anim_auto_range_text, height=22)
            ext._usd_anim_manual_frame_row.visible = True
            ext._usd_anim_auto_range_row.visible = False
            with ui.HStack(spacing=8, height=20):
                ui.CheckBox(model=ext._usd_anim_loop)
                ui.Label("루프", height=0)
            ui.Button("USD 파일 애니메이션 재생", height=28, clicked_fn=lambda: on_play_usd_animation(ext))
            ui.Button("USD 애니메이션 정지", height=24, clicked_fn=usd_animation_control.stop_usd_animation)
            ui.Spacer(height=6)
            ui.Button("가상 시그널 재생 (JSON 샘플)", height=28, clicked_fn=lambda: on_play_generator_sample(ext))
            ui.Spacer(height=6)
            ui.Rectangle(height=2, style={"background_color": 0xFF3A3A3A})
            ui.Spacer(height=6)
            with ui.Frame(style={"background_color": 0xFF23262B}):
                with ui.VStack(padding=8, spacing=6):
                    with ui.HStack(spacing=8, height=28):
                        ui.Label("XML 제너레이터 생성기", width=150, height=28, style={"color": 0xFFDDDDDD})
                        ext._xml_seq_combo = ui.ComboBox(
                            0,
                            xml_generator.SEQ_READYTOLOAD,
                            xml_generator.SEQ_ARRIVED,
                            xml_generator.SEQ_MOVE_TRANSFERING,
                            xml_generator.SEQ_MOVE,
                            xml_generator.SEQ_READYTOUNLOAD,
                            xml_generator.SEQ_REMOVED,
                        )
                        ext._xml_seq_combo.model.add_item_changed_fn(lambda m, *a: on_xml_seq_changed(ext))
                        ui.Button("OK", width=60, height=28, clicked_fn=lambda: on_xml_ok_clicked(ext))
                    ext._xml_ab_inputs_frame = ui.HStack(spacing=8, height=28)
                    with ext._xml_ab_inputs_frame:
                        ui.Label("FROM_PORT_ID", width=110, height=28)
                        ui.IntField(model=ext._xml_from_port_model, width=60, height=28)
                        ui.Label("TO_PORT_ID", width=90, height=28)
                        ui.IntField(model=ext._xml_to_port_model, width=60, height=28)
                    ext._xml_ab_inputs_frame.visible = True

                    ext._xml_port_inputs_frame = ui.HStack(spacing=8, height=28)
                    with ext._xml_port_inputs_frame:
                        ui.Label("PORT_ID", width=110, height=28)
                        ui.IntField(model=ext._xml_port_id_model, width=60, height=28)
                    ext._xml_port_inputs_frame.visible = False
                    # 콤보 초기 선택값 기준으로 입력 필드 표시 상태 동기화
                    on_xml_seq_changed(ext)
                    ui.Button("제너레이터 실행(역파싱)", height=28, clicked_fn=lambda: on_xml_run_clicked(ext))
            ui.Spacer(height=6)
            ui.Rectangle(height=2, style={"background_color": 0xFF3A3A3A})
            ui.Spacer(height=8)
            ui.Label("우선 표시 이름 규칙 (접두사, 비우면 순서대로 표시)", height=0)
            ui.StringField(model=ext._priority_prefix_model, height=22)
            ui.Spacer(height=4)
            ui.Label("로드된 USD 내 장비 prim (드롭다운)", height=0)
            ui.Button("목록 새로고침", height=28, clicked_fn=lambda: on_refresh_prim_list(ext))
            ui.Spacer(height=4)
            with ui.ScrollingFrame(style={"ScrollingFrame": {"padding": 0, "margin": 0}}):
                ext._object_list_frame = ui.VStack(height=0, alignment=ui.Alignment.LEFT_TOP)
    refresh_object_list(ext)


def on_usd_anim_mode_changed(ext: Any) -> None:
    try:
        idx = ext._usd_anim_mode_combo.model.get_item_value_model().as_int
    except Exception:
        idx = 0
    is_auto = idx == 1
    ext._usd_anim_manual_frame_row.visible = not is_auto
    ext._usd_anim_auto_range_row.visible = is_auto
    if is_auto:
        rng = usd_animation_control.resolve_saved_animation_frame_range()
        if rng:
            ext._usd_anim_auto_range_text.set_value(f"AUTO RANGE: {rng[0]} ~ {rng[1]}")
        else:
            ext._usd_anim_auto_range_text.set_value("AUTO RANGE: (감지 실패)")


def on_xml_seq_changed(ext: Any) -> None:
    try:
        idx = ext._xml_seq_combo.model.get_item_value_model().as_int
    except Exception:
        idx = 0
    seqs = [
        xml_generator.SEQ_READYTOLOAD,
        xml_generator.SEQ_ARRIVED,
        xml_generator.SEQ_MOVE_TRANSFERING,
        xml_generator.SEQ_MOVE,
        xml_generator.SEQ_READYTOUNLOAD,
        xml_generator.SEQ_REMOVED,
    ]
    seq = seqs[idx] if 0 <= idx < len(seqs) else xml_generator.SEQ_READYTOLOAD
    ext._xml_ab_inputs_frame.visible = seq in xml_generator.FROM_TO_SEQS
    ext._xml_port_inputs_frame.visible = seq in xml_generator.PORT_ID_ONLY_SEQS


def on_xml_ok_clicked(ext: Any) -> None:
    try:
        idx = ext._xml_seq_combo.model.get_item_value_model().as_int
    except Exception:
        idx = 0
    seqs = [
        xml_generator.SEQ_READYTOLOAD,
        xml_generator.SEQ_ARRIVED,
        xml_generator.SEQ_MOVE_TRANSFERING,
        xml_generator.SEQ_MOVE,
        xml_generator.SEQ_READYTOUNLOAD,
        xml_generator.SEQ_REMOVED,
    ]
    seq = seqs[idx] if 0 <= idx < len(seqs) else xml_generator.SEQ_READYTOLOAD
    try:
        if seq in xml_generator.FROM_TO_SEQS:
            from_port = ext._xml_from_port_model.get_value_as_int()
            to_port = ext._xml_to_port_model.get_value_as_int()
            xml = xml_generator.build_xml_string(seq, from_port_id=from_port, to_port_id=to_port)
        else:
            port_id = ext._xml_port_id_model.get_value_as_int()
            xml = xml_generator.build_xml_string(seq, port_id=port_id)
        ext._last_generated_xml = xml
        print(xml, flush=True)
    except Exception as e:
        print(f"[morph.tbs_control_1][xml_generator] XML 생성 실패: {e}", flush=True)


def on_xml_run_clicked(ext: Any) -> None:
    xml_text = (ext._last_generated_xml or "").strip()
    if not xml_text:
        print("[morph.tbs_control_1][xml_generator] 저장된 XML이 없습니다. 먼저 OK로 XML을 생성하세요.", flush=True)
        return
    parsed = xml_generator.parse_xml_string(xml_text)
    if not parsed:
        print("[morph.tbs_control_1][xml_generator] XML 역파싱 실패.", flush=True)
        return
    lines = ["[XML PARSE RESULT]"]
    if parsed.get("action_desc"):
        lines.append("[ACTION]")
        lines.append(parsed.get("action_desc", ""))

    for k in (
        "sequence_name",
        "destination",
        "origination",
        "tid",
        "facility",
        "equipment_id",
        "port_id",
        "from_port_id",
        "to_port_id",
    ):
        lines.append(f"{k} = {parsed.get(k, '')}")
    print("\n".join(lines), flush=True)


def on_play_usd_animation(ext: Any) -> None:
    loop = ext._usd_anim_loop.get_value_as_bool()
    try:
        mode = ext._usd_anim_mode_combo.model.get_item_value_model().as_int
    except Exception:
        mode = 0
    if mode == 1:
        rng = usd_animation_control.resolve_saved_animation_frame_range()
        if not rng:
            print("[USD ANIM] 자동 범위 감지 실패.", flush=True)
            return
        start, end = int(rng[0]), int(rng[1])
        ext._usd_anim_auto_range_text.set_value(f"AUTO RANGE: {start} ~ {end}")
    else:
        start = ext._usd_anim_start_frame.get_value_as_int()
        end = ext._usd_anim_end_frame.get_value_as_int()
    usd_animation_control.play_usd_animation(
        start_frame=start, end_frame=end, loop=loop,
        on_completed=(lambda: print(f"[USD ANIM] 완료: {start}~{end}", flush=True)) if not loop else None,
    )


def on_play_generator_sample(ext: Any) -> None:
    parsed = parse_signal(SAMPLE_GENERATOR_JSON, "json")
    if parsed:
        run_generator_from_parsed(ext, parsed)


def receive_signal_data(ext: Any, data: str, format: str = "json") -> bool:
    parsed = parse_signal(data, format)
    if not parsed:
        return False
    run_generator_from_parsed(ext, parsed)
    return True


def run_generator_from_parsed(ext: Any, parsed: dict) -> None:
    stage = get_stage()
    if not stage:
        return
    objects = parsed.get("objects") or []
    segments = parsed.get("segments") or []
    if not objects or not segments:
        return
    for name in objects:
        if not isinstance(name, str):
            continue
        paths = find_all_prim_paths_by_name(stage, name)
        for path in paths:
            if not path:
                continue
            stop_prim_translate_animation(path)
            stop_prim_curve_animation(path)
            run_prim_translate_animation(path, segments, loop=False)


def on_refresh_prim_list(ext: Any) -> None:
    stage = get_stage()
    if not stage:
        if getattr(ext, "_load_status_label", None):
            ext._load_status_label.text = "스테이지가 없습니다. USD를 먼저 로드하세요."
        return
    ext._tracked_paths = collect_prim_paths_safe(stage)
    refresh_object_list(ext)


def refresh_object_list(ext: Any) -> None:
    if ext._object_list_frame is None:
        return
    ext._object_list_frame.clear()
    stage = get_stage()
    if not stage:
        with ext._object_list_frame:
            ui.Label("USD를 먼저 로드하세요.")
        return

    def _valid_path(p: str) -> bool:
        try:
            return stage.GetPrimAtPath(p).IsValid()
        except (UnicodeDecodeError, UnicodeEncodeError):
            return False

    valid_paths = [p for p in ext._tracked_paths if _valid_path(p)]
    total = len(valid_paths)
    priority_prefix = (ext._priority_prefix_model.get_value_as_string().strip() or "")

    if priority_prefix:
        priority_paths: List[str] = []
        rest_paths: List[str] = []
        for p in valid_paths:
            try:
                prim = stage.GetPrimAtPath(p)
                if not prim or not prim.IsValid():
                    rest_paths.append(p)
                    continue
                name = safe_str(prim.GetName())
                if name.startswith(priority_prefix):
                    priority_paths.append(p)
                else:
                    rest_paths.append(p)
            except Exception:
                rest_paths.append(p)
        need = max(0, MAX_PRIMS_DISPLAY - len(priority_paths))
        display_paths = priority_paths[:MAX_PRIMS_DISPLAY] + rest_paths[:need]
    else:
        display_paths = valid_paths[:MAX_PRIMS_DISPLAY]

    with ext._object_list_frame:
        if total > MAX_PRIMS_DISPLAY:
            ui.Label(f"총 {total}개 prim 중 {len(display_paths)}개만 표시됩니다.", height=0)
            ui.Spacer(height=4)
        if priority_prefix:
            n_priority = min(len(priority_paths), MAX_PRIMS_DISPLAY)
            n_rest = len(display_paths) - n_priority
            ui.Label(f"접두사 '{priority_prefix}' 우선: {n_priority}개, 나머지 순서대로 {n_rest}개", height=0)
            ui.Spacer(height=4)
        for idx, prim_path in enumerate(display_paths):
            build_object_panel(ext, ext._object_list_frame, prim_path, idx + 1)


def build_object_panel(ext: Any, parent: ui.VStack, prim_path: str, index: int) -> None:
    try:
        stage = get_stage()
        prim = stage.GetPrimAtPath(prim_path) if stage else None
        if not prim or not prim.IsValid():
            return
        title = get_prim_display_name(prim, index)
        local = get_prim_local_translate(prim)
        pos_models = [
            ui.SimpleFloatModel(local[0]),
            ui.SimpleFloatModel(local[1]),
            ui.SimpleFloatModel(local[2]),
        ]

        def update_prim_position():
            s = get_stage()
            p = s.GetPrimAtPath(prim_path) if s else None
            if p and p.IsValid():
                set_prim_translate_only(p, Gf.Vec3f(
                    pos_models[0].get_value_as_float(),
                    pos_models[1].get_value_as_float(),
                    pos_models[2].get_value_as_float(),
                ))

        with parent:
            with ui.CollapsableFrame(title, collapsed=False):
                with ui.VStack(spacing=6):
                    ui.Label("Position (X, Y, Z)", height=0)
                    with ui.HStack():
                        for i, label in enumerate(["X", "Y", "Z"]):
                            ui.Label(label, width=24)
                            ui.FloatField(model=pos_models[i])
                    for m in pos_models:
                        m.add_value_changed_fn(lambda _: update_prim_position())
                    ui.Spacer(height=4)
                    ui.Button("3D 정보 보기", height=24, clicked_fn=lambda p=prim_path: show_prim_info_in_viewport(ext, p))
                    ui.Spacer(height=4)
                    with ui.HStack(spacing=8):
                        ui.Button("button_0", width=0, clicked_fn=lambda p=prim_path: on_button_0(ext, p))
                        ui.Button("button_1", width=0, clicked_fn=lambda p=prim_path: on_button_1(ext, p))
                        ui.Button("button_2", width=0, clicked_fn=lambda p=prim_path: on_button_2(ext, p))
    except (UnicodeDecodeError, UnicodeEncodeError):
        return


def on_button_0(ext: Any, prim_path: str) -> None:
    stop_prim_translate_animation(prim_path)
    stop_prim_curve_animation(prim_path)
    stop_prim_rotate_animation(prim_path)
    run_prim_translate_animation(
        prim_path,
        [
            {"duration": 1.0, "delta": (100.0, 0.0, 0.0)},
            {"duration": 1.0, "delta": (0.0, 0.0, 100.0)},
        ],
        loop=False,
    )


def on_button_1(ext: Any, prim_path: str) -> None:
    stop_prim_translate_animation(prim_path)
    stop_prim_curve_animation(prim_path)
    stop_prim_rotate_animation(prim_path)
    stage = get_stage()
    prim = stage.GetPrimAtPath(prim_path) if stage else None
    if not prim or not prim.IsValid():
        return
    start = get_prim_local_translate(prim)
    start_t = (start[0], start[1], start[2])
    end_t = (start[0] + 100.0, start[1], start[2])
    path_points = make_parabolic_path(start=start_t, end=end_t, arc_height=30.0, num_points=24)
    run_prim_curve_animation(prim_path, path_points, duration_sec=1.0, loop=False)


def on_button_2(ext: Any, prim_path: str) -> None:
    stop_prim_translate_animation(prim_path)
    stop_prim_curve_animation(prim_path)
    stop_prim_rotate_animation(prim_path)
    run_prim_rotate_animation(
        prim_path,
        [{"duration": 3.0, "delta": (0.0, 90.0, 0.0)}],
        loop=False,
    )
