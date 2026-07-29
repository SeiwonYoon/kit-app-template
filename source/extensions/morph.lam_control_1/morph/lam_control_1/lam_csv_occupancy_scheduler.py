"""CSV Play 점유 시뮬·시각 재조정·합성 aligner 예외 (후처리 전용).

``CSV_PLAYBACK_OCCUPANCY_SCHEDULER_ENABLED`` 가 False 이면 호출하지 않는다.
호출부에서 플래그로 가드하므로, 이 모듈은 True 경로 전용이다.
기존 ``build_csv_playback_plan`` 본체는 수정하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

_PRINT_PREFIX = "[LAM/occ-sched]"

_VIS_TYPES = frozenset(
    {"PRIM_VISIBILITY", "SET_PRIM_VISIBILITY", "PRIM_HIDE", "PRIM_SHOW"}
)
_ALIGNER_CATS = frozenset({"aligner_place", "aligner_pick"})


@dataclass(frozen=True)
class OccupancyDiagLine:
    """재생창 진단 패널 한 줄."""

    t_sec: float
    kind: str
    message: str

    def as_text(self) -> str:
        return f"t={self.t_sec:.1f}s [{self.kind}] {self.message}"


def occupancy_scheduler_enabled() -> bool:
    try:
        from .lam_sim_control_defaults import CSV_PLAYBACK_OCCUPANCY_SCHEDULER_ENABLED

        return bool(CSV_PLAYBACK_OCCUPANCY_SCHEDULER_ENABLED)
    except Exception:
        return False


def apply_occupancy_scheduler(
    dwells: Sequence[Any],
    schedule: List[Any],
    blocks: List[Any],
) -> Tuple[List[Any], List[Any], Tuple[OccupancyDiagLine, ...]]:
    """plan 후처리 — schedule/blocks 재정렬·필터 + 진단 목록.

    Returns:
        (new_schedule, new_blocks, diagnostics)
    """
    diags: List[OccupancyDiagLine] = []
    if not blocks:
        return list(schedule or []), list(blocks or []), tuple()

    # 1) 합성 aligner — CSV ATM 동작과 시간 겹치면 삭제
    blocks2, schedule2, d1 = _drop_conflicting_synth_aligners(schedule, blocks)
    diags.extend(d1)

    # 2) visibility 오프셋만큼 블록 시작 시각 앞당김
    blocks3, schedule3, d2 = _shift_blocks_by_visibility_offset(blocks2, schedule2)
    diags.extend(d2)

    # 3) 점유 dry-run — 위반 후보만 진단 (재생 목록은 유지, 시각·삭제만 반영)
    d3 = _occupancy_dry_run_diagnostics(dwells, blocks3)
    diags.extend(d3)

    # 최종 정렬 + row id
    schedule3.sort(key=lambda e: (float(e.time_sec), int(e.sort_order)))
    blocks3.sort(key=lambda b: (float(b.time_sec), int(b.sort_order)))
    try:
        from .simulation_play import _reattach_block_schedules, _stamp_schedule_row_ids

        schedule3 = _stamp_schedule_row_ids(schedule3)
        blocks3 = _reattach_block_schedules(blocks3, schedule3)
    except Exception:
        pass

    if diags:
        print(
            f"{_PRINT_PREFIX} 후처리 진단 {len(diags)}건 "
            f"(aligner 삭제/시각이동/점유경고)",
            flush=True,
        )
    return schedule3, blocks3, tuple(diags)


def _step_duration_sec(st: Dict[str, Any]) -> float:
    t = str(st.get("type") or "").upper()
    if t in (
        "MOVE",
        "ROTATE",
        "DELAY",
        "PRIM_VISIBILITY",
        "SET_PRIM_VISIBILITY",
        "PRIM_HIDE",
        "PRIM_SHOW",
    ):
        return max(0.0, float(st.get("duration", 0.0) or 0.0))
    if t in ("TIMESAMPLES_REPLAY", "USD_TIMELINE"):
        try:
            from .lam_types import LAM_FIXED_FPS

            tps = float(LAM_FIXED_FPS)
        except Exception:
            tps = 30.0
        sf = float(st.get("start_frame", 0) or 0)
        ef = float(st.get("end_frame", 0) or 0)
        sp = float(st.get("speed_scale", 1.0) or 1.0)
        if ef > sf:
            return (ef - sf) / tps / max(0.01, sp)
    return 0.0


def _is_occupancy_visibility_step(st: Dict[str, Any]) -> bool:
    if str(st.get("type") or "").upper() not in _VIS_TYPES:
        return False
    ctx = st.get("_lam_wafer_label_ctx")
    if isinstance(ctx, dict):
        po = str(ctx.get("pick_or_place") or "").strip().lower()
        if po in ("pick", "place"):
            return True
        if (ctx.get("slot_wafer_path") or ctx.get("arm_wafer_path")):
            return True
    # ctx 없어도 wafer prim 교차 show/hide 로 보이는 PRIM_VISIBILITY 는 앵커 후보
    return True


def visibility_offset_until_occupancy_sec(steps: Sequence[Dict[str, Any]]) -> float:
    """블록 시작 → 점유 변경 visibility 직전까지 누적 초 (동시 스텝 고려)."""
    if not steps:
        return 0.0
    elapsed = 0.0
    i = 0
    n = len(steps)
    while i < n:
        st = steps[i] if isinstance(steps[i], dict) else {}
        if _is_occupancy_visibility_step(st):
            return float(elapsed)
        dur = _step_duration_sec(st)
        group_end = elapsed + dur
        j = i + 1
        while j < n:
            stj = steps[j] if isinstance(steps[j], dict) else {}
            if not bool(stj.get("run_with_previous")):
                break
            if _is_occupancy_visibility_step(stj):
                return float(elapsed)
            group_end = max(group_end, elapsed + _step_duration_sec(stj))
            j += 1
        elapsed = group_end
        i = j if j > i + 1 else i + 1
    return 0.0


def _event_is_atm(ent: Any) -> bool:
    en = str(getattr(ent, "event_name", "") or "").strip().lower()
    if en.startswith("atm_"):
        return True
    cat = str(getattr(ent, "category", "") or "").strip().lower()
    if cat in ("pick", "place", "aligner_place", "aligner_pick"):
        return True
    if cat == "transfer" and "atm" in str(getattr(ent, "title_ko", "") or "").lower():
        return True
    return False


def _drop_conflicting_synth_aligners(
    schedule: List[Any],
    blocks: List[Any],
) -> Tuple[List[Any], List[Any], List[OccupancyDiagLine]]:
    """FOUP pick 후 합성 aligner 구간에 다른 ATM 동작이 있으면 aligner 제거."""
    diags: List[OccupancyDiagLine] = []
    try:
        from .simulation_play import (
            FOUP_PICK_SYNTH_ALIGNER_PICK_DELAY_SEC,
            FOUP_PICK_SYNTH_ALIGNER_PLACE_DELAY_SEC,
        )

        place_d = float(FOUP_PICK_SYNTH_ALIGNER_PLACE_DELAY_SEC)
        pick_d = float(FOUP_PICK_SYNTH_ALIGNER_PICK_DELAY_SEC)
    except Exception:
        place_d, pick_d = 2.5, 6.0

    # pick 시각 → 해당 투어 aligner 제거 여부
    drop_aligner_anchors: set = set()
    picks: List[Tuple[float, Any]] = []
    for e in schedule or []:
        if str(getattr(e, "category", "") or "") != "pick":
            continue
        picks.append((float(getattr(e, "time_sec", 0.0) or 0.0), e))

    for anchor_t, pick_e in picks:
        win_lo = anchor_t + 1e-6
        win_hi = anchor_t + pick_d + 1e-6
        conflict = None
        for e in schedule or []:
            cat = str(getattr(e, "category", "") or "")
            if cat in _ALIGNER_CATS or cat == "dwell" or cat == "pick":
                continue
            if not _event_is_atm(e):
                continue
            t = float(getattr(e, "time_sec", 0.0) or 0.0)
            if win_lo < t <= win_hi:
                conflict = e
                break
        if conflict is not None:
            drop_aligner_anchors.add(round(anchor_t, 6))
            diags.append(
                OccupancyDiagLine(
                    t_sec=float(anchor_t),
                    kind="aligner_skip",
                    message=(
                        f"합성 aligner 생략 — ATM 동작과 겹침 "
                        f"(t={float(getattr(conflict, 'time_sec', 0) or 0):.1f}s "
                        f"{getattr(conflict, 'event_name', '') or getattr(conflict, 'category', '')})"
                    ),
                )
            )

    if not drop_aligner_anchors:
        return list(blocks), list(schedule), diags

    def _aligner_belongs_to_dropped(ent: Any) -> bool:
        if str(getattr(ent, "category", "") or "") not in _ALIGNER_CATS:
            return False
        t = float(getattr(ent, "time_sec", 0.0) or 0.0)
        # place = anchor+2.5, pick = anchor+6
        for a in drop_aligner_anchors:
            if abs(t - (a + place_d)) < 0.05 or abs(t - (a + pick_d)) < 0.05:
                return True
        return False

    new_blocks: List[Any] = []
    for b in blocks:
        sch = getattr(b, "schedule", None)
        if sch is not None and _aligner_belongs_to_dropped(sch):
            continue
        cat = str(getattr(b, "category", "") or "")
        if cat in _ALIGNER_CATS:
            t = float(getattr(b, "time_sec", 0.0) or 0.0)
            drop = False
            for a in drop_aligner_anchors:
                if abs(t - (a + place_d)) < 0.05 or abs(t - (a + pick_d)) < 0.05:
                    drop = True
                    break
            if drop:
                continue
        new_blocks.append(b)
    new_sched = [
        getattr(b, "schedule")
        for b in new_blocks
        if getattr(b, "schedule", None) is not None
    ]
    return new_blocks, new_sched, diags


def _shift_blocks_by_visibility_offset(
    blocks: List[Any],
    schedule: List[Any],
) -> Tuple[List[Any], List[Any], List[OccupancyDiagLine]]:
    """JSON 블록 시작 시각을 visibility 앵커까지 오프셋만큼 앞당긴다."""
    _ = schedule  # schedule 은 blocks.schedule 에서 재구성
    diags: List[OccupancyDiagLine] = []
    new_blocks: List[Any] = []

    for b in blocks:
        steps = [s for s in (getattr(b, "steps", None) or []) if isinstance(s, dict)]
        if not steps:
            new_blocks.append(b)
            continue
        off = visibility_offset_until_occupancy_sec(steps)
        old_t = float(getattr(b, "time_sec", 0.0) or 0.0)
        if off <= 1e-9:
            new_blocks.append(b)
            continue
        new_t = float(old_t - off)
        sch = getattr(b, "schedule", None)
        if sch is not None:
            new_sch = replace(sch, time_sec=new_t)
            new_b = replace(b, time_sec=new_t, schedule=new_sch)
        else:
            new_b = replace(b, time_sec=new_t)
        new_blocks.append(new_b)
        en = ""
        if sch is not None:
            en = str(
                getattr(sch, "event_name", "")
                or getattr(sch, "category", "")
                or ""
            )
        diags.append(
            OccupancyDiagLine(
                t_sec=new_t,
                kind="t_shift",
                message=(
                    f"{en or getattr(b, 'label', '') or 'json'} "
                    f"시작 {old_t:.1f}s → {new_t:.1f}s "
                    f"(visibility까지 −{off:.2f}s)"
                ),
            )
        )

    new_sched = [
        getattr(b, "schedule")
        for b in new_blocks
        if getattr(b, "schedule", None) is not None
    ]
    return new_blocks, new_sched, diags


def _wafer_id_from_title(title: str) -> Optional[Tuple[str, int]]:
    """title_ko 에서 lot / 웨이퍼# 추출."""
    import re

    lot = ""
    m = re.search(r"lot=['\"]([^'\"]+)['\"]", title or "")
    if m:
        lot = m.group(1).strip()
    cassette = -1
    m2 = re.search(r"웨이퍼#(\d+)", title or "")
    if m2:
        try:
            cassette = int(m2.group(1))
        except Exception:
            cassette = -1
    if lot and cassette >= 0:
        return (lot, cassette)
    return None


