# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
rotate_animation.py — 회전 애니메이션 (prim 구간별 x/y/z 축 회전, 도 단위).

기능: TBS 제어창 button_2 등에서 사용. 시퀀서를 위해 on_completed 콜백 지원.
"""

from typing import List, Dict, Any, Optional, Callable

import omni.kit.app
import omni.usd as ou
from pxr import Gf, UsdGeom, Usd

_rot_animations: Dict[str, Dict[str, Any]] = {}
_update_sub = None

# 여러 prim을 동일한 월드 피봇·월드 축 기준으로 도는 애니메이션 유틸.
# 현재 `sequence_engine`의 ROTATE 기본 동작은 prim별 로컬 회전(rx/ry/rz delta 적용)이므로,
# 이 "월드 피봇/월드 축" 루틴은 레거시/별도 사용처 용도로 남아있습니다.
_world_pivot_state: Optional[Dict[str, Any]] = None
_world_pivot_sub = None

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


def _get_or_create_offset_rotate_op(prim):
    x = UsdGeom.Xformable(prim)
    if not x:
        return None
    try:
        for op in x.GetOrderedXformOps():
            try:
                if op.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ and _OFFSET_SUFFIX in op.GetName():
                    return op
            except Exception:
                continue
    except Exception:
        pass
    try:
        return x.AddRotateXYZOp(opSuffix=_OFFSET_SUFFIX)
    except Exception:
        return None


def _get_prim_local_rotate_xyz(prim) -> Gf.Vec3f:
    if not prim or not prim.IsValid():
        return Gf.Vec3f(0, 0, 0)
    try:
        op = _get_or_create_offset_rotate_op(prim)
        if op:
            val = op.Get()
            if val is not None:
                return Gf.Vec3f(float(val[0]), float(val[1]), float(val[2]))
    except Exception:
        pass
    return Gf.Vec3f(0, 0, 0)


def _set_prim_rotate_xyz(prim, euler_deg_xyz: Gf.Vec3f) -> None:
    if not prim or not prim.IsValid():
        return
    try:
        _try_fix_xform_order(prim)
        op = _get_or_create_offset_rotate_op(prim)
        if op:
            op.Set(Gf.Vec3f(float(euler_deg_xyz[0]), float(euler_deg_xyz[1]), float(euler_deg_xyz[2])))
            return
    except Exception:
        pass
    try:
        xform = UsdGeom.Xformable(prim)
        if not xform:
            return
        rot_op = None
        for op in xform.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ:
                rot_op = op
                break
        if rot_op is None:
            rot_op = xform.AddRotateXYZOp()
        rot_op.Set(Gf.Vec3f(euler_deg_xyz[0], euler_deg_xyz[1], euler_deg_xyz[2]))
    except Exception:
        pass


def run_prim_rotate_animation(
    prim_path: str,
    segments: List[Dict[str, Any]],
    loop: bool = False,
    on_completed: Optional[Callable[[], None]] = None,
) -> None:
    global _rot_animations, _update_sub
    if not segments:
        return
    stage = ou.get_context().get_stage() if ou.get_context() else None
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return
    start_rot = _get_prim_local_rotate_xyz(prim)
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
    _rot_animations[prim_path] = {
        "start_rot": Gf.Vec3f(start_rot[0], start_rot[1], start_rot[2]),
        "segments": normalized,
        "segment_index": 0,
        "elapsed_in_segment": 0.0,
        "loop": loop,
        "on_completed": on_completed,
    }
    if _update_sub is None:
        stream = omni.kit.app.get_app().get_update_event_stream()
        _update_sub = stream.create_subscription_to_pop(_on_update, name="morph.tbs_control_1.rotate_animation")


def _world_orbit_matrix_4d(pivot_world: Gf.Vec3d, axis_unit: Gf.Vec3d, angle_deg: float) -> Gf.Matrix4d:
    """월드 점 pivot_world, 단위축 axis_unit, 각도 angle_deg(도)인 궤도 회전 4x4. M' = T*R*T^{-1}."""
    if abs(angle_deg) < 1e-15:
        return Gf.Matrix4d(1.0)
    try:
        r = Gf.Rotation(axis_unit, float(angle_deg))
    except Exception:
        return Gf.Matrix4d(1.0)
    t_inv = Gf.Matrix4d(1.0)
    t_inv.SetTranslateOnly(Gf.Vec3d(-pivot_world[0], -pivot_world[1], -pivot_world[2]))
    t_px = Gf.Matrix4d(1.0)
    t_px.SetTranslateOnly(pivot_world)
    r4 = Gf.Matrix4d(1.0)
    r4.SetRotateOnly(r)
    return t_px * r4 * t_inv


