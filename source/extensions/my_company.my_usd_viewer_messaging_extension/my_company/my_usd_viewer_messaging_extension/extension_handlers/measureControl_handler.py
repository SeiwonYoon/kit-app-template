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
from morph.measure_control.extension import get_service

from typing import Dict, Callable, List

from .base_handler import BaseHandler

class MeasureControlHandler(BaseHandler):
    """measure_control 익스텐션과의 메시지 통신을 처리하는 클래스"""

    def __init__(self):
        self._service = get_service()
        print(f"self._service: {self._service}")
        super().__init__()

    def get_outgoing_events(self) -> List[str]:
        """클라이언트로 보낼 이벤트 리스트"""
        return [
            "measure_get_state_response",
            "measure_set_path_response",
        ]

    def get_event_handlers(self) -> Dict[str, Callable]:
        """이벤트 핸들러 맵 반환"""
        return {
            'measure_get_state_request': self._on_get_state,
            'measure_set_path_request': self._on_set_path,
        }

    def _on_get_state(self, event: carb.events.IEvent) -> None:
        """ 현재 상태 요청 처리 """
        result = self._service.get_state()
        print(f"measure_get_state result: {result}")

        self.dispatch_event("measure_get_state_response", result)

    def _on_set_path(self, event: carb.events.IEvent) -> None:
        """ 상태 설정 처리 """
        p = event.payload
        path = p['path']
        result = self._service.measure_mesh_for_prim_path(path)
        print(f"measure_set_path path: {path}, result: {result}")

        self.dispatch_event("measure_set_path_response", {"path": path, "result": result})