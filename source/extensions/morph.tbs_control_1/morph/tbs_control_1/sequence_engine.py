# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
Sequence Engine (TBS Control)

목표:
- 사용자가 정의한 step 리스트를 순서대로 실행 (USD 타임라인 + 코드 기반 이동/회전)
- step 완료 콜백을 기반으로 다음 step 실행 (체이닝)
- JSON으로 저장/로드 가능한 step 스키마 제공

지원 step 타입(최소):
- USD_TIMELINE: USD 저장 애니메이션을 프레임 구간 재생 (수동/자동)
- MOVE: 코드 기반 직선 이동 (translate_animation)
- ROTATE: 각 prim의 로컬 회전축(TBS_OFFSET rotateXYZ) 기준 제자리 회전 (rx/ry/rz delta 적용)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import omni.kit.app as kit_app
import omni.usd as ou
from pxr import Usd, UsdGeom, Gf

from .prim_info import safe_str
from .translate_animation import run_prim_translate_animation, stop_prim_translate_animation
from .rotate_animation import (
    run_prim_rotate_animation,
    stop_prim_rotate_animation,
    run_world_pivot_rotate_animation,
    stop_world_pivot_rotate_animation,
)
from . import usd_animation_control

_OFFSET_SUFFIX = "TBS_OFFSET"


def _op_value_at_time(op, time_code: Usd.TimeCode):
    """xform op의 time_code 시점 값을 반환. (tuple/vec 등)"""
    try:
        return op.Get(time_code)
    except Exception:
        return op.Get()


def _matrix_from_translate(v) -> Gf.Matrix4d:
    m = Gf.Matrix4d(1.0)
    if v is not None and hasattr(v, "__len__") and len(v) >= 3:
        m.SetTranslateOnly(Gf.Vec3d(float(v[0]), float(v[1]), float(v[2])))
    return m


def _matrix_from_rotate_xyz(v) -> Gf.Matrix4d:
    """Euler XYZ (degrees) -> 4x4 rotation matrix."""
    m = Gf.Matrix4d(1.0)
    if v is not None and hasattr(v, "__len__") and len(v) >= 3:
        try:
            # Gf.Rotation(axis, angle): angle in degrees
            r = (
                Gf.Rotation(Gf.Vec3d(1, 0, 0), float(v[0]))
                * Gf.Rotation(Gf.Vec3d(0, 1, 0), float(v[1]))
                * Gf.Rotation(Gf.Vec3d(0, 0, 1), float(v[2]))
            )
            m.SetRotateOnly(r)
        except Exception:
            pass
    return m


def _matrix_from_scale(v) -> Gf.Matrix4d:
    m = Gf.Matrix4d(1.0)
    if v is not None and hasattr(v, "__len__") and len(v) >= 3:
        m.SetScale(Gf.Vec3d(float(v[0]), float(v[1]), float(v[2])))
    return m


def _xform_op_to_matrix_at_time(op, time_code: Usd.TimeCode) -> Gf.Matrix4d:
    """단일 xform op -> 4x4 (USD op 순서와 _compose_xform_segment 규칙에 맞춤)."""
    try:
        t = op.GetOpType()
        val = _op_value_at_time(op, time_code)
        if t == UsdGeom.XformOp.TypeTranslate:
            return _matrix_from_translate(val)
        if t == UsdGeom.XformOp.TypeRotateXYZ:
            return _matrix_from_rotate_xyz(val)
        if t == UsdGeom.XformOp.TypeScale:
            return _matrix_from_scale(val)
    except Exception:
        pass
    return Gf.Matrix4d(1.0)


def _tbs_op_indices(prim: Usd.Prim) -> List[int]:
    """이름에 TBS_OFFSET이 들어간 xform op 인덱스(오름차순)."""
    out: List[int] = []
    try:
        x = UsdGeom.Xformable(prim)
        ops = list(x.GetOrderedXformOps()) if x else []
        for i, op in enumerate(ops):
            try:
                if _OFFSET_SUFFIX in op.GetName():
                    out.append(i)
            except Exception:
                pass
    except Exception:
        pass
    return out


def _tbs_indices_consecutive(idxs: List[int]) -> bool:
    if len(idxs) <= 1:
        return True
    for a, b in zip(idxs, idxs[1:]):
        if b != a + 1:
            return False
    return True


def _compose_xform_segment(prim: Usd.Prim, lo: int, hi: int, time_code: Usd.TimeCode) -> Gf.Matrix4d:
    """
    xform op 인덱스 [lo..hi]를 USD 적용 순서대로 합성.
    최종 행렬 = M_hi * M_{hi-1} * ... * M_lo (열 벡터, 왼쪽 곱).
    """
    if lo > hi:
        return Gf.Matrix4d(1.0)
    try:
        x = UsdGeom.Xformable(prim)
        ops = list(x.GetOrderedXformOps()) if x else []
        m = Gf.Matrix4d(1.0)
        for idx in range(lo, hi + 1):
            if 0 <= idx < len(ops):
                m = _xform_op_to_matrix_at_time(ops[idx], time_code) * m
        return m
    except Exception:
        return Gf.Matrix4d(1.0)


def _compute_rest_matrix_at_time(prim: Usd.Prim, time_code: Usd.TimeCode) -> Gf.Matrix4d:
    """TBS_OFFSET op 이후의 op들만 곱한 로컬 행렬 (start_frame 시점)."""
    try:
        idxs = _tbs_op_indices(prim)
        if not idxs:
            return Gf.Matrix4d(1.0)
        last_tbs = idxs[-1]
        x = UsdGeom.Xformable(prim)
        ops = list(x.GetOrderedXformOps()) if x else []
        n = len(ops)
        return _compose_xform_segment(prim, last_tbs + 1, n - 1, time_code)
    except Exception:
        return Gf.Matrix4d(1.0)


