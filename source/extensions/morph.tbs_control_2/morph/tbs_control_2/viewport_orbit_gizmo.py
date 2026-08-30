"""Viewport 우측 상단 Unity 스타일 orbit 기즈모 (단일 파일 · 단일 Viewport 전용).

다른 확장에 붙일 때는 이 파일만 복사/import 하고 ``attach_orbit_gizmo(ext, prim_path)`` 호출.
화면2·분할 viewport 는 고려하지 않는다 — 메인 ``Viewport`` 하나만 대상.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom  # type: ignore

_PRINT_PREFIX = "[TBS/OrbitGizmo]"
_FRAME_SLOT = "morph.tbs_control_2:zz_orbit_gizmo"
_PERSP_CAMERA_PATH = "/OmniverseKit_Persp"
_COI_ATTR = "omni:kit:centerOfInterest"

_ANIM_DURATION_SEC = 0.28
_DRAG_SENSITIVITY_DEG = 0.35
_DISTANCE_SCALE = 2.2
_MIN_DISTANCE = 0.05
_MAX_DISTANCE = 1.0e6


# ---------------------------------------------------------------------------
# Orbit camera math
# ---------------------------------------------------------------------------


@dataclass
class _OrbitCameraState:
    target: Tuple[float, float, float]
    distance: float
    yaw_deg: float
    pitch_deg: float
    up: Tuple[float, float, float] = (0.0, 0.0, 1.0)

    def eye(self) -> Tuple[float, float, float]:
        yaw = math.radians(float(self.yaw_deg))
        pitch = math.radians(float(self.pitch_deg))
        cp = math.cos(pitch)
        dx = float(self.distance) * cp * math.cos(yaw)
        dy = float(self.distance) * cp * math.sin(yaw)
        dz = float(self.distance) * math.sin(pitch)
        tx, ty, tz = self.target
        return (tx + dx, ty + dy, tz + dz)

    def snapshot(self) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        return self.eye(), tuple(self.target)


def _vec3(t: Tuple[float, float, float]) -> Gf.Vec3d:
    return Gf.Vec3d(float(t[0]), float(t[1]), float(t[2]))


def _smoothstep01(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def _clamp_pitch(pitch_deg: float) -> float:
    return max(-89.0, min(89.0, float(pitch_deg)))


def _get_viewport_api() -> Any:
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name  # type: ignore

        return get_viewport_from_window_name("Viewport")
    except Exception:
        return None


def _resolve_viewport_window() -> Any:
    """단일 메인 Viewport — 분할 화면2 등은 대상 아님."""
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name  # type: ignore

        api = get_viewport_from_window_name("Viewport")
        for attr in ("viewport_window", "window", "_viewport_window", "_window"):
            cand = getattr(api, attr, None) if api is not None else None
            if cand is not None and callable(getattr(cand, "get_frame", None)):
                return cand
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


def _resolve_viewport_context_name(viewport_api: Any) -> str:
    if viewport_api is None:
        return ""
    try:
        cn = getattr(viewport_api, "usd_context_name", None)
        if cn is not None and str(cn).strip():
            return str(cn).strip()
    except Exception:
        pass
    try:
        ctx = getattr(viewport_api, "usd_context", None)
        if ctx is not None and hasattr(ctx, "get_name"):
            return str(ctx.get_name() or "").strip()
    except Exception:
        pass
    return ""


def _get_stage(usd_context_name: str = "") -> Optional[Usd.Stage]:
    try:
        import omni.usd  # type: ignore

        if usd_context_name:
            ctx = omni.usd.get_context(str(usd_context_name))
            if ctx is not None:
                return ctx.get_stage()
        return omni.usd.get_context().get_stage()
    except Exception:
        return None


def _prim_world_center(stage: Usd.Stage, prim_path: str) -> Optional[Tuple[float, float, float]]:
    path = str(prim_path or "").strip()
    if not stage or not path:
        return None
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return None
    try:
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        box = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        c = (box.GetMin() + box.GetMax()) * 0.5
        return (float(c[0]), float(c[1]), float(c[2]))
    except Exception:
        pass
    try:
        t = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
        return (float(t[0]), float(t[1]), float(t[2]))
    except Exception:
        return None


def _prim_bounds_radius(stage: Usd.Stage, prim_path: str, *, fallback: float = 1.0) -> float:
    path = str(prim_path or "").strip()
    if not stage or not path:
        return float(fallback)
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return float(fallback)
    try:
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        box = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        diag = (box.GetMax() - box.GetMin()).GetLength()
        if diag > 1e-6:
            return float(diag) * 0.5
    except Exception:
        pass
    return float(fallback)


def _read_viewport_eye_target(viewport_api: Any) -> Optional[
    Tuple[Tuple[float, float, float], Tuple[float, float, float]]
]:
    if viewport_api is None:
        return None
    try:
        from omni.kit.viewport.utility.camera_state import ViewportCameraState  # type: ignore

        st = ViewportCameraState(viewport_api)
        if st is None:
            return None
        eye = st.position_world()
        target = st.target_world()
        if eye is None or target is None:
            return None
        return (
            (float(eye[0]), float(eye[1]), float(eye[2])),
            (float(target[0]), float(target[1]), float(target[2])),
        )
    except Exception:
        return None


def _state_from_eye_target(
    eye: Tuple[float, float, float],
    target: Tuple[float, float, float],
    *,
    up: Tuple[float, float, float] = (0.0, 0.0, 1.0),
) -> _OrbitCameraState:
    dx = float(eye[0]) - float(target[0])
    dy = float(eye[1]) - float(target[1])
    dz = float(eye[2]) - float(target[2])
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    if dist < 1e-9:
        return _OrbitCameraState(target=target, distance=1.0, yaw_deg=0.0, pitch_deg=15.0, up=up)
    pitch_deg = math.degrees(math.asin(max(-1.0, min(1.0, dz / dist))))
    yaw_deg = math.degrees(math.atan2(dy, dx))
    return _OrbitCameraState(
        target=target, distance=dist, yaw_deg=yaw_deg, pitch_deg=pitch_deg, up=up
    )


def _snap_axis_angles(axis: str) -> Tuple[float, float]:
    key = str(axis or "").strip().lower()
    return {
        "x": (0.0, 0.0),
        "+x": (0.0, 0.0),
        "-x": (180.0, 0.0),
        "y": (90.0, 0.0),
        "+y": (90.0, 0.0),
        "-y": (-90.0, 0.0),
        "z": (0.0, 89.0),
        "+z": (0.0, 89.0),
        "-z": (0.0, -89.0),
    }.get(key, (0.0, 20.0))


def _resolve_camera_up(
    eye: Tuple[float, float, float],
    target: Tuple[float, float, float],
    up_hint: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    fwd = _vec3(target) - _vec3(eye)
    if fwd.GetLength() < 1e-9:
        return (0.0, 0.0, 1.0)
    fwd.Normalize()
    up_v = _vec3(up_hint)
    if up_v.GetLength() < 1e-9:
        up_v = Gf.Vec3d(0.0, 0.0, 1.0)
    else:
        up_v.Normalize()
    if abs(Gf.Dot(fwd, up_v)) > 0.999:
        alt = Gf.Vec3d(0.0, 0.0, 1.0)
        if abs(Gf.Dot(fwd, alt)) > 0.999:
            alt = Gf.Vec3d(0.0, 1.0, 0.0)
        up_v = alt
    return (float(up_v[0]), float(up_v[1]), float(up_v[2]))


def _camera_local_matrix(
    cam_prim: Usd.Prim,
    eye: Tuple[float, float, float],
    target: Tuple[float, float, float],
    up: Tuple[float, float, float],
) -> Gf.Matrix4d:
    fwd = _vec3(target) - _vec3(eye)
    if fwd.GetLength() < 1e-9:
        return Gf.Matrix4d(1.0)
    fwd.Normalize()
    up_r = _resolve_camera_up(eye, target, up)
    up_v = _vec3(up_r)
    right = Gf.Cross(fwd, up_v)
    if right.GetLength() < 1e-9:
        return Gf.Matrix4d(1.0)
    right.Normalize()
    up_c = Gf.Cross(right, fwd)
    up_c.Normalize()
    m = Gf.Matrix4d(1.0)
    m.SetRow(0, Gf.Vec4d(right[0], right[1], right[2], 0.0))
    m.SetRow(1, Gf.Vec4d(up_c[0], up_c[1], up_c[2], 0.0))
    m.SetRow(2, Gf.Vec4d(-fwd[0], -fwd[1], -fwd[2], 0.0))
    m.SetRow(3, Gf.Vec4d(float(eye[0]), float(eye[1]), float(eye[2]), 1.0))
    parent = cam_prim.GetParent()
    if parent and parent.IsValid() and not parent.IsPseudoRoot():
        px = UsdGeom.Xformable(parent).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        return px.GetInverse() * m
    return m


def _apply_eye_target(
    eye: Tuple[float, float, float],
    target: Tuple[float, float, float],
    *,
    up: Tuple[float, float, float] = (0.0, 0.0, 1.0),
    usd_context_name: str = "",
    viewport_api: Any = None,
) -> bool:
    stage = _get_stage(usd_context_name)
    if not stage:
        return False
    cam_prim = stage.GetPrimAtPath(_PERSP_CAMERA_PATH)
    if not cam_prim or not cam_prim.IsValid():
        return False
    dist = (_vec3(target) - _vec3(eye)).GetLength()
    if dist < 1e-9:
        return False
    local = _camera_local_matrix(cam_prim, eye, target, up)
    try:
        edit = Usd.EditContext(stage, Usd.EditTarget(stage.GetSessionLayer()))
        with edit:
            xformable = UsdGeom.Xformable(cam_prim)
            xformable.ClearXformOpOrder()
            op = xformable.AddTransformOp()
            op.Set(local)
            coi = Gf.Vec3d(0.0, 0.0, -float(dist))
            coi_attr = cam_prim.GetAttribute(_COI_ATTR)
            if not coi_attr or not coi_attr.IsValid():
                cam_prim.CreateAttribute(_COI_ATTR, Sdf.ValueTypeNames.Vector3d, custom=True).Set(coi)
            else:
                coi_attr.Set(coi)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} camera write failed: {exc}", flush=True)
        return False
    api = viewport_api if viewport_api is not None else _get_viewport_api()
    if api is not None:
        try:
            from omni.kit.viewport.utility.camera_state import ViewportCameraState  # type: ignore

            st = ViewportCameraState(api)
            if st is not None:
                st.set_position_world(_vec3(eye), True)
                st.set_target_world(_vec3(target), True)
        except Exception:
            pass
    return True


# ---------------------------------------------------------------------------
# Gizmo UI
# ---------------------------------------------------------------------------


def _build_gizmo_widget(
    *,
    on_orbit_drag: Callable[[float, float], None],
    on_axis_snap: Callable[[str], None],
    on_frame_target: Callable[[], None],
) -> Any:
    import omni.ui as ui  # type: ignore

    drag = {"on": False, "lx": 0.0, "ly": 0.0}

    def _press(x: float, y: float, *_) -> bool:
        drag["on"] = True
        drag["lx"], drag["ly"] = float(x), float(y)
        return True

    def _release(*_) -> bool:
        drag["on"] = False
        return True

    def _move(x: float, y: float, *_) -> bool:
        if not drag["on"]:
            return False
        dx, dy = float(x) - drag["lx"], float(y) - drag["ly"]
        drag["lx"], drag["ly"] = float(x), float(y)
        if abs(dx) > 1e-6 or abs(dy) > 1e-6:
            on_orbit_drag(dx, dy)
        return True

    def _wire(w: Any) -> None:
        w.set_mouse_pressed_fn(_press)
        w.set_mouse_released_fn(_release)
        w.set_mouse_moved_fn(_move)

    root = ui.ZStack(width=92, height=108)
    with root:
        bg = ui.Rectangle(style={"background_color": 0xCC1A1D22, "border_radius": 6})
        _wire(bg)
        with ui.VStack(spacing=3):
            ui.Spacer(height=4)
            with ui.HStack():
                ui.Spacer()
                ui.Label("Persp", style={"color": 0xFFCCCCCC, "font_size": 11})
                ui.Spacer(width=8)
            with ui.ZStack(height=64):
                c = ui.Label("◎", alignment=ui.Alignment.CENTER, style={"color": 0x88FFFFFF, "font_size": 24})
                _wire(c)
                with ui.VStack():
                    ui.Button("Y", width=20, height=20, clicked_fn=lambda: on_axis_snap("+y"),
                              style={"background_color": 0xFF50E070})
                    ui.Spacer()
                with ui.HStack():
                    ui.Button("Z", width=20, height=20, clicked_fn=lambda: on_axis_snap("+z"),
                              style={"background_color": 0xFF5080F0})
                    ui.Spacer()
                    ui.Button("X", width=20, height=20, clicked_fn=lambda: on_axis_snap("+x"),
                              style={"background_color": 0xFFE05050})
            with ui.HStack(height=20):
                ui.Spacer(width=6)
                ui.Button("Frame", height=18, clicked_fn=on_frame_target,
                          tooltip="타깃 PRIM 프레이밍")
                ui.Spacer(width=6)
    return root


# ---------------------------------------------------------------------------
# Controller + public API
# ---------------------------------------------------------------------------


class _OrbitGizmoController:
    def __init__(self, ext: Any, target_prim_path: str) -> None:
        self._ext = ext
        self._target_prim_path = str(target_prim_path or "").strip()
        self._root: Any = None
        self._mount: Any = None
        self._sched_token = 0
        self._orbit: Optional[_OrbitCameraState] = None
        self._viewport_api: Any = None
        self._usd_context = ""
        self._anim_sub: Any = None
        self._anim_start = 0.0
        self._anim_from_eye = (0.0, 0.0, 0.0)
        self._anim_to_eye = (0.0, 0.0, 0.0)
        self._anim_target = (0.0, 0.0, 0.0)
        self._anim_up = (0.0, 0.0, 1.0)

    def destroy(self) -> None:
        self._stop_anim()
        self._root = None
        mount = self._mount or _resolve_viewport_window()
        if mount is not None and callable(getattr(mount, "get_frame", None)):
            try:
                import omni.ui as ui  # type: ignore

                with mount.get_frame(_FRAME_SLOT):
                    ui.Spacer(height=0)
            except Exception:
                pass
        try:
            self._ext._orbit_gizmo_mounted = False
        except Exception:
            pass

    def sync_mount(self, *, delay_frames: int = 12) -> None:
        if not self._target_prim_path:
            return
        self._sched_token += 1
        token = self._sched_token

        def _try(n: int) -> None:
            if token != self._sched_token:
                return
            mount = _resolve_viewport_window()
            if mount is not None:
                self._mount_on(mount, token)
                return
            if n > 0:
                try:
                    import omni.kit.app  # type: ignore

                    app = omni.kit.app.get_app()
                    if app is not None:
                        app.post_update(lambda: _try(n - 1))
                except Exception:
                    pass
            else:
                print(f"{_PRINT_PREFIX} Viewport unavailable", flush=True)

        _try(max(0, int(delay_frames)))

    def _mount_on(self, mount: Any, token: int) -> None:
        if token != self._sched_token:
            return
        import omni.ui as ui  # type: ignore

        try:
            with mount.get_frame(_FRAME_SLOT):
                ui.Spacer(height=0)
        except Exception:
            pass
        self._mount = mount
        self._viewport_api = _get_viewport_api()
        self._usd_context = _resolve_viewport_context_name(self._viewport_api)
        self._refresh_target()
        pair = _read_viewport_eye_target(self._viewport_api)
        if pair and self._orbit:
            self._orbit = _state_from_eye_target(pair[0], self._orbit.target, up=self._orbit.up)

        ra = getattr(ui, "Alignment", None)
        rt = getattr(ra, "RIGHT_TOP", None) if ra is not None else None
        with mount.get_frame(_FRAME_SLOT):
            outer = ui.ZStack(alignment=rt) if rt is not None else ui.ZStack()
            self._root = outer
            with outer:
                with ui.VStack(width=0, height=0):
                    ui.Spacer(height=8)
                    with ui.HStack():
                        ui.Spacer()
                        _build_gizmo_widget(
                            on_orbit_drag=self._on_drag,
                            on_axis_snap=self._on_axis,
                            on_frame_target=lambda: self._frame(True),
                        )
                        ui.Spacer(width=8)
        try:
            self._ext._orbit_gizmo_mounted = True
        except Exception:
            pass
        print(f"{_PRINT_PREFIX} mounted target={self._target_prim_path!r}", flush=True)

    def _refresh_target(self) -> None:
        stage = _get_stage(self._usd_context)
        center = _prim_world_center(stage, self._target_prim_path) if stage else None
        if center is None:
            center = (0.0, 0.0, 0.0)
            print(f"{_PRINT_PREFIX} prim not found: {self._target_prim_path!r}", flush=True)
        radius = _prim_bounds_radius(stage, self._target_prim_path) if stage else 1.0
        dist = max(_MIN_DISTANCE, min(_MAX_DISTANCE, float(radius) * _DISTANCE_SCALE))
        if self._orbit is None:
            self._orbit = _OrbitCameraState(center, dist, 35.0, 22.0)
        else:
            self._orbit = _OrbitCameraState(
                center, self._orbit.distance, self._orbit.yaw_deg, self._orbit.pitch_deg, self._orbit.up
            )

    def _apply(self, st: _OrbitCameraState) -> None:
        eye, tgt = st.snapshot()
        _apply_eye_target(eye, tgt, up=st.up, usd_context_name=self._usd_context, viewport_api=self._viewport_api)

    def _on_drag(self, dx: float, dy: float) -> None:
        if not self._orbit:
            return
        self._stop_anim()
        self._orbit = _OrbitCameraState(
            self._orbit.target,
            self._orbit.distance,
            self._orbit.yaw_deg - dx * _DRAG_SENSITIVITY_DEG,
            _clamp_pitch(self._orbit.pitch_deg - dy * _DRAG_SENSITIVITY_DEG),
            self._orbit.up,
        )
        self._apply(self._orbit)

    def _on_axis(self, axis: str) -> None:
        if not self._orbit:
            return
        yaw, pitch = _snap_axis_angles(axis)
        self._orbit = _OrbitCameraState(
            self._orbit.target, self._orbit.distance, yaw, pitch, self._orbit.up
        )
        self._animate_to(self._orbit)

    def _frame(self, smooth: bool) -> None:
        self._refresh_target()
        if not self._orbit:
            return
        stage = _get_stage(self._usd_context)
        radius = _prim_bounds_radius(stage, self._target_prim_path) if stage else 1.0
        dist = max(_MIN_DISTANCE, min(_MAX_DISTANCE, float(radius) * _DISTANCE_SCALE))
        self._orbit = _OrbitCameraState(self._orbit.target, dist, 35.0, 22.0, self._orbit.up)
        if smooth:
            self._animate_to(self._orbit)
        else:
            self._apply(self._orbit)

    def _animate_to(self, state: _OrbitCameraState) -> None:
        pair = _read_viewport_eye_target(self._viewport_api)
        if not pair:
            self._apply(state)
            return
        eye1, tgt = state.snapshot()
        self._anim_from_eye = pair[0]
        self._anim_to_eye = eye1
        self._anim_target = tgt
        self._anim_up = state.up
        self._anim_start = time.perf_counter()
        self._stop_anim()
        try:
            import omni.kit.app  # type: ignore

            app = omni.kit.app.get_app()
            if app is None:
                self._tick_anim(1.0)
                return
            self._anim_sub = app.get_update_event_stream().create_subscription_to_pop(
                self._on_anim, name="tbs.viewport_orbit_gizmo.anim"
            )
        except Exception:
            self._tick_anim(1.0)

    def _stop_anim(self) -> None:
        sub = self._anim_sub
        self._anim_sub = None
        if sub is not None:
            try:
                sub.unsubscribe()
            except Exception:
                pass

    def _on_anim(self, *_a: Any) -> None:
        t = (time.perf_counter() - self._anim_start) / max(1e-3, _ANIM_DURATION_SEC)
        if t >= 1.0:
            self._tick_anim(1.0)
            self._stop_anim()
            return
        self._tick_anim(t)

    def _tick_anim(self, t: float) -> None:
        u = _smoothstep01(t)
        eye = tuple(
            self._anim_from_eye[i] + (self._anim_to_eye[i] - self._anim_from_eye[i]) * u for i in range(3)
        )
        _apply_eye_target(
            eye, self._anim_target, up=self._anim_up,
            usd_context_name=self._usd_context, viewport_api=self._viewport_api,
        )


def attach_orbit_gizmo(ext: Any, target_prim_path: str, *, delay_frames: int = 12) -> None:
    """메인 Viewport 우측 상단에 orbit 기즈모를 붙인다."""
    path = str(target_prim_path or "").strip()
    if not path:
        print(f"{_PRINT_PREFIX} attach skipped — empty prim path", flush=True)
        return
    destroy_orbit_gizmo(ext)
    ctrl = _OrbitGizmoController(ext, path)
    try:
        ext._orbit_gizmo_controller = ctrl
    except Exception:
        pass
    ctrl.sync_mount(delay_frames=int(delay_frames))
    print(f"{_PRINT_PREFIX} attach {path!r}", flush=True)


def destroy_orbit_gizmo(ext: Any) -> None:
    ctrl = getattr(ext, "_orbit_gizmo_controller", None)
    if ctrl is not None:
        try:
            ctrl.destroy()
        except Exception:
            pass
    try:
        ext._orbit_gizmo_controller = None
        ext._orbit_gizmo_mounted = False
    except Exception:
        pass


__all__ = ["attach_orbit_gizmo", "destroy_orbit_gizmo"]
