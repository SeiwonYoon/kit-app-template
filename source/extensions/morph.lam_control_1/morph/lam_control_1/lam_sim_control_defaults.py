"""LAM CSV 시뮬·뷰포트 분할 기본값 SSOT (morph.lam_control_1 전용).

TBS ``sim_control_defaults.py`` 대응. **화면 분할·Widget/Dock·CSV 프리런 등은 여기만 수정.**

Master USD 경로는 ``lam_window.py`` (``default_load_usd_path`` / ``default_aux_load_usd_path``).
"""

from __future__ import annotations

# 프리런 완료 시 ``data/csv_prerun/prerun_screen{N}_*.json`` 저장 여부.
# False → 메모리만 유지, 디스크에는 쓰지 않음.
# True  → 화면별 JSON 파일 생성.
CSV_PRERUN_EXPORT_JSON: bool = True

# 하위 호환용. LAM은 이제 화면1·2 런타임을 항상 모두 로드한다.
START_WITH_DUAL_SCREEN: bool = True

# 앱 시작 시 실제로 표시할 화면. 최소 하나는 반드시 True.
# 둘 다 True  → Dock 50:50
# 화면1만 True → 화면1 100%, 화면2 숨김
# 화면2만 True → 화면1 숨김, 화면2 100%
# 숨겨진 화면의 USD/context/runtime은 계속 유지되며 다시 표시할 때 정지·초기화된다.
STARTUP_SHOW_SCREEN_1: bool = True
STARTUP_SHOW_SCREEN_2: bool = True

# 뷰포트 분할 UI·적용 상한 (1 또는 2만 사용).
MAX_VIEWPORT_SPLIT_COUNT: int = 2

# True: ViewportWidget 2분할 host (독립 usd_context·stage).
# False: Dock + create_viewport_window (LAM_SimSplit_*).
# LAM_SIM_VIEWPORT_WIDGET_SPLIT=0 / TBS_SIM_VIEWPORT_WIDGET_SPLIT=0 도 False.
USE_VIEWPORT_WIDGET_SPLIT: bool = False

# RenderProduct 생성 원인 조사 (증상 수정 아님 — 관측·실험 전용).
VIEWPORT_RP_DIAG_ENABLED: bool = True
# True → aux Context + master_2 로드 후 독립 ui.Window 에 ViewportWidget 1개 생성.
VIEWPORT_RP_ISOLATED_WINDOW_TEST: bool = False
VIEWPORT_RP_TIMELINE_FRAMES: int = 12

# P0 카메라 coupling / 렌더 프로필 조사 로그.
VIEWPORT_COUPLING_DIAG_ENABLED: bool = True

# 화면2(aux) Stage 조명 — 화면1(default ctx) UsdLux 스펙을 session layer 로 복제.
VIEWPORT_AUX_LIGHTING_SYNC_FROM_MAIN: bool = True

# Widget 분할 시 ViewportWindow 전역 camera bindings 비활성.
VIEWPORT_DISABLE_NATIVE_CAMERA_BINDINGS: bool = True

# HyView / livestream (LAM v1 — 구조만 유지, 기본 off).
HYVIEW_BRIDGE_DIAG_ENABLED: bool = False
HYVIEW_BRIDGE_WATCHDOG_SEC: float = 120.0
HYVIEW_STREAM_LOCK_LAYOUT: bool = False
STREAMING_ALLOW_DYNAMIC_RESIZE: bool = False

# 2분할 시 Viewport·보조 창 사용자 리사이즈 차단. LAM_SIM_VIEWPORT_SPLIT_LOCK_RESIZE=0 으로 끔.
LOCK_VIEWPORT_SPLIT_USER_RESIZE: bool = True

# Viewport HUD 「창 표시」 체크박스 — 앱 시작 시 기본 visible.
UI_SHOW_LAM_USD_WINDOW_DEFAULT: bool = False
UI_SHOW_LAM_SEQUENCE_EDITOR_DEFAULT: bool = False
UI_SHOW_LAM_CSV_PLAY_WINDOW_DEFAULT: bool = False

