# ---------------------------------------------------------------------
# ui_dummy.py  (Dummy UI: 외부 호출자처럼 Service Public API만 사용)
# - 주의: omni.ui.Label은 model= 파라미터를 받지 않는 버전이 많음
#   => 상태 표시용은 StringField(read-only)로 구현
# ---------------------------------------------------------------------
import omni.ui as ui


class DummySectionControlUI:
    """
    DummySectionControlUI
    - 테스트/디버그 용도로 SectionControlService를 UI로 조작한다.
    - "외부에서 호출"하는 것처럼 service의 public API만 사용한다.
      (controller 직접 접근 금지)
    """

    def __init__(self, service):
        self._service = service

        # UI models
        st0 = self._service.get_state()
        self._m_enabled = ui.SimpleBoolModel(bool(st0.get("enabled", False)))
        self._m_axis = ui.SimpleStringModel(str(st0.get("axis", "X")))
        self._m_flip = ui.SimpleBoolModel(bool(st0.get("flip", False)))
        self._m_offset = ui.SimpleFloatModel(float(st0.get("offset", 0.0)))

        # status models (표시 전용)
        self._m_stage_ready = ui.SimpleStringModel(str(bool(st0.get("stage_ready", False))))
        self._m_sec_mgr_ready = ui.SimpleStringModel(str(bool(st0.get("sec_mgr_ready", False))))
        self._m_widget_path = ui.SimpleStringModel(str(st0.get("widget_path", "")))
        self._m_applied_axis = ui.SimpleStringModel(str(st0.get("applied_axis", "")))
        self._m_applied_signed_offset = ui.SimpleStringModel(str(st0.get("applied_signed_offset", 0.0)))
        self._m_dirty_axis = ui.SimpleStringModel(str(bool(st0.get("dirty_axis", False))))
        self._m_dirty_offset = ui.SimpleStringModel(str(bool(st0.get("dirty_offset", False))))

        self._window = ui.Window("Section Control (Dummy UI)", width=520, height=330, visible=False)

        self._controls_stack = None
        self._btn_axis = {"X": None, "Y": None, "Z": None}

        self._build()
        self._bind()

        # 초기 1회 상태 표시 업데이트
        self._sync_status_from_service()

    @property
    def window(self):
        return self._window

    def destroy(self):
        self._window = None
        self._service = None

    # ---------------- internal helpers ----------------
    def _read_models(self):
        enabled = self._m_enabled.get_value_as_bool()
        axis = self._m_axis.get_value_as_string()
        flip = self._m_flip.get_value_as_bool()
        offset = self._m_offset.get_value_as_float()
        return enabled, axis, flip, offset

    def _sync_status_from_service(self):
        """service.get_state()를 다시 읽어서 표시용 모델 갱신"""
        if not self._service:
            return

        st = self._service.get_state()

        self._m_stage_ready.set_value(str(bool(st.get("stage_ready", False))))
        self._m_sec_mgr_ready.set_value(str(bool(st.get("sec_mgr_ready", False))))
        self._m_widget_path.set_value(str(st.get("widget_path", "")))

        self._m_applied_axis.set_value(str(st.get("applied_axis", "")))
        self._m_applied_signed_offset.set_value(str(st.get("applied_signed_offset", 0.0)))

        self._m_dirty_axis.set_value(str(bool(st.get("dirty_axis", False))))
        self._m_dirty_offset.set_value(str(bool(st.get("dirty_offset", False))))

    def _apply_from_models(self, reason: str):
        """
        UI → Service 로 적용 요청
        - controller 직접 접근 금지
        - service public API(set_all)만 호출
        """
        if not self._service:
            return

        enabled, axis, flip, offset = self._read_models()

        self._service.set_all(
            enabled=enabled,
            axis=axis,
            flip=flip,
            offset=offset,
            reason=reason,
        )

        if self._controls_stack:
            self._controls_stack.visible = bool(enabled)

        self._sync_status_from_service()

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

    def _ro_string(self, model: ui.SimpleStringModel, width=0):
        """
        read-only string 표시용 위젯.
        버전에 따라 StringField가 read_only 인자를 지원하지 않을 수 있어
        enabled=False로 안전하게 잠그는 방식 사용.
        """
        try:
            return ui.StringField(model=model, read_only=True, width=width)
        except TypeError:
            # 구버전 호환: read_only 미지원일 수 있음
            w = ui.StringField(model=model, width=width)
            try:
                w.enabled = False
            except Exception:
                pass
            return w

    # ---------------- UI build ----------------
    def _build(self):
        with self._window.frame:
            with ui.VStack(spacing=8, height=0):
                # --- Header / Enabled ---
                with ui.HStack(height=24):
                    ui.Label("Section", width=70)
                    ui.CheckBox(model=self._m_enabled)
                    ui.Label("On", width=24)
                    ui.Spacer()
                    ui.Label("stage_ready:", width=95)
                    self._ro_string(self._m_stage_ready, width=60)
                    ui.Spacer(width=10)
                    ui.Label("sec_mgr_ready:", width=105)
                    self._ro_string(self._m_sec_mgr_ready, width=60)

                # --- Controls (enabled일 때만) ---
                self._controls_stack = ui.VStack(spacing=8, height=0, visible=self._m_enabled.get_value_as_bool())
                with self._controls_stack:
                    with ui.HStack(height=28):
                        ui.Label("Axis", width=70)
                        self._btn_axis["X"] = ui.Button("X", clicked_fn=lambda: self._set_axis("X"))
                        self._btn_axis["Y"] = ui.Button("Y", clicked_fn=lambda: self._set_axis("Y"))
                        self._btn_axis["Z"] = ui.Button("Z", clicked_fn=lambda: self._set_axis("Z"))
                        ui.Spacer()

                    with ui.HStack(height=24):
                        ui.Label("Flip", width=70)
                        ui.CheckBox(model=self._m_flip)
                        ui.Spacer()

                    with ui.HStack(height=24):
                        ui.Label("Offset", width=70)
                        ui.FloatSlider(model=self._m_offset, min=-1000.0, max=1000.0)
                        ui.Spacer(width=8)
                        ui.FloatField(model=self._m_offset, width=90)
                        ui.Spacer(width=8)
                        ui.Button("Apply", width=70, clicked_fn=lambda: self._apply_from_models("ui_apply_btn"))

                ui.Separator()

                # --- Status (read-only) ---
                with ui.VStack(spacing=4, height=0):
                    ui.Label("Status (read-only)", style={"color": 0xFFAAAAAA})

                    with ui.HStack(height=20):
                        ui.Label("widget_path:", width=90)
                        self._ro_string(self._m_widget_path, width=0)

                    with ui.HStack(height=20):
                        ui.Label("applied_axis:", width=90)
                        self._ro_string(self._m_applied_axis, width=70)
                        ui.Spacer(width=12)
                        ui.Label("applied_offset:", width=110)
                        self._ro_string(self._m_applied_signed_offset, width=0)

                    with ui.HStack(height=20):
                        ui.Label("dirty_axis:", width=90)
                        self._ro_string(self._m_dirty_axis, width=70)
                        ui.Spacer(width=12)
                        ui.Label("dirty_offset:", width=110)
                        self._ro_string(self._m_dirty_offset, width=0)

                ui.Separator()
                ui.Label("즉시 반영 모드 (UI 조작 시 post_update에서 재시도 적용)", style={"color": 0xFFAAAAAA})

        self._refresh_axis_button_styles()

    def _bind(self):
        self._m_enabled.add_value_changed_fn(lambda m: self._apply_from_models("ui_enabled_changed"))
        self._m_flip.add_value_changed_fn(lambda m: self._apply_from_models("ui_flip_changed"))
        self._m_offset.add_value_changed_fn(lambda m: self._apply_from_models("ui_offset_changed"))