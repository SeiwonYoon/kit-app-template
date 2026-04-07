# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
sequence_editor.py — TBS 시퀀스 편집기 UI (별도 창)

【역할】
- STEP_TYPES(USD_TIMELINE, MOVE, ROTATE, DELAY 등)별로 행 UI를 구성하고, JSON 저장/불러오기.
- SequenceRunner에 스텝 리스트를 넘검 실행·일시정지·중지.

【수정 가이드】
- 지원 스텝 종류 추가/이름 변경: STEP_TYPES, _build_step_row*, 시리얼라이즈 필드
- 실행 로직·병렬 그룹 규칙: sequence_engine.py (SequenceRunner, _execute_step 등)
- 제어창 XML 6종과 무관 — XML은 control_window + xml_generator 참고

저장 포맷(요약 JSON):
- run_with_previous: 병렬 그룹 시작
- step_delay_ms: 다음 그룹 지연(ms), 음수는 앵커 기준 앞당김

【운영/유지보수 상세 가이드】
- "현재 위치부터 시작" 옵션:
  · _start_from_current / _start_from_current_paths / _start_snapshot 메타를 첫 스텝에 저장
  · _start_snapshot 은 TBS_OFFSET 만이 아니라 뷰포트에서 합성된 부모-상대 로컬 변환(XformCache)을
    mode=composed_local + m16 로 저장하고, 재생 시 _apply_tbs_for_target_local_matrix 로 맞춘다.
  · 실행 엔진 해석은 sequence_engine.py SequenceRunner.run()
- MOVE 랜덤 범위:
  · duration_min/max, dx_min/max, dy_min/max, dz_min/max 키를 사용
  · 실제 샘플링은 sequence_engine._sample_step_value()
- 새 step 타입 추가 절차:
  1) STEP_TYPES에 타입명 추가
  2) _build_one_step의 타입 분기 + 기본값 초기화 추가
  3) _ui_step_* UI 생성 함수 추가
  4) JSON 저장/로드 키를 _update_json_from_steps / _load_steps_from_json 경로에서 유지
  5) sequence_engine._start_step 분기 구현
- JSON을 시뮬레이션에서 재사용할 때:
  · 본 파일은 편집/저장 담당, 실제 이벤트 매핑 실행은 control_window.py
  · 파일 경로 등록은 config/event_animation_rules.json 또는 event_animation_map.json

【주요 메서드 색인】
- 창/스텝: __init__, _build_ui, _build_one_step, _build_step_row*, _ui_step_move/usd_timeline/rotate/delay
- 실행: _run, _stop, _pause, SequenceRunner 연동
- JSON: _update_json_from_steps, _load_steps_from_json, _sync_runtime_start_options_to_steps, _capture_start_snapshot
- 기타: _fill_selected_prim, _fill_rotate_pivot_world_from_prim
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import omni.kit.app as app
import omni.ui as ui
import omni.usd as ou
from pxr import Gf, Usd, UsdGeom

from .sequence_engine import SequenceRunner, capture_composed_local_start_snapshot_for_paths, resolve_prim_paths_multi


CHECKBOX_WHITE_STYLE = {
    # 체크 표시는 검정색(마크), 배경은 밝게
    "color": 0xFF000000,
    "background_color": 0xFFEEEEEE,
}

INPUT_FIELD_STYLE = {
    "background_color": 0xFF3B4250,
    "color": 0xFFFFFFFF,
}

STEP_TYPES = ["USD_TIMELINE", "MOVE", "ROTATE", "DELAY"]
_OFFSET_SUFFIX = "TBS_OFFSET"


