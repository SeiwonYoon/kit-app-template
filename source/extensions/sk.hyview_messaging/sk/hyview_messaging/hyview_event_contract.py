"""HyView T2V/V2T 이벤트명·payload 키 SSOT (LAM)."""

from __future__ import annotations

from typing import Tuple

T2V_REQUEST_START_SIMULATION = "T2V_request_start_simulation"
V2T_RESPONSE_START_SIMULATION = "V2T_response_start_simulation"

PAYLOAD_CONFIGS = "configs"

ALL_V2T_EVENT_TYPES: Tuple[str, ...] = (V2T_RESPONSE_START_SIMULATION,)

__all__ = [
    "ALL_V2T_EVENT_TYPES",
    "PAYLOAD_CONFIGS",
    "T2V_REQUEST_START_SIMULATION",
    "V2T_RESPONSE_START_SIMULATION",
]
