"""Viewport Orbit Gizmo — 단일 파일 구현.

Blender 스타일 뷰큐브 + Cam/Persp 전환 + orbit 줌/Frame 패널.
기능 수정 시 아래 FUNCTION INDEX 로 해당 함수를 찾아 해당 섹션만 편집하세요.

FUNCTION INDEX
--------------
+---------------------------+------------------------------------------+
| 기능                      | 함수                                     |
+===========================+==========================================+
| 패널 조립                 | build_gizmo_panel                        |
| 패널 위치                 | mount_panel, PanelAnchor                 |
| 패널 드래그 회전          | handle_panel_drag, handle_panel_drag_end |
| Cam/Persp 전환            | switch_to_camera_mode,                   |
|                           | switch_to_perspective_mode               |
| 줌                        | handle_zoom                              |
| Frame                     | handle_frame                             |
| 3D 축 그리기              | draw_axis_arms, build_view_cube_scene    |
| Camera 생성               | create_camera_prim, ensure_camera_pose   |
| Camera pose 읽기/쓰기     | read_camera_pose, write_camera_pose       |
| orbit 초기화/적용         | init_orbit_state, apply_orbit_to_camera  |
| 뷰큐브 회전 갱신          | refresh_view_cube                        |
| 축 fly 애니메이션         | animate_orbit_to, tick_orbit_animation   |
| Viewport API              | get_viewport_api, read_viewport_pose     |
| 공개 attach/destroy       | attach_orbit_gizmo, destroy_orbit_gizmo  |
+---------------------------+------------------------------------------+
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, List, Optional, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom  # type: ignore


# =============================================================================
# 설정 (config)
# =============================================================================
# 수정 가이드: 패널 크기/위치 → PANEL_*, PANEL_ANCHOR, PANEL_MARGIN
#              뷰큐브 축 모양 → CUBE_*, AXIS_*, BULB_*, RING_*
#              orbit/줌/애니 → ANIM_SEC, DRAG_SENS, ZOOM_STEP, FRAME_*
#              기본 Camera 경로 → extension.py 의 _orbit_gizmo_camera_path 변수

_PRINT_PREFIX = "[TBS/OrbitGizmo]"
PRINT_PREFIX = _PRINT_PREFIX
_FRAME_SLOT = "morph.tbs_control_2:zz_orbit_gizmo"
FRAME_SLOT = _FRAME_SLOT
_COI_ATTR = "omni:kit:centerOfInterest"
COI_ATTR = _COI_ATTR
_KIT_PERSP_PATH = "/OmniverseKit_Persp"
KIT_PERSP_PATH = _KIT_PERSP_PATH

# 조작 대상 orbit 타깃 (Camera 경로는 호출측에서 전달)
_DEFAULT_CAMERA_PATH: str = "/Camera"   # 내부 폴백 전용 — 외부에서 직접 참조 금지
ORBIT_GIZMO_DEFAULT_TARGET_PRIM_PATH: str = ""
ORBIT_GIZMO_START_IN_CAMERA_MODE: bool = True

# 패널 레이아웃 — 상/하 버튼 행 숨김 후 축 gizmo 영역만
PANEL_WIDTH: int = 120        # gizmo 전용 (버튼 행 없음)
PANEL_HEIGHT: int = 120       # 정사각형
PANEL_MARGIN: int = 10
PANEL_BG: int = 0xBB1A1D22
PANEL_RADIUS: int = 8

# 패널 앵커: left_top, left_center, left_bottom,
#           center_top, center_center, center_bottom,
#           right_top, right_center, right_bottom
PANEL_ANCHOR: str = "right_center"

# 뷰큐브 (SceneView)
CUBE_HEIGHT: int = 120        # 패널과 같은 높이
AXIS_LEN: float = 0.8         # 양/음 축 길이 통일
AXIS_NEG_LEN: float = 0.8
BULB_RADIUS: float = 0.18
RING_RADIUS: float = 0.18     # 반대축도 같은 크기
DRAG_ARC_R: float = 0.92
LABEL_SIZE: int = 16

# orbit 동작
ANIM_SEC: float = 0.28
DRAG_SENS: float = 0.35
ZOOM_STEP: float = 1.12
MIN_DIST: float = 0.05
MAX_DIST: float = 1.0e6
FRAME_DIST_SCALE: float = 2.2
FRAME_YAW: float = 35.0
FRAME_PITCH: float = 22.0
SYNC_EPS: float = 1e-4
# 드래그 시 pitch 상한 — 89°(Z 스냅 각)까지 가면 극점에서 yaw 가 튀음
PITCH_DRAG_MAX: float = 85.0
# 패널 드래그와 축 클릭 구분
AXIS_DRAG_BLOCK_PX: float = 5.0
# 축 원(Arc) 클릭 픽 확장 (SceneView intersection_thickness, px)
AXIS_HIT: int = 20
# release 폴백 픽 반경 (로컬 좌표)
AXIS_PICK_RADIUS: int = 44
CUBE_SCREEN_SCALE: float = 0.46
# 축 클릭 디버그 로그
AXIS_CLICK_DEBUG: bool = True

# 축 색 (RGBA 0..1)
COL_X  = (0.90, 0.25, 0.25, 1.0)
COL_Y  = (0.25, 0.82, 0.38, 1.0)
COL_Z  = (0.30, 0.50, 0.95, 1.0)
# 반대축: 동일 색상이지만 ~70% 투명
COL_BX = (0.90, 0.25, 0.25, 0.30)   # -x
COL_BY = (0.25, 0.82, 0.38, 0.30)   # -y
COL_BZ = (0.30, 0.50, 0.95, 0.30)   # -z
COL_CENTER = (0.95, 0.96, 0.98, 1.0)

AXIS_SNAP = {
    "+x": (0.0, 0.0), "-x": (180.0, 0.0),
    "+y": (90.0, 0.0), "-y": (-90.0, 0.0),
    "+z": (0.0, 89.0), "-z": (0.0, -89.0),
}


# =============================================================================
# 타입 (types)
# =============================================================================
# 수정 가이드: orbit 상태 구조 → OrbitState
#              Cam/Persp 모드 enum → ViewMode

class ViewMode(str, Enum):
    CAMERA = "camera"
    PERSPECTIVE = "perspective"


class PanelAnchor(str, Enum):
    """뷰포트 내 패널 고정 위치."""

    LEFT_TOP = "left_top"
    LEFT_CENTER = "left_center"
    LEFT_BOTTOM = "left_bottom"
    CENTER_TOP = "center_top"
    CENTER_CENTER = "center_center"
    CENTER_BOTTOM = "center_bottom"
    RIGHT_TOP = "right_top"
    RIGHT_CENTER = "right_center"
    RIGHT_BOTTOM = "right_bottom"


@dataclass
class PanelInsets:
    """패널 여백 (픽셀). PanelAnchor 에 추가되는 오프셋."""

    top: int = 0
    left: int = 0
    right: int = 0
    bottom: int = 0

    @classmethod
    def uniform(cls, v: int) -> "PanelInsets":
        return cls(top=v, left=v, right=v, bottom=v)

    @classmethod
    def from_value(cls, v: Any) -> "PanelInsets":
        """int / tuple / PanelInsets 를 모두 수용."""
        if isinstance(v, PanelInsets):
            return v
        if isinstance(v, int):
            return cls.uniform(v)
        if isinstance(v, (tuple, list)) and len(v) >= 4:
            return cls(int(v[0]), int(v[1]), int(v[2]), int(v[3]))
        return cls.uniform(int(v) if v else 0)


@dataclass
class OrbitState:
    """타깃 중심 구면 좌표 — distance 가 줌 상태."""

    target: Tuple[float, float, float]
    distance: float
    yaw_deg: float
    pitch_deg: float
    up: Tuple[float, float, float] = (0.0, 0.0, 1.0)

    def eye(self) -> Tuple[float, float, float]:
        y, p = math.radians(self.yaw_deg), math.radians(self.pitch_deg)
        cp = math.cos(p)
        dx = self.distance * cp * math.cos(y)
        dy = self.distance * cp * math.sin(y)
        dz = self.distance * math.sin(p)
        t = self.target
        return (t[0] + dx, t[1] + dy, t[2] + dz)

    def with_angles(self, yaw: float, pitch: float) -> OrbitState:
        return OrbitState(self.target, self.distance, yaw, pitch, self.up)

    def with_distance(self, dist: float) -> OrbitState:
        return OrbitState(self.target, dist, self.yaw_deg, self.pitch_deg, self.up)


@dataclass
class ViewSnap:
    eye: Tuple[float, float, float]
    target: Tuple[float, float, float]


# =============================================================================
# 3D 수학 (math3d)
# =============================================================================
# 수정 가이드: orbit↔eye 변환 → orbit_from_eye
#              카메라 look-at → camera_basis
#              뷰큐브 역회전 → view_cube_matrix
#              축 방향 벡터 → axis_vec

def vec3(t: Tuple[float, float, float]) -> Gf.Vec3d:
    return Gf.Vec3d(float(t[0]), float(t[1]), float(t[2]))


def smoothstep(u: float) -> float:
    u = max(0.0, min(1.0, float(u)))
    return u * u * (3.0 - 2.0 * u)


def clamp_pitch(p: float, *, for_drag: bool = False) -> float:
    limit = PITCH_DRAG_MAX if for_drag else 89.0
    return max(-limit, min(limit, float(p)))


def lerp3(a: Tuple[float, float, float], b: Tuple[float, float, float], u: float):
    return tuple(a[i] + (b[i] - a[i]) * u for i in range(3))


def orbit_from_eye(
    eye,
    target,
    up=(0.0, 0.0, 1.0),
    *,
    prev_yaw: Optional[float] = None,
) -> OrbitState:
    dx, dy, dz = eye[0] - target[0], eye[1] - target[1], eye[2] - target[2]
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    if dist < 1e-9:
        return OrbitState(target, 1.0, 0.0, 15.0, up)
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, dz / dist))))
    horiz = math.hypot(dx, dy)
    # Z 극점 근처: yaw 재계산 시 0° 로 튀는 gimbal lock 방지
    if horiz < dist * 0.08 and prev_yaw is not None:
        yaw = prev_yaw
    else:
        yaw = math.degrees(math.atan2(dy, dx))
    return OrbitState(target, dist, yaw, pitch, up)


def camera_basis(eye, target, up_hint) -> Gf.Matrix4d:
    """eye→target look-at (USD Camera -Z 전방)."""
    fwd = vec3(target) - vec3(eye)
    if fwd.GetLength() < 1e-9:
        return Gf.Matrix4d(1.0)
    fwd.Normalize()
    up = vec3(up_hint)
    if up.GetLength() < 1e-9:
        up = Gf.Vec3d(0, 0, 1)
    else:
        up.Normalize()
    if abs(Gf.Dot(fwd, up)) > 0.999:
        up = Gf.Vec3d(0, 1, 0) if abs(Gf.Dot(fwd, up)) > 0.999 else Gf.Vec3d(0, 0, 1)
    right = Gf.Cross(fwd, up)
    right.Normalize()
    up_c = Gf.Cross(right, fwd)
    up_c.Normalize()
    m = Gf.Matrix4d(1.0)
    m.SetRow(0, Gf.Vec4d(right[0], right[1], right[2], 0))
    m.SetRow(1, Gf.Vec4d(up_c[0], up_c[1], up_c[2], 0))
    m.SetRow(2, Gf.Vec4d(-fwd[0], -fwd[1], -fwd[2], 0))
    m.SetRow(3, Gf.Vec4d(eye[0], eye[1], eye[2], 1))
    return m


def view_cube_matrix(eye, target, up=(0.0, 0.0, 1.0)) -> Gf.Matrix4d:
    """월드 축을 현재 카메라 시점에 맞게 역회전 (Blender 뷰큐브)."""
    cam = camera_basis(eye, target, up)
    inv = cam.GetInverse()
    m = Gf.Matrix4d(1.0)
    m.SetRotate(inv.ExtractRotation())
    return m


def axis_vec(axis: str, length: float) -> Tuple[float, float, float]:
    a = axis.lower()
    table = {
        "+x": (1, 0, 0), "-x": (-1, 0, 0),
        "+y": (0, 1, 0), "-y": (0, -1, 0),
        "+z": (0, 0, 1), "-z": (0, 0, -1),
    }
    d = table.get(a, (0, 0, 0))
    return (d[0] * length, d[1] * length, d[2] * length)


def _axis_dbg(msg: str) -> None:
    if AXIS_CLICK_DEBUG:
        print(f"{_PRINT_PREFIX}/AxisClick] {msg}", flush=True)


def project_axis_tip_to_screen(
    cube_m: Gf.Matrix4d,
    axis: str,
    width: float,
    height: float,
) -> Tuple[float, float]:
    tip = Gf.Vec3d(*axis_vec(axis, AXIS_LEN))
    p = cube_m.Transform(tip)
    scale = min(float(width), float(height)) * CUBE_SCREEN_SCALE
    cx, cy = float(width) * 0.5, float(height) * 0.5
    return cx + float(p[0]) * scale, cy - float(p[1]) * scale


def try_pick_axis_at_screen(
    cube_m: Gf.Matrix4d,
    local_x: float,
    local_y: float,
    width: float,
    height: float,
) -> Tuple[Optional[str], str]:
    if width < 1.0 or height < 1.0:
        return None, f"bad size w={width} h={height}"
    parts: List[str] = []
    best_axis: Optional[str] = None
    best_dist = float(AXIS_PICK_RADIUS) + 1.0
    for axis in ("+x", "-x", "+y", "-y", "+z", "-z"):
        sx, sy = project_axis_tip_to_screen(cube_m, axis, width, height)
        d = math.hypot(float(local_x) - sx, float(local_y) - sy)
        parts.append(f"{axis}=({sx:.0f},{sy:.0f}) d={d:.0f}")
        if d < best_dist:
            best_dist = d
            best_axis = axis
    detail = " ".join(parts)
    click = f"click=({local_x:.0f},{local_y:.0f})"
    if best_axis and best_dist <= float(AXIS_PICK_RADIUS):
        return best_axis, f"HIT {best_axis} d={best_dist:.0f} {click} | {detail}"
    near = f"nearest={best_axis} d={best_dist:.0f}" if best_axis else "no axis"
    return None, f"MISS {near} thr={AXIS_PICK_RADIUS} {click} | {detail}"


def _widget_local_xy(widget: Any, x: float, y: float) -> Tuple[float, float]:
    """SceneView 마우스 콜백 좌표 → 위젯 로컬 픽셀 (screen / local 자동 판별)."""
    fx, fy = float(x), float(y)
    if widget is None:
        return fx, fy
    try:
        w = float(widget.computed_width or 0)
        h = float(widget.computed_height or 0)
        wx = float(getattr(widget, "screen_position_x", 0) or 0)
        wy = float(getattr(widget, "screen_position_y", 0) or 0)
    except Exception:
        return fx, fy
    # 이미 로컬 좌표 (위젯 크기 이내)
    if w > 1.0 and h > 1.0 and 0.0 <= fx <= w + 4.0 and 0.0 <= fy <= h + 4.0:
        return fx, fy
    # 화면 좌표 → 로컬
    if wx != 0.0 or wy != 0.0:
        return fx - wx, fy - wy
    return fx, fy


def _viewport_target(ctrl: "OrbitGizmoController") -> Any:
    return ctrl.mount or get_viewport_window() or ctrl.api


def _pointer_over_widget(widget: Any) -> bool:
    if widget is None:
        return False
    xy = _pointer_screen_xy()
    if not xy:
        return False
    mx, my = xy
    try:
        x = float(getattr(widget, "screen_position_x", 0))
        y = float(getattr(widget, "screen_position_y", 0))
        w = float(widget.computed_width or 0)
        h = float(widget.computed_height or 0)
        if w < 2.0 or h < 2.0:
            return False
        pad = 4.0
        return (x - pad) <= mx < x + w + pad and (y - pad) <= my < y + h + pad
    except Exception:
        return False


def sync_panel_viewport_input_block(ctrl: "OrbitGizmoController") -> None:
    """패널 위 포인터가 있으면 Viewport 선택·클릭을 차단한다."""
    block = bool(ctrl.panel_pointer_down or ctrl.panel_hovered)
    if not block and ctrl.panel_root is not None:
        block = _pointer_over_widget(ctrl.panel_root)

    if block:
        vp = _viewport_target(ctrl)
        if vp and ctrl.selection_disable_scope is None:
            try:
                import omni.kit.viewport.utility as vpu  # type: ignore
                ctrl.selection_disable_scope = vpu.disable_selection(vp, disable_click=True)
            except Exception:
                ctrl.selection_disable_scope = None
        set_viewport_input_enabled(ctrl.api, False)
    else:
        if ctrl.selection_disable_scope is not None:
            ctrl.selection_disable_scope = None
        set_viewport_input_enabled(ctrl.api, True)
    ctrl.panel_viewport_blocked = block


def set_viewport_input_enabled(api: Any, enabled: bool) -> None:
    """네이티브 Viewport 입력 on/off."""
    if not api:
        return
    for attr in ("enable_input", "inputs_enabled"):
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


def _pointer_screen_xy() -> Optional[Tuple[float, float]]:
    try:
        import omni.appwindow as appwindow  # type: ignore

        window = appwindow.get_default_app_window()
        if window is None:
            return None
        mouse = window.get_mouse()
        if mouse is None:
            return None
        pos = mouse.get_position()
        return float(pos[0]), float(pos[1])
    except Exception:
        return None


# =============================================================================
# USD I/O (usd_io)
# =============================================================================
# 수정 가이드: 타깃 경로 해석 → resolve_target_path
#              타깃 중심/반경 → prim_center, prim_radius
#              Camera 생성/쓰기 → create_camera_prim, write_camera_pose,
#                                 ensure_camera_pose
#              Camera pose 읽기 → read_camera_pose  (viewport_io 아님!)

def get_stage(ctx: str = "") -> Optional[Usd.Stage]:
    try:
        import omni.usd  # type: ignore
        c = omni.usd.get_context(ctx) if ctx else omni.usd.get_context()
        return c.get_stage() if c else None
    except Exception:
        return None


def resolve_target_path(stage: Usd.Stage, path: str) -> str:
    """attach 경로(`/ebsonoff`) → stage 에서 실제 존재하는 prim 경로."""
    raw = str(path or "").strip()
    if not raw or not stage:
        return raw
    if not raw.startswith("/"):
        raw = "/" + raw
    candidates = [raw]
    if not raw.startswith("/World"):
        candidates.append(f"/World{raw}")
    name = raw.rstrip("/").split("/")[-1]
    if name:
        candidates.append(f"/World/{name}")
    seen = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        prim = stage.GetPrimAtPath(c)
        if prim and prim.IsValid():
            return c
    return raw


def prim_center(stage: Usd.Stage, path: str) -> Optional[Tuple[float, float, float]]:
    resolved = resolve_target_path(stage, path)
    if not stage or not resolved:
        return None
    prim = stage.GetPrimAtPath(resolved)
    if not prim or not prim.IsValid():
        return None
    try:
        box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]).ComputeWorldBound(prim).ComputeAlignedBox()
        c = (box.GetMin() + box.GetMax()) * 0.5
        return (float(c[0]), float(c[1]), float(c[2]))
    except Exception:
        pass
    try:
        t = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
        return (float(t[0]), float(t[1]), float(t[2]))
    except Exception:
        return None


def prim_radius(stage: Usd.Stage, path: str, fb: float = 1.0) -> float:
    resolved = resolve_target_path(stage, path)
    if not stage or not resolved:
        return fb
    prim = stage.GetPrimAtPath(resolved)
    if not prim or not prim.IsValid():
        return fb
    try:
        box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]).ComputeWorldBound(prim).ComputeAlignedBox()
        d = (box.GetMax() - box.GetMin()).GetLength()
        if d > 1e-6:
            return d * 0.5
    except Exception:
        pass
    return fb


def create_camera_prim(path: str, ctx: str = "") -> bool:
    """지정 경로에 Camera prim 이 없으면 stage 에 생성."""
    stage = get_stage(ctx)
    raw = str(path or "").strip()
    if not stage or not raw:
        return False
    prim = stage.GetPrimAtPath(raw)
    if prim and prim.IsValid() and prim.IsA(UsdGeom.Camera):
        return True
    for layer in (stage.GetSessionLayer(), stage.GetRootLayer()):
        if not layer:
            continue
        try:
            with Usd.EditContext(stage, Usd.EditTarget(layer)):
                cam = UsdGeom.Camera.Define(stage, raw)
                cam.CreateFocalLengthAttr(50.0)
                cam.CreateHorizontalApertureAttr(20.955)
                cam.CreateVerticalApertureAttr(15.2908)
                cam.CreateClippingRangeAttr(Gf.Vec2f(0.01, 1000000.0))
            prim = stage.GetPrimAtPath(raw)
            if prim and prim.IsValid():
                print(f"{_PRINT_PREFIX} created camera prim {raw!r}", flush=True)
                return True
        except Exception as exc:
            print(f"{_PRINT_PREFIX} create camera {raw!r} failed: {exc}", flush=True)
    return False


def write_camera_pose(path: str, eye, target, up=(0.0, 0.0, 1.0), ctx: str = "") -> bool:
    """Camera prim pose + centerOfInterest 기록."""
    stage = get_stage(ctx)
    if not stage:
        return False
    path = str(path or "").strip()
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        if not create_camera_prim(path, ctx):
            return False
        prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return False
    dist = (vec3(target) - vec3(eye)).GetLength()
    if dist < 1e-9:
        return False
    local = camera_basis(eye, target, up)
    parent = prim.GetParent()
    if parent and parent.IsValid() and not parent.IsPseudoRoot():
        local = UsdGeom.Xformable(parent).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).GetInverse() * local
    try:
        with Usd.EditContext(stage, Usd.EditTarget(stage.GetSessionLayer())):
            xf = UsdGeom.Xformable(prim)
            xf.ClearXformOpOrder()
            xf.AddTransformOp().Set(local)
            coi = Gf.Vec3d(0, 0, -float(dist))
            a = prim.GetAttribute(COI_ATTR)
            if not a or not a.IsValid():
                prim.CreateAttribute(COI_ATTR, Sdf.ValueTypeNames.Vector3d, custom=True).Set(coi)
            else:
                a.Set(coi)
        return True
    except Exception as exc:
        print(f"{_PRINT_PREFIX} write {path!r} failed: {exc}", flush=True)
        return False


def ensure_camera_pose(path: str, eye, target, up=(0.0, 0.0, 1.0), ctx: str = "") -> bool:
    """Camera prim 이 없으면 생성 후 orbit pose 로 초기 배치."""
    if create_camera_prim(path, ctx):
        return write_camera_pose(path, eye, target, up, ctx)
    return False


def read_camera_pose(path: str, ctx: str = "") -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
    """Camera prim → (eye, target) 쌍."""
    stage, path = get_stage(ctx), str(path or "").strip()
    if not stage or not path:
        return None
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return None
    try:
        xf = UsdGeom.XformCache(Usd.TimeCode.Default())
        w = xf.GetLocalToWorldTransform(prim)
        eye = w.ExtractTranslation()
        coi = prim.GetAttribute(COI_ATTR)
        dist = 500.0
        if coi and coi.IsValid() and coi.Get() is not None:
            dist = max(1e-6, abs(float(coi.Get()[2])))
        fwd = w.TransformDir(Gf.Vec3d(0, 0, -1))
        if fwd.GetLength() < 1e-9:
            fwd = Gf.Vec3d(0, 0, -1)
        else:
            fwd.Normalize()
        tgt = eye + fwd * dist
        return ((float(eye[0]), float(eye[1]), float(eye[2])), (float(tgt[0]), float(tgt[1]), float(tgt[2])))
    except Exception:
        return None


# =============================================================================
# Viewport I/O (viewport_io)
# =============================================================================
# 수정 가이드: Viewport 찾기 → get_viewport_api, get_viewport_window
#              Cam/Persp look-through → look_through_camera
#              뷰포트 pose 읽기 → read_viewport_pose
#              (read_camera_pose 는 USD 섹션 — 여기서 import 하지 않음)

def get_viewport_api() -> Any:
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name  # type: ignore
        return get_viewport_from_window_name("Viewport")
    except Exception:
        return None


def get_viewport_window() -> Any:
    api = get_viewport_api()
    if api:
        for a in ("viewport_window", "window", "_viewport_window", "_window"):
            w = getattr(api, a, None)
            if w and callable(getattr(w, "get_frame", None)):
                return w
    try:
        from omni.kit.viewport.utility import get_active_viewport_window  # type: ignore
        w = get_active_viewport_window()
        if w and callable(getattr(w, "get_frame", None)):
            return w
    except Exception:
        pass
    return None


def get_viewport_window_from_api(api: Any) -> Any:
    """viewport_api 기준으로 mount_panel에 쓸 수 있는 frame container를 찾는다."""
    if not api:
        return None
    for a in ("viewport_window", "window", "_viewport_window", "_window"):
        try:
            w = getattr(api, a, None)
            if w and callable(getattr(w, "get_frame", None)):
                return w
        except Exception:
            pass
    return None


def get_context_name(api: Any) -> str:
    if not api:
        return ""
    for a in ("usd_context_name",):
        v = getattr(api, a, None)
        if v and str(v).strip():
            return str(v).strip()
    ctx = getattr(api, "usd_context", None)
    return str(ctx.get_name() or "").strip() if ctx and hasattr(ctx, "get_name") else ""


def read_viewport_pose(api: Any) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
    if not api:
        return None
    try:
        from omni.kit.viewport.utility.camera_state import ViewportCameraState  # type: ignore
        st = ViewportCameraState(api)
        eye, tgt = st.position_world(), st.target_world()
        if eye is None or tgt is None:
            return None
        return ((float(eye[0]), float(eye[1]), float(eye[2])), (float(tgt[0]), float(tgt[1]), float(tgt[2])))
    except Exception:
        return None


def look_through_camera(api: Any, path: str) -> bool:
    """Viewport 가 지정 Camera prim 을 바라보도록 전환."""
    if not api or not path:
        return False
    try:
        api.camera_path = Sdf.Path(path)
        return True
    except Exception:
        try:
            api.camera_path = path
            return True
        except Exception:
            return False


def apply_viewport_snap(snap: ViewSnap, persp_path: str, api: Any, ctx: str = "") -> bool:
    """Persp camera prim + ViewportCameraState 동시 갱신 (레거시 유틸)."""
    if not write_camera_pose(persp_path, snap.eye, snap.target, ctx=ctx):
        return False
    if api:
        try:
            from omni.kit.viewport.utility.camera_state import ViewportCameraState  # type: ignore
            st = ViewportCameraState(api)
            st.set_position_world(vec3(snap.eye), True)
            st.set_target_world(vec3(snap.target), True)
        except Exception:
            pass
    return True


# =============================================================================
# 뷰큐브 (view_cube)
# =============================================================================
# 수정 가이드: 축 선/원 그리기 → draw_axis_arms, draw_center_dot
#              축 클릭 스냅 각도 → AXIS_SNAP (config 섹션)
#              뷰큐브 회전 갱신 → ViewCubeWidget.set_matrix
#              SceneView 조립 → build_view_cube_scene

def to_scene_matrix(m: Gf.Matrix4d) -> Any:
    import omni.ui.scene as sc  # type: ignore
    return sc.Matrix44(*[float(m[r][c]) for r in range(4) for c in range(4)])


def _wire_axis_click(
    ctrl: Optional["OrbitGizmoController"],
    on_axis: Callable[[str], None],
    axis: str,
) -> None:
    if ctrl:
        ctrl.axis_click_from_gesture = True
    _axis_dbg(f"Arc click {axis}")
    on_axis(axis)


def draw_center_dot(sc: Any) -> None:
    """뷰큐브 중심점 (픽 없음)."""
    sc.Arc(
        0.04,
        begin=0.0,
        end=math.pi * 2,
        thickness=6,
        color=COL_CENTER,
        sector=True,
        intersection_thickness=0,
    )


def draw_axis_arms(
    sc: Any,
    on_axis_click: Callable[[str], None],
    cube_matrix: Optional[Gf.Matrix4d] = None,
) -> None:
    """X/Y/Z ±축 — 깊이 순서로 정렬 후 원(Arc) 자체에 ClickGesture.

    카메라에서 멀리 있는 축(음수축) 먼저 그리고, 가까운 축(양수축) 나중에 그려
    앞쪽 원이 위에 렌더링된다.  cube_matrix 가 주어지면 Z 깊이를 계산해 정렬.
    """
    import omni.ui as ui  # type: ignore

    origin = (0.0, 0.0, 0.0)

    # axis → (length, color, label_text, line_thickness, arc_thickness, sector, hit)
    axis_defs: dict = {
        "+x": (AXIS_LEN,     COL_X,  "x",  4, 10, True,  AXIS_HIT),
        "-x": (AXIS_NEG_LEN, COL_BX, "",   2,  3, False, AXIS_HIT),
        "+y": (AXIS_LEN,     COL_Y,  "y",  4, 10, True,  AXIS_HIT),
        "-y": (AXIS_NEG_LEN, COL_BY, "",   2,  3, False, AXIS_HIT),
        "+z": (AXIS_LEN,     COL_Z,  "z",  4, 10, True,  AXIS_HIT),
        "-z": (AXIS_NEG_LEN, COL_BZ, "",   2,  3, False, AXIS_HIT),
    }

    # 뷰 공간 Z 깊이 계산 (카메라 방향 = +Z in view)
    def _depth(axis: str) -> float:
        if cube_matrix is None:
            return 0.0
        v = Gf.Vec4d(*axis_vec(axis, 1.0), 0.0)
        tv = cube_matrix.Transform(Gf.Vec3d(*axis_vec(axis, 1.0)))
        # 카메라는 -Z 방향을 바라봄 → tv.z 가 클수록 카메라에 가까움
        return float(tv[2])

    # 깊이 내림차순 정렬 → 뒤쪽부터 그려야 앞쪽이 위에 덮임
    ordered = sorted(axis_defs.keys(), key=_depth)

    def _make_arc(sc: Any, axis_name: str, r: float, at: int, col_draw: tuple, hit: int) -> None:
        """클로저 격리: axis_name 이 루프 변수에 묶이지 않도록 별도 함수로 분리."""
        def _tap(_sender=None):
            _axis_dbg(f"ClickGesture {axis_name}")
            on_axis_click(axis_name)
        sc.Arc(
            r,
            begin=0.0, end=math.pi * 2,
            thickness=at, color=col_draw,
            wireframe=False, sector=True,
            intersection_thickness=hit,
            gesture=sc.ClickGesture(on_ended_fn=_tap),
        )

    for axis in ordered:
        length, col, label, lt, at, sector, hit = axis_defs[axis]
        tip = axis_vec(axis, length)
        sc.Line(origin, tip, color=col, thickness=lt, intersection_thickness=0)
        r = BULB_RADIUS if sector else RING_RADIUS
        col_draw = col if sector else (col[0], col[1], col[2], 0.30)

        with sc.Transform(
            transform=sc.Matrix44.get_translation_matrix(*tip),
            look_at=sc.Transform.LookAt.CAMERA,
        ):
            _make_arc(sc, axis, r, at, col_draw, hit)
            if label:
                with sc.Transform(scale_to=sc.Space.SCREEN):
                    sc.Label(label, size=LABEL_SIZE, color=(1, 1, 1, 1), alignment=ui.Alignment.CENTER)


def build_view_cube_scene(
    sc: Any,
    *,
    on_axis: Callable[[str], None],
    on_press: Callable[[], None],
    on_drag: Callable[[float, float], None],
    on_drag_end: Callable[[float, float], None],
    on_wheel: Callable[[float], None],
    ctrl: Optional["OrbitGizmoController"] = None,
) -> Tuple[Any, Any, Any, ViewCubeWidget]:
    """SceneView + 축. 축 클릭=Arc ClickGesture, 드래그=SceneView 마우스."""
    import omni.ui as ui  # type: ignore

    cube = ViewCubeWidget()
    stack = ui.ZStack(height=CUBE_HEIGHT)
    root_tf = None
    drag_arc = None
    with stack:
        sv = sc.SceneView(
            aspect_ratio_policy=sc.AspectRatioPolicy.PRESERVE_ASPECT_FIT,
            height=ui.Percent(100),
        )
        with sv.scene:
            drag_arc = sc.Arc(
                DRAG_ARC_R,
                begin=0.0,
                end=0.05,
                thickness=3,
                color=(1, 1, 1, 0.55),
                wireframe=True,
                sector=False,
                visible=False,
                intersection_thickness=0,
            )
            root_tf = sc.Transform()
            with root_tf:
                draw_center_dot(sc)
                draw_axis_arms(
                    sc,
                    on_axis,
                    cube_matrix=ctrl.view_cube_matrix if ctrl else None,
                )
        wire_cube_scene_input(
            sv,
            ctrl,
            on_axis=on_axis,
            on_press=on_press,
            on_drag=on_drag,
            on_drag_end=on_drag_end,
            on_wheel_zoom=on_wheel,
        )
        cube.bind_scene(root_tf, drag_arc, sv)
    return stack, root_tf, drag_arc, cube


class ViewCubeWidget:
    """뷰큐브 위젯 — 회전 행렬·드래그 arc 갱신."""

    def __init__(self) -> None:
        self._root_tf: Any = None
        self._drag_arc: Any = None
        self._scene_view: Any = None
        self._cube_matrix = Gf.Matrix4d(1.0)
        self._dragging = False
        self._ddx = self._ddy = 0.0

    def bind_scene(self, root_tf: Any, drag_arc: Any, scene_view: Any) -> None:
        self._root_tf = root_tf
        self._drag_arc = drag_arc
        self._scene_view = scene_view

    def set_matrix(self, matrix: Gf.Matrix4d, *, ddx=0.0, ddy=0.0, dragging=False) -> None:
        self._cube_matrix = matrix
        if self._root_tf:
            try:
                self._root_tf.transform = to_scene_matrix(matrix)
            except Exception:
                pass
        self._dragging, self._ddx, self._ddy = dragging, ddx, ddy
        self._update_drag_arc(ddx, ddy, dragging)

    def scene_size(self) -> Tuple[float, float]:
        try:
            w = float(self._scene_view.computed_width)
            h = float(self._scene_view.computed_height)
            if w > 1.0 and h > 1.0:
                return w, h
        except Exception:
            pass
        return float(PANEL_WIDTH), float(CUBE_HEIGHT)

    def clear_drag(self) -> None:
        self._dragging = False
        self._ddx = self._ddy = 0.0
        self._update_drag_arc(0.0, 0.0, False)

    def _update_drag_arc(self, ddx: float, ddy: float, dragging: bool) -> None:
        """드래그 방향 표시 arc — 항상 숨김 (UI 단순화)."""
        arc = self._drag_arc
        if not arc:
            return
        try:
            arc.visible = False
        except Exception:
            pass


# =============================================================================
# 패널 UI (panel_ui)
# =============================================================================
# 수정 가이드: 패널 전체 조립 → build_gizmo_panel
#              Cam/Persp 버튼 행 → build_mode_button_row
#              줌/Frame 버튼 행 → build_zoom_button_row
#              뒤 prim 클릭 차단 → wire_panel_input_blocker

def wire_viewport_input_guard(widget: Any, ctrl: "OrbitGizmoController") -> None:
    """위젯 hover/press 시 패널 입력 차단 상태 갱신."""
    if not ctrl.api:
        return

    def hovered(is_hovered, *_):
        ctrl.panel_hovered = bool(is_hovered)
        sync_panel_viewport_input_block(ctrl)
        return True

    try:
        widget.set_mouse_hovered_fn(hovered)
    except Exception:
        pass


def wire_cube_scene_input(
    widget: Any,
    ctrl: Optional["OrbitGizmoController"],
    *,
    on_axis: Callable[[str], None],
    on_press: Callable[[], None],
    on_drag: Callable[[float, float], None],
    on_drag_end: Callable[[float, float], None],
    on_wheel_zoom: Optional[Callable[[float], None]] = None,
) -> None:
    """드래그는 이동 {AXIS_DRAG_BLOCK_PX}px 이후만. 축=Arc ClickGesture + release 폴백 픽."""
    state = {
        "on": False,
        "lx": 0.0,
        "ly": 0.0,
        "press_x": 0.0,
        "press_y": 0.0,
        "dragging": False,
    }

    def _size() -> Tuple[float, float]:
        try:
            w = float(widget.computed_width)
            h = float(widget.computed_height)
            if w > 1.0 and h > 1.0:
                return w, h
        except Exception:
            pass
        return float(PANEL_WIDTH), float(CUBE_HEIGHT)

    if ctrl:
        wire_viewport_input_guard(widget, ctrl)

    def press(x, y, *_):
        state["on"] = True
        state["dragging"] = False
        lx, ly = _widget_local_xy(widget, float(x), float(y))
        state["lx"] = state["press_x"] = lx
        state["ly"] = state["press_y"] = ly
        if ctrl:
            ctrl.panel_pointer_down = True
            ctrl.axis_click_from_gesture = False
            sync_panel_viewport_input_block(ctrl)
        on_press()
        return False

    def release(x, y, *_):
        state["on"] = False
        lx, ly = _widget_local_xy(widget, float(x), float(y))
        was_drag = state["dragging"] or bool(ctrl and ctrl.drag_accum >= AXIS_DRAG_BLOCK_PX)

        def _try_fallback_pick() -> None:
            if not ctrl or was_drag or ctrl.axis_click_from_gesture:
                return
            w, h = _size()
            pick, info = try_pick_axis_at_screen(ctrl.view_cube_matrix, lx, ly, w, h)
            if pick:
                _axis_dbg(f"pick fallback {pick} | {info}")
                on_axis(pick)
            else:
                _axis_dbg(f"no hit | {info}")

        try:
            import omni.kit.app  # type: ignore
            omni.kit.app.get_app().post_update(lambda *_a: _try_fallback_pick())
        except Exception:
            _try_fallback_pick()

        on_drag_end(lx, ly)
        if ctrl:
            ctrl.panel_pointer_down = False
            sync_panel_viewport_input_block(ctrl)
        state["dragging"] = False
        return False

    def move(x, y, *_):
        if not state["on"]:
            return False
        lx, ly = _widget_local_xy(widget, float(x), float(y))
        total = math.hypot(lx - state["press_x"], ly - state["press_y"])
        if not state["dragging"]:
            if total < AXIS_DRAG_BLOCK_PX:
                return False
            state["dragging"] = True
            state["lx"], state["ly"] = lx, ly
            return False
        dx, dy = lx - state["lx"], ly - state["ly"]
        state["lx"], state["ly"] = lx, ly
        if abs(dx) > 1e-6 or abs(dy) > 1e-6:
            on_drag(dx, dy)
        return False

    widget.set_mouse_pressed_fn(press)
    widget.set_mouse_released_fn(release)
    widget.set_mouse_moved_fn(move)
    if on_wheel_zoom:
        def wheel(step, *_):
            if step > 0:
                on_wheel_zoom(1.0 / ZOOM_STEP)
            elif step < 0:
                on_wheel_zoom(ZOOM_STEP)
            return True

        try:
            widget.set_mouse_wheel_fn(wheel)
        except Exception:
            pass


def wire_cube_wheel(
    widget: Any,
    ctrl: Optional["OrbitGizmoController"],
    on_wheel_zoom: Callable[[float], None],
) -> None:
    """하위 호환 — wire_cube_scene_input 사용 권장."""
    wire_cube_scene_input(
        widget, ctrl,
        on_axis=lambda _a: None,
        on_press=lambda: None,
        on_drag=lambda _dx, _dy: None,
        on_drag_end=lambda _x, _y: None,
        on_wheel_zoom=on_wheel_zoom,
    )


def wire_panel_input_blocker(
    widget: Any,
    ctrl: "OrbitGizmoController",
    *,
    on_press: Optional[Callable[[], None]] = None,
    on_drag: Callable[[float, float], None],
    on_drag_end: Callable[[float, float], None],
    on_wheel_zoom: Callable[[float], None],
) -> None:
    """패널 위 마우스 이벤트를 소비해 뷰포트 prim 선택을 막는다."""
    drag = {"on": False, "lx": 0.0, "ly": 0.0}
    wire_viewport_input_guard(widget, ctrl)

    def press(x, y, *_):
        drag["on"], drag["lx"], drag["ly"] = True, float(x), float(y)
        ctrl.panel_pointer_down = True
        sync_panel_viewport_input_block(ctrl)
        if on_press:
            on_press()
        return True

    def release(x, y, *_):
        drag["on"] = False
        on_drag_end(float(x), float(y))
        ctrl.panel_pointer_down = False
        sync_panel_viewport_input_block(ctrl)
        return True

    def move(x, y, *_):
        if drag["on"]:
            dx, dy = float(x) - drag["lx"], float(y) - drag["ly"]
            drag["lx"], drag["ly"] = float(x), float(y)
            if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                on_drag(dx, dy)
        return True

    def wheel(step, *_):
        if step > 0:
            on_wheel_zoom(1.0 / ZOOM_STEP)
        elif step < 0:
            on_wheel_zoom(ZOOM_STEP)
        return True

    widget.set_mouse_pressed_fn(press)
    widget.set_mouse_released_fn(release)
    widget.set_mouse_moved_fn(move)
    try:
        widget.set_mouse_wheel_fn(wheel)
    except Exception:
        pass


def build_mode_button_row(
    ui: Any,
    *,
    on_cam: Callable[[], None],
    on_persp: Callable[[], None],
    ctrl: Optional["OrbitGizmoController"] = None,
) -> Tuple[Any, Any]:
    """상단 Cam / Persp 모드 버튼 행. ``(mode_label, row_widget)`` 반환."""
    with ui.HStack(height=30) as row:
        ui.Spacer(width=8)
        mode_lbl = ui.Label("Camera", style={"color": 0xFFCCCCCC, "font_size": 13})
        ui.Spacer()
        cam_btn = ui.Button("Cam", width=42, height=27, clicked_fn=on_cam, tooltip="Camera prim look-through")
        persp_btn = ui.Button("Persp", width=54, height=27, clicked_fn=on_persp, tooltip="씬에서 Camera prim 확인")
        ui.Spacer(width=6)
    if ctrl:
        for w in (row, mode_lbl, cam_btn, persp_btn):
            wire_viewport_input_guard(w, ctrl)
    return mode_lbl, row


def build_zoom_button_row(
    ui: Any,
    *,
    on_zoom_out: Callable[[], None],
    on_zoom_in: Callable[[], None],
    on_frame: Callable[[], None],
    ctrl: Optional["OrbitGizmoController"] = None,
) -> Any:
    """하단 - / + / Frame 버튼 행. 행 위젯 반환."""
    with ui.HStack(height=33, spacing=4) as row:
        ui.Spacer(width=8)
        zoom_out = ui.Button("-", width=36, height=30, clicked_fn=on_zoom_out, tooltip="줌 아웃")
        zoom_in = ui.Button("+", width=36, height=30, clicked_fn=on_zoom_in, tooltip="줌 인")
        ui.Spacer()
        frame_btn = ui.Button("Frame", width=66, height=30, clicked_fn=on_frame, tooltip="타깃 prim 중심으로 맞춤")
        ui.Spacer(width=8)
    if ctrl:
        for w in (row, zoom_out, zoom_in, frame_btn):
            wire_viewport_input_guard(w, ctrl)
    return row


def build_gizmo_panel(
    *,
    on_mode_cam: Callable[[], None],
    on_mode_persp: Callable[[], None],
    on_press: Optional[Callable[[], None]] = None,
    on_drag: Callable[[float, float], None],
    on_drag_end: Callable[[float, float], None],
    on_axis: Callable[[str], None],
    on_zoom: Callable[[float], None],
    on_frame: Callable[[], None],
    cube_out: List[Optional[ViewCubeWidget]],
    ctrl: Optional["OrbitGizmoController"] = None,
) -> tuple[Any, Any]:
    """조작 패널 루트 위젯 + 모드 라벨 반환 ``(root, mode_label)``."""
    import omni.ui as ui  # type: ignore
    import omni.ui.scene as sc  # type: ignore

    def wire(w: Any) -> None:
        if not ctrl:
            return
        wire_panel_input_blocker(
            w,
            ctrl,
            on_press=on_press,
            on_drag=on_drag,
            on_drag_end=on_drag_end,
            on_wheel_zoom=on_zoom,
        )

    root = ui.ZStack(width=PANEL_WIDTH, height=PANEL_HEIGHT)
    mode_lbl = None  # 버튼 행 숨김 — 라벨 미사용
    with root:
        bg = ui.Rectangle(style={"background_color": PANEL_BG, "border_radius": PANEL_RADIUS})
        wire(bg)
        # 상단 Cam/Persp 행 · 하단 -/+/Frame 행 숨김: SceneView 만 표시
        _, _root_tf, _drag_arc, cube = build_view_cube_scene(
            sc,
            on_axis=lambda a: _wire_axis_click(ctrl, on_axis, a),
            on_press=on_press or (lambda: None),
            on_drag=on_drag,
            on_drag_end=on_drag_end,
            on_wheel=on_zoom,
            ctrl=ctrl,
        )
        cube_out.clear()
        cube_out.append(cube)
    wire(root)
    if ctrl:
        wire_viewport_input_guard(root, ctrl)
        ctrl.panel_root = root
    return root, mode_lbl


# =============================================================================
# 패널 마운트 (panel_mount)
# =============================================================================
# 수정 가이드: 패널 화면 위치/정렬 → mount_panel, PanelAnchor
#              frame 슬롯 이름 → FRAME_SLOT (config 섹션)

def clear_mount_slot(mount: Any, slot: str = FRAME_SLOT) -> None:
    try:
        import omni.ui as ui  # type: ignore
        with mount.get_frame(slot):
            ui.Spacer(height=0)
    except Exception:
        pass


def _parse_panel_anchor(anchor: PanelAnchor | str) -> PanelAnchor:
    if isinstance(anchor, PanelAnchor):
        return anchor
    raw = str(anchor or PANEL_ANCHOR).strip().lower().replace("-", "_")
    try:
        return PanelAnchor(raw)
    except ValueError:
        return PanelAnchor.RIGHT_CENTER


def mount_panel(
    mount: Any,
    build_panel_fn: Callable[[], Any],
    *,
    anchor: PanelAnchor | str = PANEL_ANCHOR,
    margin: Any = PANEL_MARGIN,
    insets: Optional[PanelInsets] = None,
) -> None:
    """Viewport frame 내 지정 앵커에 고정 크기 패널을 배치한다.

    ``mount`` 는 ``get_frame(slot)`` 을 지원하는 객체이면 어떤 것이든 사용 가능.
    dock 방식과 widget(viewport_api) 방식 모두 지원한다.
    ``insets`` 가 주어지면 top/left/right/bottom 각각 독립 여백 적용.
    ``margin`` 은 PanelInsets 생성 편의 인자 (int 또는 PanelInsets).
    """
    import omni.ui as ui  # type: ignore

    anchor = _parse_panel_anchor(anchor)

    # 여백 통합 — insets 우선, margin 은 fallback
    if insets is None:
        insets = PanelInsets.from_value(margin)
    else:
        insets = PanelInsets.from_value(insets)

    h_side = anchor.value.split("_", 1)[0]   # left / center / right
    v_side = anchor.value.rsplit("_", 1)[-1]  # top  / center / bottom

    # PanelInsets 는 앵커 기준 추가 오프셋:
    #   top    → 앵커 위치에서 아래로 N px 더 이동  (top/center 앵커)
    #   bottom → 앵커 위치에서 위로  N px 더 이동  (bottom/center 앵커)
    #   left   → 앵커 위치에서 오른쪽으로 N px 더 이동  (left/center 앵커)
    #   right  → 앵커 위치에서 왼쪽으로  N px 더 이동  (right/center 앵커)

    clear_mount_slot(mount)
    with mount.get_frame(FRAME_SLOT):
        with ui.ZStack(width=ui.Percent(100), height=ui.Percent(100)):
            with ui.VStack(width=ui.Percent(100), height=ui.Percent(100)):

                # ── 위쪽 spacer ────────────────────────────────────────────
                if v_side == "top":
                    # 상단에서 아래로: top 오프셋만큼 고정 여백
                    ui.Spacer(height=insets.top)
                elif v_side == "center":
                    # 중앙에서: top 오프셋이면 아래로 편중, bottom 오프셋이면 위로 편중
                    # flexible + 고정 오프셋으로 구현
                    ui.Spacer()   # 위쪽 flexible (중앙 기준점)
                    if insets.top > 0:
                        ui.Spacer(height=insets.top)
                elif v_side == "bottom":
                    ui.Spacer()   # 위쪽 전부 flexible

                with ui.HStack(width=ui.Percent(100)):

                    # ── 왼쪽 spacer ────────────────────────────────────────
                    if h_side == "left":
                        ui.Spacer(width=insets.left)   # 좌측 기준 + 오른쪽 오프셋
                    elif h_side == "center":
                        ui.Spacer()
                        if insets.left > 0:
                            ui.Spacer(width=insets.left)
                    elif h_side == "right":
                        ui.Spacer()                    # 왼쪽 전부 flexible

                    build_panel_fn()

                    # ── 오른쪽 spacer ──────────────────────────────────────
                    if h_side == "right":
                        ui.Spacer(width=insets.right)  # 우측 기준 + 왼쪽 오프셋
                    elif h_side == "center":
                        if insets.right > 0:
                            ui.Spacer(width=insets.right)
                        ui.Spacer()
                    elif h_side == "left":
                        ui.Spacer()                    # 오른쪽 전부 flexible

                # ── 아래쪽 spacer ──────────────────────────────────────────
                if v_side == "bottom":
                    ui.Spacer(height=insets.bottom)    # 하단 기준 + 위쪽 오프셋
                elif v_side == "center":
                    if insets.bottom > 0:
                        ui.Spacer(height=insets.bottom)
                    ui.Spacer()   # 아래쪽 flexible (중앙 기준점)
                elif v_side == "top":
                    ui.Spacer()   # 아래쪽 전부 flexible


def mount_panel_right_center(mount: Any, build_panel_fn: Callable[[], Any]) -> None:
    """하위 호환 — ``mount_panel(..., anchor=right_center)``."""
    mount_panel(mount, build_panel_fn, anchor=PanelAnchor.RIGHT_CENTER, margin=PANEL_MARGIN)


# =============================================================================
# Orbit 연산 (orbit_ops)
# =============================================================================
# 수정 가이드: orbit 최초 생성 → init_orbit_state
#              타깃 prim 중심 갱신 → refresh_orbit_target
#              Camera prim 적용 → apply_orbit_to_camera
#              Cam 모드 줌 동기 → sync_orbit_from_viewport (read_camera_pose 사용)
#              뷰큐브 회전 갱신 → refresh_view_cube

def init_orbit_state(ctrl: OrbitGizmoController) -> None:
    """타깃 prim 기준으로 orbit 상태를 처음 만든다."""
    stage = get_stage(ctrl.ctx)
    ctrl.target_resolved = resolve_target_path(stage, ctrl.target_path) if stage else ctrl.target_path
    center = prim_center(stage, ctrl.target_path) if stage else None
    if center is None:
        center = (0.0, 0.0, 0.0)
        print(f"{_PRINT_PREFIX} target not found yet: {ctrl.target_path!r}", flush=True)
    r = prim_radius(stage, ctrl.target_path) if stage else 1.0
    d = max(MIN_DIST, min(MAX_DIST, r * FRAME_DIST_SCALE))
    ctrl.orbit = OrbitState(center, d, FRAME_YAW, FRAME_PITCH)


def refresh_orbit_target(ctrl: OrbitGizmoController) -> bool:
    """타깃 prim 월드 중심으로 orbit.target 을 갱신한다."""
    stage = get_stage(ctrl.ctx)
    if not stage:
        return False
    resolved = resolve_target_path(stage, ctrl.target_path)
    prim = stage.GetPrimAtPath(resolved)
    if not prim or not prim.IsValid():
        return False
    ctrl.target_resolved = resolved
    center = prim_center(stage, ctrl.target_path)
    if not center:
        return False
    if not ctrl.orbit:
        init_orbit_state(ctrl)
    if not ctrl.orbit:
        return False
    ctrl.orbit = OrbitState(
        center, ctrl.orbit.distance, ctrl.orbit.yaw_deg, ctrl.orbit.pitch_deg, ctrl.orbit.up,
    )
    return True


def apply_orbit_to_camera(ctrl: OrbitGizmoController) -> None:
    """orbit → Camera prim. Cam/Persp 모두 Camera prim 만 이동."""
    if not ctrl.orbit:
        return
    eye, tgt = ctrl.orbit.eye(), ctrl.orbit.target
    ensure_camera_pose(ctrl.camera_path, eye, tgt, ctrl.orbit.up, ctrl.ctx)


def sync_orbit_from_viewport(ctrl: OrbitGizmoController) -> None:
    """Cam 모드: 뷰포트 네이티브 줌/회전 → orbit distance·각도 반영."""
    if ctrl.sync_skip_frames > 0:
        ctrl.sync_skip_frames -= 1
        return
    if ctrl.mode == ViewMode.PERSPECTIVE:
        return
    if ctrl.panel_drag or ctrl.anim_sub or not ctrl.orbit:
        return
    tgt = ctrl.orbit.target
    pair = read_camera_pose(ctrl.camera_path, ctrl.ctx)
    if not pair:
        return
    st = orbit_from_eye(pair[0], tgt, ctrl.orbit.up, prev_yaw=ctrl.orbit.yaw_deg)
    if (abs(st.distance - ctrl.orbit.distance) < SYNC_EPS
            and abs(st.yaw_deg - ctrl.orbit.yaw_deg) < 0.05
            and abs(st.pitch_deg - ctrl.orbit.pitch_deg) < 0.05):
        return
    ctrl.orbit = OrbitState(tgt, st.distance, st.yaw_deg, st.pitch_deg, ctrl.orbit.up)


def schedule_target_retry(ctrl: OrbitGizmoController, frames: int) -> None:
    """stage 로드 직후 prim 이 늦게 올라오면 orbit 타깃 재시도."""

    def tick(n: int) -> None:
        if refresh_orbit_target(ctrl):
            apply_orbit_to_camera(ctrl)
            refresh_view_cube(ctrl)
            print(f"{_PRINT_PREFIX} target={ctrl.target_resolved!r} center={ctrl.orbit.target}", flush=True)
        elif n > 0:
            try:
                import omni.kit.app  # type: ignore
                omni.kit.app.get_app().post_update(lambda: tick(n - 1))
            except Exception:
                pass

    tick(max(0, int(frames)))


def defer_apply_orbit(ctrl: OrbitGizmoController, *, frames: int = 1) -> None:
    """look-through 직후 1프레임 뒤 Camera pose 재적용."""

    def tick(n: int) -> None:
        if n <= 0:
            refresh_orbit_target(ctrl)
            apply_orbit_to_camera(ctrl)
            refresh_view_cube(ctrl)
            return
        try:
            import omni.kit.app  # type: ignore
            omni.kit.app.get_app().post_update(lambda: tick(n - 1))
        except Exception:
            tick(0)

    ctrl.sync_skip_frames = max(ctrl.sync_skip_frames, int(frames) + 1)
    tick(max(0, int(frames)))


def refresh_view_cube(ctrl: OrbitGizmoController) -> None:
    """뷰큐브 회전 행렬을 현재 orbit(또는 애니 중간 eye)에 맞춘다."""
    if not ctrl.cube or not ctrl.orbit:
        return
    eye = ctrl.orbit.eye()
    if ctrl.anim_dest and ctrl.anim_sub:
        u = smoothstep((time.perf_counter() - ctrl.anim_t0) / max(1e-3, ANIM_SEC))
        u = min(1.0, u)
        eye = lerp3(ctrl.anim_from_eye, ctrl.anim_to_eye, u)
    cube_m = view_cube_matrix(eye, ctrl.orbit.target, ctrl.orbit.up)
    ctrl.view_cube_matrix = cube_m
    ctrl.cube.set_matrix(cube_m, ddx=ctrl.ddx, ddy=ctrl.ddy, dragging=ctrl.panel_drag)


# =============================================================================
# 모드 전환 (mode_switch)
# =============================================================================
# 수정 가이드: Cam 모드 진입 → switch_to_camera_mode
#              Persp 모드 진입 → switch_to_perspective_mode
#              모드 라벨 갱신 → update_mode_label

def switch_to_camera_mode(ctrl: OrbitGizmoController, *, silent: bool = False) -> None:
    """Viewport 를 ``/Camera`` prim 으로 look-through."""
    refresh_orbit_target(ctrl)
    pair = read_viewport_pose(ctrl.api)
    if pair:
        ctrl.saved_persp = ViewSnap(pair[0], pair[1])
    ctrl.mode = ViewMode.CAMERA
    look_through_camera(ctrl.api, ctrl.camera_path)
    defer_apply_orbit(ctrl, frames=1)
    update_mode_label(ctrl)
    refresh_view_cube(ctrl)
    if not silent:
        print(f"{_PRINT_PREFIX} mode → Camera", flush=True)


def switch_to_perspective_mode(ctrl: OrbitGizmoController, *, silent: bool = False) -> None:
    """Viewport 를 Persp 로 전환. gizmo 조작은 Camera prim 만 이동."""
    refresh_orbit_target(ctrl)
    if ctrl.orbit:
        pair = read_camera_pose(ctrl.camera_path, ctrl.ctx)
        if pair:
            st = orbit_from_eye(pair[0], ctrl.orbit.target, ctrl.orbit.up, prev_yaw=ctrl.orbit.yaw_deg)
            ctrl.orbit = OrbitState(
                ctrl.orbit.target, st.distance, st.yaw_deg, st.pitch_deg, ctrl.orbit.up,
            )
    ctrl.mode = ViewMode.PERSPECTIVE
    look_through_camera(ctrl.api, KIT_PERSP_PATH)
    defer_apply_orbit(ctrl, frames=1)
    update_mode_label(ctrl)
    refresh_view_cube(ctrl)
    if not silent:
        print(f"{_PRINT_PREFIX} mode → Persp", flush=True)


def apply_view_mode(ctrl: OrbitGizmoController, mode: ViewMode, *, silent: bool = False, force: bool = False) -> None:
    """Cam/Persp 전환 — ``force=True`` 면 동일 모드여도 즉시 재적용."""
    if mode == ViewMode.CAMERA:
        switch_to_camera_mode(ctrl, silent=silent)
    else:
        switch_to_perspective_mode(ctrl, silent=silent)


def update_mode_label(ctrl: OrbitGizmoController) -> None:
    if not ctrl.mode_label:
        return
    lbl = "Camera" if ctrl.mode == ViewMode.CAMERA else "Persp"
    try:
        ctrl.mode_label.text = lbl
    except Exception:
        pass


# =============================================================================
# 입력 핸들러 (handlers)
# =============================================================================
# 수정 가이드: 패널 드래그 회전 → handle_panel_drag, handle_panel_drag_end
#              X/Y/Z 축 클릭 → handle_axis_click
#              +/- 줌 → handle_zoom
#              Frame 버튼 → handle_frame

def handle_panel_press(ctrl: OrbitGizmoController) -> None:
    """드래그 시작."""
    ctrl.drag_accum = 0.0
    ctrl.panel_pointer_down = True
    sync_panel_viewport_input_block(ctrl)


def handle_panel_drag(ctrl: OrbitGizmoController, dx: float, dy: float) -> None:
    """패널 드래그 → orbit yaw/pitch 변경 → Camera 적용."""
    if not ctrl.orbit:
        return
    stop_orbit_animation(ctrl)
    ctrl.panel_drag = True
    ctrl.ddx, ctrl.ddy = dx, dy
    ctrl.drag_accum += math.hypot(dx, dy)
    ctrl.orbit = ctrl.orbit.with_angles(
        ctrl.orbit.yaw_deg - dx * DRAG_SENS,
        clamp_pitch(ctrl.orbit.pitch_deg - dy * DRAG_SENS, for_drag=True),
    )
    apply_orbit_to_camera(ctrl)
    refresh_view_cube(ctrl)


def handle_axis_click(ctrl: OrbitGizmoController, axis: str) -> None:
    """뷰큐브 축 클릭 → 해당 방향으로 fly."""
    if not ctrl.orbit:
        _axis_dbg(f"FLY_SKIP {axis} (no orbit)")
        return
    stop_orbit_animation(ctrl)
    yaw, pitch = AXIS_SNAP.get(axis.lower(), (0.0, 20.0))
    _axis_dbg(
        f"FLY {axis} yaw={yaw:.1f} pitch={pitch:.1f} "
        f"(from yaw={ctrl.orbit.yaw_deg:.1f} pitch={ctrl.orbit.pitch_deg:.1f})"
    )
    ctrl.orbit = ctrl.orbit.with_angles(yaw, pitch)
    animate_orbit_to(ctrl, ctrl.orbit)


def handle_panel_drag_end(ctrl: OrbitGizmoController, px: float, py: float) -> None:
    """드래그 종료 — arc 표시 제거."""
    ctrl.panel_drag = False
    ctrl.panel_pointer_down = False
    ctrl.ddx = ctrl.ddy = 0.0
    ctrl.drag_accum = 0.0
    if ctrl.cube:
        ctrl.cube.clear_drag()
    refresh_view_cube(ctrl)
    sync_panel_viewport_input_block(ctrl)


def handle_zoom(ctrl: OrbitGizmoController, factor: float) -> None:
    """``factor>1`` 줌아웃, ``<1`` 줌인. distance 유지가 핵심."""
    if not ctrl.orbit:
        return
    stop_orbit_animation(ctrl)
    sync_orbit_from_viewport(ctrl)
    d = max(MIN_DIST, min(MAX_DIST, ctrl.orbit.distance * factor))
    ctrl.orbit = ctrl.orbit.with_distance(d)
    apply_orbit_to_camera(ctrl)
    refresh_view_cube(ctrl)


def handle_frame(ctrl: OrbitGizmoController, *, smooth: bool = True) -> None:
    """타깃 prim 중심 + 기본 각도/거리로 맞춤."""
    if not refresh_orbit_target(ctrl):
        stage = get_stage(ctrl.ctx)
        center = prim_center(stage, ctrl.target_path) if stage else (0.0, 0.0, 0.0)
        if center and ctrl.orbit:
            ctrl.orbit = OrbitState(
                center, ctrl.orbit.distance, ctrl.orbit.yaw_deg, ctrl.orbit.pitch_deg, ctrl.orbit.up,
            )
    if not ctrl.orbit:
        return
    stage = get_stage(ctrl.ctx)
    r = prim_radius(stage, ctrl.target_path) if stage else 1.0
    d = max(MIN_DIST, min(MAX_DIST, r * FRAME_DIST_SCALE))
    ctrl.orbit = OrbitState(ctrl.orbit.target, d, FRAME_YAW, FRAME_PITCH, ctrl.orbit.up)
    if smooth:
        animate_orbit_to(ctrl, ctrl.orbit)
    else:
        apply_orbit_to_camera(ctrl)
        refresh_view_cube(ctrl)


# =============================================================================
# 애니메이션 (animation)
# =============================================================================
# 수정 가이드: fly 시작 → animate_orbit_to
#              fly 중단 → stop_orbit_animation
#              프레임 보간 → tick_orbit_animation

def stop_orbit_animation(ctrl: OrbitGizmoController) -> None:
    if ctrl.anim_sub:
        try:
            ctrl.anim_sub.unsubscribe()
        except Exception:
            pass
    ctrl.anim_sub = None


def animate_orbit_to(ctrl: OrbitGizmoController, dest: OrbitState) -> None:
    """Camera prim 을 부드럽게 목표 orbit pose 로 이동."""
    pair = read_camera_pose(ctrl.camera_path, ctrl.ctx)
    if not pair or not ctrl.orbit:
        apply_orbit_to_camera(ctrl)
        refresh_view_cube(ctrl)
        return
    ctrl.anim_from_eye = pair[0]
    ctrl.anim_to_eye = dest.eye()
    ctrl.anim_tgt = dest.target
    ctrl.anim_up = dest.up
    ctrl.anim_dest = dest
    ctrl.anim_yaw0, ctrl.anim_pitch0 = ctrl.orbit.yaw_deg, ctrl.orbit.pitch_deg
    ctrl.panel_drag = False
    ctrl.anim_t0 = time.perf_counter()
    stop_orbit_animation(ctrl)
    try:
        import omni.kit.app  # type: ignore
        app = omni.kit.app.get_app()
        if not app:
            tick_orbit_animation(ctrl, 1.0)
            return
        ctrl.anim_sub = app.get_update_event_stream().create_subscription_to_pop(
            lambda *_: _on_anim_tick(ctrl), name="tbs.orbit_gizmo.anim",
        )
    except Exception:
        tick_orbit_animation(ctrl, 1.0)


def _on_anim_tick(ctrl: OrbitGizmoController) -> None:
    t = (time.perf_counter() - ctrl.anim_t0) / max(1e-3, ANIM_SEC)
    if t >= 1.0:
        tick_orbit_animation(ctrl, 1.0)
        stop_orbit_animation(ctrl)
        return
    tick_orbit_animation(ctrl, t)


def tick_orbit_animation(ctrl: OrbitGizmoController, t: float) -> None:
    u = smoothstep(t)
    eye = lerp3(ctrl.anim_from_eye, ctrl.anim_to_eye, u)
    write_camera_pose(ctrl.camera_path, eye, ctrl.anim_tgt, ctrl.anim_up, ctrl.ctx)
    refresh_view_cube(ctrl)
    if u >= 1.0 - 1e-9 and ctrl.anim_dest:
        ctrl.orbit = ctrl.anim_dest
        ctrl.anim_dest = None
        refresh_view_cube(ctrl)


# =============================================================================
# 컨트롤러 (controller)
# =============================================================================
# 수정 가이드: 생명주기/마운트 → OrbitGizmoController.destroy, sync_mount
#              패널 콜백 연결 → _build_panel, _set_mode
#              폴링 루프 → _start_poll

class OrbitGizmoController:
    """상태 보관 + 콜백 라우팅. 로직은 위 모듈 수준 함수에 위임."""

    def __init__(
        self,
        target: str,
        camera: str,
        *,
        panel_anchor: PanelAnchor | str = PANEL_ANCHOR,
        panel_margin: Any = PANEL_MARGIN,
        panel_insets: Optional[PanelInsets] = None,
        viewport_api: Any = None,
        ext: Any = None,           # 선택 — lifecycle 플래그 전파용
    ) -> None:
        self.ext = ext
        self.target_path = target.strip()
        self.camera_path = camera.strip()
        self.panel_anchor = _parse_panel_anchor(panel_anchor)
        self.panel_insets: PanelInsets = (
            PanelInsets.from_value(panel_insets) if panel_insets is not None
            else PanelInsets.from_value(panel_margin)
        )
        self.mode = ViewMode.CAMERA if ORBIT_GIZMO_START_IN_CAMERA_MODE else ViewMode.PERSPECTIVE
        self.orbit: Optional[OrbitState] = None
        self.saved_persp: Optional[ViewSnap] = None
        self.api: Any = viewport_api
        self.ctx = ""
        self.mount: Any = None
        self.mode_label: Any = None
        self.cube: Optional[ViewCubeWidget] = None
        self.cube_holder: List[Optional[ViewCubeWidget]] = []
        self.sched = 0
        self.poll_sub: Any = None
        self.anim_sub: Any = None
        self.anim_t0 = 0.0
        self.anim_from_eye = self.anim_to_eye = (0.0, 0.0, 0.0)
        self.anim_tgt = (0.0, 0.0, 0.0)
        self.anim_up = (0.0, 0.0, 1.0)
        self.anim_dest: Optional[OrbitState] = None
        self.anim_yaw0 = self.anim_pitch0 = 0.0
        self.panel_drag = False
        self.ddx = self.ddy = 0.0
        self.drag_accum = 0.0
        self.axis_click_from_gesture = False
        self.panel_pointer_down = False
        self.panel_hovered = False
        self.panel_viewport_blocked = False
        self.panel_root: Any = None
        self.selection_disable_scope: Any = None
        self.target_resolved = ""
        self.sync_skip_frames = 0
        self.view_cube_matrix = Gf.Matrix4d(1.0)

    # --- 생명주기 ---

    def destroy(self) -> None:
        stop_orbit_animation(self)
        self._stop_poll()
        self.panel_root = None
        self.selection_disable_scope = None
        set_viewport_input_enabled(self.api, True)
        self.cube = None
        mount = self.mount or get_viewport_window()
        if mount:
            clear_mount_slot(mount)
        if self.ext is not None:
            try:
                self.ext._orbit_gizmo_mounted = False
            except Exception:
                pass

    def sync_mount(self, delay_frames: int = 12) -> None:
        self.sched += 1
        tok = self.sched

        def try_mount(n: int) -> None:
            if tok != self.sched:
                return
            if self.api is not None:
                # widget 방식: viewport_api 자체가 get_frame 을 가지면 직접 사용
                if callable(getattr(self.api, "get_frame", None)):
                    self._on_mount(self.api, tok)
                    return
                mount = get_viewport_window_from_api(self.api)
                if mount:
                    self._on_mount(mount, tok)
                    return
            w = get_viewport_window()
            if w:
                self._on_mount(w, tok)
            elif n > 0:
                try:
                    import omni.kit.app  # type: ignore
                    omni.kit.app.get_app().post_update(lambda: try_mount(n - 1))
                except Exception:
                    pass
            else:
                print(f"{_PRINT_PREFIX} Viewport unavailable", flush=True)

        try_mount(max(0, delay_frames))

    def _on_mount(self, mount: Any, tok: int) -> None:
        if tok != self.sched:
            return
        self.mount = mount
        if self.api is None:
            self.api = get_viewport_api()
        self.ctx = get_context_name(self.api)
        self.saved_persp = None
        pair = read_viewport_pose(self.api)
        if pair:
            self.saved_persp = ViewSnap(pair[0], pair[1])
        init_orbit_state(self)
        if self.orbit:
            ensure_camera_pose(
                self.camera_path, self.orbit.eye(), self.orbit.target, self.orbit.up, self.ctx,
            )
        self._build_panel()
        self._start_poll()
        # attach 시점에 항상 Camera 모드로 강제 전환
        self.mode = ViewMode.CAMERA
        apply_view_mode(self, ViewMode.CAMERA, silent=False, force=True)
        # viewport 초기화 타이밍 문제로 look_through 가 묻히는 경우를 대비해
        # 몇 프레임 후 재적용
        self._schedule_camera_mode_retry(retries=5, interval=6)
        schedule_target_retry(self, 40)
        if self.ext is not None:
            try:
                self.ext._orbit_gizmo_mounted = True
            except Exception:
                pass
        print(f"{_PRINT_PREFIX} mounted target={self.target_path!r} camera={self.camera_path!r}", flush=True)

    def _build_panel(self) -> None:
        if not self.mount:
            return

        def _build() -> None:
            _, self.mode_label = build_gizmo_panel(
                on_mode_cam=lambda: self._set_mode(ViewMode.CAMERA, force=True),
                on_mode_persp=lambda: self._set_mode(ViewMode.PERSPECTIVE, force=True),
                on_press=lambda: handle_panel_press(self),
                on_drag=lambda dx, dy: handle_panel_drag(self, dx, dy),
                on_drag_end=lambda px, py: handle_panel_drag_end(self, px, py),
                on_axis=lambda axis: handle_axis_click(self, axis),
                on_zoom=lambda f: handle_zoom(self, f),
                on_frame=lambda: handle_frame(self, smooth=True),
                cube_out=self.cube_holder,
                ctrl=self,
            )

        mount_panel(
            self.mount,
            _build,
            anchor=self.panel_anchor,
            insets=self.panel_insets,
        )
        self.cube = self.cube_holder[0] if self.cube_holder else None
        refresh_view_cube(self)

    def _start_poll(self) -> None:
        self._stop_poll()
        try:
            import omni.kit.app  # type: ignore
            self.poll_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
                lambda *_: (
                    sync_panel_viewport_input_block(self),
                    sync_orbit_from_viewport(self),
                    refresh_view_cube(self),
                ),
                name="tbs.orbit_gizmo.poll",
            )
        except Exception:
            pass

    def _stop_poll(self) -> None:
        if self.poll_sub:
            try:
                self.poll_sub.unsubscribe()
            except Exception:
                pass
        self.poll_sub = None

    def _schedule_camera_mode_retry(self, retries: int = 5, interval: int = 6) -> None:
        """viewport 초기화 경쟁 조건 대비: Camera 모드를 몇 프레임 후 재적용."""
        if retries <= 0:
            return
        count = [retries]

        def _retry(*_) -> None:
            count[0] -= 1
            if self.mode != ViewMode.CAMERA:
                return  # 사용자가 이미 다른 모드로 바꿨으면 중단
            try:
                look_through_camera(self.api, self.camera_path)
            except Exception:
                pass
            if count[0] > 0:
                try:
                    import omni.kit.app  # type: ignore
                    for _ in range(interval):
                        omni.kit.app.get_app().post_update(_retry)
                        break
                except Exception:
                    pass

        try:
            import omni.kit.app  # type: ignore
            for _ in range(interval):
                omni.kit.app.get_app().post_update(_retry)
                break
        except Exception:
            pass

    def _set_mode(self, mode: ViewMode, *, silent: bool = False, force: bool = False) -> None:
        if mode == self.mode and not force:
            return
        apply_view_mode(self, mode, silent=silent, force=force)


# =============================================================================
# 공개 API (public)
# =============================================================================
# 수정 가이드: 확장 attach/destroy → attach_orbit_gizmo, destroy_orbit_gizmo

# 모듈 레벨 컨트롤러 레지스트리 (ext 없이 호출할 때 사용)
_GLOBAL_CONTROLLERS: dict = {}


def _get_ctrl_registry(ext: Any) -> dict:
    """ext 가 있으면 ext._orbit_gizmo_controllers, 없으면 전역 dict."""
    if ext is not None:
        ctrls = getattr(ext, "_orbit_gizmo_controllers", None)
        if not isinstance(ctrls, dict):
            ctrls = {}
            try:
                ext._orbit_gizmo_controllers = ctrls
            except Exception:
                pass
        return ctrls
    return _GLOBAL_CONTROLLERS


def attach_orbit_gizmo(
    target_prim_path: str,
    camera_prim_path: str = _DEFAULT_CAMERA_PATH,
    viewport_api: Any = None,
    keep_existing: bool = False,
    *,
    ext: Any = None,
    delay_frames: int = 12,
    panel_anchor: PanelAnchor | str = PANEL_ANCHOR,
    panel_margin: Any = PANEL_MARGIN,
    panel_insets: Optional[PanelInsets] = None,
) -> None:
    """지정 viewport에 Blender 스타일 뷰큐브 패널을 붙인다.

    ext 없이 독립 호출 가능.  ext 를 넘기면 lifecycle 플래그(_orbit_gizmo_mounted 등)
    를 ext 에 전파한다.

    - `viewport_api` 를 넘기지 않으면 기본 viewport 를 사용한다.
    - `keep_existing=False` 이면 다른 viewport 의 컨트롤러를 삭제하고 새로 1개만 만든다.
    - 동일 `viewport_api`(또는 default)면 `keep_existing` 과 무관하게 항상 재생성한다.
    """
    tgt = str(ORBIT_GIZMO_DEFAULT_TARGET_PRIM_PATH or target_prim_path or "").strip()
    cam = str(camera_prim_path or _DEFAULT_CAMERA_PATH).strip()
    if not tgt:
        print(f"{_PRINT_PREFIX} attach skipped — empty target path", flush=True)
        return
    if not cam:
        cam = _DEFAULT_CAMERA_PATH

    vp_key = "default" if viewport_api is None else id(viewport_api)
    ctrls = _get_ctrl_registry(ext)

    # 동일 viewport면 항상 재생성
    old = ctrls.get(vp_key)
    if old is not None:
        try:
            old.destroy()
        except Exception:
            pass
        ctrls.pop(vp_key, None)

    if not keep_existing:
        for k, v in list(ctrls.items()):
            if k == vp_key:
                continue
            try:
                v.destroy()
            except Exception:
                pass
            ctrls.pop(k, None)

    ctrl = OrbitGizmoController(
        tgt,
        cam,
        panel_anchor=panel_anchor,
        panel_margin=panel_margin,
        panel_insets=panel_insets,
        viewport_api=viewport_api,
        ext=ext,
    )
    ctrls[vp_key] = ctrl
    ctrl.sync_mount(delay_frames=delay_frames)


def destroy_orbit_gizmo(ext: Any = None) -> None:
    """orbit gizmo 를 모든 viewport 에서 제거한다.

    ext 없이 호출하면 전역 레지스트리를 정리한다.
    """
    ctrls = _get_ctrl_registry(ext)
    for _k, v in list(ctrls.items()):
        try:
            v.destroy()
        except Exception:
            pass
    try:
        ctrls.clear()
    except Exception:
        pass
    if ext is not None:
        # 레거시 필드도 같이 정리
        try:
            ext._orbit_gizmo_controller = None
            ext._orbit_gizmo_mounted = False
        except Exception:
            pass


__all__ = [
    "attach_orbit_gizmo",
    "destroy_orbit_gizmo",
    "mount_panel",
    "mount_panel_right_center",
    "PanelAnchor",
    "PanelInsets",
    "PANEL_ANCHOR",
    "PANEL_MARGIN",
]
