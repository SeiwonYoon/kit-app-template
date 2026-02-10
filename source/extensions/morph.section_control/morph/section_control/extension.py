import carb
import carb.events
import carb.settings
import omni.ext
import omni.ui as ui
import omni.usd
import omni.kit.app
import omni.kit.livestream.messaging as messaging

# ✅ 추가
import omni.kit.window.preferences as prefs

from pxr import Gf, UsdGeom

from omni.kit.window.section.common import SectionManager
from omni.kit.window.section.common import (
    SETTING_SECTION_ENABLED,
    SETTING_SECTION_DIRECTION,
    SETTING_SECTION_ALWAYS_DISPLAY,
    SETTING_SECTION_LIGHT,
)

# -----------------------------------------------------------------------------
# Message-bus based "Web API" helper (pattern aligned with sample's ovapi.py)
# -----------------------------------------------------------------------------
class MessageBusAPI:
    """
    Web client sends event: <name>_request with payload { id, ...args }
    Kit responds: <name>_response with payload { id, response } or { id, error }
    """
    def __init__(self):
        self._subs = {}
        self._bus = omni.kit.app.get_app().get_message_bus_event_stream()

    def destroy(self):
        for sub in self._subs.values():
            try:
                sub.unsubscribe()
            except Exception:
                pass
        self._subs.clear()

    def request(self, func=None, *, name=None):
        def _decorator(f):
            api_name = name or f.__name__
            req_name = f"{api_name}_request"
            resp_name = f"{api_name}_response"

            req_type = carb.events.type_from_string(req_name)
            resp_type = carb.events.type_from_string(resp_name)

            # allow streaming layer to forward this response to web client
            messaging.register_event_type_to_send(resp_name)

            def _on_event(e: carb.events.IEvent):
                payload = dict(e.payload.get_dict())
                call_id = payload.pop("id", -1)

                try:
                    result = f(**payload)
                    out = {"id": call_id, "response": result}
                except Exception as ex:
                    out = {"id": call_id, "error": str(ex)}

                if call_id != -1:
                    self._bus.dispatch(resp_type, payload=out)
                    self._bus.pump()

            sub = self._bus.create_subscription_to_pop_by_type(
                req_type, _on_event, name=req_name
            )
            self._subs[req_name] = sub
            return f

        if func is None:
            return _decorator
        return _decorator(func)


