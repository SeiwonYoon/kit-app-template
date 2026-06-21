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
STARTUP_CHECK_PLAY_PRIM_HIDE: bool = True  # prim숨김 (Viewport HUD · CSV 본창)
STARTUP_CHECK_PLAY_CAMERA_FLY: bool = True  # Play 시 preset 뷰로 fly (일시정지 이어서 제외)


# ---------------------------------------------------------------------------
# CSV Play 시작 시 카메라 fly-to (구현: lam_play_camera_fly.py)
# 「뷰 저장」버튼으로 콘솔에 출력된 블록을 eye_xyz / target_xyz 에 붙여넣기.
# ---------------------------------------------------------------------------
PLAY_CAMERA_PRESET_ENABLED: bool = True
PLAY_CAMERA_FLY_DURATION_SEC: float = 2
PLAY_CAMERA_FLY_POSITION_EPS_M: float = 0.05
PLAY_CAMERA_FLY_DIRECTION_EPS_DEG: float = 1.0

# Play 시작 단계 사이 대기 [s] — 기준 시각 기준으로 다음 단계 시작
# 카메라→prim: (카메라 fly 끝) + PLAY_DELAY_CAMERA_TO_PRIM_HIDE_SEC
#   예) fly 2s, delay -2 → 동시 시작 / delay 0 → fly 직후 / delay +2 → fly 후 2s
# prim→CSV: (prim 숨김 끝) + PLAY_DELAY_PRIM_HIDE_TO_PLAY_SEC (음수면 fade 중 재생 시작)
PLAY_DELAY_CAMERA_TO_PRIM_HIDE_SEC: float = -2
PLAY_DELAY_PRIM_HIDE_TO_PLAY_SEC: float = 2


@dataclass(frozen=True)
class PlayCameraPresetSpec:
    """Play 시작 시 이동할 뷰 (월드 좌표, 미터)."""

    eye_xyz: Tuple[float, float, float]
    target_xyz: Tuple[float, float, float]
    up_xyz: Tuple[float, float, float] = (0.0, 0.0, 1.0)


# 캡처 후 붙여넣기 — PRESET_ENABLED = True 로 바꿀 것
PLAY_CAMERA_PRESET: PlayCameraPresetSpec = PlayCameraPresetSpec(
    eye_xyz=(5237.438457, 7949.290852, 7035.098296),
    target_xyz=(-1503.170796, 1208.681599, 294.489204),
)


# ---------------------------------------------------------------------------
# 웨이퍼 3D 번호 (Viewport 「웨이퍼번호보기」 체크 ON 일 때)
# ---------------------------------------------------------------------------
# FOUP1~3 × 25슬롯(총 75) prim 위 번호 라벨 표시 여부.
# False(기본): FOUP 슬롯 번호는 숨기고, 팔·aligner·chamber 등 나머지 슬롯만 표시.
# True: FOUP 75슬롯에도 카세트 번호(01~25) 표시.
WAFER_LABEL_SHOW_FOUP_SLOT_NUMBERS: bool = False


# ---------------------------------------------------------------------------
# CSV Play 시 prim 숨김/보임 (경로·fade 는 이 섹션만 수정)
# Play 시작 · 정지(초기화) · 「prim숨김」체크박스 — 구현: lam_play_prim_hide.py
# ---------------------------------------------------------------------------

# 정지(초기화) 클릭 시 숨겨 두었던 prim 을 다시 보이게 할지
# (「prim숨김」 체크 ON 이면 런타임에서 항상 복원 생략 — hidden 유지)
PLAY_HIDE_RESTORE_VISIBLE_ON_STOP_RESET: bool = True

# 전역 fade (항목별 fade_* 가 None 이면 아래 값 사용)
# Play 시작(play_start) fade: MDL opacity_constant 있으면 RTX 투명 보간,
# 없으면 하위 Gprim 순차 hide(progressive, 46개 등 CAD mesh).
# 체크박스(ui_hide/ui_show) 는 항상 즉시.
PLAY_HIDE_FADE_ENABLED: bool = False
PLAY_HIDE_FADE_DURATION_SEC: float = 0.35
# hide 시 1→0, show 시 0→1 (항목별 override 가능)
PLAY_HIDE_FADE_HIDE_IN: bool = True
PLAY_HIDE_FADE_SHOW_IN: bool = True


@dataclass(frozen=True)
class PlayHidePrimSpec:
    """CSV Play 시 숨길(또는 체크박스로 토글할) prim 한 항목.

    - ``prim_path``: stage 절대 경로 (존재하는 prim 만 적용).
    - ``fade_*``: None 이면 전역 ``PLAY_HIDE_FADE_*`` 사용.
    """

    prim_path: str
    fade_enabled: bool | None = None
    fade_duration_sec: float | None = None
    fade_hide_in: bool | None = None  # 숨길 때 fade in (불투명→투명)
    fade_show_in: bool | None = None  # 보일 때 fade in (투명→불투명)


