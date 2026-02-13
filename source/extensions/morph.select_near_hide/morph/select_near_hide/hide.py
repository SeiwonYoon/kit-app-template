# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express agreement with NVIDIA CORPORATION is strictly prohibited.

"""Hide mode helpers for morph.select_near_hide."""

import math

from pxr import Sdf, Usd, UsdGeom, UsdShade


HIDE_RADIUS_METERS = 100.0


def get_sibling_paths(stage: Usd.Stage, selected_paths: list[str]) -> set[str]:
    """Return sibling prim paths for all selected prims."""
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


def get_sibling_paths_with_ancestor_fallback(stage: Usd.Stage, selected_paths: list[str]) -> set[str]:
    """
    Return sibling paths for selected prims.
    If direct siblings are empty, walk up ancestors and use the first branching level.
    """
    siblings = get_sibling_paths(stage, selected_paths)
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


def get_occlusion_candidates_up_to_common_parent(stage: Usd.Stage, selected_paths: list[str]) -> set[str]:
    """
    Collect sibling candidates while walking ancestors up to (but excluding) / and /World.
    """
    candidates = set()
    selected_set = set(selected_paths)

    for path_str in selected_paths:
        prim = stage.GetPrimAtPath(Sdf.Path(path_str))
        if not prim or not prim.IsValid():
            continue

        cur = prim
        while cur and cur.IsValid():
            parent = cur.GetParent()
            if not parent or not parent.IsValid():
                break
            parent_path_str = str(parent.GetPath())
            if parent_path_str in ("/", "/World"):
                break
            cur_path = str(cur.GetPath())
            for child in parent.GetChildren():
                child_path = str(child.GetPath())
                if child_path == cur_path:
                    continue
                if child_path in selected_set:
                    continue
                candidates.add(child_path)
            cur = parent

    return candidates


def restore_prims(stage: Usd.Stage, session_layer: Sdf.Layer, paths_to_restore: dict[str, dict]) -> None:
    """Restore saved visibility or material bindings."""
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


def apply_hide(stage: Usd.Stage, session_layer: Sdf.Layer, paths: set[str]) -> dict[str, dict]:
    """Apply hide to paths and return previous visibility values for restore."""
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


def _get_world_center(cache: UsdGeom.BBoxCache, prim: Usd.Prim):
    if not prim or not prim.IsValid():
        return None
    try:
        bbox = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        return bbox.GetCenter()
    except Exception:
        return None


def _get_world_position_from_xform(prim: Usd.Prim, xform_cache: UsdGeom.XformCache):
    if not prim or not prim.IsValid():
        return None
    try:
        cur = prim
        while cur and cur.IsValid():
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
    centers = []
    if not prim or not prim.IsValid():
        return centers

    c = _get_cached_center(cache, xform_cache, prim, center_cache)
    if c is not None:
        centers.append(c)

    if not prim.IsA(UsdGeom.Gprim):
        for p in Usd.PrimRange(prim):
            if not p or not p.IsValid() or not p.IsA(UsdGeom.Gprim):
                continue
            gc = _get_cached_center(cache, xform_cache, p, center_cache)
            if gc is not None:
                centers.append(gc)
    return centers


def _compute_world_aligned_box(cache: UsdGeom.BBoxCache, prim: Usd.Prim):
    if not prim or not prim.IsValid():
        return None
    try:
        box = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        mn = box.GetMin()
        mx = box.GetMax()
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
    if not prim or not prim.IsValid():
        return None
    p = str(prim.GetPath())
    if p in box_cache:
        return box_cache[p]
    box = _compute_world_aligned_box(cache, prim)
    box_cache[p] = box
    return box


def _sphere_intersects_box(center, radius2: float, box) -> bool:
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
    for sc in selected_centers:
        if _sphere_intersects_box(sc, radius2, box):
            return True
    return False


def filter_paths_by_distance(
    stage: Usd.Stage,
    candidate_paths: set[str],
    selected_paths: list[str],
    radius_meters: float,
) -> set[str]:
    """Filter candidate paths by distance to selected prim centers."""
    if not candidate_paths or not selected_paths:
        return set()
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=True,
    )
    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    center_cache: dict[str, object] = {}
    box_cache: dict[str, object] = {}
    selected_centers = []
    for sp in selected_paths:
        prim = stage.GetPrimAtPath(Sdf.Path(sp))
        if not prim or not prim.IsValid():
            continue
        centers = _collect_reference_centers(cache, xform_cache, prim, center_cache)
        selected_centers.extend(centers)
    if not selected_centers:
        return set()
    radius2 = radius_meters * radius_meters
    result: set[str] = set()
    for path_str in candidate_paths:
        prim = stage.GetPrimAtPath(Sdf.Path(path_str))
        if not prim or not prim.IsValid():
            continue
        box = _get_cached_world_aligned_box(cache, prim, box_cache)
        if box is None:
            continue
        if _box_intersects_any_selected_sphere(box, selected_centers, radius2):
            result.add(path_str)
    return result
