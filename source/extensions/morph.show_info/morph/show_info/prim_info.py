# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""Collect prim name, type, metadata, and attributes for display."""

from typing import List, Optional, Tuple

from pxr import Usd, UsdGeom


# Approximate character width/height in pixels for panel sizing (monospace-ish)
# CHAR_WIDTH: 보수적으로 잡아서 실제 폰트(FONT_SIZE 11)가 더 넓어도 영역 안에 들어오도록
CHAR_WIDTH = 9
LINE_HEIGHT = 18
PADDING_H = 16
PADDING_V = 12
MAX_LINE_LEN = 80  # wrap or truncate long values
MAX_ATTR_VALUE_LEN = 120
# 패널 내용 영역에 맞춘 한 줄 최대 글자 수 (영역 벗어남 방지)
CHARS_PER_LINE = 40


def _wrap_line(line: str, max_chars: int = CHARS_PER_LINE) -> List[str]:
    """Wrap a long line into multiple lines; break at space when possible."""
    if not line or len(line) <= max_chars:
        return [line] if line else []
    result = []
    rest = line
    while rest:
        if len(rest) <= max_chars:
            result.append(rest)
            break
        chunk = rest[: max_chars + 1]
        last_space = chunk.rfind(" ")
        if last_space > max_chars // 2:
            split_at = last_space
        else:
            split_at = max_chars
        result.append(rest[:split_at].strip())
        rest = rest[split_at:].strip()
    return result


def _format_value(val) -> str:
    """Format a USD attribute/metadata value for display."""
    if val is None:
        return "None"
    s = str(val)
    if len(s) > MAX_ATTR_VALUE_LEN:
        s = s[: MAX_ATTR_VALUE_LEN - 3] + "..."
    return s.replace("\n", " ").replace("\r", " ")


def _flatten_with_wrap(raw_lines: List[str]) -> List[str]:
    """Take raw lines and wrap long ones so every line fits within CHARS_PER_LINE."""
    out: List[str] = []
    for line in raw_lines:
        if not line:
            out.append("")
            continue
        for wrapped in _wrap_line(line):
            out.append(wrapped)
    return out


def get_prim_display_lines(prim: Usd.Prim) -> List[str]:
    """
    Build a list of lines to show in the 3D panel: name, type, metadata, attributes.
    Long lines are wrapped to fit the panel width.
    """
    raw: List[str] = []

    if not prim or not prim.IsValid():
        return []

    path = str(prim.GetPath())
    name = prim.GetName()
    raw.append(f"Name: {name}")
    raw.append(f"Path: {path}")
    raw.append("")

    raw.append(f"Type: {prim.GetTypeName()}")
    raw.append("")

    meta_keys = []
    try:
        meta = prim.GetAllMetadata()
        if meta:
            meta_keys = sorted(meta.keys())
    except Exception:
        try:
            meta_keys = sorted(prim.GetMetadataKeys()) if hasattr(prim, "GetMetadataKeys") else []
        except Exception:
            meta_keys = []
    if meta_keys:
        raw.append("--- Metadata ---")
        for key in meta_keys:
            try:
                val = prim.GetMetadata(key)
                raw.append(f"  {key}: {_format_value(val)}")
            except Exception:
                raw.append(f"  {key}: <error>")
        raw.append("")

    attrs = list(prim.GetAttributes())
    if attrs:
        raw.append("--- Attributes ---")
        for attr in sorted(attrs, key=lambda a: a.GetName()):
            name_attr = attr.GetName()
            try:
                type_name = attr.GetTypeName().type.typeName if attr.GetTypeName() else "?"
                val = attr.Get()
                val_str = _format_value(val)
                raw.append(f"  {name_attr} ({type_name})")
                raw.append(f"    = {val_str}")
            except Exception:
                raw.append(f"  {name_attr}: <error>")
        raw.append("")

    return _flatten_with_wrap(raw)


def get_prim_world_center(prim: Usd.Prim) -> Optional[Tuple[float, float, float]]:
    """Return world-space center of the prim (bbox center or xform)."""
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
