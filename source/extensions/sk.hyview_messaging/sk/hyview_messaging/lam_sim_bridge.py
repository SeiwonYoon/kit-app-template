"""HyView 메시징 ↔ LAM Federation 시뮬 브리지."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from morph.lam_control_1.kit_main_dispatch import schedule_on_main_thread
from morph.lam_control_1.lam_extension_singleton import require_lam_extension_instance
from morph.lam_control_1.lam_federation_pipeline import run_federation_start_simulation

from .lam_handler_config import FEDERATION_FETCH_LIMIT


def _ok(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"code": 0, "message": "success", "data": dict(data or {})}


def _err(message: str, *, code: int = 1, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"code": int(code), "message": str(message), "data": dict(data or {})}


def handle_start_simulation(
    payload: Dict[str, Any],
    *,
    dispatch: Callable[[str, Dict[str, Any]], None],
    event_name: str = "V2T_response_start_simulation",
) -> None:
    """T2V ``configs`` → Federation fetch + prerun + 재생 → V2T (형식은 placeholder)."""

    def _run() -> None:
        try:
            ext = require_lam_extension_instance()
        except Exception as exc:
            dispatch(event_name, _err(str(exc)))
            return

        def _on_complete(result: Dict[str, Any]) -> None:
            code = int(result.get("code", 0))
            if code != 0:
                dispatch(event_name, result)
                return
            # V2T 형식 미정 — 최소 성공 envelope + 화면 메타
            dispatch(
                event_name,
                _ok(
                    {
                        "result": [{}, {}],
                        "pipeline": result.get("data", {}),
                    }
                ),
            )

        run_federation_start_simulation(
            ext,
            dict(payload or {}),
            on_complete=_on_complete,
            auto_play=True,
            limit_override=int(FEDERATION_FETCH_LIMIT),
        )

    schedule_on_main_thread(_run)


__all__ = ["handle_start_simulation"]
