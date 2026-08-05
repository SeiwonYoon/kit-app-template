"""LAM Viewport overlay 설정 SSOT (v1).

사용자 워크플로:
- 경로/라벨/오프셋/색/크기/컬럼명 매핑 등을 **이 파일 하나**에서 수정한다.
- 2D/3D 패널들은 이 모듈만 참조하고, 재생 로직은 건드리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 앱 시작 시 체크박스 기본 선택 (True = 체크됨)
# CSV 시뮬 재생창 · Viewport HUD 공통
# ---------------------------------------------------------------------------
STARTUP_CHECK_PROCESS_ONLY: bool = False  # 공정만보기
STARTUP_CHECK_WAFER_LABELS: bool = False  # 웨이퍼번호보기
STARTUP_CHECK_FOUP_STATUS: bool = False  # FOUP상태보기
STARTUP_CHECK_DEVICE_LABELS: bool = False  # 기기정보보기
STARTUP_CHECK_PICK_WHITELIST: bool = False  # 선택제한 (Viewport 클릭 whitelist)
STARTUP_CHECK_PLAY_PRIM_HIDE: bool = False  # prim숨김 (Viewport HUD · CSV 본창)
STARTUP_CHECK_PLAY_CAMERA_FLY: bool = True  # Play 시 preset 뷰로 fly (일시정지 이어서 제외)


# ---------------------------------------------------------------------------
# CSV Play 시작 시 카메라 fly-to (구현: lam_play_camera_fly.py)
# True(기본)=PLAY_CAMERA_PRESET 좌표 fly (현재와 동일)
# False=PLAY_CAMERA_PRIM_PATH USD Camera prim 뷰로 fly
# ---------------------------------------------------------------------------
PLAY_CAMERA_USE_PRESET_COORDS: bool = False
PLAY_CAMERA_PRIM_PATH: str = "/Camera_fly"  # stage 트리 기준 실제 경로로 수정
# Camera prim 모드(USE_PRESET_COORDS=False)에서는 fly 후 viewport 가 USD Camera 에 bind 됨.
# 아래 플래그는 레거시·문서용 (동작에는 영향 없음).
PLAY_CAMERA_BIND_VIEWPORT_TO_USD_PRIM: bool = True

PLAY_CAMERA_PRESET_ENABLED: bool = True
PLAY_CAMERA_FLY_DURATION_SEC: float = 2
PLAY_CAMERA_FLY_POSITION_EPS_M: float = 0.05
PLAY_CAMERA_FLY_DIRECTION_EPS_DEG: float = 1.0

# Play 시작 단계 사이 대기 [s] — 기준 시각 기준으로 다음 단계 시작
# 카메라→prim: (카메라 fly 끝) + PLAY_DELAY_CAMERA_TO_PRIM_HIDE_SEC
#   예) fly 2s, delay -2 → 동시 시작 / delay 0 → fly 직후 / delay +2 → fly 후 2s
# prim→CSV: (prim 숨김 끝) + PLAY_DELAY_PRIM_HIDE_TO_PLAY_SEC (음수면 fade 중 재생 시작)
PLAY_DELAY_CAMERA_TO_PRIM_HIDE_SEC: float = 0
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
# Camera prim 모드(USE_PRESET_COORDS=False) 뷰·줌 스펙 — preset 과 동일 워크플로.
# None = 저장 안 함 → 진입 시점의 USD Camera prim 현재 상태 그대로 사용 (기존 동작).
# 값 설정 = 진입할 때마다 prim 을 이 뷰(eye/target 거리 = 줌)로 강제 → 항상 일정.
# 「뷰저장」 버튼: prim 모드에서 누르면 아래 블록용 스니펫을 콘솔에 출력.
# ---------------------------------------------------------------------------
# PLAY_CAMERA_PRIM_VIEW: Optional[PlayCameraPresetSpec] = None
# 예) 캡처 후 붙여넣기:
PLAY_CAMERA_PRIM_VIEW = PlayCameraPresetSpec(
    eye_xyz=(299.167590, -1152.897089, 3570.668411),
    target_xyz=(65.168036, 106.145030, 1397.976655),
    up_xyz=(-0.157417, 0.846988, 0.507771),
)


# ---------------------------------------------------------------------------
# Viewport 「탑뷰 보기」 — preset 뷰 고정 + 카메라 조작 잠금 (lam_viewport_top_view.py)
# True(기본)=TOP_VIEW_PRESET 좌표 (현재와 동일)
# False=TOP_VIEW_CAMERA_PRIM_PATH USD Camera prim 뷰로 fly 후 고정
# ---------------------------------------------------------------------------
TOP_VIEW_USE_PRESET_COORDS: bool = False
TOP_VIEW_CAMERA_PRIM_PATH: str = "/Camera"  # stage 트리 기준 실제 경로로 수정
# Camera prim 모드(USE_PRESET_COORDS=False)에서는 fly 후 viewport 가 USD Camera 에 bind 됨.
TOP_VIEW_BIND_VIEWPORT_TO_USD_PRIM: bool = True

STARTUP_CHECK_TOP_VIEW: bool = False
TOP_VIEW_PRESET_ENABLED: bool = True
TOP_VIEW_PRESET: PlayCameraPresetSpec = PlayCameraPresetSpec(
    eye_xyz=(239.023316, 4175.874309, 12994.797994),
    target_xyz=(13.721364, 3544.061638, 2916.364276),
)

# Camera prim 모드(TOP_VIEW_USE_PRESET_COORDS=False) 뷰·줌 스펙 — Play 쪽과 동일 규칙.
# None = prim 현재 상태 사용 / 값 설정 = 탑뷰 진입 시마다 이 뷰·줌으로 강제.
TOP_VIEW_CAMERA_PRIM_VIEW: Optional[PlayCameraPresetSpec] = None
# 예) 캡처 후 붙여넣기:
TOP_VIEW_CAMERA_PRIM_VIEW = PlayCameraPresetSpec(
    eye_xyz=(0.000000, 0.000000, 4918.361498),
    target_xyz=(0.000000, 0.000000, 1343.485827),
    up_xyz=(1.000000, 0.000000, 0.000000),
)


# ---------------------------------------------------------------------------
# 화면 개수(1·2)별 카메라 preset — 카메라 모드(*_USE_PRESET_COORDS=False)에서만 사용.
# 현재 표시 중인 화면 수(화면1·화면2 체크 상태, 1 또는 2)에 따라 자동 선택된다.
# ---------------------------------------------------------------------------

# [Play 시작용 Perspective 뷰 — 화면 수별]
# 시뮬 시작: 현재 줌 상태와 무관하게 Perspective 를 이 뷰로 먼저 맞춘 뒤 fly 시작.
# 시뮬 정지: 이전 줌 복귀 대신 이 뷰로 복귀.
# None = 기존 동작(현재 뷰에서 fly 시작, 정지 시 이전 줌 복귀).
PLAY_CAMERA_START_VIEW_1_SCREEN: Optional[PlayCameraPresetSpec] = PlayCameraPresetSpec(
    eye_xyz=(2077.278376, 3283.121566, 3508.283719),
    target_xyz=(-878.949530, 326.893659, 552.055883),
    up_xyz=(-0.408248, -0.408248, 0.816497),
)
PLAY_CAMERA_START_VIEW_2_SCREEN: Optional[PlayCameraPresetSpec] = PlayCameraPresetSpec(
    eye_xyz=(1206.543447, 2215.959543, 2548.541316),
    target_xyz=(-783.804667, 225.611429, 558.193250),
    up_xyz=(-0.408248, -0.408248, 0.816497),
)

# [Play fly 종료 줌(aperture) — 화면 수별]
# fly: Perspective + START_VIEW → 목표 preset (동일 Perspective 모드에서 진행).
# aperture 값이 있으면 fly 중 Persp aperture 도 목표 Camera FOV 로 함께 보간.
# fly 종료 후 Camera_fly 모드로 전환하며 아래 aperture 를 Camera 에 적용.
# None = aperture 보간/변경 안 함.
PLAY_CAMERA_APERTURE_1_SCREEN: Optional[float] = 41
PLAY_CAMERA_APERTURE_2_SCREEN: Optional[float] = 41

# (레거시) fly 후 별도 aperture 블렌드 시간. 기본은 fly 중 Persp 보간을 쓰므로 보통 미사용.
PLAY_CAMERA_APERTURE_BLEND_SEC: float = 1.0

# [Play fly 목표 뷰 — 화면 수별]  None = 위 PLAY_CAMERA_PRIM_VIEW 사용.
PLAY_CAMERA_PRIM_VIEW_1_SCREEN: Optional[PlayCameraPresetSpec] = PlayCameraPresetSpec(
    eye_xyz=(2201.376655, 456.270208, 1159.314444),
    target_xyz=(620.354988, 456.270208, 1159.314444),
    up_xyz=(0.000000, 0.000000, 1.000000),
)
PLAY_CAMERA_PRIM_VIEW_2_SCREEN: Optional[PlayCameraPresetSpec] = PlayCameraPresetSpec(
    eye_xyz=(1151.255012, 412.297364, 1161.408388),
    target_xyz=(620.354988, 412.297364, 1161.408388),
    up_xyz=(0.000000, 0.000000, 1.000000),
)

# [탑뷰 뷰 — 화면 수별]  None = 위 TOP_VIEW_CAMERA_PRIM_VIEW 사용.
TOP_VIEW_CAMERA_PRIM_VIEW_1_SCREEN: Optional[PlayCameraPresetSpec] = PlayCameraPresetSpec(
    eye_xyz=(-463.828260, 557.906634, 3076.096106),
    target_xyz=(-463.828260, 557.906634, 1343.485827),
    up_xyz=(0.000000, 1.000000, 0.000000),
)
TOP_VIEW_CAMERA_PRIM_VIEW_2_SCREEN: Optional[PlayCameraPresetSpec] = PlayCameraPresetSpec(
    eye_xyz=(-463.828260, 557.906634, 3076.096106),
    target_xyz=(-463.828260, 557.906634, 1343.485827),
    up_xyz=(0.000000, 1.000000, 0.000000),
)

# [탑뷰 줌(aperture) — 화면 수별]
# 탑뷰 카메라는 줌 시 transform(x,y,z)이 아니라 horizontal/vertical aperture 가 변한다.
# 탑뷰 진입 시 두 aperture 를 아래 값으로 설정. None = 변경 안 함(현재 상태 유지).
TOP_VIEW_APERTURE_1_SCREEN: Optional[float] = 35.0
TOP_VIEW_APERTURE_2_SCREEN: Optional[float] = 20.0


# ---------------------------------------------------------------------------
# 웨이퍼 3D 번호 (Viewport 「웨이퍼번호보기」 체크 ON 일 때)
# ---------------------------------------------------------------------------
# FOUP1~3 × 25슬롯(총 75) prim 위 번호 라벨 표시 여부.
# False(기본): FOUP 슬롯 번호는 숨기고, 팔·aligner·chamber 등 나머지 슬롯만 표시.
# True: FOUP 75슬롯에도 카세트 번호(01~25) 표시.
WAFER_LABEL_SHOW_FOUP_SLOT_NUMBERS: bool = True


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
        prim_path="/World/aaa_1/N_07_Laser_Cutting/_7_Laser_Cutting_Machine/base_link/visual/Geometry/tn__07TL14_0428_kGXkp7c4WYV2ss8XbAac0xoV4lMimv0ohEmjN_0/TL14_1000_A00______________1030/TL14_1003_000___________1084/TL14_1003_r07__________B_1114",
        fade_enabled=True,
        fade_duration_sec=2,
    ),
    PlayHidePrimSpec(
        prim_path="/World/aaa/N_07_Laser_Cutting/_7_Laser_Cutting_Machine/base_link/visual/Geometry/tn__07TL14_0428_kGXkp7c4WYV2ss8XbAac0xoV4lMimv0ohEmjN_0/TL14_1000_A00______________1030/TL14_1003_000___________1084/TL14_1003_r07__________B_1114",
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
    PlayHidePrimSpec(prim_path="/World/aaa/N_07_Laser_Cutting/_7_Laser_Cutting_Machine/base_link/visual/Geometry/tn__07TL14_0428_kGXkp7c4WYV2ss8XbAac0xoV4lMimv0ohEmjN_0/TL14_1000_A00______________1030/TL14_1001_000___________1262"),
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
STATUS_PANEL_TITLE: str = ""

# 패널 레이아웃/스타일(사용자가 여기서 조정) — 가로 전체 너비는 STATUS_PANEL_WIDTH_PX
STATUS_PANEL_WIDTH_PX: int = 270
STATUS_PANEL_LABEL_COL_WIDTH_PX: int = 80
STATUS_PANEL_PADDING_PX: int = 10
STATUS_PANEL_TITLE_FONT_SIZE: int = 14
STATUS_PANEL_ROW_FONT_SIZE: int = 16  # 행별 label/value 미지정 시 공통 기본값
STATUS_PANEL_ROW_HEIGHT_PX: int = 26
STATUS_PANEL_TIME_ROW_HEIGHT_PX: int = 56
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
      - "{time}": ``재생 0.9%`` / ``t 15/1774s`` / ``실경과 16s/1774s`` (3줄, 스냅샷·배속 기준)
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
    # StatusRowSpec(
    #     key="eq_model",
    #     name="EQ MODEL",
    #     value="KIYO_FXE",
    #     height_px=STATUS_PANEL_ROW_HEIGHT_PX,
    #     label_font_size=14,   # 좌측만 작게
    #     value_font_size=14,
    # ),
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
        height_px=STATUS_PANEL_TIME_ROW_HEIGHT_PX,
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
FOUP_PANEL_OFFSET_XYZ_M: Tuple[float, float, float] = (-20, -35, 0.10)

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
# PM1~PM5 기기 라벨 — wafer 점유 시 배경 (읽기 전용 occupancy 미러, 시뮬 무영향)
DEVICE_LABEL_PM_OCCUPIED_BG_RGBA: Tuple[float, float, float, float] = (
    0.12,
    0.40,
    0.90,
    0.85,
)


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
        name="PM1",
        # prim_path="/LAM_Machanical_v01/LAM_Machanical_v01/MechanicalEquipment_Root/MechanicalEquipment/LoadPort_Root/LoadPort/Cooling_Station",
        prim_path="/World/aaa_1/N_07_Laser_Cutting/_7_Laser_Cutting_Machine/base_link/visual/Geometry/tn__07TL14_0428_kGXkp7c4WYV2ss8XbAac0xoV4lMimv0ohEmjN_0/TL14_1000_A00______________1030/TL14_1003_000___________1084",
        offset_xyz_m=(-0.20, 0.0, 0.15),
        color_rgba=(1.0, 1.0, 1.0, 1.0),
        font_size=16,
    )
]


# ---------------------------------------------------------------------------
# 신호등 라이트 Emissive 랜덤 (구현: lam_traffic_light_emissive.py)
# ---------------------------------------------------------------------------
TRAFFIC_LIGHT_EMISSIVE_ENABLED: bool = True
TRAFFIC_LIGHT_EMISSIVE_INTERVAL_MIN_SEC: float = 30.0
TRAFFIC_LIGHT_EMISSIVE_INTERVAL_MAX_SEC: float = 45.0
TRAFFIC_LIGHT_SHADER_PATHS: Tuple[str, str, str] = (
    "/Looks/Light_Red_01/Light_Red",
    "/Looks/Light_Green_01/Light_Green",
    "/Looks/Light_Yellow_01/Light_Yellow",
)
# False(기본)=stage 로드 직후 타이머 시작·Play 와 무관 / True=CSV 재생 중에만
# (듀얼 스크린: 기본 False 유지 — 한 화면 Pause가 신호등을 끄지 않음)
TRAFFIC_LIGHT_EMISSIVE_ONLY_DURING_PLAYBACK: bool = False
# Enable Emission — lam_traffic_light_emissive.py 에서 inputs:enable_emission 고정 사용


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
    "STARTUP_CHECK_TOP_VIEW",
    "PLAY_CAMERA_USE_PRESET_COORDS",
    "PLAY_CAMERA_PRIM_PATH",
    "PLAY_CAMERA_BIND_VIEWPORT_TO_USD_PRIM",
    "PLAY_CAMERA_PRESET_ENABLED",
    "PLAY_CAMERA_FLY_DURATION_SEC",
    "PLAY_CAMERA_FLY_POSITION_EPS_M",
    "PLAY_CAMERA_FLY_DIRECTION_EPS_DEG",
    "PLAY_DELAY_CAMERA_TO_PRIM_HIDE_SEC",
    "PLAY_DELAY_PRIM_HIDE_TO_PLAY_SEC",
    "PlayCameraPresetSpec",
    "PLAY_CAMERA_PRESET",
    "PLAY_CAMERA_PRIM_VIEW",
    "PLAY_CAMERA_PRIM_VIEW_1_SCREEN",
    "PLAY_CAMERA_PRIM_VIEW_2_SCREEN",
    "PLAY_CAMERA_START_VIEW_1_SCREEN",
    "PLAY_CAMERA_START_VIEW_2_SCREEN",
    "PLAY_CAMERA_APERTURE_1_SCREEN",
    "PLAY_CAMERA_APERTURE_2_SCREEN",
    "PLAY_CAMERA_APERTURE_BLEND_SEC",
    "TOP_VIEW_USE_PRESET_COORDS",
    "TOP_VIEW_CAMERA_PRIM_PATH",
    "TOP_VIEW_BIND_VIEWPORT_TO_USD_PRIM",
    "TOP_VIEW_PRESET_ENABLED",
    "TOP_VIEW_PRESET",
    "TOP_VIEW_CAMERA_PRIM_VIEW",
    "TOP_VIEW_CAMERA_PRIM_VIEW_1_SCREEN",
    "TOP_VIEW_CAMERA_PRIM_VIEW_2_SCREEN",
    "TOP_VIEW_APERTURE_1_SCREEN",
    "TOP_VIEW_APERTURE_2_SCREEN",
    "TRAFFIC_LIGHT_EMISSIVE_ENABLED",
    "TRAFFIC_LIGHT_EMISSIVE_INTERVAL_MIN_SEC",
    "TRAFFIC_LIGHT_EMISSIVE_INTERVAL_MAX_SEC",
    "TRAFFIC_LIGHT_SHADER_PATHS",
    "TRAFFIC_LIGHT_EMISSIVE_ONLY_DURING_PLAYBACK",
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
    "STATUS_PANEL_TIME_ROW_HEIGHT_PX",
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
    "DEVICE_LABEL_PM_OCCUPIED_BG_RGBA",
    "DeviceLabelSpec",
    "DEVICE_LABEL_SPECS",
    "VIEWPORT_PICK_WHITELIST_ROOTS",
    "STARTUP_VIEWPORT_FOCUS_PRIM_ENABLED",
    "STARTUP_VIEWPORT_FOCUS_PRIM_PATH",
]
