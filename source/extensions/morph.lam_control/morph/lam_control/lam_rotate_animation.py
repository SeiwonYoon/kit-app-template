"""LAM ROTATE animator — TBS `rotate_animation.py` 와 동일 의미의 회전 동작을 LAM 측에 별도 구현.

REQ-002 0줄 변경 원칙(USD_Timeline_Spec.md §12) 으로 본 모듈은 `morph.tbs_control_1`
의 어떤 모듈도 import 하지 않는다.

지원 모드 (TBS sequence_engine 의 ROTATE 분기와 의미 동일):
- **simple** — `auto_pivot_world_center=False`, `user_axis_rotate=False`.
    `_OFFSET_SUFFIX = "TBS_OFFSET"` 의 RotateXYZOp 에 (rx, ry, rz) 누적 보간.
- **lock_world_center** — `auto_pivot_world_center=True`. 회전을 적용하면서 매 프레임
    prim 의 월드 BBox 중심을 시작 시점 위치로 되돌리도록 TBS_OFFSET translate 를 보정.
- **world_pivot_euler** — `user_axis_rotate=True` + `pivot_world` 지정. 월드 고정 Euler
    XYZ 회전을 pivot 을 지나는 축으로 적용 — `M_w' = T(P) * R * T(-P) * M_w0`.
    parent-relative 로 환산해 prim 의 local transform 에 author.

LAM 은 default 컨텍스트(`""`) 만 사용한다.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

# IMPORTANT — Kit / pxr 모듈은 반드시 모듈 최상단에서 import 한다(TBS rotate_animation 와 동일 정책).
# 함수 내부 lazy import 로 두면 background thread 에서 첫 호출 시 처음 import 가 일어나는데,
# 그 시점에 main thread 가 MDL 컴파일/RTX 렌더 패스로 import lock 을 잡고 있으면
# cross-wait 으로 영구 deadlock 이 발생한다.
import omni.kit.app  # type: ignore  # noqa: E402
import omni.usd as ou  # type: ignore  # noqa: E402
from pxr import Gf, Usd, UsdGeom  # type: ignore  # noqa: E402


_PRINT_PREFIX = "[LAM/ROT]"
_OFFSET_SUFFIX = "TBS_OFFSET"


_rot_animations: Dict[str, Dict[str, Any]] = {}
_update_sub = None

_world_pivot_state: Optional[Dict[str, Any]] = None
_world_pivot_sub = None


# ----------------------------------------------------------------- helpers

def _stage():
    try:
        ctx = ou.get_context()
        return ctx.get_stage() if ctx else None
    except Exception:
        return None


def is_rotate_animation_running() -> bool:
    return bool(_rot_animations) or (_world_pivot_state is not None)


def _get_or_create_offset_translate_op(prim):
    try:
        from pxr import UsdGeom  # type: ignore

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


def _get_or_create_offset_rotate_op(prim):
    try:
        from pxr import UsdGeom  # type: ignore

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


def _get_prim_local_translate(prim):
    from pxr import Gf  # type: ignore

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


def _set_prim_translate(prim, v) -> None:
    from pxr import Gf  # type: ignore

    if not prim or not prim.IsValid():
        return
    try:
        op = _get_or_create_offset_translate_op(prim)
        if op:
            op.Set(Gf.Vec3f(float(v[0]), float(v[1]), float(v[2])))
    except Exception:
        pass


def _get_prim_local_rotate_xyz(prim):
    from pxr import Gf  # type: ignore

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
    from pxr import Gf  # type: ignore

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
    """`TBS_OFFSET` RotateXYZOp 을 (0,0,0) 으로 설정(Run(reset) 시 초기 자세 복귀)."""
    stage = _stage()
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return
    from pxr import Gf  # type: ignore

    _set_prim_rotate_xyz(prim, Gf.Vec3f(0.0, 0.0, 0.0))


def _prim_world_bbox_center(stage, prim):
    try:
        from pxr import Gf, Usd, UsdGeom  # type: ignore

        cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            includedPurposes=[
                UsdGeom.Tokens.default_,
                UsdGeom.Tokens.render,
                UsdGeom.Tokens.proxy,
            ],
            useExtentsHint=True,
        )
        bbox = cache.ComputeWorldBound(prim)
        rng = bbox.ComputeAlignedBox()
        mn = rng.GetMin()
        mx = rng.GetMax()
        return Gf.Vec3d(
            (float(mn[0]) + float(mx[0])) * 0.5,
            (float(mn[1]) + float(mx[1])) * 0.5,
            (float(mn[2]) + float(mx[2])) * 0.5,
        )
    except Exception:
        return None


def _matrix_from_rotate_xyz_deg(rxyz):
    from pxr import Gf  # type: ignore

    m = Gf.Matrix4d(1.0)
    if rxyz is None:
        return m
    try:
        mx = Gf.Matrix3d(Gf.Rotation(Gf.Vec3d(1, 0, 0), float(rxyz[0])))
        my = Gf.Matrix3d(Gf.Rotation(Gf.Vec3d(0, 1, 0), float(rxyz[1])))
        mz = Gf.Matrix3d(Gf.Rotation(Gf.Vec3d(0, 0, 1), float(rxyz[2])))
        r3 = mx * my * mz
        m.SetRotateOnly(Gf.Rotation(r3))
    except Exception:
        pass
    return m


def _world_orbit_matrix_euler_pivot(pivot_world, rx, ry, rz):
    from pxr import Gf  # type: ignore

    r4 = _matrix_from_rotate_xyz_deg((rx, ry, rz))
    t_inv = Gf.Matrix4d(1.0)
    t_inv.SetTranslateOnly(
        Gf.Vec3d(-float(pivot_world[0]), -float(pivot_world[1]), -float(pivot_world[2]))
    )
    t_px = Gf.Matrix4d(1.0)
    t_px.SetTranslateOnly(
        Gf.Vec3d(float(pivot_world[0]), float(pivot_world[1]), float(pivot_world[2]))
    )
    return t_px * r4 * t_inv


def _apply_local_matrix_to_offset(prim, M_local) -> None:
    """parent-relative 로컬 매트릭스를 prim 의 TBS_OFFSET (Translate + RotateXYZ) 로 분해 author.

    단순화: M_local 을 translation(=ExtractTranslation) + rotation(Euler XYZ) 로 분해해
    각각 TBS_OFFSET 의 두 op 에 박는다. (LAM 의 TBS_OFFSET op 가 없는 경우 자동 생성.)
    """
    try:
        from pxr import Gf  # type: ignore

        tr = M_local.ExtractTranslation()
        # 회전 추출 — M_local 의 rotation part 를 RotateXYZ Euler 로 환산.
        rot = M_local.ExtractRotation()
        rxyz = rot.Decompose(Gf.Vec3d(1, 0, 0), Gf.Vec3d(0, 1, 0), Gf.Vec3d(0, 0, 1))
        _set_prim_translate(prim, Gf.Vec3f(float(tr[0]), float(tr[1]), float(tr[2])))
        _set_prim_rotate_xyz(prim, Gf.Vec3f(float(rxyz[0]), float(rxyz[1]), float(rxyz[2])))
    except Exception as exc:
        print(f"{_PRINT_PREFIX} apply_local_matrix failed: {exc}", flush=True)


# ----------------------------------------------------------------- public

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

    from pxr import Gf  # type: ignore

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


def run_prim_rotate_lock_world_center_animation(
    prim_path: str,
    rx_deg: float,
    ry_deg: float,
    rz_deg: float,
    duration: float,
    on_completed: Optional[Callable[[], None]] = None,
) -> None:
    """lock_world_center 모드 — 회전 적용하면서 월드 BBox 중심을 시작 위치로 매 프레임 보정."""
    global _rot_animations
    stage = _stage()
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return
    desired = _prim_world_bbox_center(stage, prim)
    if desired is None:
        return

    from pxr import Gf  # type: ignore

    start_rot = _get_prim_local_rotate_xyz(prim)
    start_pos = _get_prim_local_translate(prim)
    _rot_animations[prim_path] = {
        "kind": "lock_world_center",
        "desired_center": Gf.Vec3d(float(desired[0]), float(desired[1]), float(desired[2])),
        "start_rot": Gf.Vec3f(start_rot[0], start_rot[1], start_rot[2]),
        "start_pos": Gf.Vec3f(start_pos[0], start_pos[1], start_pos[2]),
        "segments": [
            {
                "duration": float(max(1e-6, duration)),
                "delta": (float(rx_deg), float(ry_deg), float(rz_deg)),
            }
        ],
        "segment_index": 0,
        "elapsed_in_segment": 0.0,
        "loop": False,
        "on_completed": on_completed,
    }
    _ensure_update_sub()


def run_world_euler_pivot_rotate_animation(
    prim_paths: List[str],
    pivot_world,
    rx_deg: float,
    ry_deg: float,
    rz_deg: float,
    duration: float,
    on_completed: Optional[Callable[[], None]] = None,
) -> None:
    """world_pivot_euler 모드 — 월드 고정 Euler XYZ 회전을 `pivot_world` 를 지나는 축으로 적용.

    `pivot_world == None` 이면 prim 의 월드 원점(L2W translation) 을 P 로 사용.
    매 프레임 `M_w' = T(P)*R*T(-P) * M_w0` 로 계산해 parent-relative 로 환산 후 prim 의
    TBS_OFFSET (Translate+RotateXYZ) 로 분해해 author.
    """
    global _world_pivot_state, _world_pivot_sub
    stop_world_pivot_rotate_animation()
    for p in prim_paths:
        stop_prim_rotate_animation(p)

    stage = _stage()
    if not stage or not prim_paths:
        if on_completed:
            try:
                on_completed()
            except Exception:
                pass
        return

    from pxr import Gf, Usd, UsdGeom  # type: ignore

    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    items: List[Dict[str, Any]] = []
    for path in prim_paths:
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            continue
        try:
            M_cw = Gf.Matrix4d(cache.GetLocalToWorldTransform(prim))
        except Exception:
            continue
        parent = prim.GetParent()
        try:
            if parent and parent.IsValid() and str(parent.GetPath()) not in ("", "/"):
                M_pw = Gf.Matrix4d(cache.GetLocalToWorldTransform(parent))
                M_pw_inv = M_pw.GetInverse()
            else:
                M_pw_inv = Gf.Matrix4d(1.0)
        except Exception:
            M_pw_inv = Gf.Matrix4d(1.0)
        tr = M_cw.ExtractTranslation()
        if pivot_world is None:
            pw = Gf.Vec3d(float(tr[0]), float(tr[1]), float(tr[2]))
        else:
            pw = Gf.Vec3d(
                float(pivot_world[0]), float(pivot_world[1]), float(pivot_world[2])
            )
        items.append({"path": path, "M_cw0": M_cw, "M_pw_inv": M_pw_inv, "pivot_world": pw})

    if not items:
        if on_completed:
            try:
                on_completed()
            except Exception:
                pass
        return

    if duration <= 0:
        for it in items:
            prim = stage.GetPrimAtPath(it["path"])
            if not prim or not prim.IsValid():
                continue
            M_rot = _world_orbit_matrix_euler_pivot(it["pivot_world"], rx_deg, ry_deg, rz_deg)
            M_w = M_rot * it["M_cw0"]
            M_local = it["M_pw_inv"] * M_w
            _apply_local_matrix_to_offset(prim, M_local)
        if on_completed:
            try:
                on_completed()
            except Exception:
                pass
        return

    _world_pivot_state = {
        "kind": "euler_pivot",
        "items": items,
        "rx_deg": float(rx_deg),
        "ry_deg": float(ry_deg),
        "rz_deg": float(rz_deg),
        "duration": float(duration),
        "elapsed": 0.0,
        "on_completed": on_completed,
    }

    def _on_we_update(e) -> None:
        global _world_pivot_state, _world_pivot_sub
        st = _world_pivot_state
        if not st or st.get("kind") != "euler_pivot":
            return
        payload = getattr(e, "payload", None) or {}
        dt = float(payload.get("dt", 0.0) or 0.0)
        if dt <= 0:
            dt = 1.0 / 60.0
        st["elapsed"] = float(st["elapsed"]) + dt
        t = (
            min(1.0, st["elapsed"] / st["duration"])
            if st["duration"] > 0
            else 1.0
        )
        rx = t * float(st["rx_deg"])
        ry = t * float(st["ry_deg"])
        rz = t * float(st["rz_deg"])

        stg = _stage()
        if stg is None:
            _world_pivot_state = None
            if _world_pivot_sub is not None:
                try:
                    _world_pivot_sub.unsubscribe()
                except Exception:
                    pass
                _world_pivot_sub = None
            return

        for it in st["items"]:
            prim = stg.GetPrimAtPath(it["path"])
            if not prim or not prim.IsValid():
                continue
            try:
                M_rot = _world_orbit_matrix_euler_pivot(it["pivot_world"], rx, ry, rz)
                M_w = M_rot * it["M_cw0"]
                M_local = it["M_pw_inv"] * M_w
                _apply_local_matrix_to_offset(prim, M_local)
            except Exception as exc:
                print(f"{_PRINT_PREFIX} world_pivot apply error: {exc}", flush=True)

        if t >= 1.0:
            cb = st.get("on_completed")
            _world_pivot_state = None
            if _world_pivot_sub is not None:
                try:
                    _world_pivot_sub.unsubscribe()
                except Exception:
                    pass
                _world_pivot_sub = None
            if cb:
                try:
                    cb()
                except Exception:
                    pass

    try:
        import omni.kit.app  # type: ignore

        stream = omni.kit.app.get_app().get_update_event_stream()
        _world_pivot_sub = stream.create_subscription_to_pop(
            _on_we_update, name="morph.lam_control.world_euler_pivot_rotate"
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} subscribe world_pivot failed: {exc}", flush=True)
        _world_pivot_state = None
        if on_completed:
            try:
                on_completed()
            except Exception:
                pass


def stop_world_pivot_rotate_animation() -> None:
    global _world_pivot_state, _world_pivot_sub
    _world_pivot_state = None
    if _world_pivot_sub is not None:
        try:
            _world_pivot_sub.unsubscribe()
        except Exception:
            pass
        _world_pivot_sub = None


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
    try:
        stop_world_pivot_rotate_animation()
    except Exception:
        pass


# ----------------------------------------------------------------- internal

def _ensure_update_sub() -> None:
    global _update_sub
    if _update_sub is not None:
        return
    try:
        import omni.kit.app  # type: ignore

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
    from pxr import Gf  # type: ignore

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
            kind = state.get("kind", "simple")
            if kind == "lock_world_center":
                seg = (state.get("segments") or [{}])[0]
                duration = float(seg.get("duration", 0.0) or 0.0)
                delta = seg.get("delta") or (0.0, 0.0, 0.0)
                elapsed = float(state.get("elapsed_in_segment", 0.0)) + dt
                t = 1.0 if duration <= 1e-9 else min(1.0, max(0.0, elapsed / duration))
                state["elapsed_in_segment"] = elapsed

                rx = float(delta[0]) * t
                ry = float(delta[1]) * t
                rz = float(delta[2]) * t

                base_rot = state["start_rot"]
                current_rot = Gf.Vec3f(base_rot[0] + rx, base_rot[1] + ry, base_rot[2] + rz)
                _set_prim_rotate_xyz(prim, current_rot)

                desired = state.get("desired_center")
                cur = _prim_world_bbox_center(stage, prim)
                if desired is not None and cur is not None:
                    # 월드 보정량을 parent-world inverse 로 환산해 TBS_OFFSET translate 에 누적.
                    try:
                        from pxr import UsdGeom, Usd  # type: ignore

                        cache = UsdGeom.XformCache(Usd.TimeCode.Default())
                        parent = prim.GetParent()
                        if parent and parent.IsValid() and str(parent.GetPath()) not in ("", "/"):
                            M_pw = Gf.Matrix4d(cache.GetLocalToWorldTransform(parent))
                            M_pw_inv = M_pw.GetInverse()
                        else:
                            M_pw_inv = Gf.Matrix4d(1.0)
                        delta_world = Gf.Vec3d(
                            float(desired[0] - cur[0]),
                            float(desired[1] - cur[1]),
                            float(desired[2] - cur[2]),
                        )
                        # parent 의 회전·스케일이 단위라고 가정하고 단순 translation 변환 적용.
                        delta_local = M_pw_inv.TransformDir(delta_world)
                        pos = _get_prim_local_translate(prim)
                        _set_prim_translate(
                            prim,
                            Gf.Vec3f(
                                pos[0] + float(delta_local[0]),
                                pos[1] + float(delta_local[1]),
                                pos[2] + float(delta_local[2]),
                            ),
                        )
                    except Exception:
                        pass

                if elapsed >= duration:
                    cb = state.get("on_completed")
                    if cb:
                        try:
                            cb()
                        except Exception:
                            pass
                    to_remove.append(prim_path)
                continue

            # simple
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
    "run_prim_rotate_lock_world_center_animation",
    "run_world_euler_pivot_rotate_animation",
    "stop_world_pivot_rotate_animation",
    "stop_prim_rotate_animation",
    "stop_all_rotate_animations",
    "zero_tbs_offset_rotate_at_path",
]
