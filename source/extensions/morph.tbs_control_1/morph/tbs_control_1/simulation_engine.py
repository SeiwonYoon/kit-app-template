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
  · initial_full_ports (시작 시점 미리 적재할 포트)
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
EP_PORTS_MAX = ("EP1", "EP2", "EP3")
BASE_PORTS = ("BP1", "BP2", "BP3", "BP4")


@dataclass
class Lot:
    lot_id: str
    foup_id: str
    sequence: int


@dataclass
class SimulationTimingConfig:
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
        lo, hi = float(a), float(b)
        if lo > hi:
            lo, hi = hi, lo
        return (max(0.01, lo), max(0.01, hi))

    def rand_oht_to_bp1(self) -> float:
        lo, hi = self._norm(self.oht_to_bp1_min, self.oht_to_bp1_max)
        return random.uniform(lo, hi)

    def rand_bp1_to_bp(self) -> float:
        lo, hi = self._norm(self.bp1_to_bp_min, self.bp1_to_bp_max)
        return random.uniform(lo, hi)

    def rand_bp_to_ep(self) -> float:
        lo, hi = self._norm(self.bp_to_ep_min, self.bp_to_ep_max)
        return random.uniform(lo, hi)

    def rand_ep_to_oht(self) -> float:
        lo, hi = self._norm(self.ep_to_oht_min, self.ep_to_oht_max)
        return random.uniform(lo, hi)

    def rand_lot_spawn_interval(self) -> float:
        lo, hi = self._norm(self.lot_spawn_interval_min, self.lot_spawn_interval_max)
        return random.uniform(lo, hi)

    def rand_pickup_event_interval(self) -> float:
        lo, hi = self._norm(self.pickup_event_interval_min, self.pickup_event_interval_max)
        return random.uniform(lo, hi)


@dataclass
class SimulationLogConfig:
    progress_interval_sec: float = 0.0
    input_status_interval_sec: float = 0.0

    def progress_interval(self) -> float:
        v = float(self.progress_interval_sec)
        return 0.0 if v <= 0.0 else max(0.2, v)

    def input_status_interval(self) -> float:
        v = float(self.input_status_interval_sec)
        return 0.0 if v <= 0.0 else max(0.2, v)


@dataclass
class SimulationInitConfig:
    ep_count: int = 2
    initial_full_ports: Optional[List[str]] = None
    max_oht_lots: int = 0


