"""Federation API 테스트 창 — Live POST / pagination / 파싱·시뮬."""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, Optional

from .kit_main_dispatch import schedule_on_main_thread

_PRINT_PREFIX = "[LAM/fed-test]"
WINDOW_TITLE = "LAM Federation API Test"

_DEFAULT_BODY = {
    "fab_id": "FAB01",
    "mt": "SC2HM",
    "eqp_id": "EQP_SAMPLE",
    "lot_id": "TAGUB84",
    "mt_from": "2026-06-01 00:00:00",
    "mt_to": "2026-06-02 00:00:00",
}


def _widget_model(widget: Any) -> Any:
    """``ui.StringField`` / ``FloatField`` / ``CheckBox`` → 내부 AbstractValueModel."""
    if widget is None:
        return None
    return getattr(widget, "model", None)


class LamFederationTestWindow:
    def __init__(self, kit_ext: Any) -> None:
        self._ext = kit_ext
        self._window = None
        # 이름에 model 이 있어도 실제로는 Field/CheckBox 위젯을 저장한다.
        # 값 접근은 반드시 ``_widget_model(w)`` / ``w.model`` 을 사용한다.
        self._url_field = None
        self._body_field = None
        self._limit_field = None
        self._token_field = None
        self._headers_field = None
        self._fixture_field = None
        self._screen_field = None
        self._log_field = None
        self._busy = False

    def show(self) -> None:
        if self._window is not None:
            try:
                self._window.visible = True
                self._window.focus()
            except Exception:
                pass
            return
        self._build()

    def destroy(self) -> None:
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
        self._window = None

    def _defaults(self) -> Dict[str, Any]:
        try:
            from . import lam_sim_control_defaults as d

            return {
                "url": str(getattr(d, "FEDERATION_QUERY_URL", "") or ""),
                "limit": int(getattr(d, "FEDERATION_FETCH_LIMIT", 1000) or 1000),
                "use_fixture": bool(getattr(d, "FEDERATION_USE_FIXTURE", False)),
                "token": str(getattr(d, "FEDERATION_BEARER_TOKEN", "") or ""),
                "headers": dict(getattr(d, "FEDERATION_EXTRA_HEADERS", {}) or {}),
            }
        except Exception:
            return {
                "url": "",
                "limit": 1000,
                "use_fixture": False,
                "token": "",
                "headers": {},
            }

    def _append_log(self, text: str) -> None:
        """로그 갱신 — 백그라운드에서 호출되어도 메인 스레드로 마샬링."""

        def _update_ui() -> None:
            model = _widget_model(self._log_field)
            if model is None:
                print(f"{_PRINT_PREFIX} {text}", flush=True)
                return
            try:
                prev = str(model.get_value_as_string() or "")
                chunk = text if text.endswith("\n") else text + "\n"
                model.set_value((prev + chunk)[-120000:])
            except Exception as exc:
                print(f"{_PRINT_PREFIX} log UI update failed: {exc}", flush=True)
                print(f"{_PRINT_PREFIX} {text}", flush=True)

        schedule_on_main_thread(_update_ui)

    def _read_body(self) -> Dict[str, Any]:
        model = _widget_model(self._body_field)
        raw = ""
        if model is not None:
            raw = str(model.get_value_as_string() or "").strip()
        if not raw:
            return dict(_DEFAULT_BODY)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    def _read_headers(self) -> Dict[str, str]:
        model = _widget_model(self._headers_field)
        raw = ""
        if model is not None:
            raw = str(model.get_value_as_string() or "").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("extra headers must be a JSON object")
        return {str(k): str(v) for k, v in data.items()}

    def _ui_values(self) -> Dict[str, Any]:
        """메인(UI) 스레드에서만 호출 — 위젯 model 값을 스냅샷."""
        d = self._defaults()
        url_m = _widget_model(self._url_field)
        limit_m = _widget_model(self._limit_field)
        token_m = _widget_model(self._token_field)
        fixture_m = _widget_model(self._fixture_field)
        screen_m = _widget_model(self._screen_field)

        url = str(url_m.get_value_as_string() or "").strip() if url_m else d["url"]
        limit = int(float(limit_m.get_value_as_float())) if limit_m else d["limit"]
        token = str(token_m.get_value_as_string() or "").strip() if token_m else ""
        use_fixture = bool(fixture_m.get_value_as_bool()) if fixture_m else d["use_fixture"]
        screen = int(float(screen_m.get_value_as_float())) if screen_m else 1
        return {
            "url": url,
            "limit": max(1, limit),
            "token": token,
            "use_fixture": use_fixture,
            "screen": max(1, min(2, screen)),
            "headers": self._read_headers(),
        }

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)

    def _on_fetch_once(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        vals = self._ui_values()
        try:
            body = self._read_body()
        except Exception as exc:
            self._append_log(f"ERROR: {exc}")
            self._set_busy(False)
            return

        def _work() -> None:
            try:
                from .lam_federation_client import fetch_single_post

                status, data, raw = fetch_single_post(
                    url=vals["url"],
                    body=body,
                    bearer_token=vals["token"],
                    extra_headers=vals["headers"],
                    use_fixture=vals["use_fixture"],
                )
                self._append_log(f"POST once status={status}")
                if data:
                    self._append_log(json.dumps(data, ensure_ascii=False, indent=2)[:80000])
                elif raw:
                    self._append_log(raw[:80000])
            except Exception as exc:
                self._append_log(f"ERROR: {exc}")
            finally:
                self._set_busy(False)

        threading.Thread(target=_work, daemon=True).start()

    def _on_fetch_all(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        vals = self._ui_values()
        try:
            body = self._read_body()
        except Exception as exc:
            self._append_log(f"ERROR: {exc}")
            self._set_busy(False)
            return

        def _work() -> None:
            try:
                from .lam_federation_client import fetch_federation_pages

                merged, meta = fetch_federation_pages(
                    url=vals["url"],
                    body=body,
                    limit=vals["limit"],
                    screen=vals["screen"],
                    bearer_token=vals["token"],
                    extra_headers=vals["headers"],
                    use_fixture=vals["use_fixture"],
                )
                self._append_log(
                    f"fetch all pages={meta.get('pages')} rows={meta.get('total_rows')} "
                    f"elapsed={meta.get('elapsed_sec'):.2f}s"
                )
                self._append_log(json.dumps(merged, ensure_ascii=False, indent=2)[:80000])
            except Exception as exc:
                self._append_log(f"ERROR: {exc}")
            finally:
                self._set_busy(False)

        threading.Thread(target=_work, daemon=True).start()

    def _on_parse_sim(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        vals = self._ui_values()
        screen = vals["screen"]
        try:
            body = self._read_body()
        except Exception as exc:
            self._append_log(f"ERROR: {exc}")
            self._set_busy(False)
            return
        payload = {"configs": [{}, {}]}
        payload["configs"][screen - 1] = body

        def _done(result: Dict[str, Any]) -> None:
            self._append_log(json.dumps(result, ensure_ascii=False, indent=2)[:40000])
            self._set_busy(False)

        from .lam_federation_pipeline import run_federation_start_simulation

        run_federation_start_simulation(
            self._ext,
            payload,
            on_complete=_done,
            auto_play=True,
            limit_override=vals["limit"],
            url_override=vals["url"],
            use_fixture_override=vals["use_fixture"],
            bearer_token_override=vals["token"],
            extra_headers_override=vals["headers"],
        )

    def _build(self) -> None:
        import omni.ui as ui

        d = self._defaults()
        self._window = ui.Window(
            WINDOW_TITLE,
            width=720,
            height=720,
            flags=ui.WINDOW_FLAGS_NO_SCROLLBAR,
        )
        with self._window.frame:
            with ui.VStack(spacing=6, height=0):
                ui.Label(
                    "인증 필드를 비우면 token/header 없이 POST 합니다. "
                    "Bearer 또는 FEDERATION_EXTRA_HEADERS(JSON) 로 실무 인증을 적용하세요.",
                    word_wrap=True,
                )
                with ui.HStack(height=0):
                    ui.Label("URL", width=80)
                    self._url_field = ui.StringField()
                    self._url_field.model.set_value(d["url"])
                with ui.HStack(height=0):
                    ui.Label("limit", width=80)
                    self._limit_field = ui.FloatField()
                    self._limit_field.model.set_value(float(d["limit"]))
                    ui.Label("screen", width=50)
                    self._screen_field = ui.FloatField(width=60)
                    self._screen_field.model.set_value(1.0)
                    ui.Label("fixture", width=50)
                    self._fixture_field = ui.CheckBox()
                    self._fixture_field.model.set_value(d["use_fixture"])
                with ui.HStack(height=0):
                    ui.Label("Bearer", width=80)
                    self._token_field = ui.StringField(password=True)
                    self._token_field.model.set_value(d["token"])
                with ui.HStack(height=0):
                    ui.Label("headers", width=80)
                    self._headers_field = ui.StringField()
                    try:
                        self._headers_field.model.set_value(
                            json.dumps(d["headers"], ensure_ascii=False)
                        )
                    except Exception:
                        self._headers_field.model.set_value("{}")
                ui.Label("Request body (JSON)")
                self._body_field = ui.StringField(multiline=True, height=120)
                self._body_field.model.set_value(
                    json.dumps(_DEFAULT_BODY, ensure_ascii=False, indent=2)
                )
                with ui.HStack(height=0):
                    ui.Button("POST 1회", clicked_fn=self._on_fetch_once, width=100)
                    ui.Button("전체 fetch", clicked_fn=self._on_fetch_all, width=100)
                    ui.Button("파싱·시뮬", clicked_fn=self._on_parse_sim, width=100)
                ui.Label("응답 / 로그")
                self._log_field = ui.StringField(multiline=True, height=280, read_only=True)
