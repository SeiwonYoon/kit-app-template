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
from morph.section_control.extension import get_service

from typing import Dict, Callable, List

from .base_handler import BaseHandler

class SectionControlHandler(BaseHandler):
    """section_control 익스텐션과의 메시지 통신을 처리하는 클래스"""

    def __init__(self):
        self._service = get_service()
        print(f"self._service: {self._service}")
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
        result = self._service.get_state()
        print(f"section_get_state result: {result}")

        self.dispatch_event("section_get_response", result)


    def _on_set_all(self, event: carb.events.IEvent) -> None:
        """ 전체 값 설정 """
        p = event.payload
        enabled = p['enabled']
        axis =    p['axis']
        flip =    p['flip']
        offset =  p['offset']
        result = self._service.set_all(enabled, axis, flip, offset)
        print(f"section_set_all result: {result}")

        self.dispatch_event("section_set_all_response", result)


    def _on_set_enabled(self, event: carb.events.IEvent) -> None:
        """ 부분 설정 - 활성화 여부 """
        p = event.payload
        enabled = p['enabled']
        result = self._service.set_enabled(enabled)
        print(f"section_set_enabled result: {result}")

        self.dispatch_event("section_set_enabled_response", result)


    def _on_set_axis(self, event: carb.events.IEvent) -> None:
        """ 부분 설정 - 축 설정 """
        p = event.payload
        axis = p['axis']
        result = self._service.set_axis(axis)
        print(f"section_set_axis result: {result}")

        self.dispatch_event("section_set_axis_response", result)


    def _on_set_flip(self, event: carb.events.IEvent) -> None:
        """ 부분 설정 - 뒤집기 여부 """
        p = event.payload
        flip = p['flip']
        result = self._service.set_flip(flip)
        print(f"section_set_flip result: {result}")

        self.dispatch_event("section_set_flip_response", result)


    def _on_set_offset(self, event: carb.events.IEvent) -> None:
        """ 부분 설정 - 오프셋 설정 """
        p = event.payload
        offset = p['offset']
        result = self._service.set_offset(offset)
        print(f"section_set_offset result: {result}")

        self.dispatch_event("section_set_offset_response", result)
