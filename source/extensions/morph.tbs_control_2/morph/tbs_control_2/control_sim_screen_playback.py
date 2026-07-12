"""
화면별 독립 프리런 재생 런타임.

1화면·N화면 **동일 경로**: 화면당 ``ScreenPlaybackSession`` 1개
(자체 ``SimTimelinePlayer`` + heartbeat).

``control_window`` 는 UI sink·Kit 구독·프리런 bootstrap 만 담당한다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .control_sim_playback_gate import can_emit_timeline_event, clear_playback_gate_state
from .control_sim_playback_speed import (
    clear_playback_step_speed_locks,
    make_playback_speed_supplier,
)
from .control_sim_prerun_playback import SimPreRunResult, SimTimelinePlayer

EmitFn = Callable[[str, Any, int], None]
SpeedFn = Callable[[], float]
GateFn = Callable[[int], bool]
ProgressSinkFn = Callable[[Any, Dict[str, Any]], None]
TimelineOnlySinkFn = Callable[[Any, Dict[str, Any]], None]
BuildProgPayloadFn = Callable[[int, float, Optional[Dict[str, Any]], Any], Dict[str, Any]]

# 1화면 정상 동작과 동일한 heartbeat 주기
_HB_EP_INTERVAL = 0.10
_HB_PROG_INTERVAL = 0.20
_HB_PROG_INTERVAL_MULTI = 0.0


@dataclass
class ScreenPlaybackSession:
    """단일 화면 재생 단위 — 1화면·2화면 모두 이 클래스."""

    screen: int
    prerun: SimPreRunResult
    player: SimTimelinePlayer
    hb_ep_wall: float = 0.0
    hb_prog_wall: float = 0.0

    def sim_now(self) -> float:
        try:
            return float(self.player.sim_now(int(self.screen)))
        except Exception:
            return 0.0

    def is_playing(self) -> bool:
        try:
            return bool(self.player.is_playing())
        except Exception:
            return False

    def advance_clock_only(self) -> None:
        if self.is_playing():
            self.player.advance_sim_clock()

    def emit_due_and_sync(
        self,
        *,
        max_emits: int,
        sync_engine_now: Callable[[Any, int, float], None],
        ext: Any,
    ) -> None:
        if not self.is_playing():
            return
        self.player.emit_due_items(max_emits=max(4, int(max_emits)))
        tnow = self.sim_now()
        sync_engine_now(ext, int(self.screen), tnow)

    def refresh_playback_ui(
        self,
        ext: Any,
        *,
        now_wall: float,
        progress_sink: ProgressSinkFn,
        timeline_only_sink: TimelineOnlySinkFn,
        build_prog_payload: BuildProgPayloadFn,
        prog_hb_interval: Optional[float] = None,
    ) -> None:
        if not self.is_playing():
            return
        scr = int(self.screen)
        tnow = self.sim_now()
        prog_iv = _HB_PROG_INTERVAL if prog_hb_interval is None else float(prog_hb_interval)

        if (now_wall - self.hb_ep_wall) >= _HB_EP_INTERVAL:
            self.hb_ep_wall = float(now_wall)
            try:
                te = float(self.prerun.final_sim_time)
            except Exception:
                te = 0.0
            ep_payload: Dict[str, Any] = {
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
            if te > 0.0:
                ep_payload["sim_total_est_sec"] = f"{te:.2f}"
            try:
                timeline_only_sink(ext, ep_payload)
            except Exception:
                pass

        if prog_iv <= 1e-9 or (now_wall - self.hb_prog_wall) >= prog_iv:
            self.hb_prog_wall = float(now_wall)
            try:
                te_val = float(self.prerun.final_sim_time)
            except Exception:
                te_val = None
            try:
                p3 = build_prog_payload(
                    scr,
                    float(tnow),
                    None,
                    ext,
                )
                if isinstance(te_val, (float, int)) and float(te_val) > 0.0:
                    p3["sim_total_est_sec"] = f"{float(te_val):.2f}"
                progress_sink(ext, p3)
            except Exception:
                pass


@dataclass
class SimPlaybackRuntime:
    """N 화면 = N 개의 ``ScreenPlaybackSession`` (1화면도 세션 1개)."""

    sessions: Dict[int, ScreenPlaybackSession]
    emit_fn: EmitFn
    speed_fn: SpeedFn
    gate_fn: GateFn

    @classmethod
    def start(
        cls,
        ext: Any,
        results: Dict[int, SimPreRunResult],
        emit_fn: EmitFn,
        speed_fn: SpeedFn,
        gate_fn: Optional[GateFn] = None,
    ) -> SimPlaybackRuntime:
        if gate_fn is None:
            gate_fn = lambda scr: can_emit_timeline_event(ext, int(scr))

        try:
            clear_playback_gate_state(ext)
            clear_playback_step_speed_locks(ext)
        except Exception:
            pass

        sessions: Dict[int, ScreenPlaybackSession] = {}
        for scr_k, res in (results or {}).items():
            if res is None:
                continue
            try:
                scr_i = int(scr_k)
            except Exception:
                continue
            player = SimTimelinePlayer(
                results_by_screen={scr_i: res},
                emit_fn=emit_fn,
                speed_supplier=make_playback_speed_supplier(ext, scr_i),
                event_emit_allowed=gate_fn,
            )
            player.start()
            sessions[scr_i] = ScreenPlaybackSession(
                screen=scr_i,
                prerun=res,
                player=player,
            )

        rt = cls(sessions=sessions, emit_fn=emit_fn, speed_fn=speed_fn, gate_fn=gate_fn)
        _attach_runtime(ext, rt)
        if len(sessions) > 1:
            try:
                from .tbs_main_dispatch import set_multi_instance_dispatch_mode

                set_multi_instance_dispatch_mode(True)
            except Exception:
                pass
        return rt

    def stop(self) -> None:
        for sess in self.sessions.values():
            try:
                sess.player.stop()
            except Exception:
                pass
        if len(self.sessions) > 1:
            try:
                from .tbs_main_dispatch import set_multi_instance_dispatch_mode

                set_multi_instance_dispatch_mode(False)
            except Exception:
                pass

    def any_playing(self) -> bool:
        return any(s.is_playing() for s in self.sessions.values())

    def session(self, screen: int) -> Optional[ScreenPlaybackSession]:
        return self.sessions.get(int(screen))

    def get_player(self, screen: int) -> Optional[SimTimelinePlayer]:
        s = self.session(screen)
        return s.player if s else None

    def tick_all(
        self,
        ext: Any,
        *,
        max_emits_per_screen: int = 20,
        progress_sink: ProgressSinkFn,
        timeline_only_sink: TimelineOnlySinkFn,
        build_prog_payload: BuildProgPayloadFn,
        sync_engine_now: Callable[[Any, int, float], None],
        on_after_tick: Optional[Callable[[Any], None]] = None,
    ) -> None:
        if not self.any_playing():
            return
        playing = [s for s in self.sessions.values() if s.is_playing()]
        if not playing:
            return
        multi = len(playing) > 1
        prog_iv = _HB_PROG_INTERVAL_MULTI if multi else _HB_PROG_INTERVAL
        for sess in playing:
            sess.advance_clock_only()
        try:
            from .control_window import (
                _drain_playback_json_job_queues,
                _poll_playback_sim_aligned_json_starts,
                _try_release_all_playback_json_walls,
            )

            _try_release_all_playback_json_walls(ext)
            _poll_playback_sim_aligned_json_starts(ext)
            if multi:
                _drain_playback_json_job_queues(ext)
                _poll_playback_sim_aligned_json_starts(ext)
        except Exception:
            pass
        now_wall = time.perf_counter()
        if multi:
            for sess in playing:
                sess.refresh_playback_ui(
                    ext,
                    now_wall=now_wall,
                    progress_sink=progress_sink,
                    timeline_only_sink=timeline_only_sink,
                    build_prog_payload=build_prog_payload,
                    prog_hb_interval=prog_iv,
                )
        for sess in playing:
            sess.emit_due_and_sync(
                max_emits=max_emits_per_screen,
                sync_engine_now=sync_engine_now,
                ext=ext,
            )
        if not multi:
            now_wall = time.perf_counter()
            for sess in playing:
                sess.refresh_playback_ui(
                    ext,
                    now_wall=now_wall,
                    progress_sink=progress_sink,
                    timeline_only_sink=timeline_only_sink,
                    build_prog_payload=build_prog_payload,
                    prog_hb_interval=prog_iv,
                )
        if on_after_tick is not None:
            try:
                on_after_tick(ext)
            except Exception:
                pass

    def all_reached_end(self) -> bool:
        for sess in self.sessions.values():
            try:
                if float(sess.sim_now()) < float(sess.prerun.final_sim_time) - 1e-6:
                    return False
            except Exception:
                return False
        return bool(self.sessions)


def get_playback_runtime(ext: Any) -> Optional[SimPlaybackRuntime]:
    rt = getattr(ext, "_sim_playback_runtime", None)
    if rt is None:
        return None
    if isinstance(rt, SimPlaybackRuntime):
        return rt
    # Kit 핫리로드 시 클래스 identity 가 달라져도 tick_all 이 있으면 사용한다.
    if callable(getattr(rt, "tick_all", None)) and isinstance(getattr(rt, "sessions", None), dict):
        return rt  # type: ignore[return-value]
    return None


def get_sim_playback_player(ext: Any, screen: int) -> Optional[SimTimelinePlayer]:
    rt = get_playback_runtime(ext)
    if rt is not None:
        return rt.get_player(int(screen))
    by = getattr(ext, "_sim_playback_players_by_screen", None)
    if isinstance(by, dict) and by:
        try:
            return by.get(int(screen))
        except Exception:
            return None
    return getattr(ext, "_sim_playback_player", None)


def is_multi_playback_instances(ext: Any) -> bool:
    rt = get_playback_runtime(ext)
    if rt is not None:
        return len(rt.sessions) > 1
    by = getattr(ext, "_sim_playback_players_by_screen", None)
    return isinstance(by, dict) and len(by) > 0


def iter_sim_playback_players(ext: Any):
    rt = get_playback_runtime(ext)
    if rt is not None:
        for scr in sorted(rt.sessions.keys()):
            yield int(scr), rt.sessions[scr].player
        return
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


def _attach_runtime(ext: Any, rt: SimPlaybackRuntime) -> None:
    try:
        ext._sim_playback_runtime = rt
        by: Dict[int, SimTimelinePlayer] = {}
        for scr, sess in rt.sessions.items():
            by[int(scr)] = sess.player
        ext._sim_playback_players_by_screen = by if len(by) > 1 else None
        ext._sim_playback_player = by.get(1) if len(by) == 1 else None
    except Exception:
        pass


def bootstrap_playback_after_prerun(
    ext: Any,
    results: Dict[int, SimPreRunResult],
    emit_fn: EmitFn,
    speed_fn: SpeedFn,
    gate_fn: Optional[GateFn] = None,
) -> SimPlaybackRuntime:
    """프리런 완료 후 재생 런타임 기동 (1·N 화면 공통)."""
    try:
        from .control_sim_playback_plan import ensure_playback_plans_for_results

        ensure_playback_plans_for_results(ext, results)
        try:
            from .control_sim_playback_plan import refresh_playback_display_at_sim

            for scr_k in (results or {}):
                try:
                    scr_i = int(scr_k)
                except Exception:
                    continue
                refresh_playback_display_at_sim(ext, scr_i, 0.0, force=True)
        except Exception:
            pass
    except Exception:
        pass
    return SimPlaybackRuntime.start(ext, results, emit_fn, speed_fn, gate_fn)


def add_playback_sessions_after_prerun(
    ext: Any,
    new_results: Dict[int, SimPreRunResult],
    emit_fn: EmitFn,
    speed_fn: SpeedFn,
    gate_fn: Optional[GateFn] = None,
) -> SimPlaybackRuntime:
    """다른 화면이 이미 재생 중일 때 신규 화면 세션만 추가한다."""
    if not new_results:
        rt0 = get_playback_runtime(ext)
        if rt0 is not None:
            return rt0
        return bootstrap_playback_after_prerun(ext, {}, emit_fn, speed_fn, gate_fn)
    if gate_fn is None:
        gate_fn = lambda scr: can_emit_timeline_event(ext, int(scr))
    try:
        from .control_sim_playback_plan import ensure_playback_plans_for_results, refresh_playback_display_at_sim

        for scr_k in new_results:
            try:
                scr_i = int(scr_k)
            except Exception:
                continue
            plan_by = getattr(ext, "_sim_playback_plan_by_screen", None)
            if isinstance(plan_by, dict):
                plan_by.pop(str(scr_i), None)
                plan_by.pop(scr_i, None)
        ensure_playback_plans_for_results(ext, new_results)
        for scr_k in new_results:
            try:
                refresh_playback_display_at_sim(ext, int(scr_k), 0.0, force=True)
            except Exception:
                pass
    except Exception:
        pass
    rt = get_playback_runtime(ext)
    if rt is None:
        return bootstrap_playback_after_prerun(ext, new_results, emit_fn, speed_fn, gate_fn)
    for scr_k, res in new_results.items():
        if res is None:
            continue
        try:
            scr_i = int(scr_k)
        except Exception:
            continue
        prev_sess = rt.session(scr_i)
        if prev_sess is not None:
            try:
                prev_sess.player.stop()
            except Exception:
                pass
        player = SimTimelinePlayer(
            results_by_screen={scr_i: res},
            emit_fn=emit_fn,
            speed_supplier=make_playback_speed_supplier(ext, scr_i),
            event_emit_allowed=gate_fn,
        )
        player.start()
        rt.sessions[scr_i] = ScreenPlaybackSession(
            screen=scr_i,
            prerun=res,
            player=player,
        )
    _attach_runtime(ext, rt)
    if len(rt.sessions) > 1:
        try:
            from .tbs_main_dispatch import set_multi_instance_dispatch_mode

            set_multi_instance_dispatch_mode(True)
        except Exception:
            pass
    return rt


def any_playback_still_active(ext: Any) -> bool:
    """한 화면이라도 재생 중이면 True."""
    rt = get_playback_runtime(ext)
    if rt is not None:
        try:
            if rt.any_playing():
                return True
        except Exception:
            pass
    for _scr, p in iter_sim_playback_players(ext):
        try:
            if p is not None and bool(p.is_playing()):
                return True
        except Exception:
            pass
    return False


def is_simulation_in_progress(ext: Any) -> bool:
    """프리런 계산 중이거나 타임라인 재생 중이면 True (시작/play 재시작 금지)."""
    if bool(getattr(ext, "_sim_prerun_ui_busy", False)):
        return True
    return any_playback_still_active(ext)


def cleanup_playback_subscription_if_idle(ext: Any) -> None:
    """모든 화면 재생이 끝났으면 UI tick 구독·플래그를 정리한다."""
    if any_playback_still_active(ext):
        return
    try:
        sub = getattr(ext, "_sim_playback_ui_sub", None)
        if sub is not None:
            try:
                sub.unsubscribe()
            except Exception:
                pass
        ext._sim_playback_ui_sub = None
    except Exception:
        pass
    try:
        set_sim_playback_active(ext, False)
    except Exception:
        pass
    rt = get_playback_runtime(ext)
    if rt is not None:
        try:
            if not rt.sessions:
                ext._sim_playback_runtime = None
                ext._sim_playback_players_by_screen = None
                ext._sim_playback_player = None
        except Exception:
            pass


def stop_playback_for_screen(ext: Any, screen: int) -> bool:
    """단일 화면 프리런 재생만 정지(다른 화면 재생은 유지)."""
    try:
        sc = int(screen)
    except Exception:
        return False
    stopped = False
    rt = get_playback_runtime(ext)
    if rt is not None:
        sess = rt.session(sc)
        if sess is not None:
            try:
                sess.player.stop()
            except Exception:
                pass
            try:
                rt.sessions.pop(sc, None)
            except Exception:
                pass
            stopped = True
        try:
            by = getattr(ext, "_sim_playback_players_by_screen", None)
            if isinstance(by, dict):
                by.pop(sc, None)
                if not by:
                    ext._sim_playback_players_by_screen = None
        except Exception:
            pass
        if sc == 1:
            try:
                ext._sim_playback_player = None
            except Exception:
                pass
        if not rt.sessions:
            try:
                rt.stop()
            except Exception:
                pass
            try:
                ext._sim_playback_runtime = None
            except Exception:
                pass
            try:
                from .tbs_main_dispatch import set_multi_instance_dispatch_mode

                set_multi_instance_dispatch_mode(False)
            except Exception:
                pass
        elif len(rt.sessions) == 1:
            try:
                from .tbs_main_dispatch import set_multi_instance_dispatch_mode

                set_multi_instance_dispatch_mode(False)
            except Exception:
                pass
    else:
        by = getattr(ext, "_sim_playback_players_by_screen", None)
        if isinstance(by, dict) and sc in by:
            p = by.get(sc)
            if p is not None and hasattr(p, "stop"):
                try:
                    p.stop()
                except Exception:
                    pass
            try:
                by.pop(sc, None)
                if not by:
                    ext._sim_playback_players_by_screen = None
            except Exception:
                pass
            stopped = True
        if sc == 1:
            p1 = getattr(ext, "_sim_playback_player", None)
            if p1 is not None and hasattr(p1, "stop"):
                try:
                    p1.stop()
                except Exception:
                    pass
                try:
                    ext._sim_playback_player = None
                except Exception:
                    pass
                stopped = True
    cleanup_playback_subscription_if_idle(ext)
    return stopped


def stop_playback_runtime(ext: Any) -> None:
    rt = get_playback_runtime(ext)
    if rt is not None:
        try:
            rt.stop()
        except Exception:
            pass
    else:
        try:
            stop_legacy_players(ext)
        except Exception:
            pass
    try:
        ext._sim_playback_runtime = None
        ext._sim_playback_players_by_screen = None
        ext._sim_playback_player = None
    except Exception:
        pass
    try:
        from .tbs_main_dispatch import set_multi_instance_dispatch_mode

        set_multi_instance_dispatch_mode(False)
    except Exception:
        pass
    cleanup_playback_subscription_if_idle(ext)


def stop_legacy_players(ext: Any) -> None:
    by = getattr(ext, "_sim_playback_players_by_screen", None)
    if isinstance(by, dict):
        for p in by.values():
            if p is not None and hasattr(p, "stop"):
                try:
                    p.stop()
                except Exception:
                    pass
    p = getattr(ext, "_sim_playback_player", None)
    if p is not None and hasattr(p, "stop"):
        try:
            p.stop()
        except Exception:
            pass


# UI 프레임 예산 — 재생 중에도 ANIM/GATE/PROGRESS 는 FIFO 유지(순서 깨면 JSON·진행률 어긋남)
SIM_LOG_UI_DRAIN_MAX_IDLE = 200
SIM_LOG_UI_DRAIN_MAX_PLAYBACK = 200
SIM_LOG_UI_DRAIN_HISTORY_MAX_PLAYBACK = 24


def iter_tbs_evaluators(ext: Any):
    """메인 + 분할 화면 보조 evaluator (중복 제외)."""
    seen: set[int] = set()
    candidates = [getattr(ext, "_tbs_evaluator", None)]
    by = getattr(ext, "_tbs_split_runtime_by_screen", None)
    if isinstance(by, dict):
        for rt in by.values():
            if rt is not None:
                candidates.append(getattr(rt, "evaluator", None))
    for ev in candidates:
        if ev is None:
            continue
        key = id(ev)
        if key in seen:
            continue
        seen.add(key)
        yield ev


def set_sim_playback_active(ext: Any, active: bool) -> None:
    """프리런 재생 플래그 — 타임라인 게이트·heartbeat 등이 참조한다."""
    try:
        ext._sim_playback_started = bool(active)
    except Exception:
        pass


def sim_log_ui_drain_limit(ext: Any) -> int:
    if bool(getattr(ext, "_sim_playback_started", False)):
        return int(SIM_LOG_UI_DRAIN_MAX_PLAYBACK)
    return int(SIM_LOG_UI_DRAIN_MAX_IDLE)


def sim_log_ui_history_drain_limit(ext: Any) -> int:
    if bool(getattr(ext, "_sim_playback_started", False)):
        return int(SIM_LOG_UI_DRAIN_HISTORY_MAX_PLAYBACK)
    return int(SIM_LOG_UI_DRAIN_MAX_IDLE)


__all__ = [
    "ScreenPlaybackSession",
    "SimPlaybackRuntime",
    "SIM_LOG_UI_DRAIN_HISTORY_MAX_PLAYBACK",
    "SIM_LOG_UI_DRAIN_MAX_IDLE",
    "SIM_LOG_UI_DRAIN_MAX_PLAYBACK",
    "bootstrap_playback_after_prerun",
    "add_playback_sessions_after_prerun",
    "get_playback_runtime",
    "get_sim_playback_player",
    "is_multi_playback_instances",
    "iter_sim_playback_players",
    "iter_tbs_evaluators",
    "set_sim_playback_active",
    "sim_log_ui_drain_limit",
    "sim_log_ui_history_drain_limit",
    "any_playback_still_active",
    "is_simulation_in_progress",
    "cleanup_playback_subscription_if_idle",
    "stop_playback_for_screen",
    "stop_playback_runtime",
]
