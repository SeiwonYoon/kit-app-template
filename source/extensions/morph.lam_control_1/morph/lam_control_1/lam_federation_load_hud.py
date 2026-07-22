"""Viewport 좌상단 Federation API 로딩 HUD (화면별).

단계: 요청중 → (실패 | 수신완료 → 파싱중 → 준비완료) → 재생 시작 후 자동 숨김.

표시 on/off: ``lam_sim_control_defaults.SHOW_VIEWPORT_FEDERATION_LOAD_HUD`` (기본 True).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from .kit_main_dispatch import schedule_on_main_thread

_PRINT_PREFIX = "[LAM/FedLoadHUD]"
_FRAME_SLOT_PREFIX = "morph.lam_control_1:federation_load_hud_s"

# phase → (표시 문구, 로딩바 표시, 실패 색)
_PHASE_UI = {
    "requesting": ("요청중", True, False),
    "failed": ("요청 실패", False, True),
    "received": ("수신 완료", False, False),
    "parsing": ("파싱중", True, False),
    "ready": ("준비완료", False, False),
    "playing": ("재생 시작", False, False),
}

_PANEL_W = 168
_BAR_H = 6
_PAD = 8
_TOP = 10
_LEFT = 10

_lock = threading.RLock()
_panels: Dict[int, "_FedLoadPanel"] = {}
_anim_token = 0


def federation_load_hud_enabled() -> bool:
    try:
        from .lam_sim_control_defaults import SHOW_VIEWPORT_FEDERATION_LOAD_HUD

        return bool(SHOW_VIEWPORT_FEDERATION_LOAD_HUD)
    except Exception:
        return True


def set_federation_load_status(
    screen: int,
    phase: str,
    *,
    detail: str = "",
    ext: Any = None,
    lam_window: Any = None,
) -> None:
    """화면별 Federation 로딩 상태 갱신 (워커 스레드에서도 호출 가능)."""
    si = max(1, int(screen))
    ph = str(phase or "").strip().lower()
    if ph not in _PHASE_UI:
        return
    kit_ext = ext
    if kit_ext is None and lam_window is not None:
        kit_ext = getattr(lam_window, "_kit_ext", None)

    def _apply() -> None:
        if not federation_load_hud_enabled():
            hide_federation_load_hud(si)
            return
        panel = _ensure_panel(si, kit_ext=kit_ext, lam_window=lam_window)
        if panel is None:
            return
        panel.set_phase(ph, detail=str(detail or ""))

    schedule_on_main_thread(_apply)


def hide_federation_load_hud(screen: Optional[int] = None) -> None:
    """특정 화면 또는 전체 HUD 숨김."""

    def _apply() -> None:
        with _lock:
            if screen is None:
                targets = list(_panels.keys())
            else:
                targets = [max(1, int(screen))]
            for si in targets:
                panel = _panels.pop(si, None)
                if panel is not None:
                    try:
                        panel.destroy()
                    except Exception:
                        pass

    schedule_on_main_thread(_apply)


def _ensure_panel(
    screen: int,
    *,
    kit_ext: Any = None,
    lam_window: Any = None,
) -> Optional["_FedLoadPanel"]:
    si = max(1, int(screen))
    with _lock:
        panel = _panels.get(si)
        if panel is not None:
            return panel
        panel = _FedLoadPanel(si, kit_ext=kit_ext, lam_window=lam_window)
        if not panel.mount():
            return None
        _panels[si] = panel
        return panel


def _resolve_viewport_window(
    screen: int,
    *,
    kit_ext: Any = None,
    lam_window: Any = None,
) -> Any:
    ext = kit_ext
    if ext is None and lam_window is not None:
        ext = getattr(lam_window, "_kit_ext", None)
    main_vp = None
    if lam_window is not None:
        main_vp = getattr(lam_window, "_viewport", None)
    try:
        from .lam_csv_play_screen import resolve_viewport_window_for_screen

        return resolve_viewport_window_for_screen(
            ext, screen, main_viewport=main_vp
        )
    except Exception:
        return None


class _FedLoadPanel:
    def __init__(
        self,
        screen: int,
        *,
        kit_ext: Any = None,
        lam_window: Any = None,
    ) -> None:
        self.screen = max(1, int(screen))
        self._kit_ext = kit_ext
        self._lam_window = lam_window
        self._mounted_vw: Any = None
        self._root: Any = None
        self._label: Any = None
        self._detail_label: Any = None
        self._progress: Any = None
        self._progress_model: Any = None
        self._bar_frame: Any = None
        self._phase = ""
        self._anim_active = False
        self._hide_token = 0
        self._post_sub: Any = None

    def destroy(self) -> None:
        self._stop_anim()
        self._root = None
        self._label = None
        self._detail_label = None
        self._progress = None
        self._progress_model = None
        self._bar_frame = None
        vw = self._mounted_vw
        self._mounted_vw = None
        if vw is None:
            return
        try:
            slot = f"{_FRAME_SLOT_PREFIX}{self.screen}"
            if callable(getattr(vw, "get_frame", None)):
                with vw.get_frame(slot):
                    pass
        except Exception:
            pass

    def mount(self) -> bool:
        try:
            import omni.ui as ui  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.ui unavailable: {exc}", flush=True)
            return False
        vw = _resolve_viewport_window(
            self.screen, kit_ext=self._kit_ext, lam_window=self._lam_window
        )
        if vw is None or not callable(getattr(vw, "get_frame", None)):
            print(
                f"{_PRINT_PREFIX} screen{self.screen} viewport get_frame missing",
                flush=True,
            )
            return False
        self.destroy()
        self._mounted_vw = vw
        slot = f"{_FRAME_SLOT_PREFIX}{self.screen}"
        fail_color = 0xFFE06060
        ok_color = 0xFFE8EEF5
        bg = 0xE6181C22
        border = 0xFF5A6A80
        try:
            ra = getattr(ui, "Alignment", None)
            lt = getattr(ra, "LEFT_TOP", None) if ra is not None else None
            with vw.get_frame(slot):
                root = ui.ZStack(alignment=lt) if lt is not None else ui.ZStack()
                self._root = root
                with root:
                    with ui.VStack():
                        ui.Spacer(height=_TOP)
                        with ui.HStack():
                            ui.Spacer(width=_LEFT)
                            with ui.Frame(
                                width=_PANEL_W,
                                style={
                                    "background_color": bg,
                                    "border_width": 1,
                                    "border_color": border,
                                    "border_radius": 4,
                                    "padding": _PAD,
                                },
                            ):
                                with ui.VStack(spacing=4):
                                    self._label = ui.Label(
                                        "요청중",
                                        height=18,
                                        style={
                                            "color": ok_color,
                                            "font_size": 13,
                                        },
                                    )
                                    bar_w = _PANEL_W - (_PAD * 2)
                                    with ui.ZStack(height=_BAR_H, width=bar_w):
                                        self._bar_frame = ui.Frame(
                                            height=_BAR_H,
                                            width=bar_w,
                                            style={
                                                "background_color": 0xFF2A3340,
                                                "border_radius": 2,
                                            },
                                            visible=True,
                                        )
                                        try:
                                            from omni.ui import (  # type: ignore
                                                SimpleFloatModel,
                                            )

                                            self._progress_model = SimpleFloatModel(
                                                0.15
                                            )
                                            self._progress = ui.ProgressBar(
                                                model=self._progress_model,
                                                height=_BAR_H,
                                                width=bar_w,
                                            )
                                            try:
                                                self._bar_frame.visible = False
                                            except Exception:
                                                pass
                                        except Exception:
                                            self._progress = None
                                            self._progress_model = None
                                    self._detail_label = ui.Label(
                                        "",
                                        height=14,
                                        style={
                                            "color": 0xFF9AA6B2,
                                            "font_size": 11,
                                        },
                                        visible=False,
                                    )
            # fail_color kept for set_phase
            self._fail_color = fail_color
            self._ok_color = ok_color
            return True
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} screen{self.screen} mount failed: {exc}",
                flush=True,
            )
            self.destroy()
            return False

    def set_phase(self, phase: str, *, detail: str = "") -> None:
        if self._label is None and not self.mount():
            return
        self._phase = phase
        text, show_bar, is_fail = _PHASE_UI.get(phase, ("", False, False))
        if detail and phase == "failed":
            # 짧은 실패 사유
            short = detail.strip()
            if len(short) > 42:
                short = short[:39] + "..."
            text = f"실패: {short}" if short else text
        try:
            self._label.text = text
            color = self._fail_color if is_fail else self._ok_color
            self._label.style = {"color": int(color), "font_size": 13}
        except Exception:
            pass
        if self._detail_label is not None:
            try:
                if detail and phase != "failed":
                    self._detail_label.text = detail[:48]
                    self._detail_label.visible = True
                else:
                    self._detail_label.visible = False
            except Exception:
                pass
        self._set_bar_visible(bool(show_bar))
        if show_bar:
            self._start_anim()
        else:
            self._stop_anim()
            if self._progress_model is not None:
                try:
                    self._progress_model.set_value(1.0 if phase in ("ready", "received", "playing") else 0.0)
                except Exception:
                    pass
        if phase == "playing":
            self._schedule_auto_hide(1.2)
        elif phase == "failed":
            # 실패는 유지 (다음 Federation 요청 시 덮어씀)
            self._hide_token += 1
        else:
            self._hide_token += 1

    def _set_bar_visible(self, visible: bool) -> None:
        for w in (self._progress, self._bar_frame):
            if w is None:
                continue
            try:
                w.visible = bool(visible)
            except Exception:
                pass
        # ProgressBar 와 Frame 바가 둘 다 있으면 Progress 우선
        if visible and self._progress is not None and self._bar_frame is not None:
            try:
                self._bar_frame.visible = False
            except Exception:
                pass

    def _start_anim(self) -> None:
        global _anim_token
        self._anim_active = True
        _anim_token += 1
        token = _anim_token
        t0 = time.perf_counter()

        def _tick(dt: float = 0.0) -> None:  # noqa: ARG001
            if not self._anim_active or token != _anim_token:
                return
            if self._progress_model is None:
                return
            try:
                # 0.08~0.92 왕복
                elapsed = time.perf_counter() - t0
                v = 0.08 + 0.84 * abs((elapsed % 1.6) / 0.8 - 1.0)
                self._progress_model.set_value(float(v))
            except Exception:
                return
            try:
                import omni.kit.app  # type: ignore

                app = omni.kit.app.get_app()
                if app is not None:
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

    def _stop_anim(self) -> None:
        self._anim_active = False

    def _schedule_auto_hide(self, delay_sec: float) -> None:
        self._hide_token += 1
        token = self._hide_token
        si = self.screen

        def _hide() -> None:
            time.sleep(max(0.05, float(delay_sec)))
            if token != self._hide_token:
                return

            def _on_main() -> None:
                with _lock:
                    panel = _panels.get(si)
                    if panel is self and token == self._hide_token:
                        _panels.pop(si, None)
                        try:
                            self.destroy()
                        except Exception:
                            pass

            schedule_on_main_thread(_on_main)

        threading.Thread(
            target=_hide,
            name=f"lam-fed-hud-hide-s{si}",
            daemon=True,
        ).start()
