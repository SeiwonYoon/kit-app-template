"""Viewport 우측 상단 LAM 미니 패널 (2D 오버레이).

기존 ``LamSimulationCsvPlayWindow`` (``ui.Window``) 는 그대로 두고,
CSV HUD 본체는 Viewport ``get_frame`` 이 아닌 **floating ``ui.Window``** 에 붙인다.
(Viewport get_frame 위젯 visible/remount 가 3D 전체 깜빡임을 유발하기 때문)

좌상단 투명 토글 버튼만 Viewport ``get_frame`` 슬롯에 두고,
클릭 시 floating 창의 ``visible`` 만 토글한다.

관련 플래그 (``lam_sim_control_defaults``):
- ``SHOW_VIEWPORT_CSV_PANEL`` — 앱 시작 시 CSV HUD 표시
- ``SHOW_VIEWPORT_CSV_PANEL_TOGGLE_HOTSPOT`` — 화면1 좌상단(Federation HUD 바로 아래)
  투명 토글 버튼 (TBS ``SHOW_VIEWPORT_EBS_HUD_TOGGLE_HOTSPOT`` 대응)
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .lam_viewport import LamViewport
    from .lam_window import LamWindow
    from .simulation_play import LamSimulationCsvPlayWindow

_PRINT_PREFIX = "[LAM/CSV-HUD]"

# ---------------------------------------------------------------------------
# True → CSV Viewport HUD 기능 사용 가능.
# ---------------------------------------------------------------------------
LAM_CSV_VIEWPORT_CONTROLS_ENABLED = True

_FRAME_SLOT = "morph.lam_control_1:csv_play_hud"
_FRAME_SLOT_TOGGLE = "morph.lam_control_1:csv_hud_toggle"
_PANEL_W = 300
_PANEL_PAD = 8
_TOP_SPACER_H = 12
_TIMELINE_H = 200
_CHECKBOX_LABEL_WIDTH = 52
_CHECKBOX_ROW_HEIGHT = 22

# 좌상단 토글 — Federation Load HUD 블록 바로 아래 (tbs 유사 반투명 히트박스)
_TOGGLE_HOTSPOT_W = 20
_TOGGLE_HOTSPOT_H = 20
_TOGGLE_LEFT = 10
_TOGGLE_TOP_BELOW_FED = 60
_TOGGLE_HOTSPOT_BG = 0x18FFFFFF
_TOGGLE_HOTSPOT_LABEL = "·"


def viewport_csv_panel_feature_enabled() -> bool:
    return bool(LAM_CSV_VIEWPORT_CONTROLS_ENABLED)


def viewport_csv_panel_startup_visible() -> bool:
    """앱 시작 시 CSV HUD — ``SHOW_VIEWPORT_CSV_PANEL``."""
    if not viewport_csv_panel_feature_enabled():
        return False
    try:
        from .lam_sim_control_defaults import SHOW_VIEWPORT_CSV_PANEL

        return bool(SHOW_VIEWPORT_CSV_PANEL)
    except Exception:
        return False


def viewport_csv_panel_toggle_hotspot_enabled() -> bool:
    """투명 토글 버튼 — ``SHOW_VIEWPORT_CSV_PANEL_TOGGLE_HOTSPOT``."""
    if not viewport_csv_panel_feature_enabled():
        return False
    try:
        from .lam_sim_control_defaults import SHOW_VIEWPORT_CSV_PANEL_TOGGLE_HOTSPOT

        return bool(SHOW_VIEWPORT_CSV_PANEL_TOGGLE_HOTSPOT)
    except Exception:
        return True


def viewport_csv_panel_enabled() -> bool:
    """하위 호환: 시작 시 패널 표시 여부."""
    return viewport_csv_panel_startup_visible()


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
    """Viewport 우측 상단 — CSV Play HUD + 좌상단 투명 토글 버튼."""

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
        self._toggle_root: Any = None
        self._hud_combo: Any = None
        self._sched_token: int = 0
        self._toggle_sched_token: int = 0
        self._mounted_window: Any = None
        self._toggle_mounted_window: Any = None
        # Viewport get_frame 밖 — 토글 시 3D remount/깜빡임 방지
        self._float_window: Any = None
        self._legacy_frame_cleared: bool = False
        self._ensure_user_visible_flag()

    def _ensure_user_visible_flag(self) -> None:
        lam = self._lam
        if lam is None:
            return
        if getattr(lam, "_csv_hud_user_visible", None) is None:
            try:
                lam._csv_hud_user_visible = bool(viewport_csv_panel_startup_visible())
            except Exception:
                lam._csv_hud_user_visible = False

    def _user_wants_visible(self) -> bool:
        self._ensure_user_visible_flag()
        lam = self._lam
        if lam is None:
            return viewport_csv_panel_startup_visible()
        return bool(getattr(lam, "_csv_hud_user_visible", False))

    def _set_user_visible(self, want: bool) -> None:
        lam = self._lam
        if lam is not None:
            try:
                lam._csv_hud_user_visible = bool(want)
            except Exception:
                pass

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
        self._destroy_toggle_layer()
        self._hud_combo = None

    def sync_layers(self, *, delay_frames: int = 8, force: bool = False) -> None:
        """CSV HUD — Viewport get_frame 이 아닌 floating ``ui.Window``.

        표시 토글은 ``window.visible`` 만 바꿔 3D viewport 재구성을 피한다.
        """
        if not viewport_csv_panel_feature_enabled():
            self._destroy_layer()
            return
        if not self._user_wants_visible():
            self._apply_root_visible(False)
            return
        if not force and self._float_window is not None:
            self._apply_root_visible(True)
            return
        if force and self._float_window is not None:
            # 내용 재빌드가 필요할 때만 window 창 remount (viewport get_frame 아님)
            self._destroy_float_window_only()
        self._sched_token += 1
        token = self._sched_token

        def _try_mount(remaining: int) -> None:
            if token != self._sched_token:
                return
            if not self._user_wants_visible():
                self._apply_root_visible(False)
                return
            if self._float_window is not None:
                self._apply_root_visible(True)
                return
            self._mount_float_hud()
            if self._float_window is not None:
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
            if not getattr(self, "_hud_skip_logged", False):
                self._hud_skip_logged = True
                print(
                    f"{_PRINT_PREFIX} floating HUD mount skipped (omni.ui?).",
                    flush=True,
                )

        _try_mount(max(0, int(delay_frames)))

    def sync_toggle_hotspot(self, *, delay_frames: int = 8, force: bool = False) -> None:
        """화면1 좌상단(Federation HUD 바로 아래) 투명 토글 버튼."""
        if not viewport_csv_panel_toggle_hotspot_enabled():
            self._destroy_toggle_layer()
            return
        try:
            current = self._resolve_hud_window()
            if (
                not force
                and self._toggle_root is not None
                and self._toggle_mounted_window is not None
                and current is not None
                and current is self._toggle_mounted_window
            ):
                # 토글 버튼만 유지 — HUD는 미리 float 로 준비(숨김)해 첫 show remount 방지
                self._prebuild_float_hud_hidden()
                return
        except Exception:
            pass
        self._toggle_sched_token += 1
        token = self._toggle_sched_token

        def _try_mount(remaining: int) -> None:
            if token != self._toggle_sched_token:
                return
            if not viewport_csv_panel_toggle_hotspot_enabled():
                self._destroy_toggle_layer()
                return
            vw = self._resolve_hud_window()
            if vw is not None:
                self._mount_toggle_hotspot(vw)
                self._prebuild_float_hud_hidden()
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
            if not getattr(self, "_toggle_skip_logged", False):
                self._toggle_skip_logged = True
                print(
                    f"{_PRINT_PREFIX} Viewport get_frame unavailable — toggle skipped.",
                    flush=True,
                )

        _try_mount(max(0, int(delay_frames)))

    def toggle_csv_hud_visibility(self) -> None:
        """투명 버튼 클릭 → CSV HUD 보이기/숨기기 (floating window.visible 만)."""
        want = not self._user_wants_visible()
        self._set_user_visible(want)
        print(
            f"{_PRINT_PREFIX} toggle → HUD {'show' if want else 'hide'} (float.visible)",
            flush=True,
        )
        if want:
            if self._float_window is None:
                self._mount_float_hud()
            self._apply_root_visible(True)
        else:
            self._sched_token += 1
            self._apply_root_visible(False)

    def _prebuild_float_hud_hidden(self) -> None:
        """토글 버튼 준비 시 HUD 창을 미리 빌드(숨김) — 첫 show 도 remount 없음."""
        if self._float_window is not None:
            return
        try:
            self._mount_float_hud()
            self._apply_root_visible(False)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} prebuild float HUD: {exc}", flush=True)

    def _apply_root_visible(self, want: bool) -> None:
        win = self._float_window
        if win is not None:
            try:
                win.visible = bool(want)
                return
            except Exception:
                pass
        root = self._root
        if root is None:
            return
        try:
            root.visible = bool(want)
        except Exception:
            try:
                if hasattr(root, "set_visible"):
                    root.set_visible(bool(want))
            except Exception:
                pass

    @staticmethod
    def _clear_viewport_slot(vw: Any, slot: str) -> None:
        """레거시 get_frame 슬롯 정리용. 토글 hide 경로에서는 호출하지 않음."""
        if vw is None or not callable(getattr(vw, "get_frame", None)):
            return
        try:
            import omni.ui as ui  # type: ignore

            with vw.get_frame(slot):
                ui.Spacer(height=0)
        except Exception:
            pass

    def _clear_legacy_viewport_hud_slot(self) -> None:
        if self._legacy_frame_cleared:
            return
        self._legacy_frame_cleared = True
        try:
            self._clear_viewport_slot(self._resolve_hud_window(), _FRAME_SLOT)
        except Exception:
            pass

    def _float_window_flags(self, ui: Any) -> int:
        flags = 0
        for name in (
            "WINDOW_FLAGS_NO_SCROLLBAR",
            "WINDOW_FLAGS_NO_COLLAPSE",
            "WINDOW_FLAGS_NO_DOCKING",
        ):
            bit = getattr(ui, name, None)
            if bit is not None:
                flags |= int(bit)
        return flags

    def _destroy_float_window_only(self) -> None:
        self._csv.register_hud_timeline_ui(None)
        self._root = None
        self._hud_combo = None
        win = self._float_window
        self._float_window = None
        self._mounted_window = None
        if win is None:
            return
        try:
            win.visible = False
        except Exception:
            pass
        try:
            win.destroy()
        except Exception:
            pass

    def _destroy_layer(self) -> None:
        """HUD 완전 제거 (기능 OFF·extension destroy). 토글 hide에는 쓰지 않음."""
        self._destroy_float_window_only()
        self._clear_legacy_viewport_hud_slot()

    def _destroy_toggle_layer(self) -> None:
        self._toggle_root = None
        vw = self._toggle_mounted_window
        self._toggle_mounted_window = None
        try:
            if vw is None:
                vw = self._resolve_hud_window()
            self._clear_viewport_slot(vw, _FRAME_SLOT_TOGGLE)
        except Exception:
            pass

    def _mount_float_hud(self) -> None:
        """Viewport 와 분리된 floating 창에 CSV HUD 빌드."""
        try:
            import omni.ui as ui  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.ui unavailable: {exc}", flush=True)
            return

        self._csv.ensure_playback_models()
        self._clear_legacy_viewport_hud_slot()
        if self._float_window is not None:
            self._apply_root_visible(self._user_wants_visible())
            return

        names = self._csv.csv_file_display_names()
        idx = self._csv.get_csv_combo_index()
        flags = self._float_window_flags(ui)
        try:
            kw: dict = {
                "width": int(_PANEL_W + 24),
                "height": 560,
                "visible": False,
            }
            if flags:
                kw["flags"] = flags
            win = ui.Window("LAM CSV", **kw)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} float Window create failed: {exc}", flush=True)
            return

        self._float_window = win
        self._mounted_window = win
        try:
            with win.frame:
                with ui.VStack(spacing=0):
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
                            ui.Rectangle(style={"background_color": 0xE6181C22})
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
            self._root = win
        except Exception as exc:
            print(f"{_PRINT_PREFIX} float mount failed: {exc}", flush=True)
            self._destroy_float_window_only()
            return

        if not getattr(self, "_hud_mount_logged", False):
            self._hud_mount_logged = True
            print(
                f"{_PRINT_PREFIX} floating CSV HUD ready "
                f"(toggle uses window.visible — no viewport get_frame).",
                flush=True,
            )
        self._apply_root_visible(self._user_wants_visible())
        try:
            self._csv.apply_wafer_label_visibility_from_ui(lam_window=self._lam)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} wafer label sync after mount: {exc}", flush=True)

    def _mount_toggle_hotspot(self, vw: Any) -> None:
        try:
            import omni.ui as ui  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.ui unavailable (toggle): {exc}", flush=True)
            return

        self._destroy_toggle_layer()
        self._toggle_mounted_window = vw
        hw = int(_TOGGLE_HOTSPOT_W)
        hh = int(_TOGGLE_HOTSPOT_H)
        bg = int(_TOGGLE_HOTSPOT_BG)
        top = int(_TOGGLE_TOP_BELOW_FED)
        left = int(_TOGGLE_LEFT)
        label = str(_TOGGLE_HOTSPOT_LABEL)

        def _on_click() -> None:
            print(f"{_PRINT_PREFIX} toggle button clicked", flush=True)
            self.toggle_csv_hud_visibility()

        try:
            ra = getattr(ui, "Alignment", None)
            lt = getattr(ra, "LEFT_TOP", None) if ra is not None else None
            with vw.get_frame(_FRAME_SLOT_TOGGLE):
                outer = ui.ZStack(alignment=lt) if lt is not None else ui.ZStack()
                self._toggle_root = outer
                with outer:
                    with ui.VStack():
                        ui.Spacer(height=top)
                        with ui.HStack(height=hh):
                            ui.Spacer(width=left)
                            ui.Button(
                                label,
                                width=hw,
                                height=hh,
                                clicked_fn=_on_click,
                                style={
                                    "Button": {
                                        "background_color": bg,
                                        "border_width": 0,
                                        "border_color": bg,
                                        "color": 0x18222222,
                                        "font_size": 10,
                                        "padding": 0,
                                        "margin": 0,
                                    },
                                    "Button:hovered": {
                                        "background_color": 0x28FFFFFF,
                                        "border_width": 0,
                                        "color": 0x28222222,
                                    },
                                    "Button:pressed": {
                                        "background_color": 0x40FFFFFF,
                                        "border_width": 0,
                                        "color": 0x40222222,
                                    },
                                },
                                tooltip="CSV Viewport 패널 보이기/숨기기",
                            )
                            ui.Spacer()
                        ui.Spacer()
            print(
                f"{_PRINT_PREFIX} toggle hotspot mounted "
                f"(below federation band top={top})",
                flush=True,
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} toggle mount failed: {exc}", flush=True)
            self._toggle_root = None
            self._toggle_mounted_window = None

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
        """목록 새로고침 후 HUD 콤보만 다시 그리기 (float 창 remount — viewport 비접촉)."""
        if self._float_window is None:
            return
        self.sync_layers(delay_frames=0, force=True)


__all__ = [
    "LAM_CSV_VIEWPORT_CONTROLS_ENABLED",
    "LamCsvViewportControlsHud",
    "viewport_csv_panel_enabled",
    "viewport_csv_panel_feature_enabled",
    "viewport_csv_panel_startup_visible",
    "viewport_csv_panel_toggle_hotspot_enabled",
]
