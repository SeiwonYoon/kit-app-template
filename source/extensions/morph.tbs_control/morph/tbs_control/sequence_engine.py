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
- ROTATE: 코드 기반 회전 (rotate_animation)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import omni.kit.app as kit_app
import omni.usd as ou
from pxr import Usd, UsdGeom, Gf

from .prim_info import safe_str
from .translate_animation import run_prim_translate_animation, stop_prim_translate_animation
from .rotate_animation import run_prim_rotate_animation, stop_prim_rotate_animation
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


def _compute_rest_matrix_at_time(prim: Usd.Prim, time_code: Usd.TimeCode) -> Gf.Matrix4d:
    """TBS_OFFSET op 이후의 op들만 곱한 로컬 행렬 (start_frame 시점)."""
    rest = Gf.Matrix4d(1.0)
    try:
        x = UsdGeom.Xformable(prim)
        ops = list(x.GetOrderedXformOps()) if x else []
        last_tbs_idx = -1
        for i, op in enumerate(ops):
            try:
                if _OFFSET_SUFFIX in op.GetName():
                    last_tbs_idx = i
            except Exception:
                pass
        if last_tbs_idx < 0:
            return rest
        for op in ops[last_tbs_idx + 1 :]:
            try:
                t = op.GetOpType()
                val = _op_value_at_time(op, time_code)
                if t == UsdGeom.XformOp.TypeTranslate:
                    rest = _matrix_from_translate(val) * rest
                elif t == UsdGeom.XformOp.TypeRotateXYZ:
                    rest = _matrix_from_rotate_xyz(val) * rest
                elif t == UsdGeom.XformOp.TypeScale:
                    rest = _matrix_from_scale(val) * rest
            except Exception:
                pass
    except Exception:
        pass
    return rest


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
        # 진행 중인 코드 애니메이션은 안전하게 정리
        try:
            step = self._steps[self._index] if 0 <= self._index < len(self._steps) else None
        except Exception:
            step = None
        if isinstance(step, dict):
            t = (step.get("type") or "").upper()
            if t == "MOVE":
                for p in resolve_prim_paths(str(step.get("prim", ""))):
                    stop_prim_translate_animation(p)
            elif t == "ROTATE":
                for p in resolve_prim_paths(str(step.get("prim", ""))):
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
                name="morph.tbs_control.sequence_engine.next_frame",
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
        prim_ids: List[str] = []
        for step in self._steps:
            t = str(step.get("type") or "").upper()
            if t in ("MOVE", "ROTATE"):
                prim_ids.append(str(step.get("prim") or ""))
        for prim_id in prim_ids:
            for path in resolve_prim_paths(prim_id):
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

        def _done():
            if not self._running:
                return
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

        if t == "MOVE":
            prim_id = str(step.get("prim") or "")
            duration = float(step.get("duration", 1.0))
            dx = float(step.get("dx", 0.0))
            dy = float(step.get("dy", 0.0))
            dz = float(step.get("dz", 0.0))
            paths = resolve_prim_paths(prim_id)
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
                stop_prim_translate_animation(p)
                run_prim_translate_animation(
                    p,
                    [{"duration": duration, "delta": (dx, dy, dz)}],
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
            paths = resolve_prim_paths(prim_id)
            if not paths:
                _done()
                return
            remaining = {"n": len(paths)}

            def _one_done():
                remaining["n"] -= 1
                if remaining["n"] <= 0:
                    _done()

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
