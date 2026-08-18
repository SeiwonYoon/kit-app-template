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
import re
import time
from datetime import datetime
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
import weakref

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


from .lam_data_paths import resolve_local_data_path_or_default


def get_lam_csv_dir() -> Path:
    """시뮬 dwell CSV 디렉터리 ``data/csv`` 경로를 반환한다.

    폴더가 없으면 생성을 시도한다(실패해도 Path 는 반환).
    """
    d = resolve_local_data_path_or_default("csv")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def list_csv_paths_in_directory(directory: str | Path) -> List[Path]:
    """지정 폴더의 ``*.csv`` 파일 경로 목록 (파일명 대소문자 무시 정렬)."""
    d = Path(directory).expanduser()
    if not d.is_dir():
        return []
    return sorted(d.glob("*.csv"), key=lambda p: p.name.lower())


def list_lam_csv_paths() -> List[Path]:
    """``lam/csv`` 이하의 ``*.csv`` 파일 경로 목록을 파일명(대소문자 무시)순으로 반환한다."""
    return list_csv_paths_in_directory(get_lam_csv_dir())


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
# CSV Play 콘솔: True 이면 한 줄 요약만 (진행시간·동작·JSON·실행여부)
_csv_play_compact_log: bool = False
# compact 로그 중첩 카운트 (화면1·2 병렬 빌드 시 한쪽 finally 가 다른 쪽을 끄지 않게)
_csv_play_compact_log_refs: int = 0
_csv_play_compact_log_lock = threading.Lock()
# CSV Play 빌드 배치: True 이면 ensure/refresh 를 plan 시작 시 1회만 (이벤트 빌드 가속)
_csv_bulk_build_active: bool = False
# path+mtime → 재생 계획 캐시 (Kit 세션 메모리, 파일 수정 시 무효화)
_csv_playback_cache: Dict[str, "CachedCsvPlayback"] = {}
_csv_playback_cache_lock = threading.Lock()
# CSV/매크로 재생: event JSON 안의 **모든 스텝** (MOVE·ROTATE·DELAY·visibility·TIMESAMPLES_REPLAY 등)을
# ``LamSequenceRunner`` 가 순서대로 실행. 팔 프레임 클립은 TIMESAMPLES_REPLAY 가 있을 때만 추가 재생.
LOGICAL_SLOT_ATM_ARM: str = "LOGICAL:ATM_ARM"
LOGICAL_SLOT_VTM_EE_L: str = "LOGICAL:VTM_EE_L"
LOGICAL_SLOT_VTM_EE_R: str = "LOGICAL:VTM_EE_R"


# ---------------------------------------------------------------------------
# 1b) CSV ``module_nm`` → ``slot_key`` (EAP 실무 문자열 + prompt1 §332–363)
# ---------------------------------------------------------------------------


def build_default_module_nm_to_slot_key() -> Dict[str, str]:
    """CSV ``module_nm`` → 내부 ``slot_key``. CoolStationAL3/4 → buffer, AL1 → cooling."""
    ee_l = LOGICAL_SLOT_VTM_EE_R if VTM_END_EFFECTOR_SWAP_HANDS else LOGICAL_SLOT_VTM_EE_L
    ee_r = LOGICAL_SLOT_VTM_EE_L if VTM_END_EFFECTOR_SWAP_HANDS else LOGICAL_SLOT_VTM_EE_R
    m: Dict[str, str] = {
        "AtmArm-EndEffector11": LOGICAL_SLOT_ATM_ARM,
        "AtmArm-EndEfferctor11": LOGICAL_SLOT_ATM_ARM,
        "TransferChamber-EndEffector1": ee_l,
        "TransferChamber-EndEffector2": ee_r,
    }
    # for i in (1, 2):
    #     m[f"AirLock1-iSlot{i}"] = f"airlock1_{i}"
    #     m[f"AirLock2-iSlot{i}"] = f"airlock2_{i}"
    #     m[f"AirLock2-oSlot{i}"] = f"airlock2_{i}"

    m["AirLock1-iSlot1"] = "airlock1_1"
    m["AirLock1-iSlot2"] = "airlock1_2"
    m["AirLock1-oSlot1"] = "airlock1_2"
    m["AirLock1-oSlot2"] = "airlock1_1"

    m["AirLock2-iSlot1"] = "airlock2_1"
    m["AirLock2-iSlot2"] = "airlock2_2"
    m["AirLock2-oSlot1"] = "airlock2_2"
    m["AirLock2-oSlot2"] = "airlock2_1"

    for i in range(1, 8):
        m[f"CoolStationAL1PML{i}"] = f"cooling_{i}"
    for i in range(1, 26):
        m[f"CoolStationAL3PML{i}"] = f"buffer3_{i}"
        m[f"CoolStationAL4PML{i}"] = f"buffer4_{i}"
    for i in range(1, 6):
        m[f"PM{i}-PML1"] = f"chamber{i}"
        m[f"PM{i}PML1"] = f"chamber{i}"
    for foup_n in (1, 2, 3):
        for slot in range(1, 26):
            m[f"ATM-FOUP{foup_n}-iSlot{slot}"] = f"foup{foup_n}_{slot}"
    return m


MODULE_NM_TO_SLOT_KEY: Dict[str, str] = build_default_module_nm_to_slot_key()


def rebuild_module_nm_slot_mapping() -> None:
    """``VTM_END_EFFECTOR_SWAP_HANDS`` 변경 후 VTM EE 매핑 재생성."""
    global MODULE_NM_TO_SLOT_KEY
    MODULE_NM_TO_SLOT_KEY = build_default_module_nm_to_slot_key()


def parse_module_nm_to_slot_key(module_nm: str) -> Optional[str]:
    """``module_nm`` → ``slot_key`` (고정 dict + CoolStation/PM/Airlock 정규식)."""
    nm = (module_nm or "").strip()
    if not nm:
        return None
    sk = MODULE_NM_TO_SLOT_KEY.get(nm)
    if sk:
        return sk
    m = re.fullmatch(r"CoolStationAL(\d+)PML?(\d+)", nm, re.IGNORECASE)
    if m:
        al_n, slot_n = int(m.group(1)), int(m.group(2))
        if al_n in (3, 4):
            return f"buffer{al_n}_{slot_n}"
        return f"cooling_{slot_n}"
    m = re.fullmatch(r"PM(\d+)-PML\d+", nm, re.IGNORECASE)
    if m:
        return f"chamber{int(m.group(1))}"
    m = re.fullmatch(r"PM(\d+)PML\d+", nm, re.IGNORECASE)
    if m:
        return f"chamber{int(m.group(1))}"
    m = re.fullmatch(r"AirLock(\d+)-[io]Slot(\d+)", nm, re.IGNORECASE)
    if m:
        return f"airlock{int(m.group(1))}_{int(m.group(2))}"
    m = re.fullmatch(r"TransferChamber-EndEffector(\d+)", nm, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        return LOGICAL_SLOT_VTM_EE_R if VTM_END_EFFECTOR_SWAP_HANDS and n == 1 else (
            LOGICAL_SLOT_VTM_EE_L if VTM_END_EFFECTOR_SWAP_HANDS and n == 2 else (
                LOGICAL_SLOT_VTM_EE_L if n == 1 else LOGICAL_SLOT_VTM_EE_R
            )
        )
    low = re.sub(r"[^a-z0-9]", "", nm.lower())
    if "atmarm" in low and "effect" in low:
        return LOGICAL_SLOT_ATM_ARM
    return None


def slot_key_for_module_nm(module_nm: str) -> Optional[str]:
    """``module_nm`` → ``slot_key`` (``parse_module_nm_to_slot_key`` 별칭)."""
    return parse_module_nm_to_slot_key(module_nm)


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
    | ``lam_sim_z_slot_move_duration_sec`` | 자동 Z MOVE 시간 [s] (기본 **0.3**) | **본 필드** |
    | ``atm_z_usd_world_offset_m`` | 전 슬롯 Z 보정 (TBS/mm) | **본 필드** |
    | ``z_slot_delta_m`` | [m] 테이블 (레거시) | ``load_atm_z_tables()`` |

    프레임·Yaw·visibility 는 각 ``lam/lam_event_sequences/*.json``.
    """

    timeline_log_enabled: bool = True
    atm_height_prim_path: str = "/World/LAM/ATM/HeightStage"
    vtm_position_prim_path: str = "/World/LAM/_VIRTUAL/VTM/ZStage"
    lam_sim_z_slot_move_duration_sec: float = 0.3  # Z 동시 이동 duration — build_steps_for_event 가 사용
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
        lam_sim_z_slot_move_duration_sec=0.3,
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


def set_csv_playback_compact_log(enabled: bool) -> None:
    """CSV Play 중 상세 로그 억제 (LAM/EVENT, LAM/SEQ, dwell 타임라인 등).

    중첩 호출을 지원한다 — 병렬 화면 빌드에서 ``True`` 두 번·``False`` 두 번이
    섞여도 마지막 ``False`` 전까지 compact 가 유지된다.
    """
    global _csv_play_compact_log, _csv_play_compact_log_refs
    with _csv_play_compact_log_lock:
        if enabled:
            _csv_play_compact_log_refs += 1
        else:
            _csv_play_compact_log_refs = max(0, _csv_play_compact_log_refs - 1)
        _csv_play_compact_log = _csv_play_compact_log_refs > 0


def is_csv_playback_compact_log() -> bool:
    return _csv_play_compact_log


def is_csv_bulk_build_active() -> bool:
    return _csv_bulk_build_active


def _post_kit_main_thread(fn: Callable[[], None]) -> None:
    """백그라운드·워커 스레드 → Kit update tick 에서 UI 갱신.

    ``lam_sequence_engine._dispatch_main`` 과 동일 (``update_event_stream`` 구독).
    omni.ui 를 워커 스레드에서 직접 호출하면 크래시하므로 ``post_timer`` 는 쓰지 않는다.
    """
    try:
        from .lam_sequence_engine import _dispatch_main

        _dispatch_main(fn)
        return
    except Exception as exc:
        print(
            f"{_PRINT_PREFIX} UI dispatch 실패: {exc}",
            flush=True,
        )


class _ThrottledBuildProgress:
    """빌드 진행 콜백 — 최소 간격으로만 호출 (빌드 속도 영향 최소)."""

    __slots__ = ("_total", "_done", "_on_tick", "_min_interval", "_last_tick", "_lock")

    def __init__(
        self,
        total: int,
        on_tick: Callable[[int, int], None],
        *,
        min_interval_sec: float = 0.12,
    ) -> None:
        self._total = max(1, int(total))
        self._done = 0
        self._on_tick = on_tick
        self._min_interval = float(min_interval_sec)
        self._last_tick = 0.0
        self._lock = threading.Lock()

    def tick(self, n: int = 1) -> None:
        with self._lock:
            self._done = min(self._total, self._done + max(1, int(n)))
            now = time.monotonic()
            if (
                self._done < self._total
                and (now - self._last_tick) < self._min_interval
            ):
                return
            self._last_tick = now
            done, total = self._done, self._total
        try:
            self._on_tick(done, total)
        except Exception:
            pass

    def finish(self) -> None:
        with self._lock:
            self._done = self._total
            done, total = self._done, self._total
        try:
            self._on_tick(done, total)
        except Exception:
            pass


@dataclass
class CachedCsvPlayback:
    """CSV 파싱 + ``build_csv_playback_plan`` 결과 (Play 즉시 사용)."""

    path: Path
    mtime_ns: int
    size: int
    config_tag: str
    dwells: List["DwellRecord"]
    schedule: List["CsvPlaybackScheduleEntry"]
    blocks: List["CsvTimedPlaybackBlock"]
    build_ms: float = 0.0
    # Federation/파서가 확정한 FOUP 사용 개수. None 이면 dwells 에서 계산.
    used_foup_count: Optional[int] = None
    # 점유 스케줄러 진단 (플래그 False 이면 항상 빈 튜플 — 기존과 동일)
    occupancy_diagnostics: Tuple[str, ...] = ()


def _csv_playback_config_tag() -> str:
    # occ_order_swap: 점유/팔홀딩 충돌 시 순서 swap 만 (통째 시프트 아님)
    tag = f"vtm_swap={int(VTM_END_EFFECTOR_SWAP_HANDS)}"
    try:
        from .lam_csv_occupancy_scheduler import (
            csv_playback_plan_mode,
            occupancy_scheduler_enabled,
        )

        mode = csv_playback_plan_mode()
        # atm_end_foup=2: FOUP(웨이퍼)별 AtmArm-last place + 사이 타 ATM defer
        tag = f"{tag}|plan={mode}|atm_end_foup=2"
        if occupancy_scheduler_enabled():
            tag = f"{tag}|occ_order_swap=2|occ=1"
    except Exception:
        tag = f"{tag}|occ_order_swap=2|atm_end_foup=2"
    tag = f"{tag}|json_t0=1"
    return tag


def _maybe_apply_occupancy_scheduler(
    dwells: List["DwellRecord"],
    schedule: List["CsvPlaybackScheduleEntry"],
    blocks: List["CsvTimedPlaybackBlock"],
) -> Tuple[
    List["CsvPlaybackScheduleEntry"],
    List["CsvTimedPlaybackBlock"],
    Tuple[str, ...],
]:
    """플래그 True 일 때만 plan 후처리. False 이면 인자 그대로 반환."""
    try:
        from .lam_csv_occupancy_scheduler import (
            apply_occupancy_scheduler,
            occupancy_scheduler_enabled,
        )
    except Exception:
        return schedule, blocks, ()
    if not occupancy_scheduler_enabled():
        return schedule, blocks, ()
    try:
        sch, blk, diags = apply_occupancy_scheduler(dwells, schedule, blocks)
        sch, blk = _apply_csv_playback_arm_serial_rules(sch, blk, dwells=dwells)
        assert blk is not None
        sch, blk = _enforce_aligner_absolute_rules(sch, blk)
        assert blk is not None
        sch, blk = _ensure_buffer_to_foup_absolute_rules(sch, blk, dwells=dwells)
        assert blk is not None
        sch, blk = _apply_atm_foup_place_gap_serial(sch, blk, dwells=dwells)
        assert blk is not None
        sch = _stamp_schedule_row_ids(sch)
        blk = _reattach_block_schedules(blk, sch)
        texts = tuple(d.as_text() for d in diags)
        return sch, blk, texts
    except Exception as exc:
        print(
            f"{_PRINT_PREFIX} occupancy scheduler skipped (fallback to raw plan): {exc}",
            flush=True,
        )
        return schedule, blocks, ()


def _csv_cache_key(path: Path) -> str:
    p = path.resolve()
    try:
        st = p.stat()
        return f"{p}|{st.st_mtime_ns}|{st.st_size}|{_csv_playback_config_tag()}"
    except OSError:
        return f"{p}|{_csv_playback_config_tag()}"


def get_cached_csv_playback(path: Path) -> Optional[CachedCsvPlayback]:
    key = _csv_cache_key(path)
    with _csv_playback_cache_lock:
        return _csv_playback_cache.get(key)


def find_cached_csv_playback(path: Optional[Path]) -> Optional[CachedCsvPlayback]:
    """파일 키 hit → 동일 path 의 다른 캐시 키(API·occ 태그 등) fallback.

    공정만보기 실시간 전환 등에서 ``_prepared_playback`` 이 비었을 때 사용.
    """
    if path is None:
        return None
    hit = get_cached_csv_playback(path)
    if hit is not None:
        return hit
    try:
        target = path.resolve()
    except OSError:
        target = path
    want_tag = _csv_playback_config_tag()
    best: Optional[CachedCsvPlayback] = None
    with _csv_playback_cache_lock:
        for cached in _csv_playback_cache.values():
            try:
                cp = Path(getattr(cached, "path", "") or "").resolve()
            except OSError:
                continue
            if cp != target:
                continue
            # 현재 config_tag(occ 포함) 일치 우선
            if str(getattr(cached, "config_tag", "") or "") == want_tag:
                return cached
            if best is None:
                best = cached
    return best


def clear_csv_playback_cache() -> None:
    with _csv_playback_cache_lock:
        _csv_playback_cache.clear()


def invalidate_csv_playback_cache_for_path(path: Optional[Path]) -> int:
    """해당 CSV/가상 path 의 캐시 엔트리만 제거 (정지 후 재파싱용).

    Returns:
        삭제된 엔트리 수.
    """
    if path is None:
        return 0
    try:
        target = Path(path).resolve()
    except OSError:
        try:
            target = Path(path)
        except Exception:
            return 0
    removed = 0
    with _csv_playback_cache_lock:
        drop_keys: List[str] = []
        for key, cached in _csv_playback_cache.items():
            try:
                cp = Path(getattr(cached, "path", "") or "").resolve()
            except OSError:
                continue
            if cp == target:
                drop_keys.append(key)
        for key in drop_keys:
            _csv_playback_cache.pop(key, None)
            removed += 1
    return removed


def _estimate_csv_build_units(dwells: List["DwellRecord"]) -> int:
    """pick/transfer/place 빌드 횟수 추정 (진행률 분모)."""
    if not dwells:
        return 1
    tours: Dict[Tuple[str, int], List["DwellRecord"]] = {}
    for d in dwells:
        tours.setdefault((d.lot_id, d.cassette_slot), []).append(d)
    n = 0
    for tour in tours.values():
        tour.sort(key=lambda x: x.start_sec)
        if tour[0].slot_key == LOGICAL_SLOT_ATM_ARM:
            n += 3  # FOUP pick + 합성 aligner place/pick
        n += max(0, len(tour) - 1)
        if tour[-1].slot_key == LOGICAL_SLOT_ATM_ARM:
            n += 1
    return max(1, n)


def _group_dwell_tours(dwells: List["DwellRecord"]) -> List[Tuple[Tuple[str, int], List["DwellRecord"]]]:
    tours: Dict[Tuple[str, int], List["DwellRecord"]] = {}
    for d in dwells:
        tours.setdefault((d.lot_id, d.cassette_slot), []).append(d)
    for key in list(tours.keys()):
        tours[key].sort(key=lambda x: x.start_sec)
    return sorted(tours.items(), key=lambda kv: kv[1][0].start_sec)


def _event_step_count_estimate(event_name: str) -> int:
    if not event_name:
        return 0
    try:
        from .lam_event_sequences import event_json_path

        p = event_json_path(event_name)
        return 1 if p.is_file() else 0
    except Exception:
        return 0


def _lam_sim_log_build(context: str, message: str) -> None:
    if is_csv_playback_compact_log():
        return
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


def _summarize_lam_sim_steps_ko(steps: LamSimJsonSteps) -> str:
    """JSON 스텝 구성 요약 (로그·UI용). TIMESAMPLES 유무와 무관하게 전 스텝이 실행됨."""
    if not steps:
        return "(스텝 없음)"
    counts: Dict[str, int] = {}
    for st in steps:
        t = str(st.get("type") or "?").upper()
        counts[t] = counts.get(t, 0) + 1
    order = (
        "MOVE",
        "ROTATE",
        "DELAY",
        "PRIM_VISIBILITY",
        "SET_PRIM_VISIBILITY",
        "TIMESAMPLES_REPLAY",
        "USD_TIMELINE",
    )
    parts: List[str] = []
    for key in order:
        if key in counts:
            parts.append(f"{key}×{counts[key]}")
    for key in sorted(counts.keys()):
        if key not in order:
            parts.append(f"{key}×{counts[key]}")
    est = _lam_estimate_raw_duration_sec(steps)
    return f"{', '.join(parts)} (1x 합산 약 {est:.2f}s)"


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

    ``LamSequenceRunner`` 가 JSON 에 정의된 **모든 스텝 타입**을 순서대로 실행한다
    (MOVE·ROTATE·DELAY·visibility·TIMESAMPLES_REPLAY 등). TIMESAMPLES 가 없어도
    나머지 스텝은 그대로 재생된다.
    ``speed_scale``: 대기·MOVE·프레임 등 **해당 블록 전체** 배속.
    ``target_duration_sec``: 매크로 ``duration_sec=`` 로 블록 길이 맞출 때만 추가 조정.
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


def collect_lam_sim_reset_prim_paths(
    *,
    script_text: Optional[str] = None,
    include_all_wafer_prims: bool = False,
) -> List[str]:
    """초기화(reset) 대상 prim — Z stage + (선택) 스크립트·전체 wafer 슬롯."""
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

    if include_all_wafer_prims:
        for _sk, path in load_wafer_prim_by_slot_key().items():
            _add(path)

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
    usd_context_name: Optional[str] = None,
    screen: Optional[int] = None,
) -> None:
    """ATM/VTM Z stage 등 TBS_OFFSET 을 0 으로 — 시퀀스 편집기 Reset 과 동일 정책.

    - 진행 중 translate/rotate 애니메이션 중지
    - ``scheduler.stop_all()`` (있을 때) — **CSV Play 활성 화면이 있으면 생략**
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

    # 듀얼 Play: stop_all_* 가 다른 화면 MOVE/ROTATE 까지 끊지 않게
    dual_guard = False
    try:
        for _si in range(1, 5):
            if csv_play_session_active(screen=_si):
                dual_guard = True
                break
    except Exception:
        dual_guard = False
    ctx = (str(usd_context_name).strip() if usd_context_name else "") or None
    try:
        if dual_guard:
            print(
                f"{_PRINT_PREFIX}   CSV Play 활성 — stop_all_* 생략"
                f"{f' (context={ctx!r})' if ctx else ' (default context만)'}",
                flush=True,
            )
            _ltx.stop_translate_animations_for_context(ctx)
            _lrx.stop_rotate_animations_for_context(ctx)
        else:
            _ltx.stop_all_translate_animations()
            _lrx.stop_all_rotate_animations()
    except Exception as exc:
        print(f"{_PRINT_PREFIX}   애니 중지 경고: {exc}", flush=True)

    if scheduler is not None and not dual_guard:
        try:
            stop_fn = getattr(scheduler, "stop_all", None)
            if callable(stop_fn):
                stop_fn()
                print(f"{_PRINT_PREFIX}   scheduler.stop_all() 호출", flush=True)
        except Exception as exc:
            print(f"{_PRINT_PREFIX}   scheduler.stop_all 경고: {exc}", flush=True)
    elif scheduler is not None and dual_guard:
        print(
            f"{_PRINT_PREFIX}   scheduler.stop_all 생략 (CSV Play 활성 — 듀얼 간섭 방지)",
            flush=True,
        )

    if paths:
        ok = _dispatch_main_wait(
            lambda: _reset_tbs_offset_ops_for_paths(paths, usd_context_name=ctx),
            timeout=15.0,
            play_screen=screen,
        )
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


def _vtm_hand_side_for_transfer(prev_sk: str, curr_sk: str, *, pick_into_arm: bool) -> str:
    """VTM 이송 시 ``left`` / ``right`` (EE 논리 슬롯 기준)."""
    if pick_into_arm:
        if curr_sk == LOGICAL_SLOT_VTM_EE_L:
            return "left"
        if curr_sk == LOGICAL_SLOT_VTM_EE_R:
            return "right"
    else:
        if prev_sk == LOGICAL_SLOT_VTM_EE_L:
            return "left"
        if prev_sk == LOGICAL_SLOT_VTM_EE_R:
            return "right"
    return "left"


def _event_json_paths_for_display(event_name: str) -> Tuple[str, str]:
    """이벤트 JSON (repo 상대 표기, 절대 경로)."""
    from .lam_event_sequences import event_json_path

    rel = f"lam/lam_event_sequences/{event_name}.json"
    try:
        p = event_json_path(event_name)
        return rel, str(p.resolve())
    except Exception:
        return rel, rel


def _vtm_module_slot_key(slot_key: str) -> bool:
    """VTM 물리 모듈 슬롯(chamber / airlock) 여부 — 이송 target 후보 판별."""
    sk = (slot_key or "").strip()
    if sk.startswith("chamber"):
        rest = sk[len("chamber") :]
        return rest.isdigit() and 1 <= int(rest) <= 5
    return sk.startswith("airlock1_") or sk.startswith("airlock2_")


def _vtm_transfer_target_slot(prev_sk: str, curr_sk: str) -> str:
    """EE dwell 없이 VTM 모듈 간 이송일 때 event 대상 slot_key."""
    if _vtm_module_slot_key(curr_sk):
        return curr_sk
    if _vtm_module_slot_key(prev_sk):
        return prev_sk
    return curr_sk


def _resolve_transfer_event_name(
    prev: DwellRecord, curr: DwellRecord
) -> Tuple[str, Optional[int]]:
    """CSV 이송 → ``lam_event_sequences`` 이벤트명·슬롯 번호."""
    from .lam_event_sequences import atm_event_name_for_slot, vtm_event_name_for_slot

    robot = _classify_transfer_robot(prev.slot_key, curr.slot_key)
    if robot == "ATM":
        if curr.slot_key == LOGICAL_SLOT_ATM_ARM:
            sk, po = prev.slot_key, "pick"
        elif prev.slot_key == LOGICAL_SLOT_ATM_ARM:
            sk, po = curr.slot_key, "place"
        else:
            sk, po = curr.slot_key, "place"
        return atm_event_name_for_slot(sk, po)
    pick_into_arm = curr.slot_key in (LOGICAL_SLOT_VTM_EE_L, LOGICAL_SLOT_VTM_EE_R)
    place_from_arm = prev.slot_key in (LOGICAL_SLOT_VTM_EE_L, LOGICAL_SLOT_VTM_EE_R)
    if pick_into_arm:
        target, po = prev.slot_key, "pick"
    elif place_from_arm:
        target, po = curr.slot_key, "place"
    else:
        target = _vtm_transfer_target_slot(prev.slot_key, curr.slot_key)
        po = "pick"
    hand = (
        _vtm_hand_side_for_transfer(prev.slot_key, curr.slot_key, pick_into_arm=pick_into_arm)
        if pick_into_arm or place_from_arm
        else "left"
    )
    return vtm_event_name_for_slot(target, hand, po)


def _transfer_event_hint(prev: DwellRecord, curr: DwellRecord) -> str:
    """CSV 이송에 대응하는 event JSON 이름(로그용)."""
    try:
        name, num = _resolve_transfer_event_name(prev, curr)
        if num is not None:
            return f"build_steps_for_event({name!r}, slot_number={num})  →  {name}({num})"
        return f"build_steps_for_event({name!r})  →  {name}()"
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
                f"{_PRINT_PREFIX} [t={t0:.3f}s] INIT wafer lot={d.lot_id!r} foup={d.foup_index} "
                f"cassette={d.cassette_slot} slot={d.slot_key} module={d.module_nm!r}",
                flush=True,
            )
        else:
            _log_virtual_transfer(dwells[i - 1], d)

        wp = _virtual_wafer_prim(d.slot_key)
        z = _virtual_slot_z_m(d.slot_key)
        z_s = f"z_abs_m={z!r}" if z is not None else "z_abs_m=(none)"

        print(
            f"{_PRINT_PREFIX} [t={t0:.3f}s..{t1:.3f}s] DWELL#{i} lot={d.lot_id!r} foup={d.foup_index} "
            f"cassette={d.cassette_slot} slot={d.slot_key} module={d.module_nm!r}",
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
        f"{_PRINT_PREFIX} [t={last.end_sec:.3f}s] END virtual run cassette={last.cassette_slot} "
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
        f"{prev.slot_key} -> {curr.slot_key} (cassette={curr.cassette_slot}) -----",
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
    """CSV 한 행 — dwell(머무름) 구간.

    ``eqp_start_tm`` / ``eqp_end_tm`` 은 ``normalize_csv_timeline()`` 이후 **전역 0초 기준** [s].
    ``lot_id`` 는 등장 순서로 foup1..3 에 매핑 (``foup_index``).
    """

    eqp_id: str
    module_nm: str
    lot_id: str
    cassette_slot: int
    eqp_start_tm: float
    eqp_end_tm: float
    process_tm: float


@dataclass(frozen=True)
class DwellRecord:
    """한 웨이퍼(lot+cassette)가 한 ``slot_key`` 에 머문 구간 [start_sec, end_sec)."""

    cassette_slot: int
    lot_id: str
    foup_index: int
    module_nm: str
    slot_key: str
    start_sec: float
    end_sec: float
    process_tm: float
    eqp_id: str


@dataclass(frozen=True)
class CsvPlaybackScheduleEntry:
    """CSV 재생 UI·로그용 시간순 한 줄 (dwell / pick / transfer / place)."""

    time_sec: float
    sort_order: int
    category: str
    title_ko: str
    csv_read_ko: str = ""
    meaning_ko: str = ""
    exec_ko: str = ""
    step_count: int = 0
    event_name: str = ""
    json_path: str = ""
    schedule_row: int = -1
    # 타임라인 soft 매칭·진단용 (없으면 title_ko 에서 추정)
    lot_id: str = ""
    cassette_slot: int = 0


@dataclass(frozen=True)
class CsvTimedPlaybackBlock:
    """CSV ``eqp_start_tm`` (또는 place 의 ``end_sec``) 에 맞춰 재생할 한 덩어리.

    ``steps`` 가 비어 있으면 해당 시각에 **로그만** (dwell 체류). JSON 실행은 ``steps`` 가 있을 때만.
    """

    time_sec: float
    sort_order: int
    category: str
    label: str
    steps: LamSimJsonSteps
    schedule: Optional[CsvPlaybackScheduleEntry] = None


@dataclass(frozen=True)
class CsvPlayPauseCheckpoint:
    """일시정지 시점 — Play(이어서 재생) 시 사용."""

    csv_path: str
    speed_scale: float
    process_only: bool
    resume_csv_sec: float
    json_done: int = 0
    wall_elapsed_sec: float = 0.0
    paused_in_json: bool = False


# FOUP pick 직후 CSV에 없는 Aligner 전처리 (현장 규칙, 합성 타임라인만).
# 세트1(항상): FOUP pick → place = FOUP pick t + PLACE_DELAY.
# 세트2(Aligner pick): 끼어든 다른 wafer ATM 동작이 없으면 FOUP pick t + PICK_DELAY.
#   있으면 해당 wafer 다음 이송(보통 airlock place) 시각 직전(LEAD)에 pick 후 대기.
FOUP_PICK_SYNTH_ALIGNER_PLACE_DELAY_SEC: float = 2.5
FOUP_PICK_SYNTH_ALIGNER_PICK_DELAY_SEC: float = 6
FOUP_PICK_SYNTH_ALIGNER_PICK_LEAD_BEFORE_NEXT_SEC: float = (
    FOUP_PICK_SYNTH_ALIGNER_PICK_DELAY_SEC - FOUP_PICK_SYNTH_ALIGNER_PLACE_DELAY_SEC
)

_SCHEDULE_CATEGORY_ORDER: Dict[str, int] = {
    # 동일 CSV t 에서 FOUP place 가 pick 보다 먼저 (ATM 팔: 비운 뒤 집기).
    "place": 0,
    "aligner_place": 1,
    "transfer": 2,
    "aligner_pick": 3,
    "pick": 4,
    "dwell": 5,
}

# ATM 팔 직렬 보정 — 스텝 길이 추정 불가 시 폴백(레거시, 점유 swap 경로에서는 미사용).
ATM_ARM_SERIAL_FALLBACK_DUR_SEC: float = 2.0

_VTM_EVENT_NAME_RE = re.compile(
    r"^vtm_(?:chamber(\d+)|airlock(\d+))_(left|right)_(pick|place)$",
    re.IGNORECASE,
)

_SCHEDULE_CATEGORY_KO: Dict[str, str] = {
    "dwell": "체류",
    "pick": "FOUP 픽업",
    "aligner_place": "Aligner 내려놓기",
    "aligner_pick": "Aligner 픽업",
    "transfer": "이송",
    "place": "FOUP 반환",
}


# ---------------------------------------------------------------------------
# 5) 시간 파싱
# ---------------------------------------------------------------------------


def _parse_datetime_to_seconds(value: Any) -> float:
    """``2026-04-13 05:53:49.140000`` 형태 → epoch 초 (float)."""
    s = str(value or "").strip()
    if not s:
        return 0.0
    if "." in s:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")
    else:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return float(dt.timestamp())


def parse_time_to_seconds(value: Any) -> float:
    """CSV 시간 셀 → [s]. 숫자 또는 날짜·시간 문자열."""
    if value is None or (isinstance(value, str) and not str(value).strip()):
        return 0.0
    s = str(value).strip()
    if re.search(r"\d{4}-\d{2}-\d{2}", s):
        return _parse_datetime_to_seconds(s)
    if TIME_PARSE_MODE == "seconds_float":
        return float(s)
    return float(s)


def _parse_csv_time_field(raw: Dict[str, str], primary: str, iso_alt: str = "") -> float:
    """``eqp_start_tm`` 우선, 없으면 ``eqp_start_iso`` 등 ISO 열."""
    if iso_alt and (raw.get(iso_alt) or "").strip():
        return parse_time_to_seconds(raw.get(iso_alt))
    return parse_time_to_seconds(raw.get(primary))


def build_lot_id_to_foup_index(rows: Iterable[ParsedCsvRow]) -> Dict[str, int]:
    """``eqp_start_tm`` 순 **lot_id 최초 등장** → foup1, foup2, foup3 (최대 3)."""
    ordered = sorted(rows, key=lambda r: (r.eqp_start_tm, r.cassette_slot, r.module_nm))
    out: Dict[str, int] = {}
    n = 0
    for r in ordered:
        lid = (r.lot_id or "").strip() or f"__anon_cassette_{r.cassette_slot}"
        if lid not in out:
            n += 1
            out[lid] = min(3, n)
    return out


def normalize_csv_timeline(rows: List[ParsedCsvRow]) -> List[ParsedCsvRow]:
    """전역 타임라인: 최소 ``eqp_start_tm`` = 0, ``process_tm`` 분→초 보정(필요 시).

    이후 ``rows_to_dwell_records`` 에서 미지원 module 등이 스킵되면 첫 **유효 dwell**
    이 0보다 클 수 있다. 재생용 최종 t=0 은 ``normalize_dwell_timeline`` 이 맞춘다.
    """
    if not rows:
        return rows
    adjusted: List[ParsedCsvRow] = []
    for r in rows:
        pt = float(r.process_tm)
        dwell = float(r.eqp_end_tm) - float(r.eqp_start_tm)
        if dwell > 1e-6 and pt > 0 and pt < 180 and abs(dwell - pt * 60.0) < abs(dwell - pt):
            pt = pt * 60.0
        adjusted.append(
            ParsedCsvRow(
                eqp_id=r.eqp_id,
                module_nm=r.module_nm,
                lot_id=r.lot_id,
                cassette_slot=r.cassette_slot,
                eqp_start_tm=float(r.eqp_start_tm),
                eqp_end_tm=float(r.eqp_end_tm),
                process_tm=pt,
            )
        )
    t0 = min(x.eqp_start_tm for x in adjusted)
    return [
        ParsedCsvRow(
            eqp_id=x.eqp_id,
            module_nm=x.module_nm,
            lot_id=x.lot_id,
            cassette_slot=x.cassette_slot,
            eqp_start_tm=float(x.eqp_start_tm) - t0,
            eqp_end_tm=float(x.eqp_end_tm) - t0,
            process_tm=x.process_tm,
        )
        for x in adjusted
    ]


def normalize_dwell_timeline(dwells: List[DwellRecord]) -> List[DwellRecord]:
    """유효 dwell 기준 재생 타임라인: 최소 ``start_sec`` = 0, 상대 간격 유지.

    Simulation Play 가 첫 공정부터 바로 시작하도록 한다.
    (행 단위 ``normalize_csv_timeline`` 만으로는 스킵된 행·0초 오염 행 때문에
    첫 dwell 이 수백 초로 남을 수 있음.)
    """
    if not dwells:
        return dwells
    t0 = min(float(d.start_sec) for d in dwells)
    if abs(t0) < 1e-9:
        return dwells
    out = [
        replace(
            d,
            start_sec=float(d.start_sec) - t0,
            end_sec=float(d.end_sec) - t0,
        )
        for d in dwells
    ]
    if not is_csv_playback_compact_log():
        print(
            f"{_PRINT_PREFIX} dwell timeline: shift t0={t0:.3f}s → first valid dwell at 0s "
            f"(n={len(out)})",
            flush=True,
        )
    return out


# ---------------------------------------------------------------------------
# 6) CSV 로드
# ---------------------------------------------------------------------------


def resolve_csv_path(path: Optional[str] = None) -> Path:
    """인자가 없으면 ``DEFAULT_CSV_PATH`` 를, 있으면 사용자 경로를 ``Path`` 로 정규화한다."""
    return Path(path or DEFAULT_CSV_PATH).expanduser()


def read_csv_rows(csv_path: Path) -> List[ParsedCsvRow]:
    """UTF-8 CSV → ``ParsedCsvRow`` (정규화 전).

    필수: ``eqp_id``, ``module_nm``, ``eqp_start_tm``, ``eqp_end_tm``, ``process_tm``
    + ``cassette_slot`` (구 헤더 ``cassette_id`` 도 허용). 선택: ``lot_id``, ``eqp_*_iso``.
    """
    if not csv_path.is_file():
        raise FileNotFoundError(f"{_PRINT_PREFIX} CSV not found: {csv_path}")

    rows: List[ParsedCsvRow] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or ())
        if "cassette_slot" not in header and "cassette_id" not in header:
            raise ValueError(f"{_PRINT_PREFIX} CSV missing cassette_slot column")
        for need in ("eqp_id", "module_nm", "eqp_start_tm", "eqp_end_tm", "process_tm"):
            if need not in header:
                raise ValueError(f"{_PRINT_PREFIX} CSV missing column: {need}")

        for raw in reader:
            if "cassette_slot" in header:
                cs_raw = raw.get("cassette_slot")
            else:
                cs_raw = raw.get("cassette_id")
            try:
                cs = int(str(cs_raw).strip())
            except Exception as exc:
                raise ValueError(f"{_PRINT_PREFIX} bad cassette_slot: {raw!r}") from exc
            pt_raw = raw.get("process_tm") or raw.get("proccess_tm") or "0"
            rows.append(
                ParsedCsvRow(
                    eqp_id=str(raw.get("eqp_id") or "").strip(),
                    module_nm=str(raw.get("module_nm") or "").strip(),
                    lot_id=str(raw.get("lot_id") or "").strip(),
                    cassette_slot=cs,
                    eqp_start_tm=_parse_csv_time_field(raw, "eqp_start_tm", "eqp_start_iso"),
                    eqp_end_tm=_parse_csv_time_field(raw, "eqp_end_tm", "eqp_end_iso"),
                    process_tm=parse_time_to_seconds(pt_raw),
                )
            )
    return rows


def load_csv_dwell_timeline(csv_path: Path) -> List[DwellRecord]:
    """EAP CSV 전체 파이프라인: 읽기 → 행 t=0 → lot→foup → dwell → **유효 dwell t=0**."""
    raw = normalize_csv_timeline(read_csv_rows(csv_path))
    lot_to_foup = build_lot_id_to_foup_index(raw)
    dwells = normalize_dwell_timeline(
        sort_dwells_for_playback(rows_to_dwell_records(raw, lot_to_foup))
    )
    if not is_csv_playback_compact_log():
        print(
            f"{_PRINT_PREFIX} CSV timeline: rows={len(raw)} dwells={len(dwells)} "
            f"lots→foup={lot_to_foup}",
            flush=True,
        )
    return dwells


def rows_to_dwell_records(
    rows: Iterable[ParsedCsvRow],
    lot_id_to_foup: Optional[Dict[str, int]] = None,
) -> List[DwellRecord]:
    """``ParsedCsvRow`` → ``DwellRecord`` (미지원 ``module_nm`` 은 스킵)."""
    lot_map = lot_id_to_foup or build_lot_id_to_foup_index(rows)
    out: List[DwellRecord] = []
    for r in rows:
        sk = parse_module_nm_to_slot_key(r.module_nm)
        if sk is None:
            if not is_csv_playback_compact_log():
                print(f"{_PRINT_PREFIX} skip unknown module_nm={r.module_nm!r}", flush=True)
            continue
        if r.eqp_end_tm < r.eqp_start_tm:
            if not is_csv_playback_compact_log():
                print(
                    f"{_PRINT_PREFIX} skip inverted time lot={r.lot_id!r} cassette={r.cassette_slot} "
                    f"mod={r.module_nm}",
                    flush=True,
                )
            continue
        lid = (r.lot_id or "").strip() or f"__anon_cassette_{r.cassette_slot}"
        foup_i = int(lot_map.get(lid, 1))
        out.append(
            DwellRecord(
                cassette_slot=r.cassette_slot,
                lot_id=lid,
                foup_index=foup_i,
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
    """전역 타임라인 정렬 — ``eqp_start_tm`` (= ``start_sec``) 오름차순 (prompt1 §363)."""
    return sorted(dwells, key=lambda d: (d.start_sec, d.lot_id, d.cassette_slot, d.module_nm))


def _same_wafer_dwell(prev: DwellRecord, curr: DwellRecord) -> bool:
    return prev.lot_id == curr.lot_id and prev.cassette_slot == curr.cassette_slot


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
    """동일 웨이퍼(lot+cassette) 의 연속 dwell 사이 이송 — **다음 module_nm** 기준 (prompt1 §332-2)."""
    from .lam_event_sequences import atm_event_name_for_slot, build_steps_for_event, vtm_event_name_for_slot

    if not _same_wafer_dwell(prev, curr):
        _lam_sim_log_build(
            "transfer",
            f"이송 생략(웨이퍼 불일치): lot {prev.lot_id!r}/{prev.cassette_slot} -> "
            f"{curr.lot_id!r}/{curr.cassette_slot} ({prev.slot_key!r} -> {curr.slot_key!r}).",
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
                target = _vtm_transfer_target_slot(prev.slot_key, curr.slot_key)
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
    try:
        from .lam_wafer_viewport_labels import stamp_wafer_cassette_label_on_steps

        stamp_wafer_cassette_label_on_steps(
            steps, curr.cassette_slot, foup_index=getattr(curr, "foup_index", None)
        )
    except Exception:
        pass
    return steps


def _foup_slot_key(foup_index: int, cassette_slot: int) -> str:
    return f"foup{int(foup_index)}_{int(cassette_slot)}"


def build_foup_pick_place_steps(
    *,
    foup_index: int,
    cassette_slot: int,
    pick_or_place: str,
) -> LamSimJsonSteps:
    """투어 시작 pick / 종료 place — ``atm_foupN_pick/place(slot)``."""
    from .lam_event_sequences import atm_event_name_for_slot, build_steps_for_event

    sk = _foup_slot_key(foup_index, cassette_slot)
    event, num = atm_event_name_for_slot(sk, pick_or_place)
    steps = build_steps_for_event(
        event,
        slot_number=num,
        vtm_ee_swap=VTM_END_EFFECTOR_SWAP_HANDS,
    )
    try:
        from .lam_wafer_viewport_labels import stamp_wafer_cassette_label_on_steps

        stamp_wafer_cassette_label_on_steps(
            steps, cassette_slot, foup_index=foup_index
        )
    except Exception:
        pass
    return steps


def build_aligner_after_foup_pick_steps(
    pick_or_place: str, *, cassette_slot: int, foup_index: Optional[int] = None
) -> LamSimJsonSteps:
    """FOUP pick 직후 합성 Aligner 공정 — ``atm_aligner_place`` / ``atm_aligner_pick``."""
    from .lam_event_sequences import build_steps_for_event

    po = (pick_or_place or "pick").strip().lower()
    if po not in ("pick", "place"):
        raise ValueError(f"pick_or_place must be 'pick' or 'place' (got {pick_or_place!r})")
    event = f"atm_aligner_{po}"
    steps = build_steps_for_event(
        event,
        slot_number=None,
        vtm_ee_swap=VTM_END_EFFECTOR_SWAP_HANDS,
    )
    try:
        from .lam_wafer_viewport_labels import stamp_wafer_cassette_label_on_steps

        stamp_wafer_cassette_label_on_steps(
            steps, cassette_slot, foup_index=foup_index
        )
    except Exception:
        pass
    return steps


def _aligner_exec_hint(pick_or_place: str) -> str:
    event = f"atm_aligner_{(pick_or_place or 'pick').strip().lower()}"
    po_ko = "픽업(pick)" if event.endswith("_pick") else "내려놓기(place)"
    return f"build_steps_for_event({event!r})  →  lam_sim_actions.{event}()  [{po_ko}]"


def _other_wafer_atm_action_times_in_window(
    dwells: Sequence["DwellRecord"],
    *,
    exclude_lot_id: str,
    exclude_cassette_slot: int,
    window_lo: float,
    window_hi: float,
) -> List[float]:
    """다른 wafer 의 ATM 관련 동작 시각 (window_lo, window_hi] — 끼어듦 판별용."""
    times: List[float] = []
    lo = float(window_lo)
    hi = float(window_hi)
    if hi <= lo + 1e-9:
        return times
    for (lot_id, cassette_slot), tour in _group_dwell_tours(list(dwells)):
        if lot_id == exclude_lot_id and int(cassette_slot) == int(exclude_cassette_slot):
            continue
        if not tour:
            continue
        first, last = tour[0], tour[-1]
        if first.slot_key == LOGICAL_SLOT_ATM_ARM:
            t = float(first.start_sec)
            if lo < t <= hi:
                times.append(t)
        for i in range(len(tour) - 1):
            prev_d, curr_d = tour[i], tour[i + 1]
            if _classify_transfer_robot(prev_d.slot_key, curr_d.slot_key) != "ATM":
                continue
            t = float(curr_d.start_sec)
            if lo < t <= hi:
                times.append(t)
        if last.slot_key == LOGICAL_SLOT_ATM_ARM:
            t = float(last.end_sec)
            if lo < t <= hi:
                times.append(t)
    return times


def _resolve_synth_aligner_pick_time(
    *,
    anchor_time_sec: float,
    place_t: float,
    lot_id: str,
    cassette_slot: int,
    tour: Sequence["DwellRecord"],
    all_dwells: Sequence["DwellRecord"],
) -> Tuple[Optional[float], bool, Optional[float]]:
    """Aligner pick 시각 — ``lam_aligner_process_rules`` SSOT.

    Returns:
        (pick_t|None, deferred, airlock_place_t)
        pick_t 가 None 이면 airlock place 가 투어에 없음 → place 만 삽입, pick 보류.
    """
    from .lam_aligner_process_rules import resolve_aligner_pick_schedule

    anchor = float(anchor_time_sec)
    airlock_probe = None
    try:
        from .lam_aligner_process_rules import find_first_airlock_place_time_in_tour

        airlock_probe = find_first_airlock_place_time_in_tour(tour)
    except Exception:
        airlock_probe = None
    window_hi = float(airlock_probe) if airlock_probe is not None else (
        float(tour[1].start_sec) if len(tour) >= 2 else anchor + 3600.0
    )
    iv = _other_wafer_atm_action_times_in_window(
        all_dwells,
        exclude_lot_id=lot_id,
        exclude_cassette_slot=int(cassette_slot),
        window_lo=float(place_t),
        window_hi=window_hi,
    )
    lead = float(FOUP_PICK_SYNTH_ALIGNER_PICK_LEAD_BEFORE_NEXT_SEC)
    return resolve_aligner_pick_schedule(
        foup_pick_t=anchor,
        aligner_place_t=float(place_t),
        tour=tour,
        other_atm_action_times=iv,
        pick_lead_before_airlock_sec=lead,
    )


def _append_aligner_after_foup_pick(
    *,
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]],
    anchor_time_sec: float,
    foup_index: int,
    cassette_slot: int,
    lot_id: str,
    tour: Sequence["DwellRecord"],
    all_dwells: Sequence["DwellRecord"],
    progress: Optional[_ThrottledBuildProgress] = None,
) -> None:
    """FOUP pick 후 Aligner 절대 규칙 적용.

    - place: FOUP pick 직후 세트(항상)
    - pick: 같은 wafer 의 **airlock place 직전**에만. airlock 홉이 없으면 pick 미삽입.
    """
    try:
        from .lam_csv_occupancy_scheduler import aligner_synthesis_enabled

        if not aligner_synthesis_enabled():
            return
    except Exception:
        pass
    place_t = float(anchor_time_sec) + FOUP_PICK_SYNTH_ALIGNER_PLACE_DELAY_SEC
    pick_t, deferred_pick, next_transfer_t = _resolve_synth_aligner_pick_time(
        anchor_time_sec=anchor_time_sec,
        place_t=place_t,
        lot_id=lot_id,
        cassette_slot=cassette_slot,
        tour=tour,
        all_dwells=all_dwells,
    )
    jobs: List[Tuple[str, float, str]] = [
        ("place", place_t, "aligner_place"),
    ]
    if pick_t is not None:
        jobs.append(("pick", float(pick_t), "aligner_pick"))
    else:
        _lam_sim_log_build(
            "csv_tour",
            f"Aligner pick 보류 lot={lot_id!r} wafer#{cassette_slot} "
            f"— 투어에 ATM→airlock place 없음 (place 만 삽입)",
        )

    for po, t_sec, label in jobs:
        try:
            deferred = bool(deferred_pick and po == "pick")
            if blocks is not None:
                steps = build_aligner_after_foup_pick_steps(
                    po, cassette_slot=cassette_slot, foup_index=foup_index
                )
                if not steps:
                    continue
                ent = _aligner_schedule_entry(
                    time_sec=t_sec,
                    pick_or_place=po,
                    foup_index=foup_index,
                    cassette_slot=cassette_slot,
                    lot_id=lot_id,
                    anchor_time_sec=anchor_time_sec,
                    steps=steps,
                    deferred_pick=deferred,
                    next_transfer_t=next_transfer_t,
                )
                schedule.append(ent)
                blocks.append(_block_from_schedule(ent, steps, label=label))
            else:
                schedule.append(
                    _aligner_schedule_entry_meta(
                        time_sec=t_sec,
                        pick_or_place=po,
                        foup_index=foup_index,
                        cassette_slot=cassette_slot,
                        lot_id=lot_id,
                        anchor_time_sec=anchor_time_sec,
                        deferred_pick=deferred,
                        next_transfer_t=next_transfer_t,
                    )
                )
        except Exception as exc:
            _lam_sim_log_build("csv_tour", f"Aligner {po} skip: {exc}")
        if progress is not None:
            progress.tick(1)


def _aligner_schedule_entry(
    *,
    time_sec: float,
    pick_or_place: str,
    foup_index: int,
    cassette_slot: int,
    lot_id: str,
    anchor_time_sec: float,
    steps: LamSimJsonSteps,
    deferred_pick: bool = False,
    next_transfer_t: Optional[float] = None,
) -> CsvPlaybackScheduleEntry:
    po = (pick_or_place or "pick").strip().lower()
    event = f"atm_aligner_{po}"
    category = "aligner_place" if po == "place" else "aligner_pick"
    _ev, json_path = _schedule_entry_json_fields(event)
    if po == "place":
        delay_ko = f"FOUP pick(t={anchor_time_sec:.3f}s) 후 +{FOUP_PICK_SYNTH_ALIGNER_PLACE_DELAY_SEC:g}s"
        meaning_ko = (
            "EAP CSV 에는 없지만, FOUP 에서 집은 웨이퍼를 Aligner 에 내려놓는 "
            "전처리(세트1, 항상 강제)."
        )
    elif deferred_pick:
        nxt = (
            f"다음 이송 t={float(next_transfer_t):.3f}s"
            if next_transfer_t is not None
            else "다음 이송"
        )
        delay_ko = (
            f"다른 wafer ATM 동작 우선 후, {nxt} 직전 Aligner pick "
            f"(t={float(time_sec):.3f}s) → 팔에 들고 대기"
        )
        meaning_ko = (
            "끼어든 ATM 공정을 먼저 재생한 뒤, 이 웨이퍼를 목적지로 옮기기 직전에 "
            "Aligner 에서 집어 ATM 팔에 들고 대기(세트2)."
        )
    else:
        delay_ko = (
            f"FOUP pick(t={anchor_time_sec:.3f}s) 후 "
            f"+{FOUP_PICK_SYNTH_ALIGNER_PICK_DELAY_SEC:g}s (끼어듦 없음)"
        )
        meaning_ko = (
            "EAP CSV 에는 없지만, Aligner 에 맡긴 웨이퍼를 다시 ATM 팔로 집어 "
            "다음 이송(airlock 등)까지 대기(세트2)."
        )
    title_place = (
        f"[재생] ATM 팔 → Aligner · lot={lot_id!r} · 웨이퍼#{cassette_slot} "
        f"(FOUP{foup_index} 투어)"
    )
    title_pick = (
        f"[재생] Aligner → ATM 팔 · lot={lot_id!r} · 웨이퍼#{cassette_slot} "
        f"(FOUP{foup_index} 투어)"
    )
    return CsvPlaybackScheduleEntry(
        time_sec=float(time_sec),
        sort_order=_SCHEDULE_CATEGORY_ORDER[category],
        category=category,
        title_ko=title_place if po == "place" else title_pick,
        csv_read_ko=(
            "CSV 행 없음 — FOUP pick 직후 현장 규칙으로 타임라인에 자동 삽입. "
            f"{delay_ko}."
        ),
        meaning_ko=meaning_ko,
        exec_ko=_aligner_exec_hint(po),
        step_count=len(steps),
        event_name=event,
        json_path=json_path,
        lot_id=str(lot_id or ""),
        cassette_slot=int(cassette_slot or 0),
    )


def _aligner_schedule_entry_meta(
    *,
    time_sec: float,
    pick_or_place: str,
    foup_index: int,
    cassette_slot: int,
    lot_id: str,
    anchor_time_sec: float,
    deferred_pick: bool = False,
    next_transfer_t: Optional[float] = None,
) -> CsvPlaybackScheduleEntry:
    po = (pick_or_place or "pick").strip().lower()
    event = f"atm_aligner_{po}"
    ent = _aligner_schedule_entry(
        time_sec=time_sec,
        pick_or_place=po,
        foup_index=foup_index,
        cassette_slot=cassette_slot,
        lot_id=lot_id,
        anchor_time_sec=anchor_time_sec,
        steps=[],
        deferred_pick=deferred_pick,
        next_transfer_t=next_transfer_t,
    )
    return CsvPlaybackScheduleEntry(
        time_sec=ent.time_sec,
        sort_order=ent.sort_order,
        category=ent.category,
        title_ko=ent.title_ko,
        csv_read_ko=ent.csv_read_ko,
        meaning_ko=ent.meaning_ko,
        exec_ko=ent.exec_ko,
        step_count=_event_step_count_estimate(event),
        event_name=event,
        json_path=ent.json_path,
    )


def _slot_key_label_ko(slot_key: str) -> str:
    if slot_key == LOGICAL_SLOT_ATM_ARM:
        return "ATM 팔(EndEffector)"
    if slot_key == LOGICAL_SLOT_VTM_EE_L:
        return "VTM 좌측 EE"
    if slot_key == LOGICAL_SLOT_VTM_EE_R:
        return "VTM 우측 EE"
    if slot_key.startswith("foup"):
        return f"FOUP 슬롯 {slot_key}"
    if slot_key.startswith("chamber"):
        return f"챔버 {slot_key.replace('chamber', '')}"
    if slot_key.startswith("buffer"):
        return f"버퍼 {slot_key}"
    if slot_key.startswith("cooling_"):
        return f"쿨링 {slot_key.replace('cooling_', '')}번"
    if slot_key.startswith("airlock"):
        return f"에어록 {slot_key}"
    return slot_key


def _resolve_foup_event_name(
    foup_index: int, cassette_slot: int, pick_or_place: str
) -> Tuple[str, Optional[int]]:
    from .lam_event_sequences import atm_event_name_for_slot

    sk = _foup_slot_key(foup_index, cassette_slot)
    return atm_event_name_for_slot(sk, pick_or_place)


def _foup_exec_hint(foup_index: int, cassette_slot: int, pick_or_place: str) -> str:
    event, num = _resolve_foup_event_name(foup_index, cassette_slot, pick_or_place)
    po_ko = "픽업(pick)" if pick_or_place == "pick" else "반환(place)"
    if num is not None:
        return (
            f"build_steps_for_event({event!r}, slot_number={num})  "
            f"→  lam_sim_actions.{event}({num})  [{po_ko}]"
        )
    return f"build_steps_for_event({event!r})  [{po_ko}]"


def _schedule_entry_json_fields(event_name: str) -> Tuple[str, str]:
    """``(event_name, json_path 절대)`` — 스케줄·콘솔 공통."""
    if not event_name:
        return "", ""
    _rel, abs_path = _event_json_paths_for_display(event_name)
    return event_name, abs_path


def _dwell_schedule_entry(d: DwellRecord) -> CsvPlaybackScheduleEntry:
    dur = max(0.0, float(d.end_sec) - float(d.start_sec))
    return CsvPlaybackScheduleEntry(
        time_sec=float(d.start_sec),
        sort_order=_SCHEDULE_CATEGORY_ORDER["dwell"],
        category="dwell",
        title_ko=(
            f"[CSV 체류] lot={d.lot_id!r} · FOUP{d.foup_index} · 웨이퍼#{d.cassette_slot} · "
            f"{_slot_key_label_ko(d.slot_key)}"
        ),
        csv_read_ko=(
            f"module_nm={d.module_nm!r}, eqp_id={d.eqp_id!r}, "
            f"시작={d.start_sec:.3f}s, 종료={d.end_sec:.3f}s, process_tm={d.process_tm:.3f}s"
        ),
        meaning_ko=(
            f"웨이퍼가 이 슬롯에 약 {dur:.1f}s 머무름 (체류). process_tm 은 애니 길이가 아님 — "
            f"이 구간에는 별도 로봇 애니를 넣지 않음."
        ),
        exec_ko="(실행 없음 — dwell 구간)",
        step_count=0,
        event_name="",
        json_path="",
        lot_id=str(d.lot_id or ""),
        cassette_slot=int(d.cassette_slot or 0),
    )


def _pick_schedule_entry(
    *,
    time_sec: float,
    foup_index: int,
    cassette_slot: int,
    lot_id: str,
    steps: LamSimJsonSteps,
) -> CsvPlaybackScheduleEntry:
    event, _num = _resolve_foup_event_name(foup_index, cassette_slot, "pick")
    _ev, json_path = _schedule_entry_json_fields(event)
    return CsvPlaybackScheduleEntry(
        time_sec=float(time_sec),
        sort_order=_SCHEDULE_CATEGORY_ORDER["pick"],
        category="pick",
        title_ko=(
            f"[재생] FOUP{foup_index} → ATM 팔 픽업 · lot={lot_id!r} · 웨이퍼#{cassette_slot}"
        ),
        csv_read_ko=(
            "투어 첫 dwell 이 AtmArm 이므로, CSV 이전에 FOUP 에서 웨이퍼를 집어 올림 "
            f"(foup{foup_index}_{cassette_slot})."
        ),
        meaning_ko="공정 투어 시작 — FOUP 슬롯에서 ATM EndEffector 로 pick.",
        exec_ko=_foup_exec_hint(foup_index, cassette_slot, "pick"),
        step_count=len(steps),
        event_name=event,
        json_path=json_path,
        lot_id=str(lot_id or ""),
        cassette_slot=int(cassette_slot or 0),
    )


def _place_schedule_entry(
    *,
    time_sec: float,
    foup_index: int,
    cassette_slot: int,
    lot_id: str,
    steps: LamSimJsonSteps,
) -> CsvPlaybackScheduleEntry:
    event, _num = _resolve_foup_event_name(foup_index, cassette_slot, "place")
    _ev, json_path = _schedule_entry_json_fields(event)
    return CsvPlaybackScheduleEntry(
        time_sec=float(time_sec),
        sort_order=_SCHEDULE_CATEGORY_ORDER["place"],
        category="place",
        title_ko=(
            f"[재생] ATM 팔 → FOUP{foup_index} 반환 · lot={lot_id!r} · 웨이퍼#{cassette_slot}"
        ),
        csv_read_ko=(
            "투어 마지막 dwell 이 AtmArm → FOUP place 합성으로 wafer 공정 종료 "
            "(Buffer/airlock/cooling 등 pick 출처 무관)."
        ),
        meaning_ko="공정 투어 종료 SSOT — ATM 팔의 wafer 를 FOUP 슬롯에 되돌림.",
        exec_ko=_foup_exec_hint(foup_index, cassette_slot, "place"),
        step_count=len(steps or []),
        event_name=event,
        json_path=json_path,
        lot_id=str(lot_id or ""),
        cassette_slot=int(cassette_slot or 0),
    )


def _transfer_schedule_entry(
    prev: DwellRecord,
    curr: DwellRecord,
    steps: LamSimJsonSteps,
) -> CsvPlaybackScheduleEntry:
    hint = _transfer_event_hint(prev, curr)
    robot = _classify_transfer_robot(prev.slot_key, curr.slot_key)
    try:
        event, _num = _resolve_transfer_event_name(prev, curr)
        _ev, json_path = _schedule_entry_json_fields(event)
    except ValueError:
        event, json_path = "", ""
    return CsvPlaybackScheduleEntry(
        time_sec=float(curr.start_sec),
        sort_order=_SCHEDULE_CATEGORY_ORDER["transfer"],
        category="transfer",
        title_ko=(
            f"[재생] 이송({robot}) · lot={curr.lot_id!r} · 웨이퍼#{curr.cassette_slot} · "
            f"{_slot_key_label_ko(prev.slot_key)} → {_slot_key_label_ko(curr.slot_key)}"
        ),
        csv_read_ko=(
            f"이전 행 module_nm={prev.module_nm!r} (끝 {prev.end_sec:.3f}s) → "
            f"다음 행 module_nm={curr.module_nm!r} (시작 {curr.start_sec:.3f}s)"
        ),
        meaning_ko=(
            "CSV 규칙: 한 행은 '머무름'만 표시. 이송 애니는 **다음 module_nm** 으로 "
            "어디로 옮겼는지 추론해 이벤트 JSON 을 실행."
        ),
        exec_ko=hint,
        step_count=len(steps),
        event_name=event,
        json_path=json_path,
        lot_id=str(curr.lot_id or ""),
        cassette_slot=int(curr.cassette_slot or 0),
    )


def _short_schedule_title(title_ko: str) -> str:
    for prefix in ("[CSV 체류] ", "[재생] "):
        if title_ko.startswith(prefix):
            return title_ko[len(prefix) :]
    return title_ko


def _json_exec_label(e: CsvPlaybackScheduleEntry, *, executed: bool) -> str:
    if e.category == "dwell":
        return "(JSON 없음)"
    if not e.event_name:
        return "(이벤트 없음)"
    rel = f"{e.event_name}.json"
    if e.json_path and not Path(e.json_path).is_file():
        return f"{rel} ⚠파일없음"
    if executed and e.step_count > 0:
        return f"{rel} ✓실행"
    if e.step_count > 0:
        return f"{rel} (실행 예정)"
    return rel


def _print_csv_compact_line(
    index: int,
    e: CsvPlaybackScheduleEntry,
    *,
    wall_elapsed_sec: float,
    executed: bool,
) -> None:
    """콘솔 한 줄: 진행(CSV t)·실경과·동작·JSON·실행여부."""
    cat = _SCHEDULE_CATEGORY_KO.get(e.category, e.category)
    action = _short_schedule_title(e.title_ko)
    json_lbl = _json_exec_label(e, executed=executed)
    print(
        f"{_PRINT_PREFIX} [{index:02d}] CSV t={e.time_sec:7.2f}s | "
        f"경과 {wall_elapsed_sec:6.2f}s | {cat} | {action} | {json_lbl}",
        flush=True,
    )


def _lines_for_schedule_entry(
    e: CsvPlaybackScheduleEntry,
    index: int,
    *,
    speed_scale: float = 1.0,
    steps_summary: str = "",
    play_now: bool = False,
    wall_elapsed_sec: Optional[float] = None,
    waited_sec: float = 0.0,
) -> List[str]:
    """UI 타임라인·콘솔 Play 로그 공통 본문 (한글)."""
    cat_ko = _SCHEDULE_CATEGORY_KO.get(e.category, e.category)
    sp = max(0.1, float(speed_scale or 1.0))
    extra = ""
    if wall_elapsed_sec is not None:
        extra += f" | 실경과 {wall_elapsed_sec:.3f}s"
    if waited_sec > 0.05:
        extra += f" | 대기 {waited_sec:.2f}s"
    if play_now:
        extra += " | ▶ 지금 실행"
    lines: List[str] = [
        f"── [{index:02d}] t={e.time_sec:8.3f}s │ {cat_ko}{extra} ──",
        e.title_ko,
    ]
    if e.event_name:
        rel, _abs = _event_json_paths_for_display(e.event_name)
        if e.json_path:
            missing = not Path(e.json_path).is_file()
            flag = " ⚠ 파일 없음" if missing else ""
            lines.append(f"  · JSON 파일: {rel}")
            lines.append(f"  · 경로: {e.json_path}{flag}")
        else:
            lines.append(f"  · JSON 파일: {rel}")
    if e.csv_read_ko:
        lines.append(f"  · 읽은 값: {e.csv_read_ko}")
    if e.meaning_ko:
        lines.append(f"  · 의미: {e.meaning_ko}")
    if e.exec_ko:
        lines.append(f"  · 실행: {e.exec_ko}")
    if play_now and e.step_count > 0:
        lines.append(
            f"  · 동작: JSON 스텝 {e.step_count}개 [{steps_summary}] 재생 시작 (배속 {sp:g}x)"
        )
    elif e.step_count > 0:
        if steps_summary:
            lines.append(
                f"  · JSON 스텝: {e.step_count}개 [{steps_summary}] "
                f"(정의된 타입 전부 실행, Play 시 배속 {sp:g}x)"
            )
        else:
            lines.append(
                f"  · JSON 스텝: {e.step_count}개 (이벤트 JSON + 자동 Z MOVE)"
            )
    elif e.category == "dwell":
        lines.append("  · 동작: (애니 없음 — CSV 체류 구간 로그만)")
    return lines


def _print_schedule_lines(lines: List[str]) -> None:
    for line in lines:
        print(f"{_PRINT_PREFIX} {line}", flush=True)


def format_csv_playback_schedule_row(
    index: int,
    e: CsvPlaybackScheduleEntry,
    *,
    speed_scale: float = 1.0,
) -> str:
    """타임라인 UI 한 줄 (``format_csv_playback_schedule`` 과 동일 형식)."""
    cat = _SCHEDULE_CATEGORY_KO.get(e.category, e.category)
    action = _short_schedule_title(e.title_ko)
    json_lbl = _json_exec_label(e, executed=False)
    sp = max(0.1, float(speed_scale or 1.0))
    return (
        f"[{index:02d}] t={e.time_sec:7.2f}s | {cat} | {action} | {json_lbl} | 배속 {sp:g}x"
    )


def format_csv_playback_schedule(
    entries: List[CsvPlaybackScheduleEntry],
    *,
    speed_scale: float = 1.0,
) -> str:
    """UI·미리보기용 간단 타임라인 (콘솔 Play 와 동일 형식)."""
    if not entries:
        return "(CSV 타임라인 없음)"
    n_act = sum(1 for e in entries if e.category != "dwell")
    sp = max(0.1, float(speed_scale or 1.0))
    lines = [
        f"=== CSV 타임라인 (t=0 기준 · {len(entries)}건 · JSON {n_act}건 · 배속 {sp:g}x) ===",
        "형식: [번호] CSV t | 동작 | JSON | (재생 중 JSON 행 = 녹색)",
        "",
    ]
    for i, e in enumerate(entries, 1):
        lines.append(format_csv_playback_schedule_row(i, e, speed_scale=sp))
    return "\n".join(lines)


def _schedule_entry_match_key(e: CsvPlaybackScheduleEntry) -> Tuple[Any, ...]:
    """타임라인 행·재생 블록 매칭 (메타/풀빌드 ``title_ko`` 차이 무시)."""
    return (
        round(float(e.time_sec), 6),
        int(e.sort_order),
        str(e.category),
        str(e.event_name),
    )


def _schedule_entry_lot_cassette(e: CsvPlaybackScheduleEntry) -> Tuple[str, int]:
    lot = str(getattr(e, "lot_id", "") or "")
    try:
        cas = int(getattr(e, "cassette_slot", 0) or 0)
    except Exception:
        cas = 0
    if lot and cas > 0:
        return lot, cas
    title = str(getattr(e, "title_ko", "") or "")
    if not lot:
        m = re.search(r"lot=['\"]([^'\"]*)['\"]", title)
        if m:
            lot = str(m.group(1) or "")
    if cas <= 0:
        m = re.search(r"웨이퍼#(\d+)", title)
        if m:
            try:
                cas = int(m.group(1))
            except Exception:
                cas = 0
    return lot, cas


def _schedule_entry_soft_match_key(e: CsvPlaybackScheduleEntry) -> Tuple[Any, ...]:
    """time_sec 변동에도 Current State/강조 매칭 — lot+cassette 포함(타 wafer 오점등 방지)."""
    row = int(getattr(e, "schedule_row", -1) or -1)
    lot, cas = _schedule_entry_lot_cassette(e)
    return (str(e.category), str(e.event_name), row, int(e.sort_order), lot, cas)


def _schedule_entry_matches_active(
    e: CsvPlaybackScheduleEntry, active: frozenset
) -> bool:
    """정확 키 우선, 실패 시 soft 키( lot+cassette ). 시간 무시 광역 매칭 금지."""
    if not active:
        return False
    if _schedule_entry_match_key(e) in active:
        return True
    soft = _schedule_entry_soft_match_key(e)
    if soft in active:
        return True
    cat = str(e.category or "")
    ev = str(e.event_name or "")
    sort_o = int(e.sort_order)
    row = int(getattr(e, "schedule_row", -1) or -1)
    lot, cas = _schedule_entry_lot_cassette(e)
    for k in active:
        if not isinstance(k, tuple):
            continue
        if len(k) == 6:
            if (
                k[0] == cat
                and k[1] == ev
                and int(k[3]) == sort_o
                and str(k[4]) == lot
                and int(k[5]) == cas
            ):
                try:
                    kr = int(k[2])
                except Exception:
                    kr = -1
                if row < 0 or kr < 0 or kr == row:
                    return True
        elif len(k) == 4 and not isinstance(k[0], (int, float)):
            if k[0] == cat and k[1] == ev and int(k[3]) == sort_o:
                try:
                    kr = int(k[2])
                except Exception:
                    kr = -1
                if row >= 0 and kr == row:
                    return True
    return False


def _stamp_schedule_row_ids(
    schedule: List[CsvPlaybackScheduleEntry],
) -> List[CsvPlaybackScheduleEntry]:
    return [replace(e, schedule_row=i) for i, e in enumerate(schedule)]


def _reattach_block_schedules(
    blocks: List[CsvTimedPlaybackBlock],
    schedule: List[CsvPlaybackScheduleEntry],
) -> List[CsvTimedPlaybackBlock]:
    keyed = {_schedule_entry_match_key(e): e for e in schedule}
    out: List[CsvTimedPlaybackBlock] = []
    for b in blocks:
        if b.schedule is None:
            out.append(b)
            continue
        ne = keyed.get(_schedule_entry_match_key(b.schedule))
        out.append(replace(b, schedule=ne) if ne is not None else b)
    return out


def _align_playback_timeline_to_first_json(
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]] = None,
) -> Tuple[List[CsvPlaybackScheduleEntry], List[CsvTimedPlaybackBlock]]:
    """선행 dwell-only 구간을 재생 시계에서 제외 — 첫 JSON 이 t≈0.

    dwell 파싱·JSON 스텝·초기 wafer visibility 는 변경하지 않는다.
    타임라인·재생 ``time_sec`` 만 shift. 첫 이벤트가 이미 JSON 이면 no-op.
    """
    blks = list(blocks or [])
    lead_is_dwell_only = False
    json_times: List[float] = []

    if blks:
        ordered = sorted(blks, key=lambda b: (float(b.time_sec), int(b.sort_order)))
        lead_is_dwell_only = not bool(ordered[0].steps)
        json_times = [float(b.time_sec) for b in blks if b.steps]
    elif schedule:
        ordered_s = sorted(
            schedule, key=lambda e: (float(e.time_sec), int(e.sort_order))
        )
        lead_is_dwell_only = str(ordered_s[0].category or "") == "dwell"
        json_times = [
            float(e.time_sec)
            for e in schedule
            if str(e.category or "") in ("pick", "transfer", "place")
        ]
    else:
        return schedule, blks

    if not lead_is_dwell_only or not json_times:
        return schedule, blks
    shift = min(json_times)
    if shift < 1e-9:
        return schedule, blks

    def _shift_sec(t: float) -> float:
        return float(t) - shift

    new_schedule = [replace(e, time_sec=_shift_sec(e.time_sec)) for e in schedule]
    new_blocks: List[CsvTimedPlaybackBlock] = []
    for b in blks:
        sched = b.schedule
        if sched is not None:
            sched = replace(sched, time_sec=_shift_sec(sched.time_sec))
        new_blocks.append(
            replace(
                b,
                time_sec=_shift_sec(b.time_sec),
                schedule=sched,
            )
        )
    if not is_csv_playback_compact_log():
        print(
            f"{_PRINT_PREFIX} playback timeline: leading dwell {shift:.3f}s skipped "
            f"(first JSON → t=0)",
            flush=True,
        )
    return new_schedule, new_blocks


def _block_from_schedule(
    sched: CsvPlaybackScheduleEntry,
    steps: LamSimJsonSteps,
    *,
    label: str,
) -> CsvTimedPlaybackBlock:
    return CsvTimedPlaybackBlock(
        time_sec=float(sched.time_sec),
        sort_order=int(sched.sort_order),
        category=str(sched.category),
        label=label,
        steps=list(steps),
        schedule=sched,
    )


def _pick_schedule_entry_meta(
    *,
    time_sec: float,
    foup_index: int,
    cassette_slot: int,
    lot_id: str,
) -> CsvPlaybackScheduleEntry:
    event, _num = _resolve_foup_event_name(foup_index, cassette_slot, "pick")
    _ev, json_path = _schedule_entry_json_fields(event)
    return CsvPlaybackScheduleEntry(
        time_sec=float(time_sec),
        sort_order=_SCHEDULE_CATEGORY_ORDER["pick"],
        category="pick",
        title_ko=(
            f"[재생] FOUP{foup_index} → ATM 팔 픽업 · lot={lot_id!r} · 웨이퍼#{cassette_slot}"
        ),
        csv_read_ko=(
            "투어 첫 dwell 이 AtmArm 이므로, CSV 이전에 FOUP 에서 웨이퍼를 집어 올림 "
            f"(foup{foup_index}_{cassette_slot})."
        ),
        meaning_ko="공정 투어 시작 — FOUP 슬롯에서 ATM EndEffector 로 pick.",
        exec_ko=_foup_exec_hint(foup_index, cassette_slot, "pick"),
        step_count=_event_step_count_estimate(event),
        event_name=event,
        json_path=json_path,
        lot_id=str(lot_id or ""),
        cassette_slot=int(cassette_slot or 0),
    )


def _place_schedule_entry_meta(
    *,
    time_sec: float,
    foup_index: int,
    cassette_slot: int,
    lot_id: str,
) -> CsvPlaybackScheduleEntry:
    event, _num = _resolve_foup_event_name(foup_index, cassette_slot, "place")
    _ev, json_path = _schedule_entry_json_fields(event)
    return CsvPlaybackScheduleEntry(
        time_sec=float(time_sec),
        sort_order=_SCHEDULE_CATEGORY_ORDER["place"],
        category="place",
        title_ko=(
            f"[재생] ATM 팔 → FOUP{foup_index} 반환 · lot={lot_id!r} · 웨이퍼#{cassette_slot}"
        ),
        csv_read_ko=(
            f"투어 마지막 dwell 이 AtmArm → FOUP{foup_index} place 합성 "
            f"(슬롯 {cassette_slot}, pick 출처 무관)."
        ),
        meaning_ko="공정 투어 종료 SSOT — ATM 팔에서 FOUP 슬롯으로 반환.",
        exec_ko=_foup_exec_hint(foup_index, cassette_slot, "place"),
        step_count=_event_step_count_estimate(event),
        event_name=event,
        json_path=json_path,
        lot_id=str(lot_id or ""),
        cassette_slot=int(cassette_slot or 0),
    )


def _transfer_schedule_entry_meta(
    prev: DwellRecord, curr: DwellRecord
) -> Optional[CsvPlaybackScheduleEntry]:
    if not _same_wafer_dwell(prev, curr):
        return None
    hint = _transfer_event_hint(prev, curr)
    robot = _classify_transfer_robot(prev.slot_key, curr.slot_key)
    try:
        event, _num = _resolve_transfer_event_name(prev, curr)
        _ev, json_path = _schedule_entry_json_fields(event)
        sc = _event_step_count_estimate(event)
    except ValueError:
        event, json_path, sc = "", "", 0
    return CsvPlaybackScheduleEntry(
        time_sec=float(curr.start_sec),
        sort_order=_SCHEDULE_CATEGORY_ORDER["transfer"],
        category="transfer",
        title_ko=(
            f"[재생] 이송({robot}) · lot={curr.lot_id!r} · 웨이퍼#{curr.cassette_slot} · "
            f"{_slot_key_label_ko(prev.slot_key)} → {_slot_key_label_ko(curr.slot_key)}"
        ),
        csv_read_ko=(
            f"이전 행 module_nm={prev.module_nm!r} (끝 {prev.end_sec:.3f}s) → "
            f"다음 행 module_nm={curr.module_nm!r} (시작 {curr.start_sec:.3f}s)"
        ),
        meaning_ko=(
            "CSV 규칙: 한 행은 '머무름'만 표시. 이송 애니는 **다음 module_nm** 으로 "
            "어디로 옮겼는지 추론해 이벤트 JSON 을 실행."
        ),
        exec_ko=hint,
        step_count=sc,
        event_name=event,
        json_path=json_path,
        lot_id=str(curr.lot_id or ""),
        cassette_slot=int(curr.cassette_slot or 0),
    )


def _atm_arm_action_kind_from_entry(
    *,
    category: str,
    event_name: str,
    title_ko: str = "",
) -> Optional[str]:
    """ATM 팔 관련 pick/place 판별. 해당 없으면 None."""
    cat = (category or "").strip().lower()
    en = (event_name or "").strip().lower()
    title = title_ko or ""
    is_atm = (
        en.startswith("atm_")
        or cat in ("pick", "place", "aligner_pick", "aligner_place")
        or ("ATM" in title and cat == "transfer")
    )
    if not is_atm:
        return None
    if cat in ("pick", "aligner_pick") or en.endswith("_pick"):
        return "pick"
    if cat in ("place", "aligner_place") or en.endswith("_place"):
        return "place"
    if cat == "transfer":
        if "→" in title:
            left, right = title.split("→", 1)
            if "ATM 팔" in left or "팔" in left:
                return "place"
            if "ATM 팔" in right or "팔" in right:
                return "pick"
    return None


def _atm_arm_action_kind_from_block(block: "CsvTimedPlaybackBlock") -> Optional[str]:
    sch = getattr(block, "schedule", None)
    if sch is None:
        return None
    return _atm_arm_action_kind_from_entry(
        category=str(getattr(sch, "category", "") or ""),
        event_name=str(getattr(sch, "event_name", "") or ""),
        title_ko=str(getattr(sch, "title_ko", "") or ""),
    )


def _atm_arm_action_duration_sec(block: "CsvTimedPlaybackBlock") -> float:
    steps = [s for s in (getattr(block, "steps", None) or []) if isinstance(s, dict)]
    if steps:
        try:
            return float(_lam_estimate_raw_duration_sec(steps))
        except Exception:
            pass
    sch = getattr(block, "schedule", None)
    if sch is not None and int(getattr(sch, "step_count", 0) or 0) > 0:
        return float(ATM_ARM_SERIAL_FALLBACK_DUR_SEC)
    return float(ATM_ARM_SERIAL_FALLBACK_DUR_SEC)


def _shift_schedule_entry_time(
    ent: CsvPlaybackScheduleEntry, new_t: float
) -> CsvPlaybackScheduleEntry:
    return replace(ent, time_sec=float(new_t))


def _wafer_id_from_schedule_title(title: str) -> Optional[Tuple[str, int]]:
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


def _wafer_id_from_schedule_entry(ent: Any) -> Optional[Tuple[str, int]]:
    """스케줄 엔트리의 lot/cassette (FOUP별 웨이퍼 식별). title 파싱은 fallback."""
    if ent is None:
        return None
    lid = str(getattr(ent, "lot_id", "") or "").strip()
    try:
        cas = int(getattr(ent, "cassette_slot", 0) or 0)
    except Exception:
        cas = 0
    if lid and cas >= 0:
        return (lid, cas)
    return _wafer_id_from_schedule_title(str(getattr(ent, "title_ko", "") or ""))


def _slot_number_hint_from_title(title: str) -> Optional[int]:
    """title_ko 에서 airlockN_M / 슬롯 힌트."""
    am = re.search(r"airlock\d+_(\d+)", title or "", flags=re.IGNORECASE)
    if am:
        try:
            return int(am.group(1))
        except Exception:
            return None
    return None


def _resolve_action_slot_and_kind(
    *,
    event_name: str,
    title_ko: str = "",
    category: str = "",
) -> Optional[Tuple[str, str, str]]:
    """``(slot_key, pick|place, arm_key)`` — 슬롯 점유/팔 홀딩 대상만. 아니면 None.

    arm_key: ``LOGICAL:ATM_ARM`` / ``LOGICAL:VTM_EE_L`` / ``LOGICAL:VTM_EE_R`` / "".
    """
    en = (event_name or "").strip()
    cat = (category or "").strip().lower()
    title = title_ko or ""
    po = ""
    if cat in ("pick", "aligner_pick") or en.lower().endswith("_pick"):
        po = "pick"
    elif cat in ("place", "aligner_place") or en.lower().endswith("_place"):
        po = "place"
    elif cat == "transfer" and ("→" in title or "->" in title):
        sep = "→" if "→" in title else "->"
        left, right = title.split(sep, 1)
        if "ATM 팔" in left or ("팔" in left and "ATM" in left):
            po = "place"
        elif "ATM 팔" in right or ("팔" in right and "ATM" in right):
            po = "pick"
        elif "팔" in left:
            po = "place"
        elif "팔" in right:
            po = "pick"
    if po not in ("pick", "place"):
        return None

    arm_key = ""
    en_l = en.lower()
    if en_l.startswith("atm_") or cat in ("pick", "place", "aligner_pick", "aligner_place"):
        arm_key = LOGICAL_SLOT_ATM_ARM
    elif en_l.startswith("vtm_"):
        if "_left_" in en_l:
            arm_key = (
                LOGICAL_SLOT_VTM_EE_R if VTM_END_EFFECTOR_SWAP_HANDS else LOGICAL_SLOT_VTM_EE_L
            )
        elif "_right_" in en_l:
            arm_key = (
                LOGICAL_SLOT_VTM_EE_L if VTM_END_EFFECTOR_SWAP_HANDS else LOGICAL_SLOT_VTM_EE_R
            )

    wid = _wafer_id_from_schedule_title(title)
    slot_hint = _slot_number_hint_from_title(title)
    if slot_hint is None and wid is not None:
        # FOUP/buffer/cooling: cassette 번호를 slot_number 로
        if re.match(r"^atm_foup\d+_", en_l) or re.match(r"^atm_buffer\d+_", en_l):
            slot_hint = int(wid[1])
        elif en_l.startswith("atm_coolstation_"):
            slot_hint = int(wid[1]) if 1 <= int(wid[1]) <= 7 else None

    # title 에 이미 airlockN_M / chamberN 이 있으면 그대로 사용
    am_full = re.search(r"(airlock\d+_\d+)", title, flags=re.IGNORECASE)
    if am_full and ("airlock" in en_l or "에어록" in title):
        return am_full.group(1).lower(), po, arm_key
    cm_full = re.search(r"\b(chamber\d+)\b", title, flags=re.IGNORECASE)
    if cm_full and "chamber" in en_l:
        return cm_full.group(1).lower(), po, arm_key

    try:
        from .lam_event_sequences import slot_key_for_event

        sk = slot_key_for_event(en, slot_hint)
    except Exception:
        # transfer 등 이벤트명 비표준 — title 의 목적지/출발 slot 라벨은 스킵
        meta = _parse_vtm_event_meta(en, title)
        if meta is None:
            return None
        sk, _hand, po2 = meta
        po = po2
        if not arm_key and en_l.startswith("vtm_"):
            if "_left_" in en_l:
                arm_key = (
                    LOGICAL_SLOT_VTM_EE_R
                    if VTM_END_EFFECTOR_SWAP_HANDS
                    else LOGICAL_SLOT_VTM_EE_L
                )
            elif "_right_" in en_l:
                arm_key = (
                    LOGICAL_SLOT_VTM_EE_L
                    if VTM_END_EFFECTOR_SWAP_HANDS
                    else LOGICAL_SLOT_VTM_EE_R
                )
        return sk, po, arm_key
    return sk, po, arm_key


def _parse_vtm_event_meta(
    event_name: str, title_ko: str = ""
) -> Optional[Tuple[str, str, str]]:
    """``(station_key, hand, pick|place)`` — VTM 이벤트만. 아니면 None."""
    en = (event_name or "").strip().lower()
    m = _VTM_EVENT_NAME_RE.match(en)
    if not m:
        return None
    chamber_n, airlock_n, hand, po = m.group(1), m.group(2), m.group(3).lower(), m.group(4).lower()
    if chamber_n:
        return f"chamber{int(chamber_n)}", hand, po
    title = title_ko or ""
    am = re.search(r"airlock\d+_\d+", title, flags=re.IGNORECASE)
    if am:
        return am.group(0).lower(), hand, po
    return f"airlock{int(airlock_n)}", hand, po


def _seed_slot_occupancy_from_dwells(
    dwells: Optional[Sequence["DwellRecord"]],
) -> Dict[str, Optional[Tuple[str, int]]]:
    """투어 첫 dwell 위치 → 초기 슬롯 점유."""
    occ: Dict[str, Optional[Tuple[str, int]]] = {}
    if not dwells:
        return occ
    try:
        for (lot_id, cassette_slot), tour in _group_dwell_tours(list(dwells)):
            if not tour:
                continue
            first = tour[0]
            sk = str(getattr(first, "slot_key", "") or "")
            wid = (str(lot_id), int(cassette_slot))
            if sk == LOGICAL_SLOT_ATM_ARM:
                fi = int(getattr(first, "foup_index", 1) or 1)
                occ[f"foup{fi}_{int(cassette_slot)}"] = wid
            elif sk in (LOGICAL_SLOT_VTM_EE_L, LOGICAL_SLOT_VTM_EE_R):
                continue
            elif sk:
                occ[sk] = wid
    except Exception:
        pass
    return occ


class _OccActionNode:
    __slots__ = (
        "slot",
        "kind",
        "arm",
        "wafer",
        "t",
        "ord",
        "sched_i",
        "block_i",
    )

    def __init__(
        self,
        *,
        slot: str,
        kind: str,
        arm: str,
        wafer: Optional[Tuple[str, int]],
        t: float,
        ord: int,
        sched_i: int,
        block_i: int,
    ) -> None:
        self.slot = slot
        self.kind = kind
        self.arm = arm
        self.wafer = wafer
        self.t = float(t)
        self.ord = int(ord)
        self.sched_i = int(sched_i)
        self.block_i = int(block_i)


def _collect_occ_action_nodes(
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]],
) -> List[_OccActionNode]:
    sched_by_id = {id(e): i for i, e in enumerate(schedule)}
    nodes: List[_OccActionNode] = []

    def _add(
        *,
        si: int,
        bi: int,
        t: float,
        ord_v: int,
        en: str,
        title: str,
        cat: str,
        wafer: Optional[Tuple[str, int]] = None,
    ) -> None:
        meta = _resolve_action_slot_and_kind(
            event_name=en, title_ko=title, category=cat
        )
        if meta is None:
            return
        slot, po, arm = meta
        wid = wafer if wafer is not None else _wafer_id_from_schedule_title(title)
        nodes.append(
            _OccActionNode(
                slot=str(slot),
                kind=po,
                arm=str(arm or ""),
                wafer=wid,
                t=float(t),
                ord=int(ord_v),
                sched_i=si,
                block_i=bi,
            )
        )

    if blocks:
        for bi, b in enumerate(blocks):
            sch = b.schedule
            if sch is None:
                continue
            cat = str(getattr(sch, "category", "") or "")
            if cat == "dwell":
                continue
            en = str(getattr(sch, "event_name", "") or "")
            title = str(getattr(sch, "title_ko", "") or "")
            si = sched_by_id.get(id(sch), -1)
            if si < 0:
                key = _schedule_entry_match_key(sch)
                for j, e in enumerate(schedule):
                    if _schedule_entry_match_key(e) == key:
                        si = j
                        break
            _add(
                si=si,
                bi=bi,
                t=float(b.time_sec),
                ord_v=int(getattr(b, "sort_order", 0) or 0),
                en=en,
                title=title,
                cat=cat,
                wafer=_wafer_id_from_schedule_entry(sch),
            )
    else:
        for si, e in enumerate(schedule):
            cat = str(e.category or "")
            if cat == "dwell":
                continue
            _add(
                si=si,
                bi=-1,
                t=float(e.time_sec),
                ord_v=int(e.sort_order),
                en=str(e.event_name or ""),
                title=str(e.title_ko or ""),
                cat=cat,
                wafer=_wafer_id_from_schedule_entry(e),
            )
    nodes.sort(key=lambda n: (float(n.t), int(n.ord), n.sched_i if n.sched_i >= 0 else n.block_i))
    return nodes


def _commit_action_node_time(
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]],
    n: _OccActionNode,
    new_t: float,
    new_ord: Optional[int] = None,
) -> None:
    """단일 액션의 time_sec(·sort_order)만 교체 — 통째 시프트 아님."""
    n.t = float(new_t)
    if new_ord is not None:
        n.ord = int(new_ord)
    if n.sched_i >= 0 and n.sched_i < len(schedule):
        ent = schedule[n.sched_i]
        kw = {"time_sec": float(new_t)}
        if new_ord is not None:
            kw["sort_order"] = int(new_ord)
        schedule[n.sched_i] = replace(ent, **kw)
    if blocks is not None and n.block_i >= 0 and n.block_i < len(blocks):
        b = blocks[n.block_i]
        sch = b.schedule
        new_sch = sch
        if sch is not None:
            skw = {"time_sec": float(new_t)}
            if new_ord is not None:
                skw["sort_order"] = int(new_ord)
            new_sch = replace(sch, **skw)
            if n.sched_i >= 0 and n.sched_i < len(schedule):
                schedule[n.sched_i] = new_sch
        bkw: Dict[str, Any] = {"time_sec": float(new_t), "schedule": new_sch}
        if new_ord is not None:
            bkw["sort_order"] = int(new_ord)
        blocks[n.block_i] = replace(b, **bkw)


def _swap_action_order(
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]],
    earlier: _OccActionNode,
    later: _OccActionNode,
) -> None:
    """later 를 earlier 앞으로 — 두 이벤트의 (t, sort_order) 만 교환."""
    ta, tb = float(earlier.t), float(later.t)
    oa, ob = int(earlier.ord), int(later.ord)
    try:
        print(
            f"{_PRINT_PREFIX} [swap] t={min(ta, tb):.1f}s "
            f"{earlier.kind}@{earlier.slot!r} "
            f"↔ {later.kind}@{later.slot!r} "
            f"(wafer {earlier.wafer} / {later.wafer})",
            flush=True,
        )
    except Exception:
        pass
    _commit_action_node_time(schedule, blocks, later, ta, oa)
    _commit_action_node_time(schedule, blocks, earlier, tb, ob)


def _apply_slot_occupancy_order_swaps(
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]] = None,
    dwells: Optional[Sequence["DwellRecord"]] = None,
) -> Tuple[List[CsvPlaybackScheduleEntry], Optional[List[CsvTimedPlaybackBlock]]]:
    """슬롯 점유 충돌 시에만 앞/뒤 동작 순서를 swap (Aligner 철학).

    - place 대상에 이미 웨이퍼 → 같은 슬롯의 이후 pick 을 앞으로
    - pick 대상이 비어 있음 → 같은 슬롯의 이후 place 를 앞으로
    통째로 시각을 밀지 않음. 무관한 이벤트는 불변.
    """
    if not schedule:
        return schedule, blocks

    max_passes = max(8, len(schedule) * 2)
    for _pass in range(max_passes):
        nodes = _collect_occ_action_nodes(schedule, blocks)
        if not nodes:
            break
        occ = _seed_slot_occupancy_from_dwells(dwells)
        swapped = False
        for i, n in enumerate(nodes):
            sk = n.slot
            if n.kind == "place":
                cur = occ.get(sk)
                if cur is not None:
                    # 점유 중 place → 이후 같은 슬롯 pick 을 앞으로
                    resolver: Optional[_OccActionNode] = None
                    for m in nodes[i + 1 :]:
                        if m.kind != "pick" or m.slot != sk:
                            continue
                        if cur is not None and m.wafer is not None and m.wafer != cur:
                            continue
                        resolver = m
                        break
                    if resolver is None:
                        for m in nodes[i + 1 :]:
                            if m.kind == "pick" and m.slot == sk:
                                resolver = m
                                break
                    if resolver is not None:
                        _swap_action_order(schedule, blocks, n, resolver)
                        swapped = True
                        break
                # 충돌 없거나 해소 불가 → 점유 반영 후 진행
                if n.wafer is not None:
                    occ[sk] = n.wafer
                else:
                    occ[sk] = ("?", -1)
            elif n.kind == "pick":
                cur = occ.get(sk)
                if cur is None:
                    resolver = None
                    for m in nodes[i + 1 :]:
                        if m.kind == "place" and m.slot == sk:
                            resolver = m
                            break
                    if resolver is not None:
                        _swap_action_order(schedule, blocks, n, resolver)
                        swapped = True
                        break
                else:
                    occ[sk] = None
        if not swapped:
            break

    schedule.sort(key=lambda e: (float(e.time_sec), int(e.sort_order)))
    if blocks is not None:
        blocks.sort(key=lambda b: (float(b.time_sec), int(b.sort_order)))
    return schedule, blocks


def _apply_arm_holding_order_swaps(
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]] = None,
) -> Tuple[List[CsvPlaybackScheduleEntry], Optional[List[CsvTimedPlaybackBlock]]]:
    """팔이 웨이퍼를 든 채 또 pick 하려 하면 — 같은 팔의 이후 place 를 앞으로 swap.

    ATM/VTM 공통. 시각 대량 시프트 없음.
    """
    if not schedule:
        return schedule, blocks

    max_passes = max(8, len(schedule) * 2)
    for _pass in range(max_passes):
        nodes = _collect_occ_action_nodes(schedule, blocks)
        arm_nodes = [n for n in nodes if n.arm]
        if not arm_nodes:
            break
        holding: Dict[str, bool] = {}
        swapped = False
        for i, n in enumerate(arm_nodes):
            h = n.arm
            if n.kind == "place":
                holding[h] = False
                continue
            # pick
            if holding.get(h):
                resolver = None
                for m in arm_nodes[i + 1 :]:
                    if m.arm == h and m.kind == "place":
                        resolver = m
                        break
                if resolver is not None:
                    _swap_action_order(schedule, blocks, n, resolver)
                    swapped = True
                    break
            holding[h] = True
        if not swapped:
            break

    schedule.sort(key=lambda e: (float(e.time_sec), int(e.sort_order)))
    if blocks is not None:
        blocks.sort(key=lambda b: (float(b.time_sec), int(b.sort_order)))
    return schedule, blocks


# AtmArm-last FOUP place 직후 — 사이 끼어든 ATM 동작을 place 직후로 미룸 (정렬만, 삭제 없음)
ATM_FOUP_PLACE_FOLLOW_GAP_SEC: float = 0.05


def _occ_node_is_foup_place(
    n: "_OccActionNode",
    schedule: List[CsvPlaybackScheduleEntry],
) -> bool:
    if str(n.kind or "") != "place":
        return False
    if 0 <= int(n.sched_i) < len(schedule):
        en = str(schedule[int(n.sched_i)].event_name or "")
        return bool(re.match(r"^atm_foup\d+_place$", en, re.IGNORECASE))
    return False


def _occ_node_action_duration_sec(
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]],
    n: "_OccActionNode",
) -> float:
    if blocks is not None and 0 <= int(n.block_i) < len(blocks):
        try:
            return max(0.05, float(_atm_arm_action_duration_sec(blocks[int(n.block_i)])))
        except Exception:
            pass
    return float(ATM_ARM_SERIAL_FALLBACK_DUR_SEC)


def _occ_node_wafer_id(
    n: "_OccActionNode",
    schedule: List[CsvPlaybackScheduleEntry],
) -> Optional[Tuple[str, int]]:
    if n.wafer is not None:
        return n.wafer
    if 0 <= int(n.sched_i) < len(schedule):
        return _wafer_id_from_schedule_entry(schedule[int(n.sched_i)])
    return None


def _atm_arm_last_hold_start_sec(
    dwells: Optional[Sequence["DwellRecord"]],
    wafer: Optional[Tuple[str, int]],
) -> Optional[float]:
    """해당 FOUP 웨이퍼 투어가 AtmArm-last 이면 그 AtmArm dwell ``start_sec``.

    FOUP place 보호 구간의 하한 — 같은 구간에 끼는 **다른 FOUP/웨이퍼 ATM** 만
    place 이후로 미룬다 (전체 ATM 후퇴 아님).
    """
    if not dwells or wafer is None:
        return None
    lot_id, cassette_slot = wafer
    for (lid, cas), tour in _group_dwell_tours(list(dwells)):
        if str(lid) != str(lot_id) or int(cas) != int(cassette_slot):
            continue
        if not tour:
            return None
        last = tour[-1]
        if str(getattr(last, "slot_key", "") or "") != LOGICAL_SLOT_ATM_ARM:
            return None
        try:
            return float(last.start_sec)
        except Exception:
            return None
    return None


def _apply_atm_foup_place_gap_serial(
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]] = None,
    dwells: Optional[Sequence["DwellRecord"]] = None,
) -> Tuple[List[CsvPlaybackScheduleEntry], Optional[List[CsvTimedPlaybackBlock]]]:
    """FOUP(웨이퍼)별 AtmArm-last FOUP place 사이 타 ATM → place 직후 미룸.

    - 판정 단위: ``(lot_id, cassette_slot)`` (= FOUP 매핑된 웨이퍼). FOUP 끼리 독립.
    - FOUP place 자체·해당 wafer 의 place 이전 ATM 은 유지.
    - 보호 구간: AtmArm-last dwell 시작(또는 직전 같은 wafer ATM) ~ place 시작.
      그 사이 **다른 wafer ATM** 만 place 완료 직후로 시간 이동 (삭제 없음).
    - ``aligner_fix`` 포함 전 plan 모드에서 적용.
    """
    if not schedule:
        return schedule, blocks

    gap = float(ATM_FOUP_PLACE_FOLLOW_GAP_SEC)
    max_passes = max(16, len(schedule) * 3)
    for _pass in range(max_passes):
        nodes = _collect_occ_action_nodes(schedule, blocks)
        atm_nodes = [
            n for n in nodes if str(n.arm or "") == LOGICAL_SLOT_ATM_ARM
        ]
        if not atm_nodes:
            break
        place_nodes = [
            n for n in atm_nodes if _occ_node_is_foup_place(n, schedule)
        ]
        if not place_nodes:
            break
        place_nodes.sort(
            key=lambda n: (
                float(n.t),
                int(n.ord),
                int(n.sched_i) if n.sched_i >= 0 else int(n.block_i),
            )
        )
        moved = False
        for pn in place_nodes:
            wafer_p = _occ_node_wafer_id(pn, schedule)
            t_place = float(pn.t)
            # 같은 wafer 의 place 직전 마지막 ATM 액션
            t_lo_action: Optional[float] = None
            for n in atm_nodes:
                if int(n.sched_i) == int(pn.sched_i) and int(n.block_i) == int(
                    pn.block_i
                ):
                    continue
                wid_n = _occ_node_wafer_id(n, schedule)
                if (
                    wafer_p is not None
                    and wid_n == wafer_p
                    and float(n.t) < t_place - 1e-12
                ):
                    if t_lo_action is None or float(n.t) > float(t_lo_action):
                        t_lo_action = float(n.t)
            # AtmArm-last dwell 시작 (FOUP별 독립 보호 하한)
            t_hold = _atm_arm_last_hold_start_sec(dwells, wafer_p)
            if t_hold is not None:
                t_lo = float(t_hold)
            elif t_lo_action is not None:
                t_lo = float(t_lo_action)
            else:
                t_lo = t_place - 1.0e9

            between: List[_OccActionNode] = []
            for n in atm_nodes:
                if int(n.sched_i) == int(pn.sched_i) and int(n.block_i) == int(
                    pn.block_i
                ):
                    continue
                tn = float(n.t)
                if not (float(t_lo) + 1e-12 < tn < t_place - 1e-12):
                    continue
                wid_n = _occ_node_wafer_id(n, schedule)
                # 같은 FOUP 웨이퍼의 place 직전 자기 ATM 은 유지
                if wafer_p is not None and wid_n == wafer_p:
                    continue
                between.append(n)
            if not between:
                continue

            between.sort(
                key=lambda n: (
                    float(n.t),
                    int(n.ord),
                    int(n.sched_i) if n.sched_i >= 0 else int(n.block_i),
                )
            )
            place_dur = _occ_node_action_duration_sec(schedule, blocks, pn)
            cur_t = t_place + max(gap, float(place_dur))
            for n in between:
                old_t = float(n.t)
                new_t = float(cur_t)
                if abs(new_t - old_t) > 1e-9:
                    try:
                        print(
                            f"{_PRINT_PREFIX} [atm-foup-place-serial] "
                            f"defer ATM {n.kind}@{n.slot!r} wafer={_occ_node_wafer_id(n, schedule)} "
                            f"t={old_t:.3f}→{new_t:.3f}s "
                            f"(after foup place wafer={wafer_p} @{t_place:.3f}s)",
                            flush=True,
                        )
                    except Exception:
                        pass
                    new_ord = int(n.ord)
                    _commit_action_node_time(
                        schedule, blocks, n, new_t, new_ord=new_ord
                    )
                    moved = True
                dur = _occ_node_action_duration_sec(schedule, blocks, n)
                cur_t = new_t + max(gap, float(dur))

        if not moved:
            break
        schedule.sort(key=lambda e: (float(e.time_sec), int(e.sort_order)))
        if blocks is not None:
            blocks.sort(key=lambda b: (float(b.time_sec), int(b.sort_order)))

    schedule.sort(key=lambda e: (float(e.time_sec), int(e.sort_order)))
    if blocks is not None:
        blocks.sort(key=lambda b: (float(b.time_sec), int(b.sort_order)))
    return schedule, blocks


def _apply_atm_arm_place_before_pick(
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]] = None,
) -> Tuple[List[CsvPlaybackScheduleEntry], Optional[List[CsvTimedPlaybackBlock]]]:
    """호환 별칭 — 팔 홀딩 충돌 시 순서 swap."""
    return _apply_arm_holding_order_swaps(schedule, blocks)


def _apply_vtm_slot_hand_serial(
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]] = None,
) -> Tuple[List[CsvPlaybackScheduleEntry], Optional[List[CsvTimedPlaybackBlock]]]:
    """호환 별칭 — 슬롯 점유 충돌 시 순서 swap (dwells 없이 호출 시 초기점유 빈 맵)."""
    return _apply_slot_occupancy_order_swaps(schedule, blocks, dwells=None)


def _enforce_aligner_absolute_rules(
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]],
) -> Tuple[List[CsvPlaybackScheduleEntry], Optional[List[CsvTimedPlaybackBlock]]]:
    """Aligner pick 직후 같은 wafer 가 airlock 외로 place 되면 해당 pick 제거.

    ATM/VTM greedy swap 등으로 생긴 Aligner→buffer / Aligner→FOUP 를 plan 단계에서 차단.
    """
    from .lam_aligner_process_rules import (
        is_airlock_place_event,
        is_forbidden_after_aligner_pick_event,
    )

    if not schedule:
        return schedule, blocks

    ordered = sorted(
        enumerate(schedule),
        key=lambda it: (float(it[1].time_sec), int(it[1].sort_order), it[0]),
    )
    remove_idx: set = set()

    for pos, (si, e) in enumerate(ordered):
        cat = str(e.category or "")
        en = str(e.event_name or "")
        if cat != "aligner_pick" and en != "atm_aligner_pick":
            continue
        lot, cas = _schedule_entry_lot_cassette(e)
        next_act: Optional[CsvPlaybackScheduleEntry] = None
        for _pos2, (sj, o) in enumerate(ordered[pos + 1 :], start=pos + 1):
            if sj in remove_idx:
                continue
            o_lot, o_cas = _schedule_entry_lot_cassette(o)
            if lot and (o_lot != lot or int(o_cas) != int(cas)):
                continue
            o_cat = str(o.category or "")
            if o_cat == "dwell":
                continue
            if o_cat in ("aligner_pick", "aligner_place"):
                continue
            next_act = o
            break
        if next_act is None:
            continue
        nen = str(next_act.event_name or "")
        ok = is_airlock_place_event(nen)
        if not ok and (
            is_forbidden_after_aligner_pick_event(nen)
            or (
                nen.startswith("atm_")
                and nen.endswith("_place")
                and not is_airlock_place_event(nen)
            )
        ):
            remove_idx.add(si)
            _lam_sim_log_build(
                "csv_tour",
                f"Aligner 절대규칙: pick 제거 lot={lot!r} wafer#{cas} "
                f"— 직후 금지/비-airlock place {nen!r} (t={float(e.time_sec):.3f}s)",
            )

    if not remove_idx:
        return schedule, blocks

    new_schedule = [e for i, e in enumerate(schedule) if i not in remove_idx]
    new_blocks: Optional[List[CsvTimedPlaybackBlock]] = blocks
    if blocks is not None:
        # schedule match key 로 블록 제거 (aligner_pick)
        drop_keys = {
            _schedule_entry_match_key(schedule[i]) for i in remove_idx if i < len(schedule)
        }
        new_blocks = [
            b
            for b in blocks
            if b.schedule is None
            or _schedule_entry_match_key(b.schedule) not in drop_keys
        ]
    new_schedule.sort(key=lambda e: (float(e.time_sec), int(e.sort_order)))
    if new_blocks is not None:
        new_blocks.sort(key=lambda b: (float(b.time_sec), int(b.sort_order)))
        new_schedule = _stamp_schedule_row_ids(new_schedule)
        new_blocks = _reattach_block_schedules(new_blocks, new_schedule)
    else:
        new_schedule = _stamp_schedule_row_ids(new_schedule)
    return new_schedule, new_blocks


def _foup_index_for_lot_cassette(
    dwells: Optional[Sequence["DwellRecord"]],
    lot_id: str,
    cassette_slot: int,
) -> int:
    """lot+cassette → 투어 FOUP 번호. dwell 없으면 1."""
    lid = str(lot_id or "")
    cas = int(cassette_slot or 0)
    if dwells and lid and cas > 0:
        for d in dwells:
            if str(getattr(d, "lot_id", "") or "") != lid:
                continue
            try:
                if int(getattr(d, "cassette_slot", 0) or 0) != cas:
                    continue
            except Exception:
                continue
            try:
                fi = int(getattr(d, "foup_index", 0) or 0)
            except Exception:
                fi = 0
            if fi >= 1:
                return fi
    return 1


def _ensure_buffer_to_foup_absolute_rules(
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]],
    *,
    dwells: Optional[Sequence["DwellRecord"]] = None,
) -> Tuple[List[CsvPlaybackScheduleEntry], Optional[List[CsvTimedPlaybackBlock]]]:
    """Deprecated no-op.

    이전: Buffer pick 이후 FOUP place 없으면 강제 삽입·사이 비-FOUP place 제거.
    현재 SSOT: wafer 투어 **마지막 dwell 이 AtmArm** 이면
    ``build_csv_playback_plan`` / schedule 빌드에서 FOUP place 를 합성해 공정을 종료.
    Buffer/airlock/cooling 등 pick 출처와 무관하다.
    """
    _ = (dwells,)
    return schedule, blocks


def _apply_csv_playback_arm_serial_rules(
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]] = None,
    dwells: Optional[Sequence["DwellRecord"]] = None,
) -> Tuple[List[CsvPlaybackScheduleEntry], Optional[List[CsvTimedPlaybackBlock]]]:
    """점유·팔홀딩 충돌 구간의 순서만 swap (통째 시프트 없음).

    ``full_occ_correct`` 모드에서만 적용. ``aligner_fix``/``raw`` 에서는 no-op.
    """
    try:
        from .lam_csv_occupancy_scheduler import occupancy_scheduler_enabled

        if not occupancy_scheduler_enabled():
            return schedule, blocks
    except Exception:
        return schedule, blocks
    schedule, blocks = _apply_slot_occupancy_order_swaps(schedule, blocks, dwells=dwells)
    schedule, blocks = _apply_arm_holding_order_swaps(schedule, blocks)
    return schedule, blocks


def build_csv_playback_schedule_meta(
    dwells: List[DwellRecord],
) -> List[CsvPlaybackScheduleEntry]:
    """스텝 조립 없이 타임라인 메타만 (미리보기·캐시 miss 시 UI용, 빠름)."""
    dwells = normalize_dwell_timeline(list(dwells))
    schedule: List[CsvPlaybackScheduleEntry] = []
    for d in sort_dwells_for_playback(dwells):
        schedule.append(_dwell_schedule_entry(d))
    if not dwells:
        return schedule
    for (lot_id, cassette_slot), tour in _group_dwell_tours(dwells):
        foup_n = tour[0].foup_index
        first, last = tour[0], tour[-1]
        if first.slot_key == LOGICAL_SLOT_ATM_ARM:
            schedule.append(
                _pick_schedule_entry_meta(
                    time_sec=first.start_sec,
                    foup_index=foup_n,
                    cassette_slot=cassette_slot,
                    lot_id=lot_id,
                )
            )
            _append_aligner_after_foup_pick(
                schedule=schedule,
                blocks=None,
                anchor_time_sec=first.start_sec,
                foup_index=foup_n,
                cassette_slot=cassette_slot,
                lot_id=lot_id,
                tour=tour,
                all_dwells=dwells,
            )
        for i in range(len(tour) - 1):
            ent = _transfer_schedule_entry_meta(tour[i], tour[i + 1])
            if ent is not None:
                schedule.append(ent)
        if last.slot_key == LOGICAL_SLOT_ATM_ARM:
            schedule.append(
                _place_schedule_entry_meta(
                    time_sec=last.end_sec,
                    foup_index=foup_n,
                    cassette_slot=cassette_slot,
                    lot_id=lot_id,
                )
            )
    schedule.sort(key=lambda e: (e.time_sec, e.sort_order))
    schedule, _ = _apply_csv_playback_arm_serial_rules(schedule, None, dwells=dwells)
    schedule, _ = _enforce_aligner_absolute_rules(schedule, None)
    schedule, _ = _ensure_buffer_to_foup_absolute_rules(
        schedule, None, dwells=dwells
    )
    schedule, _ = _apply_atm_foup_place_gap_serial(schedule, None, dwells=dwells)
    schedule, _ = _align_playback_timeline_to_first_json(schedule, None)
    return _stamp_schedule_row_ids(schedule)


def build_csv_playback_plan(
    dwells: List[DwellRecord],
    *,
    progress: Optional[_ThrottledBuildProgress] = None,
) -> Tuple[List[CsvPlaybackScheduleEntry], List[CsvTimedPlaybackBlock]]:
    """dwell 체류 + pick/transfer/place 를 CSV 시각 순 **타임 블록** 으로 만든다."""
    global _csv_bulk_build_active
    dwells = normalize_dwell_timeline(list(dwells))
    schedule: List[CsvPlaybackScheduleEntry] = []
    blocks: List[CsvTimedPlaybackBlock] = []

    for d in sort_dwells_for_playback(dwells):
        ent = _dwell_schedule_entry(d)
        schedule.append(ent)
        blocks.append(
            CsvTimedPlaybackBlock(
                time_sec=ent.time_sec,
                sort_order=ent.sort_order,
                category=ent.category,
                label=f"dwell:{d.module_nm}",
                steps=[],
                schedule=ent,
            )
        )

    if not dwells:
        return schedule, blocks

    if progress is not None:
        progress._total = max(progress._total, _estimate_csv_build_units(dwells))

    _csv_bulk_build_active = True
    try:
        refresh_lam_sim_runtime_tables_from_config()
        ensure_event_json_scaffolds(overwrite=False)

        for (lot_id, cassette_slot), tour in _group_dwell_tours(dwells):
            foup_n = tour[0].foup_index
            first, last = tour[0], tour[-1]

            if first.slot_key == LOGICAL_SLOT_ATM_ARM:
                try:
                    pick_st = build_foup_pick_place_steps(
                        foup_index=foup_n,
                        cassette_slot=cassette_slot,
                        pick_or_place="pick",
                    )
                    if pick_st:
                        ent = _pick_schedule_entry(
                            time_sec=first.start_sec,
                            foup_index=foup_n,
                            cassette_slot=cassette_slot,
                            lot_id=lot_id,
                            steps=pick_st,
                        )
                        schedule.append(ent)
                        blocks.append(
                            _block_from_schedule(
                                ent, pick_st, label=f"foup{foup_n}_pick({cassette_slot})"
                            )
                        )
                        _append_aligner_after_foup_pick(
                            schedule=schedule,
                            blocks=blocks,
                            anchor_time_sec=first.start_sec,
                            foup_index=foup_n,
                            cassette_slot=cassette_slot,
                            lot_id=lot_id,
                            tour=tour,
                            all_dwells=dwells,
                            progress=progress,
                        )
                except Exception as exc:
                    _lam_sim_log_build("csv_tour", f"FOUP pick skip: {exc}")
                if progress is not None:
                    progress.tick(1)

            for i in range(len(tour) - 1):
                prev_d, curr_d = tour[i], tour[i + 1]
                tr = build_steps_for_dwell_transfer(prev_d, curr_d)
                if tr:
                    ent = _transfer_schedule_entry(prev_d, curr_d, tr)
                    schedule.append(ent)
                    blocks.append(_block_from_schedule(ent, tr, label="transfer"))
                if progress is not None:
                    progress.tick(1)

            if last.slot_key == LOGICAL_SLOT_ATM_ARM:
                # SSOT (FOUP별 독립): 해당 웨이퍼 투어 끝 AtmArm → 그 FOUP place 강제.
                # steps 비어도 place 엔트리는 넣는다. 사이 타 FOUP ATM 은 gap-serial 이 미룸.
                try:
                    place_st = build_foup_pick_place_steps(
                        foup_index=foup_n,
                        cassette_slot=cassette_slot,
                        pick_or_place="place",
                    )
                except Exception as exc:
                    place_st = []
                    _lam_sim_log_build(
                        "csv_tour",
                        f"FOUP place steps build fail (entry forced): {exc}",
                    )
                if not place_st:
                    _lam_sim_log_build(
                        "csv_tour",
                        f"FOUP place steps empty — entry still inserted "
                        f"(foup{foup_n} slot={cassette_slot} lot={lot_id!r})",
                    )
                try:
                    ent = _place_schedule_entry(
                        time_sec=last.end_sec,
                        foup_index=foup_n,
                        cassette_slot=cassette_slot,
                        lot_id=lot_id,
                        steps=place_st or [],
                    )
                    schedule.append(ent)
                    blocks.append(
                        _block_from_schedule(
                            ent,
                            place_st or [],
                            label=f"foup{foup_n}_place({cassette_slot})",
                        )
                    )
                except Exception as exc:
                    _lam_sim_log_build("csv_tour", f"FOUP place entry skip: {exc}")
                if progress is not None:
                    progress.tick(1)
    finally:
        _csv_bulk_build_active = False
        if progress is not None:
            progress.finish()

    schedule.sort(key=lambda e: (e.time_sec, e.sort_order))
    blocks.sort(key=lambda b: (b.time_sec, b.sort_order))
    schedule, blocks = _apply_csv_playback_arm_serial_rules(
        schedule, blocks, dwells=dwells
    )
    assert blocks is not None
    schedule, blocks = _enforce_aligner_absolute_rules(schedule, blocks)
    assert blocks is not None
    schedule, blocks = _ensure_buffer_to_foup_absolute_rules(
        schedule, blocks, dwells=dwells
    )
    assert blocks is not None
    # AtmArm-last FOUP place 사이 ATM 동작 → place 직후 이어재생 (삭제 없음)
    schedule, blocks = _apply_atm_foup_place_gap_serial(
        schedule, blocks, dwells=dwells
    )
    assert blocks is not None
    schedule = _stamp_schedule_row_ids(schedule)
    blocks = _reattach_block_schedules(blocks, schedule)
    return schedule, blocks


def build_and_cache_csv_playback(
    csv_path: Path,
    *,
    progress_tick: Optional[Callable[[int, int], None]] = None,
) -> CachedCsvPlayback:
    """CSV 로드 + 재생 plan 빌드 → 세션 캐시 저장."""
    path = csv_path.resolve()
    t0 = time.perf_counter()
    dwells = load_csv_dwell_timeline(path)
    prog: Optional[_ThrottledBuildProgress] = None
    if progress_tick is not None:
        prog = _ThrottledBuildProgress(
            _estimate_csv_build_units(dwells), progress_tick
        )
    schedule, blocks = build_csv_playback_plan(dwells, progress=prog)
    schedule, blocks, occ_diags = _maybe_apply_occupancy_scheduler(
        dwells, schedule, blocks
    )
    schedule, blocks = _align_playback_timeline_to_first_json(schedule, blocks)
    try:
        st = path.stat()
        mtime_ns, size = int(st.st_mtime_ns), int(st.st_size)
    except OSError:
        mtime_ns, size = 0, 0
    cached = CachedCsvPlayback(
        path=path,
        mtime_ns=mtime_ns,
        size=size,
        config_tag=_csv_playback_config_tag(),
        dwells=dwells,
        schedule=schedule,
        blocks=blocks,
        build_ms=(time.perf_counter() - t0) * 1000.0,
        occupancy_diagnostics=occ_diags,
    )
    key = _csv_cache_key(path)
    with _csv_playback_cache_lock:
        _csv_playback_cache[key] = cached
    return cached


def prepare_csv_playback(
    csv_path: Optional[str] = None,
    *,
    progress_tick: Optional[Callable[[int, int], None]] = None,
    use_cache: bool = True,
) -> CachedCsvPlayback:
    """캐시 hit 시 즉시 반환, miss 시 1회 빌드."""
    path = resolve_csv_path(csv_path)
    if use_cache:
        hit = get_cached_csv_playback(path)
        if hit is not None:
            return hit
    return build_and_cache_csv_playback(path, progress_tick=progress_tick)


def build_and_cache_from_dwells(
    virtual_path: Path,
    dwells: List["DwellRecord"],
    *,
    progress_tick: Optional[Callable[[int, int], None]] = None,
) -> CachedCsvPlayback:
    """API 파싱 결과 dwell → 재생 plan 빌드 → 세션 캐시 (가상 path)."""
    path = virtual_path.resolve()
    dwells = normalize_dwell_timeline(list(dwells))
    t0 = time.perf_counter()
    prog: Optional[_ThrottledBuildProgress] = None
    if progress_tick is not None:
        prog = _ThrottledBuildProgress(
            _estimate_csv_build_units(dwells), progress_tick
        )
    schedule, blocks = build_csv_playback_plan(dwells, progress=prog)
    schedule, blocks, occ_diags = _maybe_apply_occupancy_scheduler(
        dwells, schedule, blocks
    )
    schedule, blocks = _align_playback_timeline_to_first_json(schedule, blocks)
    t_end = float(dwells[-1].end_sec) if dwells else 0.0
    cached = CachedCsvPlayback(
        path=path,
        mtime_ns=0,
        size=len(dwells),
        config_tag=_csv_playback_config_tag(),
        dwells=dwells,
        schedule=schedule,
        blocks=blocks,
        build_ms=(time.perf_counter() - t0) * 1000.0,
        occupancy_diagnostics=occ_diags,
    )
    key = f"{path}|api|{_csv_playback_config_tag()}|n={len(dwells)}|t={t_end:.3f}"
    with _csv_playback_cache_lock:
        _csv_playback_cache[key] = cached
    return cached


def build_csv_timed_playback_blocks(dwells: List[DwellRecord]) -> List[CsvTimedPlaybackBlock]:
    """CSV 시각 동기 재생용 블록만 반환."""
    schedule, blocks = build_csv_playback_plan(dwells)
    _, blocks = _align_playback_timeline_to_first_json(schedule, blocks)
    return blocks


def build_csv_playback_steps_from_dwells(dwells: List[DwellRecord]) -> LamSimJsonSteps:
    """모든 액션 블록 스텝을 순서대로 이어 붙임 (배속·CSV 대기 없음 — 레거시/검증용)."""
    out: LamSimJsonSteps = []
    for blk in build_csv_timed_playback_blocks(dwells):
        out.extend(blk.steps)
    return out


def build_csv_playback_schedule(dwells: List[DwellRecord]) -> List[CsvPlaybackScheduleEntry]:
    """시간순 스케줄 항목만 반환 (스텝 빌드 포함 — 캐시된 plan 이 있으면 그 schedule 사용)."""
    schedule, blocks = build_csv_playback_plan(dwells)
    schedule, _ = _align_playback_timeline_to_first_json(schedule, blocks)
    return schedule


# CSV Play 실시간 진행 (UI 1초 갱신 — 콘솔 1초 틱 로그 없음)
from .lam_csv_play_screen import (
    csv_play_screen_binding,
    csv_play_screen_session,
    current_csv_play_screen,
)

_csv_play_timeline_window_refs: Dict[int, weakref.ReferenceType[Any]] = {}

_TIMELINE_UI_COLOR_DEFAULT = 0xFF9AA4B2
_TIMELINE_UI_COLOR_DWELL = 0xFF6E7580
_TIMELINE_UI_COLOR_PLAYING = 0xFF6CCB6C


def set_csv_play_timeline_highlight_callback(
    cb: Optional[Callable[[frozenset], None]],
    *,
    screen: Optional[int] = None,
) -> None:
    """Play 중 JSON 실행 행 강조: ``frozenset`` of ``_schedule_entry_match_key`` tuples."""
    csv_play_screen_session(screen).timeline_highlight_cb = cb


def register_csv_play_timeline_window(window: Any, *, screen: int = 1) -> None:
    """타임라인 녹색 강조 대상 UI (화면별 CSV 재생창)."""
    global _csv_play_timeline_window_refs
    si = int(screen)
    _csv_play_timeline_window_refs[si] = weakref.ref(window)


def unregister_csv_play_timeline_window(screen: Optional[int] = None) -> None:
    global _csv_play_timeline_window_refs
    if screen is None:
        _csv_play_timeline_window_refs = {}
        return
    _csv_play_timeline_window_refs.pop(int(screen), None)


def _csv_play_timeline_highlight_notify(
    active_keys: frozenset,
    *,
    screen: Optional[int] = None,
) -> None:
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))

    def _ui() -> None:
        notified = False
        for scr, ref in list(_csv_play_timeline_window_refs.items()):
            if int(scr) != si:
                continue
            win = ref() if ref is not None else None
            if win is None:
                continue
            try:
                win._apply_schedule_row_highlight(active_keys)
                notified = True
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} 타임라인 강조 UI 실패 (화면{scr}): {exc}",
                    flush=True,
                )
        if notified:
            return
        cb = csv_play_screen_session(si).timeline_highlight_cb
        if cb is None:
            return
        try:
            cb(active_keys)
        except Exception:
            pass

    _post_kit_main_thread(_ui)


def _status_state_line_from_schedule_entry(sched: CsvPlaybackScheduleEntry) -> str:
    """STATUS Current State — 웨이퍼# · lot · event (실행 중인 스케줄 엔트리 기준)."""
    sep = " · "
    try:
        from .lam_viewport_overlay_config import STATUS_PANEL_STATE_SEP

        sep = str(STATUS_PANEL_STATE_SEP or sep)
    except Exception:
        pass
    cas, lot = _status_wafer_lot_from_schedule_entry(sched)
    parts: List[str] = []
    if cas > 0:
        parts.append(f"웨이퍼#{cas}")
    if lot:
        parts.append(lot)
    event = str(getattr(sched, "event_name", "") or "").strip()
    if event.endswith(".json"):
        event = event[:-5]
    if event:
        parts.append(event)
    return sep.join(parts)


def _status_wafer_lot_from_schedule_entry(
    sched: CsvPlaybackScheduleEntry,
) -> Tuple[int, str]:
    """실행 스케줄 → (cassette_slot, lot_id)."""
    try:
        cas = int(getattr(sched, "cassette_slot", 0) or 0)
    except Exception:
        cas = 0
    if cas <= 0:
        title = str(getattr(sched, "title_ko", "") or "")
        m = re.search(r"웨이퍼#(\d+)", title)
        if m:
            try:
                cas = int(m.group(1))
            except Exception:
                cas = 0
    lot = str(getattr(sched, "lot_id", "") or "").strip()
    if not lot:
        title = str(getattr(sched, "title_ko", "") or "")
        m = re.search(r"lot=['\"]([^'\"]+)['\"]", title)
        if m:
            lot = m.group(1).strip()
    return int(cas), str(lot)


def _csv_play_timeline_row_begin_entry(
    sched: CsvPlaybackScheduleEntry,
    *,
    screen: Optional[int] = None,
) -> None:
    key = _schedule_entry_match_key(sched)
    soft = _schedule_entry_soft_match_key(sched)
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    try:
        from .lam_viewport_overlay_state import record_foup_event_from_schedule_entry

        record_foup_event_from_schedule_entry(sched, screen=si)
    except Exception:
        pass
    try:
        from .lam_device_label_highlight import record_device_label_event_from_schedule_entry

        record_device_label_event_from_schedule_entry(sched, screen=si)
    except Exception:
        pass
    state_line = _status_state_line_from_schedule_entry(sched)
    cas, lot = _status_wafer_lot_from_schedule_entry(sched)
    sess = csv_play_screen_session(si)
    with sess.timeline_active_keys_lock:
        sess.timeline_active_keys.add(key)
        # time_sec 이 UI 행과 어긋나도 Current State 매칭되도록 soft 키도 함께 등록
        try:
            sess.timeline_active_keys.add(soft)
        except Exception:
            pass
        snap = frozenset(sess.timeline_active_keys)
    with sess.live_status_lock:
        sess.live_status_stack.append((key, soft, state_line, int(cas), str(lot)))
        if cas > 0:
            sess.live_status_last_cassette = int(cas)
        if lot:
            sess.live_status_last_lot = str(lot)
    if state_line:
        try:
            from .lam_viewport_overlay_state import set_last_state_title

            set_last_state_title(state_line, screen=si)
        except Exception:
            pass
    _csv_play_timeline_highlight_notify(snap, screen=si)


def _csv_play_timeline_row_end_entry(
    sched: CsvPlaybackScheduleEntry,
    *,
    screen: Optional[int] = None,
) -> None:
    key = _schedule_entry_match_key(sched)
    soft = _schedule_entry_soft_match_key(sched)
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    sess = csv_play_screen_session(si)
    with sess.timeline_active_keys_lock:
        sess.timeline_active_keys.discard(key)
        try:
            sess.timeline_active_keys.discard(soft)
        except Exception:
            pass
        snap = frozenset(sess.timeline_active_keys)
    with sess.live_status_lock:
        kept: List[Tuple[Any, Any, str, int, str]] = []
        for item in sess.live_status_stack:
            if item[0] == key or item[1] == soft:
                continue
            kept.append(item)
        sess.live_status_stack = kept
        # 아직 실행 중이면 가장 최근 begin 웨이퍼로. 없으면 last_* 유지(갭 구간).
        if kept:
            top = kept[-1]
            if int(top[3] or 0) > 0:
                sess.live_status_last_cassette = int(top[3])
            if str(top[4] or "").strip():
                sess.live_status_last_lot = str(top[4])
            try:
                from .lam_viewport_overlay_state import set_last_state_title

                set_last_state_title(str(top[2] or ""), screen=si)
            except Exception:
                pass
    _csv_play_timeline_highlight_notify(snap, screen=si)


def clear_csv_play_timeline_highlight(*, screen: Optional[int] = None) -> None:
    """재생 종료·중지 시 타임라인 강조 해제."""
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    sess = csv_play_screen_session(si)
    with sess.timeline_active_keys_lock:
        sess.timeline_active_keys.clear()
    with sess.live_status_lock:
        sess.live_status_stack.clear()
        sess.live_status_last_cassette = 0
        sess.live_status_last_lot = ""
    _csv_play_timeline_highlight_notify(frozenset(), screen=si)


def get_csv_play_live_status_state(*, screen: Optional[int] = None) -> str:
    """화면별 Current State — 실행 중이면 최근 begin 웨이퍼, 없으면 직전 유지값."""
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    sess = csv_play_screen_session(si)
    with sess.live_status_lock:
        if sess.live_status_stack:
            return str(sess.live_status_stack[-1][2] or "")
    try:
        from .lam_viewport_overlay_state import get_last_state_title

        return str(get_last_state_title(screen=si) or "")
    except Exception:
        return ""


def get_csv_play_live_status_wafer(
    *, screen: Optional[int] = None
) -> Tuple[int, str]:
    """화면별 실행 중(또는 직전) 웨이퍼 ``(cassette_slot, lot_id)``.

    STATUS ``wafer 번호`` 행이 CSV dwell 시각과 다른 웨이퍼를 보이는 것을 막기 위해
    JSON begin 기준 값을 우선한다.
    """
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    sess = csv_play_screen_session(si)
    with sess.live_status_lock:
        if sess.live_status_stack:
            top = sess.live_status_stack[-1]
            return int(top[3] or 0), str(top[4] or "")
        return int(sess.live_status_last_cassette or 0), str(
            sess.live_status_last_lot or ""
        )


def get_csv_play_timeline_active_keys_snap(*, screen: Optional[int] = None) -> frozenset:
    """현재 타임라인(녹색) 강조 키 스냅샷.

    재생 로직을 건드리지 않고 UI/상태 표시용으로 조회한다.
    """
    sess = csv_play_screen_session(screen)
    with sess.timeline_active_keys_lock:
        return frozenset(sess.timeline_active_keys)


def set_csv_play_progress_ui_callback(
    cb: Optional[Callable[[float, float, float, float], None]],
    *,
    screen: Optional[int] = None,
) -> None:
    """Play 중 UI 갱신: (csv_t, csv_total, wall_elapsed, wall_total_est)."""
    csv_play_screen_session(screen).progress_ui_cb = cb


def get_csv_play_progress_snap(*, screen: Optional[int] = None) -> Dict[str, Any]:
    """UI 진행 표시용 스냅샷 (공정만보기 시 json_done / csv_t_display 등)."""
    sess = csv_play_screen_session(screen)
    with sess.progress_snap_lock:
        return dict(sess.progress_snap)


def _reset_csv_play_progress_snap(
    *,
    process_only: bool,
    json_total: int = 0,
    csv_total: float = 0.0,
    t0: float = 0.0,
    speed_scale: float = 1.0,
    screen: Optional[int] = None,
) -> None:
    sess = csv_play_screen_session(screen)
    with sess.progress_snap_lock:
        sess.progress_snap.clear()
        sess.progress_snap.update(
            {
                "process_only": bool(process_only),
                "json_done": 0,
                "json_total": max(0, int(json_total)),
                "csv_t_display": 0.0,
                "csv_time_offset": 0.0,
                "wall_elapsed_display": 0.0,
                "csv_total": float(csv_total),
                "t0": float(t0),
                "speed_scale": float(max(0.01, speed_scale or 1.0)),
                "wall_total_est": 0.0,
            }
        )


def _refresh_csv_play_progress_playhead(*, screen: Optional[int] = None) -> float:
    """대기·dwell 중 현재 CSV t 를 스냅샷에 반영 (일시정지·진행 UI용)."""
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    snap = get_csv_play_progress_snap(screen=si)
    csv_total = float(snap.get("csv_total", 0) or 0)
    if snap.get("process_only"):
        if _csv_play_json_executing(screen=si):
            csv_t = max(0.0, float(snap.get("csv_t_display", 0) or 0))
        else:
            csv_t = max(0.0, _process_only_playhead_csv_now(screen=si))
    elif csv_play_session_active(screen=si):
        csv_t = max(0.0, get_csv_play_csv_time_now(screen=si))
    else:
        csv_off = float(snap.get("csv_time_offset", 0) or 0)
        t0 = float(snap.get("t0", 0) or 0)
        sp = float(max(0.01, snap.get("speed_scale", 1.0) or 1.0))
        if t0 > 0:
            csv_t = csv_off + max(0.0, time.monotonic() - t0) * sp
        else:
            csv_t = max(0.0, float(snap.get("csv_t_display", 0) or 0))
    if csv_total > 0:
        csv_t = min(csv_total, csv_t)
    sess = csv_play_screen_session(si)
    with sess.progress_snap_lock:
        sess.progress_snap["csv_t_display"] = float(csv_t)
    return float(csv_t)


def _csv_play_progress_mark_json_start(
    block: CsvTimedPlaybackBlock,
    *,
    json_done: int,
    screen: Optional[int] = None,
) -> None:
    sess = csv_play_screen_session(screen)
    with sess.progress_snap_lock:
        sess.progress_snap["json_done"] = max(0, int(json_done))
        sess.progress_snap["csv_t_display"] = float(block.time_sec)


def _csv_play_progress_mark_json_done(
    json_done: int,
    *,
    screen: Optional[int] = None,
) -> None:
    sess = csv_play_screen_session(screen)
    with sess.progress_snap_lock:
        sess.progress_snap["json_done"] = max(0, int(json_done))


def _reset_csv_play_global_json_end(
    *,
    t0: float,
    csv_start: float = 0.0,
    screen: Optional[int] = None,
) -> None:
    """공정만보기 시작 시 전역 종료 시각 초기화."""
    sess = csv_play_screen_session(screen)
    with sess.global_end_lock:
        sess.global_wall_end = float(t0)
        sess.global_csv_end = float(csv_start)


def _bump_csv_play_global_json_end(
    block: CsvTimedPlaybackBlock,
    *,
    wall_end: float,
    screen: Optional[int] = None,
) -> None:
    """공정만보기: 어느 레인이든 JSON 종료 후 전역 시각 갱신 (타 레인 대기 당김)."""
    sess = csv_play_screen_session(screen)
    we = float(wall_end)
    ce = float(block.time_sec)
    with sess.global_end_lock:
        if we > sess.global_wall_end:
            sess.global_wall_end = we
        if ce > sess.global_csv_end:
            sess.global_csv_end = ce


def _get_csv_play_global_json_end(*, screen: Optional[int] = None) -> Tuple[float, float]:
    sess = csv_play_screen_session(screen)
    with sess.global_end_lock:
        return float(sess.global_wall_end), float(sess.global_csv_end)


def _csv_playback_block_is_same(
    a: CsvTimedPlaybackBlock, b: CsvTimedPlaybackBlock
) -> bool:
    """캐시·워커가 서로 다른 인스턴스여도 동일 재생 블록인지."""
    if a is b:
        return True
    if float(a.time_sec) != float(b.time_sec) or int(a.sort_order) != int(b.sort_order):
        return False
    sa, sb = a.schedule, b.schedule
    if sa is None and sb is None:
        return True
    if sa is None or sb is None:
        return False
    return _schedule_entry_match_key(sa) == _schedule_entry_match_key(sb)


def _reset_process_only_playhead(
    *,
    t0: float,
    csv_start: float = 0.0,
    screen: Optional[int] = None,
) -> None:
    """공정만보기 Play 시작 시 진행 시계·시작 기록 초기화."""
    sess = csv_play_screen_session(screen)
    with sess.process_only_playhead_lock:
        sess.process_only_playhead_csv = float(csv_start)
        sess.process_only_playhead_wall = float(t0)
        sess.process_only_started_keys = set()


def _seed_process_only_started_before_resume(
    all_blocks: List[CsvTimedPlaybackBlock],
    *,
    resume: float,
    paused_in_json: bool = False,
    screen: Optional[int] = None,
    completed_keys: Optional[set] = None,
) -> None:
    """이어서 공정만보기 시, resume 이전·이미 완료 JSON 을 started 로 표기.

    비우면 ``_process_only_next_unstarted_block`` 이 스케줄 맨 앞(t≈0)을
    다음 이벤트로 보고 idle 점프/진행이 막혀 재생이 멈춘다.

    모드 전환 후: 방금 완료한 resume 시각 블록을 started 에 넣지 않으면
    next_unstarted 가 그 블록에 고정되고 idle 점프가 영구히 안 되어
    이후 JSON 이 전부 대기만 한다.
    """
    resume_t = float(resume or 0.0)
    done = set(completed_keys or ())
    if resume_t <= 1e-9 and not done:
        return
    eps = 1e-6
    sess = csv_play_screen_session(screen)
    with sess.process_only_playhead_lock:
        for b in all_blocks:
            if not b.steps or b.schedule is None:
                continue
            t = float(b.time_sec)
            key = _schedule_entry_match_key(b.schedule)
            if key in done:
                sess.process_only_started_keys.add(key)
            elif resume_t > 1e-9 and t < resume_t - eps:
                sess.process_only_started_keys.add(key)
            elif paused_in_json and resume_t > 1e-9 and t <= resume_t + eps:
                # 일시정지 재개: 진행 중 블록은 필터가 다시 넣음 — started 로 두지 않음
                pass


def _process_only_json_active(*, screen: Optional[int] = None) -> bool:
    return _csv_play_json_executing(screen=screen)


def _process_only_playhead_csv_now(*, screen: Optional[int] = None) -> float:
    """JSON 이 하나라도 실행 중이면 wall 1x 로 CSV t 진행, idle 이면 마지막 시계값 유지."""
    now = time.monotonic()
    sess = csv_play_screen_session(screen)
    with sess.process_only_playhead_lock:
        base = float(sess.process_only_playhead_csv)
        anchor = float(sess.process_only_playhead_wall)
    if not _process_only_json_active(screen=screen):
        return base
    return base + max(0.0, now - anchor)


def _process_only_advance_playhead_after_block(
    block: CsvTimedPlaybackBlock,
    *,
    screen: Optional[int] = None,
) -> None:
    """JSON 종료 후 시계를 해당 블록 CSV t 이상으로 맞춤."""
    t = float(block.time_sec)
    now = time.monotonic()
    sess = csv_play_screen_session(screen)
    with sess.process_only_playhead_lock:
        if t > sess.process_only_playhead_csv:
            sess.process_only_playhead_csv = t
        sess.process_only_playhead_wall = now


def _process_only_next_unstarted_block(
    all_blocks: List[CsvTimedPlaybackBlock],
    *,
    screen: Optional[int] = None,
) -> Optional[CsvTimedPlaybackBlock]:
    ordered = sorted(all_blocks, key=lambda b: (b.time_sec, b.sort_order))
    sess = csv_play_screen_session(screen)
    with sess.process_only_playhead_lock:
        started = set(sess.process_only_started_keys)
    for b in ordered:
        if not b.steps or b.schedule is None:
            continue
        if _schedule_entry_match_key(b.schedule) in started:
            continue
        return b
    return None


def _process_only_block_is_next_unstarted(
    block: CsvTimedPlaybackBlock,
    all_blocks: List[CsvTimedPlaybackBlock],
    *,
    screen: Optional[int] = None,
) -> bool:
    nxt = _process_only_next_unstarted_block(all_blocks, screen=screen)
    if nxt is None:
        return False
    return _csv_playback_block_is_same(nxt, block)


def _process_only_try_idle_compress_to_next(
    all_blocks: List[CsvTimedPlaybackBlock],
    *,
    screen: Optional[int] = None,
) -> None:
    """전 레인 JSON 미실행(idle)일 때만 다음 예정 이벤트 CSV t 로 점프."""
    if csv_play_mode_switch_drain_active(screen=screen):
        # drain 중 점프하면 playhead 가 다음 단계(B)로 가고,
        # resume 필터(t>resume) 가 B 를 건너뛰어 C 부터 재생된다.
        return
    if _process_only_json_active(screen=screen):
        return
    nxt = _process_only_next_unstarted_block(all_blocks, screen=screen)
    if nxt is None:
        return
    target = float(nxt.time_sec)
    now = time.monotonic()
    sess = csv_play_screen_session(screen)
    with sess.process_only_playhead_lock:
        if target > sess.process_only_playhead_csv:
            sess.process_only_playhead_csv = target
        sess.process_only_playhead_wall = now


def _process_only_mark_block_started(
    block: CsvTimedPlaybackBlock,
    *,
    screen: Optional[int] = None,
) -> None:
    sched = block.schedule
    if sched is None:
        return
    key = _schedule_entry_match_key(sched)
    sess = csv_play_screen_session(screen)
    with sess.process_only_playhead_lock:
        sess.process_only_started_keys.add(key)


def _has_json_csv_between(
    lo_csv: float,
    hi_csv: float,
    all_blocks: List[CsvTimedPlaybackBlock],
    current: CsvTimedPlaybackBlock,
    *,
    lane: Optional[str] = None,
) -> bool:
    """``(lo_csv, hi_csv)`` 에 다른 JSON 이 있으면 ``True`` (현재 블록 제외, ``lane`` 필터 가능)."""
    lo = float(lo_csv)
    hi = float(hi_csv)
    for b in all_blocks:
        if _csv_playback_block_is_same(b, current) or not b.steps:
            continue
        if lane is not None and _playback_lane_from_block(b) != lane:
            continue
        t = float(b.time_sec)
        if lo < t < hi:
            return True
    return False


def _sleep_until_process_only_start(
    *,
    lane_ready: float,
    nominal_wall: float,
    block: CsvTimedPlaybackBlock,
    all_blocks: List[CsvTimedPlaybackBlock],
    lane_last_csv: float,
    lane: Optional[str],
    play_screen: Optional[int] = None,
    play_epoch: Optional[int] = None,
) -> bool:
    """공정만보기: CSV 진행 시계가 ``block.time_sec`` 에 도달할 때까지 대기.

    - JSON 실행 중: wall 1x 로 시계 진행 → VTM t=6 시작 후 2s 뒤 ATM t=8 시작 가능.
    - 전 레인 idle: 다음 예정 이벤트 CSV t 로만 점프(빈 대기 제거). 동시에 전부 시작하지 않음.
    - 같은 레인: ``lane_ready``(이전 JSON 종료) 후에만 시작.
    """
    _ = (nominal_wall, lane_last_csv, lane)
    si = max(1, int(play_screen if play_screen is not None else current_csv_play_screen()))
    target_csv = float(block.time_sec)
    lr = float(lane_ready)
    while True:
        if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
            return False
        now = time.monotonic()
        if now + 1e-6 < lr:
            if not _sleep_csv_playback(
                min(0.05, lr - now), screen=si, play_epoch=play_epoch
            ):
                return False
            continue
        if not _process_only_json_active(screen=si):
            # 아무 lane 이든 idle 이면 전역 next 로 점프.
            # (next_unstarted 전용 worker 만 점프하면, 완료 블록이 started 누락일 때
            #  그 블록은 필터에 없어 점프가 영구히 안 됨)
            _process_only_try_idle_compress_to_next(all_blocks, screen=si)
        csv_now = _process_only_playhead_csv_now(screen=si)
        if csv_now + 1e-6 >= target_csv:
            _process_only_mark_block_started(block, screen=si)
            sess = csv_play_screen_session(si)
            with sess.process_only_playhead_lock:
                if target_csv > sess.process_only_playhead_csv:
                    sess.process_only_playhead_csv = target_csv
                sess.process_only_playhead_wall = time.monotonic()
            return True
        if not _sleep_csv_playback(0.05, screen=si, play_epoch=play_epoch):
            return False


class _SecondsIntervalProgress:
    """done/total 을 1초마다 UI·콘솔에 반영 (빌드 속도 영향 최소)."""

    __slots__ = (
        "_label",
        "_set_text",
        "_lock",
        "_done",
        "_total",
        "_stop",
        "_thread",
        "_t0",
        "_console",
    )

    def __init__(
        self,
        set_text: Callable[[str], None],
        *,
        label: str = "빌드",
        console: bool = False,
    ) -> None:
        self._label = label
        self._set_text = set_text
        self._console = console
        self._lock = threading.Lock()
        self._done = 0
        self._total = 1
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0 = 0.0

    def start(self, total: int) -> None:
        self.stop()
        with self._lock:
            self._total = max(1, int(total))
            self._done = 0
        self._t0 = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"lam-sim-{self._label}-progress"
        )
        self._thread.start()

    def set_done(self, done: int) -> None:
        with self._lock:
            self._total = max(1, self._total)
            self._done = min(self._total, max(0, int(done)))

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout=2.0)
            except Exception:
                pass
        self._thread = None

    def _format_line(self, done: int, total: int, elapsed: int) -> str:
        pct = int(100.0 * done / max(1, total))
        return f"{self._label} {pct}% | 경과 {elapsed}s | ({done}/{total})"

    def _loop(self) -> None:
        while not self._stop.wait(1.0):
            with self._lock:
                done, total = self._done, self._total
            elapsed = int(time.monotonic() - self._t0)
            line = self._format_line(done, total, elapsed)
            try:
                self._set_text(line)
            except Exception:
                pass
            if self._console:
                print(f"{_PRINT_PREFIX} {line}", flush=True)


_CSV_PLAYBACK_LANES = ("atm", "vtm")


def csv_play_session_active(*, screen: Optional[int] = None) -> bool:
    return bool(csv_play_screen_session(screen).session_active)


def set_csv_play_live_speed_ui_reader(
    reader: Optional[Callable[[], float]],
    *,
    screen: Optional[int] = None,
) -> None:
    csv_play_screen_session(screen).live_speed_ui_reader = reader


def set_csv_play_live_speed_scale(
    speed_scale: float,
    *,
    screen: Optional[int] = None,
) -> float:
    sess = csv_play_screen_session(screen)
    v = float(max(0.1, min(20.0, float(speed_scale or 1.0))))
    with sess.live_speed_lock:
        if sess.session_active:
            now = time.monotonic()
            wall_el = max(0.0, now - float(sess.time_base_wall))
            old_sp = float(max(0.01, sess.live_speed_scale))
            sess.wall_elapsed_offset = float(sess.wall_elapsed_offset) + wall_el
            sess.csv_time_offset = float(sess.csv_time_offset) + wall_el * old_sp
            sess.time_base_wall = now
        sess.live_speed_scale = v
    with sess.progress_snap_lock:
        sess.progress_snap["speed_scale"] = v
    return v


def sync_csv_play_live_speed_from_ui(*, screen: Optional[int] = None) -> float:
    sess = csv_play_screen_session(screen)
    if not sess.session_active:
        return get_csv_play_live_speed_scale(screen=screen)
    reader = sess.live_speed_ui_reader
    if reader is None:
        return get_csv_play_live_speed_scale(screen=screen)
    try:
        new_sp = float(reader())
    except Exception:
        return get_csv_play_live_speed_scale(screen=screen)
    cur = get_csv_play_live_speed_scale(screen=screen)
    if abs(new_sp - cur) > 1e-6:
        return set_csv_play_live_speed_scale(new_sp, screen=screen)
    return cur


def get_csv_play_anim_dt_scale(
    speed_ref: float,
    *,
    screen: Optional[int] = None,
    usd_context_name: Optional[str] = None,
) -> float:
    from .lam_csv_play_screen import csv_play_screen_for_usd_context

    si = screen
    if si is None:
        si = csv_play_screen_for_usd_context(usd_context_name)
    sess = csv_play_screen_session(si)
    if not sess.session_active:
        return 1.0
    sync_csv_play_live_speed_from_ui(screen=si)
    ref = float(max(0.01, speed_ref or 1.0))
    return get_csv_play_live_speed_scale(screen=si) / ref


def get_csv_play_live_speed_scale(*, screen: Optional[int] = None) -> float:
    sess = csv_play_screen_session(screen)
    with sess.live_speed_lock:
        return float(sess.live_speed_scale)


def begin_csv_play_timekeeping(
    *,
    csv_offset: float = 0.0,
    speed_scale: float = 1.0,
    wall_elapsed_offset: float = 0.0,
    screen: Optional[int] = None,
) -> int:
    """Play timekeeping 시작. 반환 ``play_epoch`` — ``end_csv_play_timekeeping`` 에 전달."""
    sess = csv_play_screen_session(screen)
    sess.play_epoch += 1
    epoch = int(sess.play_epoch)
    sess.session_active = True
    sess.time_base_wall = time.monotonic()
    sess.csv_time_offset = max(0.0, float(csv_offset or 0.0))
    sess.wall_elapsed_offset = max(0.0, float(wall_elapsed_offset or 0.0))
    # resume 이어서면 이미 지난 JSON 은 완료로 간주 (필터는 begin 전에 exclude keys 사용)
    sess.last_json_completed_csv_sec = max(0.0, float(csv_offset or 0.0))
    sess.completed_json_keys.clear()
    set_csv_play_live_speed_scale(speed_scale, screen=screen)
    return epoch


def end_csv_play_timekeeping(
    *,
    screen: Optional[int] = None,
    play_epoch: Optional[int] = None,
) -> None:
    sess = csv_play_screen_session(screen)
    if play_epoch is not None and int(sess.play_epoch) != int(play_epoch):
        return
    sess.session_active = False
    sess.wall_elapsed_offset = 0.0


def get_csv_play_wall_elapsed(*, screen: Optional[int] = None) -> float:
    sess = csv_play_screen_session(screen)
    return float(sess.wall_elapsed_offset) + max(
        0.0, time.monotonic() - float(sess.time_base_wall)
    )


def _csv_play_json_executing(*, screen: Optional[int] = None) -> bool:
    sess = csv_play_screen_session(screen)
    with sess.timeline_active_keys_lock:
        return bool(sess.timeline_active_keys)


def set_csv_play_mode_switch_drain(
    enabled: bool, *, screen: Optional[int] = None
) -> None:
    """모드 전환 drain — True 면 신규 JSON 시작을 막고 진행 중 JSON 완료를 기다린다."""
    csv_play_screen_session(screen).mode_switch_drain = bool(enabled)


def csv_play_mode_switch_drain_active(*, screen: Optional[int] = None) -> bool:
    return bool(csv_play_screen_session(screen).mode_switch_drain)


def _wait_csv_play_hold_before_new_json(
    *,
    screen: Optional[int] = None,
    play_epoch: Optional[int] = None,
) -> bool:
    """모드 전환 drain 중이면 신규 JSON 시작을 보류. False = stop/epoch 로 중단."""
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    while csv_play_mode_switch_drain_active(screen=si):
        if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
            return False
        if not _sleep_csv_playback(0.05, screen=si, play_epoch=play_epoch):
            return False
    return not csv_play_worker_should_exit(screen=si, play_epoch=play_epoch)


def _wait_csv_play_in_flight_json_finish(
    *,
    screen: Optional[int] = None,
    timeout_sec: float = 180.0,
) -> bool:
    """진행 중 JSON(타임라인 active) 이 모두 끝날 때까지 대기.

    True = idle. False = timeout(fail-open — 호출측이 전환 진행).
    """
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    deadline = time.monotonic() + max(1.0, float(timeout_sec))
    if not _csv_play_json_executing(screen=si):
        return True
    print(
        f"{_PRINT_PREFIX} 공정만보기 전환 — 진행 중 JSON 완료 대기 화면{si}…",
        flush=True,
    )
    while _csv_play_json_executing(screen=si):
        if time.monotonic() >= deadline:
            print(
                f"{_PRINT_PREFIX} 공정만보기 전환 — JSON 완료 대기 timeout "
                f"화면{si} ({timeout_sec:.0f}s) → 강제 전환",
                flush=True,
            )
            return False
        time.sleep(0.05)
    print(
        f"{_PRINT_PREFIX} 공정만보기 전환 — 진행 중 JSON 완료 화면{si}",
        flush=True,
    )
    return True


def get_csv_play_csv_time_now(*, screen: Optional[int] = None) -> float:
    sess = csv_play_screen_session(screen)
    sp = get_csv_play_live_speed_scale(screen=screen)
    wall_seg = max(0.0, time.monotonic() - float(sess.time_base_wall))
    return float(sess.csv_time_offset) + wall_seg * sp


def clear_csv_playback_stop(*, screen: Optional[int] = None) -> None:
    sess = csv_play_screen_session(screen)
    sess.stop_event.clear()
    sess.material_test_stop.clear()


def csv_playback_stop_requested(*, screen: Optional[int] = None) -> bool:
    return csv_play_screen_session(screen).stop_event.is_set()


def csv_play_epoch_current(play_epoch: int, *, screen: Optional[int] = None) -> bool:
    """재생 worker 가 자신이 시작한 ``play_epoch`` 와 세션이 일치하는지."""
    return int(csv_play_screen_session(screen).play_epoch) == int(play_epoch)


def csv_play_worker_should_exit(
    *,
    screen: Optional[int] = None,
    play_epoch: Optional[int] = None,
) -> bool:
    """stop 또는 모드전환 detach(epoch 무효) 시 worker 즉시 종료."""
    if csv_playback_stop_requested(screen=screen):
        return True
    if play_epoch is not None and not csv_play_epoch_current(play_epoch, screen=screen):
        return True
    return False


def clear_csv_play_pause_checkpoint(*, screen: Optional[int] = None) -> None:
    sess = csv_play_screen_session(screen)
    sess.pause_checkpoint = None
    sess.pause_armed = False


def disarm_csv_play_pause_for_resume(*, screen: Optional[int] = None) -> None:
    csv_play_screen_session(screen).pause_armed = False


def get_csv_play_pause_checkpoint(*, screen: Optional[int] = None):
    return csv_play_screen_session(screen).pause_checkpoint


def csv_play_pause_armed(*, screen: Optional[int] = None) -> bool:
    return bool(csv_play_screen_session(screen).pause_armed)


def _flush_csv_play_time_segment_to_offset(*, screen: Optional[int] = None) -> None:
    sess = csv_play_screen_session(screen)
    if not sess.session_active:
        return
    set_csv_play_live_speed_scale(get_csv_play_live_speed_scale(screen=screen), screen=screen)


def _compute_pause_wall_elapsed_from_snap(
    snap: Dict[str, Any],
    *,
    screen: Optional[int] = None,
) -> float:
    if csv_play_session_active(screen=screen):
        return max(0.0, get_csv_play_wall_elapsed(screen=screen))
    t0 = float(snap.get("t0", 0) or 0)
    from_snap = float(snap.get("wall_elapsed_display", 0) or 0)
    if t0 > 0:
        return max(from_snap, time.monotonic() - t0)
    return max(0.0, from_snap)


def _compute_pause_csv_time_from_snap(
    snap: Dict[str, Any],
    *,
    screen: Optional[int] = None,
) -> Tuple[float, bool]:
    """일시정지 CSV t [s] · JSON 실행 중이면 해당 블록 시각(이어서 Play 시 JSON 처음부터)."""
    csv_total = float(snap.get("csv_total", 0) or 0)
    in_json = _csv_play_json_executing(screen=screen)
    resume_t = max(0.0, float(snap.get("csv_t_display", 0) or 0))
    if csv_total > 0:
        resume_t = min(csv_total, resume_t)
    return resume_t, in_json


def save_csv_play_pause_checkpoint(
    *,
    csv_path: str,
    speed_scale: float,
    process_only: bool,
    screen: Optional[int] = None,
    for_mode_switch: bool = False,
) -> CsvPlayPauseCheckpoint:
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    with csv_play_screen_binding(si):
        # omni.ui reader 는 메인 전용 — 백그라운드(모드 전환)에서는 세션 배속만 사용
        if threading.current_thread() is threading.main_thread():
            sync_csv_play_live_speed_from_ui(screen=si)
        _flush_csv_play_time_segment_to_offset(screen=si)
        in_json = _csv_play_json_executing(screen=si)
        snap = get_csv_play_progress_snap(screen=si)
        sess = csv_play_screen_session(si)
        if not in_json:
            if for_mode_switch and float(sess.last_json_completed_csv_sec or 0) > 0:
                # 모드 전환: 완료한 JSON 시각 기준 → 다음 단계(B) 포함.
                # playhead/idle압축이 이미 B 로 가 있으면 display 기준 resume 이 B 를 스킵함.
                csv_t = max(0.0, float(sess.last_json_completed_csv_sec))
            elif snap.get("process_only"):
                csv_t = max(0.0, _process_only_playhead_csv_now(screen=si))
            else:
                csv_t = max(0.0, get_csv_play_csv_time_now(screen=si))
            csv_total = float(snap.get("csv_total", 0) or 0)
            if csv_total > 0:
                csv_t = min(csv_total, csv_t)
            # wall 은 락 밖에서 계산 — 락 안에서 get_csv_play_progress_snap 재진입 시
            # (과거 Lock) 자기교착으로 Kit 전체가 멈췄다.
            wall_el_now = float(
                _compute_pause_wall_elapsed_from_snap(snap, screen=si)
            )
            with sess.progress_snap_lock:
                sess.progress_snap["csv_t_display"] = float(csv_t)
                sess.progress_snap["wall_elapsed_display"] = wall_el_now
            snap = get_csv_play_progress_snap(screen=si)
        else:
            snap = get_csv_play_progress_snap(screen=si)
        resume_t, in_json = _compute_pause_csv_time_from_snap(snap, screen=si)
        if for_mode_switch and not in_json:
            last = float(sess.last_json_completed_csv_sec or 0)
            if last > 0:
                resume_t = last
                csv_total = float(snap.get("csv_total", 0) or 0)
                if csv_total > 0:
                    resume_t = min(csv_total, resume_t)
        wall_el = _compute_pause_wall_elapsed_from_snap(snap, screen=si)
        ck = CsvPlayPauseCheckpoint(
            csv_path=str(csv_path),
            speed_scale=float(speed_scale),
            process_only=bool(process_only),
            resume_csv_sec=float(resume_t),
            json_done=int(snap.get("json_done", 0) or 0),
            wall_elapsed_sec=float(wall_el),
            paused_in_json=bool(in_json) if not for_mode_switch else False,
        )
        sess.pause_checkpoint = ck
        sess.pause_armed = True
        return ck


def match_csv_play_pause_checkpoint(
    csv_path: str,
    *,
    speed_scale: float,
    process_only: bool,
    screen: Optional[int] = None,
) -> Optional[CsvPlayPauseCheckpoint]:
    _ = speed_scale
    ck = get_csv_play_pause_checkpoint(screen=screen)
    if ck is None:
        return None
    try:
        if Path(csv_path).resolve() != Path(ck.csv_path).resolve():
            return None
    except Exception:
        return None
    if bool(process_only) != ck.process_only:
        return None
    return ck


def _filter_process_only_lane_items(
    items: List[Tuple[int, CsvTimedPlaybackBlock]],
    resume_csv_sec: float,
    *,
    paused_in_json: bool = False,
    exclude_schedule_keys: Optional[set] = None,
) -> List[Tuple[int, CsvTimedPlaybackBlock]]:
    """resume 이후 JSON 만. 모드 전환(``paused_in_json=False``) 시 과거 재실행 금지.

    ``paused_in_json=True``(일시정지 재개) 일 때만 진행 중이던 최신 블록을 포함.
    모드 전환: ``t > resume`` + 동일 시각이지만 아직 완료되지 않은 블록 포함.
    """
    resume = float(resume_csv_sec or 0.0)
    if resume <= 1e-9:
        return list(items)
    eps = 1e-6
    excl = exclude_schedule_keys or set()
    if paused_in_json:
        out = [(i, b) for i, b in items if float(b.time_sec) >= resume - eps]
        in_progress = [
            (i, b)
            for i, b in items
            if b.steps and float(b.time_sec) <= resume + eps
        ]
        if in_progress:
            pair = max(in_progress, key=lambda x: (x[1].time_sec, x[1].sort_order))
            if pair not in out:
                out = sorted(
                    out + [pair], key=lambda x: (x[1].time_sec, x[1].sort_order)
                )
        return out
    out: List[Tuple[int, CsvTimedPlaybackBlock]] = []
    for i, b in items:
        t = float(b.time_sec)
        if t > resume + eps:
            out.append((i, b))
            continue
        if abs(t - resume) <= eps and b.steps and b.schedule is not None:
            key = _schedule_entry_match_key(b.schedule)
            if key not in excl:
                out.append((i, b))
    return out


def _filter_blocks_from_csv_time(
    blocks: List[CsvTimedPlaybackBlock],
    resume_csv_sec: float,
    *,
    paused_in_json: bool = False,
    exclude_schedule_keys: Optional[set] = None,
) -> List[CsvTimedPlaybackBlock]:
    """``resume_csv_sec`` 이후 블록만.

    일시정지(``paused_in_json``): 진행 중이던 JSON 블록 포함(처음부터 재실행).
    모드 전환 등 ``paused_in_json=False``: resume 초과 + 동일시각 미완료 블록.
    """
    resume = float(resume_csv_sec or 0.0)
    if resume <= 1e-9:
        return list(blocks)
    ordered = sorted(blocks, key=lambda b: (b.time_sec, b.sort_order))
    eps = 1e-6
    excl = exclude_schedule_keys or set()
    if not paused_in_json:
        out: List[CsvTimedPlaybackBlock] = []
        for b in ordered:
            t = float(b.time_sec)
            if t > resume + eps:
                out.append(b)
            elif abs(t - resume) <= eps and b.steps and b.schedule is not None:
                key = _schedule_entry_match_key(b.schedule)
                if key not in excl:
                    out.append(b)
        return out
    out = [b for b in ordered if float(b.time_sec) >= resume - eps]
    in_progress = [
        b for b in ordered if b.steps and float(b.time_sec) <= resume + eps
    ]
    if in_progress:
        blk = max(in_progress, key=lambda b: (b.time_sec, b.sort_order))
        if blk not in out:
            out = sorted(out + [blk], key=lambda b: (b.time_sec, b.sort_order))
    return out


def _csv_play_register_runner(runner: Any, *, screen: Optional[int] = None) -> None:
    sess = csv_play_screen_session(screen)
    with sess.runner_lock:
        sess.active_runners.append(runner)


def _csv_play_unregister_runner(runner: Any, *, screen: Optional[int] = None) -> None:
    sess = csv_play_screen_session(screen)
    with sess.runner_lock:
        try:
            sess.active_runners.remove(runner)
        except ValueError:
            pass


def _playback_lane_from_block(block: CsvTimedPlaybackBlock) -> Optional[str]:
    """CSV 블록 → ``atm`` / ``vtm`` 레인 (동일 레인은 직렬·대기). 없으면 ``None``."""
    sched = block.schedule
    if sched is not None and sched.event_name:
        en = str(sched.event_name).strip().lower()
        if en.startswith("vtm_"):
            return "vtm"
        if en.startswith("atm_"):
            return "atm"
    label = (block.label or "").lower()
    if "vtm" in label or "이송(vtm" in label:
        return "vtm"
    if "atm" in label or "foup" in label:
        return "atm"
    return None


class _CsvPlaybackLaneCoordinator:
    """ATM / VTM 레인별 직렬 — 동일 레인은 이전 JSON 종료 후 다음 실행, 레인 간은 병렬."""

    def __init__(self, *, play_screen: int = 1) -> None:
        self._play_screen = max(1, int(play_screen))
        self._lane_locks = {lane: threading.Lock() for lane in _CSV_PLAYBACK_LANES}

    def run_in_lane(self, lane: str, fn: Callable[[], None]) -> None:
        """``lane`` 뮤텍스로 ``fn`` 직렬화. ``atm`` / ``vtm`` 락은 서로 독립(병렬 가능)."""
        si = self._play_screen
        if lane not in self._lane_locks:
            fn()
            return
        lock = self._lane_locks[lane]
        while True:
            if csv_playback_stop_requested(screen=si):
                return
            if lock.acquire(timeout=0.05):
                break
        try:
            if not csv_playback_stop_requested(screen=si):
                fn()
        finally:
            lock.release()


def request_pause_csv_playback(
    registry: Any = None,
    scheduler: Any = None,
    *,
    screen: Optional[int] = None,
    kit_ext: Any = None,
) -> None:
    request_stop_csv_playback(registry, scheduler, screen=screen, kit_ext=kit_ext)


def _other_csv_play_screens_active(*, exclude_screen: int) -> bool:
    """다른 화면 CSV Play 세션이 아직 살아 있는지."""
    ex = max(1, int(exclude_screen))
    for si in range(1, 5):
        if si == ex:
            continue
        if csv_play_session_active(screen=si):
            return True
    return False


def request_stop_csv_playback(
    registry: Any = None,
    scheduler: Any = None,
    *,
    screen: Optional[int] = None,
    kit_ext: Any = None,
    stop_scheduler: bool = True,
    stop_motion: bool = True,
) -> None:
    """CSV 재생 중지 요청.

    ``stop_motion=False`` / ``stop_scheduler=False`` — 공정만보기 실시간 전환용.
    (애니 dict·scheduler stop_all 이 메인 USD update 와 겹치면 Kit 가 멈출 수 있음)
    """
    _ = registry
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    sess = csv_play_screen_session(si)
    sess.stop_event.set()
    sess.material_test_stop.set()
    with sess.runner_lock:
        runners = list(sess.active_runners)
    for runner in runners:
        try:
            # CSV Play pause/stop — runner 루프만 끊고 MOVE/ROTATE 는 화면별로만 중지
            # (cancel_all_move_rotate=True 이면 stop_all_* 로 다른 화면 재생까지 끊김)
            runner.stop(cancel_all_move_rotate=False)
        except Exception as exc:
            if not is_csv_playback_compact_log():
                print(f"{_PRINT_PREFIX} CSV Play 중지 Runner 경고: {exc}", flush=True)
    if stop_motion:
        try:
            from .lam_csv_play_execution import stop_csv_play_motion_for_screen

            lam_window = None
            if kit_ext is not None:
                lam_window = getattr(kit_ext, "_lam_window", None) or getattr(
                    kit_ext, "_window", None
                )
            stop_csv_play_motion_for_screen(si, kit_ext=kit_ext, lam_window=lam_window)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} CSV Play 중지 애니 경고: {exc}", flush=True)
    else:
        # runner 캐시만 무효 — 다음 resume 이 새 runner 를 받도록
        try:
            from .lam_csv_play_execution import invalidate_csv_sequence_runner_for_screen

            lam_window = None
            if kit_ext is not None:
                lam_window = getattr(kit_ext, "_lam_window", None) or getattr(
                    kit_ext, "_window", None
                )
            invalidate_csv_sequence_runner_for_screen(lam_window, si)
        except Exception:
            pass
    if stop_scheduler and scheduler is not None:
        try:
            stop_fn = getattr(scheduler, "stop_all", None)
            if callable(stop_fn) and not _other_csv_play_screens_active(
                exclude_screen=si
            ):
                stop_fn()
        except Exception as exc:
            if not is_csv_playback_compact_log():
                print(f"{_PRINT_PREFIX} CSV Play 중지 scheduler 경고: {exc}", flush=True)


class _CsvPlayStopRequested(Exception):
    """재생 중지 요청 — 프리런 등 협조적 취소."""


_CSV_PLAY_WORKER_JOIN_SLICE_SEC = 0.1


def _csv_play_register_child_worker(
    worker: threading.Thread,
    *,
    screen: Optional[int] = None,
) -> None:
    """블록/레인 JSON worker — 화면별 추적 (공정만보기 전환·정지 시 join)."""
    sess = csv_play_screen_session(screen)
    with sess.child_workers_lock:
        if worker not in sess.child_workers:
            sess.child_workers.append(worker)


def _csv_play_unregister_child_worker(
    worker: threading.Thread,
    *,
    screen: Optional[int] = None,
) -> None:
    sess = csv_play_screen_session(screen)
    with sess.child_workers_lock:
        try:
            sess.child_workers.remove(worker)
        except ValueError:
            pass


def _alive_csv_play_child_workers(*, screen: int) -> List[threading.Thread]:
    """해당 화면의 살아 있는 자식 worker 목록."""
    si = max(1, int(screen))
    sess = csv_play_screen_session(si)
    with sess.child_workers_lock:
        alive = [t for t in sess.child_workers if t.is_alive()]
        sess.child_workers = alive
        return list(alive)


def join_csv_play_child_workers(
    *,
    screen: int,
    timeout: float = 45.0,
) -> bool:
    """등록된 자식 worker 전부 join. True = 모두 종료."""
    si = max(1, int(screen))
    deadline = time.monotonic() + max(0.05, float(timeout))
    while time.monotonic() < deadline:
        alive = _alive_csv_play_child_workers(screen=si)
        if not alive:
            return True
        for t in alive:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                t.join(timeout=min(_CSV_PLAY_WORKER_JOIN_SLICE_SEC, remaining))
            except Exception:
                pass
    return len(_alive_csv_play_child_workers(screen=si)) == 0


def detach_csv_play_workers_for_screen(
    csv_win: Any,
    *,
    screen: int,
) -> None:
    """추적만 끊고 play_epoch 무효화 — 좀비가 새 세션을 깨우지 못하게.

    Federation reap-timeout 과 동일 패턴. join 이 끝나지 않아도 UI/전환은 진행한다.
    잔존 daemon worker 는 stop·epoch 불일치로 스스로 빠져나온다.
    """
    si = max(1, int(screen))
    sess = csv_play_screen_session(si)
    # 구 play finally 의 end_csv_play_timekeeping 이 신 세션을 끄지 않게
    sess.play_epoch += 1
    sess.session_active = False
    with sess.child_workers_lock:
        sess.child_workers.clear()
    with sess.runner_lock:
        sess.active_runners.clear()
    if csv_win is not None:
        try:
            lock = getattr(csv_win, "_csv_play_launch_lock", None)
            if lock is not None:
                with lock:
                    csv_win._csv_play_thread = None
            else:
                csv_win._csv_play_thread = None
        except Exception:
            try:
                csv_win._csv_play_thread = None
            except Exception:
                pass
    print(
        f"{_PRINT_PREFIX} CSV Play detach 화면{si} — epoch={sess.play_epoch} "
        "(잔존 worker 추적 해제)",
        flush=True,
    )


def recover_csv_play_after_failed_mode_switch(*, screen: int) -> None:
    """전환 실패 복구.

    살아 있는 자식 worker 가 있으면 **stop 을 해제하지 않는다**.
    (해제 시 좀비 worker 가 재개되어 토글 반복 시 Kit 메인 데드락으로 이어짐.)
    """
    si = max(1, int(screen))
    alive = _alive_csv_play_child_workers(screen=si)
    if alive:
        sess = csv_play_screen_session(si)
        sess.stop_event.set()
        sess.material_test_stop.set()
        print(
            f"{_PRINT_PREFIX} 공정만보기 전환 실패 화면{si} — "
            f"잔존 worker {len(alive)}개, stop 유지 (Play/Stop 으로 복구)",
            flush=True,
        )
        return
    clear_csv_playback_stop(screen=si)
    disarm_csv_play_pause_for_resume(screen=si)


def abandon_csv_play_after_failed_mode_switch(*, screen: int) -> None:
    """전환 실패 — stop 유지·pause 해제. 좀비 재개 금지."""
    si = max(1, int(screen))
    sess = csv_play_screen_session(si)
    sess.stop_event.set()
    sess.material_test_stop.set()
    sess.pause_armed = False
    print(
        f"{_PRINT_PREFIX} 공정만보기 전환 abandon 화면{si} — stop 유지",
        flush=True,
    )


def reap_csv_play_workers_for_screen(
    csv_win: Any,
    *,
    screen: int,
    registry: Any = None,
    scheduler: Any = None,
    kit_ext: Any = None,
    timeout: float = 45.0,
) -> bool:
    """메인 play 스레드 + 자식 worker 모두 종료 대기 (화면별)."""
    si = max(1, int(screen))
    reg = registry if registry is not None else getattr(csv_win, "_registry", None)
    sch = scheduler if scheduler is not None else getattr(csv_win, "_scheduler", None)
    # stop 은 이미 pause 경로에서 걸렸을 수 있음 — scheduler.stop_all 중복 호출 억제
    request_stop_csv_playback(
        reg, sch, screen=si, kit_ext=kit_ext, stop_scheduler=False
    )
    if csv_win is not None:
        reap_fn = getattr(csv_win, "_reap_csv_play_thread", None)
        if callable(reap_fn):
            return bool(reap_fn(timeout=float(timeout)))
    return join_csv_play_child_workers(screen=si, timeout=timeout)


_CSV_PLAY_JOIN_ON_STOP_SEC = 2.0


def _join_csv_play_workers(
    workers: List[threading.Thread],
    *,
    screen: Optional[int] = None,
) -> None:
    """블록/레인 worker join.

    정상 완료(stop 없음) 시 끝까지 기다린다.
    **stop 요청 시** 짧은 시한만 기다린 뒤 반환한다 — 무한 join 은 play 스레드를
    좀비로 만들고, 공정만보기 전환 reap 이 메인/전환 worker 를 잠그게 한다.
    미종료 daemon 은 추적에서 detach 되며 stop·epoch 로 스스로 종료한다.
    """
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    deadline: Optional[float] = None
    if csv_playback_stop_requested(screen=si):
        deadline = time.monotonic() + float(_CSV_PLAY_JOIN_ON_STOP_SEC)
    for t in workers:
        while t.is_alive():
            if csv_playback_stop_requested(screen=si):
                if deadline is None:
                    deadline = time.monotonic() + float(_CSV_PLAY_JOIN_ON_STOP_SEC)
                if time.monotonic() >= deadline:
                    print(
                        f"{_PRINT_PREFIX} CSV Play worker join 시한 초과 화면{si} — "
                        f"detach ({getattr(t, 'name', '?')})",
                        flush=True,
                    )
                    return
            try:
                t.join(timeout=_CSV_PLAY_WORKER_JOIN_SLICE_SEC)
            except Exception:
                return


def stop_and_reap_csv_play_worker(
    csv_win: Any,
    *,
    screen: int,
    registry: Any = None,
    scheduler: Any = None,
    kit_ext: Any = None,
    timeout: float = 60.0,
) -> bool:
    """기존 CSV 재생 worker 중지 요청 후 join (메인 + 자식). idle 이면 즉시 True."""
    si = max(1, int(screen))
    if csv_win is None:
        return True
    try:
        alive_fn = getattr(csv_win, "_csv_play_thread_alive", None)
        child_only = True
        if callable(alive_fn):
            child_only = not alive_fn()
        if child_only:
            return join_csv_play_child_workers(screen=si, timeout=float(timeout))
    except Exception:
        pass
    return reap_csv_play_workers_for_screen(
        csv_win,
        screen=si,
        registry=registry,
        scheduler=scheduler,
        kit_ext=kit_ext,
        timeout=float(timeout),
    )


def _sleep_csv_playback(
    sec: float,
    *,
    screen: Optional[int] = None,
    play_epoch: Optional[int] = None,
) -> bool:
    """최대 ``sec`` 초 대기. ``False`` = 중지·epoch 무효로 조기 종료."""
    if csv_play_worker_should_exit(screen=screen, play_epoch=play_epoch):
        return False
    if sec <= 1e-6:
        return True
    end = time.monotonic() + float(sec)
    while time.monotonic() < end:
        if csv_play_worker_should_exit(screen=screen, play_epoch=play_epoch):
            return False
        time.sleep(min(0.1, max(0.0, end - time.monotonic())))
    return not csv_play_worker_should_exit(screen=screen, play_epoch=play_epoch)


def _wait_until_csv_play_time(
    target_csv_sec: float,
    *,
    screen: Optional[int] = None,
    play_epoch: Optional[int] = None,
) -> bool:
    """CSV ``target_csv_sec`` 에 도달할 때까지 대기 (배속 변경 즉시 반영)."""
    target = float(target_csv_sec)
    si = screen
    while not csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
        sync_csv_play_live_speed_from_ui(screen=si)
        if get_csv_play_csv_time_now(screen=si) >= target - 1e-6:
            return True
        sp = get_csv_play_live_speed_scale(screen=si)
        remaining_csv = target - get_csv_play_csv_time_now(screen=si)
        if remaining_csv <= 0:
            return True
        wall_sleep = min(0.1, max(1e-4, remaining_csv / sp))
        if not _sleep_csv_playback(wall_sleep, screen=si, play_epoch=play_epoch):
            return False
    return False


def _spawn_csv_play_bound_thread(
    screen: int,
    *,
    name: str,
    target: Callable[..., None],
    args: tuple = (),
    kwargs: Optional[Dict[str, Any]] = None,
) -> threading.Thread:
    """재생 자식 스레드 — ``csv_play_screen_binding`` 으로 ContextVar 유지."""
    si = max(1, int(screen))
    kw = dict(kwargs or {})

    def _run() -> None:
        with csv_play_screen_binding(si):
            try:
                target(*args, **kw)
            finally:
                _csv_play_unregister_child_worker(threading.current_thread(), screen=si)

    t = threading.Thread(target=_run, daemon=True, name=name)
    _csv_play_register_child_worker(t, screen=si)
    return t


def _run_lam_sim_steps_cancellable(
    registry: Any,
    scheduler: Any,
    steps: LamSimJsonSteps,
    *,
    speed_scale: float = 1.0,
    lane: Optional[str] = None,
    lane_coordinator: Optional[_CsvPlaybackLaneCoordinator] = None,
    usd_context_name: Optional[str] = None,
    lam_window: Any = None,
    play_screen: Optional[int] = None,
    play_epoch: Optional[int] = None,
) -> None:
    """``run_lam_sim_steps`` 와 동일하나 CSV 중지 시 Runner 를 끊을 수 있게 등록.

    **중요:** 화면당 캐시 runner 를 쓰지 않는다. ATM/VTM 레인 병렬 시 동일
    ``LamSequenceRunner.run()`` 이 ``_stop_flag.clear()`` 로 서로를 깨우고
    Kit 교착·과거 JSON 재실행을 유발한다.
    """
    if not steps or csv_play_worker_should_exit(
        screen=play_screen, play_epoch=play_epoch
    ):
        return
    try:
        from .lam_sequence_engine import LamSequenceRunner
        from .lam_csv_play_execution import resolve_csv_play_execution
    except Exception as exc:
        print(f"{_PRINT_PREFIX} LamSequenceRunner import 실패: {exc}", flush=True)
        return
    sp = float(max(0.01, speed_scale or 1.0))
    quiet = is_csv_playback_compact_log()
    si = max(1, int(play_screen if play_screen is not None else current_csv_play_screen()))
    ctx = str(usd_context_name).strip() if usd_context_name and str(usd_context_name).strip() else None

    def _execute() -> None:
        if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
            return
        reg = registry
        sch = scheduler
        ctx_run = ctx
        if lam_window is not None:
            binding = resolve_csv_play_execution(
                lam_window,
                si,
                require_aux=(si > 1),
            )
            if binding is None:
                print(
                    f"{_PRINT_PREFIX} screen{si} JSON 실행 불가 — split runtime 없음",
                    flush=True,
                )
                return
            reg = binding.registry
            sch = binding.scheduler
            ctx_run = binding.usd_context_name
        # JSON 블록마다 새 runner — 레인 병렬·모드 전환 안전
        runner = LamSequenceRunner(
            reg, sch, usd_context_name=ctx_run, play_screen=si
        )
        _csv_play_register_runner(runner, screen=si)
        try:
            if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
                return
            runner.run(
                list(steps),
                reset_each_start=False,
                speed_scale=sp,
                quiet=quiet,
            )
        finally:
            _csv_play_unregister_runner(runner, screen=si)

    if lane and lane_coordinator is not None:
        lane_coordinator.run_in_lane(lane, _execute)
    else:
        _execute()


def _csv_playback_execute_json_block(
    block: CsvTimedPlaybackBlock,
    index: int,
    registry: Any,
    scheduler: Any,
    *,
    t0: float,
    speed_scale: float,
    lane_coordinator: _CsvPlaybackLaneCoordinator,
    json_done_before: int = 0,
    usd_context_name: Optional[str] = None,
    lam_window: Any = None,
    play_screen: Optional[int] = None,
    play_epoch: Optional[int] = None,
) -> None:
    """한 JSON 블록 실행 (로그·타임라인 강조·진행 스냅샷)."""
    sp = float(max(0.01, speed_scale or 1.0))
    si = max(1, int(play_screen if play_screen is not None else current_csv_play_screen()))
    if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
        return
    sched = block.schedule
    if sched is None or not block.steps:
        return
    _csv_play_progress_mark_json_start(block, json_done=json_done_before, screen=si)
    _notify_csv_play_progress_ui(screen=si)
    wall_elapsed = time.monotonic() - t0
    lane = _playback_lane_from_block(block)
    _csv_play_timeline_row_begin_entry(sched, screen=si)
    try:
        _run_lam_sim_steps_cancellable(
            registry,
            scheduler,
            block.steps,
            speed_scale=sp,
            lane=lane,
            lane_coordinator=lane_coordinator,
            usd_context_name=usd_context_name,
            lam_window=lam_window,
            play_screen=play_screen,
            play_epoch=play_epoch,
        )
    finally:
        _csv_play_timeline_row_end_entry(sched, screen=si)
    # detach/epoch 무효 후엔 로그·시계 갱신 금지 (과거 t 재출력·널뛰기 방지)
    if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
        return
    if not csv_playback_stop_requested(screen=si):
        _print_csv_compact_line(index, sched, wall_elapsed_sec=wall_elapsed, executed=True)
    _csv_play_progress_mark_json_done(json_done_before + 1, screen=si)
    # 모드 전환 resume 기준 — 완료한 JSON CSV t (다음 단계 스킵 방지)
    try:
        sess = csv_play_screen_session(si)
        t_done = float(block.time_sec)
        if t_done >= float(sess.last_json_completed_csv_sec or 0):
            sess.last_json_completed_csv_sec = t_done
        if sched is not None:
            sess.completed_json_keys.add(_schedule_entry_match_key(sched))
    except Exception:
        pass
    _notify_csv_play_progress_ui(screen=si)
    if get_csv_play_progress_snap(screen=si).get("process_only"):
        _process_only_advance_playhead_after_block(block, screen=si)
        _bump_csv_play_global_json_end(block, wall_end=time.monotonic(), screen=si)


def _notify_csv_play_progress_ui(*, screen: Optional[int] = None) -> None:
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    ui_cb = csv_play_screen_session(si).progress_ui_cb
    if ui_cb is None:
        return
    snap = get_csv_play_progress_snap(screen=si)
    t0 = float(snap.get("t0", 0.0) or 0.0)
    csv_total = float(snap.get("csv_total", 0.0) or 0.0)
    wall_elapsed = float(snap.get("wall_elapsed_display", 0) or 0)
    if csv_play_session_active(screen=si):
        wall_elapsed = get_csv_play_wall_elapsed(screen=si)
    elif t0 > 0:
        wall_elapsed = max(wall_elapsed, time.monotonic() - t0)
    if snap.get("process_only"):
        csv_t = _process_only_playhead_csv_now(screen=si)
        wall_total_est = resolve_process_only_wall_total_est(snap, wall_elapsed)
    else:
        if csv_play_session_active(screen=si):
            sp = get_csv_play_live_speed_scale(screen=si)
            wall_elapsed = get_csv_play_wall_elapsed(screen=si)
            csv_t = min(csv_total, get_csv_play_csv_time_now(screen=si)) if csv_total > 0 else 0.0
        else:
            sp = float(max(0.01, snap.get("speed_scale", 1.0) or 1.0))
            csv_t = min(csv_total, wall_elapsed * sp) if csv_total > 0 else 0.0
        wall_total_est = csv_total / sp if csv_total > 0 else 0.0
    sess = csv_play_screen_session(si)
    with sess.progress_snap_lock:
        sess.progress_snap["csv_t_display"] = float(csv_t)
        sess.progress_snap["wall_elapsed_display"] = float(wall_elapsed)

    def _ui() -> None:
        try:
            ui_cb(csv_t, csv_total, wall_elapsed, wall_total_est)
        except Exception:
            pass

    _post_kit_main_thread(_ui)



def _csv_playback_block_worker(
    block: CsvTimedPlaybackBlock,
    index: int,
    registry: Any,
    scheduler: Any,
    *,
    t0: float = 0.0,
    speed_scale: float = 1.0,
    lane_coordinator: _CsvPlaybackLaneCoordinator,
    usd_context_name: Optional[str] = None,
    lam_window: Any = None,
    play_screen: Optional[int] = None,
    play_epoch: Optional[int] = None,
) -> None:
    """블록 1개: CSV 시각까지 대기 → (dwell 로그 | 레인별 JSON 실행). 메인 루프 비블로킹."""
    _ = t0, speed_scale
    si = max(1, int(play_screen if play_screen is not None else current_csv_play_screen()))
    if not _wait_until_csv_play_time(
        float(block.time_sec), screen=si, play_epoch=play_epoch
    ):
        return
    if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
        return
    if not _wait_csv_play_hold_before_new_json(screen=si, play_epoch=play_epoch):
        return

    wall_elapsed = get_csv_play_csv_time_now(screen=si)
    sp = get_csv_play_live_speed_scale(screen=si)
    sched = block.schedule
    if sched is None:
        return

    if not block.steps:
        _print_csv_compact_line(index, sched, wall_elapsed_sec=wall_elapsed, executed=False)
        return

    _csv_playback_execute_json_block(
        block,
        index,
        registry,
        scheduler,
        t0=t0,
        speed_scale=sp,
        lane_coordinator=lane_coordinator,
        json_done_before=0,
        usd_context_name=usd_context_name,
        lam_window=lam_window,
        play_screen=si,
        play_epoch=play_epoch,
    )


def _log_csv_playback_block(
    block: CsvTimedPlaybackBlock,
    index: int,
    *,
    speed_scale: float,
    wall_elapsed_sec: float,
    waited_sec: float,
) -> None:
    """CSV 시각에 도달했을 때 콘솔 로그 (UI 타임라인과 동일 형식)."""
    sched = block.schedule
    steps_summary = _summarize_lam_sim_steps_ko(block.steps) if block.steps else ""
    if sched is not None:
        _print_schedule_lines(
            _lines_for_schedule_entry(
                sched,
                index,
                speed_scale=speed_scale,
                steps_summary=steps_summary,
                play_now=True,
                wall_elapsed_sec=wall_elapsed_sec,
                waited_sec=waited_sec,
            )
        )
    else:
        sp = max(0.01, float(speed_scale or 1.0))
        print(
            f"{_PRINT_PREFIX} ── [{index:02d}] t={block.time_sec:8.3f}s │ {block.label} "
            f"| 실경과 {wall_elapsed_sec:.3f}s | 배속 {sp:g}x | ▶ 지금 실행 ──",
            flush=True,
        )


def _csv_play_progress_ticker_loop(screen: int = 1) -> None:
    """Play 중 1초마다 UI 진행률 콜백만 갱신 (라이브 배속). 화면별 독립."""
    si = max(1, int(screen))
    with csv_play_screen_binding(si):
        sess = csv_play_screen_session(si)
        ui_cb = sess.progress_ui_cb
        if ui_cb is None:
            return
        while not sess.progress_stop.wait(1.0):
            if csv_playback_stop_requested(screen=si):
                break
            snap = get_csv_play_progress_snap(screen=si)
            csv_total = float(snap.get("csv_total", 0.0) or 0.0)
            sp = get_csv_play_live_speed_scale(screen=si)
            wall_elapsed = get_csv_play_wall_elapsed(screen=si)
            csv_t = (
                min(csv_total, get_csv_play_csv_time_now(screen=si))
                if csv_total > 0
                else 0.0
            )
            wall_total_est = csv_total / sp if csv_total > 0 else 0.0
            with sess.progress_snap_lock:
                sess.progress_snap["csv_t_display"] = float(csv_t)
                sess.progress_snap["wall_elapsed_display"] = float(wall_elapsed)

            def _ui(
                cb: Callable = ui_cb,
                ct: float = csv_t,
                ctot: float = csv_total,
                we: float = wall_elapsed,
                wt: float = wall_total_est,
            ) -> None:
                try:
                    cb(ct, ctot, we, wt)
                except Exception:
                    pass

            _post_kit_main_thread(_ui)


def _csv_play_progress_ticker_snap_loop(screen: int = 1) -> None:
    """공정만보기: 스냅샷 기준 진행률 (JSON 시각·건수로 점프). 화면별 독립."""
    si = max(1, int(screen))
    with csv_play_screen_binding(si):
        sess = csv_play_screen_session(si)
        while not sess.progress_stop.wait(1.0):
            if csv_playback_stop_requested(screen=si):
                break
            _notify_csv_play_progress_ui(screen=si)


def _partition_json_blocks_by_lane(
    blocks: List[CsvTimedPlaybackBlock],
) -> Tuple[
    List[Tuple[int, CsvTimedPlaybackBlock]],
    List[Tuple[int, CsvTimedPlaybackBlock]],
    List[Tuple[int, CsvTimedPlaybackBlock]],
]:
    """``(index, block)`` 리스트 — ATM / VTM / 기타(JSON 있음). dwell 제외."""
    ordered = sorted(blocks, key=lambda b: (b.time_sec, b.sort_order))
    atm: List[Tuple[int, CsvTimedPlaybackBlock]] = []
    vtm: List[Tuple[int, CsvTimedPlaybackBlock]] = []
    other: List[Tuple[int, CsvTimedPlaybackBlock]] = []
    for i, b in enumerate(ordered, 1):
        if not b.steps:
            continue
        lane = _playback_lane_from_block(b)
        if lane == "atm":
            atm.append((i, b))
        elif lane == "vtm":
            vtm.append((i, b))
        else:
            other.append((i, b))
    return atm, vtm, other


def _estimate_process_only_wall_total_sec(
    atm_items: List[Tuple[int, CsvTimedPlaybackBlock]],
    vtm_items: List[Tuple[int, CsvTimedPlaybackBlock]],
    other_items: List[Tuple[int, CsvTimedPlaybackBlock]],
    *,
    sequential: bool,
    gap_sec: float,
) -> float:
    """공정만보기 예상 실경과 [s] — JSON steps 합(레인 병렬 시 max)."""

    def _items_duration(items: List[Tuple[int, CsvTimedPlaybackBlock]]) -> float:
        durs = [_lam_estimate_raw_duration_sec(b.steps) for _, b in items if b.steps]
        if not durs:
            return 0.0
        total = sum(durs)
        if sequential and gap_sec > 1e-9:
            total += float(gap_sec) * max(0, len(durs) - 1)
        return total

    if sequential:
        merged = sorted(
            atm_items + vtm_items + other_items,
            key=lambda x: (x[1].time_sec, x[1].sort_order),
        )
        return max(1.0, _items_duration(merged))
    return max(
        1.0,
        _items_duration(atm_items),
        _items_duration(vtm_items),
        _items_duration(other_items),
    )


def resolve_process_only_wall_total_est(
    snap: Dict[str, Any],
    wall_elapsed: float,
) -> float:
    """공정만보기 실경과 분모 [s] — 재생 시작 시 산출한 고정 예상값(재생 중 증가하지 않음)."""
    base = float(snap.get("wall_total_est", 0.0) or 0.0)
    if base > 1e-6:
        return base
    wall = max(0.0, float(wall_elapsed))
    return max(wall, 1.0)


def _csv_play_normal_lane_worker(
    items: List[Tuple[int, CsvTimedPlaybackBlock]],
    lane: Optional[str],
    registry: Any,
    scheduler: Any,
    *,
    t0: float,
    speed_scale: float,
    lane_coordinator: _CsvPlaybackLaneCoordinator,
    usd_context_name: Optional[str] = None,
    lam_window: Any = None,
    play_screen: Optional[int] = None,
    play_epoch: Optional[int] = None,
) -> None:
    """일반 재생 레인 worker — 레인당 1스레드로 JSON 을 CSV t 순서대로 실행.

    블록마다 스레드를 띄우면 (JSON 30개+) ``_dispatch_main``·USD 가 폭주해
    공정만보기 토글 시 Kit 메인이 멈춘다.
    """
    _ = lane
    si = max(1, int(play_screen if play_screen is not None else current_csv_play_screen()))
    for index, block in items:
        if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
            return
        if not _wait_until_csv_play_time(
            float(block.time_sec), screen=si, play_epoch=play_epoch
        ):
            return
        if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
            return
        if not _wait_csv_play_hold_before_new_json(
            screen=si, play_epoch=play_epoch
        ):
            return
        sp = get_csv_play_live_speed_scale(screen=si)
        _csv_playback_execute_json_block(
            block,
            index,
            registry,
            scheduler,
            t0=t0,
            speed_scale=float(sp if sp > 0 else speed_scale),
            lane_coordinator=lane_coordinator,
            json_done_before=0,
            usd_context_name=usd_context_name,
            lam_window=lam_window,
            play_screen=si,
            play_epoch=play_epoch,
        )


def _csv_play_process_only_lane_worker(
    items: List[Tuple[int, CsvTimedPlaybackBlock]],
    lane: Optional[str],
    registry: Any,
    scheduler: Any,
    *,
    t0: float,
    lane_coordinator: _CsvPlaybackLaneCoordinator,
    json_done_counter: List[int],
    json_done_lock: threading.Lock,
    all_json_blocks: List[CsvTimedPlaybackBlock],
    usd_context_name: Optional[str] = None,
    lam_window: Any = None,
    play_screen: Optional[int] = None,
    play_epoch: Optional[int] = None,
) -> None:
    """공정만보기 전용: CSV ``t`` 유지 + 레인·전역 빈 구간만 압축 (일반 재생과 분리)."""
    si = max(1, int(play_screen if play_screen is not None else current_csv_play_screen()))
    lane_ready_wall = float(t0)
    lane_last_csv = 0.0
    for index, block in items:
        if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
            return
        nominal_wall = float(t0) + float(block.time_sec)
        if not _sleep_until_process_only_start(
            lane_ready=lane_ready_wall,
            nominal_wall=nominal_wall,
            block=block,
            all_blocks=all_json_blocks,
            lane_last_csv=lane_last_csv,
            lane=lane,
            play_screen=si,
            play_epoch=play_epoch,
        ):
            return
        if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
            return
        if not _wait_csv_play_hold_before_new_json(
            screen=si, play_epoch=play_epoch
        ):
            return
        with json_done_lock:
            json_done_before = int(json_done_counter[0])
        _csv_playback_execute_json_block(
            block,
            index,
            registry,
            scheduler,
            t0=t0,
            speed_scale=1.0,
            lane_coordinator=lane_coordinator,
            json_done_before=json_done_before,
            usd_context_name=usd_context_name,
            lam_window=lam_window,
            play_screen=play_screen,
            play_epoch=play_epoch,
        )
        with json_done_lock:
            json_done_counter[0] = max(
                int(json_done_counter[0]), json_done_before + 1
            )
        lane_ready_wall = time.monotonic()
        lane_last_csv = max(lane_last_csv, float(block.time_sec))


def _process_only_sequential_settings() -> Tuple[bool, float]:
    """공정만보기 순차 실행 설정 — ``lam_sim_control_defaults`` SSOT."""
    try:
        from .lam_sim_control_defaults import (  # type: ignore
            PROCESS_ONLY_SEQUENTIAL_GAP_SEC,
            PROCESS_ONLY_SEQUENTIAL_LANES,
        )

        return (
            bool(PROCESS_ONLY_SEQUENTIAL_LANES),
            max(0.0, float(PROCESS_ONLY_SEQUENTIAL_GAP_SEC or 0.0)),
        )
    except Exception:
        return False, 0.0


def _process_only_sync_playhead_to_block(
    block: CsvTimedPlaybackBlock,
    *,
    screen: Optional[int] = None,
) -> None:
    """순차 공정만보기 — 블록 시작 시 진행 시계·UI 스냅을 해당 CSV t 로 맞춤."""
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    target_csv = float(block.time_sec)
    _process_only_mark_block_started(block, screen=si)
    sess = csv_play_screen_session(si)
    with sess.process_only_playhead_lock:
        if target_csv > sess.process_only_playhead_csv:
            sess.process_only_playhead_csv = target_csv
        sess.process_only_playhead_wall = time.monotonic()
    with sess.progress_snap_lock:
        sess.progress_snap["csv_t_display"] = target_csv
    _notify_csv_play_progress_ui(screen=si)


def _csv_play_process_only_sequential_worker(
    items: List[Tuple[int, CsvTimedPlaybackBlock]],
    registry: Any,
    scheduler: Any,
    *,
    t0: float,
    lane_coordinator: _CsvPlaybackLaneCoordinator,
    json_done_counter: List[int],
    json_done_lock: threading.Lock,
    all_json_blocks: List[CsvTimedPlaybackBlock],
    usd_context_name: Optional[str] = None,
    lam_window: Any = None,
    play_screen: Optional[int] = None,
    play_epoch: Optional[int] = None,
    gap_sec: float = 0.0,
) -> None:
    """공정만보기 순차 모드 — 전역 시간순 1스레드, JSON 간 겹침 없음."""
    _ = (t0, all_json_blocks)
    si = max(1, int(play_screen if play_screen is not None else current_csv_play_screen()))
    gap = max(0.0, float(gap_sec or 0.0))
    for ord_i, (index, block) in enumerate(items):
        if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
            return
        if ord_i > 0 and gap > 1e-9:
            if not _sleep_csv_playback(gap, screen=si, play_epoch=play_epoch):
                return
        if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
            return
        if not _wait_csv_play_hold_before_new_json(screen=si, play_epoch=play_epoch):
            return
        _process_only_sync_playhead_to_block(block, screen=si)
        with json_done_lock:
            json_done_before = int(json_done_counter[0])
        _csv_playback_execute_json_block(
            block,
            index,
            registry,
            scheduler,
            t0=t0,
            speed_scale=1.0,
            lane_coordinator=lane_coordinator,
            json_done_before=json_done_before,
            usd_context_name=usd_context_name,
            lam_window=lam_window,
            play_screen=play_screen,
            play_epoch=play_epoch,
        )
        with json_done_lock:
            json_done_counter[0] = max(int(json_done_counter[0]), json_done_before + 1)


def _run_csv_timed_playback_process_only(
    registry: Any,
    scheduler: Any,
    blocks: List[CsvTimedPlaybackBlock],
    *,
    resume_from_csv_sec: float = 0.0,
    initial_json_done: int = 0,
    reset_wafer_visibility: bool = True,
    wall_elapsed_offset: float = 0.0,
    paused_in_json: bool = False,
    play_screen: int = 1,
    kit_ext: Any = None,
    dwells: Optional[Sequence["DwellRecord"]] = None,
) -> None:
    """공정만보기: CSV ``t`` 스케줄 유지, dwell·JSON 없는 빈 대기만 생략. 배속 1x."""
    si = max(1, int(play_screen or current_csv_play_screen()))
    lam_window = None
    usd_ctx: Optional[str] = None
    if kit_ext is not None:
        lam_window = getattr(kit_ext, "_lam_window", None) or getattr(kit_ext, "_window", None)
    if lam_window is not None:
        try:
            from .lam_csv_play_execution import resolve_csv_play_execution

            binding = resolve_csv_play_execution(
                lam_window,
                si,
                kit_ext=kit_ext,
                require_aux=(si > 1),
            )
            if binding is not None:
                registry = binding.registry
                scheduler = binding.scheduler
                usd_ctx = binding.usd_context_name
            elif si > 1:
                print(
                    f"{_PRINT_PREFIX} 화면{si} 공정만보기 중단 — split runtime 없음",
                    flush=True,
                )
                return
        except Exception as exc:
            print(f"{_PRINT_PREFIX} resolve process-only binding screen={si}: {exc}", flush=True)
            if si > 1:
                return

    ordered = sorted(blocks, key=lambda b: (b.time_sec, b.sort_order))
    if not ordered:
        print(f"{_PRINT_PREFIX} CSV 공정만보기: 블록 없음", flush=True)
        return

    if reset_wafer_visibility:
        apply_csv_play_initial_wafer_visibility_for_screen(
            int(play_screen or 1),
            kit_ext=kit_ext,
            dwells=dwells,
        )

    resume = max(0.0, float(resume_from_csv_sec or 0.0))
    all_json_blocks = [b for b in ordered if b.steps]
    atm_items, vtm_items, other_items = _partition_json_blocks_by_lane(blocks)
    # begin 전에 스냅 — begin 이 completed_json_keys 를 clear 함
    excl_keys = set(csv_play_screen_session(si).completed_json_keys)
    atm_items = _filter_process_only_lane_items(
        atm_items,
        resume,
        paused_in_json=paused_in_json,
        exclude_schedule_keys=excl_keys,
    )
    vtm_items = _filter_process_only_lane_items(
        vtm_items,
        resume,
        paused_in_json=paused_in_json,
        exclude_schedule_keys=excl_keys,
    )
    other_items = _filter_process_only_lane_items(
        other_items,
        resume,
        paused_in_json=paused_in_json,
        exclude_schedule_keys=excl_keys,
    )
    n_json_remaining = len(atm_items) + len(vtm_items) + len(other_items)
    n_json_all = sum(1 for b in ordered if b.steps)
    if n_json_remaining <= 0:
        print(f"{_PRINT_PREFIX} 공정만보기: 이어서 재생할 JSON 없음", flush=True)
        return
    csv_total = max(float(b.time_sec) for b in ordered)

    seq_lanes, seq_gap = _process_only_sequential_settings()
    resume_note = f" · 이어서 t≥{resume:.1f}s" if resume > 1e-9 else ""
    if seq_lanes:
        gap_note = f" · JSON 간격 {seq_gap:g}s" if seq_gap > 1e-9 else ""
        print(
            f"{_PRINT_PREFIX} ▶ 공정만보기 재생(순차) | JSON {n_json_remaining}건 | "
            f"CSV t 표시 0~{csv_total:.1f}s | ATM {len(atm_items)} · VTM {len(vtm_items)} | "
            f"배속 1x · 이전 JSON 완료 후 다음 시작{gap_note}"
            f"{resume_note}",
            flush=True,
        )
    else:
        print(
            f"{_PRINT_PREFIX} ▶ 공정만보기 재생 | JSON {n_json_remaining}건 | "
            f"CSV t 표시 0~{csv_total:.1f}s | ATM {len(atm_items)} · VTM {len(vtm_items)} | "
            f"배속 1x · idle 구간만 점프 · CSV t 간격으로 레인 시작(겹침 시 병렬)"
            f"{resume_note}",
            flush=True,
        )

    wall_off = max(0.0, float(wall_elapsed_offset or 0.0))
    t0 = time.monotonic() - wall_off
    stopped = False
    clear_csv_play_timeline_highlight(screen=int(play_screen or 1))
    # 실시간 공정만보기 전환이 session_active 를 보도록 시계 세션도 연다.
    play_epoch = begin_csv_play_timekeeping(
        csv_offset=resume,
        speed_scale=1.0,
        wall_elapsed_offset=wall_off,
        screen=si,
    )
    _reset_csv_play_global_json_end(t0=t0, csv_start=resume, screen=si)
    _reset_process_only_playhead(t0=t0, csv_start=resume, screen=si)
    _seed_process_only_started_before_resume(
        all_json_blocks,
        resume=resume,
        paused_in_json=paused_in_json,
        screen=si,
        completed_keys=excl_keys,
    )
    # idle 구간 재개·시작 직후 다음 JSON CSV t 로 즉시 점프
    _process_only_try_idle_compress_to_next(all_json_blocks, screen=si)
    try:
        nxt = _process_only_next_unstarted_block(all_json_blocks, screen=si)
        if nxt is not None:
            print(
                f"{_PRINT_PREFIX} 공정만보기 resume playhead→"
                f"{_process_only_playhead_csv_now(screen=si):.1f}s "
                f"next_json t={float(nxt.time_sec):.1f}s",
                flush=True,
            )
        else:
            print(
                f"{_PRINT_PREFIX} 공정만보기 resume — next_json 없음 "
                f"(started={len(csv_play_screen_session(si).process_only_started_keys)})",
                flush=True,
            )
    except Exception:
        pass
    lane_coordinator = _CsvPlaybackLaneCoordinator(play_screen=si)
    json_done_counter: List[int] = [max(0, int(initial_json_done))]
    json_done_lock = threading.Lock()
    _reset_csv_play_progress_snap(
        process_only=True,
        json_total=max(n_json_all, n_json_remaining),
        csv_total=csv_total,
        t0=t0,
        speed_scale=1.0,
        screen=si,
    )
    wall_total_est = _estimate_process_only_wall_total_sec(
        atm_items,
        vtm_items,
        other_items,
        sequential=seq_lanes,
        gap_sec=seq_gap,
    )
    sess = csv_play_screen_session(si)
    with sess.progress_snap_lock:
        sess.progress_snap["csv_t_display"] = float(resume)
        sess.progress_snap["csv_time_offset"] = float(resume)
        sess.progress_snap["wall_elapsed_display"] = float(wall_off)
        sess.progress_snap["json_done"] = max(0, int(initial_json_done))
        sess.progress_snap["wall_total_est"] = float(wall_total_est)
    _notify_csv_play_progress_ui(screen=si)
    sess.progress_stop.clear()
    ticker = threading.Thread(
        target=_csv_play_progress_ticker_snap_loop,
        args=(si,),
        daemon=True,
        name=f"lam-sim-csv-play-progress-snap-s{si}",
    )
    ticker.start()
    workers: List[threading.Thread] = []
    try:
        lane_kw = {
            "t0": t0,
            "lane_coordinator": lane_coordinator,
            "json_done_counter": json_done_counter,
            "json_done_lock": json_done_lock,
            "all_json_blocks": all_json_blocks,
            "usd_context_name": usd_ctx,
            "lam_window": lam_window,
            "play_screen": si,
            "play_epoch": play_epoch,
        }
        if seq_lanes:
            sequential_items = sorted(
                atm_items + vtm_items + other_items,
                key=lambda x: (x[1].time_sec, x[1].sort_order),
            )
            workers.append(
                _spawn_csv_play_bound_thread(
                    si,
                    name=f"lam-csv-play-seq-s{si}",
                    target=_csv_play_process_only_sequential_worker,
                    args=(sequential_items, registry, scheduler),
                    kwargs={**lane_kw, "gap_sec": seq_gap},
                )
            )
        else:
            if atm_items:
                workers.append(
                    _spawn_csv_play_bound_thread(
                        si,
                        name=f"lam-csv-play-atm-seq-s{si}",
                        target=_csv_play_process_only_lane_worker,
                        args=(atm_items, "atm", registry, scheduler),
                        kwargs=lane_kw,
                    )
                )
            if vtm_items:
                workers.append(
                    _spawn_csv_play_bound_thread(
                        si,
                        name=f"lam-csv-play-vtm-seq-s{si}",
                        target=_csv_play_process_only_lane_worker,
                        args=(vtm_items, "vtm", registry, scheduler),
                        kwargs=lane_kw,
                    )
                )
            if other_items:
                workers.append(
                    _spawn_csv_play_bound_thread(
                        si,
                        name=f"lam-csv-play-other-seq-s{si}",
                        target=_csv_play_process_only_lane_worker,
                        args=(other_items, None, registry, scheduler),
                        kwargs=lane_kw,
                    )
                )
        for t in workers:
            if csv_playback_stop_requested(screen=si):
                stopped = True
            t.start()
        _join_csv_play_workers(workers, screen=si)
        if csv_playback_stop_requested(screen=si):
            stopped = True
        _csv_play_progress_mark_json_done(n_json_all, screen=si)
        _notify_csv_play_progress_ui(screen=si)
    finally:
        sess.progress_stop.set()
        csv_play_screen_session(si).material_test_stop.set()
        clear_csv_play_timeline_highlight(screen=si)
        end_csv_play_timekeeping(screen=si, play_epoch=play_epoch)
        try:
            ticker.join(timeout=2.0)
        except Exception:
            pass

    total_wall = time.monotonic() - t0
    if stopped:
        print(
            f"{_PRINT_PREFIX} 공정만보기 중지 | 실경과 {total_wall:.1f}s | JSON {n_json_all}건",
            flush=True,
        )
    else:
        print(
            f"{_PRINT_PREFIX} 공정만보기 완료 | 실경과 {total_wall:.1f}s | JSON {n_json_all}건",
            flush=True,
        )


@dataclass(frozen=True)
class NonAtmFirstWaferInit:
    """dwell 타임라인 — 웨이퍼(FOUP+slot)별 첫 위치가 ATM 팔이 아닐 때 Play 시작 SSOT."""

    lot_id: str
    cassette_slot: int
    foup_index: int
    slot_key: str
    label: str

    @property
    def foup_slot_key(self) -> str:
        return f"foup{int(self.foup_index)}_{int(self.cassette_slot)}"


def collect_non_atm_first_wafer_inits(
    dwells: Optional[Sequence["DwellRecord"]],
) -> Tuple[NonAtmFirstWaferInit, ...]:
    """
    웨이퍼(``foup_index`` + ``cassette_slot``)별 시간순 첫 dwell — ATM 팔이 아니면 반환.

    CSV / Federation / GET URL 모두 ``DwellRecord`` 로 통일된 뒤 이 함수만 사용한다.
    """
    if not dwells:
        return ()
    out: List[NonAtmFirstWaferInit] = []
    tours: Dict[Tuple[int, int], List["DwellRecord"]] = {}
    for d in dwells:
        try:
            wk = (max(1, min(3, int(d.foup_index))), int(d.cassette_slot))
        except Exception:
            continue
        tours.setdefault(wk, []).append(d)
    for key in tours:
        tours[key].sort(key=lambda x: float(x.start_sec))
    for (foup_i, cassette_slot), tour in sorted(
        tours.items(), key=lambda kv: (kv[0][0], kv[0][1])
    ):
        if not tour:
            continue
        first = tour[0]
        sk = str(getattr(first, "slot_key", "") or "").strip()
        if not sk or sk == LOGICAL_SLOT_ATM_ARM:
            continue
        try:
            label = f"{int(cassette_slot):02d}"
        except Exception:
            label = str(cassette_slot or "").strip()
        if not label:
            continue
        out.append(
            NonAtmFirstWaferInit(
                lot_id=str(getattr(first, "lot_id", "") or "").strip(),
                cassette_slot=int(cassette_slot),
                foup_index=int(foup_i),
                slot_key=sk,
                label=str(label),
            )
        )
    return tuple(out)


# CSV Play 시작 시 1회만 보이게 할 FOUP 슬롯 (각 25).
_CSV_PLAY_INITIAL_VISIBLE_FOUP_SLOT_KEYS = frozenset(
    f"foup{f}_{i}" for f in (1, 2, 3) for i in range(1, 26)
)
# 이벤트 JSON 스캐폴드 등 테스트 stage MOVE 대상 (TBS 리셋에 포함).
_CSV_PLAY_KNOWN_MOTION_PRIM_PATHS: Tuple[str, ...] = ("/World/aaa",)
# 정지(초기화) 시 Z 를 되돌릴 기준 슬롯 (Play 시작 전 대기 높이).
_CSV_PLAY_Z_HOME_ATM_SLOT_KEY = "foup1_1"
_CSV_PLAY_Z_HOME_VTM_SLOT_KEY = "foup1_1"


def _set_wafer_prim_visible(stage: Any, path: str, visible: bool) -> bool:
    """prim 1개 visibility — 성공 시 True."""
    from pxr import UsdGeom  # type: ignore

    try:
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            return False
        img = UsdGeom.Imageable(prim)
        if not img:
            return False
        if visible:
            img.MakeVisible()
        else:
            img.MakeInvisible()
        return True
    except Exception as exc:
        print(
            f"{_PRINT_PREFIX} CSV Play visibility 실패 path={path!r}: {exc}",
            flush=True,
        )
        return False


def apply_csv_play_initial_wafer_visibility_on_stage(
    stage: Any,
    *,
    screen: int = 1,
    sync_label_tracker: bool = True,
    dwells: Optional[Sequence["DwellRecord"]] = None,
) -> Tuple[int, int]:
    """Play 시작/정지 — FOUP baseline + (dwell) ATM 팔 외 최초 위치 wafer show.

    Args:
        sync_label_tracker: True(재생 시작) 시 FOUP baseline + non-ATM-first 라벨·점유.
        dwells: 파싱된 dwell — None 이면 FOUP-only baseline (기존과 동일).
    """
    from .lam_wafer_prim_paths import resolve_wafer_prim_path_on_stage

    si = max(1, int(screen))
    wafer_map = load_wafer_prim_by_slot_key()
    if not stage or not wafer_map:
        return (0, 0)

    non_atm_first = collect_non_atm_first_wafer_inits(dwells)
    show_slot_keys = {e.slot_key for e in non_atm_first}
    hide_foup_keys = {e.foup_slot_key for e in non_atm_first}

    show_ok = 0
    hide_ok = 0
    seen_show: set[str] = set()
    seen_hide: set[str] = set()

    for slot_key, raw in wafer_map.items():
        p = resolve_wafer_prim_path_on_stage(stage, slot_key, (raw or "").strip())
        if not p:
            continue
        sk = str(slot_key or "").strip()
        if sk in show_slot_keys:
            if p in seen_show:
                continue
            seen_show.add(p)
            if _set_wafer_prim_visible(stage, p, True):
                show_ok += 1
        elif sk in _CSV_PLAY_INITIAL_VISIBLE_FOUP_SLOT_KEYS:
            if sk in hide_foup_keys:
                if p in seen_hide:
                    continue
                seen_hide.add(p)
                if _set_wafer_prim_visible(stage, p, False):
                    hide_ok += 1
            else:
                if p in seen_show:
                    continue
                seen_show.add(p)
                if _set_wafer_prim_visible(stage, p, True):
                    show_ok += 1
        else:
            if p in seen_hide:
                continue
            seen_hide.add(p)
            if _set_wafer_prim_visible(stage, p, False):
                hide_ok += 1

    extra_note = ""
    if non_atm_first:
        extra_note = f" · non-ATM-first {len(non_atm_first)}"
    print(
        f"{_PRINT_PREFIX} CSV Play 웨이퍼 visibility: "
        f"화면{si} show {show_ok} · hide {hide_ok}{extra_note}",
        flush=True,
    )
    if sync_label_tracker:
        try:
            from .lam_wafer_viewport_labels import (
                get_wafer_label_tracker,
                notify_wafer_label_tracker_changed,
            )

            tracker = get_wafer_label_tracker(si)
            tracker.reset_foup_baseline(wafer_map, stage=stage)
            if non_atm_first:
                tracker.apply_non_atm_first_baseline(
                    non_atm_first,
                    wafer_map=wafer_map,
                    stage=stage,
                )
            notify_wafer_label_tracker_changed(si)
        except Exception:
            pass
        try:
            from .lam_floorplan_occupancy import (
                seed_floorplan_foup_baseline,
                seed_non_atm_first_wafer_occupancy,
            )

            seed_floorplan_foup_baseline(si)
            if non_atm_first:
                seed_non_atm_first_wafer_occupancy(si, non_atm_first)
            try:
                from .lam_viewport_floorplan_panel import refresh_floorplan_panels_ui

                refresh_floorplan_panels_ui(screen=si)
            except Exception:
                pass
        except Exception:
            pass
        try:
            from .lam_viewport_overlay_state import seed_foup_counts_from_non_atm_first

            n_foup_seed = seed_foup_counts_from_non_atm_first(
                non_atm_first,
                screen=si,
            )
            if n_foup_seed:
                print(
                    f"{_PRINT_PREFIX} FOUP 집계 seed: 화면{si} "
                    f"non-ATM-first {n_foup_seed} → 진행중 반영",
                    flush=True,
                )
        except Exception:
            pass
        try:
            from .lam_device_label_highlight import seed_airlock_highlights_from_dwells

            seed_airlock_highlights_from_dwells(dwells, screen=si)
        except Exception:
            pass
        try:
            from .lam_foup_lot_display import apply_foup_lot_display_from_dwells

            apply_foup_lot_display_from_dwells(dwells, screen=si)
        except Exception:
            pass
    return (show_ok, hide_ok)


def _collect_move_rotate_prims_from_event_jsons() -> List[str]:
    """``lam_event_sequences/*.json`` 의 MOVE/ROTATE ``prim`` (템플릿 토큰 제외)."""
    from .lam_event_sequences import LAM_EVENT_NAMES, event_json_path

    seen: set[str] = set()
    out: List[str] = []
    for name in LAM_EVENT_NAMES:
        path = event_json_path(name)
        if not path.is_file():
            continue
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            continue
        if not isinstance(raw, list):
            continue
        for st in raw:
            if not isinstance(st, dict):
                continue
            t = str(st.get("type") or "").upper()
            if t not in ("MOVE", "ROTATE"):
                continue
            p = str(st.get("prim") or "").strip()
            if not p or p.startswith("{") or p in seen:
                continue
            seen.add(p)
            out.append(p)
    return out


def collect_csv_play_motion_reset_prim_paths(
    registry: Any = None,
    *,
    stage: Any = None,
) -> List[str]:
    """CSV 정지(초기화) — Z stage · 이벤트 JSON MOVE · 인스턴스 · wafer (stage 존재 여부 무관)."""
    from .lam_wafer_prim_paths import resolve_wafer_prim_path_on_stage

    seen: set[str] = set()
    out: List[str] = []

    def _add(path: str) -> None:
        p = (path or "").strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)

    refresh_lam_sim_runtime_tables_from_config()
    cfg = LAM_SIM_VIRTUAL_CONFIG
    _add(cfg.atm_height_prim_path or ATM_Z_MOVE_PRIM_PATH)
    _add(cfg.vtm_position_prim_path or VTM_Z_MOVE_PRIM_PATH)
    for p in _CSV_PLAY_KNOWN_MOTION_PRIM_PATHS:
        _add(p)
    for p in _collect_move_rotate_prims_from_event_jsons():
        _add(p)
    if registry is not None:
        try:
            for inst in registry.all_instances():
                _add(str(getattr(inst, "prim_path", "") or ""))
        except Exception:
            pass
    for _sk, raw in load_wafer_prim_by_slot_key().items():
        _add(raw)
        if stage is not None:
            _add(resolve_wafer_prim_path_on_stage(stage, _sk, raw))
    return out


def _end_all_csv_play_replay_modes(registry: Any, scheduler: Any) -> int:
    """TIMESAMPLES_REPLAY freeze 해제 — 인스턴스 prim 수 반환."""
    n = 0
    if registry is None:
        return n
    inst_paths = []
    try:
        for inst in registry.all_instances():
            p = str(getattr(inst, "prim_path", "") or "").strip()
            if p.startswith("/"):
                inst_paths.append(p)
    except Exception:
        return n
    if scheduler is not None:
        try:
            stop_all = getattr(scheduler, "stop_all", None)
            if callable(stop_all):
                stop_all()
        except Exception:
            pass
        end_replay = getattr(scheduler, "end_replay_mode", None)
        if callable(end_replay):
            for p in inst_paths:
                try:
                    end_replay(p)
                    n += 1
                except Exception:
                    pass
    return n


def _snap_csv_play_robot_z_home(stage: Any) -> None:
    """ATM/VTM Z MOVE prim → Play 시작 전 기준 슬롯 높이(TBS/mm)로 즉시 스냅."""
    from . import lam_rotate_animation as _lrx
    from . import lam_translate_animation as _ltx

    cfg = LAM_SIM_VIRTUAL_CONFIG
    pairs = (
        (cfg.atm_height_prim_path or ATM_Z_MOVE_PRIM_PATH, _CSV_PLAY_Z_HOME_ATM_SLOT_KEY, "atm"),
        (cfg.vtm_position_prim_path or VTM_Z_MOVE_PRIM_PATH, _CSV_PLAY_Z_HOME_VTM_SLOT_KEY, "vtm"),
    )
    for z_prim, slot_key, robot in pairs:
        zp = (z_prim or "").strip()
        if not zp:
            continue
        prim = stage.GetPrimAtPath(zp)
        if not prim or not prim.IsValid():
            continue
        dz = cfg.slot_z_move_target_m(slot_key, robot=robot)
        try:
            _lrx.stop_prim_rotate_animation(zp)
            _ltx.stop_prim_translate_animation(zp)
            if dz is None:
                _ltx.zero_tbs_offset_translate_at_path(zp)
            else:
                _ltx.snap_tbs_offset_translate_to_absolute(zp, 0.0, 0.0, float(dz))
            print(
                f"{_PRINT_PREFIX}   Z home snap {zp} → slot={slot_key} dz={dz}",
                flush=True,
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX}   Z home snap failed {zp}: {exc}", flush=True)


def _snap_csv_play_scaffold_motion_prims_home(stage: Any) -> None:
    """테스트 JSON MOVE (예: ``/World/aaa``) TBS → (0,0,0)."""
    from . import lam_rotate_animation as _lrx
    from . import lam_translate_animation as _ltx

    for p in _CSV_PLAY_KNOWN_MOTION_PRIM_PATHS:
        path = (p or "").strip()
        if not path:
            continue
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            continue
        try:
            _lrx.stop_prim_rotate_animation(path)
            _ltx.stop_prim_translate_animation(path)
            _ltx.zero_tbs_offset_translate_at_path(path)
            print(f"{_PRINT_PREFIX}   motion prim reset {path} → TBS (0,0,0)", flush=True)
        except Exception as exc:
            print(f"{_PRINT_PREFIX}   motion prim reset failed {path}: {exc}", flush=True)


def apply_csv_play_initial_wafer_visibility(*, wait: bool = True) -> None:
    """CSV Play **시작/정지(초기화)**: FOUP show · 나머지 hide (메인 스레드 USD write)."""
    apply_csv_play_initial_wafer_visibility_for_screen(1, wait=wait)


def apply_csv_play_initial_wafer_visibility_for_screen(
    screen: int,
    *,
    wait: bool = True,
    kit_ext: Any = None,
    dwells: Optional[Sequence["DwellRecord"]] = None,
) -> None:
    """화면별 Stage 에 wafer visibility 초기화."""
    from .lam_sequence_engine import _dispatch_main, _dispatch_main_wait, _stage

    si = max(1, int(screen))
    dwells_use = list(dwells) if dwells else None

    def _do_in_main() -> None:
        st = None
        if si <= 1:
            st = _stage()
        elif kit_ext is not None:
            try:
                from .lam_csv_play_screen import get_stage_for_screen

                st = get_stage_for_screen(kit_ext, si)
            except Exception:
                st = None
        if st is None:
            print(
                f"{_PRINT_PREFIX} CSV Play visibility skip — stage 없음 (화면{si})",
                flush=True,
            )
            return
        apply_csv_play_initial_wafer_visibility_on_stage(
            st,
            screen=si,
            dwells=dwells_use,
        )

    if wait:
        if _dispatch_main_wait(_do_in_main, timeout=15.0):
            return
        print(
            f"{_PRINT_PREFIX} CSV Play visibility 타임아웃 (15s, 화면{si})",
            flush=True,
        )
    else:
        _dispatch_main(_do_in_main)


def reset_csv_play_stop_initial_state(
    registry: Any = None,
    scheduler: Any = None,
    *,
    usd_context_name: Optional[str] = None,
    screen: Optional[int] = None,
) -> None:
    """CSV Play **정지(초기화)**: 로봇 Z·JSON MOVE prim 위치 복원 + FOUP show / 기타 hide.

    - 웨이퍼 pick/place 는 ``PRIM_VISIBILITY`` — FOUP 75 show / 팔·챔버 hide.
    - 팔 Z·``atm_foup*_pick.json`` 의 ``/World/aaa`` 등은 **TBS_OFFSET→0** 후
      Z 는 ``foup1_1`` 기준 높이로 스냅.
    - ``usd_context_name`` 지정 시 해당 화면 stage 만 초기 (다른 화면 재생 유지).
  """
    from . import lam_rotate_animation as _lrx
    from . import lam_translate_animation as _ltx
    from .lam_sequence_engine import (
        _dispatch_main_wait,
        _reset_tbs_offset_ops_for_paths,
    )
    from .lam_usd_stage_context import (
        get_stage_for_context_name,
        pop_usd_context_name,
        push_usd_context_name,
    )

    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    ctx = str(usd_context_name or "").strip() or None
    print(
        f"{_PRINT_PREFIX} === CSV Play 정지(초기화) screen={si} ctx={ctx!r} ===",
        flush=True,
    )

    # STATUS 패널·진행 스냅 — 해당 화면만 비움 (다른 화면 재생 유지)
    try:
        from .lam_viewport_overlay_state import set_last_state_title

        set_last_state_title("", screen=si)
    except Exception:
        pass
    try:
        _reset_csv_play_progress_snap(
            process_only=False,
            json_total=0,
            csv_total=0.0,
            t0=0.0,
            speed_scale=1.0,
            screen=si,
        )
        clear_csv_play_timeline_highlight(screen=si)
    except Exception:
        pass
    try:
        from .lam_wafer_viewport_labels import (
            get_wafer_label_tracker,
            notify_wafer_label_tracker_changed,
            teardown_wafer_viewport_labels,
        )

        get_wafer_label_tracker(si).clear()
        try:
            teardown_wafer_viewport_labels(screen=si)
        except Exception:
            pass
        notify_wafer_label_tracker_changed(si)
        try:
            from .lam_floorplan_occupancy import clear_floorplan_occupancy

            clear_floorplan_occupancy(si)
        except Exception:
            pass
    except Exception:
        pass

    try:
        # 화면1(default context="")도 해당 context만 중지한다.
        # stop_all_*은 화면2 aux 애니메이션까지 끊으므로 화면별 reset에서 금지.
        _ltx.stop_translate_animations_for_context(ctx)
        _lrx.stop_rotate_animations_for_context(ctx)
    except Exception as exc:
        print(f"{_PRINT_PREFIX}   애니 중지 경고: {exc}", flush=True)

    def _do_in_main() -> None:
        prev = push_usd_context_name(ctx)
        try:
            st = get_stage_for_context_name(ctx)
            if st is None:
                print(f"{_PRINT_PREFIX}   초기화 skip — stage 없음", flush=True)
                return
            n_replay = _end_all_csv_play_replay_modes(registry, scheduler)
            if n_replay:
                print(
                    f"{_PRINT_PREFIX}   replay mode 해제 {n_replay} instance(s)",
                    flush=True,
                )
            all_paths = collect_csv_play_motion_reset_prim_paths(registry, stage=st)
            on_stage: List[str] = []
            for p in all_paths:
                rp = (p or "").strip()
                if not rp:
                    continue
                prim = st.GetPrimAtPath(rp)
                if prim and prim.IsValid():
                    on_stage.append(rp)
            if on_stage:
                print(
                    f"{_PRINT_PREFIX}   TBS_OFFSET→0 (stage 존재 {len(on_stage)}/{len(all_paths)} prim)",
                    flush=True,
                )
                _reset_tbs_offset_ops_for_paths(on_stage, usd_context_name=ctx)
            else:
                print(
                    f"{_PRINT_PREFIX}   ⚠ stage 에 TBS reset 대상 prim 없음",
                    flush=True,
                )
            _snap_csv_play_robot_z_home(st)
            _snap_csv_play_scaffold_motion_prims_home(st)
            # 정지: wafer prim 위치만 복원. 라벨 트래커는 이미 clear — FOUP 번호 재표시 금지.
            apply_csv_play_initial_wafer_visibility_on_stage(
                st, screen=si, sync_label_tracker=False
            )
            try:
                from .lam_foup_usage_hide import restore_foup_usage_extra_hide_on_stage

                restore_foup_usage_extra_hide_on_stage(st, screen=si)
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX}   foup usage hide restore: {exc}",
                    flush=True,
                )
        finally:
            pop_usd_context_name(prev)

    ok = _dispatch_main_wait(_do_in_main, timeout=25.0)
    if not ok:
        print(
            f"{_PRINT_PREFIX}   ⚠ 정지(초기화) main-thread 타임아웃 (25s)",
            flush=True,
        )
    print(f"{_PRINT_PREFIX} === CSV Play 정지(초기화) 완료 ===", flush=True)


# CSV Play material binding 테스트 스케줄 (setTimeout 유사, Play 중지 시 탈출)



def apply_material_binding_to_prim(mesh_prim_path: str, material_prim_path: str) -> bool:
    """합성 stage 에서 mesh prim 에 material 을 바인딩 (main thread USD write).

    실패 시 ``False`` 반환·로그만 남기고 예외는 밖으로 전파하지 않는다.
    """
    mesh_path = (mesh_prim_path or "").strip()
    mat_path = (material_prim_path or "").strip()
    if not mesh_path or not mat_path:
        print(
            f"{_PRINT_PREFIX} material bind skip — 빈 경로 mesh={mesh_path!r} mat={mat_path!r}",
            flush=True,
        )
        return False

    outcome: Dict[str, Any] = {"ok": False, "msg": ""}

    def _do_in_main() -> None:
        from pxr import Usd, UsdShade  # type: ignore

        from .lam_sequence_engine import _stage

        stage = _stage()
        if stage is None:
            outcome["msg"] = "stage 없음"
            return
        try:
            stage.SetEditTarget(Usd.EditTarget(stage.GetRootLayer()))
        except Exception as exc:
            outcome["msg"] = f"edit target: {exc}"
            return

        mesh_prim = stage.GetPrimAtPath(mesh_path)
        if not mesh_prim or not mesh_prim.IsValid():
            outcome["msg"] = f"mesh prim 없음: {mesh_path}"
            return
        mat_prim = stage.GetPrimAtPath(mat_path)
        if not mat_prim or not mat_prim.IsValid():
            outcome["msg"] = f"material prim 없음: {mat_path}"
            return
        try:
            material = UsdShade.Material(mat_prim)
            bind_api = UsdShade.MaterialBindingAPI.Apply(mesh_prim)
            bind_api.Bind(material)
            outcome["ok"] = True
            outcome["msg"] = "OK"
        except Exception as exc:
            outcome["msg"] = str(exc)

    try:
        from .lam_sequence_engine import _dispatch_main_wait

        if not _dispatch_main_wait(_do_in_main, timeout=15.0):
            print(
                f"{_PRINT_PREFIX} material bind timeout mesh={mesh_path} mat={mat_path}",
                flush=True,
            )
            return False
    except Exception as exc:
        print(
            f"{_PRINT_PREFIX} material bind dispatch 실패 mesh={mesh_path}: {exc}",
            flush=True,
        )
        return False

    if outcome["ok"]:
        print(
            f"{_PRINT_PREFIX} material bind OK mesh={mesh_path} mat={mat_path}",
            flush=True,
        )
        return True
    print(
        f"{_PRINT_PREFIX} material bind 실패 mesh={mesh_path} mat={mat_path} — {outcome['msg']}",
        flush=True,
    )
    return False


def _csv_play_material_binding_test_worker(
    calls: List[Tuple[str, str, float]],
) -> None:
    """``calls``: ``(mesh_path, material_path, delay_sec_from_play_start)`` — 오름차순 권장."""
    t0 = time.monotonic()
    for mesh_path, mat_path, delay_sec in calls:
        target = t0 + max(0.0, float(delay_sec))
        while time.monotonic() < target:
            if (
                csv_play_screen_session().material_test_stop.is_set()
                or csv_playback_stop_requested()
            ):
                return
            time.sleep(min(0.05, target - time.monotonic()))
        if csv_play_screen_session().material_test_stop.is_set() or csv_playback_stop_requested():
            return
        apply_material_binding_to_prim(mesh_path, mat_path)


def start_csv_play_material_binding_test(
    calls: Optional[List[Tuple[str, str, float]]] = None,
) -> None:
    """CSV Play 시작 직후 material 바인딩을 wall-clock 기준으로 예약 (기본 3회 / 0·1·2초).

    테스트 시 ``run_csv_timed_playback`` 안의 호출 블록 주석을 해제한다.
    """
    if calls is None:
        calls = [
            ("/World/MyMesh", "/World/Looks/MyMaterial", 0.0),
            ("/World/MyMesh2", "/World/Looks/MyMaterial2", 1.0),
            ("/World/MyMesh3", "/World/Looks/MyMaterial3", 2.0),
        ]
    ordered = sorted(calls, key=lambda c: float(c[2]))
    csv_play_screen_session().material_test_stop.clear()
    threading.Thread(
        target=_csv_play_material_binding_test_worker,
        args=(ordered,),
        daemon=True,
        name="lam-csv-play-material-test",
    ).start()


def run_csv_timed_playback(
    registry: Any,
    scheduler: Any,
    blocks: List[CsvTimedPlaybackBlock],
    *,
    speed_scale: float = 1.0,
    process_only: bool = False,
    resume_from_csv_sec: float = 0.0,
    initial_json_done: int = 0,
    reset_wafer_visibility: bool = True,
    wall_elapsed_offset: float = 0.0,
    paused_in_json: bool = False,
    play_screen: int = 1,
    kit_ext: Any = None,
    dwells: Optional[Sequence["DwellRecord"]] = None,
) -> None:
    """CSV ``eqp_start_tm`` 스케줄 재생 (``speed_scale`` 배속).

    - **일반**: JSON 은 ATM/VTM/기타 **레인당 1스레드**(최대 3) + dwell 로그 1.
      블록마다 스레드를 만들면 Kit main dispatch 가 폭주해 토글 시 앱이 멈춘다.
    - **공정만보기** (``process_only``): dwell 생략·배속 1x·빈 구간 압축·레인 간 JSON 실행 중 병렬(일반 재생 경로 무관).
    - **ATM** / **VTM** 은 CSV 시각에 맞춰 교차 시작 가능(동시 0초 시작 아님).
    - **동일 레인** 은 ``max(이전 JSON 종료, 다음 CSV t)`` 에 시작(직렬).
    - dwell(``steps`` 없음) 은 일반 모드에서만 해당 시각에 로그.
    - **이어서 재생**: ``resume_from_csv_sec`` 이후 블록만 실행(해당 JSON 은 처음부터).
    """
    if process_only:
        _run_csv_timed_playback_process_only(
            registry,
            scheduler,
            blocks,
            resume_from_csv_sec=resume_from_csv_sec,
            initial_json_done=initial_json_done,
            reset_wafer_visibility=reset_wafer_visibility,
            wall_elapsed_offset=wall_elapsed_offset,
            paused_in_json=paused_in_json,
            play_screen=int(play_screen or 1),
            kit_ext=kit_ext,
            dwells=dwells,
        )
        return

    sp = float(max(0.01, speed_scale or 1.0))
    si = max(1, int(play_screen or current_csv_play_screen()))
    lam_window = None
    usd_ctx: Optional[str] = None
    if kit_ext is not None:
        lam_window = getattr(kit_ext, "_lam_window", None) or getattr(kit_ext, "_window", None)
    if lam_window is not None:
        try:
            from .lam_csv_play_execution import resolve_csv_play_execution

            binding = resolve_csv_play_execution(
                lam_window,
                si,
                kit_ext=kit_ext,
                require_aux=(si > 1),
            )
            if binding is not None:
                registry = binding.registry
                scheduler = binding.scheduler
                usd_ctx = binding.usd_context_name
            elif si > 1:
                print(
                    f"{_PRINT_PREFIX} 화면{si} 재생 중단 — split runtime 없음",
                    flush=True,
                )
                return
        except Exception as exc:
            print(f"{_PRINT_PREFIX} resolve play binding screen={si}: {exc}", flush=True)
            if si > 1:
                return

    all_ordered = sorted(blocks, key=lambda b: (b.time_sec, b.sort_order))
    if not all_ordered:
        print(f"{_PRINT_PREFIX} CSV timed playback: 블록 없음", flush=True)
        return

    resume = max(0.0, float(resume_from_csv_sec or 0.0))
    excl_keys = set(csv_play_screen_session(si).completed_json_keys)
    ordered = _filter_blocks_from_csv_time(
        all_ordered,
        resume,
        paused_in_json=paused_in_json,
        exclude_schedule_keys=excl_keys,
    )
    if not ordered:
        print(f"{_PRINT_PREFIX} CSV Play: 이어서 재생할 블록 없음", flush=True)
        return

    if reset_wafer_visibility:
        apply_csv_play_initial_wafer_visibility_for_screen(
            int(play_screen or 1),
            kit_ext=kit_ext,
            dwells=dwells,
        )

    # -------------------------------------------------------------------------
    # CSV Play material binding 테스트 (주석 해제 후 mesh / material 경로 수정)
    # Play 시작 기준 setTimeout 유사: 즉시 1회 → 1초 후 → 2초 후 (총 3회).
    #
    # start_csv_play_material_binding_test(
    #     [
    #         ("/World/MyMesh", "/World/Looks/MyMaterial", 0.0),
    #         ("/World/MyMesh2", "/World/Looks/MyMaterial2", 1.0),
    #         ("/World/MyMesh3", "/World/Looks/MyMaterial3", 2.0),
    #     ],
    # )
    #
    # 또는 개별 호출만 쓰려면 (스케줄러 없이 직접 — Play 스레드에서 1회만 권장):
    # apply_material_binding_to_prim("/World/MyMesh", "/World/Looks/MyMaterial")
    # -------------------------------------------------------------------------

    csv_total = max(float(b.time_sec) for b in all_ordered)
    wall_total_est = csv_total / sp
    resume_note = f" · 이어서 t≥{resume:.1f}s" if resume > 1e-9 else ""
    print(
        f"{_PRINT_PREFIX} ▶ 재생 시작 | CSV 전체 ~{csv_total:.1f}s | "
        f"배속 {sp:g}x → 실시간 ~{wall_total_est:.0f}s | "
        f"모드: CSV 시각 스케줄 · ATM/VTM 레인 병렬(동일 레인 직렬·대기)"
        f"{resume_note}",
        flush=True,
    )

    wall_off = max(0.0, float(wall_elapsed_offset or 0.0))
    play_epoch = begin_csv_play_timekeeping(
        csv_offset=resume,
        speed_scale=sp,
        wall_elapsed_offset=wall_off,
        screen=int(play_screen or 1),
    )
    stopped = False
    clear_csv_play_timeline_highlight(screen=int(play_screen or 1))
    lane_coordinator = _CsvPlaybackLaneCoordinator(play_screen=si)
    n_json = sum(1 for b in all_ordered if b.steps)
    t0_snap = time.monotonic() - wall_off
    _reset_csv_play_progress_snap(
        process_only=False,
        json_total=n_json,
        csv_total=csv_total,
        t0=t0_snap,
        speed_scale=sp,
        screen=si,
    )
    sess = csv_play_screen_session(si)
    with sess.progress_snap_lock:
        sess.progress_snap["csv_t_display"] = float(resume)
        sess.progress_snap["csv_time_offset"] = float(resume)
        sess.progress_snap["wall_elapsed_display"] = float(wall_off)
        sess.progress_snap["json_done"] = max(0, int(initial_json_done))
    sess.progress_stop.clear()
    ticker = threading.Thread(
        target=_csv_play_progress_ticker_loop,
        args=(si,),
        daemon=True,
        name=f"lam-sim-csv-play-progress-s{si}",
    )
    ticker.start()
    workers: List[threading.Thread] = []
    # JSON 은 레인당 1스레드(최대 3) — 블록마다 스레드 금지(토글 시 Kit freeze 원인).
    # dwell 은 단일 로그 스레드.
    dwell_items = [(i, b) for i, b in enumerate(ordered, 1) if not b.steps]
    atm_items, vtm_items, other_items = _partition_json_blocks_by_lane(ordered)
    n_json = len(atm_items) + len(vtm_items) + len(other_items)
    print(
        f"{_PRINT_PREFIX} 화면{si} 일반재생 worker | "
        f"레인 ATM {len(atm_items)} · VTM {len(vtm_items)} · 기타 {len(other_items)} "
        f"(JSON {n_json}) · dwell로그 1 (dwell {len(dwell_items)}건)",
        flush=True,
    )

    def _dwell_log_worker() -> None:
        for index, block in dwell_items:
            if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
                return
            if not _wait_until_csv_play_time(
                float(block.time_sec), screen=si, play_epoch=play_epoch
            ):
                return
            if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
                return
            sched = block.schedule
            if sched is None:
                continue
            wall_elapsed = get_csv_play_csv_time_now(screen=si)
            _print_csv_compact_line(
                index, sched, wall_elapsed_sec=wall_elapsed, executed=False
            )

    try:
        lane_kw = {
            "t0": t0_snap,
            "speed_scale": sp,
            "lane_coordinator": lane_coordinator,
            "usd_context_name": usd_ctx,
            "lam_window": lam_window,
            "play_screen": si,
            "play_epoch": play_epoch,
        }
        if dwell_items:
            workers.append(
                _spawn_csv_play_bound_thread(
                    si,
                    name=f"lam-csv-play-dwell-log-s{si}",
                    target=_dwell_log_worker,
                )
            )
        if atm_items:
            workers.append(
                _spawn_csv_play_bound_thread(
                    si,
                    name=f"lam-csv-play-atm-s{si}",
                    target=_csv_play_normal_lane_worker,
                    args=(atm_items, "atm", registry, scheduler),
                    kwargs=lane_kw,
                )
            )
        if vtm_items:
            workers.append(
                _spawn_csv_play_bound_thread(
                    si,
                    name=f"lam-csv-play-vtm-s{si}",
                    target=_csv_play_normal_lane_worker,
                    args=(vtm_items, "vtm", registry, scheduler),
                    kwargs=lane_kw,
                )
            )
        if other_items:
            workers.append(
                _spawn_csv_play_bound_thread(
                    si,
                    name=f"lam-csv-play-other-s{si}",
                    target=_csv_play_normal_lane_worker,
                    args=(other_items, None, registry, scheduler),
                    kwargs=lane_kw,
                )
            )
        for t in workers:
            if csv_play_worker_should_exit(screen=si, play_epoch=play_epoch):
                stopped = True
            t.start()

        _join_csv_play_workers(workers, screen=si)
        if csv_playback_stop_requested(screen=si):
            stopped = True
    finally:
        sess.progress_stop.set()
        csv_play_screen_session(si).material_test_stop.set()
        clear_csv_play_timeline_highlight(screen=si)
        end_csv_play_timekeeping(screen=si, play_epoch=play_epoch)
        try:
            ticker.join(timeout=2.0)
        except Exception:
            pass

    total_wall = get_csv_play_wall_elapsed()
    if stopped:
        print(
            f"{_PRINT_PREFIX} CSV Play 중지 | 실경과 {total_wall:.1f}s | "
            f"CSV t ~{min(csv_total, get_csv_play_csv_time_now()):.1f}s",
            flush=True,
        )
    else:
        print(
            f"{_PRINT_PREFIX} CSV Play 완료 | 실경과 {total_wall:.1f}s | CSV t ~{csv_total:.1f}s",
            flush=True,
        )


def preview_csv_playback_schedule(
    csv_path: Optional[str] = None,
    *,
    speed_scale: float = 1.0,
    use_cache: bool = True,
) -> str:
    """CSV 경로 → UI 표시용 타임라인 문자열 (캐시 hit 시 즉시, miss 시 메타만 빠르게)."""
    path = resolve_csv_path(csv_path)
    if use_cache:
        hit = get_cached_csv_playback(path)
        if hit is not None:
            return format_csv_playback_schedule(hit.schedule, speed_scale=speed_scale)
    dwells = load_csv_dwell_timeline(path)
    entries = build_csv_playback_schedule_meta(dwells)
    return format_csv_playback_schedule(entries, speed_scale=speed_scale)


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
    prepared: Optional[CachedCsvPlayback] = None,
    process_only: bool = False,
    resume_from_csv_sec: float = 0.0,
    initial_json_done: int = 0,
    reset_wafer_visibility: bool = True,
    wall_elapsed_offset: float = 0.0,
    skip_play_prim_hide: bool = False,
    paused_in_json: bool = False,
    play_screen: int = 1,
    kit_ext: Any = None,
) -> None:
    """CSV ``eqp_start_tm`` 동기 재생: 시각까지 대기 → 로그 → 이벤트 JSON (``speed_scale`` 배속).

    ``prepared`` 가 있으면 파싱·빌드를 생략한다 (캐시·UI 선행 빌드).
    ``process_only=True`` 이면 공정만보기(배속 1x, CSV 시각·레인 내 빈 구간만 생략).
    ``resume_from_csv_sec`` > 0 이면 해당 CSV 시각부터 이어서 재생.
    ``wall_elapsed_offset`` — 일시정지 이어서 재생 시 실경과 누적 [s].
    ``skip_play_prim_hide=True`` — play_start prim 숨김을 호출측에서 이미 수행한 경우.

    Note:
        UI 스레드 안전을 위해 **백그라운드 스레드**에서 호출할 것.
    """
    si = max(1, int(play_screen or current_csv_play_screen()))
    sp = 1.0 if process_only else float(max(0.01, speed_scale or 1.0))
    clear_csv_playback_stop(screen=si)
    clear_csv_play_timeline_highlight(screen=si)
    set_csv_playback_compact_log(True)
    try:
        from_cache = False
        if prepared is not None:
            cached = prepared
            from_cache = True
            path = prepared.path
        else:
            path = resolve_csv_path(csv_path)
            hit = get_cached_csv_playback(path)
            from_cache = hit is not None
            cached = hit if from_cache else build_and_cache_csv_playback(path)
        blocks = list(cached.blocks)
        action_blocks = [b for b in blocks if b.steps]
        if not action_blocks:
            print(
                f"{_PRINT_PREFIX} CSV Play: JSON 실행 없음 (event JSON·prim·Z 확인).",
                flush=True,
            )
            return

        if not skip_play_prim_hide and si <= 1:
            try:
                from .lam_csv_screen_runtime import (
                    sync_play_prim_hide_checkbox_after_play_start,
                )
                from .lam_play_prim_hide import apply_play_prim_hide_phase

                if apply_play_prim_hide_phase("play_start"):
                    sync_play_prim_hide_checkbox_after_play_start(screen=1)
            except Exception as exc:
                print(f"{_PRINT_PREFIX} play prim hide (play_start): {exc}", flush=True)

        n_act = len(action_blocks)
        n_all = len(blocks)
        src = "캐시" if from_cache else "빌드"
        print(
            f"{_PRINT_PREFIX} CSV 준비 완료 ({src}): {path.name} | "
            f"dwell {len(cached.dwells)} · 블록 {n_all} (JSON {n_act}) · {cached.build_ms:.0f}ms",
            flush=True,
        )
        resume = max(0.0, float(resume_from_csv_sec or 0.0))
        if process_only:
            if resume > 1e-9:
                print(
                    f"{_PRINT_PREFIX} Play — 공정만보기 이어서 재생 (t≥{resume:.1f}s)",
                    flush=True,
                )
            else:
                print(
                    f"{_PRINT_PREFIX} Play — 공정만보기 (배속 1x, CSV 시각·레인 빈 구간 생략)",
                    flush=True,
                )
        else:
            if resume > 1e-9:
                print(
                    f"{_PRINT_PREFIX} Play — 배속 {sp:g}x | CSV t≥{resume:.1f}s 이어서 재생",
                    flush=True,
                )
            else:
                print(
                    f"{_PRINT_PREFIX} Play — 배속 {sp:g}x | CSV t=0 기준 재생 시작",
                    flush=True,
                )
        run_csv_timed_playback(
            registry,
            scheduler,
            blocks,
            speed_scale=sp,
            process_only=process_only,
            resume_from_csv_sec=resume,
            initial_json_done=int(initial_json_done or 0),
            reset_wafer_visibility=bool(reset_wafer_visibility),
            wall_elapsed_offset=max(0.0, float(wall_elapsed_offset or 0.0)),
            paused_in_json=bool(paused_in_json),
            play_screen=si,
            kit_ext=kit_ext,
            dwells=list(cached.dwells) if cached.dwells else None,
        )
    finally:
        set_csv_playback_compact_log(False)


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


def csv_play_hide_ui_below_timeline() -> bool:
    """CSV 재생창 — 타임라인 ScrollingFrame 아래 UI(매크로·이벤트 함수·로그) 숨김."""
    try:
        from .lam_sim_control_defaults import CSV_PLAY_HIDE_UI_BELOW_TIMELINE

        return bool(CSV_PLAY_HIDE_UI_BELOW_TIMELINE)
    except Exception:
        return True


class LamSimulationCsvPlayWindow:
    """Kit ``omni.ui`` 창: CSV dwell 재생 + 매크로 스크립트 + **초기화**.

    | 버튼 | 코드 |
    |------|------|
    | CSV Play | ``run_simulation_from_csv`` |
    | 스크립트 실행 | ``run_lam_sim_script_text`` |
    | 초기화 | ``reset_lam_sim_to_initial_state`` (Z prim TBS→0) |
    """

    WINDOW_TITLE = "LAM CSV 시뮬 재생"

    def __init__(self, registry: Any, scheduler: Any, *, screen: int = 1) -> None:
        """Args: ``registry`` / ``scheduler`` 는 ``run_simulation_from_csv`` · ``run_lam_sim_steps`` 에 전달."""
        self._screen = max(1, int(screen))
        self._registry = registry
        self._scheduler = scheduler
        self._window: Any = None
        self._combo: Any = None
        self._csv_dir_model: Any = None
        self._csv_file_stack: Any = None
        self._csv_paths: List[Path] = []
        self._log_label: Any = None
        self._script_model: Any = None
        self._func_combo: Any = None
        self._schedule_model: Any = None
        self._schedule_rows_stack: Any = None
        self._schedule_scroll_frame: Any = None
        self._schedule_row_labels: List[Any] = []
        self._schedule_row_entries: List[CsvPlaybackScheduleEntry] = []
        self._occupancy_diag_stack: Any = None
        self._occupancy_diag_scroll: Any = None
        self._occupancy_diag_labels: List[Any] = []
        self._occupancy_diag_lines: Tuple[str, ...] = ()
        self._schedule_highlight_keys: frozenset = frozenset()
        self._build_progress_model: Any = None
        self._speed_model: Any = None
        self._process_only_model: Any = None
        self._process_only_model_live_wired: bool = False
        self._process_only_model_syncing: bool = False
        self._process_only_switch_pending: bool = False
        self._process_only_switch_target: bool = False
        self._process_only_switch_lock = threading.Lock()
        self._process_only_switch_gen: int = 0
        self._process_only_last_applied: Optional[bool] = None
        self._process_only_deferred_for_start: Optional[bool] = None
        self._csv_prerun_export_model: Any = None
        self._wafer_label_show_model: Any = None
        self._foup_status_show_model: Any = None
        self._device_labels_show_model: Any = None
        self._floorplan_show_model: Any = None
        self._pick_whitelist_model: Any = None
        self._play_prim_hide_model: Any = None
        self._play_camera_fly_model: Any = None
        self._top_view_model: Any = None
        self._overlay_checkbox_syncing: bool = False
        self._overlay_checkbox_initialized: bool = False
        self._screen_local_overlay_hooks_installed: bool = False
        self._overlay_apply_pending: bool = False
        # (removed) overlay toggle polling fields
        self._lam_window_ref: Any = None
        self._hud_schedule_rows_stack: Any = None
        self._hud_schedule_scroll_frame: Any = None
        self._hud_schedule_row_labels: List[Any] = []
        self._hud_build_progress_model: Any = None
        self._csv_play_thread: Optional[threading.Thread] = None
        self._floorplan_ui: Optional[Dict[str, Any]] = None
        self._csv_play_launch_lock = threading.Lock()
        self._process_only_schedule_token: int = 0
        self._process_only_last_live_desired: Optional[bool] = None
        self._csv_build_thread: Optional[threading.Thread] = None
        self._prepared_playback: Optional[CachedCsvPlayback] = None
        self._build_ui_ticker: Optional[_SecondsIntervalProgress] = None
        # Viewport HUD 등 ``ui.Window`` 없이 Play API 만 쓸 때 선택 인덱스.
        self._csv_selected_index: int = 0

    @property
    def screen(self) -> int:
        return int(self._screen)

    def window_title(self) -> str:
        if self._screen <= 1:
            return self.WINDOW_TITLE
        return f"{self.WINDOW_TITLE} — 화면{self._screen}"

    def destroy(self) -> None:
        """윈도우·콤보·로그 위젯 참조를 해제한다 (``lam_window`` 종료 시 호출)."""
        si = int(self._screen)
        clear_csv_play_pause_checkpoint(screen=si)
        request_stop_csv_playback(
            self._registry,
            self._scheduler,
            screen=si,
            kit_ext=getattr(self._lam_window_ref, "_kit_ext", None),
        )
        t = self._csv_play_thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout=2.0)
            except Exception:
                pass
        self._csv_play_thread = None
        bt = self._csv_build_thread
        if bt is not None and bt.is_alive():
            try:
                bt.join(timeout=1.0)
            except Exception:
                pass
        self._csv_build_thread = None
        self._prepared_playback = None
        if self._build_ui_ticker is not None:
            try:
                self._build_ui_ticker.stop()
            except Exception:
                pass
        set_csv_play_progress_ui_callback(None, screen=si)
        set_csv_play_timeline_highlight_callback(None, screen=si)
        unregister_csv_play_timeline_window(self._screen)
        clear_csv_play_timeline_highlight(screen=si)
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass
        self._window = None
        self._combo = None
        self._csv_dir_model = None
        self._csv_file_stack = None
        self._log_label = None
        self._script_model = None
        self._func_combo = None
        self._schedule_model = None
        self._schedule_rows_stack = None
        self._schedule_row_labels = []
        self._schedule_row_entries = []
        self._schedule_highlight_keys = frozenset()
        self._schedule_scroll_frame = None
        self._hud_schedule_scroll_frame = None
        self._build_progress_model = None
        self._speed_model = None
        self._process_only_model = None
        self._process_only_model_live_wired = False
        self._process_only_model_syncing = False
        self._process_only_switch_pending = False
        self._process_only_switch_target = False
        self._process_only_deferred_for_start = None
        self._csv_prerun_export_model = None
        self._wafer_label_show_model = None
        self._lam_window_ref = None
        self._hud_schedule_rows_stack = None
        self._hud_schedule_row_labels = []
        self._hud_build_progress_model = None
        self._prepared_playback = None
        try:
            from .lam_equipment_floorplan_ui import unbind_floorplan_occupancy_ui

            unbind_floorplan_occupancy_ui(self._floorplan_ui)
        except Exception:
            pass
        self._floorplan_ui = None

    def _log(self, msg: str) -> None:
        print(f"{_PRINT_PREFIX} {msg}", flush=True)
        try:
            if self._log_label is not None:
                self._log_label.text = msg
        except Exception:
            pass

    def _uses_global_overlay_models(self) -> bool:
        return int(self._screen) <= 1

    def _read_bool_model(self, m: Any) -> bool:
        if m is None:
            return False
        try:
            return bool(m.get_value_as_bool())
        except Exception:
            pass
        try:
            return bool(m.as_bool)
        except Exception:
            pass
        try:
            return bool(m.get_value())
        except Exception:
            return False

    def _request_screen_3d_overlay_sync(self) -> None:
        """화면2+ 표시 체크(FOUP·기기·웨이퍼) — 3D 오버레이만, 카메라/줌 유지."""
        if self._overlay_checkbox_syncing:
            return
        if self._screen <= 1:
            return
        lam = self._lam_window_ref
        if lam is None:
            return
        try:
            from .lam_csv_screen_runtime import sync_csv_screen_overlays

            sync_csv_screen_overlays(lam, self._screen)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} screen{self._screen} 3d overlay sync: {exc}",
                flush=True,
            )

    def _request_screen_viewport_effects_sync(self) -> None:
        """화면2+ 탑뷰·prim숨김 — 상태 전환 시에만 카메라/visibility."""
        if self._overlay_checkbox_syncing:
            return
        if bool(getattr(self, "_play_stop_reset_in_progress", False)):
            return
        if self._screen <= 1:
            return
        lam = self._lam_window_ref
        if lam is None:
            return
        try:
            from .lam_csv_screen_runtime import sync_csv_screen_viewport_effects

            sync_csv_screen_viewport_effects(lam, self._screen)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} screen{self._screen} viewport effects sync: {exc}",
                flush=True,
            )

    def _request_screen_overlay_sync(self) -> None:
        """화면2+ 기동/레거시 — 3D 표시 + viewport 효과 (각각 분리 sync)."""
        self._request_screen_3d_overlay_sync()
        self._request_screen_viewport_effects_sync()

    def sync_play_prim_hide_checkbox_ui(self, enabled: bool) -> None:
        """Play 시작 자동 숨김 등 — 체크박스만 갱신 (visibility 재적용 없음)."""
        self.ensure_playback_models()
        m = self._play_prim_hide_model
        if m is None:
            return
        self._overlay_checkbox_syncing = True
        try:
            m.set_value(bool(enabled))
        except Exception:
            pass
        finally:
            self._overlay_checkbox_syncing = False

    def _install_screen_local_overlay_hooks(self) -> None:
        if self._uses_global_overlay_models() or self._screen_local_overlay_hooks_installed:
            return

        def _on_display_changed(*_a: Any) -> None:
            self._request_screen_3d_overlay_sync()

        def _on_viewport_effect_changed(*_a: Any) -> None:
            self._request_screen_viewport_effects_sync()

        display_models = (
            self._foup_status_show_model,
            self._device_labels_show_model,
            self._floorplan_show_model,
            self._wafer_label_show_model,
        )
        viewport_models = (
            self._play_prim_hide_model,
            self._top_view_model,
        )

        hooked = 0
        for m in display_models:
            if m is None:
                continue
            for hook in ("add_value_changed_fn", "add_item_changed_fn"):
                try:
                    fn = getattr(m, hook, None)
                    if callable(fn):
                        fn(_on_display_changed)
                        hooked += 1
                        break
                except Exception:
                    pass
        for m in viewport_models:
            if m is None:
                continue
            for hook in ("add_value_changed_fn", "add_item_changed_fn"):
                try:
                    fn = getattr(m, hook, None)
                    if callable(fn):
                        fn(_on_viewport_effect_changed)
                        hooked += 1
                        break
                except Exception:
                    pass
        self._screen_local_overlay_hooks_installed = True
        print(
            f"{_PRINT_PREFIX} screen{self._screen} local overlay hooks={hooked}",
            flush=True,
        )
        # 기동 시 기본 체크 ON 상태도 즉시 반영 (표시 + 탑뷰/prim)
        self._request_screen_overlay_sync()

    def _aux_display_checkbox_changed_fn(self) -> Any:
        """화면2+ FOUP·기기 CheckBox — 3D 표시만."""

        def _fn(*_a: Any) -> None:
            self._request_screen_3d_overlay_sync()

        return _fn

    def _aux_viewport_effect_checkbox_changed_fn(self) -> Any:
        """화면2+ 탑뷰·prim숨김 CheckBox — 카메라/visibility 전환 시에만."""

        def _fn(*_a: Any) -> None:
            self._request_screen_viewport_effects_sync()

        return _fn

    def _aux_checkbox_changed_fn(self) -> Any:
        """레거시 alias — 3D 표시 sync."""
        return self._aux_display_checkbox_changed_fn()

    def _run_aux_screen_play_preflight(self, kit_ext: Any) -> bool:
        """화면2+ Play 직전 — ``lam_csv_screen_runtime`` (화면1 전역 API 미사용)."""
        if self._screen <= 1:
            return True
        lam = self._lam_window_ref
        if lam is None:
            return False
        try:
            from .lam_csv_screen_runtime import (
                resolve_csv_screen_runtime,
                run_csv_screen_play_preflight,
            )

            runtime = resolve_csv_screen_runtime(
                lam,
                self._screen,
                csv_window=self,
                require_aux=True,
            )
            if runtime is None:
                return False
            return bool(run_csv_screen_play_preflight(runtime))
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} screen{self._screen} play preflight: {exc}",
                flush=True,
            )
            return False

    def _refresh_play_runtime(self) -> bool:
        """Play 직전 화면별 registry/scheduler·split runtime 재조회."""
        lam = self._lam_window_ref
        if lam is None:
            return self._screen <= 1
        try:
            from .lam_csv_play_execution import resolve_csv_play_execution

            binding = resolve_csv_play_execution(
                lam,
                self._screen,
                kit_ext=getattr(lam, "_kit_ext", None),
                require_aux=(self._screen > 1),
            )
            if binding is None:
                if self._screen > 1:
                    print(
                        f"{_PRINT_PREFIX} 화면{self._screen} runtime 없음 — "
                        "분할 viewport·USD hydrate 후 다시 시도",
                        flush=True,
                    )
                return self._screen <= 1
            self._registry = binding.registry
            self._scheduler = binding.scheduler
            return True
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} refresh play runtime screen={self._screen}: {exc}",
                flush=True,
            )
            return self._screen <= 1

    def ensure_playback_models(self) -> None:
        """Viewport CSV HUD 가 전용 ``ui.Window`` 없이 Play API 를 쓸 때 모델·목록만 준비."""
        try:
            from omni.ui import (  # type: ignore
                SimpleBoolModel,
                SimpleFloatModel,
                SimpleStringModel,
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} ensure_playback_models: omni.ui — {exc}", flush=True)
            return
        if self._csv_dir_model is None:
            self._csv_dir_model = SimpleStringModel(str(get_lam_csv_dir()))
            self._reload_csv_list(preserve_selection=False)
        if self._speed_model is None:
            self._speed_model = SimpleFloatModel(1.0)
        self._wire_speed_model_live_update()
        try:
            from .lam_viewport_overlay_config import (  # type: ignore
                STARTUP_CHECK_PROCESS_ONLY,
                STARTUP_CHECK_WAFER_LABELS,
            )
        except Exception:
            STARTUP_CHECK_PROCESS_ONLY = False  # type: ignore
            STARTUP_CHECK_WAFER_LABELS = False  # type: ignore
        if self._process_only_model is None:
            self._process_only_model = SimpleBoolModel(bool(STARTUP_CHECK_PROCESS_ONLY))
        self._wire_process_only_model_live_update()
        if self._wafer_label_show_model is None:
            self._wafer_label_show_model = SimpleBoolModel(bool(STARTUP_CHECK_WAFER_LABELS))
            try:
                from .lam_wafer_prim_paths import IS_LABEL_SHOW  # type: ignore
                from .lam_wafer_viewport_labels import (  # type: ignore
                    set_wafer_labels_ui_enabled,
                )

                set_wafer_labels_ui_enabled(
                    bool(IS_LABEL_SHOW) and bool(STARTUP_CHECK_WAFER_LABELS),
                    screen=max(1, int(self._screen)),
                )
            except Exception:
                pass
        # 화면1·HUD 는 전역 모델 공유. 화면2+ 는 창별 로컬 모델(화면1에 반영되지 않음).
        use_global = self._uses_global_overlay_models()
        if self._foup_status_show_model is None:
            if use_global:
                try:
                    from .lam_viewport_overlay_state import get_ui_model_foup_status

                    self._foup_status_show_model = get_ui_model_foup_status()
                except Exception:
                    use_global = False
            if not use_global:
                try:
                    from .lam_viewport_overlay_config import STARTUP_CHECK_FOUP_STATUS  # type: ignore

                    _foup_def = bool(STARTUP_CHECK_FOUP_STATUS)
                except Exception:
                    _foup_def = True
                self._foup_status_show_model = SimpleBoolModel(_foup_def)
        if self._device_labels_show_model is None:
            if use_global:
                try:
                    from .lam_viewport_overlay_state import get_ui_model_device_labels

                    self._device_labels_show_model = get_ui_model_device_labels()
                except Exception:
                    use_global = False
            if not use_global:
                try:
                    from .lam_viewport_overlay_config import STARTUP_CHECK_DEVICE_LABELS  # type: ignore

                    _dev_def = bool(STARTUP_CHECK_DEVICE_LABELS)
                except Exception:
                    _dev_def = True
                self._device_labels_show_model = SimpleBoolModel(_dev_def)
        if self._floorplan_show_model is None:
            if use_global:
                try:
                    from .lam_viewport_overlay_state import get_ui_model_floorplan_panel

                    self._floorplan_show_model = get_ui_model_floorplan_panel()
                except Exception:
                    use_global = False
            if self._floorplan_show_model is None:
                try:
                    from .lam_sim_control_defaults import (  # type: ignore
                        STARTUP_CHECK_FLOORPLAN_PANEL,
                    )

                    _fp_def = bool(STARTUP_CHECK_FLOORPLAN_PANEL)
                except Exception:
                    _fp_def = False
                self._floorplan_show_model = SimpleBoolModel(_fp_def)
        if self._pick_whitelist_model is None:
            if use_global:
                try:
                    from .lam_viewport_overlay_state import get_ui_model_pick_whitelist

                    self._pick_whitelist_model = get_ui_model_pick_whitelist()
                except Exception:
                    use_global = False
            if not use_global:
                try:
                    from .lam_viewport_overlay_config import STARTUP_CHECK_PICK_WHITELIST  # type: ignore

                    _pick_def = bool(STARTUP_CHECK_PICK_WHITELIST)
                except Exception:
                    _pick_def = False
                self._pick_whitelist_model = SimpleBoolModel(_pick_def)
        if self._play_prim_hide_model is None:
            if use_global:
                try:
                    from .lam_viewport_overlay_state import get_ui_model_play_prim_hide

                    self._play_prim_hide_model = get_ui_model_play_prim_hide()
                except Exception:
                    use_global = False
            if not use_global:
                try:
                    from .lam_viewport_overlay_config import (  # type: ignore
                        STARTUP_CHECK_PLAY_PRIM_HIDE,
                    )

                    _ph_def = bool(STARTUP_CHECK_PLAY_PRIM_HIDE)
                except Exception:
                    _ph_def = False
                self._play_prim_hide_model = SimpleBoolModel(_ph_def)
        if self._play_camera_fly_model is None:
            if use_global:
                try:
                    from .lam_viewport_overlay_state import get_ui_model_play_camera_fly

                    self._play_camera_fly_model = get_ui_model_play_camera_fly()
                except Exception:
                    use_global = False
            if not use_global:
                try:
                    from .lam_viewport_overlay_config import (  # type: ignore
                        STARTUP_CHECK_PLAY_CAMERA_FLY,
                    )

                    _cf_def = bool(STARTUP_CHECK_PLAY_CAMERA_FLY)
                except Exception:
                    _cf_def = False
                self._play_camera_fly_model = SimpleBoolModel(_cf_def)
        if self._top_view_model is None:
            if use_global:
                try:
                    from .lam_viewport_overlay_state import get_ui_model_top_view

                    self._top_view_model = get_ui_model_top_view()
                except Exception:
                    use_global = False
            if not use_global:
                try:
                    from .lam_viewport_overlay_config import (  # type: ignore
                        STARTUP_CHECK_TOP_VIEW,
                    )

                    _tv_def = bool(STARTUP_CHECK_TOP_VIEW)
                except Exception:
                    _tv_def = False
                self._top_view_model = SimpleBoolModel(_tv_def)
        if not self._uses_global_overlay_models():
            self._install_screen_local_overlay_hooks()
        register_csv_play_timeline_window(self, screen=self._screen)

    # NOTE: overlay 토글은 changed_fn/모델 이벤트로만 동기화한다.
    # 폴링 기반 동기화는 상태가 왕복하면서 3D 패널이 깜박이거나 겹침을 유발할 수 있어 제거.

    def set_lam_window(self, lam_window: Any) -> None:
        """Viewport 라벨 동기용 ``LamWindow`` 참조 (본창·HUD 체크박스)."""
        self._lam_window_ref = lam_window

    def mount_screen_visibility_checkboxes_ui(self, ui: Any) -> None:
        """화면1·2 CSV 재생창 공통 화면 표시 체크박스."""
        lam = self._lam_window_ref
        ext = getattr(lam, "_kit_ext", None) if lam is not None else None
        if ext is None:
            return
        from .lam_screen_visibility import mount_screen_visibility_checkboxes

        mount_screen_visibility_checkboxes(ext, ui, row_height=22)

    def _resolve_lam_window(self, lam_window: Any = None) -> Any:
        return lam_window if lam_window is not None else self._lam_window_ref

    def mount_wafer_label_show_checkbox_ui(
        self,
        ui: Any,
        *,
        lam_window: Any = None,
        label_width: int = 88,
        row_height: int = 22,
        wrap_row: bool = True,
    ) -> None:
        """「웨이퍼번호보기」 체크박스 (Viewport HUD · CSV 본창 공통).

        ``wrap_row=False`` 이면 Label+CheckBox 만 그린다 (호출측 HStack 에 나란히 배치).
        """
        self.ensure_playback_models()
        wl_m = self._wafer_label_show_model
        if wl_m is None:
            return
        try:
            from .lam_wafer_prim_paths import IS_LABEL_SHOW
        except Exception:
            IS_LABEL_SHOW = True  # type: ignore
        tip = (
            "3D 뷰포트 웨이퍼 슬롯 번호(01~25). pick/place hide·show 와 함께 이동."
        )
        if not IS_LABEL_SHOW:
            tip += " (코드 IS_LABEL_SHOW=False 이면 번호는 표시되지 않음.)"
        lam = self._resolve_lam_window(lam_window)

        def _on_changed(*_a: Any) -> None:
            self.apply_wafer_label_visibility_from_ui(lam_window=lam)
            if not self._uses_global_overlay_models():
                self._request_screen_3d_overlay_sync()

        def _build() -> None:
            ui.Label("웨이퍼번호보기", width=int(label_width), height=int(row_height))
            cb_kw: Dict[str, Any] = {
                "model": wl_m,
                "width": 20,
                "height": int(row_height),
                "tooltip": tip,
            }
            if not self._uses_global_overlay_models():
                cb_kw["changed_fn"] = _on_changed
            ui.CheckBox(**cb_kw)
            for hook in ("add_value_changed_fn", "add_item_changed_fn"):
                try:
                    fn = getattr(wl_m, hook, None)
                    if callable(fn):
                        fn(_on_changed)
                        break
                except Exception:
                    pass
            if not self._uses_global_overlay_models():
                self._install_screen_local_overlay_hooks()

        if wrap_row:
            with ui.HStack(spacing=4, height=int(row_height)):
                _build()
                ui.Spacer()
        else:
            _build()

    def mount_overlay_feature_checkboxes_ui(
        self,
        ui: Any,
        *,
        label_width: int = 88,
        row_height: int = 22,
        spacing: int = 8,
    ) -> None:
        """추가 상태표시 기능 체크박스 (시뮬 재생창·HUD 공통)."""
        self.ensure_playback_models()
        try:
            from .lam_viewport_overlay_state import (
                set_toggle_device_labels,
                set_toggle_foup_status,
                set_toggle_pick_whitelist,
                ui_models_are_syncing,
            )
        except Exception:
            return

        f_m = self._foup_status_show_model
        d_m = self._device_labels_show_model
        fp_m = self._floorplan_show_model
        p_m = self._pick_whitelist_model
        if f_m is None or d_m is None or p_m is None:
            return

        try:
            from .lam_viewport_floorplan_panel import (
                viewport_floorplan_panel_feature_enabled,
            )

            show_fp = bool(viewport_floorplan_panel_feature_enabled())
        except Exception:
            show_fp = True
        if show_fp and fp_m is None:
            return

        # 전역 모델을 공유하므로 여기서 상태→모델 set_value를 반복 수행하지 않는다.
        # (필요 시 overlay_state가 set_toggle_*에서 모델을 동기화한다.)
        self._overlay_checkbox_initialized = True

        aux_display_fn = (
            None
            if self._uses_global_overlay_models()
            else self._aux_display_checkbox_changed_fn()
        )

        with ui.HStack(spacing=int(spacing), height=int(row_height)):
            ui.Label("FOUP상태보기", width=int(label_width), height=int(row_height))
            if aux_display_fn is not None:
                ui.CheckBox(
                    model=f_m,
                    width=20,
                    height=int(row_height),
                    changed_fn=aux_display_fn,
                )
            else:
                # 화면1: 토글 SSOT 는 lam_viewport_overlay_state 전역 모델 훅
                ui.CheckBox(model=f_m, width=20, height=int(row_height))
            ui.Label("기기정보보기", width=int(label_width), height=int(row_height))
            if aux_display_fn is not None:
                ui.CheckBox(
                    model=d_m,
                    width=20,
                    height=int(row_height),
                    changed_fn=aux_display_fn,
                )
            else:
                ui.CheckBox(model=d_m, width=20, height=int(row_height))
            ui.Label("선택제한", width=int(label_width), height=int(row_height))
            ui.CheckBox(
                model=p_m,
                width=20,
                height=int(row_height),
                tooltip="Viewport 클릭 선택을 whitelist 루트로 제한",
            )
            ui.Spacer()

        if show_fp and fp_m is not None:
            with ui.HStack(spacing=int(spacing), height=int(row_height)):
                ui.Label(
                    "장비배치도",
                    width=int(label_width),
                    height=int(row_height),
                    tooltip="Viewport 우하단 장비배치도 패널 (시뮬 창 배치도와 동일 occupancy)",
                )
                if aux_display_fn is not None:
                    ui.CheckBox(
                        model=fp_m,
                        width=20,
                        height=int(row_height),
                        changed_fn=aux_display_fn,
                    )
                else:
                    ui.CheckBox(model=fp_m, width=20, height=int(row_height))
                ui.Spacer()

        if aux_display_fn is not None:
            self._install_screen_local_overlay_hooks()
            self._request_screen_3d_overlay_sync()

    def mount_play_camera_fly_checkbox_ui(
        self,
        ui: Any,
        *,
        label_width: int = 52,
        row_height: int = 22,
        spacing: int = 4,
    ) -> None:
        """「Play시점이동」 — preset 뷰로 fly 후 재생 (일시정지 이어서 제외)."""
        self.ensure_playback_models()
        m = self._play_camera_fly_model
        if m is None:
            return
        with ui.HStack(spacing=int(spacing), height=int(row_height)):
            ui.Label("Play시점", width=int(label_width), height=int(row_height))
            ui.CheckBox(
                model=m,
                width=20,
                height=int(row_height),
                tooltip=(
                    "체크 후 Play: 설정 뷰로 부드럽게 이동한 뒤 prim숨김·재생. "
                    "USE_PRESET_COORDS=True: PLAY_CAMERA_PRESET / "
                    "False: PLAY_CAMERA_PRIM_PATH Camera prim"
                ),
            )
            ui.Spacer()

    def mount_top_view_checkbox_ui(
        self,
        ui: Any,
        *,
        label_width: int = 52,
        row_height: int = 22,
        spacing: int = 4,
    ) -> None:
        """「탑뷰 보기」 — preset 뷰 고정 + 카메라 조작 잠금."""
        self.ensure_playback_models()
        m = self._top_view_model
        if m is None:
            return
        aux_vp_fn = (
            None
            if self._uses_global_overlay_models()
            else self._aux_viewport_effect_checkbox_changed_fn()
        )
        with ui.HStack(spacing=int(spacing), height=int(row_height)):
            ui.Label("탑뷰 보기", width=int(label_width), height=int(row_height))
            cb_kw: Dict[str, Any] = {
                "model": m,
                "width": 20,
                "height": int(row_height),
                "tooltip": (
                    "체크 ON: 탑뷰 시점으로 이동 후 pan/zoom/tumble 등 카메라 조작 잠금. "
                    "USE_PRESET_COORDS=True: TOP_VIEW_PRESET / "
                    "False: TOP_VIEW_CAMERA_PRIM_PATH Camera prim. "
                    "체크 OFF(camera 모드): Perspective 복귀"
                ),
            }
            if aux_vp_fn is not None:
                cb_kw["changed_fn"] = aux_vp_fn
            ui.CheckBox(**cb_kw)
            ui.Spacer()

    def mount_play_camera_capture_button_ui(
        self,
        ui: Any,
        *,
        width: int = 52,
        height: int = 22,
    ) -> None:
        """현재 뷰포트 시점 캡처 → 콘솔에 config 붙여넣기 블록 출력."""
        self.ensure_playback_models()

        def _on_click() -> None:
            try:
                from .lam_play_camera_fly import log_play_camera_preset_capture

                log_play_camera_preset_capture()
            except Exception as exc:
                self._log(f"뷰 저장 실패: {exc}")

        ui.Button(
            "뷰저장",
            width=int(width),
            height=int(height),
            clicked_fn=_on_click,
            tooltip="현재 뷰 eye/target → 콘솔 로그 (config에 붙여넣기)",
        )

    def mount_play_prim_hide_checkbox_ui(
        self,
        ui: Any,
        *,
        label_width: int = 52,
        row_height: int = 22,
        spacing: int = 4,
    ) -> None:
        """「prim숨김」 체크박스 (체크 ON=숨김). 시뮬 재생창·HUD 공통."""
        self.ensure_playback_models()
        m = self._play_prim_hide_model
        if m is None:
            return
        aux_vp_fn = (
            None
            if self._uses_global_overlay_models()
            else self._aux_viewport_effect_checkbox_changed_fn()
        )
        with ui.HStack(spacing=int(spacing), height=int(row_height)):
            ui.Label("prim숨김", width=int(label_width), height=int(row_height))
            cb_kw: Dict[str, Any] = {
                "model": m,
                "width": 20,
                "height": int(row_height),
                "tooltip": "체크 시 PLAY_HIDE_PRIM_SPECS 경로 prim 숨김 (설정: lam_viewport_overlay_config)",
            }
            if aux_vp_fn is not None:
                cb_kw["changed_fn"] = aux_vp_fn
            ui.CheckBox(**cb_kw)
            ui.Spacer()

    def read_wafer_label_show_enabled(self) -> bool:
        """「웨이퍼번호보기」 체크박스 (SimpleBoolModel 호환)."""
        m = self._wafer_label_show_model
        if m is None:
            return False
        try:
            return bool(m.get_value_as_bool())
        except Exception:
            pass
        try:
            return bool(m.as_bool)
        except Exception:
            pass
        try:
            return bool(m.get_value())
        except Exception:
            return False

    def apply_wafer_label_visibility_from_ui(self, lam_window: Any = None) -> None:
        """HUD/본창 「웨이퍼번호보기」 — 해당 화면 3D 라벨만 on/off (다른 화면 유지)."""
        si = max(1, int(self._screen))
        lam = self._resolve_lam_window(lam_window)
        try:
            from .lam_wafer_prim_paths import IS_LABEL_SHOW
            from .lam_wafer_viewport_labels import (
                get_wafer_label_tracker,
                notify_wafer_label_tracker_changed,
                set_wafer_labels_ui_enabled,
                teardown_wafer_viewport_labels,
                wafer_viewport_labels_enabled,
            )

            ui_on = self.read_wafer_label_show_enabled()
            set_wafer_labels_ui_enabled(bool(IS_LABEL_SHOW) and ui_on, screen=si)

            if not wafer_viewport_labels_enabled(si):
                try:
                    teardown_wafer_viewport_labels(screen=si)
                except Exception:
                    pass
                if lam is not None:
                    by = getattr(lam, "_wafer_foup_labels_by_screen", None)
                    if isinstance(by, dict):
                        by.pop(si, None)
                    if si <= 1:
                        try:
                            lam._wafer_foup_labels = None
                        except Exception:
                            pass
                return

            # ON: tracker 가 비어 있으면 FOUP baseline 복구 (Play 중 UI OFF 로 clear 된 경우)
            try:
                tracker = get_wafer_label_tracker(si)
                if tracker.mapped_prim_count() <= 0:
                    stage = None
                    if lam is not None:
                        try:
                            from .lam_csv_play_screen import get_stage_for_screen

                            ext = getattr(lam, "_kit_ext", None)
                            stage = get_stage_for_screen(ext, si) if ext is not None else None
                        except Exception:
                            stage = None
                    if stage is None and si <= 1:
                        try:
                            from .lam_prim_utils import get_stage

                            stage = get_stage()
                        except Exception:
                            stage = None
                    if stage is not None:
                        wm = load_wafer_prim_by_slot_key()
                        if wm:
                            tracker.reset_foup_baseline(wm, stage=stage)
            except Exception as exc:
                self._log(f"wafer label baseline restore failed (화면{si}): {exc}")

            if lam is not None:
                try:
                    if hasattr(lam, "sync_csv_viewport_overlays_for_screen"):
                        lam.sync_csv_viewport_overlays_for_screen(si)
                    elif si <= 1 and hasattr(lam, "_sync_wafer_foup_viewport_labels_only"):
                        lam._sync_wafer_foup_viewport_labels_only()
                except Exception as exc:
                    self._log(f"wafer label viewport sync failed (화면{si}): {exc}")
                    return
            try:
                notify_wafer_label_tracker_changed(si)
            except Exception:
                pass
        except Exception as exc:
            self._log(f"wafer label UI sync failed (화면{si}): {exc}")
            return

    def apply_web_live_controls(
        self,
        *,
        proc_only: Optional[bool] = None,
        top_view: Optional[bool] = None,
        foup_info_show: Optional[bool] = None,
        eqp_info_show: Optional[bool] = None,
        wafer_number_show: Optional[bool] = None,
        prim_hide: Optional[bool] = None,
        speed: Optional[float] = None,
    ) -> Dict[str, Any]:
        """웹 T2V_control_simulation — 전달된 항목만 이 화면에 실시간 반영.

        각 omni.ui 모델 ``set_value`` 가 화면별 훅을 유발한다
        (화면2+ 표시=3D overlay / 탑뷰·prim=viewport effects). ``None`` 은 유지.
        """
        self.ensure_playback_models()
        applied: Dict[str, Any] = {}

        if foup_info_show is not None and self._foup_status_show_model is not None:
            self._foup_status_show_model.set_value(bool(foup_info_show))
            applied["foup_info_show"] = bool(foup_info_show)

        if eqp_info_show is not None and self._device_labels_show_model is not None:
            self._device_labels_show_model.set_value(bool(eqp_info_show))
            applied["eqp_info_show"] = bool(eqp_info_show)

        if wafer_number_show is not None and self._wafer_label_show_model is not None:
            self._wafer_label_show_model.set_value(bool(wafer_number_show))
            try:
                self.apply_wafer_label_visibility_from_ui()
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} web control wafer(화면{self._screen}): {exc}",
                    flush=True,
                )
            applied["wafer_number_show"] = bool(wafer_number_show)

        if top_view is not None and self._top_view_model is not None:
            self._top_view_model.set_value(bool(top_view))
            applied["show_top_view"] = bool(top_view)

        if prim_hide is not None and self._play_prim_hide_model is not None:
            self._play_prim_hide_model.set_value(bool(prim_hide))
            applied["prim_hide"] = bool(prim_hide)

        # 배속은 공정만보기 전환 이전에 적용 (proc_only ON 시 1x 강제 정책 존중).
        # 시작 전·파싱 중에도 UI에 저장 → Federation 자동재생이 UI 값을 읽는다.
        if speed is not None and self._speed_model is not None:
            sp = float(max(0.1, min(20.0, float(speed))))
            self._speed_model.set_value(sp)
            applied["speed"] = sp
            try:
                self._apply_speed_ui_change()
            except Exception:
                pass

        # proc_only 는 pause/resume 을 유발하므로 다른 설정 반영 후 마지막에 전환.
        if proc_only is not None and self._process_only_model is not None:
            self._process_only_model.set_value(bool(proc_only))
            applied["proc_only"] = bool(proc_only)

        return applied

    def register_hud_timeline_ui(
        self,
        rows_stack: Any,
        *,
        build_progress_model: Any = None,
        scroll_frame: Any = None,
    ) -> None:
        """Viewport 2D 패널 타임라인·진행 표시 (본창 스택은 건드리지 않음)."""
        self._hud_schedule_rows_stack = rows_stack
        self._hud_schedule_scroll_frame = scroll_frame
        self._hud_schedule_row_labels = []
        self._hud_build_progress_model = build_progress_model
        if rows_stack is None:
            self._hud_schedule_scroll_frame = None
            return
        path = self._selected_csv_path()
        if path is None:
            self._rebuild_schedule_timeline_rows([], rebuild_main=False, rebuild_hud=True)
            return
        hit = get_cached_csv_playback(path)
        if hit is None:
            hit = find_cached_csv_playback(path)
        if hit is not None:
            self._prepared_playback = hit
            self._rebuild_schedule_timeline_rows(
                hit.schedule,
                speed_scale=self._read_speed_scale(),
                rebuild_main=False,
                rebuild_hud=True,
            )
            self._set_build_progress_text(
                f"준비 완료 (캐시) — dwell {len(hit.dwells)} · "
                f"JSON {sum(1 for e in hit.schedule if e.category != 'dwell')}건"
            )
        else:
            self._refresh_csv_schedule_preview(
                path, fast_only=True, rebuild_main=False, rebuild_hud=True
            )

    def _has_timeline_ui(self) -> bool:
        return (
            self._schedule_rows_stack is not None
            or self._hud_schedule_rows_stack is not None
            or self._schedule_model is not None
        )

    def set_csv_combo_index(self, index: int) -> None:
        """CSV 파일 드롭다운 인덱스 (HUD ↔ 본창 공유)."""
        if not self._csv_paths:
            self._csv_selected_index = 0
            return
        self._csv_selected_index = max(0, min(int(index), len(self._csv_paths) - 1))
        if self._csv_file_stack is not None:
            self._rebuild_csv_combo_ui(selected_index=self._csv_selected_index)
        self._on_csv_file_selection_changed()

    def get_csv_combo_index(self) -> int:
        if self._combo is not None:
            return _read_combo_index(self._combo)
        return int(self._csv_selected_index)

    def csv_file_display_names(self) -> List[str]:
        return [p.name for p in self._csv_paths]

    def _ensure_macro_script_models_without_ui(self) -> None:
        """타임라인 아래 UI 를 숨길 때도 빌드·스크립트 모델은 유지."""
        try:
            from omni.ui import SimpleStringModel  # type: ignore
        except Exception:
            return
        if self._schedule_model is None:
            self._schedule_model = SimpleStringModel("")
        if self._build_progress_model is None:
            self._build_progress_model = SimpleStringModel("(빌드·재생 진행 — 대기)")
        if self._build_ui_ticker is None:
            self._build_ui_ticker = _SecondsIntervalProgress(
                self._set_build_progress_text, label="빌드"
            )
        if self._script_model is None:
            self._script_model = SimpleStringModel(
                "atm_foup1_pick(1)\n"
                "# atm_foup1_pick(slot_number=1) 동일\n"
                "vtm_chamber1_right_pick()\n"
                "# duration_sec=3.0  선택(재생 배속)\n"
            )

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

        macro_names = list_macro_function_names()
        try:
            from omni.ui import SimpleStringModel  # type: ignore
        except Exception:
            SimpleStringModel = None  # type: ignore
        hide_below = csv_play_hide_ui_below_timeline()
        # 평면도(~200) + 기존 높이
        win_h = (580 if hide_below else 920) + 220
        self._window = ui.Window(self.window_title(), width=640, height=win_h)
        if self._screen > 1:
            try:
                self._window.position_x = 40 + (self._screen - 1) * 660
                self._window.position_y = 60 + (self._screen - 1) * 40
            except Exception:
                pass
        register_csv_play_timeline_window(self, screen=self._screen)
        with self._window.frame:
            with ui.VStack(spacing=6):
                if self._screen > 1:
                    ui.Label(
                        f"화면 {self._screen} — 독립 CSV·재생·오버레이",
                        height=20,
                    )
                try:
                    self.mount_screen_visibility_checkboxes_ui(ui)
                except Exception as exc:
                    self._log(f"screen visibility checkboxes UI: {exc}")
                ui.Label(
                    "CSV: dwell [Play] | 매크로: ``atm_foup1_pick(7)`` 또는 ``atm_foup1_pick(slot_number=7)`` "
                    "(한 줄 = 함수 하나, JSON 은 lam/lam_event_sequences/)",
                    word_wrap=True,
                    height=36,
                )
                ui.Label("CSV 폴더", height=16)
                if SimpleStringModel is not None:
                    self._csv_dir_model = SimpleStringModel(str(get_lam_csv_dir()))
                    try:
                        ui.StringField(
                            model=self._csv_dir_model,
                            height=22,
                            tooltip="CSV가 있는 폴더 경로 — [목록 새로고침]으로 드롭다운 갱신",
                        )
                    except TypeError:
                        ui.StringField(model=self._csv_dir_model, height=22)
                else:
                    ui.Label(str(get_lam_csv_dir()), height=22, word_wrap=True)
                with ui.VStack(spacing=4) as csv_file_stack:
                    self._csv_file_stack = csv_file_stack
                self._reload_csv_list(preserve_selection=False)
                with ui.HStack(spacing=6, height=28):
                    ui.Button(
                        "목록 새로고침",
                        width=120,
                        clicked_fn=self._on_refresh_clicked,
                        tooltip="위 폴더 경로에서 *.csv 를 다시 읽어 드롭다운 갱신",
                    )
                    ui.Button(
                        "타임라인 갱신",
                        width=110,
                        clicked_fn=self._on_schedule_refresh_clicked,
                        tooltip="콤보에서 고른 CSV 를 다시 파싱해 아래 목록 갱신",
                    )
                    ui.Button(
                        "정렬 저장",
                        width=90,
                        clicked_fn=self._on_save_sorted_csv_clicked,
                        tooltip="선택한 CSV를 eqp_start_tm 오름차순으로 정렬해 같은 폴더에 새 파일로 저장",
                    )
                    ui.Button("CSV Play", width=90, clicked_fn=self._on_play_clicked)
                    ui.Button(
                        "일시정지",
                        width=72,
                        clicked_fn=self._on_csv_pause_clicked,
                        tooltip="진행 위치 저장 후 멈춤 — Play 시 이어서 재생",
                    )
                    ui.Button(
                        "정지(초기화)",
                        width=88,
                        clicked_fn=self._on_csv_stop_reset_clicked,
                        tooltip=(
                            "멈춤 + Z/팔 TBS→0 + FOUP 75 show · "
                            "나머지 슬롯·팔 wafer hide · 재생 위치 삭제"
                        ),
                    )
                try:
                    from omni.ui import SimpleBoolModel  # type: ignore

                    if self._process_only_model is None:
                        try:
                            from .lam_viewport_overlay_config import (  # type: ignore
                                STARTUP_CHECK_PROCESS_ONLY,
                            )

                            _po_def = bool(STARTUP_CHECK_PROCESS_ONLY)
                        except Exception:
                            _po_def = False
                        self._process_only_model = SimpleBoolModel(_po_def)
                    self._wire_process_only_model_live_update()
                    with ui.HStack(spacing=4, width=0):
                        ui.Label("공정만보기", width=72)
                        ui.CheckBox(
                            model=self._process_only_model,
                            width=22,
                            clicked_fn=lambda: self._on_process_only_checkbox_clicked(),
                            tooltip=(
                                "재생 중에도 즉시 전환: CSV 시각(t) 유지, JSON 없는 빈 대기만 생략 "
                                "(배속 1x). ATM 종료 후 VTM 등 레인 간 빈 텀도 생략. "
                                "체크 해제 시 기존 시간 재생."
                            ),
                        )
                except Exception:
                    self._process_only_model = None
                try:
                    from omni.ui import SimpleBoolModel  # type: ignore

                    if self._csv_prerun_export_model is None:
                        try:
                            from .lam_sim_control_defaults import CSV_PRERUN_EXPORT_JSON

                            _ex_def = bool(CSV_PRERUN_EXPORT_JSON)
                        except Exception:
                            _ex_def = True
                        self._csv_prerun_export_model = SimpleBoolModel(_ex_def)
                    with ui.HStack(spacing=4, width=0):
                        ui.Label("프리런 JSON 저장", width=108)
                        ui.CheckBox(
                            model=self._csv_prerun_export_model,
                            width=22,
                            tooltip=(
                                "Play 시 CSV 프리런 결과를 "
                                "data/csv_prerun/prerun_screen*.json 으로 저장"
                            ),
                        )
                except Exception:
                    self._csv_prerun_export_model = None
                ui.Spacer()
                try:
                    self.mount_wafer_label_show_checkbox_ui(ui)
                except Exception as exc:
                    self._log(f"wafer label checkbox UI: {exc}")
                try:
                    self.mount_overlay_feature_checkboxes_ui(ui)
                except Exception as exc:
                    self._log(f"overlay feature checkboxes UI: {exc}")
                try:
                    self.mount_play_camera_fly_checkbox_ui(ui, label_width=88)
                    self.mount_play_camera_capture_button_ui(ui, width=52)
                    self.mount_top_view_checkbox_ui(ui, label_width=88)
                    self.mount_play_prim_hide_checkbox_ui(ui, label_width=88)
                except Exception as exc:
                    self._log(f"play prim hide checkbox UI: {exc}")
                with ui.HStack(spacing=6, height=28):
                    ui.Label("재생 배속", width=70)
                    try:
                        from omni.ui import SimpleFloatModel  # type: ignore

                        if self._speed_model is None:
                            self._speed_model = SimpleFloatModel(1.0)
                        self._wire_speed_model_live_update()
                        ui.FloatField(model=self._speed_model, width=72)
                    except Exception:
                        self._speed_model = None
                        ui.Label("(배속 UI 없음)", width=100)
                    ui.Button("1x", width=36, clicked_fn=lambda: self._set_speed_preset(1.0))
                    ui.Button("5x", width=36, clicked_fn=lambda: self._set_speed_preset(5.0))
                    ui.Label(
                        "(CSV t까지 대기 + JSON 전 스텝 실행, 둘 다 ÷배속)",
                        width=300,
                    )
                    ui.Spacer()
                try:
                    from .lam_equipment_floorplan_ui import mount_equipment_floorplan_ui

                    mount_equipment_floorplan_ui(
                        ui, self, height=200.0, screen=int(self._screen)
                    )
                except Exception as exc:
                    self._log(f"equipment floorplan UI: {exc}")
                ui.Label(
                    "CSV 재생 타임라인 — JSON 재생 중인 행만 녹색 · [타임라인 갱신] 또는 Play 시 준비",
                    height=18,
                )
                try:
                    from omni.ui import SimpleStringModel  # type: ignore
                except Exception:
                    SimpleStringModel = None  # type: ignore
                if SimpleStringModel is not None:
                    self._schedule_model = SimpleStringModel("")
                    with ui.ScrollingFrame(
                        height=240,
                        style={
                            "background_color": 0xFF1A1E26,
                            "border_width": 1,
                            "border_color": 0xFF3A3A3A,
                        },
                    ) as schedule_scroll:
                        with ui.VStack(spacing=2, height=0) as schedule_stack:
                            self._schedule_rows_stack = schedule_stack
                        self._schedule_scroll_frame = schedule_scroll
                    # 점유 스케줄러 진단 — 플래그 True 일 때만 UI 생성 (False 시 레이아웃 불변)
                    try:
                        from .lam_csv_occupancy_scheduler import (
                            occupancy_scheduler_enabled,
                        )

                        _occ_ui = bool(occupancy_scheduler_enabled())
                    except Exception:
                        _occ_ui = False
                    if _occ_ui:
                        ui.Label(
                            "점유·정렬 진단 (화면별) — 시각 재조정 / 점유 경고",
                            height=18,
                        )
                        with ui.ScrollingFrame(
                            height=120,
                            style={
                                "background_color": 0xFF141820,
                                "border_width": 1,
                                "border_color": 0xFF4A3A2A,
                            },
                        ) as occ_scroll:
                            with ui.VStack(spacing=2, height=0) as occ_stack:
                                self._occupancy_diag_stack = occ_stack
                            self._occupancy_diag_scroll = occ_scroll
                        self._refresh_occupancy_diag_ui(())
                    if not hide_below:
                        self._build_progress_model = SimpleStringModel("(빌드·재생 진행 — 대기)")
                        ui.StringField(
                            model=self._build_progress_model,
                            height=22,
                            read_only=True,
                        )
                        self._build_ui_ticker = _SecondsIntervalProgress(
                            self._set_build_progress_text, label="빌드"
                        )
                    else:
                        self._ensure_macro_script_models_without_ui()
                else:
                    ui.Label("타임라인 표시 불가 (SimpleStringModel 없음).", height=40)
                self._refresh_csv_schedule_preview(fast_only=True)
                if not hide_below:
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
                else:
                    self._ensure_macro_script_models_without_ui()
                    self._log_label = None

        if self._screen > 1 and self._lam_window_ref is not None:
            try:
                self._lam_window_ref.sync_csv_viewport_overlays_for_screen(self._screen)
            except Exception:
                pass

    def _read_csv_dir_text(self) -> str:
        m = self._csv_dir_model
        if m is None:
            return str(get_lam_csv_dir())
        for getter in ("get_value_as_string", "get_value"):
            try:
                fn = getattr(m, getter, None)
                if callable(fn):
                    return str(fn()).strip()
            except Exception:
                continue
        return str(get_lam_csv_dir())

    def _reload_csv_list(self, *, preserve_selection: bool = True) -> None:
        """폴더 텍스트 기준으로 ``_csv_paths`` · CSV ComboBox 를 갱신."""
        prev_name: Optional[str] = None
        if preserve_selection:
            prev = self._selected_csv_path()
            if prev is not None:
                prev_name = prev.name

        dir_text = self._read_csv_dir_text()
        d = Path(dir_text).expanduser()
        if not d.is_dir():
            self._csv_paths = []
            self._rebuild_csv_combo_ui(selected_index=0)
            self._log(f"폴더 없음 — 경로 확인 후 [목록 새로고침]: {dir_text}")
            return

        self._csv_paths = list_csv_paths_in_directory(d)
        idx = 0
        if prev_name:
            for i, p in enumerate(self._csv_paths):
                if p.name == prev_name:
                    idx = i
                    break
        self._rebuild_csv_combo_ui(selected_index=idx)
        self._log(f"CSV {len(self._csv_paths)}개 — {d}")

    def _rebuild_csv_combo_ui(self, *, selected_index: int = 0) -> None:
        if self._csv_paths:
            self._csv_selected_index = max(
                0, min(int(selected_index), len(self._csv_paths) - 1)
            )
        else:
            self._csv_selected_index = 0
        try:
            import omni.ui as ui  # type: ignore
        except Exception:
            return
        stack = self._csv_file_stack
        if stack is None:
            return
        try:
            stack.clear()
        except Exception as exc:
            self._log(f"CSV 목록 UI 갱신 실패: {exc}")
            return
        with stack:
            if not self._csv_paths:
                ui.Label("CSV 없음 — 폴더에 .csv 파일을 추가하세요.", height=28)
                self._combo = None
            else:
                names = [p.name for p in self._csv_paths]
                ui.Label("CSV 파일", height=16)
                idx = max(0, min(int(selected_index), len(names) - 1))
                self._combo = ui.ComboBox(idx, *names, width=520, height=26)
                try:
                    self._combo.model.add_item_changed_fn(
                        lambda *_a: self._on_csv_file_selection_changed()
                    )
                except Exception:
                    pass

    def _on_csv_file_selection_changed(self) -> None:
        """드롭다운 CSV 변경 시 타임라인·준비 캐시를 선택 파일에 맞춘다."""
        if not self._has_timeline_ui():
            return
        path = self._selected_csv_path()
        if path is None:
            self._rebuild_schedule_timeline_rows(
                [],
                rebuild_main=self._schedule_rows_stack is not None,
                rebuild_hud=self._hud_schedule_rows_stack is not None,
            )
            return
        prev = self._prepared_playback
        if prev is not None and prev.path.resolve() != path.resolve():
            self._prepared_playback = None
        self._refresh_csv_schedule_preview(path, fast_only=True)

    def _selected_csv_path(self) -> Optional[Path]:
        if not self._csv_paths:
            return None
        if self._combo is not None:
            idx = _read_combo_index(self._combo)
        else:
            idx = int(self._csv_selected_index)
        idx = max(0, min(idx, len(self._csv_paths) - 1))
        return self._csv_paths[idx]

    def _set_schedule_model_text(self, text: str) -> None:
        m = self._schedule_model
        if m is None:
            return
        try:
            m.set_value(text)
        except Exception:
            try:
                m.set_value_as_string(text)  # type: ignore[attr-defined]
            except Exception:
                pass

    def _clear_one_timeline_stack(self, stack: Any, labels_out: List[Any]) -> None:
        """한 타임라인 VStack 만 비움 (본창·HUD 분리 — 서로 ``clear`` 하지 않음)."""
        labels_out.clear()
        if stack is None:
            return
        try:
            stack.clear()
        except Exception:
            pass

    def _clear_schedule_timeline_stack(
        self,
        *,
        main: bool = True,
        hud: bool = True,
    ) -> None:
        if main:
            self._clear_one_timeline_stack(
                self._schedule_rows_stack, self._schedule_row_labels
            )
        if hud:
            self._clear_one_timeline_stack(
                self._hud_schedule_rows_stack, self._hud_schedule_row_labels
            )

    def _timeline_label_style(self, entry: CsvPlaybackScheduleEntry) -> Dict[str, Any]:
        if entry.category == "dwell":
            return {"color": _TIMELINE_UI_COLOR_DWELL, "background_color": 0}
        if self._entry_is_timeline_playing(entry, self._schedule_highlight_keys):
            return {
                "color": _TIMELINE_UI_COLOR_PLAYING,
                "background_color": 0x4422AA44,
            }
        return {"color": _TIMELINE_UI_COLOR_DEFAULT, "background_color": 0}

    def _entry_is_timeline_playing(
        self, entry: CsvPlaybackScheduleEntry, active_keys: frozenset
    ) -> bool:
        return _schedule_entry_matches_active(entry, active_keys)

    def _apply_timeline_label_style(self, label: Any, entry: CsvPlaybackScheduleEntry) -> None:
        style = self._timeline_label_style(entry)
        try:
            label.style = style
        except Exception:
            try:
                for prop, val in style.items():
                    label.style[prop] = val
            except Exception:
                pass

    def _first_highlighted_row_index(
        self, active_keys: frozenset
    ) -> Optional[int]:
        """타임라인 순서상 첫 번째 녹색(재생 중) 행 인덱스."""
        if not active_keys:
            return None
        for i, ent in enumerate(self._schedule_row_entries):
            if _schedule_entry_matches_active(ent, active_keys):
                return i
        return None

    def _scroll_timeline_label_into_view(
        self, label: Any, scroll_frame: Any = None
    ) -> None:
        """재생 중 행이 ScrollingFrame 안에 보이도록 스크롤."""
        if label is None:
            return
        try:
            label.scroll_here_y()
            return
        except Exception:
            pass
        if scroll_frame is None:
            return
        try:
            y = float(getattr(label, "screen_position_y", 0.0) or 0.0)
            h = float(getattr(label, "computed_height", 18.0) or 18.0)
            frame_h = float(getattr(scroll_frame, "computed_height", 0.0) or 0.0)
            if frame_h <= 0:
                return
            cur = float(getattr(scroll_frame, "scroll_y", 0.0) or 0.0)
            scroll_frame.scroll_y = max(0.0, y - frame_h * 0.25)
        except Exception:
            pass

    def _scroll_timeline_to_highlighted_rows(self, active_keys: frozenset) -> None:
        """본창·HUD 타임라인 — 녹색 행으로 자동 스크롤."""
        row_i = self._first_highlighted_row_index(active_keys)
        if row_i is None:
            return
        lbl_idx = row_i + 1
        pairs = (
            (self._schedule_row_labels, self._schedule_scroll_frame),
            (self._hud_schedule_row_labels, self._hud_schedule_scroll_frame),
        )
        for labels, scroll_frame in pairs:
            if lbl_idx >= len(labels):
                continue
            self._scroll_timeline_label_into_view(labels[lbl_idx], scroll_frame)

    def _live_timeline_highlight_keys(self) -> frozenset:
        return get_csv_play_timeline_active_keys_snap(screen=self._screen)

    def _scroll_timeline_to_top(self) -> None:
        """재생 타임라인 스크롤을 맨 위로."""
        for scroll_frame in (
            self._schedule_scroll_frame,
            self._hud_schedule_scroll_frame,
        ):
            if scroll_frame is None:
                continue
            try:
                scroll_frame.scroll_y = 0.0
            except Exception:
                pass

    def _reset_timeline_playback_highlight_ui(self) -> None:
        """정지(초기화) — 녹색 강조 제거 + 스크롤 최상단 (즉시 UI 스레드)."""
        self._schedule_highlight_keys = frozenset()
        clear_csv_play_timeline_highlight(screen=self._screen)
        entries = self._schedule_row_entries
        for labels in (self._schedule_row_labels, self._hud_schedule_row_labels):
            if len(labels) < 2:
                continue
            for i, ent in enumerate(entries):
                lbl_idx = i + 1
                if lbl_idx >= len(labels):
                    break
                self._apply_timeline_label_style(labels[lbl_idx], ent)
        self._scroll_timeline_to_top()

    def _apply_schedule_row_highlight(
        self,
        active_keys: frozenset,
        *,
        scroll_to_top_on_clear: bool = False,
    ) -> None:
        merged = frozenset(active_keys) | self._live_timeline_highlight_keys()
        self._schedule_highlight_keys = merged
        entries = self._schedule_row_entries
        for labels in (self._schedule_row_labels, self._hud_schedule_row_labels):
            if len(labels) < 2:
                continue
            for i, ent in enumerate(entries):
                lbl_idx = i + 1
                if lbl_idx >= len(labels):
                    break
                self._apply_timeline_label_style(labels[lbl_idx], ent)
        if merged:
            self._scroll_timeline_to_highlighted_rows(merged)
        elif scroll_to_top_on_clear:
            self._scroll_timeline_to_top()

    def _post_apply_cached_timeline_ui(
        self, cached: CachedCsvPlayback, *, wait: bool = False
    ) -> None:
        """캐시 타임라인을 Kit update tick 에 반영. ``wait=True`` 면 1프레임 후까지 대기."""
        if wait:
            try:
                from .lam_sequence_engine import _dispatch_main_wait

                ok = _dispatch_main_wait(
                    lambda: self._apply_cached_timeline_ui(cached),
                    timeout=5.0,
                )
                if not ok:
                    print(
                        f"{_PRINT_PREFIX} 타임라인 UI 반영 타임아웃",
                        flush=True,
                    )
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} 타임라인 UI 반영 실패: {exc}",
                    flush=True,
                )
            return
        _post_kit_main_thread(lambda: self._apply_cached_timeline_ui(cached))

    def _fill_schedule_timeline_stack(
        self,
        stack: Any,
        labels_out: List[Any],
        entries: List[CsvPlaybackScheduleEntry],
        *,
        speed_scale: float,
    ) -> None:
        import omni.ui as ui  # type: ignore

        sp = max(0.1, float(speed_scale or 1.0))
        if not entries:
            with stack:
                labels_out.append(
                    ui.Label(
                        "(CSV 선택 후 타임라인이 표시됩니다.)",
                        height=18,
                        word_wrap=True,
                        style={"color": _TIMELINE_UI_COLOR_DEFAULT},
                    )
                )
            return
        n_act = sum(1 for e in entries if e.category != "dwell")
        with stack:
            labels_out.append(
                ui.Label(
                    f"=== CSV 타임라인 · {len(entries)}건 · JSON {n_act}건 · 배속 {sp:g}x ===",
                    height=20,
                    word_wrap=True,
                    style={"color": _TIMELINE_UI_COLOR_DEFAULT},
                )
            )
            for i, ent in enumerate(entries, 1):
                line = format_csv_playback_schedule_row(i, ent, speed_scale=sp)
                labels_out.append(
                    ui.Label(
                        line,
                        height=18,
                        word_wrap=True,
                        style=self._timeline_label_style(ent),
                    )
                )

    def _rebuild_schedule_timeline_rows(
        self,
        entries: List[CsvPlaybackScheduleEntry],
        *,
        speed_scale: float = 1.0,
        rebuild_main: bool = True,
        rebuild_hud: bool = True,
    ) -> None:
        """스크롤 타임라인 행 재구성. HUD 등록 시 ``rebuild_main=False`` 로 본창 유지."""
        self._schedule_row_entries = list(entries)
        if (
            not rebuild_main
            and not rebuild_hud
        ) or (
            rebuild_main
            and self._schedule_rows_stack is None
            and rebuild_hud
            and self._hud_schedule_rows_stack is None
        ):
            self._set_schedule_model_text(
                format_csv_playback_schedule(entries, speed_scale=speed_scale)
            )
            return
        sp = max(0.1, float(speed_scale or 1.0))
        if not entries:
            self._set_schedule_model_text("(CSV 선택 후 타임라인이 표시됩니다.)")
        try:
            import omni.ui as ui  # type: ignore
        except Exception:
            self._set_schedule_model_text(
                format_csv_playback_schedule(entries, speed_scale=sp)
            )
            return
        _ = ui
        self._clear_schedule_timeline_stack(main=rebuild_main, hud=rebuild_hud)
        if rebuild_main and self._schedule_rows_stack is not None:
            self._fill_schedule_timeline_stack(
                self._schedule_rows_stack,
                self._schedule_row_labels,
                entries,
                speed_scale=sp,
            )
        if rebuild_hud and self._hud_schedule_rows_stack is not None:
            self._fill_schedule_timeline_stack(
                self._hud_schedule_rows_stack,
                self._hud_schedule_row_labels,
                entries,
                speed_scale=sp,
            )
        self._apply_schedule_row_highlight(self._live_timeline_highlight_keys())

    def _set_build_progress_text(self, text: str) -> None:
        for m in (self._build_progress_model, self._hud_build_progress_model):
            if m is None:
                continue
            try:
                m.set_value(text)
            except Exception:
                try:
                    m.set_value_as_string(text)  # type: ignore[attr-defined]
                except Exception:
                    pass

    def _format_build_progress_line(self, done: int, total: int) -> str:
        total = max(1, int(total))
        done = max(0, min(int(done), total))
        pct = int(100.0 * done / total)
        return f"프리런 진행: {pct}% ({done}/{total} 이벤트)"

    def _format_play_progress_line(
        self, csv_t: float, csv_total: float, wall_elapsed: float, wall_total_est: float
    ) -> str:
        snap = get_csv_play_progress_snap(screen=self._screen)
        if snap.get("process_only"):
            json_done = int(snap.get("json_done", 0) or 0)
            json_total = max(1, int(snap.get("json_total", 1) or 1))
            csv_t_disp = float(snap.get("csv_t_display", csv_t) or csv_t)
            csv_tot = float(snap.get("csv_total", csv_total) or csv_total)
            pct_json = 100.0 * json_done / json_total
            pct_csv = (100.0 * csv_t_disp / csv_tot) if csv_tot > 1e-6 else 0.0
            return (
                f"▶ 공정만보기 {pct_json:.1f}% (JSON {json_done}/{json_total}) | "
                f"CSV t {csv_t_disp:.1f}/{csv_tot:.1f}s ({pct_csv:.1f}%) | "
                f"실경과 {wall_elapsed:.0f}s"
            )
        pct = (100.0 * csv_t / csv_total) if csv_total > 1e-6 else 0.0
        return (
            f"▶ 재생 {pct:.1f}% | CSV t {csv_t:.1f}/{csv_total:.1f}s | "
            f"실경과 {wall_elapsed:.0f}s/{wall_total_est:.0f}s"
        )

    def _read_process_only(self) -> bool:
        m = self._process_only_model
        if m is None:
            return False
        try:
            return bool(m.get_value_as_bool())
        except Exception:
            try:
                return bool(m.as_bool)
            except Exception:
                return False

    def _wire_process_only_model_live_update(self) -> None:
        """공정만보기 — 모델 value_changed 가 SSOT (HUD CheckBox clicked_fn 미발화 대비)."""
        if self._process_only_model_live_wired:
            return
        m = self._process_only_model
        if m is None:
            return

        def _on_changed(*_a: Any) -> None:
            if getattr(self, "_process_only_model_syncing", False):
                return
            # 토글 후 모델값이 목표 — invert 금지 (clicked 타이밍과 충돌)
            self._schedule_live_process_only_changed(desired=self._read_process_only())

        wired = False
        for hook in ("add_value_changed_fn", "add_item_changed_fn"):
            try:
                fn = getattr(m, hook, None)
                if callable(fn):
                    fn(_on_changed)
                    wired = True
                    break
            except Exception:
                continue
        if wired:
            self._process_only_model_live_wired = True
            print(
                f"{_PRINT_PREFIX} 공정만보기 value_changed wire 화면{self._screen}",
                flush=True,
            )
        else:
            # clicked_fn 만으로도 동작 — 재시도 위해 wired 플래그는 올리지 않음
            print(
                f"{_PRINT_PREFIX} 공정만보기 value_changed wire 실패 화면{self._screen} "
                f"— clicked_fn 경로 사용",
                flush=True,
            )

    def _schedule_live_process_only_changed(
        self, *, desired: Optional[bool] = None
    ) -> None:
        """모델/체크 변경 → live 전환. 목표값은 **토글 후 모델값** (invert 하지 않음).

        연타 debounce: 최신 desired 만 짧은 지연 후 1회 적용 (전환 worker 폭주 방지).
        **Kit main / ``_dispatch_main`` 에 올리지 않는다** — 애니 update·USD write
        큐와 겹치면 앱이 멈춘다. debounce 백그라운드에서 바로 전환 worker 기동.
        """
        captured = desired
        si = int(self._screen)
        self._process_only_switch_gen = int(
            getattr(self, "_process_only_switch_gen", 0)
        ) + 1
        token = int(self._process_only_switch_gen)
        print(
            f"{_PRINT_PREFIX} 공정만보기 schedule 화면{si} "
            f"desired={captured if captured is not None else '(read)'} gen={token}",
            flush=True,
        )

        def _debounce() -> None:
            time.sleep(0.08)
            if token != int(getattr(self, "_process_only_switch_gen", 0)):
                return
            try:
                if captured is not None:
                    self._on_live_process_only_changed(desired_override=bool(captured))
                else:
                    # 모델 read 는 omni.ui — 메인 권장. 실패 시 스냅 기준으로 폴백.
                    try:
                        d = self._read_process_only()
                    except Exception:
                        d = bool(
                            get_csv_play_progress_snap(screen=si).get("process_only")
                        )
                    self._on_live_process_only_changed(desired_override=bool(d))
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} 공정만보기 debounce 실패 화면{si}: {exc}",
                    flush=True,
                )

        threading.Thread(
            target=_debounce,
            daemon=True,
            name=f"lam-process-only-debounce-s{si}",
        ).start()

    def _on_process_only_checkbox_clicked(self) -> None:
        """CheckBox clicked_fn — value_changed 가 있으면 그쪽이 SSOT."""
        si = int(self._screen)
        if self._process_only_model_live_wired:
            print(
                f"{_PRINT_PREFIX} 공정만보기 clicked 화면{si} — "
                f"value_changed 위임 (무시)",
                flush=True,
            )
            return
        before = self._read_process_only()
        print(
            f"{_PRINT_PREFIX} 공정만보기 clicked 화면{si} before={before}",
            flush=True,
        )

        def _after_toggle() -> None:
            after = self._read_process_only()
            # 클릭 직후 아직 모델이 안 바뀌었으면 토글 의도로 보정
            desired = after if after != before else (not before)
            print(
                f"{_PRINT_PREFIX} 공정만보기 clicked→apply 화면{si} "
                f"before={before} after={after} desired={desired}",
                flush=True,
            )
            self._schedule_live_process_only_changed(desired=bool(desired))

        try:
            import omni.kit.app  # type: ignore

            app = omni.kit.app.get_app()
            if app is not None and hasattr(app, "post_update"):
                app.post_update(_after_toggle)
                return
        except Exception:
            pass
        _post_kit_main_thread(_after_toggle)

    def _csv_play_is_active(self) -> bool:
        """해당 화면 CSV 재생 중 — 메인 스레드·세션·자식 worker 기준."""
        si = int(self._screen)
        if self._csv_play_thread_alive():
            return True
        if csv_play_session_active(screen=si):
            return True
        if _alive_csv_play_child_workers(screen=si):
            return True
        return False

    def _resolve_prepared_playback_for_live_switch(self) -> Optional[CachedCsvPlayback]:
        cached = self._prepared_playback
        if cached is not None:
            return cached
        path = self._selected_csv_path()
        hit = find_cached_csv_playback(path)
        if hit is not None:
            # 이후 전환·미리보기가 다시 잃지 않도록 복구
            self._prepared_playback = hit
            return hit
        return None

    def _preserve_prepared_playback_during_play(self) -> bool:
        """재생/전환 중이면 prepared 를 미리보기 miss 로 지우면 안 됨."""
        try:
            if self._csv_play_is_active():
                return True
        except Exception:
            pass
        try:
            if csv_play_session_active(screen=self._screen):
                return True
        except Exception:
            pass
        if bool(getattr(self, "_process_only_switch_pending", False)):
            return True
        return False

    def _on_live_process_only_changed(
        self, *, desired_override: Optional[bool] = None
    ) -> None:
        """재생 중 모드 변경: UI는 즉시 반환, pause·재개는 백그라운드에서 화면별로만."""
        if getattr(self, "_process_only_model_syncing", False):
            return
        desired = (
            bool(desired_override)
            if desired_override is not None
            else self._read_process_only()
        )
        self._process_only_last_live_desired = bool(desired)
        self._process_only_switch_target = desired
        print(
            f"{_PRINT_PREFIX} 공정만보기 live 화면{self._screen} desired={desired} "
            f"active={self._csv_play_is_active()} "
            f"pending={self._process_only_switch_pending}",
            flush=True,
        )
        # 화면별 독립 — 타 화면으로 공정만보기 전파하지 않음
        self._try_start_live_process_only_switch(desired)
        if not self._csv_play_is_active():
            try:
                self._refresh_csv_schedule_preview(fast_only=True)
            except Exception:
                pass

    def _try_start_live_process_only_switch(self, desired: bool) -> None:
        """해당 화면 재생 중이면 pause → 공정만보기/일반 모드로 이어서 재생.

        진행 중이면 목표만 갱신(latest-wins). 전환 완료 후 목표가 다르면 재전환.
        호출 스레드(메인/debounce)에서는 pending 설정·worker 기동만 한다.
        checkpoint / stop / detach / resume 은 전부 switch worker.
        """
        si = int(self._screen)
        self._process_only_switch_target = bool(desired)
        switch_lock = getattr(self, "_process_only_switch_lock", None)
        if switch_lock is None:
            self._process_only_switch_lock = threading.Lock()
            switch_lock = self._process_only_switch_lock

        with switch_lock:
            if self._process_only_switch_pending:
                print(
                    f"{_PRINT_PREFIX} 공정만보기 전환 queue 화면{si} — "
                    f"진행 중, 목표만 desired={desired}",
                    flush=True,
                )
                return
            if not self._csv_play_is_active():
                print(
                    f"{_PRINT_PREFIX} 공정만보기 전환 skip 화면{si} — 재생 세션 없음",
                    flush=True,
                )
                return
            # Play thread 는 살아 있으나 CSV 시계 세션 전(preflight/prerun) —
            # pause/reap 하면 전환 worker 가 잠기고 mid-check 가 무시된다.
            if not csv_play_session_active(screen=si):
                self._process_only_deferred_for_start = bool(desired)
                print(
                    f"{_PRINT_PREFIX} 공정만보기 deferred 화면{si} desired={desired} "
                    f"(preflight/prerun — Play 진입 시 적용)",
                    flush=True,
                )
                try:
                    _post_kit_main_thread(
                        lambda d=bool(desired), s=si: self._log(
                            f"공정만보기 {'ON' if d else 'OFF'} — "
                            f"재생 시작 시 적용 (화면{s} preflight 중)"
                        )
                    )
                except Exception:
                    pass
                return
            snap = get_csv_play_progress_snap(screen=si)
            desired_b = bool(desired)
            snap_po = bool(snap.get("process_only"))
            last_applied = getattr(self, "_process_only_last_applied", None)
            # snap·last_applied 가 모두 목표와 일치할 때만 skip.
            # last_applied 만 OFF 로 맞춰지면 snap=ON 인 채 전환이 영구 skip 되는 버그가 있었다.
            if csv_play_session_active(screen=si) and snap_po == desired_b:
                if last_applied is None or bool(last_applied) == desired_b:
                    print(
                        f"{_PRINT_PREFIX} 공정만보기 전환 skip 화면{si} — "
                        f"이미 {'ON' if desired_b else 'OFF'} "
                        f"(snap={snap_po} applied={last_applied})",
                        flush=True,
                    )
                    self._process_only_last_applied = desired_b
                    return
                print(
                    f"{_PRINT_PREFIX} 공정만보기 전환 재시도 화면{si} — "
                    f"목표={desired_b} snap={snap_po} last_applied={last_applied}",
                    flush=True,
                )
            cached = self._resolve_prepared_playback_for_live_switch()
            if cached is None:
                print(
                    f"{_PRINT_PREFIX} 공정만보기 실시간 전환 실패 — 화면{self._screen} "
                    "재생 캐시 없음",
                    flush=True,
                )
                return

            path = str(cached.path)
            try:
                speed_now = float(self._read_speed_scale())
            except Exception:
                speed_now = 1.0
            registry = self._registry
            scheduler = self._scheduler
            kit_ext = getattr(self._lam_window_ref, "_kit_ext", None)
            self._process_only_switch_pending = True

        # 메인/debounce 에서는 print + Thread.start 만 (USD·stop·join 금지)
        print(
            f"{_PRINT_PREFIX} 공정만보기 {'ON' if desired else 'OFF'} "
            f"실시간 전환 시작 — 화면{si}",
            flush=True,
        )

        def _switch_worker() -> None:
            resumed_ok = False
            follow = bool(desired)
            applied_mode: Optional[bool] = None
            print(
                f"{_PRINT_PREFIX} 공정만보기 switch worker enter 화면{si}",
                flush=True,
            )
            try:
                with csv_play_screen_binding(si):
                    # 1) 신규 JSON 시작 보류 + 진행 중 JSON/애니 가 끝날 때까지 대기
                    #    (해제·ON 모두 — 끊지 않고 현재 공정 동작 완료 후 모드 적용)
                    set_csv_play_mode_switch_drain(True, screen=si)
                    try:
                        _wait_csv_play_in_flight_json_finish(
                            screen=si, timeout_sec=180.0
                        )
                    except Exception as exc:
                        print(
                            f"{_PRINT_PREFIX} 공정만보기 JSON drain 경고 화면{si}: {exc}",
                            flush=True,
                        )
                    # drain 중 목표가 바뀌었을 수 있음
                    target0w = bool(self._process_only_switch_target)
                    print(
                        f"{_PRINT_PREFIX} 공정만보기 switch refresh… 화면{si}",
                        flush=True,
                    )
                    try:
                        self._refresh_play_runtime()
                    except Exception:
                        pass
                    pause_reg = self._registry or registry
                    pause_sch = self._scheduler or scheduler
                    if pause_reg is None or pause_sch is None:
                        raise RuntimeError("registry/scheduler 없음")
                    try:
                        ui_sp = float(self._read_speed_scale())
                    except Exception:
                        ui_sp = float(speed_now)
                    sp0 = 1.0 if target0w else ui_sp
                    print(
                        f"{_PRINT_PREFIX} 공정만보기 switch checkpoint… 화면{si}",
                        flush=True,
                    )
                    try:
                        ck = save_csv_play_pause_checkpoint(
                            csv_path=path,
                            speed_scale=sp0,
                            process_only=target0w,
                            screen=si,
                            for_mode_switch=True,
                        )
                    except Exception as exc:
                        raise RuntimeError(f"checkpoint 실패: {exc}") from exc
                    print(
                        f"{_PRINT_PREFIX} 공정만보기 switch pause/detach… 화면{si} "
                        f"t≈{ck.resume_csv_sec:.1f}s "
                        f"(last_json={csv_play_screen_session(si).last_json_completed_csv_sec:.1f}s)",
                        flush=True,
                    )
                    # soft stop — 이미 in-flight JSON 은 끝난 뒤. 대기 중 worker 만 끊음.
                    request_stop_csv_playback(
                        pause_reg,
                        pause_sch,
                        screen=si,
                        kit_ext=kit_ext,
                        stop_scheduler=False,
                        stop_motion=False,
                    )
                    # join 최소화 — 잔존은 detach (앱 정지 금지)
                    try:
                        self._reap_csv_play_thread(timeout=0.1)
                    except Exception:
                        pass
                    join_csv_play_child_workers(screen=si, timeout=0.1)
                    detach_csv_play_workers_for_screen(self, screen=si)
                    msg = (
                        f"공정만보기 {'ON' if target0w else 'OFF'} 전환 — "
                        f"CSV t≈{ck.resume_csv_sec:.1f}s"
                    )
                    print(f"{_PRINT_PREFIX} {msg}", flush=True)
                    try:
                        _post_kit_main_thread(lambda m=msg: self._log(m))
                    except Exception:
                        pass
                    target = bool(self._process_only_switch_target)
                    try:
                        ui_sp_resume = float(self._read_speed_scale())
                    except Exception:
                        ui_sp_resume = float(speed_now)
                    # 모드 전환: 진행 중 JSON 을 처음부터 다시 돌리지 않음.
                    resume_ck = CsvPlayPauseCheckpoint(
                        csv_path=ck.csv_path,
                        speed_scale=1.0 if target else ui_sp_resume,
                        process_only=target,
                        resume_csv_sec=ck.resume_csv_sec,
                        json_done=ck.json_done,
                        wall_elapsed_sec=ck.wall_elapsed_sec,
                        paused_in_json=False,
                    )
                    # resume 전에 drain 해제 — 새 lane worker 가 hold 에 영구 대기하지 않게
                    set_csv_play_mode_switch_drain(False, screen=si)
                    clear_csv_playback_stop(screen=si)
                    self._launch_live_process_only_resume(cached, resume_ck)
                    applied_mode = bool(resume_ck.process_only)
                    self._process_only_last_applied = applied_mode
                    resumed_ok = True
            except Exception as exc:
                err = f"공정만보기 실시간 전환 실패: {exc}"
                print(f"{_PRINT_PREFIX} {err}", flush=True)
                try:
                    _post_kit_main_thread(lambda m=err: self._log(m))
                except Exception:
                    pass
                abandon_csv_play_after_failed_mode_switch(screen=si)
            finally:
                set_csv_play_mode_switch_drain(False, screen=si)
                with switch_lock:
                    self._process_only_switch_pending = False
                    follow = bool(self._process_only_switch_target)
            if not resumed_ok:
                return
            # snap.process_only 는 새 Play 스레드가 올리기 전 False 일 수 있음.
            # snap 기준 follow-up 하면 방금 재개한 세션을 즉시 stop 해 진행이 멈춘다.
            if applied_mode is not None and bool(applied_mode) == bool(follow):
                print(
                    f"{_PRINT_PREFIX} 공정만보기 follow-up skip 화면{si} — "
                    f"이미 적용됨 applied={applied_mode}",
                    flush=True,
                )
                return

            def _follow_up(f: bool = follow) -> None:
                try:
                    if bool(getattr(self, "_process_only_last_applied", None)) == bool(
                        f
                    ):
                        return
                    if self._csv_play_is_active() and csv_play_session_active(
                        screen=si
                    ):
                        print(
                            f"{_PRINT_PREFIX} 공정만보기 전환 follow-up 화면{si} "
                            f"→ {f}",
                            flush=True,
                        )
                        self._try_start_live_process_only_switch(f)
                except Exception:
                    pass

            def _follow_bg() -> None:
                time.sleep(0.2)
                _follow_up()

            threading.Thread(
                target=_follow_bg,
                daemon=True,
                name=f"lam-process-only-follow-s{si}",
            ).start()

        try:
            threading.Thread(
                target=_switch_worker,
                daemon=True,
                name=f"lam-process-only-switch-s{si}",
            ).start()
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} 공정만보기 switch thread 기동 실패 화면{si}: {exc}",
                flush=True,
            )
            with switch_lock:
                self._process_only_switch_pending = False
            abandon_csv_play_after_failed_mode_switch(screen=si)

    def _launch_live_process_only_resume(
        self,
        cached: CachedCsvPlayback,
        ck: CsvPlayPauseCheckpoint,
    ) -> None:
        """모드 전환용 이어서 재생 — camera/visibility preflight는 반복하지 않는다."""
        si = int(self._screen)
        try:
            self._refresh_play_runtime()
        except Exception:
            pass
        if self._registry is None or self._scheduler is None:
            self._log(
                f"공정만보기 전환 재개 실패 — 화면{si} registry/scheduler 없음"
            )
            abandon_csv_play_after_failed_mode_switch(screen=si)
            return
        # 이전 세션 추적이 남아 있으면 detach 후 재개 (abandon 금지 — 앱 정지 방지)
        if _alive_csv_play_child_workers(screen=si) or self._csv_play_thread_alive():
            join_csv_play_child_workers(screen=si, timeout=0.5)
            detach_csv_play_workers_for_screen(self, screen=si)
        self._prepared_playback = cached
        kit_ext = getattr(self._lam_window_ref, "_kit_ext", None)
        # detach 이후 stop 해제 — 구 worker 는 무효 epoch·별도 runner 로 더 이상 진행 불가
        clear_csv_playback_stop(screen=si)
        disarm_csv_play_pause_for_resume(screen=si)

        def _on_play_ui(
            csv_t: float,
            csv_total: float,
            wall_el: float,
            wall_tot: float,
        ) -> None:
            line = self._format_play_progress_line(csv_t, csv_total, wall_el, wall_tot)
            _post_kit_main_thread(lambda: self._set_build_progress_text(line))

        def _on_timeline_highlight(active_keys: frozenset) -> None:
            keys = frozenset(active_keys)
            _post_kit_main_thread(
                lambda k=keys: self._apply_schedule_row_highlight(k)
            )

        def _worker() -> None:
            with csv_play_screen_binding(si):
                try:
                    set_csv_play_live_speed_ui_reader(
                        lambda: get_csv_play_live_speed_scale(screen=si),
                        screen=si,
                    )
                    set_csv_play_progress_ui_callback(_on_play_ui, screen=si)
                    set_csv_play_timeline_highlight_callback(
                        _on_timeline_highlight,
                        screen=si,
                    )
                    try:
                        from .lam_traffic_light_emissive import on_csv_playback_started

                        on_csv_playback_started()
                    except Exception:
                        pass
                    run_simulation_from_csv(
                        self._registry,
                        self._scheduler,
                        prepared=cached,
                        speed_scale=1.0 if ck.process_only else ck.speed_scale,
                        process_only=ck.process_only,
                        resume_from_csv_sec=ck.resume_csv_sec,
                        initial_json_done=ck.json_done,
                        reset_wafer_visibility=False,
                        wall_elapsed_offset=ck.wall_elapsed_sec,
                        skip_play_prim_hide=True,
                        paused_in_json=ck.paused_in_json,
                        play_screen=si,
                        kit_ext=kit_ext,
                    )
                except Exception as exc:
                    print(
                        f"{_PRINT_PREFIX} 화면{si} 공정만보기 전환 재생 오류: {exc}",
                        flush=True,
                    )
                    abandon_csv_play_after_failed_mode_switch(screen=si)
                finally:
                    set_csv_play_live_speed_ui_reader(None, screen=si)
                    set_csv_play_progress_ui_callback(None, screen=si)
                    set_csv_play_timeline_highlight_callback(None, screen=si)
                    clear_csv_play_timeline_highlight(screen=si)
                    if not csv_playback_stop_requested(screen=si):
                        clear_csv_play_pause_checkpoint(screen=si)
                        clear_csv_playback_stop(screen=si)
                    with self._csv_play_launch_lock:
                        if self._csv_play_thread is threading.current_thread():
                            self._csv_play_thread = None

        t = threading.Thread(
            target=_worker,
            daemon=True,
            name=f"lam-csv-play-live-mode-s{si}",
        )
        with self._csv_play_launch_lock:
            self._clear_stale_csv_play_thread_ref()
            self._csv_play_thread = t
        t.start()
        self._process_only_last_applied = bool(ck.process_only)
        done_msg = (
            f"공정만보기 {'ON' if ck.process_only else 'OFF'} 적용 완료 — "
            f"CSV t≥{ck.resume_csv_sec:.1f}s부터 계속"
        )
        _post_kit_main_thread(lambda m=done_msg: self._log(m))

    def _read_csv_prerun_export_enabled(self) -> bool:
        m = self._csv_prerun_export_model
        if m is None:
            try:
                from .lam_sim_control_defaults import CSV_PRERUN_EXPORT_JSON

                return bool(CSV_PRERUN_EXPORT_JSON)
            except Exception:
                return True
        try:
            return bool(m.get_value_as_bool())
        except Exception:
            try:
                return bool(m.as_bool)
            except Exception:
                return True

    def _read_speed_scale(self) -> float:
        """배속 UI 모델값 (공정만보기 체크와 무관 — 사용자가 설정한 일반 재생 배속)."""
        m = self._speed_model
        if m is None:
            return 1.0
        try:
            return float(max(0.1, min(20.0, float(m.get_value_as_float()))))
        except Exception:
            return 1.0

    def _playback_speed_scale_for_process_only(self, process_only: bool) -> float:
        """실제 재생에 쓸 배속 — 공정만보기일 때만 1x, 아니면 UI 배속."""
        return 1.0 if bool(process_only) else self._read_speed_scale()

    def _apply_speed_ui_change(self) -> None:
        """배속 UI 변경 → 타임라인 배속 라벨·(일반 재생 중) 라이브 세션.

        재생/Federation 등 현재 준비된 schedule 을 덮지 않는다.
        (과거: ``_refresh_csv_schedule_preview`` → combo 기본 CSV 로 타임라인이 바뀌던 버그)
        """
        in_proc_only_play = bool(
            self._read_process_only()
            and csv_play_session_active(screen=self._screen)
        )
        if not in_proc_only_play:
            try:
                self._refresh_timeline_for_speed_change()
            except Exception:
                pass
        self._apply_live_speed_during_play()

    def _refresh_timeline_for_speed_change(self) -> None:
        """배속 숫자만 반영. 현재 prepared / 표시 중 schedule 유지.

        combo 의 ``_selected_csv_path`` 로 재파싱하지 않음 — Play 중 API·다른 CSV
        타임라인이 기본 파일로 바뀌는 것을 방지.
        """
        if not self._has_timeline_ui():
            return
        sp = self._read_speed_scale()
        prepared = self._prepared_playback
        if prepared is not None and prepared.schedule:
            self._rebuild_schedule_timeline_rows(
                prepared.schedule,
                speed_scale=sp,
                rebuild_main=self._schedule_rows_stack is not None,
                rebuild_hud=self._hud_schedule_rows_stack is not None,
            )
            return
        entries = list(getattr(self, "_schedule_row_entries", None) or [])
        if entries:
            self._rebuild_schedule_timeline_rows(
                entries,
                speed_scale=sp,
                rebuild_main=self._schedule_rows_stack is not None,
                rebuild_hud=self._hud_schedule_rows_stack is not None,
            )
            return
        # 표시할 재생 schedule 이 없을 때만 combo CSV 미리보기
        self._refresh_csv_schedule_preview(fast_only=True)

    def _wire_speed_model_live_update(self) -> None:
        """Play 중 배속 필드·프리셋 변경 → 즉시 재생 속도 반영."""
        if getattr(self, "_speed_model_live_wired", False):
            return
        m = self._speed_model
        if m is None:
            return
        self._speed_model_live_wired = True

        def _on_speed_changed(*_a: Any) -> None:
            self._apply_speed_ui_change()

        for hook in ("add_value_changed_fn", "add_item_changed_fn"):
            try:
                fn = getattr(m, hook, None)
                if callable(fn):
                    fn(_on_speed_changed)
            except Exception:
                pass

    def _apply_live_speed_during_play(self) -> None:
        if self._read_process_only():
            return
        if not self._csv_play_thread_alive():
            return
        if not csv_play_session_active(screen=self._screen):
            return
        try:
            # UI 스레드에서만 모델 읽고 세션에 반영 — 워커의 ui_reader 는 세션값만 본다
            sp = set_csv_play_live_speed_scale(
                self._read_speed_scale(),
                screen=self._screen,
            )
            self._log(f"재생 배속 (즉시) = {sp:g}x")
        except Exception:
            pass

    def _set_speed_preset(self, value: float) -> None:
        m = self._speed_model
        if m is None:
            self._log("배속 UI 없음")
            return
        try:
            m.set_value(float(value))
        except Exception:
            pass
        self._log(f"재생 배속 = {value:g}x")
        self._apply_speed_ui_change()

    def _csv_build_thread_alive(self) -> bool:
        t = self._csv_build_thread
        return t is not None and t.is_alive()

    def _invalidate_playback_parse_state_for_fresh_start(self) -> None:
        """정지(초기화)·웹 start 용 — prepared 제거 + 선택 path 캐시 무효.

        다음 Play/Federation 은 반드시 재파싱. 일시정지 경로는 호출하지 않는다.
        """
        self._prepared_playback = None
        path = None
        try:
            path = self._selected_csv_path()
        except Exception:
            path = None
        try:
            n = invalidate_csv_playback_cache_for_path(path)
            if n:
                print(
                    f"{_PRINT_PREFIX} screen{self._screen} parse cache invalidate "
                    f"path={getattr(path, 'name', path)!r} removed={n}",
                    flush=True,
                )
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} screen{self._screen} cache invalidate: {exc}",
                flush=True,
            )

    def _apply_cached_timeline_ui(self, cached: CachedCsvPlayback) -> None:
        sp = self._read_speed_scale()
        self._prepared_playback = cached
        self._rebuild_schedule_timeline_rows(
            cached.schedule,
            speed_scale=sp,
            rebuild_main=self._schedule_rows_stack is not None,
            rebuild_hud=self._hud_schedule_rows_stack is not None,
        )
        try:
            self._refresh_occupancy_diag_ui(
                tuple(getattr(cached, "occupancy_diagnostics", None) or ())
            )
        except Exception:
            pass
        self._set_build_progress_text(
            f"준비 완료 (캐시) — dwell {len(cached.dwells)} · "
            f"JSON {sum(1 for e in cached.schedule if e.category != 'dwell')}건"
        )

    def _refresh_occupancy_diag_ui(self, lines: Any) -> None:
        """타임라인 아래 점유 진단 패널. 스택이 없으면(플래그 False) no-op."""
        stack = getattr(self, "_occupancy_diag_stack", None)
        if stack is None:
            return
        self._occupancy_diag_lines = tuple(lines or ())
        labels = getattr(self, "_occupancy_diag_labels", None)
        if labels is None:
            self._occupancy_diag_labels = []
            labels = self._occupancy_diag_labels
        self._clear_one_timeline_stack(stack, labels)
        try:
            import omni.ui as ui  # type: ignore
        except Exception:
            return
        try:
            with stack:
                rows = list(self._occupancy_diag_lines)
                if not rows:
                    labels.append(
                        ui.Label(
                            "(진단 없음 — 이상 없음 또는 스케줄 미준비)",
                            height=18,
                            word_wrap=True,
                            style={"color": 0xFF9AA4B2},
                        )
                    )
                else:
                    labels.append(
                        ui.Label(
                            f"진단 {len(rows)}건",
                            height=18,
                            style={"color": 0xFFE0C070},
                        )
                    )
                    for line in rows:
                        labels.append(
                            ui.Label(
                                str(line),
                                height=18,
                                word_wrap=True,
                                style={"color": 0xFFE8E8E8},
                            )
                        )
        except Exception as exc:
            try:
                self._log(f"occupancy diag UI: {exc}")
            except Exception:
                pass

    def _start_background_csv_build(self, path: Path, *, reason: str = "") -> None:
        """백그라운드에서 plan 빌드 + 캐시 (진행률만 UI 갱신, 빌드 로직은 스로틀)."""
        if self._csv_build_thread_alive():
            return
        hit = get_cached_csv_playback(path)
        if hit is not None:
            self._apply_cached_timeline_ui(hit)
            return

        # 재생 중에는 prepared 를 비우지 않음 — 공정만보기 실시간 전환이 캐시를 잃음
        if not self._preserve_prepared_playback_during_play():
            self._prepared_playback = None
        ticker = self._build_ui_ticker
        if ticker is not None:
            ticker.start(1)

        def _on_tick(done: int, total: int) -> None:
            if ticker is not None:
                ticker.set_done(done)

        def _worker() -> None:
            t_build0 = time.perf_counter()
            try:
                set_csv_playback_compact_log(True)
                dwells = load_csv_dwell_timeline(path)
                total_est = _estimate_csv_build_units(dwells)
                if ticker is not None:
                    ticker.start(total_est)
                schedule, blocks = build_csv_playback_plan(
                    dwells, progress=_ThrottledBuildProgress(total_est, _on_tick)
                    if total_est > 0
                    else None,
                )
                schedule, blocks, occ_diags = _maybe_apply_occupancy_scheduler(
                    dwells, schedule, blocks
                )
                schedule, blocks = _align_playback_timeline_to_first_json(
                    schedule, blocks
                )
                try:
                    st = path.stat()
                    mtime_ns, size = int(st.st_mtime_ns), int(st.st_size)
                except OSError:
                    mtime_ns, size = 0, 0
                cached = CachedCsvPlayback(
                    path=path,
                    mtime_ns=mtime_ns,
                    size=size,
                    config_tag=_csv_playback_config_tag(),
                    dwells=dwells,
                    schedule=schedule,
                    blocks=blocks,
                    build_ms=(time.perf_counter() - t_build0) * 1000.0,
                    occupancy_diagnostics=occ_diags,
                )
                key = _csv_cache_key(path)
                with _csv_playback_cache_lock:
                    _csv_playback_cache[key] = cached
                build_sec = time.perf_counter() - t_build0
                n_json = sum(1 for e in schedule if e.category != "dwell")

                def _done() -> None:
                    self._apply_cached_timeline_ui(cached)
                    msg = (
                        f"타임라인·캐시 빌드 완료: {path.name} | "
                        f"소요 {build_sec:.1f}s | dwell {len(dwells)} | JSON {n_json}"
                    )
                    print(f"{_PRINT_PREFIX} {msg}", flush=True)
                    self._log(msg)

                _post_kit_main_thread(_done)
            except Exception as exc:
                def _err() -> None:
                    self._set_build_progress_text(f"빌드 실패: {exc}")
                    self._set_schedule_model_text(f"타임라인 생성 실패 ({path.name}):\n{exc}")

                _post_kit_main_thread(_err)
                print(f"{_PRINT_PREFIX} 타임라인·캐시 빌드 실패: {path.name} | {exc}", flush=True)
            finally:
                if ticker is not None:
                    ticker.stop()
                set_csv_playback_compact_log(False)
                self._csv_build_thread = None

        self._csv_build_thread = threading.Thread(
            target=_worker, daemon=True, name="lam-sim-csv-build"
        )
        self._csv_build_thread.start()

    def _refresh_csv_schedule_preview(
        self,
        path: Optional[Path] = None,
        *,
        fast_only: bool = False,
        background_build: bool = False,
        rebuild_main: bool = True,
        rebuild_hud: bool = True,
    ) -> None:
        """타임라인 UI 갱신. fast_only=메타만 즉시, background_build=이후 캐시 빌드."""
        if not self._has_timeline_ui():
            return
        p = path or self._selected_csv_path()
        if p is None:
            self._rebuild_schedule_timeline_rows(
                [], rebuild_main=rebuild_main, rebuild_hud=rebuild_hud
            )
            try:
                self._refresh_occupancy_diag_ui(())
            except Exception:
                pass
            self._set_build_progress_text("(CSV 없음)")
            return
        hit = get_cached_csv_playback(p)
        if hit is None:
            # 파일 키 miss (occ 태그·API 키 등) — path 일치 캐시로 UI/prepared 유지
            hit = find_cached_csv_playback(p)
        if hit is not None:
            self._prepared_playback = hit
            self._rebuild_schedule_timeline_rows(
                hit.schedule,
                speed_scale=self._read_speed_scale(),
                rebuild_main=rebuild_main and self._schedule_rows_stack is not None,
                rebuild_hud=rebuild_hud and self._hud_schedule_rows_stack is not None,
            )
            try:
                self._refresh_occupancy_diag_ui(
                    tuple(getattr(hit, "occupancy_diagnostics", None) or ())
                )
            except Exception:
                pass
            self._set_build_progress_text(
                f"준비 완료 (캐시) — dwell {len(hit.dwells)} · "
                f"JSON {sum(1 for e in hit.schedule if e.category != 'dwell')}건"
            )
            return
        try:
            dwells = load_csv_dwell_timeline(p)
            entries = build_csv_playback_schedule_meta(dwells)
            # 재생·공정만보기 전환 중 prepared 를 지우면 "재생 캐시 없음" 실패
            if not self._preserve_prepared_playback_during_play():
                self._prepared_playback = None
            self._rebuild_schedule_timeline_rows(
                entries,
                speed_scale=self._read_speed_scale(),
                rebuild_main=rebuild_main,
                rebuild_hud=rebuild_hud,
            )
            self._set_build_progress_text(
                "(미리보기 — [타임라인 갱신] 또는 Play 시 전체 빌드)"
            )
        except Exception as exc:
            self._rebuild_schedule_timeline_rows(
                [], rebuild_main=rebuild_main, rebuild_hud=rebuild_hud
            )
            self._set_schedule_model_text(f"타임라인 생성 실패 ({p.name}):\n{exc}")
            self._set_build_progress_text(f"미리보기 실패: {exc}")
            print(f"{_PRINT_PREFIX} schedule preview error: {exc}", flush=True)
            return
        if background_build and not fast_only:
            self._start_background_csv_build(p, reason="타임라인 갱신")

    def _on_refresh_clicked(self) -> None:
        """폴더 텍스트 박스 경로에서 ``*.csv`` 목록을 다시 읽어 드롭다운 갱신."""
        self._reload_csv_list(preserve_selection=True)
        self._refresh_csv_schedule_preview(fast_only=True)

    def _on_schedule_refresh_clicked(self) -> None:
        path = self._selected_csv_path()
        if path is None:
            self._log("CSV 없음 — 타임라인을 갱신할 파일이 없습니다.")
            return
        self._refresh_csv_schedule_preview(path, background_build=True)
        self._log(f"타임라인·캐시 빌드 시작: {path.name}")

    def _on_save_sorted_csv_clicked(self) -> None:
        """선택 CSV를 eqp_start_tm 오름차순으로 정렬해 새 파일로 저장."""
        src = self._selected_csv_path()
        if src is None:
            self._log("CSV 없음 — 저장할 파일이 없습니다.")
            return
        try:
            out = self._write_sorted_csv_by_eqp_start_tm(src)
            self._log(f"정렬 CSV 저장 완료: {out.name}")
            # 같은 폴더에 파일이 생겼으므로 목록 갱신 (선택은 유지)
            self._reload_csv_list(preserve_selection=True)
        except Exception as exc:
            msg = f"정렬 CSV 저장 실패: {exc}"
            print(f"{_PRINT_PREFIX} {msg}", flush=True)
            self._log(msg)

    def _write_sorted_csv_by_eqp_start_tm(self, src: Path) -> Path:
        """원본 CSV 행을 보존한 채 eqp_start_tm 기준 정렬하여 같은 폴더에 저장."""
        import csv as _csv

        p = Path(src).resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))

        with p.open("r", encoding="utf-8", newline="") as f:
            reader = _csv.DictReader(f)
            fieldnames = list(reader.fieldnames or ())
            if not fieldnames:
                raise ValueError("CSV header 가 비었습니다.")
            rows = list(reader)

        # 키 계산: eqp_start_iso 우선(있으면), 없으면 eqp_start_tm
        def _key(raw: dict) -> float:
            return _parse_csv_time_field(raw, "eqp_start_tm", "eqp_start_iso")

        rows.sort(key=lambda r: (_key(r), str(r.get("lot_id") or ""), str(r.get("cassette_slot") or ""), str(r.get("module_nm") or "")))

        out_base = p.with_name(f"{p.stem}_sorted_by_eqp_start_tm{p.suffix}")
        out = out_base
        for i in range(1, 1000):
            if not out.exists():
                break
            out = p.with_name(f"{p.stem}_sorted_by_eqp_start_tm_{i}{p.suffix}")
        if out.exists():
            raise FileExistsError(f"output already exists: {out}")

        with out.open("w", encoding="utf-8", newline="") as f:
            writer = _csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
        return out

    def _csv_play_thread_alive(self) -> bool:
        t = self._csv_play_thread
        return t is not None and t.is_alive()

    def _clear_stale_csv_play_thread_ref(self) -> None:
        """``_csv_play_launch_lock`` 보유 중 호출 — 죽은 play 스레드 참조만 제거."""
        t = self._csv_play_thread
        if t is not None and not t.is_alive():
            self._csv_play_thread = None

    def _normalize_csv_play_thread_state(self) -> None:
        """종료된 play 스레드·잔여 stop 플래그 정리 — 재Play·live 전환 전."""
        si = int(self._screen)
        with self._csv_play_launch_lock:
            self._clear_stale_csv_play_thread_ref()
        if not self._csv_play_thread_alive():
            join_csv_play_child_workers(screen=si, timeout=0.05)
            if csv_playback_stop_requested(screen=si) and not csv_play_pause_armed(
                screen=si
            ):
                clear_csv_playback_stop(screen=si)

    def _reap_csv_play_thread(self, *, timeout: float = 30.0) -> bool:
        """중지 요청된 재생 스레드·프리런 빌드·자식 worker join. True = 새 Play 가능."""
        si = int(self._screen)
        t = self._csv_play_thread
        main_ok = True
        if t is not None:
            if not t.is_alive():
                self._csv_play_thread = None
            else:
                try:
                    t.join(timeout=max(0.05, float(timeout)))
                except Exception:
                    pass
                if not t.is_alive():
                    self._csv_play_thread = None
                else:
                    main_ok = False
        bt = self._csv_build_thread
        build_ok = True
        if bt is not None:
            if not bt.is_alive():
                self._csv_build_thread = None
            else:
                try:
                    bt.join(timeout=max(0.05, float(timeout)))
                except Exception:
                    pass
                if not bt.is_alive():
                    self._csv_build_thread = None
                else:
                    build_ok = False
        children_ok = join_csv_play_child_workers(screen=si, timeout=timeout)
        return main_ok and build_ok and children_ok

    def _schedule_reap_csv_play_thread_after_stop(
        self,
        *,
        timeout: float = 45.0,
        done_log: str = "",
    ) -> None:
        """일시정지·정지 후 백그라운드에서 재생 스레드 join — UI는 즉시 반환."""

        def _worker() -> None:
            ok = self._reap_csv_play_thread(timeout=timeout)
            if done_log and ok:
                try:
                    _post_kit_main_thread(lambda: self._log(done_log))
                except Exception:
                    pass

        threading.Thread(
            target=_worker,
            daemon=True,
            name="lam-csv-play-reap",
        ).start()

    def _on_csv_pause_clicked(self) -> None:
        """일시정지 — 진행 위치 저장, 웨이퍼 visibility 는 유지."""
        self._refresh_play_runtime()
        if not self._csv_play_thread_alive():
            self._log("재생 중이 아닙니다.")
            return
        path = self._selected_csv_path()
        if path is None:
            self._log("CSV 경로 없음")
            return
        process_only = self._read_process_only()
        sp = self._playback_speed_scale_for_process_only(process_only)
        path_s = str(path)
        registry = self._registry
        scheduler = self._scheduler
        kit_ext = getattr(self._lam_window_ref, "_kit_ext", None)
        si = int(self._screen)
        self._log("일시정지 요청…")

        def _pause_worker() -> None:
            try:
                with csv_play_screen_binding(si):
                    ck = save_csv_play_pause_checkpoint(
                        csv_path=path_s,
                        speed_scale=sp,
                        process_only=process_only,
                        screen=si,
                    )
                    request_pause_csv_playback(
                        registry,
                        scheduler,
                        screen=si,
                        kit_ext=kit_ext,
                    )
                    try:
                        from .lam_traffic_light_emissive import (
                            on_csv_playback_paused_or_stopped,
                        )

                        on_csv_playback_paused_or_stopped()
                    except Exception:
                        pass
                    clear_csv_play_timeline_highlight(screen=si)
                    json_note = " · JSON 처음부터" if ck.paused_in_json else ""
                    msg = (
                        f"일시정지 — CSV t≈{ck.resume_csv_sec:.1f}s · "
                        f"실경과 {ck.wall_elapsed_sec:.0f}s{json_note}"
                    )
                    _post_kit_main_thread(lambda m=msg: self._log(m))
                    ok = self._reap_csv_play_thread(timeout=45.0)
                    if ok:
                        _post_kit_main_thread(
                            lambda: self._log(
                                "일시정지 완료 — [재생] 으로 이어서 Play 하세요."
                            )
                        )
            except Exception as exc:
                err = f"일시정지 실패: {exc}"
                _post_kit_main_thread(lambda m=err: self._log(m))

        threading.Thread(
            target=_pause_worker,
            daemon=True,
            name=f"lam-csv-pause-s{si}",
        ).start()

    def _on_csv_stop_reset_clicked(
        self,
        on_complete: Optional[Callable[[], None]] = None,
    ) -> None:
        """정지(초기화).

        ``on_complete``는 재생 worker 종료와 stage 초기화가 모두 끝난 뒤
        Kit main thread에서 호출된다. 화면 표시 전환이 이전 위치를 노출하지
        않도록 하기 위한 완료 신호다.

        파싱 캐시·prepared 도 무효화한다 — 다음 Play/웹 start 는 재파싱.
        (일시정지는 이 경로를 쓰지 않으므로 이어서 재생 유지.)
        """
        clear_csv_play_pause_checkpoint(screen=self._screen)
        self._reset_timeline_playback_highlight_ui()
        self._play_stop_reset_in_progress = True
        # prepared + path 캐시 무효 — 정지 후 재시작은 새 파싱
        self._invalidate_playback_parse_state_for_fresh_start()
        try:
            from .lam_csv_screen_runtime import (
                resolve_csv_screen_runtime,
                schedule_play_stop_perspective_restore_for_screen,
            )

            rt = resolve_csv_screen_runtime(
                self._lam_window_ref,
                self._screen,
                csv_window=self,
                require_aux=False,
            )
            if rt is not None:
                schedule_play_stop_perspective_restore_for_screen(rt)
        except Exception:
            pass
        try:
            from .lam_viewport_foup_status_3d import reset_foup_play_session

            reset_foup_play_session(screen=self._screen)
        except Exception:
            pass
        # 재생·프리런(빌드) 중이면 모두 stop
        if self._csv_play_thread_alive() or self._csv_build_thread_alive():
            request_stop_csv_playback(
                self._registry,
                self._scheduler,
                screen=self._screen,
                kit_ext=getattr(self._lam_window_ref, "_kit_ext", None),
            )
        # 일반 정지 버튼만 전역 신호등 상태를 갱신한다.
        # 화면 표시 전 초기화(on_complete 지정)는 다른 화면 재생에 영향 금지.
        if on_complete is None:
            try:
                from .lam_traffic_light_emissive import on_csv_playback_paused_or_stopped

                on_csv_playback_paused_or_stopped()
            except Exception:
                pass
        self._log(
            "정지(초기화) 시작 — Z/팔 TBS→0, FOUP 75 show, "
            "나머지 슬롯·팔 wafer hide (백그라운드)"
        )

        def _worker() -> None:
            reset_done = False
            with csv_play_screen_binding(self._screen):
                try:
                    # 기존 재생이 완전히 끝난 뒤 초기화한다. 재생 write와 reset 경합 방지.
                    if not self._reap_csv_play_thread(timeout=60.0):
                        raise RuntimeError("기존 CSV 재생 worker 종료 시간 초과")
                    kit_ext = getattr(self._lam_window_ref, "_kit_ext", None)
                    cn = None
                    if self._screen > 1 and kit_ext is not None:
                        try:
                            from .lam_csv_play_screen import usd_context_name_for_screen

                            cn = usd_context_name_for_screen(kit_ext, self._screen)
                        except Exception:
                            cn = None
                    reset_csv_play_stop_initial_state(
                        self._registry,
                        self._scheduler,
                        usd_context_name=cn,
                        screen=self._screen,
                    )
                    # 정지(초기화)는 「prim숨김」 체크 상태를 강제로 바꾸지 않는다.
                    # (STARTUP_CHECK_PLAY_PRIM_HIDE=False 인데 기동 초기화가
                    # 체크를 ON 으로 뒤집던 문제 — play_stop_reset phase 가
                    # 현재 체크 상태를 읽어 숨김 유지/복원을 알아서 분기한다.)
                    try:
                        if cn:
                            from .lam_csv_screen_runtime import capture_csv_overlay_settings
                            from .lam_play_prim_hide import (
                                apply_play_prim_hide_phase_for_context,
                            )

                            hide_checked = bool(
                                capture_csv_overlay_settings(self).get("play_prim_hide")
                            )
                            apply_play_prim_hide_phase_for_context(
                                cn,
                                "play_stop_reset",
                                prim_hide_checked=hide_checked,
                            )
                        else:
                            from .lam_play_prim_hide import apply_play_prim_hide_phase

                            apply_play_prim_hide_phase("play_stop_reset")
                    except Exception as exc:
                        print(
                            f"{_PRINT_PREFIX} play prim hide (stop_reset): {exc}",
                            flush=True,
                        )
                    try:
                        from .lam_csv_screen_runtime import (
                            resolve_csv_screen_runtime,
                            restore_play_stop_perspective_for_screen,
                        )

                        rt = resolve_csv_screen_runtime(
                            self._lam_window_ref,
                            self._screen,
                            csv_window=self,
                            require_aux=False,
                        )
                        if rt is not None:
                            restore_play_stop_perspective_for_screen(rt)
                    except Exception as exc:
                        print(
                            f"{_PRINT_PREFIX} perspective restore (stop_reset): {exc}",
                            flush=True,
                        )
                    self._log(
                        "정지(초기화) 완료 — 위치(TBS)·visibility 복원 (콘솔 [LAM/Sim] 확인)"
                    )
                    reset_done = True
                except Exception as exc:
                    err = f"정지(초기화) 오류: {exc}"
                    print(f"{_PRINT_PREFIX} {err}", flush=True)
                    self._log(err)
                finally:
                    self._play_stop_reset_in_progress = False
                    if reset_done:
                        clear_csv_playback_stop(screen=self._screen)
                    if callable(on_complete):
                        try:
                            _post_kit_main_thread(on_complete)
                        except Exception:
                            pass

        threading.Thread(
            target=_worker,
            daemon=True,
            name="lam-csv-play-stop-reset",
        ).start()

    def _on_csv_stop_clicked(self) -> None:
        """하위 호환 — 일시정지와 동일."""
        self._on_csv_pause_clicked()

    def _on_play_clicked(self) -> None:
        self._normalize_csv_play_thread_state()
        # 이전 preflight 레이스로 남은 switch pending 정리
        if self._process_only_switch_pending and not csv_play_session_active(
            screen=self._screen
        ):
            print(
                f"{_PRINT_PREFIX} Play 클릭 — 잔여 공정만보기 pending 해제 "
                f"화면{self._screen}",
                flush=True,
            )
            self._process_only_switch_pending = False
        if not self._refresh_play_runtime():
            if self._screen > 1:
                self._log(
                    f"화면 {self._screen} 재생 불가 — 분할 viewport·USD hydrate 후 다시 시도하세요."
                )
                return
        # 재생 중 공정만보기 실시간 전환 hook — HUD/창 UI 없이 Play만 쓴 경우에도 연결
        try:
            self.ensure_playback_models()
        except Exception:
            pass
        if not self._csv_paths:
            self._log("CSV 없음 — lam/csv 에 파일을 추가하세요.")
            return
        path = self._selected_csv_path()
        if path is None:
            self._log("CSV 경로 없음")
            return
        process_only = self._read_process_only()
        self._process_only_last_applied = bool(process_only)
        sp = self._playback_speed_scale_for_process_only(process_only)
        pause_ck = match_csv_play_pause_checkpoint(
            str(path),
            speed_scale=sp,
            process_only=process_only,
            screen=self._screen,
        )
        # UI(main) 스레드에서 join 하면 재생 스레드의 dispatch_main_wait 와 deadlock →
        # 재생 중 이중 Play 는 즉시 무시. 이어서 Play 는 짧게 reap 시도.
        with self._csv_play_launch_lock:
            if self._csv_play_thread_alive():
                stopping = (
                    csv_playback_stop_requested(screen=self._screen)
                    or csv_play_pause_armed(screen=self._screen)
                )
                if pause_ck is not None and stopping:
                    if self._reap_csv_play_thread(timeout=2.0):
                        pass
                    else:
                        self._log(
                            "일시정지·정지 처리 중입니다 — "
                            "잠시 후 [재생]을 다시 눌러 주세요."
                        )
                        self._schedule_reap_csv_play_thread_after_stop(
                            timeout=45.0,
                            done_log="",
                        )
                        return
                elif stopping:
                    self._log(
                        "일시정지·정지 처리 중입니다 — "
                        "잠시 후 [재생]을 다시 눌러 주세요."
                    )
                    self._schedule_reap_csv_play_thread_after_stop(
                        timeout=45.0,
                        done_log="",
                    )
                    return
                else:
                    self._log(
                        "이미 재생 중 — [일시정지] 후 Play(이어서) 또는 [정지(초기화)] 하세요."
                    )
                    return
        resume_from = 0.0
        initial_json_done = 0
        reset_wafer = True
        wall_elapsed_offset = 0.0
        resume_from_pause = pause_ck is not None
        paused_in_json = False
        if pause_ck is not None:
            resume_from = float(pause_ck.resume_csv_sec)
            initial_json_done = int(pause_ck.json_done)
            wall_elapsed_offset = float(pause_ck.wall_elapsed_sec)
            paused_in_json = bool(pause_ck.paused_in_json)
            reset_wafer = False
            clear_csv_playback_stop(screen=self._screen)
            disarm_csv_play_pause_for_resume(screen=self._screen)
        else:
            clear_csv_play_pause_checkpoint(screen=self._screen)
            # 정지(초기화) 이후 — prepared/캐시 쓰지 않고 재파싱
            clear_csv_playback_stop(screen=self._screen)
            self._invalidate_playback_parse_state_for_fresh_start()
        if not resume_from_pause:
            try:
                from .lam_play_start_sequence import run_play_start_request_standby

                run_play_start_request_standby(
                    self._lam_window_ref,
                    self._screen,
                    csv_window=self,
                )
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} play start standby failed screen{self._screen}: {exc}",
                    flush=True,
                )
        if pause_ck is None:
            prepared = None
        else:
            prepared = self._prepared_playback
            if prepared is None or prepared.path.resolve() != path.resolve():
                prepared = get_cached_csv_playback(path)
        if prepared is not None:
            self._apply_cached_timeline_ui(prepared)
        n_items = len(prepared.schedule) if prepared is not None else 0
        n_json = (
            sum(1 for e in prepared.schedule if e.category != "dwell")
            if prepared is not None
            else 0
        )
        mode_ko = "공정만보기·배속1x" if process_only else f"배속 {sp:g}x"
        resume_ko = ""
        if pause_ck is not None:
            resume_ko = (
                f" · 이어서 t≥{resume_from:.1f}s · 실경과 {wall_elapsed_offset:.0f}s"
                + (" · JSON 처음부터" if pause_ck.paused_in_json else "")
            )
        self._log(
            f"Play: {path.name} — {mode_ko}, "
            f"스케줄 {n_items}항목 (JSON {n_json})"
            + resume_ko
            + ("" if resume_from_pause else " · 프리런 후 재생")
            + ("" if prepared is not None else " · 프리런 대기")
        )

        def _on_play_ui(csv_t: float, csv_total: float, wall_el: float, wall_tot: float) -> None:
            line = self._format_play_progress_line(csv_t, csv_total, wall_el, wall_tot)

            def _ui() -> None:
                self._set_build_progress_text(line)

            _post_kit_main_thread(_ui)

        def _on_timeline_highlight(active_keys: frozenset) -> None:
            keys = frozenset(active_keys)
            _post_kit_main_thread(
                lambda k=keys: self._apply_schedule_row_highlight(k)
            )

        def _worker() -> None:
            with csv_play_screen_binding(self._screen):
                nonlocal resume_from, initial_json_done, reset_wafer, wall_elapsed_offset
                consumed_pause_resume = resume_from_pause
                try:
                    if resume_from_pause:
                        clear_csv_playback_stop(screen=self._screen)
                        disarm_csv_play_pause_for_resume(screen=self._screen)
                    else:
                        clear_csv_playback_stop(screen=self._screen)
                    nonlocal prepared
                    set_csv_play_live_speed_ui_reader(
                        lambda: get_csv_play_live_speed_scale(screen=self._screen),
                        screen=self._screen,
                    )
                    set_csv_play_progress_ui_callback(
                        _on_play_ui,
                        screen=self._screen,
                    )
                    set_csv_play_timeline_highlight_callback(
                        _on_timeline_highlight,
                        screen=self._screen,
                    )
                    if prepared is None:
                        from .lam_csv_prerun_playback import run_csv_prerun_build

                        ticker = self._build_ui_ticker

                        def _on_tick(done: int, total: int) -> None:
                            if csv_playback_stop_requested(screen=self._screen):
                                raise _CsvPlayStopRequested()
                            if ticker is not None:
                                ticker.set_done(done)

                        set_csv_playback_compact_log(True)
                        t_build0 = time.perf_counter()
                        try:
                            if csv_playback_stop_requested(screen=self._screen):
                                return
                            dwells = load_csv_dwell_timeline(path)
                            total_est = _estimate_csv_build_units(dwells)
                            if ticker is not None:
                                ticker.start(total_est)
                            progress = (
                                _ThrottledBuildProgress(total_est, _on_tick)
                                if total_est > 0
                                else None
                            )
                            prepared, _prerun = run_csv_prerun_build(
                                path,
                                screen=self._screen,
                                progress_tick=(progress.tick if progress else None),
                                export_enabled=self._read_csv_prerun_export_enabled(),
                            )
                            build_sec = time.perf_counter() - t_build0
                            print(
                                f"{_PRINT_PREFIX} CSV 프리런 완료: {path.name} | "
                                f"항목 {len(_prerun.items)} · "
                                f"총 t {_prerun.final_csv_time_sec:.1f}s · "
                                f"소요 {build_sec:.1f}s",
                                flush=True,
                            )
                            self._post_apply_cached_timeline_ui(prepared, wait=True)
                        except _CsvPlayStopRequested:
                            return
                        finally:
                            if ticker is not None:
                                ticker.stop()
                            set_csv_playback_compact_log(False)

                    kit_ext = None
                    lam = self._lam_window_ref
                    if lam is not None:
                        kit_ext = getattr(lam, "_kit_ext", None)

                    if not resume_from_pause:
                        # 화면1·2 공통 preflight (viewport/context 만 다름)
                        preflight_ok = True
                        try:
                            from .lam_csv_screen_runtime import (
                                resolve_csv_screen_runtime,
                                run_csv_screen_play_preflight,
                            )

                            rt = resolve_csv_screen_runtime(
                                self._lam_window_ref,
                                self._screen,
                                csv_window=self,
                                require_aux=(self._screen > 1),
                            )
                            if rt is None and self._screen > 1:
                                preflight_ok = False
                            elif rt is not None:
                                preflight_ok = bool(run_csv_screen_play_preflight(rt))
                            elif self._screen <= 1:
                                from .lam_play_start_sequence import (
                                    run_play_start_preflight,
                                )

                                preflight_ok = bool(
                                    run_play_start_preflight(resume_from_pause=False)
                                )
                        except Exception as exc:
                            print(
                                f"{_PRINT_PREFIX} play start preflight "
                                f"screen{self._screen}: {exc}",
                                flush=True,
                            )
                            preflight_ok = False
                        if not preflight_ok:
                            msg = (
                                f"화면 {self._screen} Play 시작 전처리 중단 — "
                                "정지·일시정지 직후이거나 viewport/USD 미준비면 "
                                "잠시 후 다시 [재생] 하세요."
                            )
                            print(f"{_PRINT_PREFIX} {msg}", flush=True)
                            _post_kit_main_thread(lambda: self._log(msg))
                            return

                    try:
                        from .lam_traffic_light_emissive import on_csv_playback_started

                        on_csv_playback_started()
                    except Exception:
                        pass

                    if csv_playback_stop_requested(screen=self._screen):
                        msg = "Play 취소 — 중지 플래그가 남아 있습니다. [재생]을 다시 눌러 주세요."
                        print(f"{_PRINT_PREFIX} {msg}", flush=True)
                        _post_kit_main_thread(lambda: self._log(msg))
                        return

                    if prepared is not None:
                        self._prepared_playback = prepared

                    # preflight 중 공정만보기 체크 → 여기서 최종 반영 (화면1/2 동일)
                    nonlocal process_only, sp
                    deferred_po = getattr(
                        self, "_process_only_deferred_for_start", None
                    )
                    ui_po = bool(self._read_process_only())
                    if deferred_po is not None:
                        process_only = bool(deferred_po)
                        self._process_only_deferred_for_start = None
                    else:
                        process_only = ui_po
                    if process_only:
                        sp = 1.0
                    else:
                        sp = self._playback_speed_scale_for_process_only(False)
                    # preflight 레이스로 잠긴 switch pending 해제
                    if self._process_only_switch_pending and not csv_play_session_active(
                        screen=self._screen
                    ):
                        print(
                            f"{_PRINT_PREFIX} 공정만보기 pending clear 화면"
                            f"{self._screen} (Play 진입 전)",
                            flush=True,
                        )
                        self._process_only_switch_pending = False
                    print(
                        f"{_PRINT_PREFIX} Play 진입 화면{self._screen} "
                        f"process_only={process_only} speed={sp:g}x "
                        f"(ui={ui_po} deferred_was="
                        f"{deferred_po is not None})",
                        flush=True,
                    )

                    run_simulation_from_csv(
                        self._registry,
                        self._scheduler,
                        csv_path=str(path),
                        speed_scale=sp,
                        prepared=prepared,
                        process_only=process_only,
                        resume_from_csv_sec=resume_from,
                        initial_json_done=initial_json_done,
                        reset_wafer_visibility=reset_wafer,
                        wall_elapsed_offset=wall_elapsed_offset,
                        skip_play_prim_hide=True,
                        paused_in_json=paused_in_json,
                        play_screen=self._screen,
                        kit_ext=kit_ext,
                    )
                except Exception as exc:
                    print(f"{_PRINT_PREFIX} CSV Play 오류: {exc}", flush=True)
                finally:
                    try:
                        from .lam_traffic_light_emissive import on_csv_playback_paused_or_stopped

                        on_csv_playback_paused_or_stopped()
                    except Exception:
                        pass
                    set_csv_play_live_speed_ui_reader(None, screen=self._screen)
                    set_csv_play_progress_ui_callback(None, screen=self._screen)
                    set_csv_play_timeline_highlight_callback(None, screen=self._screen)
                    clear_csv_play_timeline_highlight(screen=self._screen)
                    # 일시정지로 멈춘 경우 체크포인트 유지. 정상 종료 시에만 삭제.
                    if consumed_pause_resume and csv_playback_stop_requested(
                        screen=self._screen
                    ):
                        pass
                    elif not csv_playback_stop_requested(screen=self._screen):
                        clear_csv_play_pause_checkpoint(screen=self._screen)
                        clear_csv_playback_stop(screen=self._screen)

                    def _ui_clear() -> None:
                        self._apply_schedule_row_highlight(frozenset())

                    _post_kit_main_thread(_ui_clear)
                    with self._csv_play_launch_lock:
                        if self._csv_play_thread is threading.current_thread():
                            self._csv_play_thread = None

        play_thread = threading.Thread(
            target=_worker, daemon=True, name="lam-sim-csv-play"
        )
        with self._csv_play_launch_lock:
            self._clear_stale_csv_play_thread_ref()
            if self._csv_play_thread_alive():
                self._log(
                    "재생 시작이 겹쳤습니다 — 잠시 후 [재생]을 다시 눌러 주세요."
                )
                return
            self._csv_play_thread = play_thread
        play_thread.start()

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
    dwells = load_csv_dwell_timeline(path)
    for i, d in enumerate(dwells[:limit]):
        print(
            f"{_PRINT_PREFIX} [{i}] lot={d.lot_id!r} foup={d.foup_index} cassette={d.cassette_slot} "
            f"slot={d.slot_key} [{d.start_sec:.3f},{d.end_sec:.3f}) module={d.module_nm!r}",
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
    "get_lam_csv_dir",
    "list_lam_csv_paths",
    "list_csv_paths_in_directory",
    "build_default_module_nm_to_slot_key",
    "rebuild_module_nm_slot_mapping",
    "parse_module_nm_to_slot_key",
    "load_csv_dwell_timeline",
    "build_csv_playback_steps_from_dwells",
    "build_csv_playback_plan",
    "build_csv_timed_playback_blocks",
    "build_csv_playback_schedule",
    "format_csv_playback_schedule",
    "preview_csv_playback_schedule",
    "apply_csv_play_initial_wafer_visibility",
    "apply_csv_play_initial_wafer_visibility_for_screen",
    "apply_csv_play_initial_wafer_visibility_on_stage",
    "collect_non_atm_first_wafer_inits",
    "NonAtmFirstWaferInit",
    "reset_csv_play_stop_initial_state",
    "collect_csv_play_motion_reset_prim_paths",
    "apply_material_binding_to_prim",
    "start_csv_play_material_binding_test",
    "run_csv_timed_playback",
    "CsvPlayPauseCheckpoint",
    "request_stop_csv_playback",
    "request_pause_csv_playback",
    "stop_and_reap_csv_play_worker",
    "clear_csv_play_pause_checkpoint",
    "get_csv_play_pause_checkpoint",
    "csv_play_pause_armed",
    "save_csv_play_pause_checkpoint",
    "match_csv_play_pause_checkpoint",
    "clear_csv_playback_stop",
    "csv_playback_stop_requested",
    "set_csv_playback_compact_log",
    "is_csv_playback_compact_log",
    "set_csv_play_progress_ui_callback",
    "set_csv_play_timeline_highlight_callback",
    "register_csv_play_timeline_window",
    "unregister_csv_play_timeline_window",
    "clear_csv_play_timeline_highlight",
    "get_csv_play_live_status_state",
    "get_csv_play_live_status_wafer",
    "format_csv_playback_schedule_row",
    "is_csv_bulk_build_active",
    "CachedCsvPlayback",
    "get_cached_csv_playback",
    "find_cached_csv_playback",
    "clear_csv_playback_cache",
    "invalidate_csv_playback_cache_for_path",
    "prepare_csv_playback",
    "build_and_cache_csv_playback",
    "build_csv_playback_schedule_meta",
    "CsvPlaybackScheduleEntry",
    "CsvTimedPlaybackBlock",
    "build_lot_id_to_foup_index",
    "normalize_csv_timeline",
    "normalize_dwell_timeline",
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
    # python -m morph.lam_control_1.simulation_play  (repo PYTHONPATH 설정 시)
    import sys

    p = sys.argv[1] if len(sys.argv) > 1 else None
    dwells = dry_run_print_dwells(p)
    log_virtual_timeline_from_dwells(dwells)
