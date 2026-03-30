# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
xml_generator.py — EAPEIS 포트 이벤트용 XML 생성·역파싱 (TBS Control)

【이 파일의 역할】
- build_xml_string(): SEQUENCE_NAME과 포트 인자로 `<Envelop>...</Envelop>` XML 문자열 생성.
- parse_xml_string(): 위 XML을 dict로 역파싱(제어창 로그·디버그용). action_desc 포함.
- 태그/속성 상수(TAG_*, ATTR_*), 시퀀스 이름 상수(SEQ_*), 집합 FROM_TO_SEQS / PORT_ID_ONLY_SEQS.

【시퀀스 6종과 필요한 인자】
- FROM/TO 필요 (FROM_TO_SEQS): SEQ_MOVE_TRANSFERING, SEQ_MOVE
  → build_xml_string(seq, from_port_id=, to_port_id=) — FROM_INFO/TO_INFO에 FROM/TO ID.
  → PROCESS_JOB/PORT_ID: SEQ_MOVE는 항상 TO(장비 안착측). MOVE_TRANSFERING은 TO가 EP(1~3)면 TO, 아니면 FROM(버퍼간 이송).
- PORT_ID만 필요 (PORT_ID_ONLY_SEQS): SEQ_READYTOLOAD, SEQ_ARRIVED, SEQ_READYTOUNLOAD, SEQ_REMOVED
  → build_xml_string(seq, port_id=) — PROCESS_JOB의 ATTR_PORT_ID에 반영.

【새 시퀀스 종류를 추가하려면】
1) 이 파일 상단에 상수 추가: SEQ_NEW = "EAPEIS_PORT_..."
2) ALL_SEQS / FROM_TO_SEQS 또는 PORT_ID_ONLY_SEQS 중 하나에 분류(또는 새 집합 + build_xml_string 분기).
3) build_xml_string() 내부 if seq in FROM_TO_SEQS / elif seq in PORT_ID_ONLY_SEQS 에서 실제 XML 트리 생성 로직 추가.
4) _sequence_action_desc()에 로그용 설명 문자열 추가.
5) parse_xml_string 역호환: BODY의 SEQUENCE_NAME과 _extract_values_from_tree로 읽히는 속성이면 추가 속성만 dict에 넣으면 됨.

【제어창 UI와 연동 (control_window.py)】
- 콤보 항목·순서: build_control_window() 안 ComboBox 인자 목록.
- 입력 필드 전환: on_xml_seq_changed() — FROM_TO면 FROM/TO 행, PORT만이면 PORT_ID 행 표시.
- OK 버튼: on_xml_ok_clicked() — 위와 동일한 seqs 리스트 순서를 유지해야 인덱스가 맞음.
→ 시퀀스를 추가하면 세 곳(ComboBox, on_xml_seq_changed의 seqs, on_xml_ok_clicked의 seqs)을 동일하게 맞출 것.

【요구사항 반영】
- `<Envelop>` 루트, HEADER/BODY 대문자, Envelop 케이스 유지
- 역파싱: Envelop 없는 과거 포맷도 ROOT로 감싸 호환
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple
import xml.etree.ElementTree as ET

# ----------------
# tags / attrs
# ----------------
TAG_ENVELOP = "Envelop"  # 요청대로 대문자 고정이 아니라, 예시와 동일 케이스 유지
TAG_HEADER = "HEADER"
TAG_FACILITY = "FACILITY"
TAG_ENVIRONMENT = "ENVIRONMENT"
TAG_SENDERNODE = "SENDERNODE"
TAG_BODY = "BODY"

TAG_EAPEIS_PORT_EVENT = "EAPEIS_PORT_EVENT"
TAG_PROCESS_JOB = "PROCESS_JOB"
TAG_CARRIER = "CARRIER"
TAG_LOT = "LOT"
TAG_WAFER = "WAFER"

TAG_FROM_INFO = "FROM_INFO"
TAG_TO_INFO = "TO_INFO"

ATTR_DESTINATION = "DESTINATION"
ATTR_ORIGINATION = "ORIGINATION"
ATTR_TID = "TID"
ATTR_FACILITY = "FACILITY"
ATTR_EQUIPMENT_ID = "EQUIPMENT_ID"
ATTR_SEQUENCE_NAME = "SEQUENCE_NAME"

