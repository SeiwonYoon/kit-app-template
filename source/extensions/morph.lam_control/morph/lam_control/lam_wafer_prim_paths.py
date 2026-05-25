"""LAM 시뮬 — 웨이퍼 prim 경로 SSOT (slot_key → USD prim path).

**이 파일만 수정**하면 된다. ``simulation_play`` 는 ``load_wafer_prim_by_slot_key()`` 로 맵을 읽는다.

- 아래 ``WAFER_PRIM_BY_SLOT_KEY`` 에 **슬롯 142 + 논리 3 = 145** 항목을 수동으로 적는다.
- 팔 끝은 dict 의 ``"LOGICAL:ATM_ARM"`` / ``"LOGICAL:VTM_EE_L"`` / ``"LOGICAL:VTM_EE_R"`` 줄만 수정.
- 이벤트 JSON 의 ``{SLOT_WAFER}`` / ``{ARM_WAFER}`` → ``build_steps_for_event`` 가 본 dict 로 치환.

``IS_TEST`` (모듈 상단):
    - ``False`` — 실무 ``WAFER_PRIM_BY_SLOT_KEY`` 경로.
    - ``True`` — FOUP 슬롯만 ``/wafer_01/_01``, ``/wafer_01/_02`` … 형식 (테스트 stage).

``WAFER_PRIM_PATH_PREFIX``:
- 비우면 dict 에 적은 경로 문자열을 **그대로** 쓴다.
- ``"/World/"`` 처럼 넣으면, **슬래시로 시작하지 않는** 모든 경로 앞에 한 번에 붙인다.
  (이미 ``/`` 로 시작하는 항목은 변경하지 않음. 145개마다 ``/World/`` 를 반복 입력하지 않을 때 사용.)
"""

from __future__ import annotations

import re
from typing import Dict, Optional

# False — 아래 ``WAFER_PRIM_BY_SLOT_KEY`` (실무 USD). True — 테스트 stage ``/wafer_XX`` 트리.
IS_TEST: bool = False

# 슬롯 142: /LAM_WaferPosition_v01/LAM_WaferPosition_v01/... (World 없음)
# 팔 끝 3 (LOGICAL:*): /World/atm|vtm/.../LAM_*_Robot_v01/LAM_*_Robot_v01/...
WAFER_PRIM_PATH_PREFIX: str = ""

LOGICAL_SLOT_ATM_ARM: str = "LOGICAL:ATM_ARM"
LOGICAL_SLOT_VTM_EE_L: str = "LOGICAL:VTM_EE_L"
LOGICAL_SLOT_VTM_EE_R: str = "LOGICAL:VTM_EE_R"


def _apply_prefix(path: str) -> str:
    p = (path or "").strip()
    if not p:
        return ""
    prefix = (WAFER_PRIM_PATH_PREFIX or "").strip()
    if not prefix:
        return p
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    if not prefix.endswith("/"):
        prefix = prefix + "/"
    if p.startswith("/"):
        return p
    return prefix + p.lstrip("/")


