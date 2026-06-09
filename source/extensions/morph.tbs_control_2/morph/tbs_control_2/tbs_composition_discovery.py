"""L2 — Composition Discovery.

master stage 를 traverse 하여 "재생 가능한 인스턴스 후보" 를 발견한다.

규칙(USD_Timeline_Spec.md REQ-004):
- R1 우선: prim 의 customData 에 `lam:instance == True` 가 있으면 인스턴스로 등록
          (LAM 이 직접 author 한 prim — REQ-005 메타 그대로 채움)
- R2 보조: prim 이 reference/payload 를 가지고, 그 reference target USD 가 timeSamples 보유
- R3 최후: prim 의 임의 attribute 가 timeSamples 보유

R1 > R2 > R3. 동일 prim 이 다중 규칙으로 발견되어도 인스턴스는 1개만.

등록 범위 (2026-05-17):
- R1 은 `lam:instance` 가 있는 prim (통상 `/World/<instance_id>`).
- R2/R3 은 `/World` 의 **직계 자식** prim 만 — reference 내부·drag&drop 하위 경로는
  별도 인스턴스로 올리지 않는다 (합성 USD 재오픈 시 목록이 2~3개로 유지되도록).
"""

from __future__ import annotations

import os
from typing import List

from .tbs_instance_registry import AnimationInstanceRegistry, slugify_instance_id
from .tbs_master_stage import MasterStage
from .tbs_multi_usd_loader import read_asset_time_range
from .tbs_types import AnimationInstance


_PRINT_PREFIX = "[TBS/L2]"

_DEFAULT_TPS = 30.0


def _has_lam_instance_custom_data(prim) -> bool:
    """USD save/load 후 customData 형식이 달라져도 R1 이 동작하도록 best-effort 판별."""
    try:
        v = prim.GetCustomDataByKey("lam:instance")
        if v is True or v == 1 or v == "true":
            return True
        if isinstance(v, str) and v.strip().lower() in ("true", "1", "yes"):
            return True
    except Exception:
        pass
    return False


def _is_world_direct_child(prim_path: str) -> bool:
    """`/World/<name>` 형태의 인스턴스 루트 prim 인지 (깊이 2)."""
    parts = [p for p in (prim_path or "").split("/") if p]
    return len(parts) == 2 and parts[0] == "World"


def _stage_local_time_range(prim) -> tuple[float, float, float]:
    """prim 산하 attribute 들의 timeSamples 에서 (min, max, tps) 를 best-effort 추출.

    R3 자산처럼 stage time 정보가 없을 때 사용.
    """
    mn: float | None = None
    mx: float | None = None
    try:
        from pxr import Usd  # type: ignore  # noqa: F401

        try:
            stack = [prim]
        except Exception:
            stack = []
        while stack:
            p = stack.pop()
            try:
                for ch in p.GetAllChildren():
                    stack.append(ch)
            except Exception:
                pass
            try:
                attrs = p.GetAuthoredAttributes()
            except Exception:
                attrs = []
            for attr in attrs:
                try:
                    n = attr.GetNumTimeSamples()
                except Exception:
                    continue
                if n <= 0:
                    continue
                try:
                    samples = attr.GetTimeSamples()
                except Exception:
                    samples = []
                if not samples:
                    continue
                a = float(samples[0])
                b = float(samples[-1])
                mn = a if mn is None or a < mn else mn
                mx = b if mx is None or b > mx else mx
    except Exception:
        pass

    if mn is None or mx is None:
        return (0.0, 0.0, _DEFAULT_TPS)
    return (mn, mx, _DEFAULT_TPS)


