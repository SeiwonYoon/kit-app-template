# SPDX-FileCopyrightText: Copyright (c) 2026 Morph. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""LAM Kit HTTP 브리지 — ``morph.lam_control.remote_api`` 세션에 연결.

- 포트/환경: ``TBS_REMOTE_UI`` / ``TBS_REMOTE_UI_PORT`` (기본 8720) / ``TBS_REMOTE_UI_BIND``
- 정적 UI: ``web/lam_kit_remote/`` (index.html + lam_panel.js + lam_panel.css)
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import omni.kit.app as app

from morph.lam_control.remote_api import LamKitSession, get_session

_PRINT_PREFIX = "[LAM Remote UI]"

_WEB_ROOT = Path(__file__).resolve().parent.parent.parent / "web" / "lam_kit_remote"

_server: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_update_sub: Any = None
_session_ref: Optional[LamKitSession] = None
_pending_main: Deque[Tuple[Future, Callable[[], Any]]] = deque()
_pending_lock = threading.Lock()

_csv_play_thread: Optional[threading.Thread] = None
_csv_build_thread: Optional[threading.Thread] = None

_web_state_lock = threading.Lock()
_web_state: Dict[str, Any] = {
    "log": "(대기)",
    "master_path": "",
    "csv_dir": "",
    "csv_selected": "",
    "csv_files": [],
    "schedule": "(CSV 선택 후 [타임라인 갱신] 또는 Play)",
    "progress": "(빌드·재생 진행 — 대기)",
}

_DEFAULT_PORT = 8720


def _run_on_main(fn: Callable[[], Any], *, timeout: float = 120.0) -> Any:
    fut: Future = Future()

    def _wrap() -> None:
        try:
            fut.set_result(fn())
        except Exception as e:
            fut.set_exception(e)

    with _pending_lock:
        _pending_main.append((fut, _wrap))
    return fut.result(timeout=timeout)


def _pump_main_queue(_e: Any) -> None:
    while True:
        with _pending_lock:
            if not _pending_main:
                break
            _, run = _pending_main.popleft()
        try:
            run()
        except Exception:
            pass


def _set_web_log(msg: str) -> None:
    with _web_state_lock:
        _web_state["log"] = str(msg or "")


def _csv_play_thread_alive() -> bool:
    t = _csv_play_thread
    return t is not None and t.is_alive()


def _csv_build_thread_alive() -> bool:
    t = _csv_build_thread
    return t is not None and t.is_alive()


def _resolve_csv_path(csv_path: str, csv_dir: str) -> Optional[Path]:
    from morph.lam_control.simulation_play import resolve_csv_path

    p = (csv_path or "").strip()
    if p:
        return Path(resolve_csv_path(p))
    d = (csv_dir or "").strip()
    if d:
        from morph.lam_control.simulation_play import list_csv_paths_in_directory

        files = list_csv_paths_in_directory(d)
        if files:
            return files[0]
    return None


def _snapshot(session: LamKitSession) -> Dict[str, Any]:
    from morph.lam_control.lam_window import default_load_usd_path, resolve_default_load_usd_path

    with _web_state_lock:
        mp = str(_web_state.get("master_path") or "")
        if not mp:
            mp = resolve_default_load_usd_path(default_load_usd_path)
        return {
            "log": str(_web_state.get("log") or ""),
            "master_path": mp,
            "instance_count": session.instance_count(),
            "csv_dir": str(_web_state.get("csv_dir") or ""),
            "csv_selected": str(_web_state.get("csv_selected") or ""),
            "csv_files": list(_web_state.get("csv_files") or []),
            "schedule": str(_web_state.get("schedule") or ""),
            "progress": str(_web_state.get("progress") or ""),
            "playing": _csv_play_thread_alive(),
            "building": _csv_build_thread_alive(),
        }


