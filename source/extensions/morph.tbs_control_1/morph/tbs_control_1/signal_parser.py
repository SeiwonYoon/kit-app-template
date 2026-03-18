# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
signal_parser.py — 시그널 파서: JSON/XML 형식의 가상 시그널을 파싱하여 동일한 구조의 dict로 반환.

기능: control_window에서 import. 장비로부터 수신한 데이터 파싱 및 가상 시그널 재생에 사용.
parse_signal(data, format="json"|"xml") → {"objects": [...], "segments": [...]}
"""

import json
import xml.etree.ElementTree as ET
from typing import Any, List, Optional


def parse_signal_json(text: str) -> Optional[dict]:
    if not text or not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return _normalize_parsed(data)


def parse_signal_xml(text: str) -> Optional[dict]:
    if not text or not text.strip():
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    objects: List[str] = []
    segments: List[dict] = []
    for obj in root.findall(".//object"):
        name = obj.get("name")
        if name and name.strip():
            objects.append(name.strip())
    for seg in root.findall(".//segment"):
        duration = seg.get("duration")
        dx = seg.get("dx", "0")
        dy = seg.get("dy", "0")
        dz = seg.get("dz", "0")
        try:
            dur_f = float(duration) if duration else 0.0
            if dur_f <= 0:
                continue
            delta = (float(dx), float(dy), float(dz))
            segments.append({"duration": dur_f, "delta": delta})
        except (TypeError, ValueError):
            continue
    if not objects or not segments:
        return None
    return {"objects": objects, "segments": segments}


def _normalize_parsed(data: Any) -> Optional[dict]:
    if not data or not isinstance(data, dict):
        return None
    objects = data.get("objects")
    if not objects or not isinstance(objects, list):
        return None
    objects = [str(o).strip() for o in objects if isinstance(o, str) and str(o).strip()]
    animation = data.get("animation")
    if not animation or not isinstance(animation, dict):
        return None
    segments_data = animation.get("segments")
    if not segments_data or not isinstance(segments_data, list):
        return None
    segments: List[dict] = []
    for seg in segments_data:
        if not isinstance(seg, dict):
            continue
        d = seg.get("delta")
        duration = float(seg.get("duration", 0))
        if d is None or duration <= 0:
            continue
        if isinstance(d, (list, tuple)) and len(d) >= 3:
            try:
                delta = (float(d[0]), float(d[1]), float(d[2]))
            except (TypeError, ValueError):
                continue
        else:
            continue
        segments.append({"duration": duration, "delta": delta})
    if not objects or not segments:
        return None
    return {"objects": objects, "segments": segments}


def parse_signal(data: str, format: str = "json") -> Optional[dict]:
    fmt = (format or "json").strip().lower()
    if fmt == "xml":
        return parse_signal_xml(data)
    return parse_signal_json(data)
