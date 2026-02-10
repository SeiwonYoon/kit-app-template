import carb
import carb.events
import carb.settings
import omni.ext
import omni.ui as ui
import omni.usd
import omni.kit.app
import omni.kit.livestream.messaging as messaging

from pxr import Gf, UsdGeom, Sdf, Usd

from omni.kit.window.section.common import SectionManager
from omni.kit.window.section.common import (
    SETTING_SECTION_ENABLED,
    SETTING_SECTION_DIRECTION,
    SETTING_SECTION_ALWAYS_DISPLAY,
    SETTING_SECTION_LIGHT,
    SETTING_SECTION_MANIPULATOR,
)

# -----------------------------------------------------------------------------
# Message-bus based "Web API" helper
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

            sub = self._bus.create_subscription_to_pop_by_type(req_type, _on_event, name=req_name)
            self._subs[req_name] = sub
            return f

        if func is None:
            return _decorator
        return _decorator(func)


# -----------------------------------------------------------------------------
# Section Control Core (deferred apply-safe)
# -----------------------------------------------------------------------------
class SectionController:
    AXES = ("X", "Y", "Z")

    def __init__(self):
        self._settings = carb.settings.get_settings()

        self._sec_mgr = None
        self._widget_path: Sdf.Path | None = None
        self._widget_prim: Usd.Prim | None = None

        self._enabled = bool(self._settings.get(SETTING_SECTION_ENABLED) or False)
        self._axis = "X"
        self._flip = False
        self._offset = 0.0

        self._always_display = bool(self._settings.get(SETTING_SECTION_ALWAYS_DISPLAY) or False)
        self._light = bool(self._settings.get(SETTING_SECTION_LIGHT) or False)

    def _get_stage(self):
        return omni.usd.get_context().get_stage()

    def is_stage_ready(self) -> bool:
        return self._get_stage() is not None

    def invalidate_widget_cache(self):
        self._widget_prim = None
        self._widget_path = None
        self._sec_mgr = None

    def _ensure_ready(self) -> bool:
        if self._sec_mgr is not None and self._widget_path is not None:
            return True

        stage = self._get_stage()
        if stage is None:
            return False

        self._sec_mgr = SectionManager()

        # get_section_widget_prim(True) may return Prim / Sdf.Path / str
        w = self._sec_mgr.get_section_widget_prim(True)

        if isinstance(w, Usd.Prim):
            self._widget_prim = w
            self._widget_path = w.GetPath()
        elif isinstance(w, Sdf.Path):
            self._widget_prim = None
            self._widget_path = w
        else:
            self._widget_prim = None
            self._widget_path = Sdf.Path(str(w))

        return True

    def _get_widget_prim(self):
        if not self._ensure_ready():
            return None

        stage = self._get_stage()
        if stage is None:
            return None

        if self._widget_prim and self._widget_prim.IsValid():
            return self._widget_prim

        prim = stage.GetPrimAtPath(self._widget_path)  # ✅ always Sdf.Path
        if not prim or not prim.IsValid():
            return None

        self._widget_prim = prim
        return prim

    def _apply_widget_translation(self, axis: str, offset: float):
        prim = self._get_widget_prim()
        if prim is None:
            raise RuntimeError("USD stage not ready or section widget prim is not available yet.")

        xform = UsdGeom.Xformable(prim)

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
        if not self._ensure_ready():
            return False
        signed_offset = -self._offset if self._flip else self._offset
        self._apply_widget_translation(self._axis, signed_offset)
        return True

    # ---- public state setters (NO immediate align/apply here) ----
    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self._settings.set(SETTING_SECTION_ENABLED, self._enabled)
        self._settings.set(SETTING_SECTION_MANIPULATOR, self._enabled)
        return self.get_state()

    def set_axis(self, axis: str):
        axis = (axis or "").upper()
        if axis not in self.AXES:
            raise ValueError(f"axis must be one of {self.AXES}")
        self._axis = axis
        return self.get_state()

    def set_flip(self, flip: bool):
        self._flip = bool(flip)
        self._settings.set(SETTING_SECTION_DIRECTION, 0 if self._flip else 1)
        return self.get_state()

    def set_offset(self, offset: float):
        self._offset = float(offset)
        return self.get_state()

    def set_all(self, enabled: bool, axis: str, flip: bool, offset: float):
        self.set_enabled(enabled)
        self.set_axis(axis)
        self.set_flip(flip)
        self.set_offset(offset)
        return self.get_state()

    # ---- deferred apply: call from post_update with retries ----
    def try_apply_to_stage_now(self) -> bool:
        """
        Returns True when apply succeeds.
        Returns False when it's not ready yet (no stage/manipulator/widget).
        MUST NOT throw for typical timing failures.
        """
        if not self._enabled:
            return True  # nothing to apply

        if not self._ensure_ready():
            return False

        # Ensure manipulator setting is on
        self._settings.set(SETTING_SECTION_MANIPULATOR, True)

        # align_widget may fail early if manipulator not created yet -> retry next frame
        try:
            self._sec_mgr.align_widget(self._axis.lower())
        except Exception:
            return False

        try:
            self._apply_offset_to_stage()
        except Exception:
            return False

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
# Extension + Dummy UI
# -----------------------------------------------------------------------------
class MyCompanySectionControlExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._ext_id = ext_id
        self._controller = SectionController()

        # ensure dependency extension is enabled (prevents "apply not working")
        try:
            em = omni.kit.app.get_app().get_extension_manager()
            em.set_extension_enabled_immediate("omni.kit.window.section", True)
        except Exception as ex:
            carb.log_warn(f"[section_control] failed to enable omni.kit.window.section: {ex}")

        # stage events
        self._stage_event_sub = None
        try:
            ctx = omni.usd.get_context()
            self._stage_event_sub = ctx.get_stage_event_stream().create_subscription_to_pop(
                self._on_stage_event,
                name="section_control_stage_events",
            )
        except Exception as ex:
            carb.log_warn(f"[section_control] failed to subscribe stage events: {ex}")

        # deferred apply scheduler state
        self._apply_pending = False
        self._apply_retry_left = 0
        self._apply_sub = None

        # guard for axis checkbox mutual exclusion
        self._axis_guard = False

        # web api
        self._api = MessageBusAPI()

        @self._api.request(name="section_get")
        def section_get():
            return self._controller.get_state()

        @self._api.request(name="section_set_enabled")
        def section_set_enabled(enabled: bool):
            st = self._controller.set_enabled(enabled)
            self._sync_models_from_state(st)
            self._schedule_apply(retries=60)
            return st

        @self._api.request(name="section_set_axis")
        def section_set_axis(axis: str):
            st = self._controller.set_axis(axis)
            self._sync_models_from_state(st)
            self._schedule_apply(retries=60)
            return st

        @self._api.request(name="section_set_flip")
        def section_set_flip(flip: bool):
            st = self._controller.set_flip(flip)
            self._sync_models_from_state(st)
            self._schedule_apply(retries=60)
            return st

        @self._api.request(name="section_set_offset")
        def section_set_offset(offset: float):
            st = self._controller.set_offset(offset)
            self._sync_models_from_state(st)
            self._schedule_apply(retries=60)
            return st

        @self._api.request(name="section_set_all")
        def section_set_all(enabled: bool, axis: str, flip: bool, offset: float):
            st = self._controller.set_all(enabled, axis, flip, offset)
            self._sync_models_from_state(st)
            self._schedule_apply(retries=60)
            return st

        # --------------------------
        # UI models
        # --------------------------
        st0 = self._controller.get_state()
        self._m_enabled = ui.SimpleBoolModel(st0["enabled"])
        self._m_flip = ui.SimpleBoolModel(st0["flip"])
        self._m_offset = ui.SimpleFloatModel(st0["offset"])

        # ✅ 축 선택: 버전 호환 최강(체크박스 3개를 라디오처럼)
        axis0 = (st0["axis"] or "X").upper()
        self._m_axis_x = ui.SimpleBoolModel(axis0 == "X")
        self._m_axis_y = ui.SimpleBoolModel(axis0 == "Y")
        self._m_axis_z = ui.SimpleBoolModel(axis0 == "Z")

        # --------------------------
        # UI layout
        # --------------------------
        self._window = ui.Window("Section Control (Dummy UI)", width=460, height=230, visible=False)
        self._controls_vstack = None

        with self._window.frame:
            with ui.VStack(spacing=8, height=0):
                ui.Label("Enable일 때만 조작 UI 표시 / 조작 즉시 반영(다음 프레임 적용)", word_wrap=True)

                with ui.HStack(height=24):
                    ui.CheckBox(model=self._m_enabled)
                    ui.Label("Enable Section", width=0)
                    ui.Spacer()

                self._controls_vstack = ui.VStack(spacing=8, height=0)
                with self._controls_vstack:
                    with ui.HStack(height=24):
                        ui.CheckBox(model=self._m_flip)
                        ui.Label("Flip", width=0)
                        ui.Spacer()

                    with ui.HStack(height=24):
                        ui.Label("Axis", width=60)

                        # X
                        ui.CheckBox(model=self._m_axis_x)
                        ui.Label("X", width=16)
                        ui.Spacer(width=8)

                        # Y
                        ui.CheckBox(model=self._m_axis_y)
                        ui.Label("Y", width=16)
                        ui.Spacer(width=8)

                        # Z
                        ui.CheckBox(model=self._m_axis_z)
                        ui.Label("Z", width=16)

                        ui.Spacer()

                    with ui.HStack(height=24):
                        ui.Label("Offset", width=60)
                        ui.FloatSlider(model=self._m_offset, min=-1000.0, max=1000.0)
                        ui.Spacer(width=8)
                        ui.FloatField(model=self._m_offset, width=90)

        self._set_controls_visible(self._m_enabled.get_value_as_bool())

        # hooks
        self._m_enabled.add_value_changed_fn(lambda m: self._on_enabled_changed())
        self._m_flip.add_value_changed_fn(lambda m: self._apply_from_models())
        self._m_offset.add_value_changed_fn(lambda m: self._apply_from_models())

        # axis mutual exclusion hooks
        self._m_axis_x.add_value_changed_fn(lambda m: self._on_axis_checked("X"))
        self._m_axis_y.add_value_changed_fn(lambda m: self._on_axis_checked("Y"))
        self._m_axis_z.add_value_changed_fn(lambda m: self._on_axis_checked("Z"))

        # show once
        self._post_update_sub = None
        try:
            stream = omni.kit.app.get_app().get_post_update_event_stream()

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
            try:
                self._window.visible = True
            except Exception:
                pass

        carb.log_info("[section_control] started")

        # 기본적으로 축이 하나도 체크 안 된 상태를 방지
        self._ensure_axis_one_selected()

        # enabled면 시작 시 적용 스케줄
        if self._m_enabled.get_value_as_bool():
            self._schedule_apply(retries=60)

    # --------------------------
    # Axis UI helpers
    # --------------------------
    def _ensure_axis_one_selected(self):
        # 어떤 이유로든 모두 False가 되면 X로 강제
        if (not self._m_axis_x.get_value_as_bool()
            and not self._m_axis_y.get_value_as_bool()
            and not self._m_axis_z.get_value_as_bool()):
            self._axis_guard = True
            try:
                self._m_axis_x.set_value(True)
                self._m_axis_y.set_value(False)
                self._m_axis_z.set_value(False)
            finally:
                self._axis_guard = False

    def _axis_from_models(self) -> str:
        # 우선순위는 X/Y/Z
        if self._m_axis_x.get_value_as_bool():
            return "X"
        if self._m_axis_y.get_value_as_bool():
            return "Y"
        if self._m_axis_z.get_value_as_bool():
            return "Z"
        return "X"

    def _on_axis_checked(self, axis: str):
        if self._axis_guard:
            return

        # 체크를 "해제"해서 모두 False가 되는 걸 방지: 항상 하나는 True
        # - 사용자가 체크 해제하려고 클릭한 경우에도 최소 하나 유지
        want = None
        if axis == "X":
            want = self._m_axis_x.get_value_as_bool()
        elif axis == "Y":
            want = self._m_axis_y.get_value_as_bool()
        elif axis == "Z":
            want = self._m_axis_z.get_value_as_bool()

        self._axis_guard = True
        try:
            if want:
                # 선택된 축만 True
                self._m_axis_x.set_value(axis == "X")
                self._m_axis_y.set_value(axis == "Y")
                self._m_axis_z.set_value(axis == "Z")
            else:
                # 사용자가 선택된 축을 꺼버리려는 경우 -> 다시 켜서 유지
                self._ensure_axis_one_selected()
        finally:
            self._axis_guard = False

        # 즉시 반영
        self._apply_from_models()

    # --------------------------
    # Apply scheduler (post_update retry)
    # --------------------------
    def _schedule_apply(self, retries: int = 60):
        self._apply_pending = True
        self._apply_retry_left = max(self._apply_retry_left, int(retries))

        if self._apply_sub is None:
            stream = omni.kit.app.get_app().get_post_update_event_stream()
            self._apply_sub = stream.create_subscription_to_pop(
                self._on_post_update_apply,
                name="section_control_apply_post_update",
            )

    def _on_post_update_apply(self, e):
        if not self._apply_pending:
            return

        ok = False
        try:
            ok = self._controller.try_apply_to_stage_now()
        except Exception:
            ok = False

        if ok:
            self._apply_pending = False
            self._apply_retry_left = 0
            if self._apply_sub:
                try:
                    self._apply_sub.unsubscribe()
                except Exception:
                    pass
                self._apply_sub = None
            return

        self._apply_retry_left -= 1
        if self._apply_retry_left <= 0:
            self._apply_pending = False
            if self._apply_sub:
                try:
                    self._apply_sub.unsubscribe()
                except Exception:
                    pass
                self._apply_sub = None
            carb.log_warn("[section_control] apply timed out (manipulator/widget not ready).")

    # --------------------------
    # UI helpers
    # --------------------------
    def _set_controls_visible(self, visible: bool):
        if self._controls_vstack:
            self._controls_vstack.visible = bool(visible)

    def _sync_models_from_state(self, st: dict):
        try:
            self._m_enabled.set_value(bool(st.get("enabled", False)))
            self._m_flip.set_value(bool(st.get("flip", False)))
            self._m_offset.set_value(float(st.get("offset", 0.0)))

            axis = (st.get("axis") or "X").upper()
            self._axis_guard = True
            try:
                self._m_axis_x.set_value(axis == "X")
                self._m_axis_y.set_value(axis == "Y")
                self._m_axis_z.set_value(axis == "Z")
            finally:
                self._axis_guard = False

            self._ensure_axis_one_selected()
            self._set_controls_visible(self._m_enabled.get_value_as_bool())
        except Exception:
            pass

    def _on_enabled_changed(self):
        enabled = self._m_enabled.get_value_as_bool()
        self._set_controls_visible(enabled)

        st = self._controller.set_enabled(enabled)
        carb.log_info(f"[section_control] enabled changed: {st}")

        if enabled:
            self._ensure_axis_one_selected()
            self._apply_from_models()
        else:
            self._apply_pending = False

    def _apply_from_models(self):
        enabled = self._m_enabled.get_value_as_bool()

        st = self._controller.set_all(
            enabled=enabled,
            axis=self._axis_from_models(),
            flip=self._m_flip.get_value_as_bool(),
            offset=self._m_offset.get_value_as_float(),
        )
        carb.log_info(f"[section_control] state updated: {st}")

        if enabled:
            self._schedule_apply(retries=60)

    # --------------------------
    # Stage events
    # --------------------------
    def _on_stage_event(self, e: carb.events.IEvent):
        try:
            if self._controller:
                self._controller.invalidate_widget_cache()
            self._schedule_apply(retries=120)
        except Exception as ex:
            carb.log_warn(f"[section_control] stage event handling failed: {ex}")

    def on_shutdown(self):
        try:
            if self._post_update_sub:
                self._post_update_sub.unsubscribe()
        except Exception:
            pass
        self._post_update_sub = None

        try:
            if self._apply_sub:
                self._apply_sub.unsubscribe()
        except Exception:
            pass
        self._apply_sub = None

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
