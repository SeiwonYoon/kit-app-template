"""Viewport 우측 상단 Unity 스타일 orbit 기즈모 (단일 파일 · 단일 Viewport 전용).

이식 방법
---------
1. 이 파일만 대상 프로젝트에 복사한다.
2. 확장 ``extension.py`` 의 ``on_startup`` / ``on_shutdown`` 에 아래만 추가한다::

       # from .viewport_orbit_gizmo import attach_orbit_gizmo, destroy_orbit_gizmo
       # attach_orbit_gizmo(self, "/World/YourOrbitTargetPrim")
       ...
       # destroy_orbit_gizmo(self)

   다른 모듈과 설정 공유·import 없이 이 파일만으로 동작한다.

모드 (조작은 동일, 보여주는 방식만 다름)
---------------------------------------
- **Camera 모드**: 뷰포트가 지정 Camera prim 을 look-through → 촬영 화면으로 확인.
- **Perspective 모드**: 뷰포트는 ``/OmniverseKit_Persp`` → 씬 안에서 Camera prim 이
  어떻게 움직이는지 눈으로 확인.

두 모드 모두 기즈모(드래그·3D 축 클릭·X/Y/Z·Frame)는 **동일한 USD Camera prim** 의 transform 을 갱신한다.

패널 중앙 **3D 축 뷰큐브** (``omni.ui.scene``) 는 orbit yaw/pitch 와 동기 회전하며,
축 핸들 클릭 시 거리 유지 fly(``_animate_to``) 를 수행한다.

실제 조작 흐름
--------------
``_OrbitGizmoController._on_drag`` / ``_on_axis`` / ``_frame``
  → ``_apply_orbit_state`` (orbit 수학 → eye/target)
  → ``_write_camera_prim_view`` (session layer 에 Camera prim 기록)  ← 항상 여기
  → Camera 모드일 때만 뷰포트가 자동으로 그 prim 시점을 따름
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom  # type: ignore

# =============================================================================
# 설정 — 패널 위치·크기·카메라 경로 (이 파일 최상단에서만 조정)
# =============================================================================

_PRINT_PREFIX = "[TBS/OrbitGizmo]"
_FRAME_SLOT = "morph.tbs_control_2:zz_orbit_gizmo"
_PERSP_CAMERA_PATH = "/OmniverseKit_Persp"
_COI_ATTR = "omni:kit:centerOfInterest"

# --- 조작패널 (뷰포트 우측 상단 오버레이) ---
ORBIT_GIZMO_PANEL_MARGIN_TOP: int = 8
ORBIT_GIZMO_PANEL_MARGIN_RIGHT: int = 8
ORBIT_GIZMO_PANEL_WIDTH: int = 108
ORBIT_GIZMO_PANEL_MIN_HEIGHT: int = 0  # 0 = 내용에 맞춤
ORBIT_GIZMO_PANEL_BG_COLOR: int = 0xCC1A1D22
ORBIT_GIZMO_PANEL_BORDER_RADIUS: int = 6
ORBIT_GIZMO_PANEL_SPACING: int = 4
ORBIT_GIZMO_TITLE_FONT_SIZE: int = 11

# --- 3D 축 오리엔테이션 위젯 (뷰큐브) ---
ORBIT_GIZMO_ORBIT_RING_HEIGHT: int = 76
ORBIT_GIZMO_AXIS_LENGTH: float = 0.58
ORBIT_GIZMO_AXIS_NEG_LENGTH: float = 0.38
ORBIT_GIZMO_CUBE_HALF: float = 0.11
ORBIT_GIZMO_AXIS_HIT_THICKNESS: int = 14
ORBIT_GIZMO_DRAG_ARC_RADIUS: float = 0.88
ORBIT_GIZMO_LABEL_SIZE: int = 14

# 축 색 (RGBA 0..1)
_ORBIT_GIZMO_COLOR_X = (0.88, 0.22, 0.22, 1.0)
_ORBIT_GIZMO_COLOR_Y = (0.22, 0.82, 0.35, 1.0)
_ORBIT_GIZMO_COLOR_Z = (0.28, 0.48, 0.95, 1.0)
_ORBIT_GIZMO_COLOR_NEG = (0.72, 0.74, 0.78, 0.9)
_ORBIT_GIZMO_COLOR_CUBE = (0.92, 0.93, 0.95, 1.0)

# --- 하단 버튼 일렬 (X · Y · Z · Frame) ---
ORBIT_GIZMO_BUTTON_ROW_HEIGHT: int = 22
ORBIT_GIZMO_AXIS_BUTTON_SIZE: int = 22
ORBIT_GIZMO_FRAME_BUTTON_WIDTH: int = 44
ORBIT_GIZMO_BUTTON_SPACING: int = 3
ORBIT_GIZMO_BUTTON_ROW_MARGIN_H: int = 6
ORBIT_GIZMO_BUTTON_ROW_MARGIN_BOTTOM: int = 6

# --- 카메라 경로 입력 ---
ORBIT_GIZMO_PATH_FIELD_HEIGHT: int = 20
ORBIT_GIZMO_PATH_FONT_SIZE: int = 11

# --- orbit 수학 ---
ORBIT_GIZMO_ANIM_DURATION_SEC: float = 0.28
ORBIT_GIZMO_DRAG_SENSITIVITY_DEG: float = 0.35
ORBIT_GIZMO_DISTANCE_SCALE: float = 2.2
ORBIT_GIZMO_MIN_DISTANCE: float = 0.05
ORBIT_GIZMO_MAX_DISTANCE: float = 1.0e6
ORBIT_GIZMO_FRAME_YAW_DEG: float = 35.0
ORBIT_GIZMO_FRAME_PITCH_DEG: float = 22.0

# --- 코드에서 카메라·모드 지정 (UI 입력보다 우선하지 않음 — attach 시 기본값) ---
# look-through / 조작 대상 USD Camera prim
ORBIT_GIZMO_CAMERA_PRIM_PATH: str = "/Camera"
# True → attach 직후 Camera 모드(촬영 시점). False → Perspective(씬에서 prim 확인)
ORBIT_GIZMO_START_IN_CAMERA_MODE: bool = True
# 비어 있지 않으면 attach 인자 대신 이 경로를 orbit 타깃(피벗)으로 쓴다
ORBIT_GIZMO_DEFAULT_TARGET_PRIM_PATH: str = ""


class _ViewMode(str, Enum):
    CAMERA = "camera"
    PERSPECTIVE = "perspective"


# =============================================================================
# Orbit 상태 (타깃 prim 주위 eye 위치)
# =============================================================================


@dataclass
class _OrbitCameraState:
    """타깃 중심 구면 좌표. ``eye()`` 가 USD Camera prim 이 가야 할 위치."""

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

    def eye_target(self) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        return self.eye(), tuple(self.target)


@dataclass
class _PerspViewSnapshot:
    eye_xyz: Tuple[float, float, float]
    target_xyz: Tuple[float, float, float]


# =============================================================================
# 수학 유틸
# =============================================================================


def _vec3(t: Tuple[float, float, float]) -> Gf.Vec3d:
    return Gf.Vec3d(float(t[0]), float(t[1]), float(t[2]))


def _smoothstep01(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def _clamp_pitch(pitch_deg: float) -> float:
    return max(-89.0, min(89.0, float(pitch_deg)))


def _lerp3(
    a: Tuple[float, float, float],
    b: Tuple[float, float, float],
    u: float,
) -> Tuple[float, float, float]:
    return (
        a[0] + (b[0] - a[0]) * u,
        a[1] + (b[1] - a[1]) * u,
        a[2] + (b[2] - a[2]) * u,
    )


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


def _orientation_gizmo_matrix(yaw_deg: float, pitch_deg: float) -> Gf.Matrix4d:
    """월드 축을 카메라 orbit(yaw/pitch)에 맞게 화면에 투영 — 역회전."""
    rz = Gf.Rotation(Gf.Vec3d(0.0, 0.0, 1.0), float(-yaw_deg))
    ry = Gf.Rotation(Gf.Vec3d(0.0, 1.0, 0.0), float(-pitch_deg))
    m = Gf.Matrix4d(1.0)
    m.SetRotateOnly(rz * ry)
    return m


def _axis_tip(axis: str, length: float) -> Tuple[float, float, float]:
    key = str(axis or "").strip().lower()
    if key in ("+x", "x"):
        return (float(length), 0.0, 0.0)
    if key == "-x":
        return (-float(length), 0.0, 0.0)
    if key in ("+y", "y"):
        return (0.0, float(length), 0.0)
    if key == "-y":
        return (0.0, -float(length), 0.0)
    if key in ("+z", "z"):
        return (0.0, 0.0, float(length))
    if key == "-z":
        return (0.0, 0.0, -float(length))
    return (0.0, 0.0, 0.0)


# =============================================================================
# 3D 축 오리엔테이션 위젯 (SceneView — orbit yaw/pitch 와 동기 회전)
# =============================================================================


class _OrientationGizmoWidget:
    """패널 중앙 뷰큐브 — 드래그 orbit·fly 애니와 동일 yaw/pitch 로 Transform 갱신."""

    def __init__(
        self,
        *,
        on_axis_snap: Callable[[str], None],
        on_orbit_drag_end: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_axis_snap = on_axis_snap
        self._on_orbit_drag_end = on_orbit_drag_end
        self._axes_transform: Any = None
        self._drag_arc: Any = None
        self._scene_view: Any = None
        self._dragging = False
        self._drag_dx = 0.0
        self._drag_dy = 0.0

    def mount(
        self,
        wire_mouse: Callable[[Any], None],
        *,
        height: int = ORBIT_GIZMO_ORBIT_RING_HEIGHT,
    ) -> Any:
        import omni.ui as ui  # type: ignore
        import omni.ui.scene as sc  # type: ignore

        stack = ui.ZStack(height=int(height))
        with stack:
            self._scene_view = sc.SceneView(
                aspect_ratio_policy=sc.AspectRatioPolicy.PRESERVE_ASPECT_FIT,
                height=ui.Percent(100),
            )
            with self._scene_view.scene:
                # 드래그 방향 arc — 축 회전과 분리(화면 고정 링)
                self._drag_arc = sc.Arc(
                    ORBIT_GIZMO_DRAG_ARC_RADIUS,
                    begin=0.0,
                    end=0.05,
                    thickness=3,
                    color=(1.0, 1.0, 1.0, 0.55),
                    wireframe=True,
                    sector=False,
                    visible=False,
                )
                self._axes_transform = sc.Transform()
                with self._axes_transform:
                    self._build_axes_scene(sc)
            # 투명 히트 영역 — orbit 드래그
            hit = ui.Rectangle(style={"background_color": 0x01FFFFFF})
            wire_mouse(hit)
        return stack

    def _build_axes_scene(self, sc: Any) -> None:
        import omni.ui as ui  # type: ignore

        h = ORBIT_GIZMO_CUBE_HALF
        cube_edges = (
            ((-h, -h, -h), (h, -h, -h)),
            ((h, -h, -h), (h, h, -h)),
            ((h, h, -h), (-h, h, -h)),
            ((-h, h, -h), (-h, -h, -h)),
            ((-h, -h, h), (h, -h, h)),
            ((h, -h, h), (h, h, h)),
            ((h, h, h), (-h, h, h)),
            ((-h, h, h), (-h, -h, h)),
            ((-h, -h, -h), (-h, -h, h)),
            ((h, -h, -h), (h, -h, h)),
            ((h, h, -h), (h, h, h)),
            ((-h, h, -h), (-h, h, h)),
        )
        for a, b in cube_edges:
            sc.Line(a, b, color=_ORBIT_GIZMO_COLOR_CUBE, thickness=2)

        axis_specs = (
            ("+x", ORBIT_GIZMO_AXIS_LENGTH, _ORBIT_GIZMO_COLOR_X, 4, "x"),
            ("-x", ORBIT_GIZMO_AXIS_NEG_LENGTH, _ORBIT_GIZMO_COLOR_NEG, 2, ""),
            ("+y", ORBIT_GIZMO_AXIS_LENGTH, _ORBIT_GIZMO_COLOR_Y, 4, "y"),
            ("-y", ORBIT_GIZMO_AXIS_NEG_LENGTH, _ORBIT_GIZMO_COLOR_NEG, 2, ""),
            ("+z", ORBIT_GIZMO_AXIS_LENGTH, _ORBIT_GIZMO_COLOR_Z, 4, "z"),
            ("-z", ORBIT_GIZMO_AXIS_NEG_LENGTH, _ORBIT_GIZMO_COLOR_NEG, 2, ""),
        )
        origin = (0.0, 0.0, 0.0)
        for axis_id, length, color, thick, label in axis_specs:
            tip = _axis_tip(axis_id, length)

            def _click(axis: str = axis_id) -> Callable[[Any], None]:
                def _on_ended(*_a: Any) -> None:
                    try:
                        self._on_axis_snap(axis)
                    except Exception:
                        pass

                return _on_ended

            sc.Line(
                origin,
                tip,
                color=color,
                thickness=int(thick),
                intersection_thickness=ORBIT_GIZMO_AXIS_HIT_THICKNESS,
                gesture=sc.ClickGesture(on_ended_fn=_click()),
            )
            if label:
                with sc.Transform(
                    transform=sc.Matrix44.get_translation_matrix(tip[0], tip[1], tip[2])
                ):
                    with sc.Transform(scale_to=sc.Space.SCREEN):
                        sc.Label(
                            label,
                            size=ORBIT_GIZMO_LABEL_SIZE,
                            color=(1.0, 1.0, 1.0, 1.0),
                            alignment=ui.Alignment.CENTER,
                        )

    def update(
        self,
        yaw_deg: float,
        pitch_deg: float,
        *,
        drag_dx: float = 0.0,
        drag_dy: float = 0.0,
        dragging: bool = False,
    ) -> None:
        if self._axes_transform is not None:
            try:
                self._axes_transform.transform = _orientation_gizmo_matrix(yaw_deg, pitch_deg)
            except Exception:
                pass
        self._dragging = bool(dragging)
        self._drag_dx = float(drag_dx)
        self._drag_dy = float(drag_dy)
        self._update_drag_arc()

    def clear_drag_hint(self) -> None:
        self._dragging = False
        self._drag_dx = 0.0
        self._drag_dy = 0.0
        self._update_drag_arc()

    def _update_drag_arc(self) -> None:
        arc = self._drag_arc
        if arc is None:
            return
        try:
            if not self._dragging or (abs(self._drag_dx) < 0.5 and abs(self._drag_dy) < 0.5):
                arc.visible = False
                return
            mag = math.hypot(self._drag_dx, self._drag_dy)
            span = min(1.4, 0.08 + mag * 0.018)
            center = math.atan2(self._drag_dy, self._drag_dx)
            arc.visible = True
            arc.begin = float(center - span * 0.5)
            arc.end = float(center + span * 0.5)
        except Exception:
            pass


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


# =============================================================================
# Viewport / USD 접근
# =============================================================================


def _get_viewport_api() -> Any:
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name  # type: ignore

        return get_viewport_from_window_name("Viewport")
    except Exception:
        return None


def _resolve_viewport_window() -> Any:
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


def _read_camera_prim_eye_target(
    camera_path: str,
    *,
    usd_context_name: str = "",
) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
    """USD Camera prim 의 world eye·target (COI 기반)."""
    stage = _get_stage(usd_context_name)
    path = str(camera_path or "").strip()
    if not stage or not path:
        return None
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return None
    try:
        xfc = UsdGeom.XformCache(Usd.TimeCode.Default())
        world = xfc.GetLocalToWorldTransform(prim)
        eye = world.ExtractTranslation()
        coi_attr = prim.GetAttribute(_COI_ATTR)
        coi = Gf.Vec3d(0.0, 0.0, -500.0)
        if coi_attr and coi_attr.IsValid():
            raw = coi_attr.Get()
            if raw is not None:
                coi = Gf.Vec3d(float(raw[0]), float(raw[1]), float(raw[2]))
        dist = abs(float(coi[2]))
        if dist < 1e-6:
            dist = 500.0
        forward = world.TransformDir(Gf.Vec3d(0.0, 0.0, -1.0))
        if forward.GetLength() < 1e-9:
            forward = Gf.Vec3d(0.0, 0.0, -1.0)
        else:
            forward.Normalize()
        target = eye + forward * dist
        return (
            (float(eye[0]), float(eye[1]), float(eye[2])),
            (float(target[0]), float(target[1]), float(target[2])),
        )
    except Exception:
        return None


def _write_camera_prim_view(
    camera_path: str,
    eye: Tuple[float, float, float],
    target: Tuple[float, float, float],
    *,
    up: Tuple[float, float, float] = (0.0, 0.0, 1.0),
    usd_context_name: str = "",
) -> bool:
    """**조작의 실제 적용 지점** — orbit 결과를 USD Camera prim session xform 에 기록."""
    stage = _get_stage(usd_context_name)
    path = str(camera_path or "").strip()
    if not stage or not path:
        return False
    cam_prim = stage.GetPrimAtPath(path)
    if not cam_prim or not cam_prim.IsValid():
        print(f"{_PRINT_PREFIX} Camera prim 없음: {path!r}", flush=True)
        return False
    dist = (_vec3(target) - _vec3(eye)).GetLength()
    if dist < 1e-9:
        return False
    local = _camera_local_matrix(cam_prim, eye, target, up)
    try:
        with Usd.EditContext(stage, Usd.EditTarget(stage.GetSessionLayer())):
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
        return True
    except Exception as exc:
        print(f"{_PRINT_PREFIX} camera prim write failed: {exc}", flush=True)
        return False


def _apply_persp_view(
    snap: _PerspViewSnapshot,
    *,
    usd_context_name: str = "",
    viewport_api: Any = None,
) -> bool:
    """Perspective session 카메라 + ViewportCameraState 동기화 (모드 전환·복원용)."""
    stage = _get_stage(usd_context_name)
    if not stage:
        return False
    cam_prim = stage.GetPrimAtPath(_PERSP_CAMERA_PATH)
    if not cam_prim or not cam_prim.IsValid():
        return False
    dist = (_vec3(snap.target_xyz) - _vec3(snap.eye_xyz)).GetLength()
    if dist < 1e-9:
        return False
    up = (0.0, 0.0, 1.0)
    local = _camera_local_matrix(cam_prim, snap.eye_xyz, snap.target_xyz, up)
    try:
        with Usd.EditContext(stage, Usd.EditTarget(stage.GetSessionLayer())):
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
        print(f"{_PRINT_PREFIX} persp write failed: {exc}", flush=True)
        return False
    api = viewport_api if viewport_api is not None else _get_viewport_api()
    if api is not None:
        try:
            from omni.kit.viewport.utility.camera_state import ViewportCameraState  # type: ignore

            st = ViewportCameraState(api)
            if st is not None:
                st.set_position_world(_vec3(snap.eye_xyz), True)
                st.set_target_world(_vec3(snap.target_xyz), True)
        except Exception:
            pass
    return True


def _capture_persp_snapshot(viewport_api: Any) -> Optional[_PerspViewSnapshot]:
    pair = _read_viewport_eye_target(viewport_api)
    if pair is None:
        return None
    return _PerspViewSnapshot(eye_xyz=pair[0], target_xyz=pair[1])


def _set_viewport_look_through(camera_path: str, viewport_api: Any) -> bool:
    path = str(camera_path or "").strip()
    if not path or viewport_api is None:
        return False
    try:
        viewport_api.camera_path = Sdf.Path(path)
        return True
    except Exception:
        try:
            viewport_api.camera_path = path
            return True
        except Exception:
            return False


def _persp_view_to_show_camera(
    camera_eye: Tuple[float, float, float],
    orbit_target: Tuple[float, float, float],
    *,
    distance_scale: float = 1.6,
) -> _PerspViewSnapshot:
    """Perspective 모드 진입 시 Camera prim 이 화면에 보이도록 Persp 시점 계산."""
    eye_v = _vec3(camera_eye)
    tgt_v = _vec3(orbit_target)
    diff = eye_v - tgt_v
    if diff.GetLength() < 1e-6:
        diff = Gf.Vec3d(1.0, 1.0, 0.5)
    diff.Normalize()
    back = float(distance_scale) * max(
        ORBIT_GIZMO_MIN_DISTANCE,
        (_vec3(camera_eye) - _vec3(orbit_target)).GetLength() * 0.85,
    )
    persp_eye = eye_v + diff * back
    mid = (eye_v + tgt_v) * 0.5
    return _PerspViewSnapshot(
        eye_xyz=(float(persp_eye[0]), float(persp_eye[1]), float(persp_eye[2])),
        target_xyz=(float(mid[0]), float(mid[1]), float(mid[2])),
    )


# =============================================================================
# Gizmo UI (조작패널 — 드래그·버튼 입력만 컨트롤러 콜백으로 전달)
# =============================================================================


def _build_gizmo_widget(
    *,
    mode_label: str,
    camera_path: str,
    on_camera_path_changed: Callable[[str], None],
    on_mode_camera: Callable[[], None],
    on_mode_perspective: Callable[[], None],
    on_orbit_drag: Callable[[float, float], None],
    on_axis_snap: Callable[[str], None],
    on_frame_target: Callable[[], None],
    orient_widget_out: Optional[list] = None,
    on_orbit_drag_end: Optional[Callable[[], None]] = None,
) -> Any:
    import omni.ui as ui  # type: ignore

    drag = {"on": False, "lx": 0.0, "ly": 0.0}

    def _press(x: float, y: float, *_) -> bool:
        drag["on"] = True
        drag["lx"], drag["ly"] = float(x), float(y)
        return True

    def _release(*_) -> bool:
        drag["on"] = False
        if on_orbit_drag_end is not None:
            try:
                on_orbit_drag_end()
            except Exception:
                pass
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

    panel_h = ORBIT_GIZMO_PANEL_MIN_HEIGHT if ORBIT_GIZMO_PANEL_MIN_HEIGHT > 0 else 0
    root = ui.ZStack(width=ORBIT_GIZMO_PANEL_WIDTH, height=panel_h or 0)
    with root:
        bg = ui.Rectangle(
            style={
                "background_color": ORBIT_GIZMO_PANEL_BG_COLOR,
                "border_radius": ORBIT_GIZMO_PANEL_BORDER_RADIUS,
            }
        )
        _wire(bg)
        with ui.VStack(spacing=ORBIT_GIZMO_PANEL_SPACING):
            ui.Spacer(height=4)
            # 모드 표시 + 전환
            with ui.HStack(height=ORBIT_GIZMO_BUTTON_ROW_HEIGHT):
                ui.Spacer(width=ORBIT_GIZMO_BUTTON_ROW_MARGIN_H)
                ui.Label(
                    str(mode_label),
                    style={"color": 0xFFCCCCCC, "font_size": ORBIT_GIZMO_TITLE_FONT_SIZE},
                )
                ui.Spacer()
                ui.Button(
                    "Cam",
                    width=28,
                    height=18,
                    tooltip="Camera 모드 — Camera prim 시점(look-through)",
                    clicked_fn=on_mode_camera,
                )
                ui.Button(
                    "Persp",
                    width=36,
                    height=18,
                    tooltip="Perspective 모드 — 씬에서 Camera prim 움직임 확인",
                    clicked_fn=on_mode_perspective,
                )
                ui.Spacer(width=4)
            # Camera prim 경로
            with ui.HStack(height=ORBIT_GIZMO_PATH_FIELD_HEIGHT):
                ui.Spacer(width=ORBIT_GIZMO_BUTTON_ROW_MARGIN_H)
                path_field = ui.StringField(
                    height=ORBIT_GIZMO_PATH_FIELD_HEIGHT,
                    style={"font_size": ORBIT_GIZMO_PATH_FONT_SIZE},
                )
                try:
                    path_field.model.set_value(str(camera_path or ""))
                except Exception:
                    pass

                def _on_path_end(*_a: Any) -> None:
                    try:
                        on_camera_path_changed(str(path_field.model.get_value_as_string() or "").strip())
                    except Exception:
                        pass

                path_field.model.add_end_edit_fn(_on_path_end)
                ui.Spacer(width=ORBIT_GIZMO_BUTTON_ROW_MARGIN_H)
            # 3D 축 뷰큐브 — orbit 드래그·축 클릭 (yaw/pitch 와 Transform 동기)
            orient = _OrientationGizmoWidget(
                on_axis_snap=on_axis_snap,
                on_orbit_drag_end=on_orbit_drag_end,
            )
            orient.mount(_wire, height=ORBIT_GIZMO_ORBIT_RING_HEIGHT)
            if orient_widget_out is not None:
                orient_widget_out.clear()
                orient_widget_out.append(orient)
            # 하단 일렬: X · Y · Z · Frame
            with ui.HStack(
                height=ORBIT_GIZMO_BUTTON_ROW_HEIGHT,
                spacing=ORBIT_GIZMO_BUTTON_SPACING,
            ):
                ui.Spacer(width=ORBIT_GIZMO_BUTTON_ROW_MARGIN_H)
                ui.Button(
                    "X",
                    width=ORBIT_GIZMO_AXIS_BUTTON_SIZE,
                    height=ORBIT_GIZMO_AXIS_BUTTON_SIZE,
                    tooltip="+X 축 정면에서 바라보기 (yaw=0°, pitch=0°)",
                    clicked_fn=lambda: on_axis_snap("+x"),
                    style={"background_color": 0xFFE05050},
                )
                ui.Button(
                    "Y",
                    width=ORBIT_GIZMO_AXIS_BUTTON_SIZE,
                    height=ORBIT_GIZMO_AXIS_BUTTON_SIZE,
                    tooltip="+Y 축 정면에서 바라보기 (yaw=90°, pitch=0°)",
                    clicked_fn=lambda: on_axis_snap("+y"),
                    style={"background_color": 0xFF50E070},
                )
                ui.Button(
                    "Z",
                    width=ORBIT_GIZMO_AXIS_BUTTON_SIZE,
                    height=ORBIT_GIZMO_AXIS_BUTTON_SIZE,
                    tooltip="+Z 축 위에서 바라보기 (pitch≈89°)",
                    clicked_fn=lambda: on_axis_snap("+z"),
                    style={"background_color": 0xFF5080F0},
                )
                ui.Button(
                    "Frame",
                    width=ORBIT_GIZMO_FRAME_BUTTON_WIDTH,
                    height=ORBIT_GIZMO_AXIS_BUTTON_SIZE,
                    tooltip="orbit 타깃 PRIM 을 화면에 맞게 거리·각도 재설정",
                    clicked_fn=on_frame_target,
                )
                ui.Spacer(width=ORBIT_GIZMO_BUTTON_ROW_MARGIN_H)
            ui.Spacer(height=ORBIT_GIZMO_BUTTON_ROW_MARGIN_BOTTOM)
    return root


# =============================================================================
# Controller
# =============================================================================


class _OrbitGizmoController:
    """기즈모 생명주기·모드·orbit 상태·애니메이션."""

    def __init__(self, ext: Any, target_prim_path: str, *, camera_prim_path: str) -> None:
        self._ext = ext
        self._target_prim_path = str(target_prim_path or "").strip()
        self._camera_prim_path = str(camera_prim_path or "").strip()
        self._view_mode = (
            _ViewMode.CAMERA if ORBIT_GIZMO_START_IN_CAMERA_MODE else _ViewMode.PERSPECTIVE
        )
        self._saved_persp: Optional[_PerspViewSnapshot] = None
        self._root: Any = None
        self._mount: Any = None
        self._sched_token = 0
        self._ui_rebuild_token = 0
        self._orbit: Optional[_OrbitCameraState] = None
        self._viewport_api: Any = None
        self._usd_context = ""
        self._anim_sub: Any = None
        self._anim_start = 0.0
        self._anim_from_eye = (0.0, 0.0, 0.0)
        self._anim_to_eye = (0.0, 0.0, 0.0)
        self._anim_target = (0.0, 0.0, 0.0)
        self._anim_up = (0.0, 0.0, 1.0)
        self._anim_dest_state: Optional[_OrbitCameraState] = None
        self._orient_widget: Optional[_OrientationGizmoWidget] = None
        self._anim_from_yaw = 0.0
        self._anim_from_pitch = 0.0
        self._drag_dx = 0.0
        self._drag_dy = 0.0
        self._dragging = False

    def _sync_orientation_widget(
        self,
        *,
        drag_dx: Optional[float] = None,
        drag_dy: Optional[float] = None,
        dragging: Optional[bool] = None,
    ) -> None:
        w = self._orient_widget
        if w is None:
            return
        if drag_dx is not None:
            self._drag_dx = float(drag_dx)
        if drag_dy is not None:
            self._drag_dy = float(drag_dy)
        if dragging is not None:
            self._dragging = bool(dragging)
        yaw = 0.0
        pitch = 0.0
        if self._anim_dest_state is not None:
            t = (time.perf_counter() - self._anim_start) / max(1e-3, ORBIT_GIZMO_ANIM_DURATION_SEC)
            u = _smoothstep01(min(1.0, float(t)))
            yaw = self._anim_from_yaw + (self._anim_dest_state.yaw_deg - self._anim_from_yaw) * u
            pitch = self._anim_from_pitch + (self._anim_dest_state.pitch_deg - self._anim_from_pitch) * u
        elif self._orbit is not None:
            yaw = float(self._orbit.yaw_deg)
            pitch = float(self._orbit.pitch_deg)
        w.update(
            yaw,
            pitch,
            drag_dx=self._drag_dx,
            drag_dy=self._drag_dy,
            dragging=self._dragging,
        )

    def _on_orbit_drag_end(self) -> None:
        self._dragging = False
        self._drag_dx = 0.0
        self._drag_dy = 0.0
        w = self._orient_widget
        if w is not None:
            w.clear_drag_hint()
        self._sync_orientation_widget(dragging=False)

    def destroy(self) -> None:
        self._stop_anim()
        self._orient_widget = None
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

    def _mode_label(self) -> str:
        return "Camera" if self._view_mode == _ViewMode.CAMERA else "Persp"

    def _rebuild_ui(self) -> None:
        mount = self._mount
        if mount is None:
            return
        import omni.ui as ui  # type: ignore

        self._ui_rebuild_token += 1
        token = self._ui_rebuild_token
        ra = getattr(ui, "Alignment", None)
        rt = getattr(ra, "RIGHT_TOP", None) if ra is not None else None
        try:
            with mount.get_frame(_FRAME_SLOT):
                ui.Spacer(height=0)
        except Exception:
            pass
        with mount.get_frame(_FRAME_SLOT):
            outer = ui.ZStack(alignment=rt) if rt is not None else ui.ZStack()
            self._root = outer
            with outer:
                with ui.VStack(width=0, height=0):
                    ui.Spacer(height=ORBIT_GIZMO_PANEL_MARGIN_TOP)
                    with ui.HStack():
                        ui.Spacer()
                        orient_holder: list = []
                        if token == self._ui_rebuild_token:
                            _build_gizmo_widget(
                                mode_label=self._mode_label(),
                                camera_path=self._camera_prim_path,
                                on_camera_path_changed=self._on_camera_path_changed,
                                on_mode_camera=self._enter_camera_mode,
                                on_mode_perspective=self._enter_perspective_mode,
                                on_orbit_drag=self._on_drag,
                                on_axis_snap=self._on_axis,
                                on_frame_target=lambda: self._frame(True),
                                orient_widget_out=orient_holder,
                                on_orbit_drag_end=self._on_orbit_drag_end,
                            )
                        self._orient_widget = orient_holder[0] if orient_holder else None
                        self._sync_orientation_widget()
                        ui.Spacer(width=ORBIT_GIZMO_PANEL_MARGIN_RIGHT)

    def _mount_on(self, mount: Any, token: int) -> None:
        if token != self._sched_token:
            return
        self._mount = mount
        self._viewport_api = _get_viewport_api()
        self._usd_context = _resolve_viewport_context_name(self._viewport_api)
        self._saved_persp = _capture_persp_snapshot(self._viewport_api)
        self._init_orbit_from_camera_or_target()
        self._rebuild_ui()
        if self._view_mode == _ViewMode.CAMERA:
            _set_viewport_look_through(self._camera_prim_path, self._viewport_api)
            if self._orbit:
                self._apply_orbit_state(self._orbit)
                self._sync_orientation_widget()
        else:
            self._enter_perspective_mode(silent=True)
        try:
            self._ext._orbit_gizmo_mounted = True
        except Exception:
            pass
        print(
            f"{_PRINT_PREFIX} mounted target={self._target_prim_path!r} "
            f"camera={self._camera_prim_path!r} mode={self._view_mode.value}",
            flush=True,
        )

    def _init_orbit_from_camera_or_target(self) -> None:
        """orbit 상태 초기화 — Camera prim pose 우선, 없으면 타깃 prim 중심."""
        pair = _read_camera_prim_eye_target(
            self._camera_prim_path, usd_context_name=self._usd_context
        )
        stage = _get_stage(self._usd_context)
        center = (
            _prim_world_center(stage, self._target_prim_path)
            if stage
            else None
        )
        if center is None:
            center = (0.0, 0.0, 0.0)
        if pair is not None:
            self._orbit = _state_from_eye_target(pair[0], center)
            self._orbit = _OrbitCameraState(
                center,
                self._orbit.distance,
                self._orbit.yaw_deg,
                self._orbit.pitch_deg,
                self._orbit.up,
            )
        else:
            radius = (
                _prim_bounds_radius(stage, self._target_prim_path) if stage else 1.0
            )
            dist = max(
                ORBIT_GIZMO_MIN_DISTANCE,
                min(ORBIT_GIZMO_MAX_DISTANCE, float(radius) * ORBIT_GIZMO_DISTANCE_SCALE),
            )
            self._orbit = _OrbitCameraState(
                center,
                dist,
                ORBIT_GIZMO_FRAME_YAW_DEG,
                ORBIT_GIZMO_FRAME_PITCH_DEG,
            )

    def _sync_orbit_from_camera_prim(self) -> None:
        if not self._orbit:
            return
        pair = _read_camera_prim_eye_target(
            self._camera_prim_path, usd_context_name=self._usd_context
        )
        if pair is None:
            return
        st = _state_from_eye_target(pair[0], self._orbit.target, up=self._orbit.up)
        self._orbit = _OrbitCameraState(
            self._orbit.target,
            st.distance,
            st.yaw_deg,
            st.pitch_deg,
            self._orbit.up,
        )

    def _apply_orbit_state(self, st: _OrbitCameraState) -> None:
        """orbit → Camera prim 기록 (모드 무관). Camera 모드면 look-through 가 따라감."""
        eye, tgt = st.eye_target()
        _write_camera_prim_view(
            self._camera_prim_path,
            eye,
            tgt,
            up=st.up,
            usd_context_name=self._usd_context,
        )

    def _enter_camera_mode(self, *, silent: bool = False) -> None:
        if self._view_mode == _ViewMode.CAMERA:
            return
        if self._viewport_api is not None:
            snap = _capture_persp_snapshot(self._viewport_api)
            if snap is not None:
                self._saved_persp = snap
        self._view_mode = _ViewMode.CAMERA
        _set_viewport_look_through(self._camera_prim_path, self._viewport_api)
        self._sync_orbit_from_camera_prim()
        if self._orbit:
            self._apply_orbit_state(self._orbit)
        self._rebuild_ui()
        if not silent:
            print(f"{_PRINT_PREFIX} mode → Camera (look-through {self._camera_prim_path!r})", flush=True)

    def _enter_perspective_mode(self, *, silent: bool = False) -> None:
        if self._view_mode == _ViewMode.PERSPECTIVE:
            return
        self._view_mode = _ViewMode.PERSPECTIVE
        _set_viewport_look_through(_PERSP_CAMERA_PATH, self._viewport_api)
        if self._orbit:
            eye, _ = self._orbit.eye_target()
            if self._saved_persp is not None:
                _apply_persp_view(
                    self._saved_persp,
                    usd_context_name=self._usd_context,
                    viewport_api=self._viewport_api,
                )
            else:
                show = _persp_view_to_show_camera(eye, self._orbit.target)
                _apply_persp_view(
                    show,
                    usd_context_name=self._usd_context,
                    viewport_api=self._viewport_api,
                )
        self._rebuild_ui()
        if not silent:
            print(f"{_PRINT_PREFIX} mode → Perspective (Camera prim visible in scene)", flush=True)

    def _on_camera_path_changed(self, path: str) -> None:
        path = str(path or "").strip()
        if not path or path == self._camera_prim_path:
            return
        self._camera_prim_path = path
        self._init_orbit_from_camera_or_target()
        if self._orbit:
            self._apply_orbit_state(self._orbit)
        self._sync_orientation_widget()
        if self._view_mode == _ViewMode.CAMERA:
            _set_viewport_look_through(self._camera_prim_path, self._viewport_api)
        print(f"{_PRINT_PREFIX} camera path → {path!r}", flush=True)

    # --- 사용자 조작 (드래그·축·Frame) — 모두 Camera prim 갱신 ---

    def _on_drag(self, dx: float, dy: float) -> None:
        if not self._orbit:
            return
        self._stop_anim()
        self._dragging = True
        self._drag_dx = float(dx)
        self._drag_dy = float(dy)
        self._orbit = _OrbitCameraState(
            self._orbit.target,
            self._orbit.distance,
            self._orbit.yaw_deg - dx * ORBIT_GIZMO_DRAG_SENSITIVITY_DEG,
            _clamp_pitch(self._orbit.pitch_deg - dy * ORBIT_GIZMO_DRAG_SENSITIVITY_DEG),
            self._orbit.up,
        )
        self._apply_orbit_state(self._orbit)
        self._sync_orientation_widget(drag_dx=dx, drag_dy=dy, dragging=True)

    def _on_axis(self, axis: str) -> None:
        if not self._orbit:
            return
        yaw, pitch = _snap_axis_angles(axis)
        self._orbit = _OrbitCameraState(
            self._orbit.target, self._orbit.distance, yaw, pitch, self._orbit.up
        )
        self._animate_to(self._orbit)

    def _frame(self, smooth: bool) -> None:
        self._refresh_orbit_target()
        if not self._orbit:
            return
        stage = _get_stage(self._usd_context)
        radius = _prim_bounds_radius(stage, self._target_prim_path) if stage else 1.0
        dist = max(
            ORBIT_GIZMO_MIN_DISTANCE,
            min(ORBIT_GIZMO_MAX_DISTANCE, float(radius) * ORBIT_GIZMO_DISTANCE_SCALE),
        )
        self._orbit = _OrbitCameraState(
            self._orbit.target,
            dist,
            ORBIT_GIZMO_FRAME_YAW_DEG,
            ORBIT_GIZMO_FRAME_PITCH_DEG,
            self._orbit.up,
        )
        if smooth:
            self._animate_to(self._orbit)
        else:
            self._apply_orbit_state(self._orbit)
            self._sync_orientation_widget()

    def _refresh_orbit_target(self) -> None:
        stage = _get_stage(self._usd_context)
        center = _prim_world_center(stage, self._target_prim_path) if stage else None
        if center is None:
            center = (0.0, 0.0, 0.0)
            print(f"{_PRINT_PREFIX} orbit target not found: {self._target_prim_path!r}", flush=True)
        if self._orbit is None:
            self._init_orbit_from_camera_or_target()
        elif self._orbit is not None:
            self._orbit = _OrbitCameraState(
                center,
                self._orbit.distance,
                self._orbit.yaw_deg,
                self._orbit.pitch_deg,
                self._orbit.up,
            )

    def _animate_to(self, state: _OrbitCameraState) -> None:
        pair = _read_camera_prim_eye_target(
            self._camera_prim_path, usd_context_name=self._usd_context
        )
        if not pair:
            self._apply_orbit_state(state)
            self._sync_orientation_widget()
            return
        eye1, tgt = state.eye_target()
        self._anim_from_eye = pair[0]
        self._anim_to_eye = eye1
        self._anim_target = tgt
        self._anim_up = state.up
        self._anim_dest_state = state
        if self._orbit is not None:
            self._anim_from_yaw = float(self._orbit.yaw_deg)
            self._anim_from_pitch = float(self._orbit.pitch_deg)
        else:
            self._anim_from_yaw = float(state.yaw_deg)
            self._anim_from_pitch = float(state.pitch_deg)
        self._dragging = False
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
        t = (time.perf_counter() - self._anim_start) / max(1e-3, ORBIT_GIZMO_ANIM_DURATION_SEC)
        if t >= 1.0:
            self._tick_anim(1.0)
            self._stop_anim()
            return
        self._tick_anim(t)

    def _tick_anim(self, t: float) -> None:
        u = _smoothstep01(t)
        eye = _lerp3(self._anim_from_eye, self._anim_to_eye, u)
        _write_camera_prim_view(
            self._camera_prim_path,
            eye,
            self._anim_target,
            up=self._anim_up,
            usd_context_name=self._usd_context,
        )
        self._sync_orientation_widget()
        if u >= 1.0 - 1e-9 and self._anim_dest_state is not None:
            self._orbit = self._anim_dest_state
            self._anim_dest_state = None
            self._sync_orientation_widget()


# =============================================================================
# Public API (extension.py 에서 주석 해제하여 호출)
# =============================================================================


def attach_orbit_gizmo(ext: Any, target_prim_path: str, *, delay_frames: int = 12) -> None:
    """메인 Viewport 우측 상단에 orbit 기즈모를 붙인다.

    Args:
        ext: Kit extension 인스턴스 (``self``).
        target_prim_path: orbit 피벗·Frame 대상 prim (예: ``/World/Equipment``).
        delay_frames: Viewport 창 준비 대기 프레임.

    Camera prim 경로는 ``ORBIT_GIZMO_CAMERA_PRIM_PATH`` 또는 패널 입력으로 지정.
    """
    tgt = str(ORBIT_GIZMO_DEFAULT_TARGET_PRIM_PATH or target_prim_path or "").strip()
    cam = str(ORBIT_GIZMO_CAMERA_PRIM_PATH or "").strip()
    if not tgt:
        print(f"{_PRINT_PREFIX} attach skipped — empty orbit target path", flush=True)
        return
    if not cam:
        print(f"{_PRINT_PREFIX} attach skipped — empty ORBIT_GIZMO_CAMERA_PRIM_PATH", flush=True)
        return
    destroy_orbit_gizmo(ext)
    ctrl = _OrbitGizmoController(ext, tgt, camera_prim_path=cam)
    try:
        ext._orbit_gizmo_controller = ctrl
    except Exception:
        pass
    ctrl.sync_mount(delay_frames=int(delay_frames))
    print(f"{_PRINT_PREFIX} attach target={tgt!r} camera={cam!r}", flush=True)


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
