# morph/section_control/ui_dummy.py
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import omni.ui as ui
from .core import _log


class DummySectionControlUI:
    def __init__(self, service):
        self._service = service

        st0 = self._service.controller.get_state()
        self._m_enabled = ui.SimpleBoolModel(st0["enabled"])
        self._m_axis = ui.SimpleStringModel(st0["axis"])
        self._m_flip = ui.SimpleBoolModel(st0["flip"])
        self._m_offset = ui.SimpleFloatModel(st0["offset"])

        self._window = ui.Window("Section Control (Dummy UI)", width=420, height=220, visible=False)
        self._controls_stack = None
        self._btn_axis = {"X": None, "Y": None, "Z": None}

        self._build(st0)
        self._bind()

    @property
    def window(self):
        return self._window

    def destroy(self):
        self._window = None
        self._service = None

    # ---------------- internals ----------------
    def _apply_from_models(self, reason: str):
        enabled = self._m_enabled.get_value_as_bool()
        axis = self._m_axis.get_value_as_string()
        flip = self._m_flip.get_value_as_bool()
        offset = self._m_offset.get_value_as_float()

        _log(f"apply_from_models reason={reason} enabled={enabled} axis={axis} flip={flip} offset={offset}")

        # 핵심: UI는 service 한 군데만 때린다
        self._service.set_all_from_ui(enabled, axis, flip, offset, reason)

        if self._controls_stack:
            self._controls_stack.visible = bool(enabled)

    def _set_axis(self, axis: str):
        self._m_axis.set_value(axis)
        self._refresh_axis_button_styles()
        self._apply_from_models(f"ui_axis_{axis}")

    def _refresh_axis_button_styles(self):
        current = self._m_axis.get_value_as_string()
        for a, btn in self._btn_axis.items():
            if not btn:
                continue
            btn.style = {"background_color": 0xFF4A90E2} if a == current else {"background_color": 0xFF2B2B2B}

    def _build(self, st0):
        with self._window.frame:
            with ui.VStack(spacing=8, height=0):
                with ui.HStack(height=24):
                    ui.Label("Section", width=60)
                    ui.CheckBox(model=self._m_enabled)
                    ui.Label("On", width=20)
                    ui.Spacer()
                    ui.Label("stage_ready:", width=90)
                    ui.Label(str(st0["stage_ready"]), width=0)

                self._controls_stack = ui.VStack(spacing=8, height=0, visible=bool(st0["enabled"]))
                with self._controls_stack:
                    with ui.HStack(height=28):
                        ui.Label("Axis", width=60)
                        self._btn_axis["X"] = ui.Button("X", clicked_fn=lambda: self._set_axis("X"))
                        self._btn_axis["Y"] = ui.Button("Y", clicked_fn=lambda: self._set_axis("Y"))
                        self._btn_axis["Z"] = ui.Button("Z", clicked_fn=lambda: self._set_axis("Z"))
                        ui.Spacer()

                    with ui.HStack(height=24):
                        ui.Label("Flip", width=60)
                        ui.CheckBox(model=self._m_flip)
                        ui.Spacer()

                    with ui.HStack(height=24):
                        ui.Label("Offset", width=60)
                        ui.FloatSlider(model=self._m_offset, min=-1000.0, max=1000.0)
                        ui.Spacer(width=8)
                        ui.FloatField(model=self._m_offset, width=90)

                ui.Separator()
                ui.Label("즉시 반영 모드 (UI 조작 시 post_update에서 재시도 적용)", style={"color": 0xFFAAAAAA})

        self._refresh_axis_button_styles()

    def _bind(self):
        self._m_enabled.add_value_changed_fn(lambda m: self._apply_from_models("ui_enabled_changed"))
        self._m_flip.add_value_changed_fn(lambda m: self._apply_from_models("ui_flip_changed"))
        self._m_offset.add_value_changed_fn(lambda m: self._apply_from_models("ui_offset_changed"))
