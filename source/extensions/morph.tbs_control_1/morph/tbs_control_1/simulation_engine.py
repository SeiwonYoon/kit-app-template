from __future__ import annotations

"""
simulation_engine.py — TBS simpy 공정 시뮬레이션 코어

【이 파일의 역할】
- 공정 포트(BP/EP), OHT 입력/회수, LOT 이동/공정/완료를 simpy 이벤트로 실행한다.
- UI(control_window.py)와는 콜백(on_log/on_event/on_progress/on_gate)으로만 통신한다.
- 애니메이션 실행에 필요한 이벤트 payload(seq/from/to/port/lot/ports_occupancy)를 생성한다.

【핵심 데이터 구조】
- Lot: lot_id/foup_id/sequence (EP 안착 시 곧바로 회수 대기 가능; EP 상 별도 가공 시간 없음).
- SimulationTimingConfig:
  · OHT→BP1 경유 또는 OHT→EP 직접 투입 이동 시간(oht_to_bp1_*)
  · LOT 생성 간격(lot_spawn_interval_*): 타이머마다 대기열에 LOT 추가
  · 회수 이벤트 간격(pickup_event_interval_*): READYTOUNLOAD 실행 “티켓” 누적
  · BP1→BP, BP→EP, EP→OHT(회수 이동) 랜덤 범위
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
2) _run_serial_flow: 회수 티켓 → OHT 투입 → BP→EP 이동(EP 안착 시 즉시 회수 대기)
3) EP에서는 별도 PROCESS 대기 없음; 회수 티켓으로 READYTOUNLOAD+EP→OHT 실행
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
   - data/sim_sequences/*.json 파일 생성
   - rules/map에 경로 등록(use.json)
3) UI 입력 항목 추가(시간/옵션)
   - control_window.py 모델/필드 추가
   - on_sim_start_clicked에서 config로 전달
   - 이 파일 config dataclass와 사용 함수에 연결
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional
import random
import threading


try:
    import simpy  # type: ignore
except Exception:
    simpy = None


BP1_TO_BP_MIN = 5.0
BP1_TO_BP_MAX = 10.0
BP_TO_EP_MIN = 5.0
BP_TO_EP_MAX = 10.0
EP_TO_OHT_MIN = 5.0
EP_TO_OHT_MAX = 10.0
OHT_TO_BP1_MIN = 5.0
OHT_TO_BP1_MAX = 10.0

BUFFER_PORTS = ("BP2", "BP3", "BP4")

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
BASE_PORTS = ("BP1", "BP2", "BP3", "BP4")


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
    """구간별 이동·스폰·회수 티켓 간격의 난수 범위(초). rand_* 로 샘플링한다."""
    oht_to_bp1_min: float = OHT_TO_BP1_MIN
    oht_to_bp1_max: float = OHT_TO_BP1_MAX
    bp1_to_bp_min: float = BP1_TO_BP_MIN
    bp1_to_bp_max: float = BP1_TO_BP_MAX
    bp_to_ep_min: float = BP_TO_EP_MIN
    bp_to_ep_max: float = BP_TO_EP_MAX
    ep_to_oht_min: float = EP_TO_OHT_MIN
    ep_to_oht_max: float = EP_TO_OHT_MAX
    # OHT 측 LOT 생성(대기열 적재) 간격
    lot_spawn_interval_min: float = 15.0
    lot_spawn_interval_max: float = 40.0
    # READYTOUNLOAD(회수 시도) 이벤트 간격 — 공정 시간과 별개
    pickup_event_interval_min: float = 50.0
    pickup_event_interval_max: float = 70.0

    @staticmethod
    def _norm(a: float, b: float) -> tuple:
        """최소·최대를 정규화하고 하한을 0.01초로 맞춘다(역순 입력 교정)."""
        lo, hi = float(a), float(b)
        if lo > hi:
            lo, hi = hi, lo
        return (max(0.01, lo), max(0.01, hi))

    def rand_oht_to_bp1(self) -> float:
        """OHT→BP1(또는 OHT→EP 직접 투입에 쓰이는 동일 분포) 이동 시간(초) 난수."""
        lo, hi = self._norm(self.oht_to_bp1_min, self.oht_to_bp1_max)
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
    """시뮬 시작 조건: EP 개수·초기 적재 포트·OHT가 추가 투입할 LOT 수."""

    ep_count: int = 2
    initial_full_ports: Optional[List[str]] = None
    max_oht_lots: int = 0


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
    ) -> None:
        """lots: 초기 LOT 목록(보통 비움). 타이밍·초기화·콜백을 묶어 엔진 상태를 구성한다."""
        self._lots = list(lots)
        self._timing = timing or SimulationTimingConfig()
        self._log_cfg = log_config or SimulationLogConfig()
        self._init_cfg = init_config or SimulationInitConfig()
        self._on_log = on_log
        self._on_event = on_event
        self._on_progress = on_progress
        self._on_gate = on_gate
        self._print_to_console = bool(print_to_console)
        self._running = False
        self._done = False
        self._deadlock = False
        self._sim_budget_sec = 0.0

        self.env = simpy.Environment() if simpy else None
        ep_count = int(getattr(self._init_cfg, "ep_count", 2) or 2)
        ep_count = 3 if ep_count >= 3 else 2
        self._ep_ports = EP_PORTS_MAX[:ep_count]
        self._all_ports = BASE_PORTS + self._ep_ports

        self.ports: Dict[str, Optional[Lot]] = {p: None for p in self._all_ports}
        self.port_start_cd: Dict[str, str] = {p: "EMPTY" for p in self._all_ports}
        self.port_event_cd: Dict[str, str] = {p: "READY_TO_LOAD" for p in self._all_ports}
        self._buffer_loaded_at: Dict[str, float] = {}
        self._buffer_empty_since: Dict[str, float] = {p: 0.0 for p in BUFFER_PORTS}
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
        self._status_log_policy.reset()
        self._total_lots = 0
        self._pickup_tickets = 0
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
            dt = self._timing.rand_lot_spawn_interval()
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
            self._emit_event(
                {
                    "seq": "READYTOLOAD",
                    "port_id": "OHT",
                    "lot_id": lot.lot_id,
                    "lot_seq": str(lot.sequence),
                    "foup_id": lot.foup_id,
                    "queue_len": str(len(self._oht_input_queue)),
                }
            )
            self._log_event_block(
                seq="READYTOLOAD",
                summary=f"LOT 생성·OHT 대기열 적재 (spawn {self._oht_spawn_seq}/{self._max_oht_lots}, queue={len(self._oht_input_queue)})",
                lot_id=lot.lot_id,
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
            dt = self._timing.rand_pickup_event_interval()
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

    def tick(self, sim_delta_sec: float) -> None:
        """UI 프레임 등에서 호출: wall-clock 델타를 sim 예산으로 쌓아 env.step()으로 sim time을 진행한다."""
        if not self.env or not self._running or self._done:
            return
        if sim_delta_sec <= 0:
            sim_delta_sec = 1.0 / 60.0
        # wall-clock 기반 tick을 누적해 sim time budget으로 사용.
        # (env.now가 아직 안 움직이는 구간에서도 budget은 계속 쌓여야 한다)
        self._sim_budget_sec += float(sim_delta_sec)
        steps = 0
        while self._running and not self._done:
            next_t = self.env.peek()
            if next_t == float("inf"):
                break
            cur_t = float(self.env.now)
            need = max(0.0, float(next_t) - cur_t)
            if need > self._sim_budget_sec + 1e-12:
                break
            # 같은 시각 이벤트(need=0)는 budget 소모 없이 연쇄 처리
            self._sim_budget_sec = max(0.0, self._sim_budget_sec - need)
            self.env.step()
            steps += 1
            if steps > 10000:
                self._log("[SIM] 내부 step guard 발동")
                break

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
        직렬 실행(메인 오케스트레이터).

        유지보수 관점에서 "시뮬이 다음에 무엇을 할지 결정하는 곳"을 이 함수 1곳으로 고정한다.
        세부 구현은 _step_* 헬퍼로 분리하되, 실행 순서/우선순위는 여기서만 바꾼다.

        우선순위(상단일수록 먼저 시도):
        - 0) BP1 -> BUFFER (BP1 적재분이 있으면 즉시 버퍼로)
        - 1) EP -> OHT 회수 (pickup 티켓이 있으면 FIFO EP 회수)
        - 2) OHT 투입 (빈 EP면 direct, 아니면 BP1 경유)
        - 3) BUFFER -> EP 채움
        - 4) 대기 로그 + 짧은 sleep
        """
        yield self.env.timeout(0.1)
        self._log(f"[시작] OHT 추가 LOT 목표={self._max_oht_lots}")

        while self._running and len(self.completed_lots) < self._total_lots:
            self._log_heartbeat_if_due()

            did = yield from self._step_bp1_to_buffer()
            if did:
                continue

            did = yield from self._step_pickup_to_oht()
            if did:
                continue
            if len(self.completed_lots) >= self._total_lots:
                break

            did = yield from self._step_buffer_to_ep()
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

    def _step_bp1_to_buffer(self):
        """0) BP1 적재분(초기 포함)을 버퍼로 1회 이송 가능하면 실행 후 True."""
        if self.ports.get("BP1") is not None and self._find_oldest_empty_buffer():
            yield self.env.process(self._move_bp1_to_buffer())
            return True
        return False

    def _step_pickup_to_oht(self):
        """
        1) 회수 티켓 처리: 가능한 EP를 FIFO로 회수한다.
        한 번이라도 회수를 수행하면 True를 반환(루프를 즉시 상단으로 돌려 상태를 재평가).
        """
        did_pickup = False
        while self._pickup_tickets > 0 and len(self.completed_lots) < self._total_lots:
            ep_pick = self._find_ep_awaiting_pickup()
            if not ep_pick:
                break
            self._pickup_tickets -= 1
            did_pickup = True
            yield self.env.process(self._execute_pickup(ep_pick))
            if len(self.completed_lots) >= self._total_lots:
                break
        return did_pickup

    def _step_oht_input(self):
        """2) OHT 투입: direct(빈 EP) 우선, 아니면 BP1 경유. 1건 실행하면 True."""
        # READYTOLOAD(생성/준비) 공정확인을 통과하지 않은 LOT은 아직 투입 공정으로 가져가지 않는다.
        if self._oht_input_queue and not bool(getattr(self._oht_input_queue[0], "ready_to_load_confirmed", True)):
            return False

        if self._oht_input_queue and self._can_load_to_ep_direct():
            ep_target = self._find_empty_ep()
            if ep_target:
                lot = self._oht_input_queue.pop(0)
                self._log(f"{lot.lot_id} | 직접투입→{ep_target} | q={len(self._oht_input_queue)}")
                yield self.env.process(self._load_lot_to_ep_direct(lot, ep_target))
                return True

        if self._oht_input_queue and self._can_load_to_bp1():
            lot = self._oht_input_queue.pop(0)
            self._log(f"{lot.lot_id} | OHT→BP1 투입 | q={len(self._oht_input_queue)}")
            yield self.env.process(self._load_lot_to_bp1(lot))
            return True

        return False

    def _step_buffer_to_ep(self):
        """3) 버퍼 → EP 1회 이송 가능하면 실행 후 True."""
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
        # READYTOLOAD 확인 직후 즉시 다음 공정(ARRIVED)을 우선 시도할 수 있도록 wakeup 이벤트를 함께 기다린다.
        try:
            if self.env is not None and getattr(self, "_serial_wakeup", None) is not None:
                yield simpy.AnyOf(self.env, [self.env.timeout(0.2), self._serial_wakeup])  # type: ignore
                return
        except Exception:
            pass
        yield self.env.timeout(0.2)

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
                        f"[대기] BP1={'FULL' if self.ports['BP1'] else 'EMPTY'} "
                        f"| next={nid} | q={len(self._oht_input_queue)}"
                    )
                yield self.env.timeout(0.2)
                continue
            lot = self._oht_input_queue.pop(0)
            self._log(f"{lot.lot_id} | BP1 투입시작 | q={len(self._oht_input_queue)}")
            yield self.env.process(self._load_lot_to_bp1(lot))
        self._log("[입력] 루프 종료")

    def _can_load_to_bp1(self) -> bool:
        """OHT LOT을 BP1에 넣을 수 있는지: BP1 비어 있고 버퍼에 빈 슬롯이 있으며 BP1 적재 중 아님."""
        bp1_empty = self.ports["BP1"] is None
        any_buffer_empty = any(self.ports[p] is None for p in BUFFER_PORTS)
        return bp1_empty and any_buffer_empty and not self._oht_loading_bp1

    def _can_load_to_ep_direct(self) -> bool:
        """OHT 대기열 LOT을 EP로 직접 넣을 수 있는지(빈 EP 존재 + BP1 적재 중 아님)."""
        if self._oht_loading_bp1:
            return False
        return self._find_empty_ep() is not None

    def _load_lot_to_ep_direct(self, lot: Lot, ep_port: str):
        """OHT 대기열 LOT을 EP에 직접 투입(ARRIVED + 대기 후 _set_port)."""
        oht_time = self._timing.rand_oht_to_bp1()
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
        total_wait = max(float(oht_time), float(anim_wait))
        self._stage_mark(lot.lot_id, "oht_to_bp1_start")
        self._log_brief_step(lot.lot_id, f"OHT→{ep_port}", oht_time, anim_wait)
        # 요구사항: OHT 이동은 ARRIVED(도착/안착) 이벤트로 통일. from/to를 포함해 UI 매핑에 사용.
        self._emit_event({"seq": "ARRIVED", "from_port_id": "OHT", "to_port_id": ep_port, "port_id": ep_port, "lot_id": lot.lot_id})
        _ep_aj = _log_anim_arrived_ep_json(ep_port)
        self._log_event_block(
            seq="ARRIVED",
            summary=f"OHT -> {ep_port} 직접 투입",
            lot_id=lot.lot_id,
            anim_line=f"애니메이션: {_ep_aj} (추정 {anim_wait:.1f}s)",
            proc_line=f"공정시간: {total_wait:.1f}s (max(공정 {oht_time:.1f}s, 애니 {anim_wait:.1f}s))",
        )
        yield self.env.process(
            self._wait_with_progress(
                total_sec=total_wait,
                label=f"OHT->{ep_port} {lot.lot_id}",
                detail=f"{lot.lot_id} OHT->{ep_port} 직접투입(도착포트={ep_port}) | 공정={oht_time:.1f}s 애니={anim_wait:.1f}s",
                progress_interval=self._log_cfg.progress_interval(),
                event_seq="ARRIVED",
            )
        )
        # ARRIVED 이벤트는 위에서 이미 emit 했으므로, 여기서 _set_port가 ARRIVED를 재발행하면 중복 이벤트가 된다.
        self._set_port(ep_port, "ARRIVED", "FULL", lot, emit_arrived_event=False)
        # 포트 상태 패널은 이벤트 수신 시점에 갱신된다.
        # direct input은 완료 시점에 별도 이벤트가 없으면 "다음 이벤트 때" 상태가 뒤늦게 보일 수 있어,
        # 갱신 전용 이벤트를 한 번 더 보내준다(애니/매핑 대상이 아님).
        self._emit_port_occ_refresh("직접투입 완료 후 포트 표시 갱신")
        self._stage_mark(lot.lot_id, "oht_to_bp1_end")
        self._log(f"{lot.lot_id} | {ep_port} 도착(직접)")

    def _load_lot_to_bp1(self, lot: Lot):
        """OHT 대기열 LOT을 BP1으로 투입(ARRIVED 이벤트·대기 후 BP1 안착, 이어서 버퍼로 이송)."""
        self._oht_loading_bp1 = True
        oht_time = self._timing.rand_oht_to_bp1()
        # 각 공정 확인(on_gate): UI 확인 팝업과 동기화되는 블로킹 게이트
        anim_wait = self._request_gate({
            "seq": "ARRIVED",
            "port_id": "BP1",
            "lot_id": lot.lot_id,
            "est_sec": f"{oht_time:.1f}",
            "title": "OHT -> BP1 경유 안착",
        })
        total_wait = max(float(oht_time), float(anim_wait))
        self._stage_mark(lot.lot_id, "oht_to_bp1_start")
        self._log_brief_step(lot.lot_id, "OHT→BP1", oht_time, anim_wait)
        # 요구사항 반영:
        # OHT->BP1 단계는 MOVE가 아니라 ARRIVED(포트 안착 이벤트)로 애니메이션을 구동한다.
        self._emit_event({"seq": "ARRIVED", "port_id": "BP1", "lot_id": lot.lot_id})
        self._log_event_block(
            seq="ARRIVED",
            summary="OHT -> BP1 경유 안착",
            lot_id=lot.lot_id,
            anim_line=f"애니메이션: arrived_bp1.json (추정 {anim_wait:.1f}s)",
            proc_line=f"공정시간: {total_wait:.1f}s (max(공정 {oht_time:.1f}s, 애니 {anim_wait:.1f}s))",
        )
        yield self.env.process(
            self._wait_with_progress(
                total_sec=total_wait,
                label=f"OHT->{ 'BP1' } {lot.lot_id}",
                detail=f"{lot.lot_id} OHT->BP1 이동(도착포트=BP1) | 공정={oht_time:.1f}s 애니={anim_wait:.1f}s",
                progress_interval=self._log_cfg.progress_interval(),
                event_seq="ARRIVED",
            )
        )
        self._stage_mark(lot.lot_id, "oht_to_bp1_end")
        self._set_port("BP1", "ARRIVED", "FULL", lot, emit_arrived_event=False)
        self._log(f"{lot.lot_id} | BP1 도착")
        yield self.env.process(self._move_bp1_to_buffer())
        self._oht_loading_bp1 = False

    def _move_bp1_to_buffer(self):
        """BP1에 있는 LOT을 가장 오래 비어 있던 빈 버퍼로 이송(MOVE_TRANSFERING)."""
        lot = self.ports.get("BP1")
        if lot is None:
            return
        target_bp = self._find_oldest_empty_buffer()
        if not target_bp:
            self._log(f"{lot.lot_id} | BP1→버퍼 실패(빈 슬롯 없음)")
            return
        # 이동 중에는 점유를 유지하되, 다음 공정에서 BP1/도착 버퍼가 선택되지 않도록 잠금.
        self._lock_port("BP1")
        self._lock_port(target_bp)
        self._route_mark(lot.lot_id, "bp1_to_bp_from", "BP1")
        self._route_mark(lot.lot_id, "bp1_to_bp_to", target_bp)
        move_time = self._timing.rand_bp1_to_bp()
        anim_wait = self._request_gate({
            "seq": "MOVE_TRANSFERING",
            "from_port_id": "BP1",
            "to_port_id": target_bp,
            "lot_id": lot.lot_id,
            "est_sec": f"{move_time:.1f}",
            "title": "BP1 -> BUFFER 이동",
        })
        total_wait = max(float(move_time), float(anim_wait))
        self._stage_mark(lot.lot_id, "bp1_to_bp_start")
        # 요구사항: BP1->BP 이동 애니는 EAPEIS_PORT_MOVE_TRANSFERING(=MOVE_TRANSFERING)만 실행.
        self._emit_event({"seq": "MOVE_TRANSFERING", "from_port_id": "BP1", "to_port_id": target_bp, "lot_id": lot.lot_id})
        _mv_aj = _log_anim_move_transfer_json("BP1", target_bp)
        self._log_event_block(
            seq="MOVE_TRANSFERING",
            summary=f"BP1 -> {target_bp} 이송",
            lot_id=lot.lot_id,
            anim_line=f"애니메이션: {_mv_aj} (추정 {anim_wait:.1f}s)",
            proc_line=f"공정시간: {total_wait:.1f}s (max(공정 {move_time:.1f}s, 애니 {anim_wait:.1f}s))",
        )
        self._log_brief_step(lot.lot_id, f"BP1→{target_bp}", move_time, anim_wait)
        try:
            yield self.env.process(
                self._wait_with_progress(
                    total_sec=total_wait,
                    label=f"BP1->{target_bp} {lot.lot_id}",
                    detail=f"{lot.lot_id} BP1->{target_bp} 이동(출발포트=BP1, 도착포트={target_bp}) | 공정={move_time:.1f}s 애니={anim_wait:.1f}s",
                    progress_interval=self._log_cfg.progress_interval(),
                    event_seq="MOVE_TRANSFERING",
                )
            )
        finally:
            # 완료 시점에만 상태 반영: 도착 포트 FULL, 출발 포트 EMPTY
            self._stage_mark(lot.lot_id, "bp1_to_bp_end")
            self._set_port(target_bp, "ARRIVED", "FULL", lot)
            self._buffer_loaded_at[target_bp] = float(self.env.now) if self.env is not None else 0.0
            self._remove_from_port("BP1")
            self._unlock_port(target_bp)
            self._unlock_port("BP1")
            # 완료 상태(포트 점유/매핑 prim)를 즉시 반영하기 위한 갱신 이벤트.
            self._emit_port_occ_refresh("BP1->버퍼 이송 완료 후 포트 표시 갱신")
            self._log(f"{lot.lot_id} | {target_bp} 도착(버퍼)")

    def _find_oldest_empty_buffer(self) -> Optional[str]:
        """비어 있는 버퍼 BP2~BP4 중, 비어 있기 시작한 시각이 가장 이른 포트."""
        empties = [p for p in BUFFER_PORTS if self.ports[p] is None and not self._is_port_locked(p)]
        if not empties:
            return None
        return sorted(empties, key=lambda p: self._buffer_empty_since.get(p, 0.0))[0]

    def _find_empty_ep(self) -> Optional[str]:
        """비어 있고 EP로 배정 중이 아닌 EP 포트 하나."""
        for ep in self._ep_ports:
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
            if self._ep_awaiting_pickup.get(ep) and self.ports.get(ep) is not None
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
        candidates = [bp for bp in BUFFER_PORTS if self.ports[bp] is not None and not self._is_port_locked(bp)]
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
        move_time = self._timing.rand_bp_to_ep()
        # 요구사항: BP->EP 이동 애니는 별도 시퀀스(EISEAP_PORT_MOVE_REQ)로 실행.
        anim_wait = self._request_gate({
            "seq": "MOVE_REQ",
            "from_port_id": bp_port,
            "to_port_id": ep_port,
            "lot_id": lot.lot_id,
            "est_sec": f"{move_time:.1f}",
            "title": "BUFFER -> EP 이동",
        })
        total_wait = max(float(move_time), float(anim_wait))
        self._stage_mark(lot.lot_id, "bp_to_ep_start")
        self._route_mark(lot.lot_id, "bp_to_ep_from", bp_port)
        self._route_mark(lot.lot_id, "bp_to_ep_to", ep_port)
        # 이동 중에는 점유를 유지하고, 다음 공정 선택에서만 제외(잠금).
        self._lock_port(bp_port)
        self._lock_port(ep_port)
        self._emit_event({"seq": "MOVE_REQ", "from_port_id": bp_port, "to_port_id": ep_port, "lot_id": lot.lot_id})
        _req_aj = _log_anim_move_req_json(bp_port, ep_port)
        self._log_event_block(
            seq="MOVE_REQ",
            summary=f"{bp_port} -> {ep_port} 이송",
            lot_id=lot.lot_id,
            anim_line=f"애니메이션: {_req_aj} (추정 {anim_wait:.1f}s)",
            proc_line=f"공정시간: {total_wait:.1f}s (max(공정 {move_time:.1f}s, 애니 {anim_wait:.1f}s))",
        )
        self._log_brief_step(lot.lot_id, f"{bp_port}→{ep_port}", move_time, anim_wait)
        try:
            yield self.env.process(
                self._wait_with_progress(
                    total_sec=total_wait,
                    label=f"{bp_port}->{ep_port} {lot.lot_id}",
                    detail=f"{lot.lot_id} {bp_port}->{ep_port} 이송(출발포트={bp_port}, 도착포트={ep_port}) | 공정={move_time:.1f}s 애니={anim_wait:.1f}s",
                    progress_interval=self._log_cfg.progress_interval(),
                    event_seq="MOVE_REQ",
                )
            )
        finally:
            # 완료 시점에만 상태 반영: 출발 포트 EMPTY, 도착 포트 FULL
            self._stage_mark(lot.lot_id, "bp_to_ep_end")
            # BP->EP 이동은 MOVE_REQ 이벤트로 처리하며, ARRIVED(=OHT 운반) 이벤트를 추가로 발생시키지 않는다.
            self._set_port(ep_port, "ARRIVED", "FULL", lot, emit_arrived_event=False)
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

    def _execute_pickup(self, ep_port: str):
        """회수: READYTOUNLOAD 게이트→이벤트, REMOVED 게이트→이벤트→공정+애니 대기→포트 비움·completed."""
        lot = self.ports.get(ep_port)
        if lot is None:
            self._ep_awaiting_pickup[ep_port] = False
            return
        self._ep_awaiting_pickup[ep_port] = False
        unload_time = self._timing.rand_ep_to_oht()
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
        self._emit_event({"seq": "READYTOUNLOAD", "port_id": ep_port, "lot_id": lot.lot_id})
        self._log_event_block(
            seq="READYTOUNLOAD",
            summary=f"{ep_port} 에서 OHT 회수 준비(반출 대기)",
            lot_id=lot.lot_id,
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
        total_wait = max(float(unload_time), float(anim_wait))
        self._stage_mark(lot.lot_id, "ep_to_oht_start")
        self._route_mark(lot.lot_id, "ep_to_oht_from", ep_port)
        self._route_mark(lot.lot_id, "ep_to_oht_to", "OHT")
        self._emit_event({"seq": "REMOVED", "port_id": ep_port, "lot_id": lot.lot_id})
        _rm_aj = _log_anim_removed_ep_json(ep_port)
        self._log_event_block(
            seq="REMOVED",
            summary=f"{ep_port} -> OHT 회수 실행",
            lot_id=lot.lot_id,
            anim_line=f"애니메이션: {_rm_aj} (추정 {anim_wait:.1f}s)",
            proc_line=f"공정시간: {total_wait:.1f}s (max(공정 {unload_time:.1f}s, 애니 {anim_wait:.1f}s))",
        )
        yield self.env.process(
            self._wait_with_progress(
                total_sec=total_wait,
                label=f"{ep_port}->OHT {lot.lot_id}",
                detail=f"{lot.lot_id} {ep_port}->OHT 회수(출발포트={ep_port}, 도착포트=OHT) | 공정={unload_time:.1f}s 애니={anim_wait:.1f}s",
                progress_interval=self._log_cfg.progress_interval(),
                event_seq="REMOVED",
            )
        )
        self._stage_mark(lot.lot_id, "ep_to_oht_end")
        self._remove_from_port(ep_port)
        # 완료 상태(포트 점유/매핑 prim)를 즉시 반영하기 위한 갱신 이벤트.
        self._emit_port_occ_refresh("EP 회수 완료 후 포트 표시 갱신")
        self.completed_lots.append(lot.lot_id)
        self._log(
            f"{lot.lot_id} | 회수완료 {len(self.completed_lots)}/{self._total_lots} | q={len(self._oht_input_queue)}"
        )

    def _set_port(self, port: str, event_cd: str, start_cd: str, lot: Lot, emit_arrived_event: bool = True) -> None:
        """포트 점유·상태코드를 갱신하고, EP면 회수 대기 플래그를 켠다. 필요 시 ARRIVED 이벤트."""
        self.ports[port] = lot
        self.port_event_cd[port] = event_cd
        self.port_start_cd[port] = start_cd
        if port in self._ep_ports:
            # EP 안착 = 반출 준비 완료(별도 PROCESS 대기 없음); 회수는 티켓+READYTOUNLOAD
            self._ep_awaiting_pickup[port] = True
            self._ep_ready_since[port] = float(self.env.now) if self.env is not None else 0.0
        # 정책: BP2~BP4는 "경유 버퍼"이므로 ARRIVED(안착) 이벤트를 별도로 emit 하지 않는다.
        # (BP1->BPx 이송 이벤트(MOVE_TRANSFERING)만으로 애니/로그를 대표)
        if emit_arrived_event and port not in BUFFER_PORTS:
            self._emit_event({"seq": "ARRIVED", "port_id": port, "lot_id": lot.lot_id})

    def _remove_from_port(self, port: str) -> None:
        """포트를 비우고 READY_TO_LOAD/EMPTY로 돌리며, EP 회수 플래그를 끈다."""
        self.ports[port] = None
        self.port_event_cd[port] = "READY_TO_LOAD"
        self.port_start_cd[port] = "EMPTY"
        if port in self._ep_ports:
            self._ep_awaiting_pickup[port] = False
            self._ep_ready_since[port] = 0.0
        if port in BUFFER_PORTS:
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
            if port in BUFFER_PORTS:
                self._buffer_loaded_at[port] = now
            applied.append(f"{port}={lot.lot_id}")
        if applied:
            return ", ".join(applied)
        return "(없음)"

    def _wait_with_progress(
        self,
        total_sec: float,
        label: str,
        detail: str,
        progress_interval: float = 5.0,
        event_seq: str = "",
    ):
        """
        공정 대기 시간을 simpy timeout으로 소모하고 진행률을 낸다.

        정책:
        - progress_interval <= 0: 중간 진행 출력 없이 DONE만 emit (기존 동작)
        - progress_interval > 0: 텍스트 로그([PROGRESS])는 누적하지 않고, on_progress(UI)만 주기적으로 갱신
          (요구사항: 설정한 초마다 %만 반영되도록)
        """
        total = max(0.01, float(total_sec))
        interval = self._progress_emit_policy.normalize_interval(float(progress_interval))
        ev = str(event_seq or "").strip()
        self._emit_progress({
            "label": label,
            "detail": detail,
            "event_seq": ev,
            "status": "RUNNING",
            "elapsed": "0.0",
            "total": self._progress_emit_policy.format_sec_1(total),
            "percent": "0",
        })
        if interval <= 0.0:
            # 로그 주기 0: 단계 완료 전에는 진행 로그를 출력하지 않음
            yield self.env.timeout(total)
            self._emit_progress({
                "label": label,
                "detail": detail,
                "event_seq": ev,
                "status": "DONE",
                "elapsed": self._progress_emit_policy.format_sec_1(total),
                "total": self._progress_emit_policy.format_sec_1(total),
                "percent": "100",
            })
            self._log_wait_step_done(label, total)
            return
        elapsed = 0.0
        while elapsed + 1e-9 < total:
            step = min(interval, total - elapsed)
            yield self.env.timeout(step)
            elapsed += step
            remain = max(0.0, total - elapsed)
            pct = (elapsed / total) * 100.0
            self._emit_progress({
                "label": label,
                "detail": detail,
                "event_seq": ev,
                "status": "DONE" if remain <= 1e-9 else "RUNNING",
                "elapsed": self._progress_emit_policy.format_sec_1(elapsed),
                "total": self._progress_emit_policy.format_sec_1(total),
                "percent": self._progress_emit_policy.format_percent(pct),
            })
        self._log_wait_step_done(label, total)

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
        if self._on_event:
            try:
                self._on_event(payload)
            except Exception:
                pass

    def _emit_progress(self, payload: Dict[str, str]) -> None:
        """UI 진행률 바/상세: sim_time을 붙여 on_progress 콜백으로 전달."""
        payload = dict(payload or {})
        try:
            payload["sim_time"] = f"{float(self.env.now):.2f}" if self.env is not None else "0.00"
        except Exception:
            payload["sim_time"] = "0.00"
        if self._on_progress:
            try:
                self._on_progress(payload)
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
                res = cb(dict(payload or {}))
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
        lines: List[str] = [f"[SUMMARY] 전체 t={total_t:.2f}s"]
        for lot_id in self.completed_lots:
            m = self._lot_stage_summary.get(lot_id, {})
            r = self._lot_route_summary.get(lot_id, {})
            d1 = self._dur(m, "oht_to_bp1_start", "oht_to_bp1_end")
            d2 = self._dur(m, "bp1_to_bp_start", "bp1_to_bp_end")
            d3 = self._dur(m, "bp_to_ep_start", "bp_to_ep_end")
            d5 = self._dur(m, "ep_to_oht_start", "ep_to_oht_end")
            parts = []
            parts.append(f"OHT->BP1={d1:.1f}s" if d1 >= 0 else "OHT->BP1=-")
            bp1_bp_from = r.get("bp1_to_bp_from", "BP1")
            bp1_bp_to = r.get("bp1_to_bp_to", "?")
            parts.append(f"{bp1_bp_from}->{bp1_bp_to}={d2:.1f}s" if d2 >= 0 else f"{bp1_bp_from}->{bp1_bp_to}=-")
            bp_ep_from = r.get("bp_to_ep_from", "?")
            bp_ep_to = r.get("bp_to_ep_to", "?")
            parts.append(f"{bp_ep_from}->{bp_ep_to}={d3:.1f}s" if d3 >= 0 else f"{bp_ep_from}->{bp_ep_to}=-")
            ep_oht_from = r.get("ep_to_oht_from", "EP?")
            ep_oht_to = r.get("ep_to_oht_to", "OHT")
            parts.append(f"{ep_oht_from}->{ep_oht_to}={d5:.1f}s" if d5 >= 0 else f"{ep_oht_from}->{ep_oht_to}=-")
            lines.append(f"  · {lot_id} | " + ", ".join(parts))
        self._log("\n".join(lines))

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
