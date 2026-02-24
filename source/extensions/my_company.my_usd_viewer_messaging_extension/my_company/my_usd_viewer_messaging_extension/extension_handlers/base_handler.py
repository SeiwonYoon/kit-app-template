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
import omni.kit.livestream.messaging as messaging
from carb.eventdispatcher import get_eventdispatcher
from abc import ABC, abstractmethod
from typing import Dict, Callable, List


class BaseHandler(ABC):
    """모든 익스텐션 핸들러의 기본 클래스"""

    def __init__(self):
        self._subscriptions = []
        self._handler_name = self.__class__.__name__
        self._register_outgoing_events()
        self._register_incoming_events()

    def _register_outgoing_events(self) -> None:
        """outgoing 이벤트를 자동으로 등록"""
        outgoing = self.get_outgoing_events()
        
        for event_type in outgoing:
            messaging.register_event_type_to_send(event_type)
            omni.kit.app.register_event_alias(
                carb.events.type_from_string(event_type),
                event_type,
            )
        
        if outgoing:
            carb.log_info(f"{self._handler_name} registered {len(outgoing)} outgoing events")

    def _register_incoming_events(self) -> None:
        """incoming 이벤트를 자동으로 등록"""
        incoming = self.get_event_handlers()

        ed = get_eventdispatcher()
        for event_type, handler in incoming.items():
            omni.kit.app.register_event_alias(
                carb.events.type_from_string(event_type),
                event_type,
            )
            self._subscriptions.append(
                ed.observe_event(
                    observer_name=f"{self._handler_name}:{event_type}",
                    event_name=event_type,
                    on_event=handler,
                )
            )

        if incoming:
            carb.log_info(f"{self._handler_name} registered {len(incoming)} incoming events")

    def get_outgoing_events(self) -> List[str]:
        """
        서브클래스에서 outgoing 이벤트 리스트를 반환 (선택사항)

        Returns:
            List[str]: 클라이언트로 보낼 이벤트 이름 리스트
        """
        return []

    @abstractmethod
    def get_event_handlers(self) -> Dict[str, Callable]:
        """
        서브클래스에서 incoming 이벤트 핸들러 맵을 반환

        Returns:
            Dict[str, Callable]: {'eventName': handler_method} 형태의 딕셔너리
        """
        pass

    def dispatch_event(self, event_name: str, payload: dict = None) -> None:
        """
        클라이언트로 이벤트를 전송하는 헬퍼 메서드

        Args:
            event_name: 전송할 이벤트 이름
            payload: 전송할 데이터 (dict)
        """
        if payload is None:
            payload = {}
        get_eventdispatcher().dispatch_event(event_name, payload=payload)
        carb.log_info(f"{self._handler_name} dispatched event '{event_name}'")

    def on_shutdown(self) -> None:
        """정리 작업"""
        self._subscriptions.clear()
        carb.log_info(f"{self._handler_name} shutdown complete")