def _cmd_open_master(session: LamKitSession, data: Dict[str, Any]) -> Dict[str, Any]:
    from morph.lam_control.lam_window import default_load_usd_path, resolve_default_load_usd_path

    raw = str(data.get("path") or data.get("master_path") or "").strip()
    if not raw:
        raw = default_load_usd_path
    resolved = resolve_default_load_usd_path(raw)
    if not resolved:
        _set_web_log("합성 USD 경로가 비어 있습니다.")
        return {"ok": False, "error": "empty path"}
    if not os.path.isfile(resolved):
        _set_web_log(f"파일 없음: {resolved}")
        return {"ok": False, "error": "file not found", "path": resolved}

    ok = bool(session.open_master_at_path(resolved, log_prefix="Web"))
    with _web_state_lock:
        _web_state["master_path"] = resolved
    _set_web_log(f"Open Master {'OK' if ok else 'FAIL'}: {resolved}")
    return {"ok": ok, "path": resolved}


def _cmd_csv_refresh_list(data: Dict[str, Any]) -> Dict[str, Any]:
    from morph.lam_control.simulation_play import get_lam_csv_dir, list_csv_paths_in_directory

    csv_dir = str(data.get("csv_dir") or "").strip()
    if not csv_dir:
        csv_dir = str(get_lam_csv_dir())
    files = list_csv_paths_in_directory(csv_dir)
    items = [{"name": p.name, "path": str(p)} for p in files]
    prev = ""
    with _web_state_lock:
        prev = str(_web_state.get("csv_selected") or "")
        _web_state["csv_dir"] = csv_dir
        _web_state["csv_files"] = items
        if items:
            if prev and any(it["path"] == prev for it in items):
                _web_state["csv_selected"] = prev
            else:
                _web_state["csv_selected"] = items[0]["path"]
        else:
            _web_state["csv_selected"] = ""
    _set_web_log(f"CSV {len(items)}개 — {csv_dir}")
    return {"ok": True, "csv_dir": csv_dir, "items": items}


def _cmd_csv_timeline_refresh(session: LamKitSession, data: Dict[str, Any]) -> Dict[str, Any]:
    global _csv_build_thread
    _ = session
    if _csv_build_thread_alive():
        return {"ok": False, "error": "build already running"}

    csv_dir = str(data.get("csv_dir") or "").strip()
    csv_path = str(data.get("csv_path") or data.get("path") or "").strip()
    with _web_state_lock:
        if not csv_dir:
            csv_dir = str(_web_state.get("csv_dir") or "")
        if not csv_path:
            csv_path = str(_web_state.get("csv_selected") or "")
    path = _resolve_csv_path(csv_path, csv_dir)
    if path is None or not path.is_file():
        _set_web_log("CSV 파일 없음 — 폴더·파일을 확인하세요.")
        return {"ok": False, "error": "no csv"}

    try:
        sp = float(max(0.1, min(20.0, float(data.get("speed_scale", 1.0) or 1.0))))
    except Exception:
        sp = 1.0

    from morph.lam_control.simulation_play import (
        CachedCsvPlayback,
        _csv_cache_key,
        _csv_playback_cache,
        _csv_playback_cache_lock,
        _csv_playback_config_tag,
        build_csv_playback_plan,
        format_csv_playback_schedule,
        get_cached_csv_playback,
        load_csv_dwell_timeline,
        preview_csv_playback_schedule,
        set_csv_playback_compact_log,
    )

    hit = get_cached_csv_playback(path)
    if hit is not None:
        text = format_csv_playback_schedule(hit.schedule, speed_scale=sp)
        with _web_state_lock:
            _web_state["csv_selected"] = str(path)
            _web_state["schedule"] = text
            _web_state["progress"] = (
                f"준비 완료 (캐시) — dwell {len(hit.dwells)} · "
                f"JSON {sum(1 for e in hit.schedule if e.category != 'dwell')}건"
            )
        _set_web_log(f"타임라인 (캐시): {path.name}")
        return {"ok": True, "cached": True}

    try:
        meta = preview_csv_playback_schedule(str(path), speed_scale=sp, use_cache=False)
        with _web_state_lock:
            _web_state["schedule"] = meta
            _web_state["progress"] = "(미리보기 — 전체 빌드 중…)"
    except Exception as exc:
        with _web_state_lock:
            _web_state["schedule"] = f"미리보기 실패:\n{exc}"
        return {"ok": False, "error": str(exc)}

    def _worker() -> None:
        global _csv_build_thread
        t0 = time.perf_counter()
        try:
            set_csv_playback_compact_log(True)
            dwells = load_csv_dwell_timeline(path)
            schedule, blocks = build_csv_playback_plan(dwells)
            try:
                st = path.stat()
                mtime_ns, size = int(st.st_mtime_ns), int(st.st_size)
            except OSError:
                mtime_ns, size = 0, 0
            cached = CachedCsvPlayback(
                path=path,
                mtime_ns=mtime_ns,
                size=size,
                config_tag=_csv_playback_config_tag(),
                dwells=dwells,
                schedule=schedule,
                blocks=blocks,
                build_ms=(time.perf_counter() - t0) * 1000.0,
            )
            with _csv_playback_cache_lock:
                _csv_playback_cache[_csv_cache_key(path)] = cached
            text = format_csv_playback_schedule(schedule, speed_scale=sp)
            n_json = sum(1 for e in schedule if e.category != "dwell")
            with _web_state_lock:
                _web_state["csv_selected"] = str(path)
                _web_state["schedule"] = text
                _web_state["progress"] = (
                    f"타임라인·캐시 빌드 완료 — {path.name} | "
                    f"소요 {time.perf_counter() - t0:.1f}s | JSON {n_json}건"
                )
            _set_web_log(f"타임라인·캐시 빌드 완료: {path.name}")
        except Exception as exc:
            with _web_state_lock:
                _web_state["progress"] = f"빌드 실패: {exc}"
            _set_web_log(f"빌드 실패: {exc}")
        finally:
            set_csv_playback_compact_log(False)
            _csv_build_thread = None

    _csv_build_thread = threading.Thread(
        target=_worker, daemon=True, name="lam-web-csv-build"
    )
    _csv_build_thread.start()
    _set_web_log(f"타임라인·캐시 빌드 시작: {path.name}")
    return {"ok": True, "building": True}


