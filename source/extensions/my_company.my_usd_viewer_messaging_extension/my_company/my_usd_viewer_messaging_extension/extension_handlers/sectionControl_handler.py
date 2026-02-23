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
import my_company.usd_loader
from morph.section_control.extension import get_service

from typing import Dict, Callable, List

from .base_handler import BaseHandler

class SectionControlHandler(BaseHandler):
    """section_control 익스텐션과의 메시지 통신을 처리하는 클래스"""

    def __init__(self):
        self._usd_loader = my_company.usd_loader.get_instance()
        print(f"self._usd_loader: {self._usd_loader}")
        super().__init__()

    def get_outgoing_events(self) -> List[str]:
        """클라이언트로 보낼 이벤트 리스트"""
        return [
            "section_get_response",
            "section_set_enabled_response",
            "section_set_all_response",
            "section_set_axis_response",
            "section_set_flip_response",
            "section_set_offset_response",
        ]

    def get_event_handlers(self) -> Dict[str, Callable]:
        """이벤트 핸들러 맵 반환"""
        return {
            'section_get_request': self._on_get_state,
            'section_set_enabled_request': self._on_set_enabled,
            'section_set_all_request': self._on_set_all,
            'section_set_axis_request': self._on_set_axis,
            'section_set_flip_request': self._on_set_flip,
            'section_set_offset_request': self._on_set_offset,
        }

    def _on_get_state(self, event: carb.events.IEvent) -> None:
        """ 현재 상태 요청 처리 """
        service = get_service()
        result = service.get_state()
        print(f"get_state result: {result}")

        self.dispatch_event("section_get_response", result)


    def _on_set_all(self, event: carb.events.IEvent) -> None:
        """ 전체 값 설정 """
        p = event.payload
        service = get_service()
        enabled = p['enabled']
        axis =    p['axis']
        flip =    p['flip']
        offset =  p['offset']
        print(f"_on_set_all payload: enabled={enabled}, axis={axis}, flip={flip}, offset={offset}")
        result = service.set_all(enabled, axis, flip, offset)
        print(f"set_all result: {result}")

        self.dispatch_event("section_set_all_response", result)


    def _on_set_enabled(self, event: carb.events.IEvent) -> None:
        """ 부분 설정 - 활성화 여부 """
        p = event.payload
        service = get_service()
        enabled = p['enabled']
        result = service.set_enabled(enabled)
        print(f"set_enabled result: {result}")

        self.dispatch_event("section_set_enabled_response", result)


    def _on_set_axis(self, event: carb.events.IEvent) -> None:
        """ 부분 설정 - 축 설정 """
        p = event.payload
        service = get_service()
        axis = p['axis']
        result = service.set_axis(axis)
        print(f"set_axis result: {result}")

        self.dispatch_event("section_set_axis_response", result)


    def _on_set_flip(self, event: carb.events.IEvent) -> None:
        """ 부분 설정 - 뒤집기 여부 """
        p = event.payload
        service = get_service()
        flip = p['flip']
        result = service.set_flip(flip)
        print(f"set_flip result: {result}")

        self.dispatch_event("section_set_flip_response", result)


    def _on_set_offset(self, event: carb.events.IEvent) -> None:
        """ 부분 설정 - 오프셋 설정 """
        p = event.payload
        service = get_service()
        offset = p['offset']
        result = service.set_offset(offset)
        print(f"set_offset result: {result}")

        self.dispatch_event("section_set_offset_response", result)
