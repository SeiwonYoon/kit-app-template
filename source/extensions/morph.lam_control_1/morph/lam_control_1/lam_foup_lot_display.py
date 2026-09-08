"""FOUP lot_id 표시·색상 — 3D FOUP 패널·FOUP 슬롯 웨이퍼 번호 라벨 공용.

lot 수 ≤3: 등장 순 FOUP1..N (기존).
lot 수 ≥4: 4번째→FOUP1, 5번째→FOUP2, … 순환. 해당 lot **첫 공정 시작** 시
표시 lot명·카운트·애니 대상(``foup_index``)·웨이퍼 번호 색을 그 FOUP 기준으로 맞춤.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

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


@dataclass(frozen=True)
class FoupLotTakeover:
    """4번째 이후 lot 이 기존 FOUP 슬롯을 이어받을 때."""

    t_sec: float
    foup_index: int
    lot_id: str
    ordinal: int  # 1-based 등장 순 (4, 5, …)


def foup_lot_color_rgba(foup_index: int) -> Tuple[float, float, float, float]:
    """FOUP1~3 lot_id·슬롯 웨이퍼 번호 라벨 색."""
    return _DEFAULT_LOT_COLORS.get(int(foup_index), (1.0, 1.0, 1.0, 1.0))


def _clean_lot_id(raw: str) -> str:
    lid = str(raw or "").strip()
    if not lid or lid.startswith("__anon_"):
        return ""
    return lid


def foup_slot_for_lot_ordinal(ordinal: int) -> int:
    """1-based lot 등장 순 → FOUP1~3 (순환)."""
    n = max(1, int(ordinal))
    return ((n - 1) % 3) + 1


def ordered_lot_first_starts(
    dwells: Optional[Sequence[Any]],
) -> List[Tuple[str, float]]:
    """dwell 기준 lot_id 최초 등장 (start_sec 오름차순)."""
    first: Dict[str, float] = {}
    if not dwells:
        return []
    for d in dwells:
        lid = _clean_lot_id(str(getattr(d, "lot_id", "") or ""))
        if not lid:
            continue
        try:
            t0 = float(getattr(d, "start_sec", 0.0) or 0.0)
        except Exception:
            t0 = 0.0
        if lid not in first or t0 < first[lid]:
            first[lid] = t0
    return sorted(first.items(), key=lambda kv: (float(kv[1]), str(kv[0])))


def build_foup_lot_takeovers(
    dwells: Optional[Sequence[Any]],
) -> Tuple[FoupLotTakeover, ...]:
    """4번째 이후 lot → (첫 공정 시각, 재사용 FOUP, lot_id).

    lot ≤3 이면 빈 튜플 (기존 동작만).
    """
    ordered = ordered_lot_first_starts(dwells)
    if len(ordered) <= 3:
        return ()
    out: List[FoupLotTakeover] = []
    for i, (lid, t0) in enumerate(ordered):
        ordinal = i + 1
        if ordinal <= 3:
            continue
        out.append(
            FoupLotTakeover(
                t_sec=float(t0),
                foup_index=foup_slot_for_lot_ordinal(ordinal),
                lot_id=lid,
                ordinal=int(ordinal),
            )
        )
    return tuple(out)


def initial_foup_lot_ids_from_ordered(
    ordered_lots: Sequence[Tuple[str, float]],
) -> Dict[int, str]:
    """Play 시작( t=0 ) 표시용 — 최대 FOUP1~3 = 처음 최대 3 lot."""
    out: Dict[int, str] = {}
    for i, (lid, _t) in enumerate(ordered_lots[:3]):
        fi = i + 1
        if lid:
            out[fi] = lid
    return out


def foup_lot_ids_from_lot_map(lots_to_foup: Mapping[str, int]) -> Dict[int, str]:
    """``build_lot_id_to_foup_index`` 결과 → foup_index → lot_id.

    같은 FOUP 에 여러 lot 이 매핑되면 **등장 순 앞선** lot 을 초기 표시로 쓴다
    (순환 재사용 시 4번째 lot 은 takeover 시점에 교체).
    """
    # lot 등장 순 보존: dict 삽입 순이 build_lot_id_to_foup_index 와 같음
    by_fi: Dict[int, List[str]] = {1: [], 2: [], 3: []}
    for lot_id, fi in lots_to_foup.items():
        fi_i = int(fi)
        if fi_i not in (1, 2, 3):
            continue
        lid = _clean_lot_id(lot_id)
        if lid:
            by_fi[fi_i].append(lid)
    out: Dict[int, str] = {}
    for fi in (1, 2, 3):
        if by_fi[fi]:
            out[fi] = by_fi[fi][0]
    return out


def foup_lot_ids_from_dwells(dwells: Optional[Sequence[Any]]) -> Dict[int, str]:
    """Dwell 타임라인에서 FOUP별 초기 표시 lot_id (시간순 최초 = 순환 전 lot)."""
    return initial_foup_lot_ids_from_ordered(ordered_lot_first_starts(dwells))


def apply_foup_lot_display_from_lot_map(
    lots_to_foup: Mapping[str, int],
    *,
    screen: int = 1,
) -> None:
    """파싱 직후 lot→foup 매핑을 overlay 상태에 반영 (초기 표시만)."""
    from .lam_viewport_overlay_state import set_foup_lot_id_by_index

    set_foup_lot_id_by_index(foup_lot_ids_from_lot_map(lots_to_foup), screen=screen)
    _refresh_foup_lot_display_ui(screen=screen)


def apply_foup_lot_display_from_dwells(
    dwells: Optional[Sequence[Any]],
    *,
    screen: int = 1,
) -> None:
    """Play 시작 등 dwell 목록으로 FOUP lot_id·순환 takeover 일정 등록."""
    from .lam_viewport_overlay_state import (
        register_foup_lot_takeovers,
        set_foup_lot_id_by_index,
    )

    ordered = ordered_lot_first_starts(dwells)
    set_foup_lot_id_by_index(initial_foup_lot_ids_from_ordered(ordered), screen=screen)
    register_foup_lot_takeovers(build_foup_lot_takeovers(dwells), screen=screen)
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


def assert_foup_lot_cycle_rules() -> None:
    """Kit 없이 순환 매핑·takeover 규칙 회귀."""
    from types import SimpleNamespace

    # ≤3: FOUP1..3 고정
    rows3 = [
        SimpleNamespace(eqp_start_tm=0.0, cassette_slot=1, module_nm="a", lot_id="L1"),
        SimpleNamespace(eqp_start_tm=1.0, cassette_slot=1, module_nm="a", lot_id="L2"),
        SimpleNamespace(eqp_start_tm=2.0, cassette_slot=1, module_nm="a", lot_id="L3"),
    ]
    # 로컬 복제 (omni import 없이)
    ordered = sorted(rows3, key=lambda r: (r.eqp_start_tm, r.cassette_slot, r.module_nm))
    m: Dict[str, int] = {}
    n = 0
    for r in ordered:
        lid = str(r.lot_id).strip()
        if lid not in m:
            n += 1
            m[lid] = ((n - 1) % 3) + 1
    if m != {"L1": 1, "L2": 2, "L3": 3}:
        raise AssertionError(f"≤3 map want L1..3 got {m}")

    # 4·5: FOUP1·FOUP2 재사용
    dwells = [
        SimpleNamespace(lot_id="L1", start_sec=0.0),
        SimpleNamespace(lot_id="L2", start_sec=10.0),
        SimpleNamespace(lot_id="L3", start_sec=20.0),
        SimpleNamespace(lot_id="L4", start_sec=100.0),
        SimpleNamespace(lot_id="L5", start_sec=200.0),
    ]
    tos = build_foup_lot_takeovers(dwells)
    if len(tos) != 2:
        raise AssertionError(f"takeovers want 2 got {tos}")
    if tos[0].lot_id != "L4" or tos[0].foup_index != 1 or abs(tos[0].t_sec - 100.0) > 1e-6:
        raise AssertionError(f"L4→FOUP1@100 want got {tos[0]}")
    if tos[1].lot_id != "L5" or tos[1].foup_index != 2 or abs(tos[1].t_sec - 200.0) > 1e-6:
        raise AssertionError(f"L5→FOUP2@200 want got {tos[1]}")
    if build_foup_lot_takeovers(dwells[:3]):
        raise AssertionError("≤3 에서 takeover 없어야 함")


__all__ = [
    "FoupLotTakeover",
    "apply_foup_lot_display_from_dwells",
    "apply_foup_lot_display_from_lot_map",
    "assert_foup_lot_cycle_rules",
    "build_foup_lot_takeovers",
    "foup_lot_color_rgba",
    "foup_lot_ids_from_dwells",
    "foup_lot_ids_from_lot_map",
    "foup_slot_for_lot_ordinal",
    "initial_foup_lot_ids_from_ordered",
    "ordered_lot_first_starts",
]