class SequenceEditorWindow:
    def __init__(self, title: str = "TBS 시퀀스 편집기") -> None:
        self._window = ui.Window(title, width=650, height=720)
        # 창 크기를 사용자가 650보다 작게 줄이지 못하게 고정
        # try:
        #     self._window.flags = self._window.flags | ui.WINDOW_FLAGS_NO_RESIZE
        # except Exception:
        #     self._window.flags = ui.WINDOW_FLAGS_NO_RESIZE
        self._steps: List[Dict[str, Any]] = []
        self._runner = SequenceRunner(on_sequence_completed=lambda: print("[SEQUENCE] 완료", flush=True))  # noqa: T201

        self._json_model = ui.SimpleStringModel("[]")
        self._start_from_current_model = ui.SimpleBoolModel(False)
        self._start_from_current_paths_model = ui.SimpleStringModel("")

        self._steps_frame: Optional[ui.VStack] = None
        self._refresh_pending = False
        self._refresh_sub = None
        self._json_update_sub = None
        # ROTATE 스텝: "현재 중심(BBox) 기준 회전" 체크박스 모델
        self._rotate_auto_pivot_models: Dict[int, ui.SimpleBoolModel] = {}
        self._parallel_with_prev_models: Dict[int, ui.SimpleBoolModel] = {}
        self._step_delay_ms_models: Dict[int, ui.SimpleIntModel] = {}
        self._build()

    def destroy(self) -> None:
        try:
            self._runner.stop()
        except Exception:
            pass
        if self._window:
            self._window.destroy()
        self._window = None
        self._steps_frame = None
        if self._refresh_sub is not None:
            try:
                self._refresh_sub.unsubscribe()
            except Exception:
                pass
            self._refresh_sub = None
        if self._json_update_sub is not None:
            try:
                self._json_update_sub.unsubscribe()
            except Exception:
                pass
            self._json_update_sub = None

    # ---------------- UI ----------------

    def _build(self) -> None:
        with self._window.frame:
            with ui.VStack(padding=10, spacing=8):
                with ui.HStack(spacing=8, height=28):
                    ui.Button("Step 추가", width=90, height=28, clicked_fn=self._add_step_default)
                    ui.Button("실행", width=80, height=28, clicked_fn=self._run_steps)
                    ui.Button("일시정지", width=90, height=28, clicked_fn=self._pause)
                    ui.Button("중지(초기화)", width=110, height=28, clicked_fn=self._stop)
                    ui.Button("현재스탭으로 json 생성", width=160, height=28, clicked_fn=self._update_json_from_steps)

                # 상단 버튼은 고정, 아래 영역만 스크롤
                # NOTE: 일부 Kit 버전에서는 height=0 이 레이아웃에서 0으로 접혀 JSON/Steps가 안 보입니다.
                # 창 높이(720) 기준으로 충분한 고정 높이를 둡니다.
                with ui.ScrollingFrame(height=650):
                    with ui.VStack(spacing=8):
                        ui.Label("시퀀스 JSON (저장/로드용)", height=0)
                        # multiline 지원이 되는 Kit 버전에서는 내부 스크롤이 가능하게 설정합니다.
                        # (일부 버전에서 multiline 인자가 없어서 깨질 수 있으므로 TypeError 방어)
                        try:
                            ui.StringField(model=self._json_model, height=70, multiline=True)
                        except TypeError:
                            ui.StringField(model=self._json_model, height=70)
                        with ui.HStack(spacing=8, height=28):
                            ui.Button("현재 JSON 상태로 스텝 생성하기", height=28, clicked_fn=self._load_steps_from_json)
                            ui.Button("전체 JSON 복사", width=130, height=28, clicked_fn=self._copy_json_to_clipboard)

                        ui.Spacer(height=6)
                        ui.Label("Steps", height=0)
                        self._steps_frame = ui.VStack(spacing=6)
                        self._refresh_steps_ui()

    def _refresh_steps_ui(self) -> None:
        if not self._steps_frame:
            return
        self._rotate_auto_pivot_models.clear()
        self._parallel_with_prev_models.clear()
        self._step_delay_ms_models.clear()
        self._steps_frame.clear()
        for idx, step in enumerate(self._steps):
            self._build_one_step(self._steps_frame, idx, step)

    def _schedule_refresh(self) -> None:
        """
        UI 이벤트/드로우 중에 Container.clear()를 호출하면 오류가 나므로,
        다음 post_update 프레임에 refresh를 수행한다.
        """
        if self._refresh_pending:
            return
        self._refresh_pending = True

        def _do(_e=None):
            self._refresh_pending = False
            try:
                self._refresh_steps_ui()
            finally:
                if self._refresh_sub is not None:
                    try:
                        self._refresh_sub.unsubscribe()
                    except Exception:
                        pass
                    self._refresh_sub = None

        try:
            stream = app.get_app().get_post_update_event_stream()
            self._refresh_sub = stream.create_subscription_to_pop(_do, name="morph.tbs_control_1.sequence_editor.refresh")
        except Exception:
            # fallback: 바로 호출 (최후 수단)
            self._refresh_pending = False
            self._refresh_steps_ui()

    def _build_one_step(self, parent: ui.VStack, idx: int, step: Dict[str, Any]) -> None:
        # 스텝 카드 내부 스크롤은 불필요하다고 판단하여 제거.
        # 대신 카드 높이를 충분히 확보해서 클리핑을 방지한다.
        with parent:
            with ui.Frame(height=320, style={"background_color": 0xFF1E2024}):
                with ui.VStack(spacing=6, padding=8):
                    with ui.Frame(style={"background_color": 0xFF191C20}):
                        with ui.CollapsableFrame(f"Step {idx+1}: {step.get('type','')}", collapsed=False):
                            with ui.VStack(spacing=6, padding=6):
                                ui.Rectangle(height=2, style={"background_color": 0xFF3A3A3A})
                                # 드롭다운/버튼 영역 배경 분리(가독성 개선)
                                with ui.Frame(style={"background_color": 0xFF20242A}):
                                    with ui.HStack(spacing=6, height=28):
                                        # type selector
                                        initial_type_idx = (
                                            STEP_TYPES.index(step.get("type", "MOVE"))
                                            if step.get("type") in STEP_TYPES
                                            else 1
                                        )

                                        def _on_type_change(model, *_):
                                            t = STEP_TYPES[model.get_item_value_model().as_int]
                                            step.clear()
                                            step["type"] = t
                                            # init defaults
                                            if t == "USD_TIMELINE":
                                                step.update(
                                                    {
                                                        "mode": "MANUAL",
                                                        "start_frame": 200,
                                                        "end_frame": 300,
                                                        "loop": False,
                                                        # 기본 OFF: 필요할 때만 USD_TIMELINE 시작 전 오프셋 보정 수행
                                                        "offset_correction_enabled": False,
                                                        "offset_correct_prims": "",
                                                        "hide_enabled": False,
                                                        "hide_prims": "",
                                                        "run_with_previous": False,
                                                        "step_delay_ms": 0,
                                                    }
                                                )
                                            elif t == "MOVE":
                                                step.update(
                                                    {
                                                        "prim": "",
                                                        "duration": 1.0,
                                                        "dx": 100.0,
                                                        "dy": 0.0,
                                                        "dz": 0.0,
                                                        "hide_enabled": False,
                                                        "hide_prims": "",
                                                        "run_with_previous": False,
                                                        "step_delay_ms": 0,
                                                    }
                                                )
                                            elif t == "ROTATE":
                                                step.update(
                                                    {
                                                        "prim": "",
                                                        "duration": 1.0,
                                                        "rx": 0.0,
                                                        "ry": 90.0,
                                                        "rz": 0.0,
                                                        # 새 옵션: 실행 시점 prim 중심(BBox)을 pivot으로 잡아 제자리 회전처럼 보이게 함
                                                        "auto_pivot_world_center": False,
                                                        "user_axis_rotate": False,
                                                        "pivot_wx": 0.0,
                                                        "pivot_wy": 0.0,
                                                        "pivot_wz": 0.0,
                                                        "hide_enabled": False,
                                                        "hide_prims": "",
                                                        "run_with_previous": False,
                                                        "step_delay_ms": 0,
                                                    }
                                                )
                                            elif t == "DELAY":
                                                step.update(
                                                    {
                                                        "duration": 1.0,
                                                        "hide_enabled": False,
                                                        "hide_prims": "",
                                                        "run_with_previous": False,
                                                        "step_delay_ms": 0,
                                                    }
                                                )
                                            self._schedule_refresh()

                                        cb = ui.ComboBox(initial_type_idx, *STEP_TYPES)
                                        cb.model.add_item_changed_fn(_on_type_change)
                                        ui.Button("위", width=40, height=28, clicked_fn=lambda i=idx: self._move_step(i, -1))
                                        ui.Button("아래", width=50, height=28, clicked_fn=lambda i=idx: self._move_step(i, 1))
                                        ui.Button("삭제", width=60, height=28, clicked_fn=lambda i=idx: self._remove_step(i))

                                # 설정 영역(입력/버튼) 배경을 별도로 분리
                                with ui.Frame(style={"background_color": 0xFF262A30}):
                                    with ui.VStack(spacing=6, padding=8):
                                        # "현재 위치부터 시작" 옵션은 반드시 첫 스텝 내부에만 노출한다.
                                        # (JSON 저장 시 첫 스텝 메타로 스냅샷이 들어가야 유지보수/재현이 쉬움)
                                        if idx == 0:
                                            with ui.HStack(spacing=8, height=28):
                                                ui.CheckBox(model=self._start_from_current_model, style=CHECKBOX_WHITE_STYLE)
                                                ui.Label("현재 위치부터 시작", width=120)
                                                ui.Label("대상 경로", width=85)
                                                ui.StringField(
                                                    model=self._start_from_current_paths_model,
                                                    width=360,
                                                    height=28,
                                                    style=INPUT_FIELD_STYLE,
                                                )
                                            ui.Label(
                                                "※ 이 옵션은 Step 1의 메타로 저장됩니다. "
                                                "체크 후 '현재스탭으로 json 생성'을 누르면 부모 기준 합성 로컬 변환(TBS_OFFSET뿐 아님)이 "
                                                "_start_snapshot(mode=composed_local, m16)으로 기록됩니다.",
                                                height=0,
                                                word_wrap=True,
                                            )
                                        t = (step.get("type") or "").upper()
                                        if t == "USD_TIMELINE":
                                            self._ui_step_usd_timeline(step)
                                        elif t == "ROTATE":
                                            self._ui_step_rotate(step, idx)
                                        elif t == "DELAY":
                                            self._ui_step_delay(step)
                                        else:
                                            self._ui_step_move(step)
                                        self._ui_step_timing(step, idx)
                                        self._ui_step_hide_options(step)
                                ui.Rectangle(height=2, style={"background_color": 0xFF3A3A3A})

    def _ui_step_usd_timeline(self, step: Dict[str, Any]) -> None:
        mode_items = ["MANUAL", "AUTO"]
        initial_mode_idx = 0 if str(step.get("mode", "MANUAL")).upper() == "MANUAL" else 1

        def _on_mode_changed(model, *_):
            step["mode"] = mode_items[model.get_item_value_model().as_int]

        with ui.HStack(spacing=6, height=28):
            ui.Label("MODE", width=60)
            cb = ui.ComboBox(initial_mode_idx, *mode_items)
            cb.model.add_item_changed_fn(_on_mode_changed)
            loop_model = ui.SimpleBoolModel(bool(step.get("loop", False)))
            ui.CheckBox(model=loop_model, style=CHECKBOX_WHITE_STYLE)
            ui.Label("LOOP", height=0)

            def _on_loop(_m):
                step["loop"] = bool(loop_model.get_value_as_bool())

            loop_model.add_value_changed_fn(_on_loop)

        # offset correction toggle (default OFF)
        off_model = ui.SimpleBoolModel(bool(step.get("offset_correction_enabled", False)))

        def _on_off(_m):
            step["offset_correction_enabled"] = bool(off_model.get_value_as_bool())

        off_model.add_value_changed_fn(_on_off)
        with ui.HStack(spacing=6, height=28):
            ui.CheckBox(model=off_model, style=CHECKBOX_WHITE_STYLE)
            ui.Label("오프셋 보정 적용", width=120)
        ui.Label(
            "체크 시 USD 재생 시작 프레임 기준으로 월드 오프셋(TBS_OFFSET) 보정을 수행합니다. (기본 OFF)",
            height=0,
            word_wrap=True,
        )

        # manual only fields
        if str(step.get("mode", "MANUAL")).upper() == "MANUAL":
            sf = ui.SimpleIntModel(int(step.get("start_frame", 200)))
            ef = ui.SimpleIntModel(int(step.get("end_frame", 300)))

            def _on_sf(_m):
                step["start_frame"] = int(sf.get_value_as_int())

            def _on_ef(_m):
                step["end_frame"] = int(ef.get_value_as_int())

            sf.add_value_changed_fn(_on_sf)
            ef.add_value_changed_fn(_on_ef)

            with ui.HStack(spacing=6, height=28):
                ui.Label("START", width=60)
                ui.IntField(model=sf, width=80, height=28, style=INPUT_FIELD_STYLE)
                ui.Label("END", width=40)
                ui.IntField(model=ef, width=80, height=28, style=INPUT_FIELD_STYLE)

        ocp = ui.SimpleStringModel(str(step.get("offset_correct_prims", "")))

        def _on_ocp(_m):
            step["offset_correct_prims"] = ocp.get_value_as_string()

        ocp.add_value_changed_fn(_on_ocp)
        with ui.HStack(spacing=6, height=28):
            ui.Label("보정PRIM", width=60)
            ui.StringField(model=ocp, width=420, height=28, style=INPUT_FIELD_STYLE)
        ui.Label(
            "MOVE 후 USD 재생 시 '원위치로 튀는' prim 이름/경로(,). 비우면 MOVE/ROTATE에 나온 prim만 보정.",
            height=36,
            word_wrap=True,
        )

    def _ui_step_move(self, step: Dict[str, Any]) -> None:
        prim_model = ui.SimpleStringModel(str(step.get("prim", "")))

        def _on_prim(_m):
            step["prim"] = prim_model.get_value_as_string()

        prim_model.add_value_changed_fn(_on_prim)

        with ui.HStack(spacing=6, height=28):
            ui.Label("PRIM", width=60)
            ui.StringField(model=prim_model, width=340, height=28, style=INPUT_FIELD_STYLE)
            ui.Button("선택 prim", width=90, height=28, clicked_fn=lambda: self._fill_selected_prim(prim_model))

        dur = ui.SimpleFloatModel(float(step.get("duration", 1.0)))
        dx = ui.SimpleFloatModel(float(step.get("dx", 100.0)))
        dy = ui.SimpleFloatModel(float(step.get("dy", 0.0)))
        dz = ui.SimpleFloatModel(float(step.get("dz", 0.0)))

        dur.add_value_changed_fn(lambda _m: step.__setitem__("duration", float(dur.get_value_as_float())))
        dx.add_value_changed_fn(lambda _m: step.__setitem__("dx", float(dx.get_value_as_float())))
        dy.add_value_changed_fn(lambda _m: step.__setitem__("dy", float(dy.get_value_as_float())))
        dz.add_value_changed_fn(lambda _m: step.__setitem__("dz", float(dz.get_value_as_float())))

        with ui.HStack(spacing=6, height=28):
            ui.Label("DUR", width=60)
            ui.FloatField(model=dur, width=80, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("DX", width=30)
            ui.FloatField(model=dx, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("DY", width=30)
            ui.FloatField(model=dy, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("DZ", width=30)
            ui.FloatField(model=dz, width=70, height=28, style=INPUT_FIELD_STYLE)

        # 랜덤 범위(선택 입력): *_min/max가 있으면 실행 시 범위 랜덤
        # 유지보수 규칙:
        # - 고정값(duration/dx/dy/dz)은 기본값/호환용으로 유지
        # - *_min/max가 존재하면 sequence_engine._sample_step_value가 우선 사용
        dmin = ui.SimpleFloatModel(float(step.get("duration_min", step.get("duration", 1.0))))
        dmax = ui.SimpleFloatModel(float(step.get("duration_max", step.get("duration", 1.0))))
        dxmin = ui.SimpleFloatModel(float(step.get("dx_min", step.get("dx", 0.0))))
        dxmax = ui.SimpleFloatModel(float(step.get("dx_max", step.get("dx", 0.0))))
        dymin = ui.SimpleFloatModel(float(step.get("dy_min", step.get("dy", 0.0))))
        dymax = ui.SimpleFloatModel(float(step.get("dy_max", step.get("dy", 0.0))))
        dzmin = ui.SimpleFloatModel(float(step.get("dz_min", step.get("dz", 0.0))))
        dzmax = ui.SimpleFloatModel(float(step.get("dz_max", step.get("dz", 0.0))))
        dmin.add_value_changed_fn(lambda _m: step.__setitem__("duration_min", float(dmin.get_value_as_float())))
        dmax.add_value_changed_fn(lambda _m: step.__setitem__("duration_max", float(dmax.get_value_as_float())))
        dxmin.add_value_changed_fn(lambda _m: step.__setitem__("dx_min", float(dxmin.get_value_as_float())))
        dxmax.add_value_changed_fn(lambda _m: step.__setitem__("dx_max", float(dxmax.get_value_as_float())))
        dymin.add_value_changed_fn(lambda _m: step.__setitem__("dy_min", float(dymin.get_value_as_float())))
        dymax.add_value_changed_fn(lambda _m: step.__setitem__("dy_max", float(dymax.get_value_as_float())))
        dzmin.add_value_changed_fn(lambda _m: step.__setitem__("dz_min", float(dzmin.get_value_as_float())))
        dzmax.add_value_changed_fn(lambda _m: step.__setitem__("dz_max", float(dzmax.get_value_as_float())))
        with ui.HStack(spacing=6, height=28):
            ui.Label("RND DUR", width=60)
            ui.FloatField(model=dmin, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("~", width=10)
            ui.FloatField(model=dmax, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("RND DX", width=52)
            ui.FloatField(model=dxmin, width=58, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("~", width=10)
            ui.FloatField(model=dxmax, width=58, height=28, style=INPUT_FIELD_STYLE)
        with ui.HStack(spacing=6, height=28):
            ui.Label("RND DY", width=60)
            ui.FloatField(model=dymin, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("~", width=10)
            ui.FloatField(model=dymax, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("RND DZ", width=52)
            ui.FloatField(model=dzmin, width=58, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("~", width=10)
            ui.FloatField(model=dzmax, width=58, height=28, style=INPUT_FIELD_STYLE)

    def _ui_step_delay(self, step: Dict[str, Any]) -> None:
        dur = ui.SimpleFloatModel(float(step.get("duration", 1.0)))
        dur.add_value_changed_fn(lambda _m: step.__setitem__("duration", float(dur.get_value_as_float())))

        with ui.HStack(spacing=6, height=28):
            ui.Label("DELAY", width=60)
            ui.FloatField(model=dur, width=120, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("sec", height=0)

    def _ui_step_rotate(self, step: Dict[str, Any], step_idx: int) -> None:
        # ROTATE 확장 옵션(권장):
        # - auto_pivot_world_center=True: 실행 순간 prim의 월드 BBox 중심을 pivot으로 잡아 "제자리 회전"처럼 보이게 함.
        if "auto_pivot_world_center" not in step:
            step["auto_pivot_world_center"] = False

        auto_pivot = ui.SimpleBoolModel(bool(step.get("auto_pivot_world_center", False)))
        auto_pivot.add_value_changed_fn(
            lambda _m: step.__setitem__("auto_pivot_world_center", bool(auto_pivot.get_value_as_bool()))
        )
        self._rotate_auto_pivot_models[step_idx] = auto_pivot

        with ui.HStack(spacing=6, height=28):
            ui.CheckBox(model=auto_pivot, style=CHECKBOX_WHITE_STYLE)
            ui.Label("현재 중심 기준 회전(자동)", width=200)
        ui.Label(
            "체크 시: 실행 시점의 prim '월드 BBox 중심'을 pivot으로 잡습니다. "
            "객체가 어디로 이동해 있어도 제자리(중심)에서 회전하는 것처럼 보이게 하는 것이 목표입니다.",
            height=0,
            word_wrap=True,
        )
        prim_model = ui.SimpleStringModel(str(step.get("prim", "")))
        prim_model.add_value_changed_fn(lambda _m: step.__setitem__("prim", prim_model.get_value_as_string()))

        with ui.HStack(spacing=6, height=28):
            ui.Label("PRIM", width=60)
            ui.StringField(model=prim_model, width=340, height=28, style=INPUT_FIELD_STYLE)
            ui.Button("선택 prim", width=90, height=28, clicked_fn=lambda: self._fill_selected_prim(prim_model))

        dur = ui.SimpleFloatModel(float(step.get("duration", 1.0)))
        rx = ui.SimpleFloatModel(float(step.get("rx", 0.0)))
        ry = ui.SimpleFloatModel(float(step.get("ry", 90.0)))
        rz = ui.SimpleFloatModel(float(step.get("rz", 0.0)))

        dur.add_value_changed_fn(lambda _m: step.__setitem__("duration", float(dur.get_value_as_float())))
        rx.add_value_changed_fn(lambda _m: step.__setitem__("rx", float(rx.get_value_as_float())))
        ry.add_value_changed_fn(lambda _m: step.__setitem__("ry", float(ry.get_value_as_float())))
        rz.add_value_changed_fn(lambda _m: step.__setitem__("rz", float(rz.get_value_as_float())))

        with ui.HStack(spacing=6, height=28):
            ui.Label("DUR", width=60)
            ui.FloatField(model=dur, width=80, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("RX", width=30)
            ui.FloatField(model=rx, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("RY", width=30)
            ui.FloatField(model=ry, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("RZ", width=30)
            ui.FloatField(model=rz, width=70, height=28, style=INPUT_FIELD_STYLE)
        # NOTE: "루트 축 회전 + 회전 중심(월드)" 옵션은 UX/정확도 이슈로 편집기 UI에서 제거.
        # 기존 JSON에 user_axis_rotate/pivot_wx/y/z 키가 남아있어도 그대로 유지(호환)하며,
        # 필요 시 추후 별도 전용 편집 UI로 재도입할 수 있다.

    def _ui_step_timing(self, step: Dict[str, Any], step_idx: int) -> None:
        """
        병렬 실행 체크 + 지연(ms). 첫 스텝은 동시 실행 불가.
        - 그룹 첫 줄(동시 실행 OFF): step_delay_ms = 이전 앵커 종료 후 ms(다음 그룹 시작). 음수 가능.
        - 동시 실행 ON: step_delay_ms = 그룹 리더 시작 후 ms만큼 뒤에 이 스텝 시작(0이면 리더와 동시).
        """
        if "run_with_previous" not in step:
            step["run_with_previous"] = False
        if "step_delay_ms" not in step:
            step["step_delay_ms"] = 0

        parallel = ui.SimpleBoolModel(bool(step.get("run_with_previous", False)))
        delay_ms = ui.SimpleIntModel(int(step.get("step_delay_ms", 0)))

        def _on_parallel(_m=None) -> None:
            step["run_with_previous"] = bool(parallel.get_value_as_bool())

        def _on_delay(_m=None) -> None:
            step["step_delay_ms"] = int(delay_ms.get_value_as_int())

        parallel.add_value_changed_fn(_on_parallel)
        delay_ms.add_value_changed_fn(_on_delay)
        _on_parallel()
        _on_delay()

        self._parallel_with_prev_models[step_idx] = parallel
        self._step_delay_ms_models[step_idx] = delay_ms

        with ui.HStack(spacing=6, height=28):
            if step_idx == 0:
                ui.Label("—", width=22)
                ui.Label("첫 스텝은 동시 실행 없음", width=200)
            else:
                ui.CheckBox(model=parallel, style=CHECKBOX_WHITE_STYLE)
                ui.Label("이전 스텝과 동시 실행", width=200)
        with ui.HStack(spacing=6, height=28):
            ui.Label("지연(ms)", width=60)
            ui.IntField(model=delay_ms, width=100, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("1000=1초", width=56)
        ui.Label(
            "※ 그룹 첫 줄: 이전 앵커 끝난 뒤 ms(음수면 앵커 종료 전에 다음 그룹 시작). "
            "※ 동시 실행 체크 시: 리더 시작 후 ms 뒤 이 스텝 시작.",
            height=0,
            word_wrap=True,
        )

    def _ui_step_hide_options(self, step: Dict[str, Any]) -> None:
        """
        각 스텝 하단: 숨길 prim 체크박스 + 경로 입력.
        - hide_prims: 콤마·공백으로 다중 입력 (하위 prim 포함)
        """
        hide_enabled = ui.SimpleBoolModel(bool(step.get("hide_enabled", False)))
        hide_paths = ui.SimpleStringModel(str(step.get("hide_prims", "")))
        hide_enabled.add_value_changed_fn(
            lambda _m: step.__setitem__("hide_enabled", bool(hide_enabled.get_value_as_bool()))
        )
        hide_paths.add_value_changed_fn(lambda _m: step.__setitem__("hide_prims", hide_paths.get_value_as_string()))

        with ui.HStack(spacing=6, height=28):
            ui.CheckBox(model=hide_enabled, style=CHECKBOX_WHITE_STYLE)
            ui.Label("숨길 prim", width=70)
            # 남는 폭을 자동 흡수해서 한 줄 유지
            ui.StringField(model=hide_paths, width=500, height=28, style=INPUT_FIELD_STYLE)
        ui.Label("숨길 prim 입력: 콤마·공백 구분 (하위 prim 포함)", height=0)

    # ---------------- actions ----------------

    def _add_step_default(self) -> None:
        self._steps.append(
            {
                "type": "MOVE",
                "prim": "",
                "duration": 1.0,
                "dx": 100.0,
                "dy": 0.0,
                "dz": 0.0,
                "hide_enabled": False,
                "hide_prims": "",
                "run_with_previous": False,
                "step_delay_ms": 0,
            }
        )
        self._schedule_refresh()

    def _remove_step(self, idx: int) -> None:
        if 0 <= idx < len(self._steps):
            self._steps.pop(idx)
            self._schedule_refresh()

    def _move_step(self, idx: int, delta: int) -> None:
        j = idx + delta
        if 0 <= idx < len(self._steps) and 0 <= j < len(self._steps):
            self._steps[idx], self._steps[j] = self._steps[j], self._steps[idx]
            self._schedule_refresh()

    def _update_json_from_steps(self) -> None:
        """
        JSON 저장(갱신).

        중요: "현재 위치부터 시작"이 켜져 있을 때는 현재 스테이지 transform 평가가
        update 스트림에서 한 프레임 늦게 반영되는 경우가 있어, 저장 클릭과 같은 프레임에
        스냅샷을 캡처하면 직전 값(예: 500)이 저장될 수 있다.

        따라서 enabled일 때는 "다음 프레임(post_update)"에 스냅샷을 캡처한 뒤 JSON을 갱신한다.
        """
        try:
            enabled = bool(self._start_from_current_model.get_value_as_bool())
        except Exception:
            enabled = False

        def _commit() -> None:
            try:
                # "마지막 스텝 편집 중" 상태에서 바로 JSON 생성 버튼을 누르면,
                # 체크박스/딜레이(ms) 등 일부 UI 모델 값이 step dict에 아직 반영되지 않을 수 있다.
                # 실행 버튼(_run_steps)과 동일한 기준으로 먼저 모델→dict 동기화를 수행한다.
                self._flush_rotate_step_flags_to_dict()
                self._flush_timing_models_to_dict()
                self._sync_runtime_start_options_to_steps()
                self._json_model.set_value(json.dumps(self._steps, ensure_ascii=False, indent=2))
            except Exception:
                pass

        if not enabled:
            _commit()
            return

        # enabled=True: 다음 프레임에 캡처 후 저장
        if self._json_update_sub is not None:
            try:
                self._json_update_sub.unsubscribe()
            except Exception:
                pass
            self._json_update_sub = None

        def _do(_e=None):
            try:
                _commit()
            finally:
                if self._json_update_sub is not None:
                    try:
                        self._json_update_sub.unsubscribe()
                    except Exception:
                        pass
                    self._json_update_sub = None

        try:
            stream = app.get_app().get_post_update_event_stream()
            self._json_update_sub = stream.create_subscription_to_pop(
                _do,
                name="morph.tbs_control_1.sequence_editor.json_update",
            )
        except Exception:
            _commit()

    def _load_steps_from_json(self) -> None:
        try:
            data = json.loads(self._json_model.get_value_as_string() or "[]")
            if isinstance(data, list):
                self._steps = data
                # JSON에서 읽은 시작옵션을 UI 체크/텍스트 박스로 역주입
                self._load_runtime_start_options_from_steps()
                self._schedule_refresh()
        except Exception as e:
            print(f"[SEQUENCE] JSON load 실패: {e}", flush=True)  # noqa: T201

    def _run_steps(self) -> None:
        self._flush_rotate_step_flags_to_dict()
        self._flush_timing_models_to_dict()
        self._sync_runtime_start_options_to_steps()
        self._runner.run(self._steps)
        print(f"[SEQUENCE] 실행: {len(self._steps)} steps", flush=True)  # noqa: T201

    def _flush_rotate_step_flags_to_dict(self) -> None:
        """체크박스 모델 → step (value_changed 없이 바로 실행 버튼을 누른 경우 포함)."""
        for idx, step in enumerate(self._steps):
            if not isinstance(step, dict):
                continue
            if (step.get("type") or "").upper() != "ROTATE":
                continue
            m = self._rotate_auto_pivot_models.get(idx)
            if m is not None:
                step["auto_pivot_world_center"] = bool(m.get_value_as_bool())
            # 편집기 UI에서 더 이상 지원하지 않는 월드 피봇 모드 플래그들은 실행 시 혼선을 만들 수 있어 강제 해제.
            # (기존 JSON 호환을 위해 키 자체는 남아있을 수 있지만, 실행 관점에서는 OFF가 안전)
            step["user_axis_rotate"] = False
            step["world_pivot_rotate"] = False
            step["user_pivot_rotate"] = False

    def _flush_timing_models_to_dict(self) -> None:
        """동시 실행·지연(ms) 모델 → step (실행 직전 동기화)."""
        for idx, step in enumerate(self._steps):
            if not isinstance(step, dict):
                continue
            if idx == 0:
                step["run_with_previous"] = False
            else:
                pm = self._parallel_with_prev_models.get(idx)
                if pm is not None:
                    step["run_with_previous"] = bool(pm.get_value_as_bool())
            dm = self._step_delay_ms_models.get(idx)
            if dm is not None:
                step["step_delay_ms"] = int(dm.get_value_as_int())

    def _stop(self) -> None:
        self._runner.stop()
        print("[SEQUENCE] 중지(초기화)", flush=True)  # noqa: T201

    def _pause(self) -> None:
        self._runner.pause()
        print("[SEQUENCE] 일시정지", flush=True)  # noqa: T201

    def _copy_json_to_clipboard(self) -> None:
        """현재 JSON을 클립보드로 복사."""
        try:
            from omni.kit.clipboard import copy as clipboard_copy

            self._sync_runtime_start_options_to_steps()
            self._json_model.set_value(json.dumps(self._steps, ensure_ascii=False, indent=2))
            txt = self._json_model.get_value_as_string() or ""
            clipboard_copy(txt)
            print("[SEQUENCE] JSON 복사됨", flush=True)  # noqa: T201
        except Exception as e:
            # 클립보드 확장 미탑재 등의 경우: 로그로만 남김
            print(f"[SEQUENCE] JSON 복사 실패: {e}", flush=True)  # noqa: T201

    def _sync_runtime_start_options_to_steps(self) -> None:
        if not self._steps:
            return
        first = self._steps[0]
        if not isinstance(first, dict):
            return
        enabled = bool(self._start_from_current_model.get_value_as_bool())
        first["_start_from_current"] = enabled
        path_text = str(self._start_from_current_paths_model.get_value_as_string() or "").strip()
        first["_start_from_current_paths"] = path_text
        if enabled:
            # 실행/저장 시점의 현재 뷰포트 상태를 JSON에 스냅샷으로 고정.
            # 이후 시뮬레이션에서 재사용해도 동일 시작점을 재현할 수 있다.
            first["_start_snapshot"] = self._capture_start_snapshot(path_text)
        else:
            # 체크 해제 시 스냅샷 메타를 제거해 기존 baseline 시작 동작으로 복귀.
            first.pop("_start_snapshot", None)

    def _load_runtime_start_options_from_steps(self) -> None:
        if not self._steps:
            self._start_from_current_model.set_value(False)
            self._start_from_current_paths_model.set_value("")
            return
        first = self._steps[0]
        if not isinstance(first, dict):
            self._start_from_current_model.set_value(False)
            self._start_from_current_paths_model.set_value("")
            return
        self._start_from_current_model.set_value(bool(first.get("_start_from_current", False)))
        self._start_from_current_paths_model.set_value(str(first.get("_start_from_current_paths", "") or ""))

    def _capture_start_snapshot(self, path_text: str) -> Dict[str, Dict[str, Any]]:
        """
        현재 뷰포트(스테이지)의 부모-상대 합성 로컬 변환을 JSON 메타(_start_snapshot)로 저장한다.
        (TBS_OFFSET 한 쌍만이 아니라 일반 translate/rotate 합성 포함 — sequence_engine 캡처 함수 사용)
        - path_text 비어있음: 시퀀스에 등장하는 MOVE/ROTATE 대상 전체
        - path_text 있음: 입력된 경로들만
        """
        stage = ou.get_context().get_stage()
        if not stage:
            return {}

        target_paths: List[str] = []
        if path_text:
            # 특정 경로 지정 모드: 입력된 경로만 현재 상태를 캡처 (콤마·공백 구분)
            target_paths.extend(resolve_prim_paths_multi(path_text))
        else:
            # 전체 모드: 시퀀스에서 실제로 움직이는 대상(MOVE/ROTATE)만 자동 수집
            for st in self._steps:
                if not isinstance(st, dict):
                    continue
                t = str(st.get("type", "")).upper()
                if t not in ("MOVE", "ROTATE"):
                    continue
                target_paths.extend(resolve_prim_paths_multi(str(st.get("prim", "") or "")))
        # 중복 제거(순서 유지)
        seen = set()
        uniq_paths = []
        for p in target_paths:
            if p in seen:
                continue
            seen.add(p)
            uniq_paths.append(p)

        return capture_composed_local_start_snapshot_for_paths(stage, uniq_paths)

    def _fill_selected_prim(self, model: ui.SimpleStringModel) -> None:
        """현재 뷰포트 선택 prim 경로를 PRIM 필드에 채움."""
        try:
            sel = ou.get_context().get_selection()
            paths = sel.get_selected_prim_paths() or []
            if paths:
                model.set_value(str(paths[0]))
        except Exception:
            pass

    def _fill_rotate_pivot_world_from_prim(
        self,
        step: Dict[str, Any],
        pwx: ui.SimpleFloatModel,
        pwy: ui.SimpleFloatModel,
        pwz: ui.SimpleFloatModel,
    ) -> None:
        """ROTATE 월드 모드: 첫 번째 대상 prim의 로컬 원점 월드 위치(translation)를 pivot_w*에 채움."""
        try:
            paths = resolve_prim_paths_multi(str(step.get("prim") or ""))
            if not paths:
                return
            stage = ou.get_context().get_stage()
            if not stage:
                return
            prim = stage.GetPrimAtPath(paths[0])
            if not prim or not prim.IsValid():
                return
            cache = UsdGeom.XformCache(Usd.TimeCode.Default())
            M = Gf.Matrix4d(cache.GetLocalToWorldTransform(prim))
            tr = M.ExtractTranslation()
            x, y, z = float(tr[0]), float(tr[1]), float(tr[2])
            pwx.set_value(x)
            pwy.set_value(y)
            pwz.set_value(z)
            step["pivot_wx"] = x
            step["pivot_wy"] = y
            step["pivot_wz"] = z
        except Exception:
            pass