# Viewport 우상단 STATUS 패널 (EQ MODEL / Time / Current State).
# False → 미표시. 창 표시「STATUS 패널」체크박스가 런타임 표시 여부를 덮어쓴다.
SHOW_VIEWPORT_STATUS_PANEL: bool = False

# ---------------------------------------------------------------------------
# STATUS 패널 스타일 (색 = omni.ui 0xAARRGGBB)
# ---------------------------------------------------------------------------
# 표 크기 [px] — HEIGHT 가 0 이면 행 높이 합산(자동)
STATUS_PANEL_WIDTH_PX: int = 270
STATUS_PANEL_HEIGHT_PX: int = 0
STATUS_PANEL_LABEL_COL_WIDTH_PX: int = 80
# 행 높이 (STATUS_PANEL_ROWS 기본값에 사용)
STATUS_PANEL_ROW_HEIGHT_PX: int = 26
STATUS_PANEL_TIME_ROW_HEIGHT_PX: int = 56
STATUS_PANEL_STATE_ROW_HEIGHT_PX: int = 72
# 배경·행 배경 (기본: 흰색)
STATUS_PANEL_BG_COLOR_HEX: int = 0xFFFFFFFF
STATUS_PANEL_ROW_BG_HEX: int = 0xFFFFFFFF
# 라벨·값 텍스트 (기본: 검정)
STATUS_PANEL_TEXT_COLOR_HEX: int = 0xFF000000
STATUS_PANEL_LABEL_COLOR_HEX: int = 0xFF000000
# 패널/표 외곽 테두리 — 0 이면 없음
STATUS_PANEL_BORDER_WIDTH: int = 0
STATUS_PANEL_TABLE_BORDER_WIDTH: int = 0
STATUS_PANEL_BORDER_COLOR_HEX: int = 0x00000000
STATUS_PANEL_BORDER_RADIUS: int = 0
# 행 사이 가로선 (회색). 좌우 inset 만큼 안쪽에서만 그림 (끝까지 안 붙임)
# 두께 1px 는 DPI/서브픽셀에서 들쭉날쭉해 보일 수 있어 기본 2.
STATUS_PANEL_ROW_SEP_COLOR_HEX: int = 0xFFB0B0B0
STATUS_PANEL_ROW_SEP_HEIGHT_PX: int = 2
STATUS_PANEL_ROW_SEP_INSET_PX: int = 10
# 패널 바깥 여백 / 행 안 텍스트 좌우 패딩 [px]
STATUS_PANEL_PADDING_PX: int = 10
STATUS_PANEL_CONTENT_PADDING_PX: int = 10

# Viewport 우상단 CSV 시뮬 재생 HUD.
# SHOW_VIEWPORT_CSV_PANEL: 앱 시작 시 HUD 표시 여부
# SHOW_VIEWPORT_CSV_PANEL_TOGGLE_HOTSPOT: 화면1 좌상단(Federation HUD 바로 아래)
#   투명 클릭 버튼 — 클릭 시 CSV HUD 보이기/숨기기 (시작 플래그와 독립)
SHOW_VIEWPORT_CSV_PANEL: bool = False
SHOW_VIEWPORT_CSV_PANEL_TOGGLE_HOTSPOT: bool = True

# Viewport 우하단 장비배치도 패널.
# SHOW_VIEWPORT_FLOORPLAN_PANEL: 기능·체크박스 자체 on/off (창 표시「장비배치도」기본값)
# STARTUP_CHECK_FLOORPLAN_PANEL: 앱 시작 시 체크(표시) 기본값
SHOW_VIEWPORT_FLOORPLAN_PANEL: bool = False
STARTUP_CHECK_FLOORPLAN_PANEL: bool = True

# Viewport 좌상단 Federation API 로딩 HUD (요청중/실패/수신/파싱/준비완료). False → 미표시.
SHOW_VIEWPORT_FEDERATION_LOAD_HUD: bool = False

# CSV 시뮬 재생 ui.Window — 타임라인 ScrollingFrame 아래(이벤트 함수·매크로·로그) 숨김.
CSV_PLAY_HIDE_UI_BELOW_TIMELINE: bool = True

