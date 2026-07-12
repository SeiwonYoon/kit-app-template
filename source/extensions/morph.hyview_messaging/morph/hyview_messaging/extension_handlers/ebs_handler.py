"""

EBSHandler — HyView livestream 메시징 ↔ TBS 시뮬 API 진입점.



================================================================================

【payload 키 SSOT — 웹·Kit·MES 합의】

================================================================================



  - 시뮬 시작 요청 키: ``configs``  (배열 길이 2, case0·case1 settings_snapshot)

  - 시뮬 시작 응답 키: ``data.result``  (프리런 v2 JSON 2개)

  - EBS 이벤트: ``ebs_enable`` only (``ebs_active`` / ``active`` 이벤트명 사용 안 함)

  - 실패 code: ``1`` (성공 ``0``)



■ (deprecated) ``config`` (단수) 로 보내는 구버전 클라이언트:

  - Kit 는 ``configs`` 만 수신. 웹·MES 쪽을 ``configs`` 로 맞출 것.



■ 웹이 응답 ``data.results`` (복수) 를 기대하는 경우:

  1) 본 파일 ``_dispatch_start_simulation_response``

       ``"result": [...]`` → ``"results": [...]``

  2) ``../tbs_sim_bridge.py`` ``handle_start_simulation`` 내부 dispatch

       ``_ok({"result": ...})`` 키를 ``results`` 로 변경



■ (deprecated) ``T2V_request_ebs_active`` 수신이 필요한 경우 — 현재 미지원:

  - ``get_event_handlers()`` 에 alias 추가 또는 ``ebs_enable`` 로 통일



■ 추후 배속 전용 req/res (예: ``T2V_request_sim_speed``):

  - ``get_outgoing_events`` / ``get_event_handlers`` 등록

  - ``../tbs_sim_bridge.py`` 에 ``handle_sim_speed`` 추가



================================================================================



【이 파일의 역할】

  T2V 수신 → tbs_sim_bridge 로 Kit 시뮬 실행 → V2T dispatch



  - **메시징 계층만** 담당: payload 파싱·로그·V2T envelope 조립

  - 실제 Kit 동작(EPVis·프리런·재생)은 ``tbs_sim_bridge.py`` → ``control_window``



【비동기 처리】

  bridge ``handle_*`` 는 ``schedule_on_main_thread`` 로 메인(UI) 스레드에 work 를 큐한다.

  메시징 스레드를 block 하지 않으며, V2T 는 work 완료 **콜백**에서 전송한다.



【case】 case 0 = 화면1 (CASE A), case 1 = 화면2 (CASE B)

"""



import carb

import carb.events

from typing import Any, Callable, Dict, List



from ..tbs_sim_bridge import (

    handle_control_simulation,

    handle_ebs_enable,

    handle_eqp_change,

    handle_seek_simulation,

    handle_start_simulation,

)

from ..hyview_event_contract import (
    PAYLOAD_CASE,
    PAYLOAD_T,
    T2V_REQUEST_SEEK_SIMULATION,
    V2T_RESPONSE_SEEK_SIMULATION,
)
from .base_handler import BaseHandler


# T2V_request_start_simulation configs[n] → V2T slim 응답 sim echo (Kit 시뮬 미사용)
_START_SIM_IDENTITY_KEYS: tuple = ("fab_id", "model_id", "eqp_id")

# V2T_response_simulation_timeline — 화면당 timetable_rows chunk 행 수 (slim string[] 기준)
_TIMELINE_CHUNK_ROWS: int = 20


def _merge_start_identity_into_slim(result_slim: Dict[str, Any], conf: Any) -> None:
    """요청 configs[n] 의 MES 식별 필드를 slim ``sim`` 에 merge."""
    if not isinstance(result_slim, dict) or not isinstance(conf, dict):
        return
    sim = result_slim.setdefault("sim", {})
    if not isinstance(sim, dict):
        return
    sim.update({k: conf.get(k) for k in _START_SIM_IDENTITY_KEYS})


def _slim_timetable_rows(result_slim: Dict[str, Any]) -> List[str]:
    """slim 결과에서 ``timeline.timetable_rows`` (string[]) 추출."""
    tl = result_slim.get("timeline") if isinstance(result_slim, dict) else None
    if not isinstance(tl, dict):
        return []
    rows = tl.get("timetable_rows")
    if not isinstance(rows, list):
        return []
    return [str(r) for r in rows if r is not None and str(r).strip()]


def _clear_slim_timetable_rows(result_slim: Dict[str, Any]) -> None:
    """start_simulation 응답용 — timeline.timetable_rows 를 빈 배열로."""
    if not isinstance(result_slim, dict):
        return
    tl = result_slim.get("timeline")
    if isinstance(tl, dict):
        tl["timetable_rows"] = []
    else:
        result_slim["timeline"] = {"timetable_rows": []}


