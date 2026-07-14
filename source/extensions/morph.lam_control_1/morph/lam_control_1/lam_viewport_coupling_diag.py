"""
ViewportWidget 2분할 — 카메라 coupling 조사·추적 (관측 + READY 리포트).

P0-A: orbit 시 어느 Stage 의 Camera prim 이 변하는지 UsdNotice 로 기록.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

_CAM_PATH = "/OmniverseKit_Persp"


def coupling_diag_enabled() -> bool:
    try:
        from .lam_sim_control_defaults import VIEWPORT_COUPLING_DIAG_ENABLED

        return bool(VIEWPORT_COUPLING_DIAG_ENABLED)
    except Exception:
        return True


def _fmt_id(obj: Any) -> str:
    if obj is None:
        return "None"
    try:
        return f"{type(obj).__name__}@{id(obj):#x}"
    except Exception:
        return "?"


def _camera_world_position(stage: Any, cam_path: str = _CAM_PATH) -> Optional[Tuple[float, float, float]]:
    if stage is None:
        return None
    try:
        from pxr import UsdGeom

        prim = stage.GetPrimAtPath(cam_path)
        if not prim or not prim.IsValid():
            return None
        xf = UsdGeom.Xformable(prim)
        mat = xf.ComputeLocalToWorldTransform(0)
        t = mat.ExtractTranslation()
        return (float(t[0]), float(t[1]), float(t[2]))
    except Exception:
        return None


def _stage_label(stage: Any) -> str:
    if stage is None:
        return "stage=None"
    try:
        root = stage.GetRootLayer()
        ident = str(root.identifier if root is not None else "?")
        if len(ident) > 80:
            ident = ident[:77] + "..."
        return f"{ident} id={id(stage):#x}"
    except Exception:
        return f"stage@{id(stage):#x}"


def _named_context_stage(ctx_name: str) -> Any:
    try:
        import omni.usd

        ctx = omni.usd.get_context(str(ctx_name or "").strip())
        if ctx is None:
            ctx = omni.usd.get_context()
        return ctx.get_stage() if ctx is not None and hasattr(ctx, "get_stage") else None
    except Exception:
        return None


def snapshot_both_cameras(ext: Any, label: str = "") -> Dict[str, Any]:
    """default + aux context 의 Persp 카메라 world position 스냅샷."""
    out: Dict[str, Any] = {"label": label}
    try:
        from .lam_multi_viewport_widget import _tile_usd_context_name

        main_stage = _named_context_stage("")
        aux_stage = _named_context_stage(_tile_usd_context_name(1))
        out["main_stage"] = _stage_label(main_stage)
        out["aux_stage"] = _stage_label(aux_stage)
        out["main_cam_pos"] = _camera_world_position(main_stage)
        out["aux_cam_pos"] = _camera_world_position(aux_stage)
    except Exception as exc:
        out["error"] = str(exc)
    return out


def log_camera_snapshot(ext: Any, label: str) -> None:
    if not coupling_diag_enabled():
        return
    snap = snapshot_both_cameras(ext, label)
    try:
        print(
            f"[LAM/coupling-trace] {label!r} "
            f"main_pos={snap.get('main_cam_pos')} aux_pos={snap.get('aux_cam_pos')} "
            f"main={snap.get('main_stage')} aux={snap.get('aux_stage')}",
            flush=True,
        )
    except Exception:
        pass


def probe_tile_manipulator(rec: Dict[str, Any], wn: str) -> Dict[str, str]:
    """타일 manipulator / scene_view.model 상태."""
    out: Dict[str, str] = {"tile": str(wn)}
    if not isinstance(rec, dict):
        out["rec"] = "invalid"
        return out
    api = rec.get("api")
    scene_view = rec.get("scene_view")
    manip = rec.get("camera_manipulator")
    out["api"] = _fmt_id(api)
    out["scene_view"] = _fmt_id(scene_view)
    out["camera_manipulator"] = _fmt_id(manip)
    out["scene_view.camera_model"] = _fmt_id(getattr(scene_view, "camera_model", None))
    out["scene_view.model"] = _fmt_id(getattr(scene_view, "model", None))
    out["manip.model"] = _fmt_id(getattr(manip, "model", None) if manip is not None else None)
    out["scene_view_registered"] = str(bool(rec.get("scene_view_registered", False)))
    out["manip_pending"] = str(bool(rec.get("manip_pending", True)))
    if api is not None:
        try:
            out["api.camera_path"] = str(getattr(api, "camera_path", "") or "")
        except Exception:
            out["api.camera_path"] = "?"
        try:
            out["api.usd_context_name"] = str(
                getattr(api, "usd_context_name", None)
                or getattr(api, "context_name", None)
                or ""
            )
        except Exception:
            pass
    return out


def _manip_nav_disabled(model: Any) -> Optional[bool]:
    if model is None:
        return None
    for key in ("disable_tumble", "disable_pan", "disable_zoom", "disable_look"):
        try:
            vals = model.get_ints(key)
            if vals and int(vals[0]) != 0:
                return True
        except Exception:
            pass
    return False


def log_manipulator_activation_state(ext: Any, active_wn: str, reason: str = "") -> None:
    """Orbit/타일 활성화 시 각 타일 manip.model·navigation 상태 1줄."""
    if not coupling_diag_enabled():
        return
    try:
        from .lam_multi_viewport_widget import _tile_win_name, is_split_widget_layout_active

        if not is_split_widget_layout_active(ext):
            return
        tiles = getattr(ext, "_lam_split_widget_tiles", None)
        if not isinstance(tiles, dict):
            return
        parts: List[str] = []
        for wn in ("Viewport", _tile_win_name(1)):
            rec = tiles.get(wn)
            if not isinstance(rec, dict):
                continue
            probe = probe_tile_manipulator(rec, str(wn))
            nav = _manip_nav_disabled(
                getattr(rec.get("camera_manipulator"), "model", None)
                if rec.get("camera_manipulator") is not None
                else None
            )
            on = str(wn) == str(active_wn)
            parts.append(
                f"{wn}:api={probe.get('api')} manip={probe.get('manip.model')} "
                f"nav_off={nav} active={on}"
            )
        print(
            f"[LAM/coupling-trace] MANIP_ACTIVATE reason={reason!r} active={active_wn!r} "
            + " | ".join(parts),
            flush=True,
        )
    except Exception:
        pass


def log_orbit_context_snapshot(ext: Any, usd_label: str) -> None:
    """UsdNotice Persp 변경 직후 active tile·manip·active_viewport 스냅샷."""
    if not coupling_diag_enabled():
        return
    try:
        from .lam_multi_viewport_widget import (
            _get_native_viewport_api,
            _our_widget_tile_api_ids,
            is_split_widget_layout_active,
        )

        if not is_split_widget_layout_active(ext):
            return
        active_wn = str(getattr(ext, "_lam_active_widget_tile", "") or "Viewport")
        native = _get_native_viewport_api()
        our_ids = _our_widget_tile_api_ids(ext)
        print(
            f"[LAM/coupling-trace] ORBIT_CTX usd_label={usd_label!r} "
            f"active_tile={active_wn!r} native_api={_fmt_id(native)} "
            f"native_is_embedded={native is not None and id(native) in our_ids}",
            flush=True,
        )
        log_manipulator_activation_state(ext, active_wn, f"usd-changed:{usd_label}")
    except Exception:
        pass


def log_stage_lighting_summary(ext: Any, label: str = "") -> None:
    """main vs aux Stage UsdLux 경로 비교."""
    if not coupling_diag_enabled():
        return
    try:
        from .lam_multi_viewport_widget import (
            _stage_lux_prim_paths,
            _tile_usd_context_name,
            is_split_widget_layout_active,
        )

        if not is_split_widget_layout_active(ext):
            return
        main_stage = _named_context_stage("")
        aux_stage = _named_context_stage(_tile_usd_context_name(1))
        main_lux = _stage_lux_prim_paths(main_stage)
        aux_lux = _stage_lux_prim_paths(aux_stage)
        print(
            f"[LAM/coupling-report] stage-lux {label!r} "
            f"main_count={len(main_lux)} aux_count={len(aux_lux)}",
            flush=True,
        )
        if main_lux != aux_lux:
            print(
                f"[LAM/coupling-report]  main_lux={main_lux!r} aux_lux={aux_lux!r}",
                flush=True,
            )
    except Exception as exc:
        try:
            print(f"[LAM/coupling-report] stage-lux err={exc}", flush=True)
        except Exception:
            pass


def log_manipulator_investigation(ext: Any, phase: str = "READY") -> None:
    """READY 시 manipulator·native API 상태 출력."""
    if not coupling_diag_enabled():
        return
    try:
        from .lam_multi_viewport_widget import (
            _get_native_viewport_api,
            _our_widget_tile_api_ids,
            _tile_win_name,
            is_split_widget_layout_active,
        )

        if not is_split_widget_layout_active(ext):
            return
        tiles = getattr(ext, "_lam_split_widget_tiles", None)
        if not isinstance(tiles, dict):
            return

        print(f"[LAM/coupling-report] ===== phase={phase!r} =====", flush=True)

        for wn in ("Viewport", _tile_win_name(1)):
            rec = tiles.get(wn)
            if not isinstance(rec, dict):
                continue
            probe = probe_tile_manipulator(rec, str(wn))
            for k, v in probe.items():
                print(f"[LAM/coupling-report]  {k}={v}", flush=True)

        native_api = _get_native_viewport_api()
        our_ids = _our_widget_tile_api_ids(ext)
        native_is_ours = native_api is not None and id(native_api) in our_ids
        print(
            f"[LAM/coupling-report] native_api={_fmt_id(native_api)} "
            f"native_is_embedded_tile={native_is_ours}",
            flush=True,
        )
        if native_api is not None:
            for attr in ("enable_input", "inputs_enabled", "camera_path"):
                try:
                    print(
                        f"[LAM/coupling-report]  native.{attr}={getattr(native_api, attr, None)!r}",
                        flush=True,
                    )
                except Exception:
                    pass

        try:
            from omni.kit.widget.viewport import ViewportWidget

            inst = list(ViewportWidget.get_instances())
            print(f"[LAM/coupling-report] ViewportWidget.get_instances count={len(inst)}", flush=True)
            for i, w in enumerate(inst):
                ctx = getattr(w, "usd_context_name", None)
                vapi = getattr(w, "viewport_api", None)
                print(
                    f"[LAM/coupling-report]  #{i} widget={_fmt_id(w)} "
                    f"ctx={ctx!r} api={_fmt_id(vapi)}",
                    flush=True,
                )
        except Exception as exc:
            print(f"[LAM/coupling-report] get_instances err={exc}", flush=True)

        try:
            from omni.kit.viewport.utility import get_active_viewport

            active = get_active_viewport()
            print(f"[LAM/coupling-report] get_active_viewport()={_fmt_id(active)}", flush=True)
        except Exception as exc:
            print(f"[LAM/coupling-report] get_active_viewport err={exc}", flush=True)

        snap = snapshot_both_cameras(ext, phase)
        print(
            f"[LAM/coupling-report] main_cam={snap.get('main_cam_pos')} "
            f"aux_cam={snap.get('aux_cam_pos')}",
            flush=True,
        )
        print(f"[LAM/coupling-report] ===== end {phase!r} =====", flush=True)
    except Exception as exc:
        try:
            print(f"[LAM/coupling-report] FAIL {exc}", flush=True)
        except Exception:
            pass


class _CameraChangeTracker:
    """양 context Stage 의 Persp prim 변경 UsdNotice."""

    def __init__(self, ext: Any) -> None:
        self._ext = ext
        self._keys: List[Any] = []
        self._last_main: Optional[Tuple[float, float, float]] = None
        self._last_aux: Optional[Tuple[float, float, float]] = None

    def install(self) -> None:
        if not coupling_diag_enabled():
            return
        self.teardown()
        try:
            from pxr import Tf, Usd

            from .lam_multi_viewport_widget import _tile_usd_context_name

            for label, ctx_name in (("main", ""), ("aux", _tile_usd_context_name(1))):
                stage = _named_context_stage(ctx_name)
                if stage is None:
                    continue
                key = Tf.Notice.Register(
                    Usd.Notice.ObjectsChanged,
                    lambda n, l=label, s=stage: self._on_objects_changed(l, s, n),
                    stage,
                )
                self._keys.append(key)
            try:
                print("[LAM/coupling-trace] UsdNotice installed for main+aux Persp", flush=True)
            except Exception:
                pass
        except Exception as exc:
            try:
                print(f"[LAM/coupling-trace] UsdNotice install fail: {exc}", flush=True)
            except Exception:
                pass

    def _on_objects_changed(self, label: str, stage: Any, notice: Any) -> None:
        try:
            from pxr import Sdf

            cam_path = Sdf.Path(_CAM_PATH)
            changed = False
            for p in notice.GetChangedInfoOnlyPaths():
                p_str = str(p)
                if p != cam_path and not p_str.startswith(str(cam_path) + "."):
                    continue
                if p != cam_path:
                    # xformOp 등 하위 속성만 변경된 경우
                    if "xformOp" not in p_str and "transform" not in p_str.lower():
                        continue
                pos = _camera_world_position(stage, str(cam_path))
                prev = self._last_main if label == "main" else self._last_aux
                if pos != prev:
                    if label == "main":
                        self._last_main = pos
                    else:
                        self._last_aux = pos
                    changed = True
                    try:
                        print(
                            f"[LAM/coupling-trace] USD_CHANGED label={label!r} "
                            f"path={p_str} pos={pos}",
                            flush=True,
                        )
                    except Exception:
                        pass
                break
            if changed:
                log_orbit_context_snapshot(self._ext, label)
        except Exception:
            pass

    def teardown(self) -> None:
        for key in self._keys:
            try:
                key.Revoke()
            except Exception:
                pass
        self._keys.clear()


def install_camera_change_tracker(ext: Any) -> None:
    """ext._lam_coupling_cam_tracker 에 UsdNotice 추적기 설치."""
    if not coupling_diag_enabled():
        return
    try:
        old = getattr(ext, "_lam_coupling_cam_tracker", None)
        if old is not None:
            try:
                old.teardown()
            except Exception:
                pass
        tracker = _CameraChangeTracker(ext)
        tracker.install()
        ext._lam_coupling_cam_tracker = tracker
        log_camera_snapshot(ext, "tracker-installed")
    except Exception:
        pass


def teardown_camera_change_tracker(ext: Any) -> None:
    try:
        tracker = getattr(ext, "_lam_coupling_cam_tracker", None)
        if tracker is not None:
            tracker.teardown()
        ext._lam_coupling_cam_tracker = None
    except Exception:
        pass


def log_render_profile_diff(ext: Any) -> None:
    """main vs aux ViewportAPI 시각 attr 비교."""
    if not coupling_diag_enabled():
        return
    try:
        from .lam_multi_viewport_widget import (
            _RENDER_PROFILE_ATTRS,
            _tile_win_name,
            is_split_widget_layout_active,
        )

        if not is_split_widget_layout_active(ext):
            return
        tiles = getattr(ext, "_lam_split_widget_tiles", None)
        if not isinstance(tiles, dict):
            return
        main_rec = tiles.get("Viewport")
        aux_rec = tiles.get(_tile_win_name(1))
        if not isinstance(main_rec, dict) or not isinstance(aux_rec, dict):
            return
        main_api = main_rec.get("api")
        aux_api = aux_rec.get("api")
        if main_api is None or aux_api is None:
            return
        print("[LAM/coupling-report] -- render-profile-diff --", flush=True)
        attrs = list(_RENDER_PROFILE_ATTRS) + ("render_mode",)
        seen = set()
        for attr in attrs:
            if attr in seen:
                continue
            seen.add(attr)
            try:
                mv = getattr(main_api, attr, "<missing>")
                av = getattr(aux_api, attr, "<missing>")
                if mv != av:
                    print(
                        f"[LAM/coupling-report]  DIFF {attr}: main={mv!r} aux={av!r}",
                        flush=True,
                    )
            except Exception as exc:
                print(f"[LAM/coupling-report]  {attr} err={exc}", flush=True)
    except Exception:
        pass


__all__ = [
    "coupling_diag_enabled",
    "snapshot_both_cameras",
    "log_camera_snapshot",
    "probe_tile_manipulator",
    "log_manipulator_investigation",
    "log_manipulator_activation_state",
    "log_orbit_context_snapshot",
    "log_stage_lighting_summary",
    "install_camera_change_tracker",
    "teardown_camera_change_tracker",
    "log_render_profile_diff",
]
