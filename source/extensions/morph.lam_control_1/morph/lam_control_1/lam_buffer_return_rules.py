"""Buffer→FOUP 헬퍼 (레거시).

**SSOT (2026-08):** wafer 투어 마지막 dwell 이 AtmArm 이면 FOUP place 합성
(``simulation_play.build_csv_playback_plan``). Buffer pick 전용 강제 삽입·
사이 비-FOUP place 제거는 폐지되었다.

아래 이벤트명/슬롯 판별 헬퍼만 다른 모듈에서 재사용할 수 있다.
"""

from __future__ import annotations

import re
from typing import Optional

_BUFFER_PICK_EVENT_RE = re.compile(r"^atm_buffer\d+_pick$", re.IGNORECASE)
_FOUP_PLACE_EVENT_RE = re.compile(r"^atm_foup([1-3])_place$", re.IGNORECASE)
_FOUP_PICK_EVENT_RE = re.compile(r"^atm_foup([1-3])_pick$", re.IGNORECASE)
_BUFFER_SLOT_RE = re.compile(r"^buffer[34]_(\d+)$", re.IGNORECASE)
_FOUP_SLOT_RE = re.compile(r"^foup([1-3])_(\d+)$", re.IGNORECASE)
# Buffer 반환 구간에서 금지되는 place (Aligner·FOUP 제외) — 레거시 헬퍼 유지
_FORBIDDEN_PLACE_AFTER_BUFFER_RE = re.compile(
    r"^atm_(?:buffer\d+|cooling|airlock\d+)_place$",
    re.IGNORECASE,
)
_ANY_NON_FOUP_ATM_PLACE_RE = re.compile(
    r"^atm_(?!foup\d+_)(?!aligner_)[a-z0-9_]+_place$",
    re.IGNORECASE,
)

# 레거시 상수 (더 이상 plan 삽입에 사용하지 않음)
BUFFER_PICK_SYNTH_FOUP_PLACE_GAP_SEC: float = 0.05


def is_buffer_pick_event(event_name: str) -> bool:
    return bool(_BUFFER_PICK_EVENT_RE.fullmatch((event_name or "").strip()))


def is_foup_place_event(event_name: str) -> bool:
    return bool(_FOUP_PLACE_EVENT_RE.fullmatch((event_name or "").strip()))


def is_foup_pick_event(event_name: str) -> bool:
    return bool(_FOUP_PICK_EVENT_RE.fullmatch((event_name or "").strip()))


def foup_index_from_place_event(event_name: str) -> Optional[int]:
    m = _FOUP_PLACE_EVENT_RE.fullmatch((event_name or "").strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def is_buffer_slot_key(slot_key: str) -> bool:
    return bool(_BUFFER_SLOT_RE.fullmatch((slot_key or "").strip()))


def is_foup_slot_key(slot_key: str) -> bool:
    return bool(_FOUP_SLOT_RE.fullmatch((slot_key or "").strip()))


def foup_index_from_slot_key(slot_key: str) -> Optional[int]:
    m = _FOUP_SLOT_RE.fullmatch((slot_key or "").strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def is_forbidden_place_after_buffer_pick(event_name: str) -> bool:
    """레거시 헬퍼 — Buffer pick 이후 ~ FOUP place 사이 금지 place 판별."""
    en = (event_name or "").strip()
    if not en:
        return False
    if is_foup_place_event(en):
        return False
    if en.lower().startswith("atm_aligner_"):
        return False
    if _FORBIDDEN_PLACE_AFTER_BUFFER_RE.fullmatch(en):
        return True
    return bool(_ANY_NON_FOUP_ATM_PLACE_RE.fullmatch(en))


__all__ = [
    "BUFFER_PICK_SYNTH_FOUP_PLACE_GAP_SEC",
    "foup_index_from_place_event",
    "foup_index_from_slot_key",
    "is_buffer_pick_event",
    "is_buffer_slot_key",
    "is_forbidden_place_after_buffer_pick",
    "is_foup_pick_event",
    "is_foup_place_event",
    "is_foup_slot_key",
]
