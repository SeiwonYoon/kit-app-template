# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
Sequence Editor UI

기존 TBS 제어창은 유지하고, 별도의 ui.Window에서 step(동작)들을 원하는 순서대로 편집/실행/저장/불러오기.

저장 포맷(간단 JSON):
[
  {"type": "USD_TIMELINE", "mode": "MANUAL"|"AUTO", "start_frame": 200, "end_frame": 300, "loop": false},
  {"type": "ROTATE", "prim": "/World/X" or "Mesh_1", "duration": 1.0, "rx": 0, "ry": 90, "rz": 0},
  {"type": "MOVE", "prim": "/World/X" or "Mesh_1", "duration": 1.0, "dx": 100, "dy": 0, "dz": 0}
]
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import omni.kit.app as app
import omni.ui as ui
import omni.usd as ou

from .sequence_engine import SequenceRunner


STEP_TYPES = ["USD_TIMELINE", "MOVE", "ROTATE"]


class SequenceEditorWindow:
    def __init__(self, title: str = "TBS 시퀀스 편집기") -> None:
        self._window = ui.Window(title, width=560, height=720)
        self._steps: List[Dict[str, Any]] = []
        self._runner = SequenceRunner(on_sequence_completed=lambda: print("[SEQUENCE] 완료", flush=True))  # noqa: T201

        self._json_model = ui.SimpleStringModel("[]")

        self._steps_frame: Optional[ui.VStack] = None
        self._refresh_pending = False
        self._refresh_sub = None
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

    # ---------------- UI ----------------

    def _build(self) -> None:
        with self._window.frame:
            with ui.VStack(padding=10, spacing=8):
                with ui.HStack(spacing=8, height=28):
                    ui.Button("Step 추가", width=90, height=28, clicked_fn=self._add_step_default)
                    ui.Button("실행", width=80, height=28, clicked_fn=self._run_steps)
                    ui.Button("일시정지", width=90, height=28, clicked_fn=self._pause)
                    ui.Button("중지(초기화)", width=110, height=28, clicked_fn=self._stop)
                    ui.Button("JSON 저장(갱신)", width=120, height=28, clicked_fn=self._update_json_from_steps)
                    ui.Button("JSON 불러오기", width=120, height=28, clicked_fn=self._load_steps_from_json)

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
            self._refresh_sub = stream.create_subscription_to_pop(_do, name="morph.tbs_control.sequence_editor.refresh")
        except Exception:
            # fallback: 바로 호출 (최후 수단)
            self._refresh_pending = False
            self._refresh_steps_ui()

    def _build_one_step(self, parent: ui.VStack, idx: int, step: Dict[str, Any]) -> None:
        # NOTE: 너무 길지 않게 높이 조정 (duration 설정 줄까지만 자연스럽게 보이도록)
        # omni.ui는 Frame(height=...)가 내용을 자동으로 클리핑하지 않아,
        # 내부에 고정 높이 ScrollingFrame을 두어 "카드 높이 고정"을 확실히 보장한다.
        with parent:
            with ui.Frame(height=180, style={"background_color": 0xFF1E2024}):
                with ui.VStack(spacing=6, padding=8):
                    # Step 타이틀/접기 영역도 색으로 구분
                    with ui.Frame(style={"background_color": 0xFF191C20}):
                        with ui.CollapsableFrame(f"Step {idx+1}: {step.get('type','')}", collapsed=False):
                            # 카드 내용 영역은 고정 높이 + 내부 스크롤
                            with ui.ScrollingFrame(height=120):
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
                                                    step.update({"mode": "MANUAL", "start_frame": 200, "end_frame": 300, "loop": False})
                                                elif t == "MOVE":
                                                    step.update({"prim": "", "duration": 1.0, "dx": 100.0, "dy": 0.0, "dz": 0.0})
                                                else:
                                                    step.update({"prim": "", "duration": 1.0, "rx": 0.0, "ry": 90.0, "rz": 0.0})
                                                self._schedule_refresh()

                                            cb = ui.ComboBox(initial_type_idx, *STEP_TYPES)
                                            cb.model.add_item_changed_fn(_on_type_change)
                                            ui.Button("위", width=40, height=28, clicked_fn=lambda i=idx: self._move_step(i, -1))
                                            ui.Button("아래", width=50, height=28, clicked_fn=lambda i=idx: self._move_step(i, 1))
                                            ui.Button("삭제", width=60, height=28, clicked_fn=lambda i=idx: self._remove_step(i))

                                    # 설정 영역(입력/버튼) 배경을 별도로 분리
                                    with ui.Frame(style={"background_color": 0xFF262A30}):
                                        with ui.VStack(spacing=6, padding=8):
                                            t = (step.get("type") or "").upper()
                                            if t == "USD_TIMELINE":
                                                self._ui_step_usd_timeline(step)
                                            elif t == "ROTATE":
                                                self._ui_step_rotate(step)
                                            else:
                                                self._ui_step_move(step)
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
            ui.CheckBox(model=loop_model)
            ui.Label("LOOP", height=0)

            def _on_loop(_m):
                step["loop"] = bool(loop_model.get_value_as_bool())

            loop_model.add_value_changed_fn(_on_loop)

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
                ui.IntField(model=sf, width=80, height=28)
                ui.Label("END", width=40)
                ui.IntField(model=ef, width=80, height=28)

    def _ui_step_move(self, step: Dict[str, Any]) -> None:
        prim_model = ui.SimpleStringModel(str(step.get("prim", "")))

        def _on_prim(_m):
            step["prim"] = prim_model.get_value_as_string()

        prim_model.add_value_changed_fn(_on_prim)

        with ui.HStack(spacing=6, height=28):
            ui.Label("PRIM", width=60)
            ui.StringField(model=prim_model, width=340, height=28)
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
            ui.FloatField(model=dur, width=80, height=28)
            ui.Label("DX", width=30)
            ui.FloatField(model=dx, width=70, height=28)
            ui.Label("DY", width=30)
            ui.FloatField(model=dy, width=70, height=28)
            ui.Label("DZ", width=30)
            ui.FloatField(model=dz, width=70, height=28)

    def _ui_step_rotate(self, step: Dict[str, Any]) -> None:
        prim_model = ui.SimpleStringModel(str(step.get("prim", "")))
        prim_model.add_value_changed_fn(lambda _m: step.__setitem__("prim", prim_model.get_value_as_string()))

        with ui.HStack(spacing=6, height=28):
            ui.Label("PRIM", width=60)
            ui.StringField(model=prim_model, width=340, height=28)
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
            ui.FloatField(model=dur, width=80, height=28)
            ui.Label("RX", width=30)
            ui.FloatField(model=rx, width=70, height=28)
            ui.Label("RY", width=30)
            ui.FloatField(model=ry, width=70, height=28)
            ui.Label("RZ", width=30)
            ui.FloatField(model=rz, width=70, height=28)

    # ---------------- actions ----------------

    def _add_step_default(self) -> None:
        self._steps.append({"type": "MOVE", "prim": "", "duration": 1.0, "dx": 100.0, "dy": 0.0, "dz": 0.0})
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
        try:
            self._json_model.set_value(json.dumps(self._steps, ensure_ascii=False, indent=2))
        except Exception:
            pass

    def _load_steps_from_json(self) -> None:
        try:
            data = json.loads(self._json_model.get_value_as_string() or "[]")
            if isinstance(data, list):
                self._steps = data
                self._schedule_refresh()
        except Exception as e:
            print(f"[SEQUENCE] JSON load 실패: {e}", flush=True)  # noqa: T201

    def _run_steps(self) -> None:
        self._runner.run(self._steps)
        print(f"[SEQUENCE] 실행: {len(self._steps)} steps", flush=True)  # noqa: T201

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

            txt = self._json_model.get_value_as_string() or ""
            clipboard_copy(txt)
            print("[SEQUENCE] JSON 복사됨", flush=True)  # noqa: T201
        except Exception as e:
            # 클립보드 확장 미탑재 등의 경우: 로그로만 남김
            print(f"[SEQUENCE] JSON 복사 실패: {e}", flush=True)  # noqa: T201

    def _fill_selected_prim(self, model: ui.SimpleStringModel) -> None:
        """현재 뷰포트 선택 prim 경로를 PRIM 필드에 채움."""
        try:
            sel = ou.get_context().get_selection()
            paths = sel.get_selected_prim_paths() or []
            if paths:
                model.set_value(str(paths[0]))
        except Exception:
            pass
