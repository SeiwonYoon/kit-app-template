# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

import carb
import carb.events
import omni.kit.app
import morph.section_control
import omni.kit.livestream.messaging as messaging

from carb.eventdispatcher import get_eventdispatcher

class ExtensionSectionControlManager:
    """Section Control 메시지를 처리하는 클래스"""

    def __init__(self):
        self._subscriptions = []
        outgoing = [
            "section_get_response",
            "section_set_enabled_response",
            "section_set_all_response",
            "section_set_axis_response",
            "section_set_flip_response",
            "section_set_offset_response",
        ]

        for o in outgoing:
            messaging.register_event_type_to_send(o)
            omni.kit.app.register_event_alias(
                carb.events.type_from_string(o),
                o,
            )

        incoming = {
            # request to get children of a prim
            'section_get_request': self._on_get_state,
            'section_set_enabled_request': self._on_set_enabled,
            'section_set_all_request': self._on_set_all,
            'section_set_axis_request': self._on_set_axis,
            'section_set_flip_request': self._on_set_flip,
            'section_set_offset_request': self._on_set_offset,
        }

        # loadUSD 이벤트 등록
        event_type = 'section_get_request'
        omni.kit.app.register_event_alias(
            carb.events.type_from_string(event_type),
            event_type,
        )

        ed = get_eventdispatcher()
        for event_type, handler in incoming.items():
            omni.kit.app.register_event_alias(
                carb.events.type_from_string(event_type),
                event_type,
            )
            self._subscriptions.append(
                ed.observe_event(
                    observer_name=f"SectionController:{event_type}",
                    event_name=event_type,
                    on_event=handler,
                )
            )

    def _payload_to_dict(e: carb.events.IEvent) -> dict:
        try:
            if e is None or e.payload is None:
                return {}
            return dict(e.payload.get_dict())
        except Exception:
            return {}

    def _on_get_state(self, event: carb.events.IEvent) -> None:
        """ ??? """
        service = morph.section_control.get_service()
        result = service._service.get_state()
        print(f"get_state result: {result}")
        get_eventdispatcher().dispatch_event("section_get_response", payload=result)


    def _on_set_all(self, event: carb.events.IEvent) -> None:
        """ ??? """
        p = self._payload_to_dict(event)
        service = morph.section_control.get_service()
        enabled = p.get("enabled", False)
        axis = p.get("axis", 'X')
        flip = p.get("flip", False)
        offset = p.get("offset", 0.0)
        result = service._service.set_all(enabled, axis, flip, offset)
        print(f"set_all result: {result}")
        get_eventdispatcher().dispatch_event("section_set_all_response", payload=result)


    def _on_set_enabled(self, event: carb.events.IEvent) -> None:
        """ ??? """
        p = self._payload_to_dict(event)
        service = morph.section_control.get_service()
        enabled = p.get("enabled", False)
        result = service._service.set_enabled(enabled)
        print(f"set_enabled result: {result}")
        get_eventdispatcher().dispatch_event("section_set_enabled_response", payload=result)


    def _on_set_axis(self, event: carb.events.IEvent) -> None:
        """ ??? """
        p = self._payload_to_dict(event)
        service = morph.section_control.get_service()
        axis = p.get("axis", 'X')
        result = service._service.set_axis(axis)
        print(f"set_axis result: {result}")
        get_eventdispatcher().dispatch_event("section_set_axis_response", payload=result)


    def _on_set_flip(self, event: carb.events.IEvent) -> None:
        """ ??? """
        p = self._payload_to_dict(event)
        service = morph.section_control.get_service()
        flip = p.get("flip", False)
        result = service._service.set_flip(flip)
        print(f"set_flip result: {result}")
        get_eventdispatcher().dispatch_event("section_set_flip_response", payload=result)


    def _on_set_offset(self, event: carb.events.IEvent) -> None:
        """ ??? """
        p = self._payload_to_dict(event)
        service = morph.section_control.get_service()
        offset = p.get("offset", 0.0)
        result = service._service.set_offset(offset)
        print(f"set_offset result: {result}")
        get_eventdispatcher().dispatch_event("section_set_offset_response", payload=result)


    def on_shutdown(self) -> None:
        """Extension이 비활성화될 때 호출되어 상태를 정리합니다."""
        self._subscriptions.clear()
        carb.log_info("ExtensionSectionController shutdown complete")
