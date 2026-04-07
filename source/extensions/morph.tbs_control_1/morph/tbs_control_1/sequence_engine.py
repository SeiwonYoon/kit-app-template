# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
Sequence Engine (TBS Control)

목표:
- 사용자가 정의한 step 리스트를 순서대로 실행 (USD 타임라인 + 코드 기반 이동/회전)
- run_with_previous 로 병렬 그룹. 앵커=그룹 맨 아래 스텝. 다음 그룹 첫 줄의 step_delay_ms/1000 초는 이전 앵커 종료 후(음수면 앞당김).
- 동시 실행인 스텝의 step_delay_ms 는 그룹 리더 시작 후 오프셋(ms), 0이면 리더와 동시 시작.
- step 완료 콜백을 기반으로 다음 step 실행 (체이닝)
- JSON으로 저장/로드 가능한 step 스키마 제공

지원 step 타입(최소):
- USD_TIMELINE: USD 저장 애니메이션을 프레임 구간 재생 (수동/자동).
  · MOVE/ROTATE 직후 같은 prim에서 타임라인을 켤 때 "원래 자리로 튀었다가" 재생되는 경우,
    재생 직전 `_apply_world_space_offset_correction`(TBS_OFFSET 보정)이 돈다.
  · 보정 대상: 기본은 시퀀스에 등장한 MOVE/ROTATE prim(baseline 키) + 선택 필드 `offset_correct_prims`(콤마·공백 구분 이름/경로).
  · 전제: 해당 Xformable에 `TBS_OFFSET` translate/rotate op가 있고, 타임라인이 건 키가 그 **이후** op 구간(또는 스켈/메시만 키인 경우 부모 보정으로 부족할 수 있음).
- MOVE: 코드 기반 직선 이동 (translate_animation)
- ROTATE: (1) user_axis_rotate 미체크: prim 로컬 TBS_OFFSET rotateXYZ에 rx/ry/rz 델타(도) — 제자리 회전(기존 동작).
          (2) 체크: 스테이지 루트(월드) 고정 Euler + pivot_wx/y/z(월드) 공통 중심. rx/ry/rz(도), 애니는 t 선형 보간.

【수정 가이드】
- 새 step 타입: dict 스키마 + SequenceRunner._execute_step 분기 + translate/rotate/usd_animation 모듈
- 그룹/지연/앵커 동작: group_end, duration, execute_group, schedule 관련 로직
- UI 필드 추가: sequence_editor.py 와 스키마 키를 반드시 맞출 것

