"""LAM 시퀀스 편집기 (P2 — TBS Sequence Editor 와 동일한 4 종 step UI + LAM 추가 step).

【 step 종류 】
  STEP_TYPES = ["USD_TIMELINE", "TIMESAMPLES_REPLAY", "MOVE", "ROTATE", "DELAY", "PRIM_VISIBILITY"]

  - `USD_TIMELINE` / `TIMESAMPLES_REPLAY` 둘 다 LAM 인스턴스 (`ref`) 를 지정하는 재생 step.
      * `TIMESAMPLES_REPLAY` = **실무용** — Option E offscreen 평가 (멀티 인스턴스 독립).
      * `USD_TIMELINE` = **테스트용** (TBS 와 동일 이름·동일 schema. 현 단계에서는 동작도
        TIMESAMPLES_REPLAY 와 동일. 추후 TBS 의 `omni.timeline.play()` 방식으로 재구현 예정).
  - 두 step kind 는 schema 가 완전히 동일하며 같은 UI 컴포넌트를 공유한다.
  - MOVE / ROTATE / DELAY 는 TBS 와 동일 schema.

【 USD_TIMELINE / TIMESAMPLES_REPLAY 의 차이 (REQ-011) 】
  - UI 행 맨 위에 **LAM 인스턴스 드롭다운** 한 줄 추가. 선택 시 step["ref"] 가 4-tuple
    (`prim_path / guid / instance_id / source_asset`) 로 갱신된다(REQ-006).
  - 상태 배지(● OK / ● AUTO / ● MISSING) + Re-bind 버튼.
  - 그 외 START/END/SPEED/MODE 필드는 TBS 와 동일.

【 JSON Save/Load 】
  - 동일 schema. USD_TIMELINE / TIMESAMPLES_REPLAY 만 `ref` 필드를 추가로 가진다.
  - Save: `omni.kit.window.filepicker.FilePickerDialog` 로 파일 위치 선택 후 JSON dump.
  - Load: 같은 파일 다이얼로그로 열고 step 배열을 복원. 모르는 키는 무시한다.

【 Run / Stop 】
  - Run 은 별도 background thread 에서 `TbsLamSequenceRunner.run()` 호출 → main thread 의
    UI freeze 방지. Stop 은 runner.stop() 으로 즉시 중단(다음 sleep 단계에서 빠져나옴).
  - Pause/Resume 은 P3 작업.

【 'Stage 선택에서 prim 가져오기' 】
  - MOVE / ROTATE 의 prim 텍스트 박스 옆 "Stage 선택" 버튼.
    `omni.usd.get_context().get_selection().get_selected_prim_paths()` 결과를 콤마로 채움.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Callable, Dict, List, Optional

from .tbs_id_resolver import resolve_step_ref
from .tbs_instance_registry import AnimationInstanceRegistry
from .tbs_playback_scheduler import PlaybackScheduler
from .tbs_runtime_evaluator import RuntimeEvaluator
from .tbs_lam_sequence_engine import TbsLamSequenceRunner
from .tbs_types import TBS_FIXED_FPS, RESOLVE_AUTO, RESOLVE_MISSING, RESOLVE_OK, StepRef


_PRINT_PREFIX = "[TBS/EDITOR]"

WINDOW_TITLE = "TBS Sequence Editor"


def _range_start_seconds_for_instance(inst) -> float:
    """`inst.range = (mode, s, e)` 기준 시작 초 값 (LAM 고정 30 fps).

    `tbs_json_test_unused._range_start_seconds_for_instance` 와 동일 규칙.
    Reset 버튼이 TIMESAMPLES_REPLAY/USD_TIMELINE 인스턴스의 `virtual_time` 을
    "이 인스턴스가 정의한 시작점" 으로 되돌리기 위해 사용.
    """
    try:
        mode, s, _e = inst.range
    except Exception:
        mode, s, _e = "full", 0.0, 0.0
    tps = float(TBS_FIXED_FPS)
    asset_s = float(getattr(inst, "asset_start_time", 0.0) or 0.0)
    asset_e = float(getattr(inst, "asset_end_time", 0.0) or 0.0)
    if mode == "frames":
        return float(s) / tps
    if mode == "ratio":
        length = max(0.0, asset_e - asset_s) / tps
        return (asset_s / tps) + max(0.0, min(1.0, float(s))) * length
    return asset_s / tps

STEP_TYPES = ["USD_TIMELINE", "TIMESAMPLES_REPLAY", "MOVE", "ROTATE", "DELAY", "PRIM_VISIBILITY"]

PRIM_VISIBILITY_MODES = ("hide", "show")

# JSON 로드 시 에디터 canonical type 으로 정규화.
_LOAD_TYPE_ALIASES = {
    "SET_PRIM_VISIBILITY": "PRIM_VISIBILITY",
    "PRIM_HIDE": "PRIM_VISIBILITY",
    "PRIM_SHOW": "PRIM_VISIBILITY",
}

# UI / 핸들러 측에서 USD_TIMELINE 과 TIMESAMPLES_REPLAY 를 동일하게 처리해야 할 때 사용.
# 두 kind 모두 LAM 인스턴스(ref) 를 지정해 시간 데이터를 재생한다.
_INSTANCE_PLAYBACK_STEP_TYPES = frozenset({"USD_TIMELINE", "TIMESAMPLES_REPLAY"})


def _is_instance_playback_step(t: str) -> bool:
    return (t or "").upper() in _INSTANCE_PLAYBACK_STEP_TYPES

CHECKBOX_WHITE_STYLE = {
    "color": 0xFF000000,
    "background_color": 0xFFEEEEEE,
}

INPUT_FIELD_STYLE = {
    "background_color": 0xFF3B4250,
    "color": 0xFFFFFFFF,
}


# --------------------------------------------------------------------- defaults

def _default_step_for_type(t: str) -> Dict[str, Any]:
    """TBS sequence_editor 의 _on_type_change 와 동일한 기본값."""
    t = (t or "").upper()
    if _is_instance_playback_step(t):
        # USD_TIMELINE / TIMESAMPLES_REPLAY 는 schema 가 완전 동일 — `type` 만 다름.
        return {
            "type": t,
            "ref": StepRef().to_dict(),
            "mode": "MANUAL",
            "start_frame": 200,
            "end_frame": 300,
            "speed_scale": 1.0,
            "loop": False,
            "offset_correction_enabled": False,
            "offset_correct_prims": "",
            "hide_enabled": False,
            "hide_prims": "",
            "run_with_previous": False,
            "step_delay_ms": 0,
            "description": "",
        }
    if t == "MOVE":
        return {
            "type": "MOVE",
            "prim": "",
            "duration": 1.0,
            "dx": 100.0,
            "dy": 0.0,
            "dz": 0.0,
            "move_from_initial": False,
            "hide_enabled": False,
            "hide_prims": "",
            "run_with_previous": False,
            "step_delay_ms": 0,
            "description": "",
        }
    if t == "ROTATE":
        return {
            "type": "ROTATE",
            "prim": "",
            "duration": 1.0,
            "rx": 0.0,
            "ry": 90.0,
            "rz": 0.0,
            # 2026-05-12: 월드 피봇 회전 옵션 제거 (auto_pivot_world_center /
            # user_axis_rotate / pivot_w*). 로드 시 옛 JSON 의 해당 키는 그대로
            # 보존되나, runner 는 무시한다.
            "rotate_from_initial": False,
            "hide_enabled": False,
            "hide_prims": "",
            "run_with_previous": False,
            "step_delay_ms": 0,
            "description": "",
        }
    if t == "PRIM_VISIBILITY":
        return {
            "type": "PRIM_VISIBILITY",
            "mode": "hide",
            "prim": "",
            "duration": 0.02,
            "run_with_previous": False,
            "step_delay_ms": 0,
            "description": "",
        }
    # DELAY
    return {
        "type": "DELAY",
        "duration": 1.0,
        "hide_enabled": False,
        "hide_prims": "",
        "run_with_previous": False,
        "step_delay_ms": 0,
        "description": "",
    }


def _coerce_loaded_step(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """파일/JSON 텍스트의 step dict → canonical step (알 수 없는 type 은 None)."""
    if not isinstance(raw, dict):
        return None
    try:
        from .sequence_renewal import is_renewal_marker, normalize_renewal_step

        if is_renewal_marker(raw):
            return normalize_renewal_step(raw)
    except Exception:
        pass
    raw_type = str(raw.get("type", "")).upper()
    canonical = _LOAD_TYPE_ALIASES.get(raw_type, raw_type)
    if canonical not in STEP_TYPES:
        return None
    step = _default_step_for_type(canonical)
    for k, v in raw.items():
        step[k] = v
    step["type"] = canonical
    if raw_type == "SET_PRIM_VISIBILITY":
        step["mode"] = "show" if bool(raw.get("visible", True)) else "hide"
    elif raw_type == "PRIM_HIDE":
        step["mode"] = "hide"
    elif raw_type == "PRIM_SHOW":
        step["mode"] = "show"
    elif canonical == "PRIM_VISIBILITY":
        mode = str(step.get("mode", "hide") or "hide").strip().lower()
        step["mode"] = mode if mode in PRIM_VISIBILITY_MODES else "hide"
    if canonical == "DELAY" and "duration" not in raw and "seconds" in raw:
        step["duration"] = float(raw.get("seconds", 1.0) or 1.0)
    return step


# --------------------------------------------------------------------- editor

class TbsLamSequenceEditor:
    """LAM 시퀀스 편집기 메인 창."""

    def __init__(
        self,
        registry: AnimationInstanceRegistry,
        scheduler: PlaybackScheduler,
        *,
        default_dir: Optional[str] = None,
        evaluator: Optional[RuntimeEvaluator] = None,
    ) -> None:
        self._registry = registry
        self._scheduler = scheduler
        # Reset 버튼이 TIMESAMPLES_REPLAY / USD_TIMELINE 인스턴스의 virtual_time / master
        # timeline 모드까지 초기화하기 위해 evaluator 가 필요. 호환을 위해 None 허용.
        self._evaluator: Optional[RuntimeEvaluator] = evaluator
        self._steps: List[Dict[str, Any]] = []
        self._window = None
        self._steps_inner = None
        self._status_label = None
        self._runner: Optional[TbsLamSequenceRunner] = None
        self._run_thread: Optional[threading.Thread] = None
        self._default_dir = default_dir or os.getcwd()
        self._json_model = None  # ui.SimpleStringModel — show() 에서 생성
        # UI 이벤트/드로우 도중 Container.clear() 를 직접 호출하면 omni.ui 가
        # `Container::clear was called during an event or draw` 오류와 함께 freeze 한다.
        # → ComboBox/Button 콜백에서는 즉시 rebuild 하지 말고 다음 post_update tick 으로
        #    한 번만 deferred 호출(_schedule_refresh).
        self._refresh_pending: bool = False
        self._refresh_sub = None
        self._registry.add_listener(self._refresh_dropdowns)

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

        # TBS sequence editor 와 동일한 기본 폭(650). 작은 폭에 모든 row 가 들어가도록
        # widget width 들을 더 줄였다. 사용자 환경(DPI / 모니터 폭) 에 맞춰 dock 가능.
        self._window = ui.Window(WINDOW_TITLE, width=650, height=780)
        if self._json_model is None:
            self._json_model = ui.SimpleStringModel("[]")

        with self._window.frame:
            with ui.VStack(spacing=6):
                ui.Label(
                    "LAM 시퀀스 편집기 — 5 종 step (USD_TIMELINE / TIMESAMPLES_REPLAY / MOVE / ROTATE / DELAY). "
                    "REPLAY/TIMELINE 에만 LAM 인스턴스 선택이 붙습니다. "
                    "실무에서는 TIMESAMPLES_REPLAY 를 사용하세요 (멀티 인스턴스 독립 재생).",
                    height=0,
                    word_wrap=True,
                )
                with ui.HStack(spacing=4, height=28):
                    ui.Button("+ Step", clicked_fn=self._add_default_step, width=66)
                    ui.Spacer(width=4)
                    ui.Button("Run", clicked_fn=lambda: self._run_now(reset=False), width=52)
                    ui.Button("Run (reset)", clicked_fn=lambda: self._run_now(reset=True), width=86)
                    ui.Button("Stop", clicked_fn=self._stop_now, width=52)
                    ui.Button(
                        "Reset",
                        clicked_fn=self._reset_now,
                        width=60,
                        tooltip=(
                            "현재 스텝에 등장하는 prim/인스턴스를 초기 상태로 복원합니다.\n"
                            "- MOVE/ROTATE/REPLAY/TIMELINE prim 의 TBS_OFFSET (Translate/Rotate) 을 0 으로.\n"
                            "- TIMESAMPLES_REPLAY / USD_TIMELINE 인스턴스의 virtual_time 을 시작점으로,\n"
                            "  state 를 stopped 로 되돌리고 master timeline 모드도 종료합니다.\n"
                            "시퀀스를 재생하지는 않습니다."
                        ),
                    )
                    ui.Spacer()
                self._status_label = ui.Label("status: idle", height=20)
                ui.Separator()

                ui.Label("시퀀스 JSON", height=18)
                with ui.HStack(spacing=4, height=28):
                    ui.Button("현재 → JSON", clicked_fn=self._update_json_from_steps, width=100)
                    ui.Button("JSON → 스텝", clicked_fn=self._load_steps_from_json_text, width=100)
                    ui.Spacer()
                    ui.Button("Save…", clicked_fn=self._save_json, width=70)
                    ui.Button("Load…", clicked_fn=self._load_json, width=70)
                try:
                    ui.StringField(
                        model=self._json_model,
                        height=96,
                        multiline=True,
                        style=INPUT_FIELD_STYLE,
                    )
                except TypeError:
                    ui.StringField(
                        model=self._json_model,
                        height=96,
                        style=INPUT_FIELD_STYLE,
                    )

                ui.Separator()
                ui.Label("Steps", height=18)
                # 가로 스크롤은 OFF — 내부 widget 들이 모두 컨테이너 폭에 맞도록(StringField 등
                # width 인자 제거). 윈도우 자체 가로 폭은 600 정도면 충분히 들어옴.
                self._steps_container = ui.ScrollingFrame(
                    height=400,
                    horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                    vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_ON,
                )
                with self._steps_container:
                    self._steps_inner = ui.VStack(spacing=4)
        self._schedule_refresh()

    def destroy(self) -> None:
        try:
            if self._runner:
                self._runner.stop()
        except Exception:
            pass
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass
        self._window = None

    # --------------------------------------------------------------- steps mut

    def _add_default_step(self) -> None:
        # 기본은 MOVE (TBS 와 동일).
        self._steps.append(_default_step_for_type("MOVE"))
        self._schedule_refresh()

    def _remove_step(self, idx: int) -> None:
        if 0 <= idx < len(self._steps):
            del self._steps[idx]
            self._schedule_refresh()

    def _move_step(self, idx: int, delta: int) -> None:
        target = idx + delta
        if not (0 <= idx < len(self._steps)) or not (0 <= target < len(self._steps)):
            return
        self._steps[idx], self._steps[target] = self._steps[target], self._steps[idx]
        self._schedule_refresh()

    # ----------------------------------------------------------------- ui

    def _schedule_refresh(self) -> None:
        """UI 이벤트/드로우 중 Container.clear() 호출을 피하기 위한 deferred rebuild.

        TBS sequence_editor._schedule_refresh 와 동일한 패턴.
        """
        if self._refresh_pending:
            return
        self._refresh_pending = True

        def _do(_e=None):
            self._refresh_pending = False
            try:
                self._rebuild_steps_ui()
            finally:
                if self._refresh_sub is not None:
                    try:
                        self._refresh_sub.unsubscribe()
                    except Exception:
                        pass
                    self._refresh_sub = None

        try:
            import omni.kit.app as _app  # type: ignore

            stream = _app.get_app().get_post_update_event_stream()
            self._refresh_sub = stream.create_subscription_to_pop(
                _do, name="morph.tbs_control_2.sequence_editor.refresh"
            )
        except Exception:
            # fallback: 즉시 호출 (최후 수단). Kit 가 없는 환경/테스트 등.
            self._refresh_pending = False
            self._rebuild_steps_ui()

    def _rebuild_steps_ui(self) -> None:
        try:
            import omni.ui as ui  # type: ignore
        except Exception:
            return
        if self._steps_inner is None:
            return
        self._steps_inner.clear()
        with self._steps_inner:
            for idx, step in enumerate(list(self._steps)):
                self._render_step(ui, idx, step)

    def _render_step(self, ui, idx: int, step: Dict[str, Any]) -> None:
        raw_t = str(step.get("type") or "").upper()
        if raw_t in _LOAD_TYPE_ALIASES:
            coerced = _coerce_loaded_step(step)
            if coerced is not None:
                step.clear()
                step.update(coerced)
        title = f"Step {idx+1}: {step.get('type', '?')}"
        with ui.CollapsableFrame(title, height=0):
            with ui.VStack(spacing=4, padding=4):
                # 첫 step 메타 (TBS 와 동일 schema): _start_from_current / paths / snapshot
                if idx == 0:
                    self._ui_start_from_current(ui, step)
                # 설명
                if "description" not in step:
                    step["description"] = ""
                desc_model = ui.SimpleStringModel(str(step.get("description", "") or ""))
                desc_model.add_value_changed_fn(
                    lambda _m, s=step, m=desc_model: s.__setitem__("description", m.get_value_as_string())
                )
                with ui.HStack(spacing=4, height=28):
                    ui.Label("설명", width=40)
                    ui.StringField(model=desc_model, height=28, style=INPUT_FIELD_STYLE)

                # 타입 ComboBox + 위/아래/삭제
                with ui.Frame(style={"background_color": 0xFF20242A}):
                    with ui.HStack(spacing=4, height=28):
                        cur_type = (step.get("type") or "").upper()
                        if cur_type not in STEP_TYPES:
                            cur_type = "MOVE"
                        type_idx = STEP_TYPES.index(cur_type)

                        cb = ui.ComboBox(type_idx, *STEP_TYPES)

                        def _on_type_change(model, *_a, s=step):
                            new_t = STEP_TYPES[model.get_item_value_model().as_int]
                            prev_desc = s.get("description", "")
                            s.clear()
                            s.update(_default_step_for_type(new_t))
                            s["description"] = prev_desc
                            # IMPORTANT: ComboBox 콜백은 omni.ui draw/event 안에서 호출되므로
                            # 여기서 self._rebuild_steps_ui() 를 직접 부르면
                            # `Container::clear was called during an event or draw` 오류로
                            # 앱이 freeze 한다. 반드시 _schedule_refresh 로 다음 tick 에 위임.
                            self._schedule_refresh()

                        cb.model.add_item_changed_fn(_on_type_change)
                        ui.Button("▲", width=28, height=28, tooltip="위로 이동", clicked_fn=lambda i=idx: self._move_step(i, -1))
                        ui.Button("▼", width=28, height=28, tooltip="아래로 이동", clicked_fn=lambda i=idx: self._move_step(i, 1))
                        ui.Button("✕", width=28, height=28, tooltip="이 스텝 삭제", clicked_fn=lambda i=idx: self._remove_step(i))

                # 본문 (타입별)
                with ui.Frame(style={"background_color": 0xFF262A30}):
                    with ui.VStack(spacing=4, padding=6):
                        t = (step.get("type") or "").upper()
                        if _is_instance_playback_step(t):
                            # USD_TIMELINE / TIMESAMPLES_REPLAY 는 같은 UI 컴포넌트 공유.
                            self._ui_step_usd_timeline(ui, idx, step)
                        elif t == "MOVE":
                            self._ui_step_move(ui, step)
                        elif t == "ROTATE":
                            self._ui_step_rotate(ui, step)
                        elif t == "DELAY":
                            self._ui_step_delay(ui, step)
                        elif t == "PRIM_VISIBILITY":
                            self._ui_step_prim_visibility(ui, step)
                        # 공용 timing + hide (PRIM_VISIBILITY 는 sticky — hide_enabled 미사용)
                        self._ui_step_timing(ui, step)
                        if t != "PRIM_VISIBILITY":
                            self._ui_step_hide_options(ui, step)
                ui.Rectangle(height=2, style={"background_color": 0xFF3A3A3A})

    # -------------------------------------------------------- USD_TIMELINE / TIMESAMPLES_REPLAY UI

    def _ui_step_usd_timeline(self, ui, idx: int, step: Dict[str, Any]) -> None:
        # 인스턴스 드롭다운 + 상태 배지 (LAM 전용 1줄).
        # USD_TIMELINE 과 TIMESAMPLES_REPLAY 는 동일 UI 컴포넌트를 공유한다.
        step_kind_label = str(step.get("type") or "USD_TIMELINE").upper()
        instances = self._registry.all_instances()
        ref = StepRef.from_dict(step.get("ref"))
        # AUTO BIND — ref 가 완전히 비어있고 등록된 인스턴스가 있으면
        # 첫 번째 인스턴스로 자동 바인딩 (사용자가 ComboBox 를 클릭하지 않아도
        # step 이 MISSING 으로 끝나는 사고 방지).
        if instances and not (
            (ref.prim_path or "").strip()
            or (ref.guid or "").strip()
            or (ref.instance_id or "").strip()
            or (ref.source_asset or "").strip()
        ):
            inst0 = instances[0]
            ref = StepRef(
                prim_path=inst0.prim_path,
                guid=inst0.guid,
                instance_id=inst0.instance_id,
                source_asset=inst0.source_asset,
            )
            step["ref"] = ref.to_dict()
            print(
                f"{_PRINT_PREFIX} step[{idx}] {step_kind_label} auto-bound to "
                f"first instance: {inst0.instance_id} ({inst0.prim_path})",
                flush=True,
            )

        result = resolve_step_ref(instances, ref)
        status_text = {
            RESOLVE_OK: "● OK",
            RESOLVE_AUTO: "● AUTO",
            RESOLVE_MISSING: "● MISSING (인스턴스 선택 필요)",
        }.get(result.status, "?")

        with ui.HStack(spacing=4, height=28):
            ui.Label("Instance", width=60)
            ids = [inst.instance_id for inst in instances] if instances else ["(등록된 인스턴스 없음)"]
            current_idx = 0
            for i, inst in enumerate(instances):
                if (
                    (ref.prim_path and inst.prim_path == ref.prim_path)
                    or (ref.guid and inst.guid == ref.guid)
                    or (ref.instance_id and inst.instance_id == ref.instance_id)
                ):
                    current_idx = i
                    break
            cb = ui.ComboBox(current_idx, *ids)

            def _on_change(model, *_a, idx=idx, instances=instances):
                if not instances:
                    return
                sel = model.get_item_value_model().as_int
                if not (0 <= sel < len(instances)):
                    return
                inst = instances[sel]
                self._steps[idx]["ref"] = StepRef(
                    prim_path=inst.prim_path,
                    guid=inst.guid,
                    instance_id=inst.instance_id,
                    source_asset=inst.source_asset,
                ).to_dict()
                # ComboBox 콜백 — draw/event 도중이므로 deferred rebuild.
                self._schedule_refresh()

            cb.model.add_item_changed_fn(_on_change)
            badge_color = (
                0xFF60D16A if result.status == RESOLVE_OK
                else 0xFFE0B040 if result.status == RESOLVE_AUTO
                else 0xFFE05050
            )
            ui.Label(status_text, width=140, style={"color": badge_color})
            ui.Button("Re-bind", width=70, clicked_fn=lambda i=idx: self._open_rebind_dialog(i))

        ui.Label(
            f"prim={ref.prim_path or '-'}  guid={ref.guid or '-'}  "
            f"instance_id={ref.instance_id or '-'}  source={ref.source_asset or '-'}",
            height=0,
            word_wrap=True,
            style={"color": 0xFF9AA4B2},
        )

        # 등록된 인스턴스가 0 개면 사용자가 Master USD 를 열거나 USD 를 추가해야 한다는 안내.
        if not instances:
            ui.Label(
                "⚠ 등록된 LAM 인스턴스가 없습니다 — LAM Multi-USD Load 창에서 USD 를 추가하거나 "
                "Master USD 를 열고 [Discover] 를 누르세요.",
                height=0,
                word_wrap=True,
                style={"color": 0xFFE05050},
            )

        # MODE (TBS 와 동일하게 MANUAL 만 노출, AUTO 는 호환만)
        if str(step.get("mode", "MANUAL")).upper() != "MANUAL":
            step["mode"] = "MANUAL"
        with ui.HStack(spacing=4, height=28):
            ui.Label("MODE", width=50)
            ui.ComboBox(0, "MANUAL")

        # START / END
        sf = ui.SimpleIntModel(int(step.get("start_frame", 200)))
        ef = ui.SimpleIntModel(int(step.get("end_frame", 300)))
        sf.add_value_changed_fn(
            lambda _m, s=step, m=sf: s.__setitem__("start_frame", int(m.get_value_as_int()))
        )
        ef.add_value_changed_fn(
            lambda _m, s=step, m=ef: s.__setitem__("end_frame", int(m.get_value_as_int()))
        )
        with ui.HStack(spacing=4, height=28):
            ui.Label("START", width=50)
            ui.IntField(model=sf, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("END", width=36)
            ui.IntField(model=ef, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Spacer()

        # SPEED (per-step speed_scale, TBS 와 동일)
        spm = ui.SimpleFloatModel(float(step.get("speed_scale", 1.0)))

        def _on_spm(_m, s=step, m=spm):
            try:
                v = float(m.get_value_as_float())
            except Exception:
                v = 1.0
            s["speed_scale"] = float(max(0.01, v))

        spm.add_value_changed_fn(_on_spm)
        with ui.HStack(spacing=4, height=28):
            ui.Label("SPEED", width=50)
            ui.FloatField(model=spm, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("x", height=28, width=12, style={"color": 0xFF9AA4B2})
            ui.Spacer()

        # LOOP
        loop_model = ui.SimpleBoolModel(bool(step.get("loop", False)))
        loop_model.add_value_changed_fn(
            lambda _m, s=step, m=loop_model: s.__setitem__("loop", bool(m.get_value_as_bool()))
        )
        with ui.HStack(spacing=4, height=28):
            ui.CheckBox(model=loop_model, width=20, style=CHECKBOX_WHITE_STYLE)
            ui.Label("LOOP", height=0)
            ui.Spacer()

        # offset_correction (UI 만 — 동작은 P3)
        off_model = ui.SimpleBoolModel(bool(step.get("offset_correction_enabled", False)))
        off_model.add_value_changed_fn(
            lambda _m, s=step, m=off_model: s.__setitem__(
                "offset_correction_enabled", bool(m.get_value_as_bool())
            )
        )
        with ui.HStack(spacing=4, height=28):
            ui.CheckBox(model=off_model, width=20, style=CHECKBOX_WHITE_STYLE)
            ui.Label(
                "오프셋 보정 적용 (TBS_OFFSET 재계산)",
                height=0,
            )
            ui.Spacer()
        ocp = ui.SimpleStringModel(str(step.get("offset_correct_prims", "")))
        ocp.add_value_changed_fn(
            lambda _m, s=step, m=ocp: s.__setitem__("offset_correct_prims", m.get_value_as_string())
        )
        with ui.HStack(spacing=4, height=28):
            ui.Label("보정PRIM", width=60)
            ui.StringField(model=ocp, height=28, style=INPUT_FIELD_STYLE)

    # ----------------------------------------------------------------- MOVE UI

    def _ui_step_move(self, ui, step: Dict[str, Any]) -> None:
        prim_model = ui.SimpleStringModel(str(step.get("prim", "")))
        prim_model.add_value_changed_fn(
            lambda _m, s=step, m=prim_model: s.__setitem__("prim", m.get_value_as_string())
        )
        with ui.HStack(spacing=4, height=28):
            ui.Label("PRIM", width=50)
            ui.StringField(model=prim_model, height=28, style=INPUT_FIELD_STYLE)
            ui.Button(
                "Stage",
                width=60,
                height=28,
                clicked_fn=lambda m=prim_model: self._fill_selected_prim(m),
            )

        dur_m = ui.SimpleFloatModel(float(step.get("duration", 1.0)))
        dur_m.add_value_changed_fn(
            lambda _m, s=step, m=dur_m: s.__setitem__("duration", float(m.get_value_as_float()))
        )
        with ui.HStack(spacing=4, height=28):
            ui.Label("DURATION", width=66)
            ui.FloatField(model=dur_m, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("sec", height=0)
            ui.Spacer()

        dx_m = ui.SimpleFloatModel(float(step.get("dx", 0.0)))
        dy_m = ui.SimpleFloatModel(float(step.get("dy", 0.0)))
        dz_m = ui.SimpleFloatModel(float(step.get("dz", 0.0)))
        dx_m.add_value_changed_fn(lambda _m, s=step, m=dx_m: s.__setitem__("dx", float(m.get_value_as_float())))
        dy_m.add_value_changed_fn(lambda _m, s=step, m=dy_m: s.__setitem__("dy", float(m.get_value_as_float())))
        dz_m.add_value_changed_fn(lambda _m, s=step, m=dz_m: s.__setitem__("dz", float(m.get_value_as_float())))
        with ui.HStack(spacing=4, height=28):
            ui.Label("dX", width=24)
            ui.FloatField(model=dx_m, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("dY", width=24)
            ui.FloatField(model=dy_m, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("dZ", width=24)
            ui.FloatField(model=dz_m, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Spacer()

        # move_from_initial — 입력값을 TBS_OFFSET 기준 **절대 좌표** 로 해석 (ROTATE 의 rotate_from_initial 과 동일).
        move_init_m = ui.SimpleBoolModel(bool(step.get("move_from_initial", False)))
        move_init_m.add_value_changed_fn(
            lambda _m, s=step, m=move_init_m: s.__setitem__("move_from_initial", bool(m.get_value_as_bool()))
        )
        with ui.HStack(spacing=4, height=28):
            ui.CheckBox(model=move_init_m, width=20, style=CHECKBOX_WHITE_STYLE)
            ui.Label(
                "최초 위치 기준(절대 좌표) · TBS_OFFSET",
                height=0,
                tooltip=(
                    "켜진 스텝의 (dx,dy,dz) 는 TBS_OFFSET Translate 의 **목표 절대 좌표** 입니다.\n"
                    "예: dx=900 → x=900 위치로 이동. 이미 x=900 이면 추가 이동 없음.\n"
                    "체크 해제 시: 현재 TBS_OFFSET 에 (dx,dy,dz) 만큼 **델타** 이동."
                ),
            )
            ui.Spacer()

    # --------------------------------------------------------------- ROTATE UI

    def _ui_step_rotate(self, ui, step: Dict[str, Any]) -> None:
        prim_model = ui.SimpleStringModel(str(step.get("prim", "")))
        prim_model.add_value_changed_fn(
            lambda _m, s=step, m=prim_model: s.__setitem__("prim", m.get_value_as_string())
        )
        with ui.HStack(spacing=4, height=28):
            ui.Label("PRIM", width=50)
            ui.StringField(model=prim_model, height=28, style=INPUT_FIELD_STYLE)
            ui.Button(
                "Stage",
                width=60,
                height=28,
                clicked_fn=lambda m=prim_model: self._fill_selected_prim(m),
            )

        dur_m = ui.SimpleFloatModel(float(step.get("duration", 1.0)))
        dur_m.add_value_changed_fn(
            lambda _m, s=step, m=dur_m: s.__setitem__("duration", float(m.get_value_as_float()))
        )
        with ui.HStack(spacing=4, height=28):
            ui.Label("DURATION", width=66)
            ui.FloatField(model=dur_m, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("sec", height=0)
            ui.Spacer()

        rx_m = ui.SimpleFloatModel(float(step.get("rx", 0.0)))
        ry_m = ui.SimpleFloatModel(float(step.get("ry", 0.0)))
        rz_m = ui.SimpleFloatModel(float(step.get("rz", 0.0)))
        rx_m.add_value_changed_fn(lambda _m, s=step, m=rx_m: s.__setitem__("rx", float(m.get_value_as_float())))
        ry_m.add_value_changed_fn(lambda _m, s=step, m=ry_m: s.__setitem__("ry", float(m.get_value_as_float())))
        rz_m.add_value_changed_fn(lambda _m, s=step, m=rz_m: s.__setitem__("rz", float(m.get_value_as_float())))
        with ui.HStack(spacing=4, height=28):
            ui.Label("rX", width=24)
            ui.FloatField(model=rx_m, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("rY", width=24)
            ui.FloatField(model=ry_m, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("rZ", width=24)
            ui.FloatField(model=rz_m, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Spacer()

        # rotate_from_initial — 입력값을 "최초 자세 기준 절대 목표각" 으로 해석.
        # 켜면 매 스텝의 (rx,ry,rz) 가 누적 델타가 아니라 초기 자세에서의 절대 각도.
        # 현재 자세와의 차이는 ±180° 로 wrap 되어 항상 짧은 호로 회전.
        init_m = ui.SimpleBoolModel(bool(step.get("rotate_from_initial", False)))
        init_m.add_value_changed_fn(
            lambda _m, s=step, m=init_m: s.__setitem__("rotate_from_initial", bool(m.get_value_as_bool()))
        )
        with ui.HStack(spacing=4, height=28):
            ui.CheckBox(model=init_m, width=20, style=CHECKBOX_WHITE_STYLE)
            ui.Label(
                "최초 위치 기준(절대 각도) · 짧은 호 회전",
                height=0,
                tooltip=(
                    "켜진 스텝의 (rx,ry,rz) 는 USD 로드 시점 자세 기준 절대 각도입니다.\n"
                    "예: 스텝1=rz 90 → 절대 90°, 스텝2=rz 100 → 절대 100° (추가 +10°).\n"
                    "현재 자세에서 목표까지의 차이는 (-180,180] 로 정규화돼 항상 회전반경이 작은 방향으로 돌아갑니다.\n"
                    "체크 해제 시: 입력값이 현재 자세에 더해지는 누적 델타로 동작."
                ),
            )
            ui.Spacer()

    # --------------------------------------------------------- PRIM_VISIBILITY UI

    def _ui_step_prim_visibility(self, ui, step: Dict[str, Any]) -> None:
        """sticky hide/show — 스텝당 prim 1개, 이후 스텝까지 visibility 유지."""
        mode = str(step.get("mode", "hide") or "hide").strip().lower()
        if mode not in PRIM_VISIBILITY_MODES:
            mode = "hide"
            step["mode"] = mode
        mode_idx = PRIM_VISIBILITY_MODES.index(mode)

        with ui.HStack(spacing=4, height=28):
            ui.Label("동작", width=50)
            mode_cb = ui.ComboBox(mode_idx, *PRIM_VISIBILITY_MODES)

            def _on_mode(model, *_a, s=step):
                sel = model.get_item_value_model().as_int
                s["mode"] = PRIM_VISIBILITY_MODES[sel] if 0 <= sel < len(PRIM_VISIBILITY_MODES) else "hide"

            mode_cb.model.add_item_changed_fn(_on_mode)

        prim_model = ui.SimpleStringModel(str(step.get("prim", "")))
        prim_model.add_value_changed_fn(
            lambda _m, s=step, m=prim_model: s.__setitem__("prim", m.get_value_as_string())
        )
        with ui.HStack(spacing=4, height=28):
            ui.Label("PRIM", width=50)
            ui.StringField(model=prim_model, height=28, style=INPUT_FIELD_STYLE)
            ui.Button(
                "Stage",
                width=60,
                height=28,
                clicked_fn=lambda m=prim_model: self._fill_selected_prim(m, first_only=True),
            )
        ui.Label(
            "hide: 이 스텝 이후 강제 숨김 · show: 이 스텝 이후 무조건 표시 (in 클립 직후 웨이퍼 전환용).",
            height=0,
            word_wrap=True,
            style={"color": 0xFF9AA4B2},
        )

    # ----------------------------------------------------------------- DELAY UI

    def _ui_step_delay(self, ui, step: Dict[str, Any]) -> None:
        # 호환: 과거 LAM JSON 의 "seconds" 키도 받아 들임.
        if "duration" not in step and "seconds" in step:
            step["duration"] = float(step.get("seconds", 1.0) or 1.0)
        dur_m = ui.SimpleFloatModel(float(step.get("duration", 1.0)))
        dur_m.add_value_changed_fn(
            lambda _m, s=step, m=dur_m: s.__setitem__("duration", float(m.get_value_as_float()))
        )
        with ui.HStack(spacing=4, height=28):
            ui.Label("DURATION", width=66)
            ui.FloatField(model=dur_m, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("sec", height=0)
            ui.Spacer()

    # ----------------------------------------------------------------- common

    def _ui_step_timing(self, ui, step: Dict[str, Any]) -> None:
        """모든 step 의 끝에 표시되는 공용 timing 행 — 체크박스 옆에 즉시 라벨이 붙어
        보이도록 CheckBox 폭을 명시(width=20)하고 spacing 을 4 로 좁힌다.

        체크박스 옆 라벨은 그 토글의 의미만 짧게 표현하고, ``delay(ms)`` 입력은 같은 행에
        스페이서 없이 바로 이어 붙는다. 가로 폭이 윈도우(600 기본) 안에 모두 들어오도록
        라벨 width 도 단축.
        """
        rwp_m = ui.SimpleBoolModel(bool(step.get("run_with_previous", False)))
        rwp_m.add_value_changed_fn(
            lambda _m, s=step, m=rwp_m: s.__setitem__("run_with_previous", bool(m.get_value_as_bool()))
        )
        delay_m = ui.SimpleIntModel(int(step.get("step_delay_ms", 0)))
        delay_m.add_value_changed_fn(
            lambda _m, s=step, m=delay_m: s.__setitem__("step_delay_ms", int(m.get_value_as_int()))
        )
        with ui.HStack(spacing=4, height=28):
            ui.CheckBox(model=rwp_m, width=20, style=CHECKBOX_WHITE_STYLE)
            ui.Label("이전 step 과 동시 실행", height=0, width=160)
            ui.Spacer(width=10)
            ui.Label("delay(ms)", width=64)
            ui.IntField(model=delay_m, width=70, height=28, style=INPUT_FIELD_STYLE)
            ui.Spacer()

    def _ui_start_from_current(self, ui, step: Dict[str, Any]) -> None:
        """첫 step 의 _start_from_current / _start_from_current_paths / _start_snapshot 메타 UI.

        TBS 와 동일 schema. LAM 의 baseline 모델 차이로 _start_from_current 자체는 거의
        default 동작이지만(LAM 은 baseline 강제 복원이 없음), `_start_snapshot` 의 m16 은
        시작 시 TBS_OFFSET 두 op 로 분해 author 되어 의미가 살아남는다.
        """
        from_current_m = ui.SimpleBoolModel(bool(step.get("_start_from_current", False)))
        paths_m = ui.SimpleStringModel(str(step.get("_start_from_current_paths", "") or ""))
        from_current_m.add_value_changed_fn(
            lambda _m, s=step, m=from_current_m: s.__setitem__(
                "_start_from_current", bool(m.get_value_as_bool())
            )
        )
        paths_m.add_value_changed_fn(
            lambda _m, s=step, m=paths_m: s.__setitem__(
                "_start_from_current_paths", m.get_value_as_string()
            )
        )
        with ui.HStack(spacing=4, height=28):
            ui.CheckBox(model=from_current_m, width=20, style=CHECKBOX_WHITE_STYLE)
            ui.Label("현재 위치부터 시작", width=140)
            ui.Spacer(width=6)
            ui.Label("대상 경로", width=60)
            ui.StringField(model=paths_m, height=28, style=INPUT_FIELD_STYLE)
        with ui.HStack(spacing=4, height=24):
            ui.Button(
                "Snapshot 캡처",
                width=110,
                height=24,
                clicked_fn=lambda s=step, m=paths_m: self._capture_start_snapshot(s, m),
            )
            ui.Button(
                "Snapshot 비우기",
                width=110,
                height=24,
                clicked_fn=lambda s=step: (s.__setitem__("_start_snapshot", {}), self._set_status("snapshot 비웠음")),
            )
            cnt = len((step.get("_start_snapshot") or {}))
            ui.Label(f"snapshot 항목수: {cnt}", height=0)
            ui.Spacer()
        ui.Label(
            "※ LAM 의 baseline 모델 차이로 _start_from_current 자체는 거의 default 동작. "
            "_start_snapshot 의 m16 만 시작 시 TBS_OFFSET 두 op 로 분해 author 되어 의미가 살아남습니다.",
            height=0,
            word_wrap=True,
            style={"color": 0xFF9AA4B2},
        )
        ui.Rectangle(height=2, style={"background_color": 0xFF3A3A3A})

    def _capture_start_snapshot(self, step: Dict[str, Any], paths_model) -> None:
        """대상 경로 prim 들의 parent-relative 합성 로컬 매트릭스를 m16 으로 캡처."""
        try:
            import omni.usd as ou  # type: ignore
            from pxr import Gf, Usd, UsdGeom  # type: ignore

            ctx = ou.get_context()
            stage = ctx.get_stage() if ctx else None
            if stage is None:
                self._set_status("stage 가 없습니다.")
                return
            raw = (paths_model.get_value_as_string() or "").strip()
            paths = [s.strip() for s in raw.split(",") if s.strip()]
            if not paths:
                self._set_status("대상 경로가 비어 있습니다 (콤마로 절대경로를 입력).")
                return
            tc = Usd.TimeCode.Default()
            cache = UsdGeom.XformCache(tc)
            snapshot: Dict[str, Any] = {}
            for path in paths:
                prim = stage.GetPrimAtPath(path)
                if not prim or not prim.IsValid():
                    continue
                M_w = Gf.Matrix4d(cache.GetLocalToWorldTransform(prim))
                parent = prim.GetParent()
                if parent and parent.IsValid() and str(parent.GetPath()) not in ("", "/"):
                    M_pw = Gf.Matrix4d(cache.GetLocalToWorldTransform(parent))
                    M_local = M_pw.GetInverse() * M_w
                else:
                    M_local = M_w
                tr = M_local.ExtractTranslation()
                rot = M_local.ExtractRotation()
                rxyz = rot.Decompose(Gf.Vec3d(1, 0, 0), Gf.Vec3d(0, 1, 0), Gf.Vec3d(0, 0, 1))
                m16 = [float(M_local[i][j]) for i in range(4) for j in range(4)]
                snapshot[path] = {
                    "mode": "composed_local",
                    "t": [float(tr[0]), float(tr[1]), float(tr[2])],
                    "r": [float(rxyz[0]), float(rxyz[1]), float(rxyz[2])],
                    "m16": m16,
                }
            step["_start_snapshot"] = snapshot
            self._schedule_refresh()
            self._set_status(f"snapshot 캡처 완료 — {len(snapshot)} 항목")
        except Exception as exc:
            self._set_status(f"snapshot 캡처 실패: {exc}")

    def _ui_step_hide_options(self, ui, step: Dict[str, Any]) -> None:
        hide_m = ui.SimpleBoolModel(bool(step.get("hide_enabled", False)))
        hide_m.add_value_changed_fn(
            lambda _m, s=step, m=hide_m: s.__setitem__("hide_enabled", bool(m.get_value_as_bool()))
        )
        prims_m = ui.SimpleStringModel(str(step.get("hide_prims", "")))
        prims_m.add_value_changed_fn(
            lambda _m, s=step, m=prims_m: s.__setitem__("hide_prims", m.get_value_as_string())
        )
        # 폭 절약 — 라벨은 짧게(괄호 설명은 tooltip 으로 대체) + hide_prims 필드는 가변.
        with ui.HStack(spacing=4, height=28):
            ui.CheckBox(model=hide_m, width=20, style=CHECKBOX_WHITE_STYLE)
            ui.Label(
                "hide_enabled",
                height=0,
                width=100,
                tooltip="step 시작 invisible / 종료 0.2s 후 visible 복귀",
            )
            ui.Spacer(width=6)
            ui.Label("prims", width=46)
            ui.StringField(model=prims_m, height=28, style=INPUT_FIELD_STYLE)

    # ----------------------------------------------------------------- helpers

    def _fill_selected_prim(self, model, *, first_only: bool = False) -> None:
        """omni.usd 의 현재 selection 으로 prim 텍스트박스 채움 (기본: 콤마 구분, first_only: 첫 경로만)."""
        try:
            import omni.usd as ou  # type: ignore

            ctx = ou.get_context()
            sel = ctx.get_selection() if ctx else None
            paths = list(sel.get_selected_prim_paths()) if sel else []
            if not paths:
                self._set_status("Stage 에서 prim 을 선택한 뒤 다시 누르세요.")
                return
            joined = paths[0] if first_only else ",".join(paths)
            try:
                model.set_value(joined)
            except Exception:
                # 일부 omni.ui 버전 — set_value_as_string
                try:
                    model.set_value_as_string(joined)  # type: ignore[attr-defined]
                except Exception:
                    pass
            self._set_status(f"Stage 선택 채움: {joined}")
        except Exception as exc:
            self._set_status(f"Stage 선택 실패: {exc}")

    def _open_rebind_dialog(self, idx: int) -> None:
        """간단 Re-bind: 첫 인스턴스로 즉시 매칭. (P3 에서 다이얼로그 확장)."""
        instances = self._registry.all_instances()
        if not instances:
            self._set_status("등록된 인스턴스가 없습니다 — LAM Window 에서 USD 를 먼저 추가하세요.")
            return
        inst = instances[0]
        self._steps[idx]["ref"] = StepRef(
            prim_path=inst.prim_path,
            guid=inst.guid,
            instance_id=inst.instance_id,
            source_asset=inst.source_asset,
        ).to_dict()
        self._schedule_refresh()
        print(
            f"{_PRINT_PREFIX} rebind step[{idx}] -> {inst.instance_id} ({inst.prim_path})",
            flush=True,
        )

    def _refresh_dropdowns(self) -> None:
        # Registry 변경 알림 — listener 가 어느 콜백 안에서 호출될지 알 수 없으므로
        # 항상 deferred rebuild.
        self._schedule_refresh()

    def _set_status(self, text: str) -> None:
        try:
            if self._status_label is not None:
                self._status_label.text = f"status: {text}"
        except Exception:
            pass

    # ----------------------------------------------------------------- run/stop

    def _run_now(self, reset: bool = False) -> None:
        if self._run_thread is not None and self._run_thread.is_alive():
            self._set_status("이미 실행 중 — Stop 으로 중단 후 다시 누르세요.")
            return

        # USD_TIMELINE / TIMESAMPLES_REPLAY step 중 ref 가 비어있는 게 있으면 첫 번째
        # 등록 인스턴스로 자동 바인딩. (= 사용자가 인스턴스 ComboBox 를 한 번도 펼쳐보지
        # 않은 케이스 보호)
        instances = self._registry.all_instances()
        autobind_n = 0
        for i, st in enumerate(self._steps):
            t_label = str((st or {}).get("type") or "").upper()
            if not _is_instance_playback_step(t_label):
                continue
            ref = StepRef.from_dict(st.get("ref"))
            if (
                (ref.prim_path or "").strip()
                or (ref.guid or "").strip()
                or (ref.instance_id or "").strip()
                or (ref.source_asset or "").strip()
            ):
                continue
            if not instances:
                self._set_status(
                    f"step[{i}] {t_label} 의 인스턴스가 비어있고 등록된 LAM 인스턴스도 없습니다."
                    " LAM Multi-USD Load 창에서 USD 를 추가하거나 Master 를 열고 Discover 하세요."
                )
                print(
                    f"{_PRINT_PREFIX} _run_now ABORT — step[{i}] {t_label} has empty ref and no instances",
                    flush=True,
                )
                return
            inst0 = instances[0]
            st["ref"] = StepRef(
                prim_path=inst0.prim_path,
                guid=inst0.guid,
                instance_id=inst0.instance_id,
                source_asset=inst0.source_asset,
            ).to_dict()
            autobind_n += 1
            print(
                f"{_PRINT_PREFIX} _run_now: step[{i}] {t_label} auto-bound to "
                f"first instance: {inst0.instance_id} ({inst0.prim_path})",
                flush=True,
            )
        if autobind_n > 0:
            # 다음 frame 에 UI 도 같이 갱신해서 사용자가 어떤 인스턴스가 선택됐는지 볼 수 있게.
            self._schedule_refresh()

        self._runner = TbsLamSequenceRunner(self._registry, self._scheduler)
        steps = list(self._steps)
        self._set_status(f"running… ({len(steps)} steps, reset={reset})")

        def _bg() -> None:
            try:
                self._runner.run(steps, reset_each_start=bool(reset))
                self._set_status("done")
            except Exception as exc:
                print(f"{_PRINT_PREFIX} run failed: {exc}", flush=True)
                self._set_status(f"failed: {exc}")

        self._run_thread = threading.Thread(target=_bg, name="lam_sequence_editor_run", daemon=True)
        self._run_thread.start()

    def _stop_now(self) -> None:
        if self._runner is None:
            self._set_status("not running")
            return
        self._runner.stop()
        self._set_status("stop requested")

    # -------------------------------------------------------------------- reset

    def _reset_now(self) -> None:
        """현재 스텝에 등장하는 prim/인스턴스를 초기 상태로 되돌린다(재생 X).

        - 진행 중인 시퀀스가 있으면 먼저 stop 후 reset.
        - MOVE/ROTATE / USD_TIMELINE / TIMESAMPLES_REPLAY step 의 prim 을 수집해
          **TBS_OFFSET (Translate/Rotate) 을 0 으로** 되돌린다 (자세 / 위치 초기화).
        - USD_TIMELINE / TIMESAMPLES_REPLAY step 의 `ref.prim_path` 인스턴스에 대해
          `virtual_time` 을 자기 시작점으로, `state` 를 stopped 로 되돌리고
          `evaluator.end_master_timeline_mode` / `invalidate_mapping` /
          `force_rebuild_attr_cache` 까지 호출해 timeSamples / timeline 으로 움직였던
          시각·자세까지 모두 초기화한다.

        USD write 는 main thread 에서. `_dispatch_main_wait` 는 main 스레드에서 직접 호출 시
        교착이 나므로 본 함수 자체를 백그라운드 스레드에서 실행한다 (LAM JSON Chain Tester 의 Return
        버튼과 동일 패턴).
        """
        steps = list(self._steps)
        if not steps:
            self._set_status("초기화할 스텝이 없습니다.")
            return

        from .tbs_lam_sequence_engine import _collect_prim_paths_for_reset

        paths = _collect_prim_paths_for_reset(steps)
        if not paths:
            self._set_status(
                "초기화할 prim 이 없습니다 — 시퀀스에 "
                "MOVE / ROTATE / USD_TIMELINE / TIMESAMPLES_REPLAY step 이 필요합니다."
            )
            return

        self._set_status(f"resetting… ({len(paths)} prim)")
        print(f"{_PRINT_PREFIX} reset requested for {len(paths)} prim(s): {paths}", flush=True)
        threading.Thread(
            target=self._do_reset_worker,
            args=(steps, paths),
            name="lam_seq_editor_reset",
            daemon=True,
        ).start()

    def _do_reset_worker(self, steps: List[Dict[str, Any]], paths: List[str]) -> None:
        """`_reset_now` 의 백그라운드 작업자.

        - 시퀀스 러너 중단.
        - TBS_OFFSET 0 으로 (main tick 에서 USD write, 완료까지 대기).
        - REPLAY/TIMELINE 인스턴스 virtual_time / state / evaluator 모드 정리.
        """
        from .tbs_lam_sequence_engine import (
            _dispatch_main_wait,
            _reset_tbs_offset_ops_for_paths,
        )

        if self._runner is not None:
            try:
                self._runner.stop()
            except Exception:
                pass

        try:
            ok = _dispatch_main_wait(
                lambda: _reset_tbs_offset_ops_for_paths(paths),
                timeout=15.0,
            )
            if not ok:
                self._set_status("reset(TBS_OFFSET) 시간 초과 — 다음 tick 에서 반영 예정")
        except Exception as exc:
            self._set_status(f"reset(TBS_OFFSET) 실패: {exc}")
            print(f"{_PRINT_PREFIX} reset(TBS_OFFSET) failed: {exc}", flush=True)

        ref_prims: List[str] = []
        seen: set = set()
        for st in steps:
            if not isinstance(st, dict):
                continue
            t = str(st.get("type") or "").upper()
            if t not in ("USD_TIMELINE", "TIMESAMPLES_REPLAY"):
                continue
            try:
                ref = StepRef.from_dict(st.get("ref"))
            except Exception:
                continue
            pp = (ref.prim_path or "").strip()
            if pp.startswith("/") and pp not in seen:
                seen.add(pp)
                ref_prims.append(pp)

        # 인스턴스 정리는 evaluator 의 USD write (`end_master_timeline_mode` 안의
        # `_set_prim_layer_offset` / `_set_omnigraph_active_in_sublayer` /
        # `_ensure_option_e_freeze`) 가 포함되므로 **main thread 의 다음 tick** 에서
        # 한꺼번에 수행한다. 백그라운드에서 직접 호출하면 main 의 evaluator update 와
        # 동시 USD write 로 freeze 가 발생한다 (2026-05-12 회귀 fix).
        n_inst_box: Dict[str, int] = {"n": 0}

        def _do_reset_instances_in_main() -> None:
            for pp in ref_prims:
                try:
                    inst = self._registry.get_by_prim_path(pp)
                except Exception:
                    inst = None
                if inst is None:
                    continue
                try:
                    inst.virtual_time = _range_start_seconds_for_instance(inst)
                    inst.state = "stopped"
                    n_inst_box["n"] += 1
                except Exception as exc:
                    print(
                        f"{_PRINT_PREFIX} reset instance virtual_time failed prim={pp}: {exc}",
                        flush=True,
                    )
                if self._evaluator is not None:
                    # `end_replay_mode` 도 함께 호출 — TIMESAMPLES_REPLAY step 이 도중
                    # stop / reset 된 경우 sublayer 에 남은 freeze 오버라이드
                    # (LayerOffset(0,1e-9) + OmniGraph active=false) 를 (0,1) + active=true
                    # 로 복원해 master 타임라인이 자유 평가되도록 한다.
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
                        except Exception as exc:
                            print(
                                f"{_PRINT_PREFIX} reset evaluator.{fn_name} failed prim={pp}: {exc}",
                                flush=True,
                            )

        if ref_prims:
            try:
                ok_inst = _dispatch_main_wait(_do_reset_instances_in_main, timeout=15.0)
                if not ok_inst:
                    self._set_status("reset(instance) 시간 초과 — 다음 tick 에서 반영 예정")
            except Exception as exc:
                self._set_status(f"reset(instance) 실패: {exc}")
                print(f"{_PRINT_PREFIX} reset(instance) failed: {exc}", flush=True)

        # master 타임라인을 0 으로 되돌리고 pause — TIMESAMPLES_REPLAY / USD_TIMELINE
        # step 종료 시점의 current_time (예: end_frame / 30) 이 그대로 남아있으면
        # reference 의 timeSamples 가 그 시점으로 평가되어 viewport 가 마지막 자세에
        # 멈춘 채로 보인다. ref_prims 가 비어있는 MOVE/ROTATE 전용 시퀀스에서도 동일하게
        # 안전 차원으로 호출 (이미 0 이면 부작용 없음). 항상 main tick 에서 수행.
        def _do_reset_master_timeline_in_main() -> None:
            try:
                from .tbs_master_timeline_play import _get_timeline

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
                print(
                    f"{_PRINT_PREFIX} reset master timeline pause/seek failed: {exc}",
                    flush=True,
                )

        try:
            _dispatch_main_wait(_do_reset_master_timeline_in_main, timeout=5.0)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} reset master timeline dispatch failed: {exc}", flush=True)

        n_inst = n_inst_box["n"]
        self._set_status(
            f"reset done — prim {len(paths)} 개 TBS_OFFSET=0, 인스턴스 {n_inst} 개 virtual_time 시작점 복귀"
        )
        print(
            f"{_PRINT_PREFIX} reset done ({len(paths)} prim TBS_OFFSET=0, "
            f"{n_inst} instance virtual_time reset)",
            flush=True,
        )

    # ----------------------------------------------------------------- json io

    def _update_json_from_steps(self) -> None:
        """TBS `현재스탭으로 json 생성` 과 동일 — 편집 중인 스텝을 JSON 텍스트 영역에 반영."""
        try:
            if self._json_model is None:
                self._set_status("JSON 모델 없음 — 창을 다시 여세요.")
                return
            self._json_model.set_value(json.dumps(self._steps, ensure_ascii=False, indent=2))
            self._set_status(f"현재 {len(self._steps)}개 스텝을 JSON 영역에 반영했습니다.")
        except Exception as exc:
            self._set_status(f"JSON 생성 실패: {exc}")

    def _load_steps_from_json_text(self) -> None:
        """TBS `현재 JSON 상태로 스텝 생성하기` 와 동일 — 텍스트 영역을 파싱해 스텝 교체."""
        try:
            if self._json_model is None:
                self._set_status("JSON 모델 없음 — 창을 다시 여세요.")
                return
            txt = self._json_model.get_value_as_string()
            data = json.loads(txt or "[]")
            n = self._merge_loaded_steps_from_list(data)
            self._set_status(f"JSON 영역에서 {n}개 스텝을 불러왔습니다.")
            print(f"{_PRINT_PREFIX} loaded {n} steps from JSON text field", flush=True)
        except json.JSONDecodeError as exc:
            self._set_status(f"JSON 파싱 실패: {exc}")
        except Exception as exc:
            self._set_status(f"스텝 로드 실패: {exc}")

    def _merge_loaded_steps_from_list(self, data: Any) -> int:
        """파일 또는 텍스트 필드에서 온 list → self._steps. 반환: 로드된 스텝 개수."""
        if not isinstance(data, list):
            return 0
        cleaned: List[Dict[str, Any]] = []
        for raw in data:
            step = _coerce_loaded_step(raw)
            if step is not None:
                cleaned.append(step)
        self._steps = cleaned
        self._schedule_refresh()
        return len(cleaned)

    def _save_json(self) -> None:
        path_default = os.path.join(self._default_dir, "tbs_sequence.json")
        self._show_file_dialog(
            title="LAM 시퀀스 JSON 저장",
            apply_label="저장",
            initial_path=path_default,
            on_apply=self._do_save_json,
        )

    def _load_json(self) -> None:
        self._show_file_dialog(
            title="LAM 시퀀스 JSON 불러오기",
            apply_label="열기",
            initial_path=self._default_dir,
            on_apply=self._do_load_json,
        )

    def _do_save_json(self, path: str) -> None:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._steps, f, ensure_ascii=False, indent=2)
            if self._json_model is not None:
                try:
                    self._json_model.set_value(json.dumps(self._steps, ensure_ascii=False, indent=2))
                except Exception:
                    pass
            self._set_status(f"saved: {path}")
            print(f"{_PRINT_PREFIX} saved {len(self._steps)} steps -> {path}", flush=True)
        except Exception as exc:
            self._set_status(f"save failed: {exc}")

    def _do_load_json(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                self._set_status("JSON 최상위가 list 가 아닙니다.")
                return
            n = self._merge_loaded_steps_from_list(data)
            if self._json_model is not None:
                try:
                    self._json_model.set_value(json.dumps(self._steps, ensure_ascii=False, indent=2))
                except Exception:
                    pass
            self._set_status(f"loaded {n} steps from {path}")
            print(f"{_PRINT_PREFIX} loaded {n} steps <- {path}", flush=True)
        except Exception as exc:
            self._set_status(f"load failed: {exc}")

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
            self._set_status(f"FilePicker 사용 불가 — {exc}")
            return
        init_dir = (
            os.path.dirname(initial_path) if os.path.splitext(initial_path)[1] else initial_path
        )
        init_name = os.path.basename(initial_path) if os.path.splitext(initial_path)[1] else ""
        try:
            dlg = FilePickerDialog(
                title,
                apply_button_label=apply_label,
                click_apply_handler=lambda fn, dn: self._on_file_picker_apply(fn, dn, on_apply),
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
            self._set_status(f"FilePicker open 실패 — {exc}")

    @staticmethod
    def _on_file_picker_apply(filename: str, dirname: str, on_apply: Callable[[str], None]) -> None:
        try:
            full = filename
            if dirname and not os.path.isabs(filename):
                full = os.path.join(dirname, filename)
            on_apply(full)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} file picker apply failed: {exc}", flush=True)


__all__ = ["TbsLamSequenceEditor", "STEP_TYPES"]
