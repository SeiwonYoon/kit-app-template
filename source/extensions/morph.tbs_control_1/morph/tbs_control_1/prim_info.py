# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
prim_info.py — USD prim 표시용 문자열·웹뷰 패널 레이아웃 상수

【역할】
- prim 이름/속성/줄 단위 텍스트, 월드 중심 좌표 (오버레이·목록 표시).

【수정 가이드】
- 패널 글자 크기·줄 길이: CHAR_WIDTH, LINE_HEIGHT, CHARS_PER_LINE 등 상수
- 표시할 속성 종류: get_prim_display_lines 내부

사용처: selection_overlay, viewport_overlay, control_window(간접)

【유지보수 시나리오】
1) 패널에 lot/port 상태를 추가로 보여주고 싶을 때
   - get_prim_display_lines에서 CustomData/Attribute 키를 추가
2) 텍스트가 너무 길어 패널이 깨질 때
   - CHARS_PER_LINE, MAX_ATTR_VALUE_LEN 조정
   - viewport_overlay MAX_* 상수와 함께 튜닝
3) 한글/비UTF-8 문자열 깨짐
   - safe_str() 경로 유지(강제 UTF-8 replace)
"""

from typing import List, Any, Optional, Tuple

from pxr import Usd, UsdGeom

MAX_ATTR_VALUE_LEN = 120
CHARS_PER_LINE = 50

# 3D 패널 레이아웃용 (show_info와 동일)
CHAR_WIDTH = 9
LINE_HEIGHT = 18
PADDING_H = 16
PADDING_V = 12


# 제목/이름 표시 시 최대 글자 수 (드롭다운 제목용)
MAX_DISPLAY_NAME_LEN = 48


def get_prim_display_name(prim: "Usd.Prim", fallback_index: int) -> str:
    """
    UI 제목용: prim의 displayName/title/name 중 하나를 안전한 문자열로 반환.
    실패 시 'Prim {fallback_index}' 반환. UTF-8 안전(safe_str) 적용.
    """
    if not prim or not prim.IsValid():
        return f"Prim {fallback_index}"
    try:
        for key in ("displayName", "title", "name"):
            if prim.HasMetadata(key):
                val = prim.GetMetadata(key)
                if val is not None:
                    s = safe_str(val)
                    if s and s != "None":
                        if len(s) > MAX_DISPLAY_NAME_LEN:
                            s = s[: MAX_DISPLAY_NAME_LEN - 3] + "..."
                        return s
        n = prim.GetName()
        if n is not None:
            s = safe_str(n)
            if s and len(s) > 0:
                if len(s) > MAX_DISPLAY_NAME_LEN:
                    s = s[: MAX_DISPLAY_NAME_LEN - 3] + "..."
                return s
    except Exception:
        pass
    return f"Prim {fallback_index}"


def safe_str(val: Any) -> str:
    """
    UI/UTF-8 안전 문자열로 변환. bytes 또는 비UTF-8 문자가 있으면 replace로 복구.
    UnicodeDecodeError 방지 (예: CP949 경로·속성값).
    """
    if val is None:
        return "None"
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    s = str(val)
    try:
        s.encode("utf-8")
        return s
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s.encode("utf-8", errors="replace").decode("utf-8")


def _wrap_line(line: str, max_chars: int = CHARS_PER_LINE) -> List[str]:
    if not line or len(line) <= max_chars:
        return [line] if line else []
    result = []
    rest = line
    while rest:
        if len(rest) <= max_chars:
            result.append(rest)
            break
        split_at = min(max_chars, rest.rfind(" ") + 1 or max_chars)
        if split_at <= 0:
            split_at = max_chars
        result.append(rest[:split_at].strip())
        rest = rest[split_at:].strip()
    return result


def _format_value(val: Any) -> str:
    s = safe_str(val)
    if len(s) > MAX_ATTR_VALUE_LEN:
        s = s[: MAX_ATTR_VALUE_LEN - 3] + "..."
    return s.replace("\n", " ").replace("\r", " ")


def get_prim_display_lines(prim: Usd.Prim) -> List[str]:
    """prim의 이름·경로·타입·메타데이터·속성을 표시용 줄 리스트로 반환 (UTF-8 안전)."""
    raw: List[str] = []
    if not prim or not prim.IsValid():
        return []
    path = safe_str(prim.GetPath())
    name = safe_str(prim.GetName())
    raw.append(f"Name: {name}")
    raw.append(f"Path: {path}")
    raw.append("")
    raw.append(f"Type: {safe_str(prim.GetTypeName())}")
    raw.append("")
    try:
        meta = prim.GetAllMetadata()
        if meta:
            for key in sorted(meta.keys(), key=safe_str):
                try:
                    val = prim.GetMetadata(key)
                    raw.append(f"  {safe_str(key)}: {_format_value(val)}")
                except Exception:
                    raw.append(f"  {key}: <error>")
            raw.append("")
    except Exception:
        pass
    attrs = list(prim.GetAttributes())
    if attrs:
        raw.append("--- Attributes ---")
        for attr in sorted(attrs, key=lambda a: safe_str(a.GetName())):
            name_attr = safe_str(attr.GetName())
            try:
                type_name = attr.GetTypeName().type.typeName if attr.GetTypeName() else "?"
                type_name = safe_str(type_name)
                val = attr.Get()
                raw.append(f"  {name_attr} ({type_name})")
                raw.append(f"    = {_format_value(val)}")
            except Exception:
                raw.append(f"  {name_attr}: <error>")
        raw.append("")
    out: List[str] = []
    for line in raw:
        if not line:
            out.append("")
            continue
        for wrapped in _wrap_line(line):
            out.append(wrapped)
    return out


def get_prim_world_center(prim: Usd.Prim) -> Optional[Tuple[float, float, float]]:
    """prim의 월드 공간 중심 (x, y, z) 반환. BBox 중심 우선, 없으면 Xformable 변환 원점. (show_info와 동일)"""
    if not prim or not prim.IsValid():
        return None
    try:
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        bbox = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        center = bbox.GetMidpoint()
        return (center[0], center[1], center[2])
    except Exception:
        pass
    try:
        xform = UsdGeom.Xformable(prim)
        if xform:
            xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
            m = xform_cache.GetLocalToWorldTransform(prim)
            t = m.ExtractTranslation()
            return (t[0], t[1], t[2])
    except Exception:
        pass
    return None
