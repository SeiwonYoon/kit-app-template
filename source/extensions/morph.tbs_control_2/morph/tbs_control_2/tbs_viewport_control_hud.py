"""Viewport 좌측 상단 EBS 미니 제어 패널 (2D 오버레이).

관련 플래그 (``sim_control_defaults.py``):
- ``SHOW_VIEWPORT_EBS_CONTROL_HUD``
    앱 **시작 시** EBS HUD 를 보일지.
- ``SHOW_VIEWPORT_EBS_HUD_TOGGLE_HOTSPOT``
    화면1 **좌하단** 클릭 버튼을 둘지.
    클릭하면 HUD 보이기/숨기기를 토글한다 (시작 플래그와 독립).
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

_PRINT_PREFIX = "[TBS/EBS-HUD]"


def _ebs_hud_startup_visible() -> bool:
    """앱 시작 시 HUD 표시 여부 — ``SHOW_VIEWPORT_EBS_CONTROL_HUD``."""
    try:
        from .sim_control_defaults import SHOW_VIEWPORT_EBS_CONTROL_HUD

        return bool(SHOW_VIEWPORT_EBS_CONTROL_HUD)
    except Exception:
        return True


def _ebs_hud_toggle_hotspot_enabled() -> bool:
    """좌하단 토글 버튼 사용 여부 — ``SHOW_VIEWPORT_EBS_HUD_TOGGLE_HOTSPOT``."""
    try:
        from .sim_control_defaults import SHOW_VIEWPORT_EBS_HUD_TOGGLE_HOTSPOT

        return bool(SHOW_VIEWPORT_EBS_HUD_TOGGLE_HOTSPOT)
    except Exception:
        return True


# 패널 / 토글 각각 별도 frame 슬롯 (한쪽 destroy 가 다른 쪽을 지우지 않게).
# zz_/zzz_ 접두사: ``00_split_viewport_widgets`` 보다 위에 그려지게.
_FRAME_SLOT = "morph.tbs_control_2:ebs_control_hud"
_FRAME_SLOT_WIDGET = "morph.tbs_control_2:zz_ebs_control_hud"
_FRAME_SLOT_TOGGLE = "morph.tbs_control_2:ebs_hud_toggle"
_FRAME_SLOT_TOGGLE_WIDGET = "morph.tbs_control_2:zzz_ebs_hud_toggle"

_PANEL_W = 300
_PANEL_PAD = 8
_TOP_SPACER_H = 12
_MAX_SCROLL_H = 520

# 좌하단 토글 버튼 (확인용 80px 의 1/4 = 20px)
_TOGGLE_HOTSPOT_W = 20
_TOGGLE_HOTSPOT_H = 20
_TOGGLE_HOTSPOT_MARGIN = 12
# Kit 은 alpha≈0 이면 마우스 히트를 무시함.
# “거의 안 보이지만 클릭은 되는” 수준 — AA≈0x18(약 9%). 더 옅게: 0x10 / 더 진하게: 0x28
_TOGGLE_HOTSPOT_BG = 0x18FFFFFF
_TOGGLE_HOTSPOT_LABEL = "·"  # 빈 문자열이면 버튼 크기가 0 이 되는 Kit 이 있음


def _resolve_viewport_window(ext: Any) -> Optional[Any]:
    """``get_frame`` 을 제공하는 ViewportWindow (EBS 패널과 동일 경로)."""
    try:
        vw = getattr(ext, "_tbs_split_main_viewport_window", None)
        # 타일 hud_mount(_overlay) 는 여기서 쓰지 않음 — ViewportWindow 만
        if (
            vw is not None
            and callable(getattr(vw, "get_frame", None))
            and not hasattr(vw, "_overlay")
        ):
            return vw
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
    try:
        from .sim_multi_view import _resolve_viewport_window_for_workspace_name

        vw = _resolve_viewport_window_for_workspace_name("Viewport")
        if (
            vw is not None
            and callable(getattr(vw, "get_frame", None))
            and not hasattr(vw, "_overlay")
        ):
            return vw
    except Exception:
        pass
    return None


def _is_widget_split_hud(ext: Any) -> bool:
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active

        return bool(is_split_widget_layout_active(ext))
    except Exception:
        return False


def _resolve_ebs_hud_mount(ext: Any) -> Tuple[Optional[Any], str]:
    """EBS 제어 패널 — 항상 ViewportWindow ``get_frame`` (검증된 경로)."""
    vw = _resolve_viewport_window(ext)
    if vw is not None:
        slot = _FRAME_SLOT_WIDGET if _is_widget_split_hud(ext) else _FRAME_SLOT
        return vw, slot
    return None, _FRAME_SLOT


def _resolve_toggle_mount(ext: Any) -> Tuple[Optional[Any], str]:
    """토글 버튼 — EBS 패널과 **같은** ViewportWindow 경로.

    (타일 hud_mount Frame 은 크기 0 이 되어 클릭/표시가 안 되는 사례가 있어 사용하지 않음)
    """
    vw = _resolve_viewport_window(ext)
    if vw is not None:
        slot = (
            _FRAME_SLOT_TOGGLE_WIDGET
            if _is_widget_split_hud(ext)
            else _FRAME_SLOT_TOGGLE
        )
        return vw, slot
    return None, _FRAME_SLOT_TOGGLE


def _user_wants_ebs_hud_visible(ext: Any) -> bool:
    """런타임 표시 의도. attach 시 시작 플래그로 초기화됨."""
    return bool(getattr(ext, "_tbs_ebs_hud_user_visible", False))


class TbsViewportControlHud:
    """Viewport 좌측 상단 — EBS 시뮬 제어 패널 + 좌하단 토글 버튼."""

    def __init__(self, ext: Any) -> None:
        self._ext = ext
        self._root: Any = None
        self._toggle_root: Any = None
        self._sched_token: int = 0
        self._toggle_sched_token: int = 0

    def destroy(self) -> None:
        self._destroy_layer()
        self._destroy_toggle_layer()

    def sync_layers(self, *, delay_frames: int = 8, force: bool = False) -> None:
        """EBS 제어 패널 마운트/갱신."""
        if not _user_wants_ebs_hud_visible(self._ext):
            self._destroy_layer()
            return
        if not force and bool(getattr(self._ext, "_tbs_ebs_hud_mounted", False)):
            return
        try:
            from .sim_multi_view import startup_dual_orchestration_active

            if not force and startup_dual_orchestration_active(self._ext):
                return
        except Exception:
            pass
        self._sched_token += 1
        token = self._sched_token

        def _try_mount(remaining: int) -> None:
            if token != self._sched_token:
                return
            if not _user_wants_ebs_hud_visible(self._ext):
                self._destroy_layer()
                return
            mount, slot = _resolve_ebs_hud_mount(self._ext)
            if mount is not None:
                try:
                    self._ext._tbs_split_main_viewport_window = mount
                except Exception:
                    pass
                self._mount_on_viewport(mount, slot, sched_token=token)
                return
            if remaining > 0:
                try:
                    import omni.kit.app  # type: ignore

                    kit_app = omni.kit.app.get_app()
                    if kit_app is not None:
                        kit_app.post_update(lambda: _try_mount(remaining - 1))
                        return
                except Exception:
                    pass
            print(f"{_PRINT_PREFIX} Viewport get_frame unavailable — HUD skipped.", flush=True)

        _try_mount(max(0, int(delay_frames)))

    def sync_toggle_hotspot(self, *, delay_frames: int = 8, force: bool = False) -> None:
        """화면1 좌하단 토글 버튼 마운트."""
        if not _ebs_hud_toggle_hotspot_enabled():
            self._destroy_toggle_layer()
            return
        if not force and bool(getattr(self._ext, "_tbs_ebs_hud_toggle_mounted", False)):
            return
        try:
            from .sim_multi_view import startup_dual_orchestration_active

            if not force and startup_dual_orchestration_active(self._ext):
                return
        except Exception:
            pass
        self._toggle_sched_token += 1
        token = self._toggle_sched_token

        def _try_mount(remaining: int) -> None:
            if token != self._toggle_sched_token:
                return
            if not _ebs_hud_toggle_hotspot_enabled():
                self._destroy_toggle_layer()
                return
            mount, slot = _resolve_toggle_mount(self._ext)
            if mount is not None:
                try:
                    self._ext._tbs_split_main_viewport_window = mount
                except Exception:
                    pass
                self._mount_toggle_hotspot(mount, slot, sched_token=token)
                return
            if remaining > 0:
                try:
                    import omni.kit.app  # type: ignore

                    kit_app = omni.kit.app.get_app()
                    if kit_app is not None:
                        kit_app.post_update(lambda: _try_mount(remaining - 1))
                        return
                except Exception:
                    pass
            print(
                f"{_PRINT_PREFIX} Viewport get_frame unavailable — toggle skipped "
                f"(remain=0). 분할 READY 후 재시도 예정.",
                flush=True,
            )

        _try_mount(max(0, int(delay_frames)))

    def toggle_ebs_hud_visibility(self) -> None:
        """좌하단 클릭 → EBS HUD 보이기/숨기기."""
        want = not _user_wants_ebs_hud_visible(self._ext)
        try:
            self._ext._tbs_ebs_hud_user_visible = bool(want)
        except Exception:
            return
        print(
            f"{_PRINT_PREFIX} toggle → HUD {'show' if want else 'hide'}",
            flush=True,
        )
        if want:
            self.sync_layers(delay_frames=0, force=True)
        else:
            self._destroy_layer()

    def _destroy_layer(self) -> None:
        self._root = None
        try:
            self._ext._tbs_ebs_hud_mounted = False
        except Exception:
            pass
        try:
            mount, slot = _resolve_ebs_hud_mount(self._ext)
            if mount is not None and callable(getattr(mount, "get_frame", None)):
                self._clear_viewport_slot(mount, slot)
            vw = _resolve_viewport_window(self._ext)
            if vw is not None and callable(getattr(vw, "get_frame", None)):
                for s in (_FRAME_SLOT, _FRAME_SLOT_WIDGET):
                    self._clear_viewport_slot(vw, s)
        except Exception:
            pass

    def _destroy_toggle_layer(self) -> None:
        self._toggle_root = None
        try:
            self._ext._tbs_ebs_hud_toggle_mounted = False
        except Exception:
            pass
        try:
            vw = _resolve_viewport_window(self._ext)
            if vw is not None and callable(getattr(vw, "get_frame", None)):
                for s in (_FRAME_SLOT_TOGGLE, _FRAME_SLOT_TOGGLE_WIDGET):
                    self._clear_viewport_slot(vw, s)
        except Exception:
            pass

    @staticmethod
    def _clear_viewport_slot(vw: Any, slot: str) -> None:
        try:
            import omni.ui as ui  # type: ignore

            with vw.get_frame(slot):
                ui.Spacer(height=0)
        except Exception:
            pass

    def _mount_on_viewport(self, mount: Any, slot: str, *, sched_token: int) -> None:
        if sched_token != self._sched_token:
            return
        try:
            import omni.ui as ui  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.ui unavailable: {exc}", flush=True)
            return

        from .ebs_control_panel_ui import build_ebs_control_panel_content

        self._clear_viewport_slot(mount, slot)
        if sched_token != self._sched_token:
            return

        widget_split = _is_widget_split_hud(self._ext)
        bg_color = 0xE6181C22
        try:
            ra = getattr(ui, "Alignment", None)
            lt = getattr(ra, "LEFT_TOP", None) if ra is not None else None
            with mount.get_frame(slot):
                outer = ui.ZStack(alignment=lt) if lt is not None else ui.ZStack()
                self._root = outer
                with outer:
                    if widget_split:
                        with ui.HStack(spacing=0):
                            self._build_panel_column(ui, bg_color, build_ebs_control_panel_content)
                            ui.Spacer(width=ui.Fraction(0.5))
                    else:
                        self._build_panel_column(ui, bg_color, build_ebs_control_panel_content)
            try:
                self._ext._tbs_ebs_hud_mounted = True
            except Exception:
                pass
            print(f"{_PRINT_PREFIX} EBS panel mounted slot={slot!r}", flush=True)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} mount failed: {exc}", flush=True)

    def _mount_toggle_hotspot(self, mount: Any, slot: str, *, sched_token: int) -> None:
        if sched_token != self._toggle_sched_token:
            return
        try:
            import omni.ui as ui  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.ui unavailable (toggle): {exc}", flush=True)
            return

        self._clear_viewport_slot(mount, slot)
        if sched_token != self._toggle_sched_token:
            return

        widget_split = _is_widget_split_hud(self._ext)
        margin = int(_TOGGLE_HOTSPOT_MARGIN)
        hw = int(_TOGGLE_HOTSPOT_W)
        hh = int(_TOGGLE_HOTSPOT_H)
        bg = int(_TOGGLE_HOTSPOT_BG)
        label = str(_TOGGLE_HOTSPOT_LABEL)

        def _on_click() -> None:
            print(f"{_PRINT_PREFIX} toggle button clicked", flush=True)
            self.toggle_ebs_hud_visibility()

        try:
            with mount.get_frame(slot):
                # 전체 뷰포트 위 ZStack — 하단 왼쪽만 버튼
                outer = ui.ZStack()
                self._toggle_root = outer
                with outer:
                    with ui.VStack():
                        ui.Spacer()  # 위쪽 공간 → 버튼을 하단으로
                        with ui.HStack(height=hh + margin):
                            if widget_split:
                                # 화면1(왼쪽 50%) 안에만 배치
                                with ui.HStack(width=ui.Fraction(0.5)):
                                    ui.Spacer(width=margin)
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
                                            },
                                            "Button:pressed": {
                                                "background_color": 0x40FFFFFF,
                                                "border_width": 0,
                                            },
                                        },
                                    )
                                    ui.Spacer()
                                ui.Spacer(width=ui.Fraction(0.5))
                            else:
                                ui.Spacer(width=margin)
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
                                        },
                                        "Button:pressed": {
                                            "background_color": 0x40FFFFFF,
                                            "border_width": 0,
                                        },
                                    },
                                )
                                ui.Spacer()
                        ui.Spacer(height=margin)
            try:
                self._ext._tbs_ebs_hud_toggle_mounted = True
            except Exception:
                pass
            print(
                f"{_PRINT_PREFIX} toggle hotspot mounted "
                f"slot={slot!r} size={hw}x{hh} widget_split={widget_split}",
                flush=True,
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} toggle mount failed: {exc}", flush=True)
            try:
                self._ext._tbs_ebs_hud_toggle_mounted = False
            except Exception:
                pass

    def _build_panel_column(self, ui: Any, bg_color: int, build_fn: Any) -> None:
        with ui.VStack(width=ui.Fraction(0.5)):
            ui.Spacer(height=_TOP_SPACER_H)
            with ui.HStack():
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
                        ui.Rectangle(style={"background_color": bg_color})
                        with ui.ScrollingFrame(
                            height=_MAX_SCROLL_H,
                            style={"ScrollingFrame": {"padding": 2, "margin": 0}},
                        ):
                            with ui.VStack(spacing=0):
                                build_fn(self._ext, compact=True)
                ui.Spacer()
            ui.Spacer()


def attach_tbs_viewport_control_hud(ext: Any) -> TbsViewportControlHud:
    """확장에 Viewport EBS HUD(+토글 버튼) 를 붙인다."""
    hud = TbsViewportControlHud(ext)
    try:
        ext._tbs_viewport_control_hud = hud
        ext._tbs_ebs_hud_mounted = False
        ext._tbs_ebs_hud_toggle_mounted = False
        ext._tbs_ebs_hud_user_visible = _ebs_hud_startup_visible()
    except Exception:
        pass
    defer = False
    try:
        from .sim_control_defaults import START_WITH_DUAL_SCREEN
        from .sim_multi_view_widget import sim_viewport_split_widget_enabled

        defer = bool(START_WITH_DUAL_SCREEN) and sim_viewport_split_widget_enabled()
    except Exception:
        defer = False
    if not defer:
        hud.sync_layers()
        hud.sync_toggle_hotspot(delay_frames=0, force=True)
    else:
        # 분할 READY 전에도 몇 프레임 뒤 재시도 (READY 콜백이 놓쳐도 복구)
        _schedule_toggle_retries(hud, attempts=40)
    return hud


def _schedule_toggle_retries(hud: TbsViewportControlHud, *, attempts: int = 40) -> None:
    """Widget 분할 기동 중 토글 마운트가 실패해도 반복 재시도."""
    box = {"n": int(attempts)}

    def _tick() -> None:
        if box["n"] <= 0:
            return
        box["n"] -= 1
        try:
            if bool(getattr(hud._ext, "_tbs_ebs_hud_toggle_mounted", False)):
                return
        except Exception:
            pass
        try:
            from .sim_multi_view import startup_dual_orchestration_active

            if startup_dual_orchestration_active(hud._ext):
                # 아직 분할 중이면 다음 프레임
                pass
            else:
                hud.sync_toggle_hotspot(delay_frames=0, force=True)
                if bool(getattr(hud._ext, "_tbs_ebs_hud_toggle_mounted", False)):
                    return
        except Exception:
            try:
                hud.sync_toggle_hotspot(delay_frames=0, force=True)
            except Exception:
                pass
        try:
            import omni.kit.app  # type: ignore

            app = omni.kit.app.get_app()
            if app is not None and box["n"] > 0:
                app.post_update(_tick)
        except Exception:
            pass

    try:
        import omni.kit.app  # type: ignore

        app = omni.kit.app.get_app()
        if app is not None:
            app.post_update(_tick)
    except Exception:
        pass


def destroy_tbs_viewport_control_hud(ext: Any) -> None:
    hud = getattr(ext, "_tbs_viewport_control_hud", None)
    if hud is not None:
        try:
            hud.destroy()
        except Exception:
            pass
    try:
        ext._tbs_viewport_control_hud = None
        ext._tbs_ebs_hud_mounted = False
        ext._tbs_ebs_hud_toggle_mounted = False
    except Exception:
        pass