# -----------------------------------------------------------------------------
# Section Control Core (LAZY INIT + Stage Event Apply)
# -----------------------------------------------------------------------------
class SectionController:
    """
    Controls omni.kit.window.section:
      - enable/disable section tool
      - axis align (X/Y/Z)
      - flip direction
      - offset by moving the section widget prim

    IMPORTANT:
      - SectionManager MUST NOT be constructed before a USD Stage exists.
        (prevents: NoneType has no attribute GetSessionLayer)
      - So we lazy-init SectionManager when stage becomes available.
    """
    AXES = ("X", "Y", "Z")

    def __init__(self):
        self._settings = carb.settings.get_settings()

        # ✅ DO NOT create SectionManager here (stage may be None)
        self._sec_mgr = None
        self._widget_path = None

        # cached state
        self._enabled = bool(self._settings.get(SETTING_SECTION_ENABLED) or False)
        self._axis = "X"
        self._flip = False
        self._offset = 0.0

        # optional settings (not used yet, but kept for parity)
        self._always_display = bool(self._settings.get(SETTING_SECTION_ALWAYS_DISPLAY) or False)
        self._light = bool(self._settings.get(SETTING_SECTION_LIGHT) or False)

    def _get_stage(self):
        return omni.usd.get_context().get_stage()

    def is_stage_ready(self) -> bool:
        return self._get_stage() is not None

    def _ensure_ready(self) -> bool:
        """
        Ensure SectionManager + widget prim path exist.
        Returns True if ready; False if stage is not available yet.
        """
        if self._sec_mgr is not None and self._widget_path is not None:
            return True

        stage = self._get_stage()
        if stage is None:
            return False

        # ✅ safe to create now
        self._sec_mgr = SectionManager()
        self._widget_path = self._sec_mgr.get_section_widget_prim(True)
        return True

    def _get_widget_prim(self):
        if not self._ensure_ready():
            return None

        stage = self._get_stage()
        if not stage:
            return None

        prim = stage.GetPrimAtPath(self._widget_path)
        if not prim or not prim.IsValid():
            return None
        return prim

    def _apply_widget_translation(self, axis: str, offset: float):
        """
        Move the section widget prim along chosen axis by 'offset' (stage units).
        """
        prim = self._get_widget_prim()
        if prim is None:
            # stage 준비 전에는 적용 불가 → 값만 저장하고 나중에 apply_cached()에서 적용
            raise RuntimeError("USD stage not ready or section widget prim is not available yet.")

        xform = UsdGeom.Xformable(prim)

        # Keep it simple: replace translate op (or create one) and set it.
        ops = xform.GetOrderedXformOps()
        t_op = None
        for op in ops:
            if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                t_op = op
                break
        if t_op is None:
            t_op = xform.AddTranslateOp()

        v = Gf.Vec3d(0.0, 0.0, 0.0)
        if axis == "X":
            v[0] = offset
        elif axis == "Y":
            v[1] = offset
        elif axis == "Z":
            v[2] = offset
        t_op.Set(v)

    def _apply_offset_to_stage(self):
        """
        Apply signed offset to actual widget prim, if ready.
        """
        if not self._ensure_ready():
            return False

        signed_offset = -self._offset if self._flip else self._offset
        self._apply_widget_translation(self._axis, signed_offset)
        return True

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self._settings.set(SETTING_SECTION_ENABLED, self._enabled)
        return self.get_state()

    def set_axis(self, axis: str):
        axis = (axis or "").upper()
        if axis not in self.AXES:
            raise ValueError(f"axis must be one of {self.AXES}")

        self._axis = axis

        # stage ready면 실제로 align + offset 적용
        if self._ensure_ready():
            self._sec_mgr.align_widget(axis.lower())
            self._apply_offset_to_stage()

        return self.get_state()

    def set_flip(self, flip: bool):
        self._flip = bool(flip)

        # Direction setting exists in sample (SETTING_SECTION_DIRECTION).
        # We'll map: flip=False -> 1, flip=True -> 0
        self._settings.set(SETTING_SECTION_DIRECTION, 0 if self._flip else 1)

        # stage ready면 실제 적용
        if self._ensure_ready():
            self._apply_offset_to_stage()

        return self.get_state()

    def set_offset(self, offset: float):
        self._offset = float(offset)

        # stage ready면 실제 적용
        if self._ensure_ready():
            self._apply_offset_to_stage()

        return self.get_state()

    def set_all(self, enabled: bool, axis: str, flip: bool, offset: float):
        # order: enable -> axis align -> flip -> offset
        self.set_enabled(enabled)
        self.set_axis(axis)
        self.set_flip(flip)
        self.set_offset(offset)
        return self.get_state()

    def apply_cached_to_stage_if_ready(self):
        """
        Call this when stage becomes available/opened.
        Applies cached axis/flip/offset to the actual widget.
        """
        if not self._ensure_ready():
            return False

        # enable is already in settings
        self._sec_mgr.align_widget(self._axis.lower())
        self._apply_offset_to_stage()
        return True

    def get_state(self):
        return {
            "enabled": self._enabled,
            "axis": self._axis,
            "flip": self._flip,
            "offset": self._offset,
            "widget_path": str(self._widget_path) if self._widget_path else "",
            "stage_ready": self.is_stage_ready(),
        }


