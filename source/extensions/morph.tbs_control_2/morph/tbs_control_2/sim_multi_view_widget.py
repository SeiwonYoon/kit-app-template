"""
ViewportWidget 기반 2분할 — Workspace ``Viewport`` 탭 1개, ``get_frame`` HStack 50:50.

- Startup 시 ``ViewportWidget`` — 화면1 즉시 1회, 화면2는 **master_2 Stage 준비 후** 1회 (총 2회, 재생성 없음)
- 화면2를 빈 aux 컨텍스트에서 미리 만들지 않음 → RenderProduct 미생성 방지
- USD open 후 Stage/Context/Camera/Manipulator 만 연결
- ``TBS_SimSplit_*`` Workspace 창 / Dock / ``create_viewport_window`` **절대 사용 안 함**

``USE_VIEWPORT_WIDGET_SPLIT=False`` → Dock + ``create_viewport_window``.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

import omni.kit.app as kit_app
import omni.ui as ui

# ``ebs_control_hud`` 슬롯보다 아래(z-order)에 두기 위해 접두사 ``00_`` 사용.
_SPLIT_FRAME_SLOT = "morph.tbs_control_2:00_split_viewport_widgets"
_VP_TILE_MIN_PX = 64
_MAIN_TILE_CAMERA = "/OmniverseKit_Persp"
_AUX_TILE_CAMERA = "/OmniverseKit_Persp"
_DEFAULT_CAMERA = _MAIN_TILE_CAMERA
_WIDGET_CREATE_COUNT = 0


def sim_viewport_split_widget_enabled() -> bool:
    try:
        from .sim_control_defaults import USE_VIEWPORT_WIDGET_SPLIT

        if not bool(USE_VIEWPORT_WIDGET_SPLIT):
            return False
    except Exception:
        pass
    try:
        v = str(os.environ.get("TBS_SIM_VIEWPORT_WIDGET_SPLIT", "") or "").strip().lower()
    except Exception:
        return True
    if v in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    return True


def is_split_widget_layout_active(ext: Any) -> bool:
    return bool(getattr(ext, "_tbs_split_used_widget_layout", False))


def _tile_usd_context_name(tile_index: int) -> str:
    return "" if int(tile_index) <= 0 else f"morph_tbs_split_aux_{int(tile_index)}"


def _tile_win_name(tile_index: int) -> str:
    return "Viewport" if int(tile_index) <= 0 else f"TBS_SimSplit_{int(tile_index)}"


def _tile_camera_path(cell_idx: int) -> str:
    return _MAIN_TILE_CAMERA


def _lifecycle_id(obj: Any) -> str:
    if obj is None:
        return "None"
    try:
        return f"{id(obj):#x}"
    except Exception:
        return "?"


def _ui_parent_id(obj: Any) -> str:
    if obj is None:
        return "None"
    try:
        return _lifecycle_id(getattr(obj, "parent", None))
    except Exception:
        return "?"


def _widget_destroy_callbacks(widget: Any) -> str:
    if widget is None:
        return "n/a"
    try:
        fr = getattr(widget, "frame", None)
        if fr is None:
            return "no-frame"
        cbs = []
        for name in ("set_destroyed_fn", "set_destroy_fn"):
            if hasattr(fr, name):
                cbs.append(name)
        return ",".join(cbs) if cbs else "none"
    except Exception:
        return "?"


def _log_widget_lifecycle(ext: Any, phase: str, wn: str, rec: Optional[Dict[str, Any]]) -> None:
    """Widget / viewport_api / UI parent 수명 추적 — READY 전후 동일 id 인지 확인."""
    if not isinstance(rec, dict):
        try:
            print(f"[TBS widget-life] {phase} tile={wn!r} rec=None", flush=True)
        except Exception:
            pass
        return
    widget = rec.get("widget")
    api = rec.get("api")
    if api is None and widget is not None:
        try:
            api = getattr(widget, "viewport_api", None)
            if api is not None:
                rec["api"] = api
        except Exception:
            pass
    scene_view = rec.get("scene_view")
    try:
        print(
            f"[TBS widget-life] {phase} tile={wn!r} "
            f"widget={_lifecycle_id(widget)} api={_lifecycle_id(api)} "
            f"scene_view={_lifecycle_id(scene_view)} "
            f"widget.frame={_lifecycle_id(getattr(widget, 'frame', None))} "
            f"widget.parent={_ui_parent_id(widget)} "
            f"destroy_cbs={_widget_destroy_callbacks(widget)} "
            f"shell_hstack={_lifecycle_id(getattr(ext, '_tbs_widget_shell_hstack', None))} "
            f"create_count={int(getattr(ext, '_tbs_widget_create_total', 0) or 0)}",
            flush=True,
        )
    except Exception:
        pass


def _refresh_rec_api_from_widget(rec: Dict[str, Any], wn: str) -> Any:
    """Widget 에서 viewport_api 를 항상 다시 읽어 rec 과 동기화."""
    widget = rec.get("widget")
    if widget is None:
        if bool(rec.get("_deferred_create", False)):
            return None
        try:
            print(
                f"[TBS widget-life] api=None tile={wn!r} — widget already destroyed (no recreate)",
                flush=True,
            )
        except Exception:
            pass
        return None
    api = None
    try:
        api = getattr(widget, "viewport_api", None)
    except Exception:
        api = None
    if api is not None:
        rec["api"] = api
        return api
    rec["api"] = None
    try:
        print(
            f"[TBS widget-life] api=None tile={wn!r} widget={_lifecycle_id(widget)} still alive",
            flush=True,
        )
    except Exception:
        pass
    return None


def _get_native_viewport_api() -> Any:
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        return get_viewport_from_window_name("Viewport")
    except Exception:
        return None


def _destroy_all_aux_workspace_windows(ext: Any = None) -> None:
    """
    Dock 모드 잔여 ``TBS_SimSplit_*`` Workspace 창 제거.

    Widget 분할 모드에서는 ``TBS_SimSplit_1`` 이 **논리 타일명**이기도 하다.
    ``get_viewport_from_window_name('TBS_SimSplit_1')`` 이 embedded ``ViewportWidget`` API 를
    반환하면 ``_destroy_kit_viewport`` 가 RenderProduct 를 파괴한다 — **호출 금지**.
    """
    try:
        from .sim_multi_view import (
            _destroy_kit_viewport,
            _destroy_stale_split_workspace_window,
            _split_window_name,
            _workspace_show_named_window,
        )

        widget_mode = ext is not None and is_split_widget_layout_active(ext)

        if ext is not None and not widget_mode:
            try:
                for ent in list(getattr(ext, "_sim_multi_viewport_entries", []) or []):
                    wn = str(ent.get("win_name") or "")
                    if not wn.startswith("TBS_SimSplit"):
                        continue
                    for key in ("kit_vp", "viewport_window", "window"):
                        obj = ent.get(key)
                        if obj is not None:
                            _destroy_kit_viewport(obj)
                        ent[key] = None
            except Exception:
                pass
            tiles = getattr(ext, "_tbs_split_widget_tiles", None)
            if isinstance(tiles, dict):
                for rec in tiles.values():
                    if not isinstance(rec, dict):
                        continue
                    backend = rec.get("_backend_viewport")
                    if backend is not None:
                        _destroy_kit_viewport(backend)
                        rec["_backend_viewport"] = None

        for ti in range(1, 5):
            wn = _split_window_name(ti)
            if widget_mode:
                # ghost Workspace 탭만 숨김 — viewport API destroy 하지 않음
                try:
                    if ui.Workspace.get_window(wn) is not None:
                        _workspace_show_named_window(wn, False)
                except Exception:
                    pass
                continue
            _destroy_stale_split_workspace_window(wn)
            _workspace_show_named_window(wn, False)
    except Exception:
        pass


def _stage_has_usd_lights(stage: Any) -> bool:
    try:
        from pxr import UsdLux

        for prim in stage.Traverse():
            if prim.IsA(UsdLux.LightAPI):
                return True
    except Exception:
        pass
    return False


def _stage_lux_prim_paths(stage: Any) -> List[str]:
    """Stage 내 UsdLux 조명 prim 경로 목록."""
    paths: List[str] = []
    if stage is None:
        return paths
    try:
        from pxr import UsdLux

        light_types = frozenset(
            {
                "DomeLight",
                "DistantLight",
                "RectLight",
                "DiskLight",
                "SphereLight",
                "CylinderLight",
                "PortalLight",
            }
        )
        for prim in stage.Traverse():
            if prim.IsA(UsdLux.LightAPI) or prim.GetTypeName() in light_types:
                paths.append(str(prim.GetPath()))
    except Exception:
        pass
    return paths


def _ensure_world_xform(stage: Any) -> None:
    try:
        from pxr import UsdGeom

        if not stage.GetPrimAtPath("/World").IsValid():
            UsdGeom.Xform.Define(stage, "/World")
    except Exception:
        pass


def _ensure_prim_ancestor_xforms(stage: Any, prim_path: str) -> None:
    """session layer 에 조명 복제 전 상위 Xform 경로를 만든다."""
    try:
        from pxr import Sdf, Usd, UsdGeom

        parts = [p for p in str(prim_path or "").strip("/").split("/") if p]
        if not parts:
            return
        session = stage.GetSessionLayer()
        if session is None:
            return
        with Usd.EditContext(stage, session):
            cur = Sdf.Path("/")
            for part in parts[:-1]:
                cur = cur.AppendChild(part)
                if not stage.GetPrimAtPath(cur).IsValid():
                    UsdGeom.Xform.Define(stage, cur)
    except Exception:
        pass


def _sync_aux_stage_lighting_from_main(aux_ctx_name: str) -> int:
    """
    화면1(default ctx) UsdLux 스펙을 화면2 aux session layer 로 복제.
    generic DomeLight 대신 화면1과 동일 조명 환경을 목표로 한다.
    """
    aux_ctx_name = str(aux_ctx_name or "").strip()
    if not aux_ctx_name:
        return 0
    try:
        from .sim_control_defaults import VIEWPORT_AUX_LIGHTING_SYNC_FROM_MAIN

        if not bool(VIEWPORT_AUX_LIGHTING_SYNC_FROM_MAIN):
            return 0
    except Exception:
        pass

    main_ctx = _named_usd_context("")
    aux_ctx = _named_usd_context(aux_ctx_name)
    if main_ctx is None or aux_ctx is None:
        return 0
    try:
        main_stage = main_ctx.get_stage() if hasattr(main_ctx, "get_stage") else None
        aux_stage = aux_ctx.get_stage() if hasattr(aux_ctx, "get_stage") else None
    except Exception:
        return 0
    if main_stage is None or aux_stage is None:
        return 0

    copied = 0
    try:
        from pxr import Sdf, Usd, UsdUtils

        main_layer = main_stage.GetRootLayer()
        session = aux_stage.GetSessionLayer()
        if main_layer is None or session is None:
            return 0

        stale = aux_stage.GetPrimAtPath("/World/TBS_DefaultDomeLight")
        if stale is not None and stale.IsValid():
            with Usd.EditContext(aux_stage, session):
                try:
                    aux_stage.RemovePrim(Sdf.Path("/World/TBS_DefaultDomeLight"))
                except Exception:
                    pass

        src_paths = _stage_lux_prim_paths(main_stage)
        with Usd.EditContext(aux_stage, session):
            for src_path in src_paths:
                _ensure_prim_ancestor_xforms(aux_stage, src_path)
                dst_prim = aux_stage.GetPrimAtPath(src_path)
                if dst_prim is not None and dst_prim.IsValid():
                    continue
                try:
                    UsdUtils.CopySpec(main_layer, Sdf.Path(src_path), session, Sdf.Path(src_path))
                    if aux_stage.GetPrimAtPath(src_path).IsValid():
                        copied += 1
                except Exception:
                    pass
    except Exception as exc:
        try:
            print(
                f"[TBS multi-sim] aux 조명 동기화 실패 ctx={aux_ctx_name!r}: {exc}",
                flush=True,
            )
        except Exception:
            pass
        return copied

    if copied > 0:
        try:
            print(
                f"[TBS multi-sim] aux 조명 동기화 완료 ctx={aux_ctx_name!r} "
                f"copied={copied} paths={_stage_lux_prim_paths(aux_stage)}",
                flush=True,
            )
        except Exception:
            pass
    return copied


def _ensure_aux_stage_default_lighting(ctx_name: str) -> None:
    """보조 스테이지 조명 — 화면1 복제 우선, 없으면 generic DomeLight fallback."""
    ctx_name = str(ctx_name or "").strip()
    if not ctx_name:
        return
    synced = _sync_aux_stage_lighting_from_main(ctx_name)
    if synced > 0:
        return
    ctx = _named_usd_context(ctx_name)
    if ctx is None:
        return
    try:
        stage = ctx.get_stage() if hasattr(ctx, "get_stage") else None
    except Exception:
        stage = None
    if stage is None:
        return
    if _stage_has_usd_lights(stage):
        return
    try:
        from pxr import Gf, UsdLux

        _ensure_world_xform(stage)
        light_path = "/World/TBS_DefaultDomeLight"
        if stage.GetPrimAtPath(light_path).IsValid():
            return
        dome = UsdLux.DomeLight.Define(stage, light_path)
        dome.CreateIntensityAttr(300.0)
        dome.CreateColorAttr(Gf.Vec3f(1.0, 1.0, 1.0))
        try:
            print(
                f"[TBS multi-sim] 보조 스테이지 fallback DomeLight 추가 ctx={ctx_name!r}",
                flush=True,
            )
        except Exception:
            pass
    except Exception as exc:
        try:
            print(
                f"[TBS multi-sim] 보조 스테이지 조명 추가 실패 ctx={ctx_name!r}: {exc}",
                flush=True,
            )
        except Exception:
            pass


async def connect_widget_tile_main_stage(ext: Any, token: int = 0) -> bool:
    """
    master_1.usd open 후 화면1 Widget 에 기본 Context/Stage 연결.
    ``ViewportWidget()`` 호출 없음.
    """
    if not is_split_widget_layout_active(ext):
        return False
    if bool(getattr(ext, "_tbs_main_stage_connected", False)):
        return True
    tiles = getattr(ext, "_tbs_split_widget_tiles", None)
    if not isinstance(tiles, dict):
        return False
    main_rec = tiles.get("Viewport")
    if not isinstance(main_rec, dict) or main_rec.get("widget") is None:
        return False

    _log_widget_lifecycle(ext, "connect-main-before", "Viewport", main_rec)
    api = _refresh_rec_api_from_widget(main_rec, "Viewport")
    if api is None:
        return False

    main_rec["_win_name"] = "Viewport"
    _bind_tile_viewport_to_context(main_rec, "", cam_path=_MAIN_TILE_CAMERA)

    ref_api = _reference_viewport_render_api(ext, tiles)
    api = main_rec.get("api")
    if ref_api is not None and api is not None:
        _copy_visual_render_profile_only(ref_api, api)

    main_rec["stage_connected"] = True
    _kick_viewport_widget_render(main_rec)
    _log_hydra_pipeline_diag("Viewport", main_rec, "connect-main")

    try:
        ext._tbs_main_stage_connected = True
    except Exception:
        pass

    _log_widget_lifecycle(ext, "connect-main-after", "Viewport", main_rec)
    try:
        print("[TBS multi-sim] 화면1 Stage 연결 (Widget 재생성 없음)", flush=True)
    except Exception:
        pass
    return True


async def assign_widget_split_cameras(
    ext: Any, token: int, win_names: Optional[List[str]] = None
) -> None:
    """Widget 분할 — 타일별 camera_path 1회 설정 (스테이지별 독립 prim)."""
    if not is_split_widget_layout_active(ext):
        return
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != int(token):
        return
    names = list(win_names or [])
    if not names:
        names = ["Viewport", _tile_win_name(1)]
    _bind_widget_split_cameras_once(ext, names)
    _activate_tile_manipulator_only(
        ext, str(getattr(ext, "_tbs_active_widget_tile", "") or "Viewport")
    )


def _bind_widget_split_cameras_once(ext: Any, win_names: List[str]) -> None:
    """타일 stage 에 유효한 camera prim 이 있으면 api.camera_path 1회 설정."""
    for wn in win_names:
        wn = str(wn)
        api = get_split_viewport_api(ext, wn)
        if api is None:
            continue
        tiles = getattr(ext, "_tbs_split_widget_tiles", None)
        rec = tiles.get(wn) if isinstance(tiles, dict) else None
        ctx_name = ""
        if isinstance(rec, dict):
            ctx_name = str(rec.get("context_name") or "")
            if not ctx_name and wn != "Viewport":
                ctx_name = _tile_usd_context_name(int(rec.get("cell_index", 1) or 1))
        ctx = _named_usd_context(ctx_name)
        stage = ctx.get_stage() if ctx is not None and hasattr(ctx, "get_stage") else None
        cam = _resolve_camera_path_for_stage(stage, _MAIN_TILE_CAMERA)
        if cam is None:
            continue
        try:
            api.camera_path = cam
            if isinstance(rec, dict):
                rec["camera_path"] = str(cam)
        except Exception:
            pass


def _disable_native_viewport_navigation_permanent(ext: Any) -> None:
    """Widget 분할 — Workspace 네이티브 Viewport manipulator·입력 영구 비활성."""
    if not is_split_widget_layout_active(ext):
        return
    _disable_viewport_window_camera_bindings(ext)
    try:
        from .sim_multi_view import (
            _collect_camera_manipulator_models_for_window,
            _set_model_navigation_enabled,
        )

        for model in _collect_camera_manipulator_models_for_window("Viewport"):
            _set_model_navigation_enabled(model, False)
    except Exception:
        pass
    native_api = _get_native_viewport_api()
    if native_api is not None and id(native_api) not in _our_widget_tile_api_ids(ext):
        for attr in ("enable_input", "inputs_enabled"):
            if hasattr(native_api, attr):
                try:
                    setattr(native_api, attr, False)
                except Exception:
                    pass


_CAM_BINDINGS_CARB_KEY = "/exts/omni.kit.viewport.window/bindings/camera"


def _disable_viewport_window_camera_bindings(ext: Any) -> None:
    """ViewportWindow 전역 camera mouse bindings 비활성 — native manipulator 경로 차단."""
    if not is_split_widget_layout_active(ext):
        return
    try:
        from .sim_control_defaults import VIEWPORT_DISABLE_NATIVE_CAMERA_BINDINGS

        if not bool(VIEWPORT_DISABLE_NATIVE_CAMERA_BINDINGS):
            return
    except Exception:
        pass
    if bool(getattr(ext, "_tbs_camera_bindings_disabled", False)):
        return
    try:
        import carb.settings

        settings = carb.settings.get_settings()
        if not hasattr(ext, "_tbs_camera_bindings_saved"):
            try:
                ext._tbs_camera_bindings_saved = settings.get(_CAM_BINDINGS_CARB_KEY)
            except Exception:
                ext._tbs_camera_bindings_saved = None
        settings.set(_CAM_BINDINGS_CARB_KEY, {})
        ext._tbs_camera_bindings_disabled = True
        try:
            print(
                "[TBS multi-sim] ViewportWindow camera bindings disabled "
                f"(key={_CAM_BINDINGS_CARB_KEY!r})",
                flush=True,
            )
        except Exception:
            pass
    except Exception as exc:
        try:
            print(f"[TBS multi-sim] camera bindings disable fail: {exc}", flush=True)
        except Exception:
            pass


def _activate_tile_manipulator_only(ext: Any, win_name: str) -> None:
    """활성 타일 manipulator model 만 navigation on — focus/enable_input 사용 안 함."""
    wn = str(win_name or "").strip() or "Viewport"
    try:
        ext._tbs_active_widget_tile = wn
    except Exception:
        pass
    tiles = getattr(ext, "_tbs_split_widget_tiles", None)
    if not isinstance(tiles, dict):
        _disable_native_viewport_navigation_permanent(ext)
        return
    for name, rec in tiles.items():
        if not isinstance(rec, dict):
            continue
        _set_tile_manipulator_navigation(rec, str(name) == wn)
    _disable_native_viewport_navigation_permanent(ext)
    try:
        from .sim_viewport_coupling_diag import log_manipulator_activation_state

        log_manipulator_activation_state(ext, wn, "activate")
    except Exception:
        pass


def _enforce_widget_tile_manipulator_isolation(ext: Any) -> None:
    """활성 타일 manipulator 만 켜고 네이티브 Viewport 조작은 항상 끈다."""
    active = str(getattr(ext, "_tbs_active_widget_tile", "") or "Viewport")
    _activate_tile_manipulator_only(ext, active)


def _named_usd_context(ctx_name: str) -> Any:
    try:
        import omni.usd

        if not str(ctx_name or "").strip():
            return omni.usd.get_context()
        return omni.usd.get_context(str(ctx_name))
    except Exception:
        return None


def _stage_has_renderable_content(ctx_name: str) -> bool:
    try:
        ctx = _named_usd_context(ctx_name)
        if ctx is None:
            return False
        st = ctx.get_stage() if hasattr(ctx, "get_stage") else None
        if st is None:
            return False
        return bool(st.GetPseudoRoot().GetChildren())
    except Exception:
        return False


class _WidgetHudMount:
    """보조 타일 HUD 용 ``get_frame``."""

    def __init__(self, overlay: Any) -> None:
        self._overlay = overlay

    @contextmanager
    def get_frame(self, _slot: str):
        yield self._overlay


def _resolve_main_viewport_window(ext: Any) -> Any:
    """EBS HUD 와 동일 — ``get_frame`` 제공 ``ViewportWindow``."""
    try:
        vw = getattr(ext, "_tbs_split_main_viewport_window", None)
        if vw is not None and callable(getattr(vw, "get_frame", None)):
            return vw
    except Exception:
        pass
    try:
        from omni.kit.viewport.utility import get_active_viewport_window

        win = get_active_viewport_window()
        if win is not None and callable(getattr(win, "get_frame", None)):
            return win
    except Exception:
        pass
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        api = get_viewport_from_window_name("Viewport")
        for attr in ("viewport_window", "window", "_viewport_window", "_window"):
            cand = getattr(api, attr, None) if api is not None else None
            if cand is not None and callable(getattr(cand, "get_frame", None)):
                return cand
        if api is not None and callable(getattr(api, "get_frame", None)):
            return api
    except Exception:
        pass
    try:
        w = ui.Workspace.get_window("Viewport")
        if w is not None and callable(getattr(w, "get_frame", None)):
            return w
    except Exception:
        pass
    return None


def ensure_viewport_workspace_tab_visible() -> None:
    """Widget 분할: Workspace ``Viewport`` 기본 탭 유지 (Dock 전용 noTabBar 미적용)."""
    try:
        wui = ui.Workspace.get_window("Viewport")
        if wui is None:
            return
        try:
            wui.noTabBar = False
        except Exception:
            pass
        for attr in ("dock_tab_bar_enabled", "dock_tab_bar_visible"):
            try:
                if hasattr(wui, attr):
                    setattr(wui, attr, True)
            except Exception:
                pass
    except Exception:
        pass


def _suspend_native_viewport_widget_presenter(ext: Any) -> None:
    """
    ``ViewportWindow`` 내장 ``viewport_widget`` 숨김 — ``get_frame`` HStack 타일만 표시.
    내장 Widget 이 전체 창을 그리면 HStack 타일과 겹쳐 깜빡임이 난다.
    """
    if not is_split_widget_layout_active(ext):
        return
    try:
        vw_host = getattr(ext, "_tbs_split_main_viewport_window", None) or _resolve_main_viewport_window(
            ext
        )
        if vw_host is None:
            return
        candidates: List[Any] = []
        for attr in ("viewport_widget", "_viewport_widget"):
            candidates.append(getattr(vw_host, attr, None))
        try:
            from omni.kit.viewport.utility import get_viewport_from_window_name

            native_api = get_viewport_from_window_name("Viewport")
            if native_api is not None:
                for attr in ("widget", "_widget", "viewport_widget"):
                    candidates.append(getattr(native_api, attr, None))
        except Exception:
            pass
        saved = getattr(ext, "_tbs_native_vp_presenter_state", None)
        if not isinstance(saved, dict):
            saved = {}
            try:
                ext._tbs_native_vp_presenter_state = saved
            except Exception:
                pass
        for obj in candidates:
            if obj is None:
                continue
            try:
                oid = id(obj)
            except Exception:
                continue
            if oid not in saved:
                snap: Dict[str, Any] = {}
                for attr in ("visible", "enabled", "updates_enabled"):
                    try:
                        snap[attr] = getattr(obj, attr, None)
                    except Exception:
                        pass
                saved[oid] = snap
            for attr in ("visible",):
                if hasattr(obj, attr):
                    try:
                        setattr(obj, attr, False)
                    except Exception:
                        pass
            for attr in ("updates_enabled", "enabled"):
                if hasattr(obj, attr):
                    try:
                        setattr(obj, attr, False)
                    except Exception:
                        pass
    except Exception:
        pass


def _restore_native_viewport_widget_presenter(ext: Any) -> None:
    saved = getattr(ext, "_tbs_native_vp_presenter_state", None)
    if not isinstance(saved, dict):
        return
    try:
        ext._tbs_native_vp_presenter_state = {}
    except Exception:
        pass


def _our_widget_tile_api_ids(ext: Any) -> set[int]:
    ids: set[int] = set()
    tiles = getattr(ext, "_tbs_split_widget_tiles", None)
    if not isinstance(tiles, dict):
        return ids
    for rec in tiles.values():
        if not isinstance(rec, dict):
            continue
        api = rec.get("api")
        if api is not None:
            ids.add(id(api))
    return ids


def _suspend_orphan_viewport_manipulators(ext: Any) -> None:
    """``ViewportWidget.get_instances`` 중 우리 타일이 아닌 고아 인스턴스 manipulator 끔."""
    our_ids = _our_widget_tile_api_ids(ext)
    try:
        from omni.kit.widget.viewport import ViewportWidget

        from .sim_multi_view import _set_model_navigation_enabled

        instances = ViewportWidget.get_instances()
        if callable(instances):
            instances = instances()
        for inst in list(instances or []):
            api = getattr(inst, "viewport_api", None) or inst
            if id(api) in our_ids:
                continue
            for model in _collect_camera_manipulator_models_for_window_from_api(api):
                _set_model_navigation_enabled(model, False)
            for attr in ("enable_input", "inputs_enabled"):
                if hasattr(api, attr):
                    try:
                        setattr(api, attr, False)
                    except Exception:
                        pass
    except Exception:
        pass


def _collect_camera_manipulator_models_for_window_from_api(api: Any) -> List[Any]:
    models: List[Any] = []
    if api is None:
        return models
    try:
        from .sim_multi_view import _is_viewport_camera_manipulator_model
    except Exception:
        return models
    for attr in (
        "camera_manipulator",
        "_camera_manipulator",
        "manipulator",
        "camera_model",
        "_camera_model",
    ):
        obj = getattr(api, attr, None)
        if obj is not None and _is_viewport_camera_manipulator_model(obj):
            models.append(obj)
    return models


def _suspend_native_viewport_manipulators(ext: Any = None) -> None:
    """Widget 분할 시 네이티브·고아 Viewport manipulator 끔 (embedded 타일 manipulator 제외)."""
    try:
        from .sim_multi_view import (
            _collect_camera_manipulator_models_for_window,
            _set_model_navigation_enabled,
        )

        # Widget 모드: ``TBS_SimSplit_*`` 는 논리 타일명 — embedded API 와 충돌하므로 제외.
        win_names = ["Viewport"]
        try:
            if ext is None:
                from .tbs_extension_singleton import get_tbs_extension_instance

                ext = get_tbs_extension_instance()
            if ext is not None and not is_split_widget_layout_active(ext):
                win_names.append(_tile_win_name(1))
        except Exception:
            pass
        for wn in win_names:
            for model in _collect_camera_manipulator_models_for_window(str(wn)):
                _set_model_navigation_enabled(model, False)
        if ext is not None and is_split_widget_layout_active(ext):
            _suspend_orphan_viewport_manipulators(ext)
    except Exception:
        pass
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        api = get_viewport_from_window_name("Viewport")
        if api is not None and id(api) not in _our_widget_tile_api_ids(ext):
            for attr in ("enable_input", "inputs_enabled"):
                if hasattr(api, attr):
                    try:
                        setattr(api, attr, False)
                    except Exception:
                        pass
            try:
                blur_fn = getattr(api, "blur", None)
                if callable(blur_fn):
                    blur_fn()
            except Exception:
                pass
    except Exception:
        pass
    try:
        from .tbs_extension_singleton import get_tbs_extension_instance

        ext = get_tbs_extension_instance()
        if ext is not None and is_split_widget_layout_active(ext):
            tiles = getattr(ext, "_tbs_split_widget_tiles", None)
            active = str(getattr(ext, "_tbs_active_widget_tile", "") or "Viewport")
            if isinstance(tiles, dict):
                for name, rec in tiles.items():
                    if not isinstance(rec, dict):
                        continue
                    _set_tile_manipulator_navigation(rec, str(name) == active)
    except Exception:
        pass


def _stop_native_viewport_input_guard(ext: Any) -> None:
    sub = getattr(ext, "_tbs_widget_native_guard_sub", None)
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
    try:
        ext._tbs_widget_native_guard_sub = None
    except Exception:
        pass


def _start_native_viewport_input_guard(ext: Any, token: int) -> None:
    """Kit 이 manipulator 를 재생성해도 네이티브 Viewport 입력·조작이 Widget 타일을 가로채지 않게."""
    _stop_native_viewport_input_guard(ext)

    def _tick(_ev: Any = None) -> None:
        if not is_split_widget_layout_active(ext):
            _stop_native_viewport_input_guard(ext)
            return
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != int(token):
            _stop_native_viewport_input_guard(ext)
            return
        _set_native_viewport_input_blocked(ext, True)
        _suspend_native_viewport_widget_presenter(ext)
        _destroy_all_aux_workspace_windows(ext)
        _enforce_widget_tile_manipulator_isolation(ext)

    try:
        ext._tbs_widget_native_guard_sub = (
            kit_app.get_app()
            .get_post_update_event_stream()
            .create_subscription_to_pop(_tick, name="morph.tbs_control_2.widget_native_guard")
        )
    except Exception:
        pass


def _set_native_viewport_input_blocked(ext: Any, blocked: bool) -> None:
    """Widget 분할 — 네이티브 Viewport 입력만 차단 (렌더 펌프는 유지)."""
    try:
        if blocked:
            _suspend_native_viewport_manipulators(ext)
        from omni.kit.viewport.utility import get_viewport_from_window_name

        api = get_viewport_from_window_name("Viewport")
        if api is not None and id(api) in _our_widget_tile_api_ids(ext):
            api = None
        if api is None:
            try:
                ext._tbs_widget_native_vp_suspended = bool(blocked)
            except Exception:
                pass
            return
        if blocked:
            for attr in ("enable_input", "inputs_enabled"):
                if hasattr(api, attr):
                    try:
                        setattr(api, attr, False)
                    except Exception:
                        pass
            try:
                blur_fn = getattr(api, "blur", None)
                if callable(blur_fn):
                    blur_fn()
            except Exception:
                pass
        else:
            for attr in ("enable_input", "inputs_enabled"):
                if hasattr(api, attr):
                    try:
                        setattr(api, attr, True)
                    except Exception:
                        pass
        try:
            ext._tbs_widget_native_vp_suspended = bool(blocked)
        except Exception:
            pass
    except Exception:
        pass


def _set_native_viewport_updates_enabled(ext: Any, enabled: bool) -> None:
    """레거시 — teardown 시 네이티브 Viewport 전체 복원."""
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        api = get_viewport_from_window_name("Viewport")
        if api is None:
            return
        for attr in ("updates_enabled", "enabled", "enable_input", "inputs_enabled"):
            if hasattr(api, attr):
                try:
                    setattr(api, attr, bool(enabled))
                except Exception:
                    pass
        if not enabled:
            try:
                blur_fn = getattr(api, "blur", None)
                if callable(blur_fn):
                    blur_fn()
            except Exception:
                pass
            _suspend_native_viewport_manipulators(ext)
        try:
            ext._tbs_widget_native_vp_suspended = not bool(enabled)
        except Exception:
            pass
    except Exception:
        pass


def _destroy_viewport_widget(widget: Any) -> None:
    if widget is None:
        return
    for meth in ("destroy", "close"):
        fn = getattr(widget, meth, None)
        if callable(fn):
            try:
                fn()
                return
            except Exception:
                pass


def _destroy_tile_manipulator(rec: Dict[str, Any]) -> None:
    manip = rec.get("camera_manipulator")
    if manip is not None:
        try:
            fn = getattr(manip, "destroy", None)
            if callable(fn):
                fn()
        except Exception:
            pass
    scene_view = rec.get("scene_view")
    api = rec.get("api")
    if api is not None and scene_view is not None:
        try:
            rm = getattr(api, "remove_scene_view", None)
            if callable(rm):
                rm(scene_view)
        except Exception:
            pass


def _attach_camera_manipulator(api: Any, scene_view: Any) -> Any:
    """
  Kit ``add_scene_view`` 가 ``SceneCameraModel``(view/projection) 을 scene_view.model 에 둔다.
  ``scene_view.model = manip.model`` 로 덮으면 projection 동기화가 깨져 화면2 미렌더·입력 오류가 난다.
    """
    if api is None or scene_view is None:
        return None
    try:
        from omni.kit.manipulator.camera import ViewportCameraManipulator
    except Exception:
        return None
    manip = None
    try:
        with scene_view.scene:
            manip = ViewportCameraManipulator(api)
        try:
            model = getattr(manip, "model", None)
            if model is not None:
                model.set_ints("disable_undo", [1])
        except Exception:
            pass
    except Exception:
        manip = None
    return manip


def _set_tile_manipulator_navigation(rec: Dict[str, Any], enabled: bool) -> None:
    if bool(rec.get("_uses_viewport_window", False)):
        wn = str(rec.get("_win_name") or "")
        if not wn:
            return
        try:
            from .sim_multi_view import (
                _collect_camera_manipulator_models_for_window,
                _ensure_viewport_camera_navigation_enabled,
                _set_model_navigation_enabled,
            )

            if enabled:
                _ensure_viewport_camera_navigation_enabled(wn)
                return
            for model in _collect_camera_manipulator_models_for_window(wn):
                _set_model_navigation_enabled(model, False)
        except Exception:
            pass
        return
    manip = rec.get("camera_manipulator")
    model = getattr(manip, "model", None) if manip is not None else None
    if model is None:
        return
    try:
        from .sim_multi_view import _set_model_navigation_enabled

        _set_model_navigation_enabled(model, bool(enabled))
    except Exception:
        pass


def _purge_widget_mode_stale_windows(ext: Any = None) -> None:
    """Widget 분할 — ``TBS_SimSplit_*`` Workspace 창이 있으면 즉시 제거."""
    _destroy_all_aux_workspace_windows(ext)


def _purge_aux_workspace_windows(ext: Any = None) -> None:
    """Widget 분할 — 보조 Workspace 창 금지. Dock 모드만 잔여 창 정리."""
    if ext is not None and is_split_widget_layout_active(ext):
        _destroy_all_aux_workspace_windows(ext)
        return
    try:
        from .sim_multi_view import (
            _destroy_stale_split_workspace_window,
            _split_window_name,
            _workspace_show_named_window,
        )

        keep: set[str] = set()
        if ext is not None:
            try:
                for ent in list(getattr(ext, "_sim_multi_viewport_entries", []) or []):
                    wn = str(ent.get("win_name") or "")
                    if not wn.startswith("TBS_SimSplit"):
                        continue
                    if ent.get("kit_vp") is not None or ent.get("viewport_window") is not None:
                        keep.add(wn)
            except Exception:
                pass
        for ti in range(1, 5):
            wn = _split_window_name(ti)
            if wn in keep:
                continue
            _destroy_stale_split_workspace_window(wn)
            _workspace_show_named_window(wn, False)
    except Exception:
        pass


def _aux_viewport_entry(ext: Any, aux_wn: str) -> Optional[Dict[str, Any]]:
    try:
        for ent in list(getattr(ext, "_sim_multi_viewport_entries", []) or []):
            if str(ent.get("win_name") or "") == str(aux_wn):
                return ent
    except Exception:
        pass
    return None


def _reset_promoted_widget_aux_entries(ext: Any) -> None:
    """이전 하이브리드 빌드가 ``widget_aux`` → ``aux_viewport`` 로 승격한 entry 를 되돌린다."""
    try:
        entries = list(getattr(ext, "_sim_multi_viewport_entries", []) or [])
        changed = False
        for ent in entries:
            if ent.get("kind") != "aux_viewport":
                continue
            wn = str(ent.get("win_name") or "")
            if not wn.startswith("TBS_SimSplit"):
                continue
            ent["kind"] = "widget_aux"
            ent["kit_vp"] = None
            ent["viewport_window"] = None
            ent["window"] = None
            changed = True
        if changed:
            ext._sim_multi_viewport_entries = entries
    except Exception:
        pass


def _set_active_widget_tile(ext: Any, win_name: str) -> None:
    _activate_tile_manipulator_only(ext, win_name)


def _enable_tile_manipulator_navigation(rec: Dict[str, Any]) -> None:
    _set_tile_manipulator_navigation(rec, True)


_RENDER_PROFILE_ATTRS = (
    "rendering_mode",
    "shading_mode",
    "hdr",
    "show_grid",
    "grid_scale",
    "ambient_light_color",
    "ambient_light_intensity",
    "display_render_var",
    "resolution_scale",
    "lock_to_render_result",
    "background_color",
    "background_enable",
    "show_fps",
    "scene_visibility",
)

_RENDER_PROFILE_SKIP_COPY = frozenset(
    {
        "stage",
        "usd_context",
        "hydra_engine",
        "hd_engine",
        "camera_path",
        "resolution",
        "full_resolution",
        "render_product_path",
        "id",
        "viewport_id",
        "texture",
        "hydra_texture",
        "renderer",
        "projection",
    }
)


def _api_has_render_product(api: Any) -> bool:
    if api is None:
        return False
    fn = getattr(api, "get_render_product_path", None)
    if callable(fn):
        try:
            path = fn()
            if path is not None and str(path).strip():
                return True
        except Exception:
            pass
    try:
        path = getattr(api, "render_product_path", None)
        return path is not None and str(path).strip() != ""
    except Exception:
        return False


def _new_aux_tile_slot_record(
    ext: Any,
    wn: str,
    ctx_name: str,
    cell_idx: int,
    half_w: int,
    th: int,
    *,
    aux_zstack: Any,
) -> Dict[str, Any]:
    """화면2 슬롯 — ``master_2`` 로드 전 Rectangle 만 (ViewportWidget 미생성)."""
    return {
        "widget": None,
        "scene_view": None,
        "scene_view_registered": False,
        "camera_manipulator": None,
        "manip_pending": True,
        "api": None,
        "hud_mount": None,
        "viewport_window": None,
        "context_name": ctx_name,
        "cell_index": int(cell_idx),
        "camera_path": _tile_camera_path(cell_idx),
        "_last_w": int(half_w),
        "_last_h": int(th),
        "_backend_viewport": None,
        "_viewport_api_bridge": False,
        "stage_connected": False,
        "_widget_create_index": 0,
        "_ctor_hd_engine_passed": False,
        "_deferred_create": True,
        "_aux_zstack": aux_zstack,
        "_win_name": wn,
    }


def _create_deferred_aux_viewport_widget(ext: Any, aux_rec: Dict[str, Any]) -> bool:
    """
    ``master_2`` 가 aux 컨텍스트에 올라온 뒤 ViewportWidget #2 를 **최초 1회** 생성.
    (재생성·Placeholder 교체 아님 — shell 에서는 Rectangle 만 있었음)
    """
    if not isinstance(aux_rec, dict):
        return False
    if aux_rec.get("widget") is not None:
        return True
    if not bool(aux_rec.get("_deferred_create", False)):
        return False
    if int(_WIDGET_CREATE_COUNT) != 1:
        try:
            print(
                f"[TBS/hydra-diag] WARN deferred aux create: "
                f"create_count={_WIDGET_CREATE_COUNT} (expect 1 before #2)",
                flush=True,
            )
        except Exception:
            pass

    aux_wn = str(aux_rec.get("_win_name") or _tile_win_name(1))
    aux_ctx = str(aux_rec.get("context_name") or _tile_usd_context_name(1))
    zstack = aux_rec.get("_aux_zstack") or getattr(ext, "_tbs_widget_shell_zstack_aux", None)
    if zstack is None:
        try:
            print(f"[TBS/hydra-diag] deferred aux create FAIL: no zstack tile={aux_wn!r}", flush=True)
        except Exception:
            pass
        return False

    try:
        from omni.kit.widget.viewport import ViewportWidget
    except Exception as exc:
        try:
            print(f"[TBS/hydra-diag] deferred aux ViewportWidget import FAIL: {exc}", flush=True)
        except Exception:
            pass
        return False

    half_w = int(aux_rec.get("_last_w", 640) or 640)
    th = int(aux_rec.get("_last_h", 480) or 480)
    try:
        print(
            f"[TBS/hydra-diag] deferred aux ViewportWidget create START "
            f"tile={aux_wn!r} ctx={aux_ctx!r} ctx_stage={_stage_identity(_tile_stage(aux_rec))!r}",
            flush=True,
        )
    except Exception:
        pass

    built = _create_viewport_tile(
        ext,
        aux_wn,
        aux_ctx,
        1,
        half_w,
        th,
        ViewportWidget=ViewportWidget,
        hd_engine=None,
        ui_container=zstack,
        include_background_rect=False,
    )
    if built is None:
        return False

    aux_rec.update(built)
    aux_rec["_deferred_create"] = False
    aux_rec.pop("_aux_zstack", None)
    aux_rec["_win_name"] = aux_wn
    try:
        ext._tbs_widget_create_total = int(_WIDGET_CREATE_COUNT)
    except Exception:
        pass
    try:
        print(
            f"[TBS/hydra-diag] deferred aux ViewportWidget create DONE "
            f"create_total={_WIDGET_CREATE_COUNT} "
            f"has_render_product={_api_has_render_product(aux_rec.get('api'))}",
            flush=True,
        )
    except Exception:
        pass
    return aux_rec.get("widget") is not None


def _probe_viewport_render_pipeline(api: Any, widget: Any = None) -> Dict[str, str]:
    """Viewport API / Widget 의 Hydra·RenderProduct 관련 필드 스냅샷."""
    out: Dict[str, str] = {}
    targets: List[Tuple[str, Any]] = [("api", api)]
    if widget is not None:
        targets.append(("widget", widget))

    attr_names = (
        "hydra_engine",
        "hd_engine",
        "hydra",
        "render_product",
        "render_product_path",
        "hydra_texture",
        "renderer",
        "render_delegate",
        "texture",
        "resolution",
        "fps",
        "render_mode",
        "rendering_mode",
        "camera_path",
        "fill_frame",
        "updates_enabled",
        "enabled",
        "usd_context_name",
        "context_name",
    )
    for label, obj in targets:
        if obj is None:
            out[f"{label}"] = "None"
            continue
        for attr in attr_names:
            key = f"{label}.{attr}"
            try:
                val = getattr(obj, attr, None)
            except Exception as exc:
                out[key] = f"err:{exc}"
                continue
            if val is None:
                out[key] = "None"
            elif isinstance(val, (int, float, bool)):
                out[key] = repr(val)
            elif isinstance(val, str):
                out[key] = val if len(val) < 120 else val[:117] + "..."
            else:
                try:
                    tname = type(val).__name__
                except Exception:
                    tname = "?"
                try:
                    out[key] = f"{tname}@{id(val):#x}"
                except Exception:
                    out[key] = tname

    if api is not None:
        for meth in (
            "get_render_product_path",
            "get_hydra_texture",
            "get_texture",
            "get_renderer",
            "get_render_product",
        ):
            fn = getattr(api, meth, None)
            if not callable(fn):
                continue
            key = f"api.{meth}()"
            try:
                val = fn()
                if val is None:
                    out[key] = "None"
                elif isinstance(val, (int, float, bool, str)):
                    out[key] = repr(val)
                else:
                    out[key] = f"{type(val).__name__}@{id(val):#x}"
            except Exception as exc:
                out[key] = f"err:{exc}"

    return out


def _format_pipeline_probe(probe: Dict[str, str]) -> str:
    if not probe:
        return "(empty)"
    keys = sorted(probe.keys())
    return " ".join(f"{k}={probe[k]}" for k in keys)


def _log_hydra_pipeline_diag(
    wn: str,
    rec: Dict[str, Any],
    phase: str,
    *,
    extra: str = "",
) -> None:
    """Hydra / RenderProduct / resolution 생성 여부 — widget-create · connect · READY."""
    if isinstance(rec, dict):
        _refresh_rec_api_from_widget(rec, str(wn))
    api = rec.get("api") if isinstance(rec, dict) else None
    widget = rec.get("widget") if isinstance(rec, dict) else None
    if api is None and widget is not None:
        try:
            api = getattr(widget, "viewport_api", None)
        except Exception:
            api = None
    ctx_stage = _tile_stage(rec) if isinstance(rec, dict) else None
    probe = _probe_viewport_render_pipeline(api, widget)
    hd_kw = "?"
    if isinstance(rec, dict):
        hd_kw = "omitted" if not rec.get("_ctor_hd_engine_passed") else "passed"
    try:
        print(
            f"[TBS/hydra-diag] phase={phase!r} tile={wn!r} "
            f"ctor_hd_engine={hd_kw} "
            f"ctx_stage={_stage_identity(ctx_stage)!r} "
            f"{_format_pipeline_probe(probe)}"
            f"{(' ' + extra) if extra else ''}",
            flush=True,
        )
    except Exception:
        pass
    try:
        from .sim_viewport_rp_diag import log_rp_investigation, viewport_rp_diag_enabled

        if viewport_rp_diag_enabled():
            scene_view = rec.get("scene_view") if isinstance(rec, dict) else None
            log_rp_investigation(
                phase,
                str(wn),
                api,
                widget,
                scene_view=scene_view,
                extra=extra,
            )
    except Exception:
        pass


async def _wait_aux_usd_stage_settled(
    ext: Any, ctx_name: str, token: int, *, max_frames: int = 36
) -> bool:
    """
    master_2 open 직후 ``Stage opening or closing already in progress`` 경합 완화.
    Hydra attach 전에 aux 컨텍스트 Stage 가 안정될 때까지 대기한다.
    """
    ctx_key = str(ctx_name or "").strip()
    settle_after_ready = 0
    for frame_i in range(max(4, int(max_frames))):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != int(token):
            return False
        await kit_app.get_app().next_update_async()

        ctx = _named_usd_context(ctx_key)
        if ctx is None:
            continue
        stage = ctx.get_stage() if hasattr(ctx, "get_stage") else None
        if stage is None:
            continue

        busy = False
        for fn_name in ("is_stage_loading", "stage_loading", "is_loading", "is_opening"):
            fn = getattr(ctx, fn_name, None)
            if callable(fn):
                try:
                    if bool(fn()):
                        busy = True
                        break
                except Exception:
                    pass
        if busy:
            settle_after_ready = 0
            continue

        if not _stage_has_renderable_content(ctx_key):
            settle_after_ready = 0
            continue

        settle_after_ready += 1
        if settle_after_ready >= 6:
            try:
                print(
                    f"[TBS/hydra-diag] aux stage settled ctx={ctx_key!r} "
                    f"root={_stage_identity(stage)!r} waited_frames={frame_i + 1}",
                    flush=True,
                )
            except Exception:
                pass
            return True

    ok = _stage_has_renderable_content(ctx_key)
    try:
        print(
            f"[TBS/hydra-diag] WARN aux stage settle timeout ctx={ctx_key!r} "
            f"max_frames={max_frames} has_content={ok}",
            flush=True,
        )
    except Exception:
        pass
    return ok


def _warn_tile_stage_isolation(ext: Any, rec: Dict[str, Any], *, label: str = "") -> None:
    tiles = getattr(ext, "_tbs_split_widget_tiles", None)
    if not isinstance(tiles, dict):
        return
    main_rec = tiles.get("Viewport")
    if not isinstance(main_rec, dict) or main_rec is rec:
        return
    main_stage = _tile_stage(main_rec)
    aux_stage = _tile_stage(rec)
    wn = str(rec.get("_win_name") or "?")
    try:
        if main_stage is not None and aux_stage is not None and main_stage is aux_stage:
            print(
                f"[TBS multi-sim] WARN stage isolation FAIL tile={wn!r} "
                f"main==aux stage_id={id(main_stage):#x} {label}",
                flush=True,
            )
        elif main_stage is not None and aux_stage is not None:
            print(
                f"[TBS multi-sim] stage isolation OK tile={wn!r} "
                f"main={_stage_identity(main_stage)!r} aux={_stage_identity(aux_stage)!r} "
                f"main_id={id(main_stage):#x} aux_id={id(aux_stage):#x} {label}",
                flush=True,
            )
    except Exception:
        pass


def _read_viewport_hydra_engine(api: Any) -> Any:
    if api is None:
        return None
    for attr in ("hydra_engine", "hd_engine"):
        try:
            eng = getattr(api, attr, None)
            if eng:
                return eng
        except Exception:
            pass
    return None


def _resolve_hd_engine_for_new_tile(ext: Any, tiles: Optional[Dict[str, Dict[str, Any]]]) -> Any:
    """aux ``ViewportWidget`` — 화면1·네이티브 Viewport 와 동일 Hydra 엔진."""
    tiles = tiles if isinstance(tiles, dict) else {}
    try:
        main_rec = tiles.get("Viewport")
        if isinstance(main_rec, dict):
            api = main_rec.get("api")
            eng = _read_viewport_hydra_engine(api)
            if eng:
                return eng
    except Exception:
        pass
    ref = _reference_viewport_render_api(ext, tiles)
    return _read_viewport_hydra_engine(ref)


def _reference_viewport_render_api(ext: Any, tiles: Dict[str, Dict[str, Any]]) -> Any:
    """렌더 프로필 복사 기준 — Widget 분할 시 화면1 Widget API, 그 외 네이티브 Viewport."""
    main_rec = tiles.get("Viewport")
    if is_split_widget_layout_active(ext) and isinstance(main_rec, dict):
        api = main_rec.get("api")
        if api is not None:
            return api
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        native = get_viewport_from_window_name("Viewport")
        if native is not None:
            return native
    except Exception:
        pass
    if isinstance(main_rec, dict):
        return main_rec.get("api")
    return None


def _stage_identity(stage: Any) -> str:
    if stage is None:
        return "stage=None"
    try:
        root = stage.GetRootLayer()
        return str(root.identifier if root is not None else "?")
    except Exception:
        return "stage=?"


def _api_context_name(api: Any) -> str:
    if api is None:
        return ""
    for attr in ("usd_context_name", "context_name"):
        try:
            v = str(getattr(api, attr, "") or "").strip()
            if v:
                return v
        except Exception:
            pass
    return ""


def _resolve_camera_path_for_stage(stage: Any, cam_path: str = _DEFAULT_CAMERA) -> Any:
    if stage is None:
        return None
    try:
        from pxr import Sdf, UsdGeom

        want = Sdf.Path(str(cam_path or _DEFAULT_CAMERA))
        if stage.GetPrimAtPath(want).IsValid():
            return want
        for prim in stage.Traverse():
            if prim.IsA(UsdGeom.Camera):
                return prim.GetPath()
    except Exception:
        pass
    return None


def _tile_stage(rec: Dict[str, Any]) -> Any:
    """타일 레코드의 USD 컨텍스트에서 Stage — ``api.stage``(메인 폴백) 사용 금지."""
    ctx_name = str(rec.get("context_name") or "")
    ctx = _named_usd_context(ctx_name)
    if ctx is None:
        return None
    try:
        return ctx.get_stage() if hasattr(ctx, "get_stage") else None
    except Exception:
        return None


def _tile_camera_for_stage(rec: Dict[str, Any], api: Any) -> Any:
    stage = _tile_stage(rec)
    want = str(rec.get("camera_path") or getattr(api, "camera_path", None) or _DEFAULT_CAMERA)
    return _resolve_camera_path_for_stage(stage, want)


def _bind_tile_viewport_to_context(
    rec: Dict[str, Any],
    ctx_name: str,
    *,
    cam_path: str = _DEFAULT_CAMERA,
) -> bool:
    """``usd_context_name`` + ``viewport_changed`` — ``api.stage`` 직접 대입 금지."""
    widget = rec.get("widget")
    if widget is None:
        return False
    api = rec.get("api")
    if api is None:
        try:
            api = getattr(widget, "viewport_api", None)
        except Exception:
            api = None
    if api is None:
        return False
    rec["api"] = api

    ctx_key = str(ctx_name or "").strip()
    rec["context_name"] = ctx_key
    ctx = _named_usd_context(ctx_key)
    stage = ctx.get_stage() if ctx is not None and hasattr(ctx, "get_stage") else None

    try:
        if ctx_key:
            for attr in ("usd_context_name", "context_name"):
                if hasattr(api, attr):
                    setattr(api, attr, ctx_key)
    except Exception:
        pass

    resolved_cam = _resolve_camera_path_for_stage(stage, cam_path)
    wn = str(rec.get("_win_name") or "?")
    if resolved_cam is not None:
        try:
            api.camera_path = resolved_cam
            rec["camera_path"] = str(resolved_cam)
        except Exception:
            pass

    try:
        fn = getattr(api, "viewport_changed", None)
        had_rp_before = _api_has_render_product(api)
        if callable(fn) and resolved_cam is not None and stage is not None:
            if not had_rp_before:
                fn(resolved_cam, stage)
            else:
                try:
                    api.camera_path = resolved_cam
                    rec["camera_path"] = str(resolved_cam)
                except Exception:
                    pass
                try:
                    print(
                        f"[TBS/hydra-diag] bind skip viewport_changed "
                        f"(render_product exists) tile={wn!r}",
                        flush=True,
                    )
                except Exception:
                    pass
    except Exception:
        pass

    try:
        rec["_bound_stage_id"] = id(stage)
        rec["_bound_stage_file"] = _stage_identity(stage)
    except Exception:
        pass

    try:
        print(
            f"[TBS multi-sim] bind ctx tile={wn!r} ctx={ctx_key!r} "
            f"api_ctx={_api_context_name(api)!r} stage={_stage_identity(stage)!r} "
            f"stage_id={id(stage):#x} cam={resolved_cam!r}",
            flush=True,
        )
    except Exception:
        pass
    return resolved_cam is not None and stage is not None


def _copy_visual_render_profile_only(src: Any, dst: Any) -> None:
    """Hydra·viewport_changed 없이 시각 설정만 복사 (그리드·ambient·render_mode 등)."""
    if src is None or dst is None:
        return
    try:
        rm = getattr(src, "render_mode", None)
        if rm and hasattr(dst, "render_mode"):
            dst.render_mode = rm
    except Exception:
        pass
    for attr in _RENDER_PROFILE_ATTRS:
        try:
            if hasattr(src, attr):
                setattr(dst, attr, getattr(src, attr))
        except Exception:
            pass
    _copy_matching_viewport_display_attrs(src, dst)


def _copy_matching_viewport_display_attrs(src: Any, dst: Any) -> None:
    """동일 이름·단순 타입 ViewportAPI 속성을 추가 복사 (톤·배경 등 누락 방지)."""
    if src is None or dst is None:
        return
    for attr in dir(src):
        if attr.startswith("_") or attr in _RENDER_PROFILE_SKIP_COPY:
            continue
        if attr in _RENDER_PROFILE_ATTRS or attr == "render_mode":
            continue
        if not hasattr(dst, attr):
            continue
        try:
            val = getattr(src, attr)
        except Exception:
            continue
        if callable(val):
            continue
        if isinstance(val, (bool, int, float, str, tuple, list)):
            try:
                setattr(dst, attr, val)
            except Exception:
                pass


def _copy_viewport_render_profile(
    src: Any, dst: Any, *, share_hydra_engine: bool = False
) -> None:
    if src is None or dst is None:
        return
    if share_hydra_engine:
        try:
            eng = _read_viewport_hydra_engine(src)
            if eng and hasattr(dst, "hydra_engine"):
                dst.hydra_engine = eng
        except Exception:
            pass
        try:
            setter = getattr(dst, "set_hd_engine", None)
            eng = _read_viewport_hydra_engine(src)
            rm = getattr(src, "render_mode", None)
            if callable(setter) and eng:
                try:
                    setter(eng, rm)
                except TypeError:
                    try:
                        setter(eng)
                    except Exception:
                        pass
        except Exception:
            pass
    try:
        rm = getattr(src, "render_mode", None)
        if rm and hasattr(dst, "render_mode"):
            dst.render_mode = rm
    except Exception:
        pass
    for attr in _RENDER_PROFILE_ATTRS:
        try:
            if hasattr(src, attr):
                setattr(dst, attr, getattr(src, attr))
        except Exception:
            pass
    for attr in ("fill_frame",):
        try:
            if hasattr(dst, attr):
                setattr(dst, attr, False)
        except Exception:
            pass
    for attr in ("updates_enabled", "enabled"):
        try:
            if hasattr(dst, attr):
                setattr(dst, attr, True)
        except Exception:
            pass
    try:
        if share_hydra_engine and not str(getattr(dst, "camera_path", "") or "").strip():
            dst.camera_path = _DEFAULT_CAMERA
    except Exception:
        pass


def _sync_aux_tile_render_from_main(ext: Any) -> None:
    """
    보조 타일 — RenderProduct 가 이미 있으면 메인 Widget API 시각 프로필만 동기화.
    Hydra 엔진 공유·viewport_changed 호출 없음.
    """
    if not bool(getattr(ext, "_tbs_aux_stage_connected", False)):
        return
    tiles = getattr(ext, "_tbs_split_widget_tiles", None)
    if not isinstance(tiles, dict):
        return
    aux_wn = _tile_win_name(1)
    aux_rec = tiles.get(aux_wn)
    main_rec = tiles.get("Viewport")
    if not isinstance(aux_rec, dict) or not isinstance(main_rec, dict):
        return
    _refresh_rec_api_from_widget(aux_rec, aux_wn)
    aux_api = aux_rec.get("api")
    if aux_api is None:
        return

    ref_api = _reference_viewport_render_api(ext, tiles)
    if ref_api is not None:
        _copy_visual_render_profile_only(ref_api, aux_api)

    if not _api_has_render_product(aux_api):
        main_api = main_rec.get("api")
        if main_api is not None and ref_api is None:
            _copy_visual_render_profile_only(main_api, aux_api)
        _kick_viewport_widget_render(aux_rec)


def _api_projection_ready(api: Any) -> bool:
    """``ViewportAPI.projection`` 유효 또는 RenderProduct 존재 시 manipulator 부착 허용."""
    if api is None:
        return False
    if _api_has_render_product(api):
        return True
    try:
        proj = getattr(api, "projection", None)
        if proj is None:
            return False
        for i in range(4):
            for j in range(4):
                if abs(float(proj[i][j])) > 1e-10:
                    return True
        return False
    except Exception:
        return False


def _manipulator_attach_block_reason(rec: Dict[str, Any]) -> str:
    if not isinstance(rec, dict):
        return "invalid-rec"
    if rec.get("camera_manipulator") is not None:
        return "already-attached"
    if not bool(rec.get("manip_pending", True)):
        return "manip-not-pending"
    api = rec.get("api")
    scene_view = rec.get("scene_view")
    if api is None:
        return "api-none"
    if scene_view is None:
        return "scene_view-none"
    if not _api_ready_for_manipulator(api):
        return "resolution-not-ready"
    if not _api_projection_ready(api):
        return "projection-not-ready"
    cam = getattr(api, "camera_path", None)
    if cam is None:
        return "camera_path-none"
    try:
        from pxr import Sdf

        if not isinstance(cam, Sdf.Path):
            cam = Sdf.Path(str(cam))
        stage = _tile_stage(rec)
        if stage is None or not stage.GetPrimAtPath(cam).IsValid():
            return "camera-prim-invalid"
    except Exception:
        return "camera-prim-check-fail"
    if not bool(rec.get("scene_view_registered", False)):
        return "scene_view-not-registered"
    return "unknown"


def _api_ready_for_manipulator(api: Any) -> bool:
    if api is None:
        return False
    try:
        res = getattr(api, "resolution", None)
        if res is not None:
            w, h = int(res[0]), int(res[1])
            return w >= 8 and h >= 8
    except Exception:
        pass
    return True


def _register_tile_scene_view(rec: Dict[str, Any]) -> bool:
    """projection 유효 시 ``add_scene_view`` 1회 — 조기 호출 시 projection 0 오류 방지."""
    if bool(rec.get("scene_view_registered", False)):
        return True
    api = rec.get("api")
    scene_view = rec.get("scene_view")
    if api is None or scene_view is None:
        return False
    if not _api_projection_ready(api):
        return False
    try:
        api.add_scene_view(scene_view)
        rec["scene_view_registered"] = True
        return True
    except Exception:
        return False


def _ensure_tile_manipulator(ext: Any, win_name: str, rec: Dict[str, Any]) -> bool:
    """Viewport API projection 준비 후 SceneView 에 manipulator 1회 부착."""
    if not isinstance(rec, dict):
        return False
    if bool(rec.get("_uses_viewport_window", False)):
        rec["manip_pending"] = False
        return True
    if rec.get("camera_manipulator") is not None:
        rec["manip_pending"] = False
        return True
    if not bool(rec.get("manip_pending", True)):
        return False
    api = rec.get("api")
    scene_view = rec.get("scene_view")
    if api is None or scene_view is None:
        return False
    if not _api_ready_for_manipulator(api):
        return False
    if not _api_projection_ready(api):
        return False
    cam = getattr(api, "camera_path", None)
    if cam is None:
        return False
    try:
        from pxr import Sdf

        if not isinstance(cam, Sdf.Path):
            cam = Sdf.Path(str(cam))
        stage = _tile_stage(rec)
        if stage is None or not stage.GetPrimAtPath(cam).IsValid():
            return False
    except Exception:
        return False
    if not _register_tile_scene_view(rec):
        return False
    manip = _attach_camera_manipulator(api, scene_view)
    if manip is None:
        try:
            print(
                f"[TBS multi-sim] manipulator attach FAIL tile={win_name!r} "
                f"reason=ViewportCameraManipulator-returned-None",
                flush=True,
            )
        except Exception:
            pass
        return False
    rec["camera_manipulator"] = manip
    rec["manip_pending"] = False
    rec["camera_path"] = _tile_camera_path(int(rec.get("cell_index", 0) or 0))
    _bind_tile_manipulator_activation(ext, win_name, scene_view)
    try:
        from .sim_viewport_coupling_diag import probe_tile_manipulator

        probe = probe_tile_manipulator(rec, str(win_name))
        print(
            f"[TBS multi-sim] manipulator attached tile={win_name!r} "
            f"ctx={_api_context_name(api)!r} "
            f"scene_view.model={probe.get('scene_view.model')} "
            f"manip.model={probe.get('manip.model')}",
            flush=True,
        )
    except Exception:
        try:
            print(
                f"[TBS multi-sim] manipulator attached tile={win_name!r} "
                f"ctx={_api_context_name(api)!r}",
                flush=True,
            )
        except Exception:
            pass
    try:
        from .tbs_extension_singleton import get_tbs_extension_instance

        ext = get_tbs_extension_instance()
        if ext is not None:
            _activate_tile_manipulator_only(
                ext, str(getattr(ext, "_tbs_active_widget_tile", "") or "Viewport")
            )
    except Exception:
        pass
    return True


def _schedule_tile_manipulators_when_ready(ext: Any, token: int, split_n: int) -> None:
    """projection 이 0 인 상태에서 manipulator 를 붙이지 않도록 post_update 폴링."""
    sub_ref: List[Any] = [None]
    remaining = [max(32, int(split_n) * 24)]

    def _tick(_ev: Any = None) -> None:
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != int(token):
            if sub_ref[0] is not None:
                try:
                    sub_ref[0].unsubscribe()
                except Exception:
                    pass
            return
        if remaining[0] <= 0:
            if sub_ref[0] is not None:
                try:
                    sub_ref[0].unsubscribe()
                except Exception:
                    pass
            tiles_timeout = getattr(ext, "_tbs_split_widget_tiles", None)
            if isinstance(tiles_timeout, dict):
                try:
                    sn = max(2, int(split_n))
                except Exception:
                    sn = 2
                for wn in ("Viewport",) + tuple(_tile_win_name(ti) for ti in range(1, sn)):
                    rec = tiles_timeout.get(str(wn))
                    if isinstance(rec, dict) and rec.get("camera_manipulator") is None:
                        try:
                            print(
                                f"[TBS multi-sim] manipulator TIMEOUT tile={wn!r} "
                                f"reason={_manipulator_attach_block_reason(rec)}",
                                flush=True,
                            )
                        except Exception:
                            pass
            return
        tiles = getattr(ext, "_tbs_split_widget_tiles", None)
        if not isinstance(tiles, dict):
            remaining[0] -= 1
            return
        pending = False
        try:
            sn = max(2, int(split_n))
        except Exception:
            sn = 2
        for wn in ("Viewport",) + tuple(_tile_win_name(ti) for ti in range(1, sn)):
            rec = tiles.get(str(wn))
            if not isinstance(rec, dict) or rec.get("api") is None:
                continue
            if rec.get("camera_manipulator") is not None:
                continue
            if _ensure_tile_manipulator(ext, str(wn), rec):
                continue
            pending = True
        if not pending:
            if sub_ref[0] is not None:
                try:
                    sub_ref[0].unsubscribe()
                except Exception:
                    pass
            active = str(getattr(ext, "_tbs_active_widget_tile", "") or "Viewport")
            _set_active_widget_tile(ext, active)
            return
        remaining[0] -= 1

    try:
        old = getattr(ext, "_tbs_widget_manip_poll_sub", None)
        if old is not None:
            try:
                old.unsubscribe()
            except Exception:
                pass
        sub_ref[0] = kit_app.get_app().get_post_update_event_stream().create_subscription_to_pop(
            _tick,
            name="morph.tbs_control_2.widget_manip_ready_poll",
        )
        ext._tbs_widget_manip_poll_sub = sub_ref[0]
    except Exception:
        pass


def _bind_tile_manipulator_activation(ext: Any, win_name: str, scene_view: Any) -> None:
    """SceneView 마우스 press/hover 시 해당 타일 manipulator 만 활성화 (focus/enable_input 없음)."""
    if scene_view is None:
        return

    def _activate(*_a: Any, **_k: Any) -> bool:
        _activate_tile_manipulator_only(ext, win_name)
        return False

    for fn_name in ("set_mouse_pressed_fn", "set_mouse_hovered_fn"):
        fn = getattr(scene_view, fn_name, None)
        if callable(fn):
            try:
                fn(_activate)
            except Exception:
                pass


def _wire_tile_input(ext: Any, win_name: str, widget: Any, api: Any, scene_view: Any) -> None:
    """레거시 — ``_bind_tile_manipulator_activation`` 사용."""
    _bind_tile_manipulator_activation(ext, win_name, scene_view)


def _wire_frame_focus_input(ext: Any, win_name: str, frame: Any) -> None:
    """placeholder 셀 — 네이티브 Viewport 가 우측 클릭을 가로채지 않게."""
    if frame is None:
        return

    def _on_activate(*_a: Any, **_k: Any) -> bool:
        _set_active_widget_tile(ext, win_name)
        return False

    for fn_name in (
        "set_mouse_pressed_fn",
        "set_mouse_released_fn",
        "set_mouse_moved_fn",
    ):
        fn = getattr(frame, fn_name, None)
        if callable(fn):
            try:
                fn(_on_activate)
            except Exception:
                pass
    hover_fn = getattr(frame, "set_mouse_hovered_fn", None)
    if callable(hover_fn):
        try:
            hover_fn(_on_activate)
        except Exception:
            pass


def _viewport_client_pixel_size() -> Tuple[int, int]:
    try:
        w = ui.Workspace.get_window("Viewport")
        if w is None:
            return (0, 0)
        ww = int(getattr(w, "width", 0) or 0)
        hh = int(getattr(w, "height", 0) or 0)
        return (ww, hh)
    except Exception:
        return (0, 0)


def _tile_pixel_size(rec: Dict[str, Any]) -> Tuple[int, int]:
    try:
        return (int(rec.get("_last_w", 0) or 0), int(rec.get("_last_h", 0) or 0))
    except Exception:
        return (0, 0)


def _clamp_tile_resolution(width: int, height: int) -> Tuple[int, int]:
    return (max(_VP_TILE_MIN_PX, int(width)), max(_VP_TILE_MIN_PX, int(height)))


def _kick_viewport_widget_render(rec: Dict[str, Any]) -> None:
    """
    ``fill_frame`` 는 get_frame 슬롯 안에서 computed size 가 0 이면 Hydra 가 (0,0) 에 머문다.
    명시적 픽셀 해상도 + viewport_changed 로 렌더 루프를 깨운다.
    """
    api = rec.get("api")
    widget = rec.get("widget")
    w, h = _clamp_tile_resolution(*_tile_pixel_size(rec))
    if widget is not None:
        try:
            if hasattr(widget, "fill_frame"):
                widget.fill_frame = False
        except Exception:
            pass
        try:
            widget.set_resolution((w, h))
        except Exception:
            try:
                widget.resolution = (w, h)
            except Exception:
                pass
    if api is None:
        return
    try:
        api.fill_frame = False
    except Exception:
        pass
    try:
        api.resolution = (w, h)
    except Exception:
        pass
    try:
        cam = _tile_camera_for_stage(rec, api)
        stage = _tile_stage(rec)
        if cam is not None and stage is not None:
            try:
                api.camera_path = cam
                rec["camera_path"] = str(cam)
            except Exception:
                pass
            fn = getattr(api, "viewport_changed", None)
            if callable(fn) and not _api_has_render_product(api):
                fn(cam, stage)
    except Exception:
        pass
    for attr in ("updates_enabled", "enabled"):
        try:
            if hasattr(api, attr):
                setattr(api, attr, True)
        except Exception:
            pass
    for fn_name in ("wake_up", "request_render", "invalidate"):
        fn = getattr(api, fn_name, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass


def _log_tile_render_diag(wn: str, rec: Dict[str, Any]) -> None:
    _log_hydra_pipeline_diag(wn, rec, "render-diag")


def _refresh_tile_viewport_api(
    rec: Dict[str, Any],
    *,
    main_cam: str = _DEFAULT_CAMERA,
    sync_camera: bool = True,
) -> None:
    api = rec.get("api")
    widget = rec.get("widget")
    if api is None:
        return
    if sync_camera:
        cam = str(main_cam or _DEFAULT_CAMERA)
        try:
            api.camera_path = cam
        except Exception:
            pass
    else:
        try:
            cam = str(rec.get("camera_path") or "").strip()
            if not cam:
                cam = _tile_camera_path(int(rec.get("cell_index", 0) or 0))
            if cam:
                api.camera_path = cam
        except Exception:
            pass
    _kick_viewport_widget_render(rec)
    if widget is not None:
        try:
            widget.fill_frame = False
        except Exception:
            pass


def sync_widget_aux_resolution_from_workspace(ext: Any, win_name: str) -> None:
    """논리 타일 이름 → ``Viewport`` 클라이언트 절반 크기로 해상도 동기화."""
    sync_split_widget_fill_frame(ext, 2)


def sync_split_widget_fill_frame(ext: Any, split_n: int) -> None:
    """``Viewport`` 창 크기 기준 50:50 — Dock·Workspace 보조 창 없음."""
    try:
        from .hyview_stream import is_hyview_stream_layout_locked, bridge_stream_skip

        if is_hyview_stream_layout_locked(ext):
            bridge_stream_skip(
                "sync_split_widget_fill_frame",
                "layout_locked",
                split_n=int(split_n),
            )
            return
    except Exception:
        pass
    if not is_split_widget_layout_active(ext):
        return
    try:
        sn = max(2, int(split_n))
    except Exception:
        sn = 2
    ww, hh = _viewport_client_pixel_size()
    half_w = max(_VP_TILE_MIN_PX, int(ww) // 2)
    th = max(_VP_TILE_MIN_PX, int(hh))
    tiles = getattr(ext, "_tbs_split_widget_tiles", None)
    if not isinstance(tiles, dict):
        return
    main_cam = _DEFAULT_CAMERA
    try:
        main_rec = tiles.get("Viewport")
        if isinstance(main_rec, dict) and main_rec.get("api") is not None:
            main_cam = str(getattr(main_rec["api"], "camera_path", "") or "").strip() or main_cam
    except Exception:
        pass
    for wn in ("Viewport",) + tuple(_tile_win_name(ti) for ti in range(1, sn)):
        rec = tiles.get(str(wn))
        if not isinstance(rec, dict):
            continue
        if rec.get("widget") is None:
            continue
        _refresh_rec_api_from_widget(rec, str(wn))
        if rec.get("api") is None:
            continue
        rec["_last_w"] = int(half_w)
        rec["_last_h"] = int(th)
        _refresh_tile_viewport_api(
            rec,
            main_cam=main_cam,
            sync_camera=(str(wn) == "Viewport"),
        )
    ensure_viewport_workspace_tab_visible()
    active = str(getattr(ext, "_tbs_active_widget_tile", "") or "Viewport")
    _set_active_widget_tile(ext, active)
    try:
        tok = int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0)
        _schedule_tile_manipulators_when_ready(ext, tok, sn)
    except Exception:
        pass


def get_split_viewport_api(ext: Any, win_name: str) -> Any:
    if ext is None or not is_split_widget_layout_active(ext):
        return None
    wn = str(win_name or "").strip()
    tiles = getattr(ext, "_tbs_split_widget_tiles", None)
    if not isinstance(tiles, dict):
        return None
    rec = tiles.get(wn)
    if not isinstance(rec, dict):
        return None
    return _refresh_rec_api_from_widget(rec, wn)


def get_split_hud_mount(ext: Any, win_name: str) -> Optional[_WidgetHudMount]:
    return get_viewport_tile_hud_mount(ext, win_name)


def get_viewport_tile_hud_mount(ext: Any, win_name: str) -> Optional[_WidgetHudMount]:
    """Widget 분할 타일 ``ZStack`` 내 HUD 오버레이 (화면1 EBS 패널용)."""
    if ext is None or not is_split_widget_layout_active(ext):
        return None
    tiles = getattr(ext, "_tbs_split_widget_tiles", None)
    if not isinstance(tiles, dict):
        return None
    rec = tiles.get(str(win_name or "Viewport"))
    if not isinstance(rec, dict):
        return None
    mount = rec.get("hud_mount")
    return mount if isinstance(mount, _WidgetHudMount) else None


def _log_widget_tile_api(wn: str, ctx_name: str, api: Any) -> None:
    try:
        got = str(getattr(api, "usd_context_name", "") or "")
        print(
            f"[TBS multi-sim] Widget 타일 api tile={wn!r} expect_ctx={ctx_name!r} api_ctx={got!r}",
            flush=True,
        )
    except Exception:
        pass


def _aux_stage_ready(ext: Any) -> bool:
    return _stage_has_renderable_content(_tile_usd_context_name(1))


def _destroy_aux_tile_record(rec: Dict[str, Any]) -> None:
    if not isinstance(rec, dict):
        return
    backend = rec.get("_backend_viewport")
    if backend is not None:
        try:
            from .sim_multi_view import _destroy_kit_viewport

            _destroy_kit_viewport(backend)
        except Exception:
            pass
        rec["_backend_viewport"] = None
    if bool(rec.get("_uses_viewport_window", False)):
        try:
            from .sim_multi_view import _destroy_kit_viewport

            _destroy_kit_viewport(rec.get("viewport_window"))
        except Exception:
            pass
        rec["viewport_window"] = None
        rec["api"] = None
        return
    _destroy_tile_manipulator(rec)
    _destroy_viewport_widget(rec.get("widget"))


async def _materialize_aux_viewport_widget_impl(ext: Any, token: int, sn: int) -> bool:
    """레거시 이름 — Stage/Context 연결만 (ViewportWidget 재생성 금지)."""
    return await _connect_widget_tile_aux_stage(ext, token, sn)


async def _connect_widget_tile_aux_stage(ext: Any, token: int, sn: int) -> bool:
    """
    master_2 / aux USD 준비 후 화면2 Widget 에 Stage·Context 연결.
    ``ViewportWidget()`` 호출 없음.
    """
    if bool(getattr(ext, "_tbs_aux_stage_connected", False)):
        return True
    if not _aux_stage_ready(ext):
        return False
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return False

    tiles = getattr(ext, "_tbs_split_widget_tiles", None)
    if not isinstance(tiles, dict):
        return False
    aux_wn = _tile_win_name(1)
    aux_ctx = _tile_usd_context_name(1)
    aux_rec = tiles.get(aux_wn)
    if not isinstance(aux_rec, dict):
        return False
    if aux_rec.get("widget") is None:
        if not bool(aux_rec.get("_deferred_create", False)):
            _log_widget_lifecycle(ext, "connect-aux-abort", aux_wn, aux_rec)
            return False

    for _ in range(6):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return False
        await kit_app.get_app().next_update_async()

    await _wait_aux_usd_stage_settled(ext, aux_ctx, int(token))

    _ensure_aux_stage_default_lighting(aux_ctx)
    try:
        from .sim_viewport_coupling_diag import log_stage_lighting_summary

        log_stage_lighting_summary(ext, "after-lighting-sync")
    except Exception:
        pass

    # --- RenderProduct 원인 조사: embedded 생성 전 독립 ui.Window 실험 (CASE A/B) ---
    try:
        from .sim_viewport_rp_diag import (
            run_isolated_viewport_widget_test,
            viewport_rp_isolated_test_enabled,
        )

        if viewport_rp_isolated_test_enabled():
            half_w = int(aux_rec.get("_last_w", 640) or 640)
            th = int(aux_rec.get("_last_h", 480) or 480)
            await run_isolated_viewport_widget_test(
                ext,
                aux_ctx,
                half_w,
                th,
                token=int(token),
                hd_engine=None,
                hd_engine_mode="omitted",
            )
            try:
                from .sim_viewport_rp_diag import teardown_isolated_rp_test_window

                teardown_isolated_rp_test_window(ext)
            except Exception:
                pass
    except Exception as exc:
        try:
            print(f"[TBS/rp-isolated] hook error: {exc}", flush=True)
        except Exception:
            pass

    if aux_rec.get("widget") is None:
        if not _create_deferred_aux_viewport_widget(ext, aux_rec):
            _log_widget_lifecycle(ext, "connect-aux-defer-fail", aux_wn, aux_rec)
            return False
        _log_widget_lifecycle(ext, "connect-aux-deferred-create", aux_wn, aux_rec)

    _purge_widget_mode_stale_windows(ext)

    try:
        print(
            f"[TBS multi-sim] 화면2 ViewportWidget 최초 생성+Stage 연결 tile={aux_wn!r} ctx={aux_ctx!r}",
            flush=True,
        )
    except Exception:
        pass

    _log_widget_lifecycle(ext, "connect-aux-before", aux_wn, aux_rec)

    api = _refresh_rec_api_from_widget(aux_rec, aux_wn)
    if api is None:
        return False

    aux_rec["_win_name"] = aux_wn
    if not _bind_tile_viewport_to_context(aux_rec, aux_ctx, cam_path=_MAIN_TILE_CAMERA):
        return False
    _log_hydra_pipeline_diag(aux_wn, aux_rec, "connect-bind")

    ref_api = _reference_viewport_render_api(ext, tiles)
    api = aux_rec.get("api")
    if ref_api is not None and api is not None and not _api_has_render_product(api):
        _copy_visual_render_profile_only(ref_api, api)
    _log_hydra_pipeline_diag(aux_wn, aux_rec, "connect-profile")

    aux_rec["context_name"] = aux_ctx
    aux_rec["stage_connected"] = True

    _kick_viewport_widget_render(aux_rec)
    _warn_tile_stage_isolation(ext, aux_rec, label="connect-aux")
    await _bootstrap_tile_viewport_render_async(ext, aux_rec, int(token), frames=6)

    await assign_widget_split_cameras(ext, int(token), ["Viewport", aux_wn])

    if ref_api is not None and aux_rec.get("api") is not None:
        _copy_visual_render_profile_only(ref_api, aux_rec.get("api"))
    _kick_viewport_widget_render(aux_rec)

    _schedule_tile_manipulators_when_ready(ext, int(token), int(sn))
    _set_active_widget_tile(ext, str(getattr(ext, "_tbs_active_widget_tile", "") or "Viewport"))

    try:
        ext._tbs_aux_stage_connected = True
        ext._tbs_aux_vw_materialized = True
    except Exception:
        pass

    _log_widget_lifecycle(ext, "connect-aux-after", aux_wn, aux_rec)

    try:
        _log_hydra_pipeline_diag(aux_wn, aux_rec, "connect-aux-done")
    except Exception:
        pass

    # embedded Widget #2 — RP 타임라인 관측 (수정 없음)
    try:
        from .sim_viewport_rp_diag import observe_rp_timeline, viewport_rp_diag_enabled

        if viewport_rp_diag_enabled():
            await observe_rp_timeline(
                ext,
                aux_rec.get("api"),
                aux_rec.get("widget"),
                f"embedded:{aux_wn}",
                token=int(token),
                scene_view=aux_rec.get("scene_view"),
            )
    except Exception:
        pass

    _destroy_all_aux_workspace_windows(ext)

    try:
        print(
            "[TBS multi-sim] 화면2 Stage 연결 완료 (단일 Viewport 탭·HStack 50:50)",
            flush=True,
        )
    except Exception:
        pass
    return True


def _destroy_tile_records(tiles: Dict[str, Dict[str, Any]]) -> None:
    for rec in tiles.values():
        if not isinstance(rec, dict):
            continue
        _destroy_aux_tile_record(rec)
        rec["api"] = None
        rec["scene_view"] = None
        rec["camera_manipulator"] = None
        rec["widget"] = None
        rec["viewport_window"] = None
        rec["manip_pending"] = True


def _bootstrap_tile_viewport_render(rec: Dict[str, Any]) -> None:
    """Hydra texture·렌더 루프를 깨운 뒤 한 프레임이라도 그리게 한다."""
    _kick_viewport_widget_render(rec)
    widget = rec.get("widget")
    if widget is not None:
        for fn_name in ("invalidate", "refresh"):
            fn = getattr(widget, fn_name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass


async def _bootstrap_tile_viewport_render_async(
    ext: Any, rec: Dict[str, Any], token: int, *, frames: int = 8
) -> None:
    if not isinstance(rec, dict) or rec.get("api") is None:
        return
    for _ in range(max(2, int(frames))):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != int(token):
            return
        await kit_app.get_app().next_update_async()
        _bootstrap_tile_viewport_render(rec)


def _create_viewport_tile(
    ext: Any,
    wn: str,
    ctx_name: str,
    cell_idx: int,
    half_w: int,
    th: int,
    *,
    ViewportWidget: Any,
    hd_engine: Any = None,
    viewport_api: Any = None,
    backend_viewport: Any = None,
    ui_container: Any = None,
    include_background_rect: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    ``ViewportWidget + SceneView`` — shell 또는 deferred aux 슬롯에 **1회** 생성.
    """
    global _WIDGET_CREATE_COUNT
    import omni.ui.scene as sc

    _WIDGET_CREATE_COUNT += 1
    create_n = int(_WIDGET_CREATE_COUNT)
    try:
        ext._tbs_widget_create_total = create_n
    except Exception:
        pass
    try:
        print(
            f"[TBS multi-sim] ViewportWidget #{create_n} 생성 tile={wn!r} ctx={ctx_name!r}",
            flush=True,
        )
    except Exception:
        pass

    tile_w, tile_h = _clamp_tile_resolution(half_w, th)
    cam_path = _tile_camera_path(cell_idx)
    vw_kw: Dict[str, Any] = {
        "camera_path": cam_path,
        "resolution": (tile_w, tile_h),
    }
    if viewport_api is not None:
        vw_kw["viewport_api"] = viewport_api
    else:
        # Workspace 창명 ``TBS_SimSplit_*`` 와 충돌하지 않는 고유 API id.
        vw_kw["viewport_api"] = f"morph.tbs_control_2:widget_tile_{create_n}"
    if str(ctx_name or "").strip():
        vw_kw["usd_context_name"] = str(ctx_name)
    # 모든 타일 — Widget 자체 Hydra (네이티브 presenter 는 숨김).
    ctor_hd_engine = False
    try:
        print(
            f"[TBS/hydra-diag] ViewportWidget ctor tile={wn!r} ctx={ctx_name!r} "
            f"hd_engine={'passed' if ctor_hd_engine else 'omitted'} "
            f"usd_context_name={vw_kw.get('usd_context_name', '')!r}",
            flush=True,
        )
    except Exception:
        pass
    vw_tile = None
    api = None
    scene_view = None

    def _build_contents() -> None:
        nonlocal vw_tile, api, scene_view
        with ui.ZStack():
            if include_background_rect:
                ui.Rectangle(style={"background_color": 0xFF101010})
            vw_tile = ViewportWidget(**vw_kw)
            policy = getattr(sc, "AspectRatioPolicy", None)
            stretch = getattr(policy, "STRETCH", None) if policy is not None else None
            if stretch is not None:
                scene_view = sc.SceneView(aspect_ratio_policy=stretch)
            else:
                scene_view = sc.SceneView()

    try:
        if ui_container is not None:
            with ui_container:
                _build_contents()
        else:
            with ui.ZStack():
                _build_contents()
    except Exception as exc:
        try:
            print(
                f"[TBS multi-sim] ViewportWidget 실패 tile={wn!r} ctx={ctx_name!r}: {exc}",
                flush=True,
            )
        except Exception:
            pass
        return None
    try:
        vw_tile.fill_frame = False
        vw_tile.set_resolution((tile_w, tile_h))
    except Exception:
        pass
    try:
        scene_view.name = f"TBS_WidgetCam_{wn}"
    except Exception:
        pass
    api = getattr(vw_tile, "viewport_api", None)
    scene_registered = False
    if api is not None:
        try:
            api.fill_frame = False
            api.resolution = (tile_w, tile_h)
            api.camera_path = cam_path
            if str(ctx_name or "").strip():
                for attr in ("usd_context_name", "context_name"):
                    if hasattr(api, attr):
                        setattr(api, attr, str(ctx_name))
            for attr in ("updates_enabled", "enabled"):
                if hasattr(api, attr):
                    setattr(api, attr, True)
        except Exception:
            pass
    _log_widget_tile_api(wn, ctx_name, api)
    hud_mount: Any = None
    if str(wn) == "Viewport":
        hud_overlay = ui.Frame()
        with hud_overlay:
            pass
        hud_mount = _WidgetHudMount(hud_overlay)
    _bind_tile_manipulator_activation(ext, wn, scene_view)
    rec: Dict[str, Any] = {
        "widget": vw_tile,
        "scene_view": scene_view,
        "scene_view_registered": scene_registered,
        "camera_manipulator": None,
        "manip_pending": True,
        "api": api,
        "hud_mount": hud_mount,
        "viewport_window": hud_mount if hud_mount is not None else None,
        "context_name": ctx_name,
        "cell_index": int(cell_idx),
        "camera_path": cam_path,
        "_last_w": int(half_w),
        "_last_h": int(th),
        "_backend_viewport": backend_viewport,
        "_viewport_api_bridge": viewport_api is not None,
        "stage_connected": False,
        "_widget_create_index": int(create_n),
        "_ctor_hd_engine_passed": bool(ctor_hd_engine),
    }
    _log_widget_lifecycle(ext, f"create-#{create_n}", wn, rec)
    _log_hydra_pipeline_diag(wn, rec, "widget-create")
    return rec


