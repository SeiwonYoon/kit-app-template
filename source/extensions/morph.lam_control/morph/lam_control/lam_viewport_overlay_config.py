"""LAM Viewport overlay 설정 SSOT (v1).

사용자 워크플로:
- 경로/라벨/오프셋/색/크기/컬럼명 매핑 등을 **이 파일 하나**에서 수정한다.
- 2D/3D 패널들은 이 모듈만 참조하고, 재생 로직은 건드리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


# ---------------------------------------------------------------------------
# 앱 시작 시 체크박스 기본 선택 (True = 체크됨)
# CSV 시뮬 재생창 · Viewport HUD 공통
# ---------------------------------------------------------------------------
STARTUP_CHECK_PROCESS_ONLY: bool = False  # 공정만보기
STARTUP_CHECK_WAFER_LABELS: bool = False  # 웨이퍼번호보기
STARTUP_CHECK_FOUP_STATUS: bool = True  # FOUP상태보기
STARTUP_CHECK_DEVICE_LABELS: bool = True  # 기기정보보기
STARTUP_CHECK_PICK_WHITELIST: bool = False  # 선택제한 (Viewport 클릭 whitelist)


# ---------------------------------------------------------------------------
# CSV 컬럼명 매핑 (향후 컬럼 변경 대비)
# ---------------------------------------------------------------------------
CSV_COL_EQP_ID: str = "eqp_id"
CSV_COL_LOT_ID: str = "lot_id"
CSV_COL_CASSETTE_SLOT: str = "cassette_slot"
CSV_COL_MODULE_NM: str = "module_nm"


# ---------------------------------------------------------------------------
# 기능 #1: 2D 상태 패널 기본값
# ---------------------------------------------------------------------------
STATUS_PANEL_TITLE: str = "STATUS"

# 패널 레이아웃/스타일(사용자가 여기서 조정)
STATUS_PANEL_WIDTH_PX: int = 300
STATUS_PANEL_LABEL_COL_WIDTH_PX: int = 120
STATUS_PANEL_PADDING_PX: int = 10
STATUS_PANEL_TITLE_FONT_SIZE: int = 14
STATUS_PANEL_ROW_FONT_SIZE: int = 13
STATUS_PANEL_ROW_HEIGHT_PX: int = 26
STATUS_PANEL_STATE_ROW_HEIGHT_PX: int = 72

STATUS_PANEL_BG_COLOR_HEX: int = 0xE6181C22
STATUS_PANEL_BORDER_COLOR_HEX: int = 0xFF5A6A80
STATUS_PANEL_ROW_BG_HEX: int = 0x1AFFFFFF  # 연한 행 배경(투명도 포함)
STATUS_PANEL_TEXT_COLOR_HEX: int = 0xffffffff
# STATUS_PANEL_TEXT_COLOR_HEX: int = 0xff000000
STATUS_PANEL_LABEL_COLOR_HEX: int = 0xFFB8C0CC

# EQ MODEL 은 v1에서 “수동값(고정 텍스트)”로 시작. 사용자는 여기 값을 수정.
STATUS_PANEL_EQ_MODEL_VALUE: str = "KIYO_FXE"

# Current State 한 줄: 웨이퍼# · lot_id · JSON이벤트명(확장자 없음)
STATUS_PANEL_STATE_SEP: str = " · "


@dataclass(frozen=True)
class StatusRowSpec:
    """2D 상태 패널 한 행 정의(DEVICE_LABEL_SPECS와 동일한 패턴).

    value 는 고정 텍스트 또는 템플릿 문자열.

    규칙(v1):
    - 중괄호 토큰이 없으면 **그대로 표시**(수동 입력).
      예: "KIYO_FXE"
    - "{컬럼명}" 형태면, CSV의 "현재 재생행(없으면 최초행)"에서 해당 컬럼 값을 찾아 표시.
      예: "{eqp_id}", "{lot_id}", "{cassette_slot}", "{module_nm}"
    - 예약 토큰(대소문자 무시):
      - "{time}": ``재생 0.9% | t 15.1/1773.7s | 실경과 16s/1774s`` (스냅샷·배속 기준)
      - "{state}": ``웨이퍼#N · lot_id · event_json`` (JSON은 이름만, dwell 시 JSON 생략) — 일시정지/dwell 시 마지막 값 유지
      - "{eq_model}": `STATUS_PANEL_EQ_MODEL_VALUE` (기본 수동값; 필요 시 사용)
    """

    key: str
    name: str
    value: str
    height_px: int = 26


# 사용자가 여기 리스트만 수정하면 행 추가/삭제/순서 변경 가능
STATUS_PANEL_ROWS: List[StatusRowSpec] = [
    # 수동 입력(고정 텍스트) 예시
    StatusRowSpec(
        key="eq_model",
        name="EQ MODEL",
        value="KIYO_FXE",
        height_px=STATUS_PANEL_ROW_HEIGHT_PX,
    ),
    # CSV 컬럼 매핑 예시
    StatusRowSpec(
        key="eqp_id",
        name="EQP ID",
        value="{eqp_id}",
        height_px=STATUS_PANEL_ROW_HEIGHT_PX,
    ),
    # wafer 번호 매핑 예시
    StatusRowSpec(
        key="cassette_slot",
        name="wafer 번호",
        value="{cassette_slot}",
        height_px=STATUS_PANEL_ROW_HEIGHT_PX,
    ),
    # 예약 토큰 예시
    StatusRowSpec(
        key="time",
        name="Time",
        value="{time}",
        height_px=STATUS_PANEL_ROW_HEIGHT_PX,
    ),
    StatusRowSpec(
        key="state",
        name="Current State",
        value="{state}",
        height_px=STATUS_PANEL_STATE_ROW_HEIGHT_PX,
    ),
]


# ---------------------------------------------------------------------------
# 기능 #2: FOUP 진행상황 3D 패널 앵커 prim (확정)
# ---------------------------------------------------------------------------
FOUP_ANCHOR_PRIM_BY_INDEX: Dict[int, str] = {
    # 1: "/LAM_Foup_v01/LAM_Foup_v01/Foup_Root/Foup_01/Foup_01_Body",
    1: "/World/aaa/N_07_Laser_Cutting/_7_Laser_Cutting_Machine/base_link/visual/Geometry/tn__07TL14_0428_kGXkp7c4WYV2ss8XbAac0xoV4lMimv0ohEmjN_0/TL14_1000_A00______________1030/TL14_1003_000___________1084/TL14_1003_r07__________B_1114/O6_0mm________1115/Mesh_583",
    2: "/LAM_Foup_v01/LAM_Foup_v01/Foup_Root/Foup_02/Foup_02_Body",
    3: "/LAM_Foup_v01/LAM_Foup_v01/Foup_Root/Foup_03/Foup_03_Body",
}

# FOUP 패널 위치 오프셋(객체 중심 기준) — 초기값은 임시. 실제로 보며 조정.
FOUP_PANEL_OFFSET_XYZ_M: Tuple[float, float, float] = (300, 0.0, 0.10)

# FOUP 3D 패널 스타일(표 형태) — 줄간격/배경 크기/글자 크기 등은 여기서 조정
FOUP_PANEL_WIDTH_PX: int = 220
FOUP_PANEL_HEIGHT_PX: int = 94
FOUP_PANEL_LINE_HEIGHT_PX: int = 26
FOUP_PANEL_FONT_SIZE: int = 15
FOUP_PANEL_BG_RGBA: Tuple[float, float, float, float] = (0.10, 0.12, 0.15, 0.75)
FOUP_PANEL_BORDER_RGBA: Tuple[float, float, float, float] = (0.45, 0.55, 0.70, 0.90)


# ---------------------------------------------------------------------------
# 기능 #3: 기기정보보기 라벨 정의
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceLabelSpec:
    name: str
    prim_path: str
    offset_xyz_m: Tuple[float, float, float] = (0.0, 0.0, 0.10)
    color_rgba: Tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    font_size: int = 16


# v1: CoolStation 1개만 채우고 나머지는 사용자가 추가
DEVICE_LABEL_SPECS: List[DeviceLabelSpec] = [
    DeviceLabelSpec(
        name="CoolStation",
        # prim_path="/LAM_Machanical_v01/LAM_Machanical_v01/MechanicalEquipment_Root/MechanicalEquipment/LoadPort_Root/LoadPort/Cooling_Station",
        prim_path="/World/aaa/N_07_Laser_Cutting/_7_Laser_Cutting_Machine/base_link/visual/Geometry/tn__07TL14_0428_kGXkp7c4WYV2ss8XbAac0xoV4lMimv0ohEmjN_0/TL14_1000_A00______________1030/TL14_1001_000___________1262/TL14_1001_r11___________SSA4001A_04_1319",
        offset_xyz_m=(-0.20, 0.0, 0.15),
        color_rgba=(1.0, 1.0, 1.0, 1.0),
        font_size=16,
    ),
    DeviceLabelSpec(
        name="Chamber1",
        # prim_path="/LAM_Machanical_v01/LAM_Machanical_v01/MechanicalEquipment_Root/MechanicalEquipment/LoadPort_Root/LoadPort/Cooling_Station",
        prim_path="/World/aaa_1/N_07_Laser_Cutting/_7_Laser_Cutting_Machine/base_link/visual/Geometry/tn__07TL14_0428_kGXkp7c4WYV2ss8XbAac0xoV4lMimv0ohEmjN_0/TL14_1000_A00______________1030/TL14_1003_000___________1084",
        offset_xyz_m=(-0.20, 0.0, 0.15),
        color_rgba=(1.0, 1.0, 1.0, 1.0),
        font_size=16,
    )
]


# ---------------------------------------------------------------------------
# 기능 #4: Viewport 선택 제한(화이트리스트)
# ---------------------------------------------------------------------------

# 사용자가 허용할 루트 prim 경로들. 하위 클릭 시 루트로 선택 치환.
VIEWPORT_PICK_WHITELIST_ROOTS: List[str] = [
    # 예시:
    # "/World",
]


__all__ = [
    "STARTUP_CHECK_PROCESS_ONLY",
    "STARTUP_CHECK_WAFER_LABELS",
    "STARTUP_CHECK_FOUP_STATUS",
    "STARTUP_CHECK_DEVICE_LABELS",
    "STARTUP_CHECK_PICK_WHITELIST",
    "CSV_COL_EQP_ID",
    "CSV_COL_LOT_ID",
    "CSV_COL_CASSETTE_SLOT",
    "CSV_COL_MODULE_NM",
    "STATUS_PANEL_TITLE",
    "STATUS_PANEL_WIDTH_PX",
    "STATUS_PANEL_LABEL_COL_WIDTH_PX",
    "STATUS_PANEL_PADDING_PX",
    "STATUS_PANEL_TITLE_FONT_SIZE",
    "STATUS_PANEL_ROW_FONT_SIZE",
    "STATUS_PANEL_ROW_HEIGHT_PX",
    "STATUS_PANEL_STATE_ROW_HEIGHT_PX",
    "STATUS_PANEL_BG_COLOR_HEX",
    "STATUS_PANEL_BORDER_COLOR_HEX",
    "STATUS_PANEL_ROW_BG_HEX",
    "STATUS_PANEL_TEXT_COLOR_HEX",
    "STATUS_PANEL_LABEL_COLOR_HEX",
    "STATUS_PANEL_EQ_MODEL_VALUE",
    "StatusRowSpec",
    "STATUS_PANEL_ROWS",
    "FOUP_ANCHOR_PRIM_BY_INDEX",
    "FOUP_PANEL_OFFSET_XYZ_M",
    "FOUP_PANEL_WIDTH_PX",
    "FOUP_PANEL_HEIGHT_PX",
    "FOUP_PANEL_LINE_HEIGHT_PX",
    "FOUP_PANEL_FONT_SIZE",
    "FOUP_PANEL_BG_RGBA",
    "FOUP_PANEL_BORDER_RGBA",
    "DeviceLabelSpec",
    "DEVICE_LABEL_SPECS",
    "VIEWPORT_PICK_WHITELIST_ROOTS",
]
