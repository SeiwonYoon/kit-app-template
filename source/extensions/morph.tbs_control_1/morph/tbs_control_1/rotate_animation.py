# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
rotate_animation.py — 회전 애니메이션 (도 단위, 로컬 offset / 월드 피봇 궤도)

【역할】
- TBS_OFFSET rotateXYZ 또는 월드 피봇 기준 회전. 제어창·시퀀스 ROTATE.

【수정 가이드】
- Euler 순서·피봇 수학: run_prim_rotate_animation, run_world_euler_pivot_rotate_animation
- 시퀀서와 제어창 동작 일치: sequence_engine의 ROTATE 분기와 인자 키 이름 유지

사용처: control_window, sequence_engine

【유지보수 시나리오】
1) 월드 피봇 회전 중심이 어긋날 때
   - run_world_euler_pivot_rotate_animation의 pivot/world 행렬 계산 확인
2) 로컬 회전과 월드 회전 동작이 다를 때
   - user_axis_rotate 플래그 전달 경로(sequence_editor -> sequence_engine) 검증
3) 회전 축 순서(XYZ) 변경 필요 시
   - 본 파일 보간/적용 순서와 sequence_engine 문서 동시 수정
"""

from typing import List, Dict, Any, Optional, Callable

import omni.kit.app
import omni.usd as ou
from pxr import Gf, UsdGeom, Usd

from .xform_utils import ensure_scale_xform_ops_first

_rot_animations: Dict[str, Dict[str, Any]] = {}
_update_sub = None

# 월드 피봇 궤도 회전(단일 축 각도 / 루트 Euler) 공용 상태. 한 번에 하나만 재생.
_world_pivot_state: Optional[Dict[str, Any]] = None
_world_pivot_sub = None

_OFFSET_SUFFIX = "TBS_OFFSET"


def is_rotate_animation_running() -> bool:
    """control_window에서 sim tick pause 판단에 사용."""
    try:
        return bool(_rot_animations) or (_world_pivot_state is not None)
    except Exception:
        return False


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


def _set_prim_translate(prim, v: Gf.Vec3f) -> None:
    if not prim or not prim.IsValid():
        return
    try:
        ensure_scale_xform_ops_first(prim)
        op = _get_or_create_offset_translate_op(prim)
        if op:
            op.Set(Gf.Vec3f(float(v[0]), float(v[1]), float(v[2])))
            return
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
        ensure_scale_xform_ops_first(prim)
        op = _get_or_create_offset_rotate_op(prim)
        if op:
            op.Set(Gf.Vec3f(float(euler_deg_xyz[0]), float(euler_deg_xyz[1]), float(euler_deg_xyz[2])))
            return
    except Exception:
        pass
    try:
        ensure_scale_xform_ops_first(prim)
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


def run_prim_rotate_pivot_local_animation(
    prim_path: str,
    pivot_local: Gf.Vec3d,
    rx_deg: float,
    ry_deg: float,
    rz_deg: float,
    duration: float,
    on_completed: Optional[Callable[[], None]] = None,
) -> None:
    """
    로컬 pivot_local(prim local 기준) 점을 중심으로 회전하도록,
    ROTATE(TBS_OFFSET)와 TRANSLATE(TBS_OFFSET)를 함께 보정하며 애니메이션한다.

    의도: 사용자가 "제자리 회전"으로 인식하는 동작(형상 중심 고정)을 만들기 위함.
    """
    global _rot_animations, _update_sub
    stage = ou.get_context().get_stage() if ou.get_context() else None
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return
    # TBS_OFFSET op 순서(Translate/Rotate)가 에셋마다 다를 수 있어,
    # "제자리 회전" 보정식은 op 순서에 맞게 선택해야 한다.
    order = "TR"  # default: Translate -> Rotate
    try:
        x = UsdGeom.Xformable(prim)
        if x:
            ops = list(x.GetOrderedXformOps())
            t_i = None
            r_i = None
            for i, op in enumerate(ops):
                try:
                    nm = op.GetName()
                except Exception:
                    nm = ""
                if _OFFSET_SUFFIX not in str(nm):
                    continue
                try:
                    ot = op.GetOpType()
                except Exception:
                    continue
                if ot == UsdGeom.XformOp.TypeTranslate and t_i is None:
                    t_i = i
                if ot == UsdGeom.XformOp.TypeRotateXYZ and r_i is None:
                    r_i = i
            if t_i is not None and r_i is not None:
                order = "TR" if t_i < r_i else "RT"
    except Exception:
        order = "TR"
    start_rot = _get_prim_local_rotate_xyz(prim)
    start_pos = _get_prim_local_translate(prim)
    _rot_animations[prim_path] = {
        "kind": "pivot_local",
        "start_rot": Gf.Vec3f(start_rot[0], start_rot[1], start_rot[2]),
        "start_pos": Gf.Vec3f(start_pos[0], start_pos[1], start_pos[2]),
        "pivot_local": Gf.Vec3d(float(pivot_local[0]), float(pivot_local[1]), float(pivot_local[2])),
        "op_order": order,
        "segments": [{"duration": float(max(1e-6, duration)), "delta": (float(rx_deg), float(ry_deg), float(rz_deg))}],
        "segment_index": 0,
        "elapsed_in_segment": 0.0,
        "loop": False,
        "on_completed": on_completed,
    }
    if _update_sub is None:
        stream = omni.kit.app.get_app().get_update_event_stream()
        _update_sub = stream.create_subscription_to_pop(_on_update, name="morph.tbs_control_1.rotate_animation")


def _get_current_time_code() -> Usd.TimeCode:
    try:
        import omni.timeline as ot
        tl = ot.get_timeline_interface()
        if tl:
            return Usd.TimeCode(float(tl.get_current_time()))
    except Exception:
        pass
    return Usd.TimeCode.Default()


def _prim_world_bbox_center(stage: Usd.Stage, prim: Usd.Prim, tc: Usd.TimeCode) -> Optional[Gf.Vec3d]:
    try:
        if not stage or not prim or not prim.IsValid():
            return None
        cache = UsdGeom.BBoxCache(
            tc,
            includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
            useExtentsHint=True,
        )
        bbox = cache.ComputeWorldBound(prim)
        rng = bbox.ComputeAlignedBox()
        mn = rng.GetMin()
        mx = rng.GetMax()
        return Gf.Vec3d(
            (float(mn[0]) + float(mx[0])) * 0.5,
            (float(mn[1]) + float(mx[1])) * 0.5,
            (float(mn[2]) + float(mx[2])) * 0.5,
        )
    except Exception:
        return None


def run_prim_rotate_lock_world_center_animation(
    prim_path: str,
    rx_deg: float,
    ry_deg: float,
    rz_deg: float,
    duration: float,
    on_completed: Optional[Callable[[], None]] = None,
) -> None:
    """
    ROTATE 동안 prim의 "월드 바운드 중심"이 움직이지 않도록 매 프레임 translate를 보정한다.

    방식:
    1) 시작 시점의 desired_world_center 저장
    2) 매 프레임:
       - rotate(TBS_OFFSET) 적용
       - 현재 world_center 재계산
       - delta_world = desired - current
       - delta_local = sequence_engine._world_delta_to_tbs_offset_translate_delta(...)로 변환
       - translate(TBS_OFFSET) += delta_local
    """
    global _rot_animations, _update_sub
    stage = ou.get_context().get_stage() if ou.get_context() else None
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return
    tc0 = _get_current_time_code()
    desired = _prim_world_bbox_center(stage, prim, tc0)
    if desired is None:
        return
    start_rot = _get_prim_local_rotate_xyz(prim)
    start_pos = _get_prim_local_translate(prim)
    _rot_animations[prim_path] = {
        "kind": "lock_world_center",
        "desired_center": Gf.Vec3d(float(desired[0]), float(desired[1]), float(desired[2])),
        "start_rot": Gf.Vec3f(start_rot[0], start_rot[1], start_rot[2]),
        "start_pos": Gf.Vec3f(start_pos[0], start_pos[1], start_pos[2]),
        "segments": [{"duration": float(max(1e-6, duration)), "delta": (float(rx_deg), float(ry_deg), float(rz_deg))}],
        "segment_index": 0,
        "elapsed_in_segment": 0.0,
        "loop": False,
        "on_completed": on_completed,
    }
    if _update_sub is None:
        stream = omni.kit.app.get_app().get_update_event_stream()
        _update_sub = stream.create_subscription_to_pop(_on_update, name="morph.tbs_control_1.rotate_animation")


def _matrix_from_rotate_xyz_deg(v) -> Gf.Matrix4d:
    """
    루트(월드) 고정: Euler XYZ (도) → 회전 4x4.
    UsdGeom RotateXYZ 와 동일하게 Matrix3d: xRot * yRot * zRot.
    """
    m = Gf.Matrix4d(1.0)
    if v is not None and hasattr(v, "__len__") and len(v) >= 3:
        try:
            mx = Gf.Matrix3d(Gf.Rotation(Gf.Vec3d(1, 0, 0), float(v[0])))
            my = Gf.Matrix3d(Gf.Rotation(Gf.Vec3d(0, 1, 0), float(v[1])))
            mz = Gf.Matrix3d(Gf.Rotation(Gf.Vec3d(0, 0, 1), float(v[2])))
            r3 = mx * my * mz
            try:
                m.SetRotateOnly(Gf.Rotation(r3))
            except Exception:
                try:
                    m.SetRotateOnly(r3)
                except Exception:
                    for i in range(3):
                        for j in range(3):
                            m[i][j] = r3[i][j]
        except Exception:
            pass
    return m


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


def _world_orbit_matrix_euler_pivot(pivot_world: Gf.Vec3d, rx_deg: float, ry_deg: float, rz_deg: float) -> Gf.Matrix4d:
    """월드 고정 Euler XYZ(도) 회전을 pivot_world 를 지나는 축으로 적용: T(P)*R*inv(T(P))."""
    r4 = _matrix_from_rotate_xyz_deg((rx_deg, ry_deg, rz_deg))
    t_inv = Gf.Matrix4d(1.0)
    t_inv.SetTranslateOnly(Gf.Vec3d(-pivot_world[0], -pivot_world[1], -pivot_world[2]))
    t_px = Gf.Matrix4d(1.0)
    t_px.SetTranslateOnly(pivot_world)
    return t_px * r4 * t_inv


def _matrix_from_translate_3(v: Gf.Vec3d) -> Gf.Matrix4d:
    m = Gf.Matrix4d(1.0)
    m.SetTranslateOnly(Gf.Vec3d(float(v[0]), float(v[1]), float(v[2])))
    return m


def run_local_euler_pivot_rotate_animation(
    prim_paths: List[str],
    pivot_local: Gf.Vec3d,
    rx_deg: float,
    ry_deg: float,
    rz_deg: float,
    duration: float,
    time_code: Usd.TimeCode,
    on_completed: Optional[Callable[[], None]] = None,
) -> None:
    """
    "로컬 축 기준" 회전 + "로컬 pivot_local 고정"을 만족하도록 월드 목표 행렬을 매 프레임 구성해 적용.

    수식(개념):
    - M_w(t) = M_w0 * T(pivot_local) * R_local(t) * T(-pivot_local)
    - 여기서 R_local(t)는 prim 로컬 축(XYZ) 기준 Euler(rx,ry,rz)*t.
    - 위 형태면 pivot_local에 해당하는 점은 월드에서 고정되고, 축도 로컬 기준이라 "이상한 축" 문제가 줄어든다.
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
            M_w0 = Gf.Matrix4d(cache.GetLocalToWorldTransform(prim))
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
        items.append({"path": path, "M_w0": M_w0, "M_pw_inv": M_pw_inv})

    if not items:
        if on_completed:
            try:
                on_completed()
            except Exception:
                pass
        return

    if duration <= 0:
        from . import sequence_engine as _se
        R = _matrix_from_rotate_xyz_deg((rx_deg, ry_deg, rz_deg))
        T_p = _matrix_from_translate_3(pivot_local)
        T_n = _matrix_from_translate_3(Gf.Vec3d(-pivot_local[0], -pivot_local[1], -pivot_local[2]))
        M_loc = T_p * R * T_n
        for it in items:
            prim = stage.GetPrimAtPath(it["path"])
            if not prim or not prim.IsValid():
                continue
            M_w = it["M_w0"] * M_loc
            _se._apply_world_pivot_frame_for_prim(prim, M_w, it["M_pw_inv"], time_code)
        if on_completed:
            try:
                on_completed()
            except Exception:
                pass
        return

    _world_pivot_state = {
        "kind": "local_euler_pivot",
        "items": items,
        "pivot_local": Gf.Vec3d(float(pivot_local[0]), float(pivot_local[1]), float(pivot_local[2])),
        "rx_deg": float(rx_deg),
        "ry_deg": float(ry_deg),
        "rz_deg": float(rz_deg),
        "duration": float(duration),
        "elapsed": 0.0,
        "on_completed": on_completed,
        "time_code": time_code,
    }

    def _on_update(e) -> None:
        global _world_pivot_state, _world_pivot_sub
        st = _world_pivot_state
        if not st or st.get("kind") != "local_euler_pivot":
            return
        payload = getattr(e, "payload", None) or {}
        dt = float(payload.get("dt", 0.0) or 0.0)
        if dt <= 0:
            dt = 1.0 / 60.0
        st["elapsed"] = float(st["elapsed"]) + dt
        t = min(1.0, st["elapsed"] / st["duration"]) if st["duration"] > 0 else 1.0
        rx = float(st["rx_deg"]) * t
        ry = float(st["ry_deg"]) * t
        rz = float(st["rz_deg"]) * t
        R = _matrix_from_rotate_xyz_deg((rx, ry, rz))
        p = st["pivot_local"]
        T_p = _matrix_from_translate_3(p)
        T_n = _matrix_from_translate_3(Gf.Vec3d(-p[0], -p[1], -p[2]))
        M_loc = T_p * R * T_n
        from . import sequence_engine as _se
        tc_wp = st.get("time_code", Usd.TimeCode.Default())
        for it in st["items"]:
            prim = stage.GetPrimAtPath(it["path"])
            if not prim or not prim.IsValid():
                continue
            M_w = it["M_w0"] * M_loc
            _se._apply_world_pivot_frame_for_prim(prim, M_w, it["M_pw_inv"], tc_wp)
        if st["elapsed"] >= st["duration"]:
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

    stream = omni.kit.app.get_app().get_update_event_stream()
    _world_pivot_sub = stream.create_subscription_to_pop(_on_update, name="morph.tbs_control_1.local_euler_pivot_rotate")

