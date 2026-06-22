"""2~4 화면 시뮬 독립성 진단 로그.

Console 필터: ``[TBS/multi-diag]``

분할 뷰(N>1)에서만 기본 활성. 단일 화면에서도 강제하려면
``ext._sim_multi_diag_force = True`` 를 설정한다.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

_PREFIX = "[TBS/multi-diag]"
_session_active = False


def set_session_active(active: bool) -> None:
    global _session_active
    _session_active = bool(active)


def enabled(ext: Any = None) -> bool:
    if _session_active:
        return True
    if ext is not None:
        try:
            if bool(getattr(ext, "_sim_multi_diag_force", False)):
                return True
        except Exception:
            pass
        try:
            n = int(getattr(ext, "_sim_viewport_split_count", 1) or 1)
            if n > 1:
                return True
        except Exception:
            pass
    return False


def _log(msg: str, *, ext: Any = None) -> None:
    if ext is not None and not enabled(ext):
        return
    print(f"{_PREFIX} {msg}", flush=True)


def usd_context_for_screen(ext: Any, screen: int) -> Optional[str]:
    try:
        s = int(screen)
    except Exception:
        s = 1
    if s <= 1:
        return None
    try:
        names = list(getattr(ext, "_sim_multi_context_names", []) or [])
    except Exception:
        names = []
    idx = s - 2
    if 0 <= idx < len(names):
        nm = str(names[idx] or "").strip()
        return nm if nm else None
    return f"morph_tbs_split_aux_{s - 1}"


def _obj_tag(obj: Any) -> str:
    if obj is None:
        return "None"
    return f"{type(obj).__name__}@{id(obj) & 0xFFFF:04x}"


def runtime_line(ext: Any, screen: int) -> str:
    try:
        from .tbs_split_composed_loader import get_split_runtime_for_screen

        rt = get_split_runtime_for_screen(ext, int(screen))
    except Exception:
        rt = None
    ctx = usd_context_for_screen(ext, int(screen))
    if rt is None:
        return f"screen={screen} ctx={ctx!r} runtime=MISSING(fallback_main)"
    try:
        n_inst = len(list(rt.registry.all_instances())) if rt.registry is not None else 0
    except Exception:
        n_inst = -1
    return (
        f"screen={screen} ctx={ctx!r} "
        f"reg={_obj_tag(rt.registry)} sch={_obj_tag(rt.scheduler)} "
        f"ev={_obj_tag(rt.evaluator)} instances={n_inst}"
    )


def snapshot_line(snap: Optional[Dict[str, Any]], screen: int) -> str:
    s = snap if isinstance(snap, dict) else {}
    try:
        ep_idx = int(s.get("ep_count_idx", 0) or 0)
    except Exception:
        ep_idx = 0
    ep_n = 3 if ep_idx else 2
    ports: List[str] = []
    for key, label in (
        ("init_inout", "INOUT"),
        ("init_bp1", "BP1"),
        ("init_bp2", "BP2"),
        ("init_bp3", "BP3"),
        ("init_bp4", "BP4"),
        ("init_ep1", "EP1"),
        ("init_ep2", "EP2"),
        ("init_ep3", "EP3"),
    ):
        if bool(s.get(key)):
            ports.append(label)
    def _rng(a: str, b: str, da: float, db: float) -> str:
        try:
            return f"{float(s.get(a, da)):.1f}-{float(s.get(b, db)):.1f}"
        except Exception:
            return f"{da:.1f}-{db:.1f}"

    lot = s.get("lot_count", "?")
    spawn = _rng("spawn_min", "spawn_max", 5.0, 10.0)
    pickup = _rng("pue_min", "pue_max", 50.0, 70.0)
    foup = _rng("foup_proc_min", "foup_proc_max", 30.0, 60.0)
    saved = "saved" if bool(s.get("_saved")) else "live/default"
    return (
        f"screen={screen} [{saved}] lot={lot} EP{ep_n} "
        f"spawn={spawn}s pickup={pickup}s foup={foup}s "
        f"init_ports={','.join(ports) or '-'}"
    )


def runner_state_line(ext: Any, screen: int, runner: Any = None) -> str:
    scr_s = str(int(screen))
    if runner is None:
        try:
            runners = getattr(ext, "_sim_runners_by_screen", None)
            runner = runners.get(scr_s) if isinstance(runners, dict) else None
        except Exception:
            runner = None
    lam_run = False
    leg_run = False
    if runner is not None:
        try:
            lam_run = bool(getattr(runner, "_lam_running", False))
        except Exception:
            pass
        try:
            leg_run = bool(getattr(runner, "is_running", lambda: False)())
        except Exception:
            pass
    pending_n = 0
    try:
        pending_by = getattr(ext, "_sim_anim_pending_by_screen", None)
        if isinstance(pending_by, dict):
            pending_n = len(pending_by.get(scr_s, []) or [])
    except Exception:
        pass
    active = False
    try:
        active_by = getattr(ext, "_sim_anim_active_by_screen", None)
        act = active_by.get(scr_s) if isinstance(active_by, dict) else None
        active = bool(isinstance(act, dict) and act)
    except Exception:
        pass
    reg_tag = _obj_tag(getattr(runner, "_tbs_registry", None) if runner else None)
    sch_tag = _obj_tag(getattr(runner, "_tbs_scheduler", None) if runner else None)
    return (
        f"screen={screen} runner={_obj_tag(runner)} lam_running={lam_run} "
        f"is_running={leg_run} pending={pending_n} active_job={active} "
        f"runner_reg={reg_tag} runner_sch={sch_tag}"
    )


def motion_busy_line(ext: Any, screen: int) -> str:
    """해당 화면 컨텍스트에서 lam MOVE/ROTATE 가 살아 있는지 요약."""
    ctx = usd_context_for_screen(ext, int(screen))
    tx_n = 0
    rot_n = 0
    replay_n = 0
    legacy_n = 0
    try:
        from . import tbs_lam_translate_animation as _ltx
        from . import tbs_lam_rotate_animation as _lrx
        from .translate_animation import count_translate_animations_for_context

        target = str(ctx or "").strip()
        for state in list(getattr(_ltx, "_animations", {}).values()):
            if not isinstance(state, dict):
                continue
            c = str(state.get("usd_context_name") or "").strip()
            if target:
                if c == target:
                    tx_n += 1
            elif not c:
                tx_n += 1
        for state in list(getattr(_lrx, "_rot_animations", {}).values()):
            if not isinstance(state, dict):
                continue
            c = str(state.get("usd_context_name") or "").strip()
            if target:
                if c == target:
                    rot_n += 1
            elif not c:
                rot_n += 1
        legacy_n = int(count_translate_animations_for_context(ctx))
    except Exception:
        pass
    try:
        from .tbs_split_composed_loader import get_split_runtime_for_screen

        rt = get_split_runtime_for_screen(ext, int(screen))
        if rt is not None and rt.registry is not None:
            for inst in rt.registry.all_instances():
                try:
                    if str(getattr(inst, "state", "") or "") == "playing":
                        replay_n += 1
                except Exception:
                    pass
    except Exception:
        pass
    return f"screen={screen} ctx={ctx!r} lam_tx={tx_n} lam_rot={rot_n} legacy_tx={legacy_n} replay_playing={replay_n}"


def log_runtime_registered(ext: Any, screen: int) -> None:
    if not enabled(ext):
        return
    _log(f"RUNTIME_REGISTERED {runtime_line(ext, screen)}", ext=ext)


def log_sim_start_multi(
    ext: Any,
    *,
    n_ch: int,
    run_gen: int,
    snaps: List[Any],
    engines: List[Any],
) -> None:
    if int(n_ch) > 1:
        set_session_active(True)
    if not enabled(ext):
        return
    _log(f"=== SIM_START gen={run_gen} channels={n_ch} ===", ext=ext)
    for i in range(int(n_ch)):
        scr = i + 1
        snap = snaps[i] if i < len(snaps) else None
        _log(snapshot_line(snap if isinstance(snap, dict) else None, scr), ext=ext)
        _log(runtime_line(ext, scr), ext=ext)
        eng = engines[i] if i < len(engines) else None
        te = 0.0
        try:
            te = float(getattr(eng, "_sim_total_est_sec", 0.0) or 0.0)
        except Exception:
            pass
        _log(
            f"screen={scr} engine={_obj_tag(eng)} tag=tbs_sim_screen:{scr} "
            f"total_est={te:.1f}s",
            ext=ext,
        )
    ctx_names = list(getattr(ext, "_sim_multi_context_names", []) or [])
    _log(f"context_names={ctx_names}", ext=ext)


def log_prerun_done(ext: Any, results: Dict[int, Any]) -> None:
    if not enabled(ext):
        return
    _log("=== PRERUN_DONE ===", ext=ext)
    for scr in sorted(results.keys()):
        res = results.get(scr)
        if res is None:
            continue
        try:
            n_items = len(getattr(res, "items", ()) or ())
        except Exception:
            n_items = -1
        try:
            anim_n = sum(
                1
                for it in (getattr(res, "items", ()) or ())
                if str(getattr(it, "kind", "") or "") == "event"
                and isinstance(getattr(it, "payload", None), dict)
                and str((it.payload or {}).get("seq", "") or "").strip()
            )
        except Exception:
            anim_n = -1
        _log(
            f"screen={scr} final_t={float(getattr(res, 'final_sim_time', 0.0) or 0.0):.2f}s "
            f"total_est={float(getattr(res, 'total_est_sec', 0.0) or 0.0):.1f}s "
            f"timeline_items={n_items} event_items≈{anim_n}",
            ext=ext,
        )


def log_anim_dispatch(
    ext: Any,
    *,
    screen: int,
    sim_time: str,
    event: str,
    file_name: str,
    est_total: float,
    runner_busy: bool,
    decision: str,
) -> None:
    if not enabled(ext):
        return
    _log(
        f"ANIM_DISPATCH screen={screen} t={sim_time} event={event} file={file_name} "
        f"est={est_total:.2f}s runner_busy={runner_busy} -> {decision}",
        ext=ext,
    )
    _log(runner_state_line(ext, screen), ext=ext)
    _log(motion_busy_line(ext, screen), ext=ext)


def log_anim_start(
    ext: Any,
    *,
    screen: int,
    ctx: Optional[str],
    file_name: str,
    est_total: float,
    eff_sp: float,
    proc_sec: float,
    runner: Any,
) -> None:
    if not enabled(ext):
        return
    _log(
        f"ANIM_START screen={screen} ctx={ctx!r} file={file_name} "
        f"est={est_total:.2f}s proc={proc_sec:.2f}s eff_speed={eff_sp:.2f}",
        ext=ext,
    )
    _log(runtime_line(ext, screen), ext=ext)
    _log(runner_state_line(ext, screen, runner), ext=ext)


def log_anim_reset(
    ext: Any,
    *,
    screen: int,
    ctx: Optional[str],
    motion_only: bool,
    runner_was_running: bool,
    path_count: int,
    reason: str,
) -> None:
    if not enabled(ext):
        return
    _log(
        f"ANIM_RESET screen={screen} ctx={ctx!r} motion_only={motion_only} "
        f"runner_was_running={runner_was_running} paths={path_count} reason={reason}",
        ext=ext,
    )
    _log(motion_busy_line(ext, screen), ext=ext)


def log_anim_done(
    ext: Any,
    *,
    screen: int,
    file_name: str,
    pending_left: int,
    next_file: str,
) -> None:
    if not enabled(ext):
        return
    nxt = next_file if next_file else "-"
    _log(
        f"ANIM_DONE screen={screen} file={file_name} pending_left={pending_left} "
        f"next={nxt}",
        ext=ext,
    )
    _log(motion_busy_line(ext, screen), ext=ext)


def log_scheduler_stop(
    *,
    ctx: Optional[str],
    scheduler: Any,
    motion_only: bool,
    reason: str,
) -> None:
    if not enabled():
        return
    _log(
        f"SCHEDULER_STOP ctx={ctx!r} sch={_obj_tag(scheduler)} "
        f"motion_only={motion_only} reason={reason}",
    )


def log_channel_stop(
    *,
    ctx: Optional[str],
    reason: str,
    preserve_foup: bool = False,
) -> None:
    if not enabled():
        return
    _log(
        f"CHANNEL_STOP ctx={ctx!r} preserve_foup={preserve_foup} reason={reason}",
        ext=ext,
    )


def log_interrupt(ext: Any, *, screen: Optional[str], reason: str) -> None:
    if not enabled(ext):
        return
    scr = str(screen or "ALL")
    _log(f"INTERRUPT screen={scr} reason={reason}", ext=ext)
    if screen is not None:
        try:
            si = int(str(screen).strip() or "1")
            _log(runner_state_line(ext, si), ext=ext)
            _log(motion_busy_line(ext, si), ext=ext)
        except Exception:
            pass


def log_lam_run(
    ext: Any,
    *,
    phase: str,
    ctx: Optional[str],
    steps: int,
    speed: float,
    detail: str = "",
) -> None:
    if not enabled(ext):
        return
    extra = f" {detail}" if detail else ""
    _log(
        f"LAM_{phase} ctx={ctx!r} steps={steps} speed={speed:.2f}{extra}",
        ext=ext,
    )


def log_lam_stop_step(ext: Any, *, ctx: Optional[str], step_idx: int, reason: str) -> None:
    if not enabled(ext):
        return
    _log(f"LAM_STOP ctx={ctx!r} at_step={step_idx} reason={reason}", ext=ext)


def log_lam_motion_timeout(
    ext: Any,
    *,
    ctx: Optional[str],
    tx: int,
    rot: int,
    replay: int,
) -> None:
    if not enabled(ext):
        return
    _log(
        f"LAM_MOTION_TIMEOUT ctx={ctx!r} tx={tx} rot={rot} replay={replay} "
        f"(thread proceeds; visual may still run)",
        ext=ext,
    )


def log_motion_drain_timeout(ext: Any, *, screen: int, ctx: Optional[str]) -> None:
    if not enabled(ext):
        return
    _log(
        f"MOTION_DRAIN_TIMEOUT screen={screen} ctx={ctx!r} — force stop_channel",
        ext=ext,
    )
    _log(motion_busy_line(ext, screen), ext=ext)


def log_runner_preempt(ext: Any, *, screen: int, ctx: Optional[str]) -> None:
    if not enabled(ext):
        return
    _log(
        f"RUNNER_PREEMPT screen={screen} ctx={ctx!r} "
        f"(alive lam_thread stopped before new run)",
        ext=ext,
    )
    _log(motion_busy_line(ext, screen), ext=ext)


_tick_last: Dict[str, float] = {}


def log_tick_heartbeat(ext: Any, *, screen: int, sim: Any) -> None:
    """화면별 tick 워커 — 15초마다 1회."""
    if not enabled(ext):
        return
    key = str(int(screen))
    now = time.monotonic()
    if now - _tick_last.get(key, 0.0) < 15.0:
        return
    _tick_last[key] = now
    sim_t = 0.0
    done = False
    try:
        sim_t = float(getattr(getattr(sim, "env", None), "now", 0.0) or 0.0)
    except Exception:
        pass
    try:
        done = bool(getattr(sim, "is_done", False))
    except Exception:
        pass
    _log(
        f"TICK_HEARTBEAT screen={screen} sim_t={sim_t:.2f}s done={done}",
        ext=ext,
    )
    _log(runner_state_line(ext, screen), ext=ext)


__all__ = [
    "enabled",
    "set_session_active",
    "log_anim_dispatch",
    "log_anim_done",
    "log_anim_reset",
    "log_anim_start",
    "log_runtime_registered",
    "log_scheduler_stop",
    "log_channel_stop",
    "log_interrupt",
    "log_lam_motion_timeout",
    "log_motion_drain_timeout",
    "log_lam_run",
    "log_lam_stop_step",
    "log_prerun_done",
    "log_runner_preempt",
    "log_sim_start_multi",
    "log_tick_heartbeat",
    "motion_busy_line",
    "runtime_line",
    "runner_state_line",
    "snapshot_line",
    "usd_context_for_screen",
]
