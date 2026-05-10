"""외부 시뮬레이션 결과(JSON 라인) 를 시간 순으로 소비하여
`event` 이름에 매칭되는 시퀀스 파일을 실행하는 러너.

REQ-002 결정 3(T1) 모델 + Phase 4 정밀화:
- 입력: `<repo_root>/lam/lam_external_results/*.json` 안의 라인 배열. 각 라인에 최소 `t`, `event`.
- 매칭 규약: `event=event_1` → `<repo_root>/lam/lam_event_sequences/event_1.json`.
- 시간 진행: wall-clock 으로 `t` 의 차이만큼 sleep 후 다음 라인 처리.
- **Phase 4 추가**:
    - global speed scale (`set_speed`) — wall-clock dt 를 곱한 가상 시각으로 다음 트리거 계산.
    - pause / resume — wall-clock 누적을 멈췄다가 다시 진행.
    - restart — 같은 이벤트 배열을 처음부터 다시.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, List, Optional

from .lam_instance_registry import AnimationInstanceRegistry
from .lam_playback_scheduler import PlaybackScheduler
from .lam_sequence_engine import LamSequenceRunner


_PRINT_PREFIX = "[LAM/EXT]"


class LamExternalEventRunner:
    """외부 시뮬 결과 JSON → 시퀀스 트리거 (가속/일시정지/재시작 가능)."""

    def __init__(
        self,
        registry: AnimationInstanceRegistry,
        scheduler: PlaybackScheduler,
        sequence_dir: str,
    ) -> None:
        self._registry = registry
        self._scheduler = scheduler
        self._sequence_dir = sequence_dir

        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # set = 진행, clear = 일시정지
        self._lock = threading.RLock()

        # 진행 상태(가상 시각, wall_clock dt 누적, 마지막 결과 경로/이벤트 목록)
        self._sim_time: float = 0.0
        self._last_perf: float = 0.0
        self._speed: float = 1.0
        self._events: List[dict] = []
        self._last_results_path: str = ""

    # ------------------------------------------------------------------ control

    def is_running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def get_speed(self) -> float:
        return self._speed

    def set_speed(self, factor: float) -> None:
        with self._lock:
            try:
                self._speed = max(0.01, float(factor))
            except Exception:
                self._speed = 1.0
        print(f"{_PRINT_PREFIX} set_speed -> {self._speed:.2f}x", flush=True)

    def pause(self) -> None:
        if self._pause_event.is_set():
            self._pause_event.clear()
            print(f"{_PRINT_PREFIX} pause", flush=True)

    def resume(self) -> None:
        if not self._pause_event.is_set():
            self._pause_event.set()
            # 재개 시 perf 기준점 재설정(축적된 wall-clock 갭이 한꺼번에 sim_time 으로 흡수되지 않도록).
            self._last_perf = time.perf_counter()
            print(f"{_PRINT_PREFIX} resume", flush=True)

    def start(
        self,
        results_path: str,
        *,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> bool:
        if self.is_running():
            print(f"{_PRINT_PREFIX} already running", flush=True)
            return False
        if not os.path.isfile(results_path):
            print(f"{_PRINT_PREFIX} results file not found: {results_path}", flush=True)
            return False

        try:
            with open(results_path, "r", encoding="utf-8") as f:
                lines = json.load(f)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} parse failed: {exc}", flush=True)
            return False

        if not isinstance(lines, list):
            print(f"{_PRINT_PREFIX} results must be a JSON array", flush=True)
            return False

        events = [r for r in lines if isinstance(r, dict) and "t" in r and "event" in r]
        events.sort(key=lambda r: float(r.get("t", 0.0)))

        with self._lock:
            self._events = list(events)
            self._last_results_path = results_path
            self._sim_time = 0.0
            self._last_perf = time.perf_counter()
        self._stop_flag.clear()
        self._pause_event.set()

        self._thread = threading.Thread(
            target=self._run_loop,
            args=(on_log,),
            name="lam.external_event_runner",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_flag.set()
        self._pause_event.set()  # 잠겨 있으면 풀어 줌
        self._thread = None

    def restart(self, on_log: Optional[Callable[[str], None]] = None) -> bool:
        """마지막으로 사용한 results 파일로 처음부터 다시 시작."""
        path = self._last_results_path
        if not path:
            print(f"{_PRINT_PREFIX} restart: no previous results path", flush=True)
            return False
        self.stop()
        # join 없이 새 thread 시작 — 이전 thread 는 stop_flag 보고 자연 종료.
        time.sleep(0.05)
        return self.start(path, on_log=on_log)

    # ----------------------------------------------------------------- private

    def _run_loop(self, on_log: Optional[Callable[[str], None]]) -> None:
        runner = LamSequenceRunner(self._registry, self._scheduler)

        while not self._stop_flag.is_set():
            with self._lock:
                events = list(self._events)
            if not events:
                break

            # 다음 trigger 까지 가상 sleep.
            with self._lock:
                next_t = float(events[0].get("t", 0.0))
            self._wait_until_sim_time(next_t)
            if self._stop_flag.is_set():
                break

            with self._lock:
                if not self._events:
                    break
                ev = self._events.pop(0)

            event_name = str(ev.get("event", "")).strip()
            if not event_name:
                continue

            seq_path = os.path.join(self._sequence_dir, f"{event_name}.json")
            steps = self._load_sequence(seq_path)
            msg = (
                f"{_PRINT_PREFIX} sim_t={self._sim_time:.2f}s event={event_name} "
                f"seq={'OK' if steps is not None else 'NOT_FOUND'} "
                f"steps={len(steps) if steps else 0} (sp={self._speed:.2f}x)"
            )
            print(msg, flush=True)
            if on_log is not None:
                try:
                    on_log(msg)
                except Exception:
                    pass

            if steps:
                try:
                    runner.run(steps)
                except Exception as exc:
                    print(f"{_PRINT_PREFIX} runner.run failed: {exc}", flush=True)

    def _wait_until_sim_time(self, target_sim_time: float) -> None:
        """가상 시각 기준으로 다음 trigger 까지 대기. 일시정지/속도 변경에 즉시 반응."""
        # 짧은 폴링 sleep 으로 진행. 1 회 0.02s.
        while not self._stop_flag.is_set():
            self._pause_event.wait()  # 일시정지 시 차단
            if self._stop_flag.is_set():
                return

            now = time.perf_counter()
            with self._lock:
                dt = max(0.0, now - self._last_perf)
                self._last_perf = now
                self._sim_time += dt * self._speed
                cur = self._sim_time
            if cur >= target_sim_time:
                return
            # 남은 가상 시간을 wall-clock 으로 환산해 슬립(상한 0.05s)
            remain_sim = target_sim_time - cur
            wall_step = min(0.05, remain_sim / max(0.01, self._speed))
            if wall_step > 0:
                time.sleep(wall_step)

    def _load_sequence(self, path: str) -> Optional[list]:
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                steps = json.load(f)
            if isinstance(steps, list):
                return steps
            return []
        except Exception as exc:
            print(f"{_PRINT_PREFIX} load_sequence failed: {exc}", flush=True)
            return None


__all__ = ["LamExternalEventRunner"]
