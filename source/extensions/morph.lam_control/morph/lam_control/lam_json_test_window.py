"""LAM JSON 테스트 창 — 시퀀스 편집기와 별개의 가벼운 연쇄 실행 테스터.

REQ-009 (prompt.txt 7-11 줄):
- 외부 시뮬 결과 형식과 무관하게, lam/lam_event_sequences 폴더의 *.json 시퀀스
  파일들을 "스탭 추가" 형식으로 골라서 연달아 실행할 수 있어야 한다.
- DELAY step 도 끼워 넣을 수 있어서, "JSON 이 실행되는 도중 다음 JSON 이 실행"
  되는 시나리오를 손쉽게 검증할 수 있다.

핵심:
- ADD_JSON step → `LamSequenceRunner.run(file_steps)` 호출. USD_TIMELINE step 은
  Scheduler.start() 만 하고 즉시 반환하므로 다음 step 으로 빨리 넘어간다.
  → 같은 prim 의 재생이 진행 중일 때도 다른 JSON 의 step 이 동시에 시작될 수 있다
    (REQ-004 가상 시각 분리 정책으로 인스턴스마다 독립).
- DELAY step → wall-clock sleep. (백그라운드 스레드에서 실행)
- Run 은 별도 스레드에서 step 들을 순차 처리. 창의 UI 갱신은 스레드 → 메인 스레드 라벨만
  단순 갱신.
"""

from __future__ import annotations

import os
import threading
import time
from typing import List, Optional

from .lam_instance_registry import AnimationInstanceRegistry
from .lam_playback_scheduler import PlaybackScheduler
from .lam_sequence_engine import LamSequenceRunner


_PRINT_PREFIX = "[LAM/JSONTEST]"

WINDOW_TITLE = "LAM JSON Chain Tester"

STEP_ADD_JSON = "ADD_JSON"
STEP_DELAY = "DELAY"


