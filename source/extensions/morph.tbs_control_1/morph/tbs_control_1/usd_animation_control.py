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

from typing import Optional, Callable, Dict, Any, Tuple

_states: Dict[str, Dict[str, Any]] = {}

# 프로젝트 정책: 모든 애니메이션은 30fps(TPS) 기반.
# 타임라인 인터페이스가 없거나 TPS를 얻지 못하는 예외 경로에서도 일관되게 30을 사용한다.
DEFAULT_TPS = 30.0


def _timeline_key(usd_context_name: Optional[str]) -> str:
    return (usd_context_name or "").strip() or "default"


def _state_for(usd_context_name: Optional[str]) -> Dict[str, Any]:
    k = _timeline_key(usd_context_name)
    st = _states.get(k)
    if isinstance(st, dict):
        return st
    st = {
        "_end_fix_sub": None,
        "_loop_sub": None,
        "_complete_sub": None,
        "_speed_sub": None,
        "_play_token": 0,
    }
    _states[k] = st
    return st


def _get_timeline(usd_context_name: Optional[str] = None):
    try:
        import omni.timeline
        nm = (usd_context_name or "").strip()
        # Kit 버전에 따라 get_timeline_interface(name) 형태를 지원한다.
        try:
            if nm:
                return omni.timeline.get_timeline_interface(nm)
        except TypeError:
            pass
        except Exception:
            pass
        # 폴백: 전역 타임라인
        return omni.timeline.get_timeline_interface()
    except Exception:
        return None


def reset_timeline_to_zero(usd_context_name: Optional[str] = None) -> None:
    tl = _get_timeline(usd_context_name)
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


def resolve_saved_animation_frame_range(usd_context_name: Optional[str] = None) -> Optional[tuple]:
    try:
        import omni.usd as ou
        nm = (usd_context_name or "").strip()
        ctx = ou.get_context(nm) if nm else ou.get_context()
        stage = ctx.get_stage() if ctx else None
        if stage:
            s = float(stage.GetStartTimeCode())
            e = float(stage.GetEndTimeCode())
            if e > s:
                return (int(round(s)), int(round(e)))
    except Exception:
        pass
    tl = _get_timeline(usd_context_name)
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


def frame_to_time(frame: float, usd_context_name: Optional[str] = None) -> float:
    tl = _get_timeline(usd_context_name)
    if not tl:
        return frame / DEFAULT_TPS
    tps = tl.get_time_codes_per_seconds()
    return frame / float(tps) if tps else frame / DEFAULT_TPS


def time_to_frame(time_sec: float, usd_context_name: Optional[str] = None) -> float:
    tl = _get_timeline(usd_context_name)
    if not tl:
        return time_sec * DEFAULT_TPS
    tps = tl.get_time_codes_per_seconds()
    return time_sec * float(tps) if tps else time_sec * DEFAULT_TPS


def _try_set_timeline_speed(tl: Any, speed_scale: float) -> Optional[float]:
    """
    Kit 버전/타임라인 구현마다 속도 API가 달라 best-effort로 적용한다.
    Returns:
        이전 speed 값(복구용) 또는 None(복구 불가/미지원)
    """
    try:
        s = float(speed_scale)
    except Exception:
        s = 1.0
    s = max(0.01, min(100.0, s))
    if tl is None:
        return None
    # 후보 API들
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
                return prev
        except Exception:
            continue
    # 속성 기반(time_scale/playback_rate/speed)
    for attr in ("time_scale", "playback_rate", "speed"):
        try:
            prev = getattr(tl, attr, None)
            setattr(tl, attr, float(s))
            try:
                return float(prev) if isinstance(prev, (float, int)) else None
            except Exception:
                return None
        except Exception:
            continue
    return None


