"""분할 시뮬 화면별 USD 컨텍스트 스코프 유틸."""

from __future__ import annotations

import threading
import time
from typing import Any, List, Optional


def stop_channel_animations(
    usd_context_name: Optional[str],
    *,
    preserve_foup_port_lot_prims: bool = False,
    diag_reason: str = "",
) -> None:
    """한 USD 컨텍스트의 MOVE/ROTATE 애니만 중지. ``None`` = 메인(default) 컨텍스트."""
    try:
        from . import sim_multi_diag as _mdiag

        _mdiag.log_channel_stop(
            ctx=usd_context_name,
            reason=str(diag_reason or "stop_channel_animations"),
            preserve_foup=bool(preserve_foup_port_lot_prims),
        )
    except Exception:
        pass
    try:
        from . import tbs_lam_rotate_animation as lam_rx
        from . import tbs_lam_translate_animation as lam_tx

        lam_tx.stop_translate_animations_for_context(usd_context_name)
        lam_rx.stop_rotate_animations_for_context(usd_context_name)
    except Exception:
        pass
    try:
        from . import translate_animation as leg_tx

        leg_tx.stop_translate_animations_for_context(
            usd_context_name,
            preserve_foup_port_lot_prims=preserve_foup_port_lot_prims,
        )
    except Exception:
        pass
    target = str(usd_context_name or "").strip()
    if not target:
        try:
            from .rotate_animation import stop_all_rotate_animations

            stop_all_rotate_animations()
        except Exception:
            pass
        try:
            from .curve_animation import stop_all_curve_animations

            stop_all_curve_animations()
        except Exception:
            pass


def _legacy_translate_busy(usd_context_name: Optional[str]) -> bool:
    try:
        from .translate_animation import is_translate_animation_running_for_context

        return bool(is_translate_animation_running_for_context(usd_context_name))
    except Exception:
        return False


def _lam_motion_busy(usd_context_name: Optional[str]) -> bool:
    target = str(usd_context_name or "").strip()
    try:
        from . import tbs_lam_rotate_animation as lam_rx
        from . import tbs_lam_translate_animation as lam_tx

        for state in list(getattr(lam_tx, "_animations", {}).values()):
            if not isinstance(state, dict):
                continue
            ctx = str(state.get("usd_context_name") or "").strip()
            if target:
                if ctx == target:
                    return True
            elif not ctx:
                return True
        for state in list(getattr(lam_rx, "_rot_animations", {}).values()):
            if not isinstance(state, dict):
                continue
            ctx = str(state.get("usd_context_name") or "").strip()
            if target:
                if ctx == target:
                    return True
            elif not ctx:
                return True
    except Exception:
        pass
    return False


def _replay_busy(registry: Any) -> bool:
    if registry is None or not hasattr(registry, "all_instances"):
        return False
    try:
        for inst in registry.all_instances():
            if str(getattr(inst, "state", "") or "") == "playing":
                return True
    except Exception:
        pass
    return False


def is_channel_motion_busy(
    usd_context_name: Optional[str],
    registry: Any = None,
) -> bool:
    """해당 USD 컨텍스트에서 lam/legacy MOVE·ROTATE 또는 TIMESAMPLES replay 가 진행 중인지."""
    if _lam_motion_busy(usd_context_name):
        return True
    if _legacy_translate_busy(usd_context_name):
        return True
    return _replay_busy(registry)


def _motion_busy_on_main_impl(
    usd_context_name: Optional[str],
    registry: Any,
    *,
    translate_paths: List[str],
    rotate_paths: List[str],
    replay_prims: List[str],
    check_all_channel: bool,
) -> bool:
    """main thread 전용 — lam/legacy/replay busy 판정."""
    ctx_nm = usd_context_name
    busy = False
    try:
        from . import tbs_lam_rotate_animation as lam_rx
        from . import tbs_lam_translate_animation as lam_tx

        for p in translate_paths:
            if lam_tx.is_prim_translate_animation_running(p, ctx_nm):
                busy = True
                break
        if not busy:
            for p in rotate_paths:
                if lam_rx.is_prim_rotate_animation_running(p, ctx_nm):
                    busy = True
                    break
    except Exception:
        pass
    if not busy and replay_prims and registry is not None:
        try:
            for rp in replay_prims:
                inst = registry.get_by_prim_path(rp)
                if inst is not None and str(getattr(inst, "state", "") or "") == "playing":
                    busy = True
                    break
        except Exception:
            pass
    if check_all_channel:
        if not busy and registry is not None and _replay_busy(registry):
            busy = True
        if not busy and _lam_motion_busy(ctx_nm):
            busy = True
        if not busy and _legacy_translate_busy(ctx_nm):
            busy = True
    return busy