def _cmd_csv_play(session: LamKitSession, data: Dict[str, Any]) -> Dict[str, Any]:
    global _csv_play_thread
    if _csv_play_thread_alive():
        return {"ok": False, "error": "play already running"}

    csv_dir = str(data.get("csv_dir") or "").strip()
    csv_path = str(data.get("csv_path") or data.get("path") or "").strip()
    with _web_state_lock:
        if not csv_dir:
            csv_dir = str(_web_state.get("csv_dir") or "")
        if not csv_path:
            csv_path = str(_web_state.get("csv_selected") or "")
    path = _resolve_csv_path(csv_path, csv_dir)
    if path is None or not path.is_file():
        _set_web_log("CSV Play: 파일 없음")
        return {"ok": False, "error": "no csv"}

    try:
        sp = float(max(0.1, min(20.0, float(data.get("speed_scale", 1.0) or 1.0))))
    except Exception:
        sp = 1.0

    registry = session.registry
    scheduler = session.scheduler

    def _on_play_ui(csv_t: float, csv_total: float, wall_el: float, wall_tot: float) -> None:
        pct = (100.0 * csv_t / csv_total) if csv_total > 1e-6 else 0.0
        line = (
            f"▶ 재생 {pct:.1f}% | CSV t {csv_t:.1f}/{csv_total:.1f}s | "
            f"실경과 {wall_el:.0f}s/{wall_tot:.0f}s"
        )
        with _web_state_lock:
            _web_state["progress"] = line

    def _worker() -> None:
        global _csv_play_thread
        from morph.lam_control.simulation_play import (
            get_cached_csv_playback,
            run_simulation_from_csv,
            set_csv_play_progress_ui_callback,
            set_csv_playback_compact_log,
        )

        try:
            set_csv_play_progress_ui_callback(_on_play_ui)
            set_csv_playback_compact_log(True)
            prepared = get_cached_csv_playback(path)
            with _web_state_lock:
                _web_state["csv_selected"] = str(path)
            _set_web_log(f"CSV Play 시작: {path.name} (배속 {sp:g}x)")
            run_simulation_from_csv(
                registry,
                scheduler,
                csv_path=str(path),
                speed_scale=sp,
                prepared=prepared,
            )
            _set_web_log("CSV Play 완료")
        except Exception as exc:
            _set_web_log(f"CSV Play 오류: {exc}")
            print(f"{_PRINT_PREFIX} CSV Play error: {exc}", flush=True)
        finally:
            set_csv_play_progress_ui_callback(None)
            set_csv_playback_compact_log(False)
            with _web_state_lock:
                _web_state["progress"] = "(재생 종료)"
            _csv_play_thread = None

    _csv_play_thread = threading.Thread(
        target=_worker, daemon=True, name="lam-web-csv-play"
    )
    _csv_play_thread.start()
    return {"ok": True}


