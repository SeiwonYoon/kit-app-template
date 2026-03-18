# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""곡선(경로) 애니메이션: prim이 지정한 경로를 따라 이동. TBS 제어용.

시퀀서(연속 실행)를 위해, 애니메이션이 자연 종료될 때 on_completed 콜백을 지원합니다.
"""

from typing import List, Dict, Any, Tuple, Union, Optional, Callable

import omni.kit.app
import omni.usd as ou
from pxr import Gf, UsdGeom, Usd

_curve_animations: Dict[str, Dict[str, Any]] = {}
_update_sub = None

_OFFSET_SUFFIX = "TBS_OFFSET"


def _needs_xform_order_fix(prim) -> bool:
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
    try:
        if not _needs_xform_order_fix(prim):
            return
        # 1) xformOpOrder 자체 정리(Scale을 앞쪽으로 이동)
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
        _try_fix_xform_order(prim)
        op = _get_or_create_offset_translate_op(prim)
        if op:
            op.Set(Gf.Vec3f(float(position[0]), float(position[1]), float(position[2])))
            return
    except Exception:
        pass
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


def _to_vec3f(p) -> Gf.Vec3f:
    if isinstance(p, Gf.Vec3f):
        return p
    if isinstance(p, (list, tuple)) and len(p) >= 3:
        return Gf.Vec3f(float(p[0]), float(p[1]), float(p[2]))
    return Gf.Vec3f(0, 0, 0)


def make_parabolic_path(
    start: Union[Tuple[float, float, float], Gf.Vec3f],
    end: Union[Tuple[float, float, float], Gf.Vec3f],
    arc_height: float = 2.0,
    num_points: int = 24,
    arc_axis: str = "y",
) -> List[Tuple[float, float, float]]:
    """포물선 경로: start에서 end로 포물선을 그리며 이동하는 점 리스트."""
    s = _to_vec3f(start)
    e = _to_vec3f(end)
    mx = (s[0] + e[0]) * 0.5
    my = (s[1] + e[1]) * 0.5
    mz = (s[2] + e[2]) * 0.5
    if arc_axis == "y":
        peak = (mx, my + arc_height, mz)
    elif arc_axis == "z":
        peak = (mx, my, mz + arc_height)
    else:
        peak = (mx + arc_height, my, mz)
    points = []
    for i in range(num_points + 1):
        t = float(i) / num_points
        u = 1.0 - t
        x = u * u * s[0] + 2 * u * t * peak[0] + t * t * e[0]
        y = u * u * s[1] + 2 * u * t * peak[1] + t * t * e[1]
        z = u * u * s[2] + 2 * u * t * peak[2] + t * t * e[2]
        points.append((x, y, z))
    return points


def run_prim_curve_animation(
    prim_path: str,
    path_points: List[Union[Tuple[float, float, float], Gf.Vec3f, List[float]]],
    duration_sec: float = 3.0,
    loop: bool = False,
    on_completed: Optional[Callable[[], None]] = None,
) -> None:
    global _curve_animations, _update_sub
    if not path_points or len(path_points) < 2 or duration_sec <= 0:
        return
    stage = ou.get_context().get_stage() if ou.get_context() else None
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return
    points = [_to_vec3f(p) for p in path_points]
    lengths = []
    total = 0.0
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        L = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2) ** 0.5
        lengths.append(L)
        total += L
    if total <= 0:
        segment_durations = [duration_sec / max(1, len(points) - 1)] * (len(points) - 1)
    else:
        segment_durations = [(d / total) * duration_sec for d in lengths]
    _curve_animations[prim_path] = {
        "path_points": points,
        "segment_durations": segment_durations,
        "duration_sec": duration_sec,
        "segment_index": 0,
        "elapsed_in_segment": 0.0,
        "loop": loop,
        "on_completed": on_completed,
    }
    if _update_sub is None:
        stream = omni.kit.app.get_app().get_update_event_stream()
        _update_sub = stream.create_subscription_to_pop(_on_curve_update, name="morph.tbs_control.curve_animation")


def stop_prim_curve_animation(prim_path: str) -> bool:
    global _curve_animations, _update_sub
    if prim_path in _curve_animations:
        del _curve_animations[prim_path]
        if not _curve_animations and _update_sub is not None:
            try:
                _update_sub.unsubscribe()
            except Exception:
                pass
            _update_sub = None
        return True
    return False


def _on_curve_update(e) -> None:
    payload = getattr(e, "payload", None) or {}
    dt = payload.get("dt", 0.0)
    if dt <= 0:
        dt = 1.0 / 60.0
    if not _curve_animations:
        return
    stage = ou.get_context().get_stage() if ou.get_context() else None
    if not stage:
        return
    to_remove = []
    for prim_path, state in list(_curve_animations.items()):
        try:
            prim = stage.GetPrimAtPath(prim_path)
            if not prim or not prim.IsValid():
                to_remove.append(prim_path)
                continue
            points = state["path_points"]
            seg_durs = state["segment_durations"]
            idx = state["segment_index"]
            elapsed = state["elapsed_in_segment"] + dt
            if idx >= len(seg_durs):
                if state["loop"]:
                    state["segment_index"] = 0
                    state["elapsed_in_segment"] = 0.0
                    idx = 0
                    elapsed = dt
                else:
                    _set_prim_translate(prim, points[-1])
                    cb = state.get("on_completed")
                    if cb:
                        try:
                            cb()
                        except Exception:
                            pass
                    to_remove.append(prim_path)
                    continue
            duration = seg_durs[idx]
            p0, p1 = points[idx], points[idx + 1]
            if elapsed >= duration:
                state["elapsed_in_segment"] = elapsed - duration
                state["segment_index"] = idx + 1
                _set_prim_translate(prim, p1)
                if state["segment_index"] >= len(seg_durs):
                    if state["loop"]:
                        state["segment_index"] = 0
                        state["elapsed_in_segment"] = 0.0
                    else:
                        cb = state.get("on_completed")
                        if cb:
                            try:
                                cb()
                            except Exception:
                                pass
                        to_remove.append(prim_path)
                continue
            state["elapsed_in_segment"] = elapsed
            t = elapsed / duration if duration > 0 else 1.0
            pos = Gf.Vec3f(
                p0[0] + (p1[0] - p0[0]) * t,
                p0[1] + (p1[1] - p0[1]) * t,
                p0[2] + (p1[2] - p0[2]) * t,
            )
            _set_prim_translate(prim, pos)
        except (UnicodeDecodeError, UnicodeEncodeError):
            to_remove.append(prim_path)
    for prim_path in to_remove:
        _curve_animations.pop(prim_path, None)
    global _update_sub
    if not _curve_animations and _update_sub is not None:
        try:
            _update_sub.unsubscribe()
        except Exception:
            pass
        _update_sub = None
