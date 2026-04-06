# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
Kit 내 HTTP 브리지 — 브라우저에서 TBS 제어창·USD Load 와 동일 동작을 호출한다.

사용:
  확장 로드 시 기본으로 HTTP 브리지가 켜진다. 끄려면 TBS_REMOTE_UI=0 (또는 false, no, off).
  브라우저에서 http://127.0.0.1:<포트>/ 접속 (포트 기본 8720).

정적 파일: 확장 루트 web/tbs_kit_remote/
포트: TBS_REMOTE_UI_PORT (기본 8720)
바인드 주소: TBS_REMOTE_UI_BIND (기본 127.0.0.1 = 로컬만). 원격 브라우저는 0.0.0.0 등.

모든 ext / omni.ui 접근은 메인 스레드(업데이트 스트림)에서만 수행한다.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections import deque
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import omni.kit.app as app

from . import load_window
from .kit_chrome_visibility import apply_kit_chrome_hidden, is_kit_chrome_hidden
from .control_window import (
    SimLogPanelMode,
    on_copy_sim_progress,
    on_sim_ep_count_changed,
    on_sim_log_view_changed,
    on_sim_reset_clicked,
    on_sim_start_clicked,
    on_sim_stop_clicked,
    on_xml_ok_clicked,
    on_xml_run_clicked,
    on_xml_seq_changed,
    refresh_object_list,
)
from .load_window import DEFAULT_USD_URL
from .usd_loader_utils import get_resource_usd_list

_WEB_ROOT = Path(__file__).resolve().parent.parent.parent / "web" / "tbs_kit_remote"

_server: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_update_sub: Any = None
_ext_ref: Any = None
_pending_main: Deque[Tuple[Future, Callable[[], Any]]] = deque()
_pending_lock = threading.Lock()
_DEFAULT_PORT = 8720


def _run_on_main(fn: Callable[[], Any]) -> Any:
    fut: Future = Future()

    def _wrap() -> None:
        try:
            fut.set_result(fn())
        except Exception as e:
            fut.set_exception(e)

    with _pending_lock:
        _pending_main.append((fut, _wrap))
    return fut.result(timeout=120.0)


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


def _snapshot(ext: Any) -> Dict[str, Any]:
    def _txt(get_lbl, get_model):
        try:
            if get_lbl() is not None:
                t = get_lbl().text
                if t:
                    return t
        except Exception:
            pass
        try:
            if get_model() is not None:
                return get_model().as_string or ""
        except Exception:
            pass
        return ""

    usd_status = ""
    try:
        if getattr(ext, "_load_status_label", None) is not None:
            usd_status = ext._load_status_label.text or ""
    except Exception:
        pass

    progress = _txt(lambda: getattr(ext, "_sim_progress_label", None), lambda: getattr(ext, "_sim_progress_text", None))
    history = _txt(lambda: getattr(ext, "_sim_history_label", None), lambda: getattr(ext, "_sim_history_text", None))
    sim_line = _txt(lambda: getattr(ext, "_sim_history_label", None), lambda: getattr(ext, "_sim_history_text", None))

    port_header = "[포트상태]"
    try:
        if getattr(ext, "_sim_port_state_header_label", None) is not None:
            port_header = ext._sim_port_state_header_label.text or port_header
    except Exception:
        pass

    ports: Dict[str, str] = {}
    cells = getattr(ext, "_sim_port_cells", None) or {}
    for name in ("BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3"):
        lbl = cells.get(name)
        try:
            if lbl is not None:
                raw = (lbl.text or "").strip()
                if ":" in raw:
                    ports[name] = raw.split(":", 1)[-1].strip() or "-"
                else:
                    ports[name] = raw or "-"
            else:
                ports[name] = "-"
        except Exception:
            ports[name] = "-"

    ep3_visible = True
    try:
        c = getattr(ext, "_sim_port_ep3_cell_container", None)
        if c is not None:
            ep3_visible = bool(c.visible)
    except Exception:
        pass

    kit_app = ""
    try:
        kit_app = app.get_app().get_name() or ""
    except Exception:
        pass

    return {
        "usd_status": usd_status,
        "sim_line": sim_line,
        "progress": progress,
        "history": history,
        "port_header": port_header,
        "ports": ports,
        "ep3_visible": ep3_visible,
        "kit_app": kit_app,
        "kit_chrome_hidden": is_kit_chrome_hidden(ext),
    }


