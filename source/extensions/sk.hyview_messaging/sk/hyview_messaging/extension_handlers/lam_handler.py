"""LAM HyView T2V handler — Federation 시뮬 시작."""

from __future__ import annotations

from typing import Any, Callable, Dict

import carb

from ..hyview_event_contract import (
    PAYLOAD_CONFIGS,
    T2V_REQUEST_START_SIMULATION,
    V2T_RESPONSE_START_SIMULATION,
)
from ..lam_sim_bridge import handle_start_simulation
from .base_handler import BaseHandler


class LamHandler(BaseHandler):
    def get_outgoing_events(self):
        return [V2T_RESPONSE_START_SIMULATION]

    def get_event_handlers(self) -> Dict[str, Callable]:
        return {T2V_REQUEST_START_SIMULATION: self._on_req_start_simulation}

    def _on_req_start_simulation(self, event: carb.events.IEvent) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        configs = payload.get(PAYLOAD_CONFIGS, [])
        print(
            f"[LamHandler] {T2V_REQUEST_START_SIMULATION} configs_len={len(configs) if isinstance(configs, list) else 0}",
            flush=True,
        )

        def _dispatch(_name: str, body: Dict[str, Any]) -> None:
            self.dispatch_event(V2T_RESPONSE_START_SIMULATION, body)

        handle_start_simulation(payload, dispatch=_dispatch, event_name=V2T_RESPONSE_START_SIMULATION)