def probe_channel_motion_busy_on_main(
    usd_context_name: Optional[str],
    registry: Any = None,
    *,
    translate_paths: Optional[List[str]] = None,
    rotate_paths: Optional[List[str]] = None,
    replay_prims: Optional[List[str]] = None,
    check_all_channel: bool = True,
) -> bool:
    """다음 main update 에서 motion busy 1회 샘플(BG thread 안전)."""
    tx_paths = list(translate_paths or [])
    rot_paths = list(rotate_paths or [])
    replay_paths = list(replay_prims or [])

    if threading.current_thread() is threading.main_thread():
        return _motion_busy_on_main_impl(
            usd_context_name,
            registry,
            translate_paths=tx_paths,
            rotate_paths=rot_paths,
            replay_prims=replay_paths,
            check_all_channel=bool(check_all_channel),
        )

    holder: dict = {"busy": True}

    def _check_on_main() -> None:
        holder["busy"] = _motion_busy_on_main_impl(
            usd_context_name,
            registry,
            translate_paths=tx_paths,
            rotate_paths=rot_paths,
            replay_prims=replay_paths,
            check_all_channel=bool(check_all_channel),
        )

    try:
        from .tbs_lam_sequence_engine import _dispatch_main_wait

        _dispatch_main_wait(_check_on_main, timeout=2.0)
    except Exception:
        holder["busy"] = is_channel_motion_busy(usd_context_name, registry)
    return bool(holder.get("busy", False))


def wait_channel_motion_idle(
    usd_context_name: Optional[str],
    registry: Any = None,
    *,
    max_sec: float = 30.0,
    stop_flag: Any = None,
    translate_paths: Optional[List[str]] = None,
    rotate_paths: Optional[List[str]] = None,
    replay_prims: Optional[List[str]] = None,
) -> bool:
    """lam/legacy/replay 가 모두 멈출 때까지 폴링. True=idle, False=timeout/stop.

    Kit main thread 에서 inst.state·애니 dict 를 읽어 BG thread 레이스를 피한다.
    """
    deadline = time.monotonic() + max(0.5, float(max_sec))
    poll = 0.033
    tx_paths = list(translate_paths or [])
    rot_paths = list(rotate_paths or [])
    replay_paths = list(replay_prims or [])
    ctx_nm = usd_context_name

    def _stopped() -> bool:
        if stop_flag is not None:
            try:
                if stop_flag.is_set():
                    return True
            except Exception:
                pass
        return False

    scoped_all = not tx_paths and not rot_paths and not replay_paths

    while not _stopped():
        try:
            from .tbs_main_dispatch import get_pending_dispatch_count

            if get_pending_dispatch_count(ctx_nm) > 0:
                busy = True
            else:
                busy = probe_channel_motion_busy_on_main(
                    ctx_nm,
                    registry,
                    translate_paths=tx_paths,
                    rotate_paths=rot_paths,
                    replay_prims=replay_paths,
                    check_all_channel=scoped_all,
                )
        except Exception:
            busy = probe_channel_motion_busy_on_main(
                ctx_nm,
                registry,
                translate_paths=tx_paths,
                rotate_paths=rot_paths,
                replay_prims=replay_paths,
                check_all_channel=scoped_all,
            )
        if not busy:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)
    return False


def drain_channel_motion_complete(
    usd_context_name: Optional[str],
    registry: Any = None,
    *,
    max_sec: float = 30.0,
    stable_ticks: int = 3,
) -> bool:
    """dispatch 큐 + lam/legacy/replay 가 연속 idle 일 때까지 drain. True=idle."""
    deadline = time.monotonic() + max(0.5, float(max_sec))
    poll = 0.033
    ctx_nm = usd_context_name
    need_stable = max(1, int(stable_ticks))
    stable = 0

    while time.monotonic() < deadline:
        pending = 0
        try:
            from .tbs_main_dispatch import get_pending_dispatch_count

            pending = int(get_pending_dispatch_count(ctx_nm))
        except Exception:
            pending = 0
        busy = pending > 0
        if not busy:
            busy = probe_channel_motion_busy_on_main(
                ctx_nm,
                registry,
                check_all_channel=True,
            )
        if not busy:
            stable += 1
            if stable >= need_stable:
                return True
        else:
            stable = 0
        time.sleep(poll)
    return False


__all__ = [
    "stop_channel_animations",
    "is_channel_motion_busy",
    "probe_channel_motion_busy_on_main",
    "wait_channel_motion_idle",
    "drain_channel_motion_complete",
]