def _set_tbs_span_matrix(prim: Usd.Prim, first: int, last: int, M_tbs: Gf.Matrix4d) -> None:
    """
    TBS op 구간 [first..last]에 해당하는 합성 행렬이 M_tbs가 되도록 translate/rotateXYZ만 설정.
    지원: Translate+RotateXYZ 인접 2개(순서 두 가지), 또는 단일 Rotate/Translate.
    """
    try:
        x = UsdGeom.Xformable(prim)
        ops = list(x.GetOrderedXformOps()) if x else []
        span = [ops[i] for i in range(first, last + 1) if 0 <= i < len(ops)]
        if not span:
            return

        def _set_rot(op, r3: Gf.Matrix3d) -> None:
            rx, ry, rz = _rotation_matrix_to_euler_xyz_degrees(r3)
            op.Set(Gf.Vec3f(float(rx), float(ry), float(rz)))

        if len(span) == 1:
            op = span[0]
            tt = op.GetOpType()
            if tt == UsdGeom.XformOp.TypeRotateXYZ:
                _set_rot(op, M_tbs.ExtractRotationMatrix())
            elif tt == UsdGeom.XformOp.TypeTranslate:
                tr = M_tbs.ExtractTranslation()
                op.Set(Gf.Vec3d(float(tr[0]), float(tr[1]), float(tr[2])))
            return

        if len(span) == 2:
            t0, t1 = span[0].GetOpType(), span[1].GetOpType()
            if t0 == UsdGeom.XformOp.TypeTranslate and t1 == UsdGeom.XformOp.TypeRotateXYZ:
                # 적용: R * (T * p) -> M_tbs = R * T
                rm = M_tbs.ExtractRotationMatrix()
                tw = M_tbs.ExtractTranslation()
                r_inv = rm.GetInverse()
                if r_inv is not None:
                    tl = r_inv * Gf.Vec3d(float(tw[0]), float(tw[1]), float(tw[2]))
                    span[0].Set(Gf.Vec3d(float(tl[0]), float(tl[1]), float(tl[2])))
                _set_rot(span[1], rm)
                return
            if t0 == UsdGeom.XformOp.TypeRotateXYZ and t1 == UsdGeom.XformOp.TypeTranslate:
                # M_tbs = T * R
                rm = M_tbs.ExtractRotationMatrix()
                tw = M_tbs.ExtractTranslation()
                _set_rot(span[0], rm)
                span[1].Set(Gf.Vec3d(float(tw[0]), float(tw[1]), float(tw[2])))
                return

        # 그 외: span 안의 첫 Translate / 첫 RotateXYZ만 갱신 (흔한 케이스 외 fallback)
        tr_op = next((o for o in span if o.GetOpType() == UsdGeom.XformOp.TypeTranslate), None)
        rot_op = next((o for o in span if o.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ), None)
        if tr_op and rot_op:
            i_tr, i_ro = span.index(tr_op), span.index(rot_op)
            if i_tr < i_ro:
                rm = M_tbs.ExtractRotationMatrix()
                tw = M_tbs.ExtractTranslation()
                r_inv = rm.GetInverse()
                if r_inv is not None:
                    tl = r_inv * Gf.Vec3d(float(tw[0]), float(tw[1]), float(tw[2]))
                    tr_op.Set(Gf.Vec3d(float(tl[0]), float(tl[1]), float(tl[2])))
                _set_rot(rot_op, rm)
            else:
                rm = M_tbs.ExtractRotationMatrix()
                tw = M_tbs.ExtractTranslation()
                _set_rot(rot_op, rm)
                tr_op.Set(Gf.Vec3d(float(tw[0]), float(tw[1]), float(tw[2])))
        elif rot_op:
            _set_rot(rot_op, M_tbs.ExtractRotationMatrix())
        elif tr_op:
            tw2 = M_tbs.ExtractTranslation()
            tr_op.Set(Gf.Vec3d(float(tw2[0]), float(tw2[1]), float(tw2[2])))
    except Exception:
        pass


def _apply_tbs_for_target_local_matrix(prim: Usd.Prim, M_local_target: Gf.Matrix4d, time_code: Usd.TimeCode) -> bool:
    """
    목표 부모-상대 로컬 행렬 M_local_target에 맞추도록 TBS_OFFSET 구간만 조정.

    M_local = M_after * M_tbs * M_before  이므로
    M_tbs = inv(M_after) * M_local_target * inv(M_before)
    """
    try:
        idxs = _tbs_op_indices(prim)
        if not idxs:
            return False
        if not _tbs_indices_consecutive(idxs):
            return False
        first, last = idxs[0], idxs[-1]
        x = UsdGeom.Xformable(prim)
        ops = list(x.GetOrderedXformOps()) if x else []
        n = len(ops)
        M_before = _compose_xform_segment(prim, 0, first - 1, time_code) if first > 0 else Gf.Matrix4d(1.0)
        M_after = _compose_xform_segment(prim, last + 1, n - 1, time_code) if last + 1 < n else Gf.Matrix4d(1.0)
        inv_a = M_after.GetInverse()
        inv_b = M_before.GetInverse()
        if inv_a is None or inv_b is None:
            return False
        M_tbs = inv_a * M_local_target * inv_b
        _set_tbs_span_matrix(prim, first, last, M_tbs)
        return True
    except Exception:
        return False