def _build_split_widget_shell(
    ext: Any,
    vw_host: Any,
    half_w: int,
    th: int,
    *,
    ViewportWidget: Any,
) -> Optional[Dict[str, Dict[str, Any]]]:
    """
    HStack 50:50 — 화면1 ``ViewportWidget`` 즉시 생성.
    화면2 슬롯은 Rectangle 만 두고 ``master_2`` 준비 후 deferred 1회 생성.
    """
    tiles: Dict[str, Dict[str, Any]] = {}
    aux_wn = _tile_win_name(1)
    aux_ctx = _tile_usd_context_name(1)
    shell_hstack: Any = None
    with vw_host.get_frame(_SPLIT_FRAME_SLOT):
        shell_hstack = ui.HStack(spacing=0)
        with shell_hstack:
            z1 = ui.ZStack(width=ui.Fraction(0.5))
            with z1:
                rec_main = _create_viewport_tile(
                    ext,
                    "Viewport",
                    _tile_usd_context_name(0),
                    0,
                    half_w,
                    th,
                    ViewportWidget=ViewportWidget,
                    hd_engine=None,
                )
                if rec_main is None:
                    return None
                rec_main["_win_name"] = "Viewport"
                tiles["Viewport"] = rec_main
            z2 = ui.ZStack(width=ui.Fraction(0.5))
            with z2:
                ui.Rectangle(style={"background_color": 0xFF101010})
            rec_aux = _new_aux_tile_slot_record(
                ext,
                aux_wn,
                aux_ctx,
                1,
                half_w,
                th,
                aux_zstack=z2,
            )
            rec_aux["_win_name"] = aux_wn
            tiles[aux_wn] = rec_aux
    try:
        ext._tbs_widget_shell_hstack = shell_hstack
        ext._tbs_widget_shell_zstack_main = z1
        ext._tbs_widget_shell_zstack_aux = z2
        ext._tbs_widget_create_total = int(_WIDGET_CREATE_COUNT)
    except Exception:
        pass
    for wn, rec in tiles.items():
        _log_widget_lifecycle(ext, "shell-built", str(wn), rec)
    try:
        print(
            "[TBS multi-sim] ViewportWidget shell — 화면1=#1 생성, "
            "화면2 슬롯(Rectangle) master_2 후 #2 deferred 생성",
            flush=True,
        )
    except Exception:
        pass
    return tiles


