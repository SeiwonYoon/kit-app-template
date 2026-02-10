# morph/section_control/service.py
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import carb
import carb.events
import omni.usd
import omni.kit.app

from .core import SectionController, MessageBusAPI, _log, _log_exc, StageEventType


class SectionControlService:
    def __init__(self):
        self.controller = SectionController()
        self.api = MessageBusAPI()

        self._stage_event_sub = None
        self._post_update_sub = None

        self._apply_retries_left = 0
        self._apply_attempt = 0

    # ---------- lifecycle ----------
    def startup(self):
        self._subscribe_stage_events()
        self._register_web_api()

    def shutdown(self):
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

    # ---------- web API ----------
    def _register_web_api(self):
        @self.api.request(name="section_get")
        def section_get():
            return self.controller.get_state()

        @self.api.request(name="section_set_enabled")
        def section_set_enabled(enabled: bool):
            st = self.controller.set_enabled(enabled)
            self.schedule_apply("web_set_enabled")
            return st

        @self.api.request(name="section_set_axis")
        def section_set_axis(axis: str):
            st = self.controller.set_axis(axis)
            self.schedule_apply("web_set_axis")
            return st

        @self.api.request(name="section_set_flip")
        def section_set_flip(flip: bool):
            st = self.controller.set_flip(flip)
            self.schedule_apply("web_set_flip")
            return st

        @self.api.request(name="section_set_offset")
        def section_set_offset(offset: float):
            st = self.controller.set_offset(offset)
            self.schedule_apply("web_set_offset")
            return st

        @self.api.request(name="section_set_all")
        def section_set_all(enabled: bool, axis: str, flip: bool, offset: float):
            self.controller.set_enabled(enabled)
            self.controller.set_axis(axis)
            self.controller.set_flip(flip)
            self.controller.set_offset(offset)
            self.schedule_apply("web_set_all")
            return self.controller.get_state()

    # ---------- UI/common entrypoints ----------
    # UI에서도 이 메서드들만 호출하도록 맞추면 됨.
    def set_all_from_ui(self, enabled: bool, axis: str, flip: bool, offset: float, reason: str):
        self.controller.set_enabled(enabled)
        self.controller.set_axis(axis)
        self.controller.set_flip(flip)
        self.controller.set_offset(offset)
        self.schedule_apply(reason)
        return self.controller.get_state()

    # ---------- stage events ----------
    def _subscribe_stage_events(self):
        try:
            ctx = omni.usd.get_context()
            self._stage_event_sub = ctx.get_stage_event_stream().create_subscription_to_pop(
                self._on_stage_event,
                name="section_control_stage_events",
            )
            _log("stage_event subscription OK")
        except Exception as ex:
            _log_exc("failed to subscribe stage events", ex)

    def _on_stage_event(self, e: carb.events.IEvent):
        etype = int(e.type)
        payload = dict(e.payload.get_dict()) if e.payload else {}
        _log(f"stage_event: type={etype}, payload={payload}")

        big = False
        if StageEventType is not None:
            try:
                if etype in (
                    int(StageEventType.OPENED),
                    int(StageEventType.CLOSED),
                    int(StageEventType.NEW_STAGE),
                    int(StageEventType.CLEARED),
                ):
                    big = True
            except Exception:
                pass
        else:
            if etype in (2, 3):
                big = True

        if big:
            self.controller.invalidate("stage_lifecycle_event")
            self.schedule_apply("stage_lifecycle_event")
        else:
            if self.controller.get_state().get("enabled"):
                self.schedule_apply("stage_minor_event_enabled")

    # ---------- apply loop ----------
    def schedule_apply(self, reason: str, retries: int = 240):
        self._apply_retries_left = max(self._apply_retries_left, retries)
        _log(f"schedule_apply retries={self._apply_retries_left} reason={reason}")

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
        _log(f"post_update tick retry_left={self._apply_retries_left}")

        try:
            ok = self.controller.apply_once_if_possible(self._apply_attempt)
            if ok:
                _log("APPLY DONE -> stop loop")
                self._apply_retries_left = 0
        except Exception as ex:
            _log_exc("apply loop exception", ex)
