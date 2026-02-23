# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express agreement with NVIDIA CORPORATION is strictly prohibited.

"""Semi-transparent mode helpers for morph.select_near_hide."""

import time

import carb
from pxr import Sdf, Usd, UsdGeom, UsdShade

from .hide import (
    _box_intersects_any_selected_sphere,
    _collect_reference_centers,
    _get_cached_world_aligned_box,
    restore_prims,
)


TRANSPARENT_RADIUS_METERS = 50.0
GHOST_MATERIAL_PATHS = ["/Looks/GhostPBR", "/World/Looks/GhostPBR"]
ALREADY_GHOST_SENTINEL = "__already_ghost__"


def _log_info(message: str):
    carb.log_info(message)
    print(message, flush=True)


def _log_warn(message: str):
    carb.log_warn(message)
    print(message, flush=True)


def _get_ghost_material(stage: Usd.Stage):
    for p in GHOST_MATERIAL_PATHS:
        prim = stage.GetPrimAtPath(p)
        if prim and prim.IsValid() and prim.IsA(UsdShade.Material):
            _log_info(f"[morph.select_near_hide] GhostPBR found at: {p}")
            return UsdShade.Material(prim)
    _log_warn("[morph.select_near_hide] GhostPBR not found. Tried: " + ", ".join(GHOST_MATERIAL_PATHS))
    return None


def _is_transparent_target_prim(prim: Usd.Prim, mesh_subset_cache: dict[str, bool]) -> bool:
    if not prim or not prim.IsValid():
        return False
    if prim.IsA(UsdGeom.Subset):
        parent = prim.GetParent()
        if not parent or not parent.IsValid() or not parent.IsA(UsdGeom.Gprim):
            return False
        return True
    if prim.IsA(UsdGeom.Gprim):
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


def _filter_roots_by_spatial_index(
    stage: Usd.Stage,
    cache: UsdGeom.BBoxCache,
    sibling_paths: set[str],
    selected_centers: list,
    radius2: float,
    root_bbox_cache: dict[str, object],
    box_cache: dict[str, object],
):
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
            box_cache[root_path] = box

        if box is None:
            filtered_roots.append(root.GetPath())
            continue

        if _box_intersects_any_selected_sphere(box, selected_centers, radius2):
            filtered_roots.append(root.GetPath())
        else:
            skipped += 1

    return filtered_roots, skipped, cache_hit, cache_miss


def apply_transparent(
    stage: Usd.Stage,
    session_layer: Sdf.Layer,
    selected_paths: list[str],
    sibling_paths: set[str],
    bound_material_cache: dict[str, str],
    root_bbox_cache: dict[str, object],
    previous_saved: dict[str, dict] | None = None,
):
    """
    Apply GhostPBR material in a radius around selected prims, for sibling roots only.
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

    candidate_count = 0
    root_count = 0
    root_skipped_by_index = 0
    root_cache_hit = 0
    root_cache_miss = 0
    pruned_subtrees = 0
    target_prim_paths: list[str] = []
    target_path_set: set[str] = set()
    explore_t0 = time.perf_counter()

    filtered_roots, root_skipped_by_index, root_cache_hit, root_cache_miss = _filter_roots_by_spatial_index(
        stage, cache, sibling_paths, selected_centers, radius2, root_bbox_cache, box_cache
    )

    for root_path in filtered_roots:
        root = stage.GetPrimAtPath(root_path)
        if not root or not root.IsValid():
            continue
        root_count += 1

        it = iter(Usd.PrimRange(root))
        for p in it:
            p_box = _get_cached_world_aligned_box(cache, p, box_cache)
            if p_box is not None and not _box_intersects_any_selected_sphere(p_box, selected_centers, radius2):
                it.PruneChildren()
                pruned_subtrees += 1
                continue

            if not _is_transparent_target_prim(p, mesh_subset_cache):
                continue
            candidate_count += 1

            target_box = p_box
            if target_box is None:
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

    prev_path_to_saved: dict[str, dict] = {}
    for key, value in previous_saved.items():
        if key.startswith("__prim:"):
            prev_path_to_saved[key[len("__prim:") :]] = value

    current_target_set = set(target_prim_paths)
    prev_target_set = set(prev_path_to_saved.keys())
    to_restore_paths = prev_target_set - current_target_set
    to_add_paths = current_target_set - prev_target_set
    retained_paths = current_target_set & prev_target_set

    for p_path in retained_paths:
        saved["__prim:" + p_path] = prev_path_to_saved[p_path]

    restore_delta_t0 = time.perf_counter()
    if to_restore_paths:
        restore_subset = {"__prim:" + p: prev_path_to_saved[p] for p in to_restore_paths}
        restore_prims(stage, session_layer, restore_subset)
    restore_delta_ms = (time.perf_counter() - restore_delta_t0) * 1000.0

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
                        continue
                    if cached_mpath:
                        saved[key] = {"had_material": True, "material_path": cached_mpath}
                    else:
                        saved[key] = {"had_material": False}
                else:
                    binding_api = UsdShade.MaterialBindingAPI(prim)
                    original_material, _ = binding_api.ComputeBoundMaterial()
                    if original_material:
                        mpath = str(original_material.GetPath())
                        if mpath == str(ghost_material.GetPath()):
                            bound_material_cache[p_path] = ALREADY_GHOST_SENTINEL
                            continue
                        saved[key] = {"had_material": True, "material_path": mpath}
                        bound_material_cache[p_path] = mpath
                    else:
                        saved[key] = {"had_material": False}
                        bound_material_cache[p_path] = ""

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