async def _materialize_aux_viewport_widget(ext: Any, token: int, sn: int) -> bool:
    """aux Stage 연결 (ViewportWidget 재생성 없음)."""
    if bool(getattr(ext, "_tbs_aux_stage_connected", False)):
        return True
    tiles_done = getattr(ext, "_tbs_split_widget_tiles", None)
    aux_rec = (
        tiles_done.get(_tile_win_name(1))
        if isinstance(tiles_done, dict)
        else None
    )
    if isinstance(aux_rec, dict) and aux_rec.get("widget") is not None and bool(
        aux_rec.get("stage_connected")
    ):
        return True
    if bool(getattr(ext, "_tbs_aux_vw_materialize_inflight", False)):
        for _ in range(48):
            if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                return False
            if bool(getattr(ext, "_tbs_aux_stage_connected", False)):
                return True
            await kit_app.get_app().next_update_async()
        return bool(getattr(ext, "_tbs_aux_stage_connected", False))
    if not _aux_stage_ready(ext):
        return False
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return False

    try:
        ext._tbs_aux_vw_materialize_inflight = True
    except Exception:
        pass
    try:
        return await _connect_widget_tile_aux_stage(ext, token, sn)
    finally:
        try:
            ext._tbs_aux_vw_materialize_inflight = False
        except Exception:
            pass


