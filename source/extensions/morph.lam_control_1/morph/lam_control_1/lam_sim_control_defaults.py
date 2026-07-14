"""LAM CSV 시뮬·뷰포트 분할 기본값 SSOT (morph.lam_control_1 전용).

TBS ``sim_control_defaults.py`` 대응. **화면 분할·Widget/Dock·CSV 프리런 등은 여기만 수정.**

Master USD 경로는 ``lam_window.py`` (``default_load_usd_path`` / ``default_aux_load_usd_path``).
"""

from __future__ import annotations

# 프리런 완료 시 ``data/csv_prerun/prerun_screen{N}_*.json`` 저장 여부.
# False → 메모리만 유지, 디스크에는 쓰지 않음.
# True  → 화면별 JSON 파일 생성.
CSV_PRERUN_EXPORT_JSON: bool = True

# 앱 시작 시 2분할(화면2개)로 시작할지 여부.
# True  → **레이아웃 먼저**: 2분할 Dock(50:50) 또는 ViewportWidget 50:50 + 독립 stage 컨텍스트를 만든 뒤
#         화면1에 ``lam_window.default_load_usd_path``,
#         화면2에 ``lam_window.default_aux_load_usd_path`` 를 순서대로 로드.
# False → 화면 1개로 시작.
START_WITH_DUAL_SCREEN: bool = True

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
    """앱 시작 시 ``ext._sim_viewport_split_count`` / 분할 초기값."""
    return 2 if bool(START_WITH_DUAL_SCREEN) else 1


def default_csv_play_screen_count() -> int:
    """활성 CSV 시뮬 재생 창 개수 (1 또는 2)."""
    try:
        n = int(MAX_VIEWPORT_SPLIT_COUNT)
    except Exception:
        n = 2
    n = max(1, min(n, 4))
    if not bool(START_WITH_DUAL_SCREEN):
        return 1
    return max(2, n) if n >= 2 else 2


__all__ = [
    "CSV_PRERUN_EXPORT_JSON",
    "START_WITH_DUAL_SCREEN",
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
]
