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
from morph.tbs_control_2.hyview_stream import (
    bridge_queued,
    bridge_watchdog,
    bridge_work_done,
    bridge_work_start,
)
from morph.tbs_control_2.kit_main_dispatch import schedule_on_main_thread
from morph.tbs_control_2.sim_control_defaults import HYVIEW_BRIDGE_WATCHDOG_SEC, SIM_CONTROL_DEFAULTS
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


def _schedule_hyview_main_work(
    op: str,
    work_fn: Callable[[], Dict[str, Any]],
    dispatch: Callable[[Dict[str, Any]], None],
    *,
    watchdog_sec: float = HYVIEW_BRIDGE_WATCHDOG_SEC,
    **queued_ctx: Any,
) -> None:
    """메인 스레드에 work 큐 — 메시징 스레드 block 없음. 완료 시 dispatch."""
    req_id = bridge_queued(op, **queued_ctx)
    state = {"done": False, "started": False}

    def _finish(result: Dict[str, Any]) -> None:
        if state["done"]:
            return
        state["done"] = True
        dispatch(result)

    def _work() -> None:
        state["started"] = True
        t0 = bridge_work_start(req_id, op)
        try:
            result = work_fn()
        except Exception as exc:
            result = _err(str(exc), data=dict(queued_ctx))
        bridge_work_done(req_id, op, t0)
        _finish(result)

    schedule_on_main_thread(
        _work,
        on_error=lambda exc: _finish(_err(str(exc), data=dict(queued_ctx))),
    )

    if watchdog_sec > 0:
        async def _watchdog() -> None:
            await asyncio.sleep(float(watchdog_sec))
            if state["done"]:
                return
            if not state["started"]:
                bridge_watchdog(
                    req_id,
                    f"work not started within {watchdog_sec}s since queued",
                )
            else:
                bridge_watchdog(
                    req_id,
                    f"work not done within {watchdog_sec}s since work_start",
                )

        try:
            asyncio.ensure_future(_watchdog())
        except Exception:
            pass


def _normalize_ep_count(raw: Any, *, default: int = 2) -> int:
    try:
        return 3 if int(raw) >= 3 else 2
    except Exception:
        return 3 if int(default) >= 3 else 2


def _normalize_hyview_case_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """웹 configs[n] — ``settings_snapshot`` 하위 + 최상위 병합(최상위 우선). ``ep_count`` 만 사용."""
    if not isinstance(raw, dict):
        return {}
    inner = raw.get("settings_snapshot")
    merged: Dict[str, Any] = {}
    if isinstance(inner, dict):
        merged.update(inner)
    for key, val in raw.items():
        if key == "settings_snapshot":
            continue
        merged[key] = val
    merged.pop("ep_count_idx", None)
    if "ep_count" in merged:
        merged["ep_count"] = _normalize_ep_count(merged.get("ep_count"))
    return merged


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
    raw = snap if isinstance(snap, dict) else {}
    norm = _normalize_hyview_case_config(raw)
    if not norm:
        return
    apply_case_sim_settings(ext, _case_index_to_case_id(case_index), norm)
    if "ep_count" in norm:
        try:
            _apply_ep_count_for_case(ext, case_index, int(norm["ep_count"]))
        except Exception:
            pass
    if "ebs_enabled" in norm or "ebs_enable" in norm:
        try:
            from morph.tbs_control_2.ebs_case_models import CASE_A, CASE_B
            from morph.tbs_control_2.tbs_ep_port_visibility import on_sim_ebs_enabled_changed
            from morph.tbs_control_2.control_window import on_sim_ebs_enabled_changed_for_case

            cid = _case_index_to_case_id(case_index)
            if cid == CASE_A:
                on_sim_ebs_enabled_changed(ext)
            else:
                on_sim_ebs_enabled_changed_for_case(ext, CASE_B)
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


