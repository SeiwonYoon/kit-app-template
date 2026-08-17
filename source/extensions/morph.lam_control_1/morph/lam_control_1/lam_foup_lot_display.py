"""FOUP lot_id 표시·색상 — 3D FOUP 패널·FOUP 슬롯 웨이퍼 번호 라벨 공용."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .lam_sim_control_defaults import (
    FOUP1_LOT_COLOR_RGBA,
    FOUP2_LOT_COLOR_RGBA,
    FOUP3_LOT_COLOR_RGBA,
)

_DEFAULT_LOT_COLORS: Dict[int, Tuple[float, float, float, float]] = {
    1: FOUP1_LOT_COLOR_RGBA,
    2: FOUP2_LOT_COLOR_RGBA,
    3: FOUP3_LOT_COLOR_RGBA,
}


def foup_lot_color_rgba(foup_index: int) -> Tuple[float, float, float, float]:
    """FOUP1~3 lot_id·슬롯 웨이퍼 번호 라벨 색."""
    return _DEFAULT_LOT_COLORS.get(int(foup_index), (1.0, 1.0, 1.0, 1.0))


def _clean_lot_id(raw: str) -> str:
    lid = str(raw or "").strip()
    if not lid or lid.startswith("__anon_"):
        return ""
    return lid


def foup_lot_ids_from_lot_map(lots_to_foup: Mapping[str, int]) -> Dict[int, str]:
    """``build_lot_id_to_foup_index`` 결과 → foup_index → lot_id."""
    out: Dict[int, str] = {}
    for lot_id, fi in sorted(lots_to_foup.items(), key=lambda kv: int(kv[1])):
        fi = int(fi)
        if fi not in (1, 2, 3) or fi in out:
            continue
        lid = _clean_lot_id(lot_id)
        if lid:
            out[fi] = lid
    return out


def foup_lot_ids_from_dwells(dwells: Optional[Sequence[Any]]) -> Dict[int, str]:
    """Dwell 타임라인에서 FOUP별 대표 lot_id (시간순 최초 등장)."""
    out: Dict[int, str] = {}
    if not dwells:
        return out
    ordered = sorted(
        dwells,
        key=lambda d: (float(getattr(d, "start_sec", 0.0) or 0.0), int(getattr(d, "cassette_slot", 0) or 0)),
    )
    for d in ordered:
        fi = int(getattr(d, "foup_index", 0) or 0)
        if fi not in (1, 2, 3) or fi in out:
            continue
        lid = _clean_lot_id(str(getattr(d, "lot_id", "") or ""))
        if lid:
            out[fi] = lid
    return out


def apply_foup_lot_display_from_lot_map(
    lots_to_foup: Mapping[str, int],
    *,
    screen: int = 1,
) -> None:
    """파싱 직후 lot→foup 매핑을 overlay 상태에 반영."""
    from .lam_viewport_overlay_state import set_foup_lot_id_by_index

    set_foup_lot_id_by_index(foup_lot_ids_from_lot_map(lots_to_foup), screen=screen)
    _refresh_foup_lot_display_ui(screen=screen)


def apply_foup_lot_display_from_dwells(
    dwells: Optional[Sequence[Any]],
    *,
    screen: int = 1,
) -> None:
    """Play 시작 등 dwell 목록으로 FOUP lot_id 갱신."""
    from .lam_viewport_overlay_state import set_foup_lot_id_by_index

    set_foup_lot_id_by_index(foup_lot_ids_from_dwells(dwells), screen=screen)
    _refresh_foup_lot_display_ui(screen=screen)


def _refresh_foup_lot_display_ui(*, screen: int) -> None:
    try:
        from .lam_viewport_foup_status_3d import refresh_foup_status_panel_ui

        refresh_foup_status_panel_ui(screen=screen)
    except Exception:
        pass
    try:
        from .lam_wafer_viewport_labels import notify_wafer_label_tracker_changed

        notify_wafer_label_tracker_changed(screen)
    except Exception:
        pass


__all__ = [
    "apply_foup_lot_display_from_dwells",
    "apply_foup_lot_display_from_lot_map",
    "foup_lot_color_rgba",
    "foup_lot_ids_from_dwells",
    "foup_lot_ids_from_lot_map",
]
