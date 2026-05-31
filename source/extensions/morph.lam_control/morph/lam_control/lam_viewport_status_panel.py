"""Viewport 좌상단 2D 상태 패널 (기능 #1) — v1.

재생 로직을 수정하지 않고, simulation_play 스냅샷을 읽어 표시만 한다.
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional, TYPE_CHECKING

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
_FRAME_SLOT = "morph.lam_control:status_hud"

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


def _wall_elapsed_for_display(ps: dict) -> float:
    """실경과 [s] — 일시정지 시 체크포인트 값 고정, 재생 중엔 라이브 시계."""
    try:
        from .simulation_play import (  # type: ignore
            csv_play_session_active,
            get_csv_play_pause_checkpoint,
            get_csv_play_wall_elapsed,
        )

        ck = get_csv_play_pause_checkpoint()
        if ck is not None and not csv_play_session_active():
            return max(0.0, float(getattr(ck, "wall_elapsed_sec", 0) or 0))
        if csv_play_session_active():
            return max(0.0, get_csv_play_wall_elapsed())
    except Exception:
        pass
    return max(0.0, float(ps.get("wall_elapsed_display", 0.0) or 0.0))


def _format_status_time_line(ps: dict) -> str:
    """예: 재생 0.9% | t 15.1/1773.7s | 실경과 16s/1774s"""
    csv_t = float(ps.get("csv_t_display", 0.0) or 0.0)
    csv_total = float(ps.get("csv_total", 0.0) or 0.0)
    wall = _wall_elapsed_for_display(ps)
    sp = float(max(0.01, ps.get("speed_scale", 1.0) or 1.0))

    if csv_total <= 1e-6 and csv_t <= 1e-6 and wall <= 1e-6:
        return "0s"

    if ps.get("process_only"):
        json_done = int(ps.get("json_done", 0) or 0)
        json_total = max(1, int(ps.get("json_total", 1) or 1))
        pct = 100.0 * json_done / json_total
        wall_total = float(json_total)
        return (
            f"공정만 {pct:.1f}% | t {csv_t:.1f}/{csv_total:.1f}s | "
            f"실경과 {wall:.0f}s/{wall_total:.0f}s"
        )

    pct = (100.0 * csv_t / csv_total) if csv_total > 1e-6 else 0.0
    wall_total = csv_total / sp if csv_total > 1e-6 else 0.0
    return (
        f"재생 {pct:.1f}% | t {csv_t:.1f}/{csv_total:.1f}s | "
        f"실경과 {wall:.0f}s/{wall_total:.0f}s"
    )


def _play_context_idle(ps: dict) -> bool:
    """Play 전·정지(초기화) 후 — Time 0s, Current State 초기화."""
    csv_t = float(ps.get("csv_t_display", 0.0) or 0.0)
    csv_total = float(ps.get("csv_total", 0.0) or 0.0)
    wall = float(ps.get("wall_elapsed_display", 0.0) or 0.0)
    if csv_total > 1e-6 or csv_t > 1e-6 or wall > 1e-6:
        return False
    try:
        from .simulation_play import get_csv_play_pause_checkpoint  # type: ignore

        if get_csv_play_pause_checkpoint() is not None:
            return False
    except Exception:
        pass
    return True


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
    """Viewport 좌상단 상태 패널. 앱 시작 시 기본 표시."""

    def __init__(
        self,
        csv_window: "LamSimulationCsvPlayWindow",
        *,
        viewport: Optional["LamViewport"] = None,
    ) -> None:
        self._csv = csv_window
        self._viewport = viewport
        self._root: Any = None
        self._models: dict[str, Any] = {}
        self._labels: dict[str, Any] = {}  # key -> ui.Label(value)
        self._post_update_sub: Any = None
        self._mounted = False
        self._last_tick = 0.0

    def destroy(self) -> None:
        self._stop_poll()
        self._destroy_layer()

    def sync_layers(self, *, delay_frames: int = 8) -> None:
        try:
            import omni.kit.app as kapp  # type: ignore
        except Exception:
            return
        token_box = {"token": time.time()}
        token = token_box["token"]

        def _try(remaining: int) -> None:
            if token_box["token"] != token:
                return
            vw = _resolve_viewport_window(self._viewport)
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

    def _destroy_layer(self) -> None:
        self._root = None
        self._models.clear()
        self._labels.clear()
        try:
            vw = _resolve_viewport_window(self._viewport)
            if vw is None:
                return
            with vw.get_frame(_FRAME_SLOT):
                pass
        except Exception:
            pass

    def _mount_on_viewport(self, vw: Any) -> None:
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
            lt = getattr(ra, "LEFT_TOP", None) if ra is not None else None
            with vw.get_frame(_FRAME_SLOT):
                root = ui.ZStack(alignment=lt) if lt is not None else ui.ZStack()
                self._root = root
                with root:
                    with ui.VStack():
                        ui.Spacer(height=10)
                        with ui.HStack():
                            ui.Spacer(width=10)
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
                                    ui.Label(
                                        str(STATUS_PANEL_TITLE or "STATUS"),
                                        height=18,
                                        style={
                                            "font_size": int(STATUS_PANEL_TITLE_FONT_SIZE),
                                            "color": int(STATUS_PANEL_TEXT_COLOR_HEX),
                                        },
                                    )

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
        except Exception as exc:
            print(f"{_PRINT_PREFIX} mount failed: {exc}", flush=True)
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

        self._post_update_sub = stream.create_subscription_to_pop(_on, name="morph.lam_control:status_hud_poll")

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

        # progress snap
        try:
            from .simulation_play import get_csv_play_progress_snap, get_csv_play_timeline_active_keys_snap

            ps = get_csv_play_progress_snap()
            update_progress_snap(ps)
            keys = get_csv_play_timeline_active_keys_snap()
            update_active_schedule_keys(keys)
        except Exception:
            ps = {}

        csv_t = float(ps.get("csv_t_display", 0.0) or 0.0)

        time_s = _format_status_time_line(ps)
        try:
            self._models["time"].set_value(time_s)
        except Exception:
            pass

        if _play_context_idle(ps):
            set_last_state_title("")

        # Current State: 웨이퍼# · lot · JSON명 — dwell/일시정지 시 마지막 값 유지
        active_state = ""
        eqp_id_val = ""
        try:
            from .simulation_play import (
                _schedule_entry_match_key,  # type: ignore
                get_csv_play_timeline_active_keys_snap,
            )

            active = get_csv_play_timeline_active_keys_snap()
            ent = None
            for e in getattr(self._csv, "_schedule_row_entries", []) or []:
                if _schedule_entry_match_key(e) in active:
                    ent = e
                    break
            if ent is not None:
                active_state = _format_current_state_line(ent)
                if active_state:
                    set_last_state_title(active_state)
        except Exception:
            pass

        display_state = active_state or get_last_state_title()
        try:
            self._models["state"].set_value(display_state)
        except Exception:
            pass

        # EQP ID: 선택된 CSV의 dwell(현재 시각 또는 최초 행)에서 읽기
        try:
            from .simulation_play import get_cached_csv_playback  # type: ignore

            sel_fn = getattr(self._csv, "_selected_csv_path", None)
            path = sel_fn() if callable(sel_fn) else None
            cached = getattr(self._csv, "_prepared_playback", None)
            if cached is None and path is not None:
                cached = get_cached_csv_playback(path)
            dwells = list(getattr(cached, "dwells", []) or []) if cached is not None else []
            if dwells:
                t = float(csv_t or 0.0)
                best = None
                if t <= 1e-6:
                    best = min(dwells, key=lambda d: float(getattr(d, "start_sec", 0.0) or 0.0))
                else:
                    for d in dwells:
                        s = float(getattr(d, "start_sec", 0.0) or 0.0)
                        e = float(getattr(d, "end_sec", 0.0) or 0.0)
                        if s <= t < e:
                            best = d
                            break
                    if best is None:
                        best = min(
                            dwells,
                            key=lambda d: abs(float(getattr(d, "start_sec", 0.0) or 0.0) - t),
                        )
                eqp_id_val = str(getattr(best, "eqp_id", "") or "").strip() if best is not None else ""
        except Exception:
            eqp_id_val = ""

        # CSV 현재행(또는 최초행)로부터 다른 컬럼도 참조할 수 있게 row를 보관
        cur_dwell = None
        try:
            from .simulation_play import get_cached_csv_playback  # type: ignore

            sel_fn = getattr(self._csv, "_selected_csv_path", None)
            path = sel_fn() if callable(sel_fn) else None
            cached = getattr(self._csv, "_prepared_playback", None)
            if cached is None and path is not None:
                cached = get_cached_csv_playback(path)
            dwells = list(getattr(cached, "dwells", []) or []) if cached is not None else []
            if dwells:
                t = float(csv_t or 0.0)
                if t <= 1e-6:
                    cur_dwell = min(dwells, key=lambda d: float(getattr(d, "start_sec", 0.0) or 0.0))
                else:
                    for d in dwells:
                        s = float(getattr(d, "start_sec", 0.0) or 0.0)
                        e = float(getattr(d, "end_sec", 0.0) or 0.0)
                        if s <= t < e:
                            cur_dwell = d
                            break
                    if cur_dwell is None:
                        cur_dwell = min(
                            dwells,
                            key=lambda d: abs(float(getattr(d, "start_sec", 0.0) or 0.0) - t),
                        )
        except Exception:
            cur_dwell = None

        # 행 spec 기반 텍스트 갱신(값은 config의 value 템플릿/고정값)
        def _resolve_value(raw: str) -> str:
            s = str(raw or "")
            if "{" not in s:
                return s

            def _token_value(tok_raw: str) -> str:
                tok = (tok_raw or "").strip()
                key = tok.lower()
                # 예약 토큰
                if key in ("time",):
                    return str(time_s)
                if key in ("state",):
                    return str(display_state or "").strip()
                if key in ("eq_model", "eqmodel", "eq-model"):
                    return str(STATUS_PANEL_EQ_MODEL_VALUE or "").strip()
                if key in ("eqp_id", "eqpid", "eqp-id"):
                    return str(eqp_id_val or "").strip()
                # CSV 컬럼: 현재 dwell 에서 속성으로 제공되는 값만 지원(v1)
                if cur_dwell is not None:
                    if hasattr(cur_dwell, key):
                        try:
                            v = getattr(cur_dwell, key)
                            return str(v if v is not None else "").strip()
                        except Exception:
                            return ""
                    # 몇몇 컬럼은 이름이 다를 수 있어 alias 처리
                    if key == "slot" and hasattr(cur_dwell, "cassette_slot"):
                        try:
                            return str(getattr(cur_dwell, "cassette_slot") or "").strip()
                        except Exception:
                            return ""
                return ""

            def _sub(m: re.Match) -> str:
                return _token_value(m.group(1))

            return _TOKEN_RE.sub(_sub, s)

        for spec in list(STATUS_PANEL_ROWS or []):
            k = str(getattr(spec, "key", "") or "")
            if not k:
                continue
            try:
                lbl = self._labels.get(k)
                if lbl is None:
                    continue
                lbl.text = _resolve_value(str(getattr(spec, "value", "") or ""))
            except Exception:
                pass


__all__ = ["LamViewportStatusPanel"]

