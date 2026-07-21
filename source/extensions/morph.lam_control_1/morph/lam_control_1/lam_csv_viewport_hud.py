"""Viewport 우측 상단 LAM 미니 패널 (2D 오버레이).

기존 ``LamSimulationCsvPlayWindow`` (``ui.Window``) 는 그대로 두고,
아래 플래그가 True 일 때만 default/LAM Viewport ``get_frame`` 슬롯에 컨트롤을 붙인다.

- **합성 USD** — LAM Window 「② 기존 합성 USD 열기」와 동일 경로 모델 + Open Master
- **CSV Play** — 폴더·파일·목록/타임라인/Play/중지·배속·공정만보기(1x)·재생 타임라인

**on/off:** ``lam_sim_control_defaults.SHOW_VIEWPORT_CSV_PANEL``.
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .lam_viewport import LamViewport
    from .lam_window import LamWindow
    from .simulation_play import LamSimulationCsvPlayWindow

_PRINT_PREFIX = "[LAM/CSV-HUD]"

# ---------------------------------------------------------------------------
# True → LAM Window show() 시 Viewport 우측 상단 CSV 미니 패널 표시.
# False → 기존과 동일 (LAM CSV 시뮬 재생 ui.Window 만).
# ---------------------------------------------------------------------------
LAM_CSV_VIEWPORT_CONTROLS_ENABLED = True

_FRAME_SLOT = "morph.lam_control_1:csv_play_hud"
_PANEL_W = 300
_PANEL_PAD = 8
_TOP_SPACER_H = 12
_TIMELINE_H = 200
_CHECKBOX_LABEL_WIDTH = 52
_CHECKBOX_ROW_HEIGHT = 22


def viewport_csv_panel_enabled() -> bool:
    """우상단 CSV HUD 표시 여부 (기존 상수와 defaults 설정 모두 존중)."""
    try:
        from .lam_sim_control_defaults import SHOW_VIEWPORT_CSV_PANEL

        return bool(LAM_CSV_VIEWPORT_CONTROLS_ENABLED) and bool(
            SHOW_VIEWPORT_CSV_PANEL
        )
    except Exception:
        return bool(LAM_CSV_VIEWPORT_CONTROLS_ENABLED)


def _resolve_viewport_window(viewport: Optional["LamViewport"]) -> Optional[Any]:
    """``get_frame`` 을 제공하는 ViewportWindow (전용 LAM → 활성 default)."""
    if viewport is not None:
        try:
            dedicated = getattr(viewport, "_dedicated_window", None)
            if dedicated is not None and callable(getattr(dedicated, "get_frame", None)):
                return dedicated
        except Exception:
            pass
    try:
        from omni.kit.viewport.utility import get_active_viewport_window  # type: ignore

        win = get_active_viewport_window()
        if win is not None and callable(getattr(win, "get_frame", None)):
            return win
    except Exception:
        pass
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name  # type: ignore

        api = get_viewport_from_window_name("Viewport")
        for attr in ("viewport_window", "window", "_viewport_window", "_window"):
            cand = getattr(api, attr, None) if api is not None else None
            if cand is not None and callable(getattr(cand, "get_frame", None)):
                return cand
        if api is not None and callable(getattr(api, "get_frame", None)):
            return api
    except Exception:
        pass
    return None


class LamCsvViewportControlsHud:
    """Viewport 우측 상단 — 합성 USD Open Master + CSV Play."""

    def __init__(
        self,
        csv_window: "LamSimulationCsvPlayWindow",
        *,
        lam_window: Optional["LamWindow"] = None,
        viewport: Optional["LamViewport"] = None,
    ) -> None:
        self._csv = csv_window
        self._lam = lam_window
        self._viewport = viewport
        self._root: Any = None
        self._hud_combo: Any = None
        self._sched_token: int = 0
        self._mounted_window: Any = None

    def _resolve_hud_window(self) -> Optional[Any]:
        ext = getattr(self._lam, "_kit_ext", None) if self._lam is not None else None
        if ext is not None:
            try:
                from .lam_screen_visibility import hud_viewport_window

                vw = hud_viewport_window(ext)
                if vw is not None and callable(getattr(vw, "get_frame", None)):
                    return vw
            except Exception:
                pass
        return _resolve_viewport_window(self._viewport)

    def destroy(self) -> None:
        self._csv.register_hud_timeline_ui(None)
        self._destroy_layer()
        self._hud_combo = None

    def sync_layers(self, *, delay_frames: int = 8) -> None:
        """Viewport 가 준비된 뒤 패널을 붙인다 (몇 프레임 지연)."""
        if not viewport_csv_panel_enabled():
            self._destroy_layer()
            return
        self._sched_token += 1
        token = self._sched_token

        def _try_mount(remaining: int) -> None:
            if token != self._sched_token:
                return
            vw = self._resolve_hud_window()
            if vw is not None:
                self._mount_on_viewport(vw)
                return
            if remaining > 0:
                try:
                    import omni.kit.app  # type: ignore

                    app = omni.kit.app.get_app()
                    if app is not None:
                        app.post_update(lambda: _try_mount(remaining - 1))
                        return
                except Exception:
                    pass
            print(
                f"{_PRINT_PREFIX} Viewport get_frame unavailable — HUD skipped.",
                flush=True,
            )

        _try_mount(max(0, int(delay_frames)))

    def _destroy_layer(self) -> None:
        self._csv.register_hud_timeline_ui(None)
        self._root = None
        vw = self._mounted_window
        self._mounted_window = None
        try:
            if vw is None:
                return
            slot = _FRAME_SLOT
            if callable(getattr(vw, "get_frame", None)):
                with vw.get_frame(slot):
                    pass
        except Exception:
            pass

    def _mount_on_viewport(self, vw: Any) -> None:
        try:
            import omni.ui as ui  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.ui unavailable: {exc}", flush=True)
            return

        self._csv.ensure_playback_models()
        self._destroy_layer()
        self._mounted_window = vw

        names = self._csv.csv_file_display_names()
        idx = self._csv.get_csv_combo_index()
        lam = self._lam
        master_model = (
            getattr(lam, "_master_path_model", None) if lam is not None else None
        )

        try:
            ra = getattr(ui, "Alignment", None)
            rt = getattr(ra, "RIGHT_TOP", None) if ra is not None else None
            with vw.get_frame(_FRAME_SLOT):
                root = ui.ZStack(alignment=rt) if rt is not None else ui.ZStack()
                self._root = root
                with root:
                    with ui.VStack():
                        ui.Spacer(height=_TOP_SPACER_H)
                        with ui.HStack():
                            ui.Spacer()
                            with ui.Frame(
                                width=_PANEL_W,
                                style={
                                    "border_width": 1,
                                    "border_color": 0xFF5A6A80,
                                    "border_radius": 4,
                                    "padding": _PANEL_PAD,
                                },
                            ):
                                with ui.ZStack():
                                    ui.Rectangle(
                                        style={"background_color": 0xE6181C22}
                                    )
                                    with ui.VStack(spacing=5):
                                        ui.Label(
                                            "LAM (Viewport)",
                                            height=18,
                                            style={
                                                "font_size": 13,
                                                "color": 0xFFFFFFFF,
                                            },
                                        )
                                        try:
                                            ext = (
                                                getattr(self._lam, "_kit_ext", None)
                                                if self._lam is not None
                                                else None
                                            )
                                            if ext is not None:
                                                from .lam_screen_visibility import (
                                                    mount_screen_visibility_checkboxes,
                                                )

                                                mount_screen_visibility_checkboxes(
                                                    ext,
                                                    ui,
                                                    row_height=_CHECKBOX_ROW_HEIGHT,
                                                )
                                        except Exception as exc:
                                            print(
                                                f"{_PRINT_PREFIX} screen visibility UI: {exc}",
                                                flush=True,
                                            )
                                        # if master_model is not None and lam is not None:
                                        #     ui.Label("합성 USD", height=14)
                                        #     ui.StringField(
                                        #         model=master_model,
                                        #         height=22,
                                        #         tooltip=(
                                        #             "로컬 .usd 또는 "
                                        #             "omniverse://서버/경로/file.usd"
                                        #         ),
                                        #     )
                                        #     with ui.HStack(spacing=4, height=26):
                                        #         ui.Button(
                                        #             "Open Master",
                                        #             width=120,
                                        #             clicked_fn=lam._on_open_master,
                                        #             tooltip=(
                                        #                 "LAM Window ② 와 동일 — "
                                        #                 "Discover + Extract"
                                        #             ),
                                        #         )
                                        #         ui.Spacer()
                                        #     ui.Rectangle(
                                        #         height=1,
                                        #         style={
                                        #             "background_color": 0xFF5A6A80
                                        #         },
                                        #     )
                                        # ui.Label(
                                        #     "CSV Play",
                                        #     height=18,
                                        #     style={
                                        #         "font_size": 12,
                                        #         "color": 0xFFCCCCCC,
                                        #     },
                                        # )
                                        # ui.Label("폴더", height=14)
                                        # dir_m = self._csv._csv_dir_model
                                        # if dir_m is not None:
                                        #     ui.StringField(
                                        #         model=dir_m,
                                        #         height=22,
                                        #         tooltip="CSV 폴더 — [목록]으로 갱신",
                                        #     )
                                        ui.Label("파일", height=14)
                                        if names:
                                            self._hud_combo = ui.ComboBox(
                                                max(0, min(idx, len(names) - 1)),
                                                *names,
                                                height=24,
                                            )
                                            try:
                                                self._hud_combo.model.add_item_changed_fn(
                                                    lambda *_a: self._on_hud_combo_changed()
                                                )
                                            except Exception:
                                                pass
                                        else:
                                            self._hud_combo = None
                                            ui.Label(
                                                "(CSV 없음)",
                                                height=22,
                                                word_wrap=True,
                                            )
                                        with ui.HStack(spacing=4, height=26):
                                            # ui.Button(
                                            #     "목록",
                                            #     width=56,
                                            #     clicked_fn=self._on_hud_refresh_clicked,
                                            #     tooltip="목록 새로고침",
                                            # )
                                            ui.Button(
                                                "타임라인",
                                                width=56,
                                                clicked_fn=self._csv._on_schedule_refresh_clicked,
                                                tooltip="타임라인·캐시 갱신",
                                            )
                                            ui.Button(
                                                "재생",
                                                width=52,
                                                clicked_fn=self._on_hud_play_clicked,
                                                tooltip="화면1·2 동시 재생 (화면1 HUD 설정 + 각 화면 registry)",
                                            )
                                            ui.Button(
                                                "일시정지",
                                                width=56,
                                                clicked_fn=self._on_hud_pause_clicked,
                                                tooltip="재생 중인 화면별 일시정지",
                                            )
                                            ui.Button(
                                                "정지",
                                                width=44,
                                                clicked_fn=self._on_hud_stop_clicked,
                                                tooltip=(
                                                    "초기화: TBS→0, FOUP 75 show, "
                                                    "기타 wafer hide, 재생 위치 리셋"
                                                ),
                                            )
                                        po_m = self._csv._process_only_model
                                        try:
                                            with ui.HStack(
                                                spacing=4, height=_CHECKBOX_ROW_HEIGHT
                                            ):
                                                if po_m is not None:
                                                    ui.Label(
                                                        "공정만보기",
                                                        width=_CHECKBOX_LABEL_WIDTH,
                                                        height=_CHECKBOX_ROW_HEIGHT,
                                                    )
                                                    ui.CheckBox(
                                                        model=po_m,
                                                        width=20,
                                                        height=_CHECKBOX_ROW_HEIGHT,
                                                        clicked_fn=lambda: self._csv._on_process_only_checkbox_clicked(),
                                                        tooltip=(
                                                            "재생 중에도 즉시 전환: CSV 시각 유지, "
                                                            "JSON 없는 빈 대기만 생략(배속 1x). "
                                                            "체크 해제 시 기존 시간 재생."
                                                        ),
                                                    )
                                                self._csv.mount_wafer_label_show_checkbox_ui(
                                                    ui,
                                                    lam_window=self._lam,
                                                    wrap_row=False,
                                                    label_width=_CHECKBOX_LABEL_WIDTH,
                                                    row_height=_CHECKBOX_ROW_HEIGHT,
                                                )
                                                ui.Spacer()
                                        except Exception as exc:
                                            print(
                                                f"{_PRINT_PREFIX} process/wafer checkboxes: {exc}",
                                                flush=True,
                                            )
                                        try:
                                            self._csv.mount_overlay_feature_checkboxes_ui(
                                                ui,
                                                label_width=_CHECKBOX_LABEL_WIDTH,
                                                row_height=_CHECKBOX_ROW_HEIGHT,
                                                spacing=4,
                                            )
                                        except Exception as exc:
                                            print(
                                                f"{_PRINT_PREFIX} overlay feature checkboxes: {exc}",
                                                flush=True,
                                            )
                                        try:
                                            self._csv.mount_play_camera_fly_checkbox_ui(
                                                ui,
                                                label_width=_CHECKBOX_LABEL_WIDTH,
                                                row_height=_CHECKBOX_ROW_HEIGHT,
                                                spacing=4,
                                            )
                                            self._csv.mount_play_camera_capture_button_ui(
                                                ui,
                                                width=52,
                                                height=_CHECKBOX_ROW_HEIGHT,
                                            )
                                            self._csv.mount_top_view_checkbox_ui(
                                                ui,
                                                label_width=_CHECKBOX_LABEL_WIDTH,
                                                row_height=_CHECKBOX_ROW_HEIGHT,
                                                spacing=4,
                                            )
                                        except Exception as exc:
                                            print(
                                                f"{_PRINT_PREFIX} play camera fly UI: {exc}",
                                                flush=True,
                                            )
                                        try:
                                            self._csv.mount_play_prim_hide_checkbox_ui(
                                                ui,
                                                label_width=_CHECKBOX_LABEL_WIDTH,
                                                row_height=_CHECKBOX_ROW_HEIGHT,
                                                spacing=4,
                                            )
                                        except Exception as exc:
                                            print(
                                                f"{_PRINT_PREFIX} play prim hide checkbox: {exc}",
                                                flush=True,
                                            )
                                        with ui.HStack(spacing=4, height=26):
                                            ui.Label("배속", width=36)
                                            sp_m = self._csv._speed_model
                                            if sp_m is not None:
                                                ui.FloatField(model=sp_m, width=52)
                                            ui.Button(
                                                "1x",
                                                width=30,
                                                clicked_fn=lambda: self._csv._set_speed_preset(
                                                    1.0
                                                ),
                                            )
                                            ui.Button(
                                                "5x",
                                                width=30,
                                                clicked_fn=lambda: self._csv._set_speed_preset(
                                                    5.0
                                                ),
                                                tooltip="공정만보기 체크 시 Play 는 1x 고정",
                                            )
                                            ui.Spacer()

                                        try:
                                            ext = (
                                                getattr(self._lam, "_kit_ext", None)
                                                if self._lam is not None
                                                else None
                                            )
                                            if ext is not None:
                                                from .lam_aux_kit_window_ui import (
                                                    mount_aux_kit_window_checkboxes_ui,
                                                )

                                                mount_aux_kit_window_checkboxes_ui(
                                                    ext,
                                                    ui,
                                                    label_width=0,
                                                    row_height=_CHECKBOX_ROW_HEIGHT,
                                                )
                                        except Exception as exc:
                                            print(
                                                f"{_PRINT_PREFIX} aux window checkboxes: {exc}",
                                                flush=True,
                                            )

                                        # 타임라인 창


                                        # ui.Label(
                                        #     "재생 타임라인 — JSON 재생 중 녹색",
                                        #     height=16,
                                        #     style={"font_size": 11, "color": 0xFFAAAAAA},
                                        # )
                                        # hud_prog_model = None
                                        # schedule_stack = None
                                        # schedule_scroll = None
                                        # try:
                                        #     from omni.ui import (  # type: ignore
                                        #         SimpleStringModel,
                                        #     )

                                        #     hud_prog_model = SimpleStringModel(
                                        #         "(대기)"
                                        #     )
                                        #     with ui.ScrollingFrame(
                                        #         height=_TIMELINE_H,
                                        #         style={
                                        #             "background_color": 0xFF1A1E26,
                                        #             "border_width": 1,
                                        #             "border_color": 0xFF3A3A3A,
                                        #         },
                                        #     ) as schedule_scroll:
                                        #         with ui.VStack(
                                        #             spacing=2, height=0
                                        #         ) as tl_stack:
                                        #             schedule_stack = tl_stack
                                        #     ui.StringField(
                                        #         model=hud_prog_model,
                                        #         height=20,
                                        #         read_only=True,
                                        #     )
                                        # except Exception:
                                        #     ui.Label(
                                        #         "(타임라인 UI 없음)",
                                        #         height=40,
                                        #         word_wrap=True,
                                        #     )
                                        # if schedule_stack is not None:
                                        #     self._csv.register_hud_timeline_ui(
                                        #         schedule_stack,
                                        #         build_progress_model=hud_prog_model,
                                        #         scroll_frame=schedule_scroll,
                                        #     )
                        ui.Spacer()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} mount failed: {exc}", flush=True)
            self._root = None
            return

        print(
            f"{_PRINT_PREFIX} Viewport 패널 표시 "
            f"(합성 USD + CSV·공정만보기·타임라인, 우측 상단).",
            flush=True,
        )
        try:
            self._csv.apply_wafer_label_visibility_from_ui(lam_window=self._lam)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} wafer label sync after mount: {exc}", flush=True)

    def _on_hud_play_clicked(self) -> None:
        lam = self._lam
        if lam is not None:
            try:
                lam._on_hud_csv_play_clicked()
                return
            except Exception as exc:
                print(f"{_PRINT_PREFIX} HUD play all screens: {exc}", flush=True)
        self._csv._on_play_clicked()

    def _on_hud_pause_clicked(self) -> None:
        lam = self._lam
        if lam is not None:
            try:
                lam._on_hud_csv_pause_clicked()
                return
            except Exception as exc:
                print(f"{_PRINT_PREFIX} HUD pause all screens: {exc}", flush=True)
        self._csv._on_csv_pause_clicked()

    def _on_hud_stop_clicked(self) -> None:
        lam = self._lam
        if lam is not None:
            try:
                lam._on_hud_csv_stop_reset_clicked()
                return
            except Exception as exc:
                print(f"{_PRINT_PREFIX} HUD stop all screens: {exc}", flush=True)
        self._csv._on_csv_stop_reset_clicked()

    def _on_hud_refresh_clicked(self) -> None:
        self._csv._on_refresh_clicked()
        self._rebuild_hud_combo()

    def _on_hud_combo_changed(self) -> None:
        combo = self._hud_combo
        if combo is None:
            return
        try:
            from .simulation_play import _read_combo_index

            self._csv.set_csv_combo_index(_read_combo_index(combo))
        except Exception:
            pass

    def _rebuild_hud_combo(self) -> None:
        """목록 새로고침 후 HUD 콤보만 다시 그리기."""
        if self._root is None:
            return
        self.sync_layers(delay_frames=2)


__all__ = [
    "LAM_CSV_VIEWPORT_CONTROLS_ENABLED",
    "LamCsvViewportControlsHud",
    "viewport_csv_panel_enabled",
]