def _foup_slot_key(foup_index: int, cassette: int) -> str:
    return f"foup{int(foup_index)}_{int(cassette)}"


def _occupancy_dry_run_diagnostics(
    dwells: Sequence[Any],
    blocks: List[Any],
) -> List[OccupancyDiagLine]:
    """논리 점유 dry-run — 위반만 진단 기록 (블록 삭제하지 않음)."""
    diags: List[OccupancyDiagLine] = []
    # slot_key -> (lot_id, cassette) | None
    occ: Dict[str, Optional[Tuple[str, int]]] = {}
    arm: Dict[str, Optional[Tuple[str, int]]] = {
        "LOGICAL:ATM_ARM": None,
        "LOGICAL:VTM_EE_L": None,
        "LOGICAL:VTM_EE_R": None,
    }

    # 초기: 각 투어 첫 dwell 위치
    try:
        from .simulation_play import LOGICAL_SLOT_ATM_ARM, _group_dwell_tours

        for (lot_id, cassette_slot), tour in _group_dwell_tours(list(dwells or [])):
            if not tour:
                continue
            first = tour[0]
            sk = str(getattr(first, "slot_key", "") or "")
            wid = (str(lot_id), int(cassette_slot))
            if sk == LOGICAL_SLOT_ATM_ARM:
                fi = int(getattr(first, "foup_index", 1) or 1)
                occ[_foup_slot_key(fi, int(cassette_slot))] = wid
            elif sk:
                occ[sk] = wid
    except Exception:
        pass

    for b in sorted(blocks, key=lambda x: (float(x.time_sec), int(x.sort_order))):
        sch = getattr(b, "schedule", None)
        if sch is None:
            continue
        cat = str(getattr(sch, "category", "") or "")
        if cat == "dwell" or not list(getattr(b, "steps", None) or []):
            continue
        en = str(getattr(sch, "event_name", "") or "")
        title = str(getattr(sch, "title_ko", "") or "")
        wid = _wafer_id_from_title(title)
        t = float(getattr(b, "time_sec", 0.0) or 0.0)
        po = "pick" if en.endswith("_pick") or cat in ("pick", "aligner_pick") else (
            "place" if en.endswith("_place") or cat in ("place", "aligner_place") else ""
        )
        if cat == "transfer":
            if "→" in title or "->" in title:
                # AtmArm → module = place, module → AtmArm = pick
                if "ATM 팔" in title.split("→")[0] if "→" in title else False:
                    po = "place"
                elif "→" in title and "ATM 팔" in title.split("→")[-1]:
                    po = "pick"
                elif "팔" in title and "→" in title:
                    left, right = title.split("→", 1)
                    if "팔" in left:
                        po = "place"
                    elif "팔" in right:
                        po = "pick"

        sk = None
        try:
            from .lam_event_sequences import slot_key_for_event
            import re

            m = re.search(r"atm_foup(\d+)_", en, re.I)
            if m and wid is not None:
                sk = _foup_slot_key(int(m.group(1)), wid[1])
            else:
                num = None
                m2 = re.search(r"_(\d+)$", en)
                # slot_number from event for coolstation etc.
                sk = slot_key_for_event(en, None)
        except Exception:
            sk = None

        if not po or wid is None:
            continue

        if po == "pick":
            # 슬롯에 있어야 함
            if sk:
                cur = occ.get(sk)
                if cur is None:
                    diags.append(
                        OccupancyDiagLine(
                            t_sec=t,
                            kind="occ_warn",
                            message=f"빈 슬롯 pick 후보 {sk} · {en} · wafer#{wid[1]}",
                        )
                    )
                elif cur != wid:
                    diags.append(
                        OccupancyDiagLine(
                            t_sec=t,
                            kind="occ_warn",
                            message=(
                                f"다른 웨이퍼 점유 중 pick 후보 {sk} "
                                f"(있음={cur[1]}, 요청={wid[1]}) · {en}"
                            ),
                        )
                    )
                else:
                    occ[sk] = None
                    arm["LOGICAL:ATM_ARM"] = wid
        elif po == "place":
            if sk:
                cur = occ.get(sk)
                if cur is not None and cur != wid:
                    diags.append(
                        OccupancyDiagLine(
                            t_sec=t,
                            kind="occ_warn",
                            message=(
                                f"이미 점유된 슬롯 place 후보 {sk} "
                                f"(있음=wafer#{cur[1]}, 요청=#{wid[1]}) · {en}"
                            ),
                        )
                    )
                else:
                    occ[sk] = wid
                    if arm.get("LOGICAL:ATM_ARM") == wid:
                        arm["LOGICAL:ATM_ARM"] = None

    return diags


__all__ = [
    "OccupancyDiagLine",
    "apply_occupancy_scheduler",
    "occupancy_scheduler_enabled",
    "visibility_offset_until_occupancy_sec",
]
