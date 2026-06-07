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


def _sleep_until(deadline: float) -> bool:
    """``True`` = deadline 까지 대기 완료, ``False`` = 일시정지·정지로 중단."""
    end = float(deadline)
    while True:
        if _playback_stop_requested():
            return False
        remain = end - time.monotonic()
        if remain <= 1e-6:
            return True
        time.sleep(min(0.05, remain))


def _wait_event_until(event: threading.Event, deadline: float) -> bool:
    """Event 또는 deadline 중 먼저 — stop 이면 ``False``."""
    end = float(deadline)
    while True:
        if _playback_stop_requested():
            return False
        remain = end - time.monotonic()
        if remain <= 1e-6:
            return event.is_set()
        if event.wait(timeout=min(0.05, remain)):
            return True


def run_play_start_preflight(*, resume_from_pause: bool) -> bool:
    """Play worker — CSV 재생 직전까지 타임라인 대기 (일시정지 이어서는 생략).

    ``False`` = 일시정지·정지로 preflight 중단(CSV 재생 생략).
    """
    if resume_from_pause:
        return True

    if _playback_stop_requested():
        return False

    from .lam_play_camera_fly import (  # type: ignore
        kickoff_play_camera_fly,
        planned_camera_fly_duration_sec,
    )
    from .lam_play_prim_hide import (  # type: ignore
        kickoff_play_prim_hide_play_start,
        planned_play_prim_hide_duration_sec,
    )

    delay_cp = _delay_camera_to_prim_hide_sec()
    delay_pp = _delay_prim_hide_to_play_sec()
    t0 = time.monotonic()

    cam_done = threading.Event()
    cam_kicked = kickoff_play_camera_fly(cam_done)
    cam_planned = planned_camera_fly_duration_sec() if cam_kicked else 0.0

    if cam_kicked and delay_cp > 0.0:
        cam_wait_deadline = time.monotonic() + max(15.0, cam_planned + 12.0)
        if not _wait_event_until(cam_done, cam_wait_deadline):
            print(f"{_PRINT_PREFIX} preflight aborted (camera wait)", flush=True)
            return False
        if _playback_stop_requested():
            return False
        prim_start = time.monotonic() + delay_cp
        print(
            f"{_PRINT_PREFIX} prim hide @ camera end + {delay_cp:.2f}s",
            flush=True,
        )
    else:
        prim_start = t0 + cam_planned + delay_cp
        if cam_kicked and delay_cp < 0.0:
            print(
                f"{_PRINT_PREFIX} prim hide @ t0+{cam_planned:.2f}s"
                f"{delay_cp:+.2f}s (overlap camera)",
                flush=True,
            )

    if not _sleep_until(prim_start):
        print(f"{_PRINT_PREFIX} preflight aborted (before prim hide)", flush=True)
        return False

    prim_done = threading.Event()
    prim_kicked = kickoff_play_prim_hide_play_start(prim_done)
    prim_planned = planned_play_prim_hide_duration_sec() if prim_kicked else 0.0

    if prim_kicked and delay_pp > 0.0:
        prim_wait_deadline = time.monotonic() + max(20.0, prim_planned + 15.0)
        if not _wait_event_until(prim_done, prim_wait_deadline):
            print(f"{_PRINT_PREFIX} preflight aborted (prim hide wait)", flush=True)
            return False
        if _playback_stop_requested():
            return False
        csv_start = time.monotonic() + delay_pp
        print(
            f"{_PRINT_PREFIX} CSV @ prim hide end + {delay_pp:.2f}s",
            flush=True,
        )
    else:
        csv_start = prim_start + prim_planned + delay_pp
        if prim_kicked and delay_pp < 0.0:
            print(
                f"{_PRINT_PREFIX} CSV @ prim start + {prim_planned:.2f}s"
                f"{delay_pp:+.2f}s (overlap hide)",
                flush=True,
            )

    if not _sleep_until(csv_start):
        print(f"{_PRINT_PREFIX} preflight aborted (before CSV)", flush=True)
        return False

    print(
        f"{_PRINT_PREFIX} preflight done "
        f"(cam_planned={cam_planned:.2f}s delay_cp={delay_cp:+.2f}s "
        f"prim_planned={prim_planned:.2f}s delay_pp={delay_pp:+.2f}s)",
        flush=True,
    )
    return True


__all__ = ["run_play_start_preflight"]
