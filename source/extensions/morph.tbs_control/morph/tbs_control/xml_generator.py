# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
XML 제너레이터 (TBS Control)

요구사항
- BODY 포함, 내부의 모든 태그명/속성명/속성값은 **대문자**로 생성
- sequence_name = A/B일 때 from_port_id, to_port_id 입력값을 반영
- C/D는 Body 구조가 달라질 예정이라, 쉽게 수정할 수 있도록 "수정 가이드" 주석을 상세히 포함

-------------------------------
수정 가이드 (중요)
-------------------------------
1) 태그명 변경:
   - 아래 TAG_* 상수들을 바꾸면 됩니다.
   - 예: TAG_SENDERNODE 를 "SENDER_NODE"로 바꾸고 싶으면 값을 수정하세요.

2) BODY 속성 변경:
   - build_body_attributes()에서 어떤 속성을 쓸지/기본값을 어떻게 넣을지 수정하세요.
   - 현재는 destination/origination/tid/facility/equipment_id/sequence_name 만 넣습니다.

3) A/B 시그널의 BODY 내부 구조 변경:
   - build_body_for_sequence_ab()가 A/B 케이스의 "BODY 하위" 구조를 만듭니다.
   - EAPEIS_PORT_MOVE 태그명, FOUP 속성명, FROM_INFO/TO_INFO 태그명 등을 여기서 수정하세요.

4) C/D 시그널 구현:
   - build_body_for_sequence_cd()는 지금은 "가이드용"으로 비어 있습니다.
   - C/D 요구사항이 확정되면 이 함수에 BODY 하위 구조를 추가하세요.

5) 대문자 규칙:
   - 본 모듈은 모든 태그/속성/속성값을 upper() 처리합니다.
   - 숫자("1", "6")는 upper()해도 동일합니다.
