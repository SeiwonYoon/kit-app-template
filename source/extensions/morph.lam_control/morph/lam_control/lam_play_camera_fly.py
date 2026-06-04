"""CSV Play 시작 전 뷰포트 카메라 fly-to (설정: lam_viewport_overlay_config).

순서(Play, 일시정지 이어서 제외):
  1) 체크 ON + preset 설정됨 + 현재 뷰 ≠ preset → duration 동안 eye/target 보간
  2) prim 숨김(play_start) — 호출측(simulation_play)에서 fly 이후 실행
  3) CSV 재생

「뷰 저장」: 현재 뷰를 캡처해 콘솔에 config 붙여넣기용 로그 출력.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import omni.usd as ou  # type: ignore
from pxr import Gf, Sdf, Usd, UsdGeom  # type: ignore

_PRINT_PREFIX = "[LAM/PlayCameraFly]"
_COI_ATTR = "omni:kit:centerOfInterest"


@dataclass(frozen=True)
class CameraViewSnapshot:
    eye_xyz: Tuple[float, float, float]
    target_xyz: Tuple[float, float, float]


def _smoothstep01(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def _lerp3(
    a: Tuple[float, float, float],
    b: Tuple[float, float, float],
    u: float,
) -> Tuple[float, float, float]:
    return (
        float(a[0] + (b[0] - a[0]) * u),
        float(a[1] + (b[1] - a[1]) * u),
        float(a[2] + (b[2] - a[2]) * u),
    )


def _vec3(t: Tuple[float, float, float]) -> Gf.Vec3d:
    return Gf.Vec3d(float(t[0]), float(t[1]), float(t[2]))


def _resolve_usd_context_name() -> str:
    try:
        from omni.kit.viewport.utility import get_active_viewport  # type: ignore

        vp = get_active_viewport()
        if vp is not None:
            return str(getattr(vp, "usd_context_name", "") or "")
    except Exception:
        pass
    return ""


def _get_stage() -> Optional[Usd.Stage]:
    try:
        ctx = ou.get_context(_resolve_usd_context_name())
        if ctx is None:
            ctx = ou.get_context("")
        if ctx is None:
            return None
        return ctx.get_stage()
    except Exception:
        return None


def _active_camera_path_str() -> Optional[str]:
    try:
        from omni.kit.viewport.utility import get_active_viewport_camera_string  # type: ignore

        p = get_active_viewport_camera_string()
        return str(p) if p else None
    except Exception:
        return None


def _read_coi_local(cam_prim: Usd.Prim) -> Gf.Vec3d:
    attr = cam_prim.GetAttribute(_COI_ATTR)
    if attr and attr.IsValid():
        v = attr.Get(Usd.TimeCode.Default())
        if v is not None:
            return Gf.Vec3d(float(v[0]), float(v[1]), float(v[2]))
    return Gf.Vec3d(0.0, 0.0, -500.0)


def _snapshot_from_camera_prim() -> Optional[CameraViewSnapshot]:
    """Kit session 카메라 prim + COI — apply 와 동일 규칙."""
    stage = _get_stage()
    path = _active_camera_path_str()
    if not stage or not path:
        return None
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return None
    try:
        xfc = UsdGeom.XformCache(Usd.TimeCode.Default())
        world = xfc.GetLocalToWorldTransform(prim)
        eye = world.ExtractTranslation()
        coi = _read_coi_local(prim)
        dist = abs(float(coi[2]))
        if dist < 1e-6:
            dist = (Gf.Vec3d(0, 0, -1) * world).GetLength()
        if dist < 1e-6:
            dist = 500.0
        forward = world.TransformDir(Gf.Vec3d(0.0, 0.0, -1.0))
        if forward.GetLength() < 1e-9:
            forward = Gf.Vec3d(0.0, 0.0, -1.0)
        else:
            forward.Normalize()
        target = eye + forward * dist
        return CameraViewSnapshot(
            eye_xyz=(float(eye[0]), float(eye[1]), float(eye[2])),
            target_xyz=(float(target[0]), float(target[1]), float(target[2])),
        )
    except Exception:
        return None


def _snapshot_from_viewport_state() -> Optional[CameraViewSnapshot]:
    try:
        from omni.kit.viewport.utility.camera_state import ViewportCameraState  # type: ignore

        st = ViewportCameraState()
        eye = getattr(st, "position_world", None)
        if eye is None:
            return None
        ex = (float(eye[0]), float(eye[1]), float(eye[2]))
        tgt = None
        for name in (
            "target_world",
            "center_of_interest_world",
            "pivot_world",
            "interest_world",
        ):
            raw = getattr(st, name, None)
            if raw is not None:
                tgt = (float(raw[0]), float(raw[1]), float(raw[2]))
                break
        if tgt is None:
            return None
        return CameraViewSnapshot(eye_xyz=ex, target_xyz=tgt)
    except Exception:
        return None


def capture_current_view() -> Optional[CameraViewSnapshot]:
    """캡처·적용 모두 prim+COI 우선 (일관성)."""
    snap = _snapshot_from_camera_prim()
    if snap is not None:
        return snap
    return _snapshot_from_viewport_state()


def get_play_camera_preset() -> CameraViewSnapshot:
    from .lam_viewport_overlay_config import PLAY_CAMERA_PRESET  # type: ignore

    p = PLAY_CAMERA_PRESET
    return CameraViewSnapshot(
        eye_xyz=tuple(float(x) for x in p.eye_xyz),
        target_xyz=tuple(float(x) for x in p.target_xyz),
    )


def play_camera_preset_configured() -> bool:
    try:
        from .lam_viewport_overlay_config import PLAY_CAMERA_PRESET_ENABLED  # type: ignore

        if not bool(PLAY_CAMERA_PRESET_ENABLED):
            return False
    except Exception:
        return False
    preset = get_play_camera_preset()
    eye = _vec3(preset.eye_xyz)
    tgt = _vec3(preset.target_xyz)
    if (tgt - eye).GetLength() < 0.25:
        return False
    return True


def play_camera_fly_duration_sec() -> float:
    try:
        from .lam_viewport_overlay_config import PLAY_CAMERA_FLY_DURATION_SEC  # type: ignore

        return max(0.05, float(PLAY_CAMERA_FLY_DURATION_SEC))
    except Exception:
        return 0.6


def play_camera_position_epsilon_m() -> float:
    try:
        from .lam_viewport_overlay_config import PLAY_CAMERA_FLY_POSITION_EPS_M  # type: ignore

        return max(1e-6, float(PLAY_CAMERA_FLY_POSITION_EPS_M))
    except Exception:
        return 0.05


def views_are_close(
    a: CameraViewSnapshot,
    b: CameraViewSnapshot,
    *,
    pos_eps_m: Optional[float] = None,
) -> bool:
    eps = play_camera_position_epsilon_m() if pos_eps_m is None else float(pos_eps_m)
    if (_vec3(a.eye_xyz) - _vec3(b.eye_xyz)).GetLength() > eps:
        return False
    if (_vec3(a.target_xyz) - _vec3(b.target_xyz)).GetLength() > eps:
        return False
    da = _vec3(a.target_xyz) - _vec3(a.eye_xyz)
    db = _vec3(b.target_xyz) - _vec3(b.eye_xyz)
    if da.GetLength() < 1e-9 or db.GetLength() < 1e-9:
        return True
    da.Normalize()
    db.Normalize()
    dot = float(da * db)
    dot = max(-1.0, min(1.0, dot))
    try:
        from .lam_viewport_overlay_config import PLAY_CAMERA_FLY_DIRECTION_EPS_DEG  # type: ignore

        deg = float(PLAY_CAMERA_FLY_DIRECTION_EPS_DEG)
    except Exception:
        deg = 1.0
    return dot >= math.cos(math.radians(max(0.0, deg)))


def format_config_snippet(snap: CameraViewSnapshot) -> str:
    e = snap.eye_xyz
    t = snap.target_xyz
    return (
        "# lam_viewport_overlay_config.py 에 붙여넣기\n"
        "PLAY_CAMERA_PRESET_ENABLED = True\n"
        "PLAY_CAMERA_PRESET = PlayCameraPresetSpec(\n"
        f"    eye_xyz=({e[0]:.6f}, {e[1]:.6f}, {e[2]:.6f}),\n"
        f"    target_xyz=({t[0]:.6f}, {t[1]:.6f}, {t[2]:.6f}),\n"
        ")\n"
    )


def log_play_camera_preset_capture() -> bool:
    """현재 뷰포트 시점 캡처 → 콘솔에 config 조각 출력 (UI 버튼용)."""
    snap = capture_current_view()
    if snap is None:
        print(
            f"{_PRINT_PREFIX} 캡처 실패 — 활성 뷰포트·카메라·stage 확인",
            flush=True,
        )
        return False
    print(
        f"{_PRINT_PREFIX} 현재 뷰 캡처 (eye→target 거리 "
        f"{(_vec3(snap.target_xyz) - _vec3(snap.eye_xyz)).GetLength():.3f} m):\n"
        f"{format_config_snippet(snap)}",
        flush=True,
    )
    return True


def _camera_world_from_eye_target(
    eye: Tuple[float, float, float],
    target: Tuple[float, float, float],
    up: Tuple[float, float, float],
) -> Gf.Matrix4d:
    """Kit 카메라 월드 행렬 (local -Z → target). SetLookAt 은 view 행렬이므로 역행렬."""
    view = Gf.Matrix4d(1.0)
    view.SetLookAt(_vec3(eye), _vec3(target), _vec3(up))
    try:
        return view.GetInverse()
    except Exception:
        fwd = (_vec3(target) - _vec3(eye))
        if fwd.GetLength() < 1e-9:
            return Gf.Matrix4d(1.0)
        fwd.Normalize()
        up_v = _vec3(up)
        if abs(float(fwd * up_v)) > 0.99:
            up_v = Gf.Vec3d(0.0, 1.0, 0.0)
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
        m.SetRow(3, Gf.Vec4d(eye[0], eye[1], eye[2], 1.0))
        return m


def _camera_local_matrix(
    cam_prim: Usd.Prim,
    eye: Tuple[float, float, float],
    target: Tuple[float, float, float],
    up: Tuple[float, float, float],
) -> Gf.Matrix4d:
    world = _camera_world_from_eye_target(eye, target, up)
    parent = cam_prim.GetParent()
    if parent and parent.IsValid() and not parent.IsPseudoRoot():
        px = UsdGeom.Xformable(parent).ComputeParentToWorldTransform(Usd.TimeCode.Default())
        return px.GetInverse() * world
    return world


def apply_camera_view(snap: CameraViewSnapshot) -> bool:
    stage = _get_stage()
    path = _active_camera_path_str()
    if not stage or not path:
        return False
    cam_prim = stage.GetPrimAtPath(path)
    if not cam_prim or not cam_prim.IsValid():
        return False
    try:
        from .lam_viewport_overlay_config import PLAY_CAMERA_PRESET  # type: ignore

        up = tuple(float(x) for x in PLAY_CAMERA_PRESET.up_xyz)
    except Exception:
        up = (0.0, 0.0, 1.0)

    dist = (_vec3(snap.target_xyz) - _vec3(snap.eye_xyz)).GetLength()
    if dist < 1e-6:
        return False
    coi_val = Gf.Vec3d(0.0, 0.0, -float(dist))
    new_local = _camera_local_matrix(cam_prim, snap.eye_xyz, snap.target_xyz, up)

    xformable = UsdGeom.Xformable(cam_prim)
    old_local = xformable.GetLocalTransformation(Usd.TimeCode.Default())
    ctx_name = _resolve_usd_context_name()

    try:
        import omni.kit.commands as cmds  # type: ignore

        cmds.execute(
            "TransformPrimCommand",
            path=path,
            new_transform_matrix=new_local,
            old_transform_matrix=old_local,
            time_code=Usd.TimeCode.Default(),
            usd_context_name=ctx_name,
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} TransformPrimCommand failed: {exc}", flush=True)
        try:
            edit = Usd.EditContext(stage, Usd.EditTarget(stage.GetSessionLayer()))
            with edit:
                xformable = UsdGeom.Xformable(cam_prim)
                xformable.ClearXformOpOrder()
                op = xformable.AddTransformOp()
                op.Set(new_local)
                coi_attr = cam_prim.GetAttribute(_COI_ATTR)
                if not coi_attr or not coi_attr.IsValid():
                    cam_prim.CreateAttribute(
                        _COI_ATTR, Sdf.ValueTypeNames.Vector3d, custom=True
                    ).Set(coi_val)
                else:
                    coi_attr.Set(coi_val)
        except Exception as exc2:
            print(f"{_PRINT_PREFIX} USD camera set failed: {exc2}", flush=True)
            return False
    else:
        try:
            edit = Usd.EditContext(stage, Usd.EditTarget(stage.GetSessionLayer()))
            with edit:
                coi_attr = cam_prim.GetAttribute(_COI_ATTR)
                if not coi_attr or not coi_attr.IsValid():
                    cam_prim.CreateAttribute(
                        _COI_ATTR, Sdf.ValueTypeNames.Vector3d, custom=True
                    ).Set(coi_val)
                else:
                    coi_attr.Set(coi_val)
        except Exception:
            pass
    return True


def _start_fly_animation(
    start: CameraViewSnapshot,
    end: CameraViewSnapshot,
    done: threading.Event,
) -> None:
    """main 스레드에서만 호출 — update 구독만 걸고 즉시 반환 (메인 wait 금지)."""
    dur = play_camera_fly_duration_sec()
    t0 = time.perf_counter()
    sub_box: List[Any] = [None]

    def _finish() -> None:
        try:
            if sub_box[0] is not None:
                sub_box[0].unsubscribe()
        except Exception:
            pass
        sub_box[0] = None
        done.set()

    def _tick(_event) -> None:
        elapsed = time.perf_counter() - t0
        u = _smoothstep01(elapsed / dur) if dur > 1e-9 else 1.0
        eye = _lerp3(start.eye_xyz, end.eye_xyz, u)
        tgt = _lerp3(start.target_xyz, end.target_xyz, u)
        apply_camera_view(CameraViewSnapshot(eye_xyz=eye, target_xyz=tgt))
        if u >= 1.0 - 1e-9:
            apply_camera_view(end)
            _finish()

    try:
        import omni.kit.app as _app  # type: ignore

        stream = _app.get_app().get_update_event_stream()
        sub_box[0] = stream.create_subscription_to_pop(
            _tick,
            name="morph.lam_control:play_camera_fly",
        )
        apply_camera_view(
            CameraViewSnapshot(
                eye_xyz=start.eye_xyz,
                target_xyz=start.target_xyz,
            )
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} update subscribe failed: {exc}", flush=True)
        apply_camera_view(end)
        _finish()


def will_run_play_camera_fly() -> bool:
    try:
        from .lam_viewport_overlay_state import get_toggle_play_camera_fly  # type: ignore
    except Exception:
        return False
    return bool(get_toggle_play_camera_fly()) and play_camera_preset_configured()


def planned_camera_fly_duration_sec() -> float:
    """스케줄용 — fly 가 켜져 있으면 config duration (실제 스킵 시에도 동일)."""
    if not will_run_play_camera_fly():
        return 0.0
    return play_camera_fly_duration_sec()


def kickoff_play_camera_fly(done: threading.Event) -> bool:
    """Play worker — main 에 fly 시작만 걸고 즉시 반환. 완료는 ``done``."""
    if done is None:
        raise ValueError("done event required")
    if not will_run_play_camera_fly():
        done.set()
        return False

    preset = get_play_camera_preset()
    fly_started = False
    err: List[Optional[BaseException]] = [None]

    def _kickoff_on_main() -> None:
        nonlocal fly_started
        try:
            current = capture_current_view()
            if current is None:
                print(
                    f"{_PRINT_PREFIX} 현재 뷰 읽기 실패 — fly 생략",
                    flush=True,
                )
                return
            if views_are_close(current, preset):
                print(f"{_PRINT_PREFIX} 현재 뷰 ≈ preset — fly 생략", flush=True)
                return
            print(
                f"{_PRINT_PREFIX} fly 시작 "
                f"({play_camera_fly_duration_sec():.2f}s)",
                flush=True,
            )
            fly_started = True
            _start_fly_animation(current, preset, done)
        except BaseException as e:
            err[0] = e
            raise
        finally:
            if not fly_started:
                done.set()

    try:
        from .lam_sequence_engine import _dispatch_main_wait  # type: ignore

        if not _dispatch_main_wait(_kickoff_on_main, timeout=5.0):
            print(f"{_PRINT_PREFIX} fly kickoff timeout", flush=True)
            done.set()
            return False
    except Exception as exc:
        print(f"{_PRINT_PREFIX} fly kickoff failed: {exc}", flush=True)
        done.set()
        return False
    if err[0] is not None:
        print(f"{_PRINT_PREFIX} fly error: {err[0]}", flush=True)
    return bool(fly_started)


def run_play_camera_fly_before_start() -> None:
    """Play worker — fly 완료까지 대기 (레거시·단독 호출용)."""
    if not will_run_play_camera_fly():
        return
    done = threading.Event()
    kicked = kickoff_play_camera_fly(done)
    if not kicked:
        return
    wait_sec = play_camera_fly_duration_sec() + 8.0
    if not done.wait(timeout=wait_sec):
        print(
            f"{_PRINT_PREFIX} fly wait timeout ({wait_sec:.1f}s) — 재생 계속",
            flush=True,
        )
    else:
        print(f"{_PRINT_PREFIX} fly 완료", flush=True)


__all__ = [
    "CameraViewSnapshot",
    "apply_camera_view",
    "capture_current_view",
    "format_config_snippet",
    "get_play_camera_preset",
    "log_play_camera_preset_capture",
    "play_camera_fly_duration_sec",
    "play_camera_preset_configured",
    "kickoff_play_camera_fly",
    "planned_camera_fly_duration_sec",
    "run_play_camera_fly_before_start",
    "views_are_close",
    "will_run_play_camera_fly",
]
