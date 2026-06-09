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

from .tbs_asset_diagnostics import kind_to_user_label, scan_asset_kind
from .tbs_instance_registry import AnimationInstanceRegistry, slugify_instance_id
from .tbs_master_stage import MasterStage
from .tbs_types import (
    ASSET_KIND_UNKNOWN,
    AnimationInstance,
    AssetDiag,
    make_guid,
)


_PRINT_PREFIX = "[TBS/L1b]"

_DEFAULT_TPS = 30.0
# 보정 회전 op 의 suffix — 같은 prim 에 두 번 author 되지 않도록 유일 키.
_UP_AXIS_FIX_OP_SUFFIX = "lamUpAxisFix"


def read_asset_time_range(asset_path: str) -> Tuple[float, float, float]:
    """자산 USD 의 stage start/end timeCode 를 best-effort 로 읽는다.

    LAM 의 FPS 30 고정 정책(TBS_FIXED_FPS) 에 따라 반환 tps 는 **항상 30**.
    자산 헤더의 `GetTimeCodesPerSecond()` 는 진단 로그로만 출력되며, 의사결정에는
    쓰이지 않는다.

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
            raw_tps = float(layer_stage.GetTimeCodesPerSecond())
        except Exception:
            raw_tps = _DEFAULT_TPS
        try:
            s = float(layer_stage.GetStartTimeCode())
            e = float(layer_stage.GetEndTimeCode())
        except Exception:
            s, e = 0.0, 0.0
        if abs(raw_tps - _DEFAULT_TPS) > 1e-6:
            print(
                f"{_PRINT_PREFIX} asset header tps={raw_tps} ignored — using fixed "
                f"{_DEFAULT_TPS} (TBS_FIXED_FPS) for asset={asset_path}",
                flush=True,
            )
        return (s, e, _DEFAULT_TPS)
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

        # 2.1) 자산 종류 자동 분류 (W1 — 2026-05-11) — `[Bake]` 의 조건부 분기와
        #      `TIMESAMPLES_REPLAY` step 의 동작 결정에 사용. 실패해도 add_usd 자체는 계속.
        try:
            asset_kind, asset_diag = scan_asset_kind(source_asset)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} asset scan failed: {exc} (계속 진행)", flush=True)
            asset_kind = ASSET_KIND_UNKNOWN
            asset_diag = AssetDiag()

        # 3) master stage 컨텍스트 보장 + root layer 를 edit target 으로
        if not self._master.ensure_context():
            print(f"{_PRINT_PREFIX} add_usd: master context unavailable, registering only", flush=True)
            return self._register_only(
                prim_path=prim_path,
                instance_id=final_id,
                source_asset=source_asset,
                s=s, e=e, tps=tps,
                asset_kind=asset_kind,
                asset_diag=asset_diag,
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
                asset_kind=asset_kind,
                asset_diag=asset_diag,
            )

        stage = self._master.get_stage()
        if stage is None:
            return self._register_only(
                prim_path=prim_path,
                instance_id=final_id,
                source_asset=source_asset,
                s=s, e=e, tps=tps,
                asset_kind=asset_kind,
                asset_diag=asset_diag,
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

            # Q2 — 2026-05-12: asset_kind/asset_diag 는 register() 안에서 _notify 전에
            # 박혀야 한다. 그렇지 않으면 registry listener (lam_window._refresh_instances)
            # 가 인스턴스 생성 즉시 동기 호출되어 UI 가 UNKNOWN 으로 먼저 렌더되는 회귀가
            # 발생한다. 별도로 박지 말고 반드시 인수로 전달.
            inst = self._registry.register(
                prim_path=prim_path,
                instance_id=final_id,
                source_asset=ref_path,
                guid=guid,
                discovered_by="user_register",
                asset_start_time=s,
                asset_end_time=e,
                asset_tps=tps,
                asset_kind=asset_kind,
                asset_diag=asset_diag,
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
            # 자산 종류 진단 라인 — UI / 사용자에게 명시. add_usd 직후 1 회만 출력.
            print(
                f"{_PRINT_PREFIX} asset kind={kind_to_user_label(asset_kind)} | "
                f"{asset_diag.to_log_line()}",
                flush=True,
            )

            # 2026-05-13 — 실무 FBX→USD 자산에서 "1000 프레임이 800 부근에서 평가" 회귀
            # 분석 결과 — master tcps 30 / omni.timeline framerate 24 의 불일치 +
            # master 의 startTimeCode/endTimeCode 가 자산보다 좁아서 timeline slider 가
            # 자산 전 구간을 커버하지 못하는 두 가지 문제를 add_usd 직후 일괄 보정.
            #   (1) master startTime/endTime 자동 확장 — 기존 값보다 자산 측이 크면 늘림.
            #   (2) omni.timeline start/end 도 master tcps 기준 seconds 로 동기화.
            #   (3) `force_fixed_fps_30` 재호출 — Kit timeline framerate 가 다른 값으로
            #       잡혀 있던 경우(setter API 빌드 차이 / carb.settings 미적용 등) 다시
            #       30 으로 환원하고 read-back 진단을 한 줄 출력.
            #   (4) source 자산 자체의 raw tcps 와 sample 통계를 진단 — `1000 → 800`
            #       회귀 발생 시 master / source / timeline 셋의 정합성을 즉시 비교 가능.
            try:
                self._sync_stage_and_timeline_with_source(
                    stage,
                    source_asset=source_asset,
                    src_start_tc=float(s),
                    src_end_tc=float(e),
                )
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} add_usd time-sync failed: {exc} (계속 진행)",
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
                asset_kind=asset_kind,
                asset_diag=asset_diag,
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

    def clear_instance_contents(self, prim_path: str) -> dict:
        """인스턴스 prim 자체는 유지하고 **하위 내용물만 비운다**.

        Kit Stage panel 에서 ``/World/<inst>/<자식>`` 을 Delete 키로 지우려고 하면
        "cannot delete ancestral prim" 오류가 난다 — 자식 prim 들이 자산 USD 의
        reference 로 들어온 것이라 root layer 에서 직접 제거할 수 없기 때문이다.

        본 메서드는 그 우회 경로다:
            1) ``inst_sublayer`` (session 산하) 가 있으면 폐기 — TIMESAMPLES_REPLAY 단계
               에서 박혀 있던 baked timeSamples / OmniGraph deactivate 표식 등이
               전부 비워진다.
            2) **모든 layer** (root / session / 그 산하 sublayer 모두) 에서 본
               prim_path 에 대한 ``referenceList`` / ``payloadList`` /
               ``variantSelections`` / ``inheritPathList`` / ``specializesList`` 의
               ListOp 편집을 ``ClearEdits()`` 로 비운다.
            3) root layer 의 ``PrimSpec.nameChildren`` 도 모두 제거 (root 에 spec 이
               있는 자식만 — reference 로 들어온 composition-only 자식은 (2) 의
               references clear 로 자연 소멸).
            4) 인스턴스 자체는 ``stage.GetPrimAtPath(prim_path)`` 에 ``Xform`` 으로
               유지되도록 root layer 에 ``typeName='Xform'`` spec 을 보장.
            5) Registry 의 ``inst.baked=False`` / ``inst.source_asset=''`` 로 표시.
               ``inst.asset_kind`` 는 ``UNKNOWN`` 으로 리셋해 다음 [Extract] / [Bake]
               분기가 새로 결정되게 한다.

        evaluator 측 ``forget_instance`` 호출은 본 메서드 사용자가 책임진다 (UI 핸들러에
        서 수행). evaluator 가 다음 update tick 에 offscreen stage 를 재구성할 수 있게
        하기 위해서다.

        Args:
            prim_path: 인스턴스 prim path (예: ``/World/aaa``).

        Returns:
            진단 dict — 사용자가 로그에 출력. 키:
              ``ok`` (bool), ``cleared_refs`` (int), ``cleared_payloads`` (int),
              ``cleared_other`` (int), ``removed_children_spec`` (int),
              ``removed_inst_sublayer`` (bool), ``error`` (str).
        """
        diag = {
            "ok": False,
            "cleared_refs": 0,
            "cleared_payloads": 0,
            "cleared_other": 0,
            "removed_children_spec": 0,
            "removed_inst_sublayer": False,
            "error": "",
        }

        stage = self._master.get_stage()
        if stage is None:
            diag["error"] = "master stage is None"
            return diag

        try:
            from pxr import Sdf, UsdGeom  # type: ignore
        except Exception as exc:
            diag["error"] = f"pxr import failed: {exc}"
            return diag

        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            diag["error"] = f"prim not found: {prim_path}"
            return diag

        # (1) inst sublayer 폐기 — bake / extract 잔재까지 한 번에 청소.
        try:
            if self._master.remove_inst_sublayer(prim_path):
                diag["removed_inst_sublayer"] = True
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} clear_instance_contents: remove_inst_sublayer 실패 "
                f"prim={prim_path}: {exc} (계속 진행)",
                flush=True,
            )

        # (2) 모든 layer 의 PrimSpec 에서 composition arc / nameChildren 비우기.
        def _collect_all_layers(stage_) -> list:
            """root / session + 두 layer 의 subLayerPaths 를 재귀적으로 모두 수집."""
            seen_ids: set = set()
            out: list = []

            def _walk(layer):
                if layer is None:
                    return
                try:
                    lid = layer.identifier
                except Exception:
                    lid = id(layer)
                if lid in seen_ids:
                    return
                seen_ids.add(lid)
                out.append(layer)
                try:
                    sub_paths = list(layer.subLayerPaths)
                except Exception:
                    sub_paths = []
                for sp in sub_paths:
                    try:
                        sub = Sdf.Layer.Find(sp)
                        if sub is None:
                            sub = Sdf.Layer.FindOrOpen(sp)
                    except Exception:
                        sub = None
                    if sub is not None:
                        _walk(sub)

            try:
                _walk(stage_.GetRootLayer())
            except Exception:
                pass
            try:
                _walk(stage_.GetSessionLayer())
            except Exception:
                pass
            return out

        layers = _collect_all_layers(stage)

        target_sdf_path = Sdf.Path(prim_path)
        for layer in layers:
            try:
                spec = layer.GetPrimAtPath(target_sdf_path)
            except Exception:
                spec = None
            if spec is None:
                continue

            # composition arcs ListOp 비우기
            for arc_attr, key in (
                ("referenceList", "cleared_refs"),
                ("payloadList", "cleared_payloads"),
                ("inheritPathList", "cleared_other"),
                ("specializesList", "cleared_other"),
            ):
                try:
                    arc = getattr(spec, arc_attr, None)
                    if arc is not None:
                        arc.ClearEdits()
                        diag[key] = int(diag[key]) + 1
                except Exception as exc:
                    print(
                        f"{_PRINT_PREFIX} clear_instance_contents: {arc_attr} ClearEdits "
                        f"실패 layer={layer.identifier} prim={prim_path}: {exc}",
                        flush=True,
                    )

            # variantSelections 비우기 (있다면).
            try:
                vs = spec.variantSelections
                if vs:
                    for k in list(vs.keys()):
                        try:
                            del vs[k]
                            diag["cleared_other"] = int(diag["cleared_other"]) + 1
                        except Exception:
                            pass
            except Exception:
                pass

            # nameChildren 제거 — root layer 에 박힌 spec 자식만. reference 안에서
            # 들어온 composition-only 자식 prim 은 (2) 에서 references 가 비었으므로
            # 자연 사라진다 (composition 재평가).
            try:
                names = list(spec.nameChildren.keys())
            except Exception:
                names = []
            for cn in names:
                try:
                    del spec.nameChildren[cn]
                    diag["removed_children_spec"] = int(diag["removed_children_spec"]) + 1
                except Exception as exc:
                    print(
                        f"{_PRINT_PREFIX} clear_instance_contents: nameChildren del 실패 "
                        f"layer={layer.identifier} prim={prim_path} child={cn}: {exc}",
                        flush=True,
                    )

        # (3) 인스턴스 prim 자체는 유지 — root layer 에 Xform 으로 보장.
        try:
            self._master.set_root_layer_edit_target()
            UsdGeom.Xform.Define(stage, prim_path)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} clear_instance_contents: Xform.Define 보장 실패 "
                f"prim={prim_path}: {exc}",
                flush=True,
            )

        # (4) Registry 측 표식 리셋 — bake/extract 상태 / asset_kind 모두 초기화.
        try:
            inst = self._registry.get_by_prim_path(prim_path)
        except Exception:
            inst = None
        if inst is None:
            try:
                for it in self._registry.all_instances():
                    if it.prim_path == prim_path:
                        inst = it
                        break
            except Exception:
                inst = None
        if inst is not None:
            try:
                inst.baked = False
            except Exception:
                pass
            try:
                inst.source_asset = ""
            except Exception:
                pass
            try:
                from .tbs_types import ASSET_KIND_UNKNOWN

                inst.asset_kind = ASSET_KIND_UNKNOWN
            except Exception:
                pass
            try:
                inst.mirror_root_prim_path = ""
            except Exception:
                pass

        diag["ok"] = True
        print(
            f"{_PRINT_PREFIX} clear_instance_contents OK prim={prim_path} "
            f"refs={diag['cleared_refs']} payloads={diag['cleared_payloads']} "
            f"other={diag['cleared_other']} children_spec={diag['removed_children_spec']} "
            f"inst_sublayer_removed={diag['removed_inst_sublayer']}",
            flush=True,
        )
        return diag

    # ----------------------------------------------------------------- private

    def _sync_stage_and_timeline_with_source(
        self,
        stage,
        *,
        source_asset: str,
        src_start_tc: float,
        src_end_tc: float,
    ) -> None:
        """add_usd 직후 master stage / omni.timeline 의 timing metadata 정합성 보정.

        - master 의 ``startTimeCode`` / ``endTimeCode`` 가 자산보다 좁으면 자산 범위까지
          확장 (이미 더 넓으면 유지).
        - master tcps (= TBS_FIXED_FPS=30) 기준 seconds 로 변환하여 ``omni.timeline`` 의
          start/end time 을 동기화.
        - ``master.force_fixed_fps_30()`` 를 다시 호출해 framerate / tcps / carb.settings
          를 30 으로 재확정 + read-back 진단.
        - source 자산 자체의 raw tcps / fps / sample 통계도 1행으로 출력.
        """
        try:
            from .tbs_types import TBS_FIXED_FPS
        except Exception:
            TBS_FIXED_FPS = 30.0  # type: ignore[assignment]

        fps = float(TBS_FIXED_FPS)

        # (1) source 자산의 raw 메타데이터 + sample 범위 진단.
        raw_src = self._diagnose_source_timing(
            source_asset, src_start_tc=src_start_tc, src_end_tc=src_end_tc
        )

        # (2) master start/end time 확장 — 사용자가 작업 중인 다른 자산이 더 넓을 수
        #     있으므로 강제 set 이 아니라 max 로 확장. (음수 start 도 자산이 그렇다면 허용.)
        try:
            cur_start = float(stage.GetStartTimeCode())
            cur_end = float(stage.GetEndTimeCode())
        except Exception:
            cur_start, cur_end = 0.0, 0.0
        new_start = min(cur_start, float(src_start_tc))
        new_end = max(cur_end, float(src_end_tc))
        try:
            if new_start < cur_start - 1e-6:
                stage.SetStartTimeCode(float(new_start))
            if new_end > cur_end + 1e-6:
                stage.SetEndTimeCode(float(new_end))
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} stage start/end timecode set FAIL: {exc}",
                flush=True,
            )

        # (3) omni.timeline 의 start/end seconds 동기화. fps 동기화는 (4) 가 담당.
        try:
            import omni.timeline as _ot  # type: ignore

            tl = _ot.get_timeline_interface()
        except Exception:
            tl = None
        if tl is not None:
            try:
                start_sec = float(new_start) / fps
                end_sec = float(new_end) / fps
                if hasattr(tl, "set_start_time"):
                    tl.set_start_time(start_sec)
                if hasattr(tl, "set_end_time"):
                    tl.set_end_time(end_sec)
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} timeline start/end set FAIL: {exc}",
                    flush=True,
                )

        # (4) framerate / tcps / carb.settings 일괄 30 재확정 + read-back 진단.
        try:
            fn = getattr(self._master, "force_fixed_fps_30", None)
            if callable(fn):
                fn()
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} master.force_fixed_fps_30 (post add_usd) FAIL: "
                f"{exc}",
                flush=True,
            )

        print(
            f"{_PRINT_PREFIX} timing_sync master tc=[{new_start:.3f},{new_end:.3f}] "
            f"@{fps}fps source({raw_src})",
            flush=True,
        )

    def _diagnose_source_timing(
        self,
        asset_path: str,
        *,
        src_start_tc: float,
        src_end_tc: float,
    ) -> str:
        """source 자산의 raw timing metadata + sample 분포를 1행 요약 문자열로 반환.

        예: ``tcps=24.0 fps=24.0 stage=[0.0,1000.0] sample_tc=[0.0,1000.0] attrs=18``
        """
        if not asset_path or not os.path.isfile(asset_path):
            return "no_file"
        try:
            from pxr import Usd  # type: ignore

            src = Usd.Stage.Open(asset_path)
            if src is None:
                return "open_fail"
            try:
                raw_tcps = float(src.GetTimeCodesPerSecond())
            except Exception:
                raw_tcps = -1.0
            try:
                raw_fps = float(src.GetFramesPerSecond())
            except Exception:
                raw_fps = -1.0
            tc_min = float("inf")
            tc_max = float("-inf")
            n_attr = 0
            try:
                for p in src.Traverse():
                    for a in p.GetAttributes():
                        try:
                            ts = a.GetTimeSamples()
                        except Exception:
                            ts = ()
                        if not ts:
                            continue
                        n_attr += 1
                        if ts[0] < tc_min:
                            tc_min = float(ts[0])
                        if ts[-1] > tc_max:
                            tc_max = float(ts[-1])
            except Exception:
                pass
            if tc_min == float("inf"):
                sample_str = "samples=none"
            else:
                sample_str = f"sample_tc=[{tc_min:.3f},{tc_max:.3f}] attrs={n_attr}"
            return (
                f"tcps={raw_tcps} fps={raw_fps} "
                f"stage=[{src_start_tc:.3f},{src_end_tc:.3f}] {sample_str}"
            )
        except Exception as exc:
            return f"exc={exc}"

    def _register_only(
        self,
        *,
        prim_path: str,
        instance_id: str,
        source_asset: str,
        s: float,
        e: float,
        tps: float,
        asset_kind: str = ASSET_KIND_UNKNOWN,
        asset_diag: Optional[AssetDiag] = None,
    ) -> AnimationInstance:
        """USD author 가 안 되는 환경(unit test/CI 등) 폴백.

        Q2 — 2026-05-12: add_usd 의 메인 author 경로가 부분 실패해 이 폴백으로 떨어져도
        분류 결과가 사라지지 않게 `asset_kind` / `asset_diag` 를 register 인수로 전달해
        `_notify` 전에 박히도록 한다. 이전에는 register 후에 별도로 박는 방식이라
        listener 가 UNKNOWN 상태로 먼저 UI 를 그리는 회귀가 있었다.
        """
        return self._registry.register(
            prim_path=prim_path,
            instance_id=instance_id,
            source_asset=source_asset,
            asset_start_time=s,
            asset_end_time=e,
            asset_tps=tps,
            asset_kind=asset_kind,
            asset_diag=asset_diag or AssetDiag(),
        )


__all__ = ["MultiUsdLoader"]