def _apply_world_pivot_frame_for_prim(
    prim: Usd.Prim,
    M_world_target: Gf.Matrix4d,
    M_parent_world_inv: Gf.Matrix4d,
    time_code: Usd.TimeCode,
) -> None:
    """월드 목표 행렬을 부모 기준 로컬로 바꾼 뒤 TBS 오프셋만 역산해 적용."""
    try:
        M_local = M_parent_world_inv * M_world_target
        if not _apply_tbs_for_target_local_matrix(prim, M_local, time_code):
            tr = M_local.ExtractTranslation()
            r3 = M_local.ExtractRotationMatrix()
            rx, ry, rz = _rotation_matrix_to_euler_xyz_degrees(r3)
            _set_translate(prim, Gf.Vec3f(float(tr[0]), float(tr[1]), float(tr[2])))
            _set_rotate_xyz(prim, Gf.Vec3f(float(rx), float(ry), float(rz)))
    except Exception:
        pass


def _rotation_matrix_to_euler_xyz_degrees(rot_m: Gf.Matrix3d) -> Tuple[float, float, float]:
    """3x3 회전 행렬 -> Euler XYZ (degrees)."""
    import math
    try:
        # Standard 3x3 rotation matrix to Euler XYZ (degrees)
        sy = math.sqrt(rot_m[0][0] * rot_m[0][0] + rot_m[1][0] * rot_m[1][0])
        if sy > 1e-6:
            rx = math.degrees(math.atan2(rot_m[2][1], rot_m[2][2]))
            ry = math.degrees(math.atan2(-rot_m[2][0], sy))
            rz = math.degrees(math.atan2(rot_m[1][0], rot_m[0][0]))
        else:
            rx = math.degrees(math.atan2(-rot_m[1][2], rot_m[1][1]))
            ry = math.degrees(math.atan2(-rot_m[2][0], sy))
            rz = 0.0
        return (rx, ry, rz)
    except Exception:
        return (0.0, 0.0, 0.0)


def _get_current_time_code() -> Usd.TimeCode:
    """
    현재 USD timeline의 current_time을 Usd.TimeCode로 변환.
    실패 시 Default를 사용.
    """
    try:
        import omni.timeline as ot

        tl = ot.get_timeline_interface()
        if tl:
            t_sec = float(tl.get_current_time())
            return Usd.TimeCode(t_sec)
    except Exception:
        pass
    return Usd.TimeCode.Default()


def _world_delta_to_local_delta(
    prim: Usd.Prim,
    world_delta: Gf.Vec3d,
    time_code: Optional[Usd.TimeCode] = None,
) -> Gf.Vec3d:
    """
    MOVE에서 (dx,dy,dz)를 월드 벡터로 가정하고,
    로컬 translate op(TBS_OFFSET)에 넣을 delta로 변환한다.
    """
    try:
        stage = _get_stage()
        if not stage:
            return Gf.Vec3d(world_delta[0], world_delta[1], world_delta[2])
        tc = time_code if time_code is not None else Usd.TimeCode.Default()
        xform_cache = UsdGeom.XformCache(tc)
        m_local_to_world = xform_cache.GetLocalToWorldTransform(prim)
        if m_local_to_world is None:
            return Gf.Vec3d(world_delta[0], world_delta[1], world_delta[2])
        inv_m = m_local_to_world.GetInverse()
        if inv_m is None:
            return Gf.Vec3d(world_delta[0], world_delta[1], world_delta[2])

        # translation은 무시하려고 w=0인 벡터로 변환
        v4 = inv_m.Transform(Gf.Vec4d(world_delta[0], world_delta[1], world_delta[2], 0.0))
        return Gf.Vec3d(v4[0], v4[1], v4[2])
    except Exception:
        return Gf.Vec3d(world_delta[0], world_delta[1], world_delta[2])


