"""
LamHandler — HyView livestream 메시징 ↔ LAM Federation 시뮬 API 진입점 (TBS ``EBSHandler`` 패턴).

================================================================================
【payload 키 SSOT — 웹·Kit 합의】
================================================================================

  - 시뮬 시작 요청 키: ``configs``
  - 시뮬 시작 응답 키: ``data.results``  (형식 미정 — 빈 dict 2칸 placeholder)
  - 시뮬 중지 요청 키: ``case``  (0=화면1, 1=화면2)
  - 시뮬 중지 응답 키: ``data.case``  (요청 case echo)
  - 실시간 제어 요청 키: ``case`` + optional(proc_only·show_top_view·foup_info_show·
    eqp_info_show·wafer_number_show·prim_hide·speed) — 있는 항목만 적용
  - 실시간 제어 응답 키: ``data`` = 전달 payload echo
  - STATUS 패널 통지(요청 없음): ``V2T_notify_status_panel`` — ``data.title`` + ``data.rows[]``
  - 실패 code: ``1`` (성공 ``0``)

================================================================================
【이 파일의 역할】
  T2V 수신 → lam_sim_bridge 로 Kit 시뮬 실행 → V2T dispatch

  - **메시징 계층만** 담당: payload 파싱·로그·V2T envelope 조립
  - 실제 Kit 동작(Federation fetch·prerun·재생)은 ``lam_sim_bridge.py`` → ``lam_federation_pipeline``

【비동기 처리】
  bridge ``handle_*`` 는 ``schedule_on_main_thread`` 로 메인(UI) 스레드에 work 를 큐한다.
  메시징 스레드를 block 하지 않으며, V2T 는 work 완료 **콜백**에서 전송한다.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

import carb
import carb.events

from ..hyview_event_contract import (
    PAYLOAD_CASE,
    PAYLOAD_CONFIGS,
    T2V_CONTROL_SIMULATION,
    T2V_REQUEST_START_SIMULATION,
    T2V_REQUEST_STOP_SIMULATION,
    V2T_NOTIFY_STATUS_PANEL,
    V2T_RESPONSE_CONTROL_SIMULATION,
    V2T_RESPONSE_START_SIMULATION,
    V2T_RESPONSE_STOP_SIMULATION,
)
from ..lam_sim_bridge import (
    handle_control_simulation,
    handle_start_simulation,
    handle_stop_simulation,
)
from .base_handler import BaseHandler


class LamHandler(BaseHandler):
    """LAM 시뮬 T2V / V2T 핸들러 (livestream messaging)."""

    def get_outgoing_events(self) -> List[str]:
        """Kit → 웹(V2T) 로 보낼 수 있는 이벤트명 목록 (livestream 등록용)."""
        return [
            V2T_RESPONSE_START_SIMULATION,
            V2T_RESPONSE_STOP_SIMULATION,
            V2T_RESPONSE_CONTROL_SIMULATION,
            V2T_NOTIFY_STATUS_PANEL,
        ]

    def get_event_handlers(self) -> Dict[str, Callable]:
        """웹 → Kit(T2V) 이벤트명 → 핸들러 매핑."""
        return {
            T2V_REQUEST_START_SIMULATION: self._on_req_start_simulation,
            T2V_REQUEST_STOP_SIMULATION: self._on_req_stop_simulation,
            T2V_CONTROL_SIMULATION: self._on_req_control_simulation,
        }

    # ------------------------------------------------------------------
    # V2T 공통 envelope
    # ------------------------------------------------------------------

    def _dispatch_v2t_ok(self, event_name: str, data: Dict[str, Any]) -> None:
        """bridge 성공(code=0) 시 V2T 전송."""
        self.dispatch_event(
            event_name,
            {"code": 0, "message": "success", "data": dict(data)},
        )

    def _dispatch_v2t_err(
        self,
        event_name: str,
        message: str,
        data: Dict[str, Any],
    ) -> None:
        """bridge 실패(code!=0) 시 V2T 전송 — data 필드는 요청 echo 유지."""
        self.dispatch_event(
            event_name,
            {"code": 1, "message": str(message), "data": dict(data)},
        )

    # ------------------------------------------------------------------
    # T2V — 시뮬 시작 (Federation fetch + prerun + 재생)
    # ------------------------------------------------------------------

    def _on_req_start_simulation(self, event: carb.events.IEvent) -> None:
        """
        T2V_request_start_simulation — Federation fetch·prerun·재생.

        요청: ``{"configs": [...]}``
        응답(비동기): ``data.results`` — 형식 미정, 빈 dict 2칸 + success
        """
        # [1] T2V 수신 로그 — 이 줄이 즉시 찍히면 livestream 메시징 수신 OK
        print(f"[LamHandler] _on_req_start_simulation - {event.payload}")

        payload = dict(getattr(event, "payload", None) or {})
        configs = payload.get(PAYLOAD_CONFIGS, [])
        if not isinstance(configs, list):
            configs = []

        # [2] 시뮬레이션 작업 — bridge 가 메인 스레드에서 Federation 파이프라인 실행
        handle_start_simulation(
            payload,
            dispatch=lambda name, body: self._dispatch_start_simulation_response(name, body),
        )
        # V2T 는 파이프라인 완료 후 _dispatch_start_simulation_response 에서 전송 (즉시 return)

    def _dispatch_start_simulation_response(
        self,
        event_name: str,
        bridge_body: Dict[str, Any],
    ) -> None:
        """파이프라인 완료 후 ``data.results`` 로 V2T 전송 (형식 미정 — 빈 결과 echo)."""
        code = int(bridge_body.get("code", 0))
        message = str(bridge_body.get("message", "success"))

        data = bridge_body.get("data")
        if not isinstance(data, dict):
            data = {}
        res_list = data.get("results")
        if not isinstance(res_list, list):
            res_list = []
        results: List[Dict[str, Any]] = [
            dict(res_list[i]) if i < len(res_list) and isinstance(res_list[i], dict) else {}
            for i in range(2)
        ]

        # 실패 — 빈 results 2칸
        if code != 0:
            self._dispatch_v2t_err(event_name, message, {"results": results})
            return

        # 성공 — 응답 형식 미정: 빈 results 2칸 + success
        self._dispatch_v2t_ok(event_name, {"results": results})

    # ------------------------------------------------------------------
    # T2V — 시뮬 중지 (화면별)
    # ------------------------------------------------------------------

    def _on_req_stop_simulation(self, event: carb.events.IEvent) -> None:
        """
        T2V_request_stop_simulation — 화면별 시뮬레이션 중지.

        요청: ``{"case": 0}``  (0=화면1, 1=화면2)
        성공 응답 data: ``{"case": case_index}``
        """
        # [1] T2V 수신 로그 — 이 줄이 즉시 찍히면 livestream 메시징 수신 OK
        print(f"[LamHandler] _on_req_stop_simulation - {event.payload}")

        payload = dict(getattr(event, "payload", None) or {})
        case_index = payload.get(PAYLOAD_CASE, 0)

        # [2] bridge 완료 콜백 — 화면 중지·초기화 끝난 뒤 V2T 전송
        def _on_bridge_done(event_name: str, bridge_body: Dict[str, Any]) -> None:
            code = int(bridge_body.get("code", 0))
            message = str(bridge_body.get("message", "success"))
            data = bridge_body.get("data")
            if not isinstance(data, dict):
                data = {}
            case_echo = data.get(PAYLOAD_CASE, case_index)
            if code != 0:
                self._dispatch_v2t_err(event_name, message, {PAYLOAD_CASE: case_echo})
                return
            self._dispatch_v2t_ok(event_name, {PAYLOAD_CASE: case_echo})

        # [3] Kit 시뮬 중지 위임 — 해당 화면만 정지(초기화), 다른 화면 무영향 (비동기)
        handle_stop_simulation(payload, dispatch=_on_bridge_done)

    # ------------------------------------------------------------------
    # T2V — 실시간 제어 (전달된 항목만 화면별 적용)
    # ------------------------------------------------------------------

    def _on_req_control_simulation(self, event: carb.events.IEvent) -> None:
        """
        T2V_control_simulation — 화면별 오버레이/배속 실시간 제어.

        요청: ``{"case": 0, "proc_only": true, "show_top_view": false,
                 "foup_info_show": true, "eqp_info_show": true,
                 "wafer_number_show": false, "prim_hide": true, "speed": 2.0}``
        (case 외 항목은 모두 optional — 존재하는 항목만 적용)
        응답 data: 전달받은 payload 를 그대로 echo.
        """
        # [1] T2V 수신 로그 — 이 줄이 즉시 찍히면 livestream 메시징 수신 OK
        print(f"[LamHandler] _on_req_control_simulation - {event.payload}")

        payload = dict(getattr(event, "payload", None) or {})

        # [2] bridge 완료 콜백 — 적용 끝난 뒤 전달 내용 echo + success 여부 전송
        def _on_bridge_done(event_name: str, bridge_body: Dict[str, Any]) -> None:
            code = int(bridge_body.get("code", 0))
            message = str(bridge_body.get("message", "success"))
            data = bridge_body.get("data")
            if not isinstance(data, dict):
                data = {}
            if code != 0:
                self._dispatch_v2t_err(event_name, message, data)
                return
            self._dispatch_v2t_ok(event_name, data)

        # [3] Kit 실시간 제어 위임 — 해당 화면 모델만 갱신 (비동기, 메인 스레드)
        handle_control_simulation(payload, dispatch=_on_bridge_done)