def handle_eqp_change(
    payload: Any,
    *,
    dispatch: Callable[[Dict[str, Any]], None],
) -> None:
    """T2V_request_eqp_change — eqp_id 는 무시, ep_count 만 반영."""
    pl = _event_payload_to_dict(payload)
    try:
        case_index = int(pl.get("case", 0))
        ep_count = int(pl.get("ep_count", 2))
    except Exception as exc:
        dispatch(
            _err(
                f"invalid payload: {exc}",
                data={"case": pl.get("case", 0), "ep_count": pl.get("ep_count", 2)},
            )
        )
        return

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

    _schedule_hyview_main_work(
        "eqp_change",
        _work,
        dispatch,
        case=case_index,
        ep_count=ep_count,
    )


def handle_ebs_enable(
    payload: Any,
    *,
    dispatch: Callable[[Dict[str, Any]], None],
) -> None:
    pl = _event_payload_to_dict(payload)
    try:
        case_index = int(pl.get("case", 0))
        ebs_enable = bool(pl.get("ebs_enable", True))
    except Exception as exc:
        dispatch(
            _err(
                f"invalid payload: {exc}",
                data={"case": pl.get("case", 0), "ebs_enable": pl.get("ebs_enable", False)},
            )
        )
        return

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

    _schedule_hyview_main_work(
        "ebs_enable",
        _work,
        dispatch,
        case=case_index,
        ebs_enable=bool(ebs_enable),
    )


async def _wait_prerun_done(ext: Any, *, timeout_sec: float = 600.0) -> bool:
    """프리런 스레드 완료 + 웹 응답용 export JSON 준비까지 대기.

    ``_sim_prerun_done_evt`` 만 보고 return 하면 ``_finalize_prerun_ui_assets`` 전에
    ``_collect_start_result`` 가 돌아 ``timetable_rows: []`` 가 간헐적으로 나간다.
    drain(finalize) 후 export 가 채워졌는지 확인한다.
    """
    ev = getattr(ext, "_sim_prerun_done_evt", None)
    app = kit_app.get_app()
    deadline = time.monotonic() + float(timeout_sec)
    while time.monotonic() < deadline:
        try:
            from morph.tbs_control_2.control_window import _drain_sim_log_queue

            _drain_sim_log_queue(ext)
        except Exception:
            pass
        if ev is not None and hasattr(ev, "is_set") and ev.is_set():
            if _prerun_web_export_ready(ext):
                return True
        await app.next_update_async()
    # timeout — 마지막 drain 후 ready 이면 성공, 아니면 기존처럼 event 만 본다
    try:
        from morph.tbs_control_2.control_window import _drain_sim_log_queue

        _drain_sim_log_queue(ext)
    except Exception:
        pass
    if _prerun_web_export_ready(ext):
        return True
    return bool(ev is not None and hasattr(ev, "is_set") and ev.is_set())


def _prerun_web_export_ready(ext: Any) -> bool:
    """start_simulation 수집용 — 프리런 결과 화면마다 export 문서가 준비됐는지."""
    results = getattr(ext, "_sim_prerun_results_by_screen", None)
    if not isinstance(results, dict) or not results:
        return False
    export = getattr(ext, "_sim_prerun_export_json_by_screen", None)
    if not isinstance(export, dict) or not export:
        return False
    for key in results.keys():
        try:
            scr = int(key)
        except Exception:
            continue
        doc = export.get(str(scr))
        if not isinstance(doc, dict):
            doc = export.get(scr)
        if not isinstance(doc, dict) or not doc:
            return False
        tl = doc.get("timeline")
        if not isinstance(tl, dict):
            return False
        rows = tl.get("timetable_rows")
        if not isinstance(rows, list):
            return False
    return True


def _collect_start_result(ext: Any) -> List[Dict[str, Any]]:
    return [_prerun_result_for_case(ext, 0), _prerun_result_for_case(ext, 1)]


