# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
usd_animation_control.py — USD 내장 타임라인(프레임) 재생

【역할】
- omni.timeline으로 저장된 애니메이션 구간 재생, 완료 콜백, 프레임 범위 자동 감지.

【수정 가이드】
- 재생/일시정지/루프 정책: play_usd_animation_range 등
- 프레임 범위 추정: resolve_saved_animation_frame_range
- 시퀀스 USD_TIMELINE 스텝: sequence_engine 이 본 모듈 호출

사용처: control_window, sequence_engine

【유지보수 시나리오】
1) "USD 타임라인 스텝의 시작/종료 프레임 정책" 변경
   - 본 파일: play_usd_animation* / resolve_saved_animation_frame_range
   - sequence_engine.py: USD_TIMELINE 분기(_start_step)와 동기화
2) "루프/완료 콜백 타이밍" 변경
   - 본 파일의 _loop_sub/_complete_sub 관리 로직 수정
   - control_window.py 수동 재생 버튼 동작(on_play_usd_animation) 확인
3) "프레임 단위 -> 시간 단위" 정책 변경
   - frame_to_time / time_to_frame 사용부 전역 검색
"""

from typing import Optional, Callable

_end_fix_sub = None
_loop_sub = None
_complete_sub = None

# 프로젝트 정책: 모든 애니메이션은 30fps(TPS) 기반.
# 타임라인 인터페이스가 없거나 TPS를 얻지 못하는 예외 경로에서도 일관되게 30을 사용한다.
DEFAULT_TPS = 30.0


def _get_timeline():
    try:
        import omni.timeline
        return omni.timeline.get_timeline_interface()
    except Exception:
        return None


def reset_timeline_to_zero() -> None:
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
    tl = _get_timeline()
    if not tl:
        return frame / DEFAULT_TPS
    tps = tl.get_time_codes_per_seconds()
    return frame / float(tps) if tps else frame / DEFAULT_TPS


def time_to_frame(time_sec: float) -> float:
    tl = _get_timeline()
    if not tl:
        return time_sec * DEFAULT_TPS
    tps = tl.get_time_codes_per_seconds()
    return time_sec * float(tps) if tps else time_sec * DEFAULT_TPS


def play_usd_animation(
    start_frame: int = 200,
    end_frame: int = 300,
    loop: bool = False,
    on_completed: Optional[Callable[[], None]] = None,
) -> bool:
    global _loop_sub, _complete_sub, _end_fix_sub
    tl = _get_timeline()
    if not tl:
        return False
    try:
        tps = tl.get_time_codes_per_seconds()
        if not tps:
            tps = DEFAULT_TPS
        start_time = start_frame / float(tps)
        end_time = end_frame / float(tps)
        if start_time >= end_time:
            return False
        tl.set_start_time(start_time)
        tl.set_end_time(end_time)
        tl.set_current_time(start_time)
        tl.play()

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
                    name="morph.tbs_control_1:usd_animation_loop",
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
                        tl.pause()
                        global _complete_sub
                        if _complete_sub is not None:
                            try:
                                _complete_sub.unsubscribe()
                            except Exception:
                                pass
                            _complete_sub = None
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
                                name="morph.tbs_control_1:usd_animation_end_fix",
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
                    name="morph.tbs_control_1:usd_animation_complete",
                )
            except Exception:
                _complete_sub = None
        return True
    except Exception:
        return False


def stop_usd_animation() -> None:
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
    tl = _get_timeline()
    return bool(tl and tl.is_playing())