def _apply_web_fields(ext: Any, f: Dict[str, Any]) -> None:
    if not f:
        return

    def _i(key: str, default: int = 0) -> int:
        try:
            return int(f.get(key, default))
        except Exception:
            return default

    def _f(key: str, default: float = 0.0) -> float:
        try:
            return float(f.get(key, default))
        except Exception:
            return default

    def _b(key: str) -> bool:
        return bool(f.get(key))

    try:
        ext._sim_lot_count_model.set_value_as_int(max(1, _i("lot_count", 6)))
    except Exception:
        pass
    try:
        ext._sim_ep_count_combo.model.get_item_value_model().set_value(0 if _i("ep_count_index", 0) == 0 else 1)
        on_sim_ep_count_changed(ext)
    except Exception:
        pass
    try:
        ext._sim_lot_spawn_min_model.set_value_as_float(max(0.1, _f("lot_spawn_min", 15.0)))
        ext._sim_lot_spawn_max_model.set_value_as_float(max(0.1, _f("lot_spawn_max", 40.0)))
        ext._sim_pickup_evt_min_model.set_value_as_float(max(0.1, _f("pickup_min", 50.0)))
        ext._sim_pickup_evt_max_model.set_value_as_float(max(0.1, _f("pickup_max", 70.0)))
        ext._sim_speed_model.set_value_as_float(max(0.1, _f("speed", 1.0)))
        ext._sim_log_interval_model.set_value_as_float(max(0.0, _f("log_interval", 0.0)))
        ext._sim_confirm_each_step_model.set_value_as_bool(_b("confirm_each"))
        ext._sim_oht_bp1_min_model.set_value_as_float(max(0.1, _f("oht_min", 5.0)))
        ext._sim_oht_bp1_max_model.set_value_as_float(max(0.1, _f("oht_max", 10.0)))
        ext._sim_bp1_bp_min_model.set_value_as_float(max(0.1, _f("bp1_bp_min", 5.0)))
        ext._sim_bp1_bp_max_model.set_value_as_float(max(0.1, _f("bp1_bp_max", 10.0)))
        ext._sim_bp_ep_min_model.set_value_as_float(max(0.1, _f("bp_ep_min", 5.0)))
        ext._sim_bp_ep_max_model.set_value_as_float(max(0.1, _f("bp_ep_max", 10.0)))
        ext._sim_ep_oht_min_model.set_value_as_float(max(0.1, _f("ep_oht_min", 5.0)))
        ext._sim_ep_oht_max_model.set_value_as_float(max(0.1, _f("ep_oht_max", 10.0)))
    except Exception:
        pass
    try:
        ext._sim_init_bp1_model.set_value_as_bool(_b("init_bp1"))
        ext._sim_init_bp2_model.set_value_as_bool(_b("init_bp2"))
        ext._sim_init_bp3_model.set_value_as_bool(_b("init_bp3"))
        ext._sim_init_bp4_model.set_value_as_bool(_b("init_bp4"))
        ext._sim_init_ep1_model.set_value_as_bool(_b("init_ep1"))
        ext._sim_init_ep2_model.set_value_as_bool(_b("init_ep2"))
        ext._sim_init_ep3_model.set_value_as_bool(_b("init_ep3"))
    except Exception:
        pass
    try:
        ext._priority_prefix_model.set_value_as_string(str(f.get("priority_prefix", "") or ""))
    except Exception:
        pass
    try:
        idx = max(0, min(6, _i("xml_seq_index", 0)))
        ext._xml_seq_combo.model.get_item_value_model().set_value(idx)
        on_xml_seq_changed(ext)
    except Exception:
        pass
    try:
        ext._xml_from_port_model.set_value_as_int(_i("xml_from", 1))
        ext._xml_to_port_model.set_value_as_int(_i("xml_to", 6))
        ext._xml_port_id_model.set_value_as_int(_i("xml_port_id", 1))
    except Exception:
        pass


