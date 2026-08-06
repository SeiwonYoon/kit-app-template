"""Viewport 우상단 2D 상태 패널 (기능 #1) — v1.

재생 로직을 수정하지 않고, simulation_play 스냅샷을 읽어 표시만 한다.
화면별 CSV 재생창·progress snap 을 독립 반영한다.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, Optional, TYPE_CHECKING

from .lam_viewport_overlay_config import (
    STATUS_PANEL_BG_COLOR_HEX,
    STATUS_PANEL_BORDER_COLOR_HEX,
    STATUS_PANEL_EQ_MODEL_VALUE,
    STATUS_PANEL_LABEL_COL_WIDTH_PX,
    STATUS_PANEL_LABEL_COLOR_HEX,
    STATUS_PANEL_PADDING_PX,
    STATUS_PANEL_ROW_BG_HEX,
    STATUS_PANEL_ROW_FONT_SIZE,
    STATUS_PANEL_ROWS,
    STATUS_PANEL_STATE_SEP,
    STATUS_PANEL_TEXT_COLOR_HEX,
    STATUS_PANEL_TITLE,
    STATUS_PANEL_TITLE_FONT_SIZE,
    STATUS_PANEL_WIDTH_PX,
)
from .lam_viewport_overlay_state import (
    get_last_state_title,
    set_last_state_title,
    update_active_schedule_keys,
    update_progress_snap,
)

if TYPE_CHECKING:
    from .lam_viewport import LamViewport
    from .simulation_play import LamSimulationCsvPlayWindow

_PRINT_PREFIX = "[LAM/StatusHUD]"
_FRAME_SLOT = "morph.lam_control_1:status_hud"


def _frame_slot_for_screen(screen: int) -> str:
    si = max(1, int(screen))
    if si <= 1:
        return _FRAME_SLOT
    return f"morph.lam_control_1:status_hud_s{si}"

_TOKEN_RE = re.compile(r"\{([^{}]+)\}")
_STATE_LOT_RE = re.compile(r"lot=['\"]([^'\"]+)['\"]")
_STATE_WAFER_RE = re.compile(r"웨이퍼#(\d+)")


def _format_current_state_line(ent: Any) -> str:
    """Current State: 웨이퍼# · lot_id · JSON이벤트명 (title_ko 의 [재생] 문구는 사용하지 않음)."""
    title = str(getattr(ent, "title_ko", "") or "")
    parts: list[str] = []
    wafer_m = _STATE_WAFER_RE.search(title)
    if wafer_m:
        parts.append(f"웨이퍼#{wafer_m.group(1)}")
    lot_m = _STATE_LOT_RE.search(title)
    if lot_m:
        parts.append(lot_m.group(1).strip())
    event = str(getattr(ent, "event_name", "") or "").strip()
    if event.endswith(".json"):
        event = event[:-5]
    if event:
        parts.append(event)
    return str(STATUS_PANEL_STATE_SEP).join(parts)


def _wall_elapsed_for_display(ps: dict, *, screen: Optional[int] = None) -> float:
    """실경과 [s] — 일시정지 시 체크포인트 값 고정, 재생 중엔 라이브 시계."""
    try:
        from .simulation_play import (  # type: ignore
            csv_play_session_active,
            get_csv_play_pause_checkpoint,
            get_csv_play_wall_elapsed,
        )

        ck = get_csv_play_pause_checkpoint(screen=screen)
        if ck is not None and not csv_play_session_active(screen=screen):
            return max(0.0, float(getattr(ck, "wall_elapsed_sec", 0) or 0))
        if csv_play_session_active(screen=screen):
            return max(0.0, get_csv_play_wall_elapsed(screen=screen))
    except Exception:
        pass
    return max(0.0, float(ps.get("wall_elapsed_display", 0.0) or 0.0))


