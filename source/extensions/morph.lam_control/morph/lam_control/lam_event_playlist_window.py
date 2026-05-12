"""LAM 이벤트 플레이리스트 창 — 시뮬 스케줄 P1 (독립 모듈).

- `lam/lam_event_sequences/*.json` 을 드롭다운으로 고르고, 행마다 **Play 기준 시작 지연(초)**
  를 입력한 뒤 [Play] 하면 각 JSON 이 지정 시각에 `LamSequenceRunner.run()` 으로 시작한다.
- 여러 JSON 이 시간상 겹쳐 동시에 돌아갈 수 있다.
- **충돌 선점**: 새 JSON 이 시작될 때, 그 시퀀스가 건드리는 prim / 인스턴스와 겹치는
  이미 실행 중인 시퀀스가 있으면 이전 러너는 `stop(cancel_all_move_rotate=False)` 로
  루프만 중단하고, 겹치는 키에 대해서만 `scheduler.stop` / `end_master_timeline_mode` /
  `stop_prim_*` 로 정리한다.

본 모듈은 `LamWindow` / `LamSequenceEditor` 를 수정하지 않으며, `extension.py` 에서만
인스턴스화한다.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .lam_instance_registry import AnimationInstanceRegistry
from .lam_playback_scheduler import PlaybackScheduler
from .lam_runtime_evaluator import RuntimeEvaluator
from .lam_sequence_engine import (
    LamSequenceRunner,
    _collect_prim_paths_for_reset,
    _dispatch_main_wait,
    _reset_tbs_offset_ops_for_paths,
)
from .lam_types import LAM_FIXED_FPS, StepRef


_PRINT_PREFIX = "[LAM/EVTPL]"

WINDOW_TITLE = "LAM Event Playlist (JSON schedule)"


def _range_start_seconds_for_instance(inst) -> float:
    """`inst.range = (mode, s, e)` 기준 시작 초 값.

    lam_playback_scheduler._range_start_seconds 와 동일한 규칙 (LAM 고정 30 fps).
    LAM 모듈 외부에서 같은 의미로 다시 쓰기 위해 인라인 계산.
    """
    try:
        mode, s, _e = inst.range
    except Exception:
        mode, s, _e = "full", 0.0, 0.0
    tps = float(LAM_FIXED_FPS)
    asset_s = float(getattr(inst, "asset_start_time", 0.0) or 0.0)
    asset_e = float(getattr(inst, "asset_end_time", 0.0) or 0.0)
    if mode == "frames":
        return float(s) / tps
    if mode == "ratio":
        length = max(0.0, asset_e - asset_s) / tps
        return (asset_s / tps) + max(0.0, min(1.0, float(s))) * length
    return asset_s / tps


def _find_lam_data_root() -> str:
    """`lam/` 폴더 절대 경로 (lam_window._find_lam_data_root 와 동일 규칙, 순환 import 방지)."""
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    for _ in range(12):
        cand = os.path.normpath(os.path.join(cur, "lam"))
        if os.path.isdir(cand):
            return cand
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    fallback = os.path.normpath(os.path.join(here, "..", "..", "..", "..", "..", "..", "lam"))
    try:
        os.makedirs(os.path.join(fallback, "lam_event_sequences"), exist_ok=True)
    except Exception:
        pass
    return fallback


@dataclass
class _PlaylistStep:
    """UI 행 1 개 — JSON 파일 + Play 기준 시작 지연(초)."""

    file_index_model: object = field(default=None)  # ui.SimpleIntModel
    offset_model: object = field(default=None)  # ui.SimpleFloatModel
    file_combo: object = field(default=None)


@dataclass
class _ActiveRun:
    runner: LamSequenceRunner
    keys: frozenset[str]


class LamEventPlaylistWindow:
    """JSON 플레이리스트 스케줄러 (독립 창)."""

    def __init__(
        self,
        registry: AnimationInstanceRegistry,
        scheduler: PlaybackScheduler,
        evaluator: RuntimeEvaluator,
    ) -> None:
        self._registry = registry
        self._scheduler = scheduler
        self._evaluator = evaluator
        self._sequence_dir = os.path.join(_find_lam_data_root(), "lam_event_sequences")
        self._files: List[str] = []
        self._rows: List[_PlaylistStep] = []
        self._window = None
        self._steps_inner = None
        self._files_label = None
        self._log_label = None
        self._dir_model = None

        self._play_lock = threading.Lock()
        self._active: List[_ActiveRun] = []
        self._abort_play = threading.Event()
        self._play_threads: List[threading.Thread] = []

    # ------------------------------------------------------------------ UI

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
        self._refresh_files()

        self._window = ui.Window(WINDOW_TITLE, width=760, height=560)
        with self._window.frame:
            with ui.VStack(spacing=6):
                ui.Label(
                    "각 행: JSON 선택 + Play 버튼 누른 시점으로부터 **몇 초 뒤** 해당 시퀀스 시작.",
                    height=18,
                    word_wrap=True,
                )
                ui.Label(
                    "겹치는 시간에 여러 JSON 이 동시에 돌 수 있음. 동일 인스턴스(ref.prim_path) 또는 "
                    "동일 MOVE/ROTATE prim 경로가 겹치면 **나중에 시작된 JSON** 이 이전 실행을 중단.",
                    height=36,
                    word_wrap=True,
                )
                ui.Separator()
                with ui.HStack(spacing=4, height=24):
                    ui.Label("JSON 폴더", width=90)
                    ui.StringField(model=self._dir_model)
                    ui.Button("Refresh", clicked_fn=self._on_refresh_dir, width=80)
                self._files_label = ui.Label(self._files_summary(), height=22)
                ui.Separator()
                with ui.HStack(spacing=4, height=24):
                    ui.Label("스케줄 행", width=90)
                    ui.Spacer()
                    ui.Button("+ 행 추가", clicked_fn=self._on_add_row, width=90)
                    ui.Button("행 비우기", clicked_fn=self._on_clear_rows, width=90)
                self._steps_frame = ui.ScrollingFrame(height=300)
                with self._steps_frame:
                    self._steps_inner = ui.VStack(spacing=4)
                ui.Separator()
                with ui.HStack(spacing=4, height=28):
                    ui.Spacer()
                    ui.Button(
                        "Return",
                        clicked_fn=self._on_return,
                        width=80,
                        tooltip="현재 행에서 사용된 prim/인스턴스를 초기 위치(자산 원본 + virtual_time 시작점)로 복원합니다. 진행 중 시퀀스는 중단됨.",
                    )
                    ui.Button(
                        "Return + Play",
                        clicked_fn=self._on_return_then_play,
                        width=110,
                        tooltip="먼저 초기 위치로 복원한 뒤 Play 를 실행합니다.",
                    )
                    ui.Button("Play", clicked_fn=self._on_play, width=80)
                    ui.Button("Stop", clicked_fn=self._on_stop, width=80)
                ui.Separator()
                ui.Label("로그", height=18)
                self._log_label = ui.Label("(대기)", height=100, word_wrap=True)

        self._rebuild_rows_ui()

    def destroy(self) -> None:
        self._on_stop()
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass
        self._window = None
        self._steps_inner = None

    # ------------------------------------------------------------------ handlers

    def _on_refresh_dir(self) -> None:
        if self._dir_model is not None:
            self._sequence_dir = (self._dir_model.get_value_as_string() or "").strip() or self._sequence_dir
        self._refresh_files()
        if self._files_label is not None:
            try:
                self._files_label.text = self._files_summary()
            except Exception:
                pass
        self._rebuild_rows_ui()

    def _on_add_row(self) -> None:
        self._rows.append(_PlaylistStep())
        self._rebuild_rows_ui()

    def _on_clear_rows(self) -> None:
        self._rows = []
        self._rebuild_rows_ui()

    def _on_remove_row(self, idx: int) -> None:
        if 0 <= idx < len(self._rows):
            del self._rows[idx]
        self._rebuild_rows_ui()

    def _on_play(self) -> None:
        if not self._rows:
            self._log("행이 없습니다. [+ 행 추가] 후 JSON·시작 지연(초)을 입력하세요.")
            return
        plan: List[tuple[float, int, str, list]] = []
        for i, row in enumerate(self._rows):
            idx = self._read_file_index(row)
            fname = self._files[idx] if 0 <= idx < len(self._files) else ""
            path = os.path.join(self._sequence_dir, fname) if fname else ""
            off = 0.0
            if row.offset_model is not None:
                try:
                    off = float(row.offset_model.get_value_as_float())
                except Exception:
                    off = 0.0
            off = max(0.0, off)
            if not path or not os.path.isfile(path):
                self._log(f"행[{i}] 파일 없음 — 건너뜀: {path!r}")
                continue
            steps = self._load_json_steps(path)
            if not steps:
                self._log(f"행[{i}] 빈 시퀀스 — 건너뜀: {fname}")
                continue
            plan.append((off, i, path, steps))

        if not plan:
            self._log("실행할 유효한 행이 없습니다.")
            return

        plan.sort(key=lambda x: (x[0], x[1]))
        self._abort_play.clear()
        t0 = time.monotonic()
        self._play_threads = []
        self._log(f"Play 시작 — t0=monotonic() 기준, {len(plan)} 개 JSON 스케줄")

        for off, row_idx, path, steps in plan:
            t = threading.Thread(
                target=self._run_one_scheduled,
                args=(t0, off, row_idx, path, steps),
                name=f"lam_evtpl_{row_idx}",
                daemon=True,
            )
            self._play_threads.append(t)
            t.start()

    def _on_stop(self) -> None:
        self._abort_play.set()
        with self._play_lock:
            for ar in list(self._active):
                try:
                    ar.runner.stop(cancel_all_move_rotate=True)
                except Exception:
                    pass
            self._active.clear()
        self._log("Stop — 스케줄 중단 + 활성 시퀀스 전부 중단")

    def _on_return(self) -> None:
        """현재 행들이 건드리는 prim/인스턴스를 초기 위치로 복원만 한다.

        반드시 백그라운드 스레드에서 `_do_return_to_initial` 을 실행한다.
        버튼 클릭은 Kit 메인(UI) 스레드인데, 그 스레드에서 `_dispatch_main_wait` 를 호출하면
        메인이 자기 자신을 기다리는 교착이 나서 ~15s 타임아웃까지 viewport 가 갱신되지 않는다.
        """
        threading.Thread(
            target=lambda: self._do_return_to_initial(reason="Return"),
            name="lam_evtpl_return",
            daemon=True,
        ).start()

    def _on_return_then_play(self) -> None:
        """Return 후 Play. 두 동작이 별도 스레드에서 순서 보장되도록 묶는다."""
        threading.Thread(
            target=self._return_then_play_worker,
            name="lam_evtpl_return_play",
            daemon=True,
        ).start()

    def _return_then_play_worker(self) -> None:
        self._do_return_to_initial(reason="Return+Play")
        # USD write 가 main tick 으로 dispatch 되므로 잠깐 양보 — 다음 tick 에서 복원이 반영됨.
        time.sleep(0.1)
        self._on_play()

    # ------------------------------------------------------------------ return helpers

    def _do_return_to_initial(self, *, reason: str) -> None:
        """현재 행들이 참조하는 prim/인스턴스를 초기 위치로 복원.

        `_dispatch_main_wait` 를 쓰므로 **Kit 메인(UI) 스레드에서 직접 호출하면 안 된다**
        (교착 → ~15s 후 타임아웃). 버튼 핸들러는 백그라운드 스레드로 위임할 것.
        """
        # 1) 활성 시퀀스 / 스케줄된 대기 전부 중단 + 전역 translate/rotate 애니 중단.
        self._on_stop()

        # 2) 현재 행들에서 사용되는 모든 step 을 모아 reset 대상 키 산출.
        all_steps: list = []
        ref_prims: list[str] = []
        seen_ref: set[str] = set()
        for row in self._rows:
            idx = self._read_file_index(row)
            fname = self._files[idx] if 0 <= idx < len(self._files) else ""
            if not fname:
                continue
            path = os.path.join(self._sequence_dir, fname)
            if not os.path.isfile(path):
                continue
            steps = self._load_json_steps(path) or []
            if not steps:
                continue
            all_steps.extend(steps)
            for st in steps:
                if not isinstance(st, dict):
                    continue
                t = str(st.get("type") or "").upper()
                if t in ("USD_TIMELINE", "TIMESAMPLES_REPLAY"):
                    try:
                        ref = StepRef.from_dict(st.get("ref"))
                    except Exception:
                        continue
                    pp = (ref.prim_path or "").strip()
                    if pp.startswith("/") and pp not in seen_ref:
                        seen_ref.add(pp)
                        ref_prims.append(pp)

        if not all_steps:
            self._log(f"{reason} — 복원할 행이 없습니다 (행을 추가하거나 JSON 을 선택하세요).")
            return

        rpaths = _collect_prim_paths_for_reset(all_steps)

        # 3) TBS_OFFSET (MOVE/ROTATE) 를 0 으로. USD write 라 main tick 에서.
        try:
            _dispatch_main_wait(
                lambda: _reset_tbs_offset_ops_for_paths(list(rpaths)),
                timeout=15.0,
            )
        except Exception as exc:
            self._log(f"{reason} — TBS_OFFSET 복원 실패: {exc}")

        # 4) 인스턴스 (TIMESAMPLES_REPLAY/USD_TIMELINE) — virtual_time 을 시작점으로 되돌림.
        for pp in ref_prims:
            try:
                inst = self._registry.get_by_prim_path(pp)
                if inst is None:
                    continue
                inst.virtual_time = _range_start_seconds_for_instance(inst)
                inst.state = "stopped"
                try:
                    self._evaluator.end_master_timeline_mode(pp)
                except Exception:
                    pass
                try:
                    self._evaluator.invalidate_mapping(pp)
                except Exception:
                    pass
                try:
                    self._evaluator.force_rebuild_attr_cache(pp)
                except Exception:
                    pass
            except Exception as exc:
                self._log(f"{reason} — 인스턴스 복원 실패 prim={pp}: {exc}")

        self._log(
            f"{reason} 완료 — prim {len(rpaths)} 개 TBS_OFFSET=0, 인스턴스 {len(ref_prims)} 개 virtual_time 시작점 복귀"
        )

    # ------------------------------------------------------------------ scheduled worker

    def _run_one_scheduled(
        self,
        t0: float,
        offset_sec: float,
        row_idx: int,
        path: str,
        steps: list,
    ) -> None:
        target = t0 + float(offset_sec)
        while time.monotonic() < target:
            if self._abort_play.is_set():
                self._log(f"[행{row_idx}] 취소됨 (대기 중)")
                return
            time.sleep(0.05)
        if self._abort_play.is_set():
            self._log(f"[행{row_idx}] 취소됨 (시작 직전)")
            return

        keys = frozenset(_collect_prim_paths_for_reset(steps))
        self._log(f"[행{row_idx}] 시작 {os.path.basename(path)} keys={len(keys)}")

        with self._play_lock:
            self._preempt_locked(keys)

        runner = LamSequenceRunner(self._registry, self._scheduler)
        ar = _ActiveRun(runner=runner, keys=keys)
        with self._play_lock:
            self._active.append(ar)
        try:
            runner.run(steps)
        except Exception as exc:
            self._log(f"[행{row_idx}] run 예외: {exc}")
        finally:
            with self._play_lock:
                try:
                    self._active.remove(ar)
                except ValueError:
                    pass
            self._log(f"[행{row_idx}] 종료 {os.path.basename(path)}")

    def _preempt_locked(self, new_keys: frozenset[str]) -> None:
        """`self._play_lock` 보유 중에만 호출."""
        if not new_keys:
            return
        victims: List[_ActiveRun] = []
        for ar in self._active:
            overlap = ar.keys & new_keys
            if overlap:
                victims.append(ar)
        for ar in victims:
            try:
                ar.runner.stop(cancel_all_move_rotate=False)
            except Exception:
                pass
            # 이전 JSON 전체를 취소하는 것이므로, 그 시퀀스가 건드리던 모든 prim/인스턴스를 정리.
            for pk in ar.keys:
                self._silence_hardware_for_prim(pk)
            try:
                self._active.remove(ar)
            except ValueError:
                pass

    def _silence_hardware_for_prim(self, prim_path: str) -> None:
        """인스턴스 재생 중지 + 해당 prim 의 translate/rotate 애니만 중지."""
        if not prim_path.startswith("/"):
            return
        try:
            inst = self._registry.get_by_prim_path(prim_path)
            if inst is not None:
                self._scheduler.stop(prim_path)
                try:
                    self._evaluator.end_master_timeline_mode(prim_path)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            from . import lam_translate_animation as _ltx
            from . import lam_rotate_animation as _lrx

            _ltx.stop_prim_translate_animation(prim_path)
            _lrx.stop_prim_rotate_animation(prim_path)
        except Exception:
            pass

    # ------------------------------------------------------------------ helpers

    def _refresh_files(self) -> None:
        try:
            entries = sorted(os.listdir(self._sequence_dir))
        except Exception:
            entries = []
        self._files = [e for e in entries if e.lower().endswith(".json")]

    def _files_summary(self) -> str:
        if not self._files:
            return f"(*.json 없음) {self._sequence_dir}"
        head = ", ".join(self._files[:6])
        tail = "" if len(self._files) <= 6 else f" …(+{len(self._files) - 6})"
        return f"{len(self._files)}개: {head}{tail}"

    def _rebuild_rows_ui(self) -> None:
        try:
            import omni.ui as ui  # type: ignore
        except Exception:
            return
        if self._steps_inner is None:
            return
        self._steps_inner.clear()
        with self._steps_inner:
            if not self._rows:
                ui.Label("(행 없음 — [+ 행 추가])")
                return
            items = list(self._files) if self._files else ["(파일 없음)"]
            for i, row in enumerate(self._rows):
                with ui.HStack(spacing=4, height=26):
                    ui.Label(f"{i}", width=24)
                    if row.file_index_model is None:
                        row.file_index_model = ui.SimpleIntModel(0)
                    if row.offset_model is None:
                        row.offset_model = ui.SimpleFloatModel(0.0)
                    try:
                        ix = max(0, min(int(row.file_index_model.get_value_as_int()), len(items) - 1))
                        row.file_combo = ui.ComboBox(ix, *items, width=360)
                    except Exception:
                        row.file_combo = None
                        ui.IntField(model=row.file_index_model, width=56)
                    ui.Label("시작(초)", width=56)
                    ui.FloatField(model=row.offset_model, width=72)
                    ui.Spacer()
                    ui.Button("삭제", width=52, clicked_fn=lambda idx=i: self._on_remove_row(idx))

    def _read_file_index(self, row: _PlaylistStep) -> int:
        idx = 0
        if row.file_combo is not None:
            try:
                m = row.file_combo.model
                try:
                    idx = int(m.get_item_value_model().as_int)
                    return idx
                except Exception:
                    pass
                try:
                    idx = int(m.get_item_value_model(None, 0).as_int)
                    return idx
                except Exception:
                    pass
            except Exception:
                pass
        if row.file_index_model is not None:
            try:
                idx = int(row.file_index_model.get_value_as_int())
            except Exception:
                idx = 0
        return idx

    def _load_json_steps(self, path: str) -> Optional[list]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("steps"), list):
                return list(data["steps"])
            return []
        except Exception as exc:
            self._log(f"JSON 오류 {path}: {exc}")
            return None

    def _log(self, msg: str) -> None:
        print(f"{_PRINT_PREFIX} {msg}", flush=True)
        try:
            if self._log_label is not None:
                self._log_label.text = msg
        except Exception:
            pass


__all__ = ["LamEventPlaylistWindow"]
