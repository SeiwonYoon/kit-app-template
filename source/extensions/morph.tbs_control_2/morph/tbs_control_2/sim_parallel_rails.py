"""SIM_PARALLEL_NONCONFLICTING_MOVES=True 용 2레일 분류·EP 충돌·규칙 SSOT.

===========================================================================
병렬 엔진 규칙 (simulation_engine wave SSOT — 여기와 불일치하면 엔진을 고친다)
===========================================================================
레일
  A ``oht``  : ARRIVED / REMOVED — A끼리 직렬
  B ``move`` : MOVE_*              — B끼리 직렬
  A∥B        : 허용. JSON/포트 끝 EPn 이 같으면 금지.

B 우선순위 (기동 가능할 때만)
  1) 빈 EP + BP LOT + A와 동일 EP 아님 → BP→EP
  2) INOUT FULL + 빈 BP                 → INOUT→BP
  · REMOVED 중 "곧 빌 EP"만으로는 INOUT→BP 를 막지 않음
    (그 EP로는 BP→EP 가 충돌로 불가 → B 공회전 = 규칙 위반).

Wave 기동 순서 (같은 틱)
  REMOVED → B(MOVE) → OHT ARRIVED
  · 버퍼가 채울 빈 EP: OHT→EP 직접투입 보류.

회수 티켓 (pickup)
  · 간격 타이머가 티켓을 쌓음 — FOUP 종료만으로 즉시 티켓을 주지 않음
    (첫 READYTOUNLOAD/REMOVED 는 간격 정책 유지).
  · 이미 awaiting EP 가 남아 있는데 A레일 REMOVED 가 막 끝났고 티켓=0 이면
    chain 티켓 1장 — 연속 REMOVED 사이 간격 타이머 공백 금지.
  · A/B 레일 free 시 wave 를 한곳(``_parallel_schedule_wave``)에서만 재평가.

직렬(False)
  · 위 nofollow/wave 미사용. 기존 yield 직렬 유지.
===========================================================================
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

_EP_SUFFIX_RE = re.compile(r"(EP[1-9]\d*)$", re.IGNORECASE)


def parallel_moves_enabled() -> bool:
    try:
        from .sim_control_defaults import SIM_PARALLEL_NONCONFLICTING_MOVES

        return bool(SIM_PARALLEL_NONCONFLICTING_MOVES)
    except Exception:
        return False


def classify_sim_rail(seq: str) -> Optional[str]:
    """이벤트 seq → ``oht`` | ``move`` | None(병렬 게이트 대상 아님)."""
    s = str(seq or "").strip().upper()
    if not s:
        return None
    if s in (
        "PORT_OCC_REFRESH",
        "FOUP_PROCESS",
        "FOUP_PROCESS_START",
        "FOUP_PROCESS_END",
        "READYTOLOAD",
        "READYTOUNLOAD",
    ):
        return None
    if "REMOVED" in s:
        return "oht"
    if "ARRIVED" in s:
        return "oht"
    if "MOVE" in s:
        return "move"
    return None


def ep_token_from_text(text: str) -> str:
    """문자열 끝의 EPn 토큰 (대문자). 없으면 \"\"."""
    m = _EP_SUFFIX_RE.search(str(text or "").strip().upper().replace(" ", ""))
    return str(m.group(1)).upper() if m else ""


def ep_target_from_payload(payload: Optional[Dict[str, Any]]) -> str:
    """payload 의 to_port_id / port_id / linked_anim_json / json 경로에서 EPn.

    INOUT/BP 간 MOVE 는 파일명에 EPn 이 있어도 **포트 필드가 EP 가 아니면**
    EP 충돌로 보지 않는다 (basename 오탐으로 REMOVED∥MOVE 차단 방지).
    """
    p = payload if isinstance(payload, dict) else {}
    for key in ("to_port_id", "port_id", "to", "port"):
        tok = ep_token_from_text(str(p.get(key) or ""))
        if tok:
            return tok
    fr = str(p.get("from_port_id") or p.get("from") or "").strip().upper()
    to = str(p.get("to_port_id") or p.get("to") or "").strip().upper()
    # from/to 모두 비-EP (INOUT/BP*) 이면 basename EP 추정 금지
    if fr or to:
        fr_ep = ep_token_from_text(fr)
        to_ep = ep_token_from_text(to)
        if not fr_ep and not to_ep:
            return ""
        if to_ep:
            return to_ep
        if fr_ep and to and to.startswith("EP"):
            return fr_ep
    for key in ("linked_anim_json", "json", "file", "json_path"):
        raw = str(p.get(key) or "").replace("\\", "/").split("/")[-1]
        name = raw.rsplit(".", 1)[0]
        tok = ep_token_from_text(name)
        if tok:
            return tok
    if fr and to:
        tok = ep_token_from_text(f"{fr}->{to}")
        if tok:
            return tok
    return ""


def ep_targets_conflict(ep_a: str, ep_b: str) -> bool:
    a = str(ep_a or "").strip().upper()
    b = str(ep_b or "").strip().upper()
    if not a or not b:
        return False
    return a == b


def rail_queue_key(screen: int, rail: str) -> str:
    scr = max(1, int(screen))
    r = str(rail or "").strip().lower() or "oht"
    return f"{scr}:{r}"


def anim_state_key(screen: int, rail: Optional[str] = None) -> str:
    """pending/active dict 키. 병렬+레일 → ``scr:rail``, 아니면 ``scr``."""
    scr = max(1, int(screen))
    r = str(rail or "").strip().lower()
    if parallel_moves_enabled() and r in ("oht", "move"):
        return rail_queue_key(scr, r)
    return str(scr)


def screen_from_state_key(key: str) -> int:
    """``1`` / ``1:oht`` → 화면 번호."""
    raw = str(key or "").strip()
    if not raw:
        return 1
    head = raw.split(":", 1)[0]
    try:
        return max(1, int(head))
    except Exception:
        return 1


def rail_from_state_key(key: str) -> Optional[str]:
    """``1:oht`` → ``oht``. 레일 없으면 None."""
    raw = str(key or "").strip()
    if ":" not in raw:
        return None
    r = raw.split(":", 1)[1].strip().lower()
    return r if r in ("oht", "move") else None


def twin_rail(rail: str) -> Optional[str]:
    r = str(rail or "").strip().lower()
    if r == "oht":
        return "move"
    if r == "move":
        return "oht"
    return None


def rail_from_job_or_payload(job: Optional[Dict[str, Any]]) -> str:
    j = job if isinstance(job, dict) else {}
    tagged = str(j.get("sim_rail") or "").strip().lower()
    if tagged in ("oht", "move"):
        return tagged
    seq = str(j.get("event") or j.get("event_seq") or j.get("seq") or "")
    c = classify_sim_rail(seq)
    return c or "oht"


__all__ = [
    "anim_state_key",
    "classify_sim_rail",
    "ep_target_from_payload",
    "ep_targets_conflict",
    "ep_token_from_text",
    "parallel_moves_enabled",
    "rail_from_job_or_payload",
    "rail_from_state_key",
    "rail_queue_key",
    "screen_from_state_key",
    "twin_rail",
]
