"""
LAM HyView 메시징 ↔ Kit Federation 시뮬 브리지 (TBS ``tbs_sim_bridge`` 패턴).

【이 파일의 역할】
  T2V payload → Kit 시뮬 실행(Federation fetch·prerun·재생) → bridge 결과 콜백

  - **Kit 실행 계층만** 담당: 메인 스레드 마샬링·파이프라인 실행·결과 코드 조립
  - V2T envelope(``code``/``message``/``data``) 최종 조립은 ``lam_handler`` 쪽

【비동기 처리】
  ``handle_*`` 는 ``schedule_on_main_thread`` 로 메인(UI) 스레드에 work 를 큐한다.
  메시징 스레드를 block 하지 않으며, dispatch 는 work 완료 **콜백**에서 호출한다.

【응답 형식】
  웹 응답 형식 미정 — 성공 시 빈 ``results`` 2칸(``[{}, {}]``)과 success 를 보낸다.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from morph.lam_control_1.kit_main_dispatch import schedule_on_main_thread
from morph.lam_control_1.lam_extension_singleton import require_lam_extension_instance
from morph.lam_control_1.lam_federation_pipeline import run_federation_start_simulation

from .hyview_event_contract import V2T_RESPONSE_START_SIMULATION
from .lam_handler_config import FEDERATION_FETCH_LIMIT

# 응답 형식 미정 — case0·case1 빈 결과 placeholder (TBS ``_EMPTY_START_RESULT`` 대응)
_EMPTY_START_RESULT: List[Dict[str, Any]] = [{}, {}]


def _ok(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"code": 0, "message": "success", "data": dict(data or {})}


def _err(message: str, *, code: int = 1, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"code": int(code), "message": str(message), "data": dict(data or {})}


def _empty_results_data() -> Dict[str, Any]:
    return {"results": [dict(r) for r in _EMPTY_START_RESULT]}


def handle_start_simulation(
    payload: Dict[str, Any],
    *,
    dispatch: Callable[[str, Dict[str, Any]], None],
    event_name: str = V2T_RESPONSE_START_SIMULATION,
) -> None:
    """T2V_request_start_simulation — ``configs`` → Federation fetch + prerun + 재생.

    완료 시 ``dispatch(event_name, {code, message, data})`` 호출.
    성공 data 는 빈 ``results`` 2칸 (웹 응답 형식 확정 전 placeholder).
    """

    def _run() -> None:
        try:
            ext = require_lam_extension_instance()
        except Exception as exc:
            dispatch(event_name, _err(str(exc), data=_empty_results_data()))
            return

        def _on_complete(result: Dict[str, Any]) -> None:
            code = int(result.get("code", 0))
            if code != 0:
                dispatch(
                    event_name,
                    _err(
                        str(result.get("message", "failed")),
                        code=code,
                        data=_empty_results_data(),
                    ),
                )
                return
            dispatch(event_name, _ok(_empty_results_data()))

        try:
            run_federation_start_simulation(
                ext,
                dict(payload or {}),
                on_complete=_on_complete,
                auto_play=True,
                limit_override=int(FEDERATION_FETCH_LIMIT),
            )
        except Exception as exc:
            dispatch(event_name, _err(str(exc), data=_empty_results_data()))

    schedule_on_main_thread(_run)


__all__ = ["handle_start_simulation"]