"""

from __future__ import annotations

from typing import Dict, Optional
import xml.etree.ElementTree as ET


# -------------------------------
# 태그/속성명 상수 (필요 시 여기만 바꿔도 됨)
# -------------------------------
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
    """대문자 변환(빈 문자열 포함)."""
    return (val or "").upper()


def _set_attrs(elem: ET.Element, attrs: Dict[str, str]) -> None:
    """속성명을 대문자로, 속성값도 대문자로 세팅."""
    for k, v in (attrs or {}).items():
        elem.set(_u(k), _u(str(v)))


def build_header() -> ET.Element:
    """
    <HEADER>
      <FACILITY/>
      <ENVIRONMENT/>
      <SENDERNODE/>
    </HEADER>
    """
    header = ET.Element(TAG_HEADER)
    ET.SubElement(header, TAG_FACILITY)
    ET.SubElement(header, TAG_ENVIRONMENT)
    ET.SubElement(header, TAG_SENDERNODE)
    return header


def build_body_attributes(sequence_name: str) -> Dict[str, str]:
    """
    BODY 기본 속성 세트.
    필요하면 destination/origination/tid/facility/equipment_id의 기본값을 여기서 채우세요.
    """
    return {
        ATTR_DESTINATION: "",
        ATTR_ORIGINATION: "",
        ATTR_TID: "",
        ATTR_FACILITY: "",
        ATTR_EQUIPMENT_ID: "",
        ATTR_SEQUENCE_NAME: sequence_name,
    }


def build_body_for_sequence_ab(sequence_name: str, from_port_id: int, to_port_id: int) -> ET.Element:
    """
    A/B 시퀀스용 BODY.

    예시 결과(대문자):
    <BODY ... SEQUENCE_NAME="A">
      <EAPEIS_PORT_MOVE FOUP="">
        <FROM_INFO FROM_EQP_ID="" FROM_PORT_ID="1" />
        <TO_INFO TO_EQP_ID="" TO_PORT_ID="6" />
      </EAPEIS_PORT_MOVE>
    </BODY>
    """
    body = ET.Element(TAG_BODY)
    _set_attrs(body, build_body_attributes(sequence_name))

    move = ET.SubElement(body, TAG_EAPEIS_PORT_MOVE)
    _set_attrs(move, {ATTR_FOUP: ""})

    ET.SubElement(
        move,
        TAG_FROM_INFO,
        {
            ATTR_FROM_EQP_ID: "",
            ATTR_FROM_PORT_ID: str(from_port_id),
        },
    )
    ET.SubElement(
        move,
        TAG_TO_INFO,
        {
            ATTR_TO_EQP_ID: "",
            ATTR_TO_PORT_ID: str(to_port_id),
        },
    )
    return body


def build_body_for_sequence_cd(sequence_name: str) -> ET.Element:
    """
    C/D 시퀀스용 BODY (향후 구현).

    -------------------------------
    여기를 수정해서 C/D 구조를 넣으세요.
    -------------------------------
    예: C/D는 BODY 아래에 DATA 태그가 필요하다면,
      data = ET.SubElement(body, "DATA")
      _set_attrs(data, {...})
      ...

    혹은 태그명이 완전히 다르면 TAG_* 상수를 바꾸거나,
    C/D 전용 TAG/ATTR 상수를 추가해서 사용하면 됩니다.
    """
    body = ET.Element(TAG_BODY)
    _set_attrs(body, build_body_attributes(sequence_name))

    # TODO(C/D): 여기서 body 하위 구조를 구현하세요.
    # 예시(가이드):
    # data = ET.SubElement(body, "DATA")
    # _set_attrs(data, {"SOME_ATTR": "VALUE"})
    return body


def build_xml_string(
    sequence_name: str,
    from_port_id: Optional[int] = None,
    to_port_id: Optional[int] = None,
) -> str:
    """
    최종 XML 문자열 생성.
    - sequence_name은 A/B/C/D 중 하나를 기대(대문자로 변환됨)
    - A/B: from_port_id, to_port_id 필수
    - C/D: 추후 구조 확정 시 build_body_for_sequence_cd 구현
    """
    seq = _u(sequence_name)

    header = build_header()
    if seq in ("A", "B"):
        if from_port_id is None or to_port_id is None:
            raise ValueError("A/B requires from_port_id and to_port_id")
        body = build_body_for_sequence_ab(seq, int(from_port_id), int(to_port_id))
    else:
        body = build_body_for_sequence_cd(seq)

    # 예시처럼 header와 body가 최상위에 그대로 나열되길 원하므로,
    # 임시 ROOT를 둔 뒤 직렬화 시 ROOT를 제거(문자열 조립)합니다.
    root = ET.Element("ROOT")
    root.append(header)
    root.append(body)

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    xml = xml_bytes.decode("utf-8")

    # ROOT 래퍼 제거: <ROOT> ... </ROOT> 없이 header/body만 남기기
    # (ElementTree는 단일 루트만 직렬화 가능해서 이런 방식을 사용)
    # 매우 단순한 구조라 문자열 기반으로 안전하게 제거합니다.
    xml = xml.replace("<ROOT>", "").replace("</ROOT>", "")

    # 보기 좋게 줄바꿈/들여쓰기 수준만 맞춤 (필수는 아님)
    # 필요하면 여기서 pretty print 로직을 추가하세요.
    return xml.strip() + "\n"


def parse_xml_string(xml_text: str) -> Optional[dict]:
    """
    OK 버튼으로 생성된 XML(HEADER + BODY)을 역으로 파싱하여 주요 속성값을 추출.

    반환 예:
    {
      "sequence_name": "A",
      "destination": "",
      "origination": "",
      "tid": "",
      "facility": "",
      "equipment_id": "",
      "foup": "",
      "from_eqp_id": "",
      "from_port_id": "1",
      "to_eqp_id": "",
      "to_port_id": "6",
    }

    수정 가이드:
    - 태그/속성 구조가 바뀌면 아래 find 경로(TAG_*/ATTR_*)만 맞춰주면 됩니다.
    - C/D 구현 시 build_body_for_sequence_cd()와 함께 여기 파싱도 C/D 구조에 맞게 확장하세요.
    """
    if not xml_text or not xml_text.strip():
        return None
    # ElementTree는 XML 선언(<?xml ...?>)이 다른 엘리먼트의 자식으로 들어가면 ParseError가 납니다.
    # build_xml_string() 결과를 <ROOT>...</ROOT>로 감싸서 파싱하기 때문에,
    # 여기서는 선언을 제거한 뒤 파싱합니다.
    s = xml_text.strip()
    if s.startswith("<?xml"):
        end = s.find("?>")
        if end != -1:
            s = s[end + 2 :].lstrip()
    try:
        # header/body가 최상위에 나열되어 있어서 ROOT 래퍼를 씌워 파싱
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
