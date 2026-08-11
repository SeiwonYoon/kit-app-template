"""TBS 제어창·시뮬 엔진 공통 기본값 (SSOT).

제어창 LOT 수, EP 개수, LOT 생성/회수 간격, FOUP 공정 시간·Y 이동량, 구간별 이동 랜덤 범위,
시뮬 속도 배율, 로그 주기 등 **초기값**은 이 파일만 수정한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SimControlDefaults:
    # LOT / EP
    lot_count: int = 6
    ep_count_idx: int = 0  # EP 콤보 인덱스: 0 → EP2, 1 → EP3

    # LOT 생성(OHT 대기열) 간격(초)
    lot_spawn_min: float = 5.0
    lot_spawn_max: float = 10.0

    # 회수(READYTOUNLOAD) 이벤트 간격(초)
    pickup_min: float = 50.0
    pickup_max: float = 70.0

    # FOUP 공정(EP 상) 시간(초)
    foup_process_min: float = 30.0
    foup_process_max: float = 60.0

    # FOUP 공정 중 EP LOT prim Y축 이동량(스테이지 단위).
    # START 시 +값, END 시 -값, 포트 위치 복원 시 plateau = baseline + 이 값.
    foup_proc_y_lift: float = 30.0

    # OHT → EP 직접 투입 (레거시 키명 oht_to_bp1 / 스냅샷 oht_bp1_*)
    oht_to_bp1_min: float = 5.0
    oht_to_bp1_max: float = 10.0

    # OHT → IN/OUT 안착 (웹·스냅샷: oht_inout_* ; 없으면 oht_bp1_* 폴백)
    oht_to_inout_min: float = 5.0
    oht_to_inout_max: float = 10.0

    # IN/OUT → BP(버퍼)
    bp1_to_bp_min: float = 30.0
    bp1_to_bp_max: float = 35.0

    # BP → EP
    bp_to_ep_min: float = 30.0
    bp_to_ep_max: float = 35.0

    # EP → OHT(회수)
    ep_to_oht_min: float = 30.0
    ep_to_oht_max: float = 35.0

    # 전역(화면별 스냅샷에 포함하지 않음)
    sim_speed: float = 1.0
    log_interval_sec: float = 1.0

    def ep_count(self) -> int:
        """``SimulationInitConfig.ep_count`` (2 또는 3)."""
        return 3 if int(self.ep_count_idx) else 2


SIM_CONTROL_DEFAULTS = SimControlDefaults()


# renewal 포트 갱신 디버그 로그 상시 ON/OFF (여기만 바꾸면 됨).
# True 면 [RENEWAL_DBG] 줄이 콘솔에 항상 찍힌다. 환경변수 TBS_RENEWAL_DEBUG 로도 켤 수 있음.
SIM_RENEWAL_DEBUG: bool = True

# ---------------------------------------------------------------------------
# Viewport EBS 제어 HUD (화면1 좌상단 패널)
# ---------------------------------------------------------------------------
# True  → 앱 시작 시 EBS 제어 HUD 를 보여 준다.
# False → 앱 시작 시 HUD 숨김. (아래 좌하단 토글 버튼으로 나중에 켤 수 있음)
SHOW_VIEWPORT_EBS_CONTROL_HUD: bool = False

# True  → 화면1 좌하단에 거의 안 보이는 클릭 영역을 둔다.
#         클릭 1회 = EBS HUD 보이기 ↔ 숨기기 토글.
# False → 그 클릭 영역 자체를 만들지 않음. (토글 UI 완전 비활성)
# 참고: 완전 투명(alpha=0)은 Kit 에서 클릭이 무시될 수 있어, 구현은 아주 옅은 배경을 씀.
SHOW_VIEWPORT_EBS_HUD_TOGGLE_HOTSPOT: bool = True

# Viewport 우측 상단 시뮬 설정 스냅샷 HUD (화면1 | 시뮬 설정, EP/LOT/초기적재 등).
# False 면 ``sync_viewport_snapshot_hud_layers`` 가 패널을 붙이지 않는다.
SHOW_VIEWPORT_SNAPSHOT_HUD: bool = False

# 앱 시작 시 2분할(화면2개)로 시작할지 여부.
# True  → **레이아웃 먼저**: 2분할 Dock(50:50) + 각 화면 독립 stage 컨텍스트를 만든 뒤
#         화면1에 default_load_usd_path, 화면2에 default_aux_load_usd_path 를 순서대로 로드.
# False → 화면 1개로 시작.
START_WITH_DUAL_SCREEN: bool = True

# 앱 시작 시(듀얼 로드 후) 실제로 표시할 화면. 최소 하나는 반드시 True.
# 둘 다 True  → Dock 50:50
# 화면1만 True → 화면1 100%, 화면2 숨김
# 화면2만 True → 화면1 숨김, 화면2 100%
# 숨겨진 화면의 USD/context 는 유지되며 웹·체크박스로 다시 표시 가능.
STARTUP_SHOW_SCREEN_1: bool = True
STARTUP_SHOW_SCREEN_2: bool = True

# 뷰포트 분할 UI·적용 상한 (1 또는 2만 사용).
MAX_VIEWPORT_SPLIT_COUNT: int = 2

# True: ViewportWidget 2분할 host (독립 usd_context·stage).
# False: Dock + create_viewport_window (TBS_SimSplit_*). TBS_SIM_VIEWPORT_WIDGET_SPLIT=0 도 False.
USE_VIEWPORT_WIDGET_SPLIT: bool = False

# RenderProduct 생성 원인 조사 (증상 수정 아님 — 관측·실험 전용).
# True → [TBS/rp-invest] / [TBS/rp-timeline] 상세 로그.
VIEWPORT_RP_DIAG_ENABLED: bool = True
# True → aux Context + master_2 로드 후 독립 ui.Window 에 ViewportWidget 1개 생성 (CASE A/B).
# 근본 원인 확정 후 기본 off — orphan Widget·깜빡임 유발.
VIEWPORT_RP_ISOLATED_WINDOW_TEST: bool = False
# Widget 생성 직후 RP/Hydra 프레임별 타임라인 관측 프레임 수.
VIEWPORT_RP_TIMELINE_FRAMES: int = 12

# P0 카메라 coupling / 렌더 프로필 조사 로그 ([TBS/coupling-report], [TBS/coupling-trace]).
VIEWPORT_COUPLING_DIAG_ENABLED: bool = True

# 화면2(aux) Stage 조명 — 화면1(default ctx) UsdLux 스펙을 session layer 로 복제 (톤·IBL 동기).
# False 면 조명 없을 때만 generic DomeLight fallback.
VIEWPORT_AUX_LIGHTING_SYNC_FROM_MAIN: bool = True

# Widget 분할 시 ViewportWindow 전역 camera bindings 비활성 (native manipulator 경로 차단).
VIEWPORT_DISABLE_NATIVE_CAMERA_BINDINGS: bool = True

# HyView / livestream T2V bridge 진단 로그 ([HyView/bridge] queued/work_start/work_done/watchdog).
HYVIEW_BRIDGE_DIAG_ENABLED: bool = True
# work_start·work_done 사이 watchdog (초). 0 이면 비활성.
HYVIEW_BRIDGE_WATCHDOG_SEC: float = 120.0

# streaming 배포 시 viewport fill_frame·창 resize 훅 가드 (T2V resize 연쇄 억제).
HYVIEW_STREAM_LOCK_LAYOUT: bool = True

# False → morph.editor_streaming.kit 및 startup 에서 allowDynamicResize=false 적용.
STREAMING_ALLOW_DYNAMIC_RESIZE: bool = False

# 2분할 시 Viewport·보조 창 사용자 리사이즈 차단(기본 on).
# Dock 50:50·Console/Content 레이아웃 유지. 분할선 드래그는 투명 ui.Window 오버레이 + carb.input 보조.
# 끄려면 False 또는 TBS_SIM_VIEWPORT_SPLIT_LOCK_RESIZE=0
LOCK_VIEWPORT_SPLIT_USER_RESIZE: bool = True


def default_viewport_split_count() -> int:
    """앱 시작 시 ``ext._sim_viewport_split_count`` / HUD 분할 체크 초기값."""
    return 2 if bool(START_WITH_DUAL_SCREEN) else 1


def default_visible_screens() -> tuple[bool, bool]:
    """듀얼 로드 후 초기 표시 (화면1, 화면2). 최소 하나 True."""
    s1 = bool(STARTUP_SHOW_SCREEN_1)
    s2 = bool(STARTUP_SHOW_SCREEN_2)
    if not s1 and not s2:
        s1 = True
    return s1, s2

# 프리런(시작 직후, 실제 재생 전 전체 시뮬을 빠르게 돌리는 단계) 동안 엔진 로그를 콘솔에
# print 할지 여부. LOT 수·공정시간이 크면 로그 줄 수가 폭증해 콘솔 출력(flush)만으로도
# 시작이 크게 지연된다.
# False(기본) → 프리런 동안 콘솔 출력 끔(재생 로그 패널 수집은 그대로 유지) → 시작 빨라짐.
# True        → 프리런 로그도 콘솔에 출력(디버그용).
SIM_PRERUN_CONSOLE_LOG: bool = False

# 프리런 완료 시 ``data/sim_prerun/prerun_screen{N}_*.json`` 파일 저장 여부.
# False → 메모리(``_sim_prerun_export_json_by_screen``)·재생용 export 문서만 유지, 디스크에는 쓰지 않음.
# True  → 화면별 JSON 파일 생성(기존 동작).
SIM_PRERUN_EXPORT_JSON: bool = True

# 막대그래프 "미리보기"(전체 막대 노출) 체크박스의 앱 시작 시 기본값.
# True  → 시작부터 미리보기 ON(막대 전체가 보이고 진행 마스크 숨김).
# False → 지금처럼 미리보기 OFF(재생 진행분만 보임).
SIM_BAR_PREVIEW_DEFAULT: bool = True

# ---------------------------------------------------------------------------
# 시뮬 오케스트레이터: 2레일 병렬 (simulation_engine._run_serial_flow)
# ---------------------------------------------------------------------------
# False(기본): 기존과 100% 동일 — 완전 직렬 (실무 기본).
# True: A레일(ARRIVED/REMOVED) 1 + B레일(MOVE_*) 1.
#   · A끼리·B끼리 직렬, A∥B 만 허용(다른 인스턴스).
#   · JSON/포트 끝 EPn 이 같으면 A∥B 금지.
#   · B: 지금 기동 가능하면 BP→EP 우선, 아니면 INOUT→BP (REMOVED soon-empty 로 보류 금지).
#   · Wave: REMOVED → B → OHT. A/B free·FOUP end·티켓 은 ``_parallel_schedule_wave`` SSOT.
#   · 회수: 간격 타이머 티켓 유지. awaiting backlog 시 REMOVED 종료 후 chain 티켓으로 연속 회수.
#   · 버퍼가 채울 빈 EP 에는 OHT→EP 직접투입 보류.
#   · FOUP 등은 기존처럼 독립. EBS OFF 는 MOVE 가 거의 없어 사실상 직렬.
SIM_PARALLEL_NONCONFLICTING_MOVES: bool = False

__all__ = [
    "SimControlDefaults",
    "SIM_CONTROL_DEFAULTS",
    "SIM_RENEWAL_DEBUG",
    "SHOW_VIEWPORT_EBS_CONTROL_HUD",
    "SHOW_VIEWPORT_EBS_HUD_TOGGLE_HOTSPOT",
    "SHOW_VIEWPORT_SNAPSHOT_HUD",
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
    "default_viewport_split_count",
    "default_visible_screens",
    "SIM_PRERUN_CONSOLE_LOG",
    "SIM_PRERUN_EXPORT_JSON",
    "SIM_BAR_PREVIEW_DEFAULT",
    "SIM_PARALLEL_NONCONFLICTING_MOVES",
]
