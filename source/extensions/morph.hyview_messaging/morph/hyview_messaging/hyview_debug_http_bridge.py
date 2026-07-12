"""
HyView T2V/V2T 로컬 디버그 HTTP 브리지.

스트리밍(livestream WebRTC) 없이 morph.editor.kit 에서 ebs_handler 경로를 검증한다.

  POST /hyview/t2v   — T2V 주입 (carb eventdispatcher → EBSHandler)
  GET  /hyview/v2t   — V2T 수신 ring buffer (폴링)
  GET  /hyview/health

활성화: 환경변수 ``TBS_HYVIEW_DEBUG_HTTP=1`` (기본 1, ``0`` 이면 비활성)
포트:   ``TBS_HYVIEW_DEBUG_HTTP_PORT`` (기본 8721)
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

import carb
from carb.eventdispatcher import get_eventdispatcher

# ebs_handler.get_outgoing_events() 와 동기화 (신규 V2T 추가 시 여기도)
_V2T_EVENT_TYPES: Tuple[str, ...] = (
    "V2T_response_eqp_change",
    "V2T_response_ebs_enable",
    "V2T_response_start_simulation",
    "V2T_response_control_simulation",
    "V2T_response_simulation_timeline",
)

_DEFAULT_PORT = 8721
_DEFAULT_BIND = "127.0.0.1"
_MAX_V2T_BUFFER = 500

_server: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_v2t_lock = threading.Lock()
_v2t_buffer: List[Dict[str, Any]] = []
_v2t_seq = 0
_v2t_subscriptions: List[Any] = []


def _env_enabled() -> bool:
    raw = os.environ.get("TBS_HYVIEW_DEBUG_HTTP", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _env_port() -> int:
    try:
        return max(1, min(65535, int(os.environ.get("TBS_HYVIEW_DEBUG_HTTP_PORT", str(_DEFAULT_PORT)))))
    except Exception:
        return _DEFAULT_PORT


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: Dict[str, Any]) -> None:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(data)


def _read_json_body(handler: BaseHTTPRequestHandler) -> Optional[Dict[str, Any]]:
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
    except Exception:
        length = 0
    if length <= 0:
        return {}
    try:
        raw = handler.rfile.read(length)
        obj = json.loads(raw.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _append_v2t(event_type: str, payload: Any) -> None:
    global _v2t_seq
    if not str(event_type or "").startswith("V2T_"):
        return
    entry = {
        "seq": 0,
        "ts": round(time.time(), 3),
        "event_type": str(event_type),
        "payload": dict(payload) if isinstance(payload, dict) else {},
    }
    with _v2t_lock:
        _v2t_seq += 1
        entry["seq"] = _v2t_seq
        _v2t_buffer.append(entry)
        if len(_v2t_buffer) > _MAX_V2T_BUFFER:
            del _v2t_buffer[: len(_v2t_buffer) - _MAX_V2T_BUFFER]


def _clear_v2t_buffer() -> None:
    global _v2t_seq
    with _v2t_lock:
        _v2t_buffer.clear()
        _v2t_seq = 0


def _get_v2t_since(since: int) -> Dict[str, Any]:
    with _v2t_lock:
        events = [e for e in _v2t_buffer if int(e.get("seq", 0)) > int(since)]
        latest = _v2t_seq
    return {"events": events, "latest_seq": latest, "count": len(events)}


def _dispatch_t2v_on_main(event_type: str, payload: Dict[str, Any]) -> None:
    """HTTP 스레드 → Kit 메인 스레드에서 T2V dispatch (livestream 과 동일 handler 경로)."""
    try:
        from morph.tbs_control_2.kit_main_dispatch import schedule_on_main_thread
    except Exception:
        get_eventdispatcher().dispatch_event(str(event_type), payload=dict(payload))
        return

    def _work() -> None:
        get_eventdispatcher().dispatch_event(str(event_type), payload=dict(payload))

    schedule_on_main_thread(_work)


def _install_v2t_observers() -> None:
    ed = get_eventdispatcher()
    for event_type in _V2T_EVENT_TYPES:
        try:
            sub = ed.observe_event(
                observer_name=f"HyViewDebugHttp:{event_type}",
                event_name=event_type,
                on_event=lambda e, et=event_type: _append_v2t(et, getattr(e, "payload", None)),
            )
            _v2t_subscriptions.append(sub)
        except Exception as exc:
            carb.log_warn(f"[HyViewDebugHttp] V2T observe failed {event_type}: {exc}")


def _shutdown_v2t_observers() -> None:
    _v2t_subscriptions.clear()


class _HyViewDebugHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        carb.log_info(f"[HyViewDebugHttp] {self.address_string()} {format % args}")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = (self.path or "").split("?", 1)[0]
        if path == "/hyview/health":
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "service": "hyview_debug_http",
                    "v2t_latest_seq": _get_v2t_since(0)["latest_seq"],
                },
            )
            return
        if path == "/hyview/v2t":
            since = 0
            if "?" in (self.path or ""):
                qs = (self.path or "").split("?", 1)[1]
                for part in qs.split("&"):
                    if part.startswith("since="):
                        try:
                            since = int(part.split("=", 1)[1])
                        except Exception:
                            since = 0
            _json_response(self, 200, _get_v2t_since(since))
            return
        _json_response(self, 404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        path = (self.path or "").split("?", 1)[0]
        if path != "/hyview/t2v":
            _json_response(self, 404, {"ok": False, "error": "not_found"})
            return
        body = _read_json_body(self)
        if body is None:
            _json_response(self, 400, {"ok": False, "error": "invalid_json"})
            return
        event_type = str(body.get("event_type", "") or "").strip()
        if not event_type.startswith("T2V_"):
            _json_response(self, 400, {"ok": False, "error": "event_type must start with T2V_"})
            return
        payload = body.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            _json_response(self, 400, {"ok": False, "error": "payload must be object"})
            return
        try:
            _dispatch_t2v_on_main(event_type, payload)
        except Exception as exc:
            _json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        _json_response(self, 202, {"ok": True, "accepted": True, "event_type": event_type})

    def do_DELETE(self) -> None:
        path = (self.path or "").split("?", 1)[0]
        if path == "/hyview/v2t":
            _clear_v2t_buffer()
            _json_response(self, 200, {"ok": True, "cleared": True})
            return
        _json_response(self, 404, {"ok": False, "error": "not_found"})


def start_hyview_debug_http_bridge() -> bool:
    """확장 startup 에서 호출. 이미 실행 중이면 True."""
    global _server, _server_thread
    if not _env_enabled():
        carb.log_info("[HyViewDebugHttp] disabled (TBS_HYVIEW_DEBUG_HTTP=0)")
        return False
    if _server is not None:
        return True

    _install_v2t_observers()
    port = _env_port()
    bind = _DEFAULT_BIND
    try:
        _server = ThreadingHTTPServer((bind, port), _HyViewDebugHandler)
    except Exception as exc:
        carb.log_error(f"[HyViewDebugHttp] bind failed {bind}:{port} — {exc}")
        _shutdown_v2t_observers()
        _server = None
        return False

    def _serve() -> None:
        assert _server is not None
        _server.serve_forever(poll_interval=0.5)

    _server_thread = threading.Thread(target=_serve, name="HyViewDebugHttp", daemon=True)
    _server_thread.start()
    carb.log_info(
        f"[HyViewDebugHttp] listening http://{bind}:{port} "
        f"(POST /hyview/t2v, GET /hyview/v2t, GET /hyview/health)"
    )
    return True


def stop_hyview_debug_http_bridge() -> None:
    global _server, _server_thread
    if _server is not None:
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
        _server = None
    _server_thread = None
    _shutdown_v2t_observers()
    _clear_v2t_buffer()
    carb.log_info("[HyViewDebugHttp] stopped")


__all__ = [
    "start_hyview_debug_http_bridge",
    "stop_hyview_debug_http_bridge",
]
