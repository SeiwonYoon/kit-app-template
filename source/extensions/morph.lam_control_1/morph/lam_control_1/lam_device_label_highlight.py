"""기기정보 3D 라벨 — PM / Airlock 배경색 상태 (시뮬 재생·파싱 무영향).

- PM1~5: 항상 강조(파란) 배경 — ``lam_viewport_device_labels_3d`` 가 spec 이름으로 판별.
- Airlock 1·2: VTM airlock JSON 시작 → 강조 / ATM airlock JSON 시작 → spec 기본색.
- Play 시작: dwell 타임라인으로 airlock 초기 wafer 의 **가장 빠른 다음 이송**이 ATM 이면 강조.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

_ATM_AIRLOCK_EVENT_RE = re.compile(r"^atm_airlock([12])_(pick|place)$", re.IGNORECASE)
_VTM_AIRLOCK_EVENT_RE = re.compile(
    r"^vtm_airlock([12])_(?:left|right)_(pick|place)$",
    re.IGNORECASE,
)
_AIRLOCK_SLOT_RE = re.compile(r"^airlock([12])_", re.IGNORECASE)

_lock = threading.Lock()
_airlock_highlight_by_screen: Dict[int, Dict[int, bool]] = {
    1: {1: False, 2: False},
}
_highlight_revision_by_screen: Dict[int, int] = {1: 0}


def _ensure_screen(screen: int) -> Tuple[Dict[int, bool], int]:
    si = max(1, int(screen))
    hl = _airlock_highlight_by_screen.get(si)
    if hl is None:
        hl = {1: False, 2: False}
        _airlock_highlight_by_screen[si] = hl
    rev = int(_highlight_revision_by_screen.get(si, 0))
    return hl, rev


def _bump_revision_unlocked(screen: int) -> None:
    si = max(1, int(screen))
    _highlight_revision_by_screen[si] = int(_highlight_revision_by_screen.get(si, 0)) + 1


def get_device_label_highlight_revision(*, screen: int = 1) -> int:
    si = max(1, int(screen))
    with _lock:
        return int(_highlight_revision_by_screen.get(si, 0))


def is_airlock_highlighted(airlock_index: int, *, screen: int = 1) -> bool:
    al = int(airlock_index)
    if al not in (1, 2):
        return False
    with _lock:
        hl, _ = _ensure_screen(screen)
        return bool(hl.get(al, False))


def reset_device_label_highlights(*, screen: Optional[int] = None) -> None:
    """CSV 정지(초기화) — airlock 강조 해제."""
    with _lock:
        if screen is None:
            _airlock_highlight_by_screen.clear()
            _highlight_revision_by_screen.clear()
            _airlock_highlight_by_screen[1] = {1: False, 2: False}
            _highlight_revision_by_screen[1] = 0
            return
        si = max(1, int(screen))
        _airlock_highlight_by_screen[si] = {1: False, 2: False}
        _bump_revision_unlocked(si)


def _set_airlock_highlight(airlock_index: int, highlighted: bool, *, screen: int) -> None:
    al = int(airlock_index)
    if al not in (1, 2):
        return
    with _lock:
        hl, _ = _ensure_screen(screen)
        prev = bool(hl.get(al, False))
        hl[al] = bool(highlighted)
        if prev != bool(highlighted):
            _bump_revision_unlocked(screen)


def record_device_label_event_from_schedule_entry(
    sched: Any,
    *,
    screen: Optional[int] = None,
) -> bool:
    """JSON 블록 시작 — airlock VTM/ATM pick·place 에 따라 AL1/AL2 강조 갱신."""
    try:
        from .lam_csv_play_screen import current_csv_play_screen

        si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    except Exception:
        si = max(1, int(screen or 1))

    ev = str(getattr(sched, "event_name", "") or "").strip()
    if ev.lower().endswith(".json"):
        ev = ev[:-5]
    ev_l = ev.lower()
    if not ev_l:
        return False

    changed = False
    m_atm = _ATM_AIRLOCK_EVENT_RE.match(ev_l)
    if m_atm:
        _set_airlock_highlight(int(m_atm.group(1)), False, screen=si)
        changed = True
    else:
        m_vtm = _VTM_AIRLOCK_EVENT_RE.match(ev_l)
        if m_vtm:
            _set_airlock_highlight(int(m_vtm.group(1)), True, screen=si)
            changed = True

    if changed:
        notify_device_labels_ui_refresh(screen=si)
    return changed


def seed_airlock_highlights_from_dwells(
    dwells: Optional[Sequence[Any]],
    *,
    screen: int = 1,
) -> None:
    """Play 시작 — airlock 최초 wafer 의 다음 이송(ATM/VTM)으로 AL1/AL2 초기색 seed."""
    si = max(1, int(screen))
    reset_device_label_highlights(screen=si)

    if not dwells:
        notify_device_labels_ui_refresh(screen=si)
        return

    try:
        from .simulation_play import _classify_transfer_robot, _group_dwell_tours
    except Exception:
        notify_device_labels_ui_refresh(screen=si)
        return

    next_by_airlock: Dict[int, List[Tuple[float, str]]] = {1: [], 2: []}
    try:
        for _wk, tour in _group_dwell_tours(list(dwells)):
            if not tour or len(tour) < 2:
                continue
            first = tour[0]
            second = tour[1]
            sk0 = str(getattr(first, "slot_key", "") or "").strip()
            sk1 = str(getattr(second, "slot_key", "") or "").strip()
            m = _AIRLOCK_SLOT_RE.match(sk0)
            if not m:
                continue
            al_n = int(m.group(1))
            robot = str(_classify_transfer_robot(sk0, sk1) or "ATM").upper()
            t_next = float(getattr(second, "start_sec", 0.0) or 0.0)
            next_by_airlock[al_n].append((t_next, robot))
    except Exception:
        pass

    for al_n in (1, 2):
        items = next_by_airlock.get(al_n) or []
        if not items:
            continue
        _t_min, robot = min(items, key=lambda x: float(x[0]))
        if robot == "ATM":
            _set_airlock_highlight(al_n, True, screen=si)

    notify_device_labels_ui_refresh(screen=si)


def notify_device_labels_ui_refresh(*, screen: Optional[int] = None) -> None:
    """3D 기기 라벨 배경 즉시 갱신 (재생 스레드 → post_update)."""
    try:
        from .lam_viewport_device_labels_3d import refresh_device_labels_panel_ui

        refresh_device_labels_panel_ui(screen=screen)
    except Exception:
        pass


__all__ = [
    "get_device_label_highlight_revision",
    "is_airlock_highlighted",
    "reset_device_label_highlights",
    "record_device_label_event_from_schedule_entry",
    "seed_airlock_highlights_from_dwells",
    "notify_device_labels_ui_refresh",
]