def run_world_euler_pivot_rotate_animation(
    prim_paths: List[str],
    pivot_world: Optional[Gf.Vec3d],
    rx_deg: float,
    ry_deg: float,
    rz_deg: float,
    duration: float,
    time_code: Usd.TimeCode,
    on_completed: Optional[Callable[[], None]] = None,
) -> None:
    """
    스테이지 루트(월드) 고정 X/Y/Z Euler(rx,ry,rz 도)만큼 회전 적용.
    pivot_world 가 None이면 각 prim의 월드 원점( L2W translation )을 P로 사용.
    지정 시 모든 prim에 동일한 P 사용.
    M_w' = T(P) * R_euler(rx,ry,rz) * T(-P) * M_w0 (스텝 시작 시점 M_w0 고정).
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
        tr = M_cw.ExtractTranslation()
        if pivot_world is None:
            pw = Gf.Vec3d(float(tr[0]), float(tr[1]), float(tr[2]))
        else:
            pw = Gf.Vec3d(float(pivot_world[0]), float(pivot_world[1]), float(pivot_world[2]))
        items.append({"path": path, "M_cw0": M_cw, "M_pw_inv": M_pw_inv, "pivot_world": pw})

    if not items:
        if on_completed:
            try:
                on_completed()
            except Exception:
                pass
        return

    if duration <= 0:
        from . import sequence_engine as _se

        for it in items:
            prim = stage.GetPrimAtPath(it["path"])
            if not prim or not prim.IsValid():
                continue
            pw = it["pivot_world"]
            M_rot = _world_orbit_matrix_euler_pivot(pw, rx_deg, ry_deg, rz_deg)
            M_w = M_rot * it["M_cw0"]
            _se._apply_world_pivot_frame_for_prim(prim, M_w, it["M_pw_inv"], time_code)
        if on_completed:
            try:
                on_completed()
            except Exception:
                pass
        return

    _world_pivot_state = {
        "kind": "euler_pivot",
        "items": items,
        "rx_deg": float(rx_deg),
        "ry_deg": float(ry_deg),
        "rz_deg": float(rz_deg),
        "duration": float(duration),
        "elapsed": 0.0,
        "on_completed": on_completed,
        "time_code": time_code,
    }

    def _on_we_update(e) -> None:
        global _world_pivot_state, _world_pivot_sub
        st = _world_pivot_state
        if not st or st.get("kind") != "euler_pivot":
            return
        payload = getattr(e, "payload", None) or {}
        dt = float(payload.get("dt", 0.0) or 0.0)
        if dt <= 0:
            dt = 1.0 / 60.0
        st["elapsed"] = float(st["elapsed"]) + dt
        t = min(1.0, st["elapsed"] / st["duration"]) if st["duration"] > 0 else 1.0
        rx = t * float(st["rx_deg"])
        ry = t * float(st["ry_deg"])
        rz = t * float(st["rz_deg"])

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
                pw = it["pivot_world"]
                M_rot = _world_orbit_matrix_euler_pivot(pw, rx, ry, rz)
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
        _world_pivot_sub = stream.create_subscription_to_pop(_on_we_update, name="morph.tbs_control_1.world_euler_pivot_rotate")
    except Exception:
        _world_pivot_state = None
        if on_completed:
            try:
                on_completed()
            except Exception:
                pass


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
        "kind": "axis",
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
        if not st or st.get("kind") != "axis":
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


def stop_all_rotate_animations() -> None:
    """전체 회전 애니메이션 강제 중지(SequenceRunner 정지/일시정지용)."""
    global _rot_animations, _update_sub
    try:
        _rot_animations.clear()
    except Exception:
        _rot_animations = {}
    if _update_sub is not None:
        try:
            _update_sub.unsubscribe()
        except Exception:
            pass
        _update_sub = None
    # 월드 피봇 회전도 같이 정리
    try:
        stop_world_pivot_rotate_animation()
    except Exception:
        pass


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
            if state.get("kind") == "lock_world_center":
                seg = (state.get("segments") or [{}])[0]
                duration = float(seg.get("duration", 0.0) or 0.0)
                delta = seg.get("delta") or (0.0, 0.0, 0.0)
                elapsed = float(state.get("elapsed_in_segment", 0.0)) + float(dt)
                t = 1.0 if duration <= 1e-9 else min(1.0, max(0.0, elapsed / duration))
                state["elapsed_in_segment"] = elapsed

                rx = float(delta[0]) * t
                ry = float(delta[1]) * t
                rz = float(delta[2]) * t

                base_rot = state["start_rot"]
                current_rot = Gf.Vec3f(base_rot[0] + rx, base_rot[1] + ry, base_rot[2] + rz)
                _set_prim_rotate_xyz(prim, current_rot)

                tc_now = _get_current_time_code()
                desired = state.get("desired_center")
                cur = _prim_world_bbox_center(stage, prim, tc_now)
                if desired is not None and cur is not None:
                    dw = Gf.Vec3d(float(desired[0] - cur[0]), float(desired[1] - cur[1]), float(desired[2] - cur[2]))
                    try:
                        from . import sequence_engine as _se
                        dl = _se._world_delta_to_tbs_offset_translate_delta(prim, dw, tc_now)  # type: ignore[attr-defined]
                        pos = _get_prim_local_translate(prim)
                        _set_prim_translate(prim, Gf.Vec3f(pos[0] + dl[0], pos[1] + dl[1], pos[2] + dl[2]))
                    except Exception:
                        pass

                if elapsed >= duration:
                    cb = state.get("on_completed")
                    if cb:
                        try:
                            cb()
                        except Exception:
                            pass
                    to_remove.append(prim_path)
                continue
            if state.get("kind") == "pivot_local":
                seg = (state.get("segments") or [{}])[0]
                duration = float(seg.get("duration", 0.0) or 0.0)
                delta = seg.get("delta") or (0.0, 0.0, 0.0)
                elapsed = float(state.get("elapsed_in_segment", 0.0)) + float(dt)
                t = 1.0 if duration <= 1e-9 else min(1.0, max(0.0, elapsed / duration))
                state["elapsed_in_segment"] = elapsed

                rx = float(delta[0]) * t
                ry = float(delta[1]) * t
                rz = float(delta[2]) * t
                # 회전은 기존 ROTATE XYZ에 델타 누적(절대값)
                base_rot = state["start_rot"]
                current_rot = Gf.Vec3f(base_rot[0] + rx, base_rot[1] + ry, base_rot[2] + rz)

                # translate 보정은 TBS_OFFSET op 순서에 따라 달라진다.
                # - TR(Translate->Rotate): p(t)=R(t)*(P+T(t))  목표 p(t)=P+T0
                #     => T(t)=inv(R(t))*(P+T0)-P
                # - RT(Rotate->Translate): p(t)=R(t)*P + T(t) 목표 p(t)=P+T0
                #     => T(t)=T0 + P - R(t)*P
                pivot = state["pivot_local"]
                base_pos = state["start_pos"]
                R = _matrix_from_rotate_xyz_deg((rx, ry, rz))
                order = str(state.get("op_order", "TR")).upper()
                if order == "RT":
                    p_rot = R.Transform(Gf.Vec3d(float(pivot[0]), float(pivot[1]), float(pivot[2])))
                    current_pos = Gf.Vec3f(
                        float(base_pos[0]) + float(pivot[0]) - float(p_rot[0]),
                        float(base_pos[1]) + float(pivot[1]) - float(p_rot[1]),
                        float(base_pos[2]) + float(pivot[2]) - float(p_rot[2]),
                    )
                else:
                    try:
                        R_inv = R.GetInverse()
                    except Exception:
                        R_inv = None
                    if R_inv is None:
                        R_inv = Gf.Matrix4d(1.0)
                    p_plus_t0 = Gf.Vec3d(
                        float(pivot[0]) + float(base_pos[0]),
                        float(pivot[1]) + float(base_pos[1]),
                        float(pivot[2]) + float(base_pos[2]),
                    )
                    tgt = R_inv.Transform(p_plus_t0)
                    current_pos = Gf.Vec3f(
                        float(tgt[0]) - float(pivot[0]),
                        float(tgt[1]) - float(pivot[1]),
                        float(tgt[2]) - float(pivot[2]),
                    )
                _set_prim_translate(prim, current_pos)
                _set_prim_rotate_xyz(prim, current_rot)

                if elapsed >= duration:
                    cb = state.get("on_completed")
                    if cb:
                        try:
                            cb()
                        except Exception:
                            pass
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
