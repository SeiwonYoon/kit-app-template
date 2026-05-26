"""LAM MOVE animator — TBS `translate_animation.py` 와 동일 동작을 LAM 측에 별도 구현.

REQ-002 0줄 변경 원칙(USD_Timeline_Spec.md §12) 으로 본 모듈은 `morph.tbs_control_1`
의 어떤 모듈도 import 하지 않고 동일한 의미·동일한 op 규약을 자체 구현한다.

규약 (TBS 와 동일):
- prim 의 누적 translate 는 `_OFFSET_SUFFIX = "TBS_OFFSET"` 라는 suffix 의 TranslateOp
  하나에만 author 한다. 자산 USD 가 가진 다른 translate op 와 분리되어 baseline 복원이
  쉬워진다.
- 입력은 segment list — 각 segment 는 `{"duration": float_seconds, "delta": [dx,dy,dz]}`.
  start_pos 는 호출 시점의 _OFFSET_SUFFIX 값.
- omni.kit.app 의 update_event_stream 으로 매 프레임 t = elapsed/duration 으로 보간.
- 완료 시 on_completed 콜백.

LAM 은 default 컨텍스트(`""`)만 사용하므로 컨텍스트 키 분리는 단순화한다.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

# IMPORTANT — Kit / pxr 모듈은 반드시 모듈 최상단에서 import 한다(TBS translate_animation 와 동일 정책).
# 함수 내부 lazy import 로 두면 background thread 에서 첫 호출 시 처음 import 가 일어나는데,
# 그 시점에 main thread 가 MDL 컴파일/RTX 렌더 패스로 import lock 을 잡고 있으면
# cross-wait 으로 영구 deadlock 이 발생한다(=Run 누르자마자 freeze 증상의 실제 원인).
import omni.kit.app  # type: ignore  # noqa: E402
import omni.usd as ou  # type: ignore  # noqa: E402
from pxr import Gf, UsdGeom  # type: ignore  # noqa: E402


_PRINT_PREFIX = "[LAM/MOVE]"
_OFFSET_SUFFIX = "TBS_OFFSET"

_animations: Dict[str, Dict[str, Any]] = {}
_update_sub = None


def _stage():
    try:
        ctx = ou.get_context()
        return ctx.get_stage() if ctx else None
    except Exception:
        return None


def is_translate_animation_running() -> bool:
    return bool(_animations)


def is_prim_translate_animation_running(prim_path: str) -> bool:
    """지정 prim 에 대한 TBS_OFFSET translate 보간이 진행 중인지."""
    return bool(prim_path) and prim_path in _animations


def _get_or_create_offset_translate_op(prim):
    try:
        x = UsdGeom.Xformable(prim)
        if not x:
            return None
        for op in x.GetOrderedXformOps():
            try:
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate and _OFFSET_SUFFIX in op.GetName():
                    return op
            except Exception:
                continue
        return x.AddTranslateOp(opSuffix=_OFFSET_SUFFIX)
    except Exception:
        return None


def _get_prim_local_translate(prim):
    if not prim or not prim.IsValid():
        return Gf.Vec3f(0, 0, 0)
    try:
        op = _get_or_create_offset_translate_op(prim)
        if op:
            v = op.Get()
            if v is not None:
                return Gf.Vec3f(float(v[0]), float(v[1]), float(v[2]))
    except Exception:
        pass
    return Gf.Vec3f(0, 0, 0)


def _set_prim_translate(prim, position) -> None:
    if not prim or not prim.IsValid():
        return
    try:
        op = _get_or_create_offset_translate_op(prim)
        if op:
            op.Set(Gf.Vec3f(float(position[0]), float(position[1]), float(position[2])))
    except Exception:
        pass


def read_tbs_offset_translate_xyz(prim_path: str) -> tuple[float, float, float]:
    """prim 의 ``TBS_OFFSET`` TranslateOp 현재값 (x, y, z). 없으면 (0,0,0)."""
    stage = _stage()
    if not stage:
        return (0.0, 0.0, 0.0)
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return (0.0, 0.0, 0.0)
    v = _get_prim_local_translate(prim)
    return (float(v[0]), float(v[1]), float(v[2]))


def zero_tbs_offset_translate_at_path(prim_path: str) -> None:
    """`TBS_OFFSET` TranslateOp 을 (0,0,0) 으로 설정(Run(reset) 시 초기 위치 복귀)."""
    stage = _stage()
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return
    stop_prim_translate_animation(prim_path)
    _set_prim_translate(prim, Gf.Vec3f(0.0, 0.0, 0.0))


def snap_tbs_offset_translate_to_absolute(
    prim_path: str,
    x: float,
    y: float,
    z: float,
) -> None:
    """``move_from_initial=True`` 목표 좌표로 TBS_OFFSET translate 즉시 스냅."""
    stage = _stage()
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return
    stop_prim_translate_animation(prim_path)
    _set_prim_translate(
        prim,
        Gf.Vec3f(float(x), float(y), float(z)),
    )


def run_prim_translate_animation(
    prim_path: str,
    segments: List[Dict[str, Any]],
    loop: bool = False,
    on_completed: Optional[Callable[[], None]] = None,
    speed_ref: float = 1.0,
) -> None:
    global _animations, _update_sub
    if not segments:
        return
    stage = _stage()
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        print(f"{_PRINT_PREFIX} prim not found: {prim_path}", flush=True)
        return

    start_pos = _get_prim_local_translate(prim)
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

    _animations[prim_path] = {
        "prim_path": prim_path,
        "start_pos": Gf.Vec3f(start_pos[0], start_pos[1], start_pos[2]),
        "segments": normalized,
        "segment_index": 0,
        "elapsed_in_segment": 0.0,
        "loop": loop,
        "on_completed": on_completed,
        "speed_ref": float(max(0.01, speed_ref or 1.0)),
    }
    _ensure_update_sub()


def stop_prim_translate_animation(prim_path: str) -> bool:
    global _animations
    if prim_path in _animations:
        _animations.pop(prim_path, None)
        _maybe_release_update_sub()
        return True
    return False


def stop_all_translate_animations() -> None:
    global _animations, _update_sub
    _animations.clear()
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
            _on_update, name="morph.lam_control.translate_animation"
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} subscribe update failed: {exc}", flush=True)


def _maybe_release_update_sub() -> None:
    global _update_sub
    if not _animations and _update_sub is not None:
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
    try:
        from .simulation_play import get_csv_play_anim_dt_scale
    except Exception:
        get_csv_play_anim_dt_scale = None  # type: ignore
    if not _animations:
        return
    stage = _stage()
    if not stage:
        return

    to_remove: List[str] = []
    for prim_path, state in list(_animations.items()):
        try:
            prim = stage.GetPrimAtPath(prim_path)
            if not prim or not prim.IsValid():
                to_remove.append(prim_path)
                continue
            frame_dt = dt
            if get_csv_play_anim_dt_scale is not None:
                frame_dt = dt * float(
                    get_csv_play_anim_dt_scale(float(state.get("speed_ref", 1.0) or 1.0))
                )
            segments = state["segments"]
            idx = state["segment_index"]
            elapsed = state["elapsed_in_segment"] + frame_dt
            base_pos = state["start_pos"]
            for i in range(idx):
                d = segments[i]["delta"]
                base_pos = Gf.Vec3f(base_pos[0] + d[0], base_pos[1] + d[1], base_pos[2] + d[2])
            duration = float(segments[idx]["duration"])
            delta = segments[idx]["delta"]
            if elapsed >= duration:
                state["elapsed_in_segment"] = 0.0
                state["segment_index"] = idx + 1
                final_this = Gf.Vec3f(
                    base_pos[0] + delta[0], base_pos[1] + delta[1], base_pos[2] + delta[2]
                )
                if state["segment_index"] >= len(segments):
                    _set_prim_translate(prim, final_this)
                    if state["loop"]:
                        state["segment_index"] = 0
                        state["start_pos"] = final_this
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
                    _set_prim_translate(prim, current)
                continue
            state["elapsed_in_segment"] = elapsed
            t = elapsed / duration
            current = Gf.Vec3f(
                base_pos[0] + delta[0] * t,
                base_pos[1] + delta[1] * t,
                base_pos[2] + delta[2] * t,
            )
            _set_prim_translate(prim, current)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} update error path={prim_path}: {exc}", flush=True)
            to_remove.append(prim_path)
    for k in to_remove:
        _animations.pop(k, None)
    _maybe_release_update_sub()


__all__ = [
    "is_translate_animation_running",
    "is_prim_translate_animation_running",
    "read_tbs_offset_translate_xyz",
    "run_prim_translate_animation",
    "stop_prim_translate_animation",
    "stop_all_translate_animations",
    "zero_tbs_offset_translate_at_path",
    "snap_tbs_offset_translate_to_absolute",
]