def request_aux_widget_materialize(ext: Any, token: int, sn: int) -> None:
    """hydrate 직후 aux Stage 연결 예약 — Widget 재생성 없음."""

    async def _deferred() -> None:
        for _ in range(12):
            if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != int(token):
                return
            await kit_app.get_app().next_update_async()
        await _connect_widget_tile_aux_stage(ext, int(token), int(sn))

    if not is_split_widget_layout_active(ext):
        return
    if bool(getattr(ext, "_tbs_aux_stage_connected", False)):
        return
    try:
        from .sim_multi_view import startup_dual_orchestration_active

        if startup_dual_orchestration_active(ext):
            return
    except Exception:
        pass
    asyncio.ensure_future(_deferred())


def _clear_split_frame_slot(vw: Any) -> None:
    if vw is None or not callable(getattr(vw, "get_frame", None)):
        return
    try:
        with vw.get_frame(_SPLIT_FRAME_SLOT):
            pass
    except Exception:
        pass


def _destroy_split_widget_host_ui(ext: Any) -> None:
    try:
        from .sim_viewport_coupling_diag import teardown_camera_change_tracker

        teardown_camera_change_tracker(ext)
    except Exception:
        pass
    tiles = getattr(ext, "_tbs_split_widget_tiles", None)
    if isinstance(tiles, dict):
        for rec in tiles.values():
            if not isinstance(rec, dict):
                continue
            _destroy_aux_tile_record(rec)
            rec["api"] = None
            rec["scene_view"] = None
            rec["camera_manipulator"] = None
            rec["widget"] = None
            rec["viewport_window"] = None
    try:
        ext._tbs_split_widget_tiles = {}
    except Exception:
        pass
    vw = getattr(ext, "_tbs_split_viewport_host_window", None)
    if vw is None:
        vw = _resolve_main_viewport_window(ext)
    _clear_split_frame_slot(vw)
    _purge_aux_workspace_windows(ext)
    _reset_promoted_widget_aux_entries(ext)
    try:
        ext._tbs_aux_cell_frame = None
        ext._tbs_widget_shell_hstack = None
        ext._tbs_widget_shell_zstack_main = None
        ext._tbs_widget_shell_zstack_aux = None
        ext._tbs_split_viewport_host_window = None
        ext._tbs_split_widget_host_win = None
    except Exception:
        pass
    _set_native_viewport_updates_enabled(ext, True)
    _stop_native_viewport_input_guard(ext)
    sub = getattr(ext, "_tbs_widget_nav_hold_sub", None)
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
    sub2 = getattr(ext, "_tbs_widget_manip_poll_sub", None)
    if sub2 is not None:
        try:
            sub2.unsubscribe()
        except Exception:
            pass
    try:
        ext._tbs_widget_nav_hold_sub = None
        ext._tbs_widget_manip_poll_sub = None
        ext._tbs_widget_stage_refresh_inflight = False
        ext._tbs_widget_stage_sync_key = None
        ext._tbs_widget_post_stage_hud_synced = False
        ext._tbs_widget_split_ready = False
        ext._tbs_aux_stage_connected = False
        ext._tbs_aux_vw_materialized = False
    except Exception:
        pass


