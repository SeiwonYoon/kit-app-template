"""FOUP 진행상황 3D 패널 (기능 #2) — v1.

- pick/place 집계: ``lam_viewport_overlay_state.record_foup_event_from_schedule_entry``
  (JSON 블록 실행 시작 시 ``atm_foup{n}_pick|place`` — FOUP 1~3 구분).
- 이 패널은 ``get_foup_counts`` 를 읽어 3D 텍스트만 갱신한다.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

import omni.ui as ui
from omni.ui import scene as sc
from pxr import Usd, UsdGeom

from .lam_viewport_overlay_config import (
    FOUP_ANCHOR_PRIM_BY_INDEX,
    FOUP_PANEL_BG_RGBA,
    FOUP_PANEL_BORDER_RGBA,
    FOUP_PANEL_FONT_SIZE,
    FOUP_PANEL_HEIGHT_PX,
    FOUP_PANEL_LINE_HEIGHT_PX,
    FOUP_PANEL_OFFSET_XYZ_M,
    FOUP_PANEL_WIDTH_PX,
)
from .lam_viewport_overlay_state import (
    FoupCounts,
    get_foup_counts,
    get_toggle_foup_status,
)

if TYPE_CHECKING:
    from .lam_viewport import LamViewport
    from .simulation_play import LamSimulationCsvPlayWindow

_PRINT_PREFIX = "[LAM/FOUP3D]"
_FRAME_SLOT = "morph.lam_control:foup_status_3d"

# viewport 별로 scene_view가 중복으로 남지 않도록 강제 단일화
_ACTIVE_SCENEVIEW_BY_VW: Dict[int, Any] = {}
_ACTIVE_VW_BY_ID: Dict[int, Any] = {}
_ACTIVE_PANEL_NODES_BY_VW: Dict[int, Any] = {}
_ACTIVE_FOUP_PANEL: Optional["LamFoupStatus3dPanel"] = None

_PANEL_W = int(FOUP_PANEL_WIDTH_PX)
_PANEL_H = int(FOUP_PANEL_HEIGHT_PX)
_LINE_H = int(FOUP_PANEL_LINE_HEIGHT_PX)


def force_remove_all_foup_sceneviews() -> None:
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
            pn = _ACTIVE_PANEL_NODES_BY_VW.get(vw_id) or {}
            for _fi, node in dict(pn).items():
                try:
                    node["root"].transform = sc.Matrix44.get_translation_matrix(1e9, 1e9, 1e9)
                except Exception:
                    pass
                for lbl in list(node.get("labels") or []):
                    try:
                        lbl.text = ""
                    except Exception:
                        pass
        except Exception:
            pass
    try:
        _ACTIVE_SCENEVIEW_BY_VW.clear()
        _ACTIVE_VW_BY_ID.clear()
        _ACTIVE_PANEL_NODES_BY_VW.clear()
    except Exception:
        pass


def refresh_foup_status_panel_ui() -> None:
    """FOUP 집계 변경 직후 3D 패널 숫자 갱신 (메인 스레드 post_update)."""
    inst = _ACTIVE_FOUP_PANEL
    if inst is None or not getattr(inst, "_built", False):
        return

    def _ui() -> None:
        try:
            if _ACTIVE_FOUP_PANEL is inst and inst._built:
                inst._update_ui()
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


def reset_foup_play_session() -> None:
    """CSV 정지(초기화) — FOUP 집계·pick/place 중복 추적·3D 패널 표시 리셋."""
    try:
        from .lam_viewport_overlay_state import reset_all_foup_counts

        reset_all_foup_counts()
    except Exception:
        pass
    inst = _ACTIVE_FOUP_PANEL
    if inst is not None:
        try:
            inst.reset_play_session()
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


class LamFoupStatus3dPanel:
    def __init__(self, csv_window: "LamSimulationCsvPlayWindow", *, viewport: Optional["LamViewport"] = None) -> None:
        self._csv = csv_window
        self._viewport = viewport
        self._viewport_window: Any = None
        self._vw: Any = None
        self._scene_view: Optional[sc.SceneView] = None
        self._root: Optional[sc.Transform] = None
        self._built = False
        self._post_update_sub: Any = None
        self._last_tick = 0.0
        self._sync_token: float = 0.0
        # 한번 만든 UI를 재사용(겹침/누적 방지)
        self._panel_nodes: Dict[int, Dict[str, Any]] = {}

    def destroy(self) -> None:
        global _ACTIVE_FOUP_PANEL
        if _ACTIVE_FOUP_PANEL is self:
            _ACTIVE_FOUP_PANEL = None
        self._stop_poll()
        self._destroy_layer()

    def reset_play_session(self) -> None:
        """정지(초기화) 후 3D 패널 표시만 리셋 (집계는 overlay_state)."""
        if self._built and self._root:
            self._update_ui()

    def sync_layers(self, *, delay_frames: int = 12) -> None:
        if not get_toggle_foup_status():
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
        global _ACTIVE_FOUP_PANEL
        if _ACTIVE_FOUP_PANEL is self:
            _ACTIVE_FOUP_PANEL = None
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
        self._panel_nodes.clear()
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
        global _ACTIVE_FOUP_PANEL
        _ACTIVE_FOUP_PANEL = self
        self._start_poll()
        self._ensure_ui_built()
        try:
            _ACTIVE_PANEL_NODES_BY_VW[id(vw)] = self._panel_nodes
        except Exception:
            pass
        self._update_ui()

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
            if not get_toggle_foup_status():
                self.destroy()
                return
            now = time.time()
            if now - self._last_tick < 0.2:
                return
            self._last_tick = now
            self._tick()

        self._post_update_sub = stream.create_subscription_to_pop(_on, name="morph.lam_control:foup_status_3d")

    def _stop_poll(self) -> None:
        if self._post_update_sub is not None:
            try:
                self._post_update_sub.unsubscribe()
            except Exception:
                pass
            self._post_update_sub = None

    def _tick(self) -> None:
        """앵커 위치·집계 텍스트 주기 갱신 (집계는 JSON 시작 시 overlay_state 에 기록)."""
        self._update_ui()

    def _ensure_ui_built(self) -> None:
        """Scene graph는 1회만 만든 뒤, text/transform만 업데이트."""
        if not self._built or not self._root or self._panel_nodes:
            return
        with self._root:
            # NOTE: 앵커 prim이 없는 FOUP은 노드를 만들지 않는다.
            for fi in (1, 2, 3):
                anchor_path = _normalize_path(FOUP_ANCHOR_PRIM_BY_INDEX.get(fi, ""))
                if not anchor_path:
                    continue
                root = sc.Transform(
                    look_at=sc.Transform.LookAt.CAMERA,
                    transform=sc.Matrix44.get_translation_matrix(0.0, 0.0, 0.0),
                )
                with root:
                    with sc.Transform(scale_to=sc.Space.SCREEN):
                        bg = (0.10, 0.12, 0.15, 0.75)
                        border = (0.45, 0.55, 0.70, 0.90)
                        try:
                            bg = tuple(FOUP_PANEL_BG_RGBA)
                        except Exception:
                            pass
                        try:
                            border = tuple(FOUP_PANEL_BORDER_RGBA)
                        except Exception:
                            pass
                        sc.Rectangle(width=_PANEL_W, height=_PANEL_H, color=bg, wireframe=False)
                        sc.Rectangle(width=_PANEL_W, height=_PANEL_H, color=border, wireframe=True)
                        left = -_PANEL_W // 2 + 10
                        top = _PANEL_H // 2 - 20
                        labels = []
                        for i in range(3):
                            y = top - i * _LINE_H
                            with sc.Transform(transform=sc.Matrix44.get_translation_matrix(left, y, 0)):
                                lbl = sc.Label(
                                    "",
                                    size=int(FOUP_PANEL_FONT_SIZE),
                                    color=(1.0, 1.0, 1.0, 1.0),
                                    alignment=ui.Alignment.LEFT_CENTER,
                                )
                                labels.append(lbl)
                self._panel_nodes[fi] = {"root": root, "labels": labels}

    def _update_ui(self) -> None:
        if not self._built or not self._root:
            return
        st = _stage()
        if st is None:
            return
        self._ensure_ui_built()

        for fi in (1, 2, 3):
            node = self._panel_nodes.get(fi)
            if not node:
                continue
            anchor_path = _normalize_path(FOUP_ANCHOR_PRIM_BY_INDEX.get(fi, ""))
            if not anchor_path:
                continue
            prim = st.GetPrimAtPath(anchor_path)
            if not prim or not prim.IsValid():
                # 앵커가 없으면 패널을 멀리 보내고 텍스트를 비움(뜬금없는 빈 라벨 방지)
                try:
                    node["root"].transform = sc.Matrix44.get_translation_matrix(1e9, 1e9, 1e9)
                except Exception:
                    pass
                for lbl in list(node.get("labels") or []):
                    try:
                        lbl.text = ""
                    except Exception:
                        pass
                continue
            center = _prim_world_center(prim)
            if center is None:
                continue
            ox, oy, oz = FOUP_PANEL_OFFSET_XYZ_M
            pos = (center[0] + ox, center[1] + oy, center[2] + oz)
            try:
                node["root"].transform = sc.Matrix44.get_translation_matrix(*pos)
            except Exception:
                pass

            c: FoupCounts = get_foup_counts(fi)
            lines = [
                f"FOUP{fi}  {c.current_in_foup_now}/{c.total}",
                f"진행중 {c.in_process_count}",
                f"완료 {c.done_count}",
            ]
            lbls = list(node.get("labels") or [])
            for i in range(min(len(lbls), len(lines))):
                try:
                    lbls[i].text = lines[i]
                except Exception:
                    pass

    def _build_label(self, world_pos: Tuple[float, float, float], text: str) -> None:
        root = sc.Transform(
            look_at=sc.Transform.LookAt.CAMERA,
            transform=sc.Matrix44.get_translation_matrix(*world_pos),
        )
        with root:
            with sc.Transform(scale_to=sc.Space.SCREEN):
                # 표 형태(테두리/연한 배경)로 보이도록 패널을 그림
                panel_w = _PANEL_W
                panel_h = _PANEL_H
                bg = (0.10, 0.12, 0.15, 0.75)  # 연한 배경
                border = (0.45, 0.55, 0.70, 0.90)
                sc.Rectangle(width=panel_w, height=panel_h, color=bg, wireframe=False)
                sc.Rectangle(width=panel_w, height=panel_h, color=border, wireframe=True)

                left = -panel_w // 2 + 10
                top = panel_h // 2 - 18
                lines = [ln for ln in (text or "").splitlines() if ln.strip()]
                for i, ln in enumerate(lines[:3]):
                    y = top - i * _LINE_H
                    with sc.Transform(transform=sc.Matrix44.get_translation_matrix(left, y, 0)):
                        sc.Label(
                            ln,
                            size=15,
                            color=(1.0, 1.0, 1.0, 1.0),
                            alignment=ui.Alignment.LEFT_CENTER,
                        )


__all__ = [
    "LamFoupStatus3dPanel",
    "force_remove_all_foup_sceneviews",
    "refresh_foup_status_panel_ui",
    "reset_foup_play_session",
]

