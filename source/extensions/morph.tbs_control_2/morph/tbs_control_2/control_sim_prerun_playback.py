from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SimTimelineItem:
    """프리런 결과의 단일 아이템(시뮬 시간 기준)."""

    t: float
    kind: str  # "log" | "event" | "progress"
    payload: Any


@dataclass(frozen=True)
class SimPreRunResult:
    """프리런 결과(화면 1개)."""

    screen: int
    final_sim_time: float
    total_est_sec: float
    items: Tuple[SimTimelineItem, ...]


class PlaybackEnv:
    def __init__(self) -> None:
        self.now: float = 0.0


class PlaybackEngine:
    """UI 그래프 동기화용 최소 엔진( env.now 제공 )."""

    def __init__(self, final_sim_time: float) -> None:
        self.env = PlaybackEnv()
        self._final = float(final_sim_time)
        self._running = True
        self._done = False

    @property
    def is_done(self) -> bool:
        return bool(self._done)

    @property
    def is_running(self) -> bool:
        return bool(self._running and (not self._done))

    def stop(self) -> None:
        self._running = False
        self._done = True

    def _set_now(self, t: float) -> None:
        self.env.now = max(0.0, float(t))
        if self.env.now >= self._final - 1e-9:
            self._done = True


class SimTimelinePlayer:
    """
    프리런 타임라인을 wall-clock에 맞춰 재생한다.
    - emit_fn(kind, payload, screen)
    - speed_supplier() -> float
    """

    def __init__(
        self,
        results_by_screen: Dict[int, SimPreRunResult],
        emit_fn: Callable[[str, Any, int], None],
        speed_supplier: Callable[[], float],
    ) -> None:
        self._results = dict(results_by_screen or {})
        self._emit = emit_fn
        self._speed = speed_supplier
        self._lock = threading.Lock()
        self._playing = False
        self._t0_wall = 0.0
        self._t0_sim_by_screen: Dict[int, float] = {}
        self._cursor_by_screen: Dict[int, int] = {}
        self._sim_now_by_screen: Dict[int, float] = {}

    def start(self) -> None:
        with self._lock:
            self._playing = True
            self._t0_wall = time.perf_counter()
            self._t0_sim_by_screen = {scr: 0.0 for scr in self._results.keys()}
            self._cursor_by_screen = {scr: 0 for scr in self._results.keys()}
            self._sim_now_by_screen = {scr: 0.0 for scr in self._results.keys()}

    def stop(self) -> None:
        with self._lock:
            self._playing = False

    def is_playing(self) -> bool:
        with self._lock:
            return bool(self._playing)

    def sim_now(self, screen: int) -> float:
        with self._lock:
            return float(self._sim_now_by_screen.get(int(screen), 0.0))

    def tick(self) -> None:
        with self._lock:
            if not self._playing:
                return
            sp = 1.0
            try:
                sp = max(0.05, float(self._speed()))
            except Exception:
                sp = 1.0
            wall_dt = time.perf_counter() - float(self._t0_wall)
            # 화면별로 독립 시간 축을 가진다(프리런 결과가 화면별로 다를 수 있음)
            for scr, res in self._results.items():
                t_sim = float(self._t0_sim_by_screen.get(scr, 0.0)) + float(wall_dt) * float(sp)
                t_sim = min(float(res.final_sim_time), float(t_sim))
                self._sim_now_by_screen[scr] = float(t_sim)

        # emit는 lock 밖에서(emit가 UI/로그에 영향을 주므로 re-entrancy 방지)
        for scr, res in self._results.items():
            t_sim = self.sim_now(scr)
            i = 0
            with self._lock:
                i = int(self._cursor_by_screen.get(scr, 0))
            items = res.items
            # time-ordered emit
            while i < len(items) and float(items[i].t) <= float(t_sim) + 1e-9:
                it = items[i]
                try:
                    self._emit(it.kind, it.payload, int(scr))
                except Exception:
                    pass
                i += 1
            with self._lock:
                self._cursor_by_screen[scr] = int(i)
                if t_sim >= float(res.final_sim_time) - 1e-9:
                    # 화면별 종료에 도달했으면 더 이상 emit할 게 없다.
                    pass


def prerun_engine_to_timeline(
    *,
    screen: int,
    engine: Any,
    max_tick_steps: int = 2000000,
) -> SimPreRunResult:
    """
    주어진 TBSSimulationEngine 인스턴스를 가능한 빠르게 끝까지 tick() 하며,
    on_log/on_event/on_progress로 올라오는 payload를 시뮬 시간 기준으로 수집한다.
    """
    items: List[SimTimelineItem] = []

    def _t_from_payload(payload: Any) -> float:
        if isinstance(payload, dict):
            try:
                return float(str(payload.get("sim_time", "")).strip() or "0.0")
            except Exception:
                return 0.0
        return 0.0

    # 콜백 래핑: engine이 호출하는 콜백은 이미 engine 내부에서 event_tags 병합이 끝난 merged payload이다.
    def on_log(line: str) -> None:
        try:
            items.append(SimTimelineItem(t=float(getattr(engine.env, "now", 0.0) or 0.0), kind="log", payload=str(line)))
        except Exception:
            items.append(SimTimelineItem(t=0.0, kind="log", payload=str(line)))

    def on_event(payload: Dict[str, Any]) -> None:
        items.append(SimTimelineItem(t=_t_from_payload(payload), kind="event", payload=dict(payload)))

    def on_progress(payload: Dict[str, Any]) -> None:
        items.append(SimTimelineItem(t=_t_from_payload(payload), kind="progress", payload=dict(payload)))

    # 엔진에 콜백을 주입(생성자에서 이미 들어갔더라도 프리런 전용으로 덮는다)
    try:
        engine._on_log = on_log  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        engine._on_event = on_event  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        engine._on_progress = on_progress  # type: ignore[attr-defined]
    except Exception:
        pass

    final_sim = 0.0
    # 가능한 빠르게: tick에 큰 델타를 주되, 내부 step guard(10000) 때문에 loop로 반복한다.
    steps = 0
    while True:
        try:
            if getattr(engine, "is_done", False):
                break
            if not getattr(engine, "is_running", False):
                break
        except Exception:
            break
        try:
            engine.tick(1e6)
        except Exception:
            break
        steps += 1
        if steps >= int(max_tick_steps):
            break

    try:
        final_sim = float(getattr(engine.env, "now", 0.0) or 0.0) if getattr(engine, "env", None) is not None else 0.0
    except Exception:
        final_sim = 0.0
    try:
        te = float(getattr(engine, "_sim_total_est_sec", 0.0) or 0.0)
    except Exception:
        te = 0.0

    # 안정적 재생을 위해 t, kind 순으로 정렬(동일 시각엔 log->event->progress 순으로)
    kind_prio = {"log": 0, "event": 1, "progress": 2}
    try:
        items.sort(key=lambda it: (float(it.t), int(kind_prio.get(str(it.kind), 9))))
    except Exception:
        pass

    return SimPreRunResult(
        screen=int(screen),
        final_sim_time=float(final_sim),
        total_est_sec=float(te),
        items=tuple(items),
    )
