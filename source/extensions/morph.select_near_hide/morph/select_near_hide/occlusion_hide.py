# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express agreement with NVIDIA CORPORATION is strictly prohibited.

"""
카메라와 선택한 오브젝트 사이에 있는 prim을 숨기는 기능 모듈.

Sibling 기반 + ray-AABB 검사: 선택 prim의 sibling만 대상으로,
카메라~선택 오브젝트 사이에 ray가 교차하는 것만 수집합니다.
전체 Stage 순회 없이 빠르게 동작합니다.
"""

import math

from pxr import Gf, Sdf, Usd, UsdGeom

try:
    from omni.kit.viewport.utility import get_active_viewport_camera_string
except ImportError:
    get_active_viewport_camera_string = None


def get_camera_world_position(stage: Usd.Stage, xform_cache: UsdGeom.XformCache | None = None):
    """
    현재 활성 뷰포트 카메라의 월드 좌표를 반환합니다.
    실패 시 None. xform_cache가 제공되면 재사용하여 성능 향상.
    """
    if not stage or not get_active_viewport_camera_string:
        return None
    try:
        cam_path = get_active_viewport_camera_string()
        if not cam_path:
            return None
        cam_prim = stage.GetPrimAtPath(cam_path)
        if not cam_prim or not cam_prim.IsValid():
            return None
        xform = UsdGeom.Xformable(cam_prim)
        if not xform:
            return None
        if xform_cache is None:
            xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
        m = xform_cache.GetLocalToWorldTransform(cam_prim)
        return m.ExtractTranslation()
    except Exception:
        return None


def _get_selected_world_center(
    stage: Usd.Stage,
    selected_paths: list[str],
    cache: UsdGeom.BBoxCache,
    xform_cache: UsdGeom.XformCache,
) -> Gf.Vec3d | None:
    """선택된 prim들의 바운드 중심(월드)을 반환합니다."""
    if not selected_paths:
        return None
    centers = []
    for path_str in selected_paths:
        prim = stage.GetPrimAtPath(Sdf.Path(path_str))
        if not prim or not prim.IsValid():
            continue
        try:
            bbox = cache.ComputeWorldBound(prim).ComputeAlignedBox()
            center = bbox.GetCenter()
            if all(math.isfinite(center[i]) for i in range(3)):
                centers.append(center)
        except Exception:
            try:
                xform = UsdGeom.Xformable(prim)
                if xform:
                    m = xform_cache.GetLocalToWorldTransform(prim)
                    center = m.ExtractTranslation()
                    if all(math.isfinite(center[i]) for i in range(3)):
                        centers.append(center)
            except Exception:
                pass
    if not centers:
        return None
    acc = Gf.Vec3d(0, 0, 0)
    for c in centers:
        acc += c
    acc /= len(centers)
    return acc


def _ray_aabb_intersects_before_distance(
    ray_origin: Gf.Vec3d,
    ray_dir: Gf.Vec3d,
    ray_length: float,
    box,
) -> bool:
    """레이가 AABB와 ray_length 이내에서 교차하는지 검사합니다."""
    intersects, _ = _ray_aabb_intersect_info(ray_origin, ray_dir, ray_length, box)
    return intersects


def _ray_aabb_intersect_info(ray_origin: Gf.Vec3d, ray_dir: Gf.Vec3d, ray_length: float, box) -> tuple[bool, float | None]:
    """ray-AABB 교차 검사 결과와 hit_t를 반환. (intersects, hit_t)"""
    mn = box.GetMin()
    mx = box.GetMax()
    eps = 1e-9
    if ray_length <= eps:
        return False, None
    if not all(math.isfinite(ray_origin[i]) and math.isfinite(ray_dir[i]) for i in range(3)):
        return False, None

    # Robust slab intersection on finite segment [0, ray_length].
    # Avoid inf/NaN artifacts for near-parallel ray components.
    t_enter = 0.0
    t_exit = ray_length

    for i in range(3):
        min_i = mn[i]
        max_i = mx[i]
        o = ray_origin[i]
        d = ray_dir[i]
        if not (math.isfinite(min_i) and math.isfinite(max_i) and min_i <= max_i):
            return False, None

        if abs(d) <= eps:
            if o < min_i or o > max_i:
                return False, None
            continue

        inv_d = 1.0 / d
        t0 = (min_i - o) * inv_d
        t1 = (max_i - o) * inv_d
        if t0 > t1:
            t0, t1 = t1, t0

        if t0 > t_enter:
            t_enter = t0
        if t1 < t_exit:
            t_exit = t1
        if t_enter > t_exit:
            return False, None

    if t_exit < 0.0:
        return False, None

    hit_t = max(t_enter, 0.0)
    return (hit_t <= ray_length + eps), hit_t


def collect_occlusion_prim_paths_sibling(
    stage: Usd.Stage,
    selected_paths: list[str],
    sibling_paths: set[str],
) -> set[str]:
    """
    Sibling 기반 occlusion 수집: sibling만 대상으로 ray-AABB 교차 검사.

    - 카메라 위치: 활성 뷰포트 카메라
    - 선택 중심: 선택 prim들의 바운드 중심 (ray 방향)
    - 대상: sibling_paths (전체 Stage 순회 없음)
    - 조건: 레이가 sibling prim AABB와 교차하고, 교차점이 선택 중심보다 가까움
    """
    result: set[str] = set()
    if not stage or not selected_paths or not sibling_paths:
        return result

    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=True,
    )
    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())

    camera_pos = get_camera_world_position(stage, xform_cache)
    if camera_pos is None:
        return result
    if not all(math.isfinite(camera_pos[i]) for i in range(3)):
        return result

    selected_center = _get_selected_world_center(stage, selected_paths, cache, xform_cache)
    if selected_center is None:
        return result

    diff = selected_center - camera_pos
    ray_length = math.sqrt(diff[0] ** 2 + diff[1] ** 2 + diff[2] ** 2)
    if ray_length < 1e-6:
        return result

    ray_dir = Gf.Vec3d(
        diff[0] / ray_length,
        diff[1] / ray_length,
        diff[2] / ray_length,
    )

    for path_str in sibling_paths:
        prim = stage.GetPrimAtPath(Sdf.Path(path_str))
        if not prim or not prim.IsValid():
            continue
        imageable = UsdGeom.Imageable(prim)
        if not imageable:
            continue
        try:
            bbox = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        except Exception:
            continue
        mn = bbox.GetMin()
        mx = bbox.GetMax()
        if not all(
            math.isfinite(mn[i]) and math.isfinite(mx[i]) and mn[i] <= mx[i]
            for i in range(3)
        ):
            continue
        if _ray_aabb_intersects_before_distance(camera_pos, ray_dir, ray_length, bbox):
            result.add(path_str)

    return result
