"""REQ-006 — Sequence Step ↔ Animation Instance Binding.

시퀀스 step 안의 4-튜플 `ref` (`prim_path / guid / instance_id / source_asset`)
를 우선순위 순으로 시도하여 현재 Registry 안의 `AnimationInstance` 와 매칭한다.

권장값(USD_Timeline_Spec.md REQ-006):
- Q-1 우선순위: guid → prim_path → instance_id → source_asset
- Q-3 자동 갱신: 매칭 성공 시 ref 4 키를 인스턴스의 현재 값으로 덮어 씀

본 모듈은 Registry 의 read-only API 만 사용한다(상태 수정 없음).
"""

from __future__ import annotations

import os
from typing import Iterable

from .tbs_types import (
    RESOLVE_AUTO,
    RESOLVE_MISSING,
    RESOLVE_OK,
    AnimationInstance,
    ResolveResult,
    StepRef,
)


def _by_guid(instances: Iterable[AnimationInstance], guid: str) -> AnimationInstance | None:
    if not guid:
        return None
    for inst in instances:
        if inst.guid == guid:
            return inst
    return None


def _by_prim_path(instances: Iterable[AnimationInstance], prim_path: str) -> AnimationInstance | None:
    if not prim_path:
        return None
    for inst in instances:
        if inst.prim_path == prim_path:
            return inst
    return None


def _by_instance_id(instances: Iterable[AnimationInstance], instance_id: str) -> AnimationInstance | None:
    if not instance_id:
        return None
    for inst in instances:
        if inst.instance_id == instance_id:
            return inst
    return None


def _by_source_asset(instances: Iterable[AnimationInstance], source_asset: str) -> AnimationInstance | None:
    if not source_asset:
        return None
    target_base = os.path.basename(source_asset).strip().lower()
    if not target_base:
        return None
    for inst in instances:
        cand = (inst.source_asset or "").strip()
        if not cand:
            continue
        if cand == source_asset:
            return inst
        if os.path.basename(cand).strip().lower() == target_base:
            return inst
    return None


def resolve_step_ref(
    instances: Iterable[AnimationInstance],
    ref: StepRef,
) -> ResolveResult:
    """우선순위 Resolver. 매칭 성공 시 자동 갱신된 ref 도 함께 반환."""

    cand_list = list(instances)

    # 1순위: guid (영구 고유 ID)
    inst = _by_guid(cand_list, ref.guid)
    if inst is not None:
        return _success(inst, matched_by="guid", status=RESOLVE_OK)

    # 2순위: prim_path
    inst = _by_prim_path(cand_list, ref.prim_path)
    if inst is not None:
        return _success(inst, matched_by="prim_path", status=RESOLVE_OK)

    # 3순위: instance_id (사용자 친화 alias)
    inst = _by_instance_id(cand_list, ref.instance_id)
    if inst is not None:
        return _success(inst, matched_by="instance_id", status=RESOLVE_AUTO)

    # 4순위: source_asset (절대 경로 또는 basename)
    inst = _by_source_asset(cand_list, ref.source_asset)
    if inst is not None:
        return _success(inst, matched_by="source_asset", status=RESOLVE_AUTO)

    return ResolveResult(status=RESOLVE_MISSING, matched_by="", instance=None, updated_ref=None)


def _success(inst: AnimationInstance, *, matched_by: str, status: str) -> ResolveResult:
    """매칭 성공 시 새 ref 를 즉시 만들어서 반환(Q-3 자동 갱신)."""
    new_ref = StepRef(
        prim_path=inst.prim_path,
        guid=inst.guid,
        instance_id=inst.instance_id,
        source_asset=inst.source_asset,
    )
    return ResolveResult(status=status, matched_by=matched_by, instance=inst, updated_ref=new_ref)


__all__ = ["resolve_step_ref"]
