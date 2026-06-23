"""2화면 이상 프리런 재생 — 화면별 SimTimelinePlayer 인스턴스 (1화면 경로와 분리)."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

from .control_sim_prerun_playback import SimPreRunResult, SimTimelinePlayer


def get_sim_playback_player(ext: Any, screen: int) -> Optional[SimTimelinePlayer]:
    by = getattr(ext, "_sim_playback_players_by_screen", None)
    if isinstance(by, dict) and by:
        try:
            return by.get(int(screen))
        except Exception:
            return None
    return getattr(ext, "_sim_playback_player", None)


def is_multi_playback_instances(ext: Any) -> bool:
    by = getattr(ext, "_sim_playback_players_by_screen", None)
    return isinstance(by, dict) and len(by) > 0


def iter_sim_playback_players(ext: Any) -> Iterator[Tuple[int, SimTimelinePlayer]]:
    by = getattr(ext, "_sim_playback_players_by_screen", None)
    if isinstance(by, dict) and by:
        for sk in sorted(by.keys(), key=lambda x: int(x)):
            p = by.get(sk)
            if p is not None:
                yield int(sk), p
        return
    p = getattr(ext, "_sim_playback_player", None)
    if p is not None:
        yield 1, p


def start_multi_playback_instances(
    ext: Any,
    results: Dict[int, SimPreRunResult],
    emit_fn: Callable[[str, Any, int], None],
    speed_supplier: Callable[[], float],
    event_emit_allowed: Optional[Callable[[int], bool]] = None,
) -> None:
    players: Dict[int, SimTimelinePlayer] = {}
    for scr, rr in (results or {}).items():
        try:
            scr_i = int(scr)
        except Exception:
            continue
        if rr is None:
            continue
        p = SimTimelinePlayer(
            results_by_screen={scr_i: rr},
            emit_fn=emit_fn,
            speed_supplier=speed_supplier,
            event_emit_allowed=event_emit_allowed,
        )
        p.start()
        players[scr_i] = p
    try:
        ext._sim_playback_players_by_screen = players
        ext._sim_playback_player = None
    except Exception:
        pass
    try:
        from .tbs_main_dispatch import set_multi_instance_dispatch_mode

        set_multi_instance_dispatch_mode(True)
    except Exception:
        pass


def stop_multi_playback_instances(ext: Any) -> None:
    by = getattr(ext, "_sim_playback_players_by_screen", None)
    if isinstance(by, dict):
        for p in by.values():
            if p is not None and hasattr(p, "stop"):
                try:
                    p.stop()
                except Exception:
                    pass
    try:
        ext._sim_playback_players_by_screen = None
    except Exception:
        pass
    try:
        from .tbs_main_dispatch import set_multi_instance_dispatch_mode

        set_multi_instance_dispatch_mode(False)
    except Exception:
        pass


def tick_multi_playback(ext: Any) -> None:
    from .control_sim_timetable_ui import refresh_all_timetable_highlights

    players = getattr(ext, "_sim_playback_players_by_screen", None)
    if not isinstance(players, dict) or not players:
        return

    any_playing = False
    for _scr, player in players.items():
        if player is None:
            continue
        try:
            if getattr(player, "is_playing", lambda: False)():
                any_playing = True
                player.tick()
        except Exception:
            pass
    if not any_playing:
        return

    try:
        refresh_all_timetable_highlights(ext)
    except Exception:
        pass

    from .control_window import (
        SimUiControlAction,
        _build_playback_time_tick_payload,
        _enqueue_control_action,
        _finalize_sim_timeline_on_done,
        post_sim_progress_update,
    )

    engs = getattr(ext, "_sim_engines", None)
    results = getattr(ext, "_sim_prerun_results_by_screen", None)
    if not isinstance(engs, list):
        return

    try:
        hb = getattr(ext, "_sim_playback_timeline_hb", None)
        if not isinstance(hb, dict):
            hb = {}
            ext._sim_playback_timeline_hb = hb
        now_wall = time.perf_counter()
    except Exception:
        hb = {}
        now_wall = time.perf_counter()

    for scr_k, player in players.items():
        if player is None:
            continue
        try:
            scr = int(scr_k)
        except Exception:
            scr = 1
        idx = scr - 1
        eng = engs[idx] if 0 <= idx < len(engs) else None
        if eng is None:
            continue
        try:
            tnow = float(player.sim_now(scr))
        except Exception:
            tnow = 0.0
        try:
            if hasattr(eng, "_set_now"):
                eng._set_now(tnow)  # type: ignore[attr-defined]
            elif hasattr(eng, "env") and eng.env is not None:
                eng.env.now = float(tnow)  # type: ignore[attr-defined]
        except Exception:
            pass

        try:
            last = float(hb.get(str(scr), 0.0) or 0.0)
        except Exception:
            last = 0.0
        if (now_wall - last) >= 0.10:
            try:
                hb[str(scr)] = float(now_wall)
            except Exception:
                pass
            try:
                te = None
                if isinstance(results, dict) and results.get(int(scr)) is not None:
                    te = float(results[int(scr)].final_sim_time)
                payload = {
                    "tbs_sim_screen": str(scr),
                    "sim_time": f"{float(tnow):.6f}",
                    "timeline_only": "1",
                    "label": "EP 타임라인",
                    "detail": "",
                    "status": "RUNNING",
                    "elapsed": "0.0",
                    "total": "0.0",
                    "percent": "0",
                }
                if isinstance(te, (float, int)) and float(te) > 0.0:
                    payload["sim_total_est_sec"] = f"{float(te):.2f}"
                post_sim_progress_update(ext, payload)
            except Exception:
                pass

        try:
            last2 = float(hb.get(f"prog_{scr}", 0.0) or 0.0)
        except Exception:
            last2 = 0.0
        if (now_wall - last2) >= 0.20:
            try:
                hb[f"prog_{scr}"] = float(now_wall)
            except Exception:
                pass
            try:
                by_lp = getattr(ext, "_sim_progress_last_payload_by_screen", None)
                lp = by_lp.get(str(scr)) if isinstance(by_lp, dict) else None
                te_val = None
                try:
                    if isinstance(results, dict) and results.get(int(scr)) is not None:
                        te_val = float(results[int(scr)].final_sim_time)
                except Exception:
                    te_val = None
                p3 = _build_playback_time_tick_payload(
                    scr,
                    float(tnow),
                    lp if isinstance(lp, dict) else None,
                    final_sim_time=te_val,
                    ext=ext,
                )
                post_sim_progress_update(ext, p3)
            except Exception:
                pass

    try:
        if isinstance(results, dict) and results:
            done_all = True
            for scr_k, res in results.items():
                p = players.get(int(scr_k))
                if p is None or res is None:
                    done_all = False
                    break
                try:
                    if float(p.sim_now(int(scr_k))) < float(res.final_sim_time) - 1e-6:
                        done_all = False
                        break
                except Exception:
                    done_all = False
                    break
            if done_all and (not bool(getattr(ext, "_sim_playback_done", False))):
                ext._sim_playback_done = True
                try:
                    _finalize_sim_timeline_on_done(ext)
                except Exception:
                    pass
                for scr_k, res in results.items():
                    try:
                        scr_i = int(scr_k)
                    except Exception:
                        scr_i = 1
                    try:
                        p_done = {
                            "tbs_sim_screen": str(scr_i),
                            "sim_time": f"{float(res.final_sim_time):.2f}",
                            "sim_total_est_sec": f"{float(res.final_sim_time):.2f}",
                            "label": "완료",
                            "detail": "",
                            "status": "DONE",
                            "elapsed": f"{float(res.final_sim_time):.1f}",
                            "total": f"{float(res.final_sim_time):.1f}",
                            "percent": "100",
                        }
                        post_sim_progress_update(ext, p_done)
                    except Exception:
                        pass
                try:
                    _enqueue_control_action(ext, SimUiControlAction.EXPORT_XLSX.value)
                except Exception:
                    pass
                stop_multi_playback_instances(ext)
    except Exception:
        pass


__all__ = [
    "get_sim_playback_player",
    "is_multi_playback_instances",
    "iter_sim_playback_players",
    "start_multi_playback_instances",
    "stop_multi_playback_instances",
    "tick_multi_playback",
]
