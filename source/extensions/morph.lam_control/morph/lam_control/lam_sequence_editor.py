"""LAM 시퀀스 편집기 (P2 — TBS Sequence Editor 와 동일한 4 종 step UI).

【 step 종류 — TBS 와 동일 】
  STEP_TYPES = ["USD_TIMELINE", "MOVE", "ROTATE", "DELAY"]

【 USD_TIMELINE 의 단 한 가지 차이 (REQ-011) 】
  - UI 행 맨 위에 **LAM 인스턴스 드롭다운** 한 줄 추가. 선택 시 step["ref"] 가 4-tuple
    (`prim_path / guid / instance_id / source_asset`) 로 갱신된다(REQ-006).
  - 상태 배지(● OK / ● AUTO / ● MISSING) + Re-bind 버튼.
  - 그 외 START/END/SPEED/MODE 필드는 TBS 와 동일.

【 JSON Save/Load 】
  - 동일 schema. 다만 USD_TIMELINE 만 `ref` 필드를 추가로 가진다.
  - Save: `omni.kit.window.filepicker.FilePickerDialog` 로 파일 위치 선택 후 JSON dump.
  - Load: 같은 파일 다이얼로그로 열고 step 배열을 복원. 모르는 키는 무시한다.

【 Run / Stop 】
  - Run 은 별도 background thread 에서 `LamSequenceRunner.run()` 호출 → main thread 의
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

from .lam_id_resolver import resolve_step_ref
from .lam_instance_registry import AnimationInstanceRegistry
from .lam_playback_scheduler import PlaybackScheduler
from .lam_sequence_engine import LamSequenceRunner
from .lam_types import RESOLVE_AUTO, RESOLVE_MISSING, RESOLVE_OK, StepRef


_PRINT_PREFIX = "[LAM/EDITOR]"

WINDOW_TITLE = "LAM Sequence Editor"

STEP_TYPES = ["USD_TIMELINE", "MOVE", "ROTATE", "DELAY"]

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
    if t == "USD_TIMELINE":
        return {
            "type": "USD_TIMELINE",
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
            "auto_pivot_world_center": False,
            "user_axis_rotate": False,
            "pivot_wx": 0.0,
            "pivot_wy": 0.0,
            "pivot_wz": 0.0,
            "hide_enabled": False,
            "hide_prims": "",
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


# --------------------------------------------------------------------- editor

class LamSequenceEditor:
    """LAM 시퀀스 편집기 메인 창."""

    def __init__(
        self,
        registry: AnimationInstanceRegistry,
        scheduler: PlaybackScheduler,
        *,
        default_dir: Optional[str] = None,
    ) -> None:
        self._registry = registry
        self._scheduler = scheduler
        self._steps: List[Dict[str, Any]] = []
        self._window = None
        self._steps_inner = None
        self._status_label = None
        self._runner: Optional[LamSequenceRunner] = None
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

        self._window = ui.Window(WINDOW_TITLE, width=840, height=780)
        if self._json_model is None:
            self._json_model = ui.SimpleStringModel("[]")

        with self._window.frame:
            with ui.VStack(spacing=6):
                ui.Label(
                    "LAM 시퀀스 편집기 — 4 종 step (USD_TIMELINE / MOVE / ROTATE / DELAY). "
                    "USD_TIMELINE 에만 LAM 인스턴스 선택이 붙습니다.",
                    height=36,
                )
                with ui.HStack(spacing=6, height=28):
                    ui.Button("+ Step 추가", clicked_fn=self._add_default_step, width=110)
                    ui.Spacer(width=8)
                    ui.Button("Run", clicked_fn=lambda: self._run_now(reset=False), width=60)
                    ui.Button("Run (reset)", clicked_fn=lambda: self._run_now(reset=True), width=100)
                    ui.Button("Stop", clicked_fn=self._stop_now, width=60)
                    ui.Button(
                        "Reset",
                        clicked_fn=self._reset_now,
                        width=70,
                        tooltip=(
                            "현재 스텝에 등장하는 prim 들의 TBS_OFFSET (Translate/Rotate) 을 0 으로\n"
                            "되돌려 초기 위치/자세로 복귀시킵니다. 시퀀스를 재생하지는 않습니다."
                        ),
                    )
                self._status_label = ui.Label("status: idle", height=20)
                ui.Separator()

                ui.Label("시퀀스 JSON", height=18)
                with ui.HStack(spacing=6, height=28):
                    ui.Button("현재 스텝 → JSON", clicked_fn=self._update_json_from_steps, width=130)
                    ui.Button("JSON → 스텝", clicked_fn=self._load_steps_from_json_text, width=100)
                    ui.Spacer()
                    ui.Button("Save JSON…", clicked_fn=self._save_json, width=100)
                    ui.Button("Load JSON…", clicked_fn=self._load_json, width=100)
                try:
                    ui.StringField(
                        model=self._json_model,
                        height=96,
                        width=800,
                        multiline=True,
                        style=INPUT_FIELD_STYLE,
                    )
                except TypeError:
                    ui.StringField(
                        model=self._json_model,
                        height=96,
                        width=800,
                        style=INPUT_FIELD_STYLE,
                    )

                ui.Separator()
                ui.Label("Steps", height=18)
                self._steps_container = ui.ScrollingFrame(
                    height=400,
                    horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
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
                _do, name="morph.lam_control.sequence_editor.refresh"
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
        title = f"Step {idx+1}: {step.get('type', '?')}"
        with ui.CollapsableFrame(title, height=0):
            with ui.VStack(spacing=6, padding=6):
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
                with ui.HStack(spacing=6, height=28):
                    ui.Label("설명", width=40)
                    ui.StringField(model=desc_model, width=540, height=28, style=INPUT_FIELD_STYLE)

                # 타입 ComboBox + 위/아래/삭제
                with ui.Frame(style={"background_color": 0xFF20242A}):
                    with ui.HStack(spacing=6, height=28):
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
                        ui.Button("위", width=40, height=28, clicked_fn=lambda i=idx: self._move_step(i, -1))
                        ui.Button("아래", width=50, height=28, clicked_fn=lambda i=idx: self._move_step(i, 1))
                        ui.Button("삭제", width=60, height=28, clicked_fn=lambda i=idx: self._remove_step(i))

                # 본문 (타입별)
                with ui.Frame(style={"background_color": 0xFF262A30}):
                    with ui.VStack(spacing=6, padding=8):
                        t = (step.get("type") or "").upper()
                        if t == "USD_TIMELINE":
                            self._ui_step_usd_timeline(ui, idx, step)
                        elif t == "MOVE":
                            self._ui_step_move(ui, step)
                        elif t == "ROTATE":
                            self._ui_step_rotate(ui, step)
                        elif t == "DELAY":
                            self._ui_step_delay(ui, step)
                        # 공용 timing + hide
                        self._ui_step_timing(ui, step)
                        self._ui_step_hide_options(ui, step)
                ui.Rectangle(height=2, style={"background_color": 0xFF3A3A3A})

    # -------------------------------------------------------- USD_TIMELINE UI

    def _ui_step_usd_timeline(self, ui, idx: int, step: Dict[str, Any]) -> None:
        # 인스턴스 드롭다운 + 상태 배지 (LAM 전용 1줄)
        instances = self._registry.all_instances()
        ref = StepRef.from_dict(step.get("ref"))
        # AUTO BIND — ref 가 완전히 비어있고 등록된 인스턴스가 있으면
        # 첫 번째 인스턴스로 자동 바인딩 (사용자가 ComboBox 를 클릭하지 않아도
        # USD_TIMELINE step 이 MISSING 으로 끝나는 사고 방지).
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
                f"{_PRINT_PREFIX} step[{idx}] USD_TIMELINE auto-bound to "
                f"first instance: {inst0.instance_id} ({inst0.prim_path})",
                flush=True,
            )

        result = resolve_step_ref(instances, ref)
        status_text = {
            RESOLVE_OK: "● OK",
            RESOLVE_AUTO: "● AUTO",
            RESOLVE_MISSING: "● MISSING (인스턴스 선택 필요)",
        }.get(result.status, "?")

        with ui.HStack(spacing=6, height=28):
            ui.Label("Instance", width=70)
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
            ui.Label(status_text, width=200, style={"color": badge_color})
            ui.Button("Re-bind…", width=80, clicked_fn=lambda i=idx: self._open_rebind_dialog(i))

        ui.Label(
            f"prim={ref.prim_path or '-'}  guid={ref.guid or '-'}  "
            f"instance_id={ref.instance_id or '-'}  source={ref.source_asset or '-'}",
            height=18,
        )

        # 등록된 인스턴스가 0 개면 사용자가 Master USD 를 열거나 USD 를 추가해야 한다는 안내.
        if not instances:
            ui.Label(
                "⚠ 등록된 LAM 인스턴스가 없습니다 — LAM Multi-USD Load 창에서 USD 를 추가하거나 "
                "Master USD 를 열고 [Discover] 를 누르세요.",
                height=18,
                style={"color": 0xFFE05050},
            )

        # MODE (TBS 와 동일하게 MANUAL 만 노출, AUTO 는 호환만)
        if str(step.get("mode", "MANUAL")).upper() != "MANUAL":
            step["mode"] = "MANUAL"
        with ui.HStack(spacing=6, height=28):
            ui.Label("MODE", width=60)
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
        with ui.HStack(spacing=6, height=28):
            ui.Label("START", width=60)
            ui.IntField(model=sf, width=80, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("END", width=40)
            ui.IntField(model=ef, width=80, height=28, style=INPUT_FIELD_STYLE)

        # SPEED (per-step speed_scale, TBS 와 동일)
        spm = ui.SimpleFloatModel(float(step.get("speed_scale", 1.0)))

        def _on_spm(_m, s=step, m=spm):
            try:
                v = float(m.get_value_as_float())
            except Exception:
                v = 1.0
            s["speed_scale"] = float(max(0.01, v))

        spm.add_value_changed_fn(_on_spm)
        with ui.HStack(spacing=6, height=28):
            ui.Label("SPEED", width=60)
            ui.FloatField(model=spm, width=80, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("x (USD_TIMELINE 전용)", height=28, style={"color": 0xFF9AA4B2})

        # LOOP
        loop_model = ui.SimpleBoolModel(bool(step.get("loop", False)))
        loop_model.add_value_changed_fn(
            lambda _m, s=step, m=loop_model: s.__setitem__("loop", bool(m.get_value_as_bool()))
        )
        with ui.HStack(spacing=6, height=28):
            ui.CheckBox(model=loop_model, style=CHECKBOX_WHITE_STYLE)
            ui.Label("LOOP", height=0)

        # offset_correction (UI 만 — 동작은 P3)
        off_model = ui.SimpleBoolModel(bool(step.get("offset_correction_enabled", False)))
        off_model.add_value_changed_fn(
            lambda _m, s=step, m=off_model: s.__setitem__(
                "offset_correction_enabled", bool(m.get_value_as_bool())
            )
        )
        with ui.HStack(spacing=6, height=28):
            ui.CheckBox(model=off_model, style=CHECKBOX_WHITE_STYLE)
            ui.Label(
                "오프셋 보정 적용 (USD_TIMELINE 시작 직전 TBS_OFFSET 두 op 재계산)",
                height=0,
            )
        ocp = ui.SimpleStringModel(str(step.get("offset_correct_prims", "")))
        ocp.add_value_changed_fn(
            lambda _m, s=step, m=ocp: s.__setitem__("offset_correct_prims", m.get_value_as_string())
        )
        with ui.HStack(spacing=6, height=28):
            ui.Label("보정PRIM", width=60)
            ui.StringField(model=ocp, width=420, height=28, style=INPUT_FIELD_STYLE)

    # ----------------------------------------------------------------- MOVE UI

    def _ui_step_move(self, ui, step: Dict[str, Any]) -> None:
        prim_model = ui.SimpleStringModel(str(step.get("prim", "")))
        prim_model.add_value_changed_fn(
            lambda _m, s=step, m=prim_model: s.__setitem__("prim", m.get_value_as_string())
        )
        with ui.HStack(spacing=6, height=28):
            ui.Label("PRIM", width=60)
            ui.StringField(model=prim_model, width=420, height=28, style=INPUT_FIELD_STYLE)
            ui.Button(
                "Stage 선택",
                width=90,
                height=28,
                clicked_fn=lambda m=prim_model: self._fill_selected_prim(m),
            )

        dur_m = ui.SimpleFloatModel(float(step.get("duration", 1.0)))
        dur_m.add_value_changed_fn(
            lambda _m, s=step, m=dur_m: s.__setitem__("duration", float(m.get_value_as_float()))
        )
        with ui.HStack(spacing=6, height=28):
            ui.Label("DURATION", width=80)
            ui.FloatField(model=dur_m, width=80, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("sec", height=0)

        dx_m = ui.SimpleFloatModel(float(step.get("dx", 0.0)))
        dy_m = ui.SimpleFloatModel(float(step.get("dy", 0.0)))
        dz_m = ui.SimpleFloatModel(float(step.get("dz", 0.0)))
        dx_m.add_value_changed_fn(lambda _m, s=step, m=dx_m: s.__setitem__("dx", float(m.get_value_as_float())))
        dy_m.add_value_changed_fn(lambda _m, s=step, m=dy_m: s.__setitem__("dy", float(m.get_value_as_float())))
        dz_m.add_value_changed_fn(lambda _m, s=step, m=dz_m: s.__setitem__("dz", float(m.get_value_as_float())))
        with ui.HStack(spacing=6, height=28):
            ui.Label("dX", width=30)
            ui.FloatField(model=dx_m, width=80, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("dY", width=30)
            ui.FloatField(model=dy_m, width=80, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("dZ", width=30)
            ui.FloatField(model=dz_m, width=80, height=28, style=INPUT_FIELD_STYLE)

    # --------------------------------------------------------------- ROTATE UI

    def _ui_step_rotate(self, ui, step: Dict[str, Any]) -> None:
        prim_model = ui.SimpleStringModel(str(step.get("prim", "")))
        prim_model.add_value_changed_fn(
            lambda _m, s=step, m=prim_model: s.__setitem__("prim", m.get_value_as_string())
        )
        with ui.HStack(spacing=6, height=28):
            ui.Label("PRIM", width=60)
            ui.StringField(model=prim_model, width=420, height=28, style=INPUT_FIELD_STYLE)
            ui.Button(
                "Stage 선택",
                width=90,
                height=28,
                clicked_fn=lambda m=prim_model: self._fill_selected_prim(m),
            )

        dur_m = ui.SimpleFloatModel(float(step.get("duration", 1.0)))
        dur_m.add_value_changed_fn(
            lambda _m, s=step, m=dur_m: s.__setitem__("duration", float(m.get_value_as_float()))
        )
        with ui.HStack(spacing=6, height=28):
            ui.Label("DURATION", width=80)
            ui.FloatField(model=dur_m, width=80, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("sec", height=0)

        rx_m = ui.SimpleFloatModel(float(step.get("rx", 0.0)))
        ry_m = ui.SimpleFloatModel(float(step.get("ry", 0.0)))
        rz_m = ui.SimpleFloatModel(float(step.get("rz", 0.0)))
        rx_m.add_value_changed_fn(lambda _m, s=step, m=rx_m: s.__setitem__("rx", float(m.get_value_as_float())))
        ry_m.add_value_changed_fn(lambda _m, s=step, m=ry_m: s.__setitem__("ry", float(m.get_value_as_float())))
        rz_m.add_value_changed_fn(lambda _m, s=step, m=rz_m: s.__setitem__("rz", float(m.get_value_as_float())))
        with ui.HStack(spacing=6, height=28):
            ui.Label("rX", width=30)
            ui.FloatField(model=rx_m, width=80, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("rY", width=30)
            ui.FloatField(model=ry_m, width=80, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("rZ", width=30)
            ui.FloatField(model=rz_m, width=80, height=28, style=INPUT_FIELD_STYLE)

        # auto_pivot_world_center
        auto_m = ui.SimpleBoolModel(bool(step.get("auto_pivot_world_center", False)))
        auto_m.add_value_changed_fn(
            lambda _m, s=step, m=auto_m: s.__setitem__("auto_pivot_world_center", bool(m.get_value_as_bool()))
        )
        with ui.HStack(spacing=6, height=28):
            ui.CheckBox(model=auto_m, style=CHECKBOX_WHITE_STYLE)
            ui.Label("자동 월드 중심 피봇 (auto_pivot_world_center)", height=0)

        # user_axis_rotate + pivot_w*
        user_m = ui.SimpleBoolModel(bool(step.get("user_axis_rotate", False)))
        user_m.add_value_changed_fn(
            lambda _m, s=step, m=user_m: s.__setitem__("user_axis_rotate", bool(m.get_value_as_bool()))
        )
        with ui.HStack(spacing=6, height=28):
            ui.CheckBox(model=user_m, style=CHECKBOX_WHITE_STYLE)
            ui.Label("월드 피봇 회전 사용 (user_axis_rotate + pivot_w*)", height=0)

        pwx_m = ui.SimpleFloatModel(float(step.get("pivot_wx", 0.0)))
        pwy_m = ui.SimpleFloatModel(float(step.get("pivot_wy", 0.0)))
        pwz_m = ui.SimpleFloatModel(float(step.get("pivot_wz", 0.0)))
        pwx_m.add_value_changed_fn(
            lambda _m, s=step, m=pwx_m: s.__setitem__("pivot_wx", float(m.get_value_as_float()))
        )
        pwy_m.add_value_changed_fn(
            lambda _m, s=step, m=pwy_m: s.__setitem__("pivot_wy", float(m.get_value_as_float()))
        )
        pwz_m.add_value_changed_fn(
            lambda _m, s=step, m=pwz_m: s.__setitem__("pivot_wz", float(m.get_value_as_float()))
        )
        with ui.HStack(spacing=6, height=28):
            ui.Label("pivot Wx", width=70)
            ui.FloatField(model=pwx_m, width=80, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("Wy", width=30)
            ui.FloatField(model=pwy_m, width=80, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("Wz", width=30)
            ui.FloatField(model=pwz_m, width=80, height=28, style=INPUT_FIELD_STYLE)

    # ----------------------------------------------------------------- DELAY UI

    def _ui_step_delay(self, ui, step: Dict[str, Any]) -> None:
        # 호환: 과거 LAM JSON 의 "seconds" 키도 받아 들임.
        if "duration" not in step and "seconds" in step:
            step["duration"] = float(step.get("seconds", 1.0) or 1.0)
        dur_m = ui.SimpleFloatModel(float(step.get("duration", 1.0)))
        dur_m.add_value_changed_fn(
            lambda _m, s=step, m=dur_m: s.__setitem__("duration", float(m.get_value_as_float()))
        )
        with ui.HStack(spacing=6, height=28):
            ui.Label("DURATION", width=80)
            ui.FloatField(model=dur_m, width=80, height=28, style=INPUT_FIELD_STYLE)
            ui.Label("sec", height=0)

    # ----------------------------------------------------------------- common

    def _ui_step_timing(self, ui, step: Dict[str, Any]) -> None:
        rwp_m = ui.SimpleBoolModel(bool(step.get("run_with_previous", False)))
        rwp_m.add_value_changed_fn(
            lambda _m, s=step, m=rwp_m: s.__setitem__("run_with_previous", bool(m.get_value_as_bool()))
        )
        delay_m = ui.SimpleIntModel(int(step.get("step_delay_ms", 0)))
        delay_m.add_value_changed_fn(
            lambda _m, s=step, m=delay_m: s.__setitem__("step_delay_ms", int(m.get_value_as_int()))
        )
        with ui.HStack(spacing=6, height=28):
            ui.CheckBox(model=rwp_m, style=CHECKBOX_WHITE_STYLE)
            ui.Label("이전 step 과 동시 실행 (run_with_previous)", height=0, width=300)
            ui.Label("step_delay_ms", width=110)
            ui.IntField(model=delay_m, width=80, height=28, style=INPUT_FIELD_STYLE)

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
        with ui.HStack(spacing=8, height=28):
            ui.CheckBox(model=from_current_m, style=CHECKBOX_WHITE_STYLE)
            ui.Label("현재 위치부터 시작 (_start_from_current)", width=240)
            ui.Label("대상 경로", width=70)
            ui.StringField(model=paths_m, width=300, height=28, style=INPUT_FIELD_STYLE)
        with ui.HStack(spacing=6, height=24):
            ui.Button(
                "Snapshot 캡처",
                width=120,
                height=24,
                clicked_fn=lambda s=step, m=paths_m: self._capture_start_snapshot(s, m),
            )
            ui.Button(
                "Snapshot 비우기",
                width=120,
                height=24,
                clicked_fn=lambda s=step: (s.__setitem__("_start_snapshot", {}), self._set_status("snapshot 비웠음")),
            )
            cnt = len((step.get("_start_snapshot") or {}))
            ui.Label(f"snapshot 항목수: {cnt}", height=0)
        ui.Label(
            "※ LAM 의 baseline 모델 차이로 _start_from_current 자체는 거의 default 동작입니다. "
            "_start_snapshot 의 m16 만 시작 시 TBS_OFFSET 두 op 로 분해 author 되어 의미가 살아남습니다.",
            height=0,
            word_wrap=True,
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
        with ui.HStack(spacing=6, height=28):
            ui.CheckBox(model=hide_m, style=CHECKBOX_WHITE_STYLE)
            ui.Label("hide_enabled (step 시작 invisible / 종료 0.2s 후 visible 복귀)", height=0, width=380)
            ui.Label("hide_prims", width=80)
            ui.StringField(model=prims_m, width=320, height=28, style=INPUT_FIELD_STYLE)

    # ----------------------------------------------------------------- helpers

    def _fill_selected_prim(self, model) -> None:
        """omni.usd 의 현재 selection 으로 prim 텍스트박스 채움 (콤마 구분)."""
        try:
            import omni.usd as ou  # type: ignore

            ctx = ou.get_context()
            sel = ctx.get_selection() if ctx else None
            paths = list(sel.get_selected_prim_paths()) if sel else []
            if not paths:
                self._set_status("Stage 에서 prim 을 선택한 뒤 다시 누르세요.")
                return
            joined = ",".join(paths)
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

        # USD_TIMELINE step 중 ref 가 비어있는 게 있으면 첫 번째 등록 인스턴스로 자동 바인딩.
        # (= 사용자가 인스턴스 ComboBox 를 한 번도 펼쳐보지 않은 케이스 보호)
        instances = self._registry.all_instances()
        autobind_n = 0
        for i, st in enumerate(self._steps):
            if str((st or {}).get("type") or "").upper() != "USD_TIMELINE":
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
                    f"step[{i}] USD_TIMELINE 의 인스턴스가 비어있고 등록된 LAM 인스턴스도 없습니다."
                    " LAM Multi-USD Load 창에서 USD 를 추가하거나 Master 를 열고 Discover 하세요."
                )
                print(
                    f"{_PRINT_PREFIX} _run_now ABORT — step[{i}] USD_TIMELINE has empty ref and no instances",
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
                f"{_PRINT_PREFIX} _run_now: step[{i}] USD_TIMELINE auto-bound to "
                f"first instance: {inst0.instance_id} ({inst0.prim_path})",
                flush=True,
            )
        if autobind_n > 0:
            # 다음 frame 에 UI 도 같이 갱신해서 사용자가 어떤 인스턴스가 선택됐는지 볼 수 있게.
            self._schedule_refresh()

        self._runner = LamSequenceRunner(self._registry, self._scheduler)
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
        """현재 스텝에 등장하는 prim 들의 TBS_OFFSET 을 0 으로 되돌린다(재생 X).

        - 진행 중인 시퀀스가 있으면 먼저 stop 후 reset.
        - MOVE/ROTATE step 의 `prim`, USD_TIMELINE step 의 `ref.prim_path` 에서 prim 수집.
        - 실제 USD write 는 main thread 에서(_dispatch_main).
        """
        from .lam_sequence_engine import (
            _collect_prim_paths_for_reset,
            _dispatch_main,
            _reset_tbs_offset_ops_for_paths,
        )

        if self._runner is not None:
            try:
                self._runner.stop()
            except Exception:
                pass

        steps = list(self._steps)
        paths = _collect_prim_paths_for_reset(steps)
        if not paths:
            self._set_status("초기화할 prim 이 없습니다 — 시퀀스에 MOVE/ROTATE/USD_TIMELINE step 이 필요합니다.")
            return

        self._set_status(f"resetting… ({len(paths)} prim)")
        print(f"{_PRINT_PREFIX} reset requested for {len(paths)} prim(s): {paths}", flush=True)

        def _do_in_main() -> None:
            try:
                _reset_tbs_offset_ops_for_paths(paths)
                self._set_status(f"reset done ({len(paths)} prim)")
                print(f"{_PRINT_PREFIX} reset done ({len(paths)} prim)", flush=True)
            except Exception as exc:
                self._set_status(f"reset failed: {exc}")
                print(f"{_PRINT_PREFIX} reset failed: {exc}", flush=True)

        _dispatch_main(_do_in_main)

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
            if not isinstance(raw, dict):
                continue
            t = str(raw.get("type", "")).upper()
            if t not in STEP_TYPES:
                continue
            step = _default_step_for_type(t)
            for k, v in raw.items():
                step[k] = v
            if t == "DELAY" and "duration" not in raw and "seconds" in raw:
                step["duration"] = float(raw.get("seconds", 1.0) or 1.0)
            cleaned.append(step)
        self._steps = cleaned
        self._schedule_refresh()
        return len(cleaned)

    def _save_json(self) -> None:
        path_default = os.path.join(self._default_dir, "lam_sequence.json")
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


__all__ = ["LamSequenceEditor", "STEP_TYPES"]
