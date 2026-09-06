"""
sim_sequences JSON → 1배속 예상 재생 길이 (omni 없이 프리런 SSOT용).

``control_window._estimate_sequence_total_duration_sec_for_log`` 와 동일 규칙을
가볍게 복제한다 (MOVE/ROTATE/DELAY/visibility/USD_TIMELINE 프레임).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .sim_sequence_json import resolve_sim_sequence_json_path


def _group_end_index(steps: List[dict], a: int) -> int:
    g_end = a
    while g_end + 1 < len(steps) and bool((steps[g_end + 1] or {}).get("run_with_previous", False)):
        g_end += 1
    return g_end


def _step_duration_sec(step: Dict[str, Any]) -> float:
    try:
        from .sequence_renewal import is_renewal_marker

        if is_renewal_marker(step or {}):
            return 0.0
    except Exception:
        pass
    try:
        t = str((step or {}).get("type") or "").upper()
    except Exception:
        return 0.0
    try:
        if t in ("MOVE", "ROTATE"):
            if "duration_max" in (step or {}):
                return max(0.0, float((step or {}).get("duration_max", (step or {}).get("duration", 0.0))))
            return max(0.0, float((step or {}).get("duration", 0.0)))
        if t == "DELAY":
            return max(0.0, float((step or {}).get("duration", 0.0)))
        if t in ("PRIM_VISIBILITY", "SET_PRIM_VISIBILITY", "PRIM_HIDE", "PRIM_SHOW"):
            return max(0.0, float((step or {}).get("duration", 0.02) or 0.02))
        if t in ("USD_TIMELINE", "TIMESAMPLES_REPLAY"):
            play = (step or {}).get("play") or {}
            if not isinstance(play, dict):
                play = {}

            def _frame_val(key: str, default: int = 0) -> int:
                if key in play and play[key] is not None:
                    return int(play[key])
                if key in (step or {}) and (step or {}).get(key) is not None:
                    return int((step or {}).get(key))
                return default

            start = _frame_val("start_frame", 0)
            end = _frame_val("end_frame", 0)
            if end <= start:
                return 0.0
            try:
                if "speed_scale" in play and play["speed_scale"] is not None:
                    step_sp = float(play["speed_scale"])
                else:
                    step_sp = float((step or {}).get("speed_scale", 1.0))
            except Exception:
                step_sp = 1.0
            step_sp = max(0.01, float(step_sp))
            # 30fps 환산 (usd_animation_control.frame_to_time 와 동일 취지)
            base = float(end - start) / 30.0
            return max(0.0, base / float(step_sp))
    except Exception:
        return 0.0
    return 0.0


def estimate_sequence_duration_sec(steps: List[Any]) -> float:
    """시퀀스 steps 의 1배속 예상 총 길이(초)."""
    if not steps:
        return 0.0
    typed: List[dict] = [s if isinstance(s, dict) else {} for s in steps]
    try:
        t_cursor = max(0.0, int((typed[0] or {}).get("step_delay_ms", 0)) / 1000.0)
    except Exception:
        t_cursor = 0.0
    last_finish = t_cursor
    i = 0
    while i < len(typed):
        g_end = _group_end_index(typed, i)
        t0 = t_cursor
        group_finish = t0
        for j in range(i, g_end + 1):
            st = typed[j]
            off = 0.0
            if j != i:
                try:
                    off = max(0.0, int((st or {}).get("step_delay_ms", 0)) / 1000.0)
                except Exception:
                    off = 0.0
            group_finish = max(group_finish, t0 + off + _step_duration_sec(st))
        last_finish = max(last_finish, group_finish)
        next_idx = g_end + 1
        if next_idx >= len(typed):
            break
        anchor_step = typed[g_end]
        anchor_off = 0.0
        if g_end > i:
            try:
                anchor_off = max(0.0, int((anchor_step or {}).get("step_delay_ms", 0)) / 1000.0)
            except Exception:
                anchor_off = 0.0
        anchor_end = t0 + anchor_off + _step_duration_sec(anchor_step)
        try:
            delay_next = int((typed[next_idx] or {}).get("step_delay_ms", 0)) / 1000.0
        except Exception:
            delay_next = 0.0
        t_cursor = max(t0, anchor_end + float(delay_next))
        i = next_idx
    return max(0.0, float(last_finish))


def estimate_json_file_duration_sec(path_text: str) -> float:
    """``data/sim_sequences/*.json`` 경로/파일명 → 1배속 초."""
    jp = resolve_sim_sequence_json_path(str(path_text or "").strip())
    if jp is None or not jp.is_file():
        p = Path(str(path_text or "").strip())
        if p.is_file():
            jp = p
        else:
            return 0.0
    try:
        parsed = json.loads(jp.read_text(encoding="utf-8"))
    except Exception:
        return 0.0
    if not isinstance(parsed, list):
        return 0.0
    return float(estimate_sequence_duration_sec(parsed))


# 엔진/EVENT_JSON_CASE_MAP 과 동일 basename 규칙 (확장 루트 resolve)
def linked_json_for_process(
    *,
    kind: str,
    from_port: str,
    to_port: str,
    port: str,
) -> str:
    """공정 kind → sim_sequences basename (없으면 \"\")."""
    fr = str(from_port or "").strip().upper()
    to = str(to_port or "").strip().upper()
    po = str(port or "").strip().upper()
    k = str(kind or "").strip().upper()
    if k == "OHT_TO_EP":
        ep = to or po
        return f"arrived_{ep.lower()}.json" if ep else ""
    if k == "OHT_TO_INOUT":
        return "arrived_inout.json"
    if k == "INOUT_TO_BP":
        bp = to or po
        return f"move_inout_{bp.lower()}.json" if bp else ""
    if k == "BP_TO_EP":
        if fr and to:
            return f"move_{fr.lower()}_{to.lower()}.json"
        return ""
    if k == "REMOVE":
        ep = fr or po
        return f"removed_{ep.lower()}.json" if ep else ""
    return ""


def resolve_process_anim_sec(
    *,
    kind: str,
    from_port: str,
    to_port: str,
    port: str,
    fallback_sec: float = 0.0,
) -> tuple[float, str]:
    """
    공정별 JSON 길이를 프리런에 반영.

    Returns:
        (anim_sec, linked_basename) — 파일 없거나 0이면 fallback_sec.
    """
    bn = linked_json_for_process(kind=kind, from_port=from_port, to_port=to_port, port=port)
    if not bn:
        return max(0.0, float(fallback_sec)), ""
    est = estimate_json_file_duration_sec(bn)
    if est <= 1e-9:
        est = max(0.0, float(fallback_sec))
    return float(est), bn


__all__ = [
    "estimate_sequence_duration_sec",
    "estimate_json_file_duration_sec",
    "linked_json_for_process",
    "resolve_process_anim_sec",
]