def teardown_split_widget_host(ext: Any) -> None:
    _destroy_split_widget_host_ui(ext)
    try:
        ext._tbs_split_used_widget_layout = False
        ext._tbs_widget_stage_refresh_inflight = False
        ext._tbs_widget_stage_sync_key = None
        ext._tbs_widget_post_stage_hud_synced = False
        ext._tbs_widget_split_ready = False
        ext._tbs_aux_stage_connected = False
        ext._tbs_aux_vw_materialized = False
    except Exception:
        pass
    try:
        from .sim_multi_view import set_viewport_fill_frame_for_split_count

        set_viewport_fill_frame_for_split_count(1, False)
    except Exception:
        pass


def split_widget_layout_healthy(ext: Any, split_n: int) -> bool:
    if not is_split_widget_layout_active(ext):
        return False
    try:
        sn = int(split_n)
    except Exception:
        return False
    if sn < 2:
        return False
    if getattr(ext, "_tbs_split_viewport_host_window", None) is None:
        return False
    tiles = getattr(ext, "_tbs_split_widget_tiles", None)
    if not isinstance(tiles, dict):
        return False
    if tiles.get("Viewport", {}).get("widget") is None:
        return False
    aux = tiles.get(_tile_win_name(1))
    if not isinstance(aux, dict):
        return False
    if aux.get("widget") is None:
        return False
    if bool(aux.get("_uses_viewport_window", False)):
        return True
    ctx = list(getattr(ext, "_sim_multi_context_names", None) or [])
    return len(ctx) == sn - 1


