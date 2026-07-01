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

    # OHT → IN/OUT·EP 직접 투입
    oht_to_bp1_min: float = 5.0
    oht_to_bp1_max: float = 10.0

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

# Viewport 좌측 상단 EBS 2D 제어 패널 (``TbsViewportControlHud``) 앱 시작 시 표시 여부.
# False 면 get_frame 레이어를 마운트하지 않는다.
SHOW_VIEWPORT_EBS_CONTROL_HUD: bool = True

# 앱 시작 시 2분할(화면2개)로 시작할지 여부.
# True  → **레이아웃 먼저**: 2분할 Dock(50:50) + 각 화면 독립 stage 컨텍스트를 만든 뒤
#         화면1에 default_load_usd_path, 화면2에 default_aux_load_usd_path 를 순서대로 로드.
# False → 화면 1개로 시작.
START_WITH_DUAL_SCREEN: bool = True

# 뷰포트 분할 UI·적용 상한 (1 또는 2만 사용).
MAX_VIEWPORT_SPLIT_COUNT: int = 2


def default_viewport_split_count() -> int:
    """앱 시작 시 ``ext._sim_viewport_split_count`` / HUD 분할 체크 초기값."""
    return 2 if bool(START_WITH_DUAL_SCREEN) else 1

# 프리런(시작 직후, 실제 재생 전 전체 시뮬을 빠르게 돌리는 단계) 동안 엔진 로그를 콘솔에
# print 할지 여부. LOT 수·공정시간이 크면 로그 줄 수가 폭증해 콘솔 출력(flush)만으로도
# 시작이 크게 지연된다.
# False(기본) → 프리런 동안 콘솔 출력 끔(재생 로그 패널 수집은 그대로 유지) → 시작 빨라짐.
# True        → 프리런 로그도 콘솔에 출력(디버그용).
SIM_PRERUN_CONSOLE_LOG: bool = False

# 막대그래프 "미리보기"(전체 막대 노출) 체크박스의 앱 시작 시 기본값.
# True  → 시작부터 미리보기 ON(막대 전체가 보이고 진행 마스크 숨김).
# False → 지금처럼 미리보기 OFF(재생 진행분만 보임).
SIM_BAR_PREVIEW_DEFAULT: bool = True

__all__ = [
    "SimControlDefaults",
    "SIM_CONTROL_DEFAULTS",
    "SIM_RENEWAL_DEBUG",
    "SHOW_VIEWPORT_EBS_CONTROL_HUD",
    "START_WITH_DUAL_SCREEN",
    "MAX_VIEWPORT_SPLIT_COUNT",
    "default_viewport_split_count",
    "SIM_PRERUN_CONSOLE_LOG",
    "SIM_BAR_PREVIEW_DEFAULT",
]
