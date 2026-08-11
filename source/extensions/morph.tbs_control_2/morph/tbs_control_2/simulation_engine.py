from __future__ import annotations

"""
simulation_engine.py — TBS simpy 공정 시뮬레이션 코어

【이 파일의 역할】
- 공정 포트(BP/EP), OHT 입력/회수, LOT 이동/공정/완료를 simpy 이벤트로 실행한다.
- UI(control_window.py)와는 콜백(on_log/on_event/on_progress/on_gate)으로만 통신한다.
- 애니메이션 실행에 필요한 이벤트 payload(seq/from/to/port/lot/ports_occupancy)를 생성한다.

【멀티 뷰(분할) 시 여러 엔진】
- 제어창은 분할 수만큼 ``TBSSimulationEngine`` 인스턴스를 만들며, 각 인스턴스에 ``event_tags``(예: ``tbs_sim_screen``)를 넣는다.
- ``_emit_event`` / ``_emit_progress`` / ``_request_gate``(on_gate) 호출 시 이 태그가 payload 에 **항상 병합**되어
  로그 접두(``[화면N]``)·진행현황 패널 라우팅·게이트가 **어느 채널인지** 구분 가능하다.
- **simpy 환경(env)은 엔진마다 독립**이며, ``tick()`` 호출 스레딩 전략은 control_window(단일 vs 화면별 worker)에 있다.

【핵심 데이터 구조】
- Lot: lot_id/foup_id/sequence (EP 안착 시 곧바로 회수 대기 가능; EP 상 별도 가공 시간 없음).
- SimulationTimingConfig:
  · OHT→EP 직접 투입(oht_to_bp1_*) · OHT→IN/OUT 안착(oht_to_inout_*)
  · LOT 생성 간격(lot_spawn_interval_*): 타이머마다 대기열에 LOT 추가
  · 회수 이벤트 간격(pickup_event_interval_*): READYTOUNLOAD 실행 “티켓” 누적
  · IN/OUT→버퍼(BP), BP→EP, EP→OHT(회수 이동) 랜덤 범위
- SimulationInitConfig:
  · ep_count (2/3)
  · initial_full_ports (시작 시점 미리 적재할 포트; 내부 상태만, ARRIVED 이벤트 없음)
  · max_oht_lots (OHT 쪽에서 생성·투입할 LOT 개수)
- TBSSimulationEngine 내부 상태:
  · ports: 현재 포트 점유(Lot 또는 None)
  · port_start_cd / port_event_cd: XML 이벤트 코드와 연계되는 상태
  · _oht_input_queue: OHT가 순차 투입할 LOT 큐
  · completed_lots: 완료 LOT 목록

【공정 흐름(직렬 모드)】
1) LOT 생성 타이머·회수 타이머를 별도 프로세스로 상시 구동
2) _run_serial_flow: BP→EP → 회수 → IN/OUT→BP → OHT 투입
   (OHT→IN/OUT 안착 후 IN/OUT→BP 를 같은 함수에서 체인하지 않음)
3) EP FOUP 공정(전역 capacity=1) 후 회수 대기 → REMOVED
4) total_lots(초기 적재 + max_oht_lots) 완료 시 종료/요약

【요약·마킹 헬퍼】
- _stage_mark(lot_id, key): 해당 LOT의 공정 단계 시각(sim time)을 기록. 종료 시 _log_final_summary에서 구간별 소요 시간 계산.
- _route_mark(lot_id, key, value): 이동 구간의 from/to 포트 등 문자열을 기록(요약 로그용).
- _dur(m, start_key, end_key): _stage_mark로 찍힌 두 키 사이 경과 시간(초).

【유지보수 포인트】
- 공정 순서 변경/단계 추가:
  · _run_serial_flow 의 단계 순서
  · 각 단계 함수(_load_lot_to_bp1/_move_bp1_to_buffer/_move_bp_to_ep/_execute_pickup)
- 포트 정책 변경(BP/EP 선택 규칙):
  · _find_oldest_empty_buffer, _find_oldest_bp, _find_empty_ep
  · 회수 대상 EP: _find_ep_awaiting_pickup — EP 번호 순이 아니라 _ep_ready_since(FIFO) 우선
- 이벤트 종류(seq) 변경:
  · _emit_event를 호출하는 각 단계의 seq 값 수정
  · 반드시 xml_generator.py의 SEQ_* 및 control_window.py의 SIM_SEQ_ALIAS/rules-map과 동기화
- 시뮬 시간/로그 정책 변경:
  · tick(), _wait_with_progress(), SimulationLogConfig
- 단계 확인 팝업 게이트 로직:
  · _request_gate 호출 지점 + control_window.py on_sim_start_clicked의 _on_gate 구현

【자주 하는 변경 시 체크리스트】
1) 새 이벤트/공정 추가
   - 이 파일: 새 단계 함수 + _emit_event(seq=...)
   - xml_generator.py: SEQ_* 상수/빌더/파서 반영
   - control_window.py: SIM_SEQ_ALIAS, rules/map 매핑, 설명 로그 분기 반영
   - config/event_animation_rules.json 또는 event_animation_map.json: json 경로 매핑 추가
2) 새 애니메이션 JSON 추가
   - data/sim_sequences/*.json 파일 생성(명명: `arrived_inout`, `move_inout_bp*`, `move_bp*_ep*`, `arrived_ep*`, `removed_ep*`)
   - rules/map에 경로 등록(use.json) 또는 `control_window.EVENT_JSON_CASE_MAP`
3) UI 입력 항목 추가(시간/옵션)
   - control_window.py 모델/필드 추가
   - on_sim_start_clicked에서 config로 전달
   - 이 파일 config dataclass와 사용 함수에 연결
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import random
import threading

from .sim_control_defaults import SIM_CONTROL_DEFAULTS as _SIM_DEF
from .sim_control_defaults import SIM_PARALLEL_NONCONFLICTING_MOVES as _SIM_PARALLEL_MOVES
from .sim_lot_fix_proc import LotFixProcEntry, format_lot_id_display


try:
    import simpy  # type: ignore
except Exception:
    simpy = None


INOUT_PORT = "INOUT"
BUFFER_PORTS_MAX = ("BP1", "BP2", "BP3", "BP4")

# UI 로그 블록용(엔진 추정, control_window 매핑과 동일 파일명 규칙)
_LOG_SEP = "=" * 40


def _log_anim_move_transfer_json(from_port: str, to_port: str) -> str:
    return f"move_{str(from_port).lower()}_{str(to_port).lower()}.json"


def _log_anim_move_req_json(bp: str, ep: str) -> str:
    return f"move_{str(bp).lower()}_{str(ep).lower()}.json"


def _log_anim_arrived_ep_json(ep: str) -> str:
    return f"arrived_{str(ep).lower()}.json"


def _log_anim_removed_ep_json(ep: str) -> str:
    return f"removed_{str(ep).lower()}.json"
EP_PORTS_MAX = ("EP1", "EP2", "EP3")
# BASE: IN/OUT만 고정. (BP1~BP4는 BUFFER 포트이며 EP3가 있을 때만 BP4가 추가된다.)
BASE_PORTS = (INOUT_PORT,)


@dataclass
class Lot:
    """시뮬에 등장하는 LOT 한 건(식별자·FOUP·생성 순번)."""

    lot_id: str
    foup_id: str
    sequence: int
    # READYTOLOAD(생성/준비) 공정확인(게이트) 확인 여부.
    # 확인 전에는 직렬 흐름이 이 LOT을 투입 공정(ARRIVED/MOVE_*)으로 가져가면 안 된다.
    ready_to_load_confirmed: bool = False


@dataclass
class SimulationTimingConfig:
    """구간별 이동·스폰·회수 티켓 간격의 난수 범위(초). rand_* 로 샘플링한다.

    필드 기본값은 ``sim_control_defaults.SIM_CONTROL_DEFAULTS`` 를 따른다.
    """
    oht_to_bp1_min: float = _SIM_DEF.oht_to_bp1_min
    oht_to_bp1_max: float = _SIM_DEF.oht_to_bp1_max
    oht_to_inout_min: float = _SIM_DEF.oht_to_inout_min
    oht_to_inout_max: float = _SIM_DEF.oht_to_inout_max
    bp1_to_bp_min: float = _SIM_DEF.bp1_to_bp_min
    bp1_to_bp_max: float = _SIM_DEF.bp1_to_bp_max
    bp_to_ep_min: float = _SIM_DEF.bp_to_ep_min
    bp_to_ep_max: float = _SIM_DEF.bp_to_ep_max
    ep_to_oht_min: float = _SIM_DEF.ep_to_oht_min
    ep_to_oht_max: float = _SIM_DEF.ep_to_oht_max
    lot_spawn_interval_min: float = _SIM_DEF.lot_spawn_min
    lot_spawn_interval_max: float = _SIM_DEF.lot_spawn_max
    pickup_event_interval_min: float = _SIM_DEF.pickup_min
    pickup_event_interval_max: float = _SIM_DEF.pickup_max
    foup_process_min: float = _SIM_DEF.foup_process_min
    foup_process_max: float = _SIM_DEF.foup_process_max

    @staticmethod
    def _norm(a: float, b: float) -> tuple:
        """최소·최대를 정규화하고 하한을 0.01초로 맞춘다(역순 입력 교정)."""
        lo, hi = float(a), float(b)
        if lo > hi:
            lo, hi = hi, lo
        return (max(0.01, lo), max(0.01, hi))

    def rand_oht_to_bp1(self) -> float:
        """OHT→EP 직접 투입 이동 시간(초) 난수 (스냅샷 키 ``oht_bp1_*``)."""
        lo, hi = self._norm(self.oht_to_bp1_min, self.oht_to_bp1_max)
        return random.uniform(lo, hi)

    def rand_oht_to_inout(self) -> float:
        """OHT→IN/OUT 안착 이동 시간(초) 난수 (스냅샷 키 ``oht_inout_*``)."""
        lo, hi = self._norm(self.oht_to_inout_min, self.oht_to_inout_max)
        return random.uniform(lo, hi)

    def rand_bp1_to_bp(self) -> float:
        """BP1→버퍼(BP2~4) 이송 시간(초) 난수."""
        lo, hi = self._norm(self.bp1_to_bp_min, self.bp1_to_bp_max)
        return random.uniform(lo, hi)

    def rand_bp_to_ep(self) -> float:
        """버퍼(BP2~4)→EP 이송 시간(초) 난수."""
        lo, hi = self._norm(self.bp_to_ep_min, self.bp_to_ep_max)
        return random.uniform(lo, hi)

    def rand_ep_to_oht(self) -> float:
        """EP→OHT 회수 이동 시간(초) 난수."""
        lo, hi = self._norm(self.ep_to_oht_min, self.ep_to_oht_max)
        return random.uniform(lo, hi)

    def rand_lot_spawn_interval(self) -> float:
        """OHT 대기열에 LOT을 넣는 간격(초) 난수."""
        lo, hi = self._norm(self.lot_spawn_interval_min, self.lot_spawn_interval_max)
        return random.uniform(lo, hi)

    def rand_pickup_event_interval(self) -> float:
        """회수(READYTOUNLOAD) 시도 티켓을 누적하는 간격(초) 난수."""
        lo, hi = self._norm(self.pickup_event_interval_min, self.pickup_event_interval_max)
        return random.uniform(lo, hi)

    def rand_foup_process_time(self) -> float:
        """FOUP 공정 시간(초) 난수."""
        lo, hi = self._norm(self.foup_process_min, self.foup_process_max)
        return random.uniform(lo, hi)


@dataclass
class SimulationLogConfig:
    """진행/입력 대기 로그·하트비트 출력 주기(초). 0이면 해당 로그 비활성."""

    progress_interval_sec: float = 0.0
    input_status_interval_sec: float = 0.0

    def progress_interval(self) -> float:
        """_wait_with_progress 진행 로그 주기(초). 0 이하면 비활성 처리에 쓰일 수 있음."""
        v = float(self.progress_interval_sec)
        return 0.0 if v <= 0.0 else max(0.2, v)

    def input_status_interval(self) -> float:
        """레거시 호환: [WAIT] 직렬 대기 로그에 쓰는 유효 간격(초)."""
        return self.wait_interval()

    def wait_interval(self) -> float:
        """[대기] 로그 최소 간격(초). 0이면 비활성."""
        v = float(self.input_status_interval_sec)
        return 0.0 if v <= 0.0 else max(0.5, v)

    def heartbeat_interval(self) -> float:
        """[HB] 절충: WAIT보다 긴 주기(로그 스팸 완화). 0이면 비활성."""
        v = float(self.input_status_interval_sec)
        if v <= 0.0:
            return 0.0
        return max(3.0, v * 2.0)


@dataclass
class SimulationInitConfig:
    """시뮬 시작 조건: EP 개수·초기 적재 포트·OHT가 추가 투입할 LOT 수.

    ``ep_count`` / ``max_oht_lots`` 기본값은 ``sim_control_defaults`` 를 따른다.
    """

    ep_count: int = field(default_factory=_SIM_DEF.ep_count)
    # True(기본): IN/OUT·BP 버퍼 포함. False: EP 포트만 (arrived_ep/removed_ep + FOUP).
    ebs_enabled: bool = True
    initial_full_ports: Optional[List[str]] = None
    max_oht_lots: int = field(default_factory=lambda: int(_SIM_DEF.lot_count))
    # True면 공정(랜덤) 시간만 소모하고 애니 길이는 무시(UI에서 애니 스킵/중단과 연동)
    process_time_priority: bool = False
    # 고장(비활성) 포트: 목록에 포함된 포트는 라우팅/선택에서 제외한다.
    # - 시뮬 시작 시점 초기값이며, 실행 중에도 TBSSimulationEngine.set_disabled_ports로 변경 가능.
    disabled_ports: Optional[List[str]] = None
    # fix 공정 입력 (None·빈 튜플이면 기존 랜덤 presample만 사용)
    lot_fix_proc_rows: Optional[Tuple[Any, ...]] = None


@dataclass
class _StatusLogPolicy:
    """
    상태 로그(HEARTBEAT/WAIT)의 주기·중복 방지 정책을 한 곳에서 관리한다.

    목표:
    - 같은 상태(포트 점유/큐/티켓 등)가 반복되는 동안 로그가 과도하게 누적되지 않게 한다.
    - interval(초) 기준의 최소 출력 주기는 유지한다.
    """

    last_heartbeat_t: float = -999.0
    last_wait_t: float = -999.0
    last_heartbeat_key: str = ""
    last_wait_key: str = ""

    def reset(self) -> None:
        self.last_heartbeat_t = -999.0
        self.last_wait_t = -999.0
        self.last_heartbeat_key = ""
        self.last_wait_key = ""

    def may_log_heartbeat(self, now: float, interval: float) -> bool:
        return bool(interval > 0.0 and now - self.last_heartbeat_t >= interval)

    def should_emit_heartbeat(
        self,
        *,
        now: float,
        completed: int,
        total: int,
        next_text: str,
        queue_len: int,
        pickup_tickets: int,
        ports_snapshot: str,
    ) -> bool:
        key = f"c={completed}/{total}|next={next_text}|q={queue_len}|t={pickup_tickets}|ports={ports_snapshot}"
        if key == self.last_heartbeat_key:
            return False
        self.last_heartbeat_key = key
        self.last_heartbeat_t = float(now)
        return True

    def may_log_wait(self, now: float, interval: float) -> bool:
        return bool(interval > 0.0 and now - self.last_wait_t >= interval)

    def should_emit_wait(self, *, now: float, key: str) -> bool:
        if key == self.last_wait_key:
            return False
        self.last_wait_key = key
        self.last_wait_t = float(now)
        return True


@dataclass
class _ProgressEmitPolicy:
    """
    진행현황(on_progress) emit 정책을 한 곳에서 관리한다.

    - interval <= 0: 중간 진행 없이 DONE만 emit
    - interval > 0: 주기적으로 RUNNING을 emit 하되, 텍스트 로그는 찍지 않는다(UI 갱신용)
    - 출력 포맷(소수 자리)도 여기서 고정해, 유지보수 시 _wait_with_progress를 뒤지지 않게 한다.
    """

    min_interval_sec: float = 0.2
    percent_decimals: int = 1

    def normalize_interval(self, interval: float) -> float:
        try:
            v = float(interval)
        except Exception:
            v = 0.0
        if v <= 0.0:
            return 0.0
        return max(self.min_interval_sec, v)

    def format_percent(self, pct: float) -> str:
        try:
            p = float(pct)
        except Exception:
            p = 0.0
        d = int(self.percent_decimals)
        if d <= 0:
            return f"{p:.0f}"
        if d == 1:
            return f"{p:.1f}"
        return f"{p:.{d}f}"

    def format_sec_1(self, sec: float) -> str:
        try:
            s = float(sec)
        except Exception:
            s = 0.0
        return f"{s:.1f}"


class TBSSimulationEngine:
    """
    BP1 입력 → 버퍼 → EP(반출 대기) → OHT 회수 흐름을 simpy로 돌린다.
    UI는 on_log / on_event / on_progress / on_gate 콜백으로만 연결한다.
    """

    def __init__(
        self,
        lots: List[Lot],
        timing: Optional[SimulationTimingConfig] = None,
        log_config: Optional[SimulationLogConfig] = None,
        init_config: Optional[SimulationInitConfig] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_event: Optional[Callable[[Dict[str, str]], None]] = None,
        on_progress: Optional[Callable[[Dict[str, str]], None]] = None,
        on_gate: Optional[Callable[[Dict[str, str]], object]] = None,
        print_to_console: bool = True,
        event_tags: Optional[Dict[str, str]] = None,
    ) -> None:
        """lots: 초기 LOT 목록(보통 비움). 타이밍·초기화·콜백을 묶어 엔진 상태를 구성한다.

        ``event_tags``: ``on_event`` / ``on_progress`` / ``on_gate`` 로 넘기는 payload 에 항상 병합되는
        짧은 문자열 딕셔너리(예: 멀티 뷰 ``tbs_sim_screen``).
        """
        self._lots = list(lots)
        self._timing = timing or SimulationTimingConfig()
        self._log_cfg = log_config or SimulationLogConfig()
        self._init_cfg = init_config or SimulationInitConfig()
        self._lot_fix_rows: Tuple[LotFixProcEntry, ...] = tuple(
            getattr(self._init_cfg, "lot_fix_proc_rows", None) or ()
        )
        self._on_log = on_log
        self._on_event = on_event
        self._on_progress = on_progress
        self._on_gate = on_gate
        self._print_to_console = bool(print_to_console)
        self._event_tags = dict(event_tags or {})
        self._running = False
        self._done = False
        self._deadlock = False
        self._sim_budget_sec = 0.0
        # 진행현황 타임라인 고정 스케일(총 시뮬 예상 시간)
        self._sim_total_est_sec: float = 0.0

        self.env = simpy.Environment() if simpy else None
        ep_count = int(getattr(self._init_cfg, "ep_count", 2) or 2)
        ep_count = 3 if ep_count >= 3 else 2
        self._ebs_enabled = bool(getattr(self._init_cfg, "ebs_enabled", True))
        self._ep_ports = EP_PORTS_MAX[:ep_count]
        if self._ebs_enabled:
            self._buffer_ports = BUFFER_PORTS_MAX if ep_count >= 3 else BUFFER_PORTS_MAX[:3]
            self._all_ports = BASE_PORTS + tuple(self._buffer_ports) + self._ep_ports
        else:
            self._buffer_ports = ()
            self._all_ports = self._ep_ports

        self.ports: Dict[str, Optional[Lot]] = {p: None for p in self._all_ports}
        self.port_start_cd: Dict[str, str] = {p: "EMPTY" for p in self._all_ports}
        self.port_event_cd: Dict[str, str] = {p: "READY_TO_LOAD" for p in self._all_ports}
        self._buffer_loaded_at: Dict[str, float] = {}
        self._buffer_empty_since: Dict[str, float] = {p: 0.0 for p in self._buffer_ports}
        self._dispatching_to_ep: Dict[str, bool] = {ep: False for ep in self._ep_ports}
        self._max_oht_lots = int(getattr(self._init_cfg, "max_oht_lots", 0) or 0)
        self._oht_input_queue: List[Lot] = []
        self._oht_spawn_seq = 0
        self._pickup_tickets = 0
        self._ep_awaiting_pickup: Dict[str, bool] = {ep: False for ep in self._ep_ports}
        # EP에 LOT가 회수 대기가 된 시뮬레이션 시각(가장 이른 EP부터 회수)
        self._ep_ready_since: Dict[str, float] = {ep: 0.0 for ep in self._ep_ports}
        self._oht_loading_bp1 = False
        self.completed_lots: List[str] = []
        self._total_lots = 0
        self._lot_stage_summary: Dict[str, Dict[str, float]] = {}
        self._lot_route_summary: Dict[str, Dict[str, str]] = {}
        self._initial_seed_seq = 1
        self._gate_lock = threading.Lock()
        # 다음 타이머 트리거 시각(sim time). UI 공정확인창에서 "남은 시간" 표시에 사용.
        self._next_spawn_at: Optional[float] = None
        self._next_pickup_at: Optional[float] = None
        # 상태 로그(HEARTBEAT/WAIT) 정책: 중복 방지·주기 제어를 한 곳에서 관리
        self._status_log_policy = _StatusLogPolicy()
        # 진행현황(PROGRESS) emit 정책
        self._progress_emit_policy = _ProgressEmitPolicy()
        # 포트 "이동/회수 진행 중" 잠금.
        # 점유(self.ports)는 완료 시점까지 유지하되, 다음 공정 선택에서는 잠긴 포트를 제외한다.
        # (요구사항: 포트 간 이동은 완료 시점에만 EMPTY/FULL 반영)
        self._locked_ports: set[str] = set()
        # 직렬 오케스트레이터(_run_serial_flow) 깨우기용 이벤트.
        # READYTOLOAD 확인 직후 "다른 공정이 진행중이지 않다면 ARRIVED를 우선" 수행하기 위해 사용한다.
        self._serial_wakeup = self.env.event() if self.env is not None else None
        # 이벤트 블록 로그 #[n] (SIM_START는 #[0] 고정)
        self._sim_log_event_seq = 0
        self._process_time_priority = bool(getattr(self._init_cfg, "process_time_priority", False))
        # sim_control_defaults.SIM_PARALLEL_NONCONFLICTING_MOVES — False 면 완전 직렬(기존 동일).
        self._parallel_nonconflicting_moves = bool(_SIM_PARALLEL_MOVES)
        # True 모드 2레일: A=ARRIVED/REMOVED(_oht_path_inflight), B=MOVE(_move_rail_inflight).
        # _bp_to_ep_inflight 는 하위 호환 alias (B 레일과 동일).
        self._oht_path_inflight = False
        self._a_rail_ep: str = ""
        self._move_rail_inflight = False
        self._b_rail_ep: str = ""
        self._bp_to_ep_inflight = False
        self._interrupt_anim_cb: Optional[Callable[[], None]] = None
        self._faulty_ports_supplier: Optional[Callable[[], Set[str]]] = None
        self._idle_sec: Dict[str, float] = {}
        self._faulty_sec: Dict[str, float] = {}
        # EP 점유 기반 통계(요구사항)
        # - UI(진행현황 창) 막대그래프 우측에 표시되는 "EMPTY 누적 초"의 원천 데이터.
        # FOUP 공정 전역 리소스(capacity=1): 동시에 1개만 공정 진행
        try:
            self._ep_foup_process_res = simpy.Resource(self.env, capacity=1) if (simpy and self.env is not None) else None
        except Exception:
            self._ep_foup_process_res = None
        # FOUP 공정 플래토(+Y 1초 완료 후 ~ END 직전): UI 가시성 갱신 시 +Y320 재스냅(옵션 A)용
        self._foup_proc_active_ep: str = ""
        # - 누적은 _accumulate_sim_stats()에서 매 tick(경과 dt)마다 갱신된다.
        # - 시뮬 종료 시 _log_final_summary()에서 요약 로그로 출력된다.
        self._ep_empty_sec: Dict[str, float] = {}
        self._all_ep_empty_sec: float = 0.0
        self._fault_prev_snapshot: frozenset = frozenset()
        self._last_report_text: str = ""
        # 진행현황 그래프(EP 타임라인)용: SimPy ``env.now`` 가 전진할 때만 progress(timeline_only)를 emit
        # (virtual_now는 이벤트 사이 예산 보간용이라, 막대 축은 env.now와 맞춰 진행 패널 t(sim)과 어긋나지 않게 한다.)
        self._progress_timeline_last_emit_t: float = -999.0

        # 총 시뮬 예상 시간(고정 길이 막대그래프용) — 실행 전 간단 추정.
        try:
            init_full = len(list(getattr(self._init_cfg, "initial_full_ports", None) or []))
        except Exception:
            init_full = 0
        total_lots_est = max(0, int(self._max_oht_lots) + int(init_full))

        def _avg(a: float, b: float) -> float:
            try:
                x = float(a)
                y = float(b)
            except Exception:
                return 0.0
            lo, hi = (x, y) if x <= y else (y, x)
            return max(0.01, (lo + hi) * 0.5)

        try:
            avg_oht_ep = _avg(self._timing.oht_to_bp1_min, self._timing.oht_to_bp1_max)
            avg_oht_inout = _avg(self._timing.oht_to_inout_min, self._timing.oht_to_inout_max)
            avg_oht_in = (avg_oht_ep + avg_oht_inout) * 0.5 if bool(self._ebs_enabled) else avg_oht_ep
            avg_move = (
                avg_oht_in
                + _avg(self._timing.bp1_to_bp_min, self._timing.bp1_to_bp_max)
                + _avg(self._timing.bp_to_ep_min, self._timing.bp_to_ep_max)
                + _avg(self._timing.ep_to_oht_min, self._timing.ep_to_oht_max)
            )
        except Exception:
            avg_move = 20.0
        try:
            avg_pickup = _avg(self._timing.pickup_event_interval_min, self._timing.pickup_event_interval_max)
        except Exception:
            avg_pickup = 60.0
        try:
            avg_spawn = _avg(self._timing.lot_spawn_interval_min, self._timing.lot_spawn_interval_max)
        except Exception:
            avg_spawn = 20.0
        # 보수적 추정: LOT당 이동 평균 + 회수 티켓 평균, 스폰 지연 일부 반영.
        self._sim_total_est_sec = max(
            10.0,
            (avg_move + avg_pickup) * max(1, total_lots_est) + avg_spawn * max(0, int(self._max_oht_lots)),
        )

        # -----------------------------
        # 사전 샘플링(요구사항)
        # -----------------------------
        # 정책: 시뮬 시작 시점에 난수 스트림을 고정(=풀을 미리 생성)하고,
        # 런타임에서는 "새로 uniform을 뽑지 않고" 풀에서 순차 소비한다.
        # (경로 선택은 런타임에 달라질 수 있으므로, 이벤트별 정확한 개수는 미리 알 수 없고,
        #  대신 충분한 길이의 풀을 만들어 고정된 순서로 소비한다.)
        try:
            self._rng = random.Random()
        except Exception:
            self._rng = None
        self._pre_pool: Dict[str, List[float]] = {
            "spawn": [],
            "pickup": [],
            "oht_to_bp1": [],
            "oht_to_inout": [],
            "bp1_to_bp": [],
            "bp_to_ep": [],
            "ep_to_oht": [],
            "foup_process": [],
        }
        self._pre_idx: Dict[str, int] = {k: 0 for k in self._pre_pool.keys()}
        self._presample_fill()

    # 멀티 화면 동시 시작 시 구간(min/max) 일치 여부를 보고 공유할 사전샘플 키.
    # (key → SimulationTimingConfig 의 min/max 속성명)
    _PRESAMPLE_TIMING_ATTRS: Dict[str, Tuple[str, str]] = {
        "spawn": ("lot_spawn_interval_min", "lot_spawn_interval_max"),
        "pickup": ("pickup_event_interval_min", "pickup_event_interval_max"),
        "oht_to_bp1": ("oht_to_bp1_min", "oht_to_bp1_max"),
        "oht_to_inout": ("oht_to_inout_min", "oht_to_inout_max"),
        "bp1_to_bp": ("bp1_to_bp_min", "bp1_to_bp_max"),
        "bp_to_ep": ("bp_to_ep_min", "bp_to_ep_max"),
        "ep_to_oht": ("ep_to_oht_min", "ep_to_oht_max"),
        "foup_process": ("foup_process_min", "foup_process_max"),
    }

    @staticmethod
    def _timing_range_equal(a: SimulationTimingConfig, b: SimulationTimingConfig, amin: str, amax: str) -> bool:
        """두 타이밍 설정의 동일 구간(min/max)이 같은지 (정규화 후 비교)."""
        try:
            lo1, hi1 = SimulationTimingConfig._norm(
                float(getattr(a, amin, 0.0) or 0.0),
                float(getattr(a, amax, 0.0) or 0.0),
            )
            lo2, hi2 = SimulationTimingConfig._norm(
                float(getattr(b, amin, 0.0) or 0.0),
                float(getattr(b, amax, 0.0) or 0.0),
            )
        except Exception:
            return False
        return abs(lo1 - lo2) <= 1e-9 and abs(hi1 - hi2) <= 1e-9

    def adopt_matching_presamples(self, donor: "TBSSimulationEngine") -> List[str]:
        """donor 와 min/max 가 같은 공정 구간의 사전샘플 풀을 복사한다.

        멀티 화면 **동시 시작** 전용. 구간이 다른 키는 각자 뽑은 값을 유지한다.
        소비 인덱스는 0 부터 다시 맞춘다.
        """
        adopted: List[str] = []
        if donor is None or donor is self:
            return adopted
        donor_pool = getattr(donor, "_pre_pool", None)
        if not isinstance(donor_pool, dict):
            return adopted
        src_timing = getattr(donor, "_timing", None)
        dst_timing = getattr(self, "_timing", None)
        if src_timing is None or dst_timing is None:
            return adopted
        for key, (amin, amax) in self._PRESAMPLE_TIMING_ATTRS.items():
            if not self._timing_range_equal(src_timing, dst_timing, amin, amax):
                continue
            src_arr = donor_pool.get(key)
            if not isinstance(src_arr, list) or not src_arr:
                continue
            self._pre_pool[key] = [float(x) for x in src_arr]
            self._pre_idx[key] = 0
            adopted.append(key)
        if adopted and getattr(donor, "_rng", None) is not None:
            try:
                # refill(_presample_fill) 시에도 동일 스트림이 이어지도록 RNG 상태 동기화
                if self._rng is None:
                    self._rng = random.Random()
                self._rng.setstate(donor._rng.getstate())
            except Exception:
                pass
        if adopted:
            try:
                # 공유로 풀이 바뀐 뒤, 화면2 자신의 LOT 수 기준으로 총예상 재계산
                if any(
                    k in adopted
                    for k in (
                        "spawn",
                        "oht_to_bp1",
                        "oht_to_inout",
                        "ep_to_oht",
                        "pickup",
                    )
                ):
                    self._recompute_sim_total_est_from_pool()
            except Exception:
                pass
        return adopted

    def _recompute_sim_total_est_from_pool(self) -> None:
        """현재 ``_pre_pool`` 앞부분 합으로 ``_sim_total_est_sec`` 재계산."""
        try:
            init_full = len(list(getattr(self._init_cfg, "initial_full_ports", None) or []))
        except Exception:
            init_full = 0
        n_spawn = max(0, int(self._max_oht_lots))
        n_lots = max(1, int(self._max_oht_lots) + int(init_full))
        spawn_sum = sum(self._pre_pool["spawn"][:n_spawn]) if n_spawn > 0 else 0.0
        if n_spawn > 0:
            if bool(getattr(self, "_ebs_enabled", True)):
                in_sum = 0.5 * (
                    sum(self._pre_pool["oht_to_bp1"][:n_spawn])
                    + sum(self._pre_pool["oht_to_inout"][:n_spawn])
                )
            else:
                in_sum = sum(self._pre_pool["oht_to_bp1"][:n_spawn])
        else:
            in_sum = 0.0
        out_sum = sum(self._pre_pool["ep_to_oht"][:n_lots]) if n_lots > 0 else 0.0
        pickup_sum = sum(self._pre_pool["pickup"][:n_lots]) if n_lots > 0 else 0.0
        self._sim_total_est_sec = max(10.0, float(spawn_sum + in_sum + out_sum + pickup_sum))

    def _presample_fill(self) -> None:
        """사전 샘플링 풀을 충분히 채운다."""
        # 안전 상한: 매우 큰 값도 커버하되 메모리 폭주 방지
        spawn_n = max(16, int(self._max_oht_lots) + 8)
        pickup_n = max(32, int(self._max_oht_lots) * 4 + 16)
        move_n = max(64, int(self._max_oht_lots) * 4 + 32)
        foup_n = max(32, int(self._max_oht_lots) * 2 + 16)

        def _fill(key: str, n: int, a: float, b: float) -> None:
            try:
                lo, hi = SimulationTimingConfig._norm(float(a), float(b))
            except Exception:
                lo, hi = (0.01, 0.01)
            arr = self._pre_pool.get(key, [])
            if not isinstance(arr, list):
                arr = []
                self._pre_pool[key] = arr
            while len(arr) < n:
                try:
                    if self._rng is not None:
                        arr.append(float(self._rng.uniform(lo, hi)))
                    else:
                        arr.append(float(random.uniform(lo, hi)))
                except Exception:
                    arr.append(float(lo))

        _fill("spawn", spawn_n, self._timing.lot_spawn_interval_min, self._timing.lot_spawn_interval_max)
        _fill("pickup", pickup_n, self._timing.pickup_event_interval_min, self._timing.pickup_event_interval_max)
        _fill("oht_to_bp1", move_n, self._timing.oht_to_bp1_min, self._timing.oht_to_bp1_max)
        _fill("oht_to_inout", move_n, self._timing.oht_to_inout_min, self._timing.oht_to_inout_max)
        _fill("bp1_to_bp", move_n, self._timing.bp1_to_bp_min, self._timing.bp1_to_bp_max)
        _fill("bp_to_ep", move_n, self._timing.bp_to_ep_min, self._timing.bp_to_ep_max)
        _fill("ep_to_oht", move_n, self._timing.ep_to_oht_min, self._timing.ep_to_oht_max)
        _fill(
            "foup_process",
            foup_n,
            self._timing.foup_process_min,
            self._timing.foup_process_max,
        )

        self._recompute_sim_total_est_from_pool()

    def _presampled(self, key: str, fallback_fn) -> float:
        """사전 샘플 풀에서 1개를 순차 소비. 부족하면 refill."""
        arr = self._pre_pool.get(key, [])
        idx = int(self._pre_idx.get(key, 0) or 0)
        if not isinstance(arr, list) or idx >= len(arr):
            try:
                self._presample_fill()
            except Exception:
                pass
            arr = self._pre_pool.get(key, [])
        try:
            v = float(arr[idx])
            self._pre_idx[key] = idx + 1
            return v
        except Exception:
            try:
                return float(fallback_fn())
            except Exception:
                return 0.01

    def _has_lot_fix(self) -> bool:
        return bool(self._lot_fix_rows)

    def _lot_fix_entry(self, sequence: int) -> Optional[LotFixProcEntry]:
        if not self._lot_fix_rows or int(sequence) < 1:
            return None
        idx = int(sequence) - 1
        if idx < 0 or idx >= len(self._lot_fix_rows):
            return None
        entry = self._lot_fix_rows[idx]
        if not bool(getattr(entry, "valid", False)):
            return None
        return entry

    def _lot_fix_label(self, sequence: int) -> str:
        entry = self._lot_fix_entry(sequence)
        if entry is None:
            return ""
        return str(getattr(entry, "label", "") or "").strip()

    def _lot_display_id(self, lot: Lot) -> str:
        return format_lot_id_display(lot.lot_id, self._lot_fix_label(lot.sequence))

    def _presampled_lot_move(self, key: str, lot: Lot, fallback_fn) -> Tuple[float, Optional[str]]:
        """LOT 순번 fix가 있으면 고정 초, 없으면 기존 presample 풀 소비."""
        if not self._has_lot_fix():
            return self._presampled(key, fallback_fn), None
        entry = self._lot_fix_entry(lot.sequence)
        if entry is None:
            return self._presampled(key, fallback_fn), None
        if key == "oht_to_bp1":
            return float(entry.oht_ep_sec), "fix_oht_ep"
        if key == "oht_to_inout":
            # fix 공정시간은 OHT→EP / EP→OHT 만 — INOUT 은 min~max 난수
            return self._presampled(key, fallback_fn), None
        if key == "ep_to_oht":
            return float(entry.ep_oht_sec), "fix_ep_oht"
        return self._presampled(key, fallback_fn), None

    def _enrich_lot_payload(
        self,
        payload: Dict[str, str],
        lot: Lot,
        fix_key: Optional[str],
        proc_sec: float,
    ) -> None:
        label = self._lot_fix_label(lot.sequence)
        if label:
            payload["lot_fix_label"] = label
            payload["lot_id_display"] = format_lot_id_display(lot.lot_id, label)
        if fix_key == "fix_oht_ep":
            payload["fix_oht_ep"] = f"{float(proc_sec):g}"
        elif fix_key == "fix_ep_oht":
            payload["fix_ep_oht"] = f"{float(proc_sec):g}"

    def set_runtime_hooks(
        self,
        *,
        faulty_ports_supplier: Optional[Callable[[], Set[str]]] = None,
        interrupt_anim_cb: Optional[Callable[[], None]] = None,
    ) -> None:
        """UI에서 주입: 고장 포트 집합, 애니메이션 강제 중단 콜백."""
        if faulty_ports_supplier is not None:
            self._faulty_ports_supplier = faulty_ports_supplier
        if interrupt_anim_cb is not None:
            self._interrupt_anim_cb = interrupt_anim_cb

    def kick_serial_flow(self) -> None:
        """고장 포트 등 런타임 조건 변경 시 직렬 루프를 즉시 재평가."""
        self._kick_serial_flow()

    def get_report_text(self) -> str:
        return str(getattr(self, "_last_report_text", "") or "")

    def _get_faulty_set(self) -> Set[str]:
        sup = getattr(self, "_faulty_ports_supplier", None)
        if sup is None:
            return set()
        try:
            out = sup()
            if not out:
                return set()
            return {str(x).strip().upper() for x in out if str(x).strip()}
        except Exception:
            return set()

    def _port_faulty(self, port: str) -> bool:
        p = str(port or "").strip().upper()
        return bool(p) and p in self._get_faulty_set()

    def _interrupt_anim_proc_priority(self) -> None:
        if not self._process_time_priority:
            return
        cb = getattr(self, "_interrupt_anim_cb", None)
        if cb is None:
            return
        try:
            cb()
        except Exception:
            pass

    def _proc_anim_pair(self, proc_sec: float, anim_sec: float) -> Tuple[float, float, float]:
        """반환: (anim_used, total_wait, proc_only)

        요구사항 정책(공정시간 기준):
        - 이벤트 진행 시간은 항상 proc_sec 로 고정한다.
        - JSON(anim_sec)은 UI 측에서 자동 배속 처리로 proc_sec 안에 종료되도록 맞춘다.
        """
        p = float(proc_sec)
        a = float(anim_sec)
        a2 = max(0.0, a)
        tw = max(0.01, float(p))
        return a2, tw, tw

    def _accumulate_sim_stats(self, ds: float) -> None:
        dt = max(0.0, float(ds))
        if dt <= 0.0:
            return
        for p in self._all_ports:
            if self.ports.get(p) is None:
                self._idle_sec[p] = self._idle_sec.get(p, 0.0) + dt
        faulty = self._get_faulty_set()
        for p in faulty:
            if p in self._all_ports:
                self._faulty_sec[p] = self._faulty_sec.get(p, 0.0) + dt

        # EP 통계(점유/비점유) 누적
        # - self.ports[ep] is None  => EMPTY (비어있음)
        # - self.ports[ep] not None => FULL  (점유됨)
        # - dt는 "이번 tick에서 흐른 시뮬레이션 시간(초)"이며, 배속/일시정지 정책을 모두 반영한 후의 값이어야 한다.
        # - 이 누적값은 UI 막대그래프의 우측 숫자 및 종료 요약 로그에 동일하게 사용된다.
        try:
            ep_ports = list(getattr(self, "_ep_ports", []) or [])
        except Exception:
            ep_ports = []
        if ep_ports:
            all_empty = True
            for ep in ep_ports:
                try:
                    empty = self.ports.get(ep) is None
                except Exception:
                    empty = True
                if empty:
                    self._ep_empty_sec[ep] = self._ep_empty_sec.get(ep, 0.0) + dt
                else:
                    all_empty = False
            if all_empty:
                self._all_ep_empty_sec += dt

    def _maybe_log_fault_transitions(self) -> None:
        cur = frozenset(sorted(self._get_faulty_set()))
        if cur == self._fault_prev_snapshot:
            return
        prev = set(self._fault_prev_snapshot)
        now = set(cur)
        for p in sorted(now - prev):
            self._log(f"[포트고장] {p} 비가동(고장) 시작")
        for p in sorted(prev - now):
            self._log(f"[포트복구] {p} 가동 재개")
        self._fault_prev_snapshot = cur

    def _log_raw(self, msg: str) -> None:
        """구분선·블록 등 접두 [t=] 없이 그대로 UI/콘솔로 보낸다."""
        if self._print_to_console:
            print(msg, flush=True)
        if self._on_log:
            try:
                self._on_log(msg)
            except Exception:
                pass

    def _sim_t_str(self) -> str:
        try:
            return f"{float(self.env.now):.1f}" if self.env is not None else "0.0"
        except Exception:
            return "0.0"

    def _next_log_event_num(self) -> int:
        self._sim_log_event_seq += 1
        return self._sim_log_event_seq

    def _log_event_block(
        self,
        *,
        seq: str,
        summary: str,
        lot_id: str = "-",
        anim_line: str = "애니메이션: 없음",
        proc_line: str = "공정시간: 없음",
        progress_line: str = "",
    ) -> None:
        """이벤트별 구분선·필드가 고정된 다줄 로그(설계안)."""
        n = self._next_log_event_num()
        t = self._sim_t_str()
        lines = [
            _LOG_SEP,
            f"#[{n}] t={t}s (sim) 이벤트: {seq}",
            f"요약: {summary}",
            f"lot: {lot_id}",
            anim_line,
            proc_line,
        ]
        if progress_line:
            lines.append(f"진행률: {progress_line}")
        lines.append(f"현재포트: {self._ports_snapshot()}")
        lines.append(_LOG_SEP)
        self._log_raw("\n".join(lines))

    def _log_sim_start_block(self, initial_applied: str) -> None:
        """시뮬 세션당 1회 #[0]. 이후 이벤트는 #[1]부터."""
        t = self._sim_t_str()
        lines = [
            _LOG_SEP,
            f"#[0] t={t}s (sim) 이벤트: SIM_START",
            f"요약: 시뮬레이션 시작 | EP={', '.join(self._ep_ports)} | 목표 LOT={self._total_lots} "
            f"(OHT 추가 투입={self._max_oht_lots})",
            "lot: -",
            "애니메이션: 없음",
            "공정시간: 없음",
            f"초기적재: {initial_applied}",
            f"현재포트: {self._ports_snapshot()}",
            _LOG_SEP,
        ]
        self._log_raw("\n".join(lines))
        self._sim_log_event_seq = 0

    def _log_wait_step_done(self, label: str, total_sec: float) -> None:
        """공정 대기(_wait_with_progress) 종료 한 줄."""
        self._log_raw(
            f"  -> 완료 | {label} | {float(total_sec):.1f}s"
        )

    def _emit_port_occ_refresh(self, summary: str = "포트 점유/표시 갱신(애니 매핑 prim 동기화)") -> None:
        self._emit_event({"seq": "PORT_OCC_REFRESH"})
        self._log_event_block(
            seq="PORT_OCC_REFRESH",
            summary=summary,
            lot_id="-",
            anim_line="애니메이션: 없음",
            proc_line="공정시간: 없음",
        )

    def _kick_serial_flow(self) -> None:
        """_run_serial_flow의 idle wait을 즉시 깨운다."""
        if self.env is None:
            return
        ev = getattr(self, "_serial_wakeup", None)
        if ev is None:
            try:
                self._serial_wakeup = self.env.event()
            except Exception:
                self._serial_wakeup = None
            return
        try:
            if not ev.triggered:
                ev.succeed(True)
        except Exception:
            pass
        try:
            self._serial_wakeup = self.env.event()
        except Exception:
            self._serial_wakeup = None

    def _parallel_enabled(self) -> bool:
        return bool(getattr(self, "_parallel_nonconflicting_moves", False))

    def _grant_chain_pickup_ticket_if_needed(self) -> None:
        """회수대기 EP 가 남았는데 티켓=0 이면 chain 1장.

        첫 REMOVED 는 간격 타이머 티켓을 쓰지만, 이미 awaiting 인 다음 EP 는
        타이머(50~70s)를 다시 기다리지 않고 연속 REMOVED 한다.
        """
        if int(getattr(self, "_pickup_tickets", 0) or 0) > 0:
            return
        if self._find_ep_awaiting_pickup() is None:
            return
        self._pickup_tickets = 1
        try:
            self._log("회수티켓 chain+1 | awaiting backlog (연속 REMOVED)")
        except Exception:
            pass

    def _parallel_schedule_wave(self, *, reason: str = "") -> bool:
        """병렬 SSOT: REMOVED→B→OHT wave 1회 + 오케스트레이터 kick.

        FOUP 종료·티켓 타이머·A/B 레일 free 등 모든 경로가 여기만 호출한다.
        """
        if not self._parallel_enabled():
            try:
                self._kick_serial_flow()
            except Exception:
                pass
            return False
        started = False
        try:
            started = bool(self._start_parallel_nonconflicting_wave())
        except Exception:
            started = False
        try:
            self._kick_serial_flow()
        except Exception:
            pass
        if reason and started:
            try:
                self._log(f"[wave] started reason={reason}")
            except Exception:
                pass
        return started

    def _on_a_rail_freed(self) -> None:
        """A레일(REMOVED/ARRIVED) 종료 → chain 티켓 + wave 재평가."""
        if not self._parallel_enabled():
            return
        try:
            self._grant_chain_pickup_ticket_if_needed()
        except Exception:
            pass
        self._parallel_schedule_wave(reason="a_rail_freed")

    def _on_b_rail_freed(self) -> None:
        """B레일(MOVE) 종료 → wave 재평가 (BP→EP 또는 INOUT→BP / REMOVED∥…)."""
        if not self._parallel_enabled():
            return
        self._parallel_schedule_wave(reason="b_rail_freed")

    def _lock_port(self, port: str) -> None:
        """포트를 '작업 중'으로 잠가 다음 공정 선택에서 제외."""
        p = str(port or "").strip().upper()
        if p:
            self._locked_ports.add(p)

    def _unlock_port(self, port: str) -> None:
        """포트 잠금 해제."""
        p = str(port or "").strip().upper()
        if p:
            self._locked_ports.discard(p)

    def _is_port_locked(self, port: str) -> bool:
        """해당 포트가 작업 중(잠김)이면 True."""
        p = str(port or "").strip().upper()
        return bool(p and p in self._locked_ports)

    @property
    def available(self) -> bool:
        """simpy가 로드되어 시뮬을 돌릴 수 있으면 True."""
        return self.env is not None

    @property
    def is_done(self) -> bool:
        """목표 LOT 처리 완료·중지·데드락 등으로 종료되면 True."""
        return self._done

    @property
    def is_running(self) -> bool:
        """start() 이후 stop/완료 전이면 True."""
        return self._running

    def start(self) -> bool:
        """simpy 환경을 만들고 스폰·회수 타이머·직렬 공정 프로세스를 시작한다. 실패 시 False."""
        if not self.env:
            self._log("[SIM] simpy import 실패: pip install simpy 필요")
            self._done = True
            return False
        if self._running:
            return True
        self._running = True
        self._sim_log_event_seq = 0
        self._locked_ports.clear()
        self._bp_to_ep_inflight = False
        self._oht_path_inflight = False
        self._move_rail_inflight = False
        self._a_rail_ep = ""
        self._b_rail_ep = ""
        self._status_log_policy.reset()
        self._total_lots = 0
        self._pickup_tickets = 0
        self._idle_sec = {p: 0.0 for p in self._all_ports}
        self._faulty_sec = {p: 0.0 for p in self._all_ports}
        self._fault_prev_snapshot = frozenset(sorted(self._get_faulty_set()))
        self._last_report_text = ""
        # EP 타임라인용 progress emit 타이머 초기화(시작 직후부터 움직이게)
        try:
            self._progress_timeline_last_emit_t = -999.0
        except Exception:
            pass
        # 시작 시점마다 사전 샘플 풀/커서를 초기화(=이번 실행에서 고정된 난수 시퀀스)
        try:
            self._pre_idx = {k: 0 for k in (getattr(self, "_pre_pool", {}) or {}).keys()}
        except Exception:
            pass
        try:
            self._presample_fill()
        except Exception:
            pass
        initial_applied = self._apply_initial_full_ports()
        self._total_lots += self._max_oht_lots
        if self._total_lots <= 0:
            self._log("[SIM] 완료 목표 LOT이 0입니다. 시작을 중단합니다.")
            self._running = False
            self._done = True
            return False
        self._log_sim_start_block(initial_applied)
        if initial_applied != "(없음)":
            self._emit_port_occ_refresh("초기 적재 후 포트 표시 갱신")
        # 시작 직후(t=0)에도 그래프가 바로 전진할 수 있도록 timeline_only progress를 1회 emit
        try:
            self._emit_progress(
                {
                    # EP 타임라인 누적(dt) 정밀도: centi-second 반올림로는 작은 진행이 누락될 수 있어 raw를 넉넉히 둔다.
                    "sim_time": "0.000000",
                    "timeline_only": "1",
                    "label": "EP 타임라인",
                    "detail": "",
                    "status": "RUNNING",
                    "elapsed": "0.0",
                    "total": "0.0",
                    "percent": "0",
                }
            )
        except Exception:
            pass
        self.env.process(self._lot_spawn_timer())
        self.env.process(self._pickup_event_timer())
        self.env.process(self._run_serial_flow())
        return True

    def stop(self) -> None:
        """시뮬 실행을 중단하고 완료 플래그를 세운다(UI 정지 버튼 등)."""
        if not self._running:
            return
        self._running = False
        self._done = True
        self._locked_ports.clear()
        self._bp_to_ep_inflight = False
        self._oht_path_inflight = False
        self._move_rail_inflight = False
        self._a_rail_ep = ""
        self._b_rail_ep = ""
        self._status_log_policy.reset()
        self._log(
            f"[SIM] 중지 | completed={len(self.completed_lots)}/{self._total_lots} "
            f"| input_queue={len(self._oht_input_queue)} | ports={self._ports_snapshot()}"
        )

    def _lot_spawn_timer(self):
        """설정 간격마다 LOT을 생성해 OHT 대기열에 쌓는다(타이머는 공정과 독립)."""
        yield self.env.timeout(0.05)
        while self._running:
            if self._oht_spawn_seq >= self._max_oht_lots:
                self._next_spawn_at = None
                return
            dt = self._presampled("spawn", self._timing.rand_lot_spawn_interval)
            try:
                self._next_spawn_at = float(self.env.now) + float(dt)
            except Exception:
                self._next_spawn_at = None
            yield self.env.timeout(dt)
            if not self._running:
                self._next_spawn_at = None
                return
            if self._oht_spawn_seq >= self._max_oht_lots:
                self._next_spawn_at = None
                return
            self._oht_spawn_seq += 1
            lot = Lot(
                lot_id=f"LOT_{self._oht_spawn_seq:03d}",
                foup_id=f"FOUP_{self._oht_spawn_seq:03d}",
                sequence=self._oht_spawn_seq,
            )
            lot_disp = self._lot_display_id(lot)
            self._oht_input_queue.append(lot)
            # 요구사항: 생성(준비) 이벤트(READYTOLOAD)가 먼저 발생하고, 애니는 실행하지 않는다.
            # - 공정확인 창에서 "몇번째 LOT이 생성되어 준비"인지 확인 가능해야 한다.
            # - port_id=OHT 는 "OHT 대기열에 적재(준비)" 의미로 사용한다.
            # 또한 공정확인 모드에서는 READYTOLOAD도 반드시 "확인"을 받아야 다음 공정(ARRIVED)로 넘어간다.
            try:
                _ = self._request_gate(
                    {
                        "seq": "READYTOLOAD",
                        "port_id": "OHT",
                        "lot_id": lot.lot_id,
                        "lot_seq": str(lot.sequence),
                        "foup_id": lot.foup_id,
                        "queue_len": str(len(self._oht_input_queue)),
                        "est_sec": "0.0",
                        "title": "LOT 생성(READYTOLOAD)",
                    }
                )
            except Exception:
                pass
            _rtl_evt: Dict[str, str] = {
                "seq": "READYTOLOAD",
                "port_id": "OHT",
                "lot_id": lot.lot_id,
                "lot_seq": str(lot.sequence),
                "foup_id": lot.foup_id,
                "queue_len": str(len(self._oht_input_queue)),
            }
            self._enrich_lot_payload(_rtl_evt, lot, None, 0.0)
            self._emit_event(_rtl_evt)
            self._log_event_block(
                seq="READYTOLOAD",
                summary=f"LOT 생성·OHT 대기열 적재 (spawn {self._oht_spawn_seq}/{self._max_oht_lots}, queue={len(self._oht_input_queue)})",
                lot_id=lot_disp,
                anim_line="애니메이션: 없음",
                proc_line="공정시간: 없음",
            )
            # READYTOLOAD 확인 완료 후에만 투입 공정(ARRIVED)을 진행할 수 있게 플래그를 올린다.
            try:
                lot.ready_to_load_confirmed = True
            except Exception:
                pass
            # 유휴 상태라면 즉시 다음 공정(ARRIVED) 우선 실행을 시도하도록 직렬 루프를 깨운다.
            try:
                self._kick_serial_flow()
            except Exception:
                pass

    def _pickup_event_timer(self):
        """설정 간격마다 회수(READYTOUNLOAD) 시도 티켓을 누적한다."""
        yield self.env.timeout(0.05)
        while self._running:
            if self._total_lots > 0 and len(self.completed_lots) >= self._total_lots:
                self._next_pickup_at = None
                return
            dt = self._presampled("pickup", self._timing.rand_pickup_event_interval)
            try:
                self._next_pickup_at = float(self.env.now) + float(dt)
            except Exception:
                self._next_pickup_at = None
            yield self.env.timeout(dt)
            if not self._running:
                self._next_pickup_at = None
                return
            if self._total_lots > 0 and len(self.completed_lots) >= self._total_lots:
                self._next_pickup_at = None
                return
            self._pickup_tickets += 1
            self._log(f"회수티켓+1 | 누적={self._pickup_tickets}")
            # 회수대기 EP 가 있으면 티켓으로 wave (REMOVED∥INOUT→BP 등)
            try:
                self._parallel_schedule_wave(reason="pickup_ticket")
            except Exception:
                try:
                    self._kick_serial_flow()
                except Exception:
                    pass

    def tick(self, sim_delta_sec: float) -> None:
        """UI 프레임 등에서 호출: wall-clock 델타를 sim 예산으로 쌓아 env.step()으로 sim time을 진행한다."""
        if not self.env or not self._running or self._done:
            return
        if sim_delta_sec <= 0:
            sim_delta_sec = 1.0 / 60.0
        # wall-clock 기반 tick을 누적해 sim time budget으로 사용.
        # (env.now가 아직 안 움직이는 구간에서도 budget은 계속 쌓여야 한다)
        self._sim_budget_sec += float(sim_delta_sec)
        t_before = float(self.env.now)
        steps = 0
        # "가상 sim time": 다음 이벤트가 멀리 있어 env.now가 안 움직이는 구간에서도,
        # 누적된 sim budget만큼은 사용자에게 시간이 흐르는 것으로 보여야 한다(요구사항: 시작 직후부터 그래프 진행).
        virtual_now = float(t_before)
        while self._running and not self._done:
            next_t = self.env.peek()
            if next_t == float("inf"):
                break
            cur_t = float(self.env.now)
            need = max(0.0, float(next_t) - cur_t)
            if need > self._sim_budget_sec + 1e-12:
                # 아직 다음 이벤트까지 budget이 부족한 경우:
                # env.now는 그대로지만, budget이 쌓인 만큼 가상 시간은 진행한다(단, next_t를 넘지 않게 캡).
                try:
                    virtual_now = float(cur_t) + float(min(self._sim_budget_sec, need))
                except Exception:
                    virtual_now = float(cur_t)
                break
            # 같은 시각 이벤트(need=0)는 budget 소모 없이 연쇄 처리
            self._sim_budget_sec = max(0.0, self._sim_budget_sec - need)
            self.env.step()
            steps += 1
            if steps > 10000:
                self._log("[SIM] 내부 step guard 발동")
                break
        t_after = float(self.env.now)
        try:
            virtual_now = max(float(virtual_now), float(t_after))
        except Exception:
            virtual_now = float(t_after)
        ds = max(0.0, t_after - t_before)
        if ds > 1e-12:
            self._accumulate_sim_stats(ds)
            self._maybe_log_fault_transitions()

        # EP 타임라인 막대의 시각 축은 SimPy ``env.now`` 와 동일(진행현황 t(sim)과 단일 소스).
        # (virtual_now는 내부 step 예산/스케줄용이며, UI 막대에 쓰면 대기 구간에서 시뮬 시각보다 빨리 진행된다.)
        try:
            now_sim = float(self.env.now) if self.env is not None else 0.0
        except Exception:
            now_sim = 0.0
        try:
            last_emit = float(getattr(self, "_progress_timeline_last_emit_t", -999.0) or -999.0)
            # 첫 emit(시작 직후) 또는 시뮬 시각이 실제로 전진한 경우에만 emit (중복 dt 누적 방지)
            if (last_emit < -900.0) or (float(now_sim) > float(last_emit) + 1e-7):
                self._progress_timeline_last_emit_t = float(now_sim)
                # label이 비어있으면 UI가 갱신을 스킵하므로, 최소 라벨을 넣는다.
                # NOTE: sim_time은 _emit_progress가 덮어쓰지 않도록 payload에 직접 넣는다.
                self._emit_progress(
                    {
                        "sim_time": f"{float(now_sim):.6f}",
                        # NOTE: 진행현황 텍스트를 덮어쓰지 않고 그래프만 갱신하기 위한 전용 플래그
                        "timeline_only": "1",
                        "label": "EP 타임라인",
                        "detail": "",
                        "status": "RUNNING",
                        "elapsed": "0.0",
                        "total": "0.0",
                        "percent": "0",
                    }
                )
        except Exception:
            pass

        if not self._done and self.env.peek() == float("inf"):
            self._deadlock = True
            self._done = True
            self._running = False
            self._log("[SIM] 종료: 진행 가능한 이벤트가 없어 deadlock 상태")

    def _run_loop(self):
        """(레거시) 버퍼→EP 디스패치 폴링 루프. 현재 직렬 흐름은 _run_serial_flow 사용."""
        # 시작 직후 초기화 로그가 몰리지 않도록 한 틱 대기
        yield self.env.timeout(0.1)
        while self._running and len(self.completed_lots) < self._total_lots:
            self._log_heartbeat_if_due()
            moved = self._dispatch_buffer_to_ep()
            if not moved:
                now = float(self.env.now) if self.env is not None else 0.0
                wait_interval = self._log_cfg.wait_interval()
                if self._status_log_policy.may_log_wait(now, wait_interval):
                    key = f"loop|q={len(self._oht_input_queue)}|ports={self._ports_snapshot()}"
                    if self._status_log_policy.should_emit_wait(now=now, key=key):
                        self._log(
                            f"[대기] q={len(self._oht_input_queue)} | ports={self._ports_snapshot()}"
                        )
                yield self.env.timeout(0.2)
            else:
                yield self.env.timeout(0.05)

        if self._running:
            self._running = False
            self._done = True
            self._log(
                f"[SIM] 완료: {len(self.completed_lots)}/{self._total_lots} "
                f"| done={self.completed_lots}"
            )
            self._log_final_summary()

    def _run_serial_flow(self):
        """
        메인 오케스트레이터.

        유지보수 관점에서 "시뮬이 다음에 무엇을 할지 결정하는 곳"을 이 함수 1곳으로 고정한다.
        세부 구현은 _step_* 헬퍼로 분리하되, 실행 순서/우선순위는 여기서만 바꾼다.

        우선순위(상단일수록 먼저 시도) — EBS 효율: 빈 EP 채움·회수가 IN/OUT→BP 보다 앞:
        - 1) BUFFER -> EP 채움
        - 2) EP -> OHT 회수 (pickup 티켓이 있으면 FIFO EP 회수)
        - 3) IN/OUT -> BUFFER (IN/OUT 적재분을 버퍼로) — OHT→IN/OUT 과 체인하지 않음
        - 4) OHT 투입 (빈 EP면 direct, 아니면 IN/OUT 경유; IN/OUT 안착 후 루프 재평가)
        - 5) 대기 로그 + 짧은 sleep

        ``SIM_PARALLEL_NONCONFLICTING_MOVES``:
        - False(기본): 위를 ``yield process`` 로 완전 직렬.
        - True: 2레일 — A(ARRIVED/REMOVED) ∥ B(MOVE_*). A/B 각자 직렬,
          동일 EPn 목표면 동시 불가, B는 점유 전제 필수.
          B 우선순위: 빈 EP+BP LOT 이면 BP→EP → 그다음 INOUT→BP.
          기동 순서: REMOVED → B(MOVE) → OHT(버퍼 가능 빈 EP 는 직접투입 보류).
        """
        yield self.env.timeout(0.1)
        parallel = bool(getattr(self, "_parallel_nonconflicting_moves", False))
        mode_txt = "병렬(2레일)" if parallel else "직렬"
        self._log(f"[시작] OHT 추가 LOT 목표={self._max_oht_lots} | 오케스트레이터={mode_txt}")

        while self._running and len(self.completed_lots) < self._total_lots:
            self._log_heartbeat_if_due()

            if parallel:
                started = self._start_parallel_nonconflicting_wave()
                a_busy = bool(getattr(self, "_oht_path_inflight", False))
                b_busy = bool(getattr(self, "_move_rail_inflight", False))
                if started or a_busy or b_busy:
                    yield self.env.timeout(0.05)
                    continue
                if len(self.completed_lots) >= self._total_lots:
                    break
                yield from self._step_idle_wait()
                continue

            # --- False: 완전 직렬 — BP→EP · 회수 우선, IN/OUT→BP 는 그 다음 ---
            did = yield from self._step_buffer_to_ep()
            if did:
                continue

            did = yield from self._step_pickup_to_oht()
            if did:
                continue
            if len(self.completed_lots) >= self._total_lots:
                break

            did = yield from self._step_bp1_to_buffer()
            if did:
                continue

            did = yield from self._step_oht_input()
            if did:
                continue

            yield from self._step_idle_wait()

        if self._running:
            self._running = False
            self._done = True
            self._log(
                f"[SIM] 완료: {len(self.completed_lots)}/{self._total_lots} "
                f"| done={self.completed_lots}"
            )
            self._log_final_summary()

    def _ep_target_token(self, *ports: str) -> str:
        try:
            from .sim_parallel_rails import ep_token_from_text
        except Exception:
            return ""
        for p in ports:
            tok = ep_token_from_text(str(p or ""))
            if tok:
                return tok
        return ""

    def _a_b_ep_conflict(self, other_ep: str) -> bool:
        try:
            from .sim_parallel_rails import ep_targets_conflict
        except Exception:
            return False
        return bool(
            ep_targets_conflict(str(getattr(self, "_a_rail_ep", "") or ""), str(other_ep or ""))
            or ep_targets_conflict(str(getattr(self, "_b_rail_ep", "") or ""), str(other_ep or ""))
        )

    def _start_parallel_nonconflicting_wave(self) -> bool:
        """2레일 비충돌 기동. A(ARRIVED/REMOVED)와 B(MOVE)를 각각 최대 1건.

        - A끼리·B끼리는 동시 기동하지 않음.
        - A∥B 는 끝 EPn 목표가 다를 때만.
        - 기동 순서: **REMOVED → B(MOVE) → OHT ARRIVED**
          (빈 EP 를 OHT 가 가로채기 전에 BP→EP 가 잡게)
        - B: 빈 EP+BP LOT 있으면 **BP→EP 우선**, 그다음 INOUT→BP.
        """
        started = False
        # 1) 회수(REMOVED)만 먼저 — OHT 투입은 B 보다 뒤
        if not bool(getattr(self, "_oht_path_inflight", False)):
            if self._try_start_pickup_nofollow():
                started = True
        # 2) 버퍼/INOUT MOVE — 빈 EP 보충을 OHT 직접투입보다 앞당김
        if self._try_start_b_rail_nofollow():
            started = True
        # 3) OHT→EP/INOUT (버퍼가 채울 빈 EP 는 직접투입 보류)
        if not bool(getattr(self, "_oht_path_inflight", False)):
            if self._try_start_oht_input_nofollow():
                started = True
        return started

    def _try_start_a_rail_nofollow(self) -> bool:
        """A레일: REMOVED 우선, 없으면 OHT→EP/INOUT(ARRIVED).

        주의: ``_start_parallel_nonconflicting_wave`` 는 REMOVED 와 OHT 를
        분리 호출한다. 본 함수는 직렬·레거시 호출용으로 유지.
        """
        if bool(getattr(self, "_oht_path_inflight", False)):
            return False
        if self._try_start_pickup_nofollow():
            return True
        return bool(self._try_start_oht_input_nofollow())

    def _try_start_b_rail_nofollow(self) -> bool:
        """B레일: MOVE 1건.

        우선순위:
        1) **지금** 빈 EP + BP LOT + A EP 비충돌 → BP→EP
        2) 그 외 INOUT FULL + 빈 BP → INOUT→BP
           (REMOVED 중 soon-empty EP 만으로는 INOUT→BP 를 막지 않음)
        """
        if bool(getattr(self, "_move_rail_inflight", False)):
            return False
        if self._try_start_buffer_to_ep_nofollow():
            return True
        if self._try_start_inout_to_bp_nofollow():
            return True
        return False

    def _try_start_buffer_to_ep_nofollow(self) -> bool:
        """BP→EP 를 기동만 하고 완료는 기다리지 않음 (B레일)."""
        if not self._can_start_buffer_to_ep_now():
            return False
        ep = self._find_empty_ep()
        bp = self._find_oldest_bp()
        if not ep or not bp:
            return False
        lot = self.ports.get(bp)
        if lot is None:
            return False
        ep_tok = self._ep_target_token(ep)
        self._move_rail_inflight = True
        self._bp_to_ep_inflight = True
        self._b_rail_ep = ep_tok
        self._dispatching_to_ep[ep] = True
        self._lock_port(bp)
        self._lock_port(ep)
        self.env.process(self._move_bp_to_ep_parallel(bp, ep, lot))
        return True

    def _move_bp_to_ep_parallel(self, bp_port: str, ep_port: str, lot: Lot):
        """병렬 모드용 BP→EP 래퍼 — B레일 정리 후 wave SSOT."""
        try:
            yield self.env.process(self._move_bp_to_ep(bp_port, ep_port, lot))
        finally:
            self._dispatching_to_ep[ep_port] = False
            self._unlock_port(ep_port)
            self._unlock_port(bp_port)
            self._move_rail_inflight = False
            self._bp_to_ep_inflight = False
            self._b_rail_ep = ""
            try:
                self._on_b_rail_freed()
            except Exception:
                pass

    def _can_start_buffer_to_ep_now(self) -> bool:
        """BP→EP 를 **지금** 기동할 수 있으면 True (빈 EP + BP LOT + A레일 EP 비충돌)."""
        if not self._ebs_enabled:
            return False
        if bool(getattr(self, "_move_rail_inflight", False)):
            return False
        ep = self._find_empty_ep()
        bp = self._find_oldest_bp()
        if not ep or not bp or self.ports.get(bp) is None:
            return False
        ep_tok = self._ep_target_token(ep)
        try:
            from .sim_parallel_rails import ep_targets_conflict

            if ep_targets_conflict(str(getattr(self, "_a_rail_ep", "") or ""), ep_tok):
                return False
        except Exception:
            pass
        return True

    def _should_defer_inout_to_bp(self) -> bool:
        """BP→EP 를 **지금** 기동할 수 있을 때만 INOUT→BP 보류.

        규칙 (A∥B):
        - 빈 EP + BP LOT + A와 EP 비충돌 → B는 BP→EP 우선 (INOUT→BP 보류)
        - REMOVED 중 동일 EP 만 곧 비는 경우 → BP→EP 는 EP 충돌로 불가
          → INOUT→빈BP 를 보류하면 B 공회전 = REMOVED∥INOUT→BP 위반
          → 이 경우 보류하지 않음 (INOUT→BP 기동)
        """
        return bool(self._can_start_buffer_to_ep_now())

    def _try_start_inout_to_bp_nofollow(self) -> bool:
        """INOUT→BP (B레일). INOUT FULL·빈 BP·잠금/적재중 가드."""
        if not self._ebs_enabled:
            return False
        if bool(getattr(self, "_move_rail_inflight", False)):
            return False
        if bool(getattr(self, "_oht_loading_bp1", False)):
            return False
        if self._is_port_locked(INOUT_PORT):
            return False
        if self.ports.get(INOUT_PORT) is None:
            return False
        if not self._find_oldest_empty_buffer():
            return False
        # BP→EP 를 지금 시작할 수 있으면만 보류 — REMOVED 중 soon-empty 만으로는 보류 금지
        if self._should_defer_inout_to_bp():
            return False
        self._move_rail_inflight = True
        self._bp_to_ep_inflight = True
        self._b_rail_ep = ""
        self.env.process(self._move_inout_to_bp_parallel())
        return True

    def _move_inout_to_bp_parallel(self):
        """병렬 INOUT→BP 래퍼 — `_move_bp1_to_buffer` 가 점유·잠금 담당."""
        try:
            if self.ports.get(INOUT_PORT) is None:
                return
            if not self._find_oldest_empty_buffer():
                return
            yield self.env.process(self._move_bp1_to_buffer())
        finally:
            self._move_rail_inflight = False
            self._bp_to_ep_inflight = False
            self._b_rail_ep = ""
            try:
                self._on_b_rail_freed()
            except Exception:
                pass

    def _step_bp1_to_buffer(self):
        """IN/OUT 적재분(초기 포함)을 버퍼로 1회 이송 가능하면 실행 후 True.

        OHT→IN/OUT 과 체인하지 않는다 — 오케스트레이터가 BP→EP·회수 다음 순서로 호출한다.
        """
        if not self._ebs_enabled:
            return False
        if bool(getattr(self, "_oht_loading_bp1", False)):
            return False
        if self._is_port_locked(INOUT_PORT):
            return False
        if self.ports.get(INOUT_PORT) is not None and self._find_oldest_empty_buffer():
            yield self.env.process(self._move_bp1_to_buffer())
            return True
        return False

    def _step_pickup_to_oht(self):
        """2) 회수 티켓 처리: 가능한 EP를 FIFO로 회수한다.

        연속 awaiting EP 가 있으면 pickup 종료마다 chain 티켓으로 이어서 회수
        (간격 타이머 공백 없이 REMOVED→REMOVED).
        """
        did_pickup = False
        while self._pickup_tickets > 0 and len(self.completed_lots) < self._total_lots:
            ep_pick = self._find_ep_awaiting_pickup()
            if not ep_pick:
                break
            self._pickup_tickets -= 1
            did_pickup = True
            yield self.env.process(self._execute_pickup(ep_pick))
            try:
                self._grant_chain_pickup_ticket_if_needed()
            except Exception:
                pass
            if len(self.completed_lots) >= self._total_lots:
                break
        return did_pickup

    def _step_oht_input(self):
        """3) OHT 투입: direct(빈 EP) 우선, 아니면 IN/OUT 경유. 1건 실행하면 True."""
        if self._oht_input_queue and not bool(
            getattr(self._oht_input_queue[0], "ready_to_load_confirmed", True)
        ):
            return False

        if self._oht_input_queue and self._can_load_to_ep_direct():
            ep_target = self._find_empty_ep()
            if ep_target:
                lot = self._oht_input_queue.pop(0)
                self._log(f"{lot.lot_id} | 직접투입→{ep_target} | q={len(self._oht_input_queue)}")
                yield self.env.process(self._load_lot_to_ep_direct(lot, ep_target))
                return True

        if self._ebs_enabled and self._oht_input_queue and self._can_load_to_bp1():
            lot = self._oht_input_queue.pop(0)
            self._log(f"{lot.lot_id} | OHT→IN/OUT 투입 | q={len(self._oht_input_queue)}")
            yield self.env.process(self._load_lot_to_inout(lot))
            return True

        return False

    def _step_buffer_to_ep(self):
        """1) 버퍼 → EP 1회 이송 가능하면 실행 후 True."""
        if not self._ebs_enabled:
            return False
        ep = self._find_empty_ep()
        bp = self._find_oldest_bp()
        if ep and bp:
            lot = self.ports.get(bp)
            if lot is not None:
                yield self.env.process(self._move_bp_to_ep(bp, ep, lot))
                return True
        return False

    def _step_idle_wait(self):
        """4) 할 일 없을 때: WAIT 로그(디듀프) + 짧은 sleep."""
        now = float(self.env.now) if self.env is not None else 0.0
        wait_interval = self._log_cfg.wait_interval()
        if self._status_log_policy.may_log_wait(now, wait_interval):
            key = (
                f"serial|q={len(self._oht_input_queue)}"
                f"|t={self._pickup_tickets}"
                f"|ports={self._ports_snapshot()}"
            )
            if self._status_log_policy.should_emit_wait(now=now, key=key):
                self._log(
                    f"[대기] q={len(self._oht_input_queue)} | 티={self._pickup_tickets} | {self._ports_snapshot()}"
                )
        try:
            if self.env is not None and getattr(self, "_serial_wakeup", None) is not None:
                yield simpy.AnyOf(self.env, [self.env.timeout(0.2), self._serial_wakeup])  # type: ignore
                return
        except Exception:
            pass
        yield self.env.timeout(0.2)

    def _try_start_pickup_nofollow(self) -> bool:
        """회수 1건을 기동만 하고 완료는 기다리지 않음 (A레일)."""
        if bool(getattr(self, "_oht_path_inflight", False)):
            return False
        if self._pickup_tickets <= 0 or len(self.completed_lots) >= self._total_lots:
            return False
        ep_pick = self._find_ep_awaiting_pickup()
        if not ep_pick:
            return False
        ep_tok = self._ep_target_token(ep_pick)
        try:
            from .sim_parallel_rails import ep_targets_conflict
        except Exception:
            ep_targets_conflict = None  # type: ignore
        if ep_targets_conflict is not None and ep_targets_conflict(
            str(getattr(self, "_b_rail_ep", "") or ""), ep_tok
        ):
            return False
        self._pickup_tickets -= 1
        self._oht_path_inflight = True
        self._a_rail_ep = ep_tok
        self._lock_port(ep_pick)
        self.env.process(self._execute_pickup_parallel(ep_pick))
        return True

    def _execute_pickup_parallel(self, ep_port: str):
        """병렬 모드용 회수 래퍼 — A레일 정리 후 wave SSOT."""
        try:
            yield self.env.process(self._execute_pickup(ep_port))
        finally:
            self._unlock_port(ep_port)
            self._oht_path_inflight = False
            self._a_rail_ep = ""
            try:
                self._on_a_rail_freed()
            except Exception:
                pass

    def _buffer_can_feed_empty_ep(self, ep_port: Optional[str] = None) -> bool:
        """EBS ON · 빈 EP + oldest BP 에 LOT 있으면 True (OHT 직접투입 보류 조건)."""
        if not bool(getattr(self, "_ebs_enabled", False)):
            return False
        ep = str(ep_port or "").strip().upper() or self._find_empty_ep()
        if not ep:
            return False
        bp = self._find_oldest_bp()
        if not bp:
            return False
        return self.ports.get(bp) is not None

    def _try_start_oht_input_nofollow(self) -> bool:
        """OHT→EP 또는 OHT→INOUT 을 기동만 (A레일).

        빈 EP 를 BP LOT 이 채울 수 있으면 OHT→EP 직접투입을 하지 않고
        (가능하면) OHT→INOUT 만 시도한다 — MOVE BP→EP 가 EP 를 가져야 함.
        """
        if bool(getattr(self, "_oht_path_inflight", False)):
            return False
        if self._oht_input_queue and not bool(
            getattr(self._oht_input_queue[0], "ready_to_load_confirmed", True)
        ):
            return False

        if self._oht_input_queue and self._can_load_to_ep_direct():
            ep_target = self._find_empty_ep()
            # 버퍼가 채울 빈 EP 는 OHT 직접투입 금지 (BP→EP 우선)
            if ep_target and self._buffer_can_feed_empty_ep(ep_target):
                ep_target = None
            if ep_target:
                ep_tok = self._ep_target_token(ep_target)
                try:
                    from .sim_parallel_rails import ep_targets_conflict
                except Exception:
                    ep_targets_conflict = None  # type: ignore
                if ep_targets_conflict is not None and ep_targets_conflict(
                    str(getattr(self, "_b_rail_ep", "") or ""), ep_tok
                ):
                    return False
                lot = self._oht_input_queue.pop(0)
                self._log(f"{lot.lot_id} | 직접투입→{ep_target} | q={len(self._oht_input_queue)}")
                self._oht_path_inflight = True
                self._a_rail_ep = ep_tok
                self._dispatching_to_ep[ep_target] = True
                self._lock_port(ep_target)
                self.env.process(self._load_lot_to_ep_direct_parallel(lot, ep_target))
                return True

        if self._ebs_enabled and self._oht_input_queue and self._can_load_to_bp1():
            lot = self._oht_input_queue.pop(0)
            self._log(f"{lot.lot_id} | OHT→IN/OUT 투입 | q={len(self._oht_input_queue)}")
            self._oht_path_inflight = True
            self._a_rail_ep = ""
            self.env.process(self._load_lot_to_inout_parallel(lot))
            return True

        return False

    def _load_lot_to_ep_direct_parallel(self, lot: Lot, ep_port: str):
        """병렬 모드용 OHT→EP 래퍼."""
        try:
            yield self.env.process(self._load_lot_to_ep_direct(lot, ep_port))
        finally:
            self._dispatching_to_ep[ep_port] = False
            self._unlock_port(ep_port)
            self._oht_path_inflight = False
            self._a_rail_ep = ""
            try:
                self._on_a_rail_freed()
            except Exception:
                pass

    def _load_lot_to_inout_parallel(self, lot: Lot):
        """병렬 모드용 OHT→INOUT 래퍼."""
        try:
            yield self.env.process(self._load_lot_to_inout(lot))
        finally:
            self._oht_path_inflight = False
            self._a_rail_ep = ""
            try:
                self._on_a_rail_freed()
            except Exception:
                pass

    def _load_lots_to_bp1_loop(self):
        """OHT 대기열에서 LOT을 꺼내 BP1에 순차 투입하는 프로세스(구버전 입력 루프)."""
        queued_count = len(self._oht_input_queue)
        if queued_count > 0:
            queued_first = self._oht_input_queue[0].lot_id
            queued_last = self._oht_input_queue[-1].lot_id
            self._log(f"[입력] 큐 {queued_count}건 ({queued_first}…{queued_last})")
        # 입력 프로세스 자체도 한 틱 뒤에 시작해 t=0 로그 집중 완화
        yield self.env.timeout(0.1)
        last_input_status_log_t = -999.0
        while self._running and self._oht_input_queue:
            if not self._can_load_to_bp1():
                now = float(self.env.now) if self.env is not None else 0.0
                input_interval = self._log_cfg.wait_interval()
                if input_interval > 0.0 and (now - last_input_status_log_t >= input_interval):
                    last_input_status_log_t = now
                    next_lot = self._oht_input_queue[0] if self._oht_input_queue else None
                    nid = next_lot.lot_id if next_lot else "-"
                    self._log(
                        f"[대기] IN/OUT={'FULL' if self.ports[INOUT_PORT] else 'EMPTY'} "
                        f"| next={nid} | q={len(self._oht_input_queue)}"
                    )
                yield self.env.timeout(0.2)
                continue
            lot = self._oht_input_queue.pop(0)
            self._log(f"{lot.lot_id} | IN/OUT 투입시작 | q={len(self._oht_input_queue)}")
            yield self.env.process(self._load_lot_to_inout(lot))
        self._log("[입력] 루프 종료")

    def _can_load_to_bp1(self) -> bool:
        """OHT LOT을 IN/OUT에 넣을 수 있는지: IN/OUT 비어 있고 버퍼에 빈 슬롯이 있으며 적재 중 아님.

        직렬(기본): 버퍼가 **이미 EMPTY** 인 슬롯이 있어야 함.
        병렬(``SIM_PARALLEL_NONCONFLICTING_MOVES``): BP→EP 이송 중(잠금+점유)인 버퍼는
        완료 시 EMPTY 가 되므로, OHT→INOUT 를 BP→EP 와 동시에 기동할 수 있게 허용한다.
        (예: BP4→EP3 진행 중 arrived_inout — 기기 비충돌)
        """
        if not self._ebs_enabled:
            return False
        if self._port_faulty(INOUT_PORT):
            return False
        bp1_empty = self.ports[INOUT_PORT] is None
        any_buffer_empty = any(
            self.ports[p] is None and not self._port_faulty(p) for p in self._buffer_ports
        )
        if (
            not any_buffer_empty
            and bool(getattr(self, "_parallel_nonconflicting_moves", False))
        ):
            # BP→EP 진행 중: 점유는 유지되지만 잠금되어 완료 후 비워짐 → 곧 빈 슬롯
            any_buffer_empty = any(
                self._is_port_locked(p)
                and self.ports.get(p) is not None
                and not self._port_faulty(p)
                for p in self._buffer_ports
            )
        return bp1_empty and any_buffer_empty and not self._oht_loading_bp1

    def _can_load_to_ep_direct(self) -> bool:
        """OHT 대기열 LOT을 EP로 직접 넣을 수 있는지(빈 EP 존재 + BP1 적재 중 아님)."""
        if self._ebs_enabled and self._oht_loading_bp1:
            return False
        return self._find_empty_ep() is not None

    def _load_lot_to_ep_direct(self, lot: Lot, ep_port: str):
        """OHT 대기열 LOT을 EP에 직접 투입(ARRIVED + 대기 후 _set_port)."""
        oht_time, fix_key = self._presampled_lot_move("oht_to_bp1", lot, self._timing.rand_oht_to_bp1)
        lot_disp = self._lot_display_id(lot)
        anim_wait = self._request_gate({
            # 요구사항: OHT 이동 애니는 ARRIVED에서만 실행(=MOVE 애니 불필요).
            # gate는 이벤트 발생마다 UI에서 뜨도록 변경 예정이므로, 여기서는 시간 추정만 반환받는다.
            "seq": "ARRIVED",
            "from_port_id": "OHT",
            "to_port_id": ep_port,
            "port_id": ep_port,
            "lot_id": lot.lot_id,
            "est_sec": f"{oht_time:.1f}",
            "title": f"OHT -> {ep_port} 직접 투입",
        })
        aw_u, total_wait, proc_only = self._proc_anim_pair(oht_time, anim_wait)
        self._stage_mark(lot.lot_id, "oht_to_inout_start")
        self._log_brief_step(lot_disp, f"OHT→{ep_port}", oht_time, aw_u)
        # 요구사항: OHT 이동은 ARRIVED(도착/안착) 이벤트로 통일. from/to를 포함해 UI 매핑에 사용.
        _arr_evt: Dict[str, str] = {
            "seq": "ARRIVED",
            "from_port_id": "OHT",
            "to_port_id": ep_port,
            "port_id": ep_port,
            "lot_id": lot.lot_id,
            # JSON 재생 속도 자동 배속(공정시간 동기화)용
            "proc_sec": f"{float(oht_time):.3f}",
        }
        self._enrich_lot_payload(_arr_evt, lot, fix_key, oht_time)
        self._emit_event(_arr_evt)
        _ep_aj = _log_anim_arrived_ep_json(ep_port)
        proc_txt = (
            f"공정시간 우선: {total_wait:.1f}s (공정 {proc_only:.1f}s)"
            if self._process_time_priority
            else f"공정시간: {total_wait:.1f}s (JSON {aw_u:.1f}s)"
        )
        self._log_event_block(
            seq="ARRIVED",
            summary=f"OHT -> {ep_port} 직접 투입",
            lot_id=lot_disp,
            anim_line=f"애니메이션: {_ep_aj} (추정 {aw_u:.1f}s)",
            proc_line=proc_txt,
        )
        _prog_extra: Dict[str, str] = {}
        self._enrich_lot_payload(_prog_extra, lot, fix_key, oht_time)
        yield self.env.process(
            self._wait_with_progress(
                total_sec=total_wait,
                label=f"OHT->{ep_port} {lot_disp}",
                detail=f"{lot_disp} OHT->{ep_port} 직접투입(도착포트={ep_port}) | 공정={oht_time:.1f}s 애니={aw_u:.1f}s",
                proc_sec=oht_time,
                anim_sec=float(anim_wait),
                progress_interval=self._log_cfg.progress_interval(),
                event_seq="ARRIVED",
                linked_anim_json=_ep_aj,
                from_port_id="OHT",
                to_port_id=ep_port,
                lot_id=lot.lot_id,
                port_id=ep_port,
                progress_extra=_prog_extra or None,
            )
        )
        # ARRIVED 이벤트는 위에서 이미 emit 했으므로, 여기서 _set_port가 ARRIVED를 재발행하면 중복 이벤트가 된다.
        self._set_port(ep_port, "ARRIVED", "FULL", lot, emit_arrived_event=False)
        # FOUP 공정 시작(전역 1개만). 공정 완료 전에는 회수대기가 켜지지 않으므로 REMOVED가 대기한다.
        try:
            if self.env is not None:
                self.env.process(self._run_ep_foup_process(ep_port, lot))
        except Exception:
            pass
        # 포트 상태 패널은 이벤트 수신 시점에 갱신된다.
        # direct input은 완료 시점에 별도 이벤트가 없으면 "다음 이벤트 때" 상태가 뒤늦게 보일 수 있어,
        # 갱신 전용 이벤트를 한 번 더 보내준다(애니/매핑 대상이 아님).
        self._emit_port_occ_refresh("직접투입 완료 후 포트 표시 갱신")
        self._stage_mark(lot.lot_id, "oht_to_inout_end")
        self._log(f"{lot.lot_id} | {ep_port} 도착(직접)")

    def _load_lot_to_inout(self, lot: Lot):
        """OHT 대기열 LOT을 IN/OUT으로 투입(ARRIVED·대기 후 IN/OUT 안착).

        IN/OUT 안착 후 IN/OUT→BP 를 **여기서 이어서 돌리지 않는다**(체인 분리).
        버퍼 이송은 오케스트레이터 ``_step_bp1_to_buffer`` 가 BP→EP·회수 다음 순서로 수행한다.
        """
        self._oht_loading_bp1 = True
        try:
            oht_time, fix_key = self._presampled_lot_move(
                "oht_to_inout", lot, self._timing.rand_oht_to_inout
            )
            lot_disp = self._lot_display_id(lot)
            # 각 공정 확인(on_gate): UI 확인 팝업과 동기화되는 블로킹 게이트
            anim_wait = self._request_gate({
                "seq": "ARRIVED",
                "port_id": INOUT_PORT,
                "lot_id": lot.lot_id,
                "est_sec": f"{oht_time:.1f}",
                "title": "OHT -> IN/OUT 경유 안착",
            })
            aw_u, total_wait, proc_only = self._proc_anim_pair(oht_time, anim_wait)
            self._stage_mark(lot.lot_id, "oht_to_inout_start")
            self._log_brief_step(lot_disp, "OHT→IN/OUT", oht_time, aw_u)
            # 요구사항 반영:
            # OHT->IN/OUT 단계는 MOVE가 아니라 ARRIVED(포트 안착 이벤트)로 애니메이션을 구동한다.
            _in_evt: Dict[str, str] = {
                "seq": "ARRIVED",
                "port_id": INOUT_PORT,
                "lot_id": lot.lot_id,
                # JSON 재생 속도 자동 배속(공정시간 동기화)용
                "proc_sec": f"{float(oht_time):.3f}",
            }
            self._enrich_lot_payload(_in_evt, lot, fix_key, oht_time)
            self._emit_event(_in_evt)
            proc_txt = (
                f"공정시간 우선: {total_wait:.1f}s (공정 {proc_only:.1f}s)"
                if self._process_time_priority
                else f"공정시간: {total_wait:.1f}s (JSON {aw_u:.1f}s)"
            )
            self._log_event_block(
                seq="ARRIVED",
                summary="OHT -> IN/OUT 경유 안착",
                lot_id=lot_disp,
                anim_line=f"애니메이션: arrived_inout.json (추정 {aw_u:.1f}s)",
                proc_line=proc_txt,
            )
            _in_prog: Dict[str, str] = {}
            self._enrich_lot_payload(_in_prog, lot, fix_key, oht_time)
            yield self.env.process(
                self._wait_with_progress(
                    total_sec=total_wait,
                    label=f"OHT->{INOUT_PORT} {lot_disp}",
                    detail=f"{lot_disp} OHT->IN/OUT 이동(도착포트=IN/OUT) | 공정={oht_time:.1f}s 애니={aw_u:.1f}s",
                    proc_sec=oht_time,
                    anim_sec=float(anim_wait),
                    progress_interval=self._log_cfg.progress_interval(),
                    event_seq="ARRIVED",
                    linked_anim_json="arrived_inout.json",
                    from_port_id="OHT",
                    to_port_id=INOUT_PORT,
                    lot_id=lot.lot_id,
                    port_id=INOUT_PORT,
                    progress_extra=_in_prog or None,
                )
            )
            self._stage_mark(lot.lot_id, "oht_to_inout_end")
            self._set_port(INOUT_PORT, "ARRIVED", "FULL", lot, emit_arrived_event=False)
            self._log(f"{lot_disp} | IN/OUT 도착 (→BP는 오케스트레이터)")
            self._emit_port_occ_refresh("IN/OUT 안착 후 포트 표시 갱신")
        finally:
            # 안착 완료(또는 중단) 시 플래그 해제 → _step_bp1_to_buffer 가 이어서 가능
            self._oht_loading_bp1 = False
        try:
            self._kick_serial_flow()
        except Exception:
            pass

    def _move_bp1_to_buffer(self):
        """IN/OUT에 있는 LOT을 빈 버퍼로 이송(MOVE_TRANSFERING). 빈 슬롯은 BP1부터."""
        lot = self.ports.get(INOUT_PORT)
        if lot is None:
            return
        target_bp = self._find_oldest_empty_buffer()
        if not target_bp:
            self._log(f"{lot.lot_id} | IN/OUT→버퍼 실패(빈 슬롯 없음)")
            return
        # 이동 중에는 점유를 유지하되, 다음 공정에서 IN/OUT/도착 버퍼가 선택되지 않도록 잠금.
        self._lock_port(INOUT_PORT)
        self._lock_port(target_bp)
        self._route_mark(lot.lot_id, "bp1_to_bp_from", INOUT_PORT)
        self._route_mark(lot.lot_id, "bp1_to_bp_to", target_bp)
        move_time = self._presampled("bp1_to_bp", self._timing.rand_bp1_to_bp)
        anim_wait = self._request_gate({
            "seq": "MOVE_TRANSFERING",
            "from_port_id": INOUT_PORT,
            "to_port_id": target_bp,
            "lot_id": lot.lot_id,
            "est_sec": f"{move_time:.1f}",
            "title": "IN/OUT -> BUFFER 이동",
        })
        aw_u, total_wait, proc_only = self._proc_anim_pair(move_time, anim_wait)
        self._stage_mark(lot.lot_id, "inout_to_bp_start")
        # 요구사항: IN/OUT->BP 이동 애니는 EAPEIS_PORT_MOVE_TRANSFERING(=MOVE_TRANSFERING)만 실행.
        self._emit_event(
            {
                "seq": "MOVE_TRANSFERING",
                "from_port_id": INOUT_PORT,
                "to_port_id": target_bp,
                "lot_id": lot.lot_id,
                # JSON 재생 속도 자동 배속(공정시간 동기화)용
                "proc_sec": f"{float(move_time):.3f}",
            }
        )
        _mv_aj = _log_anim_move_transfer_json(INOUT_PORT, target_bp)
        proc_txt = (
            f"공정시간 우선: {total_wait:.1f}s (공정 {proc_only:.1f}s)"
            if self._process_time_priority
            else f"공정시간: {total_wait:.1f}s (JSON {aw_u:.1f}s)"
        )
        self._log_event_block(
            seq="MOVE_TRANSFERING",
            summary=f"IN/OUT -> {target_bp} 이송",
            lot_id=lot.lot_id,
            anim_line=f"애니메이션: {_mv_aj} (추정 {aw_u:.1f}s)",
            proc_line=proc_txt,
        )
        self._log_brief_step(lot.lot_id, f"IN/OUT→{target_bp}", move_time, aw_u)
        try:
            yield self.env.process(
                self._wait_with_progress(
                    total_sec=total_wait,
                    label=f"IN/OUT->{target_bp} {lot.lot_id}",
                    detail=f"{lot.lot_id} IN/OUT->{target_bp} 이동(출발포트=IN/OUT, 도착포트={target_bp}) | 공정={move_time:.1f}s 애니={aw_u:.1f}s",
                    proc_sec=move_time,
                    anim_sec=float(anim_wait),
                    progress_interval=self._log_cfg.progress_interval(),
                    event_seq="MOVE_TRANSFERING",
                    linked_anim_json=_mv_aj,
                    from_port_id=INOUT_PORT,
                    to_port_id=target_bp,
                    lot_id=lot.lot_id,
                )
            )
        finally:
            # 완료 시점에만 상태 반영: 도착 포트 FULL, 출발 포트 EMPTY
            self._stage_mark(lot.lot_id, "inout_to_bp_end")
            self._set_port(target_bp, "ARRIVED", "FULL", lot)
            self._buffer_loaded_at[target_bp] = float(self.env.now) if self.env is not None else 0.0
            self._remove_from_port(INOUT_PORT)
            self._unlock_port(target_bp)
            self._unlock_port(INOUT_PORT)
            # 완료 상태(포트 점유/매핑 prim)를 즉시 반영하기 위한 갱신 이벤트.
            self._emit_port_occ_refresh("IN/OUT->버퍼 이송 완료 후 포트 표시 갱신")
            self._log(f"{lot.lot_id} | {target_bp} 도착(버퍼)")

    def _find_oldest_empty_buffer(self) -> Optional[str]:
        """비어 있는 버퍼 중 번호 순으로 가장 앞(BP1 우선)."""
        for p in self._buffer_ports:
            if self.ports[p] is None and not self._is_port_locked(p) and not self._port_faulty(p):
                return p
        return None

    def _find_empty_ep(self) -> Optional[str]:
        """비어 있고 EP로 배정 중이 아닌 EP 포트 하나."""
        for ep in self._ep_ports:
            if self._port_faulty(ep):
                continue
            if (
                self.ports[ep] is None
                and not self._dispatching_to_ep.get(ep, False)
                and not self._is_port_locked(ep)
            ):
                return ep
        return None

    def _find_ep_awaiting_pickup(self) -> Optional[str]:
        """회수 대기 중인 EP 중 _ep_ready_since가 가장 이른 포트(FIFO)."""
        candidates = [
            ep
            for ep in self._ep_ports
            if self._ep_awaiting_pickup.get(ep)
            and self.ports.get(ep) is not None
            and not self._is_port_locked(ep)
            and not self._port_faulty(ep)
        ]
        if not candidates:
            return None
        # EP 번호 순이 아니라, 안착·회수대기가 된 시각이 가장 이른 포트부터(FIFO)
        return min(
            candidates,
            key=lambda ep: (self._ep_ready_since.get(ep, 0.0), self._ep_ports.index(ep)),
        )

    def _find_oldest_bp(self) -> Optional[str]:
        """LOT이 있는 버퍼 BP2~BP4 중, 적재 시각이 가장 이른 포트(먼저 EP로 보냄)."""
        candidates = [
            bp
            for bp in self._buffer_ports
            if self.ports[bp] is not None and not self._is_port_locked(bp) and not self._port_faulty(bp)
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda p: self._buffer_loaded_at.get(p, 0.0))[0]

    def _dispatch_buffer_to_ep(self) -> bool:
        """(레거시 루프용) 가장 오래된 버퍼 LOT을 빈 EP로 보낼 수 있으면 프로세스를 시작하고 True."""
        ep = self._find_empty_ep()
        bp = self._find_oldest_bp()
        if not ep or not bp:
            return False
        lot = self.ports[bp]
        if lot is None:
            return False
        self._dispatching_to_ep[ep] = True
        self.env.process(self._move_bp_to_ep(bp, ep, lot))
        return True

    def _move_bp_to_ep(self, bp_port: str, ep_port: str, lot: Lot):
        """버퍼→EP 이송(MOVE_REQ). 점유는 완료 시점에만 이동시키고, 중복 선택 방지용으로 잠금."""
        move_time = self._presampled("bp_to_ep", self._timing.rand_bp_to_ep)
        # 요구사항: BP->EP 이동 애니는 별도 시퀀스(EISEAP_PORT_MOVE_REQ)로 실행.
        anim_wait = self._request_gate({
            "seq": "MOVE_REQ",
            "from_port_id": bp_port,
            "to_port_id": ep_port,
            "lot_id": lot.lot_id,
            "est_sec": f"{move_time:.1f}",
            "title": "BUFFER -> EP 이동",
        })
        aw_u, total_wait, proc_only = self._proc_anim_pair(move_time, anim_wait)
        self._stage_mark(lot.lot_id, "bp_to_ep_start")
        self._route_mark(lot.lot_id, "bp_to_ep_from", bp_port)
        self._route_mark(lot.lot_id, "bp_to_ep_to", ep_port)
        # 이동 중에는 점유를 유지하고, 다음 공정 선택에서만 제외(잠금).
        self._lock_port(bp_port)
        self._lock_port(ep_port)
        self._emit_event(
            {
                "seq": "MOVE_REQ",
                "from_port_id": bp_port,
                "to_port_id": ep_port,
                "lot_id": lot.lot_id,
                # JSON 재생 속도 자동 배속(공정시간 동기화)용
                "proc_sec": f"{float(move_time):.3f}",
            }
        )
        _req_aj = _log_anim_move_req_json(bp_port, ep_port)
        proc_txt = (
            f"공정시간 우선: {total_wait:.1f}s (공정 {proc_only:.1f}s)"
            if self._process_time_priority
            else f"공정시간: {total_wait:.1f}s (JSON {aw_u:.1f}s)"
        )
        self._log_event_block(
            seq="MOVE_REQ",
            summary=f"{bp_port} -> {ep_port} 이송",
            lot_id=lot.lot_id,
            anim_line=f"애니메이션: {_req_aj} (추정 {aw_u:.1f}s)",
            proc_line=proc_txt,
        )
        self._log_brief_step(lot.lot_id, f"{bp_port}→{ep_port}", move_time, aw_u)
        try:
            yield self.env.process(
                self._wait_with_progress(
                    total_sec=total_wait,
                    label=f"{bp_port}->{ep_port} {lot.lot_id}",
                    detail=f"{lot.lot_id} {bp_port}->{ep_port} 이송(출발포트={bp_port}, 도착포트={ep_port}) | 공정={move_time:.1f}s 애니={aw_u:.1f}s",
                    proc_sec=move_time,
                    anim_sec=float(anim_wait),
                    progress_interval=self._log_cfg.progress_interval(),
                    event_seq="MOVE_REQ",
                    linked_anim_json=_req_aj,
                    from_port_id=bp_port,
                    to_port_id=ep_port,
                    lot_id=lot.lot_id,
                )
            )
        finally:
            # 완료 시점에만 상태 반영: 출발 포트 EMPTY, 도착 포트 FULL
            self._stage_mark(lot.lot_id, "bp_to_ep_end")
            # BP->EP 이동은 MOVE_REQ 이벤트로 처리하며, ARRIVED(=OHT 운반) 이벤트를 추가로 발생시키지 않는다.
            self._set_port(ep_port, "ARRIVED", "FULL", lot, emit_arrived_event=False)
            # FOUP 공정 시작(전역 1개만). 공정 완료 전에는 회수대기가 켜지지 않으므로 REMOVED가 대기한다.
            try:
                if self.env is not None:
                    self.env.process(self._run_ep_foup_process(ep_port, lot))
            except Exception:
                pass
            self._buffer_loaded_at.pop(bp_port, None)
            self._remove_from_port(bp_port)
            self._dispatching_to_ep[ep_port] = False
            self._unlock_port(ep_port)
            self._unlock_port(bp_port)
            # 요구사항: READYTOLOAD는 상태/생성 의미만(애니 없음). 이벤트는 유지.
            self._emit_event({"seq": "READYTOLOAD", "port_id": bp_port, "lot_id": lot.lot_id})
            self._log_event_block(
                seq="READYTOLOAD",
                summary=f"{bp_port} 비움·준비완료 표시(버퍼→EP 이송 완료 후)",
                lot_id=lot.lot_id,
                anim_line="애니메이션: 없음",
                proc_line="공정시간: 없음",
            )
            # 완료 상태(포트 점유/매핑 prim)를 즉시 반영하기 위한 갱신 이벤트.
            self._emit_port_occ_refresh("버퍼→EP 이송 완료 후 포트 표시 갱신")
            self._log(f"{lot.lot_id} | {ep_port} 도착(공정)")

    def _run_ep_foup_process(self, ep_port: str, lot: Lot):
        """
        FOUP 공정(EP 상).

        요구사항:
        - EP 안착 후 FOUP 공정 시작
        - 시작: Y +3.2 이동(1초) → 공정(30~60초 랜덤) → 종료: Y -3.2 이동(1초)
        - 전역적으로 동시에 1개만 공정 진행(simpy.Resource capacity=1)
        - Removed(회수)는 공정 완료(+ -3.2 이동 완료) 전에는 대기(=awaiting_pickup을 끝에서 True)
        - Removed 애니메이션 중에도 다른 EP의 공정은 가능(Removed 자체는 이 리소스와 별개)
        """
        if self.env is None:
            return
        res = getattr(self, "_ep_foup_process_res", None)
        if res is None:
            # 리소스가 없으면(시뮬 미지원 환경 등) 공정만 시간 소모 없이 바로 회수대기로 전환
            self._ep_awaiting_pickup[ep_port] = True
            self._ep_ready_since[ep_port] = float(self.env.now)
            return
        # 공정 시간 샘플 — 사전샘플 풀 소비(동시실행 시 화면 간 동일 구간 공유용)
        try:
            proc_time = float(
                self._presampled("foup_process", self._timing.rand_foup_process_time)
            )
        except Exception:
            proc_time = 30.0
        proc_time = max(0.1, proc_time)

        with res.request() as req:
            try:
                yield req
            except Exception:
                return
            # 대기열 길이(추정)
            try:
                waiting_n = len(getattr(res, "queue", []) or [])
            except Exception:
                waiting_n = 0

            # START 직전: payload·restore 경로가 +Y lift 를 알 수 있도록 active EP 를 먼저 설정
            try:
                self._foup_proc_active_ep = str(ep_port).strip().upper()
            except Exception:
                self._foup_proc_active_ep = ""

            # START 이벤트(애니메이션 훅: Y +lift)
            try:
                self._emit_event({"seq": "FOUP_PROCESS_START", "port_id": ep_port, "lot_id": lot.lot_id})
            except Exception:
                pass
            # +3.2 이동을 시뮬 시간으로도 반영(1초)
            try:
                yield self.env.process(
                    self._wait_with_progress(
                        total_sec=1.0,
                        label=f"FOUP(+Y) {ep_port}",
                        detail=f"{ep_port} FOUP 공정 시작(+Y 3.2) | 대기={waiting_n}",
                        proc_sec=1.0,
                        anim_sec=1.0,
                        progress_interval=self._log_cfg.progress_interval(),
                        event_seq="FOUP_PROCESS",
                        linked_anim_json="",
                        port_hint=ep_port,
                    )
                )
            except Exception:
                try:
                    yield self.env.timeout(1.0)
                except Exception:
                    pass

            # 공정 시간 대기(전역 1개만)
            try:
                yield self.env.process(
                    self._wait_with_progress(
                        total_sec=float(proc_time),
                        label=f"FOUP 공정 {ep_port}",
                        detail=f"{ep_port} FOUP 공정 진행 | 대기={waiting_n}",
                        proc_sec=float(proc_time),
                        anim_sec=0.0,
                        progress_interval=self._log_cfg.progress_interval(),
                        event_seq="FOUP_PROCESS",
                        linked_anim_json="",
                        port_hint=ep_port,
                    )
                )
            except Exception:
                try:
                    yield self.env.timeout(float(proc_time))
                except Exception:
                    pass

            # END 직전: -Y 애니가 곧 시작되므로 플래토 플래그 해제(재스냅 +Y320 금지)
            try:
                self._foup_proc_active_ep = ""
            except Exception:
                pass

            # END 이벤트(애니메이션 훅: Y -3.2)
            try:
                self._emit_event({"seq": "FOUP_PROCESS_END", "port_id": ep_port, "lot_id": lot.lot_id})
            except Exception:
                pass
            # -3.2 이동을 시뮬 시간으로도 반영(1초). 이 완료 후에야 회수 가능.
            try:
                yield self.env.process(
                    self._wait_with_progress(
                        total_sec=1.0,
                        label=f"FOUP(-Y) {ep_port}",
                        detail=f"{ep_port} FOUP 공정 종료(-Y 3.2)",
                        proc_sec=1.0,
                        anim_sec=1.0,
                        progress_interval=self._log_cfg.progress_interval(),
                        event_seq="FOUP_PROCESS",
                        linked_anim_json="",
                        port_hint=ep_port,
                    )
                )
            except Exception:
                try:
                    yield self.env.timeout(1.0)
                except Exception:
                    pass

            # 이제 회수 가능
            try:
                self._ep_awaiting_pickup[ep_port] = True
                self._ep_ready_since[ep_port] = float(self.env.now)
            except Exception:
                pass
            # 병렬 SSOT: awaiting 등록 후 wave 1곳 (REMOVED·INOUT→BP 등)
            try:
                self._parallel_schedule_wave(reason="foup_end")
            except Exception:
                try:
                    self._kick_serial_flow()
                except Exception:
                    pass

    def _execute_pickup(self, ep_port: str):
        """회수: READYTOUNLOAD 게이트→이벤트, REMOVED 게이트→이벤트→공정+애니 대기→포트 비움·completed."""
        lot = self.ports.get(ep_port)
        if lot is None:
            self._ep_awaiting_pickup[ep_port] = False
            return
        self._ep_awaiting_pickup[ep_port] = False
        unload_time, fix_key = self._presampled_lot_move("ep_to_oht", lot, self._timing.rand_ep_to_oht)
        lot_disp = self._lot_display_id(lot)
        self._request_gate(
            {
                "seq": "READYTOUNLOAD",
                "port_id": ep_port,
                "lot_id": lot.lot_id,
                "lot_seq": str(lot.sequence),
                "foup_id": lot.foup_id,
                "est_sec": f"{unload_time:.1f}",
                "title": "EP -> OHT 회수(READYTOUNLOAD)",
            }
        )
        _rtu_evt: Dict[str, str] = {"seq": "READYTOUNLOAD", "port_id": ep_port, "lot_id": lot.lot_id}
        self._enrich_lot_payload(_rtu_evt, lot, None, unload_time)
        self._emit_event(_rtu_evt)
        self._log_event_block(
            seq="READYTOUNLOAD",
            summary=f"{ep_port} 에서 OHT 회수 준비(반출 대기)",
            lot_id=lot_disp,
            anim_line="애니메이션: 없음",
            proc_line=f"회수 이동 예상(공정): {unload_time:.1f}s",
        )
        anim_wait = self._request_gate(
            {
                "seq": "REMOVED",
                "port_id": ep_port,
                "lot_id": lot.lot_id,
                "lot_seq": str(lot.sequence),
                "foup_id": lot.foup_id,
                "est_sec": f"{unload_time:.1f}",
                "title": "EP -> OHT 회수(REMOVED)",
            }
        )
        aw_u, total_wait, proc_only = self._proc_anim_pair(unload_time, anim_wait)
        self._stage_mark(lot.lot_id, "ep_to_oht_start")
        self._route_mark(lot.lot_id, "ep_to_oht_from", ep_port)
        self._route_mark(lot.lot_id, "ep_to_oht_to", "OHT")
        _rm_evt: Dict[str, str] = {
            "seq": "REMOVED",
            "port_id": ep_port,
            "lot_id": lot.lot_id,
            # JSON 재생 속도 자동 배속(공정시간 동기화)용
            "proc_sec": f"{float(unload_time):.3f}",
        }
        self._enrich_lot_payload(_rm_evt, lot, fix_key, unload_time)
        self._emit_event(_rm_evt)
        _rm_aj = _log_anim_removed_ep_json(ep_port)
        proc_txt = (
            f"공정시간 우선: {total_wait:.1f}s (공정 {proc_only:.1f}s)"
            if self._process_time_priority
            else f"공정시간: {total_wait:.1f}s (JSON {aw_u:.1f}s)"
        )
        self._log_event_block(
            seq="REMOVED",
            summary=f"{ep_port} -> OHT 회수 실행",
            lot_id=lot_disp,
            anim_line=f"애니메이션: {_rm_aj} (추정 {aw_u:.1f}s)",
            proc_line=proc_txt,
        )
        _rm_prog: Dict[str, str] = {}
        self._enrich_lot_payload(_rm_prog, lot, fix_key, unload_time)
        yield self.env.process(
            self._wait_with_progress(
                total_sec=total_wait,
                label=f"{ep_port}->OHT {lot_disp}",
                detail=f"{lot_disp} {ep_port}->OHT 회수(출발포트={ep_port}, 도착포트=OHT) | 공정={unload_time:.1f}s 애니={aw_u:.1f}s",
                proc_sec=unload_time,
                anim_sec=float(anim_wait),
                progress_interval=self._log_cfg.progress_interval(),
                event_seq="REMOVED",
                linked_anim_json=_rm_aj,
                from_port_id=ep_port,
                to_port_id="OHT",
                lot_id=lot.lot_id,
                port_id=ep_port,
                progress_extra=_rm_prog or None,
            )
        )
        self._stage_mark(lot.lot_id, "ep_to_oht_end")
        self._remove_from_port(ep_port)
        # 완료 상태(포트 점유/매핑 prim)를 즉시 반영하기 위한 갱신 이벤트.
        self._emit_port_occ_refresh("EP 회수 완료 후 포트 표시 갱신")
        self.completed_lots.append(lot.lot_id)
        self._log(
            f"{lot_disp} | 회수완료 {len(self.completed_lots)}/{self._total_lots} | q={len(self._oht_input_queue)}"
        )

    def _set_port(self, port: str, event_cd: str, start_cd: str, lot: Lot, emit_arrived_event: bool = True) -> None:
        """포트 점유·상태코드를 갱신한다. 필요 시 ARRIVED 이벤트."""
        self.ports[port] = lot
        self.port_event_cd[port] = event_cd
        self.port_start_cd[port] = start_cd
        if port in self._ep_ports:
            # FOUP 공정이 추가되면 "EP 안착 즉시 회수대기"가 아니라,
            # FOUP 공정 완료(+종료 -3.2 이동 1초 완료) 이후에만 회수대기 상태가 된다.
            # 따라서 EP 안착 시점에는 awaiting_pickup을 켜지 않는다.
            self._ep_awaiting_pickup[port] = False
            self._ep_ready_since[port] = 0.0
        # 정책: BP2~BP4는 "경유 버퍼"이므로 ARRIVED(안착) 이벤트를 별도로 emit 하지 않는다.
        # (BP1->BPx 이송 이벤트(MOVE_TRANSFERING)만으로 애니/로그를 대표)
        if emit_arrived_event and port not in self._buffer_ports:
            self._emit_event({"seq": "ARRIVED", "port_id": port, "lot_id": lot.lot_id})

    def _remove_from_port(self, port: str) -> None:
        """포트를 비우고 READY_TO_LOAD/EMPTY로 돌리며, EP 회수 플래그를 끈다."""
        self.ports[port] = None
        self.port_event_cd[port] = "READY_TO_LOAD"
        self.port_start_cd[port] = "EMPTY"
        if port in self._ep_ports:
            self._ep_awaiting_pickup[port] = False
            self._ep_ready_since[port] = 0.0
        if port in self._buffer_ports:
            self._buffer_empty_since[port] = float(self.env.now) if self.env is not None else 0.0
        self._emit_event({"seq": "READYTOLOAD", "port_id": port, "lot_id": ""})

    def _ports_snapshot(self) -> str:
        """로그용: 모든 포트의 점유 LOT id를 한 줄 문자열로."""
        parts: List[str] = []
        for p in self._all_ports:
            lot = self.ports.get(p)
            parts.append(f"{p}:{lot.lot_id if lot else '-'}")
        return ", ".join(parts)

    def _log_heartbeat_if_due(self) -> None:
        if self.env is None:
            return
        now = float(self.env.now)
        interval = self._log_cfg.heartbeat_interval()
        if interval <= 0.0:
            return
        if not self._status_log_policy.may_log_heartbeat(now, interval):
            return
        next_lot = self._oht_input_queue[0] if self._oht_input_queue else None
        next_text = f"{next_lot.sequence}번째({next_lot.lot_id})" if next_lot else "-"
        ports = self._ports_snapshot()
        if not self._status_log_policy.should_emit_heartbeat(
            now=now,
            completed=len(self.completed_lots),
            total=self._total_lots,
            next_text=next_text,
            queue_len=len(self._oht_input_queue),
            pickup_tickets=self._pickup_tickets,
            ports_snapshot=ports,
        ):
            return
        self._log(
            f"[HB] {len(self.completed_lots)}/{self._total_lots} | next={next_text} "
            f"| q={len(self._oht_input_queue)} | 티={self._pickup_tickets} | {ports}"
        )

    def _apply_initial_full_ports(self) -> str:
        """시작 시 지정 포트에 미리 LOT을 올려 _total_lots에 반영한다.

        ARRIVED 이벤트는 보내지 않는다(애니/공정확인 없이 '이미 도착한 상태'만 반영).
        반환: SIM_START 블록에 넣을 초기 적재 요약 문자열.
        """
        ports = list(getattr(self._init_cfg, "initial_full_ports", None) or [])
        if not ports:
            return "(없음)"
        valid = set(self._all_ports)
        now = float(self.env.now) if self.env is not None else 0.0
        applied: List[str] = []
        for p in ports:
            port = str(p).strip().upper()
            if port not in valid:
                continue
            if self.ports.get(port) is not None:
                continue
            # 초기 적재 LOT은 시뮬레이션 생성 LOT과 별도로 "이미 존재하던 LOT"으로 취급
            lot = Lot(
                lot_id=f"LOT_A{self._initial_seed_seq}",
                foup_id=f"FOUP_A{self._initial_seed_seq}",
                sequence=0,
            )
            self._initial_seed_seq += 1
            self._total_lots += 1
            self._set_port(port, "ARRIVED", "FULL", lot, emit_arrived_event=False)
            if port in self._buffer_ports:
                self._buffer_loaded_at[port] = now
            # FOUP 공정(EP 상)은 "안착 후 공정"이므로, 초기 적재로 EP가 FULL이면
            # 해당 EP도 공정이 시작되어야 한다. (그렇지 않으면 회수대기 상태가 영원히 켜지지 않아 시뮬이 멈춘다)
            if port in self._ep_ports:
                try:
                    if self.env is not None:
                        self.env.process(self._run_ep_foup_process(port, lot))
                except Exception:
                    pass
            applied.append(f"{port}={lot.lot_id}")
        if applied:
            return ", ".join(applied)
        return "(없음)"

    def _wait_with_progress(
        self,
        total_sec: float,
        label: str,
        detail: str,
        proc_sec: float = 0.0,
        anim_sec: float = 0.0,
        progress_interval: float = 5.0,
        event_seq: str = "",
        linked_anim_json: str = "",
        port_hint: str = "",
        # UI가 "애니 종료 직후 포트 갱신"을 이벤트 단위로 정확히 적용할 수 있도록,
        # progress payload에도 from/to/lot/port 정보를 포함한다(엔진은 공정 종료 후에만 occ가 바뀌기 때문).
        from_port_id: str = "",
        to_port_id: str = "",
        lot_id: str = "",
        port_id: str = "",
        progress_extra: Optional[Dict[str, str]] = None,
    ):
        """
        공정 대기 시간을 simpy timeout으로 소모하고 진행률을 낸다.

        정책:
        - progress_interval <= 0: 중간 진행 출력 없이 DONE만 emit (기존 동작)
        - progress_interval > 0: 텍스트 로그([PROGRESS])는 누적하지 않고, on_progress(UI)만 주기적으로 갱신
          (요구사항: 설정한 초마다 %만 반영되도록)
        - linked_anim_json: UI 진행현황에 표시할 ``data/sim_sequences`` 기준 파일명(로그 anim_line 과 동일).
        - port_hint: UI 가 진행 라벨을 포트별로 분리해 표시할 때 라우팅 키로 쓰는 포트 ID(예: "EP1").
          비어 있어도 동작에는 영향이 없다.
        """
        total = max(0.01, float(total_sec))
        interval = self._progress_emit_policy.normalize_interval(float(progress_interval))
        ev = str(event_seq or "").strip()
        aj = str(linked_anim_json or "").strip()
        ph = str(port_hint or "").strip().upper()
        fr = str(from_port_id or "").strip().upper()
        to = str(to_port_id or "").strip().upper()
        lot = str(lot_id or "").strip()
        pid = str(port_id or "").strip().upper()
        psec = max(0.0, float(proc_sec))
        asec = max(0.0, float(anim_sec))
        _pex = dict(progress_extra or {})

        def _pl(core: Dict[str, str]) -> Dict[str, str]:
            if not _pex:
                return core
            merged = dict(core)
            merged.update(_pex)
            return merged

        try:
            event_start_sim_time = f"{float(self.env.now):.2f}" if self.env is not None else "0.00"
        except Exception:
            event_start_sim_time = "0.00"
        # UI 표시용: 공정/애니 시간을 각각 제공한다.
        # total_sec은 호출자가 (공정시간우선 ON이면 공정, OFF면 max(공정,애니)) 규칙으로 이미 결정한다.
        self._emit_progress(_pl({
            "label": label,
            "detail": detail,
            "event_seq": ev,
            "linked_anim_json": aj,
            "port_id": ph,
            "from_port_id": fr,
            "to_port_id": to,
            "lot_id": lot,
            "event_port_id": pid,
            "event_start_sim_time": event_start_sim_time,
            "proc_sec": self._progress_emit_policy.format_sec_1(psec),
            "anim_sec": self._progress_emit_policy.format_sec_1(asec),
            "process_time_priority": "1" if self._process_time_priority else "0",
            "status": "RUNNING",
            "elapsed": "0.0",
            "total": self._progress_emit_policy.format_sec_1(total),
            "percent": "0",
        }))
        if interval <= 0.0:
            # 로그 주기 0: 단계 완료 전에는 진행 로그를 출력하지 않음
            yield self.env.timeout(total)
            self._emit_progress(_pl({
                "label": label,
                "detail": detail,
                "event_seq": ev,
                "linked_anim_json": aj,
                "port_id": ph,
                "from_port_id": fr,
                "to_port_id": to,
                "lot_id": lot,
                "event_port_id": pid,
                "event_start_sim_time": event_start_sim_time,
                "proc_sec": self._progress_emit_policy.format_sec_1(psec),
                "anim_sec": self._progress_emit_policy.format_sec_1(asec),
                "process_time_priority": "1" if self._process_time_priority else "0",
                "status": "DONE",
                "elapsed": self._progress_emit_policy.format_sec_1(total),
                "total": self._progress_emit_policy.format_sec_1(total),
                "percent": "100",
            }))
            self._log_wait_step_done(label, total)
            # 공정시간우선 ON이고 공정이 애니보다 짧으면, 100% 시점에 애니를 즉시 중단/초기화한다.
            if self._process_time_priority and asec > psec + 1e-6:
                cb = getattr(self, "_interrupt_anim_cb", None)
                if cb is not None:
                    try:
                        cb(self._event_tags)  # type: ignore[misc]
                    except TypeError:
                        try:
                            cb()
                        except Exception:
                            pass
                    except Exception:
                        pass
            return
        elapsed = 0.0
        while elapsed + 1e-9 < total:
            step = min(interval, total - elapsed)
            yield self.env.timeout(step)
            elapsed += step
            remain = max(0.0, total - elapsed)
            pct = (elapsed / total) * 100.0
            self._emit_progress(_pl({
                "label": label,
                "detail": detail,
                "event_seq": ev,
                "linked_anim_json": aj,
                "port_id": ph,
                "from_port_id": fr,
                "to_port_id": to,
                "lot_id": lot,
                "event_port_id": pid,
                "event_start_sim_time": event_start_sim_time,
                "proc_sec": self._progress_emit_policy.format_sec_1(psec),
                "anim_sec": self._progress_emit_policy.format_sec_1(asec),
                "process_time_priority": "1" if self._process_time_priority else "0",
                "status": "DONE" if remain <= 1e-9 else "RUNNING",
                "elapsed": self._progress_emit_policy.format_sec_1(elapsed),
                "total": self._progress_emit_policy.format_sec_1(total),
                "percent": self._progress_emit_policy.format_percent(pct),
            }))
        self._log_wait_step_done(label, total)
        if self._process_time_priority and asec > psec + 1e-6:
            cb = getattr(self, "_interrupt_anim_cb", None)
            if cb is not None:
                try:
                    cb(self._event_tags)  # type: ignore[misc]
                except TypeError:
                    try:
                        cb()
                    except Exception:
                        pass
                except Exception:
                    pass

    def _log_brief_step(self, lot_id: str, route: str, proc_sec: float, anim_sec: float) -> None:
        """이력용 한 줄 요약(진행현황 detail과 동일 톤)."""
        self._log(f"{lot_id} | {route} | 공정={proc_sec:.1f}s 애니={anim_sec:.1f}s")

    def _emit_event(self, payload: Dict[str, str]) -> None:
        """UI·애니메이션으로 보내는 시뮬 이벤트. sim_time·ports_occupancy를 덧붙인다."""
        payload = dict(payload or {})
        try:
            payload["sim_time"] = f"{float(self.env.now):.2f}" if self.env is not None else "0.00"
        except Exception:
            payload["sim_time"] = "0.00"
        # 상태 기반 애니메이션 룰 매칭용 포트 점유 스냅샷.
        # rules의 when.ports_occupancy는 이 스냅샷을 기준으로 평가된다.
        try:
            occ: Dict[str, str] = {}
            for p in self._all_ports:
                lot = self.ports.get(p)
                occ[p] = lot.lot_id if lot else ""
            payload["ports_occupancy"] = occ
        except Exception:
            payload["ports_occupancy"] = {}
        try:
            payload["foup_proc_active_ep"] = str(getattr(self, "_foup_proc_active_ep", "") or "").strip().upper()
        except Exception:
            payload["foup_proc_active_ep"] = ""
        # 병렬 2레일: 재생·UI 가 주(OHT)/보조(MOVE) 를 가르도록 태그
        if bool(getattr(self, "_parallel_nonconflicting_moves", False)):
            try:
                from .sim_parallel_rails import classify_sim_rail

                seq_u = str(payload.get("seq") or payload.get("event") or "").strip()
                rail = classify_sim_rail(seq_u)
                if rail:
                    payload["sim_rail"] = rail
            except Exception:
                pass
        if self._on_event:
            try:
                merged = dict(payload or {})
                merged.update(self._event_tags)
                self._on_event(merged)
            except Exception:
                pass

    def _emit_progress(self, payload: Dict[str, str]) -> None:
        """UI 진행률 바/상세: sim_time을 붙여 on_progress 콜백으로 전달."""
        payload = dict(payload or {})
        # 호출자가 sim_time을 명시한 경우(가상 sim time 등)에는 덮어쓰지 않는다.
        if "sim_time" not in payload:
            try:
                payload["sim_time"] = f"{float(self.env.now):.2f}" if self.env is not None else "0.00"
            except Exception:
                payload["sim_time"] = "0.00"
        # 중요(포트상태/막대 안정성):
        # UI는 ports_occupancy를 기준으로 포트상태 패널과 EP 타임라인을 갱신한다.
        # progress payload에 ports_occupancy가 누락되면, UI가 "전 포트 EMPTY"로 덮어쓰는 경로가 생겨
        # 포트가 동시에 사라지거나 깜빡이는 현상이 발생할 수 있다.
        # 따라서 on_progress payload에도 항상 ports_occupancy(전체 포트 스냅샷)를 포함한다.
        try:
            occ_all: Dict[str, str] = {}
            for p in self._all_ports:
                lot = self.ports.get(p)
                occ_all[str(p)] = lot.lot_id if lot else ""
            # 빈 dict 금지: 최소한 _all_ports 키는 항상 채워진다.
            payload["ports_occupancy"] = occ_all
        except Exception:
            # 예외 시에도 UI가 전부 비는 것을 막기 위해, 가능한 키는 유지한다.
            try:
                if "ports_occupancy" not in payload or not isinstance(payload.get("ports_occupancy"), dict):
                    payload["ports_occupancy"] = {}
            except Exception:
                payload["ports_occupancy"] = {}
        # 진행현황 막대그래프용 EP 점유 스냅샷(시뮬 시간 기준)
        try:
            ep_ports = list(getattr(self, "_ep_ports", []) or [])
        except Exception:
            ep_ports = []
        try:
            # UI 타임라인이 "EP 줄"을 안정적으로 만들 수 있도록 포트 목록도 함께 보낸다.
            payload["ep_ports"] = list(ep_ports)
            ep_occ: Dict[str, str] = {}
            all_empty = True
            for ep in ep_ports:
                is_empty = self.ports.get(ep) is None
                ep_occ[str(ep)] = "EMPTY" if is_empty else "FULL"
                if not is_empty:
                    all_empty = False
            payload["ep_occ"] = ep_occ
            payload["all_ep_empty"] = "1" if (bool(ep_ports) and all_empty) else "0"
        except Exception:
            payload["ep_ports"] = []
            payload["ep_occ"] = {}
            payload["all_ep_empty"] = "0"
        try:
            payload["sim_total_est_sec"] = f"{float(getattr(self, '_sim_total_est_sec', 0.0) or 0.0):.2f}"
        except Exception:
            payload["sim_total_est_sec"] = "0.00"
        try:
            payload["foup_proc_active_ep"] = str(getattr(self, "_foup_proc_active_ep", "") or "").strip().upper()
        except Exception:
            payload["foup_proc_active_ep"] = ""
        if bool(getattr(self, "_parallel_nonconflicting_moves", False)):
            try:
                from .sim_parallel_rails import classify_sim_rail

                seq_u = str(
                    payload.get("event_seq") or payload.get("seq") or payload.get("event") or ""
                ).strip()
                rail = classify_sim_rail(seq_u)
                if rail:
                    payload["sim_rail"] = rail
            except Exception:
                pass
        if self._on_progress:
            try:
                merged = dict(payload or {})
                merged.update(self._event_tags)
                self._on_progress(merged)
            except Exception:
                pass

    def _request_gate(self, payload: Dict[str, str]) -> float:
        """공정 확인 UI(on_gate)를 띄우고, 반환된 float(초)만큼 애니 대기 시간으로 합산한다."""
        cb = self._on_gate
        if cb is None:
            return 0.0
        with self._gate_lock:
            # 게이트 콜백은 UI와 동기 통신하므로 직렬화를 위해 lock을 강제한다.
            # (다중 공정에서 dialog 중복 생성 방지)
            try:
                merged = dict(payload or {})
                merged.update(self._event_tags)
                res = cb(merged)
                # on_gate는 "단계 확인"을 위한 훅이지만,
                # 추가 요구사항(애니메이션이 더 길면 다음 공정 대기)을 위해
                # float(예상 애니메이션 길이, sec)을 반환할 수 있도록 확장한다.
                if isinstance(res, (int, float)):
                    return max(0.0, float(res))
                return 0.0
            except Exception:
                return 0.0

    def _stage_mark(self, lot_id: str, key: str) -> None:
        """LOT별 공정 단계 시각을 기록한다. _log_final_summary에서 구간별 소요 시간 계산에 사용."""
        if not lot_id:
            return
        if lot_id not in self._lot_stage_summary:
            self._lot_stage_summary[lot_id] = {}
        t = float(self.env.now) if self.env is not None else 0.0
        self._lot_stage_summary[lot_id][key] = t

    def _route_mark(self, lot_id: str, key: str, value: str) -> None:
        """LOT별 이동 구간(from/to 포트 등) 문자열을 기록해 요약 로그에 출력한다."""
        if not lot_id:
            return
        if lot_id not in self._lot_route_summary:
            self._lot_route_summary[lot_id] = {}
        self._lot_route_summary[lot_id][key] = str(value or "")

    def _dur(self, m: Dict[str, float], s: str, e: str) -> float:
        """_stage_mark 두 키 사이의 시각 차(초). 없으면 -1."""
        if s not in m or e not in m:
            return -1.0
        return max(0.0, float(m[e]) - float(m[s]))

    def _log_final_summary(self) -> None:
        """완료 LOT별로 _stage_mark/_route_mark 기록을 모아 구간별 소요 시간 로그 출력."""
        total_t = float(self.env.now) if self.env is not None else 0.0
        lines: List[str] = [
            f"[SUMMARY] 전체 t={total_t:.2f}s | 공정설정 시간 우선={'ON' if self._process_time_priority else 'OFF'}",
            f"[SUMMARY] 총 공정시간={total_t:.2f}s (막대그래프 총 시간과 일치)",
        ]
        for lot_id in self.completed_lots:
            m = self._lot_stage_summary.get(lot_id, {})
            r = self._lot_route_summary.get(lot_id, {})
            d1 = self._dur(m, "oht_to_inout_start", "oht_to_inout_end")
            d2 = self._dur(m, "inout_to_bp_start", "inout_to_bp_end")
            d3 = self._dur(m, "bp_to_ep_start", "bp_to_ep_end")
            d5 = self._dur(m, "ep_to_oht_start", "ep_to_oht_end")
            parts = []
            parts.append(f"OHT->IN/OUT={d1:.1f}s" if d1 >= 0 else "OHT->IN/OUT=-")
            bp1_bp_from = r.get("bp1_to_bp_from", INOUT_PORT)
            bp1_bp_to = r.get("bp1_to_bp_to", "?")
            parts.append(f"{bp1_bp_from}->{bp1_bp_to}={d2:.1f}s" if d2 >= 0 else f"{bp1_bp_from}->{bp1_bp_to}=-")
            bp_ep_from = r.get("bp_to_ep_from", "?")
            bp_ep_to = r.get("bp_to_ep_to", "?")
            parts.append(f"{bp_ep_from}->{bp_ep_to}={d3:.1f}s" if d3 >= 0 else f"{bp_ep_from}->{bp_ep_to}=-")
            ep_oht_from = r.get("ep_to_oht_from", "EP?")
            ep_oht_to = r.get("ep_to_oht_to", "OHT")
            parts.append(f"{ep_oht_from}->{ep_oht_to}={d5:.1f}s" if d5 >= 0 else f"{ep_oht_from}->{ep_oht_to}=-")
            lines.append(f"  · {lot_id} | " + ", ".join(parts))
        # 포트별 유휴/고장 누적(보고서용)
        lines.append("[SUMMARY] 포트별 유휴(EMPTY) 누적(초)")
        for p in self._all_ports:
            lines.append(f"  - {p}: {float(self._idle_sec.get(p, 0.0)):.2f}")
        lines.append("[SUMMARY] 포트별 고장(비가동) 누적(초)")
        for p in self._all_ports:
            lines.append(f"  - {p}: {float(self._faulty_sec.get(p, 0.0)):.2f}")

        # EP 점유 타임라인 요약(요구사항)
        # - UI 막대그래프 우측의 누적 EMPTY 시간과 "같은 원천 데이터"를 사용한다.
        # - 출력 키:
        #   - {EP}_EMPTY: 해당 EP가 비어있던 누적 시간(초)
        #   - ALL_EP_EMPTY: 모든 EP가 동시에 비어있던 누적 시간(초)
        try:
            ep_ports = list(getattr(self, "_ep_ports", []) or [])
        except Exception:
            ep_ports = []
        if ep_ports:
            lines.append("[SUMMARY] EP별 비어있던 시간(초) + 전체 EP 모두 비어있던 시간(초)")
            for ep in ep_ports:
                lines.append(f"  - {ep}_EMPTY: {float(self._ep_empty_sec.get(ep, 0.0)):.2f}")
            lines.append(f"  - ALL_EP_EMPTY: {float(getattr(self, '_all_ep_empty_sec', 0.0) or 0.0):.2f}")

        text = "\n".join(lines)
        self._last_report_text = text
        self._log(text)

    def _log(self, msg: str) -> None:
        """시뮬 시각 접두를 붙여 콘솔·on_log로 출력."""
        try:
            t = float(self.env.now) if self.env is not None else 0.0
        except Exception:
            t = 0.0
        line = f"[t={t:6.2f}] {msg}"
        if self._print_to_console:
            print(line, flush=True)
        if self._on_log:
            try:
                self._on_log(line)
            except Exception:
                pass

    def set_console_logging_enabled(self, enabled: bool) -> None:
        """콘솔 print 여부만 토글(on_log는 유지)."""
        self._print_to_console = bool(enabled)