# ---------------------------------------------------------------------------
# 수동 매핑 (142 물리 슬롯 + 3 논리 슬롯) — 값만 직접 수정
# ---------------------------------------------------------------------------
WAFER_PRIM_BY_SLOT_KEY: Dict[str, str] = {
    # --- FOUP 1 (foup1_1 .. foup1_25) ---
    "foup1_1": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_01",
    "foup1_2": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_02",
    "foup1_3": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_03",
    "foup1_4": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_04",
    "foup1_5": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_05",
    "foup1_6": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_06",
    "foup1_7": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_07",
    "foup1_8": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_08",
    "foup1_9": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_09",
    "foup1_10": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_10",
    "foup1_11": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_11",
    "foup1_12": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_12",
    "foup1_13": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_13",
    "foup1_14": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_14",
    "foup1_15": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_15",
    "foup1_16": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_16",
    "foup1_17": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_17",
    "foup1_18": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_18",
    "foup1_19": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_19",
    "foup1_20": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_20",
    "foup1_21": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_21",
    "foup1_22": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_22",
    "foup1_23": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_23",
    "foup1_24": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_24",
    "foup1_25": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_01_Wafer_Pos/Foup_01_Wafer_25",
    # --- FOUP 2 (foup2_1 .. foup2_25) ---
    "foup2_1": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_01",
    "foup2_2": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_02",
    "foup2_3": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_03",
    "foup2_4": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_04",
    "foup2_5": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_05",
    "foup2_6": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_06",
    "foup2_7": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_07",
    "foup2_8": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_08",
    "foup2_9": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_09",
    "foup2_10": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_10",
    "foup2_11": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_11",
    "foup2_12": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_12",
    "foup2_13": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_13",
    "foup2_14": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_14",
    "foup2_15": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_15",
    "foup2_16": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_16",
    "foup2_17": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_17",
    "foup2_18": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_18",
    "foup2_19": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_19",
    "foup2_20": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_20",
    "foup2_21": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_21",
    "foup2_22": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_22",
    "foup2_23": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_23",
    "foup2_24": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_24",
    "foup2_25": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_02_Wafer_Pos/Foup_02_Wafer_25",
    # --- FOUP 3 (foup3_1 .. foup3_25) ---
    "foup3_1": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_01",
    "foup3_2": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_02",
    "foup3_3": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_03",
    "foup3_4": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_04",
    "foup3_5": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_05",
    "foup3_6": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_06",
    "foup3_7": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_07",
    "foup3_8": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_08",
    "foup3_9": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_09",
    "foup3_10": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_10",
    "foup3_11": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_11",
    "foup3_12": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_12",
    "foup3_13": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_13",
    "foup3_14": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_14",
    "foup3_15": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_15",
    "foup3_16": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_16",
    "foup3_17": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_17",
    "foup3_18": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_18",
    "foup3_19": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_19",
    "foup3_20": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_20",
    "foup3_21": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_21",
    "foup3_22": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_22",
    "foup3_23": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_23",
    "foup3_24": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_24",
    "foup3_25": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/FOUP_03_Wafer_Pos/Foup_03_Wafer_25",
    # --- Buffer 3 (buffer3_1 .. buffer3_25) ---
    "buffer3_1": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_01",
    "buffer3_2": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_02",
    "buffer3_3": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_03",
    "buffer3_4": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_04",
    "buffer3_5": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_05",
    "buffer3_6": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_06",
    "buffer3_7": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_07",
    "buffer3_8": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_08",
    "buffer3_9": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_09",
    "buffer3_10": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_10",
    "buffer3_11": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_11",
    "buffer3_12": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_12",
    "buffer3_13": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_13",
    "buffer3_14": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_14",
    "buffer3_15": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_15",
    "buffer3_16": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_16",
    "buffer3_17": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_17",
    "buffer3_18": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_18",
    "buffer3_19": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_19",
    "buffer3_20": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_20",
    "buffer3_21": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_21",
    "buffer3_22": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_22",
    "buffer3_23": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_23",
    "buffer3_24": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_24",
    "buffer3_25": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_3_Wafer_Pos/Buffer_3_Wafer_25",
    # --- Buffer 4 (buffer4_1 .. buffer4_25) ---
    "buffer4_1": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_01",
    "buffer4_2": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_02",
    "buffer4_3": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_03",
    "buffer4_4": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_04",
    "buffer4_5": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_05",
    "buffer4_6": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_06",
    "buffer4_7": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_07",
    "buffer4_8": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_08",
    "buffer4_9": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_09",
    "buffer4_10": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_10",
    "buffer4_11": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_11",
    "buffer4_12": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_12",
    "buffer4_13": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_13",
    "buffer4_14": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_14",
    "buffer4_15": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_15",
    "buffer4_16": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_16",
    "buffer4_17": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_17",
    "buffer4_18": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_18",
    "buffer4_19": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_19",
    "buffer4_20": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_20",
    "buffer4_21": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_21",
    "buffer4_22": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_22",
    "buffer4_23": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_23",
    "buffer4_24": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_24",
    "buffer4_25": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Buffer_4_Wafer_Pos/Buffer_4_Wafer_25",
    # --- Cooling (cooling_1 .. cooling_7) ---
    "cooling_1": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Cooling_Wafer_Pos/Cooling_Wafer_01",
    "cooling_2": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Cooling_Wafer_Pos/Cooling_Wafer_02",
    "cooling_3": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Cooling_Wafer_Pos/Cooling_Wafer_03",
    "cooling_4": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Cooling_Wafer_Pos/Cooling_Wafer_04",
    "cooling_5": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Cooling_Wafer_Pos/Cooling_Wafer_05",
    "cooling_6": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Cooling_Wafer_Pos/Cooling_Wafer_06",
    "cooling_7": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Cooling_Wafer_Pos/Cooling_Wafer_07",
    # --- Aligner ---
    "aligner": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/Aligner_Wafer_Pos/Aligner_Wafer",
    # --- Airlock ---
    "airlock1_1": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/AirLock_01_Wafer_Pos/AirLock_01_Wafer_01",
    "airlock1_2": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/AirLock_01_Wafer_Pos/AirLock_01_Wafer_02",
    "airlock2_1": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/AirLock_02_Wafer_Pos/AirLock_02_Wafer_01",
    "airlock2_2": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer/AirLock_02_Wafer_Pos/AirLock_02_Wafer_02",
    # --- Chamber 1-4, Stripper (chamber5) ---
    "chamber1": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/TransferModuleWafer/Chamber_01_Wafer_Pos",
    "chamber2": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/TransferModuleWafer/Chamber_02_Wafer_Pos",
    "chamber3": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/TransferModuleWafer/Chamber_03_Wafer_Pos",
    "chamber4": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/TransferModuleWafer/Chamber_04_Wafer_Pos",
    "chamber5": "/LAM_WaferPosition_v01/LAM_WaferPosition_v01/WaferPosition_Root/TransferModuleWafer/Stripper_Wafer_Pos",
    # --- 팔 끝 (논리 슬롯) ---
    "LOGICAL:ATM_ARM": (
        "/World/atm/LAM_ATM_Robot_v01/LAM_ATM_Robot_v01/ATM_Robot_Root/ATM_Body_CTL/ATM_Cylinder_CTL/"
        "ATM_Arm_01_CTL/ATM_Arm_02_CTL/ATM_EndEffector_CTL/ATM_EndEffector_Wafer_Pos/"
        "ATM_EndEffector_Wafer"
    ),
    "LOGICAL:VTM_EE_L": (
        "/World/vtm/LAM_VTM_Robot_v01/LAM_VTM_Robot_v01/VTM_Robot_Root/VTM_Cylinder_Rotation_CTL/"
        "VTM_Cylinder_Postion_CTL/VTM_Arm_01_CTL/VTM_Arm_02_CTL/VTM_EndEffector_CTL/"
        "VTM_EndEffector_A_Wafer_Pos/VTM_EndEffector_A_Wafer"
    ),
    "LOGICAL:VTM_EE_R": (
        "/World/vtm/LAM_VTM_Robot_v01/LAM_VTM_Robot_v01/VTM_Robot_Root/VTM_Cylinder_Rotation_CTL/"
        "VTM_Cylinder_Postion_CTL/VTM_Arm_01_CTL/VTM_Arm_02_CTL/VTM_EndEffector_CTL/"
        "VTM_EndEffector_B_Wafer_Pos/VTM_EndEffector_B_Wafer"
    ),
}
# --- (참고) 이전 자동 조립 코드 — 사용 안 함 ---
# _ROOT_LOAD_PORT = "LAM_WaferPosition_v01/WaferPosition_Root/LoadPortWafer"
# _ROOT_TRANSFER = "LAM_WaferPosition_v01/WaferPosition_Root/TransferModuleWafer"
#
# def _default_slot_paths() -> Dict[str, str]:
#     lp = _ROOT_LOAD_PORT
#     tm = _ROOT_TRANSFER
#     p: Dict[str, str] = {}
#     for i in range(1, 26):
#         p[f"foup1_{i}"] = f"{lp}/FOUP_01_Wafer_Pos/Foup_01_Wafer_{i:02d}"
#     ... (for 루프로 foup2/3, buffer, cooling, airlock, chamber 자동 생성)
#     return p