class _Step:
    """1 스텝 데이터 + UI 모델 보유."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        # ADD_JSON: file_index (드롭다운에서 선택된 파일 인덱스), 파일 이름은 listing 으로부터
        self.file_index_model = None  # ui.SimpleIntModel
        self.file_combo = None  # ui.ComboBox (없으면 IntField fallback)
        # DELAY:
        self.delay_model = None  # ui.SimpleFloatModel(1.0)


class LamJsonTestWindow:
    """JSON 시퀀스 연쇄 실행 테스터 창."""

    def __init__(
        self,
        registry: AnimationInstanceRegistry,
        scheduler: PlaybackScheduler,
        sequence_dir: str,
    ) -> None:
        self._registry = registry
        self._scheduler = scheduler
        self._sequence_dir = sequence_dir
        self._files: List[str] = []  # *.json 파일명(폴더 내)
        self._steps: List[_Step] = []
        self._window = None
        self._steps_inner = None
        self._files_label = None
        self._log_label = None
        self._dir_model = None
        self._run_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

    # ------------------------------------------------------------------ window

    def show(self) -> None:
        try:
            import omni.ui as ui  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.ui not available: {exc}", flush=True)
            return

        if self._window is not None:
            try:
                self._window.visible = True
                return
            except Exception:
                self._window = None

        self._dir_model = ui.SimpleStringModel(self._sequence_dir)
        self._refresh_files_listing()

        self._window = ui.Window(WINDOW_TITLE, width=720, height=520)
        with self._window.frame:
            with ui.VStack(spacing=6):
                ui.Label(
                    "여러 JSON 시퀀스를 [+ Add JSON] 으로 추가, 사이사이 [+ Add Delay] 로 시간차를 주고 [Run] 으로 연쇄 실행합니다.",
                    height=18,
                )
                ui.Label(
                    "USD_TIMELINE 단계는 Scheduler 에 등록만 하고 즉시 반환하므로, "
                    "여러 JSON 의 재생이 자연스럽게 동시에 진행됩니다(독립 가상 시각).",
                    height=18,
                )
                ui.Separator()

                with ui.HStack(spacing=4, height=24):
                    ui.Label("Sequence dir", width=110)
                    ui.StringField(model=self._dir_model)
                    ui.Button("Refresh", clicked_fn=self._on_refresh, width=80)

                self._files_label = ui.Label(self._files_summary(), height=20)

                ui.Separator()

                with ui.HStack(spacing=4, height=24):
                    ui.Label("Steps", width=110)
                    ui.Spacer()
                    ui.Button("+ Add JSON", clicked_fn=self._on_add_json_step, width=120)
                    ui.Button("+ Add Delay", clicked_fn=self._on_add_delay_step, width=120)
                    ui.Button("Clear", clicked_fn=self._on_clear_steps, width=80)

                self._steps_frame = ui.ScrollingFrame(height=280)
                with self._steps_frame:
                    self._steps_inner = ui.VStack(spacing=2)

                ui.Separator()

                with ui.HStack(spacing=4, height=24):
                    ui.Spacer()
                    ui.Button("Run", clicked_fn=self._on_run, width=100)
                    ui.Button("Stop", clicked_fn=self._on_stop, width=100)

                ui.Separator()
                ui.Label("Log", height=20)
                self._log_label = ui.Label("(no log yet)", height=80, word_wrap=True)

        self._rebuild_steps_ui()

    def destroy(self) -> None:
        try:
            self._stop_flag.set()
        except Exception:
            pass
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass
        self._window = None
        self._steps_inner = None

    # ----------------------------------------------------------------- actions

    def _on_refresh(self) -> None:
        if self._dir_model is not None:
            self._sequence_dir = (self._dir_model.get_value_as_string() or "").strip() or self._sequence_dir
        self._refresh_files_listing()
        if self._files_label is not None:
            try:
                self._files_label.text = self._files_summary()
            except Exception:
                pass
        # ComboBox 들도 새 파일 목록으로 다시 그려야 한다.
        self._rebuild_steps_ui()

    def _on_add_json_step(self) -> None:
        st = _Step(STEP_ADD_JSON)
        self._steps.append(st)
        self._rebuild_steps_ui()

    def _on_add_delay_step(self) -> None:
        st = _Step(STEP_DELAY)
        self._steps.append(st)
        self._rebuild_steps_ui()

    def _on_clear_steps(self) -> None:
        self._steps = []
        self._rebuild_steps_ui()

    def _on_remove_step(self, idx: int) -> None:
        if 0 <= idx < len(self._steps):
            del self._steps[idx]
        self._rebuild_steps_ui()

    def _on_run(self) -> None:
        if self._run_thread is not None and self._run_thread.is_alive():
            self._log("이미 실행 중입니다. (Stop 후 다시 Run)")
            return
        if not self._steps:
            self._log("실행할 step 이 없습니다. [+ Add JSON] / [+ Add Delay] 로 추가하세요.")
            return
        # 현재 step 들을 (kind, payload) 형태의 가벼운 구조로 직렬화하여 백그라운드 스레드에 넘김.
        plan = []
        for st in self._steps:
            if st.kind == STEP_ADD_JSON:
                idx = self._read_combo_index(st)
                fname = self._files[idx] if 0 <= idx < len(self._files) else ""
                plan.append((STEP_ADD_JSON, os.path.join(self._sequence_dir, fname) if fname else ""))
            elif st.kind == STEP_DELAY:
                sec = 0.0
                if st.delay_model is not None:
                    try:
                        sec = float(st.delay_model.get_value_as_float())
                    except Exception:
                        sec = 0.0
                plan.append((STEP_DELAY, max(0.0, sec)))
        self._stop_flag.clear()
        self._run_thread = threading.Thread(
            target=self._run_loop,
            args=(plan,),
            name="lam.json_test",
            daemon=True,
        )
        self._run_thread.start()
        self._log(f"Run 시작 (steps={len(plan)})")

    def _on_stop(self) -> None:
        self._stop_flag.set()
        self._log("Stop 요청됨 (다음 step 진입 직전 또는 Delay 도중 중단)")

    # ----------------------------------------------------------------- run loop

    def _run_loop(self, plan: list) -> None:
        runner = LamSequenceRunner(self._registry, self._scheduler)
        for i, (kind, payload) in enumerate(plan):
            if self._stop_flag.is_set():
                self._log(f"step[{i}] 중단됨")
                return
            if kind == STEP_ADD_JSON:
                path = payload
                if not path or not os.path.isfile(path):
                    self._log(f"step[{i}] ADD_JSON 파일 없음: {path}")
                    continue
                steps = self._load_json_steps(path)
                if not steps:
                    self._log(f"step[{i}] ADD_JSON 빈 시퀀스: {os.path.basename(path)}")
                    continue
                self._log(
                    f"step[{i}] ADD_JSON {os.path.basename(path)} → {len(steps)} 개 step 트리거"
                )
                try:
                    # 동기 호출이지만 USD_TIMELINE 은 Scheduler.start 만 하고 즉시 반환.
                    runner.run(steps)
                except Exception as exc:
                    self._log(f"step[{i}] runner.run 실패: {exc}")
            elif kind == STEP_DELAY:
                sec = float(payload or 0.0)
                self._log(f"step[{i}] DELAY {sec:.2f}s")
                # 100ms 단위로 쪼개서 stop 반응성 확보.
                end = time.perf_counter() + sec
                while time.perf_counter() < end:
                    if self._stop_flag.is_set():
                        self._log(f"step[{i}] DELAY 중단됨")
                        return
                    time.sleep(0.05)
        self._log("Run 완료")

    # ----------------------------------------------------------------- helpers

    def _read_combo_index(self, st: "_Step") -> int:
        """ComboBox 의 현재 선택 인덱스를 안전하게 읽는다.

        omni.ui ComboBox 는 환경에 따라 시그니처/접근 경로가 다르므로 여러 경로로 시도하고,
        실패하면 IntField 폴백 model 값을 사용한다.
        """
        idx = 0
        if st.file_combo is not None:
            try:
                # 가장 흔한 패턴: combo.model.get_item_value_model().as_int
                m = st.file_combo.model
                try:
                    idx = int(m.get_item_value_model().as_int)
                    return idx
                except Exception:
                    pass
                # 일부 환경: combo.model.get_item_value_model(None, 0).as_int
                try:
                    idx = int(m.get_item_value_model(None, 0).as_int)
                    return idx
                except Exception:
                    pass
            except Exception:
                pass
        if st.file_index_model is not None:
            try:
                idx = int(st.file_index_model.get_value_as_int())
            except Exception:
                idx = 0
        return idx

    def _refresh_files_listing(self) -> None:
        try:
            entries = sorted(os.listdir(self._sequence_dir))
        except Exception:
            entries = []
        self._files = [e for e in entries if e.lower().endswith(".json")]

    def _files_summary(self) -> str:
        if not self._files:
            return f"(폴더 비어있음 또는 *.json 없음): {self._sequence_dir}"
        sample = ", ".join(self._files[:5])
        more = "" if len(self._files) <= 5 else f", … (+{len(self._files) - 5})"
        return f"파일 {len(self._files)}개: {sample}{more}"

    def _rebuild_steps_ui(self) -> None:
        try:
            import omni.ui as ui  # type: ignore
        except Exception:
            return
        if self._steps_inner is None:
            return
        self._steps_inner.clear()
        with self._steps_inner:
            if not self._steps:
                ui.Label("(아직 step 이 없습니다)")
                return
            for i, st in enumerate(self._steps):
                with ui.HStack(spacing=4, height=24):
                    ui.Label(f"[{i}] {st.kind}", width=110)
                    if st.kind == STEP_ADD_JSON:
                        if st.file_index_model is None:
                            st.file_index_model = ui.SimpleIntModel(0)
                        items = list(self._files) if self._files else ["(파일 없음)"]
                        try:
                            default_idx = max(0, min(st.file_index_model.get_value_as_int(), len(items) - 1))
                            st.file_combo = ui.ComboBox(default_idx, *items, width=380)
                        except Exception as exc:
                            st.file_combo = None
                            ui.IntField(model=st.file_index_model, width=80)
                            ui.Label(f"(ComboBox 생성 실패: {exc} — 인덱스 직접 입력)")
                    elif st.kind == STEP_DELAY:
                        if st.delay_model is None:
                            st.delay_model = ui.SimpleFloatModel(1.0)
                        ui.FloatField(model=st.delay_model, width=80)
                        ui.Label("초")
                    ui.Spacer()
                    ui.Button(
                        "Remove",
                        width=80,
                        clicked_fn=lambda i=i: self._on_remove_step(i),
                    )

    def _load_json_steps(self, path: str) -> Optional[list]:
        import json
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("steps"), list):
                return list(data["steps"])
            return []
        except Exception as exc:
            self._log(f"JSON 파싱 실패: {exc}")
            return None

    def _log(self, msg: str) -> None:
        print(f"{_PRINT_PREFIX} {msg}", flush=True)
        try:
            if self._log_label is not None:
                self._log_label.text = msg
        except Exception:
            pass


__all__ = ["LamJsonTestWindow"]