【주요 심볼 색인】
- Prim 경로: resolve_prim_paths, resolve_prim_paths_multi, split_prim_identifier_tokens, _expand_with_descendants
- 스냅샷·로컬: get_composed_local_matrix_relative_to_parent, capture_composed_local_start_snapshot_for_paths, _local_matrix4d_from_tr, _matrix4d_from_m16_list
- TBS xform: _tbs_op_indices, _compose_xform_segment, _apply_tbs_for_target_local_matrix, _set_tbs_span_matrix, _rotation_matrix_to_euler_xyz_degrees
- 오프셋/보정: _get_or_create_offset_translate_op, _get_or_create_offset_rotate_op, _get_translate / _set_translate, _get_rotate_xyz / _set_rotate_xyz, _apply_world_space_offset_correction, _apply_world_pivot_frame_for_prim
- MOVE/회전: _world_delta_to_local_delta, _world_delta_to_tbs_offset_translate_delta
- 실행기: SequenceRunner — run/stop/pause, _execute_step, _capture_baseline/_restore_baseline, _parse_start_snapshot/_apply_start_snapshot
"""

from __future__ import annotations

import math
import random
import re
import time
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
    run_world_euler_pivot_rotate_animation,
    stop_world_pivot_rotate_animation,
    run_local_euler_pivot_rotate_animation,
    run_prim_rotate_lock_world_center_animation,
    stop_all_rotate_animations,
    _matrix_from_rotate_xyz_deg,
)
from .translate_animation import stop_all_translate_animations
from .curve_animation import stop_all_curve_animations
from . import usd_animation_control
from .xform_utils import ensure_scale_xform_ops_first

_OFFSET_SUFFIX = "TBS_OFFSET"


def _prim_world_origin(prim: Usd.Prim, tc: Usd.TimeCode) -> Optional[Gf.Vec3d]:
    """
    prim의 월드 원점(현재 위치)을 반환.

    NOTE:
    - "제자리 회전"을 사용자 관점에서 가장 안정적으로 보이게 하려면,
      BBox 중심보다 prim의 월드 원점을 pivot으로 잡는 편이 덜 흔들린다.
      (원점과 형상 중심이 다를 때 BBox 중심 pivot은 시각적으로 이동하는 것처럼 보일 수 있음)
    """
    try:
        if not prim or not prim.IsValid():
            return None
        cache = UsdGeom.XformCache(tc)
        M = Gf.Matrix4d(cache.GetLocalToWorldTransform(prim))
        tr = M.ExtractTranslation()
        return Gf.Vec3d(float(tr[0]), float(tr[1]), float(tr[2]))
    except Exception:
        return None


def _prim_world_bbox_center(prim: Usd.Prim, tc: Usd.TimeCode) -> Optional[Gf.Vec3d]:
    """
    prim의 월드 BBox(Aligned) 중심점을 반환.

    사용 의도:
    - 사용자 요구: "객체 중심좌표를 축으로 회전" → 화면에서 객체가 이동하지 않게 보이려면
      보통 '형상 중심(대략)'을 pivot으로 잡는 것이 더 직관적이다.
    - prim 원점이 형상 중심과 다를 때, 원점 pivot은 회전 중에 객체가 원을 그리며 이동해 보일 수 있다.
    """
    try:
        if not prim or not prim.IsValid():
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


def _sample_step_value(step: Dict[str, Any], key: str) -> float:
    """
    key, key_min/key_max 를 지원.
    범위가 없으면 고정값 key 사용.
    """
    if f"{key}_min" in step or f"{key}_max" in step:
        lo = float(step.get(f"{key}_min", step.get(key, 0.0)))
        hi = float(step.get(f"{key}_max", step.get(key, 0.0)))
        if lo > hi:
            lo, hi = hi, lo
        return random.uniform(lo, hi)
    return float(step.get(key, 0.0))


def _group_end_index(steps: List[Dict[str, Any]], start_idx: int) -> int:
    """start_idx부터 run_with_previous 가 연속인 구간의 마지막 인덱스(앵커 = UI 최하단)."""
    g_end = start_idx
    while g_end + 1 < len(steps) and bool((steps[g_end + 1] or {}).get("run_with_previous", False)):
        g_end += 1
    return g_end


def _step_duration_sec(step: Dict[str, Any]) -> float:
    """스텝의 재생 길이(초). USD 타임라인은 프레임 구간을 타임라인 TPS로 환산."""
    t = str((step or {}).get("type") or "").upper()
    if t in ("MOVE", "ROTATE"):
        rt = (step or {}).get("_runtime_duration", None)
        if rt is not None:
            return max(1e-6, float(rt))
        if t == "MOVE":
            if "duration_max" in (step or {}):
                return max(1e-6, float((step or {}).get("duration_max", (step or {}).get("duration", 1.0))))
        return max(1e-6, float((step or {}).get("duration", 1.0)))
    if t == "DELAY":
        return max(1e-6, float((step or {}).get("duration", 1.0)))
    if t == "USD_TIMELINE":
        mode = str((step or {}).get("mode", "MANUAL")).upper()
        if mode == "AUTO":
            rng = usd_animation_control.resolve_saved_animation_frame_range()
            if not rng:
                return 1e-6
            start_f, end_f = int(rng[0]), int(rng[1])
        else:
            start_f = int((step or {}).get("start_frame", 0))
            end_f = int((step or {}).get("end_frame", 0))
        if end_f <= start_f:
            return 1e-6
        return max(usd_animation_control.frame_to_time(float(end_f - start_f)), 1e-6)
    return 1e-6


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
    """
    Euler XYZ (degrees) -> 4x4 rotation matrix.

    UsdGeomXformOp::GetOpTransform(TypeRotateXYZ) 와 동일:
    xRot * yRot * zRot (각각 GfMatrix3d(GfRotation(축, 각도))).
    Gf.Rotation 을 하나로 합성하는 방식은 USD 행렬 곱과 달라질 수 있음.
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


def _ensure_tbs_offset_ops_consecutive(prim: Usd.Prim) -> None:
    """
    TBS_OFFSET op들이 xformOpOrder 상에서 연속이 되도록 재정렬한다.

    왜 필요한가:
    - 월드 목표행렬을 "TBS_OFFSET 구간만" 역산해 적용하는 로직(_apply_tbs_for_target_local_matrix)은
      TBS_OFFSET op들이 연속인 구간(first..last)이라는 가정이 있다.
    - 에셋에 따라 op가 섞여 있으면(비연속) fallback 경로로 빠지며,
      그 경우 "이동하면서 회전"처럼 보이는 부작용이 생길 수 있다.
    """
    try:
        if not prim or not prim.IsValid():
            return
        x = UsdGeom.Xformable(prim)
        if not x:
            return
        ops = list(x.GetOrderedXformOps())
        if not ops:
            return
        idxs = _tbs_op_indices(prim)
        if not idxs or _tbs_indices_consecutive(idxs):
            return
        tbs_ops = []
        other_ops = []
        for op in ops:
            try:
                nm = op.GetName()
            except Exception:
                nm = ""
            if _OFFSET_SUFFIX in str(nm):
                tbs_ops.append(op)
            else:
                other_ops.append(op)
        if not tbs_ops:
            return
        # 기존 상대 순서 유지: non-TBS 먼저, TBS를 마지막에 연속으로 배치
        x.SetXformOpOrder(other_ops + tbs_ops)
    except Exception:
        return


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
        ensure_scale_xform_ops_first(prim)
        _ensure_tbs_offset_ops_consecutive(prim)
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
        ensure_scale_xform_ops_first(prim)
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


