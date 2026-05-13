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
from typing import Optional

from .lam_composition_discovery import CompositionDiscovery
from .lam_external_event_runner import LamExternalEventRunner
from .lam_instance_registry import AnimationInstanceRegistry
from .lam_json_test_window import LamJsonTestWindow
from .lam_master_stage import MasterStage
from .lam_multi_usd_loader import MultiUsdLoader
from .lam_playback_scheduler import PlaybackScheduler
from .lam_runtime_evaluator import RuntimeEvaluator
from .lam_sequence_editor import LamSequenceEditor
from .lam_viewport import LamViewport


_PRINT_PREFIX = "[LAM/WIN]"

WINDOW_TITLE = "LAM Multi-USD Load"


def _find_lam_data_root() -> str:
    """repo 루트의 `lam/` 폴더 절대 경로를 반환한다.

    `__file__` 위치가 source/extensions/... 이든 _build/.../exts/... 이든 모두 통하도록,
    부모 폴더를 거슬러 올라가며 `lam` 폴더가 존재하는 첫 위치를 반환한다.
    못 찾으면 폴더 자체를 만들어서 반환(첫 실행 자동 생성).
    """
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
    # 폴더가 어디에도 없으면 here 위로 6단계(= 일반적인 _build 깊이) 위에 생성 시도.
    fallback = os.path.normpath(os.path.join(here, "..", "..", "..", "..", "..", "..", "lam"))
    try:
        os.makedirs(os.path.join(fallback, "lam_event_sequences"), exist_ok=True)
        os.makedirs(os.path.join(fallback, "lam_external_results"), exist_ok=True)
        os.makedirs(os.path.join(fallback, "usd"), exist_ok=True)
    except Exception:
        pass
    return fallback


def _ext_data_dir() -> str:
    """레거시 호환 — 새 lam/ 폴더 위치를 반환."""
    return _find_lam_data_root()


class LamWindow:
    """다중 USD 로드 + 메인 진입 창."""

    def __init__(
        self,
        registry: AnimationInstanceRegistry,
        scheduler: PlaybackScheduler,
        evaluator: RuntimeEvaluator,
    ) -> None:
        self._registry = registry
        self._scheduler = scheduler
        self._evaluator = evaluator
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
        self._external_runner: Optional[LamExternalEventRunner] = None
        self._window = None
        self._instances_inner = None
        self._asset_path_model = None
        self._instance_id_model = None
        self._master_path_model = None
        self._results_path_model = None
        self._sim_speed_model = None
        self._log_label = None
        # 다이얼로그 보유 슬롯(중복 생성 방지).
        self._fp_open_usd = None
        self._fp_open_master = None
        self._fp_save_master = None
        self._fp_open_results = None

        self._registry.add_listener(self._refresh_instances)

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
        # 모든 기본 경로는 repo 루트의 lam/ 폴더를 가리킨다(외부 _build/.../exts/... 가 아님).
        lam_root = _find_lam_data_root()
        self._master_path_model = ui.SimpleStringModel(
            os.path.join(lam_root, "usd", "master.usd")
        )
        self._results_path_model = ui.SimpleStringModel(
            os.path.join(lam_root, "lam_external_results", "sample_external_result.json")
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
                        with ui.HStack(spacing=4, height=20):
                            ui.Label(
                                "prim_path / instance_id / kind / source_asset",
                                height=18,
                            )
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

        self._refresh_instances()

        # REQ-008 — LAM Window 가 뜨면 시퀀스 편집기도 같이 자동으로 연다.
        try:
            self._open_editor()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} auto open editor failed: {exc}", flush=True)

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
        path = (self._master_path_model.get_value_as_string() or "").strip()
        if not path:
            self._log("Master path 가 비어 있습니다.")
            return
        ok = self._master.open_master(path)
        self._log(f"Open Master {'OK' if ok else 'FAIL'}: {path}")
        if ok:
            try:
                self._master.set_root_layer_edit_target()
            except Exception:
                pass
            self._on_discover()

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

    def _on_discover(self) -> None:
        added = self._discovery.discover()
        self._log(f"Discover added={len(added)}")

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
            seq_dir = os.path.join(_find_lam_data_root(), "lam_event_sequences")
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
            seq_dir = os.path.join(_find_lam_data_root(), "lam_event_sequences")
            self._json_test_window = LamJsonTestWindow(
                registry=self._registry,
                scheduler=self._scheduler,
                sequence_dir=seq_dir,
            )
        self._json_test_window.show()

    def _on_run_external(self) -> None:
        if self._results_path_model is None:
            return
        path = (self._results_path_model.get_value_as_string() or "").strip()
        if not path:
            self._log("Results path 가 비어 있습니다.")
            return
        if self._external_runner is None:
            seq_dir = os.path.join(_find_lam_data_root(), "lam_event_sequences")
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
            start = os.path.join(_find_lam_data_root(), "usd")
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
            start = os.path.join(_find_lam_data_root(), "usd")
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
            start = os.path.join(_find_lam_data_root(), "usd")
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
            start = os.path.join(_find_lam_data_root(), "lam_external_results")
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
                    ui.Button(
                        "Remove",
                        width=70,
                        clicked_fn=lambda p=inst.prim_path: self._on_remove(p),
                    )

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
        if not raw:
            self._log(f"Bake 실패 — source_asset 비어 있음 prim={prim_path}")
            return
        raw_norm = raw.replace("\\", "/")
        abs_path = ""
        if os.path.isfile(raw_norm):
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
            self._log(f"Bake 실패 — 자산 경로 해석 실패: raw={raw!r}")
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
            inst_prim = inst.prim_path  # noqa: B023 (closure intended)
            try:
                ok = self._evaluator.attach_memory_baked_layer(
                    inst_prim,
                    baked_layer,
                    source_asset_for_log=abs_path,
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
                self._refresh_instances()
            except Exception as _ref_exc:
                print(
                    f"{_PRINT_PREFIX} bake 후 _refresh_instances 실패: {_ref_exc}",
                    flush=True,
                )

        # asyncio task 로 실행. Kit 의 main loop 가 await 를 처리하도록 ensure_future.
        try:
            import asyncio  # noqa: F401  (이미 import 되어 있을 수 있으나 안전)

            asyncio.ensure_future(_runner())
        except Exception as exc:
            self._log(f"Bake task 스케줄 실패: {exc}")

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


__all__ = ["LamWindow"]