# 사용자가 여기 리스트에 경로 추가/삭제 (DeviceLabelSpec 과 동일 패턴)
PLAY_HIDE_PRIM_SPECS: List[PlayHidePrimSpec] = [
    PlayHidePrimSpec(
        prim_path="/World/aaa/N_07_Laser_Cutting/_7_Laser_Cutting_Machine/base_link/visual/Geometry/tn__07TL14_0428_kGXkp7c4WYV2ss8XbAac0xoV4lMimv0ohEmjN_0/TL14_1000_A00______________1030/TL14_1001_000___________1262",
        fade_enabled=True,
        fade_duration_sec=2,
    ),
]

# 재생/체크박스에서 "무조건 보이게" 유지할 prim 목록.
# - PLAY_HIDE_PRIM_SPECS 와 동시에 적용된다.
# - 동일 prim 이 둘 다에 들어가면, 최종 결과는 "보임(Show 우선)"으로 처리된다.
# - fade 는 사용하지 않고 즉시 visible 만 강제한다. (lam_play_prim_hide.py)
PLAY_SHOW_PRIM_SPECS: List[PlayHidePrimSpec] = [
    # 예시:
    PlayHidePrimSpec(prim_path="/World/aaa/N_07_Laser_Cutting/_7_Laser_Cutting_Machine/base_link/visual/Geometry/tn__07TL14_0428_kGXkp7c4WYV2ss8XbAac0xoV4lMimv0ohEmjN_0/TL14_1000_A00______________1030/TL14_1003_000___________1084/TL14_1003_r06___________1085"),
]


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
STATUS_PANEL_WIDTH_PX: int = 350
STATUS_PANEL_LABEL_COL_WIDTH_PX: int = 120
STATUS_PANEL_PADDING_PX: int = 10
STATUS_PANEL_TITLE_FONT_SIZE: int = 14
STATUS_PANEL_ROW_FONT_SIZE: int = 16  # 행별 label/value 미지정 시 공통 기본값
STATUS_PANEL_ROW_HEIGHT_PX: int = 26
STATUS_PANEL_STATE_ROW_HEIGHT_PX: int = 72

