"""LAM pick/place **공개 함수 46개** — 이름 = JSON 파일명 = ``lam_sim_actions`` 함수명.

**호출 예:** ``atm_foup1_pick(7)`` / ``vtm_chamber1_right_pick()``

**내부 흐름 (모든 함수 동일)**
1. ``_action(event_name, slot_number=…)``
2. ``lam_event_sequences.build_steps_for_event()`` — JSON + 자동 Z
3. 호출 측(``simulation_play``)에서 ``run_lam_sim_steps()`` 로 재생

**수정 위치**
- 동작·애니메이션: ``lam/lam_event_sequences/<함수명>.json``
- Z·prim: ``lam_slot_z_config.py`` / ``simulation_play`` (이 파일은 이름만 등록)

**등록:** ``simulation_play.LAM_SIM_MACRO_CALLABLES`` ← ``LAM_SIM_ACTION_CALLABLES``
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .lam_event_sequences import LAM_EVENT_NAMES, build_steps_for_event, ensure_event_json_scaffolds

LamSimJsonSteps = List[Dict[str, Any]]


def _action(event_name: str, *, slot_number: Optional[int] = None) -> LamSimJsonSteps:
    """이벤트명 → 스텝 list (재생은 호출자가 ``run_lam_sim_steps`` 수행)."""
    return build_steps_for_event(event_name, slot_number=slot_number)


# ---------------------------------------------------------------------------
# VTM chamber — slot_number 없음 (slot_key = chamber1 … chamber5)
# ---------------------------------------------------------------------------

def vtm_chamber1_right_pick() -> LamSimJsonSteps:
    return _action("vtm_chamber1_right_pick")


def vtm_chamber1_right_place() -> LamSimJsonSteps:
    return _action("vtm_chamber1_right_place")


def vtm_chamber2_right_pick() -> LamSimJsonSteps:
    return _action("vtm_chamber2_right_pick")


def vtm_chamber2_right_place() -> LamSimJsonSteps:
    return _action("vtm_chamber2_right_place")


def vtm_chamber3_right_pick() -> LamSimJsonSteps:
    return _action("vtm_chamber3_right_pick")


def vtm_chamber3_right_place() -> LamSimJsonSteps:
    return _action("vtm_chamber3_right_place")


def vtm_chamber4_right_pick() -> LamSimJsonSteps:
    return _action("vtm_chamber4_right_pick")


def vtm_chamber4_right_place() -> LamSimJsonSteps:
    return _action("vtm_chamber4_right_place")


def vtm_chamber5_right_pick() -> LamSimJsonSteps:
    return _action("vtm_chamber5_right_pick")


def vtm_chamber5_right_place() -> LamSimJsonSteps:
    return _action("vtm_chamber5_right_place")


def vtm_chamber1_left_pick() -> LamSimJsonSteps:
    return _action("vtm_chamber1_left_pick")


def vtm_chamber1_left_place() -> LamSimJsonSteps:
    return _action("vtm_chamber1_left_place")


def vtm_chamber2_left_pick() -> LamSimJsonSteps:
    return _action("vtm_chamber2_left_pick")


def vtm_chamber2_left_place() -> LamSimJsonSteps:
    return _action("vtm_chamber2_left_place")


def vtm_chamber3_left_pick() -> LamSimJsonSteps:
    return _action("vtm_chamber3_left_pick")


def vtm_chamber3_left_place() -> LamSimJsonSteps:
    return _action("vtm_chamber3_left_place")


def vtm_chamber4_left_pick() -> LamSimJsonSteps:
    return _action("vtm_chamber4_left_pick")


def vtm_chamber4_left_place() -> LamSimJsonSteps:
    return _action("vtm_chamber4_left_place")


def vtm_chamber5_left_pick() -> LamSimJsonSteps:
    return _action("vtm_chamber5_left_pick")


def vtm_chamber5_left_place() -> LamSimJsonSteps:
    return _action("vtm_chamber5_left_place")


# --- VTM airlock (slot 1..2) ---

def vtm_airlock1_right_pick(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("vtm_airlock1_right_pick", slot_number=slot_number)


def vtm_airlock1_right_place(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("vtm_airlock1_right_place", slot_number=slot_number)


def vtm_airlock2_right_pick(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("vtm_airlock2_right_pick", slot_number=slot_number)


def vtm_airlock2_right_place(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("vtm_airlock2_right_place", slot_number=slot_number)


def vtm_airlock1_left_pick(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("vtm_airlock1_left_pick", slot_number=slot_number)


def vtm_airlock1_left_place(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("vtm_airlock1_left_place", slot_number=slot_number)


def vtm_airlock2_left_pick(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("vtm_airlock2_left_pick", slot_number=slot_number)


def vtm_airlock2_left_place(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("vtm_airlock2_left_place", slot_number=slot_number)


# --- ATM FOUP / buffer / coolstation / airlock / aligner ---

def atm_foup1_pick(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_foup1_pick", slot_number=slot_number)


def atm_foup1_place(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_foup1_place", slot_number=slot_number)


def atm_foup2_pick(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_foup2_pick", slot_number=slot_number)


def atm_foup2_place(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_foup2_place", slot_number=slot_number)


def atm_foup3_pick(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_foup3_pick", slot_number=slot_number)


def atm_foup3_place(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_foup3_place", slot_number=slot_number)


def atm_buffer3_pick(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_buffer3_pick", slot_number=slot_number)


def atm_buffer3_place(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_buffer3_place", slot_number=slot_number)


def atm_buffer4_pick(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_buffer4_pick", slot_number=slot_number)


def atm_buffer4_place(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_buffer4_place", slot_number=slot_number)


def atm_coolstation_pick(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_coolstation_pick", slot_number=slot_number)


def atm_coolstation_place(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_coolstation_place", slot_number=slot_number)


def atm_airlock1_pick(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_airlock1_pick", slot_number=slot_number)


def atm_airlock1_place(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_airlock1_place", slot_number=slot_number)


def atm_airlock2_pick(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_airlock2_pick", slot_number=slot_number)


def atm_airlock2_place(slot_number: int = 1) -> LamSimJsonSteps:
    return _action("atm_airlock2_place", slot_number=slot_number)


def atm_aligner_pick() -> LamSimJsonSteps:
    return _action("atm_aligner_pick")


def atm_aligner_place() -> LamSimJsonSteps:
    return _action("atm_aligner_place")


LAM_SIM_ACTION_CALLABLES: Dict[str, Any] = {
    name: globals()[name]
    for name in LAM_EVENT_NAMES
    if name in globals() and callable(globals()[name])
}

# 스크립트: ``atm_foup1_pick(7)`` 또는 ``atm_foup1_pick(slot_number=7)``, ``duration_sec=`` 선택.
LAM_SIM_MACRO_CALLABLES: Dict[str, Any] = dict(LAM_SIM_ACTION_CALLABLES)


__all__ = [
    "LAM_SIM_ACTION_CALLABLES",
    "LAM_SIM_MACRO_CALLABLES",
    "ensure_event_json_scaffolds",
] + list(LAM_EVENT_NAMES)