# ---------------------------------------------------------------------------
# CSV 재생 — plan 모드 (Aligner / 점유 보정 분리)
# ---------------------------------------------------------------------------
# raw              : Aligner 합성 OFF, occupancy swap·visibility shift OFF
# aligner_fix      : Aligner 합성 ON, occupancy swap·visibility shift OFF  ← 기본(합의)
# full_occ_correct : Aligner ON + 기존 occupancy scheduler(swap·visibility shift)
#
# 상세 맵(실무 AI용): docs/lam_control_1_sim_parse_rules_wafer_map_ko.md
# 체크리스트: docs/lam_control_1_sim_plan_structural_fix_checklist_ko.md
# ※ 실무 기본은 aligner_fix. full_occ_correct 는 ATM/VTM 순서 보정→번호 꼬임 위험.
CSV_PLAYBACK_PLAN_MODE: str = "aligner_fix"

# 하위 호환 — True 이고 PLAN_MODE 가 비어 있으면 full 로 취급하는 레거시.
# PLAN_MODE 가 설정되어 있으면 이 값은 ``full_occ_correct`` 여부 보조로만 사용.
# False(또는 aligner_fix): plan 빌드 직후 occupancy 후처리 안 함.
CSV_PLAYBACK_OCCUPANCY_SCHEDULER_ENABLED: bool = False

# ---------------------------------------------------------------------------
# 공정만보기 — JSON 실행 순서 (일반 재생·병렬 공정만보기에는 영향 없음)
# ---------------------------------------------------------------------------
# False(기본): 지금과 동일 — CSV 시각(t)이 겹치면 ATM·VTM이 동시에 움직일 수 있음.
# True: 공정만보기일 때만 JSON을 시작 시각 순으로 하나씩 실행.
#       이전 JSON 애니메이션이 끝난 뒤에만 다음 JSON 시작 (레인 간 겹침 없음).
#       예) VTM 10초 실행 중 t=5 ATM이 있어도, ATM은 VTM 완료 후에 시작.
PROCESS_ONLY_SEQUENTIAL_LANES: bool = True

# 위 순차 모드가 True일 때만 적용 — JSON이 끝난 뒤 다음 JSON 시작 전 추가 대기 [초].
# 0 = 끝나자마자 바로 다음 JSON, 2 = 2초 쉬었다가 다음 JSON.
PROCESS_ONLY_SEQUENTIAL_GAP_SEC: float = 0.2

# ---------------------------------------------------------------------------
# FOUP 번호별 추가 숨김 prim (인덱스 = FOUP 번호)
# ---------------------------------------------------------------------------
# ``FOUP_USAGE_EXTRA_HIDE_PRIMS_1`` = FOUP1 관련 경로, ``_2`` = FOUP2, ``_3`` = FOUP3.
# 파싱 후 사용 FOUP 개수 N(lot→foup 고유 개수)에 따라 시뮬레이션 시작 시:
#   - N=1: ``_1`` 숨김, ``_2``·``_3`` 강제 표시
#   - N=2: ``_1``+``_2`` 숨김, ``_3`` 강제 표시
#   - N=3: ``_1``+``_2``+``_3`` 전부 숨김
# 목록이 비어 있으면 해당 FOUP 번호는 추가 숨김 없음.
FOUP_USAGE_EXTRA_HIDE_PRIMS_1: list = [
    # "/LAM_Foup_v01/LAM_Foup_v01/Foup_Root/Foup_01/Foup_01_Body",
]
FOUP_USAGE_EXTRA_HIDE_PRIMS_2: list = [
    # "/LAM_Foup_v01/LAM_Foup_v01/Foup_Root/Foup_02/Foup_02_Body",
]
FOUP_USAGE_EXTRA_HIDE_PRIMS_3: list = [
    # "/LAM_Foup_v01/LAM_Foup_v01/Foup_Root/Foup_03/Foup_03_Body",
]

