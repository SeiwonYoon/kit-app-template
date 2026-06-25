"""
JSON 시퀀스 ``renewal`` 마커 — 포트·막대 갱신 시점 (재생 길이·배속 무영향).

형식 (최소):
  {"renewal": true}

선택 필드:
  {"renewal": true, "description": "FOUP 안착 시점"}

정책:
  - 파일당 renewal 은 **1개** 권장. 여러 개면 **첫 번째만** 갱신 트리거(예외).
  - renewal 이 있으면 JSON 종료 시 포트 갱신 생략, renewal 시점에만 갱신.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

RENEWAL_STEP_TYPE = "RENEWAL"

_TRUTHY = frozenset({True, "true", "True", "1", 1})


def is_renewal_marker(step: Any) -> bool:
    if not isinstance(step, dict):
        return False
    if step.get("renewal") in _TRUTHY:
        return True
    return str(step.get("type") or "").strip().upper() == RENEWAL_STEP_TYPE


def normalize_renewal_step(raw: Dict[str, Any]) -> Dict[str, Any]:
    desc = str(raw.get("description") or "").strip()
    if not desc:
        desc = "renewal: port/bar state sync"
    return {"renewal": True, "type": RENEWAL_STEP_TYPE, "description": desc}


def find_first_renewal_index(steps: List[Any]) -> Optional[int]:
    for i, st in enumerate(steps or []):
        if is_renewal_marker(st):
            return int(i)
    return None


def renewal_step_indices(steps: List[Any]) -> List[int]:
    return [i for i, st in enumerate(steps or []) if is_renewal_marker(st)]


__all__ = [
    "RENEWAL_STEP_TYPE",
    "find_first_renewal_index",
    "is_renewal_marker",
    "normalize_renewal_step",
    "renewal_step_indices",
]
