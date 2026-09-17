"""Viewport 좌상단 Federation API 로딩 HUD (화면별).

요청~준비완료까지 ``Loading data... N%`` 단일 칩 UI.
아이콘: ``data/img/ic_loading.png`` 를 중심 기준으로 계속 회전 (ByteImageProvider).
실 로딩 %: 약 60초 동안 0→99, 준비 완료 시 그 숫자부터 2초 동안 100까지 채운 뒤
같은 자리에 재생 버튼.
재생 클릭 → ``_PLAY_CLICK_DELAY_SEC`` 뒤 버튼 숨김 + 그 화면만 시뮬 시작.
전 화면 100% 후 일괄 자동 재생은 하지 않는다. I 단축키 미리보기와는 별개.

표시 on/off: ``lam_sim_control_defaults.SHOW_VIEWPORT_FEDERATION_LOAD_HUD``.

TEMP I-hotkey: ``lam_federation_load_hud_i_hotkey.py`` + ``TEMP_FEDERATION_LOAD_HUD_I_HOTKEY``
(테스트 후 해당 파일·플래그·아래 TEMP 블록만 제거하면 됨).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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
_ICON_W = 18
_ICON_H = 18
_LINE_H = 20  # 아이콘·텍스트 공통 행 높이 (세로 중앙 정렬용)
_GAP_ICON_TEXT = 7
_FONT_SIZE = 18
# rgba(48, 47, 64, 0.5) → omni.ui 0xAARRGGBB
_BG_ARGB = 0x80302F40
_TEXT_ARGB = 0xFFE8EEF5
_FAIL_ARGB = 0xFFE06060
_SPIN_DEG_PER_SEC = 360.0  # 1초에 1바퀴
# 실 API 로딩 전용: 60초 동안 0→99, 완료(ready) 시 현재 %에서 2초 동안 100
_RAMP_TO_99_SEC = 60.0
_RAMP_CAP_PCT = 99.0
_FINISH_TO_100_SEC = 2.0
# 전 화면 ready 후 fly/play 직전 HUD 유지(레거시 hold_ready 경로)
_PRE_PLAY_HIDE_DELAY_SEC = 1.0

# --- 재생 버튼 (로딩 100% 후 같은 자리 대체, 가로 중심 유지). ---
_PLAY_BTN_W = 140
_PLAY_BTN_H = _PANEL_H
_PLAY_BTN_BORDER_WIDTH = 0
_PLAY_BTN_BORDER_ARGB = 0x00000000
_PLAY_BTN_BG_ARGB = 0xFF5C51D6  # #5C51D6
_PLAY_BTN_BG_CLICK_ARGB = 0xFF808080  # #808080
_PLAY_GLYPH_ARGB = 0xFFFFFFFF
_PLAY_GLYPH_FONT_SIZE = 18
_PLAY_CLICK_DELAY_SEC = 1.0
_FAIL_GLYPH = "✕"


def _play_center_pad() -> int:
    """로딩칩 너비 안에서 재생 버튼이 같은 중심을 갖도록 좌우 여백."""
    return max(0, (int(_PANEL_W) - int(_PLAY_BTN_W)) // 2)


def _play_rect_style(bg_argb: int) -> Dict[str, Any]:
    """omni.ui Rectangle 은 생성자 style 보다 set_style 가 이긴다.

    평탄 키와 ``Rectangle`` 중첩 키를 같이 넣어 부모/테마 간섭을 막는다.
    """
    inner: Dict[str, Any] = {
        "background_color": int(bg_argb),
        "border_width": float(_PLAY_BTN_BORDER_WIDTH),
        "border_color": int(_PLAY_BTN_BORDER_ARGB),
    }
    return {"Rectangle": dict(inner), **inner}

_lock = threading.RLock()
_panels: Dict[int, "_FedLoadPanel"] = {}
_play_click_fns: Dict[int, Callable[[], None]] = {}

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


def _img_dir() -> Path:
    try:
        from .lam_data_paths import extension_data_root

        return extension_data_root() / "img"
    except Exception:
        return Path(__file__).resolve().parent.parent.parent / "data" / "img"


def _loading_icon_path() -> Optional[Path]:
    """``data/img/ic_loading.png``."""
    p = _img_dir() / "ic_loading.png"
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

    HUD 표시 여부와 무관하게 웹으로 ``V2T_notify_load_status`` 를 보낸다.
    웹이 무시해도 Kit 동작은 변하지 않는다.
    ``progress_pct`` 가 있으면 0~100 으로 직접 반영(단계 목표보다 우선).
    """
    si = max(1, int(screen))
    ph = str(phase or "").strip().lower()
    if ph not in _PHASE_TARGET_PCT:
        return
    try:
        from .lam_hyview_v2t_notify import notify_load_status

        notify_load_status(si, ph, detail=str(detail or ""))
    except Exception:
        pass
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
            _hide_panels_visible(si)
            return
        if ph == "playing":
            with _lock:
                panel = _panels.get(si)
            if panel is not None:
                try:
                    if not bool(getattr(panel, "_i_preview", False)):
                        panel.set_phase("playing")
                except Exception:
                    pass
            _hide_panels_visible(si)
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