ATTR_CONTROL_JOB_ID = "CONTROL_JOB_ID"
ATTR_FOUP = "FOUP"

ATTR_PROCESS_JOB_ID = "PROCESS_JOB_ID"
ATTR_BATCH_ID = "BATCH_ID"
ATTR_BATCH_COUNT = "BATCH_COUNT"
ATTR_PORT_ID = "PORT_ID"

ATTR_CARRIER_ID = "CARRIER_ID"
ATTR_LOT_ID = "LOT_ID"
ATTR_OPERATOR = "OPERATOR"
ATTR_OPERATION = "OPERATION"
ATTR_WAFER_ID = "WAFER_ID"

ATTR_FROM_EQP_ID = "FROM_EQP_ID"
ATTR_FROM_PORT_ID = "FROM_PORT_ID"
ATTR_TO_EQP_ID = "TO_EQP_ID"
ATTR_TO_PORT_ID = "TO_PORT_ID"


SEQ_MOVE_TRANSFERING = "EAPEIS_PORT_MOVE_TRANSFERING"
SEQ_MOVE = "EAPEIS_PORT_MOVE"
# BP->EP 이동(요청) — 요구사항: BP->EP 이동 시 애니 트리거 전용 시퀀스명
SEQ_MOVE_REQ = "EISEAP_PORT_MOVE_REQ"
SEQ_READYTOLOAD = "EAPEIS_PORT_READYTOLOAD"
SEQ_ARRIVED = "EAPEIS_PORT_ARRIVED"
SEQ_READYTOUNLOAD = "EAPEIS_PORT_READYTOUNLOAD"
SEQ_REMOVED = "EAPEIS_PORT_REMOVED"

FROM_TO_SEQS = {SEQ_MOVE_TRANSFERING, SEQ_MOVE, SEQ_MOVE_REQ}
PORT_ID_ONLY_SEQS = {SEQ_READYTOLOAD, SEQ_ARRIVED, SEQ_READYTOUNLOAD, SEQ_REMOVED}
ALL_SEQS = tuple(sorted(list(FROM_TO_SEQS | PORT_ID_ONLY_SEQS)))


def _u(val: str) -> str:
    """XML 속성/태그명용: 문자열을 대문자로 정규화."""
    return (val or "").upper()


def _set_attrs(elem: ET.Element, attrs: Dict[str, str]) -> None:
    """Element에 attrs 딕셔너리를 _u 키·값으로 일괄 세팅."""
    for k, v in (attrs or {}).items():
        elem.set(_u(k), _u(str(v)))


def build_header() -> ET.Element:
    """Envelop용 HEADER: FACILITY, ENVIRONMENT, SENDERNODE 자식만 두는 플레이스홀더."""
    header = ET.Element(TAG_HEADER)
    ET.SubElement(header, TAG_FACILITY)
    ET.SubElement(header, TAG_ENVIRONMENT)
    ET.SubElement(header, TAG_SENDERNODE)
    return header


def build_body_attributes(sequence_name: str) -> Dict[str, str]:
    """BODY PROCESS_JOB에 들어갈 기본 속성 딕셔너리(SEQUENCE_NAME 등)."""
    return {
        ATTR_DESTINATION: "",
        ATTR_ORIGINATION: "",
        ATTR_TID: "",
        ATTR_FACILITY: "",
        ATTR_EQUIPMENT_ID: "",
        ATTR_SEQUENCE_NAME: sequence_name,
    }


def _build_lot_and_wafer() -> ET.Element:
    """CARRIER 하위 LOT/WAFER 플레이스홀더 노드."""
    lot = ET.Element(TAG_LOT)
    _set_attrs(
        lot,
        {
            ATTR_LOT_ID: "",
            ATTR_OPERATOR: "",
            ATTR_OPERATION: "",
        },
    )
    wafer = ET.SubElement(lot, TAG_WAFER)
    _set_attrs(wafer, {ATTR_WAFER_ID: ""})
    return lot


def _build_carrier() -> ET.Element:
    """CARRIER 노드 + 하위 LOT/WAFER 플레이스홀더."""
    carrier = ET.Element(TAG_CARRIER)
    _set_attrs(carrier, {ATTR_CARRIER_ID: ""})
    carrier.append(_build_lot_and_wafer())
    return carrier