# ---------------------------------------------------------------------------
# Federation API (HyView / 웹 T2V → Kit fetch)
# ---------------------------------------------------------------------------
# 호스트는 저장소 루트 ``.env`` (로컬/개발/운영) → ``config.py`` 가 로드.
# 공통 path 는 config 에서 이어 붙이거나 client 가 붙인다.
# ``.env`` 없거나 키 비면 아래 default_host(로컬) 로 fallback.
from .config import (  # noqa: E402
    federation_query_url as _federation_query_url,
    federation_simulation_get_base_url as _federation_simulation_get_base_url,
)

# POST 전체 URL = .env FEDERATION_QUERY_URL + /queries/mcc-target-prev-lot-history/run
FEDERATION_QUERY_URL: str = _federation_query_url()
# Simulation GET base — ``{base}/api/v1/lam/simulations/{exec_id}`` (path 는 client)
FEDERATION_SIMULATION_GET_BASE_URL: str = _federation_simulation_get_base_url()
print("ㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁㅁ")
print(FEDERATION_SIMULATION_GET_BASE_URL)

# Simulation GET 전용 HTTP 헤더 이름 (Federation POST ``FEDERATION_EXTRA_HEADERS`` 와 별도).
# 실무에서 헤더명이 바뀌면 이 파일만 수정한다.
SIMULATION_GET_HEADER_FX_SERVICE_KEY: str = "Fx-Service-Key"
SIMULATION_GET_HEADER_FX_EMPLOYEE_KEY: str = "Fx-Employee-Key"
# ``accept`` — 불필요하면 ``SIMULATION_GET_INCLUDE_ACCEPT_HEADER = False`` 로 끈다.
SIMULATION_GET_INCLUDE_ACCEPT_HEADER: bool = True
SIMULATION_GET_ACCEPT_VALUE: str = "*/*"
# 페이지당 row 수 — sk.hyview_messaging.lam_handler_config 에서 override 가능.
FEDERATION_FETCH_LIMIT: int = 1000
# 화면당 전체 pagination fetch 타임아웃 [s].
FEDERATION_FETCH_TIMEOUT_SEC: float = 300.0
# True → HTTP 대신 data/federation_fixture 샘플 JSON (오프라인 파서 검증).
FEDERATION_USE_FIXTURE: bool = False
# True → 앱 시작 시 「LAM Federation API Test」 테스트 창을 자동으로 연다.
# False여도 창 표시「Federation 테스트」체크박스·메인 창 버튼으로 런타임 표시 가능.
# 기동 중 동기 open_stage 직전 창 도킹 경합을 피하려면 False 권장.
FEDERATION_TEST_WINDOW_AUTO_SHOW: bool = False
# 응답 로그에 rows 앞 N개만 출력 (0 = metadata만).
# ``FEDERATION_VERBOSE_PARSE_LOG=False`` 이면 이 값은 무시되고 샘플을 출력하지 않는다.
FEDERATION_LOG_ROW_SAMPLE: int = 5
# True → 응답 JSON 전체를 콘솔에 출력 (대량 데이터 주의).
# ``FEDERATION_VERBOSE_PARSE_LOG=False`` 이면 강제 False.
FEDERATION_LOG_FULL_RESPONSE: bool = False
# False → 파싱·재생계획 생성의 상세 콘솔 로그 억제 (속도 개선).
#   - fetch 페이지별 columns/rows 샘플 미출력
#   - 이벤트 JSON/Z/스텝 상세 로그 미출력
# True → 기존처럼 파싱·빌드 상세 로그 출력 (디버깅용).
FEDERATION_VERBOSE_PARSE_LOG: bool = False

# 인증 — 비우면 헤더 없이 POST. 실무 확인 후 값만 채우면 된다.
# Bearer 예: FEDERATION_BEARER_TOKEN = "eyJ..."
FEDERATION_BEARER_TOKEN: str = ""
# 추가 헤더 예: {"X-API-Key": "..."}
FEDERATION_EXTRA_HEADERS: dict = {}

