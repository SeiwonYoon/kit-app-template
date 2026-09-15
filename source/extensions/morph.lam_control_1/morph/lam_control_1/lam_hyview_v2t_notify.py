"""Kit → 웹 단방향 V2T 통지.

웹이 수신 후 아무 조치도 하지 않아도 Kit 시뮬·HUD·prim 숨김은 그대로 동작한다.
"""

from __future__ import annotations

from typing import Any, Dict

from .kit_main_dispatch import schedule_on_main_thread

_PRINT_PREFIX = "[LAM/HyView]"


def screen_to_case(screen: int) -> int:
    """화면 1→case 0, 화면 2→case 1."""
    return 0 if int(screen) <= 1 else 1


def dispatch_v2t_notify(event_name: str, data: Dict[str, Any]) -> None:
    """``{code, message, data}`` envelope 로 livestream V2T 전송 (메인 스레드)."""
    name = str(event_name or "").strip()
    if not name:
        return
    payload = {
        "code": 0,
        "message": "success",
        "data": dict(data or {}),
    }

    def _send() -> None:
        try:
            from carb.eventdispatcher import get_eventdispatcher  # type: ignore

            get_eventdispatcher().dispatch_event(name, payload=payload)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} {name} dispatch: {exc}", flush=True)

    schedule_on_main_thread(_send)


def notify_prim_hide(screen: int, *, prim_hide: bool = True) -> None:
    """fly 이후 자동 prim 숨김 완료 — ``T2V_control_simulation`` 과 동일 키."""
    try:
        from sk.hyview_messaging.hyview_event_contract import (  # type: ignore
            PAYLOAD_CASE,
            PAYLOAD_PRIM_HIDE,
            V2T_NOTIFY_CONTROL_SIMULATION,
        )
    except Exception:
        PAYLOAD_CASE = "case"
        PAYLOAD_PRIM_HIDE = "prim_hide"
        V2T_NOTIFY_CONTROL_SIMULATION = "V2T_notify_control_simulation"
    case_index = screen_to_case(screen)
    print(
        f"{_PRINT_PREFIX} {V2T_NOTIFY_CONTROL_SIMULATION} "
        f"case={case_index} prim_hide={bool(prim_hide)}",
        flush=True,
    )
    dispatch_v2t_notify(
        V2T_NOTIFY_CONTROL_SIMULATION,
        {PAYLOAD_CASE: case_index, PAYLOAD_PRIM_HIDE: bool(prim_hide)},
    )


def notify_load_status(
    screen: int,
    status: str,
    *,
    detail: str = "",
) -> None:
    """Federation 로딩 HUD 단계와 동일한 ``status`` 를 화면별로 통지."""
    st = str(status or "").strip().lower()
    if not st:
        return
    try:
        from sk.hyview_messaging.hyview_event_contract import (  # type: ignore
            PAYLOAD_CASE,
            PAYLOAD_DETAIL,
            PAYLOAD_STATUS,
            V2T_NOTIFY_LOAD_STATUS,
        )
    except Exception:
        PAYLOAD_CASE = "case"
        PAYLOAD_DETAIL = "detail"
        PAYLOAD_STATUS = "status"
        V2T_NOTIFY_LOAD_STATUS = "V2T_notify_load_status"
    case_index = screen_to_case(screen)
    data: Dict[str, Any] = {PAYLOAD_CASE: case_index, PAYLOAD_STATUS: st}
    detail_s = str(detail or "").strip()
    if detail_s:
        data[PAYLOAD_DETAIL] = detail_s
    print(
        f"{_PRINT_PREFIX} {V2T_NOTIFY_LOAD_STATUS} "
        f"case={case_index} status={st}"
        + (f" detail={detail_s!r}" if detail_s else ""),
        flush=True,
    )
    dispatch_v2t_notify(V2T_NOTIFY_LOAD_STATUS, data)


__all__ = [
    "dispatch_v2t_notify",
    "notify_load_status",
    "notify_prim_hide",
    "screen_to_case",
]
