from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional
import random


try:
    import simpy  # type: ignore
except Exception:
    simpy = None


BP1_TO_BP_TIME = 3.0
BP_TO_EP_TIME = 2.0
EP_TO_OHT_TIME = 1.0
OHT_TO_BP1_MIN = 15.0
OHT_TO_BP1_MAX = 120.0

BUFFER_PORTS = ("BP2", "BP3", "BP4")
EP_PORTS_MAX = ("EP1", "EP2", "EP3")
BASE_PORTS = ("BP1", "BP2", "BP3", "BP4")


@dataclass
class Lot:
    lot_id: str
    foup_id: str
    sequence: int
    metro_time: float


@dataclass
class SimulationTimingConfig:
    oht_to_bp1_min: float = OHT_TO_BP1_MIN
    oht_to_bp1_max: float = OHT_TO_BP1_MAX
    bp1_to_bp_min: float = BP1_TO_BP_TIME
    bp1_to_bp_max: float = BP1_TO_BP_TIME
    bp_to_ep_min: float = BP_TO_EP_TIME
    bp_to_ep_max: float = BP_TO_EP_TIME
    ep_to_oht_min: float = EP_TO_OHT_TIME
    ep_to_oht_max: float = EP_TO_OHT_TIME

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


@dataclass
class SimulationLogConfig:
    progress_interval_sec: float = 5.0
    input_status_interval_sec: float = 5.0

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