# FOUP lot_id / FOUP 슬롯 웨이퍼 번호 3D 라벨 색 (RGBA 0~1) — FOUP1 파랑 / FOUP2 빨강 / FOUP3 초록
FOUP1_LOT_COLOR_RGBA: tuple[float, float, float, float] = (0.20, 0.55, 1.00, 1.00)
FOUP2_LOT_COLOR_RGBA: tuple[float, float, float, float] = (1.00, 0.25, 0.25, 1.00)
FOUP3_LOT_COLOR_RGBA: tuple[float, float, float, float] = (0.25, 0.90, 0.35, 1.00)
# 색 샘플 (밝은 배경에서도 잘 보이게 채도·명도를 잡음). 필요 시 위 튜플에 복사.
#   흰색   (1.00, 1.00, 1.00, 1.00)
#   검정   (0.00, 0.00, 0.00, 1.00)
#   빨강   (0.90, 0.12, 0.12, 1.00)
#   주황   (0.95, 0.45, 0.05, 1.00)
#   노랑   (0.85, 0.70, 0.00, 1.00)
#   초록   (0.10, 0.62, 0.22, 1.00)
#   청록   (0.00, 0.55, 0.55, 1.00)
#   파랑   (0.10, 0.35, 0.90, 1.00)
#   남색   (0.08, 0.18, 0.55, 1.00)
#   보라   (0.55, 0.15, 0.75, 1.00)
#   자주   (0.70, 0.08, 0.40, 1.00)
#   갈색   (0.55, 0.28, 0.08, 1.00)
#   회색   (0.35, 0.35, 0.38, 1.00)
# FOUP 상태보기 패널 첫 줄(lot_id) 글자 크기 [px]. 나머지 줄은 overlay 의 FOUP_PANEL_FONT_SIZE.
FOUP_LOT_ID_FONT_SIZE: int = 15
# 웨이퍼 번호 3D 라벨 글자 크기 [px] (전 슬롯 공통).
WAFER_NUMBER_LABEL_FONT_SIZE: int = 16

# ---------------------------------------------------------------------------
# Extract 결과 캐시 (data/preextract/)
# ---------------------------------------------------------------------------
# False: 지금과 같이 Extract(Flatten) 후 인스턴스별 layer 를 data/preextract/ 에
#        매번 덮어쓴다. 로컬에서 실무 USD 로 캐시를 만들 때 사용.
# True : Flatten/Extract 생략. 저장된 layer 만 읽어 attach (배포용).
#        파일이 없으면 해당 인스턴스는 Extract 실패와 동일하게 처리한다.
USE_PREEXTRACTED_LAYERS: bool = False


def default_viewport_split_count() -> int:
    """로드·유지할 화면 런타임 수. 표시 개수와 무관하게 항상 2."""
    return 2


def default_csv_play_screen_count() -> int:
    """화면별 CSV 시뮬 재생 창 수. 표시 여부와 무관하게 항상 2."""
    return 2


def default_visible_screens() -> tuple[bool, bool]:
    """초기 화면 표시 마스크. 둘 다 False이면 안전하게 화면1을 표시."""
    show_1 = bool(STARTUP_SHOW_SCREEN_1)
    show_2 = bool(STARTUP_SHOW_SCREEN_2)
    if not show_1 and not show_2:
        show_1 = True
    return show_1, show_2


