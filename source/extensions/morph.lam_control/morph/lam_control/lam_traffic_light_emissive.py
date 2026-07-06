"""신호등 shader Emissive 랜덤 토글 (설정: lam_viewport_overlay_config).

3개 shader 중 30~45초마다 랜덤 1개만 Enable Emission ON.
"""

from __future__ import annotations

import random
import threading
from typing import Any, Dict, Optional, Tuple

import omni.usd as ou  # type: ignore
from pxr import Sdf, Usd, UsdShade  # type: ignore

_PRINT_PREFIX = "[LAM/TrafficLight]"

_state: Dict[str, Any] = {
    "timer": None,
    "running": False,
    "startup_sub": None,
    "last_active_index": -1,
}
_lock = threading.Lock()


def _config_enabled() -> bool:
    try:
        from .lam_viewport_overlay_config import TRAFFIC_LIGHT_EMISSIVE_ENABLED  # type: ignore

        return bool(TRAFFIC_LIGHT_EMISSIVE_ENABLED)
    except Exception:
        return False


def _only_during_playback() -> bool:
    try:
        from .lam_viewport_overlay_config import (  # type: ignore
            TRAFFIC_LIGHT_EMISSIVE_ONLY_DURING_PLAYBACK,
        )

        return bool(TRAFFIC_LIGHT_EMISSIVE_ONLY_DURING_PLAYBACK)
    except Exception:
        return False


def _shader_paths() -> Tuple[str, ...]:
    try:
        from .lam_viewport_overlay_config import TRAFFIC_LIGHT_SHADER_PATHS  # type: ignore

        return tuple(str(p or "").strip() for p in TRAFFIC_LIGHT_SHADER_PATHS if str(p or "").strip())
    except Exception:
        return ()


def _interval_bounds() -> Tuple[float, float]:
    try:
        from .lam_viewport_overlay_config import (  # type: ignore
            TRAFFIC_LIGHT_EMISSIVE_INTERVAL_MAX_SEC,
            TRAFFIC_LIGHT_EMISSIVE_INTERVAL_MIN_SEC,
        )

        lo = max(0.5, float(TRAFFIC_LIGHT_EMISSIVE_INTERVAL_MIN_SEC))
        hi = max(lo, float(TRAFFIC_LIGHT_EMISSIVE_INTERVAL_MAX_SEC))
        return lo, hi
    except Exception:
        return 30.0, 45.0


def _get_stage() -> Optional[Usd.Stage]:
    try:
        ctx = ou.get_context()
        if ctx is not None:
            st = ctx.get_stage()
            if st is not None:
                return st
    except Exception:
        pass
    return None


_EMISSION_ENABLE_ATTR = "inputs:enable_emission"


def _resolve_attr_name(shader_path: str) -> Optional[str]:
    stage = _get_stage()
    if stage is None:
        return None
    prim = stage.GetPrimAtPath(shader_path)
    if not prim or not prim.IsValid():
        print(
            f"{_PRINT_PREFIX} shader prim 없음 — skip: {shader_path}",
            flush=True,
        )
        return None
    return _EMISSION_ENABLE_ATTR


def _set_shader_emission(shader_path: str, enabled: bool) -> bool:
    stage = _get_stage()
    if stage is None:
        return False
    prim = stage.GetPrimAtPath(shader_path)
    if not prim or not prim.IsValid():
        return False
    attr_name = _resolve_attr_name(shader_path)
    if not attr_name:
        return False
    try:
        if attr_name.startswith("inputs:"):
            input_name = attr_name.split(":", 1)[1]
            shader = UsdShade.Shader(prim)
            inp = shader.GetInput(input_name)
            if inp is None:
                inp = shader.CreateInput(input_name, Sdf.ValueTypeNames.Bool)
            inp.Set(bool(enabled))
            return True
        attr = prim.GetAttribute(attr_name)
        if not attr or not attr.IsValid():
            attr = prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.Bool)
        attr.Set(bool(enabled))
        return True
    except Exception as exc:
        print(
            f"{_PRINT_PREFIX} emission set failed {shader_path} {attr_name}: {exc}",
            flush=True,
        )
        return False


