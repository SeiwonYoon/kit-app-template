# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
usd_loader_utils.py — USD 로드 관련 유틸 (경로 검증, resource 폴더, 지원 확장자)

【역할】
- 지원 스테이지 확장자 집합, 경로 검증, resource 폴더 탐색, USD 파일 목록 수집.

【수정 가이드】
- 열 수 있는 확장자 추가/변경: get_supported_stage_extensions(), _SUPPORTED_STAGE_EXTS
- resource 폴더 위치가 바뀌면: get_resource_folder_path()
- 콤보에 표시할 파일 필터: get_resource_usd_list() 내부

사용처: load_window.py

【유지보수 시나리오】
1) 특정 USD/USDA/USDC가 로드 버튼에서 거부될 때
   - path_has_supported_stage_extension 확장자 체크 로직 확인
2) 배포 환경별 resource 경로가 다를 때
   - get_resource_folder_path 토큰/상대경로 fallback 순서 조정
3) load_window 콤보 목록 정책 변경
   - get_resource_usd_list 정렬/필터 기준 수정
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