class TBSSimulationEngine:
    """BP1 입력 -> 버퍼 -> EP(반출 대기) -> OHT 회수 흐름 시뮬레이터."""

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
        self._last_wait_log_t = -999.0
        self._last_heartbeat_log_t = -999.0
        self._lot_stage_summary: Dict[str, Dict[str, float]] = {}
        self._lot_route_summary: Dict[str, Dict[str, str]] = {}
        self._initial_seed_seq = 1
        self._gate_lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.env is not None

    @property
    def is_done(self) -> bool:
        return self._done

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> bool:
        if not self.env:
            self._log("[SIM] simpy import 실패: pip install simpy 필요")
            self._done = True
            return False
        if self._running:
            return True
        self._running = True
        self._log("[SIM] 시작")
        self._total_lots = 0
        self._pickup_tickets = 0
        self._log(f"[INIT] 포트 구성: BP1~BP4 + {', '.join(self._ep_ports)}")
        self._log("[INIT] 모든 포트 READY_TO_LOAD / EMPTY")
        self._apply_initial_full_ports()
        self._total_lots += self._max_oht_lots
        if self._total_lots <= 0:
            self._log("[SIM] 완료 목표 LOT이 0입니다. 시작을 중단합니다.")
            self._running = False
            self._done = True
            return False
        self._log(
            f"[INIT] 완료 목표 LOT={self._total_lots} "
            f"(초기적재={self._total_lots - self._max_oht_lots}, OHT생성={self._max_oht_lots})"
        )
        self._log(f"[INIT] 초기 포트 상태: {self._ports_snapshot()}")
        self.env.process(self._lot_spawn_timer())
        self.env.process(self._pickup_event_timer())
        self.env.process(self._run_serial_flow())
        return True

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._done = True
        self._log(
            f"[SIM] 중지 | completed={len(self.completed_lots)}/{self._total_lots} "
            f"| input_queue={len(self._oht_input_queue)} | ports={self._ports_snapshot()}"
        )

    def _lot_spawn_timer(self):
        """설정 간격마다 LOT을 생성해 OHT 대기열에 쌓는다(타이머는 공정과 독립)."""
        yield self.env.timeout(0.05)
        while self._running:
            if self._oht_spawn_seq >= self._max_oht_lots:
                return
            dt = self._timing.rand_lot_spawn_interval()
            yield self.env.timeout(dt)
            if not self._running:
                return
            if self._oht_spawn_seq >= self._max_oht_lots:
                return
            self._oht_spawn_seq += 1
            lot = Lot(
                lot_id=f"LOT_{self._oht_spawn_seq:03d}",
                foup_id=f"FOUP_{self._oht_spawn_seq:03d}",
                sequence=self._oht_spawn_seq,
            )
            self._oht_input_queue.append(lot)
            self._log(
                f"[SPAWN] LOT 생성(대기열): {lot.lot_id} | queue={len(self._oht_input_queue)} "
                f"| spawn={self._oht_spawn_seq}/{self._max_oht_lots}"
            )

    def _pickup_event_timer(self):
        """설정 간격마다 회수(READYTOUNLOAD) 시도 티켓을 누적한다."""
        yield self.env.timeout(0.05)
        while self._running:
            if self._total_lots > 0 and len(self.completed_lots) >= self._total_lots:
                return
            dt = self._timing.rand_pickup_event_interval()
            yield self.env.timeout(dt)
            if not self._running:
                return
            if self._total_lots > 0 and len(self.completed_lots) >= self._total_lots:
                return
            self._pickup_tickets += 1
            self._log(f"[PICKUP EVT] 회수 티켓 +1 | pending_tickets={self._pickup_tickets}")

    def tick(self, sim_delta_sec: float) -> None:
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
        # 시작 직후 초기화 로그가 몰리지 않도록 한 틱 대기
        yield self.env.timeout(0.1)
        while self._running and len(self.completed_lots) < self._total_lots:
            self._log_heartbeat_if_due()
            moved = self._dispatch_buffer_to_ep()
            if not moved:
                now = float(self.env.now) if self.env is not None else 0.0
                wait_interval = self._log_cfg.input_status_interval()
                if wait_interval > 0.0 and now >= wait_interval and (now - self._last_wait_log_t >= wait_interval):
                    self._last_wait_log_t = now
                    self._log(
                        "[WAIT] BP->EP 이동 대기 "
                        f"| input_queue={len(self._oht_input_queue)} | ports={self._ports_snapshot()}"
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
        """직렬 실행: 회수 티켓·OHT 대기열을 우선 소모한 뒤 버퍼→EP 이동을 진행."""
        yield self.env.timeout(0.1)
        self._log(
            "[INPUT] LOT는 생성 간격 타이머로 대기열에 적재됩니다 "
            f"(목표 OHT LOT={self._max_oht_lots})."
        )

        while self._running and len(self.completed_lots) < self._total_lots:
            self._log_heartbeat_if_due()

            # 0) BP1 적재분(초기 포함)을 버퍼로 이송
            if self.ports.get("BP1") is not None and self._find_oldest_empty_buffer():
                yield self.env.process(self._move_bp1_to_buffer())
                continue

            # 1) 회수 티켓: EP 안착·회수 대기 중인 LOT만 READYTOUNLOAD 처리(티켓은 성공 시에만 소모)
            while self._pickup_tickets > 0 and len(self.completed_lots) < self._total_lots:
                ep_pick = self._find_ep_awaiting_pickup()
                if not ep_pick:
                    break
                self._pickup_tickets -= 1
                yield self.env.process(self._execute_pickup(ep_pick))
                if len(self.completed_lots) >= self._total_lots:
                    break
            if len(self.completed_lots) >= self._total_lots:
                break

            # 2) OHT 대기열: 빈 EP가 있으면 직접 투입, 아니면 BP1 경유(가능할 때만)
            if self._oht_input_queue and self._can_load_to_ep_direct():
                ep_target = self._find_empty_ep()
                if ep_target:
                    lot = self._oht_input_queue.pop(0)
                    self._log(
                        f"[INPUT QUEUE] {lot.sequence}번째 LOT 직접투입 "
                        f"(lot={lot.lot_id}, target={ep_target}, remaining={len(self._oht_input_queue)})"
                    )
                    yield self.env.process(self._load_lot_to_ep_direct(lot, ep_target))
                    continue

            if self._oht_input_queue and self._can_load_to_bp1():
                lot = self._oht_input_queue.pop(0)
                self._log(
                    f"[INPUT QUEUE] {lot.sequence}번째 LOT BP1 경유 투입 "
                    f"(lot={lot.lot_id}, remaining={len(self._oht_input_queue)})"
                )
                yield self.env.process(self._load_lot_to_bp1(lot))
                continue

            # 3) 버퍼 → EP (안착 시 _set_port에서 즉시 회수 대기 플래그)
            ep = self._find_empty_ep()
            bp = self._find_oldest_bp()
            if ep and bp:
                lot = self.ports.get(bp)
                if lot is not None:
                    yield self.env.process(self._move_bp_to_ep(bp, ep, lot))
                    continue

            # 4) 할 일 없음
            now = float(self.env.now) if self.env is not None else 0.0
            wait_interval = self._log_cfg.input_status_interval()
            if wait_interval > 0.0 and now >= wait_interval and (now - self._last_wait_log_t >= wait_interval):
                self._last_wait_log_t = now
                self._log(
                    "[WAIT] 직렬 모드 대기 "
                    f"| input_queue={len(self._oht_input_queue)} | pickup_tickets={self._pickup_tickets} "
                    f"| ports={self._ports_snapshot()}"
                )
            yield self.env.timeout(0.2)

        if self._running:
            self._running = False
            self._done = True
            self._log(
                f"[SIM] 완료: {len(self.completed_lots)}/{self._total_lots} "
                f"| done={self.completed_lots}"
            )
            self._log_final_summary()

    def _load_lots_to_bp1_loop(self):
        queued_count = len(self._oht_input_queue)
        if queued_count > 0:
            queued_first = self._oht_input_queue[0].lot_id
            queued_last = self._oht_input_queue[-1].lot_id
            self._log(
                f"[INPUT] LOT 입력 큐 준비: {queued_count}개 "
                f"(first={queued_first}, last={queued_last})"
            )
        # 입력 프로세스 자체도 한 틱 뒤에 시작해 t=0 로그 집중 완화
        yield self.env.timeout(0.1)
        last_input_status_log_t = -999.0
        while self._running and self._oht_input_queue:
            if not self._can_load_to_bp1():
                now = float(self.env.now) if self.env is not None else 0.0
                input_interval = self._log_cfg.input_status_interval()
                if input_interval > 0.0 and (now - last_input_status_log_t >= input_interval):
                    last_input_status_log_t = now
                    next_lot = self._oht_input_queue[0] if self._oht_input_queue else None
                    next_text = f"{next_lot.sequence}번째({next_lot.lot_id})" if next_lot else "-"
                    self._log(
                        "[INPUT WAIT] 다음 LOT 준비중 "
                        f"| next={next_text} | remaining={len(self._oht_input_queue)} "
                        f"| BP1={'FULL' if self.ports['BP1'] else 'EMPTY'}"
                    )
                yield self.env.timeout(0.2)
                continue
            lot = self._oht_input_queue.pop(0)
            self._log(
                f"[INPUT QUEUE] {lot.sequence}번째 LOT 입력 시작 "
                f"(lot={lot.lot_id}, remaining_after_pop={len(self._oht_input_queue)})"
            )
            yield self.env.process(self._load_lot_to_bp1(lot))
        self._log("[INPUT] LOT 입력 루프 종료")

    def _can_load_to_bp1(self) -> bool:
        bp1_empty = self.ports["BP1"] is None
        any_buffer_empty = any(self.ports[p] is None for p in BUFFER_PORTS)
        return bp1_empty and any_buffer_empty and not self._oht_loading_bp1

    def _can_load_to_ep_direct(self) -> bool:
        if self._oht_loading_bp1:
            return False
        return self._find_empty_ep() is not None

    def _load_lot_to_ep_direct(self, lot: Lot, ep_port: str):
        oht_time = self._timing.rand_oht_to_bp1()
        anim_wait = self._request_gate({
            "seq": "MOVE",
            "from_port_id": "OHT",
            "to_port_id": ep_port,
            "lot_id": lot.lot_id,
            "est_sec": f"{oht_time:.1f}",
            "title": f"OHT -> {ep_port} 직접 투입",
        })
        total_wait = max(float(oht_time), float(anim_wait))
        self._stage_mark(lot.lot_id, "oht_to_bp1_start")
        self._log(
            f"[INPUT] OHT -> {ep_port} 직접투입 시작: {lot.lot_id} "
            f"(foup={lot.foup_id}, seq={lot.sequence}, travel={oht_time:.1f}s)"
        )
        self._log(
            f"[STORY] {lot.lot_id}가 OHT에서 {ep_port}로 직접 투입됩니다. "
            f"예상 {total_wait:.1f}s 후 {ep_port} 도착 (공정={oht_time:.1f}s, 애니={anim_wait:.1f}s)"
        )
        self._emit_event({"seq": "MOVE", "from_port_id": "OHT", "to_port_id": ep_port, "lot_id": lot.lot_id})
        yield self.env.process(
            self._wait_with_progress(
                total_sec=total_wait,
                label=f"OHT->{ep_port} {lot.lot_id}",
                detail=f"{lot.lot_id} OHT->{ep_port} 직접투입(도착포트={ep_port}) | 공정={oht_time:.1f}s 애니={anim_wait:.1f}s",
                progress_interval=self._log_cfg.progress_interval(),
                event_seq="MOVE",
            )
        )
        self._set_port(ep_port, "ARRIVED", "FULL", lot)
        self._stage_mark(lot.lot_id, "oht_to_bp1_end")
        self._log(f"[INPUT] {ep_port} 도착(직접투입): {lot.lot_id} | ports={self._ports_snapshot()}")

    def _load_lot_to_bp1(self, lot: Lot):
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
        self._log(
            f"[INPUT] OHT -> BP1 시작: {lot.lot_id} "
            f"(foup={lot.foup_id}, seq={lot.sequence}, travel={oht_time:.1f}s)"
        )
        self._log(
            f"[STORY] {lot.lot_id}가 OHT 레일에서 BP1로 이동 중입니다. "
            f"예상 {total_wait:.1f}s 후 BP1 도착 (공정={oht_time:.1f}s, 애니={anim_wait:.1f}s)"
        )
        # 요구사항 반영:
        # OHT->BP1 단계는 MOVE가 아니라 ARRIVED(포트 안착 이벤트)로 애니메이션을 구동한다.
        self._emit_event({"seq": "ARRIVED", "port_id": "BP1", "lot_id": lot.lot_id})
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
        self._log(f"[INPUT] BP1 도착: {lot.lot_id} | ports={self._ports_snapshot()}")
        yield self.env.process(self._move_bp1_to_buffer())
        self._oht_loading_bp1 = False

    def _move_bp1_to_buffer(self):
        lot = self.ports.get("BP1")
        if lot is None:
            return
        target_bp = self._find_oldest_empty_buffer()
        if not target_bp:
            self._log(f"[BP1->BUFFER] 실패: 빈 버퍼 없음 | lot={lot.lot_id}")
            return
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
        self._emit_event({"seq": "MOVE_TRANSFERING", "from_port_id": "BP1", "to_port_id": target_bp, "lot_id": lot.lot_id})
        self._log(f"[BP1->BUFFER] {lot.lot_id}: BP1 -> {target_bp} ({move_time:.1f}s)")
        self._log(f"[STORY] {lot.lot_id}가 BP1에서 {target_bp}로 이송됩니다.")
        yield self.env.process(
            self._wait_with_progress(
                total_sec=total_wait,
                label=f"BP1->{target_bp} {lot.lot_id}",
                detail=f"{lot.lot_id} BP1->{target_bp} 이동(출발포트=BP1, 도착포트={target_bp}) | 공정={move_time:.1f}s 애니={anim_wait:.1f}s",
                progress_interval=self._log_cfg.progress_interval(),
                event_seq="MOVE_TRANSFERING",
            )
        )
        self._stage_mark(lot.lot_id, "bp1_to_bp_end")
        self._set_port(target_bp, "ARRIVED", "FULL", lot)
        self._buffer_loaded_at[target_bp] = float(self.env.now) if self.env is not None else 0.0
        self._remove_from_port("BP1")
        self._log(f"[BP1->BUFFER] 완료: {lot.lot_id} @ {target_bp} | ports={self._ports_snapshot()}")

    def _find_oldest_empty_buffer(self) -> Optional[str]:
        empties = [p for p in BUFFER_PORTS if self.ports[p] is None]
        if not empties:
            return None
        return sorted(empties, key=lambda p: self._buffer_empty_since.get(p, 0.0))[0]

    def _find_empty_ep(self) -> Optional[str]:
        for ep in self._ep_ports:
            if self.ports[ep] is None and not self._dispatching_to_ep.get(ep, False):
                return ep
        return None

    def _find_ep_awaiting_pickup(self) -> Optional[str]:
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
        candidates = [bp for bp in BUFFER_PORTS if self.ports[bp] is not None]
        if not candidates:
            return None
        return sorted(candidates, key=lambda p: self._buffer_loaded_at.get(p, 0.0))[0]

    def _dispatch_buffer_to_ep(self) -> bool:
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
        move_time = self._timing.rand_bp_to_ep()
        anim_wait = self._request_gate({
            "seq": "MOVE_TRANSFERING",
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
        # 예약 즉시 비워 중복 배정을 막는다.
        # 유지보수 주의: 이 줄들을 늦추면 같은 BP LOT이 중복 선택될 수 있다.
        self.ports[bp_port] = None
        self._buffer_loaded_at.pop(bp_port, None)
        self._buffer_empty_since[bp_port] = float(self.env.now) if self.env is not None else 0.0
        self.port_start_cd[bp_port] = "EMPTY"
        self.port_event_cd[bp_port] = "READY_TO_LOAD"
        self._emit_event({"seq": "MOVE_TRANSFERING", "from_port_id": bp_port, "to_port_id": ep_port, "lot_id": lot.lot_id})
        self._log(f"[MOVE] {lot.lot_id}: {bp_port} -> {ep_port} ({move_time:.1f}s)")
        self._log(f"[STORY] {lot.lot_id}가 버퍼 {bp_port}에서 공정 포트 {ep_port}로 이동 중입니다.")
        yield self.env.process(
            self._wait_with_progress(
                total_sec=total_wait,
                label=f"{bp_port}->{ep_port} {lot.lot_id}",
                detail=f"{lot.lot_id} {bp_port}->{ep_port} 이송(출발포트={bp_port}, 도착포트={ep_port}) | 공정={move_time:.1f}s 애니={anim_wait:.1f}s",
                progress_interval=self._log_cfg.progress_interval(),
                event_seq="MOVE_TRANSFERING",
            )
        )
        self._stage_mark(lot.lot_id, "bp_to_ep_end")
        self._set_port(ep_port, "ARRIVED", "FULL", lot)
        self._dispatching_to_ep[ep_port] = False
        self._emit_event({"seq": "READYTOLOAD", "port_id": bp_port, "lot_id": lot.lot_id})
        self._log(f"[ARRIVED] {lot.lot_id} @ {ep_port} | ports={self._ports_snapshot()}")

    def _execute_pickup(self, ep_port: str):
        lot = self.ports.get(ep_port)
        if lot is None:
            self._ep_awaiting_pickup[ep_port] = False
            return
        self._ep_awaiting_pickup[ep_port] = False
        unload_time = self._timing.rand_ep_to_oht()
        anim_wait = self._request_gate({
            "seq": "READYTOUNLOAD",
            "port_id": ep_port,
            "lot_id": lot.lot_id,
            "est_sec": f"{unload_time:.1f}",
            "title": "EP -> OHT 회수",
        })
        total_wait = max(float(unload_time), float(anim_wait))
        self._stage_mark(lot.lot_id, "ep_to_oht_start")
        self._route_mark(lot.lot_id, "ep_to_oht_from", ep_port)
        self._route_mark(lot.lot_id, "ep_to_oht_to", "OHT")
        self._emit_event({"seq": "READYTOUNLOAD", "port_id": ep_port, "lot_id": lot.lot_id})
        self._log(f"[READY_TO_UNLOAD] {ep_port}: {lot.lot_id} (to OHT {unload_time:.1f}s)")
        self._log(f"[STORY] {lot.lot_id}를 {ep_port}에서 OHT가 회수 중입니다.")
        yield self.env.process(
            self._wait_with_progress(
                total_sec=total_wait,
                label=f"{ep_port}->OHT {lot.lot_id}",
                detail=f"{lot.lot_id} {ep_port}->OHT 회수(출발포트={ep_port}, 도착포트=OHT) | 공정={unload_time:.1f}s 애니={anim_wait:.1f}s",
                progress_interval=self._log_cfg.progress_interval(),
                event_seq="READYTOUNLOAD",
            )
        )
        self._stage_mark(lot.lot_id, "ep_to_oht_end")
        self._remove_from_port(ep_port)
        self._emit_event({"seq": "REMOVED", "port_id": ep_port, "lot_id": lot.lot_id})
        self.completed_lots.append(lot.lot_id)
        self._log(
            f"[COMPLETE] OHT picked {lot.lot_id} from {ep_port} "
            f"({len(self.completed_lots)}/{self._total_lots}) | input_queue={len(self._oht_input_queue)}"
        )

    def _set_port(self, port: str, event_cd: str, start_cd: str, lot: Lot, emit_arrived_event: bool = True) -> None:
        self.ports[port] = lot
        self.port_event_cd[port] = event_cd
        self.port_start_cd[port] = start_cd
        if port in self._ep_ports:
            # EP 안착 = 반출 준비 완료(별도 PROCESS 대기 없음); 회수는 티켓+READYTOUNLOAD
            self._ep_awaiting_pickup[port] = True
            self._ep_ready_since[port] = float(self.env.now) if self.env is not None else 0.0
        if emit_arrived_event:
            self._emit_event({"seq": "ARRIVED", "port_id": port, "lot_id": lot.lot_id})

    def _remove_from_port(self, port: str) -> None:
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
        parts: List[str] = []
        for p in self._all_ports:
            lot = self.ports.get(p)
            parts.append(f"{p}:{lot.lot_id if lot else '-'}")
        return ", ".join(parts)

    def _log_heartbeat_if_due(self) -> None:
        if self.env is None:
            return
        now = float(self.env.now)
        interval = self._log_cfg.input_status_interval()
        if interval <= 0.0:
            return
        if now - self._last_heartbeat_log_t < interval:
            return
        self._last_heartbeat_log_t = now
        next_lot = self._oht_input_queue[0] if self._oht_input_queue else None
        next_text = f"{next_lot.sequence}번째({next_lot.lot_id})" if next_lot else "-"
        self._log(
            "[HEARTBEAT] 진행중 "
            f"| completed={len(self.completed_lots)}/{self._total_lots} "
            f"| next_input={next_text} | input_queue={len(self._oht_input_queue)} "
            f"| pickup_tickets={self._pickup_tickets} "
            f"| ports={self._ports_snapshot()}"
        )

    def _apply_initial_full_ports(self) -> None:
        ports = list(getattr(self._init_cfg, "initial_full_ports", None) or [])
        if not ports:
            return
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
            self._set_port(port, "ARRIVED", "FULL", lot)
            if port in BUFFER_PORTS:
                self._buffer_loaded_at[port] = now
            applied.append(f"{port}={lot.lot_id}")
        if applied:
            self._log(f"[INIT] 초기 적재 적용: {', '.join(applied)}")

    def _wait_with_progress(
        self,
        total_sec: float,
        label: str,
        detail: str,
        progress_interval: float = 5.0,
        event_seq: str = "",
    ):
        total = max(0.01, float(total_sec))
        interval = float(progress_interval)
        ev = str(event_seq or "").strip()
        self._emit_progress({
            "label": label,
            "detail": detail,
            "event_seq": ev,
            "status": "RUNNING",
            "elapsed": "0.0",
            "total": f"{total:.1f}",
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
                "elapsed": f"{total:.1f}",
                "total": f"{total:.1f}",
                "percent": "100",
            })
            return
        interval = max(0.2, interval)
        elapsed = 0.0
        while elapsed + 1e-9 < total:
            step = min(interval, total - elapsed)
            yield self.env.timeout(step)
            elapsed += step
            remain = max(0.0, total - elapsed)
            pct = (elapsed / total) * 100.0
            ev_tag = f"event={ev} | " if ev else ""
            self._log(
                f"[PROGRESS] {ev_tag}{label}: {elapsed:.1f}/{total:.1f}s ({pct:.0f}%) "
                f"remaining={remain:.1f}s | {detail}"
            )
            self._emit_progress({
                "label": label,
                "detail": detail,
                "event_seq": ev,
                "status": "DONE" if remain <= 1e-9 else "RUNNING",
                "elapsed": f"{elapsed:.1f}",
                "total": f"{total:.1f}",
                "percent": f"{pct:.0f}",
            })

    def _emit_event(self, payload: Dict[str, str]) -> None:
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
        if not lot_id:
            return
        if lot_id not in self._lot_stage_summary:
            self._lot_stage_summary[lot_id] = {}
        t = float(self.env.now) if self.env is not None else 0.0
        self._lot_stage_summary[lot_id][key] = t

    def _route_mark(self, lot_id: str, key: str, value: str) -> None:
        if not lot_id:
            return
        if lot_id not in self._lot_route_summary:
            self._lot_route_summary[lot_id] = {}
        self._lot_route_summary[lot_id][key] = str(value or "")

    def _dur(self, m: Dict[str, float], s: str, e: str) -> float:
        if s not in m or e not in m:
            return -1.0
        return max(0.0, float(m[e]) - float(m[s]))

    def _log_final_summary(self) -> None:
        total_t = float(self.env.now) if self.env is not None else 0.0
        self._log(f"[SUMMARY] 전체 시뮬레이션 종료 시각 t={total_t:.2f}s")
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
            self._log(f"[SUMMARY LOT] {lot_id} | " + ", ".join(parts))

    def _log(self, msg: str) -> None:
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
        self._print_to_console = bool(enabled)