async def _sync_widget_tiles_after_aux_stage_ready(
    ext: Any, token: int, sn: int, tiles: Dict[str, Dict[str, Any]], main_cam: str
) -> None:
    """보조 스테이지 준비 후 화면2 셀에만 ViewportWidget 주입."""
    for _ in range(2):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        await kit_app.get_app().next_update_async()

    if not bool(getattr(ext, "_tbs_aux_stage_connected", False)):
        ok = await _connect_widget_tile_aux_stage(ext, token, sn)
        if not ok:
            return
        tiles = getattr(ext, "_tbs_split_widget_tiles", None)
        if not isinstance(tiles, dict):
            return

    for wn in ("Viewport", _tile_win_name(1)):
        rec = tiles.get(wn)
        if isinstance(rec, dict) and rec.get("api") is not None:
            _refresh_tile_viewport_api(
                rec,
                main_cam=main_cam,
                sync_camera=(str(wn) == "Viewport"),
            )
    _schedule_tile_manipulators_when_ready(ext, int(token), sn)
    try:
        ext._tbs_widget_stage_sync_key = (int(token), int(sn))
    except Exception:
        pass
    try:
        print("[TBS multi-sim] ViewportWidget aux 스테이지 동기화 완료", flush=True)
    except Exception:
        pass


