# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""가변 이동 애니메이션: prim을 구간별로 x/y/z 이동. TBS 제어용.

시퀀서(연속 실행)를 위해, 애니메이션이 자연 종료될 때 on_completed 콜백을 지원합니다.
"""

from typing import List, Dict, Any, Optional, Callable

import omni.kit.app
import omni.usd as ou
from pxr import Gf, UsdGeom, Usd

_animations: Dict[str, Dict[str, Any]] = {}
_update_sub = None

_OFFSET_SUFFIX = "TBS_OFFSET"


def _needs_xform_order_fix(prim) -> bool:
    """scale이 있는 prim에서 translate/rotate가 scale보다 먼저면 경고가 반복될 수 있어 정리 필요."""
    try:
        x = UsdGeom.Xformable(prim)
        ops = list(x.GetOrderedXformOps()) if x else []
        if not ops:
            return False
        idx_scale = None
        idx_tr = None
        for i, op in enumerate(ops):
            t = op.GetOpType()
            if idx_scale is None and t == UsdGeom.XformOp.TypeScale:
                idx_scale = i
            if idx_tr is None and t in (UsdGeom.XformOp.TypeTranslate, UsdGeom.XformOp.TypeRotateXYZ):
                idx_tr = i
        return idx_scale is not None and idx_tr is not None and idx_tr < idx_scale
    except Exception:
        return False


def _try_fix_xform_order(prim) -> None:
    """현재 TRS 값을 유지한 채 common TRS op로 재기록(가능할 때만)."""
    try:
        if not _needs_xform_order_fix(prim):
            return
        # 1) 먼저 xformOpOrder 자체를 정리(Scale을 앞쪽으로 이동)
        try:
            x = UsdGeom.Xformable(prim)
            ops = list(x.GetOrderedXformOps()) if x else []
            if ops:
                scale_ops = [op for op in ops if op.GetOpType() == UsdGeom.XformOp.TypeScale]
                tbs_ops = []
                rest_ops = []
                for op in ops:
                    try:
                        if _OFFSET_SUFFIX in op.GetName():
                            tbs_ops.append(op)
                        elif op.GetOpType() != UsdGeom.XformOp.TypeScale:
                            rest_ops.append(op)
                    except Exception:
                        if op.GetOpType() != UsdGeom.XformOp.TypeScale:
                            rest_ops.append(op)
                new_order = scale_ops + tbs_ops + rest_ops
                if new_order and ops != new_order:
                    x.SetXformOpOrder(new_order)
        except Exception:
            pass
        api = UsdGeom.XformCommonAPI(prim)
        if not api:
            return
        t, r, s, p, ro = api.GetXformVectors(Usd.TimeCode.Default())
        # 같은 값을 다시 set 해서 common op 순서로 정리되도록 유도
        api.SetXformVectors(t, r, s, p, ro, Usd.TimeCode.Default())
    except Exception:
        pass


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
        # 오프셋 op는 타임라인 키(일반 translate/rotate)와 분리되어 덮어써지지 않게 함
        return x.AddTranslateOp(opSuffix=_OFFSET_SUFFIX)
    except Exception:
        return None


def _get_prim_local_translate(prim) -> Gf.Vec3f:
    if not prim or not prim.IsValid():
        return Gf.Vec3f(0, 0, 0)
    # MOVE 애니메이션은 타임라인에 덮어써지지 않는 오프셋 op를 사용
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
        _try_fix_xform_order(prim)
        op = _get_or_create_offset_translate_op(prim)
        if op:
            op.Set(Gf.Vec3f(float(position[0]), float(position[1]), float(position[2])))
            return
    except Exception:
        pass
    # fallback은 기존 동작 유지 (최소한 이동은 되도록)
    try:
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
) -> None:
    global _animations, _update_sub
    if not segments:
        return
    stage = ou.get_context().get_stage() if ou.get_context() else None
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
    _animations[prim_path] = {
        "start_pos": Gf.Vec3f(start_pos[0], start_pos[1], start_pos[2]),
        "segments": normalized,
        "segment_index": 0,
        "elapsed_in_segment": 0.0,
        "loop": loop,
        "on_completed": on_completed,
    }
    if _update_sub is None:
        stream = omni.kit.app.get_app().get_update_event_stream()
        _update_sub = stream.create_subscription_to_pop(_on_update, name="morph.tbs_control.translate_animation")


def stop_prim_translate_animation(prim_path: str) -> bool:
    global _animations, _update_sub
    if prim_path in _animations:
        del _animations[prim_path]
        if not _animations and _update_sub is not None:
            try:
                _update_sub.unsubscribe()
            except Exception:
                pass
            _update_sub = None
        return True
    return False


def _on_update(e) -> None:
    payload = getattr(e, "payload", None) or {}
    dt = payload.get("dt", 0.0)
    if dt <= 0:
        dt = 1.0 / 60.0
    if not _animations:
        return
    stage = ou.get_context().get_stage() if ou.get_context() else None
    if not stage:
        return
    to_remove = []
    for prim_path, state in list(_animations.items()):
        try:
            prim = stage.GetPrimAtPath(prim_path)
            if not prim or not prim.IsValid():
                to_remove.append(prim_path)
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
                        to_remove.append(prim_path)
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
            to_remove.append(prim_path)
    for prim_path in to_remove:
        _animations.pop(prim_path, None)
    global _update_sub
    if not _animations and _update_sub is not None:
        try:
            _update_sub.unsubscribe()
        except Exception:
            pass
        _update_sub = None
