"""
EBSHandler — HyView livestream 메시징 ↔ TBS 시뮬 API 진입점.

================================================================================
【실무 payload 키가 아래 SSOT 와 다를 때 — 수정해야 할 파일·위치】
================================================================================

현재 SSOT (웹·Kit 합의):
  - 시뮬 시작 요청 키: ``config``  (배열 길이 2)
  - 시뮬 시작 응답 키: ``data.result``
  - EBS 이벤트: ``ebs_enable`` only (``ebs_active`` / ``active`` 이벤트명 사용 안 함)
  - 실패 code: ``1`` (성공 ``0``)

■ 웹·MES 가 ``configs`` (복수) 로 보내는 경우:
  1) 본 파일 ``_on_req_start_simulation``
       ``config = event.payload["config"]``
       → ``config = event.payload["configs"]``
  2) ``../tbs_sim_bridge.py`` 함수 ``handle_start_simulation``
       ``raw_config = pl.get("config")``
       → ``pl.get("configs")`` 또는 ``pl.get("config") or pl.get("configs")``

■ 웹이 응답 ``data.results`` (복수) 를 기대하는 경우:
  1) 본 파일 ``_dispatch_start_simulation_response``
       ``"result": [dict(result0), dict(result1)]``
       → ``"results": [...]``
  2) ``../tbs_sim_bridge.py`` ``handle_start_simulation`` 내부 dispatch
       ``_ok({"result": ...})`` / ``_err(..., data={"result": ...})``
       → 키 이름을 ``results`` 로 동일 변경

■ (deprecated) ``T2V_request_ebs_active`` 수신이 필요한 경우 — 현재 미지원:
  - 본 파일 ``get_event_handlers()`` 에 alias 추가
  - 이벤트명은 ``ebs_enable`` 로 통일하는 것을 권장

■ 추후 배속 전용 req/res 이벤트 추가 시 (예: ``T2V_request_sim_speed``):
  - ``get_outgoing_events`` / ``get_event_handlers`` 에 이벤트 등록
  - ``../tbs_sim_bridge.py`` 에 ``handle_sim_speed`` 추가
  - ``control_simulation`` 의 speed 필드는 play 부가 옵션으로 유지 가능

================================================================================

【이 파일의 역할】
  T2V 수신 → tbs_sim_bridge 로 Kit 시뮬 실행 → V2T dispatch

【case】 case 0 = 화면1 (CASE A), case 1 = 화면2 (CASE B)
"""

import carb
import carb.events
from typing import Any, Callable, Dict, List

from ..tbs_sim_bridge import (
    handle_control_simulation,
    handle_ebs_enable,
    handle_eqp_change,
    handle_start_simulation,
)
from .base_handler import BaseHandler


