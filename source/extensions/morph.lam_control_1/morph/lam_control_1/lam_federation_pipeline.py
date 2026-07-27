"""Federation fetch → 파싱 → prerun → 화면별 CSV 시뮬 재생."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .kit_main_dispatch import schedule_on_main_thread
from .lam_api_timeline_parser import (
    federation_virtual_path,
    merged_response_to_dwells,
    normalize_configs,
)
from .lam_csv_play_screen import get_registry_scheduler_for_lam_screen
from .lam_csv_prerun_playback import (
    build_prerun_result_from_cached,
    maybe_export_csv_prerun_json,
)
from .lam_federation_client import fetch_federation_pages
from .lam_federation_load_hud import set_federation_load_status
from .lam_screen_visibility import request_screen_visibility
from .simulation_play import (
    build_and_cache_from_dwells,
    run_simulation_from_csv,
    stop_and_reap_csv_play_worker,
)

_PRINT_PREFIX = "[LAM/federation-pipe]"
# 실무 배포 진단용 — 콘솔에서 ``[LAM/FED-DIAG]`` 로 grep.
# 단계 번호·이름은 docs/LAM_Federation_Deploy_Diagnose_ko.md 와 동일.
_FED_DIAG_PREFIX = "[LAM/FED-DIAG]"


def _fed_diag(step: str, message: str, **fields: Any) -> None:
    """배포 진단 한 줄 로그. ``step`` 예: ``S01_t2v`` / ``S10_play_start``."""
    extra = ""
    if fields:
        parts = []
        for k, v in fields.items():
            try:
                parts.append(f"{k}={v!r}")
            except Exception:
                parts.append(f"{k}=?")
        extra = " | " + " ".join(parts)
    try:
        print(f"{_FED_DIAG_PREFIX} {step} | {message}{extra}", flush=True)
    except Exception:
        pass


# Federation 웹/배포 전용 — CSV Play UI 경로에는 영향 없음.
_FED_VISIBILITY_WATCHDOG_SEC = 20.0
_FED_REAP_TIMEOUT_SEC = 12.0
_FED_REGISTRY_RETRY = 5
_FED_REGISTRY_RETRY_SLEEP_SEC = 0.35


def _federation_ensure_main_dispatch() -> None:
    try:
        from .kit_main_dispatch import ensure_kit_main_dispatch

        ensure_kit_main_dispatch()
    except Exception:
        pass


def _federation_run_after_visibility(
    ext: Any,
    show_1: bool,
    show_2: bool,
    *,
    work: Callable[[], None],
    thread_name: str,
) -> None:
    """화면 표시 전환 후 ``work`` 1회 실행.

    - ``on_complete`` 유실(generation race)·레이아웃 지연 시 watchdog 로 진행
    - 동일 요청에 대해 work 중복 기동 방지
    - Federation 진입부에만 사용 (일반 CSV Play 무관)
    """
    _federation_ensure_main_dispatch()
    started = {"v": False}
    lock = threading.Lock()

    def _kick_once(*, reason: str) -> None:
        with lock:
            if started["v"]:
                return
            started["v"] = True
        print(
            f"{_PRINT_PREFIX} visibility gate → work ({reason})",
            flush=True,
        )
        _fed_diag("S05_visibility_gate", "work kick", reason=reason)
        threading.Thread(target=work, name=thread_name, daemon=True).start()

    def _after_visibility() -> None:
        _kick_once(reason="on_complete")

    def _watchdog() -> None:
        time.sleep(float(_FED_VISIBILITY_WATCHDOG_SEC))
        with lock:
            if started["v"]:
                return
        print(
            f"{_PRINT_PREFIX} visibility watchdog "
            f"{_FED_VISIBILITY_WATCHDOG_SEC:.0f}s — proceed without layout done",
            flush=True,
        )
        _fed_diag(
            "S05_visibility_watchdog",
            "layout on_complete missing — force proceed",
            timeout_sec=_FED_VISIBILITY_WATCHDOG_SEC,
        )
        _kick_once(reason="watchdog")

    try:
        request_screen_visibility(
            ext, show_1, show_2, on_complete=_after_visibility
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} screen visibility failed: {exc}", flush=True)
        _kick_once(reason="visibility-exception")
        return
    threading.Thread(
        target=_watchdog,
        name=f"{thread_name}-wd",
        daemon=True,
    ).start()


def _federation_reap_best_effort(
    csv_win: Any,
    *,
    screen: int,
    kit_ext: Any = None,
    registry: Any = None,
    scheduler: Any = None,
) -> None:
    """이전 재생 정리. 타임아웃이어도 Federation 파이프라인은 계속(배포 좀비 대비)."""
    si = max(1, int(screen))
    ok = stop_and_reap_csv_play_worker(
        csv_win,
        screen=si,
        registry=registry,
        scheduler=scheduler,
        kit_ext=kit_ext,
        timeout=float(_FED_REAP_TIMEOUT_SEC),
    )
    if ok:
        _fed_diag("S07_reap_ok", "previous play cleared", screen=si)
        return
    print(
        f"{_PRINT_PREFIX} screen{si} reap timeout "
        f"({_FED_REAP_TIMEOUT_SEC:.0f}s) — detach and continue",
        flush=True,
    )
    _fed_diag(
        "S07_reap_timeout",
        "detach zombies and continue",
        screen=si,
        timeout_sec=_FED_REAP_TIMEOUT_SEC,
    )
    try:
        from .simulation_play import (
            clear_csv_playback_stop,
            csv_play_screen_session,
            request_stop_csv_playback,
        )

        request_stop_csv_playback(
            registry, scheduler, screen=si, kit_ext=kit_ext
        )
        sess = csv_play_screen_session(si)
        with sess.child_workers_lock:
            sess.child_workers = [
                t for t in sess.child_workers if t is not None and t.is_alive()
            ]
            # 추적만 끊음 — stop 플래그는 새 play 진입 시 clear
            sess.child_workers.clear()
        if csv_win is not None:
            try:
                csv_win._csv_play_thread = None
            except Exception:
                pass
        # 새 Federation play 가 바로 들어갈 수 있게 stop 만 해제
        clear_csv_playback_stop(screen=si)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} screen{si} reap detach: {exc}", flush=True)


_FED_FORCE_STOP_TIMEOUT_SEC = 70.0


def _federation_force_stop_reset_for_start(
    ext: Any,
    lam_window: Any,
    screen: int,
) -> None:
    """웹 시작 요청용 — 해당 화면을 UI「정지(초기화)」와 동일하게 강제 종료·초기화.

    재생 중이든 아니든 요청 화면만 case별로 처리한다. 완료 후 새 재생이
    막히지 않도록 stop 플래그를 해제한다.
    """
    from .simulation_play import clear_csv_playback_stop

    si = max(1, int(screen))
    _fed_diag(
        "S07_force_stop_begin",
        "stop+reset before federation start",
        screen=si,
    )
    csv_win = _resolve_csv_play_window(lam_window, si)
    if csv_win is None:
        _federation_reap_best_effort(None, screen=si, kit_ext=ext)
        clear_csv_playback_stop(screen=si)
        _fed_diag(
            "S07_force_stop_skip",
            "csv window missing — reap only",
            screen=si,
        )
        return

    done = threading.Event()

    def _kick_stop_reset() -> None:
        try:
            fn = getattr(csv_win, "_on_csv_stop_reset_clicked", None)
            if not callable(fn):
                _federation_reap_best_effort(
                    csv_win, screen=si, kit_ext=ext
                )
                done.set()
                return

            def _on_done() -> None:
                done.set()

            fn(on_complete=_on_done)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} screen{si} force stop-reset kick: {exc}",
                flush=True,
            )
            try:
                _federation_reap_best_effort(
                    csv_win, screen=si, kit_ext=ext
                )
            except Exception:
                pass
            done.set()

    # UI 정지(초기화)와 동일하게 메인에서 기동
    schedule_on_main_thread(_kick_stop_reset)
    if not done.wait(timeout=float(_FED_FORCE_STOP_TIMEOUT_SEC)):
        print(
            f"{_PRINT_PREFIX} screen{si} force stop-reset timeout "
            f"({_FED_FORCE_STOP_TIMEOUT_SEC:.0f}s) — reap fallback",
            flush=True,
        )
        _fed_diag(
            "S07_force_stop_timeout",
            "fallback reap",
            screen=si,
            timeout_sec=_FED_FORCE_STOP_TIMEOUT_SEC,
        )
        _federation_reap_best_effort(csv_win, screen=si, kit_ext=ext)

    # 성공/실패와 무관 — 이어질 fetch·play 가 stop 플래그에 막히지 않게
    try:
        clear_csv_playback_stop(screen=si)
    except Exception:
        pass
    _fed_diag("S07_force_stop_done", "ready for new start", screen=si)


def _federation_force_stop_requested_screens(
    ext: Any,
    lam_window: Any,
    screens: List[int],
) -> None:
    """요청에 포함된 화면만 순차 강제 종료(다른 화면 재생은 유지)."""
    seen = set()
    for raw in screens:
        si = max(1, int(raw))
        if si in seen:
            continue
        seen.add(si)
        _federation_force_stop_reset_for_start(ext, lam_window, si)


def _federation_resolve_registry_scheduler(
    lam_window: Any,
    csv_win: Any,
    screen: int,
) -> Tuple[Any, Any]:
    """배포에서 레이아웃 직후 registry 가 늦을 수 있어 짧게 재시도."""
    si = max(1, int(screen))
    registry: Any = None
    scheduler: Any = None
    for attempt in range(1, int(_FED_REGISTRY_RETRY) + 1):
        registry, scheduler = get_registry_scheduler_for_lam_screen(
            lam_window, si, allow_fallback=(si <= 1)
        )
        if csv_win is not None:
            try:
                csv_win._refresh_play_runtime()
            except Exception:
                pass
            if getattr(csv_win, "_registry", None) is not None:
                registry = csv_win._registry
            if getattr(csv_win, "_scheduler", None) is not None:
                scheduler = csv_win._scheduler
        if registry is not None and scheduler is not None:
            if attempt > 1:
                print(
                    f"{_PRINT_PREFIX} screen{si} registry ready after retry {attempt}",
                    flush=True,
                )
            return registry, scheduler
        time.sleep(float(_FED_REGISTRY_RETRY_SLEEP_SEC))
    return registry, scheduler


@dataclass
class ScreenPipelineResult:
    screen: int
    ok: bool
    message: str
    meta: Dict[str, Any]
    prerun: Optional[Any] = None
    cached: Optional[Any] = None


def _default_prerun_export_enabled() -> bool:
    try:
        from .lam_sim_control_defaults import CSV_PRERUN_EXPORT_JSON

        return bool(CSV_PRERUN_EXPORT_JSON)
    except Exception:
        return False


def _write_federation_parse_export(
    *,
    screen: int,
    original: Dict[str, Any],
    parse_stats: Dict[str, Any],
    dwells: List[Any],
    prerun: Any,
) -> Optional[Path]:
    """Federation 응답 디스크 저장 — **비활성**.

    예전 ``data/api_queries`` mkdir/쓰기는 배포 PermissionError 의 원인이었고,
    재생 SSOT 는 메모리 ``CachedCsvPlayback`` 이라 파일 저장이 필요 없다.
    """
    _ = (screen, original, parse_stats, dwells, prerun)
    print(
        f"{_PRINT_PREFIX} federation parse export disabled "
        f"(no api_queries mkdir/write) screen={int(screen)}",
        flush=True,
    )
    return None


def _resolve_csv_play_window(lam_window: Any, screen: int) -> Any:
    """화면별 CSV 재생창 — 타임라인 UI·preflight 진입점."""
    if lam_window is None:
        return None
    ensure = getattr(lam_window, "_ensure_csv_sim_play_window", None)
    if callable(ensure):
        try:
            return ensure(int(screen))
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} ensure csv window screen={screen}: {exc}",
                flush=True,
            )
    wins = getattr(lam_window, "_csv_sim_windows", None)
    if isinstance(wins, dict):
        return wins.get(int(screen))
    return None


def _apply_federation_timeline_ui(csv_win: Any, cached: Any) -> None:
    """CSV Play 와 동일하게 화면별 타임라인 UI 를 새 schedule 로 교체.

    Federation worker 는 백그라운드 스레드이므로 ``wait=True``(``_dispatch_main_wait``)
    를 쓰지 않는다 — 메인 정체 시 파이프라인 전체가 멈추는 것을 막는다.
    """
    if csv_win is None or cached is None:
        return
    apply = getattr(csv_win, "_post_apply_cached_timeline_ui", None)
    if callable(apply):
        apply(cached, wait=False)
        return
    apply2 = getattr(csv_win, "_apply_cached_timeline_ui", None)
    if callable(apply2):
        schedule_on_main_thread(lambda: apply2(cached))


def _run_federation_play_preflight(csv_win: Any, screen: int, kit_ext: Any) -> bool:
    """CSV Play 직전과 동일 — 화면1 camera fly / 화면2+ aux preflight."""
    si = max(1, int(screen))
    try:
        from .lam_csv_screen_runtime import (
            resolve_csv_screen_runtime,
            run_csv_screen_play_preflight,
        )

        lam_window = getattr(csv_win, "_lam_window", None) if csv_win is not None else None
        if lam_window is None:
            lam_window = getattr(kit_ext, "_lam_window", None)
        runtime = resolve_csv_screen_runtime(
            lam_window,
            si,
            csv_window=csv_win,
            require_aux=si > 1,
        )
        if runtime is None:
            return False
        return bool(run_csv_screen_play_preflight(runtime))
    except Exception as exc:
        print(f"{_PRINT_PREFIX} screen{si} play preflight: {exc}", flush=True)
        return False


def _start_federation_playback(
    ext: Any,
    lam_window: Any,
    csv_win: Any,
    screen: int,
    cached: Any,
    *,
    speed_scale: float,
) -> Optional[str]:
    """타임라인 반영 후 CSV Play 와 같은 preflight → 재생 경로."""
    from .lam_csv_play_screen import csv_play_screen_binding
    from .simulation_play import (
        clear_csv_play_pause_checkpoint,
        clear_csv_play_timeline_highlight,
        clear_csv_playback_stop,
        csv_play_pause_armed,
        csv_playback_stop_requested,
        get_csv_play_live_speed_scale,
        set_csv_play_live_speed_ui_reader,
        set_csv_play_progress_ui_callback,
        set_csv_play_timeline_highlight_callback,
    )

    si = max(1, int(screen))
    registry, scheduler = _federation_resolve_registry_scheduler(
        lam_window, csv_win, si
    )
    if csv_win is not None:
        # 공정만보기 실시간 전환·일시정지 이어서 재생이 같은 캐시를 쓰도록 고정
        try:
            csv_win._prepared_playback = cached
        except Exception:
            pass
    if registry is None or scheduler is None:
        _fed_diag(
            "S11_registry_missing",
            "cannot start play",
            screen=si,
        )
        return f"registry/scheduler missing for screen {si}"
    _federation_reap_best_effort(
        csv_win,
        screen=si,
        registry=registry,
        scheduler=scheduler,
        kit_ext=ext,
    )
    process_only = False
    if csv_win is not None:
        try:
            csv_win.ensure_playback_models()
        except Exception:
            pass
        try:
            process_only = bool(csv_win._read_process_only())
        except Exception:
            process_only = False
        try:
            wire = getattr(csv_win, "_wire_process_only_model_live_update", None)
            if callable(wire):
                wire()
        except Exception:
            pass
        try:
            wire_sp = getattr(csv_win, "_wire_speed_model_live_update", None)
            if callable(wire_sp):
                wire_sp()
        except Exception:
            pass
    play_speed = 1.0 if process_only else float(speed_scale)

    def _on_play_ui(csv_t: float, csv_total: float, wall_el: float, wall_tot: float) -> None:
        if csv_win is None:
            return
        fmt = getattr(csv_win, "_format_play_progress_line", None)
        set_text = getattr(csv_win, "_set_build_progress_text", None)
        if not callable(fmt) or not callable(set_text):
            return
        line = fmt(csv_t, csv_total, wall_el, wall_tot)
        schedule_on_main_thread(lambda: set_text(line))

    def _on_timeline_highlight(active_keys: frozenset) -> None:
        if csv_win is None:
            return
        hl = getattr(csv_win, "_apply_schedule_row_highlight", None)
        if not callable(hl):
            return
        keys = frozenset(active_keys)
        schedule_on_main_thread(lambda k=keys: hl(k))

    def _resolve_play_registry_scheduler() -> Tuple[Any, Any]:
        """worker 진입 시점 registry — CSV Play [재생] 과 동일하게 csv_win 기준."""
        play_reg, play_sch = registry, scheduler
        if csv_win is not None:
            try:
                csv_win._refresh_play_runtime()
            except Exception:
                pass
            if getattr(csv_win, "_registry", None) is not None:
                play_reg = csv_win._registry
            if getattr(csv_win, "_scheduler", None) is not None:
                play_sch = csv_win._scheduler
        return play_reg, play_sch

    def _play() -> None:
        with csv_play_screen_binding(si):
            try:
                # Kit Play worker 와 동일 — 이전 pause/stop 잔여 상태 제거
                clear_csv_play_pause_checkpoint(screen=si)
                clear_csv_playback_stop(screen=si)
                play_reg, play_sch = _resolve_play_registry_scheduler()
                if play_reg is None or play_sch is None:
                    print(
                        f"{_PRINT_PREFIX} screen{si} play aborted — "
                        "registry/scheduler missing",
                        flush=True,
                    )
                    _fed_diag(
                        "S12_play_abort_registry",
                        "play worker exit",
                        screen=si,
                    )
                    return
                if csv_win is not None:
                    set_csv_play_live_speed_ui_reader(
                        lambda: get_csv_play_live_speed_scale(screen=si),
                        screen=si,
                    )
                set_csv_play_progress_ui_callback(_on_play_ui, screen=si)
                set_csv_play_timeline_highlight_callback(
                    _on_timeline_highlight, screen=si
                )
                _fed_diag("S13_preflight_begin", "camera/prim hide", screen=si)
                if not _run_federation_play_preflight(csv_win, si, ext):
                    print(
                        f"{_PRINT_PREFIX} screen{si} preflight 실패 — "
                        "카메라/prim hide 생략하고 CSV 재생 계속",
                        flush=True,
                    )
                    _fed_diag(
                        "S13_preflight_fail_continue",
                        "skip camera/hide, continue CSV",
                        screen=si,
                    )
                else:
                    _fed_diag("S13_preflight_ok", "preflight done", screen=si)
                try:
                    from .lam_traffic_light_emissive import on_csv_playback_started

                    on_csv_playback_started()
                except Exception:
                    pass
                _fed_diag(
                    "S14_run_simulation",
                    "run_simulation_from_csv enter",
                    screen=si,
                    process_only=process_only,
                    speed=play_speed,
                    blocks=len(getattr(cached, "blocks", None) or []),
                )
                run_simulation_from_csv(
                    play_reg,
                    play_sch,
                    prepared=cached,
                    speed_scale=play_speed,
                    process_only=process_only,
                    play_screen=si,
                    kit_ext=ext,
                    skip_play_prim_hide=True,
                )
                _fed_diag(
                    "S15_run_simulation_exit",
                    "play worker finished",
                    screen=si,
                )
            except Exception as exc:
                print(f"{_PRINT_PREFIX} screen{si} play failed: {exc}", flush=True)
                _fed_diag("S15_play_failed", str(exc), screen=si)
            finally:
                try:
                    from .lam_traffic_light_emissive import (
                        on_csv_playback_paused_or_stopped,
                    )

                    on_csv_playback_paused_or_stopped()
                except Exception:
                    pass
                set_csv_play_live_speed_ui_reader(None, screen=si)
                set_csv_play_progress_ui_callback(None, screen=si)
                set_csv_play_timeline_highlight_callback(None, screen=si)
                clear_csv_play_timeline_highlight(screen=si)
                # 일시정지·공정만보기 전환: switch worker 가 join 후 해제 (Kit Play 와 동일)
                if csv_win is not None and getattr(
                    csv_win, "_csv_play_thread", None
                ) is threading.current_thread():
                    if csv_playback_stop_requested(screen=si) and csv_play_pause_armed(
                        screen=si
                    ):
                        pass
                    else:
                        csv_win._csv_play_thread = None

    play_thread = threading.Thread(
        target=_play,
        name=f"lam-federation-play-s{si}",
        daemon=True,
    )
    if csv_win is not None:
        # CSV 창의 일시정지 및 공정만보기 실시간 전환이 API 재생도 추적하도록 연결.
        csv_win._csv_play_thread = play_thread
        try:
            csv_win._prepared_playback = cached
        except Exception:
            pass
    play_thread.start()
    _fed_diag(
        "S12_play_thread_started",
        "background play thread running",
        screen=si,
        thread=play_thread.name,
        auto_play=True,
    )
    return None


def _federation_verbose_parse_log() -> bool:
    """``FEDERATION_VERBOSE_PARSE_LOG`` — False 이면 파싱·빌드 상세 로그 억제."""
    try:
        from .lam_sim_control_defaults import FEDERATION_VERBOSE_PARSE_LOG

        return bool(FEDERATION_VERBOSE_PARSE_LOG)
    except Exception:
        return False


def _fed_load_hud(
    screen: int,
    phase: str,
    *,
    detail: str = "",
    ext: Any = None,
    lam_window: Any = None,
) -> None:
    try:
        set_federation_load_status(
            screen,
            phase,
            detail=detail,
            ext=ext,
            lam_window=lam_window,
        )
    except Exception:
        pass


def _process_merged_response(
    ext: Any,
    lam_window: Any,
    screen: int,
    body: Dict[str, Any],
    merged: Dict[str, Any],
    *,
    auto_play: bool,
    speed_scale: float,
    save_response_json: bool = False,
    export_default_prerun: bool = False,
) -> ScreenPipelineResult:
    """이미 수집됐거나 사용자가 붙여넣은 응답만 파싱·프리런·(옵션)재생한다."""
    from .simulation_play import set_csv_playback_compact_log

    meta: Dict[str, Any] = {"screen": screen}
    quiet = not _federation_verbose_parse_log()
    if quiet:
        set_csv_playback_compact_log(True)
    try:
        eqp_id = str(body.get("eqp_id") or "").strip()
        if not eqp_id:
            _fed_diag("S09_parse_fail", "eqp_id missing", screen=screen)
            _fed_load_hud(
                screen,
                "failed",
                detail="eqp_id missing",
                ext=ext,
                lam_window=lam_window,
            )
            return ScreenPipelineResult(
                screen, False, "eqp_id missing in config body", meta
            )
        _fed_load_hud(
            screen, "parsing", ext=ext, lam_window=lam_window
        )
        dwells, parse_stats = merged_response_to_dwells(
            merged, eqp_id=eqp_id, quiet=quiet
        )
        meta["parse"] = parse_stats
        if not dwells:
            _fed_diag(
                "S09_parse_fail",
                "no dwell records after parse",
                screen=screen,
                eqp_id=eqp_id,
            )
            _fed_load_hud(
                screen,
                "failed",
                detail="파싱 결과 없음",
                ext=ext,
                lam_window=lam_window,
            )
            return ScreenPipelineResult(screen, False, "no dwell records after parse", meta)
        _fed_diag(
            "S09_parse_ok",
            "dwells built",
            screen=screen,
            eqp_id=eqp_id,
            dwells=len(dwells),
        )
        vpath = federation_virtual_path(screen, body)
        cached = build_and_cache_from_dwells(vpath, dwells)
        prerun = build_prerun_result_from_cached(cached, screen=screen)
        _fed_diag(
            "S10_prerun_ok",
            "cached playback ready",
            screen=screen,
            items=len(prerun.items),
            csv_t=round(float(prerun.final_csv_time_sec), 1),
            blocks=len(getattr(cached, "blocks", None) or []),
        )
        if save_response_json:
            saved = _write_federation_parse_export(
                screen=screen,
                original=merged,
                parse_stats=parse_stats,
                dwells=dwells,
                prerun=prerun,
            )
            if saved is not None:
                meta["saved_json"] = str(saved)
        elif export_default_prerun:
            # dump 실패해도 재생 계속 (maybe_export 내부에서 OSError swallow)
            maybe_export_csv_prerun_json(
                prerun,
                export_enabled=_default_prerun_export_enabled(),
            )
        meta["prerun"] = {
            "items": len(prerun.items),
            "final_csv_time_sec": prerun.final_csv_time_sec,
            "build_ms": prerun.build_ms,
        }
        print(
            f"{_PRINT_PREFIX} prerun screen={screen} items={len(prerun.items)} "
            f"duration={prerun.final_csv_time_sec:.1f}s build={prerun.build_ms:.0f}ms",
            flush=True,
        )
        csv_win = _resolve_csv_play_window(lam_window, screen)
        # Play 여부와 무관하게 화면별 타임라인은 API 결과로 교체
        _fed_diag(
            "S11_timeline_ui",
            "apply schedule UI (async)",
            screen=screen,
            csv_win=csv_win is not None,
            auto_play=bool(auto_play),
        )
        _apply_federation_timeline_ui(csv_win, cached)
        try:
            from .lam_foup_usage_hide import (
                count_used_foups_from_dwells,
                count_used_foups_from_lot_map,
            )

            used_n = count_used_foups_from_dwells(dwells)
            lot_map = (parse_stats or {}).get("lots_to_foup") or {}
            used_from_map = count_used_foups_from_lot_map(
                lot_map if isinstance(lot_map, dict) else {}
            )
            used_n = max(1, min(3, max(int(used_n), int(used_from_map))))
            meta["used_foup_count"] = used_n
            # 파싱 단계에서는 visibility 를 바꾸지 않는다. Play preflight 가 이 값을
            # fly 이후 PLAY_HIDE_PRIM_SPECS 와 함께 한 번만 적용한다.
            try:
                cached.used_foup_count = used_n
            except Exception:
                pass
            if csv_win is not None:
                try:
                    csv_win._lam_used_foup_count = used_n
                except Exception:
                    pass
            _fed_diag(
                "S10_foup_usage_count",
                "defer extra hide until post-fly prim phase",
                screen=screen,
                used_foup_count=used_n,
                lots_to_foup=lot_map,
            )
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} screen{screen} foup usage count: {exc}",
                flush=True,
            )
        _fed_load_hud(
            screen, "ready", ext=ext, lam_window=lam_window
        )
        if auto_play:
            _fed_diag("S11_auto_play", "start playback", screen=screen)
            err = _start_federation_playback(
                ext,
                lam_window,
                csv_win,
                screen,
                cached,
                speed_scale=speed_scale,
            )
            if err:
                _fed_diag(
                    "S11_auto_play_fail",
                    err,
                    screen=screen,
                )
                _fed_load_hud(
                    screen,
                    "failed",
                    detail=err,
                    ext=ext,
                    lam_window=lam_window,
                )
                return ScreenPipelineResult(screen, False, err, meta)
            _fed_load_hud(
                screen, "playing", ext=ext, lam_window=lam_window
            )
        else:
            _fed_diag("S11_auto_play_skip", "auto_play=False (barrier)", screen=screen)
        return ScreenPipelineResult(
            screen, True, "ok", meta, prerun=prerun, cached=cached
        )
    except Exception as exc:
        _fed_diag("S09_exception", str(exc), screen=screen)
        _fed_load_hud(
            screen,
            "failed",
            detail=str(exc),
            ext=ext,
            lam_window=lam_window,
        )
        return ScreenPipelineResult(screen, False, str(exc), meta)
    finally:
        if quiet:
            set_csv_playback_compact_log(False)


_FEDERATION_PERIOD_KEYS = ("mt", "mt_from", "mt_to")
_DATETIME_PERIOD_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def _to_federation_period(value: Any) -> str:
    """Federation API 기간 필드 → ``YYYYMM`` (예: ``202606``).

    ISO 8601(``2026-07-07T15:00:00.000Z``), datetime 문자열, 이미 ``YYYYMM`` 인 값을
    모두 API 요구 형식으로 맞춘다. 날짜로 해석할 수 없는 값은 그대로 둔다.
    """
    s = str(value or "").strip()
    if not s:
        return ""
    if re.fullmatch(r"\d{6}", s):
        month = int(s[4:6])
        if 1 <= month <= 12:
            return s
    iso_candidate = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        return datetime.fromisoformat(iso_candidate).strftime("%Y%m")
    except ValueError:
        pass
    for fmt in _DATETIME_PERIOD_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y%m")
        except ValueError:
            continue
    return s


def _normalize_federation_body_periods(body: Dict[str, Any]) -> Dict[str, Any]:
    """``mt`` / ``mt_from`` / ``mt_to`` 를 Federation API ``YYYYMM`` 형식으로 정규화."""
    out = dict(body or {})
    for key in _FEDERATION_PERIOD_KEYS:
        if key in out:
            out[key] = _to_federation_period(out[key])
    return out


def _read_federation_defaults() -> Dict[str, Any]:
    try:
        from . import lam_sim_control_defaults as d

        verbose = bool(getattr(d, "FEDERATION_VERBOSE_PARSE_LOG", False))
        log_row_sample = int(getattr(d, "FEDERATION_LOG_ROW_SAMPLE", 5) or 5)
        log_full_response = bool(getattr(d, "FEDERATION_LOG_FULL_RESPONSE", False))
        if not verbose:
            # 상세 로그 OFF — 페이지 샘플·전체 dump 억제
            log_row_sample = 0
            log_full_response = False
        return {
            "url": str(getattr(d, "FEDERATION_QUERY_URL", "") or ""),
            "limit": int(getattr(d, "FEDERATION_FETCH_LIMIT", 1000) or 1000),
            "timeout_sec": float(getattr(d, "FEDERATION_FETCH_TIMEOUT_SEC", 300.0) or 300.0),
            "use_fixture": bool(getattr(d, "FEDERATION_USE_FIXTURE", False)),
            "verbose_parse_log": verbose,
            "log_row_sample": log_row_sample,
            "log_full_response": log_full_response,
            "bearer_token": str(getattr(d, "FEDERATION_BEARER_TOKEN", "") or ""),
            "extra_headers": dict(getattr(d, "FEDERATION_EXTRA_HEADERS", {}) or {}),
        }
    except Exception:
        return {
            "url": "",
            "limit": 1000,
            "timeout_sec": 300.0,
            "use_fixture": False,
            "verbose_parse_log": False,
            "log_row_sample": 0,
            "log_full_response": False,
            "bearer_token": "",
            "extra_headers": {},
        }


def _process_one_screen(
    ext: Any,
    lam_window: Any,
    screen: int,
    body: Dict[str, Any],
    *,
    url: str,
    limit: int,
    timeout_sec: float,
    use_fixture: bool,
    bearer_token: str,
    extra_headers: Dict[str, str],
    log_row_sample: int,
    log_full_response: bool,
    auto_play: bool,
    speed_scale: float,
    export_default_prerun: bool = False,
) -> ScreenPipelineResult:
    meta: Dict[str, Any] = {"screen": screen}
    try:
        api_body = _normalize_federation_body_periods(body)
        eqp_id = str(api_body.get("eqp_id") or "").strip()
        if not eqp_id:
            _fed_load_hud(
                screen,
                "failed",
                detail="eqp_id missing",
                ext=ext,
                lam_window=lam_window,
            )
            return ScreenPipelineResult(
                screen, False, "eqp_id missing in config body", meta
            )
        _fed_load_hud(
            screen, "requesting", ext=ext, lam_window=lam_window
        )
        merged, fetch_meta = fetch_federation_pages(
            url=url,
            body=api_body,
            limit=limit,
            screen=screen,
            bearer_token=bearer_token,
            extra_headers=extra_headers,
            timeout_sec=timeout_sec,
            use_fixture=use_fixture,
            log_row_sample=log_row_sample,
            log_full_response=log_full_response,
            quiet=not _federation_verbose_parse_log(),
        )
        meta["fetch"] = fetch_meta
        _fed_diag(
            "S08_fetch_done",
            "HTTP pages finished",
            screen=screen,
            pages=fetch_meta.get("pages"),
            rows=fetch_meta.get("total_rows"),
            elapsed=fetch_meta.get("elapsed_sec"),
            status=fetch_meta.get("http_status"),
        )
        _fed_load_hud(
            screen, "received", ext=ext, lam_window=lam_window
        )
        result = _process_merged_response(
            ext,
            lam_window,
            screen,
            api_body,
            merged,
            auto_play=auto_play,
            speed_scale=speed_scale,
            export_default_prerun=export_default_prerun,
        )
        result.meta["fetch"] = fetch_meta
        return result
    except Exception as exc:
        _fed_load_hud(
            screen,
            "failed",
            detail=str(exc),
            ext=ext,
            lam_window=lam_window,
        )
        return ScreenPipelineResult(screen, False, str(exc), meta)


def _start_ready_screens_together(
    ext: Any,
    lam_window: Any,
    results: List[ScreenPipelineResult],
    *,
    speed_scale: float,
) -> List[ScreenPipelineResult]:
    """준비완료(ok+cached) 화면만 거의 동시에 play 시작. 실패 화면은 그대로 둔다."""
    ready = [r for r in results if r.ok and r.cached is not None]
    failed = [r for r in results if not r.ok]
    _fed_diag(
        "S11_barrier",
        "start ready screens together",
        ready=[r.screen for r in ready],
        failed=[r.screen for r in failed],
    )
    out: List[ScreenPipelineResult] = []
    # 실패분은 유지, 성공분은 play 결과로 갱신
    by_screen = {r.screen: r for r in results}
    for r in ready:
        csv_win = _resolve_csv_play_window(lam_window, r.screen)
        _fed_diag("S11_auto_play", "barrier start playback", screen=r.screen)
        err = _start_federation_playback(
            ext,
            lam_window,
            csv_win,
            r.screen,
            r.cached,
            speed_scale=speed_scale,
        )
        if err:
            _fed_diag("S11_auto_play_fail", err, screen=r.screen)
            _fed_load_hud(
                r.screen,
                "failed",
                detail=err,
                ext=ext,
                lam_window=lam_window,
            )
            by_screen[r.screen] = ScreenPipelineResult(
                r.screen, False, err, dict(r.meta or {}), prerun=r.prerun, cached=r.cached
            )
        else:
            _fed_load_hud(
                r.screen, "playing", ext=ext, lam_window=lam_window
            )
    for si in sorted(by_screen.keys()):
        out.append(by_screen[si])
    return out


def run_federation_start_simulation(
    ext: Any,
    payload: Dict[str, Any],
    *,
    on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
    auto_play: bool = True,
    speed_scale: float = 1.0,
    limit_override: Optional[int] = None,
    url_override: Optional[str] = None,
    use_fixture_override: Optional[bool] = None,
    bearer_token_override: Optional[str] = None,
    extra_headers_override: Optional[Dict[str, str]] = None,
) -> None:
    """T2V ``configs`` payload → 화면 표시 + fetch + prerun + (옵션) 재생.

    요청에 포함된 화면(case)은 시작 전에 UI「정지(초기화)」와 동일하게
    강제 종료한 뒤 fetch·준비하며, 듀얼이면 성공 화면만 동시에 재생한다.
    실패 화면은 HUD에 실패를 남기고 play 하지 않는다.
    """
    defaults = _read_federation_defaults()
    url = str(url_override or defaults["url"] or "").strip()
    if not url:
        _fed_diag("S03_fail", "FEDERATION_QUERY_URL is empty")
        _finish(on_complete, _err("FEDERATION_QUERY_URL is empty"))
        return
    limit = int(limit_override if limit_override is not None else defaults["limit"])
    timeout_sec = float(defaults["timeout_sec"])
    use_fixture = (
        bool(use_fixture_override)
        if use_fixture_override is not None
        else bool(defaults["use_fixture"])
    )
    bearer_token = str(
        bearer_token_override
        if bearer_token_override is not None
        else defaults["bearer_token"]
    )
    extra_headers = dict(
        extra_headers_override
        if extra_headers_override is not None
        else defaults["extra_headers"]
    )
    log_row_sample = int(defaults["log_row_sample"])
    log_full_response = bool(defaults["log_full_response"])

    configs = payload.get("configs", payload.get("config", []))
    bodies, show_1, show_2 = normalize_configs(configs)
    print(
        f"{_PRINT_PREFIX} start configs show_1={show_1} show_2={show_2}",
        flush=True,
    )
    _fed_diag(
        "S03_pipeline_enter",
        "run_federation_start_simulation",
        show_1=show_1,
        show_2=show_2,
        auto_play=auto_play,
        url=url[:80],
        limit=limit,
    )

    def _work_after_visibility() -> Dict[str, Any]:
        _fed_diag("S06_work_begin", "after visibility — fetch/parse then barrier play")
        lam_window = getattr(ext, "_lam_window", None) or getattr(ext, "_window", None)
        if lam_window is None:
            _fed_diag("S06_fail", "LAM window is not ready")
            return _err("LAM window is not ready")
        jobs: List[Tuple[int, Dict[str, Any]]] = []
        if show_1:
            jobs.append((1, bodies[0]))
        if show_2:
            jobs.append((2, bodies[1]))
        if not jobs:
            _fed_diag("S06_fail", "both configs empty")
            return _err("both configs are empty — nothing to simulate")

        _fed_diag(
            "S06_jobs",
            "screens to process",
            screens=[s for s, _ in jobs],
        )
        # 웹 시작 = 해당 case 강제 종료(정지초기화) + 이후 fetch/준비/재생 (한 요청)
        _federation_force_stop_requested_screens(
            ext, lam_window, [s for s, _ in jobs]
        )
        try:
            from .lam_play_start_sequence import (  # type: ignore
                run_play_start_request_standby_for_screens,
            )

            run_play_start_request_standby_for_screens(
                lam_window,
                [s for s, _ in jobs],
            )
        except Exception as exc:
            _fed_diag("S06_standby_fail", str(exc))

        results: List[ScreenPipelineResult] = []
        # Phase A: fetch+parse+prerun 만 (play 보류). 순차 처리로 main-thread 경합 회피.
        for screen, body in jobs:
            _fed_diag("S08_screen_begin", "prepare screen (no play yet)", screen=screen)
            results.append(
                _process_one_screen(
                    ext,
                    lam_window,
                    screen,
                    body,
                    url=url,
                    limit=limit,
                    timeout_sec=timeout_sec,
                    use_fixture=use_fixture,
                    bearer_token=bearer_token,
                    extra_headers=extra_headers,
                    log_row_sample=log_row_sample,
                    log_full_response=log_full_response,
                    auto_play=False,
                    speed_scale=speed_scale,
                    export_default_prerun=False,
                )
            )
            r = results[-1]
            _fed_diag(
                "S08_screen_end",
                r.message,
                screen=screen,
                ok=r.ok,
            )
        results.sort(key=lambda r: r.screen)

        # Phase B: 성공 화면만 동시 시작
        if auto_play:
            results = _start_ready_screens_together(
                ext,
                lam_window,
                results,
                speed_scale=speed_scale,
            )
        else:
            for r in results:
                if r.ok:
                    _fed_diag(
                        "S11_auto_play_skip",
                        "auto_play=False after prepare",
                        screen=r.screen,
                    )

        results.sort(key=lambda r: r.screen)
        ok_list = [r for r in results if r.ok]
        failed = [r for r in results if not r.ok]
        payload_data = {
            "screens": [_result_dict(r) for r in results],
            "show_1": show_1,
            "show_2": show_2,
            "started_screens": [r.screen for r in ok_list],
            "failed_screens": [r.screen for r in failed],
        }
        if not ok_list:
            return _err(
                "; ".join(f"screen{r.screen}: {r.message}" for r in failed)
                or "all screens failed",
                data=payload_data,
            )
        if failed:
            # 일부 실패·일부 성공 → 성공분만 재생한 상태로 ok 반환
            print(
                f"{_PRINT_PREFIX} partial ok — started={payload_data['started_screens']} "
                f"failed={payload_data['failed_screens']}",
                flush=True,
            )
        return _ok(payload_data)

    def _apply_visibility_then_run() -> None:
        def _run_fetch() -> None:
            t0 = time.perf_counter()
            result = _work_after_visibility()
            result.setdefault("data", {})["elapsed_sec"] = time.perf_counter() - t0
            _finish(on_complete, result)

        _federation_run_after_visibility(
            ext,
            show_1,
            show_2,
            work=_run_fetch,
            thread_name="lam-federation-start",
        )
        _fed_diag(
            "S04_visibility_requested",
            "waiting on_complete or watchdog",
            show_1=show_1,
            show_2=show_2,
            watchdog_sec=_FED_VISIBILITY_WATCHDOG_SEC,
        )

    schedule_on_main_thread(_apply_visibility_then_run)


def run_federation_response_simulation(
    ext: Any,
    merged_response: Dict[str, Any],
    body: Dict[str, Any],
    *,
    screen: int = 1,
    on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
    auto_play: bool = True,
    speed_scale: float = 1.0,
    save_response_json: bool = False,
) -> None:
    """응답/로그 편집기의 현재 rows만 파싱·시뮬한다(API 재요청·pagination 없음)."""
    si = max(1, min(2, int(screen)))

    def _apply_visibility_then_run() -> None:
        def _work() -> None:
            lam_window = getattr(ext, "_lam_window", None) or getattr(ext, "_window", None)
            if lam_window is None:
                _finish(on_complete, _err("LAM window is not ready"))
                return
            # 시작 전 해당 화면 강제 종료+초기화 (웹 시작과 동일)
            _federation_force_stop_reset_for_start(ext, lam_window, si)
            result = _process_merged_response(
                ext,
                lam_window,
                si,
                dict(body or {}),
                dict(merged_response or {}),
                auto_play=auto_play,
                speed_scale=speed_scale,
                save_response_json=save_response_json,
                export_default_prerun=False,
            )
            if result.ok:
                _finish(
                    on_complete,
                    _ok({"screens": [_result_dict(result)], "source": "response_editor"}),
                )
            else:
                _finish(
                    on_complete,
                    _err(
                        f"screen{si}: {result.message}",
                        data={"screens": [_result_dict(result)]},
                    ),
                )

        _federation_run_after_visibility(
            ext,
            si == 1,
            si == 2,
            work=_work,
            thread_name=f"lam-federation-response-s{si}",
        )

    schedule_on_main_thread(_apply_visibility_then_run)


def _ok(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"code": 0, "message": "success", "data": dict(data or {})}


def _err(message: str, *, code: int = 1, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"code": int(code), "message": str(message), "data": dict(data or {})}


def _result_dict(r: ScreenPipelineResult) -> Dict[str, Any]:
    return {
        "screen": r.screen,
        "ok": r.ok,
        "message": r.message,
        "meta": r.meta,
    }


def _finish(cb: Optional[Callable[[Dict[str, Any]], None]], result: Dict[str, Any]) -> None:
    code = int(result.get("code", 0))
    _fed_diag(
        "S16_pipeline_done",
        str(result.get("message", "")),
        code=code,
    )
    if cb is None:
        print(
            f"{_PRINT_PREFIX} done code={code} msg={result.get('message')}",
            flush=True,
        )
        return

    _federation_ensure_main_dispatch()

    def _dispatch() -> None:
        try:
            _fed_diag("S17_v2t_callback", "on_complete → V2T", code=code)
            cb(result)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} on_complete failed: {exc}", flush=True)
            _fed_diag("S17_v2t_callback_fail", str(exc))

    schedule_on_main_thread(_dispatch)


__all__ = [
    "ScreenPipelineResult",
    "run_federation_response_simulation",
    "run_federation_start_simulation",
]
