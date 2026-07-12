"""HyView T2V/V2T 이벤트명·payload 키 SSOT.

실무에서 이벤트명·필드명이 확정/변경되면 **이 파일을 먼저** 수정한 뒤
``ebs_handler.py``, ``tbs_sim_bridge.py``, ``hyview_debug_http_bridge.py``,
``web/hyview_client/src/hyviewMessaging.ts`` 를 맞춘다.
"""

from __future__ import annotations

from typing import Tuple

# ---------------------------------------------------------------------------
# T2V (web → Kit)
# ---------------------------------------------------------------------------
T2V_REQUEST_EQP_CHANGE = "T2V_request_eqp_change"
T2V_REQUEST_EBS_ENABLE = "T2V_request_ebs_enable"
T2V_REQUEST_START_SIMULATION = "T2V_request_start_simulation"
T2V_REQUEST_CONTROL_SIMULATION = "T2V_request_control_simulation"
T2V_REQUEST_SEEK_SIMULATION = "T2V_request_seek_simulation"

# ---------------------------------------------------------------------------
# V2T (Kit → web)
# ---------------------------------------------------------------------------
V2T_RESPONSE_EQP_CHANGE = "V2T_response_eqp_change"
V2T_RESPONSE_EBS_ENABLE = "V2T_response_ebs_enable"
V2T_RESPONSE_START_SIMULATION = "V2T_response_start_simulation"
V2T_RESPONSE_SIMULATION_TIMELINE = "V2T_response_simulation_timeline"
V2T_RESPONSE_CONTROL_SIMULATION = "V2T_response_control_simulation"
V2T_RESPONSE_SEEK_SIMULATION = "V2T_response_seek_simulation"

# ---------------------------------------------------------------------------
# Payload / response data keys
# ---------------------------------------------------------------------------
PAYLOAD_CASE = "case"
PAYLOAD_T = "t"

DATA_CASE = "case"
DATA_T = "t"
DATA_T_REQUESTED = "t_requested"
DATA_ROW_INDEX = "row_index"

ALL_V2T_EVENT_TYPES: Tuple[str, ...] = (
    V2T_RESPONSE_EQP_CHANGE,
    V2T_RESPONSE_EBS_ENABLE,
    V2T_RESPONSE_START_SIMULATION,
    V2T_RESPONSE_CONTROL_SIMULATION,
    V2T_RESPONSE_SIMULATION_TIMELINE,
    V2T_RESPONSE_SEEK_SIMULATION,
)

__all__ = [
    "ALL_V2T_EVENT_TYPES",
    "DATA_CASE",
    "DATA_ROW_INDEX",
    "DATA_T",
    "DATA_T_REQUESTED",
    "PAYLOAD_CASE",
    "PAYLOAD_T",
    "T2V_REQUEST_CONTROL_SIMULATION",
    "T2V_REQUEST_EBS_ENABLE",
    "T2V_REQUEST_EQP_CHANGE",
    "T2V_REQUEST_SEEK_SIMULATION",
    "T2V_REQUEST_START_SIMULATION",
    "V2T_RESPONSE_CONTROL_SIMULATION",
    "V2T_RESPONSE_EBS_ENABLE",
    "V2T_RESPONSE_EQP_CHANGE",
    "V2T_RESPONSE_SEEK_SIMULATION",
    "V2T_RESPONSE_SIMULATION_TIMELINE",
    "V2T_RESPONSE_START_SIMULATION",
]
