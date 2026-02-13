# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""
선택된 prim 주변 sibling prim들을 숨기거나 반투명 머티리얼로 오버라이드하는 확장.
"""

import asyncio
import math
import time

import carb
import carb.events
import omni.ext
import omni.kit.app
import omni.ui as ui
import omni.usd
from carb.eventdispatcher import get_eventdispatcher
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

from morph.select_near_hide.occlusion_hide import (
    collect_occlusion_prim_paths_sibling,
    get_camera_world_position,
)


MODE_HIDE = "hide"
MODE_TRANSPARENT = "transparent"
MODE_OCCLUSION_HIDE = "occlusion_hide"
GHOST_MATERIAL_PATHS = ["/Looks/GhostPBR", "/World/Looks/GhostPBR"]
# bound material 캐시에 저장하는 sentinel 값(이미 Ghost 바인딩된 prim)
ALREADY_GHOST_SENTINEL = "__already_ghost__"
# 선택 prim 기준 주변 적용 반경 (USD stage 단위)
TRANSPARENT_RADIUS_METERS = 50.0
# Occlusion Hide 카메라 추적 시 update 쿼리 최소 간격(초)
OCCLUSION_UPDATE_INTERVAL_SEC = 0.1


def _log_info(message: str):
    """Kit 로그와 콘솔 print를 동시에 출력합니다."""
    carb.log_info(message)
    print(message, flush=True)


def _log_warn(message: str):
    """Kit 경고 로그와 콘솔 print를 동시에 출력합니다."""
    carb.log_warn(message)
    print(message, flush=True)


def _get_sibling_paths(stage: Usd.Stage, selected_paths: list[str]) -> set[str]:
    """선택된 prim들의 sibling(같은 부모의 다른 자식) 경로들을 반환합니다."""
    siblings = set()
    selected_set = set(selected_paths)

    for path_str in selected_paths:
        prim = stage.GetPrimAtPath(Sdf.Path(path_str))
        if not prim or not prim.IsValid():
            continue

        parent = prim.GetParent()
        if not parent or not parent.IsValid():
            continue

        for child in parent.GetChildren():
            child_path = str(child.GetPath())
            if child_path not in selected_set:
                siblings.add(child_path)

    return siblings


def _get_sibling_paths_with_ancestor_fallback(stage: Usd.Stage, selected_paths: list[str]) -> set[str]:
    """
    기본 sibling 수집 결과가 없을 때, 직계 조상으로 올라가며 sibling을 찾는 fallback.
    예: 선택 prim이 외동 자식이면 한 단계 위(또는 그 이상) 레벨의 형제 branch를 사용.
    """
    siblings = _get_sibling_paths(stage, selected_paths)
    if siblings:
        return siblings

    fallback = set()
    for path_str in selected_paths:
        prim = stage.GetPrimAtPath(Sdf.Path(path_str))
        if not prim or not prim.IsValid():
            continue

        cur = prim
        while cur and cur.IsValid():
            parent = cur.GetParent()
            if not parent or not parent.IsValid():
                break
            children = list(parent.GetChildren())
            if len(children) > 1:
                cur_path = str(cur.GetPath())
                for child in children:
                    cpath = str(child.GetPath())
                    if cpath != cur_path:
                        fallback.add(cpath)
                break
            cur = parent
    return fallback


def _restore_prims(stage: Usd.Stage, session_layer: Sdf.Layer, paths_to_restore: dict[str, dict]) -> None:
    """저장된 visibility 또는 material:binding 값을 복원합니다."""
    if not stage or not session_layer or not paths_to_restore:
        return

    with Usd.EditContext(stage, Usd.EditTarget(session_layer)):
        for path_str, saved in paths_to_restore.items():
            if path_str.startswith("__prim:"):
                prim_path = path_str[len("__prim:"):]
                prim = stage.GetPrimAtPath(prim_path)
                if not prim or not prim.IsValid():
                    continue
                binding_api = UsdShade.MaterialBindingAPI.Apply(prim)
                try:
                    if saved.get("had_material", False):
                        material_path = saved.get("material_path")
                        mprim = stage.GetPrimAtPath(material_path) if material_path else None
                        if mprim and mprim.IsValid() and mprim.IsA(UsdShade.Material):
                            binding_api.Bind(UsdShade.Material(mprim))
                        else:
                            binding_api.UnbindDirectBinding()
                    else:
                        binding_api.UnbindDirectBinding()
                except Exception:
                    pass
                continue

            prim = stage.GetPrimAtPath(path_str)
            if not prim or not prim.IsValid():
                continue
            if "visibility" in saved:
                attr = prim.GetAttribute("visibility")
                if attr:
                    attr.Set(saved["visibility"])


def _apply_hide(stage: Usd.Stage, session_layer: Sdf.Layer, paths: set[str]) -> dict[str, dict]:
    """지정 경로들에 hide를 적용하고 복원용 원래 visibility를 반환합니다."""
    if not stage or not session_layer or not paths:
        return {}

    saved: dict[str, dict] = {}
    with Usd.EditContext(stage, Usd.EditTarget(session_layer)):
        for path_str in paths:
            prim = stage.GetPrimAtPath(path_str)
            if not prim or not prim.IsValid():
                continue

            saved[path_str] = {}
            vis_attr = prim.GetAttribute("visibility")
            if not vis_attr:
                imageable = UsdGeom.Imageable(prim)
                if imageable:
                    vis_attr = imageable.CreateVisibilityAttr()
            if not vis_attr:
                continue

            try:
                orig = vis_attr.Get()
                if orig is not None:
                    saved[path_str]["visibility"] = orig
                vis_attr.Set("invisible")
            except Exception:
                pass
    return saved


def _get_ghost_material(stage: Usd.Stage):
    """GhostPBR 머티리얼 prim을 반환합니다."""
    for p in GHOST_MATERIAL_PATHS:
        prim = stage.GetPrimAtPath(p)
        if prim and prim.IsValid() and prim.IsA(UsdShade.Material):
            _log_info(f"[morph.select_near_hide] GhostPBR found at: {p}")
            return UsdShade.Material(prim)
    _log_warn(
        "[morph.select_near_hide] GhostPBR not found. Tried: " + ", ".join(GHOST_MATERIAL_PATHS)
    )
    return None


def _get_world_center(cache: UsdGeom.BBoxCache, prim: Usd.Prim):
    """Prim의 월드 바운드 중심을 반환합니다."""
    if not prim or not prim.IsValid():
        return None
    try:
        bbox = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        return bbox.GetCenter()
    except Exception:
        return None


def _get_world_position_from_xform(prim: Usd.Prim, xform_cache: UsdGeom.XformCache):
    """prim(또는 조상 Xform)의 월드 위치를 XformCache로 계산합니다."""
    if not prim or not prim.IsValid():
        return None
    try:
        cur = prim
        while cur and cur.IsValid():
            # Xform 타입뿐 아니라 transformable prim 모두 허용
            xformable = UsdGeom.Xformable(cur)
            if xformable:
                m = xform_cache.GetLocalToWorldTransform(cur)
                return m.ExtractTranslation()
            cur = cur.GetParent()
    except Exception:
        return None
    return None


def _get_cached_center(
    cache: UsdGeom.BBoxCache,
    xform_cache: UsdGeom.XformCache,
    prim: Usd.Prim,
    center_cache: dict[str, object],
):
    """Prim 중심점을 캐시하여 반복 계산 비용을 줄입니다."""
    if not prim or not prim.IsValid():
        return None
    p = str(prim.GetPath())
    if p in center_cache:
        return center_cache[p]
    c = _get_world_center(cache, prim)
    if c is None:
        c = _get_world_position_from_xform(prim, xform_cache)
    center_cache[p] = c
    return c


def _collect_reference_centers(
    cache: UsdGeom.BBoxCache,
    xform_cache: UsdGeom.XformCache,
    prim: Usd.Prim,
    center_cache: dict[str, object],
) -> list:
    """
    거리 계산용 기준 중심점 목록을 수집합니다.
    - 선택 prim 자체 중심
    - 선택 prim이 Xform/상위 노드면 하위 Gprim 중심들도 포함
    """
    centers = []
    if not prim or not prim.IsValid():
        return centers

    c = _get_cached_center(cache, xform_cache, prim, center_cache)
    if c is not None:
        centers.append(c)

    # 상위 노드 선택 시 하위 Mesh 기준도 포함해야 10m 필터가 자연스럽게 동작
    if not prim.IsA(UsdGeom.Gprim):
        for p in Usd.PrimRange(prim):
            if not p or not p.IsValid() or not p.IsA(UsdGeom.Gprim):
                continue
            gc = _get_cached_center(cache, xform_cache, p, center_cache)
            if gc is not None:
                centers.append(gc)
    return centers


def _is_transparent_target_prim(prim: Usd.Prim, mesh_subset_cache: dict[str, bool]) -> bool:
    """
    Semi-Transparent 적용 대상 판별.
    - GeomSubset이 있으면 Mesh(Gprim)에는 적용하지 않고 GeomSubset에만 적용
    - GeomSubset이 없으면 기존처럼 Gprim에 적용
    """
    if not prim or not prim.IsValid():
        return False
    if prim.IsA(UsdGeom.Subset):
        # GeomSubset은 보통 Mesh 하위에서만 유효하므로 부모가 Gprim일 때만 대상 처리
        parent = prim.GetParent()
        if not parent or not parent.IsValid() or not parent.IsA(UsdGeom.Gprim):
            return False
        return True
    if prim.IsA(UsdGeom.Gprim):
        # Mesh 하위에 GeomSubset이 있는 경우 Mesh 직접 바인딩은 제외
        p = str(prim.GetPath())
        has_subset = mesh_subset_cache.get(p)
        if has_subset is None:
            has_subset = False
            for child in prim.GetChildren():
                if child and child.IsValid() and child.IsA(UsdGeom.Subset):
                    has_subset = True
                    break
            mesh_subset_cache[p] = has_subset
        return not has_subset
    return False


def _compute_world_aligned_box(cache: UsdGeom.BBoxCache, prim: Usd.Prim):
    """Prim의 월드 정렬 AABB를 반환합니다. 실패 시 None."""
    if not prim or not prim.IsValid():
        return None
    try:
        box = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        mn = box.GetMin()
        mx = box.GetMax()
        # 빈/비정상 AABB(무한대, NaN, min>max)는 pruning/filter에 사용하면 누락을 만들 수 있으므로 무효 처리
        if (
            (not math.isfinite(mn[0]))
            or (not math.isfinite(mn[1]))
            or (not math.isfinite(mn[2]))
            or (not math.isfinite(mx[0]))
            or (not math.isfinite(mx[1]))
            or (not math.isfinite(mx[2]))
            or (mn[0] > mx[0])
            or (mn[1] > mx[1])
            or (mn[2] > mx[2])
        ):
            return None
        return box
    except Exception:
        return None


def _get_cached_world_aligned_box(cache: UsdGeom.BBoxCache, prim: Usd.Prim, box_cache: dict[str, object]):
    """Prim 월드 AABB를 실행 단위 캐시에 저장/재사용합니다."""
    if not prim or not prim.IsValid():
        return None
    p = str(prim.GetPath())
    if p in box_cache:
        return box_cache[p]
    box = _compute_world_aligned_box(cache, prim)
    box_cache[p] = box
    return box


def _sphere_intersects_box(center, radius2: float, box) -> bool:
    """
    구(선택 반경)와 AABB 교차 여부를 검사합니다.
    AABB와 점(center) 사이 최소거리^2 <= radius^2이면 교차로 판단합니다.
    """
    mn = box.GetMin()
    mx = box.GetMax()
    dx = 0.0
    dy = 0.0
    dz = 0.0

    if center[0] < mn[0]:
        d = mn[0] - center[0]
        dx = d * d
    elif center[0] > mx[0]:
        d = center[0] - mx[0]
        dx = d * d

    if center[1] < mn[1]:
        d = mn[1] - center[1]
        dy = d * d
    elif center[1] > mx[1]:
        d = center[1] - mx[1]
        dy = d * d

    if center[2] < mn[2]:
        d = mn[2] - center[2]
        dz = d * d
    elif center[2] > mx[2]:
        d = center[2] - mx[2]
        dz = d * d

    return (dx + dy + dz) <= radius2


def _box_intersects_any_selected_sphere(box, selected_centers: list, radius2: float) -> bool:
    """AABB가 선택 중심들의 반경 구 중 하나와라도 교차하면 True."""
    for sc in selected_centers:
        if _sphere_intersects_box(sc, radius2, box):
            return True
    return False


def _filter_roots_by_spatial_index(
    stage: Usd.Stage,
    cache: UsdGeom.BBoxCache,
    sibling_paths: set[str],
    selected_centers: list,
    radius2: float,
    root_bbox_cache: dict[str, object],
    box_cache: dict[str, object],
):
    """
    장기 캐시(root_bbox_cache)를 이용해 sibling root를 1차 필터링합니다.
    - 캐시에 없으면 1회 계산 후 저장
    - root AABB가 반경 구들과 불교차면 해당 root는 탐색 대상에서 제외
    """
    filtered_roots: list[Sdf.Path] = []
    skipped = 0
    cache_hit = 0
    cache_miss = 0

    for root_path in sibling_paths:
        root = stage.GetPrimAtPath(root_path)
        if not root or not root.IsValid():
            continue

        box = root_bbox_cache.get(root_path)
        if box is None:
            box = _get_cached_world_aligned_box(cache, root, box_cache)
            root_bbox_cache[root_path] = box
            cache_miss += 1
        else:
            cache_hit += 1
            # root 장기 캐시에 값이 있으면 실행 단위 캐시에도 미리 동기화
            box_cache[root_path] = box

        if box is None:
            # 바운드 계산 실패 root는 정확도 보장을 위해 제외하지 않고 통과
            filtered_roots.append(root.GetPath())
            continue

        if _box_intersects_any_selected_sphere(box, selected_centers, radius2):
            filtered_roots.append(root.GetPath())
        else:
            skipped += 1

    return filtered_roots, skipped, cache_hit, cache_miss


def _apply_transparent(
    stage: Usd.Stage,
    session_layer: Sdf.Layer,
    selected_paths: list[str],
    sibling_paths: set[str],
    bound_material_cache: dict[str, str],
    root_bbox_cache: dict[str, object],
    previous_saved: dict[str, dict] | None = None,
):
    """
    GhostPBR 머티리얼 바인딩을 반경 이내 sibling 범위에만 적용합니다.
    기존 material:binding은 __prim:<path> 키로 저장합니다.
    """
    saved: dict[str, dict] = {}
    previous_saved = previous_saved or {}
    if not stage or not session_layer or not selected_paths or not sibling_paths:
        _log_info(
            f"[morph.select_near_hide] transparent skipped: stage={bool(stage)}, "
            f"session_layer={bool(session_layer)}, selected={len(selected_paths) if selected_paths else 0}, "
            f"siblings={len(sibling_paths) if sibling_paths else 0}"
        )
        return saved

    ghost_material = _get_ghost_material(stage)
    if not ghost_material:
        return saved

    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=True,
    )
    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    center_cache: dict[str, object] = {}
    mesh_subset_cache: dict[str, bool] = {}
    box_cache: dict[str, object] = {}

    selected_centers = []
    selected_failed = []
    for sp in selected_paths:
        s_prim = stage.GetPrimAtPath(sp)
        centers = _collect_reference_centers(cache, xform_cache, s_prim, center_cache)
        if centers:
            selected_centers.extend(centers)
        else:
            selected_failed.append(sp)
    if not selected_centers:
        _log_warn(
            "[morph.select_near_hide] transparent skipped: selected prim center unavailable "
            f"(bbox/xform fallback both failed), selected_paths={selected_paths}"
        )
        return saved
    if selected_failed:
        _log_warn(
            f"[morph.select_near_hide] selected center fallback failed for {len(selected_failed)} path(s): "
            + ", ".join(selected_failed[:5])
        )

    radius2 = TRANSPARENT_RADIUS_METERS * TRANSPARENT_RADIUS_METERS

    # ---------------------------------------------------------------------
    # 1단계: "탐색/판별 단계" (Read 중심)
    # ---------------------------------------------------------------------
    # 이 단계에서는 어떤 prim에 바인딩을 적용할지 목록만 수집합니다.
    # 즉시 Bind()를 하지 않기 때문에 로직 추적/디버깅이 쉬워지고,
    # 후보 계산과 실제 Authoring을 분리할 수 있습니다.
    candidate_count = 0
    root_count = 0
    root_skipped_by_index = 0
    root_cache_hit = 0
    root_cache_miss = 0
    pruned_subtrees = 0
    target_prim_paths: list[str] = []
    target_path_set: set[str] = set()
    explore_t0 = time.perf_counter()

    # (1차) 장기 공간 인덱스: sibling root 후보를 먼저 축소
    filtered_roots, root_skipped_by_index, root_cache_hit, root_cache_miss = _filter_roots_by_spatial_index(
        stage, cache, sibling_paths, selected_centers, radius2, root_bbox_cache, box_cache
    )

    for root_path in filtered_roots:
        root = stage.GetPrimAtPath(root_path)
        if not root or not root.IsValid():
            continue
        root_count += 1

        # (2차) PruneChildren + AABB-구 교차 검사로 subtree를 안전하게 가지치기
        it = iter(Usd.PrimRange(root))
        for p in it:
            p_box = _get_cached_world_aligned_box(cache, p, box_cache)
            if p_box is not None and not _box_intersects_any_selected_sphere(p_box, selected_centers, radius2):
                it.PruneChildren()
                pruned_subtrees += 1
                continue

            # (내로우 페이즈) 실제 대상 prim만 거리 조건을 다시 검사
            if not _is_transparent_target_prim(p, mesh_subset_cache):
                continue
            candidate_count += 1

            # 최종 판정도 center 거리 대신 AABB 최소거리 기반으로 통일합니다.
            target_box = p_box
            if target_box is None:
                # GeomSubset AABB가 바로 계산되지 않으면 부모 Mesh AABB로 fallback
                if p.IsA(UsdGeom.Subset):
                    parent = p.GetParent()
                    if parent and parent.IsValid() and parent.IsA(UsdGeom.Gprim):
                        target_box = _get_cached_world_aligned_box(cache, parent, box_cache)
                if target_box is None:
                    continue

            if not _box_intersects_any_selected_sphere(target_box, selected_centers, radius2):
                continue

            p_path = str(p.GetPath())
            if p_path in target_path_set:
                continue
            target_path_set.add(p_path)
            target_prim_paths.append(p_path)
    explore_ms = (time.perf_counter() - explore_t0) * 1000.0

    # ---------------------------------------------------------------------
    # 2단계: "Delta 계산 + 일괄 Authoring 단계" (Write 중심)
    # ---------------------------------------------------------------------
    # 이전 적용 결과(previous_saved)와 현재 target을 비교해 변경분만 반영합니다.
    prev_path_to_saved: dict[str, dict] = {}
    for key, value in previous_saved.items():
        if key.startswith("__prim:"):
            prev_path_to_saved[key[len("__prim:"):]] = value

    current_target_set = set(target_prim_paths)
    prev_target_set = set(prev_path_to_saved.keys())
    to_restore_paths = prev_target_set - current_target_set
    to_add_paths = current_target_set - prev_target_set
    retained_paths = current_target_set & prev_target_set

    # 현재도 대상에 남아 있는 prim들은 기존 저장값을 그대로 유지
    for p_path in retained_paths:
        saved["__prim:" + p_path] = prev_path_to_saved[p_path]

    # 현재 대상에서 빠진 prim만 선택 복원
    restore_delta_t0 = time.perf_counter()
    if to_restore_paths:
        restore_subset = {"__prim:" + p: prev_path_to_saved[p] for p in to_restore_paths}
        _restore_prims(stage, session_layer, restore_subset)
    restore_delta_ms = (time.perf_counter() - restore_delta_t0) * 1000.0

    # 현재 새로 들어온 prim에 대해서만 바인딩 작성
    bind_t0 = time.perf_counter()
    with Usd.EditContext(stage, Usd.EditTarget(session_layer)):
        for p_path in to_add_paths:
            prim = stage.GetPrimAtPath(p_path)
            if not prim or not prim.IsValid():
                continue

            key = "__prim:" + p_path
            if key in saved:
                continue

            try:
                cached_mpath = bound_material_cache.get(p_path)
                if cached_mpath is not None:
                    if cached_mpath == ALREADY_GHOST_SENTINEL:
                        # 이미 Ghost 상태였던 prim은 다시 쓰지 않음(no-op write skip)
                        continue
                    if cached_mpath:
                        saved[key] = {"had_material": True, "material_path": cached_mpath}
                    else:
                        saved[key] = {"had_material": False}
                else:
                    # 원래 바인딩을 1회 조회 후 캐시에 보관해 이후 비용을 절감
                    binding_api = UsdShade.MaterialBindingAPI(prim)
                    original_material, _ = binding_api.ComputeBoundMaterial()
                    if original_material:
                        mpath = str(original_material.GetPath())
                        if mpath == str(ghost_material.GetPath()):
                            # 원래부터 Ghost인 prim은 복원 대상/재바인딩 대상에서 제외
                            bound_material_cache[p_path] = ALREADY_GHOST_SENTINEL
                            continue
                        saved[key] = {"had_material": True, "material_path": mpath}
                        bound_material_cache[p_path] = mpath
                    else:
                        saved[key] = {"had_material": False}
                        bound_material_cache[p_path] = ""

                # 실제 material:binding 오버라이드 적용
                binding_api = UsdShade.MaterialBindingAPI.Apply(prim)
                binding_api.Bind(ghost_material)
            except Exception:
                pass
    bind_ms = (time.perf_counter() - bind_t0) * 1000.0

    applied_now = len(saved) - len(retained_paths)
    _log_info(
        f"[morph.select_near_hide] transparent apply done: selected={len(selected_paths)}, "
        f"sibling_roots={len(sibling_paths)}, roots_scanned={root_count}, "
        f"roots_skipped_by_index={root_skipped_by_index}, root_cache_hit={root_cache_hit}, "
        f"root_cache_miss={root_cache_miss}, pruned_subtrees={pruned_subtrees}, "
        f"candidate_gprims={candidate_count}, targets={len(target_prim_paths)}, "
        f"delta_add={len(to_add_paths)}, delta_restore={len(to_restore_paths)}, retained={len(retained_paths)}, "
        f"applied={applied_now}, total_active={len(saved)}, radius={TRANSPARENT_RADIUS_METERS}, "
        f"explore_ms={explore_ms:.2f}, restore_ms={restore_delta_ms:.2f}, bind_ms={bind_ms:.2f}"
    )
    return saved


def some_public_function(x: int):
    """다른 확장에서 호출할 수 있는 공개 함수."""
    return x**x


class MyExtension(omni.ext.IExt):
    """선택된 prim 주변(sibling) prim들을 Hide 또는 GhostPBR로 처리하는 확장."""

    def on_startup(self, _ext_id):
        self._subscriptions = []
        self._window = None
        self._window_task = None
        self._selection_task = None

        self._enabled = False
        self._mode = MODE_HIDE
        self._last_affected_saved: dict[str, dict] = {}
        self._bound_material_cache: dict[str, str] = {}
        # 장기 공간 인덱스: sibling root path -> world aligned AABB(또는 None)
        self._root_bbox_cache: dict[str, object] = {}
        self._last_selection_key = ()
        self._last_mode_key = self._mode
        self._occlusion_update_sub = None
        self._last_occlusion_camera_pos: tuple[float, float, float] | None = None
        self._last_occlusion_update_time: float = 0.0

        usd_context = omni.usd.get_context()
        ed = get_eventdispatcher()
        self._subscriptions.append(
            ed.observe_event(
                observer_name="SelectNearHide:SelectionChanged",
                event_name=usd_context.stage_event_name(omni.usd.StageEventType.SELECTION_CHANGED),
                on_event=self._on_selection_changed,
            )
        )
        self._subscriptions.append(
            ed.observe_event(
                observer_name="SelectNearHide:StageOpened",
                event_name=usd_context.stage_event_name(omni.usd.StageEventType.OPENED),
                on_event=self._on_stage_opened,
            )
        )

        self._window_task = asyncio.ensure_future(self._create_window_late())
        _log_info("[morph.select_near_hide] Extension startup")

    def _on_stage_opened(self, _event):
        self._stop_occlusion_camera_tracking()
        self._last_affected_saved.clear()
        self._bound_material_cache.clear()
        self._root_bbox_cache.clear()
        self._last_selection_key = ()

    def _on_selection_changed(self, _event):
        """선택 변경 이벤트를 짧게 디바운스 처리."""
        if self._selection_task and not self._selection_task.done():
            self._selection_task.cancel()
        self._selection_task = asyncio.ensure_future(self._process_selection_changed_debounced())

    async def _process_selection_changed_debounced(self):
        try:
            await asyncio.sleep(0.12)
        except asyncio.CancelledError:
            return
        self._process_selection_changed()

    def _process_selection_changed(self):
        if not self._enabled:
            return

        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return
        sel = ctx.get_selection() if ctx else None
        if not sel:
            return
        session_layer = stage.GetSessionLayer()
        if not session_layer:
            return

        selected_paths = sel.get_selected_prim_paths()
        if not selected_paths:
            self._stop_occlusion_camera_tracking()
            if self._last_affected_saved:
                _log_info(
                    f"[morph.select_near_hide] restoring previous overrides: {len(self._last_affected_saved)} item(s)"
                )
                _restore_prims(stage, session_layer, self._last_affected_saved)
                self._last_affected_saved.clear()
            _log_info("[morph.select_near_hide] selection empty -> nothing to apply")
            self._last_selection_key = ()
            return

        selection_key = tuple(sorted(selected_paths))
        mode_changed = self._mode != self._last_mode_key
        if selection_key == self._last_selection_key and not mode_changed:
            return

        # 모드가 바뀌면 이전 모드 오버라이드는 먼저 전체 복원
        if mode_changed:
            self._stop_occlusion_camera_tracking()
        if mode_changed and self._last_affected_saved:
            _log_info(
                f"[morph.select_near_hide] mode changed -> restoring previous overrides: {len(self._last_affected_saved)} item(s)"
            )
            _restore_prims(stage, session_layer, self._last_affected_saved)
            self._last_affected_saved.clear()

        if self._mode == MODE_HIDE:
            # Hide 모드는 기존 동작을 유지(전체 갱신)
            if self._last_affected_saved:
                _log_info(
                    f"[morph.select_near_hide] restoring previous overrides: {len(self._last_affected_saved)} item(s)"
                )
                _restore_prims(stage, session_layer, self._last_affected_saved)
                self._last_affected_saved.clear()
            siblings = _get_sibling_paths(stage, selected_paths)
            if not siblings:
                _log_info("[morph.select_near_hide] no siblings found for current selection")
                return
            _log_info(
                f"[morph.select_near_hide] selection changed: mode={self._mode}, "
                f"selected={len(selected_paths)}, siblings={len(siblings)}"
            )
            self._last_affected_saved = _apply_hide(stage, session_layer, siblings)
            _log_info(f"[morph.select_near_hide] Applied hide to {len(self._last_affected_saved)} sibling(s)")
        elif self._mode == MODE_OCCLUSION_HIDE:
            # Occlusion Hide: sibling 기반 + ray-AABB, 카메라~선택 오브젝트 사이 sibling만 숨김
            if self._last_affected_saved:
                _log_info(
                    f"[morph.select_near_hide] restoring previous overrides: {len(self._last_affected_saved)} item(s)"
                )
                _restore_prims(stage, session_layer, self._last_affected_saved)
                self._last_affected_saved.clear()

            siblings = _get_sibling_paths_with_ancestor_fallback(stage, selected_paths)
            if not siblings:
                _log_info("[morph.select_near_hide] no sibling/ancestor-sibling found for occlusion hide")
                return
            paths = collect_occlusion_prim_paths_sibling(stage, selected_paths, siblings)
            if not paths:
                _log_info(
                    "[morph.select_near_hide] no occlusion prims found (camera or selected center unavailable)"
                )
                return
            _log_info(
                f"[morph.select_near_hide] selection changed: mode={self._mode}, "
                f"selected={len(selected_paths)}, occlusion_prims={len(paths)}"
            )
            self._last_affected_saved = _apply_hide(stage, session_layer, paths)
            _log_info(
                f"[morph.select_near_hide] Applied occlusion hide to {len(self._last_affected_saved)} prim(s)"
            )
            cam_pos = get_camera_world_position(stage)
            self._last_occlusion_camera_pos = (
                (float(cam_pos[0]), float(cam_pos[1]), float(cam_pos[2])) if cam_pos else None
            )
            self._start_occlusion_camera_tracking()
        else:
            siblings = _get_sibling_paths_with_ancestor_fallback(stage, selected_paths)
            if not siblings:
                _log_info("[morph.select_near_hide] no sibling/ancestor-sibling found for current selection")
                return
            _log_info(
                f"[morph.select_near_hide] selection changed: mode={self._mode}, "
                f"selected={len(selected_paths)}, siblings={len(siblings)}, radius={TRANSPARENT_RADIUS_METERS}"
            )
            self._last_affected_saved = _apply_transparent(
                stage,
                session_layer,
                selected_paths,
                siblings,
                self._bound_material_cache,
                self._root_bbox_cache,
                self._last_affected_saved if not mode_changed else {},
            )
            _log_info(
                f"[morph.select_near_hide] Applied transparent(within radius) "
                f"to {len(self._last_affected_saved)} prim(s)"
            )
        self._last_selection_key = selection_key
        self._last_mode_key = self._mode

    def _start_occlusion_camera_tracking(self):
        """Occlusion Hide 모드에서 카메라 변경 시 실시간 갱신을 위해 update 이벤트 구독."""
        if self._occlusion_update_sub is not None:
            return
        update_stream = omni.kit.app.get_app().get_update_event_stream()
        self._occlusion_update_sub = update_stream.create_subscription_to_pop(
            self._on_update_for_occlusion,
            name="SelectNearHide:OcclusionCameraUpdate",
        )

    def _stop_occlusion_camera_tracking(self):
        """Occlusion 카메라 추적 구독 해제."""
        if self._occlusion_update_sub is None:
            return
        try:
            self._occlusion_update_sub.unsubscribe()
        except Exception:
            pass
        self._occlusion_update_sub = None
        self._last_occlusion_camera_pos = None
        self._last_occlusion_update_time = 0.0

    def _on_update_for_occlusion(self, _event):
        """카메라 변경 시 occlusion hide 실시간 갱신 (쿼리 간격 제한)."""
        if not self._enabled or self._mode != MODE_OCCLUSION_HIDE:
            return
        now = time.perf_counter()
        if now - self._last_occlusion_update_time < OCCLUSION_UPDATE_INTERVAL_SEC:
            return
        self._last_occlusion_update_time = now
        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return
        sel = ctx.get_selection() if ctx else None
        if not sel:
            return
        selected_paths = sel.get_selected_prim_paths()
        if not selected_paths:
            return
        cam_pos = get_camera_world_position(stage)
        if cam_pos is None:
            return
        cam_tuple = (float(cam_pos[0]), float(cam_pos[1]), float(cam_pos[2]))
        eps = 1e-5
        if self._last_occlusion_camera_pos is not None:
            if all(abs(cam_tuple[i] - self._last_occlusion_camera_pos[i]) < eps for i in range(3)):
                return
        self._last_occlusion_camera_pos = cam_tuple
        session_layer = stage.GetSessionLayer()
        if not session_layer:
            return
        if self._last_affected_saved:
            _restore_prims(stage, session_layer, self._last_affected_saved)
            self._last_affected_saved.clear()

        siblings = _get_sibling_paths_with_ancestor_fallback(stage, selected_paths)
        if not siblings:
            return
        paths = collect_occlusion_prim_paths_sibling(stage, selected_paths, siblings)
        if not paths:
            return
        self._last_affected_saved = _apply_hide(stage, session_layer, paths)

    def _restore_all(self):
        self._stop_occlusion_camera_tracking()
        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return
        session_layer = stage.GetSessionLayer()
        if not session_layer or not self._last_affected_saved:
            _log_info("[morph.select_near_hide] restore_all skipped: nothing to restore")
            return
        _log_info(f"[morph.select_near_hide] restore_all: {len(self._last_affected_saved)} item(s)")
        _restore_prims(stage, session_layer, self._last_affected_saved)
        self._last_affected_saved.clear()
        _log_info("[morph.select_near_hide] Restored all affected prims")

    async def _create_window_late(self):
        app = omni.kit.app.get_app()
        for _ in range(15):
            await app.next_update_async()

        self._window = ui.Window(
            title="Select Near Hide",
            width=380,
            height=160,
            dock_preference=ui.DockPreference.RIGHT_TOP,
        )

        with self._window.frame:
            with ui.VStack(spacing=4, style={"margin": 2}, height=0):
                def on_enabled_changed(model):
                    self._enabled = model.get_value_as_bool()
                    if not self._enabled:
                        self._stop_occlusion_camera_tracking()
                        if self._last_affected_saved:
                            self._restore_all()

                enabled_model = ui.SimpleBoolModel(False)
                enabled_model.add_value_changed_fn(on_enabled_changed)
                with ui.HStack(height=20, spacing=0):
                    ui.CheckBox(model=enabled_model, name="Enable Select Near Hide")
                    ui.Label("Enable Select Near Hide", style={"font_size": 12}, width=0)
                    ui.Spacer()

                with ui.HStack(height=20, spacing=4):
                    ui.Label("Mode:", width=44, style={"font_size": 12})
                    def set_mode_hide():
                        self._mode = MODE_HIDE
                        self._refresh_mode_buttons()

                    def set_mode_transparent():
                        self._mode = MODE_TRANSPARENT
                        self._refresh_mode_buttons()

                    def set_mode_occlusion_hide():
                        self._mode = MODE_OCCLUSION_HIDE
                        self._refresh_mode_buttons()

                    self._btn_hide = ui.Button("Hide", clicked_fn=set_mode_hide, width=70)
                    self._btn_transparent = ui.Button("Semi-Transparent", clicked_fn=set_mode_transparent, width=110)
                    self._btn_occlusion_hide = ui.Button("Occlusion Hide", clicked_fn=set_mode_occlusion_hide, width=100)
                    ui.Spacer()

                def refresh_mode_buttons():
                    sel = {"background_color": 0xFF4A90E2}
                    unsel = {"background_color": 0xFF2B2B2B}
                    self._btn_hide.style = sel if self._mode == MODE_HIDE else unsel
                    self._btn_transparent.style = sel if self._mode == MODE_TRANSPARENT else unsel
                    self._btn_occlusion_hide.style = sel if self._mode == MODE_OCCLUSION_HIDE else unsel

                self._refresh_mode_buttons = refresh_mode_buttons
                self._refresh_mode_buttons()

                ui.Label(
                    f"Transparent range: within {int(TRANSPARENT_RADIUS_METERS)} from selected prim",
                    style={"font_size": 11},
                    height=18,
                )

                ui.Button(
                    "Restore All",
                    clicked_fn=lambda: self._restore_all(),
                    tooltip="Restore hidden/transparent prims to original state",
                    height=22,
                )

        try:
            if hasattr(self._window, "undock"):
                self._window.undock()
        except Exception as e:
            _log_warn(f"[morph.select_near_hide] undock failed: {e}")

    def on_shutdown(self):
        self._stop_occlusion_camera_tracking()
        if self._selection_task is not None:
            self._selection_task.cancel()
            self._selection_task = None
        if self._window_task is not None:
            self._window_task.cancel()
            self._window_task = None

        if self._last_affected_saved:
            try:
                ctx = omni.usd.get_context()
                stage = ctx.get_stage() if ctx else None
                if stage:
                    session_layer = stage.GetSessionLayer()
                    if session_layer:
                        _restore_prims(stage, session_layer, self._last_affected_saved)
            except Exception:
                pass

        for sub in self._subscriptions:
            if sub is not None and hasattr(sub, "release"):
                sub.release()
        self._subscriptions = []

        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None

        _log_info("[morph.select_near_hide] Extension shutdown")
