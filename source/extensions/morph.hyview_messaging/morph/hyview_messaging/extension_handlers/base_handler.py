import carb
import carb.events
import omni.kit.app
import omni.kit.livestream.messaging as messaging
from abc import ABC, abstractmethod
from carb.eventdispatcher import get_eventdispatcher
from typing import Callable, Dict, List


class BaseHandler(ABC):
    def __init__(self):
        self._subscriptions = []
        self._handler_name = self.__class__.__name__
        self._register_outgoing_events()
        self._register_incoming_events()

    def _register_outgoing_events(self) -> None:
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
        return []

    @abstractmethod
    def get_event_handlers(self) -> Dict[str, Callable]:
        pass

    def dispatch_event(self, event_name: str, payload: dict = None) -> None:
        if payload is None:
            payload = {}
        get_eventdispatcher().dispatch_event(event_name, payload=payload)
        carb.log_info(f"{self._handler_name} dispatched event '{event_name}'")

    def on_shutdown(self) -> None:
        self._subscriptions.clear()
        carb.log_info(f"{self._handler_name} shutdown complete")
