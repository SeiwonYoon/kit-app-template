"""CSV Play 시작 전 오케스트레이션 — 카메라 fly · prim 숨김 · 재생 사이 delay.

기준 (lam_viewport_overlay_config):
- prim 숨김 시작: (카메라 fly **끝**) + PLAY_DELAY_CAMERA_TO_PRIM_HIDE_SEC
  - fly 미실행/스킵 시 카메라 끝 = 타임라인 t0
  - delay <= 0: 예정 fly duration 기준으로 스케줄(겹침)
  - delay > 0: fly **실제 완료** 후 delay 만큼 대기
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
    """화면별 Play preflight 공통 타임라인 (카메라 → prim hide → CSV 시작 대기)."""
    if stop_requested():
        return False

    delay_cp = _delay_camera_to_prim_hide_sec()
    delay_pp = _delay_prim_hide_to_play_sec()
    t0 = time.monotonic()
    tag = f" {log_tag}" if log_tag else ""

    cam_done = threading.Event()
    cam_kicked = kickoff_camera(cam_done)
    cam_planned = planned_camera_sec() if cam_kicked else 0.0

    if cam_kicked and delay_cp > 0.0:
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
        prim_start = time.monotonic() + delay_cp
        print(
            f"{_PRINT_PREFIX}{tag} prim hide @ camera end + {delay_cp:.2f}s",
            flush=True,
        )
    else:
        prim_start = t0 + cam_planned + delay_cp
        if cam_kicked and delay_cp < 0.0:
            print(
                f"{_PRINT_PREFIX}{tag} prim hide @ t0+{cam_planned:.2f}s"
                f"{delay_cp:+.2f}s (overlap camera)",
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
    prim_kicked = kickoff_prim_hide(prim_done)
    prim_planned = planned_prim_hide_sec() if prim_kicked else 0.0

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
        csv_start = prim_start + prim_planned + delay_pp
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


def run_play_start_preflight(*, resume_from_pause: bool) -> bool:
    """Play worker — CSV 재생 직전까지 타임라인 대기 (일시정지 이어서는 생략).

    ``False`` = 일시정지·정지로 preflight 중단(CSV 재생 생략).
    """
    if resume_from_pause:
        return True

    from .lam_play_camera_fly import (  # type: ignore
        kickoff_play_camera_fly,
        planned_camera_fly_duration_sec,
    )
    from .lam_play_prim_hide import (  # type: ignore
        kickoff_play_prim_hide_play_start,
        planned_play_prim_hide_duration_sec,
    )
    from .lam_csv_screen_runtime import sync_play_prim_hide_checkbox_after_play_start

    def _kickoff_prim_hide(done: threading.Event) -> bool:
        return kickoff_play_prim_hide_play_start(
            done,
            on_hide_complete=lambda: sync_play_prim_hide_checkbox_after_play_start(
                screen=1,
            ),
        )

    return _run_play_start_preflight_timeline(
        stop_requested=_playback_stop_requested,
        kickoff_camera=kickoff_play_camera_fly,
        planned_camera_sec=planned_camera_fly_duration_sec,
        kickoff_prim_hide=_kickoff_prim_hide,
        planned_prim_hide_sec=planned_play_prim_hide_duration_sec,
    )


def run_aux_screen_play_start_preflight(runtime: Any, settings: dict) -> bool:
    """화면2+ Play preflight — 화면1 과 동일 타임라인, context·체크박스만 화면별."""
    from .lam_csv_screen_runtime import (
        apply_top_view_for_screen,
        bind_viewport_camera_for_screen,
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

    si = max(2, int(getattr(runtime, "screen", 2) or 2))
    ctx = str(getattr(runtime, "context_name", None) or "").strip()
    need_cam = bool(settings.get("play_camera_fly"))
    vp_api = getattr(runtime, "viewport_api", None)

    def _stop() -> bool:
        return bool(csv_playback_stop_requested(screen=si))

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
            bind_ok = bind_viewport_camera_for_screen(runtime, "play_camera")
            print(
                f"{_PRINT_PREFIX} screen{si} play camera fly kickoff 실패 "
                f"— bind fallback ok={bind_ok}",
                flush=True,
            )
        return bool(started)

    def _kickoff_prim_hide(done: threading.Event) -> bool:
        return kickoff_play_prim_hide_play_start(
            done,
            usd_context_name=ctx,
            on_hide_complete=lambda: sync_play_prim_hide_checkbox_after_play_start(
                screen=si,
                csv_window=getattr(runtime, "csv_window", None),
            ),
        )

    def _before_prim_hide() -> None:
        if settings.get("top_view") and not settings.get("play_camera_fly"):
            apply_top_view_for_screen(runtime, enabled=True)

    return _run_play_start_preflight_timeline(
        stop_requested=_stop,
        kickoff_camera=_kickoff_camera,
        planned_camera_sec=planned_camera_fly_duration_sec,
        kickoff_prim_hide=_kickoff_prim_hide,
        planned_prim_hide_sec=planned_play_prim_hide_duration_sec,
        on_before_prim_hide=_before_prim_hide,
        log_tag=f"screen{si}",
    )


__all__ = [
    "run_aux_screen_play_start_preflight",
    "run_play_start_preflight",
]
