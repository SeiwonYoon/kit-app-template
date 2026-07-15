"""Federation fetch → 파싱 → prerun → 화면별 CSV 시뮬 재생."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
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
    build_csv_prerun_export_document,
    build_prerun_result_from_cached,
    maybe_export_csv_prerun_json,
)
from .lam_federation_client import fetch_federation_pages
from .lam_screen_visibility import request_screen_visibility
from .simulation_play import build_and_cache_from_dwells, run_simulation_from_csv

_PRINT_PREFIX = "[LAM/federation-pipe]"


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
) -> Path:
    """테스트 창 JSON 저장 — 전체 원본 응답과 CSV 동형 프리런 결과를 한 파일에 보존."""
    out_dir = Path(__file__).resolve().parents[2] / "data" / "api_queries"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"federation_screen{int(screen)}_{stamp}.json"
    doc = {
        "version": 1,
        "screen": int(screen),
        "original_response": original,
        "parsed": {
            "stats": parse_stats,
            "dwells": [asdict(d) for d in dwells],
            "prerun": build_csv_prerun_export_document(prerun),
        },
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    return path


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
    """CSV Play 와 동일하게 화면별 타임라인 UI 를 새 schedule 로 교체."""
    if csv_win is None or cached is None:
        return
    apply = getattr(csv_win, "_post_apply_cached_timeline_ui", None)
    if callable(apply):
        apply(cached, wait=True)
        return
    apply2 = getattr(csv_win, "_apply_cached_timeline_ui", None)
    if callable(apply2):
        schedule_on_main_thread(lambda: apply2(cached))


def _run_federation_play_preflight(csv_win: Any, screen: int, kit_ext: Any) -> bool:
    """CSV Play 직전과 동일 — 화면1 camera fly / 화면2+ aux preflight."""
    si = max(1, int(screen))
    if si <= 1:
        try:
            from .lam_play_start_sequence import run_play_start_preflight

            return bool(run_play_start_preflight(resume_from_pause=False))
        except Exception as exc:
            print(f"{_PRINT_PREFIX} screen1 play preflight: {exc}", flush=True)
            return False
    if csv_win is None:
        return False
    try:
        fn = getattr(csv_win, "_run_aux_screen_play_preflight", None)
        if callable(fn):
            return bool(fn(kit_ext))
    except Exception as exc:
        print(f"{_PRINT_PREFIX} screen{si} play preflight: {exc}", flush=True)
        return False
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
        clear_csv_play_timeline_highlight,
        clear_csv_playback_stop,
        set_csv_play_progress_ui_callback,
        set_csv_play_timeline_highlight_callback,
    )

    si = max(1, int(screen))
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
    if registry is None or scheduler is None:
        return f"registry/scheduler missing for screen {si}"

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
        if callable(hl):
            hl(active_keys)

    def _play() -> None:
        with csv_play_screen_binding(si):
            try:
                clear_csv_playback_stop(screen=si)
                set_csv_play_progress_ui_callback(_on_play_ui, screen=si)
                set_csv_play_timeline_highlight_callback(
                    _on_timeline_highlight, screen=si
                )
                if not _run_federation_play_preflight(csv_win, si, ext):
                    print(
                        f"{_PRINT_PREFIX} screen{si} preflight 중단 — 재생 생략",
                        flush=True,
                    )
                    return
                try:
                    from .lam_traffic_light_emissive import on_csv_playback_started

                    on_csv_playback_started()
                except Exception:
                    pass
                run_simulation_from_csv(
                    registry,
                    scheduler,
                    prepared=cached,
                    speed_scale=speed_scale,
                    play_screen=si,
                    kit_ext=ext,
                    skip_play_prim_hide=True,
                )
            except Exception as exc:
                print(f"{_PRINT_PREFIX} screen{si} play failed: {exc}", flush=True)
            finally:
                try:
                    from .lam_traffic_light_emissive import (
                        on_csv_playback_paused_or_stopped,
                    )

                    on_csv_playback_paused_or_stopped()
                except Exception:
                    pass
                set_csv_play_progress_ui_callback(None, screen=si)
                set_csv_play_timeline_highlight_callback(None, screen=si)
                clear_csv_play_timeline_highlight(screen=si)

    threading.Thread(
        target=_play,
        name=f"lam-federation-play-s{si}",
        daemon=True,
    ).start()
    return None


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
    export_default_prerun: bool = True,
) -> ScreenPipelineResult:
    """이미 수집됐거나 사용자가 붙여넣은 응답만 파싱·프리런·재생한다."""
    meta: Dict[str, Any] = {"screen": screen}
    try:
        eqp_id = str(body.get("eqp_id") or "").strip()
        if not eqp_id:
            return ScreenPipelineResult(
                screen, False, "eqp_id missing in config body", meta
            )
        dwells, parse_stats = merged_response_to_dwells(merged, eqp_id=eqp_id)
        meta["parse"] = parse_stats
        if not dwells:
            return ScreenPipelineResult(screen, False, "no dwell records after parse", meta)
        vpath = federation_virtual_path(screen, body)
        cached = build_and_cache_from_dwells(vpath, dwells)
        prerun = build_prerun_result_from_cached(cached, screen=screen)
        if save_response_json:
            saved = _write_federation_parse_export(
                screen=screen,
                original=merged,
                parse_stats=parse_stats,
                dwells=dwells,
                prerun=prerun,
            )
            meta["saved_json"] = str(saved)
        elif export_default_prerun:
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
            f"duration={prerun.final_csv_time_sec:.1f}s",
            flush=True,
        )
        csv_win = _resolve_csv_play_window(lam_window, screen)
        # Play 여부와 무관하게 화면별 타임라인은 API 결과로 교체
        _apply_federation_timeline_ui(csv_win, cached)
        if auto_play:
            err = _start_federation_playback(
                ext,
                lam_window,
                csv_win,
                screen,
                cached,
                speed_scale=speed_scale,
            )
            if err:
                return ScreenPipelineResult(screen, False, err, meta)
        return ScreenPipelineResult(
            screen, True, "ok", meta, prerun=prerun, cached=cached
        )
    except Exception as exc:
        return ScreenPipelineResult(screen, False, str(exc), meta)


def _read_federation_defaults() -> Dict[str, Any]:
    try:
        from . import lam_sim_control_defaults as d

        return {
            "url": str(getattr(d, "FEDERATION_QUERY_URL", "") or ""),
            "limit": int(getattr(d, "FEDERATION_FETCH_LIMIT", 1000) or 1000),
            "timeout_sec": float(getattr(d, "FEDERATION_FETCH_TIMEOUT_SEC", 300.0) or 300.0),
            "use_fixture": bool(getattr(d, "FEDERATION_USE_FIXTURE", False)),
            "log_row_sample": int(getattr(d, "FEDERATION_LOG_ROW_SAMPLE", 5) or 5),
            "log_full_response": bool(getattr(d, "FEDERATION_LOG_FULL_RESPONSE", False)),
            "bearer_token": str(getattr(d, "FEDERATION_BEARER_TOKEN", "") or ""),
            "extra_headers": dict(getattr(d, "FEDERATION_EXTRA_HEADERS", {}) or {}),
        }
    except Exception:
        return {
            "url": "",
            "limit": 1000,
            "timeout_sec": 300.0,
            "use_fixture": False,
            "log_row_sample": 5,
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
) -> ScreenPipelineResult:
    meta: Dict[str, Any] = {"screen": screen}
    try:
        eqp_id = str(body.get("eqp_id") or "").strip()
        if not eqp_id:
            return ScreenPipelineResult(
                screen, False, "eqp_id missing in config body", meta
            )
        merged, fetch_meta = fetch_federation_pages(
            url=url,
            body=body,
            limit=limit,
            screen=screen,
            bearer_token=bearer_token,
            extra_headers=extra_headers,
            timeout_sec=timeout_sec,
            use_fixture=use_fixture,
            log_row_sample=log_row_sample,
            log_full_response=log_full_response,
        )
        meta["fetch"] = fetch_meta
        result = _process_merged_response(
            ext,
            lam_window,
            screen,
            body,
            merged,
            auto_play=auto_play,
            speed_scale=speed_scale,
        )
        result.meta["fetch"] = fetch_meta
        return result
    except Exception as exc:
        return ScreenPipelineResult(screen, False, str(exc), meta)


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
    """T2V ``configs`` payload → 화면 표시 + fetch + prerun + (옵션) 재생."""
    defaults = _read_federation_defaults()
    url = str(url_override or defaults["url"] or "").strip()
    if not url:
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

    def _work_after_visibility() -> Dict[str, Any]:
        lam_window = getattr(ext, "_lam_window", None) or getattr(ext, "_window", None)
        if lam_window is None:
            return _err("LAM window is not ready")
        jobs: List[Tuple[int, Dict[str, Any]]] = []
        if show_1:
            jobs.append((1, bodies[0]))
        if show_2:
            jobs.append((2, bodies[1]))
        if not jobs:
            return _err("both configs are empty — nothing to simulate")

        results: List[ScreenPipelineResult] = []
        with ThreadPoolExecutor(max_workers=min(2, len(jobs))) as pool:
            futs = {
                pool.submit(
                    _process_one_screen,
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
                    auto_play=auto_play,
                    speed_scale=speed_scale,
                ): screen
                for screen, body in jobs
            }
            for fut in as_completed(futs):
                results.append(fut.result())
        results.sort(key=lambda r: r.screen)
        failed = [r for r in results if not r.ok]
        if failed:
            return _err(
                "; ".join(f"screen{r.screen}: {r.message}" for r in failed),
                data={"screens": [_result_dict(r) for r in results]},
            )
        return _ok(
            {
                "screens": [_result_dict(r) for r in results],
                "show_1": show_1,
                "show_2": show_2,
            }
        )

    def _apply_visibility_then_run() -> None:
        try:
            request_screen_visibility(ext, show_1, show_2)
        except Exception as exc:
            _finish(on_complete, _err(f"screen visibility failed: {exc}"))
            return

        def _run_fetch() -> None:
            t0 = time.perf_counter()
            result = _work_after_visibility()
            result.setdefault("data", {})["elapsed_sec"] = time.perf_counter() - t0
            _finish(on_complete, result)

        threading.Thread(target=_run_fetch, name="lam-federation-start", daemon=True).start()

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
        try:
            request_screen_visibility(ext, si == 1, si == 2)
        except Exception as exc:
            _finish(on_complete, _err(f"screen visibility failed: {exc}"))
            return

        def _work() -> None:
            lam_window = getattr(ext, "_lam_window", None) or getattr(ext, "_window", None)
            if lam_window is None:
                _finish(on_complete, _err("LAM window is not ready"))
                return
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

        threading.Thread(
            target=_work,
            name=f"lam-federation-response-s{si}",
            daemon=True,
        ).start()

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
    if cb is None:
        code = int(result.get("code", 0))
        print(
            f"{_PRINT_PREFIX} done code={code} msg={result.get('message')}",
            flush=True,
        )
        return

    def _dispatch() -> None:
        try:
            cb(result)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} on_complete failed: {exc}", flush=True)

    schedule_on_main_thread(_dispatch)


__all__ = [
    "ScreenPipelineResult",
    "run_federation_response_simulation",
    "run_federation_start_simulation",
]
