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

# Viewport 좌상단 STATUS 패널 (EQ MODEL / Time / Current State). False → 미표시.
SHOW_VIEWPORT_STATUS_PANEL: bool = False

# CSV 시뮬 재생 ui.Window — 타임라인 ScrollingFrame 아래(이벤트 함수·매크로·로그) 숨김.
CSV_PLAY_HIDE_UI_BELOW_TIMELINE: bool = True


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
    "CSV_PLAY_HIDE_UI_BELOW_TIMELINE",
    "default_viewport_split_count",
    "default_csv_play_screen_count",
    "default_visible_screens",
]