# -----------------------------------------------------------------------------
# Extension + Dummy UI (post_update show + stage event apply)
# -----------------------------------------------------------------------------
class MyCompanySectionControlExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._ext_id = ext_id

        # core (✅ safe: doesn't touch SectionManager yet)
        self._controller = SectionController()

        # stage event subscription: when a stage is opened/available, apply cached values
        self._stage_event_sub = None
        try:
            ctx = omni.usd.get_context()
            self._stage_event_sub = ctx.get_stage_event_stream().create_subscription_to_pop(
                self._on_stage_event,
                name="section_control_stage_events",
            )
        except Exception as ex:
            carb.log_warn(f"[section_control] failed to subscribe stage events: {ex}")

        # web api
        self._api = MessageBusAPI()

        @self._api.request(name="section_get")
        def section_get():
            return self._controller.get_state()

        @self._api.request(name="section_set_enabled")
        def section_set_enabled(enabled: bool):
            return self._controller.set_enabled(enabled)

        @self._api.request(name="section_set_axis")
        def section_set_axis(axis: str):
            return self._controller.set_axis(axis)

        @self._api.request(name="section_set_flip")
        def section_set_flip(flip: bool):
            return self._controller.set_flip(flip)

        @self._api.request(name="section_set_offset")
        def section_set_offset(offset: float):
            return self._controller.set_offset(offset)

        @self._api.request(name="section_set_all")
        def section_set_all(enabled: bool, axis: str, flip: bool, offset: float):
            return self._controller.set_all(enabled, axis, flip, offset)

        # dummy ui (✅ create hidden, show after post_update for viewer)
        self._window = ui.Window("Section Control (Dummy UI)", width=420, height=240, visible=False)
        with self._window.frame:
            with ui.VStack(spacing=8, height=0):
                ui.Label("Web 메시지 수신 대신, 여기서 동일 동작을 테스트할 수 있습니다.", word_wrap=True)

                # ✅ 여기 버튼 1개 추가 (Preferences 열기)
                with ui.HStack(height=28):
                    ui.Button(
                        "Open Preferences",
                        clicked_fn=lambda: prefs.show_preferences_window(),
                        tooltip="Open Preferences window (Ctrl+P)"
                    )
                    ui.Spacer()

                # models
                st0 = self._controller.get_state()
                self._m_enabled = ui.SimpleBoolModel(st0["enabled"])
                self._m_axis = ui.SimpleStringModel(st0["axis"])
                self._m_flip = ui.SimpleBoolModel(st0["flip"])
                self._m_offset = ui.SimpleFloatModel(st0["offset"])

                def _apply_from_ui():
                    st = self._controller.set_all(
                        enabled=self._m_enabled.get_value_as_bool(),
                        axis=self._m_axis.get_value_as_string(),
                        flip=self._m_flip.get_value_as_bool(),
                        offset=self._m_offset.get_value_as_float(),
                    )
                    carb.log_info(f"[section_control] applied: {st}")

                with ui.HStack(height=24):
                    ui.CheckBox(model=self._m_enabled)
                    ui.Label("Section Enable", width=0)
                    ui.Spacer(width=10)
                    ui.CheckBox(model=self._m_flip)
                    ui.Label("Flip", width=0)

                with ui.HStack(height=24):
                    ui.Label("Axis", width=60)

                    def _set_axis(a):
                        self._m_axis.set_value(a)
                        _apply_from_ui()

                    ui.Button("X", clicked_fn=lambda: _set_axis("X"))
                    ui.Button("Y", clicked_fn=lambda: _set_axis("Y"))
                    ui.Button("Z", clicked_fn=lambda: _set_axis("Z"))
                    ui.Spacer()

                with ui.HStack(height=24):
                    ui.Label("Offset", width=60)
                    ui.FloatSlider(model=self._m_offset, min=-1000.0, max=1000.0)
                    ui.Spacer(width=8)
                    ui.FloatField(model=self._m_offset, width=90)

                with ui.HStack(height=28):
                    ui.Button("Apply", clicked_fn=_apply_from_ui)
                    ui.Button("Off", clicked_fn=lambda: self._controller.set_enabled(False))
                    ui.Button("On", clicked_fn=lambda: self._controller.set_enabled(True))
                    ui.Spacer()

                # reactive hooks
                self._m_enabled.add_value_changed_fn(lambda m: _apply_from_ui())
                self._m_flip.add_value_changed_fn(lambda m: _apply_from_ui())
                self._m_offset.add_value_changed_fn(lambda m: _apply_from_ui())

                ui.Separator()
                ui.Label("Web API names:", style={"color": 0xFFAAAAAA})
                ui.Label("- section_get", style={"color": 0xFFAAAAAA})
                ui.Label(
                    "- section_set_enabled / section_set_axis / section_set_flip / section_set_offset",
                    style={"color": 0xFFAAAAAA},
                )
                ui.Label("- section_set_all", style={"color": 0xFFAAAAAA})

        # ✅ Viewer layout 타이밍 대응: post_update에서 1회 표시
        self._post_update_sub = None
        try:
            app = omni.kit.app.get_app()
            stream = app.get_post_update_event_stream()

            def _show_once(e):
                if self._window:
                    self._window.visible = True
                    self._window.focus()
                if self._post_update_sub:
                    self._post_update_sub.unsubscribe()
                    self._post_update_sub = None

            self._post_update_sub = stream.create_subscription_to_pop(
                _show_once,
                name="section_control_show_window_once",
            )
        except Exception as ex:
            carb.log_warn(f"[section_control] failed to schedule window show: {ex}")
            # fallback
            try:
                self._window.visible = True
            except Exception:
                pass

        carb.log_info("[section_control] started")

    def _on_stage_event(self, e: carb.events.IEvent):
        # stage가 생기면(열리면) 캐시 적용
        try:
            if self._controller and self._controller.is_stage_ready():
                applied = self._controller.apply_cached_to_stage_if_ready()
                if applied:
                    carb.log_info("[section_control] applied cached section state after stage became ready.")
        except Exception as ex:
            carb.log_warn(f"[section_control] stage event apply failed: {ex}")

    def on_shutdown(self):
        try:
            if self._post_update_sub:
                self._post_update_sub.unsubscribe()
        except Exception:
            pass
        self._post_update_sub = None

        try:
            if self._stage_event_sub:
                self._stage_event_sub.unsubscribe()
        except Exception:
            pass
        self._stage_event_sub = None

        try:
            if self._api:
                self._api.destroy()
        except Exception:
            pass
        self._api = None

        self._controller = None
        self._window = None

        carb.log_info("[section_control] shutdown")
