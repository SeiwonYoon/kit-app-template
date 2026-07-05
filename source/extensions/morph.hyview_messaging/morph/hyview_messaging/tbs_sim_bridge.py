"""
HyView 메시징 확장 — EBSHandler 가 호출하는 Kit 시뮬 연동 로직.

ebs_handler.py: 이벤트 수신·V2T 응답 전송 (메시징 계층)
tbs_sim_bridge.py: 제어창·HUD 와 동일한 control_window 경로로 실제 동작 수행

배속: 요청에 speed 가 없으면 1.0 적용 (Kit 내부 하한 0.1).
추후 전용 T2V/V2T speed 이벤트 추가 시 handle_sim_speed 등으로 분리 가능.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

import omni.kit.app as kit_app

from morph.tbs_control_2.ebs_case_models import apply_case_sim_settings, case_from_screen
from morph.tbs_control_2.kit_main_dispatch import run_on_main_thread
from morph.tbs_control_2.sim_control_defaults import SIM_CONTROL_DEFAULTS
from morph.tbs_control_2.tbs_extension_singleton import require_tbs_extension_instance

_SIM_SPEED_MIN = 0.1
_SIM_SPEED_DEFAULT = 1.0
_EMPTY_START_RESULT: List[Dict[str, Any]] = [{}, {}]


def _case_index_to_screen(case_index: int) -> int:
    return max(1, int(case_index) + 1)


def _case_index_to_case_id(case_index: int) -> int:
    return case_from_screen(_case_index_to_screen(case_index))


def _ep_count_to_idx(ep_count: int) -> int:
    try:
        return 1 if int(ep_count) >= 3 else 0
    except Exception:
        return 0


def _clamp_sim_speed(speed: float) -> float:
    return max(_SIM_SPEED_MIN, float(speed))


def _read_sim_speed(ext: Any) -> float:
    try:
        m = getattr(ext, "_sim_speed_model", None)
        if m is not None:
            return _clamp_sim_speed(float(m.get_value_as_float()))
    except Exception:
        pass
    return _clamp_sim_speed(float(SIM_CONTROL_DEFAULTS.sim_speed))


def _set_sim_speed(ext: Any, speed: float) -> float:
    applied = _clamp_sim_speed(float(speed))
    m = getattr(ext, "_sim_speed_model", None)
    if m is not None:
        try:
            if hasattr(m, "set_value_as_float"):
                m.set_value_as_float(applied)
            else:
                m.set_value(applied)
        except Exception:
            pass
    return _read_sim_speed(ext)


def _parse_requested_speed(speed_raw: Any) -> float:
    if speed_raw is None:
        return _SIM_SPEED_DEFAULT
    try:
        return _clamp_sim_speed(float(speed_raw))
    except Exception:
        return _SIM_SPEED_DEFAULT


def _event_payload_to_dict(payload: Any) -> Dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return dict(payload)
    try:
        return dict(payload)
    except Exception:
        pass
    out: Dict[str, Any] = {}
    try:
        for k in payload.keys():  # type: ignore[union-attr]
            out[str(k)] = payload[k]  # type: ignore[index]
    except Exception:
        pass
    return out


def _ok(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"code": 0, "message": "success", "data": dict(data or {})}


def _err(message: str, *, code: int = 1, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"code": int(code), "message": str(message), "data": dict(data or {})}


def _apply_ep_count_for_case(ext: Any, case_index: int, ep_count: int) -> None:
    from morph.tbs_control_2.control_window import (
        on_sim_ep_count_changed,
        on_sim_ep_count_changed_for_case,
    )
    from morph.tbs_control_2.ebs_case_models import CASE_A, CASE_B, _sync_case_b_ep_count_combo_widgets
    from morph.tbs_control_2.ebs_control_panel_ui import _sync_ep_count_combo_widgets

    cid = _case_index_to_case_id(case_index)
    idx = _ep_count_to_idx(ep_count)
    if cid == CASE_A:
        _sync_ep_count_combo_widgets(ext, idx)
        on_sim_ep_count_changed(ext)
    else:
        _sync_case_b_ep_count_combo_widgets(ext, idx)
        on_sim_ep_count_changed_for_case(ext, CASE_B)


def _apply_ebs_enable_for_case(ext: Any, case_index: int, ebs_enable: bool) -> None:
    from morph.tbs_control_2.control_window import on_sim_ebs_enabled_changed_for_case
    from morph.tbs_control_2.ebs_case_models import CASE_A, CASE_B

    cid = _case_index_to_case_id(case_index)
    apply_case_sim_settings(ext, cid, {"ebs_enabled": bool(ebs_enable)})
    if cid == CASE_A:
        from morph.tbs_control_2.tbs_ep_port_visibility import on_sim_ebs_enabled_changed

        on_sim_ebs_enabled_changed(ext)
    else:
        on_sim_ebs_enabled_changed_for_case(ext, CASE_B)


def _apply_settings_snapshot_for_case(ext: Any, case_index: int, snap: Dict[str, Any]) -> None:
    if not isinstance(snap, dict) or not snap:
        return
    apply_case_sim_settings(ext, _case_index_to_case_id(case_index), dict(snap))
    if "ep_count_idx" in snap:
        try:
            ep_count = 3 if int(snap.get("ep_count_idx", 0) or 0) >= 1 else 2
            _apply_ep_count_for_case(ext, case_index, ep_count)
        except Exception:
            pass
    if "ebs_enabled" in snap or "ebs_enable" in snap:
        try:
            _apply_ebs_enable_for_case(
                ext,
                case_index,
                bool(snap.get("ebs_enable", snap.get("ebs_enabled", True))),
            )
        except Exception:
            pass
    from morph.tbs_control_2.control_window import _apply_ep_port_layout_for_sim_screen

    _apply_ep_port_layout_for_sim_screen(
        ext,
        _case_index_to_screen(case_index),
        reason=f"hyview_config_case{int(case_index)}",
    )


def _prerun_result_for_case(ext: Any, case_index: int) -> Dict[str, Any]:
    screen = _case_index_to_screen(case_index)
    raw = getattr(ext, "_sim_prerun_export_json_by_screen", None)
    if not isinstance(raw, dict):
        return {}
    doc = raw.get(str(screen)) or raw.get(screen)
    if not isinstance(doc, dict):
        return {}
    return dict(doc)


def handle_eqp_change(payload: Any) -> Dict[str, Any]:
    """T2V_request_eqp_change — eqp_id 는 무시, ep_count 만 반영."""
    pl = _event_payload_to_dict(payload)
    try:
        case_index = int(pl.get("case", 0))
        ep_count = int(pl.get("ep_count", 2))
    except Exception as exc:
        return _err(
            f"invalid payload: {exc}",
            data={"case": pl.get("case", 0), "ep_count": pl.get("ep_count", 2)},
        )

    def _work() -> Dict[str, Any]:
        ext = require_tbs_extension_instance()
        _apply_ep_count_for_case(ext, case_index, ep_count)
        from morph.tbs_control_2.control_window import _apply_ep_port_layout_for_sim_screen

        _apply_ep_port_layout_for_sim_screen(
            ext,
            _case_index_to_screen(case_index),
            reason="hyview_eqp_change",
        )
        return _ok({"case": case_index, "ep_count": ep_count})

    try:
        return run_on_main_thread(_work)
    except Exception as exc:
        return _err(str(exc), data={"case": case_index, "ep_count": ep_count})


def handle_ebs_enable(payload: Any) -> Dict[str, Any]:
    pl = _event_payload_to_dict(payload)
    try:
        case_index = int(pl.get("case", 0))
        ebs_enable = bool(pl.get("ebs_enable", True))
    except Exception as exc:
        return _err(
            f"invalid payload: {exc}",
            data={"case": pl.get("case", 0), "ebs_enable": pl.get("ebs_enable", False)},
        )

    def _work() -> Dict[str, Any]:
        ext = require_tbs_extension_instance()
        _apply_ebs_enable_for_case(ext, case_index, ebs_enable)
        from morph.tbs_control_2.control_window import _apply_ep_port_layout_for_sim_screen

        _apply_ep_port_layout_for_sim_screen(
            ext,
            _case_index_to_screen(case_index),
            reason="hyview_ebs_enable",
        )
        return _ok({"case": case_index, "ebs_enable": bool(ebs_enable)})

    try:
        return run_on_main_thread(_work)
    except Exception as exc:
        return _err(str(exc), data={"case": case_index, "ebs_enable": ebs_enable})


async def _wait_prerun_done(ext: Any, *, timeout_sec: float = 600.0) -> bool:
    ev = getattr(ext, "_sim_prerun_done_evt", None)
    app = kit_app.get_app()
    deadline = time.monotonic() + float(timeout_sec)
    while time.monotonic() < deadline:
        if ev is not None and hasattr(ev, "is_set") and ev.is_set():
            return True
        try:
            from morph.tbs_control_2.control_window import _drain_sim_log_queue

            _drain_sim_log_queue(ext)
        except Exception:
            pass
        await app.next_update_async()
    return bool(ev is not None and hasattr(ev, "is_set") and ev.is_set())


def _collect_start_result(ext: Any) -> List[Dict[str, Any]]:
    return [_prerun_result_for_case(ext, 0), _prerun_result_for_case(ext, 1)]


def handle_start_simulation(
    payload: Any,
    *,
    dispatch: Callable[[str, Dict[str, Any]], None],
) -> None:
    """T2V_request_start_simulation — config[0]=case0, config[1]=case1 settings_snapshot."""
    pl = _event_payload_to_dict(payload)
    raw_config = pl.get("config")
    if not isinstance(raw_config, list):
        raw_config = []
    config: List[Dict[str, Any]] = [snap if isinstance(snap, dict) else {} for snap in raw_config]
    while len(config) < 2:
        config.append({})

    def _begin() -> None:
        try:
            ext = require_tbs_extension_instance()
        except Exception as exc:
            dispatch(
                "V2T_response_start_simulation",
                _err(str(exc), data={"result": list(_EMPTY_START_RESULT)}),
            )
            return

        for case_index in (0, 1):
            snap = config[case_index]
            if snap:
                _apply_settings_snapshot_for_case(ext, case_index, snap)

        from morph.tbs_control_2.control_window import on_sim_start_clicked

        try:
            on_sim_start_clicked(ext)
        except Exception as exc:
            dispatch(
                "V2T_response_start_simulation",
                _err(str(exc), data={"result": list(_EMPTY_START_RESULT)}),
            )
            return

        async def _finish() -> None:
            try:
                ext2 = require_tbs_extension_instance()
            except Exception as exc:
                dispatch(
                    "V2T_response_start_simulation",
                    _err(str(exc), data={"result": list(_EMPTY_START_RESULT)}),
                )
                return
            ok = await _wait_prerun_done(ext2)
            if not ok:
                dispatch(
                    "V2T_response_start_simulation",
                    _err("prerun timeout or failed", data={"result": list(_EMPTY_START_RESULT)}),
                )
                return
            dispatch(
                "V2T_response_start_simulation",
                _ok({"result": _collect_start_result(ext2)}),
            )

        try:
            asyncio.ensure_future(_finish())
        except Exception as exc:
            dispatch(
                "V2T_response_start_simulation",
                _err(str(exc), data={"result": list(_EMPTY_START_RESULT)}),
            )

    try:
        run_on_main_thread(_begin, timeout=30.0)
    except Exception as exc:
        dispatch(
            "V2T_response_start_simulation",
            _err(str(exc), data={"result": list(_EMPTY_START_RESULT)}),
        )


def handle_control_simulation(payload: Any) -> Dict[str, Any]:
    pl = _event_payload_to_dict(payload)
    action = str(pl.get("action", "") or "").strip().lower()
    speed_requested = _parse_requested_speed(pl.get("speed"))

    def _work() -> Dict[str, Any]:
        from morph.tbs_control_2.control_window import on_sim_start_clicked, on_sim_stop_clicked

        ext = require_tbs_extension_instance()
        _set_sim_speed(ext, speed_requested)

        if action == "pause":
            on_sim_stop_clicked(ext)
            active = "pause"
        elif action == "play":
            # --- play: 기본 = 재시작 (Viewport HUD 시작과 동일) ---
            on_sim_start_clicked(ext)
            active = "play"

            # --- [RESUME] 이어하기: 미구현. 필요 시 위 on_sim_start_clicked 를 주석 처리하고 아래 해제 ---
            # from morph.tbs_control_2.control_window import ...  # _resume_playback_from_pause(ext)
            # active = "play"
        else:
            return _err(f"unknown action: {action!r}", data={"active": "", "speed": _SIM_SPEED_DEFAULT})

        return _ok({"active": active, "speed": _read_sim_speed(ext)})

    try:
        return run_on_main_thread(_work, timeout=180.0)
    except Exception as exc:
        return _err(str(exc), data={"active": "", "speed": _SIM_SPEED_DEFAULT})


def payload_from_event(event: Any) -> Dict[str, Any]:
    return _event_payload_to_dict(getattr(event, "payload", None))
