"""기기정보보기 3D 라벨 (기능 #3) — v1.

설정(SSOT): lam_viewport_overlay_config.DEVICE_LABEL_SPECS
"""

from __future__ import annotations

import time
from typing import Any, Optional, Tuple, TYPE_CHECKING

import omni.ui as ui
from omni.ui import scene as sc
from pxr import Usd, UsdGeom

from .lam_viewport_overlay_config import DEVICE_LABEL_SPECS
from .lam_viewport_overlay_state import get_toggle_device_labels

if TYPE_CHECKING:
    from .lam_viewport import LamViewport

_PRINT_PREFIX = "[LAM/DeviceLabels3D]"
_FRAME_SLOT = "morph.lam_control:device_labels_3d"

# viewport 별로 scene_view가 중복으로 남지 않도록 강제 단일화
_ACTIVE_SCENEVIEW_BY_VW: dict[int, Any] = {}
_ACTIVE_VW_BY_ID: dict[int, Any] = {}


def force_remove_all_device_sceneviews() -> None:
    """토글 OFF 등에서 남아있는 SceneView를 강제 제거."""
    try:
        items = list(_ACTIVE_SCENEVIEW_BY_VW.items())
    except Exception:
        items = []
    for vw_id, sv in items:
        vw = _ACTIVE_VW_BY_ID.get(vw_id)
        if vw is None or sv is None:
            continue
        try:
            vw.viewport_api.remove_scene_view(sv)
        except Exception:
            pass
        # remove가 실패해도 화면에서 안 보이게(최후의 안전장치)
        try:
            setattr(sv, "visible", False)
        except Exception:
            pass
        try:
            # 일부 버전에서는 scene_view.scene.clear() 가능
            scn = getattr(sv, "scene", None)
            if scn is not None and callable(getattr(scn, "clear", None)):
                scn.clear()
        except Exception:
            pass
    try:
        _ACTIVE_SCENEVIEW_BY_VW.clear()
        _ACTIVE_VW_BY_ID.clear()
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


class LamViewportDeviceLabels3d:
    def __init__(self, *, viewport: Optional["LamViewport"] = None) -> None:
        self._viewport = viewport
        self._viewport_window: Any = None
        self._vw: Any = None
        self._scene_view: Optional[sc.SceneView] = None
        self._root: Optional[sc.Transform] = None
        self._built = False
        self._post_update_sub: Any = None
        self._last_tick = 0.0
        self._sync_token: float = 0.0

    def destroy(self) -> None:
        self._stop_poll()
        self._destroy_layer()

    def sync_layers(self, *, delay_frames: int = 12) -> None:
        if not get_toggle_device_labels():
            self.destroy()
            return
        if self._built and self._scene_view is not None and self._root is not None:
            # 이미 mount 되어 있으면 중복 mount 하지 않고 갱신만
            self._rebuild()
            return
        tok = time.time()
        self._sync_token = tok

        def _try(remaining: int) -> None:
            # 이전 sync_layers()에서 예약된 post_update 시도는 무시(중복 mount 방지)
            if float(self._sync_token) != float(tok):
                return
            vw = _resolve_viewport_window(self._viewport)
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

        _try(max(0, int(delay_frames)))

    def _destroy_layer(self) -> None:
        # viewport_api에 add_scene_view 한 경우 반드시 제거해야 중복/겹침이 안 생김
        try:
            if self._scene_view is not None and self._viewport_window is not None:
                try:
                    self._viewport_window.viewport_api.remove_scene_view(self._scene_view)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if self._viewport_window is not None:
                _ACTIVE_SCENEVIEW_BY_VW.pop(id(self._viewport_window), None)
                _ACTIVE_VW_BY_ID.pop(id(self._viewport_window), None)
        except Exception:
            pass
        self._built = False
        self._scene_view = None
        self._root = None
        self._vw = None
        self._viewport_window = None
        try:
            vw = _resolve_viewport_window(self._viewport)
            if vw is None:
                return
            with vw.get_frame(_FRAME_SLOT):
                pass
        except Exception:
            pass

    def _mount(self, vw: Any) -> None:
        self._destroy_layer()
        self._vw = vw
        self._viewport_window = vw
        # 같은 viewport에 기존 scene_view가 있으면 먼저 제거(겹침 방지)
        try:
            prev = _ACTIVE_SCENEVIEW_BY_VW.get(id(vw))
            if prev is not None:
                try:
                    vw.viewport_api.remove_scene_view(prev)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            with vw.get_frame(_FRAME_SLOT):
                with ui.ZStack():
                    sv = sc.SceneView()
                    self._scene_view = sv
                    with sv.scene:
                        self._root = sc.Transform()
            try:
                vw.viewport_api.add_scene_view(self._scene_view)
                try:
                    _ACTIVE_SCENEVIEW_BY_VW[id(vw)] = self._scene_view
                    _ACTIVE_VW_BY_ID[id(vw)] = vw
                except Exception:
                    pass
            except Exception as exc:
                print(f"{_PRINT_PREFIX} add_scene_view failed: {exc}", flush=True)
                self._scene_view = None
                self._root = None
                return
        except Exception as exc:
            print(f"{_PRINT_PREFIX} mount failed: {exc}", flush=True)
            return
        self._built = True
        self._start_poll()
        self._rebuild()

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
            if not get_toggle_device_labels():
                self.destroy()
                return
            now = time.time()
            if now - self._last_tick < 0.5:
                return
            self._last_tick = now
            self._rebuild()

        self._post_update_sub = stream.create_subscription_to_pop(_on, name="morph.lam_control:device_labels_3d")

    def _stop_poll(self) -> None:
        if self._post_update_sub is not None:
            try:
                self._post_update_sub.unsubscribe()
            except Exception:
                pass
            self._post_update_sub = None

    def _rebuild(self) -> None:
        if not self._built or not self._root:
            return
        st = _stage()
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
                self._build_label(pos, spec.name, spec.font_size, spec.color_rgba)

    def _build_label(self, world_pos: Tuple[float, float, float], text: str, size: int, rgba) -> None:
        root = sc.Transform(
            look_at=sc.Transform.LookAt.CAMERA,
            transform=sc.Matrix44.get_translation_matrix(*world_pos),
        )
        with root:
            with sc.Transform(scale_to=sc.Space.SCREEN):
                sc.Label(
                    text,
                    size=int(size),
                    color=tuple(rgba),
                    alignment=ui.Alignment.LEFT_TOP,
                )


__all__ = ["LamViewportDeviceLabels3d", "force_remove_all_device_sceneviews"]

