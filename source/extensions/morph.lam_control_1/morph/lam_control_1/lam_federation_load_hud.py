"""Viewport 좌상단 Federation API 로딩 HUD (화면별).

요청~준비완료까지 ``Loading data... N%`` 단일 칩 UI.
아이콘: ``data/img/ic_loading.png`` 를 중심 기준으로 계속 회전 (ByteImageProvider).
양쪽 화면이 ready(100%) 후 재생되면 HUD 즉시 숨김(별도 '재생시작' 문구 없음).

표시 on/off: ``lam_sim_control_defaults.SHOW_VIEWPORT_FEDERATION_LOAD_HUD``.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .kit_main_dispatch import schedule_on_main_thread

_PRINT_PREFIX = "[LAM/FedLoadHUD]"
_FRAME_SLOT_PREFIX = "morph.lam_control_1:federation_load_hud_s"

# phase → 목표 % (실제 표시는 목표를 향해 증가)
_PHASE_TARGET_PCT: Dict[str, int] = {
    "requesting": 8,
    "received": 40,
    "parsing": 78,
    "ready": 100,
    "playing": 100,
    "failed": -1,
}

_PANEL_W = 182
_PANEL_H = 34
_TOP = 10
_LEFT = 10
_PAD_LEFT = 14
_ICON_W = 15
_ICON_H = 16
_GAP_ICON_TEXT = 7
# rgba(48, 47, 64, 0.5) → omni.ui ARGB
_BG_ARGB = 0x80302F40
_TEXT_ARGB = 0xFFE8EEF5
_FAIL_ARGB = 0xFFE06060
_SPIN_DEG_PER_SEC = 360.0  # 1초에 1바퀴

_lock = threading.RLock()
_panels: Dict[int, "_FedLoadPanel"] = {}
_anim_token = 0


def federation_load_hud_enabled() -> bool:
    try:
        from .lam_sim_control_defaults import SHOW_VIEWPORT_FEDERATION_LOAD_HUD

        return bool(SHOW_VIEWPORT_FEDERATION_LOAD_HUD)
    except Exception:
        return True


def _loading_icon_path() -> Optional[Path]:
    """``data/img/ic_loading.png``."""
    try:
        from .lam_data_paths import extension_data_root

        img_dir = extension_data_root() / "img"
    except Exception:
        img_dir = Path(__file__).resolve().parent.parent.parent / "data" / "img"
    p = img_dir / "ic_loading.png"
    return p if p.is_file() else None


def _load_rgba_rgba_pil(path: Path) -> Optional[Tuple[Any, int, int]]:
    """PIL Image RGBA + size. 실패 시 None."""
    try:
        from PIL import Image  # type: ignore

        im = Image.open(str(path)).convert("RGBA")
        # 표시 슬롯에 맞춤 (중심 회전 시 캔버스 유지)
        if im.size != (_ICON_W, _ICON_H):
            im = im.resize((_ICON_W, _ICON_H), Image.Resampling.LANCZOS)
        return im, int(im.size[0]), int(im.size[1])
    except Exception as exc:
        print(f"{_PRINT_PREFIX} load icon failed: {exc}", flush=True)
        return None


def _rotate_rgba_bytes(base_im: Any, angle_deg: float) -> Tuple[List[int], int, int]:
    """중심 회전 후 RGBA byte list (expand=False, 동일 크기)."""
    from PIL import Image  # type: ignore

    rotated = base_im.rotate(
        float(angle_deg),
        resample=Image.Resampling.BILINEAR,
        expand=False,
        center=(base_im.size[0] * 0.5, base_im.size[1] * 0.5),
    )
    w, h = rotated.size
    return list(rotated.tobytes()), int(w), int(h)


def set_federation_load_status(
    screen: int,
    phase: str,
    *,
    detail: str = "",
    ext: Any = None,
    lam_window: Any = None,
    progress_pct: Optional[float] = None,
) -> None:
    """화면별 Federation 로딩 상태 갱신 (워커 스레드에서도 호출 가능).

    ``progress_pct`` 가 있으면 0~100 으로 직접 반영(단계 목표보다 우선).
    """
    si = max(1, int(screen))
    ph = str(phase or "").strip().lower()
    if ph not in _PHASE_TARGET_PCT:
        return
    kit_ext = ext
    if kit_ext is None and lam_window is not None:
        kit_ext = getattr(lam_window, "_kit_ext", None)
    pct_override = None
    if progress_pct is not None:
        try:
            pct_override = max(0.0, min(100.0, float(progress_pct)))
        except Exception:
            pct_override = None

    def _apply() -> None:
        if not federation_load_hud_enabled():
            hide_federation_load_hud(si)
            return
        if ph == "playing":
            with _lock:
                panel = _panels.pop(si, None)
            if panel is not None:
                try:
                    panel.destroy()
                except Exception:
                    pass
            return
        panel = _ensure_panel(si, kit_ext=kit_ext, lam_window=lam_window)
        if panel is None:
            return
        panel.set_phase(ph, detail=str(detail or ""), progress_pct=pct_override)

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
        self._icon: Any = None
        self._icon_provider: Any = None
        self._base_im: Any = None
        self._angle_deg = 0.0
        self._phase = ""
        self._display_pct = 0.0
        self._target_pct = 0.0
        self._failed = False
        self._fail_detail = ""
        self._anim_active = False
        self._hide_token = 0
        self._spin_enabled = False

    def destroy(self) -> None:
        self._stop_anim()
        self._root = None
        self._label = None
        self._icon = None
        self._icon_provider = None
        self._base_im = None
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

    def _push_rotated_icon(self, angle_deg: float) -> None:
        if self._base_im is None or self._icon_provider is None:
            return
        try:
            data, w, h = _rotate_rgba_bytes(self._base_im, angle_deg)
            # Kit API 변형 대응
            if hasattr(self._icon_provider, "set_bytes_data"):
                self._icon_provider.set_bytes_data(data, [w, h])
            elif hasattr(self._icon_provider, "set_data"):
                self._icon_provider.set_data(data, [w, h])
            elif hasattr(self._icon_provider, "set_bytes_data_from_gpu"):
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
        icon_path = _loading_icon_path()
        loaded = _load_rgba_rgba_pil(icon_path) if icon_path is not None else None
        if loaded is not None:
            self._base_im, _, _ = loaded
        slot = f"{_FRAME_SLOT_PREFIX}{self.screen}"
        try:
            ra = getattr(ui, "Alignment", None)
            lt = getattr(ra, "LEFT_TOP", None) if ra is not None else None
            vc = getattr(ra, "LEFT_CENTER", None) if ra is not None else None
            with vw.get_frame(slot):
                root = ui.ZStack(alignment=lt) if lt is not None else ui.ZStack()
                self._root = root
                with root:
                    with ui.VStack():
                        ui.Spacer(height=_TOP)
                        with ui.HStack(height=_PANEL_H):
                            ui.Spacer(width=_LEFT)
                            with ui.Frame(
                                width=_PANEL_W,
                                height=_PANEL_H,
                                style={
                                    "background_color": _BG_ARGB,
                                    "border_radius": 4,
                                    "border_width": 0,
                                },
                            ):
                                row = (
                                    ui.HStack(height=_PANEL_H, alignment=vc)
                                    if vc is not None
                                    else ui.HStack(height=_PANEL_H)
                                )
                                with row:
                                    ui.Spacer(width=_PAD_LEFT)
                                    self._icon = None
                                    self._icon_provider = None
                                    if self._base_im is not None:
                                        try:
                                            provider_cls = getattr(
                                                ui, "ByteImageProvider", None
                                            )
                                            with_prov = getattr(
                                                ui, "ImageWithProvider", None
                                            )
                                            if provider_cls is not None and with_prov is not None:
                                                self._icon_provider = provider_cls()
                                                self._push_rotated_icon(0.0)
                                                self._icon = with_prov(
                                                    self._icon_provider,
                                                    width=_ICON_W,
                                                    height=_ICON_H,
                                                )
                                                self._spin_enabled = True
                                        except Exception as exc:
                                            print(
                                                f"{_PRINT_PREFIX} ByteImageProvider fail: {exc}",
                                                flush=True,
                                            )
                                            self._icon_provider = None
                                    if self._icon is None and icon_path is not None:
                                        # 폴백: 정적 이미지 (회전 불가)
                                        try:
                                            self._icon = ui.Image(
                                                str(icon_path),
                                                width=_ICON_W,
                                                height=_ICON_H,
                                            )
                                        except Exception:
                                            self._icon = ui.Image(
                                                width=_ICON_W,
                                                height=_ICON_H,
                                                style={
                                                    "image_url": str(icon_path)
                                                },
                                            )
                                        self._spin_enabled = False
                                    if self._icon is None:
                                        self._icon = ui.Label(
                                            "●",
                                            width=_ICON_W,
                                            height=_ICON_H,
                                            style={
                                                "color": _TEXT_ARGB,
                                                "font_size": 12,
                                            },
                                        )
                                        self._spin_enabled = False
                                    ui.Spacer(width=_GAP_ICON_TEXT)
                                    self._label = ui.Label(
                                        "Loading data... 0%",
                                        height=_PANEL_H,
                                        style={
                                            "color": _TEXT_ARGB,
                                            "font_size": 13,
                                        },
                                    )
                                    ui.Spacer()
            self._display_pct = 0.0
            self._target_pct = 0.0
            self._failed = False
            self._angle_deg = 0.0
            return True
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} screen{self.screen} mount failed: {exc}",
                flush=True,
            )
            self.destroy()
            return False

    def set_phase(
        self,
        phase: str,
        *,
        detail: str = "",
        progress_pct: Optional[float] = None,
    ) -> None:
        if self._label is None and not self.mount():
            return
        self._phase = phase
        if phase == "failed":
            self._failed = True
            short = (detail or "").strip()
            if len(short) > 28:
                short = short[:25] + "..."
            self._fail_detail = short
            self._target_pct = float(self._display_pct)
            self._stop_anim()
            self._refresh_label()
            try:
                self._label.style = {"color": _FAIL_ARGB, "font_size": 13}
            except Exception:
                pass
            return

        self._failed = False
        try:
            self._label.style = {"color": _TEXT_ARGB, "font_size": 13}
        except Exception:
            pass
        if progress_pct is not None:
            self._target_pct = float(progress_pct)
            self._display_pct = float(progress_pct)
        else:
            self._target_pct = float(_PHASE_TARGET_PCT.get(phase, 0))
            if phase == "requesting" and self._display_pct < 1.0:
                self._display_pct = 0.0
        if phase == "ready":
            self._display_pct = 100.0
            self._target_pct = 100.0
            self._refresh_label()
            self._stop_anim()
            return
        self._refresh_label()
        self._start_anim()

    def _refresh_label(self) -> None:
        if self._label is None:
            return
        try:
            if self._failed:
                msg = "Load failed"
                if self._fail_detail:
                    msg = f"Failed: {self._fail_detail}"
                self._label.text = msg
            else:
                pct = int(round(max(0.0, min(100.0, self._display_pct))))
                self._label.text = f"Loading data... {pct}%"
        except Exception:
            pass

    def _start_anim(self) -> None:
        global _anim_token
        self._anim_active = True
        _anim_token += 1
        token = _anim_token
        last_t = time.perf_counter()

        def _tick(dt: float = 0.0) -> None:  # noqa: ARG001
            nonlocal last_t
            if not self._anim_active or token != _anim_token:
                return
            now = time.perf_counter()
            elapsed = max(0.0, now - last_t)
            last_t = now
            # ic_loading.png 중심 회전
            if self._spin_enabled and not self._failed:
                self._angle_deg = (
                    self._angle_deg + _SPIN_DEG_PER_SEC * elapsed
                ) % 360.0
                self._push_rotated_icon(self._angle_deg)
            # % 추종
            if not self._failed and self._display_pct < self._target_pct:
                step = max(0.35, (self._target_pct - self._display_pct) * 0.12)
                self._display_pct = min(self._target_pct, self._display_pct + step)
                self._refresh_label()
            elif (
                not self._failed
                and self._target_pct < 100.0
                and self._display_pct >= self._target_pct - 0.05
            ):
                creep_cap = min(99.0, self._target_pct + 8.0)
                if self._display_pct < creep_cap:
                    self._display_pct = min(creep_cap, self._display_pct + 0.08)
                    self._refresh_label()
            if self._target_pct >= 100.0 and self._display_pct >= 99.5:
                self._display_pct = 100.0
                self._refresh_label()
                self._anim_active = False
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


__all__ = [
    "federation_load_hud_enabled",
    "hide_federation_load_hud",
    "set_federation_load_status",
]
