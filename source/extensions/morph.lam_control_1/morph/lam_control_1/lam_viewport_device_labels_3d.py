"""기기정보보기 3D 라벨 (기능 #3) — v1.

설정(SSOT): lam_viewport_overlay_config.DEVICE_LABEL_SPECS
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional, Tuple, TYPE_CHECKING

import omni.ui as ui
from omni.ui import scene as sc
from pxr import Usd, UsdGeom

from .lam_viewport_overlay_config import (
    DEVICE_LABEL_CHAR_WIDTH_FACTOR,
    DEVICE_LABEL_PM_OCCUPIED_BG_RGBA,
    DEVICE_LABEL_SPECS,
    DEVICE_LABEL_WIDTH_SLACK_PX,
    DeviceLabelSpec,
)
from .lam_viewport_overlay_state import get_toggle_device_labels

_PM_LABEL_NAME_RE = re.compile(r"^PM([1-5])$", re.IGNORECASE)

if TYPE_CHECKING:
    from .lam_viewport import LamViewport

_PRINT_PREFIX = "[LAM/DeviceLabels3D]"
_FRAME_SLOT = "morph.lam_control_1:device_labels_3d"

# viewport 별로 scene_view가 중복으로 남지 않도록 강제 단일화
_ACTIVE_SCENEVIEW_BY_VW: dict[int, Any] = {}
_ACTIVE_VW_BY_ID: dict[int, Any] = {}
_ACTIVE_DEVICE_PANEL_BY_SCREEN: dict[int, "LamViewportDeviceLabels3d"] = {}
_ACTIVE_SCENEVIEW_SCREEN: dict[int, int] = {}


def force_remove_device_sceneviews(*, screen: Optional[int] = None) -> None:
    """기기정보 SceneView 강제 제거.

    ``screen`` 지정 시 해당 화면만. 화면1 전역 토글 OFF 는 ``screen=1`` 만.
    """
    if screen is not None:
        si = max(1, int(screen))
        inst = _ACTIVE_DEVICE_PANEL_BY_SCREEN.get(si)
        if inst is not None:
            try:
                inst.destroy()
            except Exception:
                pass
        try:
            stale = [
                vw_id
                for vw_id, sn in list(_ACTIVE_SCENEVIEW_SCREEN.items())
                if int(sn) == si
            ]
            for vw_id in stale:
                sv = _ACTIVE_SCENEVIEW_BY_VW.pop(vw_id, None)
                vw = _ACTIVE_VW_BY_ID.pop(vw_id, None)
                _ACTIVE_SCENEVIEW_SCREEN.pop(vw_id, None)
                if sv is None:
                    continue
                api = None
                if vw is not None:
                    api = getattr(vw, "viewport_api", None)
                    if api is None and callable(getattr(vw, "add_scene_view", None)):
                        api = vw
                if api is not None:
                    try:
                        api.remove_scene_view(sv)
                    except Exception:
                        pass
                try:
                    setattr(sv, "visible", False)
                except Exception:
                    pass
        except Exception:
            pass
        return
    for inst in list(_ACTIVE_DEVICE_PANEL_BY_SCREEN.values()):
        if inst is None:
            continue
        try:
            inst.destroy()
        except Exception:
            pass
    try:
        _ACTIVE_SCENEVIEW_BY_VW.clear()
        _ACTIVE_VW_BY_ID.clear()
        _ACTIVE_SCENEVIEW_SCREEN.clear()
        _ACTIVE_DEVICE_PANEL_BY_SCREEN.clear()
    except Exception:
        pass


def force_remove_all_device_sceneviews() -> None:
    """호환: 전체 제거. 화면1 토글에서는 ``force_remove_device_sceneviews(screen=1)`` 사용."""
    force_remove_device_sceneviews(screen=None)


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


def _stage() -> Optional[Usd.Stage]:
    try:
        import omni.usd as ou  # type: ignore

        ctx = ou.get_context("")
        return ctx.get_stage() if ctx else None
    except Exception:
        return None


def _prim_world_center(prim: Usd.Prim) -> Optional[Tuple[float, float, float]]:
    if not prim or not prim.IsValid():
        return None
    try:
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        bbox = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        center = bbox.GetMidpoint()
        return (float(center[0]), float(center[1]), float(center[2]))
    except Exception:
        pass
    try:
        xform = UsdGeom.Xformable(prim)
        if xform:
            xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
            m = xform_cache.GetLocalToWorldTransform(prim)
            t = m.ExtractTranslation()
            return (float(t[0]), float(t[1]), float(t[2]))
    except Exception:
        pass
    return None


def _normalize_path(p: str) -> str:
    return (p or "").strip().rstrip("/")


def _estimate_text_content_width(text: str, font_size: int) -> int:
    """``sc.Label`` SCREEN 공간 — 글자별 폭 추정 (omni.ui.scene 는 size=font 높이, 폭은 더 넓음)."""
    fs = max(8, int(font_size))
    total = 0.0
    for ch in str(text or ""):
        if ch == " ":
            total += fs * 0.40
        elif ch.isupper():
            total += fs * 1.12
        elif ch.isdigit():
            total += fs * 0.72
        elif ch in "1il|":
            total += fs * 0.48
        else:
            total += fs * 0.90
    scaled = total * float(DEVICE_LABEL_CHAR_WIDTH_FACTOR)
    return int(scaled) + int(DEVICE_LABEL_WIDTH_SLACK_PX)


def _estimate_label_panel_size(
    text: str,
    font_size: int,
    padding_px: Tuple[int, int],
) -> Tuple[int, int]:
    """글자 길이 + padding 기준 SCREEN 공간 패널 (w, h) [px]."""
    pad_h, pad_v = int(padding_px[0]), int(padding_px[1])
    fs = max(8, int(font_size))
    content_w = _estimate_text_content_width(text, fs)
    w = max(content_w + pad_h * 2, fs * 2 + pad_h * 2)
    h = max(int(fs * 1.40) + pad_v * 2, fs + pad_v * 2)
    return w, h


class LamViewportDeviceLabels3d:
    def __init__(
        self,
        *,
        viewport: Optional["LamViewport"] = None,
        screen: int = 1,
        csv_window: Any = None,
    ) -> None:
        self._viewport = viewport
        self._screen = max(1, int(screen))
        self._csv_window = csv_window
        self._viewport_window: Any = None
        self._mounted_vp_api: Any = None
        self._vw: Any = None
        self._scene_view: Optional[sc.SceneView] = None
        self._root: Optional[sc.Transform] = None
        self._built = False
        self._post_update_sub: Any = None
        self._last_tick = 0.0
        self._last_occ_rev: int = -1
        self._sync_token: float = 0.0

    def _floorplan_occ_revision(self) -> int:
        try:
            from .lam_floorplan_occupancy import get_floorplan_occupancy

            return int(get_floorplan_occupancy(self._screen).revision)
        except Exception:
            return -1

    def _pm_region_for_spec_name(self, name: str) -> Optional[str]:
        """``PM1``~``PM5`` 라벨명 → 평면도 region ``pm1``~``pm5``. 그 외는 None."""
        m = _PM_LABEL_NAME_RE.fullmatch(str(name or "").strip())
        if not m:
            return None
        return f"pm{int(m.group(1))}"

    def _bg_rgba_for_spec(self, spec: DeviceLabelSpec) -> Tuple[float, float, float, float]:
        """PM1~5 만 점유 시 파란 배경. 그 외·비점유는 spec 기본색."""
        base = tuple(spec.bg_rgba)
        region = self._pm_region_for_spec_name(spec.name)
        if not region:
            return base  # type: ignore[return-value]
        try:
            from .lam_floorplan_occupancy import get_floorplan_occupancy

            snap = get_floorplan_occupancy(self._screen).snapshot()
            if snap.get(region):
                return tuple(DEVICE_LABEL_PM_OCCUPIED_BG_RGBA)  # type: ignore[return-value]
        except Exception:
            pass
        return base  # type: ignore[return-value]

    def _device_labels_toggle_on(self) -> bool:
        if self._screen <= 1:
            return bool(get_toggle_device_labels())
        m = getattr(self._csv_window, "_device_labels_show_model", None)
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

    def _resolve_viewport_for_panel(self) -> Optional[Any]:
        if self._screen > 1:
            lam = getattr(self._csv_window, "_lam_window_ref", None)
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

    def _stage_for_panel(self) -> Optional[Usd.Stage]:
        if self._screen > 1:
            lam = getattr(self._csv_window, "_lam_window_ref", None)
            ext = getattr(lam, "_kit_ext", None) if lam is not None else None
            if ext is not None:
                try:
                    from .lam_csv_play_screen import get_stage_for_screen

                    st = get_stage_for_screen(ext, self._screen)
                    if st is not None:
                        return st
                except Exception:
                    pass
        return _stage()

    def destroy(self) -> None:
        was_built = bool(self._built)
        self._sync_token = time.time()
        self._stop_poll()
        self._destroy_layer()
        if was_built:
            self._mount_logged = False
            print(
                f"{_PRINT_PREFIX} screen{self._screen} device labels destroy (toggle OFF)",
                flush=True,
            )

    def sync_layers(self, *, delay_frames: int = 12) -> None:
        if not self._device_labels_toggle_on():
            if self._built:
                self.destroy()
            return
        if self._built and self._scene_view is not None and self._root is not None:
            if self._screen > 1:
                target = None
                try:
                    prev = self._viewport_window
                    self._viewport_window = None
                    target = self._resolve_viewport_for_panel()
                    if target is None:
                        self._viewport_window = prev
                        target = prev
                except Exception:
                    target = self._viewport_window
                if target is not None and target is not self._vw:
                    self._mount(target)
                    return
            self._rebuild()
            return
        tok = time.time()
        self._sync_token = tok

        def _try(remaining: int) -> None:
            # 이전 sync_layers()에서 예약된 post_update 시도는 무시(중복 mount 방지)
            if float(self._sync_token) != float(tok):
                return
            if not self._device_labels_toggle_on():
                self.destroy()
                return
            vw = self._resolve_viewport_for_panel()
            if vw is not None:
                self._mount(vw)
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
            if self._screen > 1:
                print(
                    f"{_PRINT_PREFIX} screen{self._screen} device labels mount skip — "
                    "LAM_SimSplit / hud_mount 미준비",
                    flush=True,
                )

        _try(max(0, int(delay_frames)))

    def _frame_slot(self) -> str:
        if getattr(self, "_screen", 1) > 1:
            return f"{_FRAME_SLOT}:screen{int(self._screen)}"
        return _FRAME_SLOT

    def _resolve_vp_api(self, host: Any) -> Any:
        if host is None:
            return None
        api = getattr(host, "viewport_api", None)
        if api is not None:
            return api
        if callable(getattr(host, "add_scene_view", None)):
            return host
        return None

    def _destroy_layer(self) -> None:
        # viewport_api에 add_scene_view 한 경우 반드시 제거해야 중복/겹침이 안 생김
        _ACTIVE_DEVICE_PANEL_BY_SCREEN.pop(int(getattr(self, "_screen", 1) or 1), None)
        owned = self._viewport_window
        sv = self._scene_view
        api = self._mounted_vp_api or self._resolve_vp_api(owned)
        if sv is not None and api is not None:
            try:
                api.remove_scene_view(sv)
            except Exception:
                pass
            try:
                setattr(sv, "visible", False)
            except Exception:
                pass
        try:
            if owned is not None:
                _ACTIVE_SCENEVIEW_BY_VW.pop(id(owned), None)
                _ACTIVE_VW_BY_ID.pop(id(owned), None)
                _ACTIVE_SCENEVIEW_SCREEN.pop(id(owned), None)
        except Exception:
            pass
        self._built = False
        self._scene_view = None
        self._root = None
        self._vw = None
        self._viewport_window = None
        self._mounted_vp_api = None
        # 이 패널 소유 창만 클리어 — 화면1 Viewport 절대 건드리지 않음
        if owned is not None and callable(getattr(owned, "get_frame", None)):
            try:
                with owned.get_frame(self._frame_slot()):
                    pass
            except Exception:
                pass

    def _mount(self, vw: Any) -> None:
        self._destroy_layer()
        if not self._device_labels_toggle_on():
            return
        host = vw
        vp_api = getattr(vw, "viewport_api", None)
        if vp_api is None and callable(getattr(vw, "add_scene_view", None)):
            vp_api = vw
            for attr in ("viewport_window", "window", "_viewport_window", "_window"):
                cand = getattr(vw, attr, None)
                if cand is not None and callable(getattr(cand, "get_frame", None)):
                    host = cand
                    break
        if vp_api is None:
            print(
                f"{_PRINT_PREFIX} screen{self._screen} mount skip — "
                f"viewport_api 없음 type={type(vw).__name__}",
                flush=True,
            )
            return
        self._vw = host
        self._viewport_window = host
        self._mounted_vp_api = vp_api
        # 같은 viewport에 기존 scene_view가 있으면 먼저 제거(겹침 방지)
        try:
            prev = _ACTIVE_SCENEVIEW_BY_VW.get(id(host))
            if prev is not None:
                try:
                    vp_api.remove_scene_view(prev)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if not callable(getattr(host, "get_frame", None)):
                print(
                    f"{_PRINT_PREFIX} screen{self._screen} mount skip — "
                    f"get_frame 없음 type={type(host).__name__}",
                    flush=True,
                )
                self._mounted_vp_api = None
                return
            with host.get_frame(self._frame_slot()):
                with ui.ZStack():
                    sv = sc.SceneView()
                    self._scene_view = sv
                    with sv.scene:
                        self._root = sc.Transform()
            try:
                vp_api.add_scene_view(self._scene_view)
                try:
                    _ACTIVE_SCENEVIEW_BY_VW[id(host)] = self._scene_view
                    _ACTIVE_VW_BY_ID[id(host)] = host
                    _ACTIVE_SCENEVIEW_SCREEN[id(host)] = int(self._screen)
                except Exception:
                    pass
            except Exception as exc:
                print(f"{_PRINT_PREFIX} add_scene_view failed: {exc}", flush=True)
                self._scene_view = None
                self._root = None
                self._mounted_vp_api = None
                return
        except Exception as exc:
            print(f"{_PRINT_PREFIX} mount failed screen={self._screen}: {exc}", flush=True)
            self._mounted_vp_api = None
            return
        self._built = True
        _ACTIVE_DEVICE_PANEL_BY_SCREEN[int(self._screen)] = self
        self._start_poll()
        self._rebuild()
        if not getattr(self, "_mount_logged", False):
            self._mount_logged = True
            print(
                f"{_PRINT_PREFIX} screen{self._screen} device labels mounted",
                flush=True,
            )

    def _start_poll(self) -> None:
        if self._post_update_sub is not None:
            return
        try:
            import omni.kit.app as kapp  # type: ignore

            stream = kapp.get_app().get_post_update_event_stream()
        except Exception:
            return

        def _on(_e) -> None:
            if not self._built or not self._root:
                return
            if not self._device_labels_toggle_on():
                self.destroy()
                return
            occ_rev = self._floorplan_occ_revision()
            now = time.time()
            # 점유 변경 시 즉시 배경 갱신, 그 외(위치 추적)는 0.5s 주기
            if occ_rev == self._last_occ_rev and (now - self._last_tick) < 0.5:
                return
            self._last_occ_rev = occ_rev
            self._last_tick = now
            self._rebuild()

        self._post_update_sub = stream.create_subscription_to_pop(_on, name="morph.lam_control_1:device_labels_3d")

    def _stop_poll(self) -> None:
        if self._post_update_sub is not None:
            try:
                self._post_update_sub.unsubscribe()
            except Exception:
                pass
            self._post_update_sub = None

    def _rebuild(self) -> None:
        if not self._device_labels_toggle_on():
            self.destroy()
            return
        if not self._built or not self._root:
            return
        st = self._stage_for_panel()
        if st is None:
            try:
                self._root.clear()
            except Exception:
                pass
            return

        try:
            self._root.clear()
        except Exception:
            return

        with self._root:
            for spec in DEVICE_LABEL_SPECS:
                p = _normalize_path(spec.prim_path)
                if not p:
                    continue
                prim = st.GetPrimAtPath(p)
                if not prim or not prim.IsValid():
                    continue
                center = _prim_world_center(prim)
                if center is None:
                    continue
                ox, oy, oz = spec.offset_xyz_m
                pos = (center[0] + ox, center[1] + oy, center[2] + oz)
                self._build_label(pos, spec)

    def _build_label(self, world_pos: Tuple[float, float, float], spec: DeviceLabelSpec) -> None:
        text = str(spec.name or "")
        fs = int(spec.font_size)
        pad_h, pad_v = int(spec.padding_px[0]), int(spec.padding_px[1])
        panel_w, panel_h = _estimate_label_panel_size(text, fs, (pad_h, pad_v))
        bg = self._bg_rgba_for_spec(spec)
        border = tuple(spec.border_rgba)
        text_color = tuple(spec.color_rgba)

        root = sc.Transform(
            look_at=sc.Transform.LookAt.CAMERA,
            transform=sc.Matrix44.get_translation_matrix(*world_pos),
        )
        with root:
            with sc.Transform(scale_to=sc.Space.SCREEN):
                sc.Rectangle(width=panel_w, height=panel_h, color=bg, wireframe=False)
                if spec.show_border:
                    sc.Rectangle(
                        width=panel_w,
                        height=panel_h,
                        color=border,
                        wireframe=True,
                    )
                sc.Label(
                    text,
                    size=fs,
                    color=text_color,
                    alignment=ui.Alignment.CENTER,
                )


__all__ = [
    "LamViewportDeviceLabels3d",
    "force_remove_all_device_sceneviews",
    "force_remove_device_sceneviews",
]

