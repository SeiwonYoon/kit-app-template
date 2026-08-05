"""Viewport 우하단 장비배치도 패널 (화면별).

시뮬 재생창 2D 평면도(``lam_equipment_floorplan_ui``)와 동일 occupancy 스냅샷을 쓴다.
체크박스: ``get_toggle_floorplan_panel`` / CSV HUD ``_floorplan_show_model``.
앱 시작 기본: ``lam_sim_control_defaults.STARTUP_CHECK_FLOORPLAN_PANEL`` (기본 False).
기능 마스터: ``SHOW_VIEWPORT_FLOORPLAN_PANEL``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, TYPE_CHECKING

from .lam_viewport_overlay_state import get_toggle_floorplan_panel

if TYPE_CHECKING:
    from .lam_viewport import LamViewport
    from .simulation_play import LamSimulationCsvPlayWindow

_PRINT_PREFIX = "[LAM/FloorplanPanel]"
_FRAME_SLOT_BASE = "morph.lam_control_1:floorplan_panel"
_PANEL_W = 300
_PANEL_H = 168
_PANEL_PAD = 6
_TITLE_H = 16
_TITLE_GAP = 3
_RIGHT_SPACER_W = 4
# 제목 + 갭 + 평면도 — HStack 고정 높이 (상단 Spacer 가 나머지 공간을 밀어냄)
_CONTENT_H = int(_TITLE_H + _TITLE_GAP + _PANEL_H + _PANEL_PAD * 2)

_ACTIVE_PANEL_BY_SCREEN: Dict[int, "LamViewportFloorplanPanel"] = {}
# 창 표시 체크박스 런타임 오버라이드. None → defaults.SHOW_VIEWPORT_FLOORPLAN_PANEL
_FLOORPLAN_FEATURE_RT: Optional[bool] = None


def set_viewport_floorplan_panel_feature_rt(enabled: bool) -> None:
    """창 표시 「장비배치도」 — SHOW_VIEWPORT_FLOORPLAN_PANEL 런타임 표시 여부."""
    global _FLOORPLAN_FEATURE_RT
    _FLOORPLAN_FEATURE_RT = bool(enabled)


def viewport_floorplan_panel_feature_enabled() -> bool:
    if _FLOORPLAN_FEATURE_RT is not None:
        return bool(_FLOORPLAN_FEATURE_RT)
    try:
        from .lam_sim_control_defaults import SHOW_VIEWPORT_FLOORPLAN_PANEL

        return bool(SHOW_VIEWPORT_FLOORPLAN_PANEL)
    except Exception:
        return True


def _frame_slot_for_screen(screen: int) -> str:
    si = max(1, int(screen))
    return f"{_FRAME_SLOT_BASE}:s{si}"


def force_remove_floorplan_panels(*, screen: Optional[int] = None) -> None:
    """``screen`` 지정 시 해당 화면만. ``None`` 이면 전체."""
    if screen is not None:
        si = max(1, int(screen))
        inst = _ACTIVE_PANEL_BY_SCREEN.pop(si, None)
        if inst is not None:
            try:
                inst.destroy()
            except Exception:
                pass
        return
    for si, inst in list(_ACTIVE_PANEL_BY_SCREEN.items()):
        try:
            inst.destroy()
        except Exception:
            pass
        _ACTIVE_PANEL_BY_SCREEN.pop(si, None)


def refresh_floorplan_panels_ui(*, screen: Optional[int] = None) -> None:
    """점유 변경 직후 Viewport 배치도 in-place 갱신 (메인 스레드 post_update)."""
    if screen is not None:
        targets = [_ACTIVE_PANEL_BY_SCREEN.get(int(screen))]
    else:
        targets = list(_ACTIVE_PANEL_BY_SCREEN.values())

    def _ui() -> None:
        for inst in targets:
            if inst is None:
                continue
            try:
                if not inst._toggle_on():
                    continue
                if getattr(inst, "_floorplan_handle", None) is None:
                    # Play 중 토글 ON 인데 mount 유실 → 재동기
                    inst.sync_layers(delay_frames=0)
                else:
                    inst._apply_occ_now(force=True)
            except Exception:
                pass

    try:
        import omni.kit.app as kapp  # type: ignore

        app = kapp.get_app()
        if app is not None:
            app.post_update(_ui)
            return
    except Exception:
        pass
    try:
        _ui()
    except Exception:
        pass


def _resolve_viewport_window(viewport: Optional["LamViewport"]) -> Optional[Any]:
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
    return None


def _read_bool_model(m: Any) -> bool:
    if m is None:
        return False
    for attr in ("get_value_as_bool", "as_bool", "get_value"):
        try:
            fn = getattr(m, attr, None)
            if callable(fn):
                return bool(fn())
        except Exception:
            continue
    return False


class LamViewportFloorplanPanel:
    """화면별 Viewport 우하단 장비배치도 (2D occupancy UI)."""

    def __init__(
        self,
        csv_window: "LamSimulationCsvPlayWindow",
        *,
        viewport: Optional["LamViewport"] = None,
        screen: Optional[int] = None,
    ) -> None:
        self._csv = csv_window
        self._screen = max(
            1,
            int(screen if screen is not None else getattr(csv_window, "_screen", 1) or 1),
        )
        self._viewport = viewport
        self._viewport_window: Any = None
        self._mounted_window: Any = None
        self._root: Any = None
        self._floorplan_handle: Optional[Dict[str, Any]] = None
        self._frame_slot = _frame_slot_for_screen(self._screen)
        self._sync_token: float = 0.0
        self._post_update_sub: Any = None
        self._last_occ_rev: int = -1
        self._last_tick: float = 0.0
        _ACTIVE_PANEL_BY_SCREEN[self._screen] = self

    def _toggle_on(self) -> bool:
        if not viewport_floorplan_panel_feature_enabled():
            return False
        if self._screen <= 1:
            return bool(get_toggle_floorplan_panel())
        return _read_bool_model(getattr(self._csv, "_floorplan_show_model", None))

    def _resolve_viewport_for_panel(self) -> Optional[Any]:
        if self._screen > 1:
            lam = getattr(self._csv, "_lam_window_ref", None)
            ext = getattr(lam, "_kit_ext", None) if lam is not None else None
            if ext is not None:
                try:
                    from .lam_csv_play_screen import resolve_viewport_window_for_screen

                    vw = resolve_viewport_window_for_screen(
                        ext,
                        self._screen,
                        main_viewport=self._viewport,
                    )
                    if vw is not None and callable(getattr(vw, "get_frame", None)):
                        self._viewport_window = vw
                        return vw
                except Exception:
                    pass
            cached = getattr(self, "_viewport_window", None)
            if cached is not None and callable(getattr(cached, "get_frame", None)):
                return cached
            return None
        cached = getattr(self, "_viewport_window", None)
        if cached is not None and callable(getattr(cached, "get_frame", None)):
            return cached
        return _resolve_viewport_window(self._viewport)

    def destroy(self) -> None:
        self._sync_token = float(time.time())
        self._stop_poll()
        self._unbind_floorplan()
        self._destroy_layer()
        self._mounted_window = None
        cur = _ACTIVE_PANEL_BY_SCREEN.get(self._screen)
        if cur is self:
            _ACTIVE_PANEL_BY_SCREEN.pop(self._screen, None)

    def sync_layers(self, *, delay_frames: int = 8) -> None:
        if not self._toggle_on():
            self.destroy()
            return
        _ACTIVE_PANEL_BY_SCREEN[self._screen] = self

        # 이미 같은 viewport 에 mount 됨 → remount 금지 (위젯·구독 끊김 방지)
        try:
            current = self._resolve_viewport_for_panel()
            if (
                self._root is not None
                and self._floorplan_handle is not None
                and self._mounted_window is not None
                and current is not None
                and current is self._mounted_window
            ):
                self._apply_occ_now(force=True)
                self._ensure_poll()
                return
        except Exception:
            pass

        token = float(time.time())
        self._sync_token = token

        def _try(remaining: int) -> None:
            if self._sync_token != token:
                return
            if not self._toggle_on():
                self.destroy()
                return
            vw = self._resolve_viewport_for_panel()
            if vw is not None:
                self._mount_on_viewport(vw)
                return
            if remaining > 0:
                try:
                    import omni.kit.app as kapp  # type: ignore

                    app = kapp.get_app()
                    if app is not None:
                        app.post_update(lambda: _try(remaining - 1))
                        return
                except Exception:
                    pass

        _try(max(0, int(delay_frames)))

    def _unbind_floorplan(self) -> None:
        handle = self._floorplan_handle
        self._floorplan_handle = None
        if not handle:
            return
        try:
            from .lam_equipment_floorplan_ui import unbind_floorplan_occupancy_ui

            unbind_floorplan_occupancy_ui(handle)
        except Exception:
            pass

    def _destroy_layer(self) -> None:
        root = self._root
        self._root = None
        try:
            if root is not None:
                root.visible = False
        except Exception:
            pass
        try:
            vw = self._mounted_window or self._resolve_viewport_for_panel()
            self._clear_viewport_slot(vw, self._frame_slot)
        except Exception:
            pass

    @staticmethod
    def _clear_viewport_slot(vw: Any, slot: str) -> None:
        if vw is None or not callable(getattr(vw, "get_frame", None)):
            return
        try:
            import omni.ui as ui  # type: ignore

            with vw.get_frame(slot):
                ui.Spacer(height=0)
        except Exception:
            pass

    def _occ_revision(self) -> int:
        try:
            from .lam_floorplan_occupancy import get_floorplan_occupancy

            return int(get_floorplan_occupancy(self._screen).revision)
        except Exception:
            return -1

    def _apply_occ_now(self, *, force: bool = False) -> None:
        handle = self._floorplan_handle
        if not handle:
            return
        rev = self._occ_revision()
        if not force and rev == self._last_occ_rev:
            return
        try:
            from .lam_equipment_floorplan_ui import apply_floorplan_occupancy_snapshot
            from .lam_floorplan_occupancy import get_floorplan_occupancy

            snap = get_floorplan_occupancy(self._screen).snapshot()
            apply_floorplan_occupancy_snapshot(handle, snap)
            self._last_occ_rev = rev
            # 간헐 진단 — wafer 이동이 반영되는지 확인용
            n = sum(len(v) for v in snap.values() if v)
            if force and n > 0:
                print(
                    f"{_PRINT_PREFIX} occ apply screen{self._screen} "
                    f"rev={rev} occupied_labels={n}",
                    flush=True,
                )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} occ apply fail screen{self._screen}: {exc}", flush=True)

    def _stop_poll(self) -> None:
        sub = self._post_update_sub
        self._post_update_sub = None
        if sub is None:
            return
        try:
            sub.unsubscribe()
        except Exception:
            try:
                sub = None
            except Exception:
                pass

    def _ensure_poll(self) -> None:
        """시뮬창과 달리 viewport get_frame 위젯은 subscribe 만으로 갱신이 끊길 수 있어 폴링."""
        if self._post_update_sub is not None:
            return
        try:
            import omni.kit.app as kapp  # type: ignore

            app = kapp.get_app()
            if app is None:
                return
            stream = app.get_update_event_stream()
        except Exception:
            return

        def _on(_e: Any = None) -> None:
            if not self._toggle_on():
                return
            if self._floorplan_handle is None:
                return
            now = time.time()
            # 과도한 UI set_style 방지 — revision 변할 때만 (최소 간격도 둠)
            if now - self._last_tick < 0.05:
                return
            self._last_tick = now
            self._apply_occ_now(force=False)

        try:
            self._post_update_sub = stream.create_subscription_to_pop(
                _on, name=f"morph.lam_control_1:floorplan_panel:s{self._screen}"
            )
        except Exception:
            self._post_update_sub = None

    def _mount_on_viewport(self, vw: Any) -> None:
        try:
            import omni.ui as ui  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.ui unavailable: {exc}", flush=True)
            return

        self._stop_poll()
        self._unbind_floorplan()
        self._destroy_layer()
        self._viewport_window = vw
        self._mounted_window = vw
        self._last_occ_rev = -1

        try:
            from .lam_equipment_floorplan_ui import (
                bind_floorplan_occupancy_ui,
                build_equipment_floorplan_ui,
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} floorplan import: {exc}", flush=True)
            return

        content_h = float(_CONTENT_H)
        try:
            # 상단 Spacer 가 남는 높이를 먹고 → 콘텐츠는 Viewport 맨 아래(우)에 flush
            with vw.get_frame(self._frame_slot):
                root = ui.ZStack()
                self._root = root
                with root:
                    with ui.VStack():
                        ui.Spacer()
                        with ui.HStack(height=content_h):
                            ui.Spacer()
                            with ui.Frame(
                                width=_PANEL_W,
                                height=content_h,
                                style={
                                    "border_width": 1,
                                    "border_color": 0xFF5A6A80,
                                    "border_radius": 4,
                                    "padding": _PANEL_PAD,
                                    "background_color": 0xE6181C22,
                                },
                            ):
                                with ui.VStack(spacing=_TITLE_GAP):
                                    ui.Label(
                                        f"장비 배치도 (화면{self._screen})",
                                        height=_TITLE_H,
                                        style={"font_size": 12, "color": 0xFFE8EEF8},
                                    )
                                    handle = build_equipment_floorplan_ui(
                                        ui,
                                        height=float(_PANEL_H),
                                        width=float(_PANEL_W - _PANEL_PAD * 2),
                                    )
                                    self._floorplan_handle = handle
                                    bind_floorplan_occupancy_ui(
                                        handle, screen=self._screen
                                    )
                            ui.Spacer(width=_RIGHT_SPACER_W)
                        # 하단 여백 없음 — Viewport 최하단에 flush

            self._apply_occ_now(force=True)
            self._ensure_poll()
            print(
                f"{_PRINT_PREFIX} mounted screen{self._screen} "
                f"slot={self._frame_slot} flush=bottom-right",
                flush=True,
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} mount failed screen{self._screen}: {exc}", flush=True)
            self._stop_poll()
            self._unbind_floorplan()
            self._root = None
            self._mounted_window = None


__all__ = [
    "LamViewportFloorplanPanel",
    "force_remove_floorplan_panels",
    "refresh_floorplan_panels_ui",
    "set_viewport_floorplan_panel_feature_rt",
    "viewport_floorplan_panel_feature_enabled",
]