def get_composed_local_matrix_relative_to_parent(prim: Usd.Prim, time_code: Usd.TimeCode) -> Gf.Matrix4d:
    """
    XformCache 기준 부모 대비 로컬 4x4 (일반 translate/rotate 합성 포함).
    루트 프림은 월드=로컬로 취급.
    """
    try:
        cache = UsdGeom.XformCache(time_code)
        w = cache.GetLocalToWorldTransform(prim)
        if w is None:
            return Gf.Matrix4d(1.0)
        Mw = Gf.Matrix4d(w)
        parent = prim.GetParent()
        if not parent or not parent.IsValid():
            return Mw
        pw = cache.GetLocalToWorldTransform(Usd.Prim(parent))
        if pw is None:
            return Mw
        Mp = Gf.Matrix4d(pw)
        inv = Mp.GetInverse()
        if inv is None:
            return Mw
        return inv * Mw
    except Exception:
        return Gf.Matrix4d(1.0)


def _local_matrix4d_from_tr(t: Gf.Vec3f, r: Gf.Vec3f) -> Gf.Matrix4d:
    """부모 기준 로컬: p' = R*p + t — rotate_animation._matrix_from_rotate_xyz_deg 와 동일 Euler 순서."""
    Mr = _matrix_from_rotate_xyz_deg((float(r[0]), float(r[1]), float(r[2])))
    Mt = Gf.Matrix4d(1.0)
    Mt.SetTranslateOnly(Gf.Vec3d(float(t[0]), float(t[1]), float(t[2])))
    return Mt * Mr


def _matrix4d_from_m16_list(vals: Any) -> Optional[Gf.Matrix4d]:
    if not isinstance(vals, (list, tuple)) or len(vals) < 16:
        return None
    try:
        v = [float(vals[i]) for i in range(16)]
        return Gf.Matrix4d(
            v[0],
            v[1],
            v[2],
            v[3],
            v[4],
            v[5],
            v[6],
            v[7],
            v[8],
            v[9],
            v[10],
            v[11],
            v[12],
            v[13],
            v[14],
            v[15],
        )
    except Exception:
        return None