def _build_process_job(
    port_id: Optional[int],
) -> ET.Element:
    """PROCESS_JOB + CARRIER 트리. port_id는 ATTR_PORT_ID에 반영(없으면 빈 문자열)."""
    pj = ET.Element(TAG_PROCESS_JOB)
    _set_attrs(
        pj,
        {
            ATTR_PROCESS_JOB_ID: "",
            ATTR_BATCH_ID: "",
            ATTR_BATCH_COUNT: "",
            ATTR_PORT_ID: "" if port_id is None else str(int(port_id)),
        },
    )
    pj.append(_build_carrier())
    return pj


def _sequence_action_desc(
    seq: str,
    port_id: str,
    from_port_id: str,
    to_port_id: str,
) -> str:
    """시퀀스별 사람이 읽기 쉬운 설명 한 줄(제어창 로그·디버그용)."""
    # 사용자 요구사항의 1~6 시나리오를 그대로 로그로 제공
    if seq == SEQ_READYTOLOAD:
        return f"1) 장비포트에서 새로운 FOUP를 받을 준비 완료. PORT_ID={port_id} (애니없음)"
    if seq == SEQ_ARRIVED:
        return f"2) PORT_ID={port_id} 포트에 FOUP 안착 애니 실행 (PORT 이동)"
    if seq == SEQ_MOVE_TRANSFERING:
        return f"3) FROM_PORT_ID={from_port_id} -> TO_PORT_ID={to_port_id} : TBS 인아웃 포트에서 버퍼 포트로 이동 애니"
    if seq == SEQ_MOVE:
        return (
            f"4) FROM_PORT_ID={from_port_id}, TO_PORT_ID={to_port_id} : TBS 포트에서 장비포트로 내리는 애니 실행"
        )
    if seq == SEQ_MOVE_REQ:
        return f"4-2) FROM_PORT_ID={from_port_id} -> TO_PORT_ID={to_port_id} : 버퍼(BP)에서 공정포트(EP)로 이동 요청 애니"
    if seq == SEQ_READYTOUNLOAD:
        return f"5) 회수 요청 큐 적재(애니없음). OHT가 PORT_ID={port_id} 포트에서 FOUP 회수 준비"
    if seq == SEQ_REMOVED:
        return f"6) OHT가 PORT_ID={port_id} 포트에서 FOUP을 회수하는 애니 실행(회수 진행)"
    return "알 수 없는 시퀀스"


def build_xml_string(
    sequence_name: str,
    port_id: Optional[int] = None,
    from_port_id: Optional[int] = None,
    to_port_id: Optional[int] = None,
) -> str:
    """EAPEIS 포트 이벤트용 XML 문자열 전체 생성. FROM/TO 또는 PORT_ID만 시퀀스에 맞게 전달."""
    seq = _u(sequence_name)
    header = build_header()

    body = ET.Element(TAG_BODY)
    _set_attrs(body, build_body_attributes(seq))

    event = ET.SubElement(body, TAG_EAPEIS_PORT_EVENT)
    _set_attrs(event, {ATTR_CONTROL_JOB_ID: ""})

    if seq in FROM_TO_SEQS:
        if from_port_id is None or to_port_id is None:
            raise ValueError("FROM/TO 시퀀스는 from_port_id, to_port_id가 필요합니다.")

        fi = int(from_port_id)
        ti = int(to_port_id)
        # PROCESS_JOB PORT_ID: OHT→EP(MOVE) 등에서 FROM 가상 ID(9)가 아닌 도착 EP(1~3)가 되도록 TO 우선
        if seq == SEQ_MOVE:
            pj_port = ti
        elif 1 <= ti <= 3:
            pj_port = ti
        else:
            pj_port = fi

        event.append(_build_process_job(pj_port))

        from_info = ET.SubElement(event, TAG_FROM_INFO)
        _set_attrs(
            from_info,
            {
                ATTR_FROM_EQP_ID: "",
                ATTR_FROM_PORT_ID: str(int(from_port_id)),
            },
        )
        to_info = ET.SubElement(event, TAG_TO_INFO)
        _set_attrs(
            to_info,
            {
                ATTR_TO_EQP_ID: "",
                ATTR_TO_PORT_ID: str(int(to_port_id)),
            },
        )
    elif seq in PORT_ID_ONLY_SEQS:
        if port_id is None:
            raise ValueError("PORT_ID 시퀀스는 port_id가 필요합니다.")
        event.append(_build_process_job(port_id))
    else:
        raise ValueError(f"지원하지 않는 sequence_name: {sequence_name}")

    # Envelop 루트로 감싸기
    envelop = ET.Element(TAG_ENVELOP)
    envelop.append(header)
    envelop.append(body)

    xml_bytes = ET.tostring(envelop, encoding="utf-8", xml_declaration=True)
    xml = xml_bytes.decode("utf-8")
    return xml.strip() + "\n"


