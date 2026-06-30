# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
translate_animation.py — 직선 이동 애니메이션 (구간별 x/y/z)

【역할】
- TBS_OFFSET 이름의 translate op에 누적. 제어창 버튼·시퀀스 MOVE 스텝에서 호출.
- stop_prim_translate_animation: extension 종료·중지 시.

【수정 가이드】
- 보간 곡선·속도: run_prim_translate_animation 내부
- op 이름 규칙: _OFFSET_SUFFIX (rotate/curve와 공유 개념, 바꾸면 전체 검색 필요)

사용처: control_window, sequence_engine

【유지보수 시나리오】
1) MOVE가 순간이동/끊김처럼 보일 때
   - _on_update 보간(t) 계산 확인
   - segment duration/delta 누적 방식 확인
2) 실행 후 원위치/좌표 튐 문제
   - _OFFSET_SUFFIX op만 읽고 쓰는지 확인(_get_or_create_offset_translate_op)
   - sequence_engine baseline 복원 로직과 충돌 여부 확인
3) 새 파라미터(easing 등) 추가
   - run_prim_translate_animation의 segment schema 확장
   - sequence_engine MOVE 스텝 생성/실행 키와 동기화
"""

from typing import List, Dict, Any, Optional, Callable

import omni.kit.app
import omni.usd as ou
from pxr import Gf, UsdGeom, Usd

from .xform_utils import ensure_scale_xform_ops_first

_animations: Dict[str, Dict[str, Any]] = {}
_update_sub = None

_OFFSET_SUFFIX = "TBS_OFFSET"


def _tx_anim_key(prim_path: str, usd_context_name: Optional[str]) -> str:
    """분할 보조 USD 컨텍스트와 기본 컨텍스트에서 동일 prim 경로가 공존할 수 있어 키를 분리한다."""
    return f"{(usd_context_name or '').strip()}\x00{prim_path}"


def _stage_for_translate(usd_context_name: Optional[str]):
    try:
        nm = (usd_context_name or "").strip()
        ctx = ou.get_context(nm) if nm else ou.get_context()
        return ctx.get_stage() if ctx else None
    except Exception:
        return None


def is_translate_animation_running() -> bool:
    """control_window에서 sim tick pause 판단에 사용."""
    try:
        return bool(_animations)
    except Exception:
        return False


def is_translate_animation_running_for_context(
    usd_context_name: Optional[str],
) -> bool:
    """지정 USD 컨텍스트에서 legacy translate 애니가 진행 중인지."""
    target = str(usd_context_name or "").strip()
    try:
        for state in _animations.values():
            if not isinstance(state, dict):
                continue
            ctx = str(state.get("usd_context_name") or "").strip()
            if target:
                if ctx == target:
                    return True
            elif not ctx:
                return True
    except Exception:
        pass
    return False


def count_translate_animations_for_context(usd_context_name: Optional[str]) -> int:
    """진단용 — 해당 컨텍스트의 legacy translate 애니 개수."""
    target = str(usd_context_name or "").strip()
    n = 0
    try:
        for state in _animations.values():
            if not isinstance(state, dict):
                continue
            ctx = str(state.get("usd_context_name") or "").strip()
            if target:
                if ctx == target:
                    n += 1
            elif not ctx:
                n += 1
    except Exception:
        pass
    return n


def _get_or_create_offset_translate_op(prim):
    x = UsdGeom.Xformable(prim)
    if not x:
        return None
    try:
        for op in x.GetOrderedXformOps():
            try:
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate and _OFFSET_SUFFIX in op.GetName():
                    return op
            except Exception:
                continue
    except Exception:
        pass
    try:
        return x.AddTranslateOp(opSuffix=_OFFSET_SUFFIX)
    except Exception:
        return None


def _get_prim_local_translate(prim) -> Gf.Vec3f:
    if not prim or not prim.IsValid():
        return Gf.Vec3f(0, 0, 0)
    try:
        op = _get_or_create_offset_translate_op(prim)
        if op:
            val = op.Get()
            if val is not None:
                return Gf.Vec3f(float(val[0]), float(val[1]), float(val[2]))
    except Exception:
        pass
    return Gf.Vec3f(0, 0, 0)


def _set_prim_translate(prim, position: Gf.Vec3f) -> None:
    if not prim or not prim.IsValid():
        return
    try:
        ensure_scale_xform_ops_first(prim)
        op = _get_or_create_offset_translate_op(prim)
        if op:
            op.Set(Gf.Vec3f(float(position[0]), float(position[1]), float(position[2])))
            return
    except Exception:
        pass
    try:
        ensure_scale_xform_ops_first(prim)
        xform = UsdGeom.Xformable(prim)
        if not xform:
            return
        translate_op = None
        for op in xform.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                translate_op = op
                break
        if translate_op is None:
            translate_op = xform.AddTranslateOp()
        translate_op.Set(Gf.Vec3f(position[0], position[1], position[2]))
    except Exception:
        pass


def run_prim_translate_animation(
    prim_path: str,
    segments: List[Dict[str, Any]],
    loop: bool = False,
    on_completed: Optional[Callable[[], None]] = None,
    usd_context_name: Optional[str] = None,
) -> None:
    global _animations, _update_sub
    if not segments:
        return
    stage = _stage_for_translate(usd_context_name)
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return
    start_pos = _get_prim_local_translate(prim)
    normalized = []
    for seg in segments:
        d = seg.get("delta")
        if d is None:
            continue
        if isinstance(d, (list, tuple)) and len(d) >= 3:
            delta = (float(d[0]), float(d[1]), float(d[2]))
        else:
            continue
        duration = float(seg.get("duration", 0))
        if duration <= 0:
            continue
        normalized.append({"duration": duration, "delta": delta})
    if not normalized:
        return
    key = _tx_anim_key(prim_path, usd_context_name)
    _animations[key] = {
        "prim_path": prim_path,
        "usd_context_name": usd_context_name,
        "start_pos": Gf.Vec3f(start_pos[0], start_pos[1], start_pos[2]),
        "segments": normalized,
        "segment_index": 0,
        "elapsed_in_segment": 0.0,
        "loop": loop,
        "on_completed": on_completed,
    }
    if _update_sub is None:
        stream = omni.kit.app.get_app().get_update_event_stream()
        _update_sub = stream.create_subscription_to_pop(_on_update, name="morph.tbs_control_2.translate_animation")


def is_prim_translate_animation_running(
    prim_path: str,
    usd_context_name: Optional[str] = None,
) -> bool:
    """해당 prim 의 translate 애니가 진행 중인지(컨텍스트 미지정 시 전 컨텍스트 검사)."""
    pp = str(prim_path or "").strip()
    if not pp:
        return False
    if usd_context_name is not None:
        return _tx_anim_key(pp, usd_context_name) in _animations
    for k in _animations.keys():
        try:
            if str(k).split("\x00", 1)[-1] == pp:
                return True
        except Exception:
            continue
    return False


def stop_prim_translate_animation(prim_path: str, usd_context_name: Optional[str] = None) -> bool:
    global _animations, _update_sub
    key = _tx_anim_key(prim_path, usd_context_name)
    if key in _animations:
        del _animations[key]
        if not _animations and _update_sub is not None:
            try:
                _update_sub.unsubscribe()
            except Exception:
                pass
            _update_sub = None
        return True
    return False


def stop_prim_translate_animation_all_contexts(prim_path: str) -> int:
    """
    동일 prim_path 에 대해 등록된 모든 컨텍스트 키(기본/보조 USD)의 translate 애니 상태를 제거한다.
    포트 LOT 복원 등에서 한 스테이지만 지정하지 않고 전부 끊을 때 사용.
    """
    global _animations, _update_sub
    pp = str(prim_path or "").strip()
    if not pp:
        return 0
    removed = 0
    for k in list(_animations.keys()):
        try:
            tail = k.split("\x00", 1)[-1]
        except Exception:
            tail = str(k)
        if tail != pp:
            continue
        try:
            del _animations[k]
            removed += 1
        except Exception:
            pass
    if not _animations and _update_sub is not None:
        try:
            _update_sub.unsubscribe()
        except Exception:
            pass
        _update_sub = None
    return removed


def stop_translate_animations_for_context(
    usd_context_name: Optional[str],
    *,
    preserve_foup_port_lot_prims: bool = False,
) -> None:
    """지정 USD 컨텍스트의 translate 애니만 중지."""
    global _animations, _update_sub
    target = str(usd_context_name or "").strip()
    if not preserve_foup_port_lot_prims:
        for k, state in list(_animations.items()):
            ctx = str(state.get("usd_context_name") or "").strip()
            if ctx == target:
                try:
                    del _animations[k]
                except Exception:
                    pass
    else:
        try:
            from . import port_lot_visibility as _plv  # type: ignore
        except Exception:
            _plv = None
        for k, state in list(_animations.items()):
            ctx = str(state.get("usd_context_name") or "").strip()
            if ctx != target:
                continue
            if _plv is not None:
                try:
                    path = str(state.get("prim_path") or "").strip()
                    if path and _plv.is_foup_in_progress(path, usd_context_name=ctx):
                        continue
                except Exception:
                    pass
            try:
                del _animations[k]
            except Exception:
                pass
    if not _animations and _update_sub is not None:
        try:
            _update_sub.unsubscribe()
        except Exception:
            pass
        _update_sub = None


def stop_all_translate_animations(preserve_foup_port_lot_prims: bool = False) -> None:
    """전체 이동 애니메이션 강제 중지(SequenceRunner 정지·공정우선 interrupt 등).

    ``preserve_foup_port_lot_prims=True`` 인 경우, ``port_lot_visibility.is_foup_in_progress(prim)``
    가 참인 prim 의 translate 애니는 유지한다. FOUP ±Y 이동이 공정우선 interrupt 등으로
    중간에 끊겨 임의 위치에 멈추는 현상을 막는다.
    """
    global _animations, _update_sub
    if not preserve_foup_port_lot_prims:
        try:
            _animations.clear()
        except Exception:
            _animations = {}
    else:
        try:
            from . import port_lot_visibility as _plv  # type: ignore
        except Exception:
            _plv = None
        if _plv is None:
            try:
                _animations.clear()
            except Exception:
                _animations = {}
        else:
            for k, state in list(_animations.items()):
                try:
                    path = str(state.get("prim_path") or "").strip()
                    ctx = str(state.get("usd_context_name") or "").strip()
                    if path and _plv.is_foup_in_progress(path, usd_context_name=ctx):
                        continue
                except Exception:
                    pass
                try:
                    del _animations[k]
                except Exception:
                    pass
    if not _animations and _update_sub is not None:
        try:
            _update_sub.unsubscribe()
        except Exception:
            pass
        _update_sub = None


def _on_update(e) -> None:
    payload = getattr(e, "payload", None) or {}
    dt = payload.get("dt", 0.0)
    if dt <= 0:
        dt = 1.0 / 60.0
    if not _animations:
        return
    to_remove = []
    for anim_key, state in list(_animations.items()):
        try:
            prim_path = str(state.get("prim_path") or anim_key.split("\x00", 1)[-1])
            ctx_nm = state.get("usd_context_name")
            stage = _stage_for_translate(ctx_nm if ctx_nm is not None else None)
            if not stage:
                to_remove.append(anim_key)
                continue
            prim = stage.GetPrimAtPath(prim_path)
            if not prim or not prim.IsValid():
                to_remove.append(anim_key)
                continue
            segments = state["segments"]
            idx = state["segment_index"]
            elapsed = state["elapsed_in_segment"] + dt
            base_pos = state["start_pos"]
            for i in range(idx):
                d = segments[i]["delta"]
                base_pos = Gf.Vec3f(base_pos[0] + d[0], base_pos[1] + d[1], base_pos[2] + d[2])
            duration = segments[idx]["duration"]
            delta = segments[idx]["delta"]
            if elapsed >= duration:
                state["elapsed_in_segment"] = 0.0
                state["segment_index"] = idx + 1
                final_this_segment = Gf.Vec3f(
                    base_pos[0] + delta[0], base_pos[1] + delta[1], base_pos[2] + delta[2],
                )
                if state["segment_index"] >= len(segments):
                    _set_prim_translate(prim, final_this_segment)
                    if state["loop"]:
                        state["segment_index"] = 0
                        state["start_pos"] = final_this_segment
                    else:
                        cb = state.get("on_completed")
                        if cb:
                            try:
                                cb()
                            except Exception:
                                pass
                        to_remove.append(anim_key)
                else:
                    remainder = elapsed - duration
                    state["elapsed_in_segment"] = remainder
                    next_idx = state["segment_index"]
                    next_d = segments[next_idx]["delta"]
                    next_dur = segments[next_idx]["duration"]
                    t = min(1.0, remainder / next_dur) if next_dur > 0 else 1.0
                    current = Gf.Vec3f(
                        final_this_segment[0] + next_d[0] * t,
                        final_this_segment[1] + next_d[1] * t,
                        final_this_segment[2] + next_d[2] * t,
                    )
                    _set_prim_translate(prim, current)
                continue
            state["elapsed_in_segment"] = elapsed
            t = elapsed / duration
            current_pos = Gf.Vec3f(
                base_pos[0] + delta[0] * t,
                base_pos[1] + delta[1] * t,
                base_pos[2] + delta[2] * t,
            )
            _set_prim_translate(prim, current_pos)
        except (UnicodeDecodeError, UnicodeEncodeError):
            to_remove.append(anim_key)
    for k in to_remove:
        _animations.pop(k, None)
    global _update_sub
    if not _animations and _update_sub is not None:
        try:
            _update_sub.unsubscribe()
        except Exception:
            pass
        _update_sub = None
