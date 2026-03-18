# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
xml_generator.py — XML 제너레이터 (TBS Control).

BODY 포함, 태그/속성/속성값 대문자. A/B 시 from_port_id, to_port_id 반영.
C/D는 build_body_for_sequence_cd 수정 가이드 포함. parse_xml_string으로 역파싱.
"""

from __future__ import annotations

from typing import Dict, Optional
import xml.etree.ElementTree as ET

TAG_HEADER = "HEADER"
TAG_FACILITY = "FACILITY"
TAG_ENVIRONMENT = "ENVIRONMENT"
TAG_SENDERNODE = "SENDERNODE"
TAG_BODY = "BODY"
TAG_DATA = "DATA"
TAG_EAPEIS_PORT_MOVE = "EAPEIS_PORT_MOVE"
TAG_FROM_INFO = "FROM_INFO"
TAG_TO_INFO = "TO_INFO"
ATTR_DESTINATION = "DESTINATION"
ATTR_ORIGINATION = "ORIGINATION"
ATTR_TID = "TID"
ATTR_FACILITY = "FACILITY"
ATTR_EQUIPMENT_ID = "EQUIPMENT_ID"
ATTR_SEQUENCE_NAME = "SEQUENCE_NAME"
ATTR_FOUP = "FOUP"
ATTR_FROM_EQP_ID = "FROM_EQP_ID"
ATTR_FROM_PORT_ID = "FROM_PORT_ID"
ATTR_TO_EQP_ID = "TO_EQP_ID"
ATTR_TO_PORT_ID = "TO_PORT_ID"


def _u(val: str) -> str:
    return (val or "").upper()


def _set_attrs(elem: ET.Element, attrs: Dict[str, str]) -> None:
    for k, v in (attrs or {}).items():
        elem.set(_u(k), _u(str(v)))


def build_header() -> ET.Element:
    header = ET.Element(TAG_HEADER)
    ET.SubElement(header, TAG_FACILITY)
    ET.SubElement(header, TAG_ENVIRONMENT)
    ET.SubElement(header, TAG_SENDERNODE)
    return header


def build_body_attributes(sequence_name: str) -> Dict[str, str]:
    return {
        ATTR_DESTINATION: "",
        ATTR_ORIGINATION: "",
        ATTR_TID: "",
        ATTR_FACILITY: "",
        ATTR_EQUIPMENT_ID: "",
        ATTR_SEQUENCE_NAME: sequence_name,
    }


def build_body_for_sequence_ab(sequence_name: str, from_port_id: int, to_port_id: int) -> ET.Element:
    body = ET.Element(TAG_BODY)
    _set_attrs(body, build_body_attributes(sequence_name))
    move = ET.SubElement(body, TAG_EAPEIS_PORT_MOVE)
    _set_attrs(move, {ATTR_FOUP: ""})
    ET.SubElement(move, TAG_FROM_INFO, {ATTR_FROM_EQP_ID: "", ATTR_FROM_PORT_ID: str(from_port_id)})
    ET.SubElement(move, TAG_TO_INFO, {ATTR_TO_EQP_ID: "", ATTR_TO_PORT_ID: str(to_port_id)})
    return body


def build_body_for_sequence_cd(sequence_name: str) -> ET.Element:
    body = ET.Element(TAG_BODY)
    _set_attrs(body, build_body_attributes(sequence_name))
    return body


def build_xml_string(
    sequence_name: str,
    from_port_id: Optional[int] = None,
    to_port_id: Optional[int] = None,
) -> str:
    seq = _u(sequence_name)
    header = build_header()
    if seq in ("A", "B"):
        if from_port_id is None or to_port_id is None:
            raise ValueError("A/B requires from_port_id and to_port_id")
        body = build_body_for_sequence_ab(seq, int(from_port_id), int(to_port_id))
    else:
        body = build_body_for_sequence_cd(seq)
    root = ET.Element("ROOT")
    root.append(header)
    root.append(body)
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    xml = xml_bytes.decode("utf-8")
    xml = xml.replace("<ROOT>", "").replace("</ROOT>", "")
    return xml.strip() + "\n"


def parse_xml_string(xml_text: str) -> Optional[dict]:
    if not xml_text or not xml_text.strip():
        return None
    s = xml_text.strip()
    if s.startswith("<?xml"):
        end = s.find("?>")
        if end != -1:
            s = s[end + 2 :].lstrip()
    try:
        wrapped = "<ROOT>" + s + "</ROOT>"
        root = ET.fromstring(wrapped)
    except ET.ParseError:
        return None
    body = root.find(TAG_BODY)
    if body is None:
        return None
    out: Dict[str, str] = {
        "sequence_name": body.get(ATTR_SEQUENCE_NAME, ""),
        "destination": body.get(ATTR_DESTINATION, ""),
        "origination": body.get(ATTR_ORIGINATION, ""),
        "tid": body.get(ATTR_TID, ""),
        "facility": body.get(ATTR_FACILITY, ""),
        "equipment_id": body.get(ATTR_EQUIPMENT_ID, ""),
        "foup": "",
        "from_eqp_id": "",
        "from_port_id": "",
        "to_eqp_id": "",
        "to_port_id": "",
    }
    move = body.find(TAG_EAPEIS_PORT_MOVE)
    if move is not None:
        out["foup"] = move.get(ATTR_FOUP, "")
        from_info = move.find(TAG_FROM_INFO)
        if from_info is not None:
            out["from_eqp_id"] = from_info.get(ATTR_FROM_EQP_ID, "")
            out["from_port_id"] = from_info.get(ATTR_FROM_PORT_ID, "")
        to_info = move.find(TAG_TO_INFO)
        if to_info is not None:
            out["to_eqp_id"] = to_info.get(ATTR_TO_EQP_ID, "")
            out["to_port_id"] = to_info.get(ATTR_TO_PORT_ID, "")
    return out
