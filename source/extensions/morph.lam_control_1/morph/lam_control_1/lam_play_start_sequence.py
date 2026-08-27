"""CSV Play 시작 전 오케스트레이션 — 카메라 fly · prim 숨김/보임 · 재생 사이 delay.

기준 (lam_viewport_overlay_config):
- prim 숨김/보임 시작: 카메라 fly **실제 완료** 이후
  (+ PLAY_DELAY_CAMERA_TO_PRIM_HIDE_SEC, 음수면 0으로 취급 — fly 와 겹치지 않음)
  - fly 미실행/스킵 시 카메라 끝 = 즉시(또는 delay만)
  - ``PLAY_HIDE_PRIM_SPECS`` + ``PLAY_SHOW_PRIM_SPECS`` 모두 이 시점 이후 적용
- CSV 재생 시작: (prim 숨김 **끝**) + PLAY_DELAY_PRIM_HIDE_TO_PLAY_SEC
  - delay <= 0: prim 숨김 **시작** + 예정 hide duration 기준 스케줄(겹침)
  - delay > 0: hide **실제 완료** 후 delay 만큼 대기
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

_PRINT_PREFIX = "[LAM/PlayStartSeq]"


def _delay_camera_to_prim_hide_sec() -> float:
    try:
        from .lam_viewport_overlay_config import (  # type: ignore
            PLAY_DELAY_CAMERA_TO_PRIM_HIDE_SEC,
        )

        return float(PLAY_DELAY_CAMERA_TO_PRIM_HIDE_SEC)
    except Exception:
        return 0.0


def _delay_prim_hide_to_play_sec() -> float:
    try:
        from .lam_viewport_overlay_config import (  # type: ignore
            PLAY_DELAY_PRIM_HIDE_TO_PLAY_SEC,
        )

        return float(PLAY_DELAY_PRIM_HIDE_TO_PLAY_SEC)
    except Exception:
        return 0.0


def _playback_stop_requested() -> bool:
    try:
        from .simulation_play import csv_playback_stop_requested  # type: ignore

        return bool(csv_playback_stop_requested())
    except Exception:
        return False


def _sleep_until(deadline: float, *, stop_requested: Callable[[], bool]) -> bool:
    """``True`` = deadline 까지 대기 완료, ``False`` = 일시정지·정지로 중단."""
    end = float(deadline)
    while True:
        if stop_requested():
            return False
        remain = end - time.monotonic()
        if remain <= 1e-6:
            return True
        time.sleep(min(0.05, remain))


def _wait_event_until(
    event: threading.Event,
    deadline: float,
    *,
    stop_requested: Callable[[], bool],
) -> bool:
    """Event 또는 deadline 중 먼저 — stop 이면 ``False``."""
    end = float(deadline)
    while True:
        if stop_requested():
            return False
        remain = end - time.monotonic()
        if remain <= 1e-6:
            return event.is_set()
        if event.wait(timeout=min(0.05, remain)):
            return True


def _run_play_start_preflight_timeline(
    *,
    stop_requested: Callable[[], bool],
    kickoff_camera: Callable[[threading.Event], bool],
    planned_camera_sec: Callable[[], float],
    kickoff_prim_hide: Callable[[threading.Event], bool],
    planned_prim_hide_sec: Callable[[], float],
    on_before_prim_hide: Optional[Callable[[], None]] = None,
    log_tag: str = "",
) -> bool:
    """화면별 Play preflight 공통 타임라인 (카메라 → prim hide/show → CSV 시작 대기)."""
    if stop_requested():
        return False

    delay_cp = _delay_camera_to_prim_hide_sec()
    delay_pp = _delay_prim_hide_to_play_sec()
    tag = f" {log_tag}" if log_tag else ""

    cam_done = threading.Event()
    cam_kicked = kickoff_camera(cam_done)
    cam_planned = planned_camera_sec() if cam_kicked else 0.0

    # PLAY_HIDE / PLAY_SHOW 모두 fly **실제 완료** 이후에만 시작 (예정시간 겹침 없음)
    if cam_kicked:
        cam_wait_deadline = time.monotonic() + max(15.0, cam_planned + 12.0)
        if not _wait_event_until(
            cam_done,
            cam_wait_deadline,
            stop_requested=stop_requested,
        ):
            print(f"{_PRINT_PREFIX}{tag} preflight aborted (camera wait)", flush=True)
            return False
        if stop_requested():
            return False
        extra = max(0.0, float(delay_cp))
        prim_start = time.monotonic() + extra
        print(
            f"{_PRINT_PREFIX}{tag} prim hide/show @ camera end"
            + (f" + {extra:.2f}s" if extra > 1e-9 else " (immediate)"),
            flush=True,
        )
    else:
        extra = max(0.0, float(delay_cp))
        prim_start = time.monotonic() + extra
        if extra > 1e-9:
            print(
                f"{_PRINT_PREFIX}{tag} prim hide/show @ t0 + {extra:.2f}s (no camera fly)",
                flush=True,
            )

    if not _sleep_until(prim_start, stop_requested=stop_requested):
        print(f"{_PRINT_PREFIX}{tag} preflight aborted (before prim hide)", flush=True)
        return False

    if callable(on_before_prim_hide):
        try:
            on_before_prim_hide()
        except Exception as exc:
            print(f"{_PRINT_PREFIX}{tag} before prim hide hook: {exc}", flush=True)

    prim_done = threading.Event()
    # kickoff 시점에 PLAY_HIDE + PLAY_SHOW 적용 (lam_play_prim_hide)
    prim_kicked = kickoff_prim_hide(prim_done)
    prim_planned = planned_prim_hide_sec() if prim_kicked else 0.0
    # prim_start 를 CSV 스케줄 기준으로 고정 (아래 delay_pp<=0 분기)
    prim_phase_t0 = prim_start

    if prim_kicked and delay_pp > 0.0:
        prim_wait_deadline = time.monotonic() + max(20.0, prim_planned + 15.0)
        if not _wait_event_until(
            prim_done,
            prim_wait_deadline,
            stop_requested=stop_requested,
        ):
            print(f"{_PRINT_PREFIX}{tag} preflight aborted (prim hide wait)", flush=True)
            return False
        if stop_requested():
            return False
        csv_start = time.monotonic() + delay_pp
        print(
            f"{_PRINT_PREFIX}{tag} CSV @ prim hide end + {delay_pp:.2f}s",
            flush=True,
        )
    else:
        csv_start = prim_phase_t0 + prim_planned + delay_pp
        if prim_kicked and delay_pp < 0.0:
            print(
                f"{_PRINT_PREFIX}{tag} CSV @ prim start + {prim_planned:.2f}s"
                f"{delay_pp:+.2f}s (overlap hide)",
                flush=True,
            )

    if not _sleep_until(csv_start, stop_requested=stop_requested):
        print(f"{_PRINT_PREFIX}{tag} preflight aborted (before CSV)", flush=True)
        return False

    print(
        f"{_PRINT_PREFIX}{tag} preflight done "
        f"(cam_planned={cam_planned:.2f}s delay_cp={delay_cp:+.2f}s "
        f"prim_planned={prim_planned:.2f}s delay_pp={delay_pp:+.2f}s)",
        flush=True,
    )
    return True


def run_screen_play_start_preflight(runtime: Any, settings: dict) -> bool:
    """Play preflight — 화면1·2 공통 단일 경로 (viewport/context/settings 만 다름)."""
    from .lam_csv_screen_runtime import (
        apply_top_view_for_screen,
        sync_play_prim_hide_checkbox_after_play_start,
    )
    from .lam_play_camera_fly import (
        kickoff_play_camera_fly_for_screen,
        planned_camera_fly_duration_sec,
    )
    from .lam_play_prim_hide import (
        kickoff_play_prim_hide_play_start,
        planned_play_prim_hide_duration_sec,
    )
    from .simulation_play import csv_playback_stop_requested

    si = max(1, int(getattr(runtime, "screen", 1) or 1))
    ctx = str(getattr(runtime, "context_name", None) or "").strip()
    need_cam = bool(settings.get("play_camera_fly", True))
    vp_api = getattr(runtime, "viewport_api", None)
    if vp_api is None and si <= 1:
        try:
            from .lam_play_camera_fly import _get_active_viewport_api

            vp_api = _get_active_viewport_api()
        except Exception:
            vp_api = None

    # lot→FOUP 개수는 여기서 확정하되 visibility 는 fly 이후 prim hide 와 같이 적용.
    # Federation 파싱 단계가 저장한 authoritative 값이 있으면 dwells 재판정보다 우선한다.
    foup_count = 1
    try:
        from .lam_foup_usage_hide import count_used_foups_from_dwells

        csv_win = getattr(runtime, "csv_window", None)
        cached = (
            getattr(csv_win, "_prepared_playback", None) if csv_win is not None else None
        )
        dwells = getattr(cached, "dwells", None) if cached is not None else None
        saved_count = (
            getattr(cached, "used_foup_count", None) if cached is not None else None
        )
        if saved_count is None and csv_win is not None:
            saved_count = getattr(csv_win, "_lam_used_foup_count", None)
        if saved_count is not None:
            foup_count = max(1, min(3, int(saved_count)))
        else:
            foup_count = count_used_foups_from_dwells(dwells)
        print(
            f"{_PRINT_PREFIX} screen{si} FOUP count 확정={foup_count} "
            f"source={'parsed' if saved_count is not None else 'dwells'}",
            flush=True,
        )
    except Exception as exc:
        print(
            f"{_PRINT_PREFIX} screen{si} foup usage count: {exc}",
            flush=True,
        )

    def _stop() -> bool:
        return bool(csv_playback_stop_requested(screen=si))

    # 시뮬레이션 시작 직전 (카메라 FLY 시작 전):
    # 이전 실행/UI 체크로 켜져 있던 show_specs 가 FLY 도중에 노출되지 않도록 사전에 숨김 처리.
    # (카메라 FLY 완료 후 _kickoff_prim_hide 단계에서 정상적으로 show 됨)
    try:
        from .lam_play_prim_hide import apply_play_prim_hide_phase_for_context

        apply_play_prim_hide_phase_for_context(ctx or "", "pre_play_fly")
    except Exception as exc:
        print(f"{_PRINT_PREFIX} screen{si} pre_play_fly hide: {exc}", flush=True)

    def _kickoff_camera(done: threading.Event) -> bool:
        if not need_cam:
            done.set()
            return False
        if vp_api is None:
            print(
                f"{_PRINT_PREFIX} screen{si} camera fly skip — viewport_api 미준비 "
                "(prim hide 는 계속)",
                flush=True,
            )
            done.set()
            return False
        started = kickoff_play_camera_fly_for_screen(
            done,
            viewport_api=vp_api,
            usd_context_name=ctx,
        )
        if not started:
            print(
                f"{_PRINT_PREFIX} screen{si} play camera fly kickoff 실패 "
                f"— bind fallback 생략 (fly 없이 snap 하지 않음)",
                flush=True,
            )
        return bool(started)

    def _kickoff_prim_hide(done: threading.Event) -> bool:
        # fly 이후 같은 phase 에서 FOUP visibility 와 PLAY_HIDE_PRIM_SPECS 적용.
        # foup_count 를 명시해 빈/교체된 dwells 때문에 N=1로 재판정되는 것을 막는다.
        try:
            from .lam_foup_usage_hide import apply_foup_usage_extra_hide_for_playback

            apply_foup_usage_extra_hide_for_playback(
                foup_count=foup_count,
                usd_context_name=ctx or None,
                screen=si,
                wait=True,
            )
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} screen{si} foup usage hide: {exc}",
                flush=True,
            )
        return kickoff_play_prim_hide_play_start(
            done,
            usd_context_name=ctx or None,
            on_hide_complete=lambda: sync_play_prim_hide_checkbox_after_play_start(
                screen=si,
                csv_window=getattr(runtime, "csv_window", None),
            ),
        )

    def _before_prim_hide() -> None:
        if settings.get("top_view") and not settings.get("play_camera_fly"):
            apply_top_view_for_screen(runtime, enabled=True, force=True)

    return _run_play_start_preflight_timeline(
        stop_requested=_stop,
        kickoff_camera=_kickoff_camera,
        planned_camera_sec=planned_camera_fly_duration_sec,
        kickoff_prim_hide=_kickoff_prim_hide,
        planned_prim_hide_sec=planned_play_prim_hide_duration_sec,
        on_before_prim_hide=_before_prim_hide,
        log_tag=f"screen{si}",
    )


def run_play_start_preflight(*, resume_from_pause: bool) -> bool:
    """화면1 호환 wrapper — runtime 을 만들어 공통 경로로 위임."""
    if resume_from_pause:
        return True
    settings = {
        "play_camera_fly": True,
        "top_view": False,
        "play_prim_hide": True,
    }
    try:
        from .lam_viewport_overlay_state import (
            get_toggle_play_camera_fly,
            get_toggle_top_view,
        )

        settings["play_camera_fly"] = bool(get_toggle_play_camera_fly())
        settings["top_view"] = bool(get_toggle_top_view())
    except Exception:
        pass

    class _Rt:
        screen = 1
        context_name = ""
        viewport_api = None
        csv_window = None

    try:
        from .lam_play_camera_fly import _get_active_viewport_api

        _Rt.viewport_api = _get_active_viewport_api()
    except Exception:
        pass
    return run_screen_play_start_preflight(_Rt(), settings)


def run_aux_screen_play_start_preflight(runtime: Any, settings: dict) -> bool:
    """화면2+ 호환 alias — 공통 ``run_screen_play_start_preflight``."""
    return run_screen_play_start_preflight(runtime, settings)


def _read_bool_model(m: Any) -> bool:
    if m is None:
        return False
    try:
        return bool(m.get_value_as_bool())
    except Exception:
        pass
    try:
        return bool(m.as_bool)
    except Exception:
        pass
    try:
        return bool(m.get_value())
    except Exception:
        return False


def _reset_play_start_overlay_checkboxes(
    *,
    screen: int,
    csv_window: Any,
    lam_window: Any,
    runtime: Any,
) -> None:
    """시작 요청 직후 오버레이 체크 해제 (일시정지 이어서 재생 제외)."""
    if csv_window is None:
        return
    try:
        csv_window.ensure_playback_models()
    except Exception:
        pass

    read_bool = getattr(csv_window, "_read_bool_model", None)
    if not callable(read_bool):
        read_bool = _read_bool_model

    uses_global = bool(
        getattr(csv_window, "_uses_global_overlay_models", lambda: int(screen) <= 1)()
    )

    if uses_global:
        from .lam_viewport_overlay_state import (  # type: ignore
            get_toggle_play_prim_hide,
            set_toggle_device_labels,
            set_toggle_foup_status,
            set_toggle_play_prim_hide,
            set_toggle_top_view,
        )

        if get_toggle_play_prim_hide():
            set_toggle_play_prim_hide(False)
        set_toggle_foup_status(False)
        set_toggle_device_labels(False)
        # 장비배치도(Viewport)는 Play 중에도 유지 — 시뮬창 배치도와 동일 occupancy 갱신
        set_toggle_top_view(False)
    else:
        prim_m = getattr(csv_window, "_play_prim_hide_model", None)
        was_prim_hide = read_bool(prim_m)

        csv_window._overlay_checkbox_syncing = True
        try:
            for attr in (
                "_foup_status_show_model",
                "_device_labels_show_model",
                "_wafer_label_show_model",
                "_process_only_model",
                "_top_view_model",
            ):
                m = getattr(csv_window, attr, None)
                if m is not None:
                    try:
                        m.set_value(False)
                    except Exception:
                        pass
        finally:
            csv_window._overlay_checkbox_syncing = False

        if was_prim_hide:
            sync_prim_fn = getattr(csv_window, "sync_play_prim_hide_checkbox_ui", None)
            if callable(sync_prim_fn):
                sync_prim_fn(False)
            elif prim_m is not None:
                csv_window._overlay_checkbox_syncing = True
                try:
                    prim_m.set_value(False)
                except Exception:
                    pass
                finally:
                    csv_window._overlay_checkbox_syncing = False
            if runtime is not None:
                from .lam_csv_screen_runtime import apply_prim_hide_for_screen  # type: ignore

                apply_prim_hide_for_screen(runtime, enabled=False, force=True)
        if runtime is not None:
            from .lam_csv_screen_runtime import apply_top_view_for_screen  # type: ignore

            apply_top_view_for_screen(runtime, enabled=False, force=True)
        sync_fn = getattr(csv_window, "_request_screen_3d_overlay_sync", None)
        if callable(sync_fn):
            sync_fn()

    wl_m = getattr(csv_window, "_wafer_label_show_model", None)
    if wl_m is not None:
        try:
            wl_m.set_value(False)
        except Exception:
            pass
    apply_wafer = getattr(csv_window, "apply_wafer_label_visibility_from_ui", None)
    if callable(apply_wafer):
        try:
            apply_wafer(lam_window=lam_window)
        except Exception:
            pass
    po_fn = getattr(csv_window, "_schedule_live_process_only_changed", None)
    if callable(po_fn):
        try:
            po_fn(desired=False)
        except Exception:
            pass


def _run_play_start_request_standby_impl(
    lam_window: Any,
    screen: int,
    csv_window: Any = None,
) -> bool:
    from .lam_csv_screen_runtime import resolve_csv_screen_runtime  # type: ignore
    from .lam_play_camera_fly import (  # type: ignore
        _get_active_viewport_api,
        apply_play_camera_start_view_standby,
    )

    si = max(1, int(screen))
    if csv_window is None and lam_window is not None:
        sim_wins = getattr(lam_window, "_csv_sim_windows", {})
        if isinstance(sim_wins, dict):
            csv_window = sim_wins.get(si)
    runtime = resolve_csv_screen_runtime(
        lam_window,
        si,
        csv_window=csv_window,
        require_aux=False,
    )
    if csv_window is None and runtime is not None:
        csv_window = getattr(runtime, "csv_window", None)
    vp_api = None
    ctx = ""
    if runtime is not None:
        vp_api = runtime.viewport_api
        ctx = str(runtime.context_name or "")
    if si <= 1 and vp_api is None:
        vp_api = _get_active_viewport_api()
    cam_ok = apply_play_camera_start_view_standby(vp_api, ctx)
    _reset_play_start_overlay_checkboxes(
        screen=si,
        csv_window=csv_window,
        lam_window=lam_window,
        runtime=runtime,
    )
    print(
        f"{_PRINT_PREFIX} play start standby screen{si} cam_ok={cam_ok}",
        flush=True,
    )
    return cam_ok


def run_play_start_request_standby(
    lam_window: Any,
    screen: int,
    csv_window: Any = None,
) -> bool:
    """시작 요청 직후 — START_VIEW 대기 + 오버레이 체크 해제 (resume 제외 호출)."""
    result: list[bool] = [False]

    def _go() -> None:
        result[0] = _run_play_start_request_standby_impl(
            lam_window,
            screen,
            csv_window,
        )

    try:
        from .lam_sequence_engine import _dispatch_main_wait  # type: ignore

        _dispatch_main_wait(_go, timeout=8.0)
    except Exception as exc:
        print(
            f"{_PRINT_PREFIX} play start standby dispatch failed screen{screen}: {exc}",
            flush=True,
        )
        return False
    return bool(result[0])


def run_play_start_request_standby_for_screens(
    lam_window: Any,
    screens: list[int],
) -> None:
    """웹 Federation 등 — 화면별 standby (fetch/parse 전)."""
    sim_wins = getattr(lam_window, "_csv_sim_windows", {}) if lam_window else {}
    for raw in screens:
        si = max(1, int(raw))
        cw = sim_wins.get(si) if isinstance(sim_wins, dict) else None
        run_play_start_request_standby(lam_window, si, csv_window=cw)


__all__ = [
    "run_aux_screen_play_start_preflight",
    "run_play_start_preflight",
    "run_play_start_request_standby",
    "run_play_start_request_standby_for_screens",
    "run_screen_play_start_preflight",
]
