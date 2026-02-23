# ---------------------------------------------------------------------
# service.py  (깜빡임/반복 열림 방지 + 브라우저(MessageBus) 연동 버전)
# ---------------------------------------------------------------------
import asyncio

import carb
import carb.events
import omni.kit.app
import omni.usd
import omni.kit.livestream.messaging as messaging

from .core import SectionController, MessageBusAPI


class SectionControlService:
    DEBUG_WARMUP_LOG = True
    WARMUP_FRAMES = 5  # 10이면 눈에 띄게 켜져있을 수 있어서 2~5 추천

    # =========================
    # ✅ MessageBus (Browser/WebViewer) 연동 이벤트명
    # =========================
    EVT_SECTION_GET_REQ = "section_get_request"
    EVT_SECTION_GET_RESP = "section_get_response"

    EVT_SECTION_SET_ALL_REQ = "section_set_all_request"
    EVT_SECTION_SET_ALL_RESP = "section_set_all_response"

    # (선택) 부분 제어를 원하면 확장 가능
    EVT_SECTION_SET_ENABLED_REQ = "section_set_enabled_request"
    EVT_SECTION_SET_ENABLED_RESP = "section_set_enabled_response"

    EVT_SECTION_SET_AXIS_REQ = "section_set_axis_request"
    EVT_SECTION_SET_AXIS_RESP = "section_set_axis_response"

    EVT_SECTION_SET_FLIP_REQ = "section_set_flip_request"
    EVT_SECTION_SET_FLIP_RESP = "section_set_flip_response"

    EVT_SECTION_SET_OFFSET_REQ = "section_set_offset_request"
    EVT_SECTION_SET_OFFSET_RESP = "section_set_offset_response"

    def __init__(self):
        self.controller = SectionController()
        self.api = MessageBusAPI()

        self._stage_event_sub = None
        self._post_update_sub = None

        self._apply_retries_left = 0
        self._apply_attempt = 0

        self._ensured_section_backend_once = False

        # warm-up state
        self._warmup_task = None
        self._warmed_once_for_stage_id = None

        # ✅ message bus
        self._bus = None
        self._bus_subs = {}

    # ---------------- lifecycle ----------------
    def startup(self):
        self._subscribe_stage_events()
        self._register_web_api()

        # ✅ Browser/WebViewer → Kit 제어용 MessageBus 구독
        self._subscribe_message_bus_bridge()

        self.ensure_section_backend_running(force=True)

    def shutdown(self):
        # ✅ message bus 정리
        self._unsubscribe_message_bus_bridge()

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
            if self.api:
                self.api.destroy()
        except Exception:
            pass
        self.api = None

        self.controller = None
        self._warmup_task = None

    # ---------------- helpers ----------------
    def _log(self, msg: str):
        if self.DEBUG_WARMUP_LOG:
            carb.log_warn(f"[section_control] {msg}")

    async def _wait_for_frames(self, n: int):
        app = omni.kit.app.get_app()
        for _ in range(max(0, int(n))):
            await app.next_update_async()

    def _get_stage_id(self):
        st = omni.usd.get_context().get_stage()
        return None if st is None else id(st)

    @staticmethod
    def _extract_ext_ids(exts):
        if isinstance(exts, dict):
            return list(exts.keys())
        if isinstance(exts, (list, tuple)):
            out = []
            for item in exts:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, (list, tuple)) and item and isinstance(item[0], str):
                    out.append(item[0])
            return out
        return []

    @staticmethod
    def _payload_to_dict(e: carb.events.IEvent) -> dict:
        try:
            if e is None or e.payload is None:
                return {}
            return dict(e.payload.get_dict())
        except Exception:
            return {}

    def _reply(self, resp_evt_name: str, payload: dict):
        """
        브라우저로 응답 보내기.
        - messaging.register_event_type_to_send(resp_evt_name) 는 startup에서 해둔다.
        """
        if not self._bus:
            return
        try:
            resp_type = carb.events.type_from_string(resp_evt_name)
            self._bus.dispatch(resp_type, payload=payload)
            self._bus.pump()
        except Exception as ex:
            self._log(f"bus reply failed ({resp_evt_name}): {ex}")

    # ---------------- ✅ MessageBus Bridge ----------------
    def _subscribe_message_bus_bridge(self):
        """
        Browser/WebViewer → Kit 확장 제어용 bridge.
        Turntable 예시처럼 get_message_bus_event_stream()에서 이벤트를 직접 수신한다.
        """
        try:
            app = omni.kit.app.get_app()
            self._bus = app.get_message_bus_event_stream()

            # ---- 응답 이벤트 타입 send 등록 ----
            messaging.register_event_type_to_send(self.EVT_SECTION_GET_RESP)
            messaging.register_event_type_to_send(self.EVT_SECTION_SET_ALL_RESP)
            messaging.register_event_type_to_send(self.EVT_SECTION_SET_ENABLED_RESP)
            messaging.register_event_type_to_send(self.EVT_SECTION_SET_AXIS_RESP)
            messaging.register_event_type_to_send(self.EVT_SECTION_SET_FLIP_RESP)
            messaging.register_event_type_to_send(self.EVT_SECTION_SET_OFFSET_RESP)

            # ---- 구독 등록 헬퍼 ----
            def _sub(evt_name: str, cb, sub_name: str):
                evt_type = carb.events.type_from_string(evt_name)
                sub = self._bus.create_subscription_to_pop_by_type(evt_type, cb, name=sub_name)
                self._bus_subs[evt_name] = sub

            # ---- section_get_request ----
            def _on_get(e: carb.events.IEvent):
                p = self._payload_to_dict(e)
                call_id = p.get("id", -1)
                try:
                    st = self.controller.get_state()
                    out = {"id": call_id, "response": st}
                except Exception as ex:
                    out = {"id": call_id, "error": str(ex)}
                if call_id != -1:
                    self._reply(self.EVT_SECTION_GET_RESP, out)

            _sub(self.EVT_SECTION_GET_REQ, _on_get, "section_control_bus_get")

            # ---- section_set_all_request ----
            def _on_set_all(e: carb.events.IEvent):
                p = self._payload_to_dict(e)
                call_id = p.get("id", -1)

                enabled = bool(p.get("enabled", False))
                axis = str(p.get("axis", "X"))
                flip = bool(p.get("flip", False))
                try:
                    offset = float(p.get("offset", 0.0))
                except Exception:
                    offset = 0.0

                try:
                    st = self.set_all_from_ui(enabled, axis, flip, offset, reason="bus_set_all")
                    out = {"id": call_id, "response": st}
                except Exception as ex:
                    out = {"id": call_id, "error": str(ex)}

                if call_id != -1:
                    self._reply(self.EVT_SECTION_SET_ALL_RESP, out)

            _sub(self.EVT_SECTION_SET_ALL_REQ, _on_set_all, "section_control_bus_set_all")

            # ---- (선택) enabled만 ----
            def _on_set_enabled(e: carb.events.IEvent):
                p = self._payload_to_dict(e)
                call_id = p.get("id", -1)
                enabled = bool(p.get("enabled", False))
                st0 = self.controller.get_state()
                try:
                    st = self.set_all_from_ui(
                        enabled=enabled,
                        axis=str(st0.get("axis", "X")),
                        flip=bool(st0.get("flip", False)),
                        offset=float(st0.get("offset", 0.0)),
                        reason="bus_set_enabled",
                    )
                    out = {"id": call_id, "response": st}
                except Exception as ex:
                    out = {"id": call_id, "error": str(ex)}
                if call_id != -1:
                    self._reply(self.EVT_SECTION_SET_ENABLED_RESP, out)

            _sub(self.EVT_SECTION_SET_ENABLED_REQ, _on_set_enabled, "section_control_bus_set_enabled")

            # ---- (선택) axis만 ----
            def _on_set_axis(e: carb.events.IEvent):
                p = self._payload_to_dict(e)
                call_id = p.get("id", -1)
                axis = str(p.get("axis", "X"))
                st0 = self.controller.get_state()
                try:
                    st = self.set_all_from_ui(
                        enabled=bool(st0.get("enabled", False)),
                        axis=axis,
                        flip=bool(st0.get("flip", False)),
                        offset=float(st0.get("offset", 0.0)),
                        reason="bus_set_axis",
                    )
                    out = {"id": call_id, "response": st}
                except Exception as ex:
                    out = {"id": call_id, "error": str(ex)}
                if call_id != -1:
                    self._reply(self.EVT_SECTION_SET_AXIS_RESP, out)

            _sub(self.EVT_SECTION_SET_AXIS_REQ, _on_set_axis, "section_control_bus_set_axis")

            # ---- (선택) flip만 ----
            def _on_set_flip(e: carb.events.IEvent):
                p = self._payload_to_dict(e)
                call_id = p.get("id", -1)
                flip = bool(p.get("flip", False))
                st0 = self.controller.get_state()
                try:
                    st = self.set_all_from_ui(
                        enabled=bool(st0.get("enabled", False)),
                        axis=str(st0.get("axis", "X")),
                        flip=flip,
                        offset=float(st0.get("offset", 0.0)),
                        reason="bus_set_flip",
                    )
                    out = {"id": call_id, "response": st}
                except Exception as ex:
                    out = {"id": call_id, "error": str(ex)}
                if call_id != -1:
                    self._reply(self.EVT_SECTION_SET_FLIP_RESP, out)

            _sub(self.EVT_SECTION_SET_FLIP_REQ, _on_set_flip, "section_control_bus_set_flip")

            # ---- (선택) offset만 ----
            def _on_set_offset(e: carb.events.IEvent):
                p = self._payload_to_dict(e)
                call_id = p.get("id", -1)
                try:
                    offset = float(p.get("offset", 0.0))
                except Exception:
                    offset = 0.0
                st0 = self.controller.get_state()
                try:
                    st = self.set_all_from_ui(
                        enabled=bool(st0.get("enabled", False)),
                        axis=str(st0.get("axis", "X")),
                        flip=bool(st0.get("flip", False)),
                        offset=offset,
                        reason="bus_set_offset",
                    )
                    out = {"id": call_id, "response": st}
                except Exception as ex:
                    out = {"id": call_id, "error": str(ex)}
                if call_id != -1:
                    self._reply(self.EVT_SECTION_SET_OFFSET_RESP, out)

            _sub(self.EVT_SECTION_SET_OFFSET_REQ, _on_set_offset, "section_control_bus_set_offset")

            self._log("message bus bridge: subscribed (get/set_all + optional partial controls)")

        except Exception as ex:
            self._log(f"message bus bridge: subscribe failed: {ex}")

    def _unsubscribe_message_bus_bridge(self):
        for _, s in list(self._bus_subs.items()):
            try:
                s.unsubscribe()
            except Exception:
                pass
        self._bus_subs.clear()
        self._bus = None

    # ---------------- ensure backend ----------------
    def ensure_section_backend_running(self, force: bool = False) -> bool:
        if self._ensured_section_backend_once and not force:
            return True

        try:
            app = omni.kit.app.get_app()
            em = app.get_extension_manager()
            all_exts = self._extract_ext_ids(em.get_extensions())
        except Exception as ex:
            self._log(f"ensure_backend: failed to access extension manager: {ex}")
            return False

        candidates = []
        for ext_id in all_exts:
            if not isinstance(ext_id, str):
                continue
            if ext_id == "omni.kit.window.section":
                candidates.append(ext_id)
            elif "window.section" in ext_id:
                candidates.append(ext_id)
            elif ext_id.endswith(".section") and "omni.kit" in ext_id:
                candidates.append(ext_id)

        candidates = list(dict.fromkeys(candidates))
        candidates.sort(key=lambda x: (x != "omni.kit.window.section", x))

        ok_any = False
        for ext_id in candidates:
            try:
                if hasattr(em, "set_extension_enabled_immediate"):
                    em.set_extension_enabled_immediate(ext_id, True)
                else:
                    em.set_extension_enabled(ext_id, True)
                ok_any = True
                self._log(f"ensure_backend: enabled {ext_id}")
            except Exception as ex:
                self._log(f"ensure_backend: enable failed {ext_id}: {ex}")

        if ok_any:
            self._ensured_section_backend_once = True
        else:
            self._log("ensure_backend: no section extension enabled (candidates empty or failed)")

        return ok_any

    # ---------------- warm-up (enable ON 때만) ----------------
    def warmup_section_window(self, force: bool = False):
        stage_id = self._get_stage_id()

        if stage_id is None:
            self._log("warmup: stage is None (defer warmup)")
            return

        if (not force) and (self._warmed_once_for_stage_id == stage_id):
            return

        if self._warmup_task is not None:
            return

        async def _do():
            try:
                self.ensure_section_backend_running(force=True)

                try:
                    from omni.kit.window.section import get_instance as get_section_instance
                except Exception as ex:
                    self._log(f"warmup: import get_section_instance failed: {ex}")
                    return

                try:
                    inst = get_section_instance()
                except Exception as ex:
                    self._log(f"warmup: get_section_instance() failed: {ex}")
                    return

                self._log("warmup: show_window(True)")
                try:
                    inst.show_window(None, True)
                except Exception as ex:
                    self._log(f"warmup: show_window(True) failed: {ex}")
                    return

                await self._wait_for_frames(self.WARMUP_FRAMES)

                self._log("warmup: show_window(False)")
                try:
                    inst.show_window(None, False)
                except Exception as ex:
                    self._log(f"warmup: show_window(False) failed: {ex}")

                self._warmed_once_for_stage_id = stage_id
                self._log("warmup: done")

            finally:
                self._warmup_task = None

        self._warmup_task = asyncio.ensure_future(_do())

    # ---------------- state apply ----------------
    def _apply_changes(self, enabled: bool, axis: str, flip: bool, offset: float) -> bool:
        st0 = self.controller.get_state()
        changed = False

        # ✅ enable ON 순간에만 warm-up 1회
        if enabled and not bool(st0.get("enabled")):
            self._log("enable toggled ON -> ensure backend + warmup(once)")
            self.ensure_section_backend_running(force=True)
            self.warmup_section_window(force=True)

        try:
            if bool(enabled) != bool(st0.get("enabled")):
                self.controller.set_enabled(enabled)
                changed = True

            if (axis or "").upper() != (st0.get("axis") or "").upper():
                self.controller.set_axis(axis)
                changed = True

            if bool(flip) != bool(st0.get("flip")):
                self.controller.set_flip(flip)
                changed = True

            if abs(float(offset) - float(st0.get("offset", 0.0))) > 1e-9:
                self.controller.set_offset(offset)
                changed = True

        except Exception as ex:
            self._log(f"_apply_changes exception: {ex}")
            changed = True

        return changed

    # ---------------- web api ----------------
    def _register_web_api(self):
        @self.api.request(name="section_get")
        def section_get():
            return self.controller.get_state()

        @self.api.request(name="section_set_all")
        def section_set_all(enabled: bool, axis: str, flip: bool, offset: float):
            changed = self._apply_changes(enabled, axis, flip, offset)
            if changed:
                self.schedule_apply("web_set_all")
            return self.controller.get_state()

    def set_all_from_ui(self, enabled: bool, axis: str, flip: bool, offset: float, reason: str):
        changed = self._apply_changes(enabled, axis, flip, offset)
        if changed:
            self.schedule_apply(reason)
        return self.controller.get_state()

    # ---------------- stage events ----------------
    def _subscribe_stage_events(self):
        try:
            ctx = omni.usd.get_context()
            self._stage_event_sub = ctx.get_stage_event_stream().create_subscription_to_pop(
                self._on_stage_event,
                name="section_control_stage_events",
            )
        except Exception:
            pass

    def _on_stage_event(self, e: carb.events.IEvent):
        # stage 교체 시 다음 enable ON 때 warm-up 다시 하도록 초기화
        self._warmed_once_for_stage_id = None

        if self.controller and self.controller.get_state().get("enabled"):
            # stage 교체 후에도 section이 켜져 있으면 apply만 재시도 (warm-up은 여기서 하지 않음)
            self.schedule_apply("stage_event_enabled")

    # ---------------- apply loop ----------------
    def schedule_apply(self, reason: str, retries: int = 240):
        self._apply_retries_left = max(self._apply_retries_left, retries)

        if self._post_update_sub is None:
            stream = omni.kit.app.get_app().get_post_update_event_stream()
            self._post_update_sub = stream.create_subscription_to_pop(
                self._on_post_update,
                name="section_control_post_update_apply_loop",
            )

    def _on_post_update(self, e):
        if self._apply_retries_left <= 0:
            if self._post_update_sub:
                self._post_update_sub.unsubscribe()
                self._post_update_sub = None
            return

        self._apply_retries_left -= 1
        self._apply_attempt += 1

        # ✅ 여기서는 warm-up 호출 금지 (UI 깜빡임/반복 열림 방지)
        try:
            ok = self.controller.apply_once_if_possible(self._apply_attempt)
            if ok:
                self._log("apply_once_if_possible: OK")
                self._apply_retries_left = 0
            else:
                if self._apply_attempt % 30 == 0:
                    self._log("apply_once_if_possible: still not ready")
        except Exception as ex:
            if self._apply_attempt % 30 == 0:
                self._log(f"apply exception: {ex}")