def _timeline_chunk_count(rows: List[Any], chunk_size: int) -> int:
    if chunk_size <= 0 or not rows:
        return 0
    return (len(rows) + chunk_size - 1) // chunk_size


def _slice_timetable_chunk(rows: List[str], chunk_size: int, offset: int) -> List[str]:
    start = int(offset) * int(chunk_size)
    if start >= len(rows):
        return []
    return list(rows[start : start + int(chunk_size)])


def _build_timeline_chunk_payload(
    rows_by_case: List[List[str]],
    *,
    offset: int,
    end: bool,
    chunk_size: int,
) -> Dict[str, Any]:
    timelines: List[Dict[str, Any]] = []
    for rows in rows_by_case[:2]:
        timelines.append({"timetable_rows": _slice_timetable_chunk(rows, chunk_size, offset)})
    while len(timelines) < 2:
        timelines.append({"timetable_rows": []})
    return {"offset": int(offset), "end": bool(end), "timelines": timelines}




class EBSHandler(BaseHandler):

    """EBS·시뮬 T2V / V2T 핸들러 (livestream messaging)."""



    def get_outgoing_events(self) -> List[str]:

        """Kit → 웹(V2T) 로 보낼 수 있는 이벤트명 목록 (livestream 등록용)."""

        return [

            "V2T_response_eqp_change",

            "V2T_response_ebs_enable",

            "V2T_response_start_simulation",

            "V2T_response_simulation_timeline",

            "V2T_response_control_simulation",

            V2T_RESPONSE_SEEK_SIMULATION,

        ]



    def get_event_handlers(self) -> Dict[str, Callable]:

        """웹 → Kit(T2V) 이벤트명 → 핸들러 매핑."""

        return {

            "T2V_request_eqp_change": self._on_req_eqp_change,

            "T2V_request_ebs_enable": self._on_req_ebs_enable,

            "T2V_request_start_simulation": self._on_req_start_simulation,

            "T2V_request_control_simulation": self._on_req_control_simulation,

            T2V_REQUEST_SEEK_SIMULATION: self._on_req_seek_simulation,

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



    def _dispatch_bridge_result(

        self,

        event_name: str,

        bridge_res: Dict[str, Any],

        *,

        ok_data: Dict[str, Any],

        err_data: Dict[str, Any],

    ) -> None:

        """``tbs_sim_bridge`` 완료 콜백 — code 에 따라 성공/실패 V2T 조립."""

        if int(bridge_res.get("code", 0)) != 0:

            self._dispatch_v2t_err(

                event_name,

                str(bridge_res.get("message", "failed")),

                err_data,

            )

            return

        self._dispatch_v2t_ok(event_name, ok_data)



    def _dispatch_simulation_timeline_chunks(self, rows_by_case: List[List[str]]) -> None:
        """slim timetable_rows 를 chunk 단위로 ``V2T_response_simulation_timeline`` 전송."""
        chunk_size = max(1, int(_TIMELINE_CHUNK_ROWS))
        cases = [
            list(rows_by_case[0]) if len(rows_by_case) > 0 else [],
            list(rows_by_case[1]) if len(rows_by_case) > 1 else [],
        ]
        num_chunks = max(
            _timeline_chunk_count(cases[0], chunk_size),
            _timeline_chunk_count(cases[1], chunk_size),
        )
        if num_chunks <= 0:
            num_chunks = 1

        for offset in range(num_chunks):
            end = offset >= num_chunks - 1
            try:
                data = _build_timeline_chunk_payload(
                    cases,
                    offset=offset,
                    end=end,
                    chunk_size=chunk_size,
                )
                self.dispatch_event(
                    "V2T_response_simulation_timeline",
                    {"code": 0, "message": "success", "data": data},
                )
            except Exception as exc:
                self.dispatch_event(
                    "V2T_response_simulation_timeline",
                    {
                        "code": 1,
                        "message": str(exc),
                        "data": {
                            "offset": int(offset),
                            "end": True,
                            "timelines": [{"timetable_rows": []}, {"timetable_rows": []}],
                        },
                    },
                )
                return



    # ------------------------------------------------------------------

    # T2V — EP 포트 개수 변경

    # ------------------------------------------------------------------



    def _on_req_eqp_change(self, event: carb.events.IEvent) -> None:

        """

        T2V_request_eqp_change — 화면별 EP 포트 개수 변경.



        요청: ``{"case": 0, "eqp_id": "SPW1102", "ep_count": 2}`` (eqp_id 무시)

        성공 응답 data: ``{"case": case_index, "ep_count": ep_count}``

        """

        # [1] T2V 수신 로그 — 이 줄이 즉시 찍히면 livestream 메시징 수신 OK

        print(f"[EBSHandler] _on_req_eqp_change - {event.payload}")



        # [2] 요청 필드 추출 (V2T echo·bridge 전달용)

        case_index = event.payload["case"]

        ep_count = event.payload["ep_count"]



        # [3] bridge 완료 콜백 — 메인 스레드 work 끝난 뒤 V2T 전송

        def _on_bridge_done(bridge_res: Dict[str, Any]) -> None:

            # TODO: 설정 완료 후 웹 UI 갱신 등 (필요 시)

            self._dispatch_bridge_result(

                "V2T_response_eqp_change",

                bridge_res,

                ok_data={"case": case_index, "ep_count": ep_count},

                err_data={"case": case_index, "ep_count": ep_count},

            )



        # [4] Kit 시뮬 실행 위임 — EP 콤보·EPVis·포트 레이아웃 (비동기)

        # TODO: EBS 작업 실행 (eqp_id 기반 분기 필요 시 bridge 쪽 확장)

        handle_eqp_change(event.payload, dispatch=_on_bridge_done)



    # ------------------------------------------------------------------

    # T2V — EBS 적용 on/off

    # ------------------------------------------------------------------



    def _on_req_ebs_enable(self, event: carb.events.IEvent) -> None:

        """

        T2V_request_ebs_enable — 화면별 EBS 적용 여부.



        요청: ``{"case": 0, "ebs_enable": true}``

        성공/실패 data echo: ``case``, ``ebs_enable``

        """

        print(f"[EBSHandler] _on_req_ebs_enable - {event.payload}")



        case_index = event.payload["case"]

        ebs_enable = event.payload["ebs_enable"]



        def _on_bridge_done(bridge_res: Dict[str, Any]) -> None:

            # TODO: 설정 완료 후 호출 (웹 체크박스·모델링 동기화)

            self._dispatch_bridge_result(

                "V2T_response_ebs_enable",

                bridge_res,

                ok_data={"case": case_index, "ebs_enable": ebs_enable},

                err_data={"case": case_index, "ebs_enable": ebs_enable},

            )



        # TODO: EBS 작업 실행

        handle_ebs_enable(event.payload, dispatch=_on_bridge_done)



    # ------------------------------------------------------------------

    # T2V — 시뮬 시작 (2화면 프리런)

    # ------------------------------------------------------------------



    def _on_req_start_simulation(self, event: carb.events.IEvent) -> None:

        """

        T2V_request_start_simulation — 2화면 동시 프리런·시작.



        요청: ``{"configs": [{settings_snapshot}, {settings_snapshot}]}``

        응답(비동기): ``data.result``: [case0 프리런 v2 JSON, case1 프리런 v2 JSON]

        """

        print(f"[EBSHandler] _on_req_start_simulation - {event.payload}")

        start_configs = event.payload.get("configs", [])
        if not isinstance(start_configs, list):
            start_configs = []

        # 프리런 결과를 콜백에서 채울 버퍼 (현재는 dispatch 시 bridge data 직접 사용)

        result0: Dict[str, Any] = {}

        result1: Dict[str, Any] = {}



        # TODO: 시뮬레이션 작업 — bridge 가 configs 적용 → on_sim_start_clicked → 프리런 대기

        handle_start_simulation(

            event.payload,

            dispatch=lambda name, body: self._dispatch_start_simulation_response(

                name, body, result0, result1, start_configs

            ),

        )

        # V2T 는 프리런 완료 후 _dispatch_start_simulation_response 에서 전송 (즉시 return)



    def _dispatch_start_simulation_response(

        self,

        event_name: str,

        bridge_body: Dict[str, Any],

        result0: Dict[str, Any],

        result1: Dict[str, Any],

        start_configs: List[Any],

    ) -> None:

        """프리런 완료 후 ``data.results`` 로 V2T 전송."""

        code = int(bridge_body.get("code", 0))

        message = str(bridge_body.get("message", "success"))



        # 실패 — 빈 results 2칸

        if code != 0:

            self.dispatch_event(

                event_name,

                {"code": 1, "message": message, "data": {"results": [{}, {}]}},

            )

            return



        # 성공 — bridge data.results → V2T data.results

        data = bridge_body.get("data")

        if not isinstance(data, dict):

            data = {}

        res_list = data.get("results")

        if not isinstance(res_list, list):

            res_list = []

        if len(res_list) > 0 and isinstance(res_list[0], dict):

            result0.clear()

            result0.update(res_list[0])

        if len(res_list) > 1 and isinstance(res_list[1], dict):

            result1.clear()

            result1.update(res_list[1])

        # Web payload slimming: 원본(result0/1)은 유지하되 전송 직전에만 slim 변환을 적용.
        # (Kit 내부 SSOT/디스크 저장/재생 로직에는 영향 없음)
        try:
            from morph.tbs_control_2.control_sim_bar_graph import (
                build_prerun_export_document_web_slim,
            )

            result0_slim = (
                build_prerun_export_document_web_slim(dict(result0))
                if isinstance(result0, dict) and result0
                else {}
            )
            result1_slim = (
                build_prerun_export_document_web_slim(dict(result1))
                if isinstance(result1, dict) and result1
                else {}
            )
        except Exception:
            # 슬림 변환 실패 시 원본 전송(기능 보존)
            result0_slim = dict(result0)
            result1_slim = dict(result1)

        # MES 식별 필드 echo — T2V configs → V2T slim sim (시뮬 엔진·Kit 내부 SSOT 무영향)
        if len(start_configs) > 0:
            _merge_start_identity_into_slim(result0_slim, start_configs[0])
        if len(start_configs) > 1:
            _merge_start_identity_into_slim(result1_slim, start_configs[1])

        # timetable_rows 는 start 응답에서 비우고, chunk 이벤트로 분할 전송
        rows0 = _slim_timetable_rows(result0_slim)
        rows1 = _slim_timetable_rows(result1_slim)
        _clear_slim_timetable_rows(result0_slim)
        _clear_slim_timetable_rows(result1_slim)

        self.dispatch_event(

            event_name,

            {

                "code": 0,

                "message": "success",

                "data": {"results": [result0_slim, result1_slim]},

            },

        )

        self._dispatch_simulation_timeline_chunks([rows0, rows1])



    # ------------------------------------------------------------------

    # T2V — 재생 / 일시정지 / 배속

    # ------------------------------------------------------------------



    def _on_req_control_simulation(self, event: carb.events.IEvent) -> None:

        """

        T2V_request_control_simulation — play / pause / 배속.



        요청: ``{"action": "play"|"pause", "speed": 2.0}`` (speed 생략 시 Kit 1.0)

        응답 data: ``{"active": "...", "speed": <현재 Kit 배속>}``

        """

        print(f"[EBSHandler] _on_req_control_simulation - {event.payload}")



        action = event.payload["action"]

        speed = event.payload.get("speed", 1.0)



        def _on_bridge_done(bridge_res: Dict[str, Any]) -> None:

            if int(bridge_res.get("code", 0)) != 0:

                self._dispatch_v2t_err(

                    "V2T_response_control_simulation",

                    str(bridge_res.get("message", "failed")),

                    {"active": "", "speed": 1.0},

                )

                return

            res_data = bridge_res.get("data")

            if not isinstance(res_data, dict):

                res_data = {}

            self._dispatch_v2t_ok(

                "V2T_response_control_simulation",

                {

                    "active": res_data.get("active", action),

                    "speed": res_data.get("speed", speed),

                },

            )



        # TODO: 시뮬레이션 제어 (play / pause / speed)

        handle_control_simulation(event.payload, dispatch=_on_bridge_done)


    def _on_req_seek_simulation(self, event: carb.events.IEvent) -> None:

        """

        T2V_request_seek_simulation — 막대그래프 시간축 클릭과 동일 seek.

        요청: ``{"case": 0|1, "t": <sim_seconds>}``

        응답 data: ``{"case", "t", "t_requested", "row_index"}``

        """

        print(f"[EBSHandler] _on_req_seek_simulation - {event.payload}")

        pl = event.payload if isinstance(event.payload, dict) else {}
        case_index = pl.get(PAYLOAD_CASE, 0)
        t_req = pl.get(PAYLOAD_T)

        def _err_data() -> Dict[str, Any]:
            return {
                PAYLOAD_CASE: case_index,
                PAYLOAD_T: t_req,
                "row_index": None,
            }

        def _on_bridge_done(bridge_res: Dict[str, Any]) -> None:

            if int(bridge_res.get("code", 0)) != 0:

                self._dispatch_v2t_err(

                    V2T_RESPONSE_SEEK_SIMULATION,

                    str(bridge_res.get("message", "failed")),

                    _err_data(),

                )

                return

            res_data = bridge_res.get("data")

            if not isinstance(res_data, dict):

                res_data = {}

            self._dispatch_v2t_ok(V2T_RESPONSE_SEEK_SIMULATION, res_data)

        handle_seek_simulation(event.payload, dispatch=_on_bridge_done)