def _world_delta_to_tbs_offset_translate_delta(
    prim: Usd.Prim,
    world_delta: Gf.Vec3d,
    time_code: Usd.TimeCode,
    eps: float = 1.0,
) -> Gf.Vec3d:
    """
    translate_animation은 TBS_OFFSET translate op 값만 바꾼다.
    prim 전체 localToWorld 역행렬로 world_delta를 바꾸면 xformOp order와 맞지 않아 축이 틀어진다.

    TBS_OFFSET translate (tx,ty,tz)에 대해 prim 원점의 월드 위치 변화의 기울기를
    수치미분으로 구한 뒤:
      world_delta ≈ (∂p/∂tx)*lx + (∂p/∂ty)*ly + (∂p/∂tz)*lz
    를 풀어 (lx,ly,lz)를 구한다.

    중요: t_op.Set() 직후에는 XformCache를 재사용하면 안 되므로 샘플마다 새 캐시를 만든다.
    """
    if not prim or not prim.IsValid() or abs(eps) < 1e-12:
        return Gf.Vec3d(world_delta[0], world_delta[1], world_delta[2])

    try:
        t_op = _get_or_create_offset_translate_op(prim)
        if not t_op:
            return _world_delta_to_local_delta(prim, world_delta, time_code=time_code)

        saved_t = t_op.Get()
        if saved_t is None:
            saved_t = (0.0, 0.0, 0.0)
        t0 = Gf.Vec3d(float(saved_t[0]), float(saved_t[1]), float(saved_t[2]))

        def _world_origin() -> Gf.Vec3d:
            cache = UsdGeom.XformCache(time_code)
            M = cache.GetLocalToWorldTransform(prim)
            if M is None:
                return Gf.Vec3d(0.0, 0.0, 0.0)
            return Gf.Vec3d(M.ExtractTranslation())

        try:
            p0 = _world_origin()
            t_op.Set(Gf.Vec3d(t0[0] + eps, t0[1], t0[2]))
            px = _world_origin()
            t_op.Set(Gf.Vec3d(t0[0], t0[1] + eps, t0[2]))
            py = _world_origin()
            t_op.Set(Gf.Vec3d(t0[0], t0[1], t0[2] + eps))
            pz = _world_origin()
        finally:
            t_op.Set(t0)

        # 열: ∂p/∂tx, ∂p/∂ty, ∂p/∂tz (월드, 단위 길이당)
        dX = (px - p0) / eps
        dY = (py - p0) / eps
        dZ = (pz - p0) / eps

        J00, J01, J02 = float(dX[0]), float(dY[0]), float(dZ[0])
        J10, J11, J12 = float(dX[1]), float(dY[1]), float(dZ[1])
        J20, J21, J22 = float(dX[2]), float(dY[2]), float(dZ[2])

        det = (
            J00 * (J11 * J22 - J12 * J21)
            - J01 * (J10 * J22 - J12 * J20)
            + J02 * (J10 * J21 - J11 * J20)
        )
        if abs(det) < 1e-18:
            return _world_delta_to_local_delta(prim, world_delta, time_code=time_code)

        inv00 = (J11 * J22 - J12 * J21) / det
        inv01 = (J02 * J21 - J01 * J22) / det
        inv02 = (J01 * J12 - J02 * J11) / det
        inv10 = (J12 * J20 - J10 * J22) / det
        inv11 = (J00 * J22 - J02 * J20) / det
        inv12 = (J02 * J10 - J00 * J12) / det
        inv20 = (J10 * J21 - J11 * J20) / det
        inv21 = (J01 * J20 - J00 * J21) / det
        inv22 = (J00 * J11 - J01 * J10) / det

        wx, wy, wz = float(world_delta[0]), float(world_delta[1]), float(world_delta[2])
        lx = inv00 * wx + inv01 * wy + inv02 * wz
        ly = inv10 * wx + inv11 * wy + inv12 * wz
        lz = inv20 * wx + inv21 * wy + inv22 * wz
        return Gf.Vec3d(lx, ly, lz)
    except Exception:
        return _world_delta_to_local_delta(prim, world_delta, time_code=time_code)


def _apply_world_space_offset_correction(prim_paths: List[str], start_frame: int) -> None:
    """
    B안: USD_TIMELINE 시작 전, MOVE/ROTATE로 움직인 prim의 월드 위치가
    타임라인 start_frame에서도 그대로 보이도록 TBS_OFFSET을 재계산해 보정.
    """
    stage = _get_stage()
    if not stage or not prim_paths:
        return
    time_start = Usd.TimeCode(float(start_frame))
    for path in prim_paths:
        try:
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsValid():
                continue
            xform = UsdGeom.Xformable(prim)
            if not xform:
                continue
            # 현재(오프셋 포함) 월드 행렬
            cache_now = UsdGeom.XformCache(Usd.TimeCode.Default())
            M_world_now = cache_now.GetLocalToWorldTransform(prim)
            if M_world_now is None:
                continue
            M_world_now = Gf.Matrix4d(M_world_now)

            # 오프셋을 0으로 두고 start_frame에서의 월드 행렬
            t_op = _get_or_create_offset_translate_op(prim)
            r_op = _get_or_create_offset_rotate_op(prim)
            if not t_op or not r_op:
                continue
            saved_t = _op_value_at_time(t_op, Usd.TimeCode.Default())
            saved_r = _op_value_at_time(r_op, Usd.TimeCode.Default())
            try:
                t_op.Set(Gf.Vec3d(0, 0, 0))
                r_op.Set(Gf.Vec3f(0, 0, 0))
                cache_start = UsdGeom.XformCache(time_start)
                M_base_start = cache_start.GetLocalToWorldTransform(prim)
                if M_base_start is None:
                    continue
                M_base_start = Gf.Matrix4d(M_base_start)
            finally:
                if saved_t is not None:
                    t_op.Set(Gf.Vec3d(float(saved_t[0]), float(saved_t[1]), float(saved_t[2])))
                if saved_r is not None:
                    r_op.Set(Gf.Vec3f(float(saved_r[0]), float(saved_r[1]), float(saved_r[2])))

            Rest = _compute_rest_matrix_at_time(prim, time_start)
            Rest_inv = Rest.GetInverse()
            if Rest_inv is None:
                continue
            # O = Rest * (M_base_start)^{-1} * M_world_now * Rest^{-1}
            M_base_inv = M_base_start.GetInverse()
            if M_base_inv is None:
                continue
            O = Rest * M_base_inv * M_world_now * Rest_inv

            trans = O.ExtractTranslation()
            rot3 = O.ExtractRotationMatrix()
            rx, ry, rz = _rotation_matrix_to_euler_xyz_degrees(rot3)

            t_op.Set(Gf.Vec3d(float(trans[0]), float(trans[1]), float(trans[2])))
            r_op.Set(Gf.Vec3f(float(rx), float(ry), float(rz)))
        except Exception:
            pass


def _get_or_create_offset_translate_op(prim: Usd.Prim):
    x = UsdGeom.Xformable(prim)
    if not x:
        return None
    try:
        for op in x.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeTranslate and _OFFSET_SUFFIX in op.GetName():
                return op
    except Exception:
        pass
    try:
        return x.AddTranslateOp(opSuffix=_OFFSET_SUFFIX)
    except Exception:
        return None


