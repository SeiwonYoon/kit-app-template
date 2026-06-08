"""LAM 메인 창 — 다중 USD 로드 + Master Save/Open + 시퀀스 편집기 진입 + JSON 테스트 창.

UX 정책 (REQ-008, 2026-05-10 결정):
- 첫 진입에서 사용자가 헤매지 않도록 "시작 방법 두 가지" 를 가이드형으로 분리한다.
  ① 새로 시작 — USD 들을 합쳐 새 합성 USD 만들기
  ② 기존 합성 USD 열기 — 이전에 저장해 둔 합성(Master) USD 1개 열기
- "Master USD" 라는 표기를 유지하되, 파일명은 임의(예: master.usd 는 예시일 뿐)
  라는 한 줄 안내문구를 붙여 혼동을 막는다.
- 모든 경로 입력에는 omni.kit.window.filepicker 다이얼로그 버튼을 곁들인다.
- LAM Window 가 뜨면 LAM Sequence Editor 도 같이 자동으로 열린다.
- 도구 영역에 "JSON 테스트 창 열기" 진입 버튼이 있다(REQ-009).

Viewport 정책 (REQ-007, 결정 A):
- 별도 LAM viewport 를 새로 만들지 않는다. 대신 default viewport 의 source 만
  LAM master context 로 마운트한다. (lam_viewport.py 참고)
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .lam_data_paths import resolve_local_data_path
from .lam_composition_discovery import CompositionDiscovery
from .lam_external_event_runner import LamExternalEventRunner
from .lam_instance_registry import AnimationInstanceRegistry
from .lam_json_test_window import LamJsonTestWindow
from .lam_master_stage import MasterStage
from .lam_multi_usd_loader import MultiUsdLoader
from .lam_playback_scheduler import PlaybackScheduler
from .lam_runtime_evaluator import RuntimeEvaluator
from .lam_sequence_editor import LamSequenceEditor
from .simulation_play import LamSimulationCsvPlayWindow
from .lam_viewport import LamViewport
from .lam_csv_viewport_hud import (
    LAM_CSV_VIEWPORT_CONTROLS_ENABLED,
    LamCsvViewportControlsHud,
)
from .lam_viewport_status_panel import LamViewportStatusPanel
from .lam_viewport_foup_status_3d import LamFoupStatus3dPanel
from .lam_viewport_device_labels_3d import LamViewportDeviceLabels3d
from .lam_viewport_overlay_state import (
    apply_startup_checkbox_side_effects,
    get_toggle_device_labels,
    get_toggle_foup_status,
    register_toggle_listener,
    schedule_play_prim_hide_sync_after_stage_ready,
)
from .lam_wafer_viewport_labels import (
    LamWaferFoupViewportLabels,
    wafer_viewport_labels_enabled,
)


# ---------------------------------------------------------------------------
# 앱 시작 시 합성(Master) USD 자동 로드 — 필요 시 아래만 수정
# ---------------------------------------------------------------------------
# True 이면 LAM Window 첫 show() 시 default_load_usd_path 를 「② 기존 합성 USD 열기」와
# 동일하게 로드(Discover + Extract 자동). False 이면 기존과 동일(수동).
load_automatically = True

# 절대 경로 예 (Windows):
#   default_load_usd_path = r"C:\Users\ptK\Documents\kit-app-template_mine\lam\usd\master.usd"
# Nucleus URL 예:
#   default_load_usd_path = "omniverse://10.139.35.208/Users/....../Combine.usd"
# 절대 경로 예 (Linux/macOS):
#   default_load_usd_path = "/home/user/kit-app-template_mine/lam/usd/master.usd"
# 확장 data/ 기준 상대 경로 예:
#   default_load_usd_path = "usd/master.usd"
#   default_load_usd_path = "usd/LAM_v02/FBX/Combine_01.usd"
default_load_usd_path = "usd/master_1.usd"
# default_load_usd_path = "usd/combine_05.usd"

_PRINT_PREFIX = "[LAM/WIN]"

WINDOW_TITLE = "LAM Multi-USD Load"


class LamWindow:
    """다중 USD 로드 + 메인 진입 창."""

    def __init__(
        self,
        registry: AnimationInstanceRegistry,
        scheduler: PlaybackScheduler,
        evaluator: RuntimeEvaluator,
        *,
        ext_id: str = "",
    ) -> None:
        self._registry = registry
        self._scheduler = scheduler
        self._evaluator = evaluator
        self._ext_id = (ext_id or "").strip()
        self._master = MasterStage()
        self._loader = MultiUsdLoader(self._master, self._registry)
        self._discovery = CompositionDiscovery(self._master, self._registry)
        self._viewport = LamViewport(self._master.context_name)

        # Phase 2 — Evaluator 가 master stage 에 reauthor 할 수 있도록 주입.
        try:
            self._evaluator.set_master(self._master)
        except Exception:
            pass

        # 인스턴스가 사라지면 reauthor 캐시도 무효화해야 한다(Phase 2 안전망).
        self._registry.add_listener(self._invalidate_attr_caches)
        self._sequence_editor: Optional[LamSequenceEditor] = None
        self._json_test_window: Optional[LamJsonTestWindow] = None
        self._csv_sim_window: Optional[LamSimulationCsvPlayWindow] = None
        self._csv_viewport_hud: Optional[LamCsvViewportControlsHud] = None
        self._wafer_foup_labels: Optional[LamWaferFoupViewportLabels] = None
        self._status_panel: Optional[LamViewportStatusPanel] = None
        self._foup_status_3d: Optional[LamFoupStatus3dPanel] = None
        self._device_labels_3d: Optional[LamViewportDeviceLabels3d] = None
        self._overlay_toggle_listener_registered: bool = False
        self._external_runner: Optional[LamExternalEventRunner] = None
        self._window = None
        self._instances_inner = None
        self._asset_path_model = None
        self._instance_id_model = None
        self._master_path_model = None
        self._results_path_model = None
        self._sim_speed_model = None
        self._log_label = None
        self._snap_timecode_model = None  # 인스턴스 목록 헤더 — 정수 timeCode 스냅 토글
        # 다이얼로그 보유 슬롯(중복 생성 방지).
        self._fp_open_usd = None
        self._fp_open_master = None
        self._fp_save_master = None
        self._fp_open_results = None

        # 인스턴스 목록 VStack 재빌드 — 이벤트/드로우 중 ``clear()`` 방지용 (post_update 1회).
        self._inst_refresh_pending: bool = False
        self._inst_refresh_sub: Optional[Any] = None
        # 시작 자동 로드 — Kit Timeline UI 초기화와 겹치지 않도록 post_update 지연.
        self._autoload_sub: Optional[Any] = None

        self._registry.add_listener(self._schedule_instances_ui_refresh)

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
                # 시퀀스 편집기도 같이 살린다(REQ-008).
                self._open_editor()
                self._sync_csv_viewport_hud()
                self._sync_wafer_foup_viewport_labels_only()
                return
            except Exception:
                self._window = None

        # Phase 1 — master context 가 비어 있으면 즉시 새 stage 를 생성하여 첫 USD 등록이
        # 곧바로 동작하도록 한다. Viewport 는 별도로 새로 만들지 않고, 기존 default
        # viewport 의 source 만 LAM master 로 마운트한다(REQ-007 결정 A, lam_viewport.py).
        try:
            self._master.ensure_context()
            self._master.set_root_layer_edit_target()
            self._viewport.show()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} ensure_context/viewport failed: {exc}", flush=True)

        self._asset_path_model = ui.SimpleStringModel("")
        self._instance_id_model = ui.SimpleStringModel("")
        usd_dir = resolve_local_data_path("usd") or ""
        self._master_path_model = ui.SimpleStringModel(
            os.path.join(usd_dir, "master.usd") if usd_dir else "master.usd"
        )
        results_default = resolve_local_data_path(
            "lam_external_results/sample_external_result.json"
        )
        self._results_path_model = ui.SimpleStringModel(
            results_default or "lam_external_results/sample_external_result.json"
        )

        self._window = ui.Window(WINDOW_TITLE, width=860, height=720)
        with self._window.frame:
            with ui.VStack(spacing=6):
                ui.Label(
                    "LAM 컨트롤 — 여러 USD 를 합쳐 한 뷰포트에서 독립 타임라인으로 재생합니다.",
                    height=20,
                )
                ui.Label(
                    "(\"Master USD\" 라는 단어가 나오지만 파일명은 임의입니다. "
                    "예: master.usd 는 그저 예시 이름.)",
                    height=18,
                )
                ui.Label(
                    "※ LAM 은 기본(default) USD 컨텍스트를 사용합니다. 기본 Viewport·Stage 패널·Property 패널이 자동으로 LAM 의 prim 을 봅니다.",
                    height=18,
                )
                ui.Label(
                    "※ 주의: tbs_control_1 의 [USD Load] 를 누르면 기본 stage 가 새로 열려 LAM 의 author 가 사라집니다. (LAM 작업 중에는 누르지 마세요.)",
                    height=18,
                )
                ui.Separator()

                # ─── 시작 방법 ① 새로 시작 ────────────────────────────────────
                cf1 = ui.CollapsableFrame("① 새로 시작 — USD 를 추가해 합성 만들기", collapsed=False, height=0)
                with cf1:
                    with ui.VStack(spacing=4):
                        ui.Label(
                            "lam/ 폴더 안의 샘플 USD 들을 [USD 추가...] 로 하나씩 등록하세요. "
                            "여러 개 추가하면 한 뷰포트에 같이 보입니다.",
                            height=18,
                        )
                        with ui.HStack(spacing=4, height=24):
                            ui.Label("Asset path", width=90)
                            ui.StringField(model=self._asset_path_model)
                            ui.Button("...", clicked_fn=self._on_browse_usd, width=30)
                        with ui.HStack(spacing=4, height=24):
                            ui.Label("Instance ID", width=90)
                            ui.StringField(model=self._instance_id_model)
                            ui.Spacer()
                            ui.Button("+ USD 추가", clicked_fn=self._on_add_usd, width=110)
                            ui.Button(
                                "모두 초기화",
                                clicked_fn=self._on_reset_all,
                                width=110,
                            )
                        ui.Label(
                            "※ Instance ID 비워두면 파일명에서 추출. 같은 ID 충돌 시 자동 _1, _2 …",
                            height=18,
                        )

                ui.Separator()

                # ─── 시작 방법 ② 기존 합성 USD 열기 ───────────────────────────
                cf2 = ui.CollapsableFrame("② 기존 합성 USD 열기", collapsed=False, height=0)
                with cf2:
                    with ui.VStack(spacing=4):
                        ui.Label(
                            "이전에 저장해 둔 합성 USD 1 개를 열어 인스턴스를 자동으로 복원합니다.",
                            height=18,
                        )
                        with ui.HStack(spacing=4, height=24):
                            ui.Label("Master path", width=90)
                            ui.StringField(model=self._master_path_model)
                            ui.Button("...", clicked_fn=self._on_browse_open_master, width=30)
                        with ui.HStack(spacing=4, height=24):
                            ui.Spacer()
                            ui.Button("Open Master…", clicked_fn=self._on_open_master, width=120)
                            ui.Button("Save Master As…", clicked_fn=self._on_browse_save_master, width=140)
                            ui.Button("Discover", clicked_fn=self._on_discover, width=90)

                ui.Separator()

                # ─── 등록된 인스턴스 목록 ────────────────────────────────────
                cf_inst = ui.CollapsableFrame("등록된 인스턴스 (어느 시작 방법이든 여기 모임)", collapsed=False, height=0)
                with cf_inst:
                    with ui.VStack(spacing=2):
                        # 헤더 줄 — 컬럼 라벨 + 현재 합성 상태를 다른 이름으로 저장 버튼.
                        with ui.HStack(spacing=4, height=24):
                            ui.Label(
                                "prim_path / instance_id / kind / source_asset",
                                height=18,
                                width=280,
                            )
                            self._snap_timecode_model = ui.SimpleBoolModel(True)
                            try:
                                self._evaluator.set_snap_timecode_to_frame(True)
                            except Exception:
                                pass

                            def _on_snap_tc_changed(m, _self=self) -> None:
                                try:
                                    v = bool(m.get_value_as_bool())
                                except Exception:
                                    v = True
                                try:
                                    _self._evaluator.set_snap_timecode_to_frame(v)
                                except Exception as exc:
                                    _self._log(f"timeCode 스냅 설정 실패: {exc}")
                                    return
                                _self._log(f"TimeSamples 정수 timeCode 스냅: {'ON' if v else 'OFF'}")

                            try:
                                self._snap_timecode_model.add_value_changed_fn(_on_snap_tc_changed)
                            except Exception:
                                pass
                            ui.CheckBox(
                                model=self._snap_timecode_model,
                                width=22,
                                tooltip=(
                                    "ON(기본): timeCode 를 정수 프레임으로 맞춤 — 타임라인 재생에 가깝고 "
                                    "Euler 보간 튐을 줄이는 데 도움.\n"
                                    "OFF: 부동소수 timeCode(기존 동작)."
                                ),
                            )
                            ui.Label("정수 TC", width=52, tooltip="timeSamples 평가 시 정수 timeCode 스냅")
                            ui.Spacer()
                            ui.Button(
                                "Save As…",
                                width=80,
                                height=20,
                                tooltip=(
                                    "현재 등록된 모든 인스턴스를 포함한 합성 stage 를 다른 이름의 USD 파일로 저장합니다.\n"
                                    "(상단 '② 기존 합성 USD 열기' 의 [Save Master As…] 와 동일 기능)"
                                ),
                                clicked_fn=self._on_browse_save_master,
                            )
                        self._instances_frame = ui.ScrollingFrame(height=160)
                        with self._instances_frame:
                            self._instances_inner = ui.VStack(spacing=2)

                ui.Separator()

                # ─── 도구 ──────────────────────────────────────────────────
                cf_tools = ui.CollapsableFrame("도구", collapsed=False, height=0)
                with cf_tools:
                    with ui.VStack(spacing=4):
                        with ui.HStack(spacing=4, height=24):
                            ui.Button("LAM Sequence Editor 열기", clicked_fn=self._open_editor, width=200)
                            ui.Button("JSON 테스트 창 열기", clicked_fn=self._open_json_test, width=180)
                            ui.Button("CSV 시뮬 재생…", clicked_fn=self._open_csv_sim_play, width=140)
                            ui.Spacer()
                        with ui.HStack(spacing=4, height=24):
                            ui.Button("Master 진단 (등록 결과 확인)", clicked_fn=self._on_diagnose, width=210)
                            ui.Button("Option E 진단", clicked_fn=self._on_diagnose_option_e, width=130)
                            ui.Button("LAM Viewport 강제 열기", clicked_fn=self._on_force_dedicated_viewport, width=210)
                            ui.Spacer()
                        ui.Label(
                            "※ 기본은 default 컨텍스트 사용 → 기본 viewport 에 자동으로 보입니다. "
                            "[Master 진단] 으로 /World 자식 prim 확인 가능. "
                            "별도 LAM Viewport 창이 필요하면 [LAM Viewport 강제 열기].",
                            height=36,
                            word_wrap=True,
                        )

                ui.Separator()

                # ─── 외부 시뮬 결과(선택) ──────────────────────────────────
                cf_ext = ui.CollapsableFrame("외부 시뮬 결과 → 시퀀스 트리거 (선택, T1)", collapsed=True, height=0)
                with cf_ext:
                    with ui.VStack(spacing=4):
                        with ui.HStack(spacing=4, height=24):
                            ui.Label("Results path", width=90)
                            ui.StringField(model=self._results_path_model)
                            ui.Button("...", clicked_fn=self._on_browse_results, width=30)
                        with ui.HStack(spacing=4, height=24):
                            ui.Spacer()
                            ui.Button("Run External", clicked_fn=self._on_run_external, width=120)
                            ui.Button("Pause", clicked_fn=self._on_pause_external, width=70)
                            ui.Button("Resume", clicked_fn=self._on_resume_external, width=70)
                            ui.Button("Restart", clicked_fn=self._on_restart_external, width=70)
                            ui.Button("Stop External", clicked_fn=self._on_stop_external, width=120)
                        with ui.HStack(spacing=4, height=24):
                            ui.Label("Sim Speed", width=90)
                            self._sim_speed_model = ui.SimpleFloatModel(1.0)
                            ui.FloatField(model=self._sim_speed_model, width=80)
                            ui.Button("Apply Speed", clicked_fn=self._on_apply_speed, width=110)
                            ui.Spacer()
                            ui.Label("(External Runner trigger 속도 + Evaluator reauthor 속도에 동시 적용)")

                ui.Separator()
                ui.Label("Log", height=20)
                self._log_label = ui.Label("(no log yet)", height=80, word_wrap=True)

        self._schedule_instances_ui_refresh()

        # REQ-008 — LAM Window 가 뜨면 시퀀스 편집기도 같이 자동으로 연다.
        try:
            self._open_editor()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} auto open editor failed: {exc}", flush=True)

        self._schedule_autoload_master_on_startup()
        self._sync_csv_viewport_hud()
        self._sync_wafer_foup_viewport_labels_only()
        schedule_play_prim_hide_sync_after_stage_ready(delay_frames=48)

    def _sync_wafer_foup_viewport_labels_only(self, *, delay_frames: int = 12) -> None:
        """Viewport 3D 라벨 SceneView 마운트/해제 (체크 상태는 ``apply_wafer_label_visibility_from_ui``)."""
        if not wafer_viewport_labels_enabled():
            try:
                from .lam_wafer_viewport_labels import teardown_wafer_viewport_labels

                teardown_wafer_viewport_labels()
            except Exception:
                if self._wafer_foup_labels is not None:
                    try:
                        self._wafer_foup_labels.destroy()
                    except Exception:
                        pass
            self._wafer_foup_labels = None
            return
        if self._wafer_foup_labels is None:
            self._wafer_foup_labels = LamWaferFoupViewportLabels(
                viewport=self._viewport,
                master=self._master,
                ext_id=self._ext_id,
            )
        self._wafer_foup_labels.sync_layers(delay_frames=delay_frames)

    def _sync_csv_viewport_hud(self) -> None:
        """``LAM_CSV_VIEWPORT_CONTROLS_ENABLED`` 일 때만 Viewport CSV 미니 패널."""
        if not LAM_CSV_VIEWPORT_CONTROLS_ENABLED:
            if self._csv_viewport_hud is not None:
                try:
                    self._csv_viewport_hud.destroy()
                except Exception:
                    pass
                self._csv_viewport_hud = None
            return
        if self._csv_sim_window is None:
            self._csv_sim_window = LamSimulationCsvPlayWindow(
                registry=self._registry,
                scheduler=self._scheduler,
            )
            self._csv_sim_window.set_lam_window(self)
        self._csv_sim_window.ensure_playback_models()
        apply_startup_checkbox_side_effects()
        if self._csv_viewport_hud is None:
            self._csv_viewport_hud = LamCsvViewportControlsHud(
                self._csv_sim_window,
                lam_window=self,
                viewport=self._viewport,
            )
        self._csv_viewport_hud.sync_layers()
        # 상태 패널은 앱 시작 시 항상 표시(내용이 비어도 표시)
        if self._status_panel is None:
            self._status_panel = LamViewportStatusPanel(
                self._csv_sim_window,
                viewport=self._viewport,
            )
        self._status_panel.sync_layers()

        # FOUP 3D 상태 패널 (토글 ON일 때만 표시; 패널 스스로 토글을 확인)
        if self._foup_status_3d is None:
            self._foup_status_3d = LamFoupStatus3dPanel(
                self._csv_sim_window,
                viewport=self._viewport,
            )
        self._foup_status_3d.sync_layers()

        # 기기정보보기 3D 라벨 (토글 ON일 때만 표시; 라벨 스스로 토글 확인)
        if self._device_labels_3d is None:
            self._device_labels_3d = LamViewportDeviceLabels3d(viewport=self._viewport)
        self._device_labels_3d.sync_layers()

        # 토글 체크박스 OFF/ON 시 즉시 show/hide 되도록 리스너 1회 등록
        if not self._overlay_toggle_listener_registered:
            self._overlay_toggle_listener_registered = True

            def _on_overlay_toggle_changed() -> None:
                # OFF면 즉시 destroy() (남아있는 SceneView 제거가 핵심)
                try:
                    if self._foup_status_3d is not None:
                        if get_toggle_foup_status():
                            self._foup_status_3d.sync_layers(delay_frames=0)
                        else:
                            self._foup_status_3d.destroy()
                except Exception:
                    pass
                try:
                    if self._device_labels_3d is not None:
                        if get_toggle_device_labels():
                            self._device_labels_3d.sync_layers(delay_frames=0)
                        else:
                            self._device_labels_3d.destroy()
                except Exception:
                    pass

            register_toggle_listener(_on_overlay_toggle_changed)

    def _apply_overlay_toggles(self) -> None:
        """체크박스 변경 시 post_update에서 3D overlay show/hide를 강제 적용."""
        try:
            from .lam_viewport_overlay_state import (
                get_toggle_device_labels,
                get_toggle_foup_status,
            )
        except Exception:
            return
        try:
            if self._foup_status_3d is not None:
                if get_toggle_foup_status():
                    self._foup_status_3d.sync_layers(delay_frames=0)
                else:
                    self._foup_status_3d.destroy()
        except Exception:
            pass
        try:
            if self._device_labels_3d is not None:
                if get_toggle_device_labels():
                    self._device_labels_3d.sync_layers(delay_frames=0)
                else:
                    self._device_labels_3d.destroy()
        except Exception:
            pass

    def destroy(self) -> None:
        try:
            if self._sequence_editor is not None:
                self._sequence_editor.destroy()
        except Exception:
            pass
        self._sequence_editor = None
        try:
            if self._json_test_window is not None:
                self._json_test_window.destroy()
        except Exception:
            pass
        self._json_test_window = None
        try:
            if self._csv_sim_window is not None:
                self._csv_sim_window.destroy()
        except Exception:
            pass
        self._csv_sim_window = None
        try:
            if self._csv_viewport_hud is not None:
                self._csv_viewport_hud.destroy()
        except Exception:
            pass
        self._csv_viewport_hud = None
        try:
            if self._status_panel is not None:
                self._status_panel.destroy()
        except Exception:
            pass
        self._status_panel = None
        try:
            if self._foup_status_3d is not None:
                self._foup_status_3d.destroy()
        except Exception:
            pass
        self._foup_status_3d = None
        try:
            if self._device_labels_3d is not None:
                self._device_labels_3d.destroy()
        except Exception:
            pass
        self._device_labels_3d = None
        try:
            if self._wafer_foup_labels is not None:
                self._wafer_foup_labels.destroy()
        except Exception:
            pass
        self._wafer_foup_labels = None
        try:
            if self._external_runner is not None:
                self._external_runner.stop()
        except Exception:
            pass
        self._external_runner = None
        try:
            if self._viewport is not None:
                self._viewport.destroy()
        except Exception:
            pass
        # 다이얼로그 정리.
        for d in (self._fp_open_usd, self._fp_open_master, self._fp_save_master, self._fp_open_results):
            try:
                if d is not None:
                    d.hide()
            except Exception:
                pass
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass
        self._window = None
        if self._inst_refresh_sub is not None:
            try:
                self._inst_refresh_sub.unsubscribe()
            except Exception:
                pass
            self._inst_refresh_sub = None
        self._inst_refresh_pending = False
        if self._autoload_sub is not None:
            try:
                self._autoload_sub.unsubscribe()
            except Exception:
                pass
            self._autoload_sub = None

    # ----------------------------------------------------------------- actions

    def _on_add_usd(self) -> None:
        if self._asset_path_model is None:
            return
        path = (self._asset_path_model.get_value_as_string() or "").strip()
        rid = (self._instance_id_model.get_value_as_string() or "").strip() if self._instance_id_model else ""
        if not path:
            self._log("Asset path 가 비어 있습니다. (옆의 ... 버튼으로 USD 파일을 선택하세요)")
            return
        inst = self._loader.add_usd(source_asset=path, requested_id=rid)
        if inst is None:
            self._log(f"USD 등록 실패: {path}")
            return
        # 등록 직후 master stage 에 정말 prim 이 author 됐는지 즉시 검증해 사용자에게 알림.
        author_ok = self._verify_prim_in_master(inst.prim_path)
        view_status = self._viewport.status_text() if self._viewport else "viewport=N/A"
        self._log(
            f"등록 OK: {inst.instance_id} ({inst.prim_path}) | author_in_master={author_ok} | {view_status}"
        )
        if not self._viewport.is_default_visible() and not self._viewport.has_dedicated():
            self._log(
                "※ 화면에 안 보이면 도구 영역의 [LAM Viewport 강제 열기] 를 눌러 주세요."
            )

    def _on_open_master(self) -> None:
        if self._master_path_model is None:
            return
        raw = (self._master_path_model.get_value_as_string() or "").strip()
        resolved = resolve_local_data_path(raw) if raw else ""
        open_path = resolved or raw
        from .lam_usd_path import master_usd_path_is_openable

        if not master_usd_path_is_openable(open_path):
            self._log(
                "Master path 가 비어 있거나 파일/URL 을 열 수 없습니다. "
                "(로컬 .usd 또는 omniverse://…)"
            )
            return
        if open_path != raw:
            try:
                self._master_path_model.set_value(open_path)
            except Exception:
                pass
        self._open_master_at_path(open_path)

    def _open_master_at_path(self, path: str, *, log_prefix: str = "") -> bool:
        """합성 USD 열기 — Discover + Extract 자동 (Open Master / 시작 자동 로드 공통)."""
        prefix = f"{log_prefix} — " if log_prefix else ""
        path = (path or "").strip()
        if not path:
            self._log(f"{prefix}Master path 가 비어 있습니다.")
            return False
        ok = self._master.open_master(path)
        self._log(f"{prefix}Open Master {'OK' if ok else 'FAIL'}: {path}")
        if ok:
            try:
                self._master.set_root_layer_edit_target()
            except Exception:
                pass
            self._clear_registry_for_master_reload()
            added = self._discovery.discover()
            self._log(f"{prefix}Discover added={len(added)}")
            extract_prefix = log_prefix or "Open Master"
            self._auto_extract_after_master_open(log_prefix=extract_prefix)
            self._refresh_wafer_labels_after_master_open(delay_frames=24)
            self._refresh_play_prim_hide_after_master_open(delay_frames=24)
        return ok

    def _refresh_play_prim_hide_after_master_open(self, *, delay_frames: int = 24) -> None:
        """Open Master 후 「prim숨김」 체크 ON 이면 PLAY_HIDE_PRIM_SPECS 를 stage 에 맞게 숨김."""
        try:
            schedule_play_prim_hide_sync_after_stage_ready(delay_frames=delay_frames)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} play prim hide after master open schedule failed: {exc}",
                flush=True,
            )

    def _refresh_wafer_labels_after_master_open(self, *, delay_frames: int = 24) -> None:
        """Open Master 후 체크 ON 이면 트래커·SceneView 를 stage 경로에 맞게 다시 맞춘다."""
        try:
            from .lam_wafer_viewport_labels import wafer_viewport_labels_enabled
        except Exception:
            wafer_viewport_labels_enabled = lambda: False  # type: ignore

        if not wafer_viewport_labels_enabled():
            self._sync_wafer_foup_viewport_labels_only(delay_frames=delay_frames)
            return
        if self._csv_sim_window is not None:
            try:
                self._csv_sim_window.apply_wafer_label_visibility_from_ui(lam_window=self)
                return
            except Exception:
                pass
        self._sync_wafer_foup_viewport_labels_only(delay_frames=delay_frames)

    def _schedule_autoload_master_on_startup(self) -> None:
        """`load_automatically` 시 합성 로드를 몇 프레임 뒤에 실행.

        `open_master` 직후 omni.anim.window.timeline 이 selection 이벤트로
        keyframe UI 를 rebuild 하는데, 아직 ``_keyframes_container`` 가 None 이면
        AttributeError 가 난다(Kit 측 타이밍 이슈). UI·Timeline 초기화 후에 로드한다.
        """
        if not load_automatically:
            return
        if self._autoload_sub is not None:
            return

        frames_left = [3]

        def _do(_e=None):
            if frames_left[0] > 0:
                frames_left[0] -= 1
                return
            if self._autoload_sub is not None:
                try:
                    self._autoload_sub.unsubscribe()
                except Exception:
                    pass
                self._autoload_sub = None
            self._try_autoload_master_on_startup()

        try:
            import omni.kit.app as _app  # type: ignore

            stream = _app.get_app().get_post_update_event_stream()
            self._autoload_sub = stream.create_subscription_to_pop(
                _do, name="morph.lam_control.lam_window.autoload_master"
            )
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} autoload schedule failed: {exc} (즉시 시도)",
                flush=True,
            )
            self._try_autoload_master_on_startup()

    def _try_autoload_master_on_startup(self) -> None:
        """`load_automatically` 가 True 일 때 첫 show() 에서 합성 USD 를 연다."""
        if not load_automatically:
            return
        resolved = resolve_local_data_path(default_load_usd_path)
        if not resolved:
            self._log("자동 로드: default_load_usd_path 가 비어 있습니다.")
            print(f"{_PRINT_PREFIX} autoload: empty default_load_usd_path", flush=True)
            return
        if self._master_path_model is not None:
            try:
                self._master_path_model.set_value(resolved)
            except Exception:
                pass
        # if not os.path.isfile(resolved):
        #     self._log(f"자동 로드 실패 — 파일 없음:\n  {resolved}")
        #     print(
        #         f"{_PRINT_PREFIX} autoload: file not found: {resolved}",
        #         flush=True,
        #     )
        #     return
        self._log(f"자동 로드 시작: {resolved}")
        self._open_master_at_path(resolved, log_prefix="자동 로드")

    def _on_save_master(self) -> None:
        """경로 텍스트박스의 현재 경로로 저장(다이얼로그 없이)."""
        if self._master_path_model is None:
            return
        path = (self._master_path_model.get_value_as_string() or "").strip()
        if not path:
            self._log("Master path 가 비어 있습니다.")
            return
        ok = self._master.save_master(path)
        self._log(f"Save Master {'OK' if ok else 'FAIL'}: {path}")
        # 저장 후에도 author 는 계속 root layer 로 가야 한다(REQ-005 P-3).
        try:
            self._master.set_root_layer_edit_target()
        except Exception:
            pass

    def _clear_registry_for_master_reload(self) -> None:
        """합성 USD 재오픈 직전 — 이전 세션 registry / evaluator runtime 정리."""
        for inst in list(self._registry.all_instances()):
            try:
                self._evaluator.forget_instance(inst.prim_path)
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} open_master forget_instance failed "
                    f"prim={inst.prim_path}: {exc}",
                    flush=True,
                )
        self._registry.clear_all()

    def _on_discover(self) -> None:
        added = self._discovery.discover()
        self._log(f"Discover added={len(added)}")

    def _auto_extract_after_master_open(self, *, log_prefix: str = "Open Master") -> None:
        """합성 USD Open 직후 — 등록된 각 인스턴스에 Extract 를 일괄 실행."""
        instances = self._registry.all_instances()
        if not instances:
            return
        self._log(
            f"{log_prefix} — 등록 인스턴스 {len(instances)}개에 대해 Extract 자동 실행..."
        )
        for inst in instances:
            self._run_extract_for_instance(
                inst.prim_path,
                log_prefix=log_prefix,
            )

    def _on_diagnose(self) -> None:
        """master stage 의 현재 prim 들과 viewport 마운트 상태를 Log 에 dump."""
        lines = [f"viewport: {self._viewport.status_text()}"]
        try:
            stage = self._master.get_stage()
        except Exception as exc:
            self._log(" / ".join(lines + [f"stage 가져오기 실패: {exc}"]))
            return
        if stage is None:
            self._log(" / ".join(lines + ["stage=None (ensure_context 실패?)"]))
            return
        try:
            world = stage.GetPrimAtPath("/World")
            if not world or not world.IsValid():
                lines.append("/World 없음")
                self._log(" / ".join(lines))
                return
            children = list(world.GetChildren())
            lines.append(f"/World children = {len(children)}")
            for c in children[:8]:
                refs = ""
                try:
                    has_refs = c.HasAuthoredReferences()
                    refs = " [has_refs]" if has_refs else ""
                except Exception:
                    pass
                lines.append(f"  {c.GetPath()}{refs}")
            if len(children) > 8:
                lines.append(f"  ... (+{len(children) - 8})")
        except Exception as exc:
            lines.append(f"dump 실패: {exc}")
        self._log(" | ".join(lines))

    def _on_force_dedicated_viewport(self) -> None:
        """default viewport 마운트가 안 먹는 환경 폴백 — 전용 LAM Viewport 창을 강제로 띄움."""
        if self._viewport is None:
            self._log("viewport 객체가 없습니다.")
            return
        ok = self._viewport.open_dedicated()
        self._log(
            f"LAM Viewport 강제 열기 {'OK' if ok else 'FAIL'} | {self._viewport.status_text()}"
        )
        if ok:
            self._sync_csv_viewport_hud()

    def _on_diagnose_option_e(self) -> None:
        """Option E 운영 상태를 한 번에 콘솔에 dump 하고 한 줄 요약을 Log 라벨에 표시."""
        try:
            text = self._evaluator.dump_option_e_state()
        except Exception as exc:
            self._log(f"Option E 진단 실패: {exc}")
            return
        self._log(f"Option E 진단: {text}")

    def _verify_prim_in_master(self, prim_path: str) -> bool:
        """master stage 에 prim 이 author 됐는지 즉시 확인."""
        try:
            stage = self._master.get_stage()
            if stage is None:
                return False
            p = stage.GetPrimAtPath(prim_path)
            return bool(p and p.IsValid())
        except Exception:
            return False

    def _open_editor(self) -> None:
        if self._sequence_editor is None:
            seq_dir = resolve_local_data_path("lam_event_sequences") or ""
            self._sequence_editor = LamSequenceEditor(
                self._registry,
                self._scheduler,
                default_dir=seq_dir,
                evaluator=self._evaluator,
            )
        self._sequence_editor.show()

    def _open_json_test(self) -> None:
        """REQ-009 — JSON 테스트 창(시퀀스 편집기와 별개) 을 연다."""
        if self._json_test_window is None:
            seq_dir = resolve_local_data_path("lam_event_sequences") or ""
            self._json_test_window = LamJsonTestWindow(
                registry=self._registry,
                scheduler=self._scheduler,
                sequence_dir=seq_dir,
                evaluator=self._evaluator,
            )
        self._json_test_window.show()

    def _open_csv_sim_play(self) -> None:
        """``lam/csv`` CSV 선택 + Play 스켈레톤 창 (``simulation_play.py``)."""
        if self._csv_sim_window is None:
            self._csv_sim_window = LamSimulationCsvPlayWindow(
                registry=self._registry,
                scheduler=self._scheduler,
            )
            self._csv_sim_window.set_lam_window(self)
        self._csv_sim_window.show()

    def _on_run_external(self) -> None:
        if self._results_path_model is None:
            return
        path = (self._results_path_model.get_value_as_string() or "").strip()
        if not path:
            self._log("Results path 가 비어 있습니다.")
            return
        if self._external_runner is None:
            seq_dir = resolve_local_data_path("lam_event_sequences") or ""
            self._external_runner = LamExternalEventRunner(self._registry, self._scheduler, seq_dir)
        ok = self._external_runner.start(path, on_log=self._log)
        self._log(f"External {'STARTED' if ok else 'NOT_STARTED'}: {path}")

    def _on_stop_external(self) -> None:
        if self._external_runner is not None:
            self._external_runner.stop()
            self._log("External STOPPED")

    def _on_pause_external(self) -> None:
        if self._external_runner is not None:
            self._external_runner.pause()
            self._log("External PAUSED")

    def _on_resume_external(self) -> None:
        if self._external_runner is not None:
            self._external_runner.resume()
            self._log("External RESUMED")

    def _on_restart_external(self) -> None:
        if self._external_runner is not None:
            ok = self._external_runner.restart(on_log=self._log)
            self._log(f"External {'RESTARTED' if ok else 'NOT_RESTARTED'}")

    def _on_apply_speed(self) -> None:
        if self._sim_speed_model is None:
            return
        try:
            sp = float(self._sim_speed_model.get_value_as_float())
        except Exception:
            sp = 1.0
        sp = max(0.01, sp)
        # External Runner 의 trigger 속도 + Evaluator 의 reauthor 속도 모두에 동시 적용.
        try:
            self._evaluator.set_global_speed(sp)
        except Exception:
            pass
        if self._external_runner is not None:
            try:
                self._external_runner.set_speed(sp)
            except Exception:
                pass
        self._log(f"Speed applied: {sp:.2f}x (Evaluator + External Runner)")

    # -------------------------------------------------- file picker dialogs

    def _make_open_picker(self, title: str, on_pick) -> object:
        """omni.kit.window.filepicker 가 있으면 다이얼로그 생성, 없으면 None.

        on_pick(full_path: str) 를 호출.
        """
        try:
            from omni.kit.window.filepicker import FilePickerDialog  # type: ignore
        except Exception as exc:
            self._log(f"FilePickerDialog 사용 불가: {exc}")
            return None
        try:
            dlg = FilePickerDialog(
                title,
                allow_multi_selection=False,
                apply_button_label="Select",
                click_apply_handler=lambda filename, dirname: on_pick(
                    os.path.normpath(os.path.join(dirname or "", filename or ""))
                ),
            )
            dlg.hide()
            return dlg
        except Exception as exc:
            self._log(f"FilePickerDialog 생성 실패: {exc}")
            return None

    def _on_browse_usd(self) -> None:
        if self._fp_open_usd is None:
            self._fp_open_usd = self._make_open_picker(
                "USD 자산 선택 (lam/usd 또는 절대경로)",
                self._on_picked_usd,
            )
        d = self._fp_open_usd
        if d is None:
            return
        try:
            start = resolve_local_data_path("usd") or ""
            d.show(start)
        except Exception as exc:
            self._log(f"FilePicker show 실패: {exc}")

    def _on_picked_usd(self, full_path: str) -> None:
        if self._asset_path_model is None:
            return
        try:
            self._asset_path_model.set_value(full_path)
        except Exception:
            pass
        self._log(f"USD 선택됨: {full_path} (이제 [+ USD 추가] 클릭)")
        try:
            if self._fp_open_usd is not None:
                self._fp_open_usd.hide()
        except Exception:
            pass

    def _on_browse_open_master(self) -> None:
        if self._fp_open_master is None:
            self._fp_open_master = self._make_open_picker(
                "합성 USD 열기 (Master USD)",
                self._on_picked_open_master,
            )
        d = self._fp_open_master
        if d is None:
            return
        try:
            start = resolve_local_data_path("usd") or ""
            d.show(start)
        except Exception as exc:
            self._log(f"FilePicker show 실패: {exc}")

    def _on_picked_open_master(self, full_path: str) -> None:
        if self._master_path_model is None:
            return
        try:
            self._master_path_model.set_value(full_path)
        except Exception:
            pass
        try:
            if self._fp_open_master is not None:
                self._fp_open_master.hide()
        except Exception:
            pass
        # 선택 즉시 열기까지 한 번에 진행.
        self._on_open_master()

    def _on_browse_save_master(self) -> None:
        """Save 다이얼로그 — 선택한 경로로 즉시 저장."""
        if self._fp_save_master is None:
            self._fp_save_master = self._make_open_picker(
                "합성 USD 저장 (Master USD As…)",
                self._on_picked_save_master,
            )
        d = self._fp_save_master
        if d is None:
            return
        try:
            start = resolve_local_data_path("usd") or ""
            d.show(start)
        except Exception as exc:
            self._log(f"FilePicker show 실패: {exc}")

    def _on_picked_save_master(self, full_path: str) -> None:
        if self._master_path_model is None:
            return
        if not full_path:
            return
        # 확장자 없으면 .usd 자동 부여.
        if not any(full_path.lower().endswith(ext) for ext in (".usd", ".usda", ".usdc")):
            full_path = full_path + ".usd"
        try:
            self._master_path_model.set_value(full_path)
        except Exception:
            pass
        try:
            if self._fp_save_master is not None:
                self._fp_save_master.hide()
        except Exception:
            pass
        self._on_save_master()

    def _on_browse_results(self) -> None:
        if self._fp_open_results is None:
            self._fp_open_results = self._make_open_picker(
                "외부 시뮬 결과 JSON 선택",
                self._on_picked_results,
            )
        d = self._fp_open_results
        if d is None:
            return
        try:
            start = resolve_local_data_path("lam_external_results") or ""
            d.show(start)
        except Exception as exc:
            self._log(f"FilePicker show 실패: {exc}")

    def _on_picked_results(self, full_path: str) -> None:
        if self._results_path_model is None:
            return
        try:
            self._results_path_model.set_value(full_path)
        except Exception:
            pass
        try:
            if self._fp_open_results is not None:
                self._fp_open_results.hide()
        except Exception:
            pass

    # ----------------------------------------------------------------- helpers

    def _refresh_instances(self) -> None:
        try:
            import omni.ui as ui  # type: ignore
        except Exception:
            return
        if self._instances_inner is None:
            return
        # 자산 종류 helper — `_on_bake_instance` 의 분기 규칙과 동일하게 적용해
        # UI 와 동작이 어긋나지 않도록 한다.
        try:
            from .lam_asset_diagnostics import kind_to_user_label
            from .lam_types import (
                ASSET_KIND_OMNIGRAPH,
                ASSET_KIND_MIXED,
                ASSET_KIND_UNKNOWN,
                asset_kind_bake_optional,
                asset_kind_bake_unnecessary,
                asset_kind_needs_bake,
            )
        except Exception:
            # types 모듈을 못 불러오면 기존 동작(모든 인스턴스에 Bake 노출) 으로 폴백.
            kind_to_user_label = None  # type: ignore
            ASSET_KIND_OMNIGRAPH = "OMNIGRAPH"  # type: ignore
            ASSET_KIND_MIXED = "MIXED"  # type: ignore
            ASSET_KIND_UNKNOWN = "UNKNOWN"  # type: ignore

            def asset_kind_bake_optional(k):  # type: ignore
                return False

            def asset_kind_bake_unnecessary(k):  # type: ignore
                return False

            def asset_kind_needs_bake(k):  # type: ignore
                return True

        self._instances_inner.clear()
        with self._instances_inner:
            instances = self._registry.all_instances()
            if not instances:
                ui.Label("(아직 등록된 인스턴스가 없습니다 — 위에서 [+ USD 추가] 또는 [Open Master…])")
                return
            for inst in instances:
                kind = (getattr(inst, "asset_kind", "") or ASSET_KIND_UNKNOWN).strip() or ASSET_KIND_UNKNOWN
                is_baked = bool(getattr(inst, "baked", False))
                # 색상 — bake 필수=주황, optional=노랑, 불필요=초록, 알수없음=회색.
                if asset_kind_bake_unnecessary(kind):
                    kind_color = 0xFF6CCB6C  # green
                    bake_mode = "hidden"   # 버튼 자체 미노출
                elif asset_kind_bake_optional(kind):
                    kind_color = 0xFFE0B040  # yellow
                    bake_mode = "optional"
                elif asset_kind_needs_bake(kind):
                    kind_color = 0xFFE08040  # orange
                    bake_mode = "required"
                else:  # UNKNOWN / 미분류
                    kind_color = 0xFF9AA4B2  # gray
                    bake_mode = "unknown"
                # 사용자 라벨 — diagnostic helper 가 있으면 풀텍스트, 없으면 kind 자체.
                kind_label = (
                    kind_to_user_label(kind) if callable(kind_to_user_label) else kind
                )
                # Q1 — 2026-05-12: bake 완료 표시. 라벨에 BAKED 뱃지를 덧붙이고, 색상을
                # 초록으로 덮어쓴다. in-memory baked 는 Kit 재시작 시 휘발되므로 다음
                # 세션에서는 자동으로 원래 라벨/색으로 복귀한다 (D13).
                if is_baked:
                    kind_label = f"{kind_label} · BAKED ✓"
                    kind_color = 0xFF6CCB6C  # green
                with ui.HStack(spacing=4, height=22):
                    ui.Label(inst.prim_path, width=180)
                    ui.Label(inst.instance_id, width=120)
                    ui.Label(
                        kind_label,
                        width=200,
                        style={"color": kind_color},
                        tooltip=(
                            "자산 자동 분류 결과. "
                            "OMNIGRAPH/MIXED 는 [Bake] 필수, TIMESAMPLES_* 는 자산이 직접 timeSamples 를 가짐, "
                            "STATIC 은 시간 데이터 없음. add_usd 직후 scan_asset_kind 가 채움. "
                            "'BAKED ✓' 뱃지는 이번 세션에서 [Bake] 가 성공해 in-memory baked layer 가 attach 된 상태."
                        ),
                    )
                    ui.Label(inst.source_asset)
                    ui.Spacer()
                    if bake_mode == "hidden":
                        # 자리만 유지 — 다른 row 와 우측 정렬이 어긋나지 않게 같은 폭의 placeholder.
                        ui.Label("(bake 불필요)", width=90, style={"color": 0xFF6CCB6C})
                    else:
                        if is_baked:
                            btn_label = "Re-bake"
                            btn_tooltip = (
                                "이미 in-memory baked layer 가 attach 된 상태. 다시 누르면 새 layer 로 교체합니다."
                            )
                        else:
                            btn_label = (
                                "Bake" if bake_mode == "required"
                                else "Bake (선택)" if bake_mode == "optional"
                                else "Bake"  # unknown
                            )
                            btn_tooltip = {
                                "required": "OmniGraph 자산 — 멀티 인스턴스 독립 재생을 위해 in-memory 로 bake 합니다.",
                                "optional": "이 자산은 자체 timeSamples 를 가지지만, Skel/Mesh 평가 경로 검증을 위해 bake 가능.",
                                "unknown": "자산 종류 미분류 — 안전하게 사용자가 결정. [Bake] 누르면 OmniGraph 자산처럼 처리.",
                            }.get(bake_mode, "")
                        ui.Button(
                            btn_label,
                            width=90,
                            tooltip=btn_tooltip,
                            clicked_fn=(
                                lambda p=inst.prim_path: self._on_bake_instance(p)
                            ),
                        )
                    # 2026-05-13 신규 — [Extract] 버튼. fps mismatch 안전판.
                    #
                    # 사용자가 [Remove] 후 viewport 에 USD 를 drag&drop 으로 직접
                    # `/World/<인스턴스>` 하위에 넣으면 (Kit drop handler 가 timeline
                    # fps 까지 정상 sync) 본 버튼을 눌러 그 결과 트리에서 timeSamples 를
                    # 추출해 in-memory layer 로 attach. TIMESAMPLES_REPLAY 그대로 사용
                    # 가능. bake 흐름 / 기존 reference 흐름은 일절 변경하지 않는다.
                    ui.Button(
                        "Extract",
                        width=72,
                        tooltip=(
                            "현재 master 의 /World/<인스턴스> 하위 트리에서 timeSamples 를 "
                            "추출해 in-memory layer 로 attach 합니다. fps mismatch / 자산 "
                            "재배치 안전판 — drag&drop 으로 자산을 박은 직후 사용. "
                            "OmniGraph 만 있는 자산은 [Bake] 를 쓰세요."
                        ),
                        clicked_fn=lambda p=inst.prim_path: self._on_extract_instance(p),
                    )
                    # 2026-05-13 신규 — [Copy TS] 버튼. timeSamples 데이터 클립보드 복사.
                    #
                    # master 의 /World/<인스턴스> 하위 트리에서 모든 timeSamples 데이터를
                    # USDA 텍스트로 직렬화해 클립보드에 박는다. 사용자가 텍스트 에디터에
                    # 붙여넣어 FBX→USD 변환 자산의 내부 데이터를 직접 검증 가능.
                    # OmniGraph 만 있는 자산은 클립보드 복사 없이 로그에 [Bake] 필요 안내.
                    ui.Button(
                        "Copy TS",
                        width=72,
                        tooltip=(
                            "이 인스턴스의 timeSamples 데이터 전체를 USDA 텍스트로 "
                            "클립보드에 복사합니다. 텍스트 에디터에 붙여넣어 FBX→USD "
                            "내부 데이터를 검증할 때 사용. OmniGraph 자산은 복사되지 "
                            "않고 [Bake] 가 필요하다고 로그에 표시됩니다."
                        ),
                        clicked_fn=lambda p=inst.prim_path: self._on_copy_timesamples_instance(p),
                    )
                    # 2026-05-14 신규 — [Empty] 버튼. "cannot delete ancestral prim" 회피.
                    #
                    # Kit Stage panel 에서 /World/<inst>/<자식> 을 Delete 키로 지우려고 하면
                    # 자식이 reference 안에 정의된 것이라 root layer 에서 직접 제거 못함.
                    # 본 버튼은 인스턴스 prim 의 references / payloads / inst_sublayer / 자식
                    # prim spec 을 모두 비우고 빈 Xform 만 남긴다. 사용자는 그 다음 viewport
                    # 에 직접 USD 를 drag&drop 으로 박을 수 있다.
                    ui.Button(
                        "Empty",
                        width=70,
                        tooltip=(
                            "이 인스턴스 하위의 모든 reference/payload/자식 prim 을 비웁니다.\n"
                            "/World/<인스턴스> 껍데기 (빈 Xform) 만 남기므로, 그 위에 USD 를 "
                            "drag&drop 으로 다시 박을 수 있습니다.\n"
                            "Stage 패널에서 'cannot delete ancestral prim' 회피 용도. "
                            "인스턴스 등록 자체는 유지됩니다 ([Remove] 와 구분)."
                        ),
                        clicked_fn=lambda p=inst.prim_path: self._on_empty_instance(p),
                    )
                    ui.Button(
                        "Remove",
                        width=70,
                        clicked_fn=lambda p=inst.prim_path: self._on_remove(p),
                    )

    def _schedule_instances_ui_refresh(self) -> None:
        """인스턴스 목록 UI 재빌드 — 이벤트/드로우 중 ``Container.clear()`` 호출 금지.

        ``lam_sequence_editor._schedule_refresh`` 와 동일 패턴. Registry 알림 /
        [Extract] / [Bake] 완료 등에서 동기 호출되면 omni.ui 가 오류를 낸다.
        """
        if self._inst_refresh_pending:
            return
        self._inst_refresh_pending = True

        def _do(_e=None):
            self._inst_refresh_pending = False
            try:
                self._refresh_instances()
            finally:
                if self._inst_refresh_sub is not None:
                    try:
                        self._inst_refresh_sub.unsubscribe()
                    except Exception:
                        pass
                    self._inst_refresh_sub = None

        try:
            import omni.kit.app as _app  # type: ignore

            stream = _app.get_app().get_post_update_event_stream()
            self._inst_refresh_sub = stream.create_subscription_to_pop(
                _do, name="morph.lam_control.lam_window.instances_refresh"
            )
        except Exception:
            self._inst_refresh_pending = False
            self._refresh_instances()

    def _on_remove(self, prim_path: str) -> None:
        ok = self._loader.remove_usd(prim_path)
        # evaluator 의 Option E runtime 도 함께 dispose — registry listener 는 invalidate
        # 만 수행하므로, 명시 호출이 없으면 옛 offscreen_asset 캐시가 누수된다.
        try:
            self._evaluator.forget_instance(prim_path)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} remove forget_instance failed prim={prim_path}: {exc}",
                flush=True,
            )
        self._log(f"Remove {'OK' if ok else 'FAIL'}: {prim_path}")

    def _on_empty_instance(self, prim_path: str) -> None:
        """**[Empty] 신규 path (2026-05-14)** — 인스턴스 prim 자체는 유지하고 하위만 비움.

        흐름:
            1) 진행 중 평가 / replay mode 해제 — ``end_replay_mode`` 호출.
            2) ``MultiUsdLoader.clear_instance_contents`` 로 references / payloads /
               inst_sublayer / 자식 prim spec 을 모두 청소.
            3) ``RuntimeEvaluator.forget_instance`` 로 offscreen stage 도 폐기.
            4) UI 행 갱신 (deferred) — 라벨이 UNKNOWN / Bake 미정 으로 다시 표시.

        본 핸들러는 **기존 [Bake] / [Extract] / [Remove] 흐름과 독립** 한 신규 path.
        Registry 는 그대로 유지하므로 같은 prim_path 에 사용자가 viewport drag&drop
        으로 자산을 다시 박은 뒤 [Extract] / [Bake] 로 이어 작업할 수 있다.

        Args:
            prim_path: 인스턴스 prim_path (예: ``/World/aaa``).
        """
        try:
            inst = self._registry.get_by_prim_path(prim_path)
        except Exception:
            inst = None
        if inst is None:
            for it in self._registry.all_instances():
                if it.prim_path == prim_path:
                    inst = it
                    break
        if inst is None:
            self._log(f"Empty 실패 — 인스턴스를 찾을 수 없음: {prim_path}")
            return

        self._log(f"Empty 시작 prim={prim_path} (하위 reference/자식 모두 비우는 중...)")

        # (a) replay 잔재 청소 — evaluator 가 author 해 둔 default opinion / OmniGraph
        #     deactivate 표식 등을 먼저 비워야 sublayer clear 가 깔끔해진다.
        try:
            self._evaluator.end_replay_mode(prim_path)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} empty pre-cleanup end_replay_mode failed "
                f"prim={prim_path}: {exc}",
                flush=True,
            )

        # (b) loader 가 핵심 청소 — references / payloads / nameChildren / inst_sublayer.
        try:
            diag = self._loader.clear_instance_contents(prim_path)
        except Exception as exc:
            self._log(f"Empty 예외 prim={prim_path}: {exc}")
            return

        if not diag.get("ok", False):
            self._log(
                f"Empty 실패 prim={prim_path}: {diag.get('error') or '알 수 없음'}"
            )
            return

        # (c) evaluator 측 offscreen stage / attr cache 폐기 — 다음 [Bake]/[Extract] 까지
        #     평가 대상에서 빠진다. forget_instance 는 registry 의 인스턴스를 건드리지
        #     않는다 (offscreen runtime 만 dispose).
        try:
            self._evaluator.forget_instance(prim_path)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} empty forget_instance failed prim={prim_path}: {exc}",
                flush=True,
            )

        self._log(
            f"Empty 완료 prim={prim_path}\n"
            f"   cleared refs={diag.get('cleared_refs', 0)} "
            f"payloads={diag.get('cleared_payloads', 0)} "
            f"other={diag.get('cleared_other', 0)} "
            f"children_spec={diag.get('removed_children_spec', 0)} "
            f"inst_sublayer_removed={diag.get('removed_inst_sublayer', False)}\n"
            f"   → 빈 Xform({prim_path}) 만 남았습니다. 다음 워크플로 참고:\n"
            f"      · timeSamples 자산 (FBX→USD 등): viewport 에 drag&drop → [Extract] → "
            f"TIMESAMPLES_REPLAY step 으로 재생.\n"
            f"      · OmniGraph 자산: 본 인스턴스 [Remove] 후 상단 [USD 추가] 정상 등록 권장 "
            f"(drag&drop + [Bake] 는 sub_path 매칭이 어긋날 수 있음)."
        )

        # (d) UI 행 갱신 — deferred (omni.ui Container.clear 회귀 방지).
        try:
            self._schedule_instances_ui_refresh()
        except Exception as _ref_exc:
            print(
                f"{_PRINT_PREFIX} empty 후 _schedule_instances_ui_refresh 실패: {_ref_exc}",
                flush=True,
            )

    def _on_reset_all(self) -> None:
        """등록된 모든 LAM 인스턴스를 unload하고 master stage 의 /World 자식 prim 도 비운다.

        - `MultiUsdLoader.remove_usd` 가 prim 제거 + sublayer cleanup + registry unregister.
        - evaluator 의 Option E runtime 들은 `forget_instance` 가 dispose 까지 호출.
        - 사용자 USD 자산 파일과 master 저장 파일은 일절 건드리지 않는다.
        """
        instances = list(self._registry.all_instances())
        if not instances:
            self._log("초기화할 LAM 인스턴스가 없습니다.")
            return
        fail = 0
        for inst in instances:
            try:
                if not self._loader.remove_usd(inst.prim_path):
                    fail += 1
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} reset remove_usd failed prim={inst.prim_path}: {exc}",
                    flush=True,
                )
                fail += 1
            # evaluator runtime 도 청소 (offscreen_asset 캐시 누수 방지).
            try:
                self._evaluator.forget_instance(inst.prim_path)
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} reset forget_instance failed prim={inst.prim_path}: {exc}",
                    flush=True,
                )
        # 안전 보강: stage 에 남아 있을 /World 자식 prim 정리 (혹시 모를 잔재 제거).
        try:
            stage = self._master.get_stage()
            if stage is not None:
                world = stage.GetPrimAtPath("/World")
                if world and world.IsValid():
                    for child in list(world.GetChildren()):
                        try:
                            stage.RemovePrim(child.GetPath())
                        except Exception as exc:
                            print(
                                f"{_PRINT_PREFIX} reset stage RemovePrim failed "
                                f"path={child.GetPath()}: {exc}",
                                flush=True,
                            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} reset stage scan failed: {exc}", flush=True)
        self._log(f"모두 초기화 OK: removed={len(instances) - fail}, fail={fail}")

    def _on_bake_instance(self, prim_path: str) -> None:
        """선택한 인스턴스의 자산을 OmniGraph 평가 결과로 timeSamples bake (in-memory).

        흐름 (X3 정책 — 2026-05-12):
            1) 인스턴스의 절대 자산 경로 해석 (registry.source_asset).
            2) `lam_bake_omnigraph.bake_prim_to_timesamples_async(output_mode='memory')` 를
               별도 태스크로 시작. 디스크에 `*_baked.usd` 를 생성하지 않는다.
            3) 진행률 콜백 → log 라벨 갱신.
            4) 완료 시:
                - 성공: ``RuntimeEvaluator.attach_memory_baked_layer`` 로 동일 prim_path 의
                  runtime 의 offscreen Stage 를 anonymous baked layer 로 재구성. 인스턴스
                  교체 (remove_usd / add_usd) 는 하지 않는다. 사용자 viewport 의 master
                  reference 는 원본 자산 그대로 유지.
                - 실패: 에러 로그.

        본 호출은 **Kit default context 의 OmniGraph 평가를 사용해 capture** 하지만,
        결과는 메모리 layer 에만 저장되어 master stage 는 직접 건드리지 않는다.

        Args:
            prim_path: 인스턴스 prim_path (예: `/World/aaa`).
        """
        try:
            inst = self._registry.get_by_prim_path(prim_path)
        except Exception:
            inst = None
        if inst is None:
            for it in self._registry.all_instances():
                if it.prim_path == prim_path:
                    inst = it
                    break
        if inst is None:
            self._log(f"Bake 실패 — 인스턴스를 찾을 수 없음: {prim_path}")
            return

        # W2 — 자산 종류별 조건부 분기 (사용자 요구 2026-05-11 후반):
        # add_usd 직후 `scan_asset_kind` 가 채워둔 `inst.asset_kind` 를 보고 결정.
        # - TIMESAMPLES_XFORM / STATIC: bake 가 의미 없으므로 명시 안내 후 종료.
        # - TIMESAMPLES_SKEL / TIMESAMPLES_MESH: bake 진행 (W3 의 자동 탐지가 SkelAnim/
        #   Mesh-deform attribute 도 capture). 단 시각 결과 호환성은 별도 검증 필요.
        # - OMNIGRAPH / MIXED: bake 필수 — 기존 흐름 그대로.
        # - UNKNOWN: 진단 실패. 기존 흐름으로 best-effort.
        from .lam_asset_diagnostics import kind_to_user_label
        from .lam_types import (
            asset_kind_bake_optional,
            asset_kind_bake_unnecessary,
        )

        kind = (getattr(inst, "asset_kind", "") or "").strip()
        if asset_kind_bake_unnecessary(kind):
            self._log(
                f"Bake 생략 — kind={kind_to_user_label(kind)}\n"
                f"   이 자산은 이미 평가 가능한 timeSamples 를 가지고 있어 Bake 가 필요 없습니다.\n"
                f"   시퀀스 편집기에서 TIMESAMPLES_REPLAY step (인스턴스 독립 평가) 으로 바로 재생하세요.\n"
                f"   (USD_TIMELINE step 도 현 단계에서는 동일 동작이지만 추후 TBS 방식으로 재구현될 예정.)\n"
                f"   prim={prim_path} src={inst.source_asset}"
            )
            return
        if asset_kind_bake_optional(kind):
            self._log(
                f"Bake 진행 (호환 검증 필요) — kind={kind_to_user_label(kind)}\n"
                f"   주의: SkelAnim / Mesh-deform 자산은 in-memory baked layer 가 만들어져도 master "
                f"mirror 평가 경로 호환성을 별도 검증해야 합니다."
            )
        else:
            self._log(f"Bake 시작 — kind={kind_to_user_label(kind) or 'UNKNOWN'}")

        # source_asset 절대 경로 해석.
        raw = (getattr(inst, "source_asset", "") or "").strip()

        # 2026-05-14 — [Empty] + drag&drop 워크플로 보강.
        # 사용자가 [Empty] 로 인스턴스 내용을 비운 뒤 viewport 에 USD 를 drag&drop 한
        # 경우 `inst.source_asset` 는 빈 문자열인 상태가 된다. 이때 master 트리를 자동
        # 탐색해 drag&drop 으로 박힌 자산 경로를 회수한다.
        # 또한 drag&drop 결과는 `/World/aaa/test2/<asset>/...` 식으로 자식 prim 안에
        # reference 가 박히는 경우가 많아 bake 의 sub_path 매칭이 어긋날 수 있으므로,
        # OmniGraph 자산은 [USD 추가] 정상 등록 경로를 권장하는 안내를 함께 띄운다.
        if not raw:
            try:
                from .lam_extract_from_master import _discover_asset_path_from_master

                master_stage = self._master.get_stage() if self._master is not None else None
                discovered = ""
                if master_stage is not None:
                    discovered = _discover_asset_path_from_master(master_stage, prim_path) or ""
            except Exception as _disc_exc:
                discovered = ""
                print(
                    f"{_PRINT_PREFIX} bake auto-discover failed prim={prim_path}: {_disc_exc}",
                    flush=True,
                )

            if discovered:
                try:
                    inst.source_asset = discovered
                except Exception:
                    pass
                raw = discovered
                self._log(
                    f"Bake auto-discover: master 트리에서 자산 경로 회수 → {discovered}\n"
                    f"   주의: drag&drop 으로 박힌 자산은 자식 prim 안에 reference 가 있어 "
                    f"bake 의 sub_path 매칭에 실패할 수 있습니다.\n"
                    f"   OmniGraph 자산이라면 [Remove] 후 상단 [USD 추가] 로 다시 등록하는 "
                    f"것을 권장합니다."
                )
            else:
                self._log(
                    f"Bake 실패 — source_asset 비어 있음 prim={prim_path}\n"
                    f"   [Empty] 후라면 viewport 에 USD 를 drag&drop 으로 먼저 박아주세요. "
                    f"OmniGraph 자산은 [USD 추가] 정상 등록 권장."
                )
                return
        # 2026-05-14 — drag&drop 으로 박힌 reference 는 종종 `file:/C:/...` URI 로
        # 들어와 `os.path.isfile` 검사가 실패한다 (사용자 실보고 회귀). URI prefix /
        # URL-encode 를 일반 OS 경로로 정규화.
        from .lam_extract_from_master import normalize_asset_uri_to_path

        raw_norm = normalize_asset_uri_to_path(raw)
        abs_path = ""
        if raw_norm and os.path.isfile(raw_norm):
            abs_path = os.path.normpath(os.path.abspath(raw_norm))
        else:
            mp = (getattr(self._master, "master_path", "") or "").strip()
            if mp and not getattr(self._master, "is_anonymous", True):
                cand = os.path.normpath(
                    os.path.join(os.path.dirname(os.path.abspath(mp)), raw_norm)
                )
                if os.path.isfile(cand):
                    abs_path = cand
        if not abs_path:
            self._log(
                f"Bake 실패 — 자산 경로 해석 실패: raw={raw!r} normalized={raw_norm!r}\n"
                f"   drag&drop 한 자산이 omniverse:// 또는 외부 URL 인 경우 [USD 추가] "
                f"로 정상 등록을 권장합니다 (로컬 파일 경로만 [Bake] 가 지원)."
            )
            return

        from .lam_bake_omnigraph import (
            bake_prim_to_timesamples_async,
            read_bake_speed_env,
        )

        # X3 정책 (2026-05-12) — bake 는 in-memory anonymous Sdf.Layer 만 생성한다.
        # 따라서 "이미 *_baked.usd 라서 또 bake 할 필요 없음" 같은 분기는 불필요. 사용자가
        # [Bake] 를 다시 누르면 메모리 layer 를 다시 만들어 runtime 에 재주입하면 된다.

        bake_stride, bake_sparse = read_bake_speed_env()
        self._log(
            f"Bake 속도: frame_stride={bake_stride} sparse_time_samples={bake_sparse} "
            f"(기본 무손실. PowerShell 으로 가속 시 — 권장X: "
            f"$env:LAM_BAKE_FRAME_STRIDE=2)"
        )
        self._log(
            "Bake 출력 모드 = in-memory (휘발성) — *_baked.usd 파일을 생성하지 않습니다. "
            "Kit 종료 시 결과는 사라지며, 다시 사용하려면 [Bake] 를 다시 누르세요."
        )

        master_stage = None
        try:
            master_stage = self._master.get_stage()
        except Exception:
            master_stage = None
        if master_stage is None:
            self._log("Bake 실패 — master stage 가 None")
            return

        # 진행률 콜백 — main thread 의 log 라벨 안전 갱신.
        def _progress(cur: int, total: int, msg: str) -> None:
            try:
                pct = 0 if total <= 0 else int(round(cur / total * 100))
                self._log(f"Bake 진행 {pct:3d}% ({cur}/{total}) {msg}")
            except Exception:
                pass

        # bake 종료 후 후처리 — runtime 의 offscreen_stage 를 baked layer 로 교체.
        async def _runner() -> None:
            self._log(f"Bake 시작 (in-memory): src={abs_path}")

            # 2026-05-14 — bake 직전에 이전 TIMESAMPLES_REPLAY 의 흔적을 청소.
            # Extract / 이전 Bake 가 inst sublayer 의 drag&drop 자식 prim 들에 default
            # 와 over spec 을 박아두면, 본 Bake 가 그 위에 다시 author 하면서 master
            # 트리에 "내부 자산이 또 한 단계 복제된 듯한" 모습이 남는다. `end_replay_mode`
            # 가 inst sublayer 를 재귀적으로 비워 master 가 깨끗한 상태에서 capture 가
            # 시작되도록 한다 (in-memory 라 사용자 USD 파일은 변경 없음).
            try:
                self._evaluator.end_replay_mode(prim_path)
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} bake pre-cleanup end_replay_mode failed "
                    f"prim={prim_path}: {exc}",
                    flush=True,
                )

            # **회귀 fix (2026-05-12)** — TIMESAMPLES_REPLAY 모드용으로 evaluator 가
            # 매 update tick 에서 OmniGraph deactivate / LayerOffset freeze 를 다시
            # author 한다. bake 가 `await app.next_update_async()` 로 main update tick
            # 으로 넘어가는 사이 그 author 가 들어가서 master scrub 이 평가 못 함.
            # → `begin_bake_mode` 가 표식을 박아 evaluator 의 모든 자동 author 를
            # 보류하고 OmniGraph 를 일시 활성, LayerOffset 을 (0,1) 로 복귀시킨다.
            # bake 완료 후 `end_bake_mode` 가 표식 제거 + 다음 update tick 에서 자연
            # 복귀.
            try:
                ok_bake_mode = bool(self._evaluator.begin_bake_mode(prim_path))
                self._log(
                    f"begin_bake_mode prim={prim_path} ok={ok_bake_mode} "
                    f"(bake 진행 중 evaluator 자동 author 보류)"
                )
            except Exception as exc:
                self._log(f"begin_bake_mode 실패(무시 가능): {exc}")

            def _end_bake_mode_safe() -> None:
                try:
                    self._evaluator.end_bake_mode(prim_path)
                except Exception:
                    pass

            try:
                result = await bake_prim_to_timesamples_async(
                    master_stage,
                    prim_path,
                    abs_path,
                    output_mode="memory",
                    fps=30.0,
                    frame_stride=bake_stride,
                    sparse_time_samples=bake_sparse,
                    progress_cb=_progress,
                    log_baked_dump=True,
                )
            except Exception as exc:
                self._log(f"Bake 예외: {exc}")
                _end_bake_mode_safe()
                return

            if not result.ok:
                self._log(f"Bake 실패: {result.error}")
                _end_bake_mode_safe()
                return

            # 모듈 hot-reload 안전 — Kit 의 omni.ext 가 lam_bake_omnigraph.py 의 함수만
            # 새로 가져오고 BakeResult 클래스는 옛 객체를 참조하는 잘 알려진 함정에서
            # 새 필드 (`baked_layer` / `output_mode`) 가 인스턴스에 없을 수 있다. 따라서
            # 직접 attr 접근 대신 getattr 로 안전 추출.
            out_mode = getattr(result, "output_mode", None)
            baked_layer = getattr(result, "baked_layer", None)

            self._log(
                f"Bake 완료: mode={out_mode or '(unknown)'} "
                f"sample_frames={result.n_frames} stride={result.frame_stride} "
                f"sparse_cap_skip={result.n_sparse_skipped_capture} "
                f"prims={result.n_target_prims} attrs={result.n_attr_authored} "
                f"static_pruned={result.n_attr_pruned_static} "
                f"elapsed={result.elapsed_sec:.2f}s — runtime 의 offscreen stage 에 적용 중..."
            )

            if baked_layer is None:
                # 두 가지 가능성:
                #  (1) Kit hot-reload 함정 — 새 BakeResult 정의가 적용 안 됨.
                #  (2) 호출자가 의도적으로 output_mode='file' 을 강제 (현 UI 경로에서는 X).
                # 어느 쪽이든 사용자에게 명확히 안내 후 종료. attach 단계는 건너뛴다.
                hint_lines = [
                    "Bake 결과에 in-memory baked layer 가 없습니다.",
                    "   가장 가능성 높은 원인 — Kit 의 hot-reload 가 lam_bake_omnigraph "
                    "의 BakeResult 클래스 새 정의를 못 가져왔습니다.",
                    "   해결: Kit 을 완전히 재시작한 뒤 [Bake] 를 다시 눌러주세요.",
                ]
                # 옛 함수가 file 모드로 동작했다면 result.output_path 에 디스크 경로가 들어있을
                # 수 있다. 그 경우 사용자에게 안내만 한다 (자동 import 는 위험).
                old_out = getattr(result, "output_path", "")
                if isinstance(old_out, str) and old_out and os.path.isfile(old_out):
                    hint_lines.append(
                        f"   참고: hot-reload 전 옛 함수가 디스크에 baked.usd 를 만들었을 "
                        f"수 있습니다 → {old_out} (수동 정리 권장)"
                    )
                self._log("\n".join(hint_lines))
                _end_bake_mode_safe()
                return

            # 인스턴스 교체 없음 — same prim_path 의 runtime 에 in-memory baked layer 를
            # 주입해 offscreen_stage 를 재구성한다. registry / master.usd 의 reference 는
            # 원본 자산 그대로 유지된다 (master 측은 Option E freeze 로 평가 안 함).
            #
            # 2026-05-14 — drag&drop 자동 인식 (effective_inst_prim_path) 결과는 baked
            # layer 의 author path 와 매핑 시에만 사용. attach 자체는 항상 사용자가 보는
            # `inst.prim_path` (/World/aaa) 로 호출해 evaluator 의 runtime key 와 시퀀스
            # 엔진 / TIMESAMPLES_REPLAY 호출자가 일관되게 prim_path 를 쓸 수 있도록 한다.
            inst_prim = inst.prim_path  # noqa: B023 (closure intended)
            eff = getattr(result, "effective_inst_prim_path", "") or inst_prim
            if eff != inst_prim:
                self._log(
                    f"Bake drag&drop prefix 인식됨: {eff} "
                    f"(baked layer 의 author path 는 자산 root 기준으로 정규화. mirror "
                    f"write 는 사용자 prim={inst_prim} 산하에서 이름 매칭으로 진행)"
                )
            try:
                ok = self._evaluator.attach_memory_baked_layer(
                    inst_prim,
                    baked_layer,
                    source_asset_for_log=abs_path,
                    mirror_asset_path_hint=abs_path,
                )
            except Exception as exc:
                self._log(f"Bake 후 attach_memory_baked_layer 예외: {exc}")
                _end_bake_mode_safe()
                return

            if not ok:
                self._log(
                    "Bake 결과 적용 실패 — runtime 이 없거나 stage open 실패. "
                    "(인스턴스가 사라졌거나 LAM 모듈 미초기화일 수 있음)"
                )
                _end_bake_mode_safe()
                return

            # bake 모드 종료 — 다음 update tick 에서 evaluator 가 TIMESAMPLES_REPLAY 모드
            # (LayerOffset freeze + OmniGraph deactivate) 를 자동으로 다시 author 한다.
            _end_bake_mode_safe()

            self._log(
                f"Bake → 적용 완료 prim={inst_prim} "
                f"src={abs_path} (in-memory baked layer 사용. Option E 로 독립 재생 가능)"
            )
            # Q1 — 2026-05-12: attach_memory_baked_layer 가 inst.baked=True 를 박았으니
            # 인스턴스 목록을 새로 그려 [BAKED ✓ / Re-bake] 표시로 즉시 전환한다.
            try:
                self._schedule_instances_ui_refresh()
            except Exception as _ref_exc:
                print(
                    f"{_PRINT_PREFIX} bake 후 _schedule_instances_ui_refresh 실패: {_ref_exc}",
                    flush=True,
                )

        # asyncio task 로 실행. Kit 의 main loop 가 await 를 처리하도록 ensure_future.
        try:
            import asyncio  # noqa: F401  (이미 import 되어 있을 수 있으나 안전)

            asyncio.ensure_future(_runner())
        except Exception as exc:
            self._log(f"Bake task 스케줄 실패: {exc}")

    def _on_extract_instance(self, prim_path: str) -> None:
        """**[Extract] 신규 path (2026-05-13)** — master `/World/<인스턴스>` 하위 트리의
        timeSamples 를 anonymous layer 로 추출해 인스턴스 runtime 에 attach.

        사용자 워크플로(실무 fps mismatch 안전판):
            1. (선택) 인스턴스 행의 [Remove] 로 기존 reference 만 제거. — 또는 Stage
               panel 에서 자식 prim 만 수동 삭제.
            2. viewport 에 USD 파일을 **직접 drag&drop** 해서 ``/World/<인스턴스>``
               하위에 박는다. Kit drop handler 가 timeline fps 등을 자동 sync 시킨다.
            3. 본 버튼을 누른다. `RuntimeEvaluator.extract_and_attach_from_master` 가
               호출되어 결과 layer 가 같은 인스턴스 runtime 의 offscreen stage 로 attach
               된다.
            4. 인스턴스 행이 `BAKED ✓` 로 갱신되고 (= attach 성공 표식) TIMESAMPLES_REPLAY
               step 으로 즉시 재생 가능.

        본 핸들러는 **기존 bake 핸들러(`_on_bake_instance`) / reference 흐름 / 시퀀스
        엔진** 을 일절 건드리지 않는 신규 path 다.

        Args:
            prim_path: 인스턴스 prim_path (예: `/World/aaa`).
        """
        self._run_extract_for_instance(prim_path, log_prefix="")

    def _run_extract_for_instance(self, prim_path: str, *, log_prefix: str = "") -> None:
        """Extract 공통 구현 — [Extract] 버튼 및 Open Master 자동 Extract 가 공유."""
        prefix = f"{log_prefix} — " if log_prefix else ""

        try:
            inst = self._registry.get_by_prim_path(prim_path)
        except Exception:
            inst = None
        if inst is None:
            for it in self._registry.all_instances():
                if it.prim_path == prim_path:
                    inst = it
                    break
        if inst is None:
            self._log(f"{prefix}Extract 실패 — 인스턴스를 찾을 수 없음: {prim_path}")
            return

        # 추출 전 잔재 청소 — 이전 TIMESAMPLES_REPLAY step 이 inst sublayer 에 박아둔
        # default opinion 이 남아 있으면 `stage.Flatten()` 결과에 그 default 가 inline
        # 으로 따라 들어가 추출된 layer 가 정적인 자세만 갖게 된다. Reset 효과만큼
        # 강하게는 아니고, evaluator 측 default 만 비운다.
        try:
            self._evaluator.end_replay_mode(prim_path)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} extract pre-cleanup end_replay_mode failed "
                f"prim={prim_path}: {exc}",
                flush=True,
            )

        self._log(
            f"{prefix}Extract 시작 prim={prim_path} "
            f"(in-memory layer 생성 + 자산 종류 검증 중...)"
        )

        try:
            result = self._evaluator.extract_and_attach_from_master(
                prim_path,
                source_asset_for_log=f"<extracted:{inst.instance_id}>",
            )
        except Exception as exc:
            self._log(f"{prefix}Extract 예외 prim={prim_path}: {exc}")
            return

        if result is None:
            self._log(f"{prefix}Extract 실패 prim={prim_path}: 결과 없음")
            return

        # kind label / bake mode helper — 안내 메시지에 사람이 읽을 수 있는 라벨 사용.
        from .lam_asset_diagnostics import kind_to_user_label
        from .lam_types import (
            ASSET_KIND_OMNIGRAPH,
            ASSET_KIND_STATIC,
        )

        kind = (getattr(result, "kind", "") or "").strip() or "UNKNOWN"
        kind_label = kind_to_user_label(kind) or kind

        # 결과 분기:
        #   - ok=True  : timeSamples 추출 + attach 성공. inst.asset_kind 를 추출 결과로
        #                갱신 → 행 라벨이 정확히 표시되고 [Bake] 가 hidden (불필요).
        #   - kind=OMNIGRAPH : timeSamples 없음. inst.baked=False / inst.asset_kind=OMNIGRAPH
        #                갱신 → 행에 [Bake] 버튼이 다시 표시되도록 한다.
        #   - kind=STATIC : 안내만, 상태 표시는 STATIC.
        #   - 그 외   : 안내만.

        # 공통: 추정된 자산 경로가 있으면 inst.source_asset 도 갱신해 [Bake] 가 올바른
        # 자산 path 를 사용하도록 한다. URI prefix (`file:/...`) 가 남아 있을 가능성을
        # 안전망으로 한 번 더 정규화한다.
        from .lam_extract_from_master import normalize_asset_uri_to_path

        discovered_raw = (getattr(result, "discovered_asset_path", "") or "").strip()
        discovered = normalize_asset_uri_to_path(discovered_raw)
        if discovered:
            try:
                inst.source_asset = discovered
                self._log(
                    f"   ↳ drag&drop 자산 경로 탐지: {discovered} → inst.source_asset 갱신"
                )
            except Exception as _src_exc:
                print(
                    f"{_PRINT_PREFIX} extract: source_asset 갱신 실패 prim={prim_path} "
                    f"exc={_src_exc}",
                    flush=True,
                )

        if not result.ok:
            # 추출 실패라도 자산 종류는 명확히 파악되었을 수 있다 — 상태 표시 정확화.
            try:
                inst.asset_kind = kind
                inst.baked = False
            except Exception as _kx_exc:
                print(
                    f"{_PRINT_PREFIX} extract: asset_kind 갱신 실패 prim={prim_path} "
                    f"exc={_kx_exc}",
                    flush=True,
                )

            if kind == ASSET_KIND_OMNIGRAPH:
                self._log(
                    f"{prefix}Extract: 이 자산은 **OmniGraph** 입니다 (kind={kind_label}).\n"
                    f"   timeSamples 가 없어 그대로 사용할 수 없습니다.\n"
                    f"   → 인스턴스 행에 다시 표시된 **[Bake]** 버튼을 눌러 in-memory "
                    f"timeSamples 로 변환하세요.\n"
                    f"   stats: {result.to_log_line()}"
                )
            elif kind == ASSET_KIND_STATIC:
                self._log(
                    f"{prefix}Extract: 이 자산은 **STATIC** 입니다 (시간 데이터 없음).\n"
                    f"   재생 대상이 아닙니다 (TIMESAMPLES_REPLAY / Bake 모두 의미 없음).\n"
                    f"   stats: {result.to_log_line()}"
                )
            else:
                self._log(
                    f"{prefix}Extract 실패 prim={prim_path}: {result.error or '알 수 없음'} "
                    f"(kind={kind_label})\n"
                    f"   stats: {result.to_log_line()}"
                )

            # UI 행 갱신 — bake 버튼 분기가 새 kind 에 맞춰 다시 그려진다.
            try:
                self._schedule_instances_ui_refresh()
            except Exception as _ref_exc:
                print(
                    f"{_PRINT_PREFIX} extract 후 _schedule_instances_ui_refresh 실패: {_ref_exc}",
                    flush=True,
                )
            return

        # 성공 — attach_memory_baked_layer 가 inst.baked=True 박았고, 우리는 kind 도
        # 추출 결과로 정확히 갱신해서 다음 [Bake] 버튼 분기가 'hidden' (불필요) 으로
        # 표시되게 한다 (TIMESAMPLES_XFORM/SKEL/MESH 는 bake unnecessary 분류).
        try:
            inst.asset_kind = kind
        except Exception as _kx_exc:
            print(
                f"{_PRINT_PREFIX} extract: asset_kind 갱신 실패(성공 분기) prim={prim_path} "
                f"exc={_kx_exc}",
                flush=True,
            )

        self._log(
            f"{prefix}Extract 완료 prim={prim_path} kind={kind_label}\n"
            f"   prims={result.n_prims} attrs={result.n_attrs_total} "
            f"timeSamples_attrs={result.n_attrs_with_timesamples} "
            f"(xform={result.n_xform_op_ts} skel={result.n_skel_anim_ts} "
            f"mesh={result.n_mesh_points_ts} vis={result.n_visibility_ts}) "
            f"tc=[{result.tc_min:.3f},{result.tc_max:.3f}] "
            f"elapsed={result.elapsed_sec:.3f}s\n"
            f"   → in-memory layer attach 완료. TIMESAMPLES_REPLAY step 에서 바로 "
            f"재생 가능합니다."
        )

        # 인스턴스 행 라벨/표시 갱신 — attach_memory_baked_layer 가 `inst.baked=True` 박음.
        try:
            self._schedule_instances_ui_refresh()
        except Exception as _ref_exc:
            print(
                f"{_PRINT_PREFIX} extract 후 _schedule_instances_ui_refresh 실패: {_ref_exc}",
                flush=True,
            )

    def _on_copy_timesamples_instance(self, prim_path: str) -> None:
        """**[Copy TS] 신규 path (2026-05-13)** — master `/World/<인스턴스>` 트리의
        모든 ``timeSamples`` 데이터를 USDA 텍스트로 직렬화해 클립보드에 복사.

        사용자 워크플로:
            1. 인스턴스 행의 [Copy TS] 버튼 클릭.
            2. `RuntimeEvaluator.dump_master_timesamples_usda` 가 호출되어 master 트리
               하위의 ``timeSamples`` 데이터를 anonymous layer 로 추출 후 USDA 텍스트로
               직렬화.
            3. 본 핸들러가 ``omni.kit.clipboard.copy()`` 로 클립보드에 박음.
            4. 사용자가 텍스트 에디터에 붙여넣어 FBX→USD 변환 자산의 내부 데이터를
               확인.

        Bake 가 필요한 자산 (OmniGraph 만 존재) 의 경우:
            - 클립보드 복사하지 않음 (timeSamples 가 없으므로).
            - 로그에 "이 자산은 [Bake] 가 필요합니다" 라고 안내.

        본 핸들러는 **기존 기능 일절 변경하지 않는 신규 path**.

        Args:
            prim_path: 인스턴스 prim_path (예: `/World/aaa`).
        """
        try:
            inst = self._registry.get_by_prim_path(prim_path)
        except Exception:
            inst = None
        if inst is None:
            for it in self._registry.all_instances():
                if it.prim_path == prim_path:
                    inst = it
                    break
        if inst is None:
            self._log(f"Copy TS 실패 — 인스턴스를 찾을 수 없음: {prim_path}")
            return

        self._log(f"Copy TS 시작 prim={prim_path} (USDA 직렬화 중...)")

        try:
            ok, text, kind, result = self._evaluator.dump_master_timesamples_usda(prim_path)
        except Exception as exc:
            self._log(f"Copy TS 예외 prim={prim_path}: {exc}")
            return

        from .lam_asset_diagnostics import kind_to_user_label
        from .lam_types import ASSET_KIND_OMNIGRAPH, ASSET_KIND_STATIC

        kind_label = kind_to_user_label(kind) or kind

        if not ok:
            # 결과 분기 — 사용자 의도대로 "bake 필요" 안내가 명확히 나오게 한다.
            if kind == ASSET_KIND_OMNIGRAPH:
                self._log(
                    f"Copy TS 생략 — 이 자산은 **OmniGraph** 입니다 (kind={kind_label}).\n"
                    f"   timeSamples 데이터가 없어 복사할 내용이 없습니다.\n"
                    f"   → 인스턴스 행의 **[Bake]** 버튼을 눌러 in-memory timeSamples 로 "
                    f"변환한 뒤 다시 [Copy TS] 를 누르세요.\n"
                    f"   stats: {result.to_log_line()}"
                )
            elif kind == ASSET_KIND_STATIC:
                self._log(
                    f"Copy TS 생략 — 이 자산은 **STATIC** 입니다 (kind={kind_label}).\n"
                    f"   시간 데이터(timeSamples) 가 전혀 없습니다. 복사할 내용 없음.\n"
                    f"   stats: {result.to_log_line()}"
                )
            else:
                self._log(
                    f"Copy TS 실패 prim={prim_path} kind={kind_label} "
                    f"error={result.error or '알 수 없음'}\n"
                    f"   stats: {result.to_log_line()}"
                )
            return

        # 성공 — 클립보드 복사. omni.kit.clipboard.copy 가 사용 가능 (tbs_control_1 에서
        # 이미 검증된 패턴) 이지만 import 실패 fallback 도 준비한다.
        bytes_len = len(text.encode("utf-8", errors="ignore"))
        copied = False
        copy_method = ""
        try:
            from omni.kit.clipboard import copy as clipboard_copy  # type: ignore

            clipboard_copy(text)
            copied = True
            copy_method = "omni.kit.clipboard"
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} Copy TS: omni.kit.clipboard 사용 실패 ({exc}), tkinter 폴백 시도",
                flush=True,
            )
            # fallback — tkinter (Windows 환경 기본 포함)
            try:
                import tkinter  # type: ignore

                tk_root = tkinter.Tk()
                tk_root.withdraw()
                tk_root.clipboard_clear()
                tk_root.clipboard_append(text)
                tk_root.update()
                tk_root.destroy()
                copied = True
                copy_method = "tkinter"
            except Exception as exc2:
                self._log(
                    f"Copy TS: 클립보드 API 사용 실패 — omni:{exc} / tkinter:{exc2}.\n"
                    f"   대안: stage 의 prim_path 트리에서 직접 USDA 로 export 하세요."
                )

        if copied:
            self._log(
                f"Copy TS 완료 prim={prim_path} kind={kind_label}\n"
                f"   prims={result.n_prims} ts_attrs={result.n_attrs_with_timesamples} "
                f"(xform={result.n_xform_op_ts} skel={result.n_skel_anim_ts} "
                f"mesh={result.n_mesh_points_ts} vis={result.n_visibility_ts}) "
                f"tc=[{result.tc_min:.3f},{result.tc_max:.3f}]\n"
                f"   → 클립보드에 USDA 텍스트 {bytes_len:,} bytes 복사 완료 "
                f"(via {copy_method}). 텍스트 에디터에 붙여넣어 확인하세요."
            )

    def _invalidate_attr_caches(self) -> None:
        try:
            self._evaluator.invalidate_attr_cache(None)
        except Exception:
            pass
        # 핫픽스 7 — 인스턴스 등록/해제 변동 시 sublayer mapping 시그니처도 모두 무효화하여
        # 다음 update tick 에서 evaluator 가 sublayer mapping 을 다시 author 하도록 한다.
        try:
            self._evaluator.invalidate_mapping(None)
        except Exception:
            pass

    def _log(self, msg: str) -> None:
        print(f"{_PRINT_PREFIX} {msg}", flush=True)
        try:
            if self._log_label is not None:
                # 간단한 1줄 갱신.
                self._log_label.text = msg
        except Exception:
            pass


__all__ = [
    "LamWindow",
    "load_automatically",
    "default_load_usd_path",
    "resolve_local_data_path",
]