class TBSSimulationEngine:
    """BP1 입력 -> 버퍼 -> EP 공정 -> OHT 회수 흐름 시뮬레이터."""

    def __init__(
        self,
        lots: List[Lot],
        timing: Optional[SimulationTimingConfig] = None,
        log_config: Optional[SimulationLogConfig] = None,
        init_config: Optional[SimulationInitConfig] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_event: Optional[Callable[[Dict[str, str]], None]] = None,
        on_progress: Optional[Callable[[Dict[str, str]], None]] = None,
        print_to_console: bool = True,
    ) -> None:
        self._lots = list(lots)
        self._timing = timing or SimulationTimingConfig()
        self._log_cfg = log_config or SimulationLogConfig()
        self._init_cfg = init_config or SimulationInitConfig()
        self._on_log = on_log
        self._on_event = on_event
        self._on_progress = on_progress
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
        self._processing_eps: Dict[str, bool] = {ep: False for ep in self._ep_ports}
        self._dispatching_to_ep: Dict[str, bool] = {ep: False for ep in self._ep_ports}
        self._oht_input_queue: List[Lot] = list(self._lots)
        self._oht_loading_bp1 = False
        self.completed_lots: List[str] = []
        self._total_lots = len(self._lots)
        self._last_wait_log_t = -999.0
        self._last_heartbeat_log_t = -999.0
        self._lot_stage_summary: Dict[str, Dict[str, float]] = {}
        self._lot_route_summary: Dict[str, Dict[str, str]] = {}

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
        self._log(f"[INIT] 포트 구성: BP1~BP4 + {', '.join(self._ep_ports)}")
        self._log("[INIT] 모든 포트 READY_TO_LOAD / EMPTY")
        self._apply_initial_full_ports()
        self._log(f"[INIT] 초기 포트 상태: {self._ports_snapshot()}")
        # 직렬 모드: 하나의 메인 프로세스에서 단계별로 순차 진행
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
            self._try_start_ep_processes()
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
        """직렬 실행 모드: 입력->버퍼->EP->공정->회수를 LOT 단위로 순차 진행."""
        yield self.env.timeout(0.1)
        queued_count = len(self._oht_input_queue)
        if queued_count > 0:
            queued_first = self._oht_input_queue[0].lot_id
            queued_last = self._oht_input_queue[-1].lot_id
            self._log(
                f"[INPUT] LOT 입력 큐 준비: {queued_count}개 "
                f"(first={queued_first}, last={queued_last})"
            )

        while self._running and len(self.completed_lots) < self._total_lots:
            self._log_heartbeat_if_due()

            # 1) BP1이 비어 있고 버퍼 여유가 있으면 OHT에서 LOT 입력 + BP1->BUFFER
            if self._oht_input_queue and self._can_load_to_bp1():
                lot = self._oht_input_queue.pop(0)
                self._log(
                    f"[INPUT QUEUE] {lot.sequence}번째 LOT 입력 시작 "
                    f"(lot={lot.lot_id}, remaining_after_pop={len(self._oht_input_queue)})"
                )
                yield self.env.process(self._load_lot_to_bp1(lot))
                continue

            # 2) 버퍼 -> EP 이송 후 공정/회수까지 해당 LOT을 순차 완료
            ep = self._find_empty_ep()
            bp = self._find_oldest_bp()
            if ep and bp:
                lot = self.ports.get(bp)
                if lot is not None:
                    yield self.env.process(self._move_bp_to_ep(bp, ep, lot))
                    yield self.env.process(self._process_ep_lot(ep, lot))
                    continue

            # 3) 할 일이 없으면 대기
            now = float(self.env.now) if self.env is not None else 0.0
            wait_interval = self._log_cfg.input_status_interval()
            if wait_interval > 0.0 and now >= wait_interval and (now - self._last_wait_log_t >= wait_interval):
                self._last_wait_log_t = now
                self._log(
                    "[WAIT] 직렬 모드 대기 "
                    f"| input_queue={len(self._oht_input_queue)} | ports={self._ports_snapshot()}"
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

    def _load_lot_to_bp1(self, lot: Lot):
        self._oht_loading_bp1 = True
        oht_time = self._timing.rand_oht_to_bp1()
        self._stage_mark(lot.lot_id, "oht_to_bp1_start")
        self._log(
            f"[INPUT] OHT -> BP1 시작: {lot.lot_id} "
            f"(foup={lot.foup_id}, seq={lot.sequence}, travel={oht_time:.1f}s)"
        )
        self._log(
            f"[STORY] {lot.lot_id}가 OHT 레일에서 BP1로 이동 중입니다. "
            f"예상 {oht_time:.1f}s 후 BP1 도착"
        )
        self._emit_event({"seq": "MOVE", "from_port_id": "OHT", "to_port_id": "BP1", "lot_id": lot.lot_id})
        yield self.env.process(
            self._wait_with_progress(
                total_sec=oht_time,
                label=f"OHT->{ 'BP1' } {lot.lot_id}",
                detail=f"{lot.lot_id} OHT->BP1 이동(도착포트=BP1)",
                progress_interval=self._log_cfg.progress_interval(),
            )
        )
        self._stage_mark(lot.lot_id, "oht_to_bp1_end")
        self._set_port("BP1", "ARRIVED", "FULL", lot)
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
        self._stage_mark(lot.lot_id, "bp1_to_bp_start")
        self._emit_event({"seq": "MOVE_TRANSFERING", "from_port_id": "BP1", "to_port_id": target_bp, "lot_id": lot.lot_id})
        self._log(f"[BP1->BUFFER] {lot.lot_id}: BP1 -> {target_bp} ({move_time:.1f}s)")
        self._log(f"[STORY] {lot.lot_id}가 BP1에서 {target_bp}로 이송됩니다.")
        yield self.env.process(
            self._wait_with_progress(
                total_sec=move_time,
                label=f"BP1->{target_bp} {lot.lot_id}",
                detail=f"{lot.lot_id} BP1->{target_bp} 이동(출발포트=BP1, 도착포트={target_bp})",
                progress_interval=self._log_cfg.progress_interval(),
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
            if (
                self.ports[ep] is None
                and not self._processing_eps.get(ep, False)
                and not self._dispatching_to_ep.get(ep, False)
            ):
                return ep
        return None

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
        self._stage_mark(lot.lot_id, "bp_to_ep_start")
        self._route_mark(lot.lot_id, "bp_to_ep_from", bp_port)
        self._route_mark(lot.lot_id, "bp_to_ep_to", ep_port)
        # 예약 즉시 비워 중복 배정을 막는다.
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
                total_sec=move_time,
                label=f"{bp_port}->{ep_port} {lot.lot_id}",
                detail=f"{lot.lot_id} {bp_port}->{ep_port} 이송(출발포트={bp_port}, 도착포트={ep_port})",
                progress_interval=self._log_cfg.progress_interval(),
            )
        )
        self._stage_mark(lot.lot_id, "bp_to_ep_end")
        self._set_port(ep_port, "ARRIVED", "FULL", lot)
        self._dispatching_to_ep[ep_port] = False
        self._emit_event({"seq": "READYTOLOAD", "port_id": bp_port, "lot_id": lot.lot_id})
        self._log(f"[ARRIVED] {lot.lot_id} @ {ep_port} | ports={self._ports_snapshot()}")

    def _try_start_ep_processes(self) -> None:
        for ep in self._ep_ports:
            lot = self.ports.get(ep)
            if lot is None:
                continue
            if self._processing_eps.get(ep, False):
                continue
            self._processing_eps[ep] = True
            self.env.process(self._process_ep_lot(ep, lot))

    def _process_ep_lot(self, ep_port: str, lot: Lot):
        self._processing_eps[ep_port] = True
        self._stage_mark(lot.lot_id, "process_start")
        self._log(f"[PROCESS] {ep_port} metro start: {lot.lot_id} ({lot.metro_time:.1f}s)")
        self._log(
            f"[STORY] {lot.lot_id} 공정 시작({ep_port}). "
            f"예상 공정시간 {lot.metro_time:.1f}s (공정포트={ep_port})"
        )
        yield self.env.process(
            self._wait_with_progress(
                total_sec=max(0.01, lot.metro_time),
                label=f"{ep_port} PROCESS {lot.lot_id}",
                detail=f"{lot.lot_id} 공정 진행(공정포트={ep_port})",
                progress_interval=self._log_cfg.progress_interval(),
            )
        )
        self._stage_mark(lot.lot_id, "process_end")
        yield self.env.process(self._ready_to_unload(ep_port))
        self._processing_eps[ep_port] = False

    def _ready_to_unload(self, ep_port: str):
        lot = self.ports.get(ep_port)
        if lot is None:
            return
        unload_time = self._timing.rand_ep_to_oht()
        self._stage_mark(lot.lot_id, "ep_to_oht_start")
        self._route_mark(lot.lot_id, "ep_to_oht_from", ep_port)
        self._route_mark(lot.lot_id, "ep_to_oht_to", "OHT")
        self._emit_event({"seq": "READYTOUNLOAD", "port_id": ep_port, "lot_id": lot.lot_id})
        self._log(f"[READY_TO_UNLOAD] {ep_port}: {lot.lot_id} (to OHT {unload_time:.1f}s)")
        self._log(f"[STORY] {lot.lot_id}를 {ep_port}에서 OHT가 회수 중입니다.")
        yield self.env.process(
            self._wait_with_progress(
                total_sec=unload_time,
                label=f"{ep_port}->OHT {lot.lot_id}",
                detail=f"{lot.lot_id} {ep_port}->OHT 회수(출발포트={ep_port}, 도착포트=OHT)",
                progress_interval=self._log_cfg.progress_interval(),
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

    def _set_port(self, port: str, event_cd: str, start_cd: str, lot: Lot) -> None:
        self.ports[port] = lot
        self.port_event_cd[port] = event_cd
        self.port_start_cd[port] = start_cd
        self._emit_event({"seq": "ARRIVED", "port_id": port, "lot_id": lot.lot_id})

    def _remove_from_port(self, port: str) -> None:
        self.ports[port] = None
        self.port_event_cd[port] = "READY_TO_LOAD"
        self.port_start_cd[port] = "EMPTY"
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
        ep_busy = [ep for ep, busy in self._processing_eps.items() if busy]
        busy_text = ",".join(ep_busy) if ep_busy else "-"
        self._log(
            "[HEARTBEAT] 진행중 "
            f"| completed={len(self.completed_lots)}/{self._total_lots} "
            f"| next_input={next_text} | input_queue={len(self._oht_input_queue)} "
            f"| ep_processing={busy_text} | ports={self._ports_snapshot()}"
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
            if not self._oht_input_queue:
                break
            lot = self._oht_input_queue.pop(0)
            self._set_port(port, "ARRIVED", "FULL", lot)
            if port in BUFFER_PORTS:
                self._buffer_loaded_at[port] = now
            applied.append(f"{port}={lot.lot_id}")
        if applied:
            self._log(f"[INIT] 초기 적재 적용: {', '.join(applied)}")

    def _wait_with_progress(self, total_sec: float, label: str, detail: str, progress_interval: float = 5.0):
        total = max(0.01, float(total_sec))
        interval = float(progress_interval)
        self._emit_progress({
            "label": label,
            "detail": detail,
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
            self._log(
                f"[PROGRESS] {label}: {elapsed:.1f}/{total:.1f}s ({pct:.0f}%) "
                f"remaining={remain:.1f}s | {detail}"
            )
            self._emit_progress({
                "label": label,
                "detail": detail,
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
        # 상태 기반 애니메이션 룰 매칭용 포트 점유 스냅샷
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
            d4 = self._dur(m, "process_start", "process_end")
            d5 = self._dur(m, "ep_to_oht_start", "ep_to_oht_end")
            parts = []
            parts.append(f"OHT->BP1={d1:.1f}s" if d1 >= 0 else "OHT->BP1=-")
            bp1_bp_from = r.get("bp1_to_bp_from", "BP1")
            bp1_bp_to = r.get("bp1_to_bp_to", "?")
            parts.append(f"{bp1_bp_from}->{bp1_bp_to}={d2:.1f}s" if d2 >= 0 else f"{bp1_bp_from}->{bp1_bp_to}=-")
            bp_ep_from = r.get("bp_to_ep_from", "?")
            bp_ep_to = r.get("bp_to_ep_to", "?")
            parts.append(f"{bp_ep_from}->{bp_ep_to}={d3:.1f}s" if d3 >= 0 else f"{bp_ep_from}->{bp_ep_to}=-")
            parts.append(f"PROCESS={d4:.1f}s" if d4 >= 0 else "PROCESS=-")
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