STATUS_PANEL_BG_COLOR_HEX: int = 0xE6181C22
STATUS_PANEL_BORDER_COLOR_HEX: int = 0xFF5A6A80
STATUS_PANEL_ROW_BG_HEX: int = 0xE6181C22  # 연한 행 배경(투명도 포함)
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

    레이아웃:
    - ``height_px``: 행 높이 [px]
    - ``label_font_size`` / ``value_font_size``: 좌측(이름)·우측(값) 폰트 [px] (기본 ``STATUS_PANEL_ROW_FONT_SIZE``)
    """

    key: str
    name: str
    value: str
    height_px: int = 26
    label_font_size: int = STATUS_PANEL_ROW_FONT_SIZE  # 좌측(이름) 컬럼 [px]
    value_font_size: int = STATUS_PANEL_ROW_FONT_SIZE  # 우측(값) 컬럼 [px]


# 사용자가 여기 리스트만 수정하면 행 추가/삭제/순서 변경 가능
STATUS_PANEL_ROWS: List[StatusRowSpec] = [
    # 수동 입력(고정 텍스트) 예시
    StatusRowSpec(
        key="eq_model",
        name="EQ MODEL",
        value="KIYO_FXE",
        height_px=STATUS_PANEL_ROW_HEIGHT_PX,
        label_font_size=14,   # 좌측만 작게
        value_font_size=14,
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
FOUP_PANEL_OFFSET_XYZ_M: Tuple[float, float, float] = (-20, -50, 0.10)

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

# 항목별로 override 가능. 기본 배경/테두리는 FOUP 3D 패널과 동일.
DEVICE_LABEL_DEFAULT_BG_RGBA: Tuple[float, float, float, float] = FOUP_PANEL_BG_RGBA
DEVICE_LABEL_DEFAULT_BORDER_RGBA: Tuple[float, float, float, float] = FOUP_PANEL_BORDER_RGBA
# padding (가로, 세로) [px] — 패널 크기 = 글자 추정 + padding
DEVICE_LABEL_DEFAULT_PADDING_PX: Tuple[int, int] = (10, 6)
# 글자 폭 추정 후 전체에 곱하는 여유 배율 + 추가 px (sc.Label 실측보다 넉넉히)
DEVICE_LABEL_CHAR_WIDTH_FACTOR: float = 1.05
DEVICE_LABEL_WIDTH_SLACK_PX: int = 12


@dataclass(frozen=True)
class DeviceLabelSpec:
    """기기 3D 라벨 한 항목.

    - ``bg_rgba`` / ``border_rgba``: FOUP 패널과 동일 형식 (0~1 float RGBA).
    - ``padding_px``: (가로, 세로) [px]. 패널 크기는 글자 길이 + padding 으로 자동.
    - ``show_border``: FOUP 와 같이 wireframe 테두리 표시.
    """

    name: str
    prim_path: str
    offset_xyz_m: Tuple[float, float, float] = (0.0, 0.0, 0.10)
    color_rgba: Tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    font_size: int = 16
    bg_rgba: Tuple[float, float, float, float] = DEVICE_LABEL_DEFAULT_BG_RGBA
    border_rgba: Tuple[float, float, float, float] = DEVICE_LABEL_DEFAULT_BORDER_RGBA
    padding_px: Tuple[int, int] = DEVICE_LABEL_DEFAULT_PADDING_PX
    show_border: bool = True


# v1: CoolStation 1개만 채우고 나머지는 사용자가 추가
DEVICE_LABEL_SPECS: List[DeviceLabelSpec] = [
    DeviceLabelSpec(
        name="CoolStation",
        # prim_path="/LAM_Machanical_v01/LAM_Machanical_v01/MechanicalEquipment_Root/MechanicalEquipment/LoadPort_Root/LoadPort/Cooling_Station",
        prim_path="/World/aaa/N_07_Laser_Cutting/_7_Laser_Cutting_Machine/base_link/visual/Geometry/tn__07TL14_0428_kGXkp7c4WYV2ss8XbAac0xoV4lMimv0ohEmjN_0/TL14_1000_A00______________1030/TL14_1001_000___________1262/TL14_1001_r11___________SSA4001A_04_1319",
        offset_xyz_m=(-0.20, 0.0, 0.15),
        color_rgba=(1.0, 1.0, 1.0, 1.0),
        font_size=16,
        bg_rgba=(0.10, 0.12, 0.15, 0.75),   # 생략 시 FOUP와 동일
        padding_px=(12, 8),               # 항목별 padding
        show_border=True,
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
# Kit 시작 시 Viewport 오빗 pivot — prim 선택 없이 COI(회전 중심)만 설정
# 카메라 eye/줌은 유지. 구현: lam_viewport_startup_focus.py
# enabled=False 이거나 prim_path 가 비어 있으면 적용하지 않음 (기존 Kit 동작 유지).
# ---------------------------------------------------------------------------
STARTUP_VIEWPORT_FOCUS_PRIM_ENABLED: bool = True
STARTUP_VIEWPORT_FOCUS_PRIM_PATH: str = "/World/aaa"


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
    "STARTUP_CHECK_PLAY_PRIM_HIDE",
    "STARTUP_CHECK_PLAY_CAMERA_FLY",
    "PLAY_CAMERA_PRESET_ENABLED",
    "PLAY_CAMERA_FLY_DURATION_SEC",
    "PLAY_CAMERA_FLY_POSITION_EPS_M",
    "PLAY_CAMERA_FLY_DIRECTION_EPS_DEG",
    "PLAY_DELAY_CAMERA_TO_PRIM_HIDE_SEC",
    "PLAY_DELAY_PRIM_HIDE_TO_PLAY_SEC",
    "PlayCameraPresetSpec",
    "PLAY_CAMERA_PRESET",
    "PLAY_HIDE_RESTORE_VISIBLE_ON_STOP_RESET",
    "PLAY_HIDE_FADE_ENABLED",
    "PLAY_HIDE_FADE_DURATION_SEC",
    "PLAY_HIDE_FADE_HIDE_IN",
    "PLAY_HIDE_FADE_SHOW_IN",
    "PlayHidePrimSpec",
    "PLAY_HIDE_PRIM_SPECS",
    "WAFER_LABEL_SHOW_FOUP_SLOT_NUMBERS",
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
    "DEVICE_LABEL_DEFAULT_BG_RGBA",
    "DEVICE_LABEL_DEFAULT_BORDER_RGBA",
    "DEVICE_LABEL_DEFAULT_PADDING_PX",
    "DEVICE_LABEL_CHAR_WIDTH_FACTOR",
    "DEVICE_LABEL_WIDTH_SLACK_PX",
    "DeviceLabelSpec",
    "DEVICE_LABEL_SPECS",
    "VIEWPORT_PICK_WHITELIST_ROOTS",
    "STARTUP_VIEWPORT_FOCUS_PRIM_ENABLED",
    "STARTUP_VIEWPORT_FOCUS_PRIM_PATH",
]