def capture_composed_local_start_snapshot_for_paths(
    stage: Usd.Stage,
    paths: List[str],
    time_code: Optional[Usd.TimeCode] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    현재 스테이지에서 각 prim의 부모-상대 합성 로컬 변환을 스냅샷 dict로 만든다.
    JSON _start_snapshot 용: mode=composed_local, t/r(도) + 정밀 재현용 m16.

    time_code 미지정 시 Usd.TimeCode.Default() 를 쓴다.
    타임라인 현재 시각(_get_current_time_code)으로 XformCache를 만들면, 그 시점에
    xform 샘플이 없는 레이어는 0/단위행렬로 평가되어 스냅샷이 전부 0이 되는 문제가 있다.
    뷰포트·수동 편집 상태는 보통 Default 시간에 의견이 있다.
    """
    out: Dict[str, Dict[str, Any]] = {}
    tc = time_code if time_code is not None else Usd.TimeCode.Default()
    for path in paths:
        try:
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsValid():
                continue
            M = get_composed_local_matrix_relative_to_parent(prim, tc)
            tr = M.ExtractTranslation()
            r3 = M.ExtractRotationMatrix()
            rx, ry, rz = _rotation_matrix_to_euler_xyz_degrees(r3)
            m16 = [float(M[i][j]) for i in range(4) for j in range(4)]
            out[path] = {
                "mode": "composed_local",
                "t": [float(tr[0]), float(tr[1]), float(tr[2])],
                "r": [float(rx), float(ry), float(rz)],
                "m16": m16,
            }
        except Exception:
            continue
    return out


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
    B안: USD_TIMELINE 시작 전, MOVE/ROTATE(또는 수동 지정 prim)의 현재 월드 포즈가
    타임라인 start_frame을 평가했을 때와 같아지도록 TBS_OFFSET(translate/rotate)을 재계산한다.

    타임라인에 곡선/키가 "녹화 당시 월드 기준"으로 박혀 있으면, 보정 없이는 재생 시작 시
    원 위치로 되돌아간 뒤 애니가 도는 것처럼 보인다. 이 함수는 그 간극을 TBS_OFFSET으로 흡수한다.

    한계: prim에 TBS_OFFSET op가 없거나, 애니가 TBS_OFFSET **앞쪽** op만 건드리면 실패할 수 있다.
    Skel/관절만 키가 있고 부모 이동만 MOVE한 경우에는 관절 쪽까지 별도 키 설계가 필요할 수 있다.
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


def split_prim_identifier_tokens(text: str) -> List[str]:
    """
    콤마와 공백(탭·개행 포함)을 동시에 구분자로 쓴다. 연속 구분자는 하나로 본다.
    prim 경로 문자열 안에 공백이 들어가는 경우는 한 토큰으로 유지할 수 없다(콤마만 사용 권장).
    """
    if not text or not str(text).strip():
        return []
    return [p for p in re.split(r"[\s,]+", str(text).strip()) if p]


def resolve_prim_paths_multi(identifier_text: str) -> List[str]:
    """콤마·공백으로 구분된 prim 식별자를 모두 해석해 prim path 목록 반환."""
    out: List[str] = []
    seen = set()
    for key in split_prim_identifier_tokens(identifier_text or ""):
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
    except Exception as e:
        try:
            print(f"[ERROR_HIDE] Failed to set visibility for {path} to {visible}: {e}", flush=True)  # noqa: T201
        except Exception:
            pass
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
        ensure_scale_xform_ops_first(prim)
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
        ensure_scale_xform_ops_first(prim)
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
    시퀀스 실행: 병렬 그룹(run_with_previous) + 앵커(그룹 맨 아래 스텝) 기준으로 다음 그룹 스케줄.
    """

    on_sequence_completed: Optional[Callable[[], None]] = None

    def __post_init__(self) -> None:
        self._running = False
        self._steps: List[Dict[str, Any]] = []
        self._baseline: Dict[str, Tuple[Gf.Vec3f, Gf.Vec3f]] = {}
        self._next_tick_sub = None
        self._pending_delay_sub = None  # 레거시 단일 DELAY용(병렬 시 _delay_subs 사용)
        self._delay_subs: List[Any] = []
        # unhide는 스텝 종료마다 예약될 수 있으므로 큐로 누적 처리한다.
        self._unhide_sub = None
        self._unhide_queue: List[Dict[str, Any]] = []
        self._hidden_refcount: Dict[str, int] = {}
        self._group_timer_sub = None
        self._intra_group_subs: List[Any] = []
        self._group_t0 = 0.0
        self._prev_group_hide_paths: List[str] = []
        self._current_group: Optional[Tuple[int, int]] = None
        self._start_from_current: bool = False
        self._start_from_current_paths: List[str] = []
        # path -> {t, r, mode, m16?} — composed_local 는 m16 로 정밀 복원
        self._start_snapshot: Dict[str, Dict[str, Any]] = {}

    def is_running(self) -> bool:
        return self._running

    def _stop_step_animations(self, step: Dict[str, Any]) -> None:
        if not isinstance(step, dict):
            return
        t = str(step.get("type") or "").upper()
        if t == "MOVE":
            for p in resolve_prim_paths_multi(str(step.get("prim", ""))):
                stop_prim_translate_animation(p)
        elif t == "ROTATE":
            for p in resolve_prim_paths_multi(str(step.get("prim", ""))):
                stop_prim_rotate_animation(p)
        elif t == "USD_TIMELINE":
            usd_animation_control.stop_usd_animation()

    def pause(self) -> None:
        """진행 중인 애니메이션만 멈춘다. (위치/타임라인은 초기화하지 않음)"""
        self._running = False
        if self._next_tick_sub is not None:
            try:
                self._next_tick_sub.unsubscribe()
            except Exception:
                pass
            self._next_tick_sub = None
        if self._unhide_sub is not None:
            try:
                self._unhide_sub.unsubscribe()
            except Exception:
                pass
            self._unhide_sub = None
        self._unhide_queue.clear()
        if self._pending_delay_sub is not None:
            try:
                self._pending_delay_sub.unsubscribe()
            except Exception:
                pass
            self._pending_delay_sub = None
        for ds in list(self._delay_subs):
            try:
                ds.unsubscribe()
            except Exception:
                pass
        self._delay_subs.clear()
        if self._group_timer_sub is not None:
            try:
                self._group_timer_sub.unsubscribe()
            except Exception:
                pass
            self._group_timer_sub = None
        for s in list(self._intra_group_subs):
            try:
                s.unsubscribe()
            except Exception:
                pass
        self._intra_group_subs.clear()
        try:
            stop_world_pivot_rotate_animation()
        except Exception:
            pass
        # 어떤 스텝이 진행 중이었는지(타이머/그룹 경합)와 관계없이,
        # 실제 애니메이션 모듈이 돌고 있으면 무조건 전체 중지해야 "일시정지/정지"가 사용자 기대대로 동작한다.
        try:
            stop_all_translate_animations()
        except Exception:
            pass
        try:
            stop_all_rotate_animations()
        except Exception:
            pass
        try:
            stop_all_curve_animations()
        except Exception:
            pass
        # 진행 중인 코드 애니메이션은 안전하게 정리 (현재 그룹 전체)
        try:
            if self._current_group:
                a, b = self._current_group
                for idx in range(a, b + 1):
                    if 0 <= idx < len(self._steps):
                        self._stop_step_animations(self._steps[idx])
        except Exception:
            pass

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
        if self._unhide_sub is not None:
            try:
                self._unhide_sub.unsubscribe()
            except Exception:
                pass
            self._unhide_sub = None
        self._unhide_queue.clear()

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
        """delay_sec 후 숨김 refcount를 1 감소시키고 0이면 다시 표시. (예약은 누적 처리)"""
        if not paths:
            return
        try:
            delay = float(delay_sec)
        except Exception:
            delay = 0.0
        delay = max(0.0, delay)
        due = time.monotonic() + delay
        self._unhide_queue.append({"due": due, "paths": list(paths)})

        def _process_due():
            now = time.monotonic()
            remaining: List[Dict[str, Any]] = []
            for item in list(self._unhide_queue):
                try:
                    if float(item.get("due", 0.0)) > now:
                        remaining.append(item)
                        continue
                    for p in list(item.get("paths") or []):
                        cnt = self._hidden_refcount.get(p, 0) - 1
                        if cnt <= 0:
                            self._hidden_refcount.pop(p, None)
                            _set_prim_visible(p, True)
                        else:
                            self._hidden_refcount[p] = cnt
                except Exception:
                    # 실패한 항목도 더 이상 재시도하지 않음
                    continue
            self._unhide_queue = remaining
            if not self._unhide_queue and self._unhide_sub is not None:
                try:
                    self._unhide_sub.unsubscribe()
                except Exception:
                    pass
                self._unhide_sub = None

        if self._unhide_sub is None:
            try:
                self._unhide_sub = kit_app.get_app().get_update_event_stream().create_subscription_to_pop(
                    lambda e: _process_due(),
                    name="morph.tbs_control_1.sequence_engine.unhide_queue",
                )
            except Exception:
                # fallback: delay 무시하고 즉시 처리
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
        incoming_steps = list(steps or [])
        first = incoming_steps[0] if incoming_steps else {}
        start_from_current = bool((first or {}).get("_start_from_current", False))
        raw_paths = str((first or {}).get("_start_from_current_paths", "")).strip()
        # "현재 위치부터 시작" 모드에서는 run 시작 전에 baseline 복원을 하면 안 된다.
        # 기존 self.stop()은 baseline을 복원하므로, 이 모드에서는 pause+정리만 수행한다.
        if start_from_current:
            self.pause()
            try:
                self._clear_all_hides()
            except Exception:
                pass
            try:
                usd_animation_control.stop_usd_animation()
                usd_animation_control.reset_timeline_to_zero()
            except Exception:
                pass
        else:
            self.stop()

        self._steps = incoming_steps
        self._start_from_current = start_from_current
        self._start_from_current_paths = split_prim_identifier_tokens(raw_paths)
        # JSON에 저장된 시작 스냅샷(prim_path -> t/r)을 런타임 형식으로 파싱.
        self._start_snapshot = self._parse_start_snapshot((first or {}).get("_start_snapshot", {}))
        self._current_group = None
        self._prev_group_hide_paths = []
        # 실행 버튼을 누르면 타임라인은 항상 0에서 시작
        try:
            usd_animation_control.stop_usd_animation()
            usd_animation_control.reset_timeline_to_zero()
        except Exception:
            pass
        # 타임라인 time=0 적용이 "다음 프레임"에 평가되는 경우가 있어,
        # 프림 baseline 복원/시퀀스 시작을 다음 프레임으로 지연해 덮어쓰기/미복원 문제를 방지한다.
        def _start():
            # 포트 LOT 표시용 prim(port_lot_prim_paths.json)은 시퀀스 스텝에 없어도 움직일 수 있으므로,
            # 애니 시작 시 한 번 authoring 자세로 복원한다(보임/숨김은 건드리지 않음).
            try:
                from .port_lot_visibility import restore_port_lot_prims_to_authoring

                restore_port_lot_prims_to_authoring()
            except Exception:
                pass
            # baseline은 '최초 상태'를 보존해야 하므로 매 실행마다 덮어쓰지 않는다.
            # 다만 스텝 편집으로 새 prim이 등장할 수 있으니, baseline에 없는 prim만 보강 캡처한다.
            self._capture_baseline(force=False)
            if self._start_snapshot:
                # 최우선: 명시 스냅샷이 있으면 먼저 적용해서 시작 기준을 고정한다.
                self._apply_start_snapshot(self._start_snapshot)
            if not self._start_from_current:
                self._restore_baseline()
            elif self._start_from_current_paths:
                # 특정 경로만 현재 위치 유지: 그 외 경로는 baseline 복원.
                keep_current = set(self._start_from_current_paths)
                self._restore_baseline(exclude_paths=keep_current)
            elif self._start_snapshot:
                # 스냅샷이 있는 경우, 스냅샷에 없는 객체만 baseline으로 복원
                keep_current = set(self._start_snapshot.keys())
                self._restore_baseline(exclude_paths=keep_current)
            self._begin_sequence()

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

    def _restore_baseline(self, exclude_paths: Optional[set] = None) -> None:
        """baseline으로 transform을 되돌림. (실행을 항상 초기값부터 재현하기 위함)"""
        stage = _get_stage()
        if not stage:
            return
        # exclude_paths는 "현재 위치부터 시작" 부분 적용을 위한 선택적 복원 예외 목록.
        for path, (t, r) in list(self._baseline.items()):
            if exclude_paths and path in exclude_paths:
                continue
            try:
                prim = stage.GetPrimAtPath(path)
                if prim and prim.IsValid():
                    _set_translate(prim, t)
                    _set_rotate_xyz(prim, r)
            except Exception:
                pass

    def _parse_start_snapshot(self, raw: Any) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        if not isinstance(raw, dict):
            return out
        for path, rec in raw.items():
            if not isinstance(path, str) or not isinstance(rec, dict):
                continue
            t = rec.get("t")
            r = rec.get("r")
            if not (isinstance(t, (list, tuple)) and isinstance(r, (list, tuple)) and len(t) >= 3 and len(r) >= 3):
                continue
            try:
                mode_raw = str(rec.get("mode") or "").strip()
                mode = "composed_local" if mode_raw == "composed_local" else "offset_only"
                entry: Dict[str, Any] = {
                    "t": Gf.Vec3f(float(t[0]), float(t[1]), float(t[2])),
                    "r": Gf.Vec3f(float(r[0]), float(r[1]), float(r[2])),
                    "mode": mode,
                }
                m16 = rec.get("m16")
                if isinstance(m16, (list, tuple)) and len(m16) >= 16:
                    entry["m16"] = [float(m16[i]) for i in range(16)]
                out[path] = entry
            except Exception:
                continue
        return out

    def _apply_start_snapshot(self, snapshot: Dict[str, Dict[str, Any]]) -> None:
        stage = _get_stage()
        if not stage:
            return
        # 캡처(capture_composed_local_start_snapshot_for_paths)와 동일하게 Default 시간으로
        # TBS 역산·합성을 맞춘다. 타임라인 시각과 섞이면 스냅샷 적용이 0으로 떨어질 수 있다.
        tc = Usd.TimeCode.Default()
        for path, rec in snapshot.items():
            try:
                prim = stage.GetPrimAtPath(path)
                if not prim or not prim.IsValid():
                    continue
                t = rec.get("t")
                r = rec.get("r")
                mode = str(rec.get("mode") or "offset_only")
                if not isinstance(t, Gf.Vec3f) or not isinstance(r, Gf.Vec3f):
                    continue
                if mode == "composed_local":
                    M_local = _matrix4d_from_m16_list(rec.get("m16"))
                    if M_local is None:
                        M_local = _local_matrix4d_from_tr(t, r)
                    if _apply_tbs_for_target_local_matrix(prim, M_local, tc):
                        continue
                _set_translate(prim, t)
                _set_rotate_xyz(prim, r)
            except Exception:
                pass

    # ---------------- internal ----------------

    def _complete_sequence(self) -> None:
        if not self._running:
            return
        self._running = False
        self._current_group = None
        if self._group_timer_sub is not None:
            try:
                self._group_timer_sub.unsubscribe()
            except Exception:
                pass
            self._group_timer_sub = None
        try:
            self._clear_all_hides()
        except Exception:
            pass
        cb = self.on_sequence_completed
        if cb:
            try:
                cb()
            except Exception:
                pass

    def _clear_intra_group_timers(self) -> None:
        for s in list(self._intra_group_subs):
            try:
                s.unsubscribe()
            except Exception:
                pass
        self._intra_group_subs.clear()

    def _schedule_intra_group_at(self, target_monotonic: float, fn: Callable[[], None]) -> None:
        """병렬 그룹 내 후속 스텝 시작용(다음 그룹 타이머와 독립)."""
        if target_monotonic <= time.monotonic():
            self._call_next_frame(fn)
            return

        sub_holder: List[Any] = [None]

        def _on_update(e):
            if not self._running:
                return
            if time.monotonic() < target_monotonic:
                return
            sub = sub_holder[0]
            sub_holder[0] = None
            if sub is not None:
                try:
                    sub.unsubscribe()
                except Exception:
                    pass
                try:
                    self._intra_group_subs.remove(sub)
                except Exception:
                    pass
            self._call_next_frame(fn)

        try:
            sub = kit_app.get_app().get_update_event_stream().create_subscription_to_pop(
                _on_update,
                name="morph.tbs_control_1.sequence_engine.intra_group",
            )
            sub_holder[0] = sub
            self._intra_group_subs.append(sub)
        except Exception:
            self._call_next_frame(fn)

    def _schedule_at(self, target_monotonic: float, fn: Callable[[], None]) -> None:
        """monotonic() 기준 시각까지 대기 후 fn (post_update로 한 프레임 지연)."""
        if self._group_timer_sub is not None:
            try:
                self._group_timer_sub.unsubscribe()
            except Exception:
                pass
            self._group_timer_sub = None
        now = time.monotonic()
        if target_monotonic <= now:

            def _immediate(_e=None):
                if self._group_timer_sub is not None:
                    try:
                        self._group_timer_sub.unsubscribe()
                    except Exception:
                        pass
                    self._group_timer_sub = None
                self._call_next_frame(fn)

            try:
                self._group_timer_sub = kit_app.get_app().get_post_update_event_stream().create_subscription_to_pop(
                    _immediate,
                    name="morph.tbs_control_1.sequence_engine.group_timer_immediate",
                )
            except Exception:
                self._call_next_frame(fn)
            return

        def _on_update(e):
            if not self._running:
                return
            if time.monotonic() < target_monotonic:
                return
            if self._group_timer_sub is not None:
                try:
                    self._group_timer_sub.unsubscribe()
                except Exception:
                    pass
                self._group_timer_sub = None
            self._call_next_frame(fn)

        try:
            self._group_timer_sub = kit_app.get_app().get_update_event_stream().create_subscription_to_pop(
                _on_update,
                name="morph.tbs_control_1.sequence_engine.group_timer",
            )
        except Exception:
            self._call_next_frame(fn)

    def _begin_sequence(self) -> None:
        self._running = True
        self._prev_group_hide_paths = []
        self._current_group = None
        try:
            mode = "current" if self._start_from_current else "baseline"
            print(f"[SEQUENCE] start mode={mode} paths={len(self._start_from_current_paths)} snapshot={len(self._start_snapshot)}", flush=True)  # noqa: T201
        except Exception:
            pass
        if not self._steps:
            self._complete_sequence()
            return
        d0 = int(self._steps[0].get("step_delay_ms", 0)) / 1000.0
        # 첫 스텝(step_delay_ms)은 "시퀀스 시작 전 초기 대기"로 해석한다.
        if d0 <= 0:
            self._execute_group_and_schedule_next(0)
        else:
            self._schedule_at(time.monotonic() + d0, lambda: self._execute_group_and_schedule_next(0))

    def _run_from_index(self, idx: int) -> None:
        """이전 그룹의 타이머에서 호출: 다음 그룹 시작(추가 선행 지연 없음)."""
        if not self._running:
            return
        if idx >= len(self._steps):
            self._complete_sequence()
            return
        self._execute_group_and_schedule_next(idx)

    def _execute_group_and_schedule_next(self, a: int) -> None:
        if not self._running:
            return
        b = _group_end_index(self._steps, a)
        self._execute_group(a, b)
        next_idx = b + 1
        if next_idx >= len(self._steps):
            # 마지막 그룹에서 팔로워가 지연 시작이면 _complete_sequence 가 먼저 호출되면
            # _running=False 가 되어 intra 타이머가 막힘 → 완료를 최대 지연만큼 미룸.
            self._defer_complete_sequence_if_needed(a, b)
            return
        anchor_dur = _step_duration_sec(self._steps[b])
        delay_ms_next = int(self._steps[next_idx].get("step_delay_ms", 0))
        delay_sec_next = delay_ms_next / 1000.0
        t0 = self._group_t0
        if b > a:
            # 병렬 그룹 앵커(b)가 리더보다 늦게 시작할 수 있으므로 시작 오프셋을 반영한다.
            anchor_rel = int(self._steps[b].get("step_delay_ms", 0)) / 1000.0
            t_anchor_start = t0 + max(0.0, anchor_rel)
        else:
            t_anchor_start = t0
        anchor_end = t_anchor_start + anchor_dur
        # 음수 delay를 쓰더라도 현재 구현은 그룹 시작(t0)보다 앞당기지 않는다.
        next_start = max(t0, anchor_end + delay_sec_next)

        def _go() -> None:
            self._run_from_index(next_idx)

        self._schedule_at(next_start, _go)

    def _defer_complete_sequence_if_needed(self, a: int, b: int) -> None:
        """마지막 그룹: 팔로워 step_delay_ms 가 있으면 그 시작이 스케줄된 뒤에 시퀀스 종료."""
        if not self._running:
            return
        t0 = self._group_t0
        max_off = 0.0
        for i in range(a + 1, b + 1):
            max_off = max(max_off, max(0.0, int((self._steps[i] or {}).get("step_delay_ms", 0)) / 1000.0))
        if max_off <= 1e-9:
            self._complete_sequence()
            return
        # intra 타이머 → _call_next_frame 체인 여유
        self._schedule_at(t0 + max_off + 0.05, lambda: self._complete_sequence())

    def _execute_group(self, a: int, b: int) -> None:
        """구간 [a..b]: 리더(a) 즉시 시작, run_with_previous 인 스텝은 step_delay_ms 만큼 리더 시작 후 지연 시작. 앵커=b."""
        if not self._running:
            return
        self._clear_intra_group_timers()
        t0 = time.monotonic()
        self._group_t0 = t0
        self._current_group = (a, b)

        noop = lambda: None
        self._start_step(a, on_completed=noop)
        for i in range(a + 1, b + 1):
            off_sec = int((self._steps[i] or {}).get("step_delay_ms", 0)) / 1000.0
            off_sec = max(0.0, off_sec)
            target = t0 + off_sec
            if target <= time.monotonic():
                self._start_step(i, on_completed=noop)
            else:
                self._schedule_intra_group_at(target, lambda idx=i: self._start_step(idx, lambda: None))

    def _start_step(self, idx: int, on_completed: Callable[[], None]) -> None:
        """
        스텝 실행 시작.

        hide는 "스텝 단위"로 적용/복귀한다.
        - 스텝 시작 시 hide_enabled/hide_prims에 해당하는 prim을 숨기고(refcount +1)
        - 스텝 완료 시 해당 prim을 refcount -1 하여 0이면 다시 표시

        병렬(run_with_previous) 스텝이 겹칠 수 있어 refcount 방식은 유지한다.
        """
        step = self._steps[idx] or {}
        hidden_paths = self._apply_hide_for_step(step)
        t = str(step.get("type") or "").upper()
        if t == "MOVE":
            # MOVE 랜덤 범위 샘플링은 "실행 시점"에 1회 고정한다.
            # 이후 duration/dx/dy/dz는 _runtime_* 값을 사용해 로그/스케줄과 일치시킨다.
            step["_runtime_duration"] = _sample_step_value(step, "duration")
            step["_runtime_dx"] = _sample_step_value(step, "dx")
            step["_runtime_dy"] = _sample_step_value(step, "dy")
            step["_runtime_dz"] = _sample_step_value(step, "dz")

        def _done() -> None:
            try:
                # 스텝 종료 시 hide 복귀(스텝 단위 정책)
                if hidden_paths:
                    # 다음 스텝과 경계가 맞물릴 때 깜빡임을 방지하기 위해 기본 지연(0.2s)을 사용한다.
                    self._schedule_unhide(hidden_paths)
            except Exception:
                pass
            try:
                on_completed()
            except Exception:
                pass

        if t == "USD_TIMELINE":
            mode = str(step.get("mode") or "MANUAL").upper()
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
            if bool(step.get("offset_correction_enabled", False)):
                try:
                    # 코드 이동/회전으로 누적된 오프셋을 USD 시작프레임 기준으로 보정.
                    paths_for_offset: List[str] = list(self._baseline.keys())
                    extra = str(step.get("offset_correct_prims", "") or "").strip()
                    if extra:
                        for p in resolve_prim_paths_multi(extra):
                            if p not in paths_for_offset:
                                paths_for_offset.append(p)
                    if paths_for_offset:
                        _apply_world_space_offset_correction(paths_for_offset, start)
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
            sub_ref = [None]

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
                sub = sub_ref[0]
                sub_ref[0] = None
                if sub is not None:
                    try:
                        sub.unsubscribe()
                    except Exception:
                        pass
                    try:
                        self._delay_subs.remove(sub)
                    except Exception:
                        pass
                _done()

            try:
                sub_ref[0] = kit_app.get_app().get_update_event_stream().create_subscription_to_pop(
                    _on_update,
                    name="morph.tbs_control_1.sequence_engine.delay_parallel",
                )
                self._delay_subs.append(sub_ref[0])
            except Exception:
                _done()
            return

        if t == "MOVE":
            prim_id = str(step.get("prim") or "")
            duration = float(step.get("_runtime_duration", step.get("duration", 1.0)))
            dx = float(step.get("_runtime_dx", step.get("dx", 0.0)))
            dy = float(step.get("_runtime_dy", step.get("dy", 0.0)))
            dz = float(step.get("_runtime_dz", step.get("dz", 0.0)))
            stage = _get_stage()
            paths = resolve_prim_paths_multi(prim_id)
            if not paths:
                # prim 경로 해석 실패는 하드 에러로 중단하지 않고 step만 스킵한다.
                _done()
                return
            remaining = {"n": len(paths)}

            def _one_done():
                remaining["n"] -= 1
                if remaining["n"] <= 0:
                    _done()

            for p in paths:
                prim = stage.GetPrimAtPath(p) if stage else None
                world_delta = Gf.Vec3d(dx, dy, dz)
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
            auto_center = bool(step.get("auto_pivot_world_center", False))
            # 편집기에서 UI로 제어하는건 auto_pivot_world_center 뿐이다.
            # 과거 JSON의 user_axis_rotate/world_pivot_rotate 등이 남아있으면
            # "이동하면서 회전"처럼 보일 수 있으므로, 여기서는 auto_center가 아닐 때는 로컬 회전 경로를 우선한다.
            use_world_pivot = False
            pwx = float(step.get("pivot_wx", 0.0))
            pwy = float(step.get("pivot_wy", 0.0))
            pwz = float(step.get("pivot_wz", 0.0))
            paths = resolve_prim_paths_multi(prim_id)
            if not paths:
                _done()
                return
            if abs(rx) < 1e-9 and abs(ry) < 1e-9 and abs(rz) < 1e-9:
                _done()
                return

            stop_world_pivot_rotate_animation()
            for p in paths:
                # 중요: MOVE(translate_animation)와 ROTATE(자동 pivot)은 둘 다 TBS_OFFSET translate를 건드릴 수 있다.
                # 예상 duration 기반 스케줄링으로 두 스텝이 겹치면 "이동하면서 회전"처럼 보이므로,
                # ROTATE 시작 시점에 해당 prim의 이동 애니메이션을 확실히 중지해 충돌을 방지한다.
                try:
                    stop_prim_translate_animation(p)
                except Exception:
                    pass
                stop_prim_rotate_animation(p)

            # 자동 모드(권장): "프림 로컬 중심점"을 월드로 변환한 pivot_world를 고정한 뒤,
            # 월드 피봇 회전(orbit matrix)을 적용하면 해당 점이 월드에서 고정되어 "제자리 회전"처럼 보인다.
            if auto_center and len(paths) == 1:
                try:
                    # 가장 강력한 고정: 월드 중심이 흔들리면 translate를 매 프레임 보정한다.
                    run_prim_rotate_lock_world_center_animation(
                        paths[0],
                        rx,
                        ry,
                        rz,
                        duration,
                        on_completed=lambda: _done(),
                    )
                    return
                except Exception:
                    pass

            # 자동 중심 회전만 월드 피봇 경로를 사용한다.
            if auto_center:
                use_world_pivot = True

            if use_world_pivot:
                tc_now = _get_current_time_code()
                tc_rot = tc_now
                # auto_center=True면 실행 순간의 "월드 BBox 중심"을 pivot으로 고정한다(단일 prim 기준).
                if auto_center and len(paths) == 1:
                    stage = _get_stage()
                    prim = stage.GetPrimAtPath(paths[0]) if stage else None
                    c = _prim_world_bbox_center(prim, tc_now) if prim else None
                    pivot_world = c if c is not None else None
                else:
                    pivot_world = Gf.Vec3d(pwx, pwy, pwz)

                def _world_euler_done():
                    _done()

                run_world_euler_pivot_rotate_animation(
                    paths,
                    pivot_world,
                    rx,
                    ry,
                    rz,
                    duration,
                    tc_rot,
                    on_completed=_world_euler_done,
                )
                return

            remaining = {"n": len(paths)}

            def _one_done():
                remaining["n"] -= 1
                if remaining["n"] <= 0:
                    _done()

            for p in paths:
                run_prim_rotate_animation(
                    p,
                    [{"duration": duration, "delta": (rx, ry, rz)}],
                    loop=False,
                    on_completed=_one_done,
                )
            return

        _done()