def _apply_random_choice() -> None:
    paths = _shader_paths()
    if len(paths) < 1:
        return
    choices = list(range(len(paths)))
    last = int(_state.get("last_active_index", -1))
    if len(choices) > 1 and last in choices:
        choices = [i for i in choices if i != last] or list(range(len(paths)))
    active = random.choice(choices)
    _state["last_active_index"] = active
    for i, path in enumerate(paths):
        _set_shader_emission(path, enabled=(i == active))
    print(
        f"{_PRINT_PREFIX} emission ON index={active} path={paths[active]}",
        flush=True,
    )


def _cancel_timer() -> None:
    t = _state.get("timer")
    _state["timer"] = None
    if t is not None:
        try:
            t.cancel()
        except Exception:
            pass


def _schedule_next_tick() -> None:
    with _lock:
        if not _state.get("running"):
            return
        lo, hi = _interval_bounds()
        delay = random.uniform(lo, hi)

        def _on_timer() -> None:
            with _lock:
                if not _state.get("running"):
                    return
            try:
                from .lam_sequence_engine import _dispatch_main  # type: ignore

                _dispatch_main(_apply_random_choice)
            except Exception as exc:
                print(f"{_PRINT_PREFIX} tick failed: {exc}", flush=True)
            _schedule_next_tick()

        timer = threading.Timer(delay, _on_timer)
        timer.daemon = True
        _cancel_timer()
        _state["timer"] = timer
        timer.start()


def start_traffic_light_emissive_timer(*, reason: str = "") -> None:
    """랜덤 emission 타이머 시작 (이미 동작 중이면 무시)."""
    if not _config_enabled():
        return
    with _lock:
        if _state.get("running"):
            return
        paths = _shader_paths()
        if not paths:
            print(f"{_PRINT_PREFIX} shader 경로 없음 — 타이머 미시작", flush=True)
            return
        _state["running"] = True
    tag = f" ({reason})" if reason else ""
    print(f"{_PRINT_PREFIX} 타이머 시작{tag}", flush=True)
    try:
        from .lam_sequence_engine import _dispatch_main  # type: ignore

        _dispatch_main(_apply_random_choice)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} initial tick failed: {exc}", flush=True)
    _schedule_next_tick()


def stop_traffic_light_emissive_timer(*, reason: str = "") -> None:
    """타이머 중지 — 마지막 emission 상태 유지."""
    with _lock:
        if not _state.get("running"):
            return
        _state["running"] = False
        _cancel_timer()
    tag = f" ({reason})" if reason else ""
    print(f"{_PRINT_PREFIX} 타이머 정지{tag}", flush=True)


def shutdown_traffic_light_emissive() -> None:
    """확장 종료 시 타이머·구독 정리."""
    stop_traffic_light_emissive_timer(reason="shutdown")
    sub = _state.get("startup_sub")
    _state["startup_sub"] = None
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass


def schedule_traffic_light_emissive_after_stage_ready(*, delay_frames: int = 16) -> None:
    """stage 준비 후 타이머 시작 (ONLY_DURING_PLAYBACK=False 일 때)."""
    if not _config_enabled() or _only_during_playback():
        return
    frames_left = [max(0, int(delay_frames))]

    def _tick(_e=None) -> None:
        if frames_left[0] > 0:
            frames_left[0] -= 1
            return
        try:
            sub = _state.get("startup_sub")
            if sub is not None:
                sub.unsubscribe()
                _state["startup_sub"] = None
        except Exception:
            pass
        start_traffic_light_emissive_timer(reason="stage_ready")

    try:
        import omni.kit.app as _app  # type: ignore

        stream = _app.get_app().get_post_update_event_stream()
        _state["startup_sub"] = stream.create_subscription_to_pop(
            _tick,
            name="morph.lam_control.traffic_light_emissive_startup",
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} startup schedule failed: {exc}", flush=True)


def on_csv_playback_started() -> None:
    """CSV 재생 시작 — playback-only 모드면 타이머 시작."""
    if not _config_enabled() or not _only_during_playback():
        return
    start_traffic_light_emissive_timer(reason="csv_play_start")


def on_csv_playback_paused_or_stopped() -> None:
    """정지·일시정지·시뮬 종료 — playback-only 모드면 타이머 정지."""
    if not _only_during_playback():
        return
    stop_traffic_light_emissive_timer(reason="csv_play_pause_or_stop")


__all__ = [
    "on_csv_playback_paused_or_stopped",
    "on_csv_playback_started",
    "schedule_traffic_light_emissive_after_stage_ready",
    "shutdown_traffic_light_emissive",
    "start_traffic_light_emissive_timer",
    "stop_traffic_light_emissive_timer",
]