def _test_wafer_path_for_slot_key(slot_key: str) -> Optional[str]:
    """테스트 USD — ``foup{N}_k`` → ``/wafer_NN/_kk`` (예: ``foup1_1`` → ``/wafer_01/_01``)."""
    m = re.match(r"^foup(\d+)_(\d+)$", (slot_key or "").strip())
    if not m:
        return None
    foup_n = int(m.group(1))
    slot_i = int(m.group(2))
    return f"/wafer_{foup_n:02d}/_{slot_i:02d}"


def load_wafer_prim_by_slot_key() -> Dict[str, str]:
    """slot_key → prim path 맵 복사본 (``WAFER_PRIM_PATH_PREFIX`` 가 있으면 일괄 접두)."""
    out = dict(WAFER_PRIM_BY_SLOT_KEY)
    if IS_TEST:
        for k in list(out.keys()):
            test_p = _test_wafer_path_for_slot_key(k)
            if test_p is not None:
                out[k] = test_p
    if WAFER_PRIM_PATH_PREFIX:
        return {k: _apply_prefix(v) for k, v in out.items()}
    return out


__all__ = [
    "IS_TEST",
    "WAFER_PRIM_PATH_PREFIX",
    "LOGICAL_SLOT_ATM_ARM",
    "LOGICAL_SLOT_VTM_EE_L",
    "LOGICAL_SLOT_VTM_EE_R",
    "WAFER_PRIM_BY_SLOT_KEY",
    "load_wafer_prim_by_slot_key",
]
