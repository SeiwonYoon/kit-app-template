# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
signal_parser.py — 가상 시그널(JSON/XML) 파싱 → {objects, segments} dict

【역할】
- control_window의 "가상 시그널 재생" 등에서 JSON/XML 문자열을 파싱하여 애니메이션 세그먼트 구조로 변환.

【수정 가이드】
- JSON 스키마 변경: parse_signal_json, _normalize_parsed
- XML 태그/속성 변경: parse_signal_xml (object/segment 요소)
- 통합 진입점: parse_signal()

사용처: control_window.py (SAMPLE_GENERATOR_JSON 형식과 맞춰야 함)

【유지보수 시나리오】
1) 외부 신호 포맷 변경(JSON/XML)
   - 본 파일에서 파싱 키를 수정하고, 반환 스키마는 유지({objects, segments})
2) 새 애니메이션 파라미터 추가(예: easing, axis)
   - _normalize_parsed와 parse_signal_xml에 키 추가
   - control_window.run_generator_from_parsed/translate_animation 소비 로직도 동시 수정
3) 파싱 실패 디버깅
   - parse_signal() 분기(format) 확인
   - None 반환 지점(필수 키 누락) 로그 추가 권장
"""

import json
import xml.etree.ElementTree as ET
from typing import Any, List, Optional


def parse_signal_json(text: str) -> Optional[dict]:
    """JSON 문자열을 파싱해 {objects, segments} 형태로 정규화. 실패 시 None."""
    if not text or not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return _normalize_parsed(data)


def parse_signal_xml(text: str) -> Optional[dict]:
    """XML 루트에서 <object name=...>·<segment duration dx dy dz>를 읽어 {objects, segments}로 변환."""
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
    """dict(JSON 루트)에서 objects·segments 리스트를 검증·정규화."""
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
    """format에 따라 JSON 또는 XML 파싱 진입. 반환은 {objects, segments} 또는 None."""
    fmt = (format or "json").strip().lower()
    if fmt == "xml":
        return parse_signal_xml(data)
    return parse_signal_json(data)
