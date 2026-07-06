"""
HyView / livestream 세션 — 진단 로그·layout lock·창 resize 훅 (단일 모듈).

- ``[HyView/bridge]`` : T2V bridge queued / work_start / work_done / watchdog
- ``[HyView/stream]`` : allowDynamicResize, layout_lock, fill_frame·resize SKIP
- streaming Kit 창 resize 시 fill_frame 재적용 (layout lock 전·후 정책)
"""

from __future__ import annotations

import time
from typing import Any, Optional

import carb.settings
import omni.kit.app as kit_app

from .sim_control_defaults import (
    HYVIEW_BRIDGE_DIAG_ENABLED,
    HYVIEW_BRIDGE_WATCHDOG_SEC,
    HYVIEW_STREAM_LOCK_LAYOUT,
    STREAMING_ALLOW_DYNAMIC_RESIZE,
)

_REQ_COUNTER = 0


# ---------------------------------------------------------------------------
# Bridge 진단 로그
# ---------------------------------------------------------------------------


def bridge_diag_enabled() -> bool:
    return bool(HYVIEW_BRIDGE_DIAG_ENABLED)


def bridge_now() -> float:
    return time.monotonic()


def _emit(line: str) -> None:
    print(line, flush=True)
    try:
        import carb

        carb.log_info(line)
    except Exception:
        pass


def bridge_log(req_id: str, phase: str, **fields: Any) -> None:
    if not bridge_diag_enabled():
        return
    parts = [f"[HyView/bridge] {req_id} {phase}"]
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    _emit(" ".join(parts))


def bridge_queued(op: str, **ctx: Any) -> str:
    global _REQ_COUNTER
    _REQ_COUNTER += 1
    req_id = f"{op}-{_REQ_COUNTER}"
    bridge_log(req_id, "queued", op=op, t=bridge_now(), **ctx)
    return req_id


def bridge_work_start(req_id: str, op: str) -> float:
    t0 = bridge_now()
    bridge_log(req_id, "work_start", op=op, t=t0)
    return t0


def bridge_work_done(req_id: str, op: str, t_start: float) -> None:
    bridge_log(req_id, "work_done", op=op, dt_sec=f"{bridge_now() - t_start:.3f}")


def bridge_watchdog(req_id: str, message: str) -> None:
    bridge_log(req_id, "watchdog", msg=message, t=bridge_now())


def bridge_stream_skip(hook: str, reason: str, **fields: Any) -> None:
    if not bridge_diag_enabled():
        return
    parts = [f"[HyView/stream] {hook} SKIP", f"reason={reason}"]
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    _emit(" ".join(parts))


# ---------------------------------------------------------------------------
# Streaming layout lock · allowDynamicResize
# ---------------------------------------------------------------------------


def apply_streaming_livestream_settings() -> None:
    """streaming Kit 시작 시 livestream dynamic resize 비활성."""
    if bool(STREAMING_ALLOW_DYNAMIC_RESIZE):
        return
    try:
        import carb

        settings = carb.settings.get_settings()
        if settings is None:
            return
        settings.set(
            "/exts/omni.kit.livestream.app/primaryStream/allowDynamicResize",
            False,
        )
        carb.log_info(
            "[HyView/stream] allowDynamicResize=false applied "
            "(/exts/omni.kit.livestream.app/primaryStream/allowDynamicResize)"
        )
        print("[HyView/stream] allowDynamicResize=false applied", flush=True)
    except Exception as exc:
        try:
            import carb

            carb.log_warn(f"[HyView/stream] allowDynamicResize apply failed: {exc}")
        except Exception:
            pass


def enable_hyview_stream_layout_lock(ext: Any) -> None:
    if not bool(HYVIEW_STREAM_LOCK_LAYOUT):
        return
    ext._hyview_stream_lock_layout = True
    try:
        import carb

        carb.log_info("[HyView/stream] layout_lock=ON (resize/fill_frame guards active)")
        print("[HyView/stream] layout_lock=ON", flush=True)
    except Exception:
        pass


def is_hyview_stream_layout_locked(ext: Optional[Any] = None) -> bool:
    if not bool(HYVIEW_STREAM_LOCK_LAYOUT):
        return False
    if ext is None:
        try:
            from .tbs_extension_singleton import require_tbs_extension_instance

            ext = require_tbs_extension_instance()
        except Exception:
            return False
    return bool(getattr(ext, "_hyview_stream_lock_layout", False))


# ---------------------------------------------------------------------------
# 창 resize 훅
# ---------------------------------------------------------------------------


def _is_streaming_deployment() -> bool:
    try:
        settings = carb.settings.get_settings()
        if settings and bool(settings.get("/app/morph/streamingUi")):
            return True
    except Exception:
        pass
    try:
        em = kit_app.get_app().get_extension_manager()
        if em is not None and em.is_extension_enabled("omni.kit.livestream.app"):
            return True
    except Exception:
        pass
    return False


def _on_app_window_resize(ext: Any, _event: Any) -> None:
    if is_hyview_stream_layout_locked(ext):
        bridge_stream_skip("window_resize", "layout_locked")
        return
    try:
        from . import sim_multi_view as smv
        from .kit_chrome_visibility import apply_viewport_dock_tab_bars_hidden

        sn = int(getattr(ext, "_sim_viewport_split_count", 1) or 1)
        smv.set_viewport_fill_frame_for_split_count(sn, True)
        apply_viewport_dock_tab_bars_hidden()
        if sn >= 2:
            smv.apply_viewport_split_tab_chrome(sn)
    except Exception:
        pass


def install_streaming_window_resize_hooks(ext: Any) -> None:
    if not _is_streaming_deployment():
        return
    sub = getattr(ext, "_streaming_resize_sub", None)
    if sub is not None:
        return
    try:
        import omni.appwindow

        factory = omni.appwindow.acquire_app_window_factory_interface()
        aw = factory.get_default_app_window()
        ext._streaming_resize_sub = aw.get_window_resize_event_stream().create_subscription_to_pop(
            lambda e, _ext=ext: _on_app_window_resize(_ext, e),
            name="morph.tbs_control_2:streaming_window_resize",
        )
    except Exception:
        ext._streaming_resize_sub = None


def teardown_streaming_window_hooks(ext: Any) -> None:
    sub = getattr(ext, "_streaming_resize_sub", None)
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
    ext._streaming_resize_sub = None


__all__ = [
    "HYVIEW_BRIDGE_WATCHDOG_SEC",
    "apply_streaming_livestream_settings",
    "bridge_diag_enabled",
    "bridge_log",
    "bridge_now",
    "bridge_queued",
    "bridge_stream_skip",
    "bridge_watchdog",
    "bridge_work_done",
    "bridge_work_start",
    "enable_hyview_stream_layout_lock",
    "install_streaming_window_resize_hooks",
    "is_hyview_stream_layout_locked",
    "teardown_streaming_window_hooks",
]