def _cmd_csv_stop(session: LamKitSession, _data: Dict[str, Any]) -> Dict[str, Any]:
    from morph.lam_control.simulation_play import request_stop_csv_playback

    if not _csv_play_thread_alive():
        _set_web_log("CSV Play 가 실행 중이 아닙니다.")
        return {"ok": False, "error": "not playing"}
    request_stop_csv_playback(session.registry, session.scheduler)
    _set_web_log("CSV Play 중지 요청")
    return {"ok": True}


def _dispatch_command(session: LamKitSession, data: Dict[str, Any]) -> Dict[str, Any]:
    cmd = str(data.get("cmd", "") or "").strip()
    if cmd == "open_master":
        return _cmd_open_master(session, data)
    if cmd == "csv_refresh_list":
        return _cmd_csv_refresh_list(data)
    if cmd == "csv_timeline_refresh":
        return _cmd_csv_timeline_refresh(session, data)
    if cmd == "csv_play":
        return _cmd_csv_play(session, data)
    if cmd == "csv_stop":
        return _cmd_csv_stop(session, data)
    return {"ok": False, "error": f"unknown cmd: {cmd}"}


class _LamRemoteHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str, *, cors: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        if self.path.split("?", 1)[0].rstrip("/").startswith("/api"):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Connection", "close")
            self.end_headers()
        else:
            self.send_error(404)

    def do_GET(self) -> None:
        global _session_ref
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/api/state":
            if _session_ref is None:
                self._send(503, b'{"error":"lam not ready"}', "application/json; charset=utf-8", cors=True)
                return
            try:
                snap = _run_on_main(lambda: _snapshot(_session_ref))
                body = json.dumps(snap, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8", cors=True)
            except Exception as e:
                self._send(
                    500,
                    json.dumps({"error": str(e)}).encode("utf-8"),
                    "application/json; charset=utf-8",
                    cors=True,
                )
            return

        if path == "/":
            path = "/index.html"
        rel = path.lstrip("/").replace("..", "")
        fp = _WEB_ROOT / rel
        if not fp.is_file():
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        data = fp.read_bytes()
        ct = "application/octet-stream"
        if fp.suffix.lower() == ".html":
            ct = "text/html; charset=utf-8"
        elif fp.suffix.lower() == ".css":
            ct = "text/css; charset=utf-8"
        elif fp.suffix.lower() == ".js":
            ct = "application/javascript; charset=utf-8"
        self._send(200, data, ct)

    def do_POST(self) -> None:
        global _session_ref
        if self.path.split("?", 1)[0].rstrip("/") != "/api/command":
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        if _session_ref is None:
            self._send(503, b'{"error":"lam not ready"}', "application/json; charset=utf-8", cors=True)
            return
        try:
            ln = int(self.headers.get("Content-Length", "0") or "0")
        except Exception:
            ln = 0
        raw = self.rfile.read(ln) if ln > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, b'{"error":"invalid json"}', "application/json; charset=utf-8", cors=True)
            return
        if not isinstance(data, dict):
            self._send(400, b'{"error":"body must be object"}', "application/json; charset=utf-8", cors=True)
            return
        sess = _session_ref

        try:
            result = _run_on_main(lambda: _dispatch_command(sess, data))
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8", cors=True)
        except Exception as e:
            self._send(
                500,
                json.dumps({"ok": False, "error": str(e)}).encode("utf-8"),
                "application/json; charset=utf-8",
                cors=True,
            )