__all__ = [
    "CSV_PRERUN_EXPORT_JSON",
    "START_WITH_DUAL_SCREEN",
    "STARTUP_SHOW_SCREEN_1",
    "STARTUP_SHOW_SCREEN_2",
    "MAX_VIEWPORT_SPLIT_COUNT",
    "USE_VIEWPORT_WIDGET_SPLIT",
    "VIEWPORT_RP_DIAG_ENABLED",
    "VIEWPORT_RP_ISOLATED_WINDOW_TEST",
    "VIEWPORT_RP_TIMELINE_FRAMES",
    "VIEWPORT_COUPLING_DIAG_ENABLED",
    "VIEWPORT_AUX_LIGHTING_SYNC_FROM_MAIN",
    "VIEWPORT_DISABLE_NATIVE_CAMERA_BINDINGS",
    "HYVIEW_BRIDGE_DIAG_ENABLED",
    "HYVIEW_BRIDGE_WATCHDOG_SEC",
    "HYVIEW_STREAM_LOCK_LAYOUT",
    "STREAMING_ALLOW_DYNAMIC_RESIZE",
    "LOCK_VIEWPORT_SPLIT_USER_RESIZE",
    "UI_SHOW_LAM_USD_WINDOW_DEFAULT",
    "UI_SHOW_LAM_SEQUENCE_EDITOR_DEFAULT",
    "UI_SHOW_LAM_CSV_PLAY_WINDOW_DEFAULT",
    "SHOW_VIEWPORT_STATUS_PANEL",
    "STATUS_PANEL_WIDTH_PX",
    "STATUS_PANEL_HEIGHT_PX",
    "STATUS_PANEL_LABEL_COL_WIDTH_PX",
    "STATUS_PANEL_ROW_HEIGHT_PX",
    "STATUS_PANEL_TIME_ROW_HEIGHT_PX",
    "STATUS_PANEL_STATE_ROW_HEIGHT_PX",
    "STATUS_PANEL_BG_COLOR_HEX",
    "STATUS_PANEL_ROW_BG_HEX",
    "STATUS_PANEL_TEXT_COLOR_HEX",
    "STATUS_PANEL_LABEL_COLOR_HEX",
    "STATUS_PANEL_BORDER_WIDTH",
    "STATUS_PANEL_TABLE_BORDER_WIDTH",
    "STATUS_PANEL_BORDER_COLOR_HEX",
    "STATUS_PANEL_BORDER_RADIUS",
    "STATUS_PANEL_ROW_SEP_COLOR_HEX",
    "STATUS_PANEL_ROW_SEP_HEIGHT_PX",
    "STATUS_PANEL_ROW_SEP_INSET_PX",
    "STATUS_PANEL_PADDING_PX",
    "STATUS_PANEL_CONTENT_PADDING_PX",
    "SHOW_VIEWPORT_CSV_PANEL",
    "SHOW_VIEWPORT_CSV_PANEL_TOGGLE_HOTSPOT",
    "SHOW_VIEWPORT_FLOORPLAN_PANEL",
    "STARTUP_CHECK_FLOORPLAN_PANEL",
    "SHOW_VIEWPORT_FEDERATION_LOAD_HUD",
    "CSV_PLAY_HIDE_UI_BELOW_TIMELINE",
    "CSV_PLAYBACK_PLAN_MODE",
    "CSV_PLAYBACK_OCCUPANCY_SCHEDULER_ENABLED",
    "PROCESS_ONLY_SEQUENTIAL_LANES",
    "PROCESS_ONLY_SEQUENTIAL_GAP_SEC",
    "FOUP_USAGE_EXTRA_HIDE_PRIMS_1",
    "FOUP_USAGE_EXTRA_HIDE_PRIMS_2",
    "FOUP_USAGE_EXTRA_HIDE_PRIMS_3",
    "FEDERATION_QUERY_URL",
    "FEDERATION_SIMULATION_GET_BASE_URL",
    "SIMULATION_GET_HEADER_FX_SERVICE_KEY",
    "SIMULATION_GET_HEADER_FX_EMPLOYEE_KEY",
    "SIMULATION_GET_INCLUDE_ACCEPT_HEADER",
    "SIMULATION_GET_ACCEPT_VALUE",
    "FEDERATION_FETCH_LIMIT",
    "FEDERATION_FETCH_TIMEOUT_SEC",
    "FEDERATION_USE_FIXTURE",
    "FEDERATION_TEST_WINDOW_AUTO_SHOW",
    "USE_PREEXTRACTED_LAYERS",
    "FEDERATION_LOG_ROW_SAMPLE",
    "FEDERATION_LOG_FULL_RESPONSE",
    "FEDERATION_VERBOSE_PARSE_LOG",
    "FEDERATION_BEARER_TOKEN",
    "FEDERATION_EXTRA_HEADERS",
    "FOUP1_LOT_COLOR_RGBA",
    "FOUP2_LOT_COLOR_RGBA",
    "FOUP3_LOT_COLOR_RGBA",
    "FOUP_LOT_ID_FONT_SIZE",
    "WAFER_NUMBER_LABEL_FONT_SIZE",
    "default_viewport_split_count",
    "default_csv_play_screen_count",
    "default_visible_screens",
]
