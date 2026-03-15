# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
시그널 파서: JSON/XML 형식의 가상 시그널을 파싱하여 동일한 구조의 dict로 반환.
extension.py에서 import하여 사용. 장비로부터 수신한 데이터 파싱 및 애니메이션 실행에 사용.
"""

import json
import xml.etree.ElementTree as ET
from typing import Any, List, Optional


def parse_signal_json(text: str) -> Optional[dict]:
    """
    JSON 시그널 파싱.
    기대 형식: {"objects": ["Mesh_226", "Mesh_567"], "animation": {"segments": [{"duration": 1.0, "delta": [100,0,0]}, ...]}}
    반환: {"objects": [...], "segments": [{"duration": float, "delta": (x,y,z)}, ...]} 또는 None
    """
    if not text or not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return _normalize_parsed(data)


def parse_signal_xml(text: str) -> Optional[dict]:
    """
    XML 시그널 파싱. 태그 속성값을 읽어 JSON과 동일한 동작을 위한 구조로 변환.
    기대 형식 예:
      <signal>
        <objects><object name="Mesh_226"/><object name="Mesh_567"/></objects>
        <animation>
          <segment duration="1.0" dx="100" dy="0" dz="0"/>
          <segment duration="1.0" dx="0" dy="100" dz="0"/>
          <segment duration="2.0" dx="-100" dy="-100" dz="0"/>
        </animation>
      </signal>
    반환: {"objects": [...], "segments": [{"duration": float, "delta": (x,y,z)}, ...]} 또는 None
    """
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
    """JSON 파싱 결과를 공통 형식 {"objects": [...], "segments": [...]}로 정규화."""
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
    """
    시그널 문자열을 format에 따라 파싱하여 공통 구조로 반환.
    format: "json" | "xml"
    반환: {"objects": [str, ...], "segments": [{"duration": float, "delta": (x,y,z)}, ...]} 또는 None
    """
    fmt = (format or "json").strip().lower()
    if fmt == "xml":
        return parse_signal_xml(data)
    return parse_signal_json(data)
