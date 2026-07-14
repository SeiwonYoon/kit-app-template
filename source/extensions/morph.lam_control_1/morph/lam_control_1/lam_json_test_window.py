"""LAM JSON 테스트 창 — 여러 JSON 시퀀스를 시간 정렬해 연쇄 실행 + 통합 JSON 저장.

본 창의 운용 목적 (2026-05-14 갱신):
  1) **스케줄 입력 모드 (Editor)**: 행 1개 = JSON 파일 1개. 행마다 시간 설정 체크박스
     (OFF / `+초` / `@초`) 로 실행 시점을 지정. ``[+ Add Delay]`` 행도 그대로 유지.
     ``[Run]`` 으로 즉시 연쇄 실행, ``[↑]/[↓]`` 로 순서 변경, 드롭다운 선택은 sticky.
  2) **통합 JSON Save/Load**: 시간 체크(@sec / +sec) 가 **없는** 구성은 기존처럼
     **평탄 step 배열** 로 저장한다. 시간 예약이 **있으면** 단일 배열로는 동시 시작을
     표현할 수 없으므로 ``lam_chain_plan_v1`` 루트 객체(JSON 파일 행 + ``sequence_dir``)
     로 저장하고, Result ``Run`` 은 Editor 와 동일한 ``_run_loop_editor`` 로 재생한다.
     ``Load Merged`` 는 위 두 형식 + ``{steps:[...]}`` / step 배열 을 모두 읽는다.
  3) **결과 보기 모드 (Result)**: step 배열을 한 줄씩 표시 + 행 단위 ``Remove`` /
     ``↑`` / ``↓`` 만 제공 (깊은 편집은 시퀀스 에디터에서). ``Save Result`` 로 다시
     저장 가능.

시퀀스 에디터와의 관계:
  - 시퀀스 에디터 schema = step 배열 (TIMESAMPLES_REPLAY / USD_TIMELINE / MOVE /
    ROTATE / DELAY).
  - 평탄 ``Save Merged`` 산출물도 동일 schema. **시간 예약이 있는 Save** 는
    ``lam_chain_plan_v1`` 별도 루트 형식(행 스케줄)이며, 외부 step-only 파이프라인에는
    그대로 넣을 수 없다.
  - "다른" 점은 **입력 단위가 step 이 아니라 JSON 파일** 이라는 것. 자동화 매핑이
    여러 시뮬 결과 JSON 을 시간 정렬해 합쳐 한 번에 흘릴 때 본 창이 저작 도구
    역할.

기존 정책 (변경 없음):
  - USD_TIMELINE / TIMESAMPLES_REPLAY 는 ``Scheduler.start()`` 후 estimated duration
    까지 ``LamSequenceRunner`` 내부에서 wait — 즉 ``runner.run`` 반환 시점이 "그 JSON
    이 끝난 시점" 의 의미가 된다 (REQ-009 의 동작 모델).
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

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


_PRINT_PREFIX = "[LAM/JSONTEST]"

WINDOW_TITLE = "LAM JSON Chain Tester"


def _range_start_seconds_for_instance(inst) -> float:
    """`inst.range = (mode, s, e)` 기준 시작 초 (LAM 고정 fps, 시퀀스 에디터와 동일 규칙)."""
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

# Editor mode 의 행 종류.
ROW_JSON = "JSON"
ROW_DELAY = "DELAY"

# 시간 모드 3-state (Editor 행에 한정).
TIME_MODE_OFF = "OFF"     # 이전 행 종료 직후 순차 진행
TIME_MODE_PLUS = "PLUS"   # 이전 행 종료 후 +초 대기
TIME_MODE_AT = "AT"       # Run 시작(t=0) 기준 절대 초
_TIME_MODE_LABELS = {
    TIME_MODE_OFF: "OFF",
    TIME_MODE_PLUS: "+sec",
    TIME_MODE_AT: "@sec",
}
_TIME_MODE_ORDER = [TIME_MODE_OFF, TIME_MODE_PLUS, TIME_MODE_AT]

# 보기 모드.
VIEW_EDITOR = "editor"
VIEW_RESULT = "result"

# Save Merged — 시간 예약 행이 있을 때 (평탄 step 배열로는 동시 시작 불가).
MERGED_PLAN_FORMAT_V1 = "lam_chain_plan_v1"


class _Row:
    """Editor 모드 1 행 데이터 + UI 모델 보유."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        # ROW_JSON 전용
        self.file_index: int = 0
        self.time_enabled: bool = False
        self.time_mode: str = TIME_MODE_OFF
        self.time_value: float = 0.0
        # ROW_DELAY 전용
        self.delay_sec: float = 1.0


