"""HyView T2V/V2T 이벤트명·payload 키 SSOT (LAM)."""

from __future__ import annotations

from typing import Tuple

T2V_REQUEST_START_SIMULATION = "T2V_request_start_simulation"
V2T_RESPONSE_START_SIMULATION = "V2T_response_start_simulation"

T2V_REQUEST_STOP_SIMULATION = "T2V_request_stop_simulation"
V2T_RESPONSE_STOP_SIMULATION = "V2T_response_stop_simulation"

# 실시간 제어 (전달된 항목만 해당 화면에 적용)
T2V_CONTROL_SIMULATION = "T2V_control_simulation"
V2T_RESPONSE_CONTROL_SIMULATION = "V2T_response_control_simulation"

PAYLOAD_CONFIGS = "configs"
# case 0 → 화면1, case 1 → 화면2
PAYLOAD_CASE = "case"

# T2V_control_simulation payload 키 (모두 optional — 있는 항목만 적용)
PAYLOAD_PROC_ONLY = "proc_only"            # 공정만보기 (bool)
PAYLOAD_SHOW_TOP_VIEW = "show_top_view"    # 탑뷰보기 (bool)
PAYLOAD_FOUP_INFO_SHOW = "foup_info_show"  # FOUP 상태보기 (bool)
PAYLOAD_EQP_INFO_SHOW = "eqp_info_show"    # 기기정보보기 (bool)
PAYLOAD_WAFER_NUMBER_SHOW = "wafer_number_show"  # 웨이퍼 번호보기 (bool)
PAYLOAD_PRIM_HIDE = "prim_hide"            # prim 숨김 (bool)
PAYLOAD_SPEED = "speed"                    # 재생 배속 (float)

ALL_V2T_EVENT_TYPES: Tuple[str, ...] = (
    V2T_RESPONSE_START_SIMULATION,
    V2T_RESPONSE_STOP_SIMULATION,
    V2T_RESPONSE_CONTROL_SIMULATION,
)

__all__ = [
    "ALL_V2T_EVENT_TYPES",
    "PAYLOAD_CASE",
    "PAYLOAD_CONFIGS",
    "PAYLOAD_PROC_ONLY",
    "PAYLOAD_SHOW_TOP_VIEW",
    "PAYLOAD_FOUP_INFO_SHOW",
    "PAYLOAD_EQP_INFO_SHOW",
    "PAYLOAD_WAFER_NUMBER_SHOW",
    "PAYLOAD_PRIM_HIDE",
    "PAYLOAD_SPEED",
    "T2V_CONTROL_SIMULATION",
    "T2V_REQUEST_START_SIMULATION",
    "T2V_REQUEST_STOP_SIMULATION",
    "V2T_RESPONSE_CONTROL_SIMULATION",
    "V2T_RESPONSE_START_SIMULATION",
    "V2T_RESPONSE_STOP_SIMULATION",
]