def _format_status_time_line(ps: dict, *, screen: Optional[int] = None) -> str:
    """예: 재생 0.9% / t 15/1774s / 실경과 16s/1774s (3줄)."""
    csv_t = float(ps.get("csv_t_display", 0.0) or 0.0)
    csv_total = float(ps.get("csv_total", 0.0) or 0.0)
    wall = _wall_elapsed_for_display(ps, screen=screen)
    sp = float(max(0.01, ps.get("speed_scale", 1.0) or 1.0))

    if csv_total <= 1e-6 and csv_t <= 1e-6 and wall <= 1e-6:
        return "0s"

    if ps.get("process_only"):
        from .simulation_play import resolve_process_only_wall_total_est  # type: ignore

        json_done = int(ps.get("json_done", 0) or 0)
        json_total = max(1, int(ps.get("json_total", 1) or 1))
        pct = 100.0 * json_done / json_total
        wall_total = resolve_process_only_wall_total_est(ps, wall)
        return (
            f"공정만 {pct:.1f}%\n"
            f"t {csv_t:.0f}/{csv_total:.0f}s\n"
            f"실경과 {wall:.0f}s/{wall_total:.0f}s"
        )

    pct = (100.0 * csv_t / csv_total) if csv_total > 1e-6 else 0.0
    wall_total = csv_total / sp if csv_total > 1e-6 else 0.0
    return (
        f"재생 {pct:.1f}%\n"
        f"t {csv_t:.0f}/{csv_total:.0f}s\n"
        f"실경과 {wall:.0f}s/{wall_total:.0f}s"
    )


def _play_context_idle(ps: dict, *, screen: Optional[int] = None) -> bool:
    """Play 전·정지(초기화) 후 — Time 0s 표시용.

    공정만보기 전환·자식 worker 잔존 중에는 idle 로 보지 않아 Current State 가 비지 않게 한다.
    """
    try:
        from .simulation_play import (  # type: ignore
            csv_play_session_active,
            get_csv_play_pause_checkpoint,
        )

        if csv_play_session_active(screen=screen):
            return False
        if get_csv_play_pause_checkpoint(screen=screen) is not None:
            return False
    except Exception:
        pass
    try:
        from .simulation_play import (  # type: ignore
            _alive_csv_play_child_workers,
            csv_play_screen_session,
        )

        if _alive_csv_play_child_workers(screen=screen):
            return False
        sess = csv_play_screen_session(screen)
        if bool(getattr(sess, "mode_switch_drain", False)):
            return False
    except Exception:
        pass
    csv_t = float(ps.get("csv_t_display", 0.0) or 0.0)
    csv_total = float(ps.get("csv_total", 0.0) or 0.0)
    wall = float(ps.get("wall_elapsed_display", 0.0) or 0.0)
    json_done = int(ps.get("json_done", 0) or 0)
    json_total = int(ps.get("json_total", 0) or 0)
    if csv_total > 1e-6 or csv_t > 1e-6 or wall > 1e-6:
        return False
    if json_total > 0 or json_done > 0:
        return False
    return True


def _current_dwell_for_csv_t(csv_window: Any, csv_t: float) -> Any:
    """선택 CSV 캐시에서 현재 시각(또는 최초) dwell."""
    try:
        from .simulation_play import get_cached_csv_playback  # type: ignore

        sel_fn = getattr(csv_window, "_selected_csv_path", None)
        path = sel_fn() if callable(sel_fn) else None
        cached = getattr(csv_window, "_prepared_playback", None)
        if cached is None and path is not None:
            cached = get_cached_csv_playback(path)
        dwells = list(getattr(cached, "dwells", []) or []) if cached is not None else []
        if not dwells:
            return None
        t = float(csv_t or 0.0)
        if t <= 1e-6:
            return min(dwells, key=lambda d: float(getattr(d, "start_sec", 0.0) or 0.0))
        for d in dwells:
            s = float(getattr(d, "start_sec", 0.0) or 0.0)
            e = float(getattr(d, "end_sec", 0.0) or 0.0)
            if s <= t < e:
                return d
        return min(
            dwells,
            key=lambda d: abs(float(getattr(d, "start_sec", 0.0) or 0.0) - t),
        )
    except Exception:
        return None