def start_lam_remote_http_bridge() -> None:
    """``morph.lam_control.remote_api.get_session()`` 이 준비된 뒤 호출."""
    global _server, _server_thread, _update_sub, _session_ref

    session = get_session()
    if session is None:
        raise RuntimeError("morph.lam_control session is not registered")

    from morph.lam_control.lam_window import default_load_usd_path, resolve_default_load_usd_path
    from morph.lam_control.simulation_play import get_lam_csv_dir

    _session_ref = session
    csv_dir = str(get_lam_csv_dir())
    with _web_state_lock:
        _web_state["master_path"] = resolve_default_load_usd_path(default_load_usd_path)
        _web_state["csv_dir"] = csv_dir
        if not _web_state.get("csv_selected"):
            _web_state["csv_selected"] = ""

    if not _WEB_ROOT.is_dir():
        try:
            print(f"{_PRINT_PREFIX} web 폴더 없음: {_WEB_ROOT}", flush=True)
        except Exception:
            pass

    try:
        _update_sub = app.get_app().get_update_event_stream().create_subscription_to_pop(
            _pump_main_queue,
            name="morph.lam_web_bridge:lam_remote_main_queue",
        )
    except Exception as e:
        try:
            print(f"{_PRINT_PREFIX} 업데이트 구독 실패: {e}", flush=True)
        except Exception:
            pass
        return

    port = _DEFAULT_PORT
    try:
        port = int(os.environ.get("TBS_REMOTE_UI_PORT", str(_DEFAULT_PORT)).strip())
    except Exception:
        port = _DEFAULT_PORT

    bind = (os.environ.get("TBS_REMOTE_UI_BIND", "127.0.0.1") or "127.0.0.1").strip()
    if bind in ("*", "all", "ANY"):
        bind = "0.0.0.0"

    try:
        _server = ThreadingHTTPServer((bind, port), _LamRemoteHandler)
    except OSError as e:
        try:
            print(f"{_PRINT_PREFIX} 바인드 실패 {bind}:{port} — {e}", flush=True)
        except Exception:
            pass
        return

    def _serve() -> None:
        try:
            _server.serve_forever(poll_interval=0.5)
        except Exception:
            pass

    _server_thread = threading.Thread(target=_serve, name="lam_remote_http", daemon=True)
    _server_thread.start()
    try:
        if bind == "0.0.0.0":
            print(
                f"{_PRINT_PREFIX} listen {bind}:{port} — 로컬: http://127.0.0.1:{port}/ | "
                f"원격: http://<LAN-IP>:{port}/",
                flush=True,
            )
        else:
            print(f"{_PRINT_PREFIX} http://{bind}:{port}/  (LAM 정적+API)", flush=True)
    except Exception:
        pass


def stop_lam_remote_http_bridge() -> None:
    global _server, _server_thread, _update_sub, _session_ref, _csv_play_thread, _csv_build_thread
    _session_ref = None
    if _csv_play_thread is not None and _csv_play_thread.is_alive():
        try:
            from morph.lam_control.simulation_play import request_stop_csv_playback

            request_stop_csv_playback()
        except Exception:
            pass
    _csv_play_thread = None
    _csv_build_thread = None
    if _update_sub is not None:
        try:
            _update_sub.unsubscribe()
        except Exception:
            pass
        _update_sub = None
    if _server is not None:
        try:
            _server.shutdown()
        except Exception:
            pass
        try:
            _server.server_close()
        except Exception:
            pass
        _server = None
    _server_thread = None


__all__ = [
    "start_lam_remote_http_bridge",
    "stop_lam_remote_http_bridge",
]
