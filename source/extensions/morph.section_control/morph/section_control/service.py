# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import carb
import carb.events
import omni.usd
import omni.kit.app

from .core import SectionController, MessageBusAPI, StageEventType


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

        # ✅ 추가: 시작 시 enabled 상태면 바로 apply 루프를 걸어준다
        try:
            if self.controller and self.controller.get_state().get("enabled"):
                self.schedule_apply("startup_enabled", retries=240)
        except Exception:
            pass

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

    # ---------- internal helper ----------
    def _apply_changes(self, enabled: bool, axis: str, flip: bool, offset: float) -> bool:
        """
        ✅ 핵심: '변경된 값만' controller에 반영.
        UI 슬라이더/필드 조작 중에도 axis/flip을 불필요하게 재설정하지 않도록 방지.
        """
        st0 = self.controller.get_state()
        changed = False

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

            # float 미세오차 방지
            if abs(float(offset) - float(st0.get("offset", 0.0))) > 1e-9:
                self.controller.set_offset(offset)
                changed = True

        except Exception:
            # 변경 적용 중 예외가 나면 그래도 apply 루프는 돌려보는 게 낫다
            changed = True

        return changed

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
            changed = self._apply_changes(enabled, axis, flip, offset)
            if changed:
                self.schedule_apply("web_set_all")
            return self.controller.get_state()

    # ---------- UI/common entrypoints ----------
    # UI에서도 이 메서드만 호출하도록 맞추면 됨.
    def set_all_from_ui(self, enabled: bool, axis: str, flip: bool, offset: float, reason: str):
        changed = self._apply_changes(enabled, axis, flip, offset)
        if changed:
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
        except Exception:
            pass

    def _on_stage_event(self, e: carb.events.IEvent):
        etype = int(e.type)
        payload = dict(e.payload.get_dict()) if e.payload else {}

        big = False
        if StageEventType is not None:
            try:
                if etype in (
                    int(StageEventType.OPENED),
                    int(StageEventType.CLOSED),
                    int(StageEventType.NEW_STAGE),  # ✅ 원본 동작 유지: 없으면 예외 -> 아래 except에서 pass
                    int(StageEventType.CLEARED),
                ):
                    big = True
            except Exception:
                # ✅ 원본 동작 유지: enum 멤버 차이로 예외 나도 크래시 없이 넘어감
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

        try:
            ok = self.controller.apply_once_if_possible(self._apply_attempt)
            if ok:
                self._apply_retries_left = 0
        except Exception:
            pass
