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
UI_SHOW_LAM_CSV_PLAY_WINDOW_DEFAULT: bool = True

# Viewport 우상단 STATUS 패널 (EQ MODEL / Time / Current State). False → 미표시.
SHOW_VIEWPORT_STATUS_PANEL: bool = True

# Viewport 우상단 CSV 시뮬 재생 HUD. False → 미표시.
SHOW_VIEWPORT_CSV_PANEL: bool = False

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
# False여도 LAM 메인 창의 「Federation API 테스트」 버튼으로 수동 실행 가능.
# 기동 중 동기 open_stage 직전 창 도킹 경합을 피하려면 False 권장.
FEDERATION_TEST_WINDOW_AUTO_SHOW: bool = True
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
    "SHOW_VIEWPORT_CSV_PANEL",
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
    "FEDERATION_LOG_ROW_SAMPLE",
    "FEDERATION_LOG_FULL_RESPONSE",
    "FEDERATION_VERBOSE_PARSE_LOG",
    "FEDERATION_BEARER_TOKEN",
    "FEDERATION_EXTRA_HEADERS",
    "default_viewport_split_count",
    "default_csv_play_screen_count",
    "default_visible_screens",
]
