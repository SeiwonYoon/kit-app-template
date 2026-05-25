"""LAM 이벤트 JSON → 실행 스텝 list 조립 (``lam/lam_event_sequences/<이벤트명>.json``).

**진입:** ``lam_sim_actions.atm_foup1_pick(1)`` → ``build_steps_for_event()`` →
``simulation_play.run_lam_sim_steps()`` → ``lam_sequence_engine.LamSequenceRunner``.

**이 파일이 하는 일**
- 이벤트명·slot_number → ``slot_key`` (``foup1_1`` 등)
- JSON 로드 + ``{SLOT_WAFER}`` / ``{ARM_WAFER}`` 치환 (``lam_wafer_prim_paths``)
- **자동 Z MOVE** 선행 삽입 (높이·prim·시간은 아래 SSOT)
- 조립된 스텝 list 반환 (실제 재생은 ``lam_sequence_engine``)

**설정 SSOT (여기서 값을 넣지 않음 — 다른 파일)**
- Z mm 테이블·Δ: ``lam_slot_z_config.py``
- Z MOVE prim: ``ATM_Z_MOVE_PRIM_PATH`` / ``VTM_Z_MOVE_PRIM_PATH``
- Z MOVE 시간(기본 0.5s): ``simulation_play.LamSimPlayVirtualConfig.lam_sim_z_slot_move_duration_sec``
- wafer prim: ``lam_wafer_prim_paths.py``
- 팔 애니 프레임: 각 JSON 의 ``TIMESAMPLES_REPLAY`` (사용자 편집)
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .lam_data_paths import resolve_local_data_path_or_default
from .lam_slot_z_config import (
    ATM_Z_MOVE_PRIM_PATH,
    VTM_Z_MOVE_PRIM_PATH,
    slot_z_diagnostic,
)
from .lam_wafer_prim_paths import (
    LOGICAL_SLOT_ATM_ARM,
    LOGICAL_SLOT_VTM_EE_L,
    LOGICAL_SLOT_VTM_EE_R,
    load_wafer_prim_by_slot_key,
)

_PRINT_PREFIX = "[LAM/EVENT]"

# ---------------------------------------------------------------------------
# 상수 — 이벤트 이름·JSON 토큰
# ---------------------------------------------------------------------------

# JSON ``prim`` / 설명 필드에서 쓰는 플레이스홀더.
TOKEN_SLOT_WAFER = "{SLOT_WAFER}"
TOKEN_ARM_WAFER = "{ARM_WAFER}"
TOKEN_SLOT_KEY = "{SLOT_KEY}"

_TEMPLATE_TOKENS = (TOKEN_SLOT_WAFER, TOKEN_ARM_WAFER, TOKEN_SLOT_KEY)

# prompt1.txt §172–226 전체 이벤트 이름 (JSON 파일명 = 함수명).
LAM_EVENT_NAMES: Tuple[str, ...] = tuple(
    [f"vtm_chamber{i}_{side}_{act}" for i in range(1, 6) for side in ("right", "left") for act in ("pick", "place")]
    + [
        f"vtm_airlock{n}_{side}_{act}"
        for n in (1, 2)
        for side in ("right", "left")
        for act in ("pick", "place")
    ]
    + [
        f"atm_foup{n}_{act}"
        for n in (1, 2, 3)
        for act in ("pick", "place")
    ]
    + [
        f"atm_buffer{n}_{act}"
        for n in (3, 4)
        for act in ("pick", "place")
    ]
    + [f"atm_coolstation_{act}" for act in ("pick", "place")]
    + [f"atm_airlock{n}_{act}" for n in (1, 2) for act in ("pick", "place")]
    + ["atm_aligner_pick", "atm_aligner_place"]
)

_EVENT_NEEDS_SLOT_NUMBER: frozenset[str] = frozenset(
    n
    for n in LAM_EVENT_NAMES
    if n.startswith(
        (
            "atm_foup",
            "atm_buffer",
            "atm_coolstation",
            "atm_airlock",
            "vtm_airlock",
        )
    )
)


def event_needs_slot_number(event_name: str) -> bool:
    return (event_name or "").strip() in _EVENT_NEEDS_SLOT_NUMBER


def format_event_description(event_name: str, slot_number: Optional[int] = None) -> str:
    """사람이 읽기 쉬운 동작 설명 (로그·UI용)."""
    name = (event_name or "").strip()
    sn = slot_number
    m = re.fullmatch(r"vtm_chamber(\d+)_(left|right)_(pick|place)", name)
    if m:
        hand = "좌손(EE-L)" if m.group(2) == "left" else "우손(EE-R)"
        act = "집기(pick)" if m.group(3) == "pick" else "내려놓기(place)"
        return f"VTM — Chamber {m.group(1)}, {hand}, 슬롯 chamber{m.group(1)} 에서 {act}"
    m = re.fullmatch(r"vtm_airlock(\d+)_(left|right)_(pick|place)", name)
    if m:
        n = int(sn or 1)
        hand = "좌손" if m.group(2) == "left" else "우손"
        act = "집기" if m.group(3) == "pick" else "내려놓기"
        return f"VTM — Airlock {m.group(1)} 슬롯 {n}, {hand}, {act} (airlock{m.group(1)}_{n})"
    m = re.fullmatch(r"atm_foup(\d+)_(pick|place)", name)
    if m:
        n = int(sn or 1)
        act = "집기" if m.group(2) == "pick" else "내려놓기"
        return f"ATM — FOUP {m.group(1)} 슬롯 {n}, {act} (foup{m.group(1)}_{n})"
    m = re.fullmatch(r"atm_buffer(\d+)_(pick|place)", name)
    if m:
        n = int(sn or 1)
        act = "집기" if m.group(2) == "pick" else "내려놓기"
        return f"ATM — Buffer {m.group(1)} 슬롯 {n}, {act} (buffer{m.group(1)}_{n})"
    m = re.fullmatch(r"atm_coolstation_(pick|place)", name)
    if m:
        n = int(sn or 1)
        act = "집기" if m.group(1) == "pick" else "내려놓기"
        return f"ATM — Coolstation 슬롯 {n}, {act} (cooling_{n})"
    m = re.fullmatch(r"atm_airlock(\d+)_(pick|place)", name)
    if m:
        n = int(sn or 1)
        act = "집기" if m.group(2) == "pick" else "내려놓기"
        return f"ATM — Airlock {m.group(1)} 슬롯 {n}, {act} (airlock{m.group(1)}_{n})"
    if name == "atm_aligner_pick":
        return "ATM — Aligner 에서 집기 (aligner)"
    if name == "atm_aligner_place":
        return "ATM — Aligner 에 내려놓기 (aligner)"
    return name


def _summarize_steps_for_log(steps: List[Dict[str, Any]]) -> str:
    from collections import Counter

    kinds = Counter(str(s.get("type") or "?").upper() for s in steps)
    parts = [f"{k}={v}" for k, v in sorted(kinds.items())]
    n_ts = kinds.get("TIMESAMPLES_REPLAY", 0)
    hint = ""
    if n_ts == 0:
        hint = " | ⚠ TIMESAMPLES_REPLAY 없음 — JSON 에 in/out 프레임 스텝을 추가하세요"
    return ", ".join(parts) + hint


def _event_verbose_log_enabled() -> bool:
    try:
        from .simulation_play import is_csv_playback_compact_log

        return not is_csv_playback_compact_log()
    except Exception:
        return True


def log_event_invoke(event_name: str, *, slot_number: Optional[int] = None) -> None:
    """함수 호출 시 콘솔에 상세 설명 (JSON 경로·슬롯·동작)."""
    if not _event_verbose_log_enabled():
        return
    name = (event_name or "").strip()
    sk = slot_key_for_event(name, slot_number)
    path = event_json_path(name)
    desc = format_event_description(name, slot_number)
    sn_s = f"slot_number={slot_number}" if slot_number is not None else "(단일 슬롯)"
    print(f"{_PRINT_PREFIX} ========== EVENT {name} ==========", flush=True)
    print(f"{_PRINT_PREFIX}   설명: {desc}", flush=True)
    print(f"{_PRINT_PREFIX}   매개변수: {sn_s}  →  slot_key={sk!r}", flush=True)
    print(f"{_PRINT_PREFIX}   JSON: {path}", flush=True)
    if not path.is_file():
        print(f"{_PRINT_PREFIX}   ⚠ JSON 파일 없음 — ensure_event_json_scaffolds() 확인", flush=True)
    print(f"{_PRINT_PREFIX} =====================================", flush=True)


def log_slot_z_resolution(
    event_name: str,
    slot_key: str,
    *,
    robot: str,
    prim_path: str,
    move_target_m: Optional[float],
) -> None:
    """이벤트 실행 시 자동 Z MOVE 에 쓰인 값 (SSOT → 시뮬 목표)."""
    if not _event_verbose_log_enabled():
        return
    d = slot_z_diagnostic(slot_key, robot=robot)
    print(f"{_PRINT_PREFIX} --- Z 슬롯 ({event_name} → {slot_key!r}, robot={robot}) ---", flush=True)
    if not d.get("defined"):
        print(f"{_PRINT_PREFIX}   ⚠ lam_slot_z_config 에 {slot_key!r} 없음", flush=True)
        return
    print(
        f"{_PRINT_PREFIX}   CAD 절대 Z = {d['document_absolute_mm']:.3f} mm  "
        f"(문서 기준 {d['document_reference_mm']:.2f} 대비 Δ = {d['delta_from_document_mm']:.3f} mm)",
        flush=True,
    )
    delta_mm = d.get("delta_from_applied_mm")
    print(
        f"{_PRINT_PREFIX}   TBS 원점 = 적용 기준 {d['applied_reference_mm']:.2f} mm (= 0)  "
        f"→ 슬롯 오프셋 Δ = {float(delta_mm):.3f} mm",
        flush=True,
    )
    print(f"{_PRINT_PREFIX}   Z MOVE prim (장비): {prim_path!r}", flush=True)
    if move_target_m is not None and delta_mm is not None:
        print(
            f"{_PRINT_PREFIX}   move_from_initial=True  dz={move_target_m:.3f}  "
            f"(TBS/mm, 기준 0 = Δ {float(delta_mm):.3f} mm)",
            flush=True,
        )
    print(f"{_PRINT_PREFIX}   SSOT: {d.get('source')}  (prim·Z 테이블)", flush=True)
    print(f"{_PRINT_PREFIX} --- Z 끝 ---", flush=True)


def log_event_steps_built(event_name: str, steps: List[Dict[str, Any]], *, slot_number: Optional[int] = None) -> None:
    """스텝 조립 후 요약 로그."""
    if not _event_verbose_log_enabled():
        return
    summary = _summarize_steps_for_log(steps)
    print(
        f"{_PRINT_PREFIX} {event_name}: 실행 스텝 {len(steps)}개 — {summary}",
        flush=True,
    )
    for i, st in enumerate(steps):
        t = str(st.get("type") or "").upper()
        d = (st.get("description") or "")[:80]
        extra = ""
        if t == "TIMESAMPLES_REPLAY":
            extra = f" frames={st.get('start_frame')}..{st.get('end_frame')}"
        elif t == "MOVE":
            dz = st.get("dz")
            from_init = bool(st.get("move_from_initial", False))
            prim_move = str(st.get("prim") or "")
            src = "auto-Z" if str(d).startswith("auto Z") else "JSON"
            if from_init and dz is not None:
                extra = f" [{src}] prim={prim_move!r} dz={dz} (TBS/mm, 기준0)"
            else:
                extra = f" [{src}] prim={prim_move!r} dz={dz} dy={st.get('dy')} dx={st.get('dx')}"
        elif t == "PRIM_VISIBILITY":
            prim_vis = str(st.get("prim") or "").strip()
            if not prim_vis:
                extra = f" mode={st.get('mode')} prim=(비어 있음 — lam_wafer_prim_paths.py)"
            else:
                extra = f" mode={st.get('mode')}"
        print(f"{_PRINT_PREFIX}   [{i}] {t}{extra}  {d!r}", flush=True)
        if t == "PRIM_VISIBILITY":
            prim_vis = str(st.get("prim") or "").strip()
            if prim_vis:
                print(f"{_PRINT_PREFIX}        prim={prim_vis}", flush=True)


def get_event_sequences_dir() -> Path:
    d = resolve_local_data_path_or_default("lam_event_sequences")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def event_json_path(event_name: str) -> Path:
    return get_event_sequences_dir() / f"{event_name}.json"


# ---------------------------------------------------------------------------
# 이벤트명 ↔ slot_key (Z 테이블·wafer dict 키)
# ---------------------------------------------------------------------------


def slot_key_for_event(event_name: str, slot_number: Optional[int] = None) -> str:
    """이벤트 이름 + 슬롯 번호 → 물리 ``slot_key`` (``lam_slot_z_config`` / wafer dict 키)."""
    name = (event_name or "").strip()
    if not name:
        raise ValueError("event_name 이 비었습니다.")

    m = re.fullmatch(r"vtm_chamber(\d+)_(?:left|right)_(?:pick|place)", name)
    if m:
        return f"chamber{int(m.group(1))}"

    m = re.fullmatch(r"vtm_airlock(\d+)_(?:left|right)_(?:pick|place)", name)
    if m:
        n = int(slot_number or 0)
        if n not in (1, 2):
            raise ValueError(f"{name}: slot_number 는 1 또는 2 여야 합니다 (got {slot_number!r})")
        return f"airlock{int(m.group(1))}_{n}"

    m = re.fullmatch(r"atm_foup(\d+)_(?:pick|place)", name)
    if m:
        n = int(slot_number or 0)
        if not (1 <= n <= 25):
            raise ValueError(f"{name}: slot_number 는 1..25 (got {n})")
        return f"foup{int(m.group(1))}_{n}"

    m = re.fullmatch(r"atm_buffer(\d+)_(?:pick|place)", name)
    if m:
        n = int(slot_number or 0)
        if not (1 <= n <= 25):
            raise ValueError(f"{name}: slot_number 는 1..25 (got {n})")
        return f"buffer{int(m.group(1))}_{n}"

    m = re.fullmatch(r"atm_coolstation_(?:pick|place)", name)
    if m:
        n = int(slot_number or 0)
        if not (1 <= n <= 7):
            raise ValueError(f"{name}: slot_number 는 1..7 (got {n})")
        return f"cooling_{n}"

    m = re.fullmatch(r"atm_airlock(\d+)_(?:pick|place)", name)
    if m:
        n = int(slot_number or 0)
        if n not in (1, 2):
            raise ValueError(f"{name}: slot_number 는 1 또는 2 (got {n})")
        return f"airlock{int(m.group(1))}_{n}"

    if name in ("atm_aligner_pick", "atm_aligner_place"):
        return "aligner"

    raise ValueError(f"알 수 없는 이벤트 이름: {name!r}")


def robot_for_event(event_name: str) -> str:
    return "vtm" if event_name.startswith("vtm_") else "atm"


def arm_slot_key_for_event(event_name: str, *, vtm_ee_swap: bool = False) -> str:
    if robot_for_event(event_name) == "atm":
        return LOGICAL_SLOT_ATM_ARM
    if "_left_" in event_name:
        return LOGICAL_SLOT_VTM_EE_R if vtm_ee_swap else LOGICAL_SLOT_VTM_EE_L
    if "_right_" in event_name:
        return LOGICAL_SLOT_VTM_EE_L if vtm_ee_swap else LOGICAL_SLOT_VTM_EE_R
    return LOGICAL_SLOT_VTM_EE_L


def atm_event_name_for_slot(slot_key: str, pick_or_place: str) -> Tuple[str, Optional[int]]:
    """물리 slot_key + pick/place → ``atm_*`` 이벤트명, slot_number(없으면 None)."""
    sk = (slot_key or "").strip()
    po = (pick_or_place or "pick").strip().lower()
    if po not in ("pick", "place"):
        po = "pick"

    if sk == "aligner":
        return f"atm_aligner_{po}", None

    m = re.fullmatch(r"cooling_(\d+)", sk)
    if m:
        return f"atm_coolstation_{po}", int(m.group(1))

    m = re.fullmatch(r"foup(\d+)_(\d+)", sk)
    if m:
        return f"atm_foup{int(m.group(1))}_{po}", int(m.group(2))

    m = re.fullmatch(r"buffer(\d+)_(\d+)", sk)
    if m:
        buf_n = int(m.group(1))
        if buf_n not in (3, 4):
            raise ValueError(f"ATM buffer 는 3·4 만 지원: {sk!r}")
        return f"atm_buffer{buf_n}_{po}", int(m.group(2))

    m = re.fullmatch(r"airlock(\d+)_(\d+)", sk)
    if m:
        return f"atm_airlock{int(m.group(1))}_{po}", int(m.group(2))

    raise ValueError(f"ATM 이벤트로 매핑할 수 없는 slot_key: {sk!r}")


def vtm_event_name_for_slot(slot_key: str, hand: str, pick_or_place: str) -> Tuple[str, Optional[int]]:
    sk = (slot_key or "").strip()
    h = (hand or "left").strip().lower()
    if h not in ("left", "right"):
        h = "left"
    po = (pick_or_place or "pick").strip().lower()
    if po not in ("pick", "place"):
        po = "pick"

    m = re.fullmatch(r"chamber(\d+)", sk)
    if m:
        n = int(m.group(1))
        if not (1 <= n <= 5):
            raise ValueError(f"chamber index 범위 초과: {sk!r}")
        return f"vtm_chamber{n}_{h}_{po}", None

    m = re.fullmatch(r"airlock(\d+)_(\d+)", sk)
    if m:
        return f"vtm_airlock{int(m.group(1))}_{h}_{po}", int(m.group(2))

    raise ValueError(f"VTM 이벤트로 매핑할 수 없는 slot_key: {sk!r}")


def _default_scaffold_steps(event_name: str) -> List[Dict[str, Any]]:
    """시퀀스 에디터에서 편집할 최소 스캐폴드."""
    if event_name.endswith("_pick"):
        slot_mode, arm_mode = "hide", "show"
    elif event_name.endswith("_place"):
        slot_mode, arm_mode = "show", "hide"
    else:
        slot_mode, arm_mode = "show", "hide"
    return [
        {
            "type": "PRIM_VISIBILITY",
            "mode": slot_mode,
            "prim": TOKEN_SLOT_WAFER,
            "duration": 0.02,
            "run_with_previous": False,
            "step_delay_ms": 0,
            "description": f"{event_name} — SLOT wafer (편집)",
        },
        {
            "type": "PRIM_VISIBILITY",
            "mode": arm_mode,
            "prim": TOKEN_ARM_WAFER,
            "duration": 0.02,
            "run_with_previous": True,
            "step_delay_ms": 0,
            "description": f"{event_name} — ARM wafer (편집)",
        },
        {
            "type": "DELAY",
            "duration": 0.5,
            "run_with_previous": False,
            "step_delay_ms": 0,
            "description": f"{event_name} — TIMESAMPLES_REPLAY 등을 여기에 추가",
        },
    ]


def ensure_event_json_scaffolds(*, overwrite: bool = False) -> int:
    """``lam_event_sequences/*.json`` 이 없으면 스캐폴드 생성. 생성 개수 반환."""
    created = 0
    for name in LAM_EVENT_NAMES:
        path = event_json_path(name)
        if path.is_file() and not overwrite:
            continue
        path.write_text(
            json.dumps(_default_scaffold_steps(name), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        created += 1
    return created


def _substitute_templates(obj: Any, mapping: Dict[str, str]) -> Any:
    if isinstance(obj, str):
        out = obj
        for tok, val in mapping.items():
            out = out.replace(tok, val)
        return out
    if isinstance(obj, list):
        return [_substitute_templates(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {k: _substitute_templates(v, mapping) for k, v in obj.items()}
    return obj


def _resolve_wafer_path(slot_key: str, wafer_map: Dict[str, str]) -> str:
    p = (wafer_map.get(slot_key) or "").strip()
    if not p and _event_verbose_log_enabled():
        print(
            f"{_PRINT_PREFIX} wafer prim 없음 slot_key={slot_key!r} — "
            f"lam_wafer_prim_paths.py 확인",
            flush=True,
        )
    return p


def _make_slot_z_move_step(
    *,
    prim_path: str,
    target_z_m: float,
    duration_sec: float,
    slot_key: str,
    concurrent_with_next: bool,
    description_suffix: str = "",
) -> Dict[str, Any]:
    """Python이 JSON 앞에 넣는 **자동 Z MOVE** 스텝 dict 한 개.

    - ``prim``: HeightStage / VTM Z (``lam_slot_z_config`` 의 ``*_Z_MOVE_PRIM_PATH``)
    - ``dz``: TBS/mm 절대 목표 (기준 0 = 905.92 mm). 예: 25.928
    - ``move_from_initial=True``: 편집기 “최초 위치 기준(절대 좌표)” 와 동일
    - ``duration``: ``lam_sim_z_slot_move_duration_sec`` (기본 0.5)
    """
    return {
        "type": "MOVE",
        "prim": prim_path,
        "duration": float(duration_sec),
        "dx": 0.0,
        "dy": 0.0,
        "dz": float(target_z_m),
        "move_from_initial": True,
        "run_with_previous": bool(concurrent_with_next),
        "step_delay_ms": 0,
        "description": f"auto Z → {slot_key} dz={target_z_m:.3f} (TBS/mm, 기준=0){description_suffix}",
    }


def build_steps_for_event(
    event_name: str,
    *,
    slot_number: Optional[int] = None,
    vtm_ee_swap: bool = False,
) -> List[Dict[str, Any]]:
    """이벤트 한 건 → LamSequenceRunner 에 넘길 **스텝 list** 조립.

    순서 (반환 list 인덱스):
      [0] 자동 Z MOVE (항상 Python 삽입)
      [1..] JSON 파일 스텝 (visibility, TIMESAMPLES_REPLAY, DELAY …)

    수정 포인트:
      - JSON 내용만 바꿀 때: ``lam/lam_event_sequences/<event_name>.json``
      - Z 높이: ``lam_slot_z_config.py`` (이 함수는 Δ→dz 만 계산)
      - Z prim / 0.5s: ``lam_slot_z_config`` prim + ``simulation_play`` duration
    """
    from .simulation_play import (  # 순환 import 방지 — 런타임만
        LAM_SIM_VIRTUAL_CONFIG,
        refresh_lam_sim_runtime_tables_from_config,
    )

    # --- 1) 이벤트명·슬롯 번호 검증 ---
    name = (event_name or "").strip()
    if name not in LAM_EVENT_NAMES:
        raise ValueError(f"등록되지 않은 이벤트: {name!r}")

    if name in _EVENT_NEEDS_SLOT_NUMBER and slot_number is None:
        slot_number = 1

    log_event_invoke(name, slot_number=slot_number)

    # --- 2) JSON 파일 로드 (없으면 스캐폴드만 생성, 덮어쓰기 안 함) ---
    try:
        from .simulation_play import is_csv_bulk_build_active

        bulk = is_csv_bulk_build_active()
    except Exception:
        bulk = False
    if not bulk:
        ensure_event_json_scaffolds(overwrite=False)
    path = event_json_path(name)
    if not path.is_file():
        raise FileNotFoundError(f"이벤트 JSON 없음: {path}")

    # --- 3) slot_key · wafer prim 매핑 (치환용) ---
    if not bulk:
        refresh_lam_sim_runtime_tables_from_config()
    wafer_map = load_wafer_prim_by_slot_key()
    sk = slot_key_for_event(name, slot_number)
    arm_sk = arm_slot_key_for_event(name, vtm_ee_swap=vtm_ee_swap)

    slot_wafer = _resolve_wafer_path(sk, wafer_map)
    arm_wafer = _resolve_wafer_path(arm_sk, wafer_map)
    mapping = {
        TOKEN_SLOT_WAFER: slot_wafer,
        TOKEN_ARM_WAFER: arm_wafer,
        TOKEN_SLOT_KEY: sk,
    }

    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError(f"{path}: 최상위는 list 여야 합니다.")

    steps: List[Dict[str, Any]] = copy.deepcopy(raw)
    steps = _substitute_templates(steps, mapping)

    # --- 4) 자동 Z MOVE (JSON 앞에 삽입) ---
    cfg = LAM_SIM_VIRTUAL_CONFIG
    robot = robot_for_event(name)  # "atm" | "vtm" → 어느 Z prim·테이블 쓸지
    z_move_prim = ""
    move_target_z: Optional[float] = None
    # Z 이동 시간 [s] — simulation_play.LamSimPlayVirtualConfig (기본 0.5)
    z_dur = float(cfg.lam_sim_z_slot_move_duration_sec or 0.5)

    if robot == "atm":
        z_move_prim = (cfg.atm_height_prim_path or ATM_Z_MOVE_PRIM_PATH or "").strip()
    else:
        z_move_prim = (cfg.vtm_position_prim_path or VTM_Z_MOVE_PRIM_PATH or "").strip()
    if _event_verbose_log_enabled():
        print(
            f"{_PRINT_PREFIX}   Z 장비 prim ({robot}): {z_move_prim!r}  "
            f"(SSOT: lam_slot_z_config.py → ATM_Z_MOVE_PRIM_PATH / VTM_Z_MOVE_PRIM_PATH)",
            flush=True,
        )
    # dz [TBS/mm]: 기준 905.92mm = 0 → foup1_1 은 25.928 (slot_z_move_target_m)
    move_target_z = cfg.slot_z_move_target_m(sk, robot=robot)

    out: List[Dict[str, Any]] = []
    if z_move_prim and move_target_z is not None:
        log_slot_z_resolution(
            name,
            sk,
            robot=robot,
            prim_path=z_move_prim,
            move_target_m=float(move_target_z),
        )
        diag = slot_z_diagnostic(sk, robot=robot)
        delta_mm = diag.get("delta_from_applied_mm")
        desc_extra = ""
        if isinstance(delta_mm, (int, float)):
            desc_extra = f" ({float(delta_mm):.3f} mm)"
        out.append(
            _make_slot_z_move_step(
                prim_path=z_move_prim,
                target_z_m=float(move_target_z),
                duration_sec=z_dur,
                slot_key=sk,
                concurrent_with_next=False,
                description_suffix=desc_extra,
            )
        )
    elif not z_move_prim and _event_verbose_log_enabled():
        print(f"{_PRINT_PREFIX} {name}: Z MOVE prim 경로 비어 있음 (robot={robot})", flush=True)
    elif move_target_z is None and _event_verbose_log_enabled():
        print(f"{_PRINT_PREFIX} {name}: slot Z 미정의 slot_key={sk!r}", flush=True)

    # JSON 첫 스텝을 Z 와 동시 시작 (run_with_previous)
    if steps and out:
        steps[0] = dict(steps[0])
        steps[0]["run_with_previous"] = True

    try:
        from .lam_wafer_viewport_labels import (
            annotate_steps_with_wafer_label_context,
            make_wafer_label_step_context,
        )

        label_ctx = make_wafer_label_step_context(
            event_name=name,
            slot_key=sk,
            arm_slot_key=arm_sk,
            slot_wafer_path=slot_wafer,
            arm_wafer_path=arm_wafer,
        )
        steps = annotate_steps_with_wafer_label_context(steps, label_ctx)
    except Exception:
        pass

    out.extend(steps)
    log_event_steps_built(name, out, slot_number=slot_number)
    return out


def load_event_json_steps(event_name: str, **kwargs: Any) -> List[Dict[str, Any]]:
    """``build_steps_for_event`` 별칭."""
    return build_steps_for_event(event_name, **kwargs)


# 모듈 import 시 스캐폴드만 보장 (기존 event_*.json 은 덮어쓰지 않음).
try:
    ensure_event_json_scaffolds(overwrite=False)
except Exception as exc:
    print(f"{_PRINT_PREFIX} scaffold skip: {exc}", flush=True)


__all__ = [
    "LAM_EVENT_NAMES",
    "TOKEN_SLOT_WAFER",
    "TOKEN_ARM_WAFER",
    "TOKEN_SLOT_KEY",
    "build_steps_for_event",
    "ensure_event_json_scaffolds",
    "event_json_path",
    "event_needs_slot_number",
    "format_event_description",
    "get_event_sequences_dir",
    "log_event_invoke",
    "log_event_steps_built",
    "slot_key_for_event",
    "atm_event_name_for_slot",
    "vtm_event_name_for_slot",
    "robot_for_event",
]
