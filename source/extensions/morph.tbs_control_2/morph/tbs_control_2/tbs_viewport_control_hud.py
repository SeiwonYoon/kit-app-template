"""Viewport 좌측 상단 EBS 미니 제어 패널 (2D 오버레이)."""

from __future__ import annotations

from typing import Any, Optional

_PRINT_PREFIX = "[TBS/EBS-HUD]"


def _viewport_control_hud_enabled() -> bool:
    try:
        from .sim_control_defaults import SHOW_VIEWPORT_EBS_CONTROL_HUD

        return bool(SHOW_VIEWPORT_EBS_CONTROL_HUD)
    except Exception:
        return True

_FRAME_SLOT = "morph.tbs_control_2:ebs_control_hud"
_PANEL_W = 300
_PANEL_PAD = 8
_TOP_SPACER_H = 12
_MAX_SCROLL_H = 520


def _resolve_viewport_window(ext: Any) -> Optional[Any]:
    """``get_frame`` 을 제공하는 ViewportWindow."""
    try:
        vw = getattr(ext, "_tbs_split_main_viewport_window", None)
        if vw is not None and callable(getattr(vw, "get_frame", None)):
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
    return None


class TbsViewportControlHud:
    """Viewport 좌측 상단 — EBS 시뮬 제어 패널."""

    def __init__(self, ext: Any) -> None:
        self._ext = ext
        self._root: Any = None
        self._sched_token: int = 0

    def destroy(self) -> None:
        self._destroy_layer()

    def sync_layers(self, *, delay_frames: int = 8) -> None:
        if not _viewport_control_hud_enabled():
            self._destroy_layer()
            return
        self._sched_token += 1
        token = self._sched_token

        def _try_mount(remaining: int) -> None:
            if token != self._sched_token:
                return
            vw = _resolve_viewport_window(self._ext)
            if vw is not None:
                try:
                    self._ext._tbs_split_main_viewport_window = vw
                except Exception:
                    pass
                self._mount_on_viewport(vw)
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

    def _destroy_layer(self) -> None:
        self._root = None
        try:
            vw = _resolve_viewport_window(self._ext)
            if vw is None:
                return
            if callable(getattr(vw, "get_frame", None)):
                with vw.get_frame(_FRAME_SLOT):
                    pass
        except Exception:
            pass

    def _mount_on_viewport(self, vw: Any) -> None:
        try:
            import omni.ui as ui  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.ui unavailable: {exc}", flush=True)
            return

        from .ebs_control_panel_ui import build_ebs_control_panel_content

        self._destroy_layer()
        try:
            ra = getattr(ui, "Alignment", None)
            lt = getattr(ra, "LEFT_TOP", None) if ra is not None else None
            with vw.get_frame(_FRAME_SLOT):
                root = ui.ZStack(alignment=lt) if lt is not None else ui.ZStack()
                self._root = root
                with root:
                    with ui.VStack():
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
                                    ui.Rectangle(style={"background_color": 0xE6181C22})
                                    with ui.ScrollingFrame(
                                        height=_MAX_SCROLL_H,
                                        style={"ScrollingFrame": {"padding": 2, "margin": 0}},
                                    ):
                                        with ui.VStack(spacing=0):
                                            build_ebs_control_panel_content(self._ext, compact=True)
                            ui.Spacer()
                        ui.Spacer()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} mount failed: {exc}", flush=True)


def attach_tbs_viewport_control_hud(ext: Any) -> TbsViewportControlHud:
    """확장에 Viewport EBS HUD 를 붙이고 즉시 sync 를 예약한다."""
    hud = TbsViewportControlHud(ext)
    try:
        ext._tbs_viewport_control_hud = hud
    except Exception:
        pass
    hud.sync_layers()
    return hud


def destroy_tbs_viewport_control_hud(ext: Any) -> None:
    hud = getattr(ext, "_tbs_viewport_control_hud", None)
    if hud is not None:
        try:
            hud.destroy()
        except Exception:
            pass
    try:
        ext._tbs_viewport_control_hud = None
    except Exception:
        pass