def build_status_panel_snapshot(
    csv_window: Any,
    *,
    screen: Optional[int] = None,
) -> Dict[str, Any]:
    """STATUS 패널과 동일 규칙의 해석된 스냅샷 ``{title, rows:[{key,name,value}]}``.

    Viewport 패널 표시 플래그와 무관 — 웹 V2T 통지·HUD 공통 소스.
    """
    si = max(
        1,
        int(
            screen
            if screen is not None
            else getattr(csv_window, "_screen", 1) or 1
        ),
    )
    try:
        from .simulation_play import (
            get_csv_play_progress_snap,
            get_csv_play_timeline_active_keys_snap,
        )

        ps = get_csv_play_progress_snap(screen=si)
        update_progress_snap(ps, screen=si)
        update_active_schedule_keys(
            get_csv_play_timeline_active_keys_snap(screen=si),
            screen=si,
        )
    except Exception:
        ps = {}

    idle = _play_context_idle(ps, screen=si)
    csv_t = float(ps.get("csv_t_display", 0.0) or 0.0)
    if idle:
        # Play 전·정지 후: Time 은 0. Current State 는 명시적 정지/초기화에서만 비움.
        # (공정만보기 전환 중 잠깐 idle 로 보이면 문구를 지우지 않음)
        time_s = "0s"
        display_state = get_last_state_title(screen=si)
        eqp_id_val = ""
        cur_dwell = None
    else:
        time_s = _format_status_time_line(ps, screen=si)
        # 화면별 실시간 실행 스택(최근 begin JSON) — ATM/VTM place·pick 등 웨이퍼 즉시 반영
        display_state = ""
        try:
            from .simulation_play import get_csv_play_live_status_state  # type: ignore

            display_state = str(get_csv_play_live_status_state(screen=si) or "").strip()
        except Exception:
            display_state = ""
        if not display_state:
            # fallback: active 키 중 time_sec 최신 행 (예전 첫 매칭 잔상 완화)
            try:
                from .simulation_play import (  # type: ignore
                    _schedule_entry_matches_active,
                    get_csv_play_timeline_active_keys_snap,
                )

                active = get_csv_play_timeline_active_keys_snap(screen=si)
                matched: list = []
                for e in getattr(csv_window, "_schedule_row_entries", []) or []:
                    if not _schedule_entry_matches_active(e, active):
                        continue
                    candidate = _format_current_state_line(e)
                    if candidate:
                        matched.append((float(getattr(e, "time_sec", 0.0) or 0.0), candidate))
                if matched:
                    matched.sort(key=lambda x: x[0])
                    display_state = matched[-1][1]
                    set_last_state_title(display_state, screen=si)
            except Exception:
                pass
            if not display_state:
                display_state = get_last_state_title(screen=si)

        cur_dwell = _current_dwell_for_csv_t(csv_window, csv_t)
        eqp_id_val = ""
        if cur_dwell is not None:
            try:
                eqp_id_val = str(getattr(cur_dwell, "eqp_id", "") or "").strip()
            except Exception:
                eqp_id_val = ""

    def _token_value(tok_raw: str) -> str:
        tok = (tok_raw or "").strip()
        key = tok.lower()
        if key in ("time",):
            return str(time_s)
        if key in ("state",):
            return str(display_state or "").strip()
        if key in ("eq_model", "eqmodel", "eq-model"):
            return str(STATUS_PANEL_EQ_MODEL_VALUE or "").strip()
        if key in ("eqp_id", "eqpid", "eqp-id"):
            return str(eqp_id_val or "").strip()
        # wafer 번호 / lot — 실행 중 JSON 웨이퍼 우선 (dwell 시각 겹침으로 이전 슬롯이 남는 문제 방지)
        live_cas = 0
        live_lot = ""
        try:
            from .simulation_play import get_csv_play_live_status_wafer  # type: ignore

            live_cas, live_lot = get_csv_play_live_status_wafer(screen=si)
        except Exception:
            live_cas, live_lot = 0, ""
        if key in ("cassette_slot", "slot", "wafer", "wafer_번호", "wafer번호"):
            if int(live_cas or 0) > 0:
                return str(int(live_cas))
        if key in ("lot_id", "lot"):
            if str(live_lot or "").strip():
                return str(live_lot).strip()
        if cur_dwell is not None:
            if hasattr(cur_dwell, key):
                try:
                    v = getattr(cur_dwell, key)
                    return str(v if v is not None else "").strip()
                except Exception:
                    return ""
            if key == "slot" and hasattr(cur_dwell, "cassette_slot"):
                try:
                    return str(getattr(cur_dwell, "cassette_slot") or "").strip()
                except Exception:
                    return ""
        return ""

    def _resolve_value(raw: str) -> str:
        s = str(raw or "")
        if "{" not in s:
            return s

        def _sub(m: re.Match) -> str:
            return _token_value(m.group(1))

        return _TOKEN_RE.sub(_sub, s)

    rows: list[dict[str, str]] = []
    for spec in list(STATUS_PANEL_ROWS or []):
        k = str(getattr(spec, "key", "") or "")
        if not k:
            continue
        rows.append(
            {
                "key": k,
                "name": str(getattr(spec, "name", "") or ""),
                "value": _resolve_value(str(getattr(spec, "value", "") or "")),
            }
        )
    return {
        "title": str(STATUS_PANEL_TITLE or ""),
        "rows": rows,
    }