async def refresh_split_widget_tiles_after_stage(ext: Any, token: int, n: int) -> None:
    if not is_split_widget_layout_active(ext):
        return
    if bool(getattr(ext, "_tbs_widget_stage_refresh_inflight", False)):
        return
    try:
        sn = max(2, int(n))
    except Exception:
        sn = 2
    sync_key = (int(token), int(sn))
    if getattr(ext, "_tbs_widget_stage_sync_key", None) == sync_key:
        return
    try:
        ext._tbs_widget_stage_refresh_inflight = True
    except Exception:
        pass
    try:
        tiles = getattr(ext, "_tbs_split_widget_tiles", None)
        if not isinstance(tiles, dict):
            return
        main_cam = _DEFAULT_CAMERA
        main_rec = tiles.get("Viewport")
        if isinstance(main_rec, dict) and main_rec.get("api") is not None:
            try:
                main_cam = str(getattr(main_rec["api"], "camera_path", "") or "").strip() or main_cam
            except Exception:
                pass
        for _ in range(24):
            if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                return
            aux = tiles.get(_tile_win_name(1))
            if not isinstance(aux, dict):
                await kit_app.get_app().next_update_async()
                continue
            ctx_name = str(aux.get("context_name") or _tile_usd_context_name(1))
            if not _stage_has_renderable_content(ctx_name):
                await kit_app.get_app().next_update_async()
                continue
            await _sync_widget_tiles_after_aux_stage_ready(ext, token, sn, tiles, main_cam)
            return
    finally:
        try:
            ext._tbs_widget_stage_refresh_inflight = False
        except Exception:
            pass


