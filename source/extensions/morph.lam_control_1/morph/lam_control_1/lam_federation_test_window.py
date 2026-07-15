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
        self._offset_field = None
        self._token_field = None
        self._headers_field = None
        self._fixture_field = None
        self._screen_field = None
        self._save_json_field = None
        self._log_field = None
        # UI 표시가 길이 제한으로 잘려도 fetch/병합 원본 전체는 여기 유지한다.
        self._response_data: Optional[Dict[str, Any]] = None
        self._display_snapshot = ""
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
                shown = (prev + chunk)[-120000:]
                model.set_value(shown)
                self._display_snapshot = shown
            except Exception as exc:
                print(f"{_PRINT_PREFIX} log UI update failed: {exc}", flush=True)
                print(f"{_PRINT_PREFIX} {text}", flush=True)

        schedule_on_main_thread(_update_ui)

    def _set_response_data(self, data: Dict[str, Any]) -> None:
        """전체 응답은 메모리에, 편집기에는 표시 가능한 범위만 넣는다."""
        self._response_data = dict(data or {})
        full_text = json.dumps(self._response_data, ensure_ascii=False, indent=2)
        if len(full_text) <= 120000:
            shown = full_text
        else:
            shown = (
                full_text[:119800]
                + "\n\n[UI 표시 생략 — 파싱·시뮬은 메모리의 전체 응답을 사용합니다.]"
            )

        def _update_ui() -> None:
            model = _widget_model(self._log_field)
            if model is not None:
                model.set_value(shown)
                self._display_snapshot = shown

        schedule_on_main_thread(_update_ui)

    @staticmethod
    def _parse_response_text(raw: str) -> Dict[str, Any]:
        """붙여넣은 JSON 또는 로그 안의 마지막 columns/rows JSON 객체를 찾는다."""
        text = str(raw or "").strip()
        if not text:
            raise ValueError("응답/로그에 파싱할 JSON이 없습니다")
        try:
            data = json.loads(text)
            if isinstance(data, dict) and isinstance(data.get("rows"), list):
                return data
        except Exception:
            pass
        decoder = json.JSONDecoder()
        found: Optional[Dict[str, Any]] = None
        for i, ch in enumerate(text):
            if ch != "{":
                continue
            try:
                candidate, _end = decoder.raw_decode(text[i:])
            except Exception:
                continue
            if (
                isinstance(candidate, dict)
                and isinstance(candidate.get("columns"), list)
                and isinstance(candidate.get("rows"), list)
            ):
                found = candidate
        if found is None:
            raise ValueError("응답/로그에서 columns/rows JSON 객체를 찾지 못했습니다")
        return found

    def _read_response_for_parse(self) -> Dict[str, Any]:
        model = _widget_model(self._log_field)
        raw = str(model.get_value_as_string() or "") if model is not None else ""
        # fetch 결과를 사용자가 수정하지 않았다면 UI truncate와 무관하게 전체 메모리 사용.
        if self._response_data is not None and raw == self._display_snapshot:
            return dict(self._response_data)
        return self._parse_response_text(raw)

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
        offset_m = _widget_model(self._offset_field)
        token_m = _widget_model(self._token_field)
        fixture_m = _widget_model(self._fixture_field)
        screen_m = _widget_model(self._screen_field)
        save_m = _widget_model(self._save_json_field)

        url = str(url_m.get_value_as_string() or "").strip() if url_m else d["url"]
        limit = int(float(limit_m.get_value_as_float())) if limit_m else d["limit"]
        offset = int(float(offset_m.get_value_as_float())) if offset_m else 0
        token = str(token_m.get_value_as_string() or "").strip() if token_m else ""
        use_fixture = bool(fixture_m.get_value_as_bool()) if fixture_m else d["use_fixture"]
        screen = int(float(screen_m.get_value_as_float())) if screen_m else 1
        return {
            "url": url,
            "limit": max(1, limit),
            "offset": max(0, offset),
            "token": token,
            "use_fixture": use_fixture,
            "screen": max(1, min(2, screen)),
            "save_json": bool(save_m.get_value_as_bool()) if save_m else False,
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
                    limit=vals["limit"],
                    offset=vals["offset"],
                    bearer_token=vals["token"],
                    extra_headers=vals["headers"],
                    use_fixture=vals["use_fixture"],
                )
                if data:
                    self._set_response_data(data)
                elif raw:
                    self._response_data = None
                    self._append_log(raw)
                print(
                    f"{_PRINT_PREFIX} POST once status={status} "
                    f"limit={vals['limit']} offset={vals['offset']}",
                    flush=True,
                )
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
                    initial_offset=vals["offset"],
                    screen=vals["screen"],
                    bearer_token=vals["token"],
                    extra_headers=vals["headers"],
                    use_fixture=vals["use_fixture"],
                )
                self._set_response_data(merged)
                print(
                    f"{_PRINT_PREFIX} fetch all pages={meta.get('pages')} "
                    f"rows={meta.get('total_rows')} "
                    f"elapsed={meta.get('elapsed_sec'):.2f}s",
                    flush=True,
                )
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
            merged = self._read_response_for_parse()
        except Exception as exc:
            self._append_log(f"ERROR: {exc}")
            self._set_busy(False)
            return
        def _done(result: Dict[str, Any]) -> None:
            # 응답 원문은 편집기에 유지하고 결과 요약은 콘솔 및 끝부분에 기록.
            print(
                f"{_PRINT_PREFIX} parse/sim result: "
                f"{json.dumps(result, ensure_ascii=False)}",
                flush=True,
            )
            self._append_log(
                "\n--- 파싱·시뮬 결과 ---\n"
                + json.dumps(result, ensure_ascii=False, indent=2)
            )
            self._set_busy(False)

        from .lam_federation_pipeline import run_federation_response_simulation

        run_federation_response_simulation(
            self._ext,
            merged,
            body,
            screen=screen,
            on_complete=_done,
            auto_play=True,
            save_response_json=vals["save_json"],
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
                    self._limit_field = ui.FloatField(width=80)
                    self._limit_field.model.set_value(float(d["limit"]))
                    ui.Label("offset", width=50)
                    self._offset_field = ui.FloatField(width=80)
                    self._offset_field.model.set_value(0.0)
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
                    ui.Spacer(width=12)
                    ui.Label("JSON 저장", width=70)
                    self._save_json_field = ui.CheckBox(width=20)
                    self._save_json_field.model.set_value(False)
                ui.Label("응답 / 로그")
                self._log_field = ui.StringField(multiline=True, height=280, read_only=False)
