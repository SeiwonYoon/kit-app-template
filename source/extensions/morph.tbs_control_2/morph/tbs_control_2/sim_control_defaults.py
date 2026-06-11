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
    foup_proc_y_lift: float = 4.0

    # OHT → IN/OUT·EP 직접 투입
    oht_to_bp1_min: float = 5.0
    oht_to_bp1_max: float = 10.0

    # IN/OUT → BP(버퍼)
    bp1_to_bp_min: float = 5.0
    bp1_to_bp_max: float = 10.0

    # BP → EP
    bp_to_ep_min: float = 5.0
    bp_to_ep_max: float = 10.0

    # EP → OHT(회수)
    ep_to_oht_min: float = 5.0
    ep_to_oht_max: float = 10.0

    # 전역(화면별 스냅샷에 포함하지 않음)
    sim_speed: float = 1.0
    log_interval_sec: float = 1.0

    def ep_count(self) -> int:
        """``SimulationInitConfig.ep_count`` (2 또는 3)."""
        return 3 if int(self.ep_count_idx) else 2


SIM_CONTROL_DEFAULTS = SimControlDefaults()

__all__ = ["SimControlDefaults", "SIM_CONTROL_DEFAULTS"]