def run_world_pivot_rotate_animation(
    prim_paths: List[str],
    pivot_world: Gf.Vec3d,
    axis_world_unit: Gf.Vec3d,
    angle_deg: float,
    duration: float,
    time_code: Usd.TimeCode,
    on_completed: Optional[Callable[[], None]] = None,
) -> None:
    """
    모든 prim에 대해 동일한 월드 피봇·월드 축 기준으로 회전.
    각 프레임: M_w' = T(P) R(axis,θ) T(-P) * M_w0 (스텝 시작 시점 M_w0 고정).
    로컬 목표 M_loc = inv(M_parent_w0)*M_w' 를 M_after*M_tbs*M_before 역산으로 TBS_OFFSET에만 반영.
    """
    global _world_pivot_state, _world_pivot_sub
    stop_world_pivot_rotate_animation()
    for p in prim_paths:
        stop_prim_rotate_animation(p)

    stage = ou.get_context().get_stage() if ou.get_context() else None
    if not stage or not prim_paths:
        if on_completed:
            try:
                on_completed()
            except Exception:
                pass
        return

    cache = UsdGeom.XformCache(time_code)
    items: List[Dict[str, Any]] = []
    for path in prim_paths:
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            continue
        try:
            M_cw = Gf.Matrix4d(cache.GetLocalToWorldTransform(prim))
        except Exception:
            continue
        parent = prim.GetParent()
        try:
            if parent and parent.IsValid():
                ppath = str(parent.GetPath())
                if ppath and ppath != "/":
                    M_pw = Gf.Matrix4d(cache.GetLocalToWorldTransform(parent))
                    M_pw_inv = M_pw.GetInverse()
                else:
                    M_pw_inv = Gf.Matrix4d(1.0)
            else:
                M_pw_inv = Gf.Matrix4d(1.0)
        except Exception:
            M_pw_inv = Gf.Matrix4d(1.0)
        items.append({"path": path, "M_cw0": M_cw, "M_pw_inv": M_pw_inv})

    if not items:
        if on_completed:
            try:
                on_completed()
            except Exception:
                pass
        return

    if duration <= 0:
        from . import sequence_engine as _se

        M_rot = _world_orbit_matrix_4d(pivot_world, axis_world_unit, angle_deg)
        for it in items:
            prim = stage.GetPrimAtPath(it["path"])
            if not prim or not prim.IsValid():
                continue
            M_w = M_rot * it["M_cw0"]
            _se._apply_world_pivot_frame_for_prim(prim, M_w, it["M_pw_inv"], time_code)
        if on_completed:
            try:
                on_completed()
            except Exception:
                pass
        return

    _world_pivot_state = {
        "items": items,
        "pivot_world": Gf.Vec3d(pivot_world),
        "axis_unit": Gf.Vec3d(axis_world_unit),
        "angle_deg": float(angle_deg),
        "duration": float(duration),
        "elapsed": 0.0,
        "on_completed": on_completed,
        "time_code": time_code,
    }

    def _on_wp_update(e) -> None:
        global _world_pivot_state, _world_pivot_sub
        st = _world_pivot_state
        if not st:
            return
        payload = getattr(e, "payload", None) or {}
        dt = float(payload.get("dt", 0.0) or 0.0)
        if dt <= 0:
            dt = 1.0 / 60.0
        st["elapsed"] = float(st["elapsed"]) + dt
        t = min(1.0, st["elapsed"] / st["duration"]) if st["duration"] > 0 else 1.0
        theta_deg = t * float(st["angle_deg"])
        M_rot = _world_orbit_matrix_4d(st["pivot_world"], st["axis_unit"], theta_deg)

        stg = ou.get_context().get_stage() if ou.get_context() else None
        if not stg:
            _world_pivot_state = None
            if _world_pivot_sub is not None:
                try:
                    _world_pivot_sub.unsubscribe()
                except Exception:
                    pass
                _world_pivot_sub = None
            return

        from . import sequence_engine as _se

        tc_wp = st.get("time_code", Usd.TimeCode.Default())
        for it in st["items"]:
            prim = stg.GetPrimAtPath(it["path"])
            if not prim or not prim.IsValid():
                continue
            try:
                M_w = M_rot * it["M_cw0"]
                _se._apply_world_pivot_frame_for_prim(prim, M_w, it["M_pw_inv"], tc_wp)
            except Exception:
                pass

        if t >= 1.0:
            cb = st.get("on_completed")
            _world_pivot_state = None
            if _world_pivot_sub is not None:
                try:
                    _world_pivot_sub.unsubscribe()
                except Exception:
                    pass
                _world_pivot_sub = None
            if cb:
                try:
                    cb()
                except Exception:
                    pass

    try:
        stream = omni.kit.app.get_app().get_update_event_stream()
        _world_pivot_sub = stream.create_subscription_to_pop(_on_wp_update, name="morph.tbs_control_1.world_pivot_rotate")
    except Exception:
        _world_pivot_state = None
        if on_completed:
            try:
                on_completed()
            except Exception:
                pass


