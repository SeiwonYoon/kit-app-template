"""LAM 시뮬: CSV dwell → (향후) 시퀀스 스텝 JSON → 재생.

이 파일을 읽는 순서(구조만):
  1) **상단 상수** — ``LOGICAL:...`` 같은 내부 슬롯 이름, CSV 시간 모드, VTM 좌우 스왑 여부.
  2) **``default_lam_sim_virtual_config()``** — Z / height stage 경로만 (프레임·Yaw 는 event JSON).
     (웨이퍼 prim 경로는 **``lam_wafer_prim_paths.py``** SSOT.)
  3) **``MODULE_NM_TO_SLOT_KEY``** — CSV 의 ``module_nm`` 문자열이 위 슬롯 키로 바뀌는 표
     (``build_default_module_nm_to_slot_key()``).
  4) **CSV 파싱** — 행을 읽어 dwell 리스트로 만듦.
  5) **``log_virtual_timeline_from_dwells``** — 콘솔에 이송·머무름 텍스트 로그.
  6) **dwell 간 이송** — ``build_steps_for_dwell_transfer`` 가 ``lam_event_sequences`` JSON 을 실행.
  7) **동작별 함수** — ``atm_foup1_pick`` / ``vtm_chamber1_right_pick`` 등 (``lam_sim_actions``).

용어를 짧게:
  - **dwell** — CSV 한 줄: 웨이퍼가 어떤 모듈에 **머문 시간 구간**.
  - **slot_key** — 코드 안에서 쓰는 위치 이름 (예: ``foup1_3``, ``chamber2``, ``LOGICAL:ATM_ARM``).
규칙: 장비 도메인·시뮬 조합 설계의 문서 SSOT 는 ``docs/LAM_Equipment_Model.md`` (코드와 표를 맞출 것).

Kit 에서 재생: ``run_simulation_from_csv(...)`` 는 ``LamSequenceRunner`` 와 같이
백그라운드 스레드에서 호출하는 것이 안전하다.
"""

from __future__ import annotations

import csv
import json
import ast
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .lam_slot_z_config import (
    ATM_Z_MOVE_PRIM_PATH,
    VTM_Z_MOVE_PRIM_PATH,
    load_atm_z_tables,
    load_vtm_z_tables,
    refresh_slot_z_deltas,
)
from .lam_wafer_prim_paths import load_wafer_prim_by_slot_key

# ---------------------------------------------------------------------------
# 1) CSV 루트·기본 경로·시간 모드 등 (Kit 외 스크립트에서도 사용)
# ---------------------------------------------------------------------------


