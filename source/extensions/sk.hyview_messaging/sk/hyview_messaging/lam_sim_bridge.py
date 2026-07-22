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

from .hyview_event_contract import (
    PAYLOAD_CASE,
    PAYLOAD_EQP_INFO_SHOW,
    PAYLOAD_FOUP_INFO_SHOW,
    PAYLOAD_PRIM_HIDE,
    PAYLOAD_PROC_ONLY,
    PAYLOAD_SHOW_TOP_VIEW,
    PAYLOAD_SPEED,
    PAYLOAD_WAFER_NUMBER_SHOW,
    V2T_RESPONSE_CONTROL_SIMULATION,
    V2T_RESPONSE_START_SIMULATION,
    V2T_RESPONSE_STOP_SIMULATION,
)
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
        print(
            "[LAM/FED-DIAG] S02_bridge_main | handle_start_simulation on main",
            flush=True,
        )
        try:
            ext = require_lam_extension_instance()
        except Exception as exc:
            print(
                f"[LAM/FED-DIAG] S02_bridge_fail | ext missing: {exc}",
                flush=True,
            )
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
            print(
                f"[LAM/FED-DIAG] S02_bridge_fail | pipeline raise: {exc}",
                flush=True,
            )
            dispatch(event_name, _err(str(exc), data=_empty_results_data()))

    schedule_on_main_thread(_run)


def handle_stop_simulation(
    payload: Dict[str, Any],
    *,
    dispatch: Callable[[str, Dict[str, Any]], None],
    event_name: str = V2T_RESPONSE_STOP_SIMULATION,
) -> None:
    """T2V_request_stop_simulation — ``case`` 화면만 중지 (0=화면1, 1=화면2).

    UI 「정지(초기화)」와 동일한 화면별 경로를 사용해 다른 화면 재생에 영향을
    주지 않는다. 중지·초기화 완료 콜백에서 ``dispatch(event_name, {code, message,
    data:{case}})`` 를 호출한다.
    """
    pl = dict(payload or {})
    raw_case = pl.get(PAYLOAD_CASE, 0)

    def _run() -> None:
        try:
            case_index = int(raw_case)
        except Exception:
            case_index = -1
        if case_index not in (0, 1):
            dispatch(
                event_name,
                _err(f"invalid case: {raw_case!r}", data={PAYLOAD_CASE: raw_case}),
            )
            return
        screen = case_index + 1
        try:
            ext = require_lam_extension_instance()
            lam_win = getattr(ext, "_lam_window", None)
            csv_win = (
                lam_win._ensure_csv_sim_play_window(screen)
                if lam_win is not None
                else None
            )
            if csv_win is None:
                raise RuntimeError(f"screen{screen} CSV play window unavailable")

            def _on_stop_done() -> None:
                dispatch(event_name, _ok({PAYLOAD_CASE: case_index}))

            csv_win._on_csv_stop_reset_clicked(on_complete=_on_stop_done)
        except Exception as exc:
            dispatch(event_name, _err(str(exc), data={PAYLOAD_CASE: case_index}))

    schedule_on_main_thread(_run)


def handle_control_simulation(
    payload: Dict[str, Any],
    *,
    dispatch: Callable[[str, Dict[str, Any]], None],
    event_name: str = V2T_RESPONSE_CONTROL_SIMULATION,
) -> None:
    """T2V_control_simulation — ``case`` 화면에 전달된 항목만 실시간 적용.

    payload 에 존재하는 키만 반영하고 나머지는 현재 상태 유지. 응답 ``data`` 는
    전달받은 payload 를 그대로 echo 한다(성공/실패 공통).
    """
    pl = dict(payload or {})
    raw_case = pl.get(PAYLOAD_CASE, 0)

    def _run() -> None:
        try:
            case_index = int(raw_case)
        except Exception:
            case_index = -1
        if case_index not in (0, 1):
            dispatch(event_name, _err(f"invalid case: {raw_case!r}", data=dict(pl)))
            return
        screen = case_index + 1
        try:
            kwargs: Dict[str, Any] = {}
            if PAYLOAD_PROC_ONLY in pl:
                kwargs["proc_only"] = bool(pl[PAYLOAD_PROC_ONLY])
            if PAYLOAD_SHOW_TOP_VIEW in pl:
                kwargs["top_view"] = bool(pl[PAYLOAD_SHOW_TOP_VIEW])
            if PAYLOAD_FOUP_INFO_SHOW in pl:
                kwargs["foup_info_show"] = bool(pl[PAYLOAD_FOUP_INFO_SHOW])
            if PAYLOAD_EQP_INFO_SHOW in pl:
                kwargs["eqp_info_show"] = bool(pl[PAYLOAD_EQP_INFO_SHOW])
            if PAYLOAD_WAFER_NUMBER_SHOW in pl:
                kwargs["wafer_number_show"] = bool(pl[PAYLOAD_WAFER_NUMBER_SHOW])
            if PAYLOAD_PRIM_HIDE in pl:
                kwargs["prim_hide"] = bool(pl[PAYLOAD_PRIM_HIDE])
            if PAYLOAD_SPEED in pl:
                kwargs["speed"] = float(pl[PAYLOAD_SPEED])

            ext = require_lam_extension_instance()
            lam_win = getattr(ext, "_lam_window", None)
            csv_win = (
                lam_win._ensure_csv_sim_play_window(screen)
                if lam_win is not None
                else None
            )
            if csv_win is None:
                raise RuntimeError(f"screen{screen} CSV play window unavailable")

            csv_win.apply_web_live_controls(**kwargs)
            dispatch(event_name, _ok(dict(pl)))
        except Exception as exc:
            dispatch(event_name, _err(str(exc), data=dict(pl)))

    schedule_on_main_thread(_run)


__all__ = [
    "handle_start_simulation",
    "handle_stop_simulation",
    "handle_control_simulation",
]