def _status_snapshot_fingerprint(data: Dict[str, Any]) -> str:
    """내용 변경 감지용 — rows 의 key/value 만."""
    parts: list[str] = [str(data.get("title") or "")]
    for row in list(data.get("rows") or []):
        if not isinstance(row, dict):
            continue
        parts.append(f"{row.get('key', '')}={row.get('value', '')}")
    return "\n".join(parts)


def dispatch_v2t_notify_status_panel(data: Dict[str, Any]) -> bool:
    """Kit → 웹 ``V2T_notify_status_panel`` (T2V 요청 없음)."""
    try:
        from sk.hyview_messaging.hyview_event_contract import (  # type: ignore
            V2T_NOTIFY_STATUS_PANEL,
        )
    except Exception:
        V2T_NOTIFY_STATUS_PANEL = "V2T_notify_status_panel"
    try:
        from carb.eventdispatcher import get_eventdispatcher  # type: ignore

        get_eventdispatcher().dispatch_event(
            V2T_NOTIFY_STATUS_PANEL,
            payload={
                "code": 0,
                "message": "success",
                "data": dict(data or {}),
            },
        )
        return True
    except Exception as exc:
        print(f"{_PRINT_PREFIX} V2T_notify_status_panel dispatch: {exc}", flush=True)
        return False


class LamStatusPanelWebNotifier:
    """STATUS 패널 내용 변경 시 웹으로 V2T 통지.

    ``SHOW_VIEWPORT_STATUS_PANEL`` 과 무관하게 동작한다.
    """

    def __init__(self, csv_window: "LamSimulationCsvPlayWindow") -> None:
        self._csv = csv_window
        self._post_update_sub: Any = None
        self._last_tick = 0.0
        self._last_fp = ""

    def start(self) -> None:
        if self._post_update_sub is not None:
            return
        try:
            import omni.kit.app as kapp  # type: ignore

            stream = kapp.get_app().get_post_update_event_stream()
        except Exception:
            return

        def _on(_e) -> None:
            now = time.time()
            if now - self._last_tick < 0.2:
                return
            self._last_tick = now
            self._tick()

        self._post_update_sub = stream.create_subscription_to_pop(
            _on,
            name="morph.lam_control_1:status_panel_web_notify",
        )
        # 기동 직후 1회 스냅샷 전송 시도
        self._tick(force=True)

    def set_csv_window(self, csv_window: "LamSimulationCsvPlayWindow") -> None:
        self._csv = csv_window

    def stop(self) -> None:
        if self._post_update_sub is not None:
            try:
                self._post_update_sub.unsubscribe()
            except Exception:
                pass
            self._post_update_sub = None
        self._last_fp = ""

    def destroy(self) -> None:
        self.stop()

    def _tick(self, *, force: bool = False) -> None:
        if self._csv is None:
            return
        try:
            data = build_status_panel_snapshot(
                self._csv,
                screen=max(1, int(getattr(self._csv, "_screen", 1) or 1)),
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} status snapshot: {exc}", flush=True)
            return
        fp = _status_snapshot_fingerprint(data)
        if not force and fp == self._last_fp:
            return
        if dispatch_v2t_notify_status_panel(data):
            self._last_fp = fp