def _get_or_create_offset_rotate_op(prim: Usd.Prim):
    x = UsdGeom.Xformable(prim)
    if not x:
        return None
    try:
        for op in x.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ and _OFFSET_SUFFIX in op.GetName():
                return op
    except Exception:
        pass
    try:
        return x.AddRotateXYZOp(opSuffix=_OFFSET_SUFFIX)
    except Exception:
        return None


def _get_stage() -> Optional[Usd.Stage]:
    ctx = ou.get_context()
    return ctx.get_stage() if ctx else None


def resolve_prim_paths(identifier: str) -> List[str]:
    """
    prim 식별자(identifier)로 경로 리스트 반환.
    - '/World/...'로 시작하면 해당 경로 1개만 유효할 때 반환
    - 그 외에는 prim.GetName() == identifier 인 모든 prim 경로를 반환 (동일 이름 다중 지원)
    """
    stage = _get_stage()
    if not stage:
        return []
    name = (identifier or "").strip()
    if not name:
        return []
    try:
        if name.startswith("/"):
            prim = stage.GetPrimAtPath(name)
            return [name] if prim and prim.IsValid() else []
    except Exception:
        pass

    result: List[str] = []

    def visit(prim: Usd.Prim) -> None:
        try:
            if prim.GetPath().pathString == "/":
                for ch in prim.GetChildren():
                    visit(ch)
                return
        except Exception:
            return
        try:
            if safe_str(prim.GetName()) == name:
                result.append(str(prim.GetPath()))
        except Exception:
            pass
        try:
            for ch in prim.GetChildren():
                visit(ch)
        except Exception:
            pass

    try:
        root = stage.GetPseudoRoot()
        if root:
            visit(root)
    except Exception:
        pass
    return result


def resolve_prim_paths_multi(identifier_text: str) -> List[str]:
    """','로 구분된 prim 식별자를 모두 해석해 prim path 목록 반환."""
    out: List[str] = []
    seen = set()
    for token in (identifier_text or "").split(","):
        key = token.strip()
        if not key:
            continue
        for p in resolve_prim_paths(key):
            if p and p not in seen:
                seen.add(p)
                out.append(p)
    return out


def _expand_with_descendants(paths_csv: str) -> List[str]:
    """입력한 prim 경로(또는 prim name)를 포함해 하위 prim까지 모두 반환."""
    stage = _get_stage()
    if not stage:
        return []
    roots = resolve_prim_paths_multi(paths_csv)
    if not roots:
        return []

    out: List[str] = []
    seen = set()

    def visit(prim: Usd.Prim) -> None:
        try:
            p = str(prim.GetPath())
        except Exception:
            return
        if p not in seen:
            seen.add(p)
            out.append(p)
        try:
            for ch in prim.GetChildren():
                visit(ch)
        except Exception:
            pass

    for rp in roots:
        try:
            prim = stage.GetPrimAtPath(rp)
            if prim and prim.IsValid():
                visit(prim)
        except Exception:
            pass
    return out


def _set_prim_visible(path: str, visible: bool) -> None:
    stage = _get_stage()
    if not stage:
        return
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return
    try:
        img = UsdGeom.Imageable(prim)
        if not img:
            return
        if visible:
            img.MakeVisible()
        else:
            img.MakeInvisible()
    except Exception:
        pass


def _get_translate(prim: Usd.Prim) -> Gf.Vec3f:
    if not prim or not prim.IsValid():
        return Gf.Vec3f(0, 0, 0)
    # 시퀀서의 MOVE/ROTATE는 타임라인에 덮어써지지 않는 오프셋 op만 사용
    try:
        op = _get_or_create_offset_translate_op(prim)
        if op:
            v = op.Get()
            return Gf.Vec3f(v[0], v[1], v[2]) if v is not None else Gf.Vec3f(0, 0, 0)
    except Exception:
        pass
    return Gf.Vec3f(0, 0, 0)


def _set_translate(prim: Usd.Prim, v: Gf.Vec3f) -> None:
    if not prim or not prim.IsValid():
        return
    try:
        # scale이 있는 prim에서 translate/rotate가 먼저 적용되는 경우 경고가 반복될 수 있어,
        # 가능한 환경에서는 common TRS로 한 번 정리(동일 값으로 재기록)한다.
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
            idx_scale = None
            idx_tr = None
            for i, op in enumerate(ops):
                t = op.GetOpType()
                if idx_scale is None and t == UsdGeom.XformOp.TypeScale:
                    idx_scale = i
                if idx_tr is None and t in (UsdGeom.XformOp.TypeTranslate, UsdGeom.XformOp.TypeRotateXYZ):
                    idx_tr = i
            if idx_scale is not None and idx_tr is not None and idx_tr < idx_scale:
                api0 = UsdGeom.XformCommonAPI(prim)
                if api0:
                    t0, r0, s0, p0, ro0 = api0.GetXformVectors(Usd.TimeCode.Default())
                    api0.SetXformVectors(t0, r0, s0, p0, ro0, Usd.TimeCode.Default())
        except Exception:
            pass
        op = _get_or_create_offset_translate_op(prim)
        if op:
            op.Set(Gf.Vec3f(float(v[0]), float(v[1]), float(v[2])))
            return
    except Exception:
        pass
    x = UsdGeom.Xformable(prim)
    if not x:
        return
    op = None
    for o in x.GetOrderedXformOps():
        if o.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            op = o
            break
    if op is None:
        op = x.AddTranslateOp()
    op.Set(Gf.Vec3f(v[0], v[1], v[2]))