def handle_start_simulation(
    payload: Any,
    *,
    dispatch: Callable[[str, Dict[str, Any]], None],
) -> None:
    """T2V_request_start_simulation — configs[0]=case0, configs[1]=case1 settings_snapshot."""
    pl = _event_payload_to_dict(payload)
    raw_config = pl.get("configs")
    if not isinstance(raw_config, list):
        raw_config = []
    config: List[Dict[str, Any]] = [
        _normalize_hyview_case_config(snap if isinstance(snap, dict) else {}) for snap in raw_config
    ]
    while len(config) < 2:
        config.append({})

    req_id = bridge_queued("start_simulation", cases=2)

    def _begin() -> None:
        t0 = bridge_work_start(req_id, "start_simulation")
        try:
            ext = require_tbs_extension_instance()
        except Exception as exc:
            bridge_work_done(req_id, "start_simulation", t0)
            dispatch(
                "V2T_response_start_simulation",
                _err(str(exc), data={"results": list(_EMPTY_START_RESULT)}),
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
            bridge_work_done(req_id, "start_simulation", t0)
            dispatch(
                "V2T_response_start_simulation",
                _err(str(exc), data={"results": list(_EMPTY_START_RESULT)}),
            )
            return

        bridge_work_done(req_id, "start_simulation_begin", t0)

        async def _finish() -> None:
            t1 = bridge_work_start(req_id, "start_simulation_prerun_wait")
            try:
                ext2 = require_tbs_extension_instance()
            except Exception as exc:
                bridge_work_done(req_id, "start_simulation_prerun_wait", t1)
                dispatch(
                    "V2T_response_start_simulation",
                    _err(str(exc), data={"results": list(_EMPTY_START_RESULT)}),
                )
                return
            ok = await _wait_prerun_done(ext2)
            bridge_work_done(req_id, "start_simulation_prerun_wait", t1)
            if not ok:
                dispatch(
                    "V2T_response_start_simulation",
                    _err("prerun timeout or failed", data={"results": list(_EMPTY_START_RESULT)}),
                )
                return
            dispatch(
                "V2T_response_start_simulation",
                _ok({"results": _collect_start_result(ext2)}),
            )

        try:
            asyncio.ensure_future(_finish())
        except Exception as exc:
            dispatch(
                "V2T_response_start_simulation",
                _err(str(exc), data={"results": list(_EMPTY_START_RESULT)}),
            )

    schedule_on_main_thread(
        _begin,
        on_error=lambda exc: dispatch(
            "V2T_response_start_simulation",
            _err(str(exc), data={"results": list(_EMPTY_START_RESULT)}),
        ),
    )


def handle_restart_simulation(
    payload: Any,
    *,
    dispatch: Callable[[str, Dict[str, Any]], None],
) -> None:
    """T2V_request_restart_simulation — 직전 프리런으로 재생만 재시작.

    웹은 이미 start 응답 데이터를 보유하므로 V2T ``data`` 는 비운다.
    """
    _ = _event_payload_to_dict(payload)
    req_id = bridge_queued("restart_simulation")

    def _work() -> None:
        t0 = bridge_work_start(req_id, "restart_simulation")
        try:
            from morph.tbs_control_2.control_window import on_sim_restart_clicked

            ext = require_tbs_extension_instance()
            bundle = getattr(ext, "_sim_restart_prerun_bundle", None)
            if not isinstance(bundle, dict) or not bundle.get("results"):
                bridge_work_done(req_id, "restart_simulation", t0)
                dispatch(
                    "V2T_response_restart_simulation",
                    _err("no cached prerun — start simulation first", data={}),
                )
                return
            on_sim_restart_clicked(ext)
            bridge_work_done(req_id, "restart_simulation", t0)
            # 웹이 기존 start 데이터를 보관 — data 재전송 없음
            dispatch("V2T_response_restart_simulation", _ok({}))
        except Exception as exc:
            bridge_work_done(req_id, "restart_simulation", t0)
            dispatch(
                "V2T_response_restart_simulation",
                _err(str(exc), data={}),
            )

    schedule_on_main_thread(
        _work,
        on_error=lambda exc: dispatch(
            "V2T_response_restart_simulation",
            _err(str(exc), data={}),
        ),
    )


def handle_control_simulation(
    payload: Any,
    *,
    dispatch: Callable[[Dict[str, Any]], None],
) -> None:
    pl = _event_payload_to_dict(payload)
    action = str(pl.get("action", "") or "").strip().lower()
    speed_requested = _parse_requested_speed(pl.get("speed"))

    def _work() -> Dict[str, Any]:
        from morph.tbs_control_2.control_sim_screen_playback import is_simulation_in_progress
        from morph.tbs_control_2.control_window import on_sim_start_clicked, on_sim_stop_clicked

        ext = require_tbs_extension_instance()

        if action == "pause":
            _set_sim_speed(ext, speed_requested)
            on_sim_stop_clicked(ext)
            return _ok({"active": "pause", "speed": _read_sim_speed(ext)})

        if action == "play":
            if is_simulation_in_progress(ext):
                set_sim_speed(ext, speed_requested)
                return ok({"active": "play", "speed": _read_sim_speed(ext)})
            _set_sim_speed(ext, speed_requested)
            on_sim_start_clicked(ext)
            return _ok({"active": "play", "speed": _read_sim_speed(ext)})

        return _err(f"unknown action: {action!r}", data={"active": "", "speed": _SIM_SPEED_DEFAULT})

    _schedule_hyview_main_work(
        "control_simulation",
        _work,
        dispatch,
        action=action,
        speed=speed_requested,
    )


def handle_seek_simulation(
    payload: Any,
    *,
    dispatch: Callable[[Dict[str, Any]], None],
) -> None:
    """웹 seek — 막대그래프 시간축 클릭과 동일 (``_on_bar_timeline_seek``)."""
    from .hyview_event_contract import (
        DATA_CASE,
        DATA_ROW_INDEX,
        DATA_T,
        DATA_T_REQUESTED,
        PAYLOAD_CASE,
        PAYLOAD_T,
    )

    pl = _event_payload_to_dict(payload)
    try:
        case_index = int(pl.get(PAYLOAD_CASE, 0))
    except Exception:
        case_index = 0
    if case_index not in (0, 1):
        dispatch(
            _err(
                f"invalid {PAYLOAD_CASE}: {case_index!r} (0 or 1)",
                data={DATA_CASE: case_index, DATA_T: pl.get(PAYLOAD_T)},
            )
        )
        return

    t_raw = pl.get(PAYLOAD_T)
    if t_raw is None:
        dispatch(
            _err(
                f"missing {PAYLOAD_T}",
                data={DATA_CASE: case_index, DATA_T: None, DATA_ROW_INDEX: None},
            )
        )
        return
    try:
        t_requested = float(t_raw)
    except Exception:
        dispatch(
            _err(
                f"invalid {PAYLOAD_T}: {t_raw!r}",
                data={DATA_CASE: case_index, DATA_T: t_raw, DATA_ROW_INDEX: None},
            )
        )
        return

    def _work() -> Dict[str, Any]:
        from morph.tbs_control_2.control_sim_timetable_ui import refresh_timetable_row_highlight
        from morph.tbs_control_2.control_window import (
            _fast_apply_prerun_seek,
            _resolve_timetable_row_index_for_sim_time,
        )

        ext = require_tbs_extension_instance()
        screen = _case_index_to_screen(case_index)
        err_base = {
            DATA_CASE: case_index,
            DATA_T: float(t_requested),
            DATA_T_REQUESTED: float(t_requested),
            DATA_ROW_INDEX: None,
        }

        results = getattr(ext, "_sim_prerun_results_by_screen", None)
        if not isinstance(results, dict) or results.get(int(screen)) is None:
            return _err(
                "prerun not loaded — start_simulation 후 seek 가능",
                data=err_base,
            )

        row_idx = _resolve_timetable_row_index_for_sim_time(
            ext,
            int(screen),
            float(t_requested),
        )
        if row_idx is None:
            return _err("no timetable rows for seek", data=err_base)

        try:
            t_target, _play_cursor = _fast_apply_prerun_seek(
                ext,
                screen=int(screen),
                row_index=int(row_idx),
            )
            try:
                refresh_timetable_row_highlight(
                    ext,
                    screen=int(screen),
                    sim_now=float(t_target),
                )
            except Exception:
                pass
            print(
                f"[HyView/bridge] seek_simulation case={case_index} screen={screen} "
                f"t_req={float(t_requested):.2f} → t={float(t_target):.2f} row={int(row_idx)}",
                flush=True,
            )
        except Exception as exc:
            return _err(
                f"seek failed: {exc}",
                data={**err_base, DATA_ROW_INDEX: int(row_idx)},
            )

        return _ok(
            {
                DATA_CASE: case_index,
                DATA_T: float(t_target),
                DATA_T_REQUESTED: float(t_requested),
                DATA_ROW_INDEX: int(row_idx),
            }
        )

    _schedule_hyview_main_work(
        "seek_simulation",
        _work,
        dispatch,
        case=case_index,
        t=t_requested,
    )


def handle_time_sync(
    payload: Any,
    *,
    dispatch: Callable[[Dict[str, Any]], None],
) -> None:
    """웹 진행시간 드리프트 보정 — Kit 현재 sim 진행 시각(초) 반환.

    요청: ``{}`` (필드 없음)
    성공 data: ``{"time": <sim_seconds>}`` — 화면1(case0) 시계 기준.
    """
    from .hyview_event_contract import DATA_TIME

    def _work() -> Dict[str, Any]:
        from morph.tbs_control_2.control_sim_multi_playback import get_sim_playback_player
        from morph.tbs_control_2.control_window import _resolve_ep_timeline_sim_time

        ext = require_tbs_extension_instance()
        t_now = 0.0
        # 화면1 플레이어가 있으면 sim_now 우선, 없으면 EP 타임라인 공용 resolve.
        player = get_sim_playback_player(ext, 1)
        if player is not None:
            try:
                t_now = float(player.sim_now(1))
            except Exception:
                t_now = 0.0
        if t_now <= 1e-9:
            try:
                t_now = float(_resolve_ep_timeline_sim_time(ext, 1, ""))
            except Exception:
                t_now = 0.0
        t_out = round(max(0.0, float(t_now)), 2)
        print(f"[HyView/bridge] time_sync → time={t_out}", flush=True)
        return _ok({DATA_TIME: t_out})

    _schedule_hyview_main_work("time_sync", _work, dispatch)


def handle_screen_visibility(
    payload: Any,
    *,
    dispatch: Callable[[Dict[str, Any]], None],
) -> None:
    """T2V_request_screen_visibility — 화면1·2 Dock 표시 전환 (start 와 독립).

    요청::
        ``{"show_1": true, "show_2": false}``
        또는 ``{"screens": [1]}`` / ``{"screens": [1, 2]}``
        또는 ``{"case": 0}`` (해당 case 화면만)

    응답 data::
        ``{"show_1": bool, "show_2": bool}``
    """
    from typing import Tuple as _Tuple

    pl = _event_payload_to_dict(payload)

    def _parse_flags() -> _Tuple[bool, bool]:
        if "show_1" in pl or "show_2" in pl:
            return bool(pl.get("show_1", False)), bool(pl.get("show_2", False))
        raw = pl.get("screens")
        if isinstance(raw, (list, tuple)):
            wanted = set()
            for x in raw:
                try:
                    wanted.add(int(x))
                except Exception:
                    continue
            return (1 in wanted), (2 in wanted)
        if "case" in pl:
            try:
                ci = int(pl.get("case", 0))
            except Exception:
                ci = 0
            return (ci == 0), (ci == 1)
        return True, True

    show_1, show_2 = _parse_flags()
    if not show_1 and not show_2:
        show_1 = True

    def _work() -> Dict[str, Any]:
        from morph.tbs_control_2.tbs_screen_visibility import (
            request_screen_visibility,
            visible_screens,
        )

        ext = require_tbs_extension_instance()
        # Dock 전환은 async — 모델은 즉시 갱신되고 레이아웃은 다음 틱에 적용.
        request_screen_visibility(ext, show_1, show_2)
        s1, s2 = visible_screens(ext)
        return _ok({"show_1": bool(s1), "show_2": bool(s2)})

    _schedule_hyview_main_work(
        "screen_visibility",
        _work,
        dispatch,
        show_1=show_1,
        show_2=show_2,
    )


def payload_from_event(event: Any) -> Dict[str, Any]:
    return _event_payload_to_dict(getattr(event, "payload", None))