def _dispatch_command(ext: Any, data: Dict[str, Any]) -> Dict[str, Any]:
    cmd = str(data.get("cmd", "") or "").strip()
    if cmd == "load_usd":
        path = str(data.get("path", "") or "").strip()
        ri = int(data.get("resource_index", 0) or 0)
        try:
            ext._path_model.set_value_as_string(path or getattr(ext, "DEFAULT_USD_URL", DEFAULT_USD_URL))
        except Exception:
            pass
        try:
            if getattr(ext, "_resource_combo", None) is not None and getattr(ext, "_resource_names", None):
                n = len(ext._resource_names)
                ext._resource_combo.model.get_item_value_model().set_value(max(0, min(ri, n - 1)))
        except Exception:
            pass
        asyncio.ensure_future(load_window.on_load_usd(ext))
        return {"ok": True}

    if cmd == "sim_start":
        fields = data.get("fields")
        if isinstance(fields, dict):
            _apply_web_fields(ext, fields)
        on_sim_start_clicked(ext)
        return {"ok": True}

    if cmd == "sim_stop":
        on_sim_stop_clicked(ext)
        return {"ok": True}

    if cmd == "sim_reset":
        on_sim_reset_clicked(ext)
        return {"ok": True}

    if cmd == "prim_refresh":
        refresh_object_list(ext)
        return {"ok": True}

    if cmd == "log_mode":
        idx = int(data.get("index", 0) or 0)
        if idx > int(SimLogPanelMode.HISTORY_ONLY):
            idx = int(SimLogPanelMode.ALL)
        try:
            ext._sim_log_view_combo.model.get_item_value_model().set_value(idx)
            on_sim_log_view_changed(ext)
        except Exception:
            pass
        return {"ok": True}

    if cmd == "copy_progress":
        on_copy_sim_progress(ext)
        return {"ok": True}

    if cmd == "xml_ok":
        fields = data.get("fields")
        if isinstance(fields, dict):
            _apply_web_fields(ext, fields)
        on_xml_ok_clicked(ext)
        return {"ok": True}

    if cmd == "xml_run":
        on_xml_run_clicked(ext)
        return {"ok": True}

    if cmd == "kit_chrome_hide":
        hidden = bool(data.get("hidden", False))
        apply_kit_chrome_hidden(ext, hidden)
        try:
            m = getattr(ext, "_kit_chrome_hide_model", None)
            if m is not None:
                if hasattr(m, "set_value"):
                    m.set_value(hidden)
                elif hasattr(m, "set_value_as_bool"):
                    m.set_value_as_bool(hidden)
        except Exception:
            pass
        return {"ok": True}

    return {"ok": False, "error": f"unknown cmd: {cmd}"}


def _resources_json() -> Dict[str, Any]:
    items: List[Dict[str, str]] = []
    try:
        for name, path in get_resource_usd_list():
            items.append({"name": name, "path": path})
    except Exception:
        pass
    return {"items": items}


class _TbsRemoteHandler(BaseHTTPRequestHandler):
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
        global _ext_ref
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/api/state":
            if _ext_ref is None:
                self._send(503, b'{"error":"ext not ready"}', "application/json; charset=utf-8", cors=True)
                return
            try:
                snap = _run_on_main(lambda: _snapshot(_ext_ref))
                body = json.dumps(snap, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8", cors=True)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}).encode("utf-8"), "application/json; charset=utf-8", cors=True)
            return
        if path == "/api/resources":
            body = json.dumps(_resources_json(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8", cors=True)
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
        global _ext_ref
        if self.path.split("?", 1)[0].rstrip("/") != "/api/command":
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        if _ext_ref is None:
            self._send(503, b'{"error":"ext not ready"}', "application/json; charset=utf-8", cors=True)
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
        try:
            result = _run_on_main(lambda: _dispatch_command(_ext_ref, data))
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8", cors=True)
        except Exception as e:
            self._send(500, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"), "application/json; charset=utf-8", cors=True)


def start_tbs_remote_http_bridge(ext: Any) -> None:
    global _server, _server_thread, _update_sub, _ext_ref
    _ext_ref = ext
    if _WEB_ROOT.is_dir():
        pass
    else:
        try:
            print(f"[TBS Remote UI] web 폴더 없음: {_WEB_ROOT}", flush=True)
        except Exception:
            pass

    try:
        _update_sub = app.get_app().get_update_event_stream().create_subscription_to_pop(
            _pump_main_queue,
            name="morph.tbs_control_1:tbs_remote_main_queue",
        )
    except Exception as e:
        try:
            print(f"[TBS Remote UI] 업데이트 구독 실패: {e}", flush=True)
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
        _server = ThreadingHTTPServer((bind, port), _TbsRemoteHandler)
    except OSError as e:
        try:
            print(f"[TBS Remote UI] 바인드 실패 {bind}:{port} — {e}", flush=True)
        except Exception:
            pass
        return

    def _serve() -> None:
        try:
            _server.serve_forever(poll_interval=0.5)
        except Exception:
            pass

    _server_thread = threading.Thread(target=_serve, name="tbs_remote_http", daemon=True)
    _server_thread.start()
    try:
        if bind == "0.0.0.0":
            print(
                f"[TBS Remote UI] listen {bind}:{port} — 로컬: http://127.0.0.1:{port}/ | "
                f"원격 PC 브라우저: http://<이-Kit-PC의-LAN-IP>:{port}/",
                flush=True,
            )
        else:
            print(f"[TBS Remote UI] http://{bind}:{port}/  (정적+API)", flush=True)
    except Exception:
        pass


def stop_tbs_remote_http_bridge() -> None:
    global _server, _server_thread, _update_sub, _ext_ref
    _ext_ref = None
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