def _get_rotate_xyz(prim: Usd.Prim) -> Gf.Vec3f:
    if not prim or not prim.IsValid():
        return Gf.Vec3f(0, 0, 0)
    try:
        op = _get_or_create_offset_rotate_op(prim)
        if op:
            v = op.Get()
            return Gf.Vec3f(v[0], v[1], v[2]) if v is not None else Gf.Vec3f(0, 0, 0)
    except Exception:
        pass
    return Gf.Vec3f(0, 0, 0)


def _set_rotate_xyz(prim: Usd.Prim, v: Gf.Vec3f) -> None:
    if not prim or not prim.IsValid():
        return
    try:
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
            idx_scale = None
            idx_tr = None
            for i, op in enumerate(ops):
                t = op.GetOpType()
                if idx_scale is None and t == UsdGeom.XformOp.TypeScale:
                    idx_scale = i
                if idx_tr is None and t in (UsdGeom.XformOp.TypeTranslate, UsdGeom.XformOp.TypeRotateXYZ):
                    idx_tr = i
            if idx_scale is not None and idx_tr is not None and idx_tr < idx_scale:
                api0 = UsdGeom.XformCommonAPI(prim)
                if api0:
                    t0, r0, s0, p0, ro0 = api0.GetXformVectors(Usd.TimeCode.Default())
                    api0.SetXformVectors(t0, r0, s0, p0, ro0, Usd.TimeCode.Default())
        except Exception:
            pass
        op = _get_or_create_offset_rotate_op(prim)
        if op:
            op.Set(Gf.Vec3f(float(v[0]), float(v[1]), float(v[2])))
            return
    except Exception:
        pass
    x = UsdGeom.Xformable(prim)
    if not x:
        return
    op = None
    for o in x.GetOrderedXformOps():
        if o.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ:
            op = o
            break
    if op is None:
        op = x.AddRotateXYZOp()
    op.Set(Gf.Vec3f(v[0], v[1], v[2]))


