"""FOUP 진행상황 3D 패널 (기능 #2) — v1.

- pick/place 집계: ``lam_viewport_overlay_state``
  · Play 시작: ``seed_foup_counts_from_non_atm_first`` (slot 최초 wafer → 진행중)
  · JSON 실행: ``record_foup_event_from_schedule_entry`` (``atm_foup{n}_pick|place``)
- 제목(lot_id) + 반출 / 공정진행 / 공정완료 (좌·우 끝 정렬)
- 웨이퍼 번호 3D 라벨 색: ``lam_foup_lot_display`` — FOUP·팔·장비 등 표시 위치와 무관하게 lot 색 유지
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

import omni.ui as ui
from omni.ui import scene as sc
from pxr import Usd, UsdGeom

from .lam_sim_control_defaults import FOUP_LOT_ID_FONT_SIZE
from .lam_viewport_overlay_config import (
    FOUP_ANCHOR_PRIM_BY_INDEX,
    FOUP_PANEL_BG_RGBA,
    FOUP_PANEL_BODY_H_PX,
    FOUP_PANEL_DIVIDER_H_PX,
    FOUP_PANEL_DIVIDER_RGBA,
    FOUP_PANEL_FONT_SIZE,
    FOUP_PANEL_HEIGHT_PX,
    FOUP_PANEL_OFFSET_XYZ_M,
    FOUP_PANEL_PAD_X_PX,
    FOUP_PANEL_TITLE_H_PX,
    FOUP_PANEL_TOP_BAR_H_PX,
    FOUP_PANEL_WIDTH_PX,
)
from .lam_viewport_overlay_state import (
    FoupCounts,
    get_foup_counts,
    get_lot_id_for_foup,
    get_toggle_foup_status,
)
from .lam_foup_lot_display import foup_lot_color_rgba

if TYPE_CHECKING:
    from .lam_viewport import LamViewport
    from .simulation_play import LamSimulationCsvPlayWindow

_PRINT_PREFIX = "[LAM/FOUP3D]"
_FRAME_SLOT = "morph.lam_control_1:foup_status_3d"

# viewport 별로 scene_view가 중복으로 남지 않도록 강제 단일화
_ACTIVE_SCENEVIEW_BY_VW: Dict[int, Any] = {}
_ACTIVE_VW_BY_ID: Dict[int, Any] = {}
_ACTIVE_PANEL_NODES_BY_VW: Dict[int, Any] = {}
_ACTIVE_FOUP_PANEL_BY_SCREEN: Dict[int, "LamFoupStatus3dPanel"] = {}
# SceneView id → screen (화면1 전역 OFF 가 화면2 를 지우지 않도록)
_ACTIVE_SCENEVIEW_SCREEN: Dict[int, int] = {}

_BODY_ROW_COUNT = 3
_WHITE = (1.0, 1.0, 1.0, 1.0)
_PANEL_W = int(FOUP_PANEL_WIDTH_PX)
_PANEL_H = int(FOUP_PANEL_HEIGHT_PX)
_TITLE_H = int(FOUP_PANEL_TITLE_H_PX)
_BODY_H = int(FOUP_PANEL_BODY_H_PX)
_TOP_BAR_H = int(FOUP_PANEL_TOP_BAR_H_PX)
_PAD_X = int(FOUP_PANEL_PAD_X_PX)
_DIVIDER_H = float(FOUP_PANEL_DIVIDER_H_PX)
_BODY_ROW_LABELS = ("반출", "공정진행", "공정완료")
_LAYOUT_VER = 5


def force_remove_foup_sceneviews(*, screen: Optional[int] = None) -> None:
    """FOUP SceneView 강제 제거.

    ``screen`` 지정 시 해당 화면 패널만. ``None`` 이면 전체(레거시·teardown).
    화면1 전역 토글 OFF 는 반드시 ``screen=1`` 만 호출해야 한다.
    """
    if screen is not None:
        si = max(1, int(screen))
        inst = _ACTIVE_FOUP_PANEL_BY_SCREEN.get(si)
        if inst is not None:
            try:
                inst.destroy()
            except Exception:
                pass
        # destroy 가 실패해도 해당 screen 의 sceneview 잔여 제거
        try:
            stale = [
                vw_id
                for vw_id, sn in list(_ACTIVE_SCENEVIEW_SCREEN.items())
                if int(sn) == si
            ]
            for vw_id in stale:
                sv = _ACTIVE_SCENEVIEW_BY_VW.pop(vw_id, None)
                vw = _ACTIVE_VW_BY_ID.pop(vw_id, None)
                _ACTIVE_PANEL_NODES_BY_VW.pop(vw_id, None)
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
    # 전체 제거 (창 teardown 등)
    for inst in list(_ACTIVE_FOUP_PANEL_BY_SCREEN.values()):
        if inst is None:
            continue
        try:
            inst.destroy()
        except Exception:
            pass
    try:
        _ACTIVE_SCENEVIEW_BY_VW.clear()
        _ACTIVE_VW_BY_ID.clear()
        _ACTIVE_PANEL_NODES_BY_VW.clear()
        _ACTIVE_SCENEVIEW_SCREEN.clear()
        _ACTIVE_FOUP_PANEL_BY_SCREEN.clear()
    except Exception:
        pass


def force_remove_all_foup_sceneviews() -> None:
    """호환: 전체 제거. 화면1 토글에서는 ``force_remove_foup_sceneviews(screen=1)`` 사용."""
    force_remove_foup_sceneviews(screen=None)


def refresh_foup_status_panel_ui(*, screen: Optional[int] = None) -> None:
    """FOUP 집계 변경 직후 3D 패널 숫자 갱신 (메인 스레드 post_update)."""
    if screen is not None:
        targets = [_ACTIVE_FOUP_PANEL_BY_SCREEN.get(int(screen))]
    else:
        targets = list(_ACTIVE_FOUP_PANEL_BY_SCREEN.values())

    def _ui() -> None:
        for inst in targets:
            if inst is None or not getattr(inst, "_built", False):
                continue
            try:
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


def reset_foup_play_session(*, screen: Optional[int] = None) -> None:
    """CSV 정지(초기화) — FOUP 집계·pick/place 중복 추적·3D 패널 표시 리셋."""
    try:
        from .lam_viewport_overlay_state import reset_all_foup_counts

        reset_all_foup_counts(screen=screen)
    except Exception:
        pass
    try:
        from .lam_device_label_highlight import reset_device_label_highlights

        reset_device_label_highlights(screen=screen)
    except Exception:
        pass
    if screen is not None:
        inst = _ACTIVE_FOUP_PANEL_BY_SCREEN.get(int(screen))
        if inst is not None:
            try:
                inst.reset_play_session()
            except Exception:
                pass
        return
    for inst in list(_ACTIVE_FOUP_PANEL_BY_SCREEN.values()):
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
            int(screen if screen is not None else getattr(csv_window, "_screen", 1)),
        )
        self._viewport = viewport
        self._viewport_window: Any = None
        self._mounted_vp_api: Any = None
        self._vw: Any = None
        self._scene_view: Optional[sc.SceneView] = None
        self._root: Optional[sc.Transform] = None
        self._built = False
        self._post_update_sub: Any = None
        self._last_tick = 0.0
        self._sync_token: float = 0.0
        self._poll_error_logged: bool = False
        # 한번 만든 UI를 재사용(겹침/누적 방지)
        self._panel_nodes: Dict[int, Dict[str, Any]] = {}

    def _foup_toggle_on(self) -> bool:
        if self._screen <= 1:
            return bool(get_toggle_foup_status())
        m = getattr(self._csv, "_foup_status_show_model", None)
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
            # runtime 이 미리 넣어 둔 타일 창만 허용 (메인 Viewport 폴백 금지)
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
            lam = getattr(self._csv, "_lam_window_ref", None)
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
        # 예약된 delayed mount 취소
        was_built = bool(self._built)
        self._sync_token = time.time()
        _ACTIVE_FOUP_PANEL_BY_SCREEN.pop(self._screen, None)
        self._stop_poll()
        self._destroy_layer()
        if was_built:
            self._mount_logged = False
            print(
                f"{_PRINT_PREFIX} screen{self._screen} FOUP destroy (toggle OFF)",
                flush=True,
            )

    def reset_play_session(self) -> None:
        """정지(초기화) 후 3D 패널 표시만 리셋 (집계는 overlay_state)."""
        if self._built and self._root:
            self._update_ui()

    def sync_layers(self, *, delay_frames: int = 12) -> None:
        if not self._foup_toggle_on():
            if self._built:
                self.destroy()
            return
        if self._built and self._scene_view is not None and self._root is not None:
            # 이미 mount — 화면2+ 는 타일 창이 바뀌었으면 remount, 아니면 갱신만
            if self._screen > 1:
                target = None
                try:
                    # 캐시를 잠시 비워 최신 Dock/Widget 타일 resolve
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
            # 토글이 그 사이 OFF 되었으면 mount 금지
            if not self._foup_toggle_on():
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
                    f"{_PRINT_PREFIX} screen{self._screen} FOUP mount skip — "
                    "LAM_SimSplit / hud_mount 미준비",
                    flush=True,
                )

        _try(max(0, int(delay_frames)))

    def _rebuild(self) -> None:
        """이미 mount 된 패널 내용만 갱신 (sync_layers 재진입)."""
        if not self._foup_toggle_on():
            self.destroy()
            return
        self._update_ui()

    def _frame_slot(self) -> str:
        if self._screen > 1:
            return f"{_FRAME_SLOT}:screen{self._screen}"
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
        _ACTIVE_FOUP_PANEL_BY_SCREEN.pop(self._screen, None)
        owned = self._viewport_window
        sv = self._scene_view
        api = self._mounted_vp_api or self._resolve_vp_api(owned)
        # add_scene_view 로 붙인 SceneView 는 반드시 제거 (frame clear 만으로는 안 사라짐)
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
                _ACTIVE_PANEL_NODES_BY_VW.pop(id(owned), None)
        except Exception:
            pass
        self._built = False
        self._scene_view = None
        self._root = None
        self._vw = None
        self._viewport_window = None
        self._mounted_vp_api = None
        self._panel_nodes.clear()
        # 이 패널이 붙었던 창의 frame 만 비움 — 화면1(default Viewport) 절대 건드리지 않음
        if owned is not None and callable(getattr(owned, "get_frame", None)):
            try:
                with owned.get_frame(self._frame_slot()):
                    pass
            except Exception:
                pass

    def _mount(self, vw: Any) -> None:
        self._destroy_layer()
        if not self._foup_toggle_on():
            return
        host = vw
        vp_api = getattr(vw, "viewport_api", None)
        if vp_api is None and callable(getattr(vw, "add_scene_view", None)):
            # create_viewport_window / get_viewport_from_window_name 결과가 API 자체
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
            get_frame = getattr(host, "get_frame", None)
            if not callable(get_frame):
                print(
                    f"{_PRINT_PREFIX} screen{self._screen} mount skip — "
                    f"get_frame 없음 type={type(host).__name__} "
                    f"(Dock 창 resolve 확인)",
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
        _ACTIVE_FOUP_PANEL_BY_SCREEN[self._screen] = self
        self._start_poll()
        self._ensure_ui_built()
        try:
            _ACTIVE_PANEL_NODES_BY_VW[id(host)] = self._panel_nodes
        except Exception:
            pass
        self._update_ui()
        # mount 로그는 신규 attach 1회만 (sync 반복 스팸 방지)
        if not getattr(self, "_mount_logged", False):
            self._mount_logged = True
            print(
                f"{_PRINT_PREFIX} screen{self._screen} FOUP panel mounted",
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
            if not self._foup_toggle_on():
                self.destroy()
                return
            now = time.time()
            if now - self._last_tick < 0.2:
                return
            self._last_tick = now
            try:
                self._tick()
            except Exception as exc:
                if not self._poll_error_logged:
                    self._poll_error_logged = True
                    print(f"{_PRINT_PREFIX} poll tick failed: {exc}", flush=True)
                self._stop_poll()

        self._post_update_sub = stream.create_subscription_to_pop(
            _on,
            name=f"morph.lam_control_1:foup_status_3d:screen{self._screen}",
        )

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

    def _panel_layout_ok(self, node: Dict[str, Any]) -> bool:
        rows = node.get("rows")
        return (
            node.get("layout_ver") == _LAYOUT_VER
            and node.get("title") is not None
            and node.get("top_bar") is not None
            and isinstance(rows, list)
            and len(rows) == _BODY_ROW_COUNT
        )

    def _iter_node_labels(self, node: Dict[str, Any]) -> list:
        out: list = []
        title = node.get("title")
        if title is not None:
            out.append(title)
        for row in list(node.get("rows") or []):
            for key in ("left", "right"):
                w = row.get(key) if isinstance(row, dict) else None
                if w is not None:
                    out.append(w)
        return out

    def _clear_node_texts(self, node: Dict[str, Any]) -> None:
        for lbl in self._iter_node_labels(node):
            try:
                lbl.text = ""
            except Exception:
                pass

    def _ensure_ui_built(self) -> None:
        """Scene graph는 1회만 만든 뒤, text/transform만 업데이트."""
        if not self._built or not self._root:
            return
        if self._panel_nodes:
            for node in self._panel_nodes.values():
                if not self._panel_layout_ok(node):
                    self._panel_nodes.clear()
                    try:
                        self._root.clear()
                    except Exception:
                        pass
                    break
            else:
                return
        half_w = float(_PANEL_W) * 0.5
        half_h = float(_PANEL_H) * 0.5
        left_x = -half_w + float(_PAD_X)
        right_x = half_w - float(_PAD_X)
        title_y = half_h - float(_TOP_BAR_H) - (float(_TITLE_H) - float(_TOP_BAR_H)) * 0.5
        bar_y = half_h - float(_TOP_BAR_H) * 0.5
        div_y = half_h - float(_TITLE_H)
        body_top = half_h - float(_TITLE_H)
        slot_h = float(_BODY_H) / float(_BODY_ROW_COUNT)
        align_left = ui.Alignment.LEFT_CENTER
        align_right = getattr(ui.Alignment, "RIGHT_CENTER", None) or align_left
        title_font = int(FOUP_LOT_ID_FONT_SIZE)
        body_font = int(FOUP_PANEL_FONT_SIZE)

        with self._root:
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
                        bg = tuple(FOUP_PANEL_BG_RGBA)
                        sc.Rectangle(
                            width=_PANEL_W,
                            height=_PANEL_H,
                            color=bg,
                            wireframe=False,
                        )
                        with sc.Transform(
                            transform=sc.Matrix44.get_translation_matrix(0.0, bar_y, 0.0)
                        ):
                            top_bar = sc.Rectangle(
                                width=_PANEL_W,
                                height=_TOP_BAR_H,
                                color=_WHITE,
                                wireframe=False,
                            )
                        with sc.Transform(
                            transform=sc.Matrix44.get_translation_matrix(0.0, div_y, 0.0)
                        ):
                            sc.Rectangle(
                                width=_PANEL_W,
                                height=_DIVIDER_H,
                                color=tuple(FOUP_PANEL_DIVIDER_RGBA),
                                wireframe=False,
                            )
                        with sc.Transform(
                            transform=sc.Matrix44.get_translation_matrix(left_x, title_y, 0.0)
                        ):
                            title = sc.Label(
                                "",
                                size=title_font,
                                color=_WHITE,
                                alignment=align_left,
                            )
                        rows: list = []
                        for i in range(_BODY_ROW_COUNT):
                            y = body_top - slot_h * (float(i) + 0.5)
                            with sc.Transform(
                                transform=sc.Matrix44.get_translation_matrix(
                                    left_x, y, 0.0
                                )
                            ):
                                left_lbl = sc.Label(
                                    "",
                                    size=body_font,
                                    color=_WHITE,
                                    alignment=align_left,
                                )
                            with sc.Transform(
                                transform=sc.Matrix44.get_translation_matrix(
                                    right_x, y, 0.0
                                )
                            ):
                                right_lbl = sc.Label(
                                    "",
                                    size=body_font,
                                    color=_WHITE,
                                    alignment=align_right,
                                )
                            rows.append({"left": left_lbl, "right": right_lbl})
                self._panel_nodes[fi] = {
                    "layout_ver": _LAYOUT_VER,
                    "root": root,
                    "title": title,
                    "top_bar": top_bar,
                    "rows": rows,
                }

    def _update_ui(self) -> None:
        if not self._built or not self._root:
            return
        st = self._stage_for_panel()
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
                try:
                    node["root"].transform = sc.Matrix44.get_translation_matrix(
                        1e9, 1e9, 1e9
                    )
                except Exception:
                    pass
                self._clear_node_texts(node)
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

            c: FoupCounts = get_foup_counts(fi, screen=self._screen)
            lot_id = get_lot_id_for_foup(fi, screen=self._screen)
            lot_color = foup_lot_color_rgba(fi)
            try:
                node["top_bar"].color = lot_color
            except Exception:
                pass
            title = node.get("title")
            if title is not None:
                try:
                    title.text = str(lot_id or "")
                    title.color = lot_color
                    title.size = int(FOUP_LOT_ID_FONT_SIZE)
                except Exception:
                    pass
            values = (
                f"{c.current_in_foup_now}/{c.total}",
                str(int(c.in_process_count)),
                str(int(c.done_count)),
            )
            rows = list(node.get("rows") or [])
            for i, row in enumerate(rows[:_BODY_ROW_COUNT]):
                left = row.get("left") if isinstance(row, dict) else None
                right = row.get("right") if isinstance(row, dict) else None
                try:
                    if left is not None:
                        left.text = _BODY_ROW_LABELS[i]
                        left.color = _WHITE
                        left.size = int(FOUP_PANEL_FONT_SIZE)
                    if right is not None:
                        right.text = values[i]
                        right.color = _WHITE
                        right.size = int(FOUP_PANEL_FONT_SIZE)
                except Exception:
                    pass

    def _build_label(self, world_pos: Tuple[float, float, float], text: str) -> None:
        root = sc.Transform(
            look_at=sc.Transform.LookAt.CAMERA,
            transform=sc.Matrix44.get_translation_matrix(*world_pos),
        )
        with root:
            with sc.Transform(scale_to=sc.Space.SCREEN):
                sc.Rectangle(
                    width=_PANEL_W,
                    height=_PANEL_H,
                    color=tuple(FOUP_PANEL_BG_RGBA),
                    wireframe=False,
                )


__all__ = [
    "LamFoupStatus3dPanel",
    "force_remove_all_foup_sceneviews",
    "force_remove_foup_sceneviews",
    "refresh_foup_status_panel_ui",
    "reset_foup_play_session",
]