def sync_viewport_hud_when_ready(ext: Any, *, force: bool = False) -> None:
    """Widget 분할 READY 후 EBS HUD 1회만 마운트."""
    if not force and bool(getattr(ext, "_tbs_ebs_hud_mounted", False)):
        return
    try:
        from .sim_multi_view import startup_dual_orchestration_active

        if not force and startup_dual_orchestration_active(ext):
            return
    except Exception:
        pass
    try:
        from .sim_multi_view import _resolve_viewport_window_for_workspace_name

        vw = _resolve_viewport_window_for_workspace_name("Viewport")
        if vw is not None:
            ext._tbs_split_main_viewport_window = vw
    except Exception:
        pass
    if force:
        try:
            ext._tbs_ebs_hud_mounted = False
        except Exception:
            pass
    try:
        hud = getattr(ext, "_tbs_viewport_control_hud", None)
        if hud is not None and hasattr(hud, "sync_layers"):
            hud.sync_layers(delay_frames=2, force=force)
    except Exception:
        pass


def _remount_viewport_hud_layers(ext: Any) -> None:
    """레거시 호출 — orchestration 중이면 무시, READY 시 1회만."""
    sync_viewport_hud_when_ready(ext, force=False)


async def finalize_widget_split_startup(ext: Any, token: int, n: int) -> None:
    """
    layout-first startup 종료 시 1회만:
    Stage 연결 · geometry · navigation · HUD (Widget 재생성 없음).
    """
    if not is_split_widget_layout_active(ext):
        return
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != int(token):
        return
    try:
        sn = max(2, int(n))
    except Exception:
        sn = 2

    if not bool(getattr(ext, "_tbs_main_stage_connected", False)):
        try:
            await connect_widget_tile_main_stage(ext, int(token))
        except Exception:
            pass

    if not bool(getattr(ext, "_tbs_aux_stage_connected", False)):
        if _aux_stage_ready(ext):
            ok = await _connect_widget_tile_aux_stage(ext, int(token), sn)
            if not ok:
                await refresh_split_widget_tiles_after_stage(ext, int(token), sn)
        else:
            await refresh_split_widget_tiles_after_stage(ext, int(token), sn)

    sync_split_widget_fill_frame(ext, sn)

    tiles_now = getattr(ext, "_tbs_split_widget_tiles", None)
    aux_wn = _tile_win_name(1)
    aux_rec = tiles_now.get(aux_wn) if isinstance(tiles_now, dict) else None

    try:
        from .sim_viewport_rp_diag import log_finalize_rp_step

        if isinstance(aux_rec, dict):
            log_finalize_rp_step(aux_rec, "after-fill-frame")
    except Exception:
        pass
    try:
        from .sim_multi_view import _sync_entries_from_widget_tiles

        _sync_entries_from_widget_tiles(ext)
    except Exception:
        pass

    if isinstance(aux_rec, dict):
        _refresh_rec_api_from_widget(aux_rec, aux_wn)

    try:
        from .sim_viewport_rp_diag import log_finalize_rp_step

        if isinstance(aux_rec, dict):
            log_finalize_rp_step(aux_rec, "finalize-enter")
    except Exception:
        pass

    aux_hydra_ok = (
        isinstance(aux_rec, dict)
        and aux_rec.get("widget") is not None
        and _api_has_render_product(aux_rec.get("api"))
    )

    if aux_hydra_ok:
        try:
            print(
                "[TBS/hydra-diag] finalize: aux render_product OK — "
                "visual profile sync (no hydra/viewport_changed)",
                flush=True,
            )
        except Exception:
            pass

    try:
        from .sim_viewport_rp_diag import log_finalize_rp_step

        if isinstance(aux_rec, dict):
            log_finalize_rp_step(aux_rec, "after-aux-sync")
    except Exception:
        pass

    if isinstance(tiles_now, dict):
        for rec in tiles_now.values():
            if not isinstance(rec, dict) or rec.get("widget") is None:
                continue
            wn = str(rec.get("_win_name") or "")
            if wn:
                _refresh_rec_api_from_widget(rec, wn)

    try:
        await assign_widget_split_cameras(
            ext,
            int(token),
            ["Viewport", aux_wn],
        )
    except Exception:
        pass

    _sync_aux_tile_render_from_main(ext)
    try:
        from .sim_viewport_rp_diag import log_finalize_rp_step

        if isinstance(aux_rec, dict):
            log_finalize_rp_step(aux_rec, "after-assign-cameras")
    except Exception:
        pass
    _schedule_tile_manipulators_when_ready(ext, int(token), sn)
    tiles_ready = getattr(ext, "_tbs_split_widget_tiles", None)
    if isinstance(tiles_ready, dict):
        for wn in ("Viewport", aux_wn):
            rec_m = tiles_ready.get(str(wn))
            if isinstance(rec_m, dict):
                _ensure_tile_manipulator(ext, str(wn), rec_m)
    apply_split_widget_navigation(ext, sn, int(token))
    _suspend_native_viewport_widget_presenter(ext)
    _disable_native_viewport_navigation_permanent(ext)
    _activate_tile_manipulator_only(ext, "Viewport")
    _destroy_all_aux_workspace_windows(ext)
    try:
        from .sim_viewport_rp_diag import log_finalize_rp_step

        if isinstance(aux_rec, dict):
            log_finalize_rp_step(aux_rec, "after-destroy-aux-windows")
    except Exception:
        pass
    sync_viewport_hud_when_ready(ext, force=True)
    try:
        from .sim_viewport_rp_diag import log_finalize_rp_step

        if isinstance(aux_rec, dict):
            log_finalize_rp_step(aux_rec, "after-hud-sync")
    except Exception:
        pass

    tiles_diag = getattr(ext, "_tbs_split_widget_tiles", None)
    if isinstance(tiles_diag, dict):
        for wn in ("Viewport", _tile_win_name(1)):
            rec = tiles_diag.get(wn)
            if isinstance(rec, dict):
                _refresh_rec_api_from_widget(rec, str(wn))
                _log_widget_lifecycle(ext, "READY", str(wn), rec)
                if rec.get("api") is not None:
                    _log_hydra_pipeline_diag(str(wn), rec, "READY")
                    _warn_tile_stage_isolation(ext, rec, label="READY")

    try:
        from .sim_viewport_coupling_diag import (
            install_camera_change_tracker,
            log_manipulator_investigation,
            log_render_profile_diff,
        )

        log_manipulator_investigation(ext, "READY")
        log_render_profile_diff(ext)
        log_stage_lighting_summary(ext, "READY")
        install_camera_change_tracker(ext)
    except Exception:
        pass

    try:
        total = int(getattr(ext, "_tbs_widget_create_total", 0) or 0)
        print(
            f"[TBS multi-sim] ViewportWidget 분할 startup READY (create_total={total}, expect=2)",
            flush=True,
        )
    except Exception:
        try:
            print("[TBS multi-sim] ViewportWidget 분할 startup READY", flush=True)
        except Exception:
            pass

    try:
        ext._tbs_widget_split_ready = True
        ext._tbs_widget_post_stage_hud_synced = True
    except Exception:
        pass
    try:
        from .sim_multi_view import schedule_viewport_snapshot_hud_refresh

        schedule_viewport_snapshot_hud_refresh(ext)
    except Exception:
        pass

    # aux 타일 fps=0 이면 post-READY 렌더 pump 1회
    if isinstance(aux_rec, dict) and aux_rec.get("api") is not None:
        try:
            fps = float(getattr(aux_rec.get("api"), "fps", 0) or 0)
        except Exception:
            fps = 0.0
        if fps < 0.5:
            await _bootstrap_tile_viewport_render_async(ext, aux_rec, int(token), frames=16)


async def apply_split_widget_layout(ext: Any, token: int, n: int) -> bool:
    """
    Workspace ``Viewport`` ``get_frame`` 슬롯에 ``ViewportWidget`` 2칸(HStack 50:50).
    Dock / ``TBS_SimSplit_*`` 창은 만들지 않는다.
    """
    if not sim_viewport_split_widget_enabled():
        return False
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return False
    try:
        from omni.kit.widget.viewport import ViewportWidget
    except Exception as exc:
        try:
            print(f"[TBS multi-sim] ViewportWidget import 실패: {exc}", flush=True)
        except Exception:
            pass
        return False

    try:
        sn = max(2, int(n))
    except Exception:
        sn = 2

    vw_host = None
    for _ in range(24):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return False
        vw_host = _resolve_main_viewport_window(ext)
        if vw_host is not None:
            break
        await kit_app.get_app().next_update_async()
    if vw_host is None:
        try:
            print("[TBS multi-sim] ViewportWindow(get_frame) 없음 — Widget 분할 불가", flush=True)
        except Exception:
            pass
        return False

    _destroy_split_widget_host_ui(ext)
    _purge_widget_mode_stale_windows(ext)

    ww, hh = _viewport_client_pixel_size()
    half_w = max(_VP_TILE_MIN_PX, int(ww) // 2 if ww >= 16 else 640)
    th = max(_VP_TILE_MIN_PX, int(hh) if hh >= 16 else 480)

    try:
        tiles = _build_split_widget_shell(
            ext,
            vw_host,
            half_w,
            th,
            ViewportWidget=ViewportWidget,
        )
        if tiles is None:
            teardown_split_widget_host(ext)
            return False
    except Exception as exc:
        try:
            print(f"[TBS multi-sim] Viewport get_frame Widget 분할 실패: {exc}", flush=True)
        except Exception:
            pass
        teardown_split_widget_host(ext)
        return False

    try:
        ext._tbs_split_widget_tiles = tiles
        ext._tbs_split_viewport_host_window = vw_host
        ext._tbs_split_widget_host_win = vw_host
        ext._tbs_split_used_widget_layout = True
        ext._tbs_split_used_dock_layout = False
        ext._tbs_active_widget_tile = "Viewport"
        ext._tbs_split_main_viewport_window = vw_host
        ext._tbs_main_stage_connected = False
        ext._tbs_aux_stage_connected = False
        ext._tbs_aux_vw_materialized = False
        ext._tbs_widget_stage_sync_key = None
        ext._tbs_widget_post_stage_hud_synced = False
        ext._tbs_widget_split_ready = False
        try:
            ext._tbs_widget_create_total = int(_WIDGET_CREATE_COUNT)
        except Exception:
            pass
        ref_api = _reference_viewport_render_api(ext, tiles)
        main_rec = tiles.get("Viewport")
        if isinstance(main_rec, dict) and main_rec.get("api") is not None:
            _copy_visual_render_profile_only(ref_api, main_rec.get("api"))
            _kick_viewport_widget_render(main_rec)
    except Exception:
        pass

    _suspend_native_viewport_widget_presenter(ext)
    _disable_native_viewport_navigation_permanent(ext)
    ensure_viewport_workspace_tab_visible()
    sync_split_widget_fill_frame(ext, sn)
    main_rec = tiles.get("Viewport")
    if isinstance(main_rec, dict):
        _schedule_tile_manipulators_when_ready(ext, int(token), sn)
    _set_active_widget_tile(ext, "Viewport")

    try:
        print(
            "[TBS multi-sim] ViewportWidget shell 적용 — 화면1=#1, 화면2=master_2 후 deferred #2",
            flush=True,
        )
    except Exception:
        pass
    return True


def sync_split_widget_aux_render(ext: Any) -> None:
    """보조 ViewportWidget 렌더 설정을 메인 타일과 맞춘다."""
    _sync_aux_tile_render_from_main(ext)


def apply_split_widget_navigation(ext: Any, n: int, token: int, *, hold_ticks: int = 16) -> None:
    """Widget 타일 카메라 조작 — 네이티브 manipulator 영구 off + 타일별 manipulator."""
    if not is_split_widget_layout_active(ext):
        return
    _suspend_native_viewport_widget_presenter(ext)
    _disable_native_viewport_navigation_permanent(ext)
    ensure_viewport_workspace_tab_visible()
    active = str(getattr(ext, "_tbs_active_widget_tile", "") or "Viewport")
    _activate_tile_manipulator_only(ext, active)
    _schedule_tile_manipulators_when_ready(ext, int(token), int(n))


__all__ = [
    "ensure_viewport_workspace_tab_visible",
    "sync_split_widget_aux_render",
    "apply_split_widget_navigation",
    "sim_viewport_split_widget_enabled",
    "is_split_widget_layout_active",
    "get_split_viewport_api",
    "get_split_hud_mount",
    "get_viewport_tile_hud_mount",
    "teardown_split_widget_host",
    "split_widget_layout_healthy",
    "sync_split_widget_fill_frame",
    "sync_widget_aux_resolution_from_workspace",
    "refresh_split_widget_tiles_after_stage",
    "apply_split_widget_layout",
    "assign_widget_split_cameras",
    "finalize_widget_split_startup",
    "connect_widget_tile_main_stage",
    "request_aux_widget_materialize",
    "sync_viewport_hud_when_ready",
]