def _hide_panels_visible(
    screen: Optional[int] = None,
    *,
    skip_i_preview: bool = False,
) -> None:
    """패널 객체는 유지하고 ``visible`` 만 False. pop/destroy 없음."""
    with _lock:
        if screen is None:
            targets = list(_panels.keys())
        else:
            targets = [max(1, int(screen))]
        panels = []
        for si in targets:
            panel = _panels.get(si)
            if panel is None:
                continue
            if skip_i_preview and bool(getattr(panel, "_i_preview", False)):
                continue
            panels.append(panel)
    for panel in panels:
        try:
            panel.apply_user_overlay_visible(False)
        except Exception:
            pass


def hide_federation_load_hud(screen: Optional[int] = None) -> None:
    """특정 화면 또는 전체 HUD 숨김 (객체 유지, visible=False)."""

    def _apply() -> None:
        _hide_panels_visible(screen)

    schedule_on_main_thread(_apply)


def arm_federation_play_button(
    screen: int,
    on_play: Callable[[], None],
    *,
    ext: Any = None,
    lam_window: Any = None,
) -> None:
    """해당 화면 로딩 HUD를 재생 버튼으로 바꾸고, 클릭 시 ``on_play`` 를 호출한다."""
    si = max(1, int(screen))
    if not callable(on_play):
        return
    with _lock:
        _play_click_fns[si] = on_play

    def _apply() -> None:
        panel = _ensure_panel(si, kit_ext=ext, lam_window=lam_window)
        if panel is None:
            return
        panel._play_fn = on_play
        panel._play_starting = False
        try:
            panel.apply_user_overlay_visible(True)
        except Exception:
            pass
        mode = str(getattr(panel, "_pct_mode", "") or "")
        if mode == "finish":
            return
        if str(getattr(panel, "_phase", "") or "") in ("ready", "playing"):
            panel._enter_play_mode()
        elif mode == "complete":
            panel._enter_play_mode()

    schedule_on_main_thread(_apply)


