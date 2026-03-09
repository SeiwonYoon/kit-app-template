# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
가변 이동 애니메이션: prim을 x/y/z 축으로 원하는 만큼, 구간별 시간으로 이동시킵니다.

- 단일 구간: 예) 3초 동안 x축 5 이동
- 다중 구간(순차): 예) 2초 동안 x축 2 이동 → 3초 동안 y축 4 이동
- segments: [{"duration": 초, "delta": (dx, dy, dz)}, ...]
"""

from typing import List, Tuple, Dict, Any, Optional

import omni.kit.app
import omni.usd as ou
from pxr import Gf, UsdGeom


# 진행 중인 애니메이션: prim_path -> 상태
_animations: Dict[str, Dict[str, Any]] = {}
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
    """prim의 translate만 설정합니다. 기존 scale 등 다른 xformOp은 유지하려고 하며,
    Translate op이 없으면 AddTranslateOp으로 추가합니다."""
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


def run_prim_translate_animation(
    prim_path: str,
    segments: List[Dict[str, Any]],
    loop: bool = False,
) -> None:
    """
    해당 prim에 대해 이동 애니메이션을 시작합니다.

    Args:
        prim_path: USD prim 경로 (예: "/World/Cube_0")
        segments: 구간 리스트. 각 구간은 다음 키를 가집니다.
            - "duration": 구간 길이 (초)
            - "delta": (dx, dy, dz) 이동량 (단위)
        loop: True이면 모든 구간 완료 후 처음부터 반복합니다.

    예:
        # 3초 동안 x축으로 5만큼
        run_prim_translate_animation("/World/Cube_0", [{"duration": 3, "delta": (5, 0, 0)}])

        # 2초 x축 2, 이어서 3초 y축 4
        run_prim_translate_animation("/World/Cube_0", [
            {"duration": 2, "delta": (2, 0, 0)},
            {"duration": 3, "delta": (0, 4, 0)},
        ])

        # x, y, z 동시 이동
        run_prim_translate_animation("/World/Cube_0", [{"duration": 2, "delta": (1, 2, 3)}])
    """
    global _animations, _update_sub

    if not segments:
        return

    stage = ou.get_context().get_stage() if ou.get_context() else None
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return

    start_pos = _get_prim_local_translate(prim)

    # 정규화: delta를 (dx, dy, dz) 튜플/리스트로 통일
    normalized = []
    for seg in segments:
        d = seg.get("delta")
        if d is None:
            continue
        if isinstance(d, (list, tuple)) and len(d) >= 3:
            delta = (float(d[0]), float(d[1]), float(d[2]))
        else:
            continue
        duration = float(seg.get("duration", 0))
        if duration <= 0:
            continue
        normalized.append({"duration": duration, "delta": delta})

    if not normalized:
        return

    _animations[prim_path] = {
        "start_pos": Gf.Vec3f(start_pos[0], start_pos[1], start_pos[2]),
        "segments": normalized,
        "segment_index": 0,
        "elapsed_in_segment": 0.0,
        "loop": loop,
    }

    if _update_sub is None:
        stream = omni.kit.app.get_app().get_update_event_stream()
        _update_sub = stream.create_subscription_to_pop(_on_update, name="morph.measure_control_1.translate_animation")


def stop_prim_translate_animation(prim_path: str) -> bool:
    """해당 prim의 이동 애니메이션을 중단합니다. 애니메이션이 있었으면 True."""
    global _animations, _update_sub
    if prim_path in _animations:
        del _animations[prim_path]
        if not _animations and _update_sub is not None:
            _update_sub = None
        return True
    return False


def _on_update(e) -> None:
    dt = e.payload.get("dt", 0.0)
    if dt <= 0 or not _animations:
        return

    stage = ou.get_context().get_stage() if ou.get_context() else None
    if not stage:
        return

    to_remove = []
    for prim_path, state in list(_animations.items()):
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            to_remove.append(prim_path)
            continue

        segments = state["segments"]
        idx = state["segment_index"]
        elapsed = state["elapsed_in_segment"] + dt
        base_pos = state["start_pos"]

        # 완료된 이전 구간들의 delta 합산
        for i in range(idx):
            d = segments[i]["delta"]
            base_pos = Gf.Vec3f(base_pos[0] + d[0], base_pos[1] + d[1], base_pos[2] + d[2])

        duration = segments[idx]["duration"]
        delta = segments[idx]["delta"]

        if elapsed >= duration:
            # 이 구간 완료
            state["elapsed_in_segment"] = 0.0
            state["segment_index"] = idx + 1
            final_this_segment = Gf.Vec3f(
                base_pos[0] + delta[0],
                base_pos[1] + delta[1],
                base_pos[2] + delta[2],
            )
            if state["segment_index"] >= len(segments):
                _set_prim_translate(prim, final_this_segment)
                if state["loop"]:
                    state["segment_index"] = 0
                    state["start_pos"] = final_this_segment
                else:
                    to_remove.append(prim_path)
            else:
                # 다음 구간으로 넘어가며 남은 시간(remainder) 적용
                remainder = elapsed - duration
                state["elapsed_in_segment"] = remainder
                next_idx = state["segment_index"]
                next_d = segments[next_idx]["delta"]
                next_dur = segments[next_idx]["duration"]
                t = min(1.0, remainder / next_dur) if next_dur > 0 else 1.0
                current = Gf.Vec3f(
                    final_this_segment[0] + next_d[0] * t,
                    final_this_segment[1] + next_d[1] * t,
                    final_this_segment[2] + next_d[2] * t,
                )
                _set_prim_translate(prim, current)
            continue

        state["elapsed_in_segment"] = elapsed
        t = elapsed / duration
        current_pos = Gf.Vec3f(
            base_pos[0] + delta[0] * t,
            base_pos[1] + delta[1] * t,
            base_pos[2] + delta[2] * t,
        )
        _set_prim_translate(prim, current_pos)

    for prim_path in to_remove:
        _animations.pop(prim_path, None)

    global _update_sub
    if not _animations and _update_sub is not None:
        _update_sub = None