def play_usd_animation(
    start_frame: int = 200,
    end_frame: int = 300,
    loop: bool = False,
    on_completed: Optional[Callable[[], None]] = None,
    usd_context_name: Optional[str] = None,
    speed_scale: float = 1.0,
) -> bool:
    st = _state_for(usd_context_name)
    tl = _get_timeline(usd_context_name)
    if not tl:
        return False
    try:
        # 배속 적용(지원되는 경우에만). 완료/중단 시 복구 시도.
        prev_speed = _try_set_timeline_speed(tl, float(speed_scale))
        st["_play_token"] = int(st.get("_play_token", 0) or 0) + 1
        my_token = int(st["_play_token"])
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

        # 속도 API가 없는 환경(또는 적용 실패)에서는 current_time을 직접 가속해서 배속을 맞춘다.
        # - tl 자체는 1x로 재생되므로, 추가로 (sp-1)x 만큼 current_time을 더 밀어 총 sp가 되게 한다.
        try:
            if st.get("_speed_sub") is not None:
                try:
                    st["_speed_sub"].unsubscribe()
                except Exception:
                    pass
                st["_speed_sub"] = None
        except Exception:
            pass
        try:
            sp = float(speed_scale)
        except Exception:
            sp = 1.0
        if prev_speed is None and sp > 1.000001:
            try:
                import omni.kit.app as kit_app

                def _on_update(e):
                    try:
                        # 재생 토큰이 바뀌면(새 재생/stop) 즉시 종료
                        if int(st.get("_play_token", 0) or 0) != int(my_token):
                            if st.get("_speed_sub") is not None:
                                try:
                                    st["_speed_sub"].unsubscribe()
                                except Exception:
                                    pass
                                st["_speed_sub"] = None
                            return
                        if not tl.is_playing():
                            return
                        dt = 0.0
                        try:
                            dt = float(getattr(e, "payload", {}) or {}).get("dt", 0.0)  # type: ignore[union-attr]
                        except Exception:
                            dt = 0.0
                        if dt <= 0.0:
                            dt = 1.0 / 60.0
                        # 추가 가속분만큼 current_time을 더 전진시킨다.
                        extra = dt * max(0.0, sp - 1.0)
                        t = float(tl.get_current_time())
                        t2 = t + extra
                        if t2 >= end_time - 1e-6:
                            try:
                                tl.pause()
                            except Exception:
                                pass
                            try:
                                tl.set_current_time(end_time)
                            except Exception:
                                pass
                            if st.get("_speed_sub") is not None:
                                try:
                                    st["_speed_sub"].unsubscribe()
                                except Exception:
                                    pass
                                st["_speed_sub"] = None
                            if st.get("_complete_sub") is not None:
                                try:
                                    st["_complete_sub"].unsubscribe()
                                except Exception:
                                    pass
                                st["_complete_sub"] = None
                            if on_completed:
                                try:
                                    on_completed()
                                except Exception:
                                    pass
                            return
                        try:
                            tl.set_current_time(t2)
                        except Exception:
                            pass
                    except Exception:
                        pass

                st["_speed_sub"] = kit_app.get_app().get_update_event_stream().create_subscription_to_pop(
                    _on_update,
                    name="morph.tbs_control_1:usd_animation_speed_fallback",
                )
            except Exception:
                pass

        if st.get("_complete_sub") is not None:
            try:
                st["_complete_sub"].unsubscribe()
            except Exception:
                pass
            st["_complete_sub"] = None
        if st.get("_end_fix_sub") is not None:
            try:
                st["_end_fix_sub"].unsubscribe()
            except Exception:
                pass
            st["_end_fix_sub"] = None

        if loop:
            if st.get("_loop_sub") is not None:
                try:
                    st["_loop_sub"].unsubscribe()
                except Exception:
                    pass
                st["_loop_sub"] = None
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
                st["_loop_sub"] = stream.create_subscription_to_pop(
                    _on_tick,
                    name="morph.tbs_control_1:usd_animation_loop",
                )
            except Exception:
                pass
        else:
            if st.get("_loop_sub") is not None:
                try:
                    st["_loop_sub"].unsubscribe()
                except Exception:
                    pass
                st["_loop_sub"] = None
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
                        try:
                            if prev_speed is not None:
                                _try_set_timeline_speed(tl, float(prev_speed))
                        except Exception:
                            pass
                        try:
                            if st.get("_speed_sub") is not None:
                                try:
                                    st["_speed_sub"].unsubscribe()
                                except Exception:
                                    pass
                                st["_speed_sub"] = None
                        except Exception:
                            pass
                        # 완료 직후 즉시 end_time으로 고정해, 다음 스텝이 start_time을 세팅할 때
                        # "end->start->end"로 튀는 레이스를 최소화한다.
                        try:
                            tl.set_current_time(end_time)
                        except Exception:
                            pass
                        # 이 함수는 usd_context별 state dict(st)로 subscription을 관리한다.
                        # 전역 변수(_complete_sub/_end_fix_sub)는 사용하지 않는다(정의되지 않거나 컨텍스트가 섞일 수 있음).
                        if st.get("_complete_sub") is not None:
                            try:
                                st["_complete_sub"].unsubscribe()
                            except Exception:
                                pass
                            st["_complete_sub"] = None
                        try:
                            import omni.kit.app as app
                            def _fix(_e=None):
                                # 이전 재생에서 예약된 end_fix가, 다음 재생(start_time 세팅)에
                                # 끼어들어 프레임이 튀는 것을 방지한다.
                                if int(st.get("_play_token", 0) or 0) != int(my_token):
                                    return
                                try:
                                    tl.set_current_time(end_time)
                                except Exception:
                                    pass
                                if st.get("_end_fix_sub") is not None:
                                    try:
                                        st["_end_fix_sub"].unsubscribe()
                                    except Exception:
                                        pass
                                    st["_end_fix_sub"] = None
                            st["_end_fix_sub"] = app.get_app().get_post_update_event_stream().create_subscription_to_pop(
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
                st["_complete_sub"] = stream.create_subscription_to_pop(
                    _on_complete,
                    name="morph.tbs_control_1:usd_animation_complete",
                )
            except Exception:
                st["_complete_sub"] = None
        return True
    except Exception:
        return False


def stop_usd_animation(usd_context_name: Optional[str] = None) -> None:
    st = _state_for(usd_context_name)
    if st.get("_speed_sub") is not None:
        try:
            st["_speed_sub"].unsubscribe()
        except Exception:
            pass
        st["_speed_sub"] = None
    if st.get("_loop_sub") is not None:
        try:
            st["_loop_sub"].unsubscribe()
        except Exception:
            pass
        st["_loop_sub"] = None
    if st.get("_complete_sub") is not None:
        try:
            st["_complete_sub"].unsubscribe()
        except Exception:
            pass
        st["_complete_sub"] = None
    if st.get("_end_fix_sub") is not None:
        try:
            st["_end_fix_sub"].unsubscribe()
        except Exception:
            pass
        st["_end_fix_sub"] = None
    tl = _get_timeline(usd_context_name)
    if tl:
        try:
            tl.pause()
        except Exception:
            pass
        # 속도는 기본값(1.0)으로 복구 시도(지원되는 경우)
        try:
            _try_set_timeline_speed(tl, 1.0)
        except Exception:
            pass


def is_playing() -> bool:
    tl = _get_timeline()
    return bool(tl and tl.is_playing())
