# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
USD 파일 내장 애니메이션(타임라인) 재생 제어.
curve_editor / timeline 으로 USD에 추가한 애니메이션을 프레임 구간(예: 200~300) 재생, 루프/1회 제어.
"""

from typing import Optional

# 재생 구간 루프 시 구독 해제용
_loop_sub = None


def _get_timeline():
    """omni.timeline 인터페이스 반환. 없으면 None."""
    try:
        import omni.timeline
        return omni.timeline.get_timeline_interface()
    except Exception:
        return None


def frame_to_time(frame: float) -> float:
    """프레임 → 시간(초). tps 기준."""
    tl = _get_timeline()
    if not tl:
        return frame / 24.0
    tps = tl.get_time_codes_per_seconds()
    return frame / float(tps) if tps else frame / 24.0


def time_to_frame(time_sec: float) -> float:
    """시간(초) → 프레임."""
    tl = _get_timeline()
    if not tl:
        return time_sec * 24.0
    tps = tl.get_time_codes_per_seconds()
    return time_sec * float(tps) if tps else time_sec * 24.0


def play_usd_animation(
    start_frame: int = 200,
    end_frame: int = 300,
    loop: bool = False,
) -> bool:
    """
    USD 타임라인 애니메이션 재생. start_frame ~ end_frame 구간만 재생.
    loop=True 이면 구간 끝에서 처음으로 되돌려 반복.
    """
    global _loop_sub
    tl = _get_timeline()
    if not tl:
        return False
    try:
        tps = tl.get_time_codes_per_seconds()
        if not tps:
            tps = 24.0
        start_time = start_frame / float(tps)
        end_time = end_frame / float(tps)
        if start_time >= end_time:
            return False
        tl.set_start_time(start_time)
        tl.set_end_time(end_time)
        tl.set_current_time(start_time)
        tl.play()

        if loop:
            if _loop_sub is not None:
                try:
                    _loop_sub.unsubscribe()
                except Exception:
                    pass
                _loop_sub = None

            try:
                import omni.timeline as ot
                ticked = getattr(ot.TimelineEventType, "CURRENT_TIME_TICKED", None)
                ticked_val = ticked.value if ticked is not None else 0
            except Exception:
                ticked_val = 0

            def _on_tick(event):
                try:
                    if getattr(event, "type", None) != ticked_val:
                        return
                    if not tl.is_playing():
                        return
                    t = tl.get_current_time()
                    if t >= end_time - 1e-6:
                        tl.set_current_time(start_time)
                except Exception:
                    pass

            try:
                stream = tl.get_timeline_event_stream()
                _loop_sub = stream.create_subscription_to_pop(
                    _on_tick,
                    name="morph.tbs_control:usd_animation_loop",
                )
            except Exception:
                pass
        else:
            if _loop_sub is not None:
                try:
                    _loop_sub.unsubscribe()
                except Exception:
                    pass
                _loop_sub = None
        return True
    except Exception:
        return False


def stop_usd_animation() -> None:
    """USD 타임라인 재생 중지 및 루프 구독 해제."""
    global _loop_sub
    if _loop_sub is not None:
        try:
            _loop_sub.unsubscribe()
        except Exception:
            pass
        _loop_sub = None
    tl = _get_timeline()
    if tl:
        try:
            tl.pause()
        except Exception:
            pass


def is_playing() -> bool:
    """타임라인이 재생 중인지."""
    tl = _get_timeline()
    return bool(tl and tl.is_playing())