def stop_world_pivot_rotate_animation() -> None:
    global _world_pivot_state, _world_pivot_sub
    _world_pivot_state = None
    if _world_pivot_sub is not None:
        try:
            _world_pivot_sub.unsubscribe()
        except Exception:
            pass
        _world_pivot_sub = None


def stop_prim_rotate_animation(prim_path: str) -> bool:
    global _rot_animations, _update_sub
    if prim_path in _rot_animations:
        del _rot_animations[prim_path]
        if not _rot_animations and _update_sub is not None:
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
    if not _rot_animations:
        return
    stage = ou.get_context().get_stage() if ou.get_context() else None
    if not stage:
        return
    to_remove = []
    for prim_path, state in list(_rot_animations.items()):
        try:
            prim = stage.GetPrimAtPath(prim_path)
            if not prim or not prim.IsValid():
                to_remove.append(prim_path)
                continue
            segments = state["segments"]
            idx = state["segment_index"]
            elapsed = state["elapsed_in_segment"] + dt
            base_rot = state["start_rot"]
            for i in range(idx):
                d = segments[i]["delta"]
                base_rot = Gf.Vec3f(base_rot[0] + d[0], base_rot[1] + d[1], base_rot[2] + d[2])
            duration = segments[idx]["duration"]
            delta = segments[idx]["delta"]
            if elapsed >= duration:
                state["elapsed_in_segment"] = 0.0
                state["segment_index"] = idx + 1
                final_this_segment = Gf.Vec3f(
                    base_rot[0] + delta[0], base_rot[1] + delta[1], base_rot[2] + delta[2],
                )
                if state["segment_index"] >= len(segments):
                    _set_prim_rotate_xyz(prim, final_this_segment)
                    if state["loop"]:
                        state["segment_index"] = 0
                        state["start_rot"] = final_this_segment
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
                    _set_prim_rotate_xyz(prim, current)
                continue
            state["elapsed_in_segment"] = elapsed
            t = elapsed / duration if duration > 0 else 1.0
            current_rot = Gf.Vec3f(
                base_rot[0] + delta[0] * t,
                base_rot[1] + delta[1] * t,
                base_rot[2] + delta[2] * t,
            )
            _set_prim_rotate_xyz(prim, current_rot)
        except (UnicodeDecodeError, UnicodeEncodeError):
            to_remove.append(prim_path)
    for prim_path in to_remove:
        _rot_animations.pop(prim_path, None)
    global _update_sub
    if not _rot_animations and _update_sub is not None:
        try:
            _update_sub.unsubscribe()
        except Exception:
            pass
        _update_sub = None