def _resolve_viewport_window(viewport: Optional["LamViewport"]) -> Optional[Any]:
    if viewport is not None:
        try:
            dedicated = getattr(viewport, "_dedicated_window", None)
            if dedicated is not None and callable(getattr(dedicated, "get_frame", None)):
                return dedicated
        except Exception:
            pass
    try:
        from omni.kit.viewport.utility import get_active_viewport_window  # type: ignore

        win = get_active_viewport_window()
        if win is not None and callable(getattr(win, "get_frame", None)):
            return win
    except Exception:
        pass
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name  # type: ignore

        api = get_viewport_from_window_name("Viewport")
        for attr in ("viewport_window", "window", "_viewport_window", "_window"):
            cand = getattr(api, attr, None) if api is not None else None
            if cand is not None and callable(getattr(cand, "get_frame", None)):
                return cand
        if api is not None and callable(getattr(api, "get_frame", None)):
            return api
    except Exception:
        pass
    return None


class LamViewportStatusPanel:
    """Viewport 우상단 STATUS 패널 — ``lam_sim_control_defaults.SHOW_VIEWPORT_STATUS_PANEL``."""

    def __init__(
        self,
        csv_window: "LamSimulationCsvPlayWindow",
        *,
        viewport: Optional["LamViewport"] = None,
        screen: Optional[int] = None,
    ) -> None:
        self._csv = csv_window
        self._screen = max(
            1,
            int(
                screen
                if screen is not None
                else getattr(csv_window, "_screen", 1) or 1
            ),
        )
        self._viewport = viewport
        self._viewport_window: Any = None
        self._root: Any = None
        self._models: dict[str, Any] = {}
        self._labels: dict[str, Any] = {}  # key -> ui.Label(value)
        self._post_update_sub: Any = None
        self._mounted = False
        self._last_tick = 0.0
        self._sync_token: float = 0.0
        self._frame_slot = _frame_slot_for_screen(self._screen)

    def _resolve_viewport_for_panel(self) -> Optional[Any]:
        if self._screen > 1:
            lam = getattr(self._csv, "_lam_window_ref", None)
            ext = getattr(lam, "_kit_ext", None) if lam is not None else None
            if ext is not None:
                try:
                    from .lam_csv_play_screen import resolve_viewport_window_for_screen

                    vw = resolve_viewport_window_for_screen(
                        ext,
                        self._screen,
                        main_viewport=self._viewport,
                    )
                    if vw is not None and callable(getattr(vw, "get_frame", None)):
                        self._viewport_window = vw
                        return vw
                except Exception:
                    pass
            cached = getattr(self, "_viewport_window", None)
            if cached is not None and callable(getattr(cached, "get_frame", None)):
                return cached
            return None
        cached = getattr(self, "_viewport_window", None)
        if cached is not None and callable(getattr(cached, "get_frame", None)):
            return cached
        return _resolve_viewport_window(self._viewport)

    def _status_panel_wanted(self) -> bool:
        """창 표시「STATUS 패널」런타임 플래그 (없으면 defaults)."""
        try:
            lam = getattr(self._csv, "_lam_window_ref", None)
            if lam is not None and hasattr(lam, "_show_viewport_status_panel_rt"):
                return bool(lam._show_viewport_status_panel_rt)
        except Exception:
            pass
        try:
            from .lam_sim_control_defaults import SHOW_VIEWPORT_STATUS_PANEL

            return bool(SHOW_VIEWPORT_STATUS_PANEL)
        except Exception:
            return False

    def destroy(self) -> None:
        self._sync_token = float(time.time())
        self._stop_poll()
        self._destroy_layer()

    def sync_layers(self, *, delay_frames: int = 8) -> None:
        if not self._status_panel_wanted():
            self.destroy()
            return
        try:
            import omni.kit.app as kapp  # type: ignore
        except Exception:
            return
        token = float(time.time())
        self._sync_token = token

        def _try(remaining: int) -> None:
            if getattr(self, "_sync_token", 0.0) != token:
                return
            if not self._status_panel_wanted():
                self.destroy()
                return
            vw = self._resolve_viewport_for_panel()
            if vw is not None:
                self._mount_on_viewport(vw)
                return
            if remaining > 0:
                try:
                    app = kapp.get_app()
                    if app is not None:
                        app.post_update(lambda: _try(remaining - 1))
                        return
                except Exception:
                    pass

        _try(max(0, int(delay_frames)))

    @staticmethod
    def _clear_viewport_slot(vw: Any, slot: str) -> None:
        if vw is None or not callable(getattr(vw, "get_frame", None)):
            return
        try:
            import omni.ui as ui  # type: ignore

            with vw.get_frame(slot):
                ui.Spacer(height=0)
        except Exception:
            pass

    def _destroy_layer(self) -> None:
        root = self._root
        self._root = None
        self._models.clear()
        self._labels.clear()
        self._mounted = False
        try:
            if root is not None:
                root.visible = False
        except Exception:
            pass
        try:
            vw = self._resolve_viewport_for_panel()
            self._clear_viewport_slot(vw, self._frame_slot)
        except Exception:
            pass

    def _mount_on_viewport(self, vw: Any) -> None:
        if not self._status_panel_wanted():
            self._destroy_layer()
            return
        try:
            import omni.ui as ui  # type: ignore
            from omni.ui import SimpleStringModel  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.ui unavailable: {exc}", flush=True)
            return

        self._destroy_layer()
        self._mounted = True

        # Models (legacy; 일부 환경에서 Label(model=)이 불안정해 직접 text를 갱신)
        self._models["eq_model"] = SimpleStringModel(str(STATUS_PANEL_EQ_MODEL_VALUE or ""))
        self._models["eqp_id"] = SimpleStringModel("")
        self._models["time"] = SimpleStringModel("0s")
        self._models["state"] = SimpleStringModel("")

        try:
            ra = getattr(ui, "Alignment", None)
            rt = getattr(ra, "RIGHT_TOP", None) if ra is not None else None
            with vw.get_frame(self._frame_slot):
                root = ui.ZStack(alignment=rt) if rt is not None else ui.ZStack()
                self._root = root
                with root:
                    with ui.VStack():
                        ui.Spacer(height=10)
                        with ui.HStack():
                            ui.Spacer()
                            panel_w = int(STATUS_PANEL_WIDTH_PX)
                            pad = int(STATUS_PANEL_PADDING_PX)
                            label_w = int(STATUS_PANEL_LABEL_COL_WIDTH_PX)
                            # 값 컬럼은 고정 너비로 강제 (패널이 내용에 따라 커지지 않도록)
                            # spacing(10) + 내부 좌/우 여백을 고려해 약간 보수적으로 잡음
                            value_w = max(20, panel_w - (pad * 2) - label_w - 18)

                            with ui.Frame(
                                width=panel_w,
                                style={
                                    "background_color": int(STATUS_PANEL_BG_COLOR_HEX),
                                    "border_width": 1,
                                    "border_color": int(STATUS_PANEL_BORDER_COLOR_HEX),
                                    "border_radius": 4,
                                    "padding": pad,
                                },
                            ):
                                with ui.VStack(spacing=6, width=panel_w):
                                    # ui.Label(
                                    #     str(STATUS_PANEL_TITLE or "STATUS"),
                                    #     height=18,
                                    #     style={
                                    #         "font_size": int(STATUS_PANEL_TITLE_FONT_SIZE),
                                    #         "color": int(STATUS_PANEL_TEXT_COLOR_HEX),
                                    #     },
                                    # )

                                    # 표(테이블): 전체 테두리 1개 + 내부 구분선만
                                    with ui.Frame(
                                        width=panel_w - pad * 2,
                                        style={
                                            "border_width": 1,
                                            "border_color": int(STATUS_PANEL_BORDER_COLOR_HEX),
                                            "border_radius": 2,
                                        },
                                    ):
                                        with ui.VStack(spacing=0, width=panel_w - pad * 2):

                                            def _row(spec, *, draw_sep: bool) -> None:
                                                h = int(getattr(spec, "height_px", 26) or 26)
                                                label_fs = int(
                                                    getattr(
                                                        spec,
                                                        "label_font_size",
                                                        STATUS_PANEL_ROW_FONT_SIZE,
                                                    )
                                                    or STATUS_PANEL_ROW_FONT_SIZE
                                                )
                                                value_fs = int(
                                                    getattr(
                                                        spec,
                                                        "value_font_size",
                                                        STATUS_PANEL_ROW_FONT_SIZE,
                                                    )
                                                    or STATUS_PANEL_ROW_FONT_SIZE
                                                )
                                                with ui.ZStack(height=h, width=panel_w - pad * 2):
                                                    ui.Rectangle(
                                                        style={"background_color": int(STATUS_PANEL_ROW_BG_HEX)}
                                                    )
                                                    with ui.HStack(height=h, spacing=10):
                                                        ui.Label(
                                                            str(getattr(spec, "name", "")),
                                                            width=label_w,
                                                            style={
                                                                "font_size": label_fs,
                                                                "color": int(STATUS_PANEL_LABEL_COLOR_HEX),
                                                            },
                                                        )
                                                        v = ui.Label(
                                                            "",
                                                            width=value_w,
                                                            height=h,
                                                            word_wrap=True,
                                                            style={
                                                                "font_size": value_fs,
                                                                "color": int(STATUS_PANEL_TEXT_COLOR_HEX),
                                                            },
                                                        )
                                                        self._labels[str(getattr(spec, "key", ""))] = v
                                                if draw_sep:
                                                    ui.Rectangle(
                                                        height=1,
                                                        style={
                                                            "background_color": int(
                                                                STATUS_PANEL_BORDER_COLOR_HEX
                                                            )
                                                        },
                                                    )

                                            rows = list(STATUS_PANEL_ROWS or [])
                                            for i, spec in enumerate(rows):
                                                _row(spec, draw_sep=(i != len(rows) - 1))
                            ui.Spacer(width=10)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} screen{self._screen} mount failed: {exc}",
                flush=True,
            )
            self._destroy_layer()
            return

        self._start_poll()

    def _start_poll(self) -> None:
        if self._post_update_sub is not None:
            return
        try:
            import omni.kit.app as kapp  # type: ignore

            stream = kapp.get_app().get_post_update_event_stream()
        except Exception:
            return

        def _on(_e) -> None:
            # 5Hz 정도로만 갱신
            now = time.time()
            if now - self._last_tick < 0.2:
                return
            self._last_tick = now
            self._tick_update()

        self._post_update_sub = stream.create_subscription_to_pop(
            _on,
            name=f"morph.lam_control_1:status_hud_poll_s{self._screen}",
        )

    def _stop_poll(self) -> None:
        if self._post_update_sub is not None:
            try:
                self._post_update_sub.unsubscribe()
            except Exception:
                pass
            self._post_update_sub = None

    def _tick_update(self) -> None:
        if not self._mounted:
            return
        try:
            data = build_status_panel_snapshot(self._csv, screen=self._screen)
        except Exception:
            return
        for row in list(data.get("rows") or []):
            if not isinstance(row, dict):
                continue
            k = str(row.get("key") or "")
            if not k:
                continue
            try:
                lbl = self._labels.get(k)
                if lbl is None:
                    continue
                lbl.text = str(row.get("value") or "")
            except Exception:
                pass
        # legacy models (있으면 동기화)
        by_key = {
            str(r.get("key")): str(r.get("value") or "")
            for r in list(data.get("rows") or [])
            if isinstance(r, dict)
        }
        for mk in ("time", "state", "eq_model", "eqp_id"):
            if mk not in self._models:
                continue
            try:
                self._models[mk].set_value(by_key.get(mk, ""))
            except Exception:
                pass


__all__ = [
    "LamStatusPanelWebNotifier",
    "LamViewportStatusPanel",
    "build_status_panel_snapshot",
    "dispatch_v2t_notify_status_panel",
]
