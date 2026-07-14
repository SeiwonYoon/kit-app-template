"""Federation fetch → 파싱 → prerun → 화면별 CSV 시뮬 재생."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .kit_main_dispatch import schedule_on_main_thread
from .lam_api_timeline_parser import (
    federation_virtual_path,
    merged_response_to_dwells,
    normalize_configs,
)
from .lam_csv_play_screen import get_registry_scheduler_for_lam_screen
from .lam_csv_prerun_playback import build_prerun_result_from_cached, maybe_export_csv_prerun_json
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
        dwells, parse_stats = merged_response_to_dwells(merged, eqp_id=eqp_id)
        meta["parse"] = parse_stats
        if not dwells:
            return ScreenPipelineResult(screen, False, "no dwell records after parse", meta)
        vpath = federation_virtual_path(screen, body)
        cached = build_and_cache_from_dwells(vpath, dwells)
        prerun = build_prerun_result_from_cached(cached, screen=screen)
        maybe_export_csv_prerun_json(prerun)
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
        if auto_play:
            registry, scheduler = get_registry_scheduler_for_lam_screen(
                lam_window, screen, allow_fallback=False
            )
            if registry is None or scheduler is None:
                return ScreenPipelineResult(
                    screen, False, f"registry/scheduler missing for screen {screen}", meta
                )

            def _play() -> None:
                run_simulation_from_csv(
                    registry,
                    scheduler,
                    prepared=cached,
                    speed_scale=speed_scale,
                    play_screen=screen,
                    kit_ext=ext,
                )

            threading.Thread(
                target=_play,
                name=f"lam-federation-play-s{screen}",
                daemon=True,
            ).start()
        return ScreenPipelineResult(
            screen, True, "ok", meta, prerun=prerun, cached=cached
        )
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
    "run_federation_start_simulation",
]
