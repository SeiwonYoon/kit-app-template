# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
xform_utils.py — USD xformOp 순서 정리 및 경고 필터

【역할】
- ensure_scale_xform_ops_first: Scale을 스택 앞으로 — 바인딩 경고 방지.
- install_xform_op_order_warning_filter: 로그 노이즈 억제 (Kit 버전별 로거 경로).

【수정 가이드】
- 애니메이션 모듈에서 prim 수정 전에 ensure_scale_xform_ops_first 호출 유지 권장
- 경고가 다른 모듈명으로 바뀌면: install_xform_op_order_warning_filter 내부 경로 목록

translate·rotate 가 scale 보다 앞에 있으면 Omni 바인딩에서 xformOpOrder 경고가 난다.

【유지보수 시나리오】
1) 특정 에셋에서 xform 경고가 계속 발생
   - ensure_scale_xform_ops_first의 scale 삽입/재정렬 로직 점검
   - 단일 transform matrix op 예외 처리 유지 확인
2) 로그 필터가 동작하지 않는 Kit 버전
   - install_xform_op_order_warning_filter의 logger 경로/patch 대상 확장
3) 성능 이슈
   - 매 프레임 호출 금지, 애니메이션 시작 시점 호출만 유지
"""

from __future__ import annotations

import logging

from pxr import Gf, UsdGeom, Usd

_LOGGING_FILTER_INSTALLED = False
_CARB_THRESHOLD_INSTALLED = False
_CARB_LOG_WARN_PATCHED = False


def _xform_op_order_signature(ops) -> tuple:
    """바인딩이 매번 다른 래퍼 객체를 줄 수 있어 op 정체성은 이름 튜플로 비교한다."""
    names = []
    for o in ops:
        try:
            names.append(o.GetName())
        except Exception:
            names.append(None)
    return tuple(names)


def ensure_scale_xform_ops_first(prim: Usd.Prim) -> None:
    """
    모든 Scale xform op 을 비-scale op 보다 앞에 오도록 순서만 조정.

    - 로컬 스택에 **Scale op 가 없으면** (1,1,1) 을 하나 추가한다.
      (T/R 만 있는 에셋에서 Omni 가 scale 없이 T 가 scale 보다 앞에 있다고 경고하는 경우 완화)
    - 단일 `xformOp:transform`(4x4 matrix) 만 있는 prim 은 건드리지 않는다.
    """
    try:
        if not prim or not prim.IsValid():
            return
        x = UsdGeom.Xformable(prim)
        if not x:
            return
        ops = list(x.GetOrderedXformOps()) if x else []
        if not ops:
            return
        if len(ops) == 1:
            try:
                if ops[0].GetOpType() == UsdGeom.XformOp.TypeTransform:
                    return
            except Exception:
                pass

        scales = [o for o in ops if o.GetOpType() == UsdGeom.XformOp.TypeScale]
        others = [o for o in ops if o.GetOpType() != UsdGeom.XformOp.TypeScale]

        if not scales:
            try:
                s_op = x.AddScaleOp()
                s_op.Set(Gf.Vec3f(1.0, 1.0, 1.0))
            except Exception:
                return
            ops = list(x.GetOrderedXformOps()) if x else []
            scales = [o for o in ops if o.GetOpType() == UsdGeom.XformOp.TypeScale]
            others = [o for o in ops if o.GetOpType() != UsdGeom.XformOp.TypeScale]

        if not scales:
            return
        new_order = scales + others
        if len(new_order) != len(ops):
            return
        if _xform_op_order_signature(new_order) == _xform_op_order_signature(ops):
            return
        x.SetXformOpOrder(new_order)
    except Exception:
        pass


class _SuppressIncompatibleXformOpOrder(logging.Filter):
    """동일 문구의 반복 경고만 숨긴다."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "Incompatible xformOpOrder" in msg and "applied before scale" in msg:
            return False
        return True


def _patch_carb_log_warn() -> None:
    """carb.log_warn 이 문자열을 넘기는 Kit 빌드에서 콘솔 스팸을 줄인다."""
    global _CARB_LOG_WARN_PATCHED
    if _CARB_LOG_WARN_PATCHED:
        return
    try:
        import carb

        if getattr(carb, "_morph_tbs_xform_warn_patched", False):
            _CARB_LOG_WARN_PATCHED = True
            return

        def _wrap(orig):
            """carb.log_warn 원본을 감싸 Incompatible xformOpOrder 메시지는 무시."""

            def _inner(msg, *args, **kwargs):
                try:
                    if "Incompatible xformOpOrder" in str(msg):
                        return
                except Exception:
                    pass
                return orig(msg, *args, **kwargs)

            return _inner

        if hasattr(carb, "log_warn"):
            carb.log_warn = _wrap(carb.log_warn)
        if hasattr(carb, "log_warning"):
            carb.log_warning = _wrap(carb.log_warning)
        carb._morph_tbs_xform_warn_patched = True
        _CARB_LOG_WARN_PATCHED = True
    except Exception:
        pass


def install_xform_op_order_warning_filter() -> None:
    """
    확장 on_startup에서 한 번 호출: xformOpOrder/scale 관련 logging.Filter 추가,
    carb.log_warn 래핑, 일부 omni.usd 로거 임계치 상향으로 스팸 완화.
    """
    global _LOGGING_FILTER_INSTALLED, _CARB_THRESHOLD_INSTALLED
    _patch_carb_log_warn()

    if not _LOGGING_FILTER_INSTALLED:
        try:
            f = _SuppressIncompatibleXformOpOrder()
            for name in (
                "",
                "omni.usd._impl.utils",
                "omni.usd",
            ):
                logging.getLogger(name).addFilter(f)
            _LOGGING_FILTER_INSTALLED = True
        except Exception:
            pass

    if not _CARB_THRESHOLD_INSTALLED:
        try:
            import carb.logging

            iface = carb.logging.get_logging()
            beh = carb.logging.LogSettingBehavior.OVERRIDE
            try:
                lvl = int(carb.logging.LogLevel.ERROR)
            except Exception:
                lvl = 3
            for src in (
                "omni.usd._impl.utils",
                "omni.usd",
                "usd",
            ):
                try:
                    iface.set_level_threshold_for_source(src, beh, lvl)
                except Exception:
                    pass
            _CARB_THRESHOLD_INSTALLED = True
        except Exception:
            pass
