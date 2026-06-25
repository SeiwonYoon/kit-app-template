"""프리런 타임테이블 — 별도 창 + Placer 스크롤 + sim_now 녹색 하이라이트."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import omni.ui as ui

from .control_sim_prerun_playback import TimetableRowMeta

_STYLE_ROW_IDLE = {
    "color": 0xFFDDDDDD,
    "background_color": 0x001A1E26,
}
_STYLE_ROW_ACTIVE = {
    "color": 0xFF00FF88,
    "background_color": 0x4422AA44,
}
_STYLE_BUSY = {"color": 0xFF9AA4B2}
# 타임테이블 폰트·행 높이 — 여기만 바꾸면 UI 전체에 반영된다.
_TIMETABLE_HEADER_FONT_SIZE = 14
_TIMETABLE_ROW_FONT_SIZE = 13
_TIMETABLE_ROW_HEIGHT_PX = 26
_STYLE_HEADER = {"color": 0xFFBFE7FF, "font_size": _TIMETABLE_HEADER_FONT_SIZE}
_ROW_STEP_PX = float(_TIMETABLE_ROW_HEIGHT_PX)
_TIMETABLE_VIEWPORT_H = 440
_THUMB_MIN_H = 24.0
_TIMETABLE_INNER_SPACING_PX = 2.0
_TIMETABLE_HEADER_EST_HEIGHT_PX = 22.0
_TIMETABLE_DEFAULT_CONTENT_WIDTH_PX = 680.0


def _timetable_line_height_px(font_size: int = _TIMETABLE_ROW_FONT_SIZE) -> float:
    return max(16.0, float(font_size) + 5.0)


def _sync_timetable_content_width(ch: Dict[str, Any]) -> float:
    """뷰포트 실제 너비를 읽어 줄바꿈·스크롤 높이 추정에 쓴다."""
    w = float(_TIMETABLE_DEFAULT_CONTENT_WIDTH_PX)
    vp = _viewport(ch)
    if vp is not None:
        for attr in ("computed_width", "width"):
            try:
                raw = getattr(vp, attr, None)
                if raw is None:
                    continue
                cw = float(raw)
                if cw > 40.0:
                    w = cw - 6.0
                    break
            except Exception:
                continue
    w = max(120.0, w)
    ch["timetable_content_width_px"] = float(w)
    return float(w)


def _estimate_wrapped_text_height(
    text: str,
    *,
    content_width_px: float,
    font_size: int = _TIMETABLE_ROW_FONT_SIZE,
) -> float:
    """창 너비 기준 대략적인 줄 수로 행 높이를 추정한다."""
    txt = str(text or "")
    if not txt.strip():
        return float(_TIMETABLE_ROW_HEIGHT_PX)
    line_h = _timetable_line_height_px(font_size)
    char_w = max(5.5, float(font_size) * 0.52)
    cols = max(16, int(float(content_width_px) / char_w))
    total_lines = 0
    for block in txt.split("\n"):
        n = len(block)
        total_lines += max(1, (n + cols - 1) // cols)
    h = float(total_lines) * line_h + 6.0
    return max(float(_TIMETABLE_ROW_HEIGHT_PX), h)


def _average_row_step_px(ch: Dict[str, Any]) -> float:
    stored = ch.get("timetable_row_heights")
    if isinstance(stored, list) and stored:
        try:
            return max(float(_TIMETABLE_ROW_HEIGHT_PX), sum(float(h) for h in stored) / float(len(stored)))
        except Exception:
            pass
    return float(_ROW_STEP_PX)


def timetable_viewport_height() -> int:
    return int(_TIMETABLE_VIEWPORT_H)


def _viewport_h(ch: Dict[str, Any]) -> float:
    try:
        h = float(ch.get("timetable_viewport_h", _TIMETABLE_VIEWPORT_H) or _TIMETABLE_VIEWPORT_H)
        if h > 1.0:
            return h
    except Exception:
        pass
    return float(_TIMETABLE_VIEWPORT_H)


def _placer(ch: Dict[str, Any]) -> Any:
    return ch.get("timetable_placer")


def _viewport(ch: Dict[str, Any]) -> Any:
    return ch.get("timetable_viewport")


def _saved_scroll_y(ch: Dict[str, Any]) -> float:
    try:
        return max(0.0, float(ch.get("_tt_scroll_y", 0.0) or 0.0))
    except Exception:
        return 0.0


def _content_height(ch: Dict[str, Any]) -> float:
    stored = ch.get("timetable_row_heights")
    if isinstance(stored, list) and stored:
        try:
            header_h = float(
                ch.get("timetable_header_height_px", _TIMETABLE_HEADER_EST_HEIGHT_PX)
                or _TIMETABLE_HEADER_EST_HEIGHT_PX
            )
            row_sum = sum(float(h) for h in stored)
            gaps = float(len(stored)) * float(_TIMETABLE_INNER_SPACING_PX)
            return max(0.0, header_h + row_sum + gaps + 8.0)
        except Exception:
            pass
    metas = ch.get("timetable_row_metas")
    n_rows = 1
    if isinstance(metas, list):
        n_rows += len(metas)
    return max(0.0, float(n_rows) * _ROW_STEP_PX + 8.0)


def _max_scroll_y(ch: Dict[str, Any]) -> float:
    return max(0.0, _content_height(ch) - _viewport_h(ch))


def _clamp_scroll_y(ch: Dict[str, Any], scroll_y: float) -> float:
    v = max(0.0, float(scroll_y))
    m = _max_scroll_y(ch)
    if m > 0.0:
        v = min(v, m)
    return v


def _apply_scroll_y(ch: Dict[str, Any], scroll_y: float) -> float:
    y = _clamp_scroll_y(ch, scroll_y)
    ch["_tt_scroll_y"] = float(y)
    pl = _placer(ch)
    if pl is not None:
        try:
            pl.offset_y = -float(y)
        except Exception:
            pass
    _update_scroll_thumb(ch)
    return float(y)


def _update_scroll_thumb(ch: Dict[str, Any]) -> None:
    thumb_pl = ch.get("timetable_scroll_thumb_placer")
    thumb = ch.get("timetable_scroll_thumb")
    if thumb_pl is None or thumb is None:
        return
    max_y = _max_scroll_y(ch)
    cur = _saved_scroll_y(ch)
    track_h = _viewport_h(ch)
    if max_y <= 1e-6:
        try:
            thumb.visible = False
        except Exception:
            pass
        return
    ratio = float(cur) / float(max_y)
    thumb_h = max(_THUMB_MIN_H, track_h * (track_h / max(track_h, _content_height(ch))))
    thumb_h = min(track_h, thumb_h)
    thumb_y = ratio * max(0.0, track_h - thumb_h)
    try:
        thumb.visible = True
        thumb_pl.offset_y = float(thumb_y)
    except Exception:
        pass


def _wheel_step(wheel_y: float, ch: Optional[Dict[str, Any]] = None) -> float:
    d = float(wheel_y)
    if abs(d) <= 1e-9:
        return 0.0
    step = _average_row_step_px(ch) if isinstance(ch, dict) else float(_ROW_STEP_PX)
    if abs(d) <= 4.0:
        return d * float(step)
    return d


def bind_timetable_wheel(ch: Dict[str, Any]) -> None:
    vp = _viewport(ch)
    if vp is None:
        return

    def _on_wheel(_x: float, wheel_y: float, _mod: int) -> None:
        step = _wheel_step(wheel_y, ch)
        if abs(step) <= 1e-9:
            return
        _apply_scroll_y(ch, _saved_scroll_y(ch) - step)

    try:
        vp.set_mouse_wheel_fn(_on_wheel)
    except Exception:
        pass


def bind_timetable_scroll_ui(ch: Dict[str, Any]) -> None:
    bind_timetable_wheel(ch)
    _apply_scroll_y(ch, _saved_scroll_y(ch))


def build_timetable_column_ui(ch: Dict[str, Any], *, screen: int, viewport_h: Optional[int] = None) -> None:
    """타임테이블 전용 창 열에 Placer 뷰포트를 구성한다."""
    vh = int(viewport_h or _TIMETABLE_VIEWPORT_H)
    ch["timetable_viewport_h"] = vh
    ch["timetable_panel"] = ui.Frame(height=ui.Fraction(1.0))
    with ch["timetable_panel"]:
        with ui.VStack(spacing=2, height=ui.Fraction(1.0)):
            ui.Label(
                f"타임테이블 (프리런) — 화면{int(screen)}",
                height=max(18, _TIMETABLE_HEADER_FONT_SIZE + 4),
                style={"color": 0xFFBFE7FF, "font_size": _TIMETABLE_HEADER_FONT_SIZE},
            )
            with ui.HStack(spacing=0, height=ui.Fraction(1.0)):
                ch["timetable_viewport"] = ui.Frame(
                    height=ui.Fraction(1.0),
                    width=ui.Fraction(1.0),
                    style={"background_color": 0xFF1A1E26, "border_width": 1, "border_color": 0xFF3A3A3A},
                )
                with ch["timetable_viewport"]:
                    ch["timetable_placer"] = ui.Placer(offset_x=0, offset_y=0)
                    with ch["timetable_placer"]:
                        ch["timetable_host"] = ui.VStack(spacing=2, height=0)
                ch["timetable_scroll_track"] = ui.Frame(
                    width=12,
                    height=ui.Fraction(1.0),
                    style={"background_color": 0xFF2A3038, "border_width": 1, "border_color": 0xFF3A3A3A},
                )
                with ch["timetable_scroll_track"]:
                    ch["timetable_scroll_thumb_placer"] = ui.Placer(offset_x=1, offset_y=0)
                    with ch["timetable_scroll_thumb_placer"]:
                        ch["timetable_scroll_thumb"] = ui.Rectangle(
                            width=8,
                            height=48,
                            style={"background_color": 0xFF7B8799},
                        )
    ch["history_frame"] = ch["timetable_viewport"]
    ch["history_label"] = None
    ch["timetable_inner"] = None
    ch["timetable_busy_widget"] = None
    ch["timetable_row_buttons"] = []
    ch["timetable_row_labels"] = []
    ch["timetable_row_metas"] = []
    ch["timetable_interactive"] = False
    ch.setdefault("_tt_scroll_y", 0.0)


def _destroy_ui_children(container: Any) -> None:
    try:
        for child in list(getattr(container, "children", []) or []):
            try:
                child.destroy()
            except Exception:
                pass
    except Exception:
        pass


def timetable_rows_locked(ext: Any) -> bool:
    """재생 중 타임테이블 행 UI 를 다른 코드가 지우지 못하게 막는다."""
    return bool(getattr(ext, "_sim_timetable_rows_locked", False))


def unlock_timetable_rows(ext: Any) -> None:
    try:
        ext._sim_timetable_rows_locked = False
    except Exception:
        pass


def lock_timetable_rows(ext: Any) -> None:
    try:
        ext._sim_timetable_rows_locked = True
    except Exception:
        pass


def _clear_timetable_host_content(ch: Dict[str, Any], ext: Any, *, force: bool = False) -> bool:
    """host 자식(행·busy) 제거. force=False 이고 재생 잠금이면 아무 것도 하지 않는다."""
    if (not force) and timetable_rows_locked(ext):
        return False
    host = ch.get("timetable_host")
    if host is None:
        return False
    _destroy_ui_children(host)
    ch["timetable_inner"] = None
    ch["timetable_busy_widget"] = None
    return True


def _hide_history_label(ch: Dict[str, Any]) -> None:
    lbl = ch.get("history_label")
    if lbl is None:
        return
    try:
        lbl.visible = False
        lbl.height = 1
    except Exception:
        pass


def reset_timetable_channel_to_idle(
    ch: Dict[str, Any],
    *,
    screen: int,
    message: str = "타임테이블 대기 중 — Start 후 프리런 결과 표시",
    ext: Any = None,
    force: bool = True,
) -> None:
    """기존 인터랙티브 행을 모두 제거하고 대기 문구만 표시한다(Start/Reset 전용)."""
    if ext is not None and (not force) and timetable_rows_locked(ext):
        return
    host = ch.get("timetable_host")
    ch["timetable_interactive"] = False
    ch["timetable_row_buttons"] = []
    ch["timetable_row_labels"] = []
    ch["timetable_row_label_pairs"] = []
    ch["timetable_row_bgs"] = []
    ch["timetable_row_metas"] = []
    ch["timetable_row_heights"] = []
    ch["_timetable_row_style_cache"] = {}
    ch["_timetable_highlight_t"] = None
    ch["_timetable_highlight_idx"] = None
    ch["_tt_scroll_y"] = 0.0
    if host is None:
        return
    if ext is not None:
        _clear_timetable_host_content(ch, ext, force=force)
    else:
        _destroy_ui_children(host)
        ch["timetable_inner"] = None
        ch["timetable_busy_widget"] = None
    with host:
        ch["timetable_busy_widget"] = ui.Label(
            str(message or "타임테이블 대기 중"),
            word_wrap=True,
            style=_STYLE_BUSY,
        )
    _hide_history_label(ch)
    try:
        _apply_scroll_y(ch, 0.0)
    except Exception:
        pass


def set_timetable_busy_label(
    ch: Dict[str, Any],
    busy: bool,
    *,
    screen: int,
    ext: Any = None,
) -> None:
    """프리런 시작(busy=True) 시에만 기존 행을 지운다. busy=False 는 busy 라벨만 제거."""
    if not busy:
        bw = ch.get("timetable_busy_widget")
        if bw is not None:
            try:
                bw.destroy()
            except Exception:
                pass
            ch["timetable_busy_widget"] = None
        return
    if ext is not None and timetable_rows_locked(ext):
        return
    host = ch.get("timetable_host")
    ch["timetable_interactive"] = False
    ch["timetable_row_buttons"] = []
    ch["timetable_row_labels"] = []
    ch["timetable_row_label_pairs"] = []
    ch["timetable_row_bgs"] = []
    ch["timetable_row_metas"] = []
    ch["timetable_row_heights"] = []
    ch["_timetable_row_style_cache"] = {}
    ch["_timetable_highlight_t"] = None
    ch["_timetable_highlight_idx"] = None
    ch["_tt_scroll_y"] = 0.0
    if host is not None:
        if ext is not None:
            _clear_timetable_host_content(ch, ext, force=True)
        else:
            _destroy_ui_children(host)
            ch["timetable_inner"] = None
            ch["timetable_busy_widget"] = None
        with host:
            ch["timetable_busy_widget"] = ui.Label(
                "타임테이블 생성 중…",
                word_wrap=True,
                style=_STYLE_BUSY,
            )
    _hide_history_label(ch)
    try:
        _apply_scroll_y(ch, 0.0)
    except Exception:
        pass


def mount_interactive_timetable(
    ext: Any,
    ch: Dict[str, Any],
    *,
    screen: int,
    header: str,
    row_metas: List[TimetableRowMeta],
    on_row_clicked: Callable[[int], None],
) -> None:
    host = ch.get("timetable_host")
    if host is None:
        return
    ch["_tt_scroll_y"] = 0.0
    ch["timetable_row_metas"] = list(row_metas or [])
    ch["timetable_on_row_clicked"] = on_row_clicked
    ch["timetable_interactive"] = True
    ch["timetable_row_buttons"] = []
    ch["timetable_row_labels"] = []
    ch["timetable_row_label_pairs"] = []
    ch["timetable_row_bgs"] = []
    ch["_timetable_row_style_cache"] = {}
    ch["_timetable_highlight_t"] = None
    ch["_timetable_highlight_idx"] = None
    _clear_timetable_host_content(ch, ext, force=True)
    ch["timetable_busy_widget"] = None
    _hide_history_label(ch)

    row_labels: List[Any] = []
    row_pairs: List[Tuple[Any, Any]] = []
    row_bgs: List[Any] = []
    row_stacks: List[Any] = []
    row_heights: List[float] = []
    content_w = _sync_timetable_content_width(ch)

    with host:
        inner = ui.VStack(spacing=int(_TIMETABLE_INNER_SPACING_PX), height=0)
        ch["timetable_inner"] = inner
        with inner:
            ui.Label(
                str(header or f"[SIM] 타임테이블(프리런) — 화면{int(screen)}"),
                width=ui.Fraction(1.0),
                height=0,
                word_wrap=True,
                style=_STYLE_HEADER,
            )
            for meta in row_metas:
                ri = int(meta.row_index)
                line = str(meta.display_line)
                row_h = _estimate_wrapped_text_height(line, content_width_px=content_w)
                row_heights.append(float(row_h))
                row_z = ui.ZStack(width=ui.Fraction(1.0))

                def _mk_press(row_i: int = ri) -> Callable[[float, float, int, Any], None]:
                    def _on_press(_x: float, _y: float, button: int, _mods: Any) -> None:
                        if int(button) != 0:
                            return
                        try:
                            fn = ch.get("timetable_on_row_clicked")
                            if callable(fn):
                                fn(int(row_i))
                        except Exception:
                            pass

                    return _on_press

                try:
                    row_z.set_mouse_pressed_fn(_mk_press())
                except Exception:
                    pass

                with row_z:
                    bg = ui.Rectangle(
                        width=ui.Fraction(1.0),
                        height=ui.Fraction(1.0),
                        style={"background_color": _STYLE_ROW_IDLE["background_color"]},
                    )
                    lbl_idle = ui.Label(
                        line,
                        word_wrap=True,
                        width=ui.Fraction(1.0),
                        height=0,
                        alignment=ui.Alignment.LEFT_TOP,
                        style={**_STYLE_ROW_IDLE, "font_size": _TIMETABLE_ROW_FONT_SIZE},
                    )
                    lbl_active = ui.Label(
                        line,
                        word_wrap=True,
                        width=ui.Fraction(1.0),
                        height=0,
                        alignment=ui.Alignment.LEFT_TOP,
                        style={**_STYLE_ROW_ACTIVE, "font_size": _TIMETABLE_ROW_FONT_SIZE},
                        visible=False,
                    )
                row_bgs.append(bg)
                row_stacks.append(row_z)
                row_pairs.append((lbl_idle, lbl_active))
                row_labels.append(lbl_idle)

    ch["timetable_row_labels"] = row_labels
    ch["timetable_row_label_pairs"] = row_pairs
    ch["timetable_row_bgs"] = row_bgs
    ch["timetable_row_buttons"] = row_stacks
    ch["timetable_row_heights"] = row_heights
    ch["timetable_header_height_px"] = float(_TIMETABLE_HEADER_EST_HEIGHT_PX)

    try:
        ext._sim_timetable_channels = getattr(ext, "_sim_timetable_channels", None) or {}
        if not isinstance(ext._sim_timetable_channels, dict):
            ext._sim_timetable_channels = {}
        ext._sim_timetable_channels[str(int(screen))] = ch
    except Exception:
        pass
    bind_timetable_scroll_ui(ch)
    _apply_scroll_y(ch, 0.0)
    lock_timetable_rows(ext)


def resolve_active_timetable_bucket(metas: List[TimetableRowMeta], sim_now: float) -> Optional[float]:
    if not metas:
        return None
    t_now = float(sim_now)
    active_t: Optional[float] = None
    for meta in metas:
        try:
            t_row = float(meta.t)
        except Exception:
            continue
        if t_row <= t_now + 1e-6:
            active_t = t_row
        else:
            break
    return active_t


def _active_row_indices_for_bucket(metas: List[TimetableRowMeta], active_t: Optional[float]) -> Set[int]:
    if active_t is None:
        return set()
    out: Set[int] = set()
    for idx, meta in enumerate(metas):
        try:
            if abs(float(meta.t) - float(active_t)) <= 1e-6:
                out.add(int(idx))
        except Exception:
            continue
    return out


def _apply_row_highlight(
    idx: int,
    *,
    want_active: bool,
    pairs: List[Tuple[Any, Any]],
    bgs: List[Any],
) -> None:
    if idx >= len(pairs):
        return
    lbl_idle, lbl_active = pairs[idx]
    if lbl_idle is not None and lbl_active is not None:
        try:
            lbl_idle.visible = not want_active
            lbl_active.visible = want_active
        except Exception:
            pass
    if idx < len(bgs):
        bg = bgs[idx]
        if bg is not None:
            try:
                color = _STYLE_ROW_ACTIVE["background_color"] if want_active else _STYLE_ROW_IDLE["background_color"]
                bg.style = {"background_color": color}
            except Exception:
                pass


def refresh_timetable_row_highlight(ext: Any, *, screen: int, sim_now: float) -> None:
    """현재 sim_now 버킷 행을 녹색으로 — 다음 행 전까지 유지."""
    try:
        ch: Optional[Dict[str, Any]] = None
        by = getattr(ext, "_sim_timetable_channels", None)
        if isinstance(by, dict):
            ch = by.get(str(int(screen)))
        if not isinstance(ch, dict):
            chans = getattr(ext, "_sim_monitor_channels", None)
            if isinstance(chans, list) and 0 < int(screen) <= len(chans):
                cand = chans[int(screen) - 1]
                if isinstance(cand, dict) and cand.get("timetable_interactive"):
                    ch = cand
        if not isinstance(ch, dict) or not ch.get("timetable_interactive"):
            return
        pairs = ch.get("timetable_row_label_pairs")
        bgs = ch.get("timetable_row_bgs")
        metas = ch.get("timetable_row_metas")
        if not isinstance(pairs, list) or not isinstance(metas, list):
            return
        if not isinstance(bgs, list):
            bgs = []
            ch["timetable_row_bgs"] = bgs
        active_t = resolve_active_timetable_bucket(metas, float(sim_now))
        active_idx = _active_row_indices_for_bucket(metas, active_t)
        prev_t = ch.get("_timetable_highlight_t")
        prev_idx = ch.get("_timetable_highlight_idx")
        if prev_t == active_t and prev_idx == active_idx:
            return
        ch["_timetable_highlight_t"] = active_t
        ch["_timetable_highlight_idx"] = active_idx
        for idx in range(len(metas)):
            want_active = int(idx) in active_idx
            _apply_row_highlight(idx, want_active=want_active, pairs=pairs, bgs=bgs)
    except Exception:
        pass


def refresh_all_timetable_highlights(ext: Any) -> None:
    """재생 중·Seek 후 모든 화면 하이라이트 동기화."""
    try:
        from .control_sim_multi_playback import get_sim_playback_player

        seen: Set[int] = set()
        by = getattr(ext, "_sim_timetable_channels", None)
        if isinstance(by, dict) and by:
            for sk, ch in by.items():
                if not isinstance(ch, dict) or not ch.get("timetable_interactive"):
                    continue
                try:
                    si = int(sk)
                except Exception:
                    try:
                        si = int(ch.get("screen", 1) or 1)
                    except Exception:
                        continue
                seen.add(int(si))
                t_now = 0.0
                player = get_sim_playback_player(ext, si)
                if player is not None:
                    try:
                        t_now = float(player.sim_now(si))
                    except Exception:
                        t_now = 0.0
                refresh_timetable_row_highlight(ext, screen=si, sim_now=t_now)
        chans = getattr(ext, "_sim_monitor_channels", None)
        if not isinstance(chans, list):
            return
        for ch in chans:
            if not isinstance(ch, dict) or not ch.get("timetable_interactive"):
                continue
            try:
                si = int(ch.get("screen", 1))
            except Exception:
                continue
            if int(si) in seen:
                continue
            t_now = 0.0
            player = get_sim_playback_player(ext, si)
            if player is not None:
                try:
                    t_now = float(player.sim_now(si))
                except Exception:
                    t_now = 0.0
            refresh_timetable_row_highlight(ext, screen=si, sim_now=t_now)
    except Exception:
        pass
