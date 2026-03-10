# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
곡선(경로) 애니메이션: prim이 지정한 경로(path)를 따라 이동하도록 합니다.

여러 상황에 맞는 곡선을 path 경로(포인트 리스트)로 정의할 수 있습니다.

사용 예 (주석으로 정리):

    # 예 1: 직선 경로 — A점에서 B점으로 3초 동안
    path_points = [(0, 0, 0), (5, 0, 0)]
    run_prim_curve_animation("/World/Cube_0", path_points, duration_sec=3.0)

    # 예 2: 꺾인 경로 — 여러 구간을 순차 이동 (삼각형/사각형 등)
    path_points = [(0, 0, 0), (3, 0, 0), (3, 2, 0), (0, 2, 0), (0, 0, 0)]
    run_prim_curve_animation("/World/Cube_0", path_points, duration_sec=5.0, loop=True)

    # 예 3: 3D 곡선 — 나선/계단 형태
    path_points = [
        (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
        (0, 1, 1), (1, 1, 1), (1, 0, 1), (0, 0, 1),
    ]
    run_prim_curve_animation("/World/Cube_0", path_points, duration_sec=8.0)

    # 예 4: 원호/원형 경로 — 수평 원 (x-y 평면)
    from .curve_animation import make_circle_path
    path_points = make_circle_path(center=(0, 0, 0), radius=5.0, num_points=32)
    run_prim_curve_animation("/World/Cube_0", path_points, duration_sec=4.0, loop=True)

    # 예 5: 타원/사다리꼴 등 — 원하는 꼭지점을 리스트로 정의
    path_points = [(0, 0, 0), (4, 0, 0), (3, 3, 0), (0, 3, 0)]
    run_prim_curve_animation("/World/Cube_0", path_points, duration_sec=6.0, loop=True)

    # 예 6: 베지어 스타일 스무스 곡선 (제어점 4개)
    from .curve_animation import make_bezier_path
    path_points = make_bezier_path(
        start=(0, 0, 0), control1=(2, 3, 0), control2=(4, 3, 0), end=(6, 0, 0),
        num_points=40
    )
    run_prim_curve_animation("/World/Cube_0", path_points, duration_sec=3.0)

    # 예 7: 포물선 경로 — 한 점에서 다른 점으로 포물선을 그리며 이동 (move_1 버튼 등)
    from .curve_animation import make_parabolic_path, run_prim_curve_animation
    start_pos = (1, 0, 2)   # 현재 위치
    end_pos = (6, 0, 2)     # 도착 위치
    path_points = make_parabolic_path(start=start_pos, end=end_pos, arc_height=3.0, num_points=24)
    run_prim_curve_animation("/World/Loaded_0/Part1", path_points, duration_sec=2.0)

    # 예 8: 여러 방식 조합 — 직선 후 포물선
    from .curve_animation import make_line_path, make_parabolic_path, run_prim_curve_animation
    p1 = make_line_path((0,0,0), (2,0,0), num_points=10)
    p2 = make_parabolic_path((2,0,0), (5,0,3), arc_height=2.0)
    run_prim_curve_animation("/World/Prim", p1 + p2[1:], duration_sec=4.0)  # p2[1:]로 중복 점 제거
"""

from typing import List, Dict, Any, Tuple, Optional, Union

import omni.kit.app
import omni.usd as ou
from pxr import Gf, UsdGeom


# 진행 중인 곡선 애니메이션: prim_path -> 상태
_curve_animations: Dict[str, Dict[str, Any]] = {}
_update_sub = None


def _get_prim_local_translate(prim) -> Gf.Vec3f:
    """prim의 현재 로컬 translate 값을 반환합니다."""
    if not prim or not prim.IsValid():
        return Gf.Vec3f(0, 0, 0)
    xform = UsdGeom.Xformable(prim)
    if not xform:
        return Gf.Vec3f(0, 0, 0)
    for op in xform.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            val = op.Get()
            if val is not None:
                return Gf.Vec3f(val[0], val[1], val[2])
            break
    return Gf.Vec3f(0, 0, 0)


def _set_prim_translate(prim, position: Gf.Vec3f) -> None:
    """prim의 translate만 설정합니다."""
    if not prim or not prim.IsValid():
        return
    xform = UsdGeom.Xformable(prim)
    if not xform:
        return
    translate_op = None
    for op in xform.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            translate_op = op
            break
    if translate_op is None:
        translate_op = xform.AddTranslateOp()
    translate_op.Set(Gf.Vec3f(position[0], position[1], position[2]))


def _to_vec3f(p: Union[Tuple[float, float, float], Gf.Vec3f, List[float]]) -> Gf.Vec3f:
    if isinstance(p, Gf.Vec3f):
        return p
    if isinstance(p, (list, tuple)) and len(p) >= 3:
        return Gf.Vec3f(float(p[0]), float(p[1]), float(p[2]))
    return Gf.Vec3f(0, 0, 0)


# -----------------------------------------------------------------------------
# 경로 생성 헬퍼 (여러 상황에 맞는 곡선 예시)
# -----------------------------------------------------------------------------

def make_circle_path(
    center: Union[Tuple[float, float, float], Gf.Vec3f],
    radius: float,
    num_points: int = 32,
    axis: str = "z",
) -> List[Tuple[float, float, float]]:
    """
    원형 경로 포인트 리스트를 만듭니다.
    axis="z" 이면 x-y 평면 원, axis="y" 이면 x-z 평면 원.

    예:
        path = make_circle_path(center=(0, 0, 0), radius=5.0, num_points=32)
    """
    cx, cy, cz = _to_vec3f(center)[0], _to_vec3f(center)[1], _to_vec3f(center)[2]
    points = []
    for i in range(num_points + 1):
        t = (float(i) / num_points) * 2.0 * 3.14159265359
        if axis == "z":
            points.append((cx + radius * __import__("math").cos(t), cy + radius * __import__("math").sin(t), cz))
        elif axis == "y":
            points.append((cx + radius * __import__("math").cos(t), cy, cz + radius * __import__("math").sin(t)))
        else:
            points.append((cx, cy + radius * __import__("math").cos(t), cz + radius * __import__("math").sin(t)))
    return points


def make_bezier_path(
    start: Union[Tuple[float, float, float], Gf.Vec3f],
    control1: Union[Tuple[float, float, float], Gf.Vec3f],
    control2: Union[Tuple[float, float, float], Gf.Vec3f],
    end: Union[Tuple[float, float, float], Gf.Vec3f],
    num_points: int = 40,
) -> List[Tuple[float, float, float]]:
    """
    3차 베지어 곡선 경로를 만듭니다. (start -> control1 -> control2 -> end)

    예:
        path = make_bezier_path(
            start=(0, 0, 0), control1=(2, 3, 0), control2=(4, 3, 0), end=(6, 0, 0),
            num_points=40
        )
    """
    s = _to_vec3f(start)
    c1 = _to_vec3f(control1)
    c2 = _to_vec3f(control2)
    e = _to_vec3f(end)
    points = []
    for i in range(num_points + 1):
        t = float(i) / num_points
        u = 1.0 - t
        # B(t) = (1-t)^3 P0 + 3(1-t)^2 t P1 + 3(1-t) t^2 P2 + t^3 P3
        x = u * u * u * s[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * e[0]
        y = u * u * u * s[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * e[1]
        z = u * u * u * s[2] + 3 * u * u * t * c1[2] + 3 * u * t * t * c2[2] + t * t * t * e[2]
        points.append((x, y, z))
    return points


def make_parabolic_path(
    start: Union[Tuple[float, float, float], Gf.Vec3f],
    end: Union[Tuple[float, float, float], Gf.Vec3f],
    arc_height: float = 2.0,
    num_points: int = 24,
    arc_axis: str = "y",
) -> List[Tuple[float, float, float]]:
    """
    포물선(arc) 경로: start에서 end로 포물선을 그리며 이동하는 점 리스트.
    arc_height: 꼭대기 높이 (arc_axis 방향).
    arc_axis: "y" 이면 Y축 방향으로 휘어 오름, "z" 이면 Z축.

    예 (move_1 등 — 곡선으로 포물선 이동):
        path = make_parabolic_path(start=(0,0,0), end=(5,0,0), arc_height=3.0, num_points=24)
        run_prim_curve_animation(prim_path, path, duration_sec=2.0)
    """
    s = _to_vec3f(start)
    e = _to_vec3f(end)
    mx = (s[0] + e[0]) * 0.5
    my = (s[1] + e[1]) * 0.5
    mz = (s[2] + e[2]) * 0.5
    if arc_axis == "y":
        peak = (mx, my + arc_height, mz)
    elif arc_axis == "z":
        peak = (mx, my, mz + arc_height)
    else:
        peak = (mx + arc_height, my, mz)
    points = []
    for i in range(num_points + 1):
        t = float(i) / num_points
        # 2차 베지어: (1-t)^2*start + 2*(1-t)*t*peak + t^2*end
        u = 1.0 - t
        x = u * u * s[0] + 2 * u * t * peak[0] + t * t * e[0]
        y = u * u * s[1] + 2 * u * t * peak[1] + t * t * e[1]
        z = u * u * s[2] + 2 * u * t * peak[2] + t * t * e[2]
        points.append((x, y, z))
    return points


def make_line_path(
    start: Union[Tuple[float, float, float], Gf.Vec3f],
    end: Union[Tuple[float, float, float], Gf.Vec3f],
    num_points: int = 2,
) -> List[Tuple[float, float, float]]:
    """직선 경로. num_points=2 이면 [start, end] 만 (기본)."""
    s = _to_vec3f(start)
    e = _to_vec3f(end)
    if num_points <= 2:
        return [(s[0], s[1], s[2]), (e[0], e[1], e[2])]
    points = []
    for i in range(num_points):
        t = float(i) / (num_points - 1)
        points.append((
            s[0] + (e[0] - s[0]) * t,
            s[1] + (e[1] - s[1]) * t,
            s[2] + (e[2] - s[2]) * t,
        ))
    return points


# -----------------------------------------------------------------------------
# 곡선 애니메이션 실행/중지
# -----------------------------------------------------------------------------

def run_prim_curve_animation(
    prim_path: str,
    path_points: List[Union[Tuple[float, float, float], Gf.Vec3f, List[float]]],
    duration_sec: float = 3.0,
    loop: bool = False,
) -> None:
    """
    해당 prim이 주어진 경로(path)를 따라 이동하는 곡선 애니메이션을 시작합니다.

    Args:
        prim_path: USD prim 경로 (예: "/World/Cube_0")
        path_points: 경로를 이루는 3D 점 리스트. 각 원소는 (x, y, z) 또는 Gf.Vec3f.
                     구간별 선형 보간(linear)으로 이동합니다.
        duration_sec: 전체 경로를 도는 데 걸리는 시간(초).
        loop: True이면 경로 끝에 도달 후 처음부터 반복합니다.

    예:
        # 직선
        run_prim_curve_animation("/World/Cube_0", [(0,0,0), (5,0,0)], duration_sec=3.0)

        # 사각형 경로
        run_prim_curve_animation("/World/Cube_0", [
            (0,0,0), (3,0,0), (3,2,0), (0,2,0), (0,0,0)
        ], duration_sec=5.0, loop=True)

        # 원형 (make_circle_path 사용)
        path = make_circle_path((0,0,0), radius=5.0)
        run_prim_curve_animation("/World/Cube_0", path, duration_sec=4.0, loop=True)
    """
    global _curve_animations, _update_sub

    if not path_points or len(path_points) < 2:
        return
    if duration_sec <= 0:
        return

    stage = ou.get_context().get_stage() if ou.get_context() else None
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return

    points = [_to_vec3f(p) for p in path_points]
    # 구간별 길이로 시간 배분 (등속에 가깝게)
    lengths = []
    total = 0.0
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        L = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2) ** 0.5
        lengths.append(L)
        total += L
    if total <= 0:
        segment_durations = [duration_sec / max(1, len(points) - 1)] * (len(points) - 1)
    else:
        segment_durations = [(d / total) * duration_sec for d in lengths]

    start_pos = _get_prim_local_translate(prim)
    # 경로가 월드/로컬 기준인지: 사용자가 path_points로 절대 위치를 주는 경우와
    # 현재 위치를 기준으로 상대 이동을 원하는 경우가 있을 수 있음.
    # 여기서는 path_points를 "절대 위치"로 해석합니다. 첫 점이 현재 위치와 다를 수 있음.
    # 옵션으로 "relative"를 두려면 run_prim_curve_animation(..., path_mode="absolute") 등으로 확장 가능.

    _curve_animations[prim_path] = {
        "path_points": points,
        "segment_durations": segment_durations,
        "duration_sec": duration_sec,
        "segment_index": 0,
        "elapsed_in_segment": 0.0,
        "loop": loop,
    }

    if _update_sub is None:
        stream = omni.kit.app.get_app().get_update_event_stream()
        _update_sub = stream.create_subscription_to_pop(_on_curve_update, name="morph.measure_control_1.curve_animation")


def stop_prim_curve_animation(prim_path: str) -> bool:
    """해당 prim의 곡선 애니메이션을 중단합니다."""
    global _curve_animations, _update_sub
    if prim_path in _curve_animations:
        del _curve_animations[prim_path]
        if not _curve_animations and _update_sub is not None:
            _update_sub = None
        return True
    return False


def _on_curve_update(e) -> None:
    dt = e.payload.get("dt", 0.0)
    if dt <= 0 or not _curve_animations:
        return

    stage = ou.get_context().get_stage() if ou.get_context() else None
    if not stage:
        return

    to_remove = []
    for prim_path, state in list(_curve_animations.items()):
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            to_remove.append(prim_path)
            continue

        points = state["path_points"]
        seg_durs = state["segment_durations"]
        idx = state["segment_index"]
        elapsed = state["elapsed_in_segment"] + dt

        if idx >= len(seg_durs):
            if state["loop"]:
                state["segment_index"] = 0
                state["elapsed_in_segment"] = 0.0
                idx = 0
                elapsed = dt
            else:
                _set_prim_translate(prim, points[-1])
                to_remove.append(prim_path)
                continue

        duration = seg_durs[idx]
        p0, p1 = points[idx], points[idx + 1]

        if elapsed >= duration:
            state["elapsed_in_segment"] = elapsed - duration
            state["segment_index"] = idx + 1
            _set_prim_translate(prim, p1)
            if state["segment_index"] >= len(seg_durs):
                if state["loop"]:
                    state["segment_index"] = 0
                    state["elapsed_in_segment"] = 0.0
                else:
                    to_remove.append(prim_path)
            continue

        state["elapsed_in_segment"] = elapsed
        t = elapsed / duration if duration > 0 else 1.0
        pos = Gf.Vec3f(
            p0[0] + (p1[0] - p0[0]) * t,
            p0[1] + (p1[1] - p0[1]) * t,
            p0[2] + (p1[2] - p0[2]) * t,
        )
        _set_prim_translate(prim, pos)

    for prim_path in to_remove:
        _curve_animations.pop(prim_path, None)

    global _update_sub
    if not _curve_animations and _update_sub is not None:
        _update_sub = None