@dataclass
class SequenceRunner:
    """
    단일 시퀀스를 순차 실행하는 러너.
    - 병렬 실행은 여기서 하지 않음(필요하면 별도 정책으로 확장)
    """

    on_sequence_completed: Optional[Callable[[], None]] = None

    def __post_init__(self) -> None:
        self._running = False
        self._steps: List[Dict[str, Any]] = []
        self._index = 0
        self._baseline: Dict[str, Tuple[Gf.Vec3f, Gf.Vec3f]] = {}
        self._next_tick_sub = None
        self._pending_delay_sub = None
        self._pending_unhide_sub = None
        self._hidden_refcount: Dict[str, int] = {}

    def is_running(self) -> bool:
        return self._running

    def pause(self) -> None:
        """진행 중인 애니메이션만 멈춘다. (위치/타임라인은 초기화하지 않음)"""
        self._running = False
        if self._next_tick_sub is not None:
            try:
                self._next_tick_sub.unsubscribe()
            except Exception:
                pass
            self._next_tick_sub = None
        if self._pending_unhide_sub is not None:
            try:
                self._pending_unhide_sub.unsubscribe()
            except Exception:
                pass
            self._pending_unhide_sub = None
        if self._pending_delay_sub is not None:
            try:
                self._pending_delay_sub.unsubscribe()
            except Exception:
                pass
            self._pending_delay_sub = None
        try:
            stop_world_pivot_rotate_animation()
        except Exception:
            pass
        # 진행 중인 코드 애니메이션은 안전하게 정리
        try:
            step = self._steps[self._index] if 0 <= self._index < len(self._steps) else None
        except Exception:
            step = None
        if isinstance(step, dict):
            t = (step.get("type") or "").upper()
            if t == "MOVE":
                for p in resolve_prim_paths_multi(str(step.get("prim", ""))):
                    stop_prim_translate_animation(p)
            elif t == "ROTATE":
                for p in resolve_prim_paths_multi(str(step.get("prim", ""))):
                    stop_prim_rotate_animation(p)
            elif t == "USD_TIMELINE":
                usd_animation_control.stop_usd_animation()

    def stop(self) -> None:
        """완전 중지: 객체 위치/회전 상태 + 타임라인을 실행 전(초기) 상태로 초기화한다."""
        # 먼저 현재 진행 중인 것만 멈춤(=pause와 동일한 정리)
        self.pause()

        # baseline 복원(초기 위치/회전)
        try:
            if not self._baseline:
                self._capture_baseline(force=False)
            self._restore_baseline()
        except Exception:
            pass

        # 타임라인 0으로 초기화 + 일시정지
        try:
            usd_animation_control.stop_usd_animation()
            usd_animation_control.reset_timeline_to_zero()
        except Exception:
            pass

        # hide 상태도 초기화
        try:
            self._clear_all_hides()
        except Exception:
            pass

    def _clear_all_hides(self) -> None:
        """현재 refcount 기준으로 숨김 상태를 모두 해제."""
        if self._pending_unhide_sub is not None:
            try:
                self._pending_unhide_sub.unsubscribe()
            except Exception:
                pass
            self._pending_unhide_sub = None

        for p in list(self._hidden_refcount.keys()):
            _set_prim_visible(p, True)
        self._hidden_refcount.clear()

    def _step_hide_paths(self, step: Dict[str, Any]) -> List[str]:
        if not bool(step.get("hide_enabled", False)):
            return []
        return _expand_with_descendants(str(step.get("hide_prims", "")))

    def _apply_hide_for_step(self, step: Dict[str, Any]) -> List[str]:
        paths = self._step_hide_paths(step)
        for p in paths:
            self._hidden_refcount[p] = self._hidden_refcount.get(p, 0) + 1
            _set_prim_visible(p, False)
        return paths

    def _schedule_unhide(self, paths: List[str], delay_sec: float = 0.2) -> None:
        """delay_sec 후 숨김 refcount를 1 감소시키고 0이면 다시 표시."""
        if not paths:
            return

        if self._pending_unhide_sub is not None:
            try:
                self._pending_unhide_sub.unsubscribe()
            except Exception:
                pass
            self._pending_unhide_sub = None

        elapsed = {"t": 0.0}

        def _on_update(e):
            payload = getattr(e, "payload", None) or {}
            dt = payload.get("dt", 0.0)
            if dt <= 0:
                dt = 1.0 / 60.0
            elapsed["t"] += dt
            if elapsed["t"] < delay_sec:
                return

            if self._pending_unhide_sub is not None:
                try:
                    self._pending_unhide_sub.unsubscribe()
                except Exception:
                    pass
                self._pending_unhide_sub = None

            for p in paths:
                cnt = self._hidden_refcount.get(p, 0) - 1
                if cnt <= 0:
                    self._hidden_refcount.pop(p, None)
                    _set_prim_visible(p, True)
                else:
                    self._hidden_refcount[p] = cnt

        try:
            self._pending_unhide_sub = kit_app.get_app().get_update_event_stream().create_subscription_to_pop(
                _on_update,
                name="morph.tbs_control_1.sequence_engine.unhide_delay",
            )
        except Exception:
            # fallback: delay 없이 즉시 복원
            for p in paths:
                cnt = self._hidden_refcount.get(p, 0) - 1
                if cnt <= 0:
                    self._hidden_refcount.pop(p, None)
                    _set_prim_visible(p, True)
                else:
                    self._hidden_refcount[p] = cnt

    def _call_next_frame(self, fn: Callable[[], None]) -> None:
        """update 콜백 재진입을 피하기 위해 다음 프레임(post_update)에 호출."""
        try:
            if self._next_tick_sub is not None:
                try:
                    self._next_tick_sub.unsubscribe()
                except Exception:
                    pass
                self._next_tick_sub = None

            def _do(_e=None):
                if self._next_tick_sub is not None:
                    try:
                        self._next_tick_sub.unsubscribe()
                    except Exception:
                        pass
                    self._next_tick_sub = None
                try:
                    fn()
                except Exception:
                    pass

            self._next_tick_sub = kit_app.get_app().get_post_update_event_stream().create_subscription_to_pop(
                _do,
                name="morph.tbs_control_1.sequence_engine.next_frame",
            )
        except Exception:
            fn()

    def run(self, steps: List[Dict[str, Any]]) -> None:
        """시퀀스 실행 시작."""
        self.stop()
        self._steps = list(steps or [])
        self._index = 0
        # 실행 버튼을 누르면 타임라인은 항상 0에서 시작
        try:
            usd_animation_control.stop_usd_animation()
            usd_animation_control.reset_timeline_to_zero()
        except Exception:
            pass
        # 타임라인 time=0 적용이 "다음 프레임"에 평가되는 경우가 있어,
        # 프림 baseline 복원/시퀀스 시작을 다음 프레임으로 지연해 덮어쓰기/미복원 문제를 방지한다.
        def _start():
            # baseline은 '최초 상태'를 보존해야 하므로 매 실행마다 덮어쓰지 않는다.
            # 다만 스텝 편집으로 새 prim이 등장할 수 있으니, baseline에 없는 prim만 보강 캡처한다.
            self._capture_baseline(force=False)
            self._restore_baseline()
            self._running = True
            self._run_current_step()

        self._call_next_frame(_start)

    def reset_baseline(self) -> None:
        """현재 상태를 새로운 최초 상태로 다시 캡처."""
        self._capture_baseline(force=True)

    def _capture_baseline(self, force: bool = False) -> None:
        """현재 스테이지의 prim transform을 baseline으로 저장. force=True면 기존 baseline을 덮어씀."""
        if force:
            self._baseline.clear()
        stage = _get_stage()
        if not stage:
            return
        # 시퀀스에 등장하는 prim들을 수집
        for step in self._steps:
            t = str(step.get("type") or "").upper()
            if t in ("MOVE", "ROTATE"):
                prim_id_text = str(step.get("prim") or "")
                for path in resolve_prim_paths_multi(prim_id_text):
                    try:
                        if not force and path in self._baseline:
                            continue
                        prim = stage.GetPrimAtPath(path)
                        if not prim or not prim.IsValid():
                            continue
                        self._baseline[path] = (_get_translate(prim), _get_rotate_xyz(prim))
                    except Exception:
                        pass

    def _restore_baseline(self) -> None:
        """baseline으로 transform을 되돌림. (실행을 항상 초기값부터 재현하기 위함)"""
        stage = _get_stage()
        if not stage:
            return
        for path, (t, r) in list(self._baseline.items()):
            try:
                prim = stage.GetPrimAtPath(path)
                if prim and prim.IsValid():
                    _set_translate(prim, t)
                    _set_rotate_xyz(prim, r)
            except Exception:
                pass

    # ---------------- internal ----------------

    def _run_current_step(self) -> None:
        if not self._running:
            return
        if self._index >= len(self._steps):
            self._running = False
            cb = self.on_sequence_completed
            if cb:
                try:
                    cb()
                except Exception:
                    pass
            return

        step = self._steps[self._index] or {}
        t = str(step.get("type") or "").upper()
        current_hide_paths = self._apply_hide_for_step(step)

        def _done():
            if not self._running:
                return
            next_idx = self._index + 1
            is_last = next_idx >= len(self._steps)
            if not is_last:
                next_step = self._steps[next_idx] if 0 <= next_idx < len(self._steps) else {}
                next_hide_set = set(self._step_hide_paths(next_step or {}))
                to_unhide = [p for p in current_hide_paths if p not in next_hide_set]
                # 마지막 step이면 복원하지 않아서 숨김 상태 유지
                if to_unhide:
                    self._schedule_unhide(to_unhide, delay_sec=0.2)
            # update 이벤트 내부에서 바로 다음 step을 시작하면, 다음 MOVE/ROTATE가 무시되는 등
            # 재진입 문제가 생길 수 있어 next frame으로 넘긴다.
            def _advance():
                if not self._running:
                    return
                self._index += 1
                self._run_current_step()

            self._call_next_frame(_advance)

        if t == "USD_TIMELINE":
            mode = str(step.get("mode") or "MANUAL").upper()  # MANUAL|AUTO
            loop = bool(step.get("loop", False))
            if mode == "AUTO":
                rng = usd_animation_control.resolve_saved_animation_frame_range()
                if not rng:
                    _done()
                    return
                start, end = int(rng[0]), int(rng[1])
            else:
                start = int(step.get("start_frame", 0))
                end = int(step.get("end_frame", 0))
                if end <= start:
                    _done()
                    return
            # B안: MOVE/ROTATE로 움직인 prim들이 타임라인 시작 시에도 같은 월드 위치를 유지하도록 오프셋 보정
            try:
                _apply_world_space_offset_correction(list(self._baseline.keys()), start)
            except Exception:
                pass
            usd_animation_control.play_usd_animation(
                start_frame=start,
                end_frame=end,
                loop=loop,
                on_completed=_done if not loop else None,
            )
            return

        if t == "DELAY":
            delay_sec = float(step.get("duration", 1.0))
            if delay_sec <= 0:
                _done()
                return

            elapsed = {"t": 0.0}

            # 이전 delay 구독이 남아있으면 정리
            if self._pending_delay_sub is not None:
                try:
                    self._pending_delay_sub.unsubscribe()
                except Exception:
                    pass
                self._pending_delay_sub = None

            def _on_update(e):
                if not self._running:
                    return
                payload = getattr(e, "payload", None) or {}
                dt = payload.get("dt", 0.0)
                if dt <= 0:
                    dt = 1.0 / 60.0
                elapsed["t"] += dt
                if elapsed["t"] < delay_sec:
                    return

                # 완료 시 구독 해제 후 다음 step 진행
                if self._pending_delay_sub is not None:
                    try:
                        self._pending_delay_sub.unsubscribe()
                    except Exception:
                        pass
                    self._pending_delay_sub = None
                _done()

            try:
                self._pending_delay_sub = kit_app.get_app().get_update_event_stream().create_subscription_to_pop(
                    _on_update,
                    name="morph.tbs_control_1.sequence_engine.delay",
                )
            except Exception:
                # fallback: 즉시 진행
                self._pending_delay_sub = None
                _done()
            return

        if t == "MOVE":
            prim_id = str(step.get("prim") or "")
            duration = float(step.get("duration", 1.0))
            dx = float(step.get("dx", 0.0))
            dy = float(step.get("dy", 0.0))
            dz = float(step.get("dz", 0.0))
            stage = _get_stage()
            paths = resolve_prim_paths_multi(prim_id)
            if not paths:
                _done()
                return
            # 여러 prim에 적용 시 마지막 prim 완료를 기준으로 다음으로 넘어감
            remaining = {"n": len(paths)}

            def _one_done():
                remaining["n"] -= 1
                if remaining["n"] <= 0:
                    _done()

            for p in paths:
                prim = stage.GetPrimAtPath(p) if stage else None
                world_delta = Gf.Vec3d(dx, dy, dz)
                # dx/dy/dz = 월드 이동. TBS_OFFSET translate만 조작하므로
                # prim 전체 L2W 역변환이 아니라 "offset op → 월드 원점" 자코비안으로 역산.
                tc = _get_current_time_code()
                local_delta = (
                    _world_delta_to_tbs_offset_translate_delta(prim, world_delta, tc)
                    if prim
                    else world_delta
                )
                stop_prim_translate_animation(p)
                run_prim_translate_animation(
                    p,
                    [{"duration": duration, "delta": (local_delta[0], local_delta[1], local_delta[2])}],
                    loop=False,
                    on_completed=_one_done,
                )
            return

        if t == "ROTATE":
            prim_id = str(step.get("prim") or "")
            duration = float(step.get("duration", 1.0))
            rx = float(step.get("rx", 0.0))
            ry = float(step.get("ry", 0.0))
            rz = float(step.get("rz", 0.0))
            stage = _get_stage()
            paths = resolve_prim_paths_multi(prim_id)
            if not paths:
                _done()
                return
            if abs(rx) < 1e-9 and abs(ry) < 1e-9 and abs(rz) < 1e-9:
                _done()
                return

            remaining = {"n": len(paths)}

            def _one_done():
                remaining["n"] -= 1
                if remaining["n"] <= 0:
                    _done()

            # "제자리 회전": prim별 로컬 회전축(rotateXYZ op) 기준으로 rx/ry/rz를 그대로 적용
            stop_world_pivot_rotate_animation()
            for p in paths:
                stop_prim_rotate_animation(p)
                run_prim_rotate_animation(
                    p,
                    [{"duration": duration, "delta": (rx, ry, rz)}],
                    loop=False,
                    on_completed=_one_done,
                )
            return

        # 알 수 없는 step은 스킵
        _done()