def _find_lam_data_root() -> str:
    """저장소에서 ``lam`` 폴더(이벤트·USD·csv 공용 데이터 루트)를 찾는다.

    ``lam_window`` 와 동일: 이 파일 위치에서 상위 디렉터리를 최대 12단계까지 올라가며
    ``.../lam`` 이 존재하는 경로를 반환. 없으면 상대 경로로 ``lam`` 을 가리키는 fallback.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    for _ in range(12):
        cand = os.path.normpath(os.path.join(cur, "lam"))
        if os.path.isdir(cand):
            return cand
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    return os.path.normpath(os.path.join(here, "..", "..", "..", "..", "..", "..", "lam"))


def get_lam_csv_dir() -> Path:
    """시뮬 dwell CSV 디렉터리 ``lam/csv`` 경로를 반환한다.

    폴더가 없으면 생성을 시도한다(실패해도 Path 는 반환).
    """
    d = Path(_find_lam_data_root()) / "csv"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def list_lam_csv_paths() -> List[Path]:
    """``lam/csv`` 이하의 ``*.csv`` 파일 경로 목록을 파일명(대소문자 무시)순으로 반환한다."""
    d = get_lam_csv_dir()
    if not d.is_dir():
        return []
    return sorted(d.glob("*.csv"), key=lambda p: p.name.lower())


def _default_csv_path() -> str:
    """확장 UI·CLI 에서 쓸 기본 CSV 파일 경로 문자열.

    ``lam/csv/wafer01_tour_v1.csv`` 가 있으면 우선, 없으면 ``lam/csv`` 안 첫 ``*.csv``,
    그것도 없으면 샘플 파일명만 가리키는 경로(아직 없을 수 있음).
    """
    paths = list_lam_csv_paths()
    preferred = get_lam_csv_dir() / "wafer01_tour_v1.csv"
    if preferred.is_file():
        return str(preferred)
    if paths:
        return str(paths[0])
    return str(get_lam_csv_dir() / "wafer01_tour_v1.csv")


DEFAULT_CSV_PATH: str = os.environ.get("LAM_SIM_CSV", _default_csv_path())
# ``LAM_SIM_CSV`` 환경변수 우선, 없으면 ``_default_csv_path()`` (모듈 로드 시 한 번만 평가).

# 아래 스칼라·논리 슬롯 문자열은 **한 블록**에서만 수정한다.
# ``MODULE_NM_TO_SLOT_KEY`` 는 ``build_default_module_nm_to_slot_key()`` 정의 직후 채운다.
# ``VTM_END_EFFECTOR_SWAP_HANDS`` 를 런타임에 바꾼 뒤에는 ``rebuild_module_nm_slot_mapping()`` 을 호출한다.
TIME_PARSE_MODE: str = "seconds_float"
VTM_END_EFFECTOR_SWAP_HANDS: bool = False
FOUP1_CASSETTE_ID_MIN: int = 1
FOUP1_CASSETTE_ID_MAX: int = 25
_PRINT_PREFIX: str = "[LAM/SIMPLAY]"
# 매크로·CSV 이송 스텝의 **프레임 재생** 은 ``TIMESAMPLES_REPLAY`` 만 사용한다.
# 프레임 재생은 event JSON 의 ``TIMESAMPLES_REPLAY`` (``LamSequenceRunner``).
LOGICAL_SLOT_ATM_ARM: str = "LOGICAL:ATM_ARM"
LOGICAL_SLOT_VTM_EE_L: str = "LOGICAL:VTM_EE_L"
LOGICAL_SLOT_VTM_EE_R: str = "LOGICAL:VTM_EE_R"


# ---------------------------------------------------------------------------
# 2) Z / height stage — ``lam_slot_z_config.py`` + ``default_lam_sim_virtual_config()``
# 프레임·회전·TIMESAMPLES_REPLAY 구간은 ``lam_event_sequences/<이벤트>.json`` 에만 둔다.
# ---------------------------------------------------------------------------


@dataclass
class LamSimPlayVirtualConfig:
    """시뮬 런타임 설정 — Z stage prim·이동 시간·USD 보정.

    | 필드 | 역할 | SSOT |
    |------|------|------|
    | ``atm_height_prim_path`` | ATM Z MOVE 대상 prim | ``lam_slot_z_config.ATM_Z_MOVE_PRIM_PATH`` |
    | ``vtm_position_prim_path`` | VTM Z MOVE 대상 prim | ``lam_slot_z_config.VTM_Z_MOVE_PRIM_PATH`` |
    | ``lam_sim_z_slot_move_duration_sec`` | 자동 Z MOVE 시간 [s] (기본 **0.5**) | **본 필드** |
    | ``atm_z_usd_world_offset_m`` | 전 슬롯 Z 보정 (TBS/mm) | **본 필드** |
    | ``z_slot_delta_m`` | [m] 테이블 (레거시) | ``load_atm_z_tables()`` |

    프레임·Yaw·visibility 는 각 ``lam/lam_event_sequences/*.json``.
    """

    timeline_log_enabled: bool = True
    atm_height_prim_path: str = "/World/LAM/ATM/HeightStage"
    vtm_position_prim_path: str = "/World/LAM/_VIRTUAL/VTM/ZStage"
    lam_sim_z_slot_move_duration_sec: float = 0.5  # Z 동시 이동 duration — build_steps_for_event 가 사용
    vtm_z_table_authored_baseline_m: float = 0.101
    vtm_z_baseline_applied_m: float = 0.101
    vtm_z_usd_world_offset_m: float = 0.0
    vtm_z_slot_delta_m: Dict[str, float] = field(default_factory=dict)
    z_table_authored_baseline_m: float = 0.101
    z_baseline_applied_m: float = 0.101
    atm_z_usd_world_offset_m: float = 0.0
    z_slot_delta_m: Dict[str, float] = field(default_factory=dict)

    def atm_z_total_world_offset_m(self) -> float:
        return float(self.atm_z_usd_world_offset_m) + float(self.z_baseline_applied_m) - float(
            self.z_table_authored_baseline_m
        )

    def vtm_z_total_world_offset_m(self) -> float:
        return float(self.vtm_z_usd_world_offset_m) + float(self.vtm_z_baseline_applied_m) - float(
            self.vtm_z_table_authored_baseline_m
        )

    def effective_slot_z_m(self, slot_key: str) -> Optional[float]:
        if slot_key not in self.z_slot_delta_m:
            return None
        authored_abs = float(self.z_table_authored_baseline_m + self.z_slot_delta_m[slot_key])
        return authored_abs + self.atm_z_total_world_offset_m()

    def effective_vtm_slot_z_m(self, slot_key: str) -> Optional[float]:
        off = self.vtm_z_total_world_offset_m()
        if slot_key in self.vtm_z_slot_delta_m:
            return float(self.vtm_z_table_authored_baseline_m + self.vtm_z_slot_delta_m[slot_key]) + off
        if slot_key in self.z_slot_delta_m:
            return float(self.vtm_z_table_authored_baseline_m + self.z_slot_delta_m[slot_key]) + off
        return None

    def slot_z_move_target_m(self, slot_key: str, *, robot: str = "atm") -> Optional[float]:
        """HeightStage MOVE ``dz`` — **TBS/mm 스케일** (편집기에 25.928 넣는 것과 동일, m 아님).

        이름은 레거시(``_m``)이나 값은 ``slot_z_move_target_dz`` [mm] + USD 보정(같은 단위).
        """
        from .lam_slot_z_config import slot_z_move_target_dz

        r = (robot or "atm").strip().lower()
        off = slot_z_move_target_dz(slot_key, robot=r)
        if off is None and r == "vtm":
            off = slot_z_move_target_dz(slot_key, robot="atm")
        if off is None:
            return None
        usd = float(self.vtm_z_usd_world_offset_m if r == "vtm" else self.atm_z_usd_world_offset_m)
        return float(off) + usd


def default_lam_sim_virtual_config() -> LamSimPlayVirtualConfig:
    """Z from lam_slot_z_config. Animation clips in lam_event_sequences/*.json."""
    atm_z0_m, _z_slot_delta = load_atm_z_tables()
    vtm_z0_m, _vtm_z_slot_delta = load_vtm_z_tables()
    return LamSimPlayVirtualConfig(
        timeline_log_enabled=True,
        atm_height_prim_path=ATM_Z_MOVE_PRIM_PATH,
        vtm_position_prim_path=VTM_Z_MOVE_PRIM_PATH,
        lam_sim_z_slot_move_duration_sec=0.5,
        vtm_z_table_authored_baseline_m=vtm_z0_m,
        vtm_z_baseline_applied_m=vtm_z0_m,
        vtm_z_usd_world_offset_m=0.0,
        vtm_z_slot_delta_m=dict(_vtm_z_slot_delta),
        z_table_authored_baseline_m=atm_z0_m,
        z_baseline_applied_m=atm_z0_m,
        atm_z_usd_world_offset_m=0.0,
        z_slot_delta_m=dict(_z_slot_delta),
    )


# 모듈 로드 시 ``default_lam_sim_virtual_config()`` 결과를 전역으로 둔다. 런타임에 필드를 바꿀 수 있음.
LAM_SIM_VIRTUAL_CONFIG: LamSimPlayVirtualConfig = default_lam_sim_virtual_config()

# ``refresh_lam_sim_runtime_tables_from_config()`` 가 채운다 (다른 모듈에서 import 하는 조회용 캐시).
WAFER_PRIM_BY_SLOT_KEY: Dict[str, str] = {}
SLOT_Z_METERS: Dict[str, float] = {}
ATM_HEIGHT_PRIM_PATH: str = ""


def refresh_lam_sim_runtime_tables_from_config() -> None:
    """``LAM_SIM_VIRTUAL_CONFIG`` 내용을 아래 전역 dict / 문자열에 다시 쓴다.

    - ``WAFER_PRIM_BY_SLOT_KEY``: 슬롯별 웨이퍼 prim 경로
    - ``SLOT_Z_METERS``: 슬롯별 **적용 절대 Z** (= ``effective_slot_z_m`` = 문서 절대 Z + ``atm_z_total_world_offset_m()``)
    - ``ATM_HEIGHT_PRIM_PATH``: ATM Z MOVE 대상 prim

    ``LAM_SIM_VIRTUAL_CONFIG`` 의 필드를 코드에서 바꾼 뒤에는 반드시 이 함수를 호출해야
    기존 import 한 모듈들이 보는 캐시가 갱신된다.
    """
    global WAFER_PRIM_BY_SLOT_KEY, ATM_HEIGHT_PRIM_PATH
    refresh_slot_z_deltas()
    atm_z0_m, atm_delta_m = load_atm_z_tables()
    vtm_z0_m, vtm_delta_m = load_vtm_z_tables()
    LAM_SIM_VIRTUAL_CONFIG.z_table_authored_baseline_m = atm_z0_m
    LAM_SIM_VIRTUAL_CONFIG.z_baseline_applied_m = atm_z0_m
    LAM_SIM_VIRTUAL_CONFIG.z_slot_delta_m = dict(atm_delta_m)
    LAM_SIM_VIRTUAL_CONFIG.vtm_z_table_authored_baseline_m = vtm_z0_m
    LAM_SIM_VIRTUAL_CONFIG.vtm_z_baseline_applied_m = vtm_z0_m
    LAM_SIM_VIRTUAL_CONFIG.vtm_z_slot_delta_m = dict(vtm_delta_m)
    WAFER_PRIM_BY_SLOT_KEY = load_wafer_prim_by_slot_key()
    ATM_HEIGHT_PRIM_PATH = LAM_SIM_VIRTUAL_CONFIG.atm_height_prim_path
    SLOT_Z_METERS.clear()
    for sk in LAM_SIM_VIRTUAL_CONFIG.z_slot_delta_m:
        zv = LAM_SIM_VIRTUAL_CONFIG.effective_slot_z_m(sk)
        if zv is not None:
            SLOT_Z_METERS[sk] = zv


refresh_lam_sim_runtime_tables_from_config()


def build_default_wafer_prim_paths() -> Dict[str, str]:
    """호환용 별칭: ``lam_wafer_prim_paths.load_wafer_prim_by_slot_key()`` 와 동일."""
    return load_wafer_prim_by_slot_key()


# ---------------------------------------------------------------------------
# 동작별 매크로 — 사용자가 지정한 **함수 이름** (``prompt1.txt``). 스텝 JSON 은 차례로 채움.
# ---------------------------------------------------------------------------

LamSimJsonSteps = List[Dict[str, Any]]
LAM_SIM_LAST_BUILT_JSON: str = ""


def _lam_sim_log_build(context: str, message: str) -> None:
    """스텝 조립 시 현장 점검용 로그(누락 설정·수정 위치 안내)."""
    print(f"{_PRINT_PREFIX} [build:{context}] {message}", flush=True)


def _lam_sim_publish_json(steps: LamSimJsonSteps) -> None:
    global LAM_SIM_LAST_BUILT_JSON
    LAM_SIM_LAST_BUILT_JSON = json.dumps(steps, indent=2, ensure_ascii=False)


def _lam_step_tail(description: str = "") -> Dict[str, Any]:
    return {
        "hide_enabled": False,
        "hide_prims": "",
        "run_with_previous": False,
        "step_delay_ms": 0,
        "description": description,
    }


def _lam_estimate_raw_duration_sec(steps: LamSimJsonSteps) -> float:
    from .lam_types import LAM_FIXED_FPS

    tps = float(LAM_FIXED_FPS)
    total = 0.0
    for st in steps:
        t = str(st.get("type") or "").upper()
        if t in ("MOVE", "ROTATE", "DELAY", "SET_PRIM_VISIBILITY", "PRIM_VISIBILITY", "PRIM_HIDE", "PRIM_SHOW"):
            total += float(st.get("duration", 0.0) or 0.0)
        elif t == "TIMESAMPLES_REPLAY":
            sf = float(st.get("start_frame", 0) or 0)
            ef = float(st.get("end_frame", 0) or 0)
            sp = float(st.get("speed_scale", 1.0) or 1.0)
            if ef > sf:
                total += (ef - sf) / tps / max(0.01, sp)
        elif t == "USD_TIMELINE":
            # simulation_play 조립 스텝에는 넣지 않음. 외부 JSON 이 섞인 경우 길이 추정만 호환.
            sf = float(st.get("start_frame", 0) or 0)
            ef = float(st.get("end_frame", 0) or 0)
            sp = float(st.get("speed_scale", 1.0) or 1.0)
            if ef > sf:
                total += (ef - sf) / tps / max(0.01, sp)
    return max(0.05, total)


def lam_sim_steps_from_json_string(s: str) -> LamSimJsonSteps:
    data = json.loads(s)
    if not isinstance(data, list):
        raise ValueError("JSON 루트는 스텝 배열([...])이어야 합니다.")
    return [dict(x) for x in data]


def run_lam_sim_steps(
    registry: Any,
    scheduler: Any,
    steps: LamSimJsonSteps,
    *,
    target_duration_sec: Optional[float] = None,
    speed_scale: float = 1.0,
) -> None:
    """JSON/매크로에서 만든 스텝 list → **실제 Kit 재생** (단일 진입점).

    내부: ``lam_sequence_engine.LamSequenceRunner.run(steps)`` — MOVE/visibility/TIMESAMPLES 실행.
    ``target_duration_sec``: 매크로 ``duration_sec=`` 로 전체 길이 맞출 때 배속 조정.
    """
    if not steps:
        print(f"{_PRINT_PREFIX} run_lam_sim_steps: 빈 스텝", flush=True)
        return
    for st in steps:
        if str(st.get("type") or "").upper() == "USD_TIMELINE":
            _lam_sim_log_build(
                "runner",
                "스텝에 USD_TIMELINE 이 포함되어 있습니다. simulation_play 의 매크로는 "
                "프레임 재생에 TIMESAMPLES_REPLAY 만 생성합니다. 수동 JSON 이라면 "
                "TIMESAMPLES_REPLAY 로 바꾸거나 본 스텝의 의도를 확인하세요.",
            )
            break
    try:
        from .lam_sequence_engine import LamSequenceRunner
    except Exception as exc:
        print(f"{_PRINT_PREFIX} LamSequenceRunner import 실패: {exc}", flush=True)
        return
    t_raw = _lam_estimate_raw_duration_sec(steps)
    sp = float(max(0.01, speed_scale or 1.0))
    if target_duration_sec is not None and float(target_duration_sec) > 1e-6:
        sp *= max(0.05, t_raw / float(target_duration_sec))
    LamSequenceRunner(registry, scheduler).run(list(steps), reset_each_start=False, speed_scale=sp)


# ---------------------------------------------------------------------------
# 8) 매크로 스크립트 — lam_sim_actions 함수 → build_steps_for_event → run_lam_sim_steps
# ---------------------------------------------------------------------------

# JSON 이벤트 동작 — lam/lam_event_sequences/<이름>.json (lam_sim_actions)
from .lam_event_sequences import (
    LAM_EVENT_NAMES,
    atm_event_name_for_slot,
    build_steps_for_event,
    ensure_event_json_scaffolds,
    event_needs_slot_number,
    format_event_description,
    vtm_event_name_for_slot,
)
from .lam_sim_actions import LAM_SIM_ACTION_CALLABLES, LAM_SIM_MACRO_CALLABLES


def list_macro_function_names() -> List[str]:
    """매크로 스크립트에서 호출 가능한 이벤트 함수 이름 목록."""
    return sorted(LAM_SIM_MACRO_CALLABLES.keys())


def format_macro_call_example(func_name: str) -> str:
    """스크립트 한 줄 예시 문자열."""
    if event_needs_slot_number(func_name):
        return f"{func_name}(slot_number=1)"
    return f"{func_name}()"


def parse_macro_call_line(line: str) -> Tuple[str, Dict[str, Any], Optional[float]]:
    """한 줄 ``atm_foup1_pick(7)`` / ``atm_foup1_pick(slot_number=7)`` 파싱.

    Returns:
        (func_name, kwargs_for_fn, duration_sec_or_none)
    """
    line = line.strip()
    if not line or line.startswith("#"):
        raise ValueError("빈 줄 또는 주석")
    tree = ast.parse(line, mode="eval")
    if not isinstance(tree, ast.Expression):
        raise ValueError("표현식 한 개만 허용합니다.")
    call = tree.body
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
        raise ValueError("함수 호출만 허용합니다. 예: atm_foup1_pick(slot_number=1)")
    func_name = call.func.id
    if func_name not in LAM_SIM_MACRO_CALLABLES:
        known = ", ".join(list_macro_function_names()[:8])
        raise ValueError(
            f"알 수 없는 매크로: {func_name!r}. (등록 예: {known}, … 총 {len(LAM_SIM_MACRO_CALLABLES)}개)"
        )
    kwargs: Dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise ValueError("키워드 인자만 허용합니다 (예: duration_sec=3.0).")
        kwargs[kw.arg] = ast.literal_eval(kw.value)
    if "slot_index" in kwargs and "slot_number" not in kwargs:
        kwargs["slot_number"] = kwargs.pop("slot_index")
    dur = kwargs.pop("duration_sec", None)
    pos = [ast.literal_eval(a) for a in call.args]
    if pos:
        if not event_needs_slot_number(func_name):
            raise ValueError(
                f"{func_name!r} 은 위치 인자를 받지 않습니다. 예: {format_macro_call_example(func_name)}"
            )
        if len(pos) != 1:
            raise ValueError(f"{func_name!r}: 위치 인자는 slot 번호 1개만. 예: {func_name}(7)")
        if "slot_number" in kwargs:
            raise ValueError(f"{func_name!r}: slot_number 를 위치·키워드에 중복 지정했습니다.")
        kwargs["slot_number"] = int(pos[0])
    return func_name, kwargs, (float(dur) if dur is not None else None)


def run_lam_sim_script_line(registry: Any, scheduler: Any, line: str) -> None:
    """한 줄 이벤트 함수 호출 → JSON 스텝 실행."""
    try:
        func_name, kwargs, dur = parse_macro_call_line(line)
    except ValueError as exc:
        print(f"{_PRINT_PREFIX} 매크로 파싱 실패: {line!r} — {exc}", flush=True)
        raise
    fn = LAM_SIM_MACRO_CALLABLES[func_name]
    print(
        f"{_PRINT_PREFIX} 매크로 실행: {line.strip()}  →  {format_event_description(func_name, kwargs.get('slot_number'))}",
        flush=True,
    )
    steps = fn(**kwargs)
    run_lam_sim_steps(
        registry,
        scheduler,
        steps,
        target_duration_sec=dur,
    )


def print_macro_function_catalog() -> None:
    """등록된 46개 이벤트 함수를 콘솔에 출력."""
    names = list_macro_function_names()
    print(f"{_PRINT_PREFIX} === 매크로 함수 목록 ({len(names)}개) ===", flush=True)
    for n in names:
        ex = format_macro_call_example(n)
        sn = 1 if event_needs_slot_number(n) else None
        print(f"{_PRINT_PREFIX}   {ex}  —  {format_event_description(n, sn)}", flush=True)
    print(f"{_PRINT_PREFIX} === 끝 ===", flush=True)


def run_lam_sim_script_text(registry: Any, scheduler: Any, text: str) -> None:
    """여러 줄 — 주석·빈 줄 제외 후 ``run_lam_sim_script_line`` 순차 실행."""
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        run_lam_sim_script_line(registry, scheduler, ln)


# ---------------------------------------------------------------------------
# 9) 초기화 — TBS_OFFSET=0 (CSV 창 [초기화] 버튼)
# ---------------------------------------------------------------------------


def collect_lam_sim_reset_prim_paths(*, script_text: Optional[str] = None) -> List[str]:
    """초기화(reset) 대상 prim — Z stage + (선택) 스크립트 스텝에 등장하는 prim."""
    from .lam_sequence_engine import _collect_prim_paths_for_reset

    refresh_lam_sim_runtime_tables_from_config()
    seen: set[str] = set()
    out: List[str] = []

    def _add(path: str) -> None:
        p = (path or "").strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)

    cfg = LAM_SIM_VIRTUAL_CONFIG
    _add(cfg.atm_height_prim_path or ATM_Z_MOVE_PRIM_PATH)
    _add(cfg.vtm_position_prim_path or VTM_Z_MOVE_PRIM_PATH)

    if script_text:
        for raw in script_text.splitlines():
            ln = raw.strip()
            if not ln or ln.startswith("#"):
                continue
            try:
                func_name, kwargs, _dur = parse_macro_call_line(ln)
                fn = LAM_SIM_MACRO_CALLABLES[func_name]
                steps = fn(**kwargs)
                for p in _collect_prim_paths_for_reset(steps):
                    _add(p)
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} reset: 스크립트 줄 스킵 {ln!r} — {exc}",
                    flush=True,
                )
    return out


def reset_lam_sim_to_initial_state(
    registry: Any = None,
    scheduler: Any = None,
    *,
    script_text: Optional[str] = None,
) -> None:
    """ATM/VTM Z stage 등 TBS_OFFSET 을 0 으로 — 시퀀스 편집기 Reset 과 동일 정책.

    - 진행 중 translate/rotate 애니메이션 중지
    - ``scheduler.stop_all()`` (있을 때)
    - Z MOVE prim + 스크립트에 등장한 MOVE/ROTATE/visibility prim 의 TBS translate·rotate 0
    """
    from . import lam_rotate_animation as _lrx
    from . import lam_translate_animation as _ltx
    from .lam_sequence_engine import _dispatch_main_wait, _reset_tbs_offset_ops_for_paths

    paths = collect_lam_sim_reset_prim_paths(script_text=script_text)
    print(f"{_PRINT_PREFIX} === 초기화 (TBS_OFFSET → 0) ===", flush=True)
    print(f"{_PRINT_PREFIX}   대상 prim {len(paths)}개:", flush=True)
    for p in paths:
        print(f"{_PRINT_PREFIX}     {p}", flush=True)

    try:
        _ltx.stop_all_translate_animations()
        _lrx.stop_all_rotate_animations()
    except Exception as exc:
        print(f"{_PRINT_PREFIX}   애니 중지 경고: {exc}", flush=True)

    if scheduler is not None:
        try:
            stop_fn = getattr(scheduler, "stop_all", None)
            if callable(stop_fn):
                stop_fn()
                print(f"{_PRINT_PREFIX}   scheduler.stop_all() 호출", flush=True)
        except Exception as exc:
            print(f"{_PRINT_PREFIX}   scheduler.stop_all 경고: {exc}", flush=True)

    if paths:
        ok = _dispatch_main_wait(lambda: _reset_tbs_offset_ops_for_paths(paths), timeout=15.0)
        if not ok:
            print(f"{_PRINT_PREFIX}   ⚠ TBS reset main-thread 타임아웃", flush=True)
    else:
        print(f"{_PRINT_PREFIX}   ⚠ reset 대상 prim 없음 — lam_slot_z_config Z MOVE prim 확인", flush=True)

    print(f"{_PRINT_PREFIX} === 초기화 완료 ===", flush=True)


def _virtual_slot_z_m(slot_key: str) -> Optional[float]:
    """가상 타임라인 로그용: 해당 슬롯의 **적용 절대 Z** [m] 또는 미정의 시 None."""
    return LAM_SIM_VIRTUAL_CONFIG.effective_slot_z_m(slot_key)


def _virtual_slot_z_m_vtm(slot_key: str) -> Optional[float]:
    """VTM 구간 로그용: ``effective_vtm_slot_z_m`` 우선, 없으면 ATM ``effective_slot_z_m``."""
    zv = LAM_SIM_VIRTUAL_CONFIG.effective_vtm_slot_z_m(slot_key)
    if zv is not None:
        return zv
    return LAM_SIM_VIRTUAL_CONFIG.effective_slot_z_m(slot_key)


def _virtual_wafer_prim(slot_key: str) -> str:
    """가상 타임라인 로그용: 슬롯(또는 논리 EE)에 대응하는 웨이퍼 prim 경로 문자열."""
    p = WAFER_PRIM_BY_SLOT_KEY.get(slot_key, "")
    return p if p else f"(unset wafer prim for {slot_key})"


def _is_logical_arm_slot(slot_key: str) -> bool:
    """``LOGICAL:ATM_ARM`` / ``LOGICAL:VTM_EE_*`` 처럼 물리 슬롯이 아닌 EE 추적용 키인지."""
    return slot_key.startswith("LOGICAL:")


def _classify_transfer_robot(prev_sk: str, next_sk: str) -> str:
    """연속 dwell 사이 이송 장비(``ATM`` / ``VTM``) 1차 분류.

    Returns:
        ``"ATM"`` 또는 ``"VTM"``. (로그 문자열 및 추후 ``build_steps_for_dwell`` 분기에 사용.)

    규칙(단순 휴리스틱, CSV 가 실제와 다르면 조정 필요):
        - 이전 또는 다음 dwell 이 ``LOGICAL:ATM_ARM`` 이면 ATM (FOUP/버퍼/에어록 ATM 구간 등).
        - 이전 또는 다음 이 ``LOGICAL:VTM_EE_*`` 이거나, ``chamber*`` 가 끼면 VTM.
        - 그 외(예: airlock 만 있는 경우)는 기본 ATM.
    """
    if prev_sk == LOGICAL_SLOT_ATM_ARM or next_sk == LOGICAL_SLOT_ATM_ARM:
        return "ATM"
    if (
        prev_sk in (LOGICAL_SLOT_VTM_EE_L, LOGICAL_SLOT_VTM_EE_R)
        or next_sk in (LOGICAL_SLOT_VTM_EE_L, LOGICAL_SLOT_VTM_EE_R)
    ):
        return "VTM"
    if prev_sk.startswith("chamber") or next_sk.startswith("chamber"):
        return "VTM"
    return "ATM"


def _transfer_event_hint(prev: DwellRecord, curr: DwellRecord) -> str:
    """CSV 이송에 대응하는 event JSON 이름(로그용)."""
    from .lam_event_sequences import atm_event_name_for_slot, vtm_event_name_for_slot

    robot = _classify_transfer_robot(prev.slot_key, curr.slot_key)
    try:
        if robot == "ATM":
            if curr.slot_key == LOGICAL_SLOT_ATM_ARM:
                sk, po = prev.slot_key, "pick"
            elif prev.slot_key == LOGICAL_SLOT_ATM_ARM:
                sk, po = curr.slot_key, "place"
            else:
                sk, po = curr.slot_key, "place"
            name, num = atm_event_name_for_slot(sk, po)
        else:
            pick_into_arm = curr.slot_key in (LOGICAL_SLOT_VTM_EE_L, LOGICAL_SLOT_VTM_EE_R)
            place_from_arm = prev.slot_key in (LOGICAL_SLOT_VTM_EE_L, LOGICAL_SLOT_VTM_EE_R)
            if pick_into_arm:
                target, po = prev.slot_key, "pick"
            elif place_from_arm:
                target, po = curr.slot_key, "place"
            else:
                target = (
                    curr.slot_key
                    if vtm_clip_station_key_for_slot(curr.slot_key)
                    else (
                        prev.slot_key
                        if vtm_clip_station_key_for_slot(prev.slot_key)
                        else curr.slot_key
                    )
                )
                po = "pick"
            hand = (
                _vtm_hand_side_for_transfer(prev.slot_key, curr.slot_key, pick_into_arm=pick_into_arm)
                if pick_into_arm or place_from_arm
                else "left"
            )
            name, num = vtm_event_name_for_slot(target, hand, po)
        if num is not None:
            return f"{name}(slot_number={num})"
        return name
    except ValueError as exc:
        return f"(event unresolved: {exc})"


def log_virtual_timeline_from_dwells(dwells: List[DwellRecord]) -> None:
    """dwell 타임라인 요약 로그 (USD 미기록). 애니 구간은 event JSON 참고."""
    if not LAM_SIM_VIRTUAL_CONFIG.timeline_log_enabled or not dwells:
        return

    refresh_lam_sim_runtime_tables_from_config()
    cfg = LAM_SIM_VIRTUAL_CONFIG
    print(f"{_PRINT_PREFIX} ========== VIRTUAL TIMELINE (no USD write) ==========", flush=True)
    print(
        f"{_PRINT_PREFIX} Z: atm_z_total={cfg.atm_z_total_world_offset_m()!r} "
        f"vtm_z_total={cfg.vtm_z_total_world_offset_m()!r} "
        f"(clips/frames/yaw → lam_event_sequences/*.json)",
        flush=True,
    )

    for i, d in enumerate(dwells):
        t0, t1 = d.start_sec, d.end_sec
        if i == 0:
            print(
                f"{_PRINT_PREFIX} [t={t0:.3f}s] INIT wafer cassette={d.cassette_id} "
                f"first_dwell slot={d.slot_key} module={d.module_nm!r}",
                flush=True,
            )
        else:
            _log_virtual_transfer(dwells[i - 1], d)

        wp = _virtual_wafer_prim(d.slot_key)
        z = _virtual_slot_z_m(d.slot_key)
        z_s = f"z_abs_m={z!r}" if z is not None else "z_abs_m=(none)"

        print(
            f"{_PRINT_PREFIX} [t={t0:.3f}s..{t1:.3f}s] DWELL#{i} cassette={d.cassette_id} "
            f"slot={d.slot_key} module={d.module_nm!r}",
            flush=True,
        )
        print(
            f"{_PRINT_PREFIX}   visibility: wafer ON-SLOT prim={wp!r}",
            flush=True,
        )
        print(f"{_PRINT_PREFIX}   hold {z_s} process_tm={d.process_tm!r}s eqp_id={d.eqp_id!r}", flush=True)

        if _is_logical_arm_slot(d.slot_key):
            robot = "ATM" if d.slot_key == LOGICAL_SLOT_ATM_ARM else "VTM"
            print(
                f"{_PRINT_PREFIX}   idle_on_arm: robot={robot} (animation in event JSON)",
                flush=True,
            )

    last = dwells[-1]
    print(
        f"{_PRINT_PREFIX} [t={last.end_sec:.3f}s] END virtual run cassette={last.cassette_id} "
        f"last_slot={last.slot_key}",
        flush=True,
    )
    print(f"{_PRINT_PREFIX} ========== END VIRTUAL TIMELINE ==========", flush=True)


def _log_virtual_transfer(prev: DwellRecord, curr: DwellRecord) -> None:
    """두 dwell 사이 가상 이송 로그 — 실행 JSON 이름만 표시."""
    t = curr.start_sec
    robot = _classify_transfer_robot(prev.slot_key, curr.slot_key)
    event_hint = _transfer_event_hint(prev, curr)

    print(
        f"{_PRINT_PREFIX} ----- TRANSFER @ t={t:.3f}s robot={robot} "
        f"{prev.slot_key} -> {curr.slot_key} (cassette={curr.cassette_id}) -----",
        flush=True,
    )
    print(
        f"{_PRINT_PREFIX}   event_json → {event_hint}.json (+ auto Z MOVE from code)",
        flush=True,
    )
    print(
        f"{_PRINT_PREFIX}   wafer_prim prev={_virtual_wafer_prim(prev.slot_key)!r} "
        f"next={_virtual_wafer_prim(curr.slot_key)!r}",
        flush=True,
    )
    print(f"{_PRINT_PREFIX} ----- END TRANSFER -----", flush=True)


# ---------------------------------------------------------------------------
# 4) 데이터 구조
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedCsvRow:
    """CSV 한 행에서 시뮬에 필요한 6개 필드만 담는다.

    Attributes:
        eqp_id: 장비/호기 식별 (로그용).
        module_nm: 장비 모듈명. ``MODULE_NM_TO_SLOT_KEY`` 로 ``slot_key`` 로 변환된다.
        cassette_id: 랏 내 웨이퍼 번호 (FOUP1 이면 보통 1~25).
        eqp_start_tm: 이 모듈에 **들어온** 시각 [s] (CSV 원본을 ``parse_time_to_seconds`` 로 변환).
        eqp_end_tm: 이 모듈에서 **나간** 시각 [s].
        process_tm: 공정/체류 시간 열 (현재 로그에만 사용, 추후 스텝 길이에 반영 가능).
    """

    eqp_id: str
    module_nm: str
    cassette_id: int
    eqp_start_tm: float
    eqp_end_tm: float
    process_tm: float


@dataclass(frozen=True)
class DwellRecord:
    """한 웨이퍼가 한 ``slot_key`` 에 머문 시간 구간 [start_sec, end_sec).

    Attributes:
        cassette_id: 어떤 웨이퍼인지 (FOUP 랏 번호 등).
        module_nm: 원본 CSV 모듈명 (추적·로그용).
        slot_key: 내부 고정 슬롯 또는 ``LOGICAL:*`` (팔 위 웨이퍼).
        start_sec, end_sec: 구간 [초].
        process_tm: CSV 의 process_tm (참고).
        eqp_id: CSV 의 eqp_id.
    """

    cassette_id: int
    module_nm: str
    slot_key: str
    start_sec: float
    end_sec: float
    process_tm: float
    eqp_id: str


# ---------------------------------------------------------------------------
# 5) 시간 파싱
# ---------------------------------------------------------------------------


def parse_time_to_seconds(value: Any) -> float:
    """CSV 시간 셀을 초 단위 실수로 변환한다.

    ``TIME_PARSE_MODE == "seconds_float"`` 일 때: 빈 문자열은 0, 그 외 ``float`` 변환.
    향후 분·``HH:MM`` 등은 이 함수에 분기 추가.
    """
    if value is None or (isinstance(value, str) and not str(value).strip()):
        return 0.0
    if TIME_PARSE_MODE == "seconds_float":
        return float(value)
    # TODO: 분/복합 문자열 파서
    return float(value)


# ---------------------------------------------------------------------------
# 6) CSV 로드
# ---------------------------------------------------------------------------


def resolve_csv_path(path: Optional[str] = None) -> Path:
    """인자가 없으면 ``DEFAULT_CSV_PATH`` 를, 있으면 사용자 경로를 ``Path`` 로 정규화한다."""
    return Path(path or DEFAULT_CSV_PATH).expanduser()


def read_csv_rows(csv_path: Path) -> List[ParsedCsvRow]:
    """UTF-8 CSV 를 읽어 ``ParsedCsvRow`` 리스트로 반환한다.

    첫 행은 헤더. 필수 열:
        ``eqp_id``, ``module_nm``, ``cassette_id``, ``eqp_start_tm``, ``eqp_end_tm``, ``process_tm``
    """
    if not csv_path.is_file():
        raise FileNotFoundError(f"{_PRINT_PREFIX} CSV not found: {csv_path}")

    rows: List[ParsedCsvRow] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = (
            "eqp_id",
            "module_nm",
            "cassette_id",
            "eqp_start_tm",
            "eqp_end_tm",
            "process_tm",
        )
        header = reader.fieldnames or ()
        miss = [c for c in required if c not in header]
        if miss:
            raise ValueError(f"{_PRINT_PREFIX} CSV missing columns: {miss}")

        for raw in reader:
            try:
                cid = int(str(raw["cassette_id"]).strip())
            except Exception as exc:
                raise ValueError(f"{_PRINT_PREFIX} bad cassette_id: {raw!r}") from exc
            rows.append(
                ParsedCsvRow(
                    eqp_id=str(raw.get("eqp_id") or "").strip(),
                    module_nm=str(raw.get("module_nm") or "").strip(),
                    cassette_id=cid,
                    eqp_start_tm=parse_time_to_seconds(raw.get("eqp_start_tm")),
                    eqp_end_tm=parse_time_to_seconds(raw.get("eqp_end_tm")),
                    process_tm=parse_time_to_seconds(raw.get("process_tm")),
                )
            )
    return rows


def slot_key_for_module_nm(module_nm: str) -> Optional[str]:
    """``module_nm`` 에 대응하는 ``slot_key`` 를 반환. 맵에 없으면 None (미지원 모듈)."""
    return MODULE_NM_TO_SLOT_KEY.get(module_nm)


def rows_to_dwell_records(rows: Iterable[ParsedCsvRow]) -> List[DwellRecord]:
    """``ParsedCsvRow`` 를 ``DwellRecord`` 로 바꾼다.

    ``module_nm`` 이 ``MODULE_NM_TO_SLOT_KEY`` 에 없으면 해당 행은 건너뛰고 로그만 남긴다.
    ``eqp_end_tm < eqp_start_tm`` 인 행도 스킵.
    """
    out: List[DwellRecord] = []
    for r in rows:
        sk = slot_key_for_module_nm(r.module_nm)
        if sk is None:
            print(f"{_PRINT_PREFIX} skip unknown module_nm={r.module_nm!r}", flush=True)
            continue
        if r.eqp_end_tm < r.eqp_start_tm:
            print(
                f"{_PRINT_PREFIX} skip inverted time cassette={r.cassette_id} mod={r.module_nm}",
                flush=True,
            )
            continue
        out.append(
            DwellRecord(
                cassette_id=r.cassette_id,
                module_nm=r.module_nm,
                slot_key=sk,
                start_sec=r.eqp_start_tm,
                end_sec=r.eqp_end_tm,
                process_tm=r.process_tm,
                eqp_id=r.eqp_id,
            )
        )
    return out


def sort_dwells_for_playback(dwells: List[DwellRecord]) -> List[DwellRecord]:
    """여러 웨이퍼 dwell 을 한 타임라인으로 재생할 때의 정렬 순서.

    정렬 키: ``start_sec`` 오름차순, 같으면 ``cassette_id``, 그다음 ``module_nm``.
    """
    return sorted(dwells, key=lambda d: (d.start_sec, d.cassette_id, d.module_nm))


# ---------------------------------------------------------------------------
# 7) dwell → 시퀀스 step / JSON (CSV 재생은 dwell **간** 이송 스텝)
# ---------------------------------------------------------------------------


def dwell_duration_sec(d: DwellRecord) -> float:
    """``DwellRecord`` 구간 길이 ``end_sec - start_sec`` [s] (0 미만이면 0)."""
    return max(0.0, d.end_sec - d.start_sec)


def _vtm_chamber_index_for_move_slot(slot_key: str) -> int:
    """``vtm_arm_move_to_chamber`` 의 ``chamber_index`` (에어록 등은 더미 1)."""
    sk = (slot_key or "").strip()
    if sk.startswith("chamber"):
        tail = sk[len("chamber") :]
        if tail.isdigit():
            n = int(tail)
            if 1 <= n <= 5:
                return n
    return 1


def _csv_transfer_target_duration_sec(prev: DwellRecord, curr: DwellRecord) -> float:
    """dwell 간 이송에 쓰는 ``duration_sec`` / ``run_lam_sim_steps`` 배속 목표 [s].

    CSV 간격이 매우 짧아도 클립·MOVE 가 지나치게 압축되지 않게 하한을 둔다.
    """
    gap = max(0.0, curr.start_sec - prev.end_sec)
    span = max(dwell_duration_sec(prev), dwell_duration_sec(curr))
    dt = max(1e-6, curr.start_sec - prev.start_sec)
    return max(8.0, dt + span, gap + span)


def build_steps_for_dwell_transfer(prev: DwellRecord, curr: DwellRecord) -> LamSimJsonSteps:
    """연속 dwell (동일 ``cassette_id``) 사이 이송 → ``lam_event_sequences/<이벤트>.json`` 실행."""
    from .lam_event_sequences import atm_event_name_for_slot, build_steps_for_event, vtm_event_name_for_slot

    if prev.cassette_id != curr.cassette_id:
        _lam_sim_log_build(
            "transfer",
            f"이송 생략(cassette 불일치): {prev.cassette_id} -> {curr.cassette_id} "
            f"{prev.slot_key!r} -> {curr.slot_key!r}.",
        )
        return []
    refresh_lam_sim_runtime_tables_from_config()
    robot = _classify_transfer_robot(prev.slot_key, curr.slot_key)
    try:
        if robot == "ATM":
            if curr.slot_key == LOGICAL_SLOT_ATM_ARM:
                sk = prev.slot_key
                po = "pick"
            elif prev.slot_key == LOGICAL_SLOT_ATM_ARM:
                sk = curr.slot_key
                po = "place"
            else:
                sk = curr.slot_key
                po = "place"
            event, num = atm_event_name_for_slot(sk, po)
            steps = build_steps_for_event(
                event,
                slot_number=num,
                vtm_ee_swap=VTM_END_EFFECTOR_SWAP_HANDS,
            )
        else:
            pick_into_arm = curr.slot_key in (LOGICAL_SLOT_VTM_EE_L, LOGICAL_SLOT_VTM_EE_R)
            place_from_arm = prev.slot_key in (LOGICAL_SLOT_VTM_EE_L, LOGICAL_SLOT_VTM_EE_R)
            if pick_into_arm:
                target = prev.slot_key
                hand = _vtm_hand_side_for_transfer(prev.slot_key, curr.slot_key, pick_into_arm=True)
                po = "pick"
            elif place_from_arm:
                target = curr.slot_key
                hand = _vtm_hand_side_for_transfer(prev.slot_key, curr.slot_key, pick_into_arm=False)
                po = "place"
            else:
                target = (
                    curr.slot_key
                    if vtm_clip_station_key_for_slot(curr.slot_key)
                    else (
                        prev.slot_key
                        if vtm_clip_station_key_for_slot(prev.slot_key)
                        else curr.slot_key
                    )
                )
                hand = "left"
                po = "pick"
            event, num = vtm_event_name_for_slot(target, hand, po)
            steps = build_steps_for_event(
                event,
                slot_number=num,
                vtm_ee_swap=VTM_END_EFFECTOR_SWAP_HANDS,
            )
    except (ValueError, FileNotFoundError) as exc:
        _lam_sim_log_build(
            "transfer",
            f"이송 이벤트 실패 {prev.slot_key!r}->{curr.slot_key!r}: {exc}",
        )
        return []

    if not steps:
        _lam_sim_log_build(
            "transfer",
            f"이송 스텝 0개: {prev.slot_key!r}->{curr.slot_key!r} — "
            f"lam/lam_event_sequences/*.json 및 prim/Z 설정 확인.",
        )
    return steps


def scale_speed_factor(*, t_raw: float, T_target: float) -> float:
    """스텝 합산 재생 시간 ``t_raw`` [s] 를 목표 ``T_target`` [s] 에 맞출 배속 계수.

    ``LamSequenceRunner`` 의 ``speed_scale`` 에 넣을 때: ``t_raw / T_target``.
    ``t_raw`` 가 크면 1 초과(빨리), 작으면 1 미만(느리게).
    """
    if T_target <= 0.0:
        return 1.0
    if t_raw <= 0.0:
        return 1.0
    return t_raw / T_target


def build_steps_for_dwell(
    d: DwellRecord,
    *,
    total_duration_sec: float,
) -> List[Dict[str, Any]]:
    """단일 dwell **내부** 동작만 스텝으로 쪼갤 때 (공정 중 움직임 등). 현재는 빈 리스트.

    CSV 재생의 로봇 이송은 **연속 dwell 사이** ``build_steps_for_dwell_transfer`` 로 생성한다.
    """
    _ = (d, total_duration_sec)
    return []


def build_sequence_json_for_dwells(dwells: List[DwellRecord]) -> Dict[str, Any]:
    """시퀀스 에디터 호환 JSON (메모리 또는 파일 저장용).

    TODO: 스키마 확정 후 ``steps`` / 메타 필드 채움.
    """
    return {
        "_format": "lam_simulation_sequence_v0",
        "source": "simulation_play.py",
        "dwell_count": len(dwells),
        "steps": [],  # flatten later
    }


# ---------------------------------------------------------------------------
# 8) 동작 레지스트리 (문서 §6.1 항목 1) — 이름 → 함수
# ---------------------------------------------------------------------------

ActionFn = Callable[[DwellRecord, float], List[Dict[str, Any]]]


def _action_placeholder(d: DwellRecord, T: float) -> List[Dict[str, Any]]:
    """등록만 해 둔 자리. 실제 동작별로 ``build_steps_for_dwell`` 변형을 연결."""
    return build_steps_for_dwell(d, total_duration_sec=T)


ACTION_REGISTRY: Dict[str, ActionFn] = {
    "default": _action_placeholder,
    # TODO: "airlock_pick", "chamber_process", ... module_nm 또는 (module_nm, 이전슬롯) 복합키
}


def resolve_action_for_dwell(d: DwellRecord) -> ActionFn:
    """dwell 마다 쓸 함수 선택. 현재는 전부 ``default``."""
    _ = d
    return ACTION_REGISTRY["default"]


# ---------------------------------------------------------------------------
# 9) Kit 연동 — ``LamSequenceRunner`` (TODO: registry / scheduler 주입)
# ---------------------------------------------------------------------------


def run_simulation_from_csv(
    registry: Any,
    scheduler: Any,
    *,
    csv_path: Optional[str] = None,
    speed_scale: float = 1.0,
) -> None:
    """CSV dwell 타임라인을 읽고, dwell 간 이송 스텝을 합쳐 ``LamSequenceRunner.run`` 으로 재생한다.

    동작:
        1. ``refresh_lam_sim_runtime_tables_from_config()`` 로 Prim/Z 캐시 갱신.
        2. dwell 파싱·정렬 후 ``log_virtual_timeline_from_dwells`` 로 로그 출력.
        3. 인접 dwell 쌍마다 ``build_steps_for_dwell_transfer`` 로 스텝 생성 후 Runner 호출.

    Args:
        registry: Kit ``AnimationInstanceRegistry`` (확장에서 주입).
        scheduler: ``PlaybackScheduler``.
        csv_path: None 이면 ``DEFAULT_CSV_PATH`` 또는 환경변수 ``LAM_SIM_CSV``.
        speed_scale: Runner 에 넘길 배속 하한 0.01 클램프 적용 값.

    Note:
        UI 스레드 안전을 위해 **백그라운드 스레드**에서 호출할 것.
    """
    path = resolve_csv_path(csv_path)
    refresh_lam_sim_runtime_tables_from_config()
    raw_rows = read_csv_rows(path)
    dwells = sort_dwells_for_playback(rows_to_dwell_records(raw_rows))
    print(f"{_PRINT_PREFIX} loaded dwells={len(dwells)} from {path}", flush=True)
    log_virtual_timeline_from_dwells(dwells)

    all_steps: List[Dict[str, Any]] = []
    if len(dwells) < 2:
        print(
            f"{_PRINT_PREFIX} Play: dwell {len(dwells)}개 — 이송 구간이 없어 Runner 를 호출하지 않습니다.",
            flush=True,
        )
    else:
        for i in range(1, len(dwells)):
            prev, curr = dwells[i - 1], dwells[i]
            all_steps.extend(build_steps_for_dwell_transfer(prev, curr))

    if not all_steps:
        print(
            f"{_PRINT_PREFIX} Play: 이송 스텝이 비어 있음 (dwell={len(dwells)}, event JSON·prim·Z 확인).",
            flush=True,
        )
        return

    # 지연 import — Kit 밖에서 모듈 로드 시 omni 실패 방지.
    try:
        from .lam_sequence_engine import LamSequenceRunner
    except Exception as exc:
        print(f"{_PRINT_PREFIX} LamSequenceRunner import failed: {exc}", flush=True)
        return

    runner = LamSequenceRunner(registry, scheduler)
    n_ts_all = sum(1 for s in all_steps if str(s.get("type")).upper() == "TIMESAMPLES_REPLAY")
    n_del_all = sum(1 for s in all_steps if str(s.get("type")).upper() == "DELAY")
    n_move = sum(1 for s in all_steps if str(s.get("type")).upper() == "MOVE")
    n_rot = sum(1 for s in all_steps if str(s.get("type")).upper() == "ROTATE")
    n_vis = sum(
        1
        for s in all_steps
        if str(s.get("type")).upper() in ("SET_PRIM_VISIBILITY", "PRIM_VISIBILITY")
    )
    print(
        f"{_PRINT_PREFIX} Play: LamSequenceRunner.run total_steps={len(all_steps)} "
        f"TIMESAMPLES_REPLAY={n_ts_all} DELAY={n_del_all} MOVE={n_move} ROTATE={n_rot} VIS={n_vis} "
        f"speed_scale={speed_scale!r}",
        flush=True,
    )
    if n_ts_all == 0:
        _lam_sim_log_build(
            "csv_play",
            "이 CSV 런에 TIMESAMPLES_REPLAY 스텝이 없습니다. "
            "`lam/lam_event_sequences/<이벤트>.json` 에 재생 구간을 추가하세요.",
        )
    runner.run(all_steps, reset_each_start=False, speed_scale=float(max(0.01, speed_scale)))


# ---------------------------------------------------------------------------
# 10) Kit UI — ``lam/csv/*.csv`` 선택 + Play (스켈레톤)
# ---------------------------------------------------------------------------


def _read_combo_index(combo: Any) -> int:
    """Kit ``ui.ComboBox`` 의 현재 선택 인덱스를 정수로 읽는다.

    ``get_item_value_model`` 계열 API 차이를 흡수하기 위해 여러 getter 를 시도.
    실패 시 0.
    """
    try:
        m = combo.model
    except Exception:
        return 0
    for getter in (
        lambda: m.get_item_value_model(),
        lambda: m.get_item_value_model(None, 0),
    ):
        try:
            inner = getter()
            if inner is not None:
                return int(inner.as_int)
        except Exception:
            continue
    return 0


class LamSimulationCsvPlayWindow:
    """Kit ``omni.ui`` 창: CSV dwell 재생 + 매크로 스크립트 + **초기화**.

    | 버튼 | 코드 |
    |------|------|
    | CSV Play | ``run_simulation_from_csv`` |
    | 스크립트 실행 | ``run_lam_sim_script_text`` |
    | 초기화 | ``reset_lam_sim_to_initial_state`` (Z prim TBS→0) |
    """

    WINDOW_TITLE = "LAM CSV 시뮬 재생"

    def __init__(self, registry: Any, scheduler: Any) -> None:
        """Args: ``registry`` / ``scheduler`` 는 ``run_simulation_from_csv`` · ``run_lam_sim_steps`` 에 전달."""
        self._registry = registry
        self._scheduler = scheduler
        self._window: Any = None
        self._combo: Any = None
        self._csv_paths: List[Path] = []
        self._log_label: Any = None
        self._script_model: Any = None
        self._func_combo: Any = None

    def destroy(self) -> None:
        """윈도우·콤보·로그 위젯 참조를 해제한다 (``lam_window`` 종료 시 호출)."""
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass
        self._window = None
        self._combo = None
        self._log_label = None
        self._script_model = None
        self._func_combo = None

    def _log(self, msg: str) -> None:
        print(f"{_PRINT_PREFIX} {msg}", flush=True)
        try:
            if self._log_label is not None:
                self._log_label.text = msg
        except Exception:
            pass

    def show(self) -> None:
        try:
            import omni.ui as ui  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.ui not available: {exc}", flush=True)
            return

        if self._window is not None:
            try:
                self._window.visible = True
                return
            except Exception:
                self._window = None

        self._csv_paths = list_lam_csv_paths()
        macro_names = list_macro_function_names()
        self._window = ui.Window(self.WINDOW_TITLE, width=600, height=680)
        with self._window.frame:
            with ui.VStack(spacing=6):
                ui.Label(
                    "CSV: dwell [Play] | 매크로: ``atm_foup1_pick(7)`` 또는 ``atm_foup1_pick(slot_number=7)`` "
                    "(한 줄 = 함수 하나, JSON 은 lam/lam_event_sequences/)",
                    word_wrap=True,
                    height=36,
                )
                ui.Label(f"폴더: {get_lam_csv_dir()}", height=20, word_wrap=True)
                if not self._csv_paths:
                    ui.Label("CSV 없음 — ``lam/csv`` 에 ``.csv`` 추가.", height=28)
                else:
                    names = [p.name for p in self._csv_paths]
                    ui.Label("CSV 파일", height=16)
                    self._combo = ui.ComboBox(0, *names, width=520, height=26)
                with ui.HStack(spacing=6, height=28):
                    ui.Button(
                        "목록 새로고침",
                        width=120,
                        clicked_fn=self._on_refresh_clicked,
                    )
                    ui.Button("CSV Play", width=90, clicked_fn=self._on_play_clicked)
                    ui.Spacer()
                ui.Separator()
                with ui.CollapsableFrame(f"이벤트 함수 목록 ({len(macro_names)}개)", height=0):
                    with ui.VStack(spacing=4, padding=4):
                        ui.Label(
                            "아래 ComboBox 에서 고른 뒤 [스크립트에 삽입] 또는 콘솔 [목록 출력].",
                            word_wrap=True,
                            height=28,
                        )
                        with ui.HStack(spacing=4, height=28):
                            if macro_names:
                                self._func_combo = ui.ComboBox(0, *macro_names)
                            else:
                                ui.Label("(등록된 함수 없음)", height=0)
                            ui.Button(
                                "스크립트에 삽입",
                                width=110,
                                clicked_fn=self._on_insert_macro_fn_clicked,
                            )
                            ui.Button(
                                "목록 콘솔 출력",
                                width=110,
                                clicked_fn=self._on_print_macro_catalog_clicked,
                            )
                ui.Label("매크로 스크립트 (한 줄 = 호출 하나, ``#`` 주석)", height=18)
                try:
                    from omni.ui import SimpleStringModel  # type: ignore
                except Exception:
                    SimpleStringModel = None  # type: ignore
                if SimpleStringModel is not None:
                    self._script_model = SimpleStringModel(
                        "atm_foup1_pick(1)\n"
                        "# atm_foup1_pick(slot_number=1) 동일\n"
                        "vtm_chamber1_right_pick()\n"
                        "# duration_sec=3.0  선택(재생 배속)\n"
                    )
                    try:
                        ui.StringField(
                            model=self._script_model,
                            height=120,
                            multiline=True,
                        )
                    except TypeError:
                        ui.StringField(model=self._script_model, height=120)
                else:
                    ui.Label("SimpleStringModel 없음 — Kit 버전 확인.", height=40)
                with ui.HStack(spacing=6, height=28):
                    ui.Button("스크립트 실행", width=120, clicked_fn=self._on_script_run_clicked)
                    ui.Button(
                        "초기화",
                        width=90,
                        clicked_fn=self._on_init_clicked,
                        tooltip="Z stage TBS_OFFSET=0, 애니 중지, 스크립트 prim 복귀",
                    )
                    ui.Spacer()
                self._log_label = ui.Label("(대기)", height=80, word_wrap=True)

    def _on_refresh_clicked(self) -> None:
        """창을 닫았다가 다시 열어 ``lam/csv`` 목록을 재스캔."""
        self.destroy()
        self.show()

    def _on_play_clicked(self) -> None:
        if not self._csv_paths:
            self._log("CSV 없음 — lam/csv 에 파일을 추가하세요.")
            return
        if self._combo is None:
            self._log("Combo 없음")
            return
        idx = _read_combo_index(self._combo)
        idx = max(0, min(idx, len(self._csv_paths) - 1))
        path = self._csv_paths[idx]
        self._log(f"Play 시작 (thread): {path.name}")

        def _worker() -> None:
            try:
                run_simulation_from_csv(
                    self._registry,
                    self._scheduler,
                    csv_path=str(path),
                )
                print(f"{_PRINT_PREFIX} play thread finished: {path.name}", flush=True)
            except Exception as exc:
                print(f"{_PRINT_PREFIX} play thread error: {exc}", flush=True)

        threading.Thread(target=_worker, daemon=True, name="lam-sim-csv-play").start()

    def _read_script_editor_text(self) -> str:
        m = self._script_model
        if m is None:
            return ""
        try:
            v = getattr(m, "as_string", None)
            if v is not None:
                return str(v)
        except Exception:
            pass
        try:
            fn = getattr(m, "get_value_as_string", None)
            if callable(fn):
                return str(fn() or "")
        except Exception:
            pass
        return ""

    def _on_print_macro_catalog_clicked(self) -> None:
        print_macro_function_catalog()
        self._log(f"함수 목록 {len(list_macro_function_names())}개 — 콘솔 확인")

    def _on_insert_macro_fn_clicked(self) -> None:
        if self._func_combo is None or self._script_model is None:
            self._log("함수 ComboBox 없음")
            return
        idx = _read_combo_index(self._func_combo)
        names = list_macro_function_names()
        if not (0 <= idx < len(names)):
            return
        line = format_macro_call_example(names[idx]) + "\n"
        try:
            cur = self._read_script_editor_text()
            new = (cur + ("\n" if cur and not cur.endswith("\n") else "") + line).lstrip("\n")
            self._script_model.set_value(new)
        except Exception:
            try:
                self._script_model.set_value_as_string(line)  # type: ignore[attr-defined]
            except Exception as exc:
                self._log(f"삽입 실패: {exc}")
                return
        self._log(f"삽입: {line.strip()}")

    def _on_script_run_clicked(self) -> None:
        text = self._read_script_editor_text()
        if not text.strip():
            self._log("스크립트가 비어 있습니다.")
            return
        self._log("스크립트 실행 시작 (thread)…")

        def _worker() -> None:
            try:
                run_lam_sim_script_text(self._registry, self._scheduler, text)
                self._log("스크립트 실행 완료 (콘솔 로그 확인).")
            except Exception as exc:
                err = f"스크립트 오류: {exc}"
                print(f"{_PRINT_PREFIX} {err}", flush=True)
                self._log(err)

        threading.Thread(target=_worker, daemon=True, name="lam-sim-macro-script").start()

    def _on_init_clicked(self) -> None:
        text = self._read_script_editor_text()
        self._log("초기화 시작 (TBS_OFFSET → 0)…")

        def _worker() -> None:
            try:
                reset_lam_sim_to_initial_state(
                    self._registry,
                    self._scheduler,
                    script_text=text,
                )
                self._log("초기화 완료 — Z/팔 TBS 0, 애니 중지 (콘솔 확인).")
            except Exception as exc:
                err = f"초기화 오류: {exc}"
                print(f"{_PRINT_PREFIX} {err}", flush=True)
                self._log(err)

        threading.Thread(target=_worker, daemon=True, name="lam-sim-init-reset").start()


# ---------------------------------------------------------------------------
# 11) CLI / 스모크 (Kit 없이 CSV 파싱만 검증)
# ---------------------------------------------------------------------------


def dry_run_print_dwells(csv_path: Optional[str] = None, *, limit: int = 50) -> List[DwellRecord]:
    """CSV 를 읽어 dwell 요약을 표준 출력에 찍고, dwell 리스트를 그대로 반환한다.

    Kit 없이 ``python simulation_play.py some.csv`` 로 파싱 검증할 때 사용.
    ``limit`` 을 넘는 뒷부분은 ``...`` 한 줄로만 표시.
    """
    path = resolve_csv_path(csv_path)
    dwells = sort_dwells_for_playback(rows_to_dwell_records(read_csv_rows(path)))
    for i, d in enumerate(dwells[:limit]):
        print(
            f"{_PRINT_PREFIX} [{i}] cassette={d.cassette_id} slot={d.slot_key} "
            f"[{d.start_sec},{d.end_sec}) module={d.module_nm!r}",
            flush=True,
        )
    if len(dwells) > limit:
        print(f"{_PRINT_PREFIX} ... ({len(dwells) - limit} more)", flush=True)
    return dwells


__all__ = [
    "DEFAULT_CSV_PATH",
    "TIME_PARSE_MODE",
    "VTM_END_EFFECTOR_SWAP_HANDS",
    "MODULE_NM_TO_SLOT_KEY",
    "WAFER_PRIM_BY_SLOT_KEY",
    "SLOT_Z_METERS",
    "ATM_HEIGHT_PRIM_PATH",
    "atm_clip_station_key_for_slot",
    "vtm_clip_station_key_for_slot",
    "LamSimPlayVirtualConfig",
    "LAM_SIM_VIRTUAL_CONFIG",
    "default_lam_sim_virtual_config",
    "refresh_lam_sim_runtime_tables_from_config",
    "log_virtual_timeline_from_dwells",
    "LamSimJsonSteps",
    "LAM_SIM_LAST_BUILT_JSON",
    "run_lam_sim_steps",
    "lam_sim_steps_from_json_string",
    "run_lam_sim_script_line",
    "run_lam_sim_script_text",
    "LAM_SIM_MACRO_CALLABLES",
    "list_macro_function_names",
    "format_macro_call_example",
    "parse_macro_call_line",
    "print_macro_function_catalog",
    "build_steps_for_event",
    "ensure_event_json_scaffolds",
    "ParsedCsvRow",
    "DwellRecord",
    "_find_lam_data_root",
    "get_lam_csv_dir",
    "list_lam_csv_paths",
    "build_default_module_nm_to_slot_key",
    "rebuild_module_nm_slot_mapping",
    "build_default_wafer_prim_paths",
    "load_wafer_prim_by_slot_key",
    "parse_time_to_seconds",
    "resolve_csv_path",
    "read_csv_rows",
    "rows_to_dwell_records",
    "sort_dwells_for_playback",
    "slot_key_for_module_nm",
    "dwell_duration_sec",
    "build_steps_for_dwell_transfer",
    "scale_speed_factor",
    "build_steps_for_dwell",
    "build_sequence_json_for_dwells",
    "ACTION_REGISTRY",
    "resolve_action_for_dwell",
    "run_simulation_from_csv",
    "dry_run_print_dwells",
    "collect_lam_sim_reset_prim_paths",
    "reset_lam_sim_to_initial_state",
    "LamSimulationCsvPlayWindow",
]


if __name__ == "__main__":
    # python -m morph.lam_control.simulation_play  (repo PYTHONPATH 설정 시)
    import sys

    p = sys.argv[1] if len(sys.argv) > 1 else None
    dwells = dry_run_print_dwells(p)
    log_virtual_timeline_from_dwells(dwells)
