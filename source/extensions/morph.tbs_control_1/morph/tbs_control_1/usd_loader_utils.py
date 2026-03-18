# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
usd_loader_utils.py — USD 로드 관련 유틸 (경로 검증, resource 폴더, 지원 확장자).

기능:
- get_supported_stage_extensions(): open_stage()로 열 수 있는 파일 확장자 집합.
- path_has_supported_stage_extension(path): URL/경로가 지원 확장자로 끝나는지 검사.
- get_resource_folder_path(): 프로젝트 최상단 resource 폴더 Path (carb tokens / __file__ / cwd).
- get_resource_usd_list(): resource 폴더 내 로드 가능한 USD 파일 목록 [(이름, 절대경로), ...].

사용처: load_window에서 USD Load 창 경로 입력/콤보 및 로드 전 검증에 사용.
"""

from pathlib import Path
from typing import List, Optional, Set

from pxr import Sdf

_SUPPORTED_STAGE_EXTS: Optional[Set[str]] = None


def get_supported_stage_extensions() -> Set[str]:
    """현재 Kit 환경에서 open_stage()로 열 수 있는 확장자 집합. 실패 시 보수적 fallback."""
    global _SUPPORTED_STAGE_EXTS
    if _SUPPORTED_STAGE_EXTS is not None:
        return _SUPPORTED_STAGE_EXTS
    exts = set()
    try:
        for fmt in Sdf.FileFormat.FindAllFileFormats():
            for e in fmt.GetFileExtensions() or []:
                if not e:
                    continue
                exts.add("." + str(e).lower())
    except Exception:
        exts = set()
    if not exts:
        exts = {".usd", ".usda", ".usdc", ".usdz", ".sdf", ".sda", ".sdc"}
    _SUPPORTED_STAGE_EXTS = exts
    return exts


def path_has_supported_stage_extension(path: str) -> bool:
    """URL query/fragment 제거 후 확장자 체크."""
    if not path:
        return False
    p = path.strip().lower()
    if not p:
        return False
    p = p.split("#", 1)[0].split("?", 1)[0]
    return any(p.endswith(ext) for ext in get_supported_stage_extensions())


def get_resource_folder_path() -> Optional[Path]:
    """launch 실행 최상단 경로(${root}) 아래의 resource 폴더."""
    try:
        import carb
        tokens = carb.tokens.get_tokens_interface()
        if tokens:
            root = tokens.resolve("${root}")
            if root:
                resource_dir = Path(root) / "resource"
                if resource_dir.is_dir():
                    return resource_dir
    except Exception:
        pass
    try:
        current = Path(__file__).resolve()
        for _ in range(10):
            current = current.parent
            if not current:
                break
            resource_dir = current / "resource"
            if resource_dir.is_dir():
                return resource_dir
    except Exception:
        pass
    try:
        cwd_resource = Path.cwd() / "resource"
        if cwd_resource.is_dir():
            return cwd_resource
    except Exception:
        pass
    return None


def get_resource_usd_list() -> List[tuple]:
    """resource 폴더 내 '스테이지로 직접 로드 가능한' 확장자 파일 목록. [(이름, 절대경로), ...]"""
    resource_dir = get_resource_folder_path()
    if not resource_dir:
        return []
    exts = get_supported_stage_extensions()
    result: List[tuple] = []
    try:
        for p in sorted(resource_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in exts:
                result.append((p.name, str(p)))
    except Exception:
        pass
    return result