class EBSHandler(BaseHandler):
    """EBS·시뮬 T2V / V2T 핸들러 (livestream messaging)."""

    def get_outgoing_events(self) -> List[str]:
        return [
            "V2T_response_eqp_change",
            "V2T_response_ebs_enable",
            "V2T_response_start_simulation",
            "V2T_response_control_simulation",
        ]

    def get_event_handlers(self) -> Dict[str, Callable]:
        return {
            "T2V_request_eqp_change": self._on_req_eqp_change,
            "T2V_request_ebs_enable": self._on_req_ebs_enable,
            "T2V_request_start_simulation": self._on_req_start_simulation,
            "T2V_request_control_simulation": self._on_req_control_simulation,
        }

    def _on_req_eqp_change(self, event: carb.events.IEvent) -> None:
        """
        T2V_request_eqp_change — 화면별 EP 포트 개수 변경.

        요청: ``{"case": 0, "eqp_id": "SPW1102", "ep_count": 2}`` (eqp_id 무시)
        성공 응답 data: ``{"case": case_index, "ep_count": ep_count}``
        실패 응답 code ``1``, data 동일 echo
        """
        print(f"[EBSHandler] _on_req_eqp_change - {event.payload}")
        case_index = event.payload["case"]
        ep_count = event.payload["ep_count"]

        # TODO: EBS작업실행
        bridge_res = handle_eqp_change(event.payload)

        # TODO: 설정 완료후 호출(ep 개수에 따른 모델링 변경)
        if int(bridge_res.get("code", 0)) != 0:
            self.dispatch_event(
                "V2T_response_eqp_change",
                {
                    "code": 1,
                    "message": str(bridge_res.get("message", "failed")),
                    "data": {"case": case_index, "ep_count": ep_count},
                },
            )
            return
        self.dispatch_event(
            "V2T_response_eqp_change",
            {
                "code": 0,
                "message": "success",
                "data": {"case": case_index, "ep_count": ep_count},
            },
        )

    def _on_req_ebs_enable(self, event: carb.events.IEvent) -> None:
        """
        T2V_request_ebs_enable — 화면별 EBS 적용 여부.

        요청: ``{"case": 0, "ebs_enable": true}``
        성공/실패 data echo: ``case``, ``ebs_enable``
        """
        print(f"[EBSHandler] _on_req_ebs_enable - {event.payload}")
        case_index = event.payload["case"]
        ebs_enable = event.payload["ebs_enable"]

        # TODO: EBS작업실행
        bridge_res = handle_ebs_enable(event.payload)

        # TODO: 설정 완료후 호출
        if int(bridge_res.get("code", 0)) != 0:
            self.dispatch_event(
                "V2T_response_ebs_enable",
                {
                    "code": 1,
                    "message": str(bridge_res.get("message", "failed")),
                    "data": {"case": case_index, "ebs_enable": ebs_enable},
                },
            )
            return
        self.dispatch_event(
            "V2T_response_ebs_enable",
            {
                "code": 0,
                "message": "success",
                "data": {"case": case_index, "ebs_enable": ebs_enable},
            },
        )

    def _on_req_start_simulation(self, event: carb.events.IEvent) -> None:
        """
        T2V_request_start_simulation — 2화면 동시 프리런·시작.

        요청: ``{"config": [{settings_snapshot}, {settings_snapshot}]}``
        응답(비동기): ``data.result``: [case0 프리런 v2 JSON, case1 프리런 v2 JSON]
        실패 code ``1``, ``result``: ``[{}, {}]``
        """
        print(f"[EBSHandler] _on_req_start_simulation - {event.payload}")
        config = event.payload["config"]

        result0: Dict[str, Any] = {}
        result1: Dict[str, Any] = {}

        # TODO: 시뮬레이션 작업
        handle_start_simulation(
            event.payload,
            dispatch=lambda name, body: self._dispatch_start_simulation_response(
                name, body, result0, result1
            ),
        )

        # TODO: 설정 완료후 호출
        # → _dispatch_start_simulation_response 가 프리런 완료 후 dispatch

    def _dispatch_start_simulation_response(
        self,
        event_name: str,
        bridge_body: Dict[str, Any],
        result0: Dict[str, Any],
        result1: Dict[str, Any],
    ) -> None:
        """프리런 완료 후 ``data.result`` 로 V2T 전송."""
        code = int(bridge_body.get("code", 0))
        message = str(bridge_body.get("message", "success"))

        if code != 0:
            self.dispatch_event(
                event_name,
                {
                    "code": 1,
                    "message": message,
                    "data": {"result": [{}, {}]},
                },
            )
            return

        data = bridge_body.get("data")
        if not isinstance(data, dict):
            data = {}
        res_list = data.get("result")
        if not isinstance(res_list, list):
            res_list = []
        if len(res_list) > 0 and isinstance(res_list[0], dict):
            result0.clear()
            result0.update(res_list[0])
        if len(res_list) > 1 and isinstance(res_list[1], dict):
            result1.clear()
            result1.update(res_list[1])

        self.dispatch_event(
            event_name,
            {
                "code": 0,
                "message": "success",
                "data": {"result": [dict(result0), dict(result1)]},
            },
        )

    def _on_req_control_simulation(self, event: carb.events.IEvent) -> None:
        """
        T2V_request_control_simulation — play / pause / 배속.

        요청: ``{"action": "play"|"pause", "speed": 2.0}`` (speed 생략 시 Kit 에 1.0 적용)
        응답 data: ``{"active": "...", "speed": <현재 Kit 배속>}``
        실패 code ``1``, ``active`` 빈 문자열, ``speed`` 1.0
        """
        print(f"[EBSHandler] _on_req_control_simulation - {event.payload}")
        action = event.payload["action"]
        speed = event.payload.get("speed", 1.0)

        # TODO: 시뮬레이션 제어 (play / pause / speed)
        bridge_res = handle_control_simulation(event.payload)

        # TODO: 설정 완료후 호출
        if int(bridge_res.get("code", 0)) != 0:
            self.dispatch_event(
                "V2T_response_control_simulation",
                {
                    "code": 1,
                    "message": str(bridge_res.get("message", "failed")),
                    "data": {"active": "", "speed": 1.0},
                },
            )
            return
        res_data = bridge_res.get("data")
        if not isinstance(res_data, dict):
            res_data = {}
        self.dispatch_event(
            "V2T_response_control_simulation",
            {
                "code": 0,
                "message": "success",
                "data": {
                    "active": res_data.get("active", action),
                    "speed": res_data.get("speed", speed),
                },
            },
        )
