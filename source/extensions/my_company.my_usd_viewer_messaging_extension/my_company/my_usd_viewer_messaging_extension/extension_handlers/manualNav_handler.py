import carb
import carb.events
from morph.manual_nav.core import get_singleton_service

from typing import Dict, Callable, List

from .base_handler import BaseHandler


class ManualNavHandler(BaseHandler):
    """usd_loader 익스텐션과의 메시지 통신을 처리하는 클래스"""

    def __init__(self):
        self.service = get_singleton_service()
        print(f"self.service: {self.service}")
        super().__init__()

    def get_outgoing_events(self) -> List[str]:
        """클라이언트로 보낼 이벤트 리스트"""
        return [
            "teleport_toggle_response",
            "teleport_get_state_response",
        ]

    def get_event_handlers(self) -> Dict[str, Callable]:
        """이벤트 핸들러 맵 반환"""
        return {
            'teleport_toggle_request': self._on_teleport_toggle,
            'teleport_get_state_request': self._on_teleport_get_state,
        }
    
    def _on_teleport_toggle(self, event: carb.events.IEvent) -> None:
        """토글 요청 처리"""
        p = event.payload
        isOn = p['enabled']

        if(isOn):
            result = self.service.teleport_on()
        else:
            result = self.service.teleport_off()
        
        print(f"teleport_toggle result: {result}")
        self.dispatch_event("teleport_toggle_response", result)

    
    def _on_teleport_get_state(self, event: carb.events.IEvent) -> None:
        """현재 상태 요청 처리"""
        result = self.service.get_state()
        print(f"teleport_get_state result: {result}")
        self.dispatch_event("teleport_get_state_response", result)

