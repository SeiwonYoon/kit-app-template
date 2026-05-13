"""LAM — USD_TIMELINE 테스트용 `omni.timeline` 재생 (TBS `play_usd_animation` 축약판).

- `morph.tbs_control_1` 는 import 하지 않는다 (REQ-002).
- **main thread** 에서만 호출할 것 (`lam_sequence_engine._dispatch_main_wait` 경유).
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

_DEFAULT_TPS = 30.0


def _get_timeline() -> Any:
    try:
        import omni.timeline as ot  # type: ignore

        return ot.get_timeline_interface()
    except Exception:
        return None


def _try_set_timeline_speed(tl: Any, speed_scale: float) -> Optional[float]:
    """Kit 버전별 배속 API best-effort. 성공 시 이전 값, 실패 시 None."""
    if tl is None:
        return None
    try:
        s = float(max(0.01, min(100.0, float(speed_scale))))
    except Exception:
        s = 1.0
    for get_nm, set_nm in (
        ("get_time_scale", "set_time_scale"),
        ("get_playback_rate", "set_playback_rate"),
        ("get_speed", "set_speed"),
    ):
        try:
            g = getattr(tl, get_nm, None)
            f = getattr(tl, set_nm, None)
            if callable(f):
                prev = float(g()) if callable(g) else None
                f(float(s))
                if callable(g):
                    try:
                        cur = float(g())
                        if abs(cur - float(s)) <= 1e-4:
                            return prev
                    except Exception:
                        pass
        except Exception:
            continue
    return None


def _restore_timeline_speed(tl: Any, prev: Optional[float]) -> None:
    if tl is None or prev is None:
        return
    _try_set_timeline_speed(tl, float(prev))


def begin_play_frame_range(
    *,
    start_frame: float,
    end_frame: float,
    speed_scale: float = 1.0,
    fps: float = _DEFAULT_TPS,
) -> bool:
    """master 의 omni.timeline 으로 [start_frame, end_frame) 구간 재생 시작.

    Returns:
        성공 여부. 실패 시 호출자가 `end_play_pause()` 로 정리해도 무방.
    """
    tl = _get_timeline()
    if tl is None:
        return False
    try:
        tps = float(tl.get_time_codes_per_seconds())  # type: ignore[attr-defined]
    except Exception:
        tps = float(fps)
    if not tps or tps <= 0:
        tps = float(fps)
    try:
        sf = float(start_frame)
        ef = float(end_frame)
    except Exception:
        return False
    if ef <= sf:
        return False
    start_time = sf / tps
    end_time = ef / tps
    if start_time >= end_time:
        return False

    try:
        tl.pause()  # type: ignore[attr-defined]
    except Exception:
        pass

    for setter_name, value in (
        ("set_time_codes_per_second", float(fps)),
        ("set_target_framerate", float(fps)),
    ):
        fn = getattr(tl, setter_name, None)
        if callable(fn):
            try:
                fn(float(value))
            except Exception:
                pass

    _try_set_timeline_speed(tl, float(speed_scale))

    try:
        tl.set_start_time(float(start_time))  # type: ignore[attr-defined]
        tl.set_end_time(float(end_time))  # type: ignore[attr-defined]
        tl.set_current_time(float(start_time))  # type: ignore[attr-defined]
    except Exception:
        return False
    try:
        tl.play()  # type: ignore[attr-defined]
    except Exception:
        return False
    return True


def end_play_pause() -> None:
    """재생 중지 + 배속 복구는 호출자가 `speed_prev_holder` 로 처리할 수 없으므로
    pause 만 수행. 배속은 다음 `begin_play_frame_range` 전에 다시 set 된다."""
    tl = _get_timeline()
    if tl is None:
        return
    try:
        tl.pause()  # type: ignore[attr-defined]
    except Exception:
        pass


def snapshot_timeline() -> Tuple[Any, float, bool, Optional[float]]:
    """(timeline, saved_time, was_playing, prev_speed_or_None) — begin 전 스냅샷."""
    tl = _get_timeline()
    saved = 0.0
    playing = False
    prev_sp: Optional[float] = None
    if tl is None:
        return tl, saved, playing, prev_sp
    try:
        saved = float(tl.get_current_time())  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        playing = bool(tl.is_playing())  # type: ignore[attr-defined]
    except Exception:
        pass
    if playing:
        try:
            tl.pause()  # type: ignore[attr-defined]
        except Exception:
            pass
    for get_nm in ("get_time_scale", "get_playback_rate", "get_speed"):
        g = getattr(tl, get_nm, None)
        if callable(g):
            try:
                prev_sp = float(g())
                break
            except Exception:
                continue
    return tl, saved, playing, prev_sp


def restore_timeline_after_usd_timeline(
    tl: Any,
    saved_time: float,
    was_playing: bool,
    prev_speed: Optional[float],
) -> None:
    """USD_TIMELINE step 종료 후 Kit 타임라인 상태 best-effort 복구.

    2026-05-13 — 순서 수정: ``set_current_time`` 을 ``pause`` 보다 **먼저** 호출.
    이전에는 (pause → speed restore → set_current_time) 순서였는데, pause 직후 다음
    set_current_time 사이에 Hydra 가 한 frame 을 그릴 때 OmniGraph 가 ``time=0`` (혹은
    기본 포즈) 로 평가되어 prim 이 "원래위치로 튀었다 끝난 지점으로 복귀" 하는 시각
    artifact 가 발생했다 (사용자 보고 2026-05-13). 시각 갱신 순서를 (set_current_time
    → pause) 로 뒤집어 timeline 이 목표 시각으로 먼저 이동한 뒤 정지하도록 한다.
    """
    if tl is None:
        return
    try:
        tl.set_current_time(float(saved_time))  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        tl.pause()  # type: ignore[attr-defined]
    except Exception:
        pass
    _restore_timeline_speed(tl, prev_speed)
    if was_playing:
        try:
            tl.play()  # type: ignore[attr-defined]
        except Exception:
            pass
