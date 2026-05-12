"""LAM ROTATE animator — simple 모드 한 가지만 지원.

REQ-002 0줄 변경 원칙(USD_Timeline_Spec.md §12) 으로 본 모듈은 `morph.tbs_control_1`
의 어떤 모듈도 import 하지 않는다.

지원 모드:
- **simple** — `_OFFSET_SUFFIX = "TBS_OFFSET"` 의 RotateXYZOp 에 (rx, ry, rz) 누적 보간.

『월드 피봇 회전 / lock_world_center』 모드는 LAM 사용 시나리오에서 안정적으로 동작하지
않아 2026-05-12 에 제거되었다. 외부 모듈이 import 하는
`stop_world_pivot_rotate_animation` 은 호환을 위해 no-op 으로 남겨 둔다.

LAM 은 default 컨텍스트(`""`) 만 사용한다.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

# IMPORTANT — Kit / pxr 모듈은 반드시 모듈 최상단에서 import 한다(TBS rotate_animation 와 동일 정책).
# 함수 내부 lazy import 로 두면 background thread 에서 첫 호출 시 처음 import 가 일어나는데,
# 그 시점에 main thread 가 MDL 컴파일/RTX 렌더 패스로 import lock 을 잡고 있으면
# cross-wait 으로 영구 deadlock 이 발생한다.
import omni.kit.app  # type: ignore  # noqa: E402,F401
import omni.usd as ou  # type: ignore  # noqa: E402
from pxr import Gf, UsdGeom  # type: ignore  # noqa: E402,F401


_PRINT_PREFIX = "[LAM/ROT]"
_OFFSET_SUFFIX = "TBS_OFFSET"


_rot_animations: Dict[str, Dict[str, Any]] = {}
_update_sub = None


# ----------------------------------------------------------------- helpers

def _stage():
    try:
        ctx = ou.get_context()
        return ctx.get_stage() if ctx else None
    except Exception:
        return None


def is_rotate_animation_running() -> bool:
    return bool(_rot_animations)


def _get_or_create_offset_rotate_op(prim):
    try:
        x = UsdGeom.Xformable(prim)
        if not x:
            return None
        for op in x.GetOrderedXformOps():
            try:
                if op.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ and _OFFSET_SUFFIX in op.GetName():
                    return op
            except Exception:
                continue
        return x.AddRotateXYZOp(opSuffix=_OFFSET_SUFFIX)
    except Exception:
        return None


def _get_prim_local_rotate_xyz(prim):
    if not prim or not prim.IsValid():
        return Gf.Vec3f(0, 0, 0)
    try:
        op = _get_or_create_offset_rotate_op(prim)
        if op:
            v = op.Get()
            if v is not None:
                return Gf.Vec3f(float(v[0]), float(v[1]), float(v[2]))
    except Exception:
        pass
    return Gf.Vec3f(0, 0, 0)


def _set_prim_rotate_xyz(prim, euler_deg_xyz) -> None:
    if not prim or not prim.IsValid():
        return
    try:
        op = _get_or_create_offset_rotate_op(prim)
        if op:
            op.Set(
                Gf.Vec3f(
                    float(euler_deg_xyz[0]),
                    float(euler_deg_xyz[1]),
                    float(euler_deg_xyz[2]),
                )
            )
    except Exception:
        pass


def zero_tbs_offset_rotate_at_path(prim_path: str) -> None:
    """`TBS_OFFSET` RotateXYZOp 을 (0,0,0) 으로 설정(Run(reset) / Return 시 초기 자세 복귀)."""
    stage = _stage()
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return
    _set_prim_rotate_xyz(prim, Gf.Vec3f(0.0, 0.0, 0.0))


def read_tbs_offset_rotate_xyz_deg(prim_path: str) -> tuple[float, float, float]:
    """현재 prim 의 `TBS_OFFSET` RotateXYZ 값을 (rx,ry,rz) deg 로 반환 — **read-only**.

    `lam_sequence_engine` 의 `rotate_from_initial` 분기가 "현재 각도" 를 읽을 때 사용.

    **중요**: 본 함수는 `AddRotateXYZOp` 등 어떤 USD write 도 수행하지 않는다.
    op 가 아직 author 되지 않았으면 `(0,0,0)` 을 반환한다 (TBS_OFFSET 가 없으면 회전 0
    이라는 정의와 일치). main thread 밖에서 호출되어도 안전하도록 read-only.
    """
    stage = _stage()
    if not stage:
        return (0.0, 0.0, 0.0)
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return (0.0, 0.0, 0.0)
    try:
        x = UsdGeom.Xformable(prim)
        if not x:
            return (0.0, 0.0, 0.0)
        for op in x.GetOrderedXformOps():
            try:
                if (
                    op.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ
                    and _OFFSET_SUFFIX in op.GetName()
                ):
                    v = op.Get()
                    if v is None:
                        return (0.0, 0.0, 0.0)
                    return (float(v[0]), float(v[1]), float(v[2]))
            except Exception:
                continue
    except Exception:
        pass
    return (0.0, 0.0, 0.0)


# ----------------------------------------------------------------- public (run)

def run_prim_rotate_animation(
    prim_path: str,
    segments: List[Dict[str, Any]],
    loop: bool = False,
    on_completed: Optional[Callable[[], None]] = None,
) -> None:
    """simple 모드 — TBS_OFFSET RotateXYZ 에 (rx,ry,rz) 누적 보간."""
    global _rot_animations
    if not segments:
        return
    stage = _stage()
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        print(f"{_PRINT_PREFIX} prim not found: {prim_path}", flush=True)
        return

    start_rot = _get_prim_local_rotate_xyz(prim)
    normalized: List[Dict[str, Any]] = []
    for seg in segments:
        d = seg.get("delta")
        if d is None or not (isinstance(d, (list, tuple)) and len(d) >= 3):
            continue
        duration = float(seg.get("duration", 0.0) or 0.0)
        if duration <= 0:
            continue
        normalized.append(
            {"duration": duration, "delta": (float(d[0]), float(d[1]), float(d[2]))}
        )
    if not normalized:
        if on_completed:
            try:
                on_completed()
            except Exception:
                pass
        return

    _rot_animations[prim_path] = {
        "kind": "simple",
        "start_rot": Gf.Vec3f(start_rot[0], start_rot[1], start_rot[2]),
        "segments": normalized,
        "segment_index": 0,
        "elapsed_in_segment": 0.0,
        "loop": loop,
        "on_completed": on_completed,
    }
    _ensure_update_sub()


def stop_world_pivot_rotate_animation() -> None:
    """레거시 호환 no-op (월드 피봇 회전 기능 제거됨)."""
    return None


def stop_prim_rotate_animation(prim_path: str) -> bool:
    global _rot_animations
    if prim_path in _rot_animations:
        _rot_animations.pop(prim_path, None)
        _maybe_release_update_sub()
        return True
    return False


def stop_all_rotate_animations() -> None:
    global _rot_animations, _update_sub
    _rot_animations.clear()
    if _update_sub is not None:
        try:
            _update_sub.unsubscribe()
        except Exception:
            pass
        _update_sub = None


# ----------------------------------------------------------------- internal

def _ensure_update_sub() -> None:
    global _update_sub
    if _update_sub is not None:
        return
    try:
        stream = omni.kit.app.get_app().get_update_event_stream()
        _update_sub = stream.create_subscription_to_pop(
            _on_update, name="morph.lam_control.rotate_animation"
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} subscribe update failed: {exc}", flush=True)


def _maybe_release_update_sub() -> None:
    global _update_sub
    if not _rot_animations and _update_sub is not None:
        try:
            _update_sub.unsubscribe()
        except Exception:
            pass
        _update_sub = None


def _on_update(e) -> None:
    payload = getattr(e, "payload", None) or {}
    dt = float(payload.get("dt", 0.0) or 0.0)
    if dt <= 0:
        dt = 1.0 / 60.0
    if not _rot_animations:
        return
    stage = _stage()
    if not stage:
        return

    to_remove: List[str] = []
    for prim_path, state in list(_rot_animations.items()):
        try:
            prim = stage.GetPrimAtPath(prim_path)
            if not prim or not prim.IsValid():
                to_remove.append(prim_path)
                continue

            segments = state["segments"]
            idx = state["segment_index"]
            elapsed = state["elapsed_in_segment"] + dt
            base_rot = state["start_rot"]
            for i in range(idx):
                d = segments[i]["delta"]
                base_rot = Gf.Vec3f(
                    base_rot[0] + d[0], base_rot[1] + d[1], base_rot[2] + d[2]
                )
            duration = float(segments[idx]["duration"])
            delta = segments[idx]["delta"]
            if elapsed >= duration:
                state["elapsed_in_segment"] = 0.0
                state["segment_index"] = idx + 1
                final_this = Gf.Vec3f(
                    base_rot[0] + delta[0],
                    base_rot[1] + delta[1],
                    base_rot[2] + delta[2],
                )
                if state["segment_index"] >= len(segments):
                    _set_prim_rotate_xyz(prim, final_this)
                    if state["loop"]:
                        state["segment_index"] = 0
                        state["start_rot"] = final_this
                    else:
                        cb = state.get("on_completed")
                        if cb:
                            try:
                                cb()
                            except Exception:
                                pass
                        to_remove.append(prim_path)
                else:
                    remainder = elapsed - duration
                    state["elapsed_in_segment"] = remainder
                    next_idx = state["segment_index"]
                    next_d = segments[next_idx]["delta"]
                    next_dur = float(segments[next_idx]["duration"])
                    t = min(1.0, remainder / next_dur) if next_dur > 0 else 1.0
                    current = Gf.Vec3f(
                        final_this[0] + next_d[0] * t,
                        final_this[1] + next_d[1] * t,
                        final_this[2] + next_d[2] * t,
                    )
                    _set_prim_rotate_xyz(prim, current)
                continue
            state["elapsed_in_segment"] = elapsed
            t = elapsed / duration if duration > 0 else 1.0
            current_rot = Gf.Vec3f(
                base_rot[0] + delta[0] * t,
                base_rot[1] + delta[1] * t,
                base_rot[2] + delta[2] * t,
            )
            _set_prim_rotate_xyz(prim, current_rot)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} update error path={prim_path}: {exc}", flush=True)
            to_remove.append(prim_path)
    for k in to_remove:
        _rot_animations.pop(k, None)
    _maybe_release_update_sub()


__all__ = [
    "is_rotate_animation_running",
    "run_prim_rotate_animation",
    "stop_world_pivot_rotate_animation",
    "stop_prim_rotate_animation",
    "stop_all_rotate_animations",
    "zero_tbs_offset_rotate_at_path",
    "read_tbs_offset_rotate_xyz_deg",
]