def hold_ready_then_hide_federation_load_huds(
    screens: List[int],
    *,
    delay_sec: Optional[float] = None,
) -> None:
    """실 시뮬레이션 재생 직전용 — ready(100%) 유지 후 delay 뒤 HUD 숨김.

    I 미리보기(``_i_preview``) 패널은 건드리지 않는다.
    호출 스레드(Federation worker)를 ``delay_sec`` 동안 block 한 뒤
    메인 스레드에서 visible=False 한다. camera fly / play 시작 **직전**에 호출.
    패널 객체는 ``_panels`` 에 남겨 다음 로딩·I 토글이 다시 켤 수 있게 한다.
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
            _hide_panels_visible(si, skip_i_preview=True)
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
        self._load_root: Any = None
        self._play_root: Any = None
        self._play_bg: Any = None
        self._play_mark: Any = None
        self._fail_mark: Any = None
        self._chip_wrap: Any = None
        self._play_fn: Optional[Callable[[], None]] = None
        self._play_starting = False
        self._sel_disable_scope: Any = None
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
        self._finish_t0: Optional[float] = None
        self._finish_from_pct: float = 0.0
        self._pct_mode: str = "idle"  # idle|ramp|fixed|finish|complete
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
            self._release_pick_block()
            self._stop_anim()

    # --- END TEMP: I-hotkey ---

    def destroy(self) -> None:
        self._release_pick_block()
        self._stop_anim()
        self._root = None
        self._label = None
        self._icon = None
        self._icon_provider = None
        self._base_im = None
        self._load_root = None
        self._play_root = None
        self._play_bg = None
        self._play_mark = None
        self._fail_mark = None
        self._chip_wrap = None
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
                        row_h = max(int(_PANEL_H), int(_PLAY_BTN_H))
                        with ui.HStack(height=row_h):
                            ui.Spacer(width=_LEFT)
                            # Viewport overlay 에서 Frame.style 배경이 무시되는 경우가 많아
                            # CSV HUD 와 같이 ZStack + Rectangle 로 배경을 그림.
                            wrap_w = int(_PANEL_W)
                            wrap_h = max(int(_PANEL_H), int(_PLAY_BTN_H))
                            self._chip_wrap = ui.ZStack(width=wrap_w, height=wrap_h)
                            with self._chip_wrap:
                                self._load_root = ui.ZStack(
                                    width=_PANEL_W, height=_PANEL_H
                                )
                                with self._load_root:
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
                                pad = _play_center_pad()
                                with ui.HStack(width=wrap_w, height=int(_PLAY_BTN_H)):
                                    if pad:
                                        ui.Spacer(width=pad)
                                    self._build_play_chip(ui)
                                    if pad:
                                        ui.Spacer(width=pad)
                            self._wire_hud_pick_block(
                                self._chip_wrap, on_press=self._on_overlay_pressed
                            )
            self._display_pct = 0.0
            self._target_pct = 0.0
            self._failed = False
            self._angle_deg = 0.0
            self._play_starting = False
            with _lock:
                pending_fn = _play_click_fns.get(self.screen)
            if pending_fn is not None:
                self._play_fn = pending_fn
            self._set_play_visible(False)
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

    def _clear_overlay_prim_selection(self) -> None:
        try:
            import omni.usd as ou

            names: List[str] = [""]
            try:
                from .lam_csv_play_screen import usd_context_name_for_screen
                from .lam_extension_singleton import get_lam_extension_instance

                ext = get_lam_extension_instance()
                cn = usd_context_name_for_screen(ext, int(self.screen)) if ext else None
                if cn:
                    names.append(str(cn))
            except Exception:
                pass
            seen = set()
            for nm in names:
                key = str(nm or "")
                if key in seen:
                    continue
                seen.add(key)
                ctx = ou.get_context(key) if key else ou.get_context()
                if ctx is None:
                    continue
                sel = ctx.get_selection()
                if sel is not None:
                    sel.clear_selected_prim_paths()
        except Exception:
            pass

    def _set_pick_block(self, block: bool) -> None:
        if bool(block):
            if self._sel_disable_scope is not None:
                return
            vw = self._mounted_vw
            if vw is None:
                return
            try:
                import omni.kit.viewport.utility as vpu

                api = getattr(vw, "viewport_api", None)
                last_exc: Optional[BaseException] = None
                for cand in (vw, api):
                    if cand is None:
                        continue
                    try:
                        self._sel_disable_scope = vpu.disable_selection(
                            cand, disable_click=True
                        )
                        last_exc = None
                        break
                    except Exception as exc:
                        last_exc = exc
                if last_exc is not None and self._sel_disable_scope is None:
                    self._sel_disable_scope = None
            except Exception:
                self._sel_disable_scope = None
            return
        self._release_pick_block()

    def _release_pick_block(self) -> None:
        scope = self._sel_disable_scope
        self._sel_disable_scope = None
        self._sel_disable_scope = None
        if scope is None:
            return
        for closer in ("__exit__", "release", "unsubscribe"):
            fn = getattr(scope, closer, None)
            if not callable(fn):
                continue
            try:
                if closer == "__exit__":
                    fn(None, None, None)
                else:
                    fn()
            except Exception:
                pass
            break

    def _wire_hud_pick_block(
        self, widget: Any, *, on_press: Optional[Callable[[], None]] = None
    ) -> None:
        """오버레이 위 마우스 이벤트를 먹고, 뒤 prim 선택을 끈다."""
        if widget is None:
            return

        def _press(*args: Any, **_k: Any) -> bool:
            btn = args[2] if len(args) >= 3 else 0
            try:
                if int(btn) not in (0, 1):
                    return True
            except Exception:
                pass
            self._set_pick_block(True)
            self._clear_overlay_prim_selection()
            try:
                import omni.kit.app

                omni.kit.app.get_app().post_update(
                    lambda *_a: self._clear_overlay_prim_selection()
                )
            except Exception:
                pass
            if on_press is not None:
                try:
                    if int(btn) == 0:
                        on_press()
                except Exception:
                    on_press()
            return True

        def _release(*_a: Any, **_k: Any) -> bool:
            return True

        def _move(*_a: Any, **_k: Any) -> bool:
            return True

        def _hover(hovered: Any = False, *_a: Any, **_k: Any) -> bool:
            self._set_pick_block(bool(hovered))
            if bool(hovered):
                self._clear_overlay_prim_selection()
            return True

        for name, fn in (
            ("set_mouse_pressed_fn", _press),
            ("set_mouse_released_fn", _release),
            ("set_mouse_moved_fn", _move),
            ("set_mouse_hovered_fn", _hover),
        ):
            setter = getattr(widget, name, None)
            if callable(setter):
                try:
                    setter(fn)
                except Exception:
                    pass

    def _build_play_chip(self, ui: Any) -> None:
        """로딩 칩과 같은 가로 중심의 재생 버튼 (Play 텍스트, 배경 #5C51D6)."""
        pw = int(_PLAY_BTN_W)
        ph = int(_PLAY_BTN_H)
        ra = getattr(ui, "Alignment", None)
        center = getattr(ra, "CENTER", None) if ra is not None else None
        self._play_root = ui.ZStack(width=pw, height=ph)
        with self._play_root:
            self._play_bg = ui.Rectangle(width=pw, height=ph)
            glyph_kw: Dict[str, Any] = {
                "width": pw,
                "height": ph,
                "style": {
                    "color": int(_PLAY_GLYPH_ARGB),
                    "font_size": int(_PLAY_GLYPH_FONT_SIZE),
                },
            }
            if center is not None:
                glyph_kw["alignment"] = center
            self._play_mark = ui.Label("Play", **glyph_kw)
            fail_kw: Dict[str, Any] = {
                "width": pw,
                "height": ph,
                "style": {
                    "color": int(_PLAY_GLYPH_ARGB),
                    "font_size": int(_PLAY_GLYPH_FONT_SIZE),
                },
            }
            if center is not None:
                fail_kw["alignment"] = center
            self._fail_mark = ui.Label(str(_FAIL_GLYPH), **fail_kw)
            self._set_widget_visible(self._fail_mark, False)
            # Kit 기본 Button 회색이 Rectangle 을 가리지 않게 완전 투명.
            btn_style = {
                "Button": {
                    "background_color": 0x00000000,
                    "border_width": 0,
                    "border_color": 0x00000000,
                    "padding": 0,
                    "margin": 0,
                },
                "Button:hovered": {
                    "background_color": 0x00000000,
                    "border_width": 0,
                },
                "Button:pressed": {
                    "background_color": 0x00000000,
                    "border_width": 0,
                },
            }
            ui.Button(
                "",
                width=pw,
                height=ph,
                clicked_fn=self._on_play_clicked,
                style=btn_style,
            )
        self._apply_play_bg()
        self._set_widget_visible(self._play_root, False)

    def _set_widget_visible(self, widget: Any, want: bool) -> None:
        if widget is None:
            return
        try:
            widget.visible = bool(want)
        except Exception:
            try:
                if hasattr(widget, "set_visible"):
                    widget.set_visible(bool(want))
            except Exception:
                pass

    def _set_play_visible(self, play_on: bool) -> None:
        self._set_widget_visible(self._load_root, not bool(play_on))
        self._set_widget_visible(self._play_root, bool(play_on))
        # 숨김 상태에서 set_style 가 먹지 않는 경우가 있어, 보일 때 다시 입힌다.
        if play_on:
            if self._play_starting:
                self._set_play_bg_color(int(_PLAY_BTN_BG_CLICK_ARGB))
            else:
                self._apply_play_bg()

    def _enter_load_mode(self) -> None:
        self._play_starting = False
        self._sync_play_marks(failed=False)
        self._set_play_visible(False)

    def _sync_play_marks(self, *, failed: bool) -> None:
        self._set_widget_visible(self._play_mark, not bool(failed))
        self._set_widget_visible(self._fail_mark, bool(failed))

    def _apply_play_bg(self) -> None:
        self._set_play_bg_color(int(_PLAY_BTN_BG_ARGB))

    def _set_play_bg_color(self, bg_argb: int) -> None:
        st = _play_rect_style(int(bg_argb))
        try:
            if self._play_bg is not None:
                self._play_bg.set_style(st)
        except Exception:
            try:
                if self._play_bg is not None:
                    self._play_bg.style = st
            except Exception:
                pass

    def _enter_fail_mode(self) -> None:
        """재생 버튼과 같은 자리·배경, Play 대신 가운데 X. 클릭해도 재생하지 않음."""
        if bool(getattr(self, "_i_preview", False)):
            return
        self._stop_anim()
        self._play_starting = False
        self._play_fn = None
        self._sync_play_marks(failed=True)
        self._apply_play_bg()
        self._set_play_visible(True)
        try:
            if self._root is not None:
                self._root.visible = True
        except Exception:
            pass

    def _enter_play_mode(self) -> None:
        if bool(getattr(self, "_i_preview", False)):
            return
        if self._play_fn is None:
            with _lock:
                self._play_fn = _play_click_fns.get(self.screen)
        if self._play_fn is None:
            return
        self._stop_anim()
        self._play_starting = False
        self._sync_play_marks(failed=False)
        self._apply_play_bg()
        self._set_play_visible(True)
        try:
            if self._root is not None:
                self._root.visible = True
        except Exception:
            pass

    def _on_overlay_pressed(self) -> None:
        play = self._play_root
        if play is None:
            return
        try:
            if not bool(getattr(play, "visible", False)):
                return
        except Exception:
            return
        self._on_play_clicked()

    def _on_play_clicked(self) -> None:
        if self._failed:
            return
        if self._play_starting:
            return
        fn = self._play_fn
        if fn is None:
            with _lock:
                fn = _play_click_fns.get(self.screen)
            self._play_fn = fn
        if not callable(fn):
            return
        self._play_starting = True
        self._set_play_bg_color(int(_PLAY_BTN_BG_CLICK_ARGB))
        si = int(self.screen)
        delay = max(0.0, float(_PLAY_CLICK_DELAY_SEC))

        def _after_delay() -> None:
            try:
                if delay > 1e-9:
                    time.sleep(delay)
            except Exception:
                pass

            def _go() -> None:
                try:
                    _hide_panels_visible(si, skip_i_preview=True)
                except Exception:
                    pass
                try:
                    fn()
                except Exception as exc:
                    print(
                        f"{_PRINT_PREFIX} screen{si} play click failed: {exc}",
                        flush=True,
                    )

            schedule_on_main_thread(_go)

        threading.Thread(
            target=_after_delay,
            name=f"lam-fed-play-click-s{si}",
            daemon=True,
        ).start()

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
            self._fail_detail = ""
            self._target_pct = float(self._display_pct)
            self._stop_anim()
            self._enter_fail_mode()
            return

        self._failed = False
        try:
            self._label.style = {"color": _TEXT_ARGB, "font_size": _FONT_SIZE}
        except Exception:
            pass

        # I 단축키 미리보기 — 고정 % / 스핀만 (실로딩 60초 램프와 무관)
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

        # 실 로딩 완료 → 현재 %에서 2초 동안 100, 끝난 뒤 재생 버튼 (I 미리보기 제외)
        if phase == "ready":
            self._i_preview = False
            if self._pct_mode == "complete":
                self._enter_play_mode()
                return
            if self._pct_mode != "finish":
                self._begin_finish_to_100()
            return
        if phase == "playing":
            self._i_preview = False
            self._pct_mode = "complete"
            self._ramp_t0 = None
            self._finish_t0 = None
            self._display_pct = 100.0
            self._target_pct = 100.0
            self._stop_anim()
            return

        # 실 API 로딩(requesting/received/parsing): 60초 동안 0→99
        self._i_preview = False
        with _lock:
            _play_click_fns.pop(self.screen, None)
        self._play_fn = None
        self._play_starting = False
        self._enter_load_mode()
        self._pct_mode = "ramp"
        self._finish_t0 = None
        if phase == "requesting" or self._ramp_t0 is None:
            self._ramp_t0 = time.perf_counter()
            self._display_pct = 0.0
        self._target_pct = float(_RAMP_CAP_PCT)
        self._refresh_label()
        self._start_anim()

    def _begin_finish_to_100(self) -> None:
        """ready 시점 표시 %에서 2초 동안 100까지. 끝나면 재생 버튼."""
        from_pct = max(0.0, min(float(_RAMP_CAP_PCT), float(self._display_pct)))
        self._finish_from_pct = from_pct
        self._finish_t0 = time.perf_counter()
        self._ramp_t0 = None
        self._pct_mode = "finish"
        self._target_pct = 100.0
        self._refresh_label()
        self._start_anim()

    def _complete_finish_to_100(self) -> None:
        self._display_pct = 100.0
        self._target_pct = 100.0
        self._pct_mode = "complete"
        self._finish_t0 = None
        self._refresh_label()
        self._enter_play_mode()

    def _refresh_label(self) -> None:
        if self._label is None:
            return
        try:
            if self._failed:
                return
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
            # 실로딩: 벽시계 기준 60초 → 99% (그 전에는 99 캡)
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
            elif (
                self._pct_mode == "finish"
                and self._finish_t0 is not None
                and not self._failed
            ):
                t = max(0.0, now - float(self._finish_t0))
                dur = max(1e-6, float(_FINISH_TO_100_SEC))
                span = 100.0 - float(self._finish_from_pct)
                if t >= dur:
                    self._complete_finish_to_100()
                else:
                    new_pct = float(self._finish_from_pct) + span * (t / dur)
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
    "arm_federation_play_button",
    "federation_load_hud_enabled",
    "federation_load_hud_user_overlay_visible",
    "hide_federation_load_hud",
    "hold_ready_then_hide_federation_load_huds",
    "set_federation_load_hud_user_overlay_visible",
    "set_federation_load_status",
    "toggle_federation_load_hud_user_overlay_visible",
]
