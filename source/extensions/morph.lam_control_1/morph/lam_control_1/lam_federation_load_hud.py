"""Viewport 좌상단 Federation API 로딩 HUD (화면별).

요청~준비완료까지 ``Loading data... N%`` 단일 칩 UI.
아이콘: ``data/img/ic_loading.png`` 를 중심 기준으로 계속 회전 (ByteImageProvider).
실 로딩 %: 약 10초 동안 0→99, ready/play 직전 100. 전 화면 ready 후 1초 뒤
(camera fly / 재생 직전) HUD 숨김. I 단축키 미리보기와는 별개.

표시 on/off: ``lam_sim_control_defaults.SHOW_VIEWPORT_FEDERATION_LOAD_HUD``.

TEMP I-hotkey: ``lam_federation_load_hud_i_hotkey.py`` + ``TEMP_FEDERATION_LOAD_HUD_I_HOTKEY``
(테스트 후 해당 파일·플래그·아래 TEMP 블록만 제거하면 됨).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .kit_main_dispatch import schedule_on_main_thread

_PRINT_PREFIX = "[LAM/FedLoadHUD]"
_FRAME_SLOT_PREFIX = "morph.lam_control_1:federation_load_hud_s"

# phase 목록 (실로딩 % 는 시간 램프 — phase 목표 % 미사용)
_PHASE_TARGET_PCT: Dict[str, int] = {
    "requesting": 0,
    "received": 0,
    "parsing": 0,
    "ready": 100,
    "playing": 100,
    "failed": -1,
}

_PANEL_W = 200
_PANEL_H = 36
_TOP = 10
_LEFT = 10
_PAD_LEFT = 14
_ICON_W = 15
_ICON_H = 16
_LINE_H = 20  # 아이콘·텍스트 공통 행 높이 (세로 중앙 정렬용)
_GAP_ICON_TEXT = 7
_FONT_SIZE = 16  # 기존 13 + 3
# rgba(48, 47, 64, 0.5) → omni.ui 0xAARRGGBB
_BG_ARGB = 0x80302F40
_TEXT_ARGB = 0xFFE8EEF5
_FAIL_ARGB = 0xFFE06060
_SPIN_DEG_PER_SEC = 360.0  # 1초에 1바퀴
# 실 API 로딩 전용: 10초 동안 0→99, 완료(ready/playing) 시 즉시 100
_RAMP_TO_99_SEC = 10.0
_RAMP_CAP_PCT = 99.0
# 전 화면 ready(재생준비) 후 fly/play 직전 HUD 유지 시간
_PRE_PLAY_HIDE_DELAY_SEC = 1.0

_lock = threading.RLock()
_panels: Dict[int, "_FedLoadPanel"] = {}

# --- BEGIN TEMP: I-hotkey (delete with lam_federation_load_hud_i_hotkey.py) ---
_user_overlay_visible: bool = True  # 실 Federation 로딩은 기본 표시


def federation_load_hud_user_overlay_visible() -> bool:
    return bool(_user_overlay_visible)


def _any_fed_load_panel_visible() -> bool:
    with _lock:
        panels = list(_panels.values())
    for panel in panels:
        root = getattr(panel, "_root", None)
        if root is None:
            continue
        try:
            if bool(getattr(root, "visible", True)):
                return True
        except Exception:
            return True
    return False


def _ensure_i_hotkey_preview_panels() -> None:
    """로딩 중이 아닐 때도 I 로 확인할 수 있게 미리보기 패널 생성."""
    try:
        from .lam_extension_singleton import get_lam_extension_instance

        ext = get_lam_extension_instance()
    except Exception:
        ext = None
    lam_window = None
    screens = [1]
    if ext is not None:
        lam_window = getattr(ext, "_lam_window", None) or getattr(ext, "_window", None)
        try:
            n = max(1, min(2, int(getattr(ext, "_sim_viewport_split_count", 1) or 1)))
            screens = list(range(1, n + 1))
        except Exception:
            screens = [1]
    for si in screens:
        with _lock:
            if si in _panels:
                continue
        panel = _ensure_panel(si, kit_ext=ext, lam_window=lam_window)
        if panel is None:
            print(f"{_PRINT_PREFIX} I preview: screen{si} mount failed", flush=True)
            continue
        try:
            panel._i_preview = True
        except Exception:
            pass
        try:
            # I 미리보기: 고정 % + 스핀만 (10초 램프 없음)
            panel.set_phase(
                "requesting",
                detail="I-hotkey preview",
                progress_pct=32.0,
                preview=True,
            )
        except Exception:
            pass


def _destroy_i_hotkey_preview_panels() -> None:
    with _lock:
        to_drop = [
            si
            for si, p in _panels.items()
            if bool(getattr(p, "_i_preview", False))
        ]
    for si in to_drop:
        with _lock:
            panel = _panels.pop(int(si), None)
        if panel is not None:
            try:
                panel.destroy()
            except Exception:
                pass


def set_federation_load_hud_user_overlay_visible(want: bool) -> None:
    """사용자 I 토글 — 배경·아이콘·텍스트 전체 show/hide (스핀은 보일 때 재개)."""
    global _user_overlay_visible
    _user_overlay_visible = bool(want)
    print(
        f"{_PRINT_PREFIX} user overlay → {'show' if _user_overlay_visible else 'hide'}",
        flush=True,
    )

    def _apply() -> None:
        if _user_overlay_visible:
            if not federation_load_hud_enabled():
                print(
                    f"{_PRINT_PREFIX} SHOW_VIEWPORT_FEDERATION_LOAD_HUD=False — I toggle noop",
                    flush=True,
                )
                return
            with _lock:
                empty = not _panels
            if empty:
                _ensure_i_hotkey_preview_panels()
        with _lock:
            panels = list(_panels.values())
        if not panels and _user_overlay_visible:
            print(f"{_PRINT_PREFIX} I show: no panel (viewport mount failed?)", flush=True)
            return
        for panel in panels:
            try:
                panel.apply_user_overlay_visible(_user_overlay_visible)
            except Exception:
                pass
        if not _user_overlay_visible:
            _destroy_i_hotkey_preview_panels()

    schedule_on_main_thread(_apply)


def toggle_federation_load_hud_user_overlay_visible() -> None:
    # 패널이 없거나 전부 숨김이면 → 표시(미리보기 포함). 보이면 → 숨김.
    if _any_fed_load_panel_visible():
        set_federation_load_hud_user_overlay_visible(False)
    else:
        set_federation_load_hud_user_overlay_visible(True)


# --- END TEMP: I-hotkey ---


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


def _rotate_rgba_bytes(base_im: Any, angle_deg: float) -> Tuple[Any, int, int]:
    """중심 회전 후 ByteImageProvider용 RGBA 시퀀스 (expand=False, 동일 크기)."""
    from PIL import Image  # type: ignore

    rotated = base_im.rotate(
        float(angle_deg),
        resample=Image.Resampling.BILINEAR,
        expand=False,
        center=(base_im.size[0] * 0.5, base_im.size[1] * 0.5),
    )
    w, h = rotated.size
    # getdata() 가 Kit ByteImageProvider 에서 tobytes() 보다 안정적
    return rotated.getdata(), int(w), int(h)


def _texture_format_rgba8() -> Any:
    try:
        from omni.gpu_foundation_factory import TextureFormat  # type: ignore

        return getattr(TextureFormat, "RGBA8_UNORM", None)
    except Exception:
        return None


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
        # --- BEGIN TEMP: I-hotkey ---
        # 실로딩은 기존처럼 항상 표시 (이전에 I 로 숨겼어도 복구)
        global _user_overlay_visible
        _user_overlay_visible = True
        try:
            panel._i_preview = False
        except Exception:
            pass
        try:
            panel.apply_user_overlay_visible(True)
        except Exception:
            pass
        # --- END TEMP: I-hotkey ---
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


def hold_ready_then_hide_federation_load_huds(
    screens: List[int],
    *,
    delay_sec: Optional[float] = None,
) -> None:
    """실 시뮬레이션 재생 직전용 — ready(100%) 유지 후 delay 뒤 HUD 제거.

    I 미리보기(``_i_preview``) 패널은 건드리지 않는다.
    호출 스레드(Federation worker)를 ``delay_sec`` 동안 block 한 뒤
    메인 스레드에서 destroy 한다. camera fly / play 시작 **직전**에 호출.
    """
    from .kit_main_dispatch import run_on_main_thread

    delay = (
        float(_PRE_PLAY_HIDE_DELAY_SEC)
        if delay_sec is None
        else max(0.0, float(delay_sec))
    )
    sis = sorted({max(1, int(s)) for s in (screens or []) if int(s) >= 1})
    if not sis:
        return

    def _snap_ready_100() -> None:
        for si in sis:
            with _lock:
                panel = _panels.get(si)
            if panel is None:
                continue
            if bool(getattr(panel, "_i_preview", False)):
                continue
            try:
                panel.set_phase("ready")
            except Exception:
                pass

    try:
        schedule_on_main_thread(_snap_ready_100)
    except Exception:
        pass

    try:
        time.sleep(delay)
    except Exception:
        pass

    def _hide_real() -> None:
        for si in sis:
            with _lock:
                panel = _panels.get(si)
                if panel is not None and bool(getattr(panel, "_i_preview", False)):
                    continue
                panel = _panels.pop(si, None)
            if panel is not None:
                try:
                    panel.destroy()
                except Exception:
                    pass
        print(
            f"{_PRINT_PREFIX} pre-play hide screens={sis} after {delay:.1f}s",
            flush=True,
        )

    try:
        run_on_main_thread(_hide_real, timeout=30.0)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} pre-play hide failed: {exc}", flush=True)
        try:
            schedule_on_main_thread(_hide_real)
        except Exception:
            pass


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
        self._update_sub: Any = None
        self._anim_gen = 0
        self._spin_err_logged = False
        self._ramp_t0: Optional[float] = None
        self._pct_mode: str = "idle"  # idle|ramp|fixed|complete
        # --- BEGIN TEMP: I-hotkey ---
        self._i_preview = False
        # --- END TEMP: I-hotkey ---

    # --- BEGIN TEMP: I-hotkey ---
    def apply_user_overlay_visible(self, want: bool) -> None:
        """배경 프레임 포함 전체 visible + 스핀 재개/정지."""
        if self._root is not None:
            try:
                self._root.visible = bool(want)
            except Exception:
                try:
                    if hasattr(self._root, "set_visible"):
                        self._root.set_visible(bool(want))
                except Exception:
                    pass
        if want:
            if (not self._failed) and self._phase not in ("ready", "playing", "failed"):
                self._start_anim()
        else:
            self._stop_anim()

    # --- END TEMP: I-hotkey ---

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
            fmt = _texture_format_rgba8()
            if hasattr(self._icon_provider, "set_bytes_data"):
                if fmt is not None:
                    try:
                        self._icon_provider.set_bytes_data(data, [w, h], fmt)
                        return
                    except TypeError:
                        pass
                self._icon_provider.set_bytes_data(data, [w, h])
            elif hasattr(self._icon_provider, "set_data"):
                self._icon_provider.set_data(data, [w, h])
        except Exception as exc:
            if not self._spin_err_logged:
                self._spin_err_logged = True
                print(f"{_PRINT_PREFIX} spin push failed: {exc}", flush=True)

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
            row_align = None
            if ra is not None:
                row_align = getattr(ra, "LEFT_CENTER", None) or getattr(
                    ra, "CENTER", None
                )
            with vw.get_frame(slot):
                root = ui.ZStack(alignment=lt) if lt is not None else ui.ZStack()
                self._root = root
                with root:
                    with ui.VStack():
                        ui.Spacer(height=_TOP)
                        with ui.HStack(height=_PANEL_H):
                            ui.Spacer(width=_LEFT)
                            # Viewport overlay 에서 Frame.style 배경이 무시되는 경우가 많아
                            # CSV HUD 와 같이 ZStack + Rectangle 로 배경을 그림.
                            with ui.ZStack(width=_PANEL_W, height=_PANEL_H):
                                ui.Rectangle(
                                    width=_PANEL_W,
                                    height=_PANEL_H,
                                    style={
                                        "background_color": _BG_ARGB,
                                        "border_width": 0,
                                    },
                                )
                                # 칩 전체 높이에서 콘텐츠 행을 Spacer 로 세로 중앙 배치
                                with ui.VStack(height=_PANEL_H):
                                    ui.Spacer()
                                    if row_align is not None:
                                        row = ui.HStack(
                                            height=_LINE_H, alignment=row_align
                                        )
                                    else:
                                        row = ui.HStack(height=_LINE_H)
                                    with row:
                                        ui.Spacer(width=_PAD_LEFT)
                                        self._icon = None
                                        self._icon_provider = None
                                        self._spin_enabled = False

                                        def _make_icon_widget() -> None:
                                            # 아이콘을 LINE_H 안에서 세로 중앙
                                            with ui.VStack(
                                                width=_ICON_W, height=_LINE_H
                                            ):
                                                ui.Spacer()
                                                if self._base_im is not None:
                                                    try:
                                                        provider_cls = getattr(
                                                            ui,
                                                            "ByteImageProvider",
                                                            None,
                                                        )
                                                        with_prov = getattr(
                                                            ui,
                                                            "ImageWithProvider",
                                                            None,
                                                        )
                                                        if (
                                                            provider_cls is not None
                                                            and with_prov is not None
                                                        ):
                                                            self._icon_provider = (
                                                                provider_cls()
                                                            )
                                                            self._push_rotated_icon(
                                                                0.0
                                                            )
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
                                                if (
                                                    self._icon is None
                                                    and icon_path is not None
                                                ):
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
                                                                "image_url": str(
                                                                    icon_path
                                                                )
                                                            },
                                                        )
                                                    self._spin_enabled = False
                                                    print(
                                                        f"{_PRINT_PREFIX} spin disabled (static Image fallback)",
                                                        flush=True,
                                                    )
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
                                                ui.Spacer()

                                        _make_icon_widget()
                                        ui.Spacer(width=_GAP_ICON_TEXT)
                                        label_kw: Dict[str, Any] = {
                                            "height": _LINE_H,
                                            "style": {
                                                "color": _TEXT_ARGB,
                                                "font_size": _FONT_SIZE,
                                            },
                                        }
                                        if row_align is not None:
                                            label_kw["alignment"] = row_align
                                        self._label = ui.Label(
                                            "Loading data... 0%", **label_kw
                                        )
                                        ui.Spacer()
                                    ui.Spacer()
            self._display_pct = 0.0
            self._target_pct = 0.0
            self._failed = False
            self._angle_deg = 0.0
            # --- BEGIN TEMP: I-hotkey ---
            try:
                self.apply_user_overlay_visible(
                    federation_load_hud_user_overlay_visible()
                )
            except Exception:
                pass
            # --- END TEMP: I-hotkey ---
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
        preview: bool = False,
    ) -> None:
        if self._label is None and not self.mount():
            return
        self._phase = phase
        is_preview = bool(preview) or bool(getattr(self, "_i_preview", False))

        if phase == "failed":
            self._failed = True
            self._pct_mode = "idle"
            short = (detail or "").strip()
            if len(short) > 28:
                short = short[:25] + "..."
            self._fail_detail = short
            self._target_pct = float(self._display_pct)
            self._stop_anim()
            self._refresh_label()
            try:
                self._label.style = {"color": _FAIL_ARGB, "font_size": _FONT_SIZE}
            except Exception:
                pass
            return

        self._failed = False
        try:
            self._label.style = {"color": _TEXT_ARGB, "font_size": _FONT_SIZE}
        except Exception:
            pass

        # I 단축키 미리보기 — 고정 % / 스핀만 (실로딩 10초 램프와 무관)
        if is_preview:
            self._i_preview = True
            self._pct_mode = "fixed"
            self._ramp_t0 = None
            if progress_pct is not None:
                self._display_pct = float(progress_pct)
                self._target_pct = float(progress_pct)
            self._refresh_label()
            self._start_anim()
            return

        # 실 로딩 완료 → 즉시 100
        if phase in ("ready", "playing"):
            self._i_preview = False
            self._pct_mode = "complete"
            self._ramp_t0 = None
            self._display_pct = 100.0
            self._target_pct = 100.0
            self._refresh_label()
            self._stop_anim()
            return

        # 실 API 로딩(requesting/received/parsing): 10초 동안 0→99
        self._i_preview = False
        self._pct_mode = "ramp"
        if phase == "requesting" or self._ramp_t0 is None:
            self._ramp_t0 = time.perf_counter()
            self._display_pct = 0.0
        self._target_pct = float(_RAMP_CAP_PCT)
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
        # --- BEGIN TEMP: I-hotkey ---
        if not federation_load_hud_user_overlay_visible():
            self._anim_active = False
            self._unsubscribe_anim()
            return
        # --- END TEMP: I-hotkey ---
        self._anim_active = True
        self._anim_gen += 1
        gen = self._anim_gen
        last_t = time.perf_counter()

        def _on_update(_event: Any = None) -> None:
            nonlocal last_t
            if not self._anim_active or gen != self._anim_gen:
                return
            # --- BEGIN TEMP: I-hotkey ---
            if not federation_load_hud_user_overlay_visible():
                return
            # --- END TEMP: I-hotkey ---
            now = time.perf_counter()
            elapsed = max(0.0, now - last_t)
            last_t = now
            if self._spin_enabled and not self._failed:
                self._angle_deg = (
                    self._angle_deg + _SPIN_DEG_PER_SEC * elapsed
                ) % 360.0
                self._push_rotated_icon(self._angle_deg)
            # 실로딩: 벽시계 기준 10초 → 99% (그 전에는 99 캡)
            if (
                self._pct_mode == "ramp"
                and self._ramp_t0 is not None
                and not self._failed
            ):
                t = max(0.0, now - float(self._ramp_t0))
                new_pct = min(
                    float(_RAMP_CAP_PCT),
                    (t / float(_RAMP_TO_99_SEC)) * float(_RAMP_CAP_PCT),
                )
                if int(round(new_pct)) != int(round(self._display_pct)) or abs(
                    new_pct - self._display_pct
                ) >= 0.2:
                    self._display_pct = new_pct
                    self._refresh_label()
            # fixed(I preview) / complete — % 추종 없음

        # IApp.post_update 사용 금지 — update event stream 구독
        self._unsubscribe_anim()
        try:
            import omni.kit.app as kit_app  # type: ignore

            stream = kit_app.get_app().get_update_event_stream()
            self._update_sub = stream.create_subscription_to_pop(
                _on_update,
                name=f"morph.lam_control_1:fed_load_hud_spin_s{self.screen}",
            )
        except Exception as exc:
            self._update_sub = None
            print(f"{_PRINT_PREFIX} anim subscribe failed: {exc}", flush=True)

    def _unsubscribe_anim(self) -> None:
        sub = self._update_sub
        self._update_sub = None
        if sub is None:
            return
        try:
            sub.unsubscribe()
        except Exception:
            pass

    def _stop_anim(self) -> None:
        self._anim_active = False
        self._anim_gen += 1
        self._unsubscribe_anim()


__all__ = [
    "federation_load_hud_enabled",
    "federation_load_hud_user_overlay_visible",
    "hide_federation_load_hud",
    "hold_ready_then_hide_federation_load_huds",
    "set_federation_load_hud_user_overlay_visible",
    "set_federation_load_status",
    "toggle_federation_load_hud_user_overlay_visible",
]
