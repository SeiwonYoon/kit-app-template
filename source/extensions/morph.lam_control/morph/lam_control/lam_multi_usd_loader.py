"""L1-b — Multi-USD Loader.

사용자가 LAM 다중 USD 로드 창에서 USD 파일을 추가할 때 호출되는 핵심 모듈.
master stage 의 root layer 에 다음을 author 한다.
  - `/World/<usd_id>` Xform prim
  - `references.AddReference(<source_asset 경로 — REQ-005 P-2 정책>)`
  - REQ-005 customData 메타(`lam:guid`, `lam:instance_id`, `lam:source_asset`)
  - `prim.SetDisplayName(usd_id)` (Stage 패널 가독성)

또한 자산이 가진 stage time(`startTimeCode/endTimeCode`) 와 timeCodesPerSecond 를
인스턴스에 채워서 L5 Evaluator 가 range_mode="full" 일 때 그대로 사용한다.

본 모듈은 omni.timeline 미사용. omni.usd 와 pxr.Usd/UsdGeom 만 사용한다.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

from .lam_instance_registry import AnimationInstanceRegistry, slugify_instance_id
from .lam_master_stage import MasterStage
from .lam_types import AnimationInstance, make_guid


_PRINT_PREFIX = "[LAM/L1b]"

_DEFAULT_TPS = 30.0
# 보정 회전 op 의 suffix — 같은 prim 에 두 번 author 되지 않도록 유일 키.
_UP_AXIS_FIX_OP_SUFFIX = "lamUpAxisFix"


def read_asset_time_range(asset_path: str) -> Tuple[float, float, float]:
    """자산 USD 의 stage start/end timeCode + tps 를 best-effort 로 읽는다.

    실패 시 (0.0, 0.0, 30.0) 폴백.
    """
    if not asset_path or not os.path.isfile(asset_path):
        return (0.0, 0.0, _DEFAULT_TPS)
    try:
        from pxr import Usd  # type: ignore

        layer_stage = Usd.Stage.Open(asset_path)
        if layer_stage is None:
            return (0.0, 0.0, _DEFAULT_TPS)
        try:
            tps = float(layer_stage.GetTimeCodesPerSecond())
        except Exception:
            tps = _DEFAULT_TPS
        try:
            s = float(layer_stage.GetStartTimeCode())
            e = float(layer_stage.GetEndTimeCode())
        except Exception:
            s, e = 0.0, 0.0
        return (s, e, tps if tps > 0 else _DEFAULT_TPS)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} read_asset_time_range failed: {exc}", flush=True)
        return (0.0, 0.0, _DEFAULT_TPS)


def read_asset_up_axis(asset_path: str) -> str:
    """자산 USD 의 upAxis 를 'Y' / 'Z' 둘 중 하나로 반환. 알 수 없으면 'Y' 폴백.

    USD 표준 기본은 'Y' 이지만 Omniverse 자산은 'Z' 가 흔하다.
    """
    if not asset_path or not os.path.isfile(asset_path):
        return "Y"
    try:
        from pxr import Usd, UsdGeom  # type: ignore

        st = Usd.Stage.Open(asset_path)
        if st is None:
            return "Y"
        ax = UsdGeom.GetStageUpAxis(st)
        return "Z" if str(ax).upper() == "Z" else "Y"
    except Exception as exc:
        print(f"{_PRINT_PREFIX} read_asset_up_axis failed: {exc}", flush=True)
        return "Y"


def _stage_up_axis(stage) -> str:
    try:
        from pxr import UsdGeom  # type: ignore

        return "Z" if str(UsdGeom.GetStageUpAxis(stage)).upper() == "Z" else "Y"
    except Exception:
        return "Z"


def _author_up_axis_fix(prim, master_axis: str, asset_axis: str) -> Optional[float]:
    """master 와 asset 의 upAxis 가 다르면 reference prim 에 RotateX 보정을 author.

    반환: 적용한 회전 각(deg). 보정이 필요 없으면 None.
    """
    if master_axis == asset_axis:
        return None
    # master Z-up + asset Y-up:  asset 의 +Y → master 의 +Z 로. RotateX(+90).
    # master Y-up + asset Z-up:  asset 의 +Z → master 의 +Y 로. RotateX(-90).
    if master_axis == "Z" and asset_axis == "Y":
        deg = 90.0
    elif master_axis == "Y" and asset_axis == "Z":
        deg = -90.0
    else:
        return None
    try:
        from pxr import UsdGeom  # type: ignore

        xf = UsdGeom.Xform(prim)
        # 이미 같은 suffix 의 RotateX 가 있으면 set 만, 없으면 add.
        existing = None
        for op in xf.GetOrderedXformOps():
            try:
                if op.GetOpName().endswith(":" + _UP_AXIS_FIX_OP_SUFFIX):
                    existing = op
                    break
            except Exception:
                continue
        if existing is None:
            existing = xf.AddRotateXOp(opSuffix=_UP_AXIS_FIX_OP_SUFFIX)
        existing.Set(deg)
        return deg
    except Exception as exc:
        print(f"{_PRINT_PREFIX} up axis fix author failed: {exc}", flush=True)
        return None


class MultiUsdLoader:
    """master stage 안에 USD reference 를 추가/제거."""

    def __init__(self, master: MasterStage, registry: AnimationInstanceRegistry) -> None:
        self._master = master
        self._registry = registry

    def add_usd(self, *, source_asset: str, requested_id: str = "") -> Optional[AnimationInstance]:
        """master stage 에 USD 1개를 reference 로 추가하고 인스턴스를 등록.

        반환: 등록된 `AnimationInstance` (실패 시 None).
        """
        if not source_asset:
            return None

        # 1) 표시 이름(usd_id) 결정
        base_id = (requested_id or "").strip()
        if not base_id:
            base_id = os.path.splitext(os.path.basename(source_asset))[0]
        base_id = slugify_instance_id(base_id)
        final_id = self._registry.reserve_instance_id(base_id)
        prim_path = f"/World/{final_id}"

        # 2) 자산 시간 범위 사전 조회
        s, e, tps = read_asset_time_range(source_asset)

        # 3) master stage 컨텍스트 보장 + root layer 를 edit target 으로
        if not self._master.ensure_context():
            print(f"{_PRINT_PREFIX} add_usd: master context unavailable, registering only", flush=True)
            return self._register_only(
                prim_path=prim_path,
                instance_id=final_id,
                source_asset=source_asset,
                s=s, e=e, tps=tps,
            )
        self._master.set_root_layer_edit_target()

        # 4) prim author
        try:
            from pxr import Sdf, Usd, UsdGeom  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} pxr not available: {exc}", flush=True)
            return self._register_only(
                prim_path=prim_path,
                instance_id=final_id,
                source_asset=source_asset,
                s=s, e=e, tps=tps,
            )

        stage = self._master.get_stage()
        if stage is None:
            return self._register_only(
                prim_path=prim_path,
                instance_id=final_id,
                source_asset=source_asset,
                s=s, e=e, tps=tps,
            )

        try:
            # /World 가 없다면 한 번 만든다
            if not stage.GetPrimAtPath("/World"):
                UsdGeom.Xform.Define(stage, "/World")
            xform = UsdGeom.Xform.Define(stage, prim_path)
            prim = xform.GetPrim()

            # 자산 reference attach (REQ-005 P-2 — 가능하면 master 기준 상대 경로)
            ref_path = self._master.make_relative_to_master(source_asset)
            prim.GetReferences().AddReference(ref_path)

            # REQ-010 — upAxis 자동 보정. master 와 asset 의 stage upAxis 가 다르면
            # reference prim 에 RotateX(±90) 을 author 해 자산이 눕혀 보이는 문제를 피한다.
            asset_axis = read_asset_up_axis(source_asset)
            master_axis = _stage_up_axis(stage)
            applied_deg = _author_up_axis_fix(prim, master_axis=master_axis, asset_axis=asset_axis)

            # REQ-005 customData 메타 author (instance_id 는 등록 후 확정값을 다시 박아 줌)
            guid = make_guid()
            prim.SetCustomDataByKey("lam:instance", True)
            prim.SetCustomDataByKey("lam:guid", guid)
            prim.SetCustomDataByKey("lam:instance_id", final_id)
            prim.SetCustomDataByKey("lam:source_asset", ref_path)
            prim.SetCustomDataByKey("lam:asset_up_axis", asset_axis)
            prim.SetCustomDataByKey("lam:master_up_axis", master_axis)
            try:
                prim.SetDisplayName(final_id)
            except Exception:
                pass

            inst = self._registry.register(
                prim_path=prim_path,
                instance_id=final_id,
                source_asset=ref_path,
                guid=guid,
                discovered_by="user_register",
                asset_start_time=s,
                asset_end_time=e,
                asset_tps=tps,
            )
            up_msg = ""
            if applied_deg is not None:
                up_msg = f" upAxis_fix={asset_axis}->{master_axis} RotateX({applied_deg:+.0f})"
            elif asset_axis != master_axis:
                up_msg = f" upAxis_mismatch={asset_axis}vs{master_axis} (보정 실패)"
            print(
                f"{_PRINT_PREFIX} add_usd OK prim={prim_path} src={ref_path} time=[{s},{e}]@{tps}fps{up_msg}",
                flush=True,
            )
            return inst
        except Exception as exc:
            print(f"{_PRINT_PREFIX} add_usd author failed: {exc}", flush=True)
            return self._register_only(
                prim_path=prim_path,
                instance_id=final_id,
                source_asset=source_asset,
                s=s, e=e, tps=tps,
            )

    def remove_usd(self, prim_path: str) -> bool:
        """master stage 에서 prim 을 제거하고 Registry 에서도 unregister."""
        ok = True
        try:
            stage = self._master.get_stage()
            if stage is not None and stage.GetPrimAtPath(prim_path):
                self._master.set_root_layer_edit_target()
                stage.RemovePrim(prim_path)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} remove_usd stage error: {exc}", flush=True)
            ok = False
        # 핫픽스 7 — 인스턴스 전용 sublayer 도 같이 청소(없으면 no-op).
        try:
            self._master.remove_inst_sublayer(prim_path)
        except Exception:
            pass
        self._registry.unregister(prim_path)
        return ok

    # ----------------------------------------------------------------- private

    def _register_only(
        self,
        *,
        prim_path: str,
        instance_id: str,
        source_asset: str,
        s: float,
        e: float,
        tps: float,
    ) -> AnimationInstance:
        """USD author 가 안 되는 환경(unit test/CI 등) 폴백."""
        return self._registry.register(
            prim_path=prim_path,
            instance_id=instance_id,
            source_asset=source_asset,
            asset_start_time=s,
            asset_end_time=e,
            asset_tps=tps,
        )


__all__ = ["MultiUsdLoader"]
