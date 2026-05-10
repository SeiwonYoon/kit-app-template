"""LAM 측 offset correction — TBS `sequence_engine._apply_world_space_offset_correction` 와
동일 의미·동일 수식을 LAM 모듈로 별도 구현(REQ-002 0줄 변경 원칙).

【 언제 】 USD_TIMELINE step 이 시작되기 직전.

【 왜 】 MOVE/ROTATE 가 누적시킨 `TBS_OFFSET` 가 있는데 USD timeline 의 start_frame
키가 "녹화 당시 월드 기준" 이면 재생 시작 순간 prim 이 자산 원본 자세로 점프해 보임.
그 간극을 `TBS_OFFSET` 두 op 에 다시 박아 흡수해 화면 점프를 막는다.

【 어떻게 】
1. M_world_now = 현재(=오프셋 포함) 월드 행렬
2. TBS_OFFSET 의 translate/rotate op 를 임시로 (0,0,0) 으로 set
3. M_base_start = start_time 에서의 월드 행렬 (= USD timeline 만 평가한 자세)
4. O = Rest * M_base_inv * M_world_now * Rest_inv  →  TBS_OFFSET 두 op 에 박음

【 LAM 에서의 차이 】
- LAM 은 omni.timeline 을 쓰지 않으므로 start_time 은 인스턴스의 `asset_tps` 로 환산한
  Usd.TimeCode 로 직접 만든다.
- evaluator(`lam_attribute_reauthor`) 가 자산 USD 의 timeSamples attr 을 root layer
  default 로 박는 모델이라, TBS_OFFSET 두 op 와는 별 op 이므로 보정이 살아남는다.
- 단 자산 자체가 root prim 의 transform op 에 timeSamples 를 가지면 그 op 가 매 프레임
  덮어쓰여 보정 효과가 가려질 수 있음(TBS 의 한계와 동일).
"""

from __future__ import annotations

import math
from typing import Any, List, Optional

# IMPORTANT — Kit / pxr 모듈은 반드시 모듈 최상단에서 import 한다(deadlock 방지, lam_translate_animation 주석 참고).
import omni.usd as ou  # type: ignore  # noqa: E402
from pxr import Gf, Usd, UsdGeom  # type: ignore  # noqa: E402


_PRINT_PREFIX = "[LAM/OFFCORR]"
_OFFSET_SUFFIX = "TBS_OFFSET"


def _stage():
    try:
        import omni.usd as ou  # type: ignore

        ctx = ou.get_context()
        return ctx.get_stage() if ctx else None
    except Exception:
        return None


def _get_or_create_offset_translate_op(prim):
    try:
        from pxr import UsdGeom  # type: ignore

        x = UsdGeom.Xformable(prim)
        if not x:
            return None
        for op in x.GetOrderedXformOps():
            try:
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate and _OFFSET_SUFFIX in op.GetName():
                    return op
            except Exception:
                continue
        return x.AddTranslateOp(opSuffix=_OFFSET_SUFFIX)
    except Exception:
        return None


def _get_or_create_offset_rotate_op(prim):
    try:
        from pxr import UsdGeom  # type: ignore

        x = UsdGeom.Xformable(prim)
        if not x:
            return None
        for op in x.GetOrderedXformOps():
            try:
                if op.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ and _OFFSET_SUFFIX in op.GetName():
                    return op
            except Exception:
                continue
        return x.AddRotateXYZOp(opSuffix=_OFFSET_SUFFIX)
    except Exception:
        return None


def _op_value(op):
    try:
        v = op.Get()
        return v
    except Exception:
        return None


def _rotation_matrix_to_euler_xyz_degrees(r3) -> tuple:
    """Gf.Matrix3d → Euler(XYZ degrees). UsdGeom RotateXYZ 와 동일 순서."""
    try:
        from pxr import Gf  # type: ignore

        rot = Gf.Rotation()
        rot.SetMatrix(r3)
        v = rot.Decompose(Gf.Vec3d(1, 0, 0), Gf.Vec3d(0, 1, 0), Gf.Vec3d(0, 0, 1))
        return float(v[0]), float(v[1]), float(v[2])
    except Exception:
        return 0.0, 0.0, 0.0


def _compute_rest_matrix_at_time(prim, time_code):
    """parent local-to-world(time_code) 를 반환 → 부모 좌표계."""
    try:
        from pxr import Gf, UsdGeom  # type: ignore

        parent = prim.GetParent()
        if not parent or not parent.IsValid() or str(parent.GetPath()) in ("", "/"):
            return Gf.Matrix4d(1.0)
        cache = UsdGeom.XformCache(time_code)
        return Gf.Matrix4d(cache.GetLocalToWorldTransform(parent))
    except Exception:
        from pxr import Gf  # type: ignore

        return Gf.Matrix4d(1.0)


def apply_world_space_offset_correction(
    prim_paths: List[str],
    start_seconds: float,
    *,
    asset_tps: float = 24.0,
) -> None:
    """LAM 버전 offset correction — TBS 와 의미 동일.

    Args:
        prim_paths: 보정 대상 prim 경로 목록.
        start_seconds: USD_TIMELINE 의 시작 위치(초). 인스턴스의 asset_tps 로 환산해 사용.
        asset_tps: TimeCode 환산용. 인스턴스에 따라 다르므로 caller 가 전달.
    """
    stage = _stage()
    if not stage or not prim_paths:
        return
    try:
        from pxr import Gf, Usd, UsdGeom  # type: ignore

        time_start = Usd.TimeCode(float(start_seconds) * float(max(0.001, asset_tps)))
        for path in prim_paths:
            try:
                prim = stage.GetPrimAtPath(path)
                if not prim or not prim.IsValid():
                    continue
                xform = UsdGeom.Xformable(prim)
                if not xform:
                    continue

                cache_now = UsdGeom.XformCache(Usd.TimeCode.Default())
                M_world_now = cache_now.GetLocalToWorldTransform(prim)
                if M_world_now is None:
                    continue
                M_world_now = Gf.Matrix4d(M_world_now)

                t_op = _get_or_create_offset_translate_op(prim)
                r_op = _get_or_create_offset_rotate_op(prim)
                if not t_op or not r_op:
                    continue

                saved_t = _op_value(t_op)
                saved_r = _op_value(r_op)
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
                        try:
                            t_op.Set(
                                Gf.Vec3d(float(saved_t[0]), float(saved_t[1]), float(saved_t[2]))
                            )
                        except Exception:
                            pass
                    if saved_r is not None:
                        try:
                            r_op.Set(
                                Gf.Vec3f(float(saved_r[0]), float(saved_r[1]), float(saved_r[2]))
                            )
                        except Exception:
                            pass

                Rest = _compute_rest_matrix_at_time(prim, time_start)
                Rest_inv = Rest.GetInverse()
                if Rest_inv is None:
                    continue
                M_base_inv = M_base_start.GetInverse()
                if M_base_inv is None:
                    continue
                O = Rest * M_base_inv * M_world_now * Rest_inv

                trans = O.ExtractTranslation()
                rot3 = O.ExtractRotationMatrix()
                rx, ry, rz = _rotation_matrix_to_euler_xyz_degrees(rot3)
                t_op.Set(Gf.Vec3d(float(trans[0]), float(trans[1]), float(trans[2])))
                r_op.Set(Gf.Vec3f(float(rx), float(ry), float(rz)))
            except Exception as exc:
                print(f"{_PRINT_PREFIX} skip path={path}: {exc}", flush=True)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} apply failed: {exc}", flush=True)


__all__ = ["apply_world_space_offset_correction"]
