# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
prim_utils.py — 스테이지/prim 조회 및 변환 유틸 (경로 수집, 이름 검색, 위치 get/set, 프레임)

【역할】
- get_stage(): 현재 Usd.Stage 반환.
- is_utf8_safe(s): UTF-8 인코딩 가능 여부.
- collect_prim_paths_safe(stage): 스테이지 전체에서 Xform/Gprim/Scope prim 경로 수집 (UTF-8 안전).
- find_prim_path_by_name(stage, name): 이름/경로 일치 첫 번째 경로.
- find_all_prim_paths_by_name(stage, name): 이름 일치 모든 경로.
- get_prim_local_translate(prim): prim의 로컬 translate (첫 번째 translate op).
- set_prim_translate_only(prim, position): 첫 translate op에만 설정 (CommonAPI 미사용).
- frame_prim_in_viewport(prim_path): 뷰포트에서 해당 prim으로 프레임.

【수정 가이드】
- 목록에 포함할 prim 타입: collect_prim_paths_safe 의 IsA 조건
- 이름 검색 규칙: find_prim_path_by_name / find_all_prim_paths_by_name
- translate 적용 방식: set_prim_translate_only (TBS_OFFSET op 이름은 xform_utils와 연동)

사용처: control_window, load_window

【유지보수 시나리오】
1) prim 목록에 특정 타입이 빠지는 경우
   - collect_prim_paths_safe의 IsA 필터 수정
2) 이름 검색 중복/오탐 이슈
   - find_all_prim_paths_by_name 비교 규칙(name/path exact match) 조정
3) 위치 이동이 기존 애니메이션과 충돌
   - set_prim_translate_only 호출 경로 확인
   - sequence_engine/translate_animation의 TBS_OFFSET 정책과 충돌 여부 점검
"""

from typing import List, Optional

import omni.usd as ou
from pxr import Gf, Usd, UsdGeom

from .prim_info import safe_str
from .xform_utils import ensure_scale_xform_ops_first


def get_stage():
    """omni.usd 컨텍스트에서 현재 활성 Usd.Stage 반환(없으면 None)."""
    ctx = ou.get_context()
    return ctx.get_stage() if ctx else None


def is_utf8_safe(s: str) -> bool:
    """문자열이 UTF-8로 인코딩 가능한지 검사(경로 수집 시 잘못된 prim 제외)."""
    if not s:
        return True
    try:
        s.encode("utf-8")
        return True
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False


def collect_prim_paths_safe(stage: Usd.Stage) -> List[str]:
    """open_stage 후 스테이지 전체에서 prim 경로 수집. UTF-8 안전 경로만."""
    paths: List[str] = []

    def visit(prim: Usd.Prim) -> None:
        """스테이지 트리를 DFS로 순회하며 Xform/Gprim/Scope 경로를 paths에 누적."""
        try:
            path = str(prim.GetPath())
        except Exception:
            return
        if path == "/":
            try:
                for ch in prim.GetChildren():
                    visit(ch)
            except Exception:
                pass
            return
        try:
            if not is_utf8_safe(path):
                for ch in prim.GetChildren():
                    visit(ch)
                return
            if prim.IsA(UsdGeom.Xform) or prim.IsA(UsdGeom.Gprim) or prim.GetTypeName() == "Scope":
                paths.append(path)
            for ch in prim.GetChildren():
                visit(ch)
        except Exception:
            pass

    try:
        root = stage.GetPseudoRoot()
        if root:
            visit(root)
    except Exception:
        pass
    return paths


def find_prim_path_by_name(stage: Usd.Stage, name: str) -> Optional[str]:
    """이름 또는 절대경로로 prim을 찾아 첫 경로만 반환."""
    paths = find_all_prim_paths_by_name(stage, name)
    return paths[0] if paths else None


def find_all_prim_paths_by_name(stage: Usd.Stage, name: str) -> List[str]:
    """해당 이름(GetName()) 또는 경로와 일치하는 모든 prim 경로."""
    result: List[str] = []
    name_s = name.strip()
    if not name_s:
        return result
    try:
        if name_s.startswith("/"):
            prim = stage.GetPrimAtPath(name_s)
            if prim and prim.IsValid():
                result.append(name_s)
                return result
        else:
            prim = stage.GetPrimAtPath("/" + name_s)
            if prim and prim.IsValid():
                result.append("/" + name_s)
                return result
    except Exception:
        pass

    def visit(prim: Usd.Prim) -> None:
        """이름이 name_s와 같은 prim 경로를 result에 추가."""
        if prim.GetPath().pathString == "/":
            for ch in prim.GetChildren():
                visit(ch)
            return
        try:
            if safe_str(prim.GetName()) == name_s:
                result.append(str(prim.GetPath()))
        except Exception:
            pass
        for ch in prim.GetChildren():
            visit(ch)

    try:
        root = stage.GetPseudoRoot()
        if root:
            visit(root)
    except Exception:
        pass
    return result


def get_prim_local_translate(prim: Usd.Prim) -> Gf.Vec3f:
    """xform 스택에서 첫 번째 Translate op 값을 읽어 반환(없으면 0). TBS_OFFSET 전용 아님."""
    if not prim or not prim.IsValid():
        return Gf.Vec3f(0, 0, 0)
    xform = UsdGeom.Xformable(prim)
    if not xform:
        return Gf.Vec3f(0, 0, 0)
    for op in xform.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            val = op.Get()
            return Gf.Vec3f(val[0], val[1], val[2]) if val is not None else Gf.Vec3f(0, 0, 0)
    return Gf.Vec3f(0, 0, 0)


def set_prim_translate_only(prim: Usd.Prim, position: Gf.Vec3f) -> None:
    """첫 번째 translate op에 값 설정. XformCommonAPI 미사용(에셋 T,R,S 순서에서 경고 방지)."""
    if not prim or not prim.IsValid():
        return
    ensure_scale_xform_ops_first(prim)
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
    try:
        translate_op.Set(Gf.Vec3f(float(position[0]), float(position[1]), float(position[2])))
    except Exception:
        pass


def frame_prim_in_viewport(prim_path: str) -> None:
    """활성 뷰포트 카메라를 해당 prim에 맞춤(비동기)."""
    try:
        from omni.kit.viewport.utility import frame_viewport_prims, get_active_viewport
    except ImportError:
        return
    import asyncio

    async def _do_frame():
        """다음 업데이트 후 활성 뷰포트에서 prim에 프레임 맞춤."""
        await omni.kit.app.get_app().next_update_async()
        viewport_api = get_active_viewport()
        if not viewport_api:
            try:
                from omni.kit.viewport.utility import get_active_viewport_window
                win = get_active_viewport_window()
                viewport_api = win.viewport_api if win else None
            except Exception:
                pass
        if viewport_api:
            frame_viewport_prims(viewport_api, prims=[prim_path])
    asyncio.ensure_future(_do_frame())
