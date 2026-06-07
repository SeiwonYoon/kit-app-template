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


def _sleep_until(deadline: float) -> None:
    remain = float(deadline) - time.monotonic()
    if remain > 1e-6:
        time.sleep(remain)


def run_play_start_preflight(*, resume_from_pause: bool) -> None:
    """Play worker — CSV 재생 직전까지 타임라인 대기 (일시정지 이어서는 생략)."""
    if resume_from_pause:
        return

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
        cam_done.wait(timeout=max(15.0, cam_planned + 12.0))
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

    _sleep_until(prim_start)

    prim_done = threading.Event()
    prim_kicked = kickoff_play_prim_hide_play_start(prim_done)
    prim_planned = planned_play_prim_hide_duration_sec() if prim_kicked else 0.0

    if prim_kicked and delay_pp > 0.0:
        prim_done.wait(timeout=max(20.0, prim_planned + 15.0))
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

    _sleep_until(csv_start)

    print(
        f"{_PRINT_PREFIX} preflight done "
        f"(cam_planned={cam_planned:.2f}s delay_cp={delay_cp:+.2f}s "
        f"prim_planned={prim_planned:.2f}s delay_pp={delay_pp:+.2f}s)",
        flush=True,
    )


__all__ = ["run_play_start_preflight"]