class LamJsonTestWindow:
    """JSON 시퀀스 연쇄 실행 + 통합 JSON 저장/표시 창."""

    def __init__(
        self,
        registry: AnimationInstanceRegistry,
        scheduler: PlaybackScheduler,
        sequence_dir: str,
        evaluator: RuntimeEvaluator,
    ) -> None:
        self._registry = registry
        self._scheduler = scheduler
        self._evaluator = evaluator
        self._sequence_dir = sequence_dir
        self._files: List[str] = []  # *.json 파일명(폴더 내)
        self._rows: List[_Row] = []
        self._merged_steps: List[Dict[str, Any]] = []  # Result: 평탄 step 배열
        self._merged_plan: Optional[List[Dict[str, Any]]] = None  # Result: plan v1 행 목록
        self._merged_plan_dir: str = ""  # plan JSON 의 sequence_dir (파일 resolve 용)
        self._merged_source_label: str = ""  # Result 모드 소스 경로

        self._view_mode: str = VIEW_EDITOR

        self._window = None
        self._steps_inner = None
        self._files_label = None
        self._log_label = None
        self._dir_model = None
        self._mode_label = None
        self._run_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._runners_lock = threading.Lock()
        # 시간 체크가 켜진 행은 자체 스레드 + 자체 runner 로 동시 실행된다.
        # Stop / Return 시 모두 한 번에 멈춰야 하므로 list 로 관리한다.
        self._active_runners: List[LamSequenceRunner] = []

        # ComboBox / Field model subscription 보관 (GC 방지). row_idx -> list[(model, sub)]
        self._subs: Dict[int, List[Any]] = {}

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

        self._window = ui.Window(WINDOW_TITLE, width=820, height=620)
        with self._window.frame:
            with ui.VStack(spacing=6):
                ui.Label(
                    "여러 JSON 시퀀스를 [+ Add JSON] 으로 추가하고 [Run] 으로 실행합니다. "
                    "시간 체크박스가 OFF 인 행은 직전 행이 끝난 뒤 직렬 실행, "
                    "ON (@sec / +sec) 인 행은 예약 시각에 별도 러너로 동시 실행됩니다.",
                    height=36,
                    word_wrap=True,
                )
                ui.Label(
                    "Save Merged → 시간 예약 없으면 평탄 step 배열, @/+ 시간 체크가 있으면 "
                    "lam_chain_plan_v1 (행 스케줄) 로 저장. Load Merged → 두 형식 모두 표시·재생.",
                    height=36,
                    word_wrap=True,
                )
                ui.Separator()

                with ui.HStack(spacing=4, height=24):
                    ui.Label("Sequence dir", width=110)
                    ui.StringField(model=self._dir_model)
                    ui.Button("Refresh", clicked_fn=self._on_refresh, width=80)

                self._files_label = ui.Label(self._files_summary(), height=20)

                ui.Separator()

                # ---- 모드 + 액션 바 ----
                with ui.HStack(spacing=4, height=24):
                    ui.Label("Mode", width=60)
                    ui.Button("Editor", clicked_fn=self._on_switch_editor, width=80)
                    ui.Button("Result", clicked_fn=self._on_switch_result, width=80)
                    self._mode_label = ui.Label(self._mode_label_text(), width=320)
                    ui.Spacer()
                    ui.Button("Save Merged", clicked_fn=self._on_save_merged, width=110)
                    ui.Button("Load Merged", clicked_fn=self._on_load_merged, width=110)

                # ---- 행 액션 바 ----
                with ui.HStack(spacing=4, height=24):
                    ui.Label("Rows", width=110)
                    ui.Spacer()
                    ui.Button("+ Add JSON", clicked_fn=self._on_add_json_row, width=120)
                    ui.Button("+ Add Delay", clicked_fn=self._on_add_delay_row, width=120)
                    ui.Button("Clear", clicked_fn=self._on_clear_rows, width=80)

                self._steps_frame = ui.ScrollingFrame(height=340)
                with self._steps_frame:
                    self._steps_inner = ui.VStack(spacing=2)

                ui.Separator()

                with ui.HStack(spacing=4, height=24):
                    ui.Spacer()
                    ui.Button(
                        "Return",
                        clicked_fn=self._on_return,
                        width=90,
                        tooltip="현재 구성이 건드리는 prim/인스턴스를 초기 위치로 복원합니다. 실행 중이면 중단됩니다.",
                    )
                    ui.Button(
                        "Return+Play",
                        clicked_fn=self._on_return_then_play,
                        width=110,
                        tooltip="초기 위치로 복원한 뒤 Run 과 동일하게 연쇄 실행합니다.",
                    )
                    ui.Button("Run", clicked_fn=self._on_run, width=100)
                    ui.Button("Stop", clicked_fn=self._on_stop, width=100)

                ui.Separator()
                ui.Label("Log", height=20)
                self._log_label = ui.Label("(no log yet)", height=80, word_wrap=True)

        self._rebuild_rows_ui()

    def destroy(self) -> None:
        self._quick_stop_run()
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

    # ----------------------------------------------------------------- mode

    def _mode_label_text(self) -> str:
        if self._view_mode == VIEW_RESULT:
            src = self._merged_source_label or "(in-memory)"
            if self._merged_plan is not None:
                return f"Result: plan {len(self._merged_plan)} 행 / source={src}"
            n = len(self._merged_steps)
            return f"Result: {n} step / source={src}"
        return f"Editor: {len(self._rows)} 행"

    def _refresh_mode_label(self) -> None:
        if self._mode_label is None:
            return
        try:
            self._mode_label.text = self._mode_label_text()
        except Exception:
            pass

    def _on_switch_editor(self) -> None:
        self._view_mode = VIEW_EDITOR
        self._refresh_mode_label()
        self._rebuild_rows_ui()

    def _on_switch_result(self) -> None:
        self._view_mode = VIEW_RESULT
        self._refresh_mode_label()
        self._rebuild_rows_ui()

    # ----------------------------------------------------------------- actions (editor)

    def _on_refresh(self) -> None:
        if self._dir_model is not None:
            self._sequence_dir = (
                self._dir_model.get_value_as_string() or ""
            ).strip() or self._sequence_dir
        self._refresh_files_listing()
        if self._files_label is not None:
            try:
                self._files_label.text = self._files_summary()
            except Exception:
                pass
        # 파일 목록이 줄어들면 row.file_index 가 out-of-range 일 수 있다. clamp.
        n = max(1, len(self._files))
        for row in self._rows:
            if row.kind == ROW_JSON:
                row.file_index = max(0, min(row.file_index, n - 1))
        self._rebuild_rows_ui()

    def _on_add_json_row(self) -> None:
        if self._view_mode != VIEW_EDITOR:
            self._on_switch_editor()
        self._rows.append(_Row(ROW_JSON))
        self._refresh_mode_label()
        self._rebuild_rows_ui()

    def _on_add_delay_row(self) -> None:
        if self._view_mode != VIEW_EDITOR:
            self._on_switch_editor()
        self._rows.append(_Row(ROW_DELAY))
        self._refresh_mode_label()
        self._rebuild_rows_ui()

    def _on_clear_rows(self) -> None:
        if self._view_mode == VIEW_RESULT:
            self._merged_steps = []
            self._merged_plan = None
            self._merged_plan_dir = ""
            self._merged_source_label = ""
        else:
            self._rows = []
        self._refresh_mode_label()
        self._rebuild_rows_ui()

    def _on_remove_row(self, idx: int) -> None:
        if self._view_mode == VIEW_RESULT:
            if self._merged_plan is not None:
                if 0 <= idx < len(self._merged_plan):
                    del self._merged_plan[idx]
            elif 0 <= idx < len(self._merged_steps):
                del self._merged_steps[idx]
        else:
            if 0 <= idx < len(self._rows):
                del self._rows[idx]
        self._refresh_mode_label()
        self._rebuild_rows_ui()

    def _on_move_up(self, idx: int) -> None:
        if self._view_mode == VIEW_RESULT:
            seq = self._merged_plan if self._merged_plan is not None else self._merged_steps
        else:
            seq = self._rows
        if 0 < idx < len(seq):
            seq[idx - 1], seq[idx] = seq[idx], seq[idx - 1]
            self._rebuild_rows_ui()

    def _on_move_down(self, idx: int) -> None:
        if self._view_mode == VIEW_RESULT:
            seq = self._merged_plan if self._merged_plan is not None else self._merged_steps
        else:
            seq = self._rows
        if 0 <= idx < len(seq) - 1:
            seq[idx + 1], seq[idx] = seq[idx], seq[idx + 1]
            self._rebuild_rows_ui()

    def _on_run(self) -> None:
        if self._run_thread is not None and self._run_thread.is_alive():
            self._log("이미 실행 중입니다. (Stop 후 다시 Run)")
            return

        # Run 의 입력: Editor 모드이면 _rows → plan 변환, Result 모드이면 plan v1 또는 평탄 steps.
        if self._view_mode == VIEW_RESULT:
            if self._merged_plan is not None:
                plan = self._normalize_plan_for_run(
                    self._merged_plan,
                    self._resolve_plan_base_dir(),
                )
                if not plan:
                    self._log("실행할 plan 행이 없습니다.")
                    return
                self._stop_flag.clear()
                self._run_thread = threading.Thread(
                    target=self._run_loop_editor,
                    args=(plan,),
                    name="lam.json_test.result_plan",
                    daemon=True,
                )
                self._run_thread.start()
                self._log(f"Run (Result plan) 시작 rows={len(plan)}")
                return
            if not self._merged_steps:
                self._log("실행할 step 이 없습니다. (Result 모드) Load Merged 후 시도하세요.")
                return
            steps_for_run = list(self._merged_steps)
            self._stop_flag.clear()
            self._run_thread = threading.Thread(
                target=self._run_loop_merged,
                args=(steps_for_run,),
                name="lam.json_test.result",
                daemon=True,
            )
            self._run_thread.start()
            self._log(f"Run (Result mode) 시작 steps={len(steps_for_run)}")
            return

        if not self._rows:
            self._log("실행할 행이 없습니다. [+ Add JSON] / [+ Add Delay] 로 추가하세요.")
            return

        plan = self._build_editor_plan_with_paths()
        self._stop_flag.clear()
        self._run_thread = threading.Thread(
            target=self._run_loop_editor,
            args=(plan,),
            name="lam.json_test.editor",
            daemon=True,
        )
        self._run_thread.start()
        self._log(f"Run (Editor mode) 시작 rows={len(plan)}")

    def _build_editor_plan_with_paths(self) -> List[Dict[str, Any]]:
        """Editor ``_rows`` → ``_run_loop_editor`` 가 기대하는 plan (path 포함)."""
        plan: List[Dict[str, Any]] = []
        for row in self._rows:
            if row.kind == ROW_JSON:
                fname = (
                    self._files[row.file_index]
                    if 0 <= row.file_index < len(self._files)
                    else ""
                )
                plan.append({
                    "kind": ROW_JSON,
                    "file": fname,
                    "path": os.path.join(self._sequence_dir, fname) if fname else "",
                    "time_enabled": bool(row.time_enabled),
                    "time_mode": row.time_mode,
                    "time_value": float(row.time_value),
                })
            elif row.kind == ROW_DELAY:
                plan.append({
                    "kind": ROW_DELAY,
                    "sec": max(0.0, float(row.delay_sec)),
                })
        return plan

    @staticmethod
    def _editor_row_is_scheduled(row: _Row) -> bool:
        if row.kind != ROW_JSON or not row.time_enabled:
            return False
        return row.time_mode in (TIME_MODE_AT, TIME_MODE_PLUS)

    def _editor_has_scheduled_json(self) -> bool:
        return any(self._editor_row_is_scheduled(r) for r in self._rows)

    def _build_editor_plan_for_save(self) -> List[Dict[str, Any]]:
        """디스크 저장용 plan — ``file`` + 시간 필드만 (path 는 ``sequence_dir`` 로 복원)."""
        out: List[Dict[str, Any]] = []
        for item in self._build_editor_plan_with_paths():
            d = dict(item)
            d.pop("path", None)
            out.append(d)
        return out

    def _resolve_plan_base_dir(self) -> str:
        """plan v1 JSON 의 파일 경로를 풀 때 사용할 기준 폴더."""
        d = (self._merged_plan_dir or "").strip()
        if d and os.path.isdir(d):
            return os.path.normpath(d)
        src = (self._merged_source_label or "").strip()
        if src and os.path.isfile(src):
            return os.path.normpath(os.path.dirname(os.path.abspath(src)))
        return os.path.normpath(self._sequence_dir)

    def _normalize_plan_for_run(
        self,
        plan: List[Dict[str, Any]],
        base_dir: str,
    ) -> List[Dict[str, Any]]:
        """저장된 plan 에 ``path`` 를 채워 ``_run_loop_editor`` 입력으로 만든다."""
        base_dir = os.path.normpath(base_dir)
        out: List[Dict[str, Any]] = []
        for raw in plan:
            item = dict(raw or {})
            if item.get("kind") == ROW_JSON:
                fn = (item.get("file") or "").strip()
                p = (item.get("path") or "").strip()
                if p and os.path.isfile(p):
                    item["path"] = os.path.normpath(p)
                elif fn:
                    item["path"] = os.path.normpath(os.path.join(base_dir, fn))
                else:
                    item["path"] = ""
                if fn:
                    item["file"] = fn
            out.append(item)
        return out

    def _prepare_merge_save_payload(self) -> None:
        """Editor 에서 Save Merged 직전: 평탄 step 또는 plan v1 중 하나로 메모리를 맞춘다."""
        if self._editor_has_scheduled_json():
            self._merged_plan = self._build_editor_plan_for_save()
            self._merged_plan_dir = self._sequence_dir
            self._merged_steps = []
        else:
            self._merged_steps = self._compute_merged_from_rows()
            self._merged_plan = None
            self._merged_plan_dir = ""

    def _quick_stop_run(self) -> None:
        """UI 스레드에서도 호출 가능 — 모든 활성 러너 중단만 하고 join 하지 않는다."""
        self._stop_flag.set()
        with self._runners_lock:
            runners = list(self._active_runners)
        for r in runners:
            try:
                r.stop(cancel_all_move_rotate=True)
            except Exception:
                pass

    def _stop_run_and_join_worker(self, *, timeout: float = 20.0) -> None:
        """백그라운드에서 Return 직전에 호출 — 연쇄 실행 스레드가 끝날 때까지 대기."""
        self._quick_stop_run()
        t = self._run_thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)

    def _on_stop(self) -> None:
        self._quick_stop_run()
        self._log("Stop 요청됨 (다음 행 진입 직전 또는 sleep 도중 중단)")

    def _on_return(self) -> None:
        """메인(UI) 스레드에서 `_dispatch_main_wait` 를 호출하면 교착이 나므로 백그라운드로 위임."""
        threading.Thread(
            target=lambda: self._do_return_to_initial(reason="Return"),
            name="lam.json_test.return",
            daemon=True,
        ).start()

    def _on_return_then_play(self) -> None:
        threading.Thread(
            target=self._return_then_play_worker,
            name="lam.json_test.return_play",
            daemon=True,
        ).start()

    def _return_then_play_worker(self) -> None:
        self._do_return_to_initial(reason="Return+Play")
        time.sleep(0.1)
        self._on_run()

    def _collect_all_steps_for_return(self) -> tuple[list, list[str]]:
        """Editor / Result 현재 구성에서 reset 대상 step 목록 + ref prim_path 목록."""
        all_steps: list = []
        ref_prims: list[str] = []
        seen_ref: set[str] = set()

        if self._view_mode == VIEW_RESULT:
            if self._merged_plan is not None:
                step_iter = []
                base = self._resolve_plan_base_dir()
                for raw in self._merged_plan:
                    item = dict(raw or {})
                    if item.get("kind") != ROW_JSON:
                        continue
                    fn = (item.get("file") or "").strip()
                    p = (item.get("path") or "").strip()
                    if p and os.path.isfile(p):
                        path = os.path.normpath(p)
                    elif fn:
                        path = os.path.normpath(os.path.join(base, fn))
                    else:
                        continue
                    if not os.path.isfile(path):
                        continue
                    steps = self._load_json_steps(path) or []
                    step_iter.extend(steps)
            else:
                step_iter = list(self._merged_steps)
        else:
            step_iter = []
            for row in self._rows:
                if row.kind != ROW_JSON:
                    continue
                fname = (
                    self._files[row.file_index]
                    if 0 <= row.file_index < len(self._files)
                    else ""
                )
                if not fname:
                    continue
                path = os.path.join(self._sequence_dir, fname)
                if not os.path.isfile(path):
                    continue
                steps = self._load_json_steps(path) or []
                step_iter.extend(steps)

        for st in step_iter:
            if not isinstance(st, dict):
                continue
            all_steps.append(st)
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

        return all_steps, ref_prims

    def _do_return_to_initial(self, *, reason: str) -> None:
        """현재 구성이 참조하는 prim/인스턴스를 초기 위치로 복원."""
        self._stop_run_and_join_worker(timeout=20.0)

        all_steps, ref_prims = self._collect_all_steps_for_return()
        if not all_steps:
            self._log(f"{reason} — 복원할 step 이 없습니다 (JSON 행을 추가하거나 Result 에 step 을 불러오세요).")
            return

        rpaths = _collect_prim_paths_for_reset(all_steps)

        try:
            _dispatch_main_wait(
                lambda: _reset_tbs_offset_ops_for_paths(list(rpaths)),
                timeout=15.0,
            )
        except Exception as exc:
            self._log(f"{reason} — TBS_OFFSET 복원 실패: {exc}")

        def _do_reset_instances_in_main() -> None:
            for pp in ref_prims:
                try:
                    inst = self._registry.get_by_prim_path(pp)
                    if inst is None:
                        continue
                    inst.virtual_time = _range_start_seconds_for_instance(inst)
                    inst.state = "stopped"
                    for fn_name in (
                        "end_replay_mode",
                        "end_master_timeline_mode",
                        "invalidate_mapping",
                        "force_rebuild_attr_cache",
                    ):
                        fn = getattr(self._evaluator, fn_name, None)
                        if fn is None:
                            continue
                        try:
                            fn(pp)
                        except Exception:
                            pass
                except Exception as exc:
                    self._log(f"{reason} — 인스턴스 복원 실패 prim={pp}: {exc}")

        if ref_prims:
            try:
                _dispatch_main_wait(_do_reset_instances_in_main, timeout=15.0)
            except Exception as exc:
                self._log(f"{reason} — 인스턴스 복원 dispatch 실패: {exc}")

        def _do_reset_master_timeline_in_main() -> None:
            try:
                from .lam_master_timeline_play import _get_timeline

                tl = _get_timeline()
                if tl is None:
                    return
                try:
                    tl.pause()  # type: ignore[attr-defined]
                except Exception:
                    pass
                try:
                    tl.set_current_time(0.0)  # type: ignore[attr-defined]
                except Exception:
                    pass
            except Exception as exc:
                self._log(f"{reason} — master timeline pause/seek 실패: {exc}")

        try:
            _dispatch_main_wait(_do_reset_master_timeline_in_main, timeout=5.0)
        except Exception as exc:
            self._log(f"{reason} — master timeline dispatch 실패: {exc}")

        self._log(
            f"{reason} 완료 — prim {len(rpaths)} 개 TBS_OFFSET=0, "
            f"인스턴스 {len(ref_prims)} 개 virtual_time 시작점 복귀, master timeline=0"
        )

    # ----------------------------------------------------------------- run loop

    def _sleep_stoppable(self, sec: float) -> bool:
        """sec 초 동안 100ms 단위로 쪼개서 sleep. stop 요청이 오면 True 반환(즉시 종료)."""
        if sec <= 0:
            return False
        end = time.perf_counter() + sec
        while time.perf_counter() < end:
            if self._stop_flag.is_set():
                return True
            time.sleep(0.05)
        return False

    def _run_loop_editor(self, plan: List[Dict[str, Any]]) -> None:
        """Editor 모드 실행.

        - **시간 체크가 켜진 JSON 행** (``@sec`` / ``+sec``) → 각각 별도 스레드에서
          "예약 시각" 에 자체 ``LamSequenceRunner`` 로 발사한다. 메인 루프는
          이 행에서 블로킹되지 않으므로 첫 행이 끝나기 전이라도 후속 행이 동시에
          시작될 수 있다.
        - **시간 체크가 꺼진 JSON 행 / DELAY 행** → 기존처럼 메인 루프에서 순차 실행.

        예약 시각 계산:
            * ``@sec`` : ``run_start + sec``
            * ``+sec`` : 직전 *시간-예약* 행의 예약 시각 ``+ sec``
              (이전 시간-예약 행이 없으면 ``run_start + sec``)
        """
        run_start = time.perf_counter()

        # ---- 1) 시간-예약 행의 발사 시각 사전 계산.
        scheduled_targets: Dict[int, float] = {}
        last_scheduled = run_start
        for i, item in enumerate(plan):
            if item.get("kind") != ROW_JSON:
                continue
            if not bool(item.get("time_enabled")):
                continue
            mode = str(item.get("time_mode") or TIME_MODE_OFF)
            value = max(0.0, float(item.get("time_value") or 0.0))
            if mode == TIME_MODE_AT:
                target = run_start + value
            elif mode == TIME_MODE_PLUS:
                target = last_scheduled + value
            else:
                # 체크는 됐지만 모드는 OFF — 직렬 처리에 맡긴다.
                continue
            scheduled_targets[i] = target
            last_scheduled = target

        # ---- 2) 시간-예약 행을 별도 스레드로 spawn.
        scheduled_threads: List[threading.Thread] = []
        for i, item in enumerate(plan):
            if i not in scheduled_targets:
                continue
            t = threading.Thread(
                target=self._run_scheduled_json_row,
                args=(scheduled_targets[i], i, dict(item), run_start),
                name=f"lam.json_test.row{i}",
                daemon=True,
            )
            scheduled_threads.append(t)
            t.start()

        # ---- 3) 메인 루프: 시간 OFF JSON 행 + DELAY 행만 직렬 실행.
        seq_runner = LamSequenceRunner(self._registry, self._scheduler)
        with self._runners_lock:
            self._active_runners.append(seq_runner)
        try:
            for i, item in enumerate(plan):
                if self._stop_flag.is_set():
                    self._log(f"row[{i}] 중단됨 (직렬 루프)")
                    break
                kind = item.get("kind")
                if kind == ROW_JSON:
                    if i in scheduled_targets:
                        # 시간-예약 행은 별도 스레드가 처리.
                        continue
                    path = str(item.get("path") or "")
                    if not path or not os.path.isfile(path):
                        self._log(f"row[{i}] JSON 파일 없음: {path}")
                        continue
                    steps = self._load_json_steps(path)
                    if not steps:
                        self._log(f"row[{i}] JSON 빈 시퀀스: {os.path.basename(path)}")
                        continue
                    self._log(
                        f"row[{i}] JSON {os.path.basename(path)} → {len(steps)} step (직렬)"
                    )
                    try:
                        seq_runner.run(steps)
                    except Exception as exc:
                        self._log(f"row[{i}] runner.run 실패: {exc}")
                elif kind == ROW_DELAY:
                    sec = float(item.get("sec") or 0.0)
                    self._log(f"row[{i}] DELAY {sec:.2f}s")
                    if self._sleep_stoppable(sec):
                        self._log(f"row[{i}] DELAY 중단됨")
                        break
        finally:
            with self._runners_lock:
                try:
                    self._active_runners.remove(seq_runner)
                except ValueError:
                    pass

        # ---- 4) 모든 예약 스레드 완료까지 대기 (Stop 시 join 도 곧 풀린다).
        for t in scheduled_threads:
            try:
                t.join()
            except Exception:
                pass
        self._log("Run 완료")

    def _run_scheduled_json_row(
        self,
        target_time: float,
        idx: int,
        item: Dict[str, Any],
        run_start: float,
    ) -> None:
        """예약 시각까지 대기 후, 행 1개를 자체 ``LamSequenceRunner`` 로 실행."""
        wait = max(0.0, target_time - time.perf_counter())
        if wait > 0:
            self._log(
                f"row[{idx}] 예약 대기 {wait:.2f}s (mode={item.get('time_mode')} "
                f"value={item.get('time_value')}, t≈{target_time - run_start:.2f}s)"
            )
            if self._sleep_stoppable(wait):
                self._log(f"row[{idx}] 예약 대기 중단됨")
                return
        if self._stop_flag.is_set():
            self._log(f"row[{idx}] 시작 직전 중단됨")
            return

        path = str(item.get("path") or "")
        fname = str(item.get("file") or os.path.basename(path))
        if not path or not os.path.isfile(path):
            self._log(f"row[{idx}] JSON 파일 없음: {path}")
            return
        steps = self._load_json_steps(path)
        if not steps:
            self._log(f"row[{idx}] JSON 빈 시퀀스: {fname}")
            return

        self._log(
            f"row[{idx}] JSON {fname} → {len(steps)} step (예약 발사 "
            f"t={target_time - run_start:.2f}s)"
        )
        runner = LamSequenceRunner(self._registry, self._scheduler)
        with self._runners_lock:
            self._active_runners.append(runner)
        try:
            runner.run(steps)
        except Exception as exc:
            self._log(f"row[{idx}] runner.run 실패: {exc}")
        finally:
            with self._runners_lock:
                try:
                    self._active_runners.remove(runner)
                except ValueError:
                    pass
        self._log(f"row[{idx}] 종료 {fname}")

    def _run_loop_merged(self, steps: List[Dict[str, Any]]) -> None:
        """Result 모드: merged step 배열을 ``LamSequenceRunner`` 로 한 번에 실행."""
        runner = LamSequenceRunner(self._registry, self._scheduler)
        with self._runners_lock:
            self._active_runners.append(runner)
        try:
            try:
                runner.run(list(steps))
            except Exception as exc:
                self._log(f"merged runner.run 실패: {exc}")
                return
            self._log("Run (Result mode) 완료")
        finally:
            with self._runners_lock:
                try:
                    self._active_runners.remove(runner)
                except ValueError:
                    pass

    # ----------------------------------------------------------------- merge / save / load

    def _compute_merged_from_rows(self) -> List[Dict[str, Any]]:
        """현재 Editor 행 구성을 inline 평탄화한 step 배열로 변환.

        시간 모드 처리:
          - ``OFF``  : 추가 DELAY 없음 (직전 step 종료 직후 시작).
          - ``+sec`` : 그 JSON 시작 전에 ``DELAY {duration: sec}`` 끼움.
          - ``@sec`` : Run 시작 누적 ``DELAY`` 합 = baseline. ``max(0, sec - baseline)``
                      만큼 ``DELAY`` 끼움. 의미적으로 "Run 시작 후 sec 시점에 이 JSON
                      시작" (자동 재생기에서도 동일하게 해석).
        ``[+ Add Delay]`` 행은 그대로 ``DELAY`` step 으로 보존.

        baseline 누적은 끼워 넣은 DELAY 들만 합산 — JSON 내부 step 의 실제 duration 까지
        합산하긴 어렵기 때문에 근사. 이 한계는 사용자도 동의한 모델.
        """
        merged: List[Dict[str, Any]] = []
        baseline_sec = 0.0
        for row in self._rows:
            if row.kind == ROW_JSON:
                fname = (
                    self._files[row.file_index]
                    if 0 <= row.file_index < len(self._files)
                    else ""
                )
                path = os.path.join(self._sequence_dir, fname) if fname else ""
                wait_sec = 0.0
                if row.time_enabled and row.time_mode == TIME_MODE_PLUS:
                    wait_sec = max(0.0, float(row.time_value))
                elif row.time_enabled and row.time_mode == TIME_MODE_AT:
                    wait_sec = max(0.0, float(row.time_value) - baseline_sec)
                if wait_sec > 0:
                    merged.append({
                        "type": "DELAY",
                        "duration": wait_sec,
                        "description": f"auto-from-JSONTester before {fname}",
                    })
                    baseline_sec += wait_sec
                if not path or not os.path.isfile(path):
                    self._log(f"merge: JSON 파일 없음 → 건너뜀: {path}")
                    continue
                file_steps = self._load_json_steps(path) or []
                if not file_steps:
                    self._log(f"merge: 빈 시퀀스 → 건너뜀: {fname}")
                    continue
                for s in file_steps:
                    merged.append(s)
            elif row.kind == ROW_DELAY:
                sec = max(0.0, float(row.delay_sec))
                merged.append({
                    "type": "DELAY",
                    "duration": sec,
                    "description": "JSONTester manual delay",
                })
                baseline_sec += sec
        return merged

    def _on_save_merged(self) -> None:
        if self._view_mode == VIEW_EDITOR:
            self._prepare_merge_save_payload()
        has_plan = self._merged_plan is not None and len(self._merged_plan) > 0
        has_steps = len(self._merged_steps) > 0
        if not has_plan and not has_steps:
            self._log(
                "저장할 내용이 없습니다. Editor 에서 행을 추가하거나 Result 에 데이터가 있는지 확인하세요."
            )
            return
        init_path = os.path.join(self._sequence_dir, "merged.json")
        self._show_file_dialog(
            title="Save Merged JSON",
            apply_label="Save",
            initial_path=init_path,
            on_apply=lambda full: self._do_save_merged(full),
        )

    def _do_save_merged(self, full_path: str) -> None:
        try:
            path = full_path
            if not path.lower().endswith(".json"):
                path += ".json"
            with open(path, "w", encoding="utf-8") as f:
                if self._merged_plan is not None:
                    payload = {
                        "format": MERGED_PLAN_FORMAT_V1,
                        "sequence_dir": self._merged_plan_dir or self._sequence_dir,
                        "plan": self._merged_plan,
                    }
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                    self._log(f"Save Merged OK plan rows={len(self._merged_plan)} → {path}")
                else:
                    json.dump(self._merged_steps, f, indent=2, ensure_ascii=False)
                    self._log(f"Save Merged OK steps={len(self._merged_steps)} → {path}")
            self._merged_source_label = path
            # 자동으로 결과 보기 모드 전환.
            self._view_mode = VIEW_RESULT
            self._refresh_mode_label()
            self._rebuild_rows_ui()
        except Exception as exc:
            self._log(f"Save Merged 실패: {exc}")

    def _on_load_merged(self) -> None:
        init_path = self._merged_source_label if (
            self._merged_source_label and os.path.isfile(self._merged_source_label)
        ) else os.path.join(self._sequence_dir, "merged.json")
        self._show_file_dialog(
            title="Load Merged JSON",
            apply_label="Load",
            initial_path=init_path,
            on_apply=lambda full: self._do_load_merged(full),
        )

    def _do_load_merged(self, full_path: str) -> None:
        if not full_path or not os.path.isfile(full_path):
            self._log(f"Load Merged 실패 — 파일 없음: {full_path}")
            return
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            self._log(f"Load Merged 실패 — JSON 파싱: {exc}")
            return

        self._merged_plan = None
        self._merged_plan_dir = ""

        if isinstance(data, dict):
            fmt = data.get("format")
            if fmt == MERGED_PLAN_FORMAT_V1 and isinstance(data.get("plan"), list):
                self._merged_plan = [dict(x or {}) for x in data["plan"]]
                seqdir = str(data.get("sequence_dir") or "").strip()
                self._merged_plan_dir = seqdir or os.path.dirname(os.path.abspath(full_path))
                self._merged_steps = []
                self._merged_source_label = full_path
                self._view_mode = VIEW_RESULT
                self._refresh_mode_label()
                self._rebuild_rows_ui()
                self._log(f"Load Merged OK plan rows={len(self._merged_plan)} ← {full_path}")
                return
            if isinstance(data.get("steps"), list):
                steps = list(data["steps"])
                self._merged_steps = [dict(s or {}) for s in steps]
                self._merged_source_label = full_path
                self._view_mode = VIEW_RESULT
                self._refresh_mode_label()
                self._rebuild_rows_ui()
                self._log(f"Load Merged OK steps={len(self._merged_steps)} ← {full_path}")
                return
            self._log("Load Merged 실패 — dict 이지만 lam_chain_plan_v1 또는 steps 키가 없음")
            return

        if isinstance(data, list):
            self._merged_steps = [dict(s or {}) for s in data]
            self._merged_source_label = full_path
            self._view_mode = VIEW_RESULT
            self._refresh_mode_label()
            self._rebuild_rows_ui()
            self._log(f"Load Merged OK steps={len(self._merged_steps)} ← {full_path}")
            return

        self._log("Load Merged 실패 — JSON 최상위가 list 또는 지원 dict 가 아님")

    def _show_file_dialog(
        self,
        *,
        title: str,
        apply_label: str,
        initial_path: str,
        on_apply: Callable[[str], None],
    ) -> None:
        try:
            from omni.kit.window.filepicker import FilePickerDialog  # type: ignore
        except Exception as exc:
            self._log(f"FilePicker 사용 불가 — {exc}")
            return
        init_dir = (
            os.path.dirname(initial_path) if os.path.splitext(initial_path)[1] else initial_path
        )
        init_name = os.path.basename(initial_path) if os.path.splitext(initial_path)[1] else ""

        def _handle_apply(filename: str, dirname: str) -> None:
            try:
                full = filename
                if dirname and not os.path.isabs(filename):
                    full = os.path.join(dirname, filename)
                on_apply(full)
            except Exception as _exc:
                self._log(f"FilePicker apply 실패: {_exc}")

        try:
            dlg = FilePickerDialog(
                title,
                apply_button_label=apply_label,
                click_apply_handler=_handle_apply,
                item_filter_options=["*.json", "*"],
            )
            if init_dir and os.path.isdir(init_dir):
                try:
                    dlg.set_current_directory(init_dir)
                except Exception:
                    pass
            if init_name:
                try:
                    dlg.set_filename(init_name)
                except Exception:
                    pass
            dlg.show()
        except Exception as exc:
            self._log(f"FilePicker open 실패: {exc}")

    # ----------------------------------------------------------------- listing / log helpers

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
            self._log(f"JSON 파싱 실패: {exc}")
            return None

    def _log(self, msg: str) -> None:
        print(f"{_PRINT_PREFIX} {msg}", flush=True)
        try:
            if self._log_label is not None:
                self._log_label.text = msg
        except Exception:
            pass

    # ----------------------------------------------------------------- UI rebuild

    def _rebuild_rows_ui(self) -> None:
        try:
            import omni.ui as ui  # type: ignore
        except Exception:
            return
        if self._steps_inner is None:
            return
        self._subs.clear()  # 이전 row 의 subscription 들은 widget 과 함께 사라짐.
        self._steps_inner.clear()

        if self._view_mode == VIEW_RESULT:
            self._rebuild_result_rows_ui(ui)
        else:
            self._rebuild_editor_rows_ui(ui)
        self._refresh_mode_label()

    # ------- Editor mode

    def _rebuild_editor_rows_ui(self, ui) -> None:
        with self._steps_inner:
            if not self._rows:
                ui.Label("(아직 행이 없습니다 — [+ Add JSON] / [+ Add Delay] 로 추가)")
                return
            for i, row in enumerate(self._rows):
                last = i == len(self._rows) - 1
                with ui.HStack(spacing=4, height=24):
                    ui.Label(f"[{i}] {row.kind}", width=80)
                    if row.kind == ROW_JSON:
                        self._build_json_row_widgets(ui, i, row)
                    elif row.kind == ROW_DELAY:
                        self._build_delay_row_widgets(ui, i, row)
                    ui.Spacer()
                    self._build_reorder_remove_buttons(ui, i, first=(i == 0), last=last)

    def _build_json_row_widgets(self, ui, idx: int, row: _Row) -> None:
        items = list(self._files) if self._files else ["(파일 없음)"]
        default_idx = max(0, min(row.file_index, len(items) - 1))
        try:
            combo = ui.ComboBox(default_idx, *items, width=300)
        except Exception as exc:
            self._log(f"ComboBox 생성 실패 row={idx}: {exc}")
            return

        # 드롭다운 선택을 row.file_index 에 즉시 반영 (sticky).
        try:
            inner = self._combo_inner_model(combo)
            if inner is not None:
                def _cb(_m, _row=row, _combo=combo) -> None:
                    try:
                        _row.file_index = int(self._read_combo_index(_combo))
                    except Exception:
                        pass

                sub = inner.add_value_changed_fn(_cb)
                self._subs.setdefault(idx, []).append((inner, sub))
        except Exception:
            pass

        # 시간 컨트롤: [X] time  [+/@]  [____] sec
        chk_model = ui.SimpleBoolModel(bool(row.time_enabled))
        ui.CheckBox(model=chk_model, width=18)
        ui.Label("time", width=36)

        def _on_chk_changed(_m, _row=row) -> None:
            try:
                _row.time_enabled = bool(_m.get_value_as_bool())
            except Exception:
                pass

        try:
            sub_c = chk_model.add_value_changed_fn(_on_chk_changed)
            self._subs.setdefault(idx, []).append((chk_model, sub_c))
        except Exception:
            pass

        # 시간 모드 토글 — Button 으로 클릭마다 OFF → +sec → @sec → OFF.
        def _cycle_mode(_row=row, _idx=idx) -> None:
            try:
                cur = _row.time_mode if _row.time_mode in _TIME_MODE_ORDER else TIME_MODE_OFF
                nxt_idx = (_TIME_MODE_ORDER.index(cur) + 1) % len(_TIME_MODE_ORDER)
                _row.time_mode = _TIME_MODE_ORDER[nxt_idx]
                if _row.time_mode != TIME_MODE_OFF:
                    _row.time_enabled = True
                self._rebuild_rows_ui()
            except Exception:
                pass

        ui.Button(
            _TIME_MODE_LABELS.get(row.time_mode, TIME_MODE_OFF),
            clicked_fn=_cycle_mode,
            width=50,
        )

        sec_model = ui.SimpleFloatModel(float(row.time_value))
        ui.FloatField(model=sec_model, width=70)
        ui.Label("sec", width=28)

        def _on_sec_changed(_m, _row=row) -> None:
            try:
                _row.time_value = float(_m.get_value_as_float())
            except Exception:
                pass

        try:
            sub_s = sec_model.add_value_changed_fn(_on_sec_changed)
            self._subs.setdefault(idx, []).append((sec_model, sub_s))
        except Exception:
            pass

    def _build_delay_row_widgets(self, ui, idx: int, row: _Row) -> None:
        sec_model = ui.SimpleFloatModel(float(row.delay_sec))
        ui.FloatField(model=sec_model, width=80)
        ui.Label("초", width=30)

        def _on_changed(_m, _row=row) -> None:
            try:
                _row.delay_sec = max(0.0, float(_m.get_value_as_float()))
            except Exception:
                pass

        try:
            sub = sec_model.add_value_changed_fn(_on_changed)
            self._subs.setdefault(idx, []).append((sec_model, sub))
        except Exception:
            pass

    # ------- Result mode

    def _rebuild_result_rows_ui(self, ui) -> None:
        with self._steps_inner:
            if self._merged_plan is not None:
                if not self._merged_plan:
                    ui.Label(
                        "(plan 비어 있음 — Editor 에서 구성 후 다시 Save Merged 하거나 Load 하세요)"
                    )
                    return
                for i, item in enumerate(self._merged_plan):
                    kind = str(item.get("kind", "?"))
                    if kind == ROW_JSON:
                        summary = (
                            f"file={item.get('file', '')}  "
                            f"time_on={bool(item.get('time_enabled'))} "
                            f"{item.get('time_mode', '')} {item.get('time_value', '')}"
                        )
                    elif kind == ROW_DELAY:
                        summary = f"sec={float(item.get('sec') or 0.0):.3f}"
                    else:
                        summary = json.dumps(item, ensure_ascii=False)[:120]
                    last = i == len(self._merged_plan) - 1
                    with ui.HStack(spacing=4, height=22):
                        ui.Label(f"[{i}] {kind}", width=80)
                        ui.Label(summary, word_wrap=False)
                        ui.Spacer()
                        self._build_reorder_remove_buttons(ui, i, first=(i == 0), last=last)
                return

            if not self._merged_steps:
                ui.Label(
                    "(Result — Editor 에서 [Save Merged] 하거나 [Load Merged] 로 "
                    "step 배열 / plan JSON 을 불러오세요)"
                )
                return
            for i, st in enumerate(self._merged_steps):
                t = str((st or {}).get("type", "?"))
                summary = self._summarize_step(st or {})
                last = i == len(self._merged_steps) - 1
                with ui.HStack(spacing=4, height=22):
                    ui.Label(f"[{i}] {t}", width=120)
                    ui.Label(summary, word_wrap=False)
                    ui.Spacer()
                    self._build_reorder_remove_buttons(ui, i, first=(i == 0), last=last)

    @staticmethod
    def _summarize_step(st: Dict[str, Any]) -> str:
        t = str(st.get("type", "?")).upper()
        if t == "DELAY":
            return f"duration={float(st.get('duration', 0.0) or 0.0):.3f}s"
        if t in ("USD_TIMELINE", "TIMESAMPLES_REPLAY"):
            ref = st.get("ref") or {}
            return (
                f"prim={ref.get('prim_path','?')} "
                f"frames=[{st.get('start_frame','?')},{st.get('end_frame','?')}] "
                f"speed={st.get('speed_scale','?')} loop={st.get('loop','?')}"
            )
        if t in ("MOVE",):
            return (
                f"prim={st.get('prim','?')} dur={st.get('duration','?')} "
                f"dx={st.get('dx',0)} dy={st.get('dy',0)} dz={st.get('dz',0)} "
                f"move_from_initial={st.get('move_from_initial', False)}"
            )
        if t in ("ROTATE",):
            return (
                f"prim={st.get('prim','?')} dur={st.get('duration','?')} "
                f"rx={st.get('rx',0)} ry={st.get('ry',0)} rz={st.get('rz',0)}"
            )
        return json.dumps({k: v for k, v in st.items() if k != "type"}, ensure_ascii=False)[:120]

    # ------- shared widgets

    def _build_reorder_remove_buttons(self, ui, idx: int, *, first: bool, last: bool) -> None:
        ui.Button(
            "↑",
            width=28,
            enabled=not first,
            clicked_fn=lambda i=idx: self._on_move_up(i),
        )
        ui.Button(
            "↓",
            width=28,
            enabled=not last,
            clicked_fn=lambda i=idx: self._on_move_down(i),
        )
        ui.Button(
            "Remove",
            width=70,
            clicked_fn=lambda i=idx: self._on_remove_row(i),
        )

    # ----------------------------------------------------------------- combo helpers

    @staticmethod
    def _combo_inner_model(combo) -> Any:
        """ComboBox.model 의 ``get_item_value_model()`` 결과(IntModel) 를 안전 추출."""
        try:
            m = combo.model
        except Exception:
            return None
        for getter in (
            lambda: m.get_item_value_model(),
            lambda: m.get_item_value_model(None, 0),
        ):
            try:
                inner = getter()
                if inner is not None:
                    return inner
            except Exception:
                continue
        return None

    @classmethod
    def _read_combo_index(cls, combo) -> int:
        """ComboBox 의 현재 선택 인덱스를 안전하게 읽는다."""
        inner = cls._combo_inner_model(combo)
        if inner is None:
            return 0
        try:
            return int(inner.as_int)
        except Exception:
            try:
                return int(inner.get_value_as_int())
            except Exception:
                return 0


__all__ = ["LamJsonTestWindow"]
