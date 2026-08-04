"""Buffer pick 후 FOUP 반환 절대 규칙 (실무 데이터 순서 유지).

실무·코드 공통 SSOT. Aligner 규칙 적용·JSON 순서 재정립 **이후**에만 검사한다.
그 외 ATM/VTM 점유·순서 보정과 무관하다.

규칙 요약
---------
1. Buffer pick 이후 파싱/스케줄에서 **같은 wafer** 의 ``atm_foup*_place`` 를 검색.
2. 있으면 — 데이터 그대로 실행 (FOUP 상태보기 완료 +1 이 정상).
   단, Buffer pick ~ FOUP place **사이** 같은 wafer 의 비-FOUP place 는
   데이터/파싱 오류로 보고 plan 에서 제거(사이드버그 방지).
3. 없으면 — (실무상 비정상) Buffer pick **직후** 에 같은 wafer 의 FOUP place JSON 을
   강제 삽입·실행한 뒤, 나머지 동작을 이어간다.
   동시에 Buffer pick 이후 같은 wafer 의 비-FOUP place 도 제거한다.
4. 정상 데이터에서는 2의 제거·3의 삽입이 발생하지 않아야 한다.
"""

from __future__ import annotations

import re
from typing import Optional

_BUFFER_PICK_EVENT_RE = re.compile(r"^atm_buffer\d+_pick$", re.IGNORECASE)
_FOUP_PLACE_EVENT_RE = re.compile(r"^atm_foup([1-3])_place$", re.IGNORECASE)
_FOUP_PICK_EVENT_RE = re.compile(r"^atm_foup([1-3])_pick$", re.IGNORECASE)
_BUFFER_SLOT_RE = re.compile(r"^buffer[34]_(\d+)$", re.IGNORECASE)
_FOUP_SLOT_RE = re.compile(r"^foup([1-3])_(\d+)$", re.IGNORECASE)
# Buffer 반환 구간에서 금지되는 place (Aligner·FOUP 제외)
_FORBIDDEN_PLACE_AFTER_BUFFER_RE = re.compile(
    r"^atm_(?:buffer\d+|cooling|airlock\d+)_place$",
    re.IGNORECASE,
)
_ANY_NON_FOUP_ATM_PLACE_RE = re.compile(
    r"^atm_(?!foup\d+_)(?!aligner_)[a-z0-9_]+_place$",
    re.IGNORECASE,
)

# Buffer pick 직후 강제 FOUP place 삽입 gap (초). 데이터 시간축을 크게 밀지 않음.
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
    """Buffer pick 이후 ~ FOUP place 사이 금지 place (동일 wafer)."""
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
