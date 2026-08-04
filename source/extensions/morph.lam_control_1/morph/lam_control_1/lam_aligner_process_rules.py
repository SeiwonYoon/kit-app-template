"""Aligner 공정 절대 규칙 (EAP CSV 에 없음 — FOUP pick 후 합성).

실무·코드 공통 SSOT. 증상 패치 금지 — 이 모듈의 규칙만 plan 에 반영한다.

규칙 요약
---------
1. FOUP pick 직후 **Aligner place** 는 같은 wafer 에 대해 **이어서 세트**로 반드시 삽입.
2. **Aligner pick** 은 그 wafer 가 **Airlock1/2 로 place** 되기 **직전**에만 실행.
   - 그 사이에 다른 wafer 의 ATM 동작이 있으면 그쪽을 먼저 진행(끼어듦).
   - 파싱상 ATM→airlock place 홉이 없으면 → Aligner place 만 두고 pick 보류.
3. Aligner pick 직후 같은 wafer 의 다음 place 가 buffer / FOUP / cooling / 비-airlock
   이면 **금지** → plan 에서 해당 Aligner pick 제거.
4. hide/show 시 cassette 번호는 해당 wafer 와 반드시 일치
   (``stamp_wafer_cassette_label_on_steps`` + visibility mode 정규화).
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence, Tuple

_AIRLOCK_SLOT_RE = re.compile(r"^airlock[12]_\d+$", re.IGNORECASE)
_AIRLOCK_PLACE_EVENT_RE = re.compile(r"^atm_airlock[12]_place$", re.IGNORECASE)
_FORBIDDEN_AFTER_ALIGNER_PICK_RE = re.compile(
    r"^atm_(?:buffer\d+|foup\d+|cooling)_place$",
    re.IGNORECASE,
)
_BUFFER_OR_FOUP_SLOT_RE = re.compile(
    r"^(?:buffer[34]_\d+|foup[123]_\d+|cooling_\d+)$",
    re.IGNORECASE,
)


def is_airlock_slot_key(slot_key: str) -> bool:
    return bool(_AIRLOCK_SLOT_RE.fullmatch((slot_key or "").strip()))


def is_forbidden_aligner_pick_destination_slot(slot_key: str) -> bool:
    return bool(_BUFFER_OR_FOUP_SLOT_RE.fullmatch((slot_key or "").strip()))


def is_airlock_place_event(event_name: str) -> bool:
    return bool(_AIRLOCK_PLACE_EVENT_RE.fullmatch((event_name or "").strip()))


def is_forbidden_after_aligner_pick_event(event_name: str) -> bool:
    return bool(_FORBIDDEN_AFTER_ALIGNER_PICK_RE.fullmatch((event_name or "").strip()))


def find_first_airlock_place_time_in_tour(
    tour: Sequence[Any],
) -> Optional[float]:
    """투어에서 ``LOGICAL:ATM_ARM → airlock*`` 홉의 airlock ``start_sec``.

    없으면 None (Aligner pick 보류).
    """
    from .lam_wafer_prim_paths import LOGICAL_SLOT_ATM_ARM

    if not tour or len(tour) < 2:
        return None
    for i in range(len(tour) - 1):
        prev_sk = str(getattr(tour[i], "slot_key", "") or "")
        curr_sk = str(getattr(tour[i + 1], "slot_key", "") or "")
        if prev_sk == LOGICAL_SLOT_ATM_ARM and is_airlock_slot_key(curr_sk):
            return float(getattr(tour[i + 1], "start_sec", 0.0) or 0.0)
    return None


def resolve_aligner_pick_schedule(
    *,
    foup_pick_t: float,
    aligner_place_t: float,
    tour: Sequence[Any],
    other_atm_action_times: Sequence[float],
    pick_lead_before_airlock_sec: float,
) -> Tuple[Optional[float], bool, Optional[float]]:
    """Aligner pick 시각.

    Returns:
        (pick_t|None, deferred, airlock_place_t)
        None → airlock 홉 없음, place 만 두고 pick 미삽입.
    """
    _ = foup_pick_t
    airlock_t = find_first_airlock_place_time_in_tour(tour)
    if airlock_t is None:
        return None, True, None

    place_t = float(aligner_place_t)
    next_t = float(airlock_t)
    if next_t <= place_t + 1e-6:
        return place_t + 0.05, False, next_t

    iv = [float(x) for x in other_atm_action_times if place_t < float(x) < next_t]
    lead = max(0.05, float(pick_lead_before_airlock_sec))
    if not iv:
        pick_t = max(place_t + 0.05, next_t - lead)
        return float(pick_t), False, next_t

    last_iv = max(iv)
    pick_t = max(place_t + 0.05, last_iv + 0.05, next_t - lead)
    if pick_t >= next_t - 1e-4:
        pick_t = max(place_t + 0.05, next_t - 0.05)
    return float(pick_t), True, next_t


__all__ = [
    "find_first_airlock_place_time_in_tour",
    "is_airlock_place_event",
    "is_airlock_slot_key",
    "is_forbidden_after_aligner_pick_event",
    "is_forbidden_aligner_pick_destination_slot",
    "resolve_aligner_pick_schedule",
]