def _strip_xml_declaration(s: str) -> str:
    """선행 <?xml ...?> 선언을 제거해 파싱용 본문만 남김."""
    s2 = (s or "").strip()
    if s2.startswith("<?xml"):
        end = s2.find("?>")
        if end != -1:
            s2 = s2[end + 2 :].lstrip()
    return s2


def _extract_values_from_tree(root: ET.Element) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """XML 트리에서 PROCESS_JOB PORT_ID, FROM_INFO, TO_INFO 숫자 문자열을 추출. 반환 (port_id, from, to)."""
    # return: (port_id, from_port_id, to_port_id)
    port_id = None
    from_port_id = None
    to_port_id = None

    # PROCESS_JOB PORT_ID
    pj = root.find(f".//{TAG_PROCESS_JOB}")
    if pj is not None:
        port_id = pj.get(ATTR_PORT_ID)

    from_info = root.find(f".//{TAG_FROM_INFO}")
    if from_info is not None:
        from_port_id = from_info.get(ATTR_FROM_PORT_ID)

    to_info = root.find(f".//{TAG_TO_INFO}")
    if to_info is not None:
        to_port_id = to_info.get(ATTR_TO_PORT_ID)

    return port_id, from_port_id, to_port_id


def parse_xml_string(xml_text: str) -> Optional[dict]:
    """XML 문자열을 dict로 역파싱(sequence_name, 포트, action_desc 등). 실패 시 None."""
    if not xml_text or not xml_text.strip():
        return None

    s = _strip_xml_declaration(xml_text)
    try:
        # 과거 포맷(HEADER/BODY가 최상위에 나열되고 Envelop가 없는 형태) 호환:
        # - Envelop가 있으면 그대로 파싱
        # - 없으면 ROOT로 감싸서 BODY를 찾기 쉽게 함
        if "<Envelop" in s or "</Envelop>" in s:
            root = ET.fromstring(s)
        else:
            root = ET.fromstring("<ROOT>" + s + "</ROOT>")
    except ET.ParseError:
        return None

    body = root.find(f".//{TAG_BODY}")
    if body is None:
        return None

    seq_name = body.get(ATTR_SEQUENCE_NAME, "") or ""
    port_id, from_port_id, to_port_id = _extract_values_from_tree(root)

    seq_name_u = _u(seq_name)
    port_id_s = "" if port_id is None else str(port_id)
    from_port_id_s = "" if from_port_id is None else str(from_port_id)
    to_port_id_s = "" if to_port_id is None else str(to_port_id)

    out: Dict[str, str] = {
        "sequence_name": seq_name_u,
        "destination": body.get(ATTR_DESTINATION, "") or "",
        "origination": body.get(ATTR_ORIGINATION, "") or "",
        "tid": body.get(ATTR_TID, "") or "",
        "facility": body.get(ATTR_FACILITY, "") or "",
        "equipment_id": body.get(ATTR_EQUIPMENT_ID, "") or "",
        "port_id": port_id_s,
        "from_port_id": from_port_id_s,
        "to_port_id": to_port_id_s,
        # 기존 UI 출력 호환: 값이 없으면 빈 문자열
        "foup": "",
        "from_eqp_id": "",
        "to_eqp_id": "",
        "action_desc": _sequence_action_desc(
            seq=seq_name_u,
            port_id=port_id_s,
            from_port_id=from_port_id_s,
            to_port_id=to_port_id_s,
        ),
    }
    return out
