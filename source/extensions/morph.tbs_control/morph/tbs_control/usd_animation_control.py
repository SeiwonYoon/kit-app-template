# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
USD 파일 내장 애니메이션(타임라인) 재생 제어.
curve_editor / timeline 으로 USD에 추가한 애니메이션을 프레임 구간(예: 200~300) 재생, 루프/1회 제어.
"""

from typing import Optional, Callable

# end 프레임 고정(깜빡임 완화)용
_end_fix_sub = None
# 재생 구간 루프 시 구독 해제용
_loop_sub = None
# 1회 재생 완료 감지용
_complete_sub = None


def _get_timeline():
    """omni.timeline 인터페이스 반환. 없으면 None."""
    try:
        import omni.timeline
        return omni.timeline.get_timeline_interface()
    except Exception:
        return None


def reset_timeline_to_zero() -> None:
    """
    타임라인을 0으로 초기화(pause + current_time=0).
    Sequence 실행 시 "항상 0에서 시작"을 보장하기 위한 유틸.
    """
    tl = _get_timeline()
    if not tl:
        return
    try:
        tl.pause()
    except Exception:
        pass
    try:
        tl.set_current_time(0.0)
    except Exception:
        pass


def resolve_saved_animation_frame_range() -> Optional[tuple]:
    """
    저장된(USD/Stage) 애니메이션의 프레임 범위를 자동으로 추정.

    우선순위:
    1) Stage의 Start/EndTimeCode
    2) Timeline의 start/end time (API가 존재하는 경우)

    반환: (start_frame:int, end_frame:int) 또는 None
    """
    # 1) Stage timeCode (가장 일반적)
    try:
        import omni.usd as ou
        ctx = ou.get_context()
        stage = ctx.get_stage() if ctx else None
        if stage:
            s = float(stage.GetStartTimeCode())
            e = float(stage.GetEndTimeCode())
            if e > s:
                return (int(round(s)), int(round(e)))
    except Exception:
        pass

    # 2) Timeline start/end time (초 단위) → frame 변환
    tl = _get_timeline()
    if tl:
        try:
            get_start = getattr(tl, "get_start_time", None)
            get_end = getattr(tl, "get_end_time", None)
            if callable(get_start) and callable(get_end):
                s_t = float(get_start())
                e_t = float(get_end())
                if e_t > s_t:
                    return (int(round(time_to_frame(s_t))), int(round(time_to_frame(e_t))))
        except Exception:
            pass
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
    on_completed: Optional[Callable[[], None]] = None,
) -> bool:
    """
    USD 타임라인 애니메이션 재생. start_frame ~ end_frame 구간만 재생.
    loop=True 이면 구간 끝에서 처음으로 되돌려 반복.
    """
    global _loop_sub, _complete_sub, _end_fix_sub
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

        # 이전 완료 감지 구독 정리
        if _complete_sub is not None:
            try:
                _complete_sub.unsubscribe()
            except Exception:
                pass
            _complete_sub = None
        if _end_fix_sub is not None:
            try:
                _end_fix_sub.unsubscribe()
            except Exception:
                pass
            _end_fix_sub = None

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

            # 1회 재생: end_time 도달 시 pause + 콜백 호출
            try:
                import omni.timeline as ot
                ticked = getattr(ot.TimelineEventType, "CURRENT_TIME_TICKED", None)
                ticked_val = ticked.value if ticked is not None else 0
            except Exception:
                ticked_val = 0

            def _on_complete(event):
                try:
                    if getattr(event, "type", None) != ticked_val:
                        return
                    if not tl.is_playing():
                        return
                    t = tl.get_current_time()
                    if t >= end_time - 1e-6:
                        # 일부 환경에서 같은 tick에 current_time을 고정하면 순간적으로 되감기는 느낌이 있어,
                        # pause 후 "다음 프레임(post_update)"에 end_time으로 고정한다.
                        tl.pause()
                        # 구독 해제 후 콜백 호출 (중복 호출 방지)
                        global _complete_sub
                        if _complete_sub is not None:
                            try:
                                _complete_sub.unsubscribe()
                            except Exception:
                                pass
                            _complete_sub = None
                        # 다음 프레임에서 end_time 고정
                        global _end_fix_sub
                        try:
                            import omni.kit.app as app

                            def _fix(_e=None):
                                global _end_fix_sub
                                try:
                                    tl.set_current_time(end_time)
                                except Exception:
                                    pass
                                if _end_fix_sub is not None:
                                    try:
                                        _end_fix_sub.unsubscribe()
                                    except Exception:
                                        pass
                                    _end_fix_sub = None

                            _end_fix_sub = app.get_app().get_post_update_event_stream().create_subscription_to_pop(
                                _fix,
                                name="morph.tbs_control:usd_animation_end_fix",
                            )
                        except Exception:
                            pass
                        if on_completed:
                            try:
                                on_completed()
                            except Exception:
                                pass
                except Exception:
                    pass

            try:
                stream = tl.get_timeline_event_stream()
                _complete_sub = stream.create_subscription_to_pop(
                    _on_complete,
                    name="morph.tbs_control:usd_animation_complete",
                )
            except Exception:
                _complete_sub = None
        return True
    except Exception:
        return False


def stop_usd_animation() -> None:
    """USD 타임라인 재생 중지 및 루프 구독 해제."""
    global _loop_sub, _complete_sub, _end_fix_sub
    if _loop_sub is not None:
        try:
            _loop_sub.unsubscribe()
        except Exception:
            pass
        _loop_sub = None
    if _complete_sub is not None:
        try:
            _complete_sub.unsubscribe()
        except Exception:
            pass
        _complete_sub = None
    if _end_fix_sub is not None:
        try:
            _end_fix_sub.unsubscribe()
        except Exception:
            pass
        _end_fix_sub = None
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