class CompositionDiscovery:
    """master stage 의 prim 트리에서 인스턴스 후보를 찾아 Registry 에 등록."""

    def __init__(self, master: MasterStage, registry: AnimationInstanceRegistry) -> None:
        self._master = master
        self._registry = registry

    def discover(self) -> List[AnimationInstance]:
        """현재 master stage 안의 모든 인스턴스 후보를 등록(이미 등록된 것은 skip)."""
        stage = self._master.get_stage()
        if stage is None:
            print(f"{_PRINT_PREFIX} discover: no stage", flush=True)
            return []

        try:
            from pxr import Usd, UsdGeom  # type: ignore  # noqa: F401
        except Exception as exc:
            print(f"{_PRINT_PREFIX} pxr not available: {exc}", flush=True)
            return []

        added: List[AnimationInstance] = []
        for prim in stage.Traverse():
            try:
                prim_path = str(prim.GetPath())
            except Exception:
                continue

            if self._registry.get_by_prim_path(prim_path) is not None:
                continue  # 이미 등록됨

            if _has_lam_instance_custom_data(prim):
                inst = self._try_r1(prim, prim_path)
            elif _is_world_direct_child(prim_path):
                inst = self._try_r2(prim, prim_path) or self._try_r3(prim, prim_path)
            else:
                continue

            if inst is not None:
                added.append(inst)

        print(f"{_PRINT_PREFIX} discover added={len(added)}", flush=True)
        return added

    # ----------------------------------------------------------------- R1
    def _try_r1(self, prim, prim_path: str) -> AnimationInstance | None:
        try:
            if not _has_lam_instance_custom_data(prim):
                return None
            source_asset = prim.GetCustomDataByKey("lam:source_asset") or ""
            metadata = {
                "lam:instance": True,
                "lam:guid": prim.GetCustomDataByKey("lam:guid") or "",
                "lam:instance_id": prim.GetCustomDataByKey("lam:instance_id") or os.path.basename(prim_path),
                "lam:source_asset": source_asset,
            }
            inst = self._registry.from_metadata(prim_path=prim_path, metadata=metadata)
            if inst is not None:
                self._fill_time_info(inst, prim, source_asset)
            return inst
        except Exception:
            return None

    # ----------------------------------------------------------------- R2
    def _try_r2(self, prim, prim_path: str) -> AnimationInstance | None:
        try:
            refs = prim.GetReferences()
            payloads = prim.GetPayloads()
            has_composition = bool(refs.GetAddedOrExplicitItems()) or bool(payloads.GetAddedOrExplicitItems())
            if not has_composition:
                return None
        except Exception:
            return None
        # R2 만족 시: 자산 path 는 reference list 에서 best-effort 로 첫 항목 추출.
        source_asset = ""
        try:
            items = list(prim.GetReferences().GetAddedOrExplicitItems())
            if items:
                source_asset = str(items[0].assetPath or "")
        except Exception:
            pass
        instance_id = slugify_instance_id(prim.GetName() or os.path.basename(prim_path))
        inst = self._registry.register(
            prim_path=prim_path,
            instance_id=instance_id,
            source_asset=source_asset,
            discovered_by="composition_discovery",
        )
        if inst is not None:
            self._fill_time_info(inst, prim, source_asset)
        return inst

    # ----------------------------------------------------------------- R3
    def _try_r3(self, prim, prim_path: str) -> AnimationInstance | None:
        try:
            for attr in prim.GetAttributes():
                try:
                    if attr.GetNumTimeSamples() > 0:
                        instance_id = slugify_instance_id(prim.GetName() or os.path.basename(prim_path))
                        inst = self._registry.register(
                            prim_path=prim_path,
                            instance_id=instance_id,
                            source_asset="",
                            discovered_by="composition_discovery",
                        )
                        if inst is not None:
                            self._fill_time_info(inst, prim, "")
                        return inst
                except Exception:
                    continue
        except Exception:
            return None
        return None

    # --------------------------------------------------------------- helpers

    def _fill_time_info(self, inst: AnimationInstance, prim, source_asset: str) -> None:
        """Phase 3 — 인스턴스의 asset_start/end/tps 를 best-effort 로 채운다.

        우선순위:
          1) source_asset 파일이 실제로 존재하면 그 USD 의 stage start/end/tps 사용.
          2) 그렇지 않으면 prim 산하 attribute 의 timeSamples 범위에서 추출(tps 는 default).
        """
        # 이미 채워져 있으면 그대로(중복 호출 방지).
        if inst.asset_end_time > inst.asset_start_time and inst.asset_tps > 0:
            return

        if source_asset:
            try:
                abs_path = source_asset
                if not os.path.isabs(abs_path):
                    # master.usd 가 저장된 디렉터리 기준 상대 경로로 시도(REQ-005 P-2).
                    master_path = self._master.master_path
                    if master_path:
                        cand = os.path.normpath(
                            os.path.join(os.path.dirname(os.path.abspath(master_path)), source_asset)
                        )
                        if os.path.isfile(cand):
                            abs_path = cand
                if os.path.isfile(abs_path):
                    s, e, tps = read_asset_time_range(abs_path)
                    if e > s:
                        inst.asset_start_time = s
                        inst.asset_end_time = e
                        inst.asset_tps = tps
                        return
            except Exception:
                pass

        # 폴백 — prim 자체의 timeSamples 에서 추정.
        s2, e2, tps2 = _stage_local_time_range(prim)
        if e2 > s2:
            inst.asset_start_time = s2
            inst.asset_end_time = e2
            inst.asset_tps = tps2


__all__ = ["CompositionDiscovery"]
