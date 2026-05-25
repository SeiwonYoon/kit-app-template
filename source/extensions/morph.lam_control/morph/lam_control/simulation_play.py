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
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
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
    for i in (1, 2):
        m[f"AirLock1-iSlot{i}"] = f"airlock1_{i}"
        m[f"AirLock2-iSlot{i}"] = f"airlock2_{i}"
        m[f"AirLock2-oSlot{i}"] = f"airlock2_{i}"
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


def set_csv_playback_compact_log(enabled: bool) -> None:
    """CSV Play 중 상세 로그 억제 (LAM/EVENT, LAM/SEQ, dwell 타임라인 등)."""
    global _csv_play_compact_log
    _csv_play_compact_log = bool(enabled)


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


def _csv_playback_config_tag() -> str:
    return f"vtm_swap={int(VTM_END_EFFECTOR_SWAP_HANDS)}"


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


def clear_csv_playback_cache() -> None:
    with _csv_playback_cache_lock:
        _csv_playback_cache.clear()


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


# FOUP pick 직후 CSV에 없는 Aligner 전처리 (현장 규칙, 합성 타임라인만).
# 시각 = 투어 FOUP pick 의 CSV t(``first.start_sec``) + 아래 오프셋 [s].
FOUP_PICK_SYNTH_ALIGNER_PLACE_DELAY_SEC: float = 2.5
FOUP_PICK_SYNTH_ALIGNER_PICK_DELAY_SEC: float = 7.5

_SCHEDULE_CATEGORY_ORDER: Dict[str, int] = {
    "pick": 0,
    "aligner_place": 1,
    "aligner_pick": 2,
    "transfer": 3,
    "place": 4,
    "dwell": 5,
}

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
    """``eqp_start_tm`` 순 **lot_id 최초 등장** → foup1, foup2, foup3 (prompt1 §332-1)."""
    ordered = sorted(rows, key=lambda r: (r.eqp_start_tm, r.cassette_slot, r.module_nm))
    out: Dict[str, int] = {}
    n = 0
    for r in ordered:
        lid = (r.lot_id or "").strip() or f"__anon_cassette_{r.cassette_slot}"
        if lid not in out:
            n += 1
            out[lid] = n
    return out


def normalize_csv_timeline(rows: List[ParsedCsvRow]) -> List[ParsedCsvRow]:
    """전역 타임라인: 최소 ``eqp_start_tm`` = 0, ``process_tm`` 분→초 보정(필요 시)."""
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
    """EAP CSV 전체 파이프라인: 읽기 → t=0 정규화 → lot→foup → dwell (시간순 정렬)."""
    raw = normalize_csv_timeline(read_csv_rows(csv_path))
    lot_to_foup = build_lot_id_to_foup_index(raw)
    dwells = sort_dwells_for_playback(rows_to_dwell_records(raw, lot_to_foup))
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
    return build_steps_for_event(
        event,
        slot_number=num,
        vtm_ee_swap=VTM_END_EFFECTOR_SWAP_HANDS,
    )


def build_aligner_after_foup_pick_steps(pick_or_place: str) -> LamSimJsonSteps:
    """FOUP pick 직후 합성 Aligner 공정 — ``atm_aligner_place`` / ``atm_aligner_pick``."""
    from .lam_event_sequences import build_steps_for_event

    po = (pick_or_place or "pick").strip().lower()
    if po not in ("pick", "place"):
        raise ValueError(f"pick_or_place must be 'pick' or 'place' (got {pick_or_place!r})")
    event = f"atm_aligner_{po}"
    return build_steps_for_event(
        event,
        slot_number=None,
        vtm_ee_swap=VTM_END_EFFECTOR_SWAP_HANDS,
    )


def _aligner_exec_hint(pick_or_place: str) -> str:
    event = f"atm_aligner_{(pick_or_place or 'pick').strip().lower()}"
    po_ko = "픽업(pick)" if event.endswith("_pick") else "내려놓기(place)"
    return f"build_steps_for_event({event!r})  →  lam_sim_actions.{event}()  [{po_ko}]"


def _aligner_schedule_entry(
    *,
    time_sec: float,
    pick_or_place: str,
    foup_index: int,
    cassette_slot: int,
    lot_id: str,
    anchor_time_sec: float,
    steps: LamSimJsonSteps,
) -> CsvPlaybackScheduleEntry:
    po = (pick_or_place or "pick").strip().lower()
    event = f"atm_aligner_{po}"
    category = "aligner_place" if po == "place" else "aligner_pick"
    _ev, json_path = _schedule_entry_json_fields(event)
    place_t = float(anchor_time_sec) + FOUP_PICK_SYNTH_ALIGNER_PLACE_DELAY_SEC
    pick_t = float(anchor_time_sec) + FOUP_PICK_SYNTH_ALIGNER_PICK_DELAY_SEC
    if po == "place":
        delay_ko = f"FOUP pick(t={anchor_time_sec:.3f}s) 후 +{FOUP_PICK_SYNTH_ALIGNER_PLACE_DELAY_SEC:g}s"
    else:
        delay_ko = (
            f"FOUP pick(t={anchor_time_sec:.3f}s) 후 "
            f"+{FOUP_PICK_SYNTH_ALIGNER_PICK_DELAY_SEC:g}s"
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
        meaning_ko=(
            "EAP CSV 에는 없지만, FOUP 에서 집은 웨이퍼를 Aligner 에 잠시 맡겼다가 "
            "다시 ATM 팔로 집어 오는 전처리 공정."
        ),
        exec_ko=_aligner_exec_hint(po),
        step_count=len(steps),
        event_name=event,
        json_path=json_path,
    )


def _aligner_schedule_entry_meta(
    *,
    time_sec: float,
    pick_or_place: str,
    foup_index: int,
    cassette_slot: int,
    lot_id: str,
    anchor_time_sec: float,
) -> CsvPlaybackScheduleEntry:
    po = (pick_or_place or "pick").strip().lower()
    event = f"atm_aligner_{po}"
    category = "aligner_place" if po == "place" else "aligner_pick"
    _ev, json_path = _schedule_entry_json_fields(event)
    ent = _aligner_schedule_entry(
        time_sec=time_sec,
        pick_or_place=po,
        foup_index=foup_index,
        cassette_slot=cassette_slot,
        lot_id=lot_id,
        anchor_time_sec=anchor_time_sec,
        steps=[],
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
        json_path=json_path,
    )


def _append_aligner_after_foup_pick(
    *,
    schedule: List[CsvPlaybackScheduleEntry],
    blocks: Optional[List[CsvTimedPlaybackBlock]],
    anchor_time_sec: float,
    foup_index: int,
    cassette_slot: int,
    lot_id: str,
    progress: Optional[_ThrottledBuildProgress] = None,
) -> None:
    """FOUP pick 시각 기준 합성 aligner place → pick (CSV 파일은 변경하지 않음)."""
    place_t = float(anchor_time_sec) + FOUP_PICK_SYNTH_ALIGNER_PLACE_DELAY_SEC
    pick_t = float(anchor_time_sec) + FOUP_PICK_SYNTH_ALIGNER_PICK_DELAY_SEC
    for po, t_sec, label in (
        ("place", place_t, "aligner_place"),
        ("pick", pick_t, "aligner_pick"),
    ):
        try:
            if blocks is not None:
                steps = build_aligner_after_foup_pick_steps(po)
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
                    )
                )
        except Exception as exc:
            _lam_sim_log_build("csv_tour", f"Aligner {po} skip: {exc}")
        if progress is not None:
            progress.tick(1)


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
            "투어 마지막 dwell 이 AtmArm 이고 다음 CSV 행이 없음 → FOUP 에 place."
        ),
        meaning_ko="공정 투어 종료 — 웨이퍼를 FOUP 슬롯에 되돌림.",
        exec_ko=_foup_exec_hint(foup_index, cassette_slot, "place"),
        step_count=len(steps),
        event_name=event,
        json_path=json_path,
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
            f"투어 마지막 dwell 이 AtmArm — FOUP{foup_index} 슬롯 {cassette_slot} 에 place."
        ),
        meaning_ko="공정 투어 종료 — ATM 팔에서 FOUP 슬롯으로 반환.",
        exec_ko=_foup_exec_hint(foup_index, cassette_slot, "place"),
        step_count=_event_step_count_estimate(event),
        event_name=event,
        json_path=json_path,
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
    )


def build_csv_playback_schedule_meta(
    dwells: List[DwellRecord],
) -> List[CsvPlaybackScheduleEntry]:
    """스텝 조립 없이 타임라인 메타만 (미리보기·캐시 miss 시 UI용, 빠름)."""
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
    return _stamp_schedule_row_ids(schedule)


def build_csv_playback_plan(
    dwells: List[DwellRecord],
    *,
    progress: Optional[_ThrottledBuildProgress] = None,
) -> Tuple[List[CsvPlaybackScheduleEntry], List[CsvTimedPlaybackBlock]]:
    """dwell 체류 + pick/transfer/place 를 CSV 시각 순 **타임 블록** 으로 만든다."""
    global _csv_bulk_build_active
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
                try:
                    place_st = build_foup_pick_place_steps(
                        foup_index=foup_n,
                        cassette_slot=cassette_slot,
                        pick_or_place="place",
                    )
                    if place_st:
                        ent = _place_schedule_entry(
                            time_sec=last.end_sec,
                            foup_index=foup_n,
                            cassette_slot=cassette_slot,
                            lot_id=lot_id,
                            steps=place_st,
                        )
                        schedule.append(ent)
                        blocks.append(
                            _block_from_schedule(
                                ent, place_st, label=f"foup{foup_n}_place({cassette_slot})"
                            )
                        )
                except Exception as exc:
                    _lam_sim_log_build("csv_tour", f"FOUP place skip: {exc}")
                if progress is not None:
                    progress.tick(1)
    finally:
        _csv_bulk_build_active = False
        if progress is not None:
            progress.finish()

    schedule.sort(key=lambda e: (e.time_sec, e.sort_order))
    blocks.sort(key=lambda b: (b.time_sec, b.sort_order))
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


def build_csv_timed_playback_blocks(dwells: List[DwellRecord]) -> List[CsvTimedPlaybackBlock]:
    """CSV 시각 동기 재생용 블록만 반환."""
    _, blocks = build_csv_playback_plan(dwells)
    return blocks


def build_csv_playback_steps_from_dwells(dwells: List[DwellRecord]) -> LamSimJsonSteps:
    """모든 액션 블록 스텝을 순서대로 이어 붙임 (배속·CSV 대기 없음 — 레거시/검증용)."""
    out: LamSimJsonSteps = []
    for blk in build_csv_timed_playback_blocks(dwells):
        out.extend(blk.steps)
    return out


def build_csv_playback_schedule(dwells: List[DwellRecord]) -> List[CsvPlaybackScheduleEntry]:
    """시간순 스케줄 항목만 반환 (스텝 빌드 포함 — 캐시된 plan 이 있으면 그 schedule 사용)."""
    schedule, _ = build_csv_playback_plan(dwells)
    return schedule


# CSV Play 실시간 진행 (UI 1초 갱신 — 콘솔 1초 틱 로그 없음)
_csv_play_progress_ui_cb: Optional[Callable[[float, float, float, float], None]] = None
_csv_play_progress_stop = threading.Event()
_csv_play_progress_snap_lock = threading.Lock()
_csv_play_progress_snap: Dict[str, Any] = {
    "process_only": False,
    "json_done": 0,
    "json_total": 0,
    "csv_t_display": 0.0,
    "csv_total": 0.0,
    "t0": 0.0,
    "speed_scale": 1.0,
}

# 공정만보기 전용 — 마지막 JSON 종료(전 레인) 시각·CSV t (레인 간 빈 텀 압축)
_csv_play_global_end_lock = threading.Lock()
_csv_play_global_wall_end: float = 0.0
_csv_play_global_csv_end: float = 0.0

# CSV Play 타임라인 — JSON 실행 중인 행만 UI 에서 녹색 강조 (항목 match key)
_csv_play_timeline_highlight_cb: Optional[Callable[[frozenset], None]] = None
_csv_play_timeline_window_ref: Optional[weakref.ReferenceType[Any]] = None
_csv_play_timeline_active_keys_lock = threading.Lock()
_csv_play_timeline_active_keys: set = set()

_TIMELINE_UI_COLOR_DEFAULT = 0xFF9AA4B2
_TIMELINE_UI_COLOR_DWELL = 0xFF6E7580
_TIMELINE_UI_COLOR_PLAYING = 0xFF6CCB6C


def set_csv_play_timeline_highlight_callback(
    cb: Optional[Callable[[frozenset], None]],
) -> None:
    """Play 중 JSON 실행 행 강조: ``frozenset`` of ``_schedule_entry_match_key`` tuples."""
    global _csv_play_timeline_highlight_cb
    _csv_play_timeline_highlight_cb = cb


def register_csv_play_timeline_window(window: Any) -> None:
    """타임라인 녹색 강조 대상 UI (본창·HUD 공유 ``LamSimulationCsvPlayWindow``)."""
    global _csv_play_timeline_window_ref
    _csv_play_timeline_window_ref = weakref.ref(window)


def unregister_csv_play_timeline_window() -> None:
    global _csv_play_timeline_window_ref
    _csv_play_timeline_window_ref = None


def _csv_play_timeline_highlight_notify(active_keys: frozenset) -> None:
    def _ui() -> None:
        ref = _csv_play_timeline_window_ref
        win = ref() if ref is not None else None
        if win is not None:
            try:
                win._apply_schedule_row_highlight(active_keys)
                return
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} 타임라인 강조 UI 실패: {exc}",
                    flush=True,
                )
        cb = _csv_play_timeline_highlight_cb
        if cb is None:
            return
        try:
            cb(active_keys)
        except Exception:
            pass

    _post_kit_main_thread(_ui)


def _csv_play_timeline_row_begin_entry(sched: CsvPlaybackScheduleEntry) -> None:
    key = _schedule_entry_match_key(sched)
    with _csv_play_timeline_active_keys_lock:
        _csv_play_timeline_active_keys.add(key)
        snap = frozenset(_csv_play_timeline_active_keys)
    _csv_play_timeline_highlight_notify(snap)


def _csv_play_timeline_row_end_entry(sched: CsvPlaybackScheduleEntry) -> None:
    key = _schedule_entry_match_key(sched)
    with _csv_play_timeline_active_keys_lock:
        _csv_play_timeline_active_keys.discard(key)
        snap = frozenset(_csv_play_timeline_active_keys)
    _csv_play_timeline_highlight_notify(snap)


def clear_csv_play_timeline_highlight() -> None:
    """재생 종료·중지 시 타임라인 강조 해제."""
    with _csv_play_timeline_active_keys_lock:
        _csv_play_timeline_active_keys.clear()
    _csv_play_timeline_highlight_notify(frozenset())


def set_csv_play_progress_ui_callback(
    cb: Optional[Callable[[float, float, float, float], None]],
) -> None:
    """Play 중 UI 갱신: (csv_t, csv_total, wall_elapsed, wall_total_est)."""
    global _csv_play_progress_ui_cb
    _csv_play_progress_ui_cb = cb


def get_csv_play_progress_snap() -> Dict[str, Any]:
    """UI 진행 표시용 스냅샷 (공정만보기 시 json_done / csv_t_display 등)."""
    with _csv_play_progress_snap_lock:
        return dict(_csv_play_progress_snap)


def _reset_csv_play_progress_snap(
    *,
    process_only: bool,
    json_total: int = 0,
    csv_total: float = 0.0,
    t0: float = 0.0,
    speed_scale: float = 1.0,
) -> None:
    with _csv_play_progress_snap_lock:
        _csv_play_progress_snap.clear()
        _csv_play_progress_snap.update(
            {
                "process_only": bool(process_only),
                "json_done": 0,
                "json_total": max(0, int(json_total)),
                "csv_t_display": 0.0,
                "csv_total": float(csv_total),
                "t0": float(t0),
                "speed_scale": float(max(0.01, speed_scale or 1.0)),
            }
        )


def _csv_play_progress_mark_json_start(
    block: CsvTimedPlaybackBlock, *, json_done: int
) -> None:
    with _csv_play_progress_snap_lock:
        _csv_play_progress_snap["json_done"] = max(0, int(json_done))
        _csv_play_progress_snap["csv_t_display"] = float(block.time_sec)


def _csv_play_progress_mark_json_done(json_done: int) -> None:
    with _csv_play_progress_snap_lock:
        _csv_play_progress_snap["json_done"] = max(0, int(json_done))


def _reset_csv_play_global_json_end(*, t0: float, csv_start: float = 0.0) -> None:
    """공정만보기 시작 시 전역 종료 시각 초기화."""
    global _csv_play_global_wall_end, _csv_play_global_csv_end
    with _csv_play_global_end_lock:
        _csv_play_global_wall_end = float(t0)
        _csv_play_global_csv_end = float(csv_start)


def _bump_csv_play_global_json_end(
    block: CsvTimedPlaybackBlock, *, wall_end: float
) -> None:
    """공정만보기: 어느 레인이든 JSON 종료 후 전역 시각 갱신 (타 레인 대기 당김)."""
    global _csv_play_global_wall_end, _csv_play_global_csv_end
    we = float(wall_end)
    ce = float(block.time_sec)
    with _csv_play_global_end_lock:
        if we > _csv_play_global_wall_end:
            _csv_play_global_wall_end = we
        if ce > _csv_play_global_csv_end:
            _csv_play_global_csv_end = ce


def _get_csv_play_global_json_end() -> Tuple[float, float]:
    with _csv_play_global_end_lock:
        return float(_csv_play_global_wall_end), float(_csv_play_global_csv_end)


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


def _process_only_start_wall(
    *,
    lane_ready: float,
    nominal_wall: float,
    block: CsvTimedPlaybackBlock,
    all_blocks: List[CsvTimedPlaybackBlock],
    lane_last_csv: float,
    lane: Optional[str],
) -> float:
    """공정만보기 전용 시작 시각 (일반 재생은 ``_csv_playback_block_worker`` 유지).

    - 전역 ``g_csv`` 기준 사이에 다른 JSON 이 남아 있으면 ``nom`` 까지 대기.
    - 사이가 비었거나(VTM→VTM) 모두 끝났으면 ``max(레인 준비, 전역 종료)`` 로 즉시 이어 붙임.
    - JSON wall 시간 > CSV 간격(긴 애니) 이어도 대기하지 않음.
    - VTM t=1·ATM t=3 처럼 시각 정렬 필요 시에만 ``nom`` 대기.
    """
    lr = float(lane_ready)
    nom = float(nominal_wall)
    g_wall, g_csv = _get_csv_play_global_json_end()
    now = time.monotonic()
    block_csv = float(block.time_sec)
    lane_lo = float(lane_last_csv)

    if _has_json_csv_between(g_csv, block_csv, all_blocks, block):
        return max(lr, nom)

    if lane and _has_json_csv_between(lane_lo, block_csv, all_blocks, block, lane=lane):
        return max(lr, nom)

    if now >= nom or lr >= nom or g_wall >= nom:
        return max(lr, g_wall)

    csv_span = block_csv - g_csv
    wall_gap = nom - g_wall
    if (
        now < nom
        and csv_span > 1e-9
        and abs(csv_span - wall_gap) <= 1e-3
        and lr <= g_wall + 1e-3
    ):
        return max(lr, nom)
    return max(lr, g_wall)


def _sleep_until_process_only_start(
    *,
    lane_ready: float,
    nominal_wall: float,
    block: CsvTimedPlaybackBlock,
    all_blocks: List[CsvTimedPlaybackBlock],
    lane_last_csv: float,
    lane: Optional[str],
) -> bool:
    """공정만보기: 목표 시각까지 대기(전역·레인 완료 시각 재계산). ``False`` = 중지."""
    while True:
        if csv_playback_stop_requested():
            return False
        start = _process_only_start_wall(
            lane_ready=lane_ready,
            nominal_wall=nominal_wall,
            block=block,
            all_blocks=all_blocks,
            lane_last_csv=lane_last_csv,
            lane=lane,
        )
        wait = max(0.0, start - time.monotonic())
        if wait <= 0.001:
            return True
        if not _sleep_csv_playback(min(wait, 0.05)):
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


# CSV Play 중지 — 백그라운드 대기(sleep)·LamSequenceRunner 루프 공통
_csv_play_stop_event = threading.Event()
_csv_play_runner_lock = threading.Lock()
_csv_play_active_runners: List[Any] = []

_CSV_PLAYBACK_LANES = ("atm", "vtm")


def clear_csv_playback_stop() -> None:
    """새 CSV Play 시작 전 호출."""
    _csv_play_stop_event.clear()
    _csv_play_material_test_stop.clear()


def csv_playback_stop_requested() -> bool:
    return _csv_play_stop_event.is_set()


def _csv_play_register_runner(runner: Any) -> None:
    with _csv_play_runner_lock:
        _csv_play_active_runners.append(runner)


def _csv_play_unregister_runner(runner: Any) -> None:
    with _csv_play_runner_lock:
        try:
            _csv_play_active_runners.remove(runner)
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

    def __init__(self) -> None:
        self._lane_locks = {lane: threading.Lock() for lane in _CSV_PLAYBACK_LANES}

    def run_in_lane(self, lane: str, fn: Callable[[], None]) -> None:
        """``lane`` 뮤텍스로 ``fn`` 직렬화. ``atm`` / ``vtm`` 락은 서로 독립(병렬 가능)."""
        if lane not in self._lane_locks:
            fn()
            return
        lock = self._lane_locks[lane]
        while True:
            if csv_playback_stop_requested():
                return
            if lock.acquire(timeout=0.05):
                break
        try:
            if not csv_playback_stop_requested():
                fn()
        finally:
            lock.release()


def request_stop_csv_playback(
    registry: Any = None,
    scheduler: Any = None,
) -> None:
    """CSV Play 중지 — 대기 루프 탈출 + 진행 중 Runner·애니·스케줄러 정지."""
    _ = registry
    _csv_play_stop_event.set()
    _csv_play_material_test_stop.set()
    with _csv_play_runner_lock:
        runners = list(_csv_play_active_runners)
    for runner in runners:
        try:
            runner.stop(cancel_all_move_rotate=True)
        except Exception as exc:
            if not is_csv_playback_compact_log():
                print(f"{_PRINT_PREFIX} CSV Play 중지 Runner 경고: {exc}", flush=True)
    try:
        from . import lam_rotate_animation as _lrx
        from . import lam_translate_animation as _ltx

        _ltx.stop_all_translate_animations()
        _lrx.stop_all_rotate_animations()
    except Exception as exc:
        print(f"{_PRINT_PREFIX} CSV Play 중지 애니 경고: {exc}", flush=True)
    if scheduler is not None:
        try:
            stop_fn = getattr(scheduler, "stop_all", None)
            if callable(stop_fn):
                stop_fn()
        except Exception as exc:
            if not is_csv_playback_compact_log():
                print(f"{_PRINT_PREFIX} CSV Play 중지 scheduler 경고: {exc}", flush=True)


def _sleep_csv_playback(sec: float) -> bool:
    """최대 ``sec`` 초 대기. ``False`` = 중지 요청으로 조기 종료."""
    if csv_playback_stop_requested():
        return False
    if sec <= 1e-6:
        return True
    end = time.monotonic() + float(sec)
    while time.monotonic() < end:
        if csv_playback_stop_requested():
            return False
        time.sleep(min(0.1, max(0.0, end - time.monotonic())))
    return not csv_playback_stop_requested()


def _run_lam_sim_steps_cancellable(
    registry: Any,
    scheduler: Any,
    steps: LamSimJsonSteps,
    *,
    speed_scale: float = 1.0,
    lane: Optional[str] = None,
    lane_coordinator: Optional[_CsvPlaybackLaneCoordinator] = None,
) -> None:
    """``run_lam_sim_steps`` 와 동일하나 CSV 중지 시 Runner 를 끊을 수 있게 등록."""
    if not steps or csv_playback_stop_requested():
        return
    try:
        from .lam_sequence_engine import LamSequenceRunner
    except Exception as exc:
        print(f"{_PRINT_PREFIX} LamSequenceRunner import 실패: {exc}", flush=True)
        return
    sp = float(max(0.01, speed_scale or 1.0))
    quiet = is_csv_playback_compact_log()

    def _execute() -> None:
        runner = LamSequenceRunner(registry, scheduler)
        _csv_play_register_runner(runner)
        try:
            runner.run(
                list(steps),
                reset_each_start=False,
                speed_scale=sp,
                quiet=quiet,
            )
        finally:
            _csv_play_unregister_runner(runner)

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
) -> None:
    """한 JSON 블록 실행 (로그·타임라인 강조·진행 스냅샷)."""
    sp = float(max(0.01, speed_scale or 1.0))
    sched = block.schedule
    if sched is None or not block.steps:
        return
    _csv_play_progress_mark_json_start(block, json_done=json_done_before)
    _notify_csv_play_progress_ui()
    wall_elapsed = time.monotonic() - t0
    lane = _playback_lane_from_block(block)
    _csv_play_timeline_row_begin_entry(sched)
    try:
        _run_lam_sim_steps_cancellable(
            registry,
            scheduler,
            block.steps,
            speed_scale=sp,
            lane=lane,
            lane_coordinator=lane_coordinator,
        )
    finally:
        _csv_play_timeline_row_end_entry(sched)
    if not csv_playback_stop_requested():
        _print_csv_compact_line(index, sched, wall_elapsed_sec=wall_elapsed, executed=True)
    _csv_play_progress_mark_json_done(json_done_before + 1)
    _notify_csv_play_progress_ui()
    if get_csv_play_progress_snap().get("process_only"):
        _bump_csv_play_global_json_end(block, wall_end=time.monotonic())


def _notify_csv_play_progress_ui() -> None:
    ui_cb = _csv_play_progress_ui_cb
    if ui_cb is None:
        return
    snap = get_csv_play_progress_snap()
    t0 = float(snap.get("t0", 0.0) or 0.0)
    csv_total = float(snap.get("csv_total", 0.0) or 0.0)
    wall_elapsed = max(0.0, time.monotonic() - t0) if t0 > 0 else 0.0
    if snap.get("process_only"):
        csv_t = float(snap.get("csv_t_display", 0.0) or 0.0)
        wall_total_est = float(max(1, snap.get("json_total", 1) or 1))
    else:
        sp = float(max(0.01, snap.get("speed_scale", 1.0) or 1.0))
        csv_t = min(csv_total, wall_elapsed * sp) if csv_total > 0 else 0.0
        wall_total_est = csv_total / sp if csv_total > 0 else 0.0

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
    t0: float,
    speed_scale: float,
    lane_coordinator: _CsvPlaybackLaneCoordinator,
) -> None:
    """블록 1개: CSV 시각까지 대기 → (dwell 로그 | 레인별 JSON 실행). 메인 루프 비블로킹."""
    sp = float(max(0.01, speed_scale or 1.0))
    target_wall = t0 + float(block.time_sec) / sp
    wait = max(0.0, target_wall - time.monotonic())
    if wait > 0.001 and not _sleep_csv_playback(wait):
        return
    if csv_playback_stop_requested():
        return

    wall_elapsed = time.monotonic() - t0
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


def _csv_play_progress_ticker_loop(
    t0: float,
    csv_total: float,
    speed_scale: float,
) -> None:
    """Play 중 1초마다 UI 진행률 콜백만 갱신 (콘솔 로그 없음)."""
    sp = float(max(0.01, speed_scale or 1.0))
    wall_total_est = float(csv_total) / sp if csv_total > 0 else 0.0
    ui_cb = _csv_play_progress_ui_cb
    if ui_cb is None:
        return
    while not _csv_play_progress_stop.wait(1.0):
        if csv_playback_stop_requested():
            break
        wall_elapsed = time.monotonic() - t0
        csv_t = min(float(csv_total), wall_elapsed * sp)
        try:
            ui_cb(csv_t, csv_total, wall_elapsed, wall_total_est)
        except Exception:
            pass


def _csv_play_progress_ticker_snap_loop() -> None:
    """공정만보기: 스냅샷 기준 진행률 (JSON 시각·건수로 점프)."""
    while not _csv_play_progress_stop.wait(1.0):
        if csv_playback_stop_requested():
            break
        _notify_csv_play_progress_ui()


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
) -> None:
    """공정만보기 전용: CSV ``t`` 유지 + 레인·전역 빈 구간만 압축 (일반 재생과 분리)."""
    lane_ready_wall = float(t0)
    lane_last_csv = 0.0
    for index, block in items:
        if csv_playback_stop_requested():
            return
        nominal_wall = float(t0) + float(block.time_sec)
        if not _sleep_until_process_only_start(
            lane_ready=lane_ready_wall,
            nominal_wall=nominal_wall,
            block=block,
            all_blocks=all_json_blocks,
            lane_last_csv=lane_last_csv,
            lane=lane,
        ):
            return
        if csv_playback_stop_requested():
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
        )
        with json_done_lock:
            json_done_counter[0] = max(
                int(json_done_counter[0]), json_done_before + 1
            )
        lane_ready_wall = time.monotonic()
        lane_last_csv = max(lane_last_csv, float(block.time_sec))


def _run_csv_timed_playback_process_only(
    registry: Any,
    scheduler: Any,
    blocks: List[CsvTimedPlaybackBlock],
) -> None:
    """공정만보기: CSV ``t`` 스케줄 유지, dwell·JSON 없는 빈 대기만 생략. 배속 1x."""
    ordered = sorted(blocks, key=lambda b: (b.time_sec, b.sort_order))
    if not ordered:
        print(f"{_PRINT_PREFIX} CSV 공정만보기: 블록 없음", flush=True)
        return

    apply_csv_play_initial_wafer_visibility()

    all_json_blocks = [b for b in ordered if b.steps]
    atm_items, vtm_items, other_items = _partition_json_blocks_by_lane(blocks)
    n_json = len(atm_items) + len(vtm_items) + len(other_items)
    csv_total = max(float(b.time_sec) for b in ordered)

    print(
        f"{_PRINT_PREFIX} ▶ 공정만보기 재생 | JSON {n_json}건 | "
        f"CSV t 표시 0~{csv_total:.1f}s | ATM {len(atm_items)} · VTM {len(vtm_items)} | "
        f"배속 1x · CSV 시각 유지 · 레인·전역 빈 구간만 생략",
        flush=True,
    )

    t0 = time.monotonic()
    stopped = False
    clear_csv_play_timeline_highlight()
    _reset_csv_play_global_json_end(t0=t0, csv_start=0.0)
    lane_coordinator = _CsvPlaybackLaneCoordinator()
    json_done_counter: List[int] = [0]
    json_done_lock = threading.Lock()
    _reset_csv_play_progress_snap(
        process_only=True,
        json_total=n_json,
        csv_total=csv_total,
        t0=t0,
        speed_scale=1.0,
    )
    _notify_csv_play_progress_ui()
    _csv_play_progress_stop.clear()
    ticker = threading.Thread(
        target=_csv_play_progress_ticker_snap_loop,
        daemon=True,
        name="lam-sim-csv-play-progress-snap",
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
        }
        if atm_items:
            workers.append(
                threading.Thread(
                    target=_csv_play_process_only_lane_worker,
                    args=(atm_items, "atm", registry, scheduler),
                    kwargs=lane_kw,
                    daemon=True,
                    name="lam-csv-play-atm-seq",
                )
            )
        if vtm_items:
            workers.append(
                threading.Thread(
                    target=_csv_play_process_only_lane_worker,
                    args=(vtm_items, "vtm", registry, scheduler),
                    kwargs=lane_kw,
                    daemon=True,
                    name="lam-csv-play-vtm-seq",
                )
            )
        if other_items:
            workers.append(
                threading.Thread(
                    target=_csv_play_process_only_lane_worker,
                    args=(other_items, None, registry, scheduler),
                    kwargs=lane_kw,
                    daemon=True,
                    name="lam-csv-play-other-seq",
                )
            )
        for t in workers:
            if csv_playback_stop_requested():
                stopped = True
            t.start()
        for t in workers:
            if csv_playback_stop_requested():
                stopped = True
            try:
                t.join()
            except Exception:
                pass
        _csv_play_progress_mark_json_done(n_json)
        _notify_csv_play_progress_ui()
    finally:
        _csv_play_progress_stop.set()
        _csv_play_material_test_stop.set()
        clear_csv_play_timeline_highlight()
        try:
            ticker.join(timeout=2.0)
        except Exception:
            pass

    total_wall = time.monotonic() - t0
    if stopped:
        print(
            f"{_PRINT_PREFIX} 공정만보기 중지 | 실경과 {total_wall:.1f}s | JSON {n_json}건",
            flush=True,
        )
    else:
        print(
            f"{_PRINT_PREFIX} 공정만보기 완료 | 실경과 {total_wall:.1f}s | JSON {n_json}건",
            flush=True,
        )


# CSV Play 시작 시 1회만 보이게 할 FOUP 슬롯 (각 25).
_CSV_PLAY_INITIAL_VISIBLE_FOUP_SLOT_KEYS = frozenset(
    f"foup{f}_{i}" for f in (1, 2, 3) for i in range(1, 26)
)


def apply_csv_play_initial_wafer_visibility() -> None:
    """CSV Play **시작 1회**: FOUP1~3 슬롯(각 25) 웨이퍼만 보이게, 나머지는 숨김.

    이후 이벤트 JSON ``PRIM_VISIBILITY`` 등은 기존대로 동작한다(강제 없음).
    """
    from pxr import UsdGeom  # type: ignore

    from .lam_sequence_engine import _dispatch_main_wait, _stage

    wafer_map = WAFER_PRIM_BY_SLOT_KEY or load_wafer_prim_by_slot_key()
    if not wafer_map:
        return

    show_paths: List[str] = []
    hide_paths: List[str] = []
    for slot_key, path in wafer_map.items():
        p = (path or "").strip()
        if not p:
            continue
        if slot_key in _CSV_PLAY_INITIAL_VISIBLE_FOUP_SLOT_KEYS:
            show_paths.append(p)
        else:
            hide_paths.append(p)

    def _set_path_visible(stage: Any, path: str, visible: bool) -> None:
        try:
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsValid():
                return
            img = UsdGeom.Imageable(prim)
            if not img:
                return
            if visible:
                img.MakeVisible()
            else:
                img.MakeInvisible()
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} CSV Play 초기 visibility 실패 path={path!r}: {exc}",
                flush=True,
            )

    def _do_in_main() -> None:
        st = _stage()
        if st is None:
            return
        for p in hide_paths:
            _set_path_visible(st, p, False)
        for p in show_paths:
            _set_path_visible(st, p, True)

    if _dispatch_main_wait(_do_in_main, timeout=10.0):
        print(
            f"{_PRINT_PREFIX} CSV Play 초기 웨이퍼 visibility: "
            f"FOUP 보임 {len(show_paths)} · 숨김 {len(hide_paths)}",
            flush=True,
        )


# CSV Play material binding 테스트 스케줄 (setTimeout 유사, Play 중지 시 탈출)
_csv_play_material_test_stop = threading.Event()


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
                _csv_play_material_test_stop.is_set()
                or csv_playback_stop_requested()
            ):
                return
            time.sleep(min(0.05, target - time.monotonic()))
        if _csv_play_material_test_stop.is_set() or csv_playback_stop_requested():
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
    _csv_play_material_test_stop.clear()
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
) -> None:
    """CSV ``eqp_start_tm`` 스케줄 재생 (``speed_scale`` 배속).

    - **일반**: 각 블록 스레드가 CSV 시각까지 대기 후 실행.
    - **공정만보기** (``process_only``): CSV ``t`` 스케줄 유지, dwell·레인 내 **빈** 대기만 생략, 배속 1x.
    - **ATM** / **VTM** 은 CSV 시각에 맞춰 교차 시작 가능(동시 0초 시작 아님).
    - **동일 레인** 은 ``max(이전 JSON 종료, 다음 CSV t)`` 에 시작(직렬).
    - dwell(``steps`` 없음) 은 일반 모드에서만 해당 시각에 로그.
    """
    if process_only:
        _run_csv_timed_playback_process_only(registry, scheduler, blocks)
        return

    sp = float(max(0.01, speed_scale or 1.0))
    ordered = sorted(blocks, key=lambda b: (b.time_sec, b.sort_order))
    if not ordered:
        print(f"{_PRINT_PREFIX} CSV timed playback: 블록 없음", flush=True)
        return

    apply_csv_play_initial_wafer_visibility()

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

    csv_total = max(float(b.time_sec) for b in ordered)
    wall_total_est = csv_total / sp
    print(
        f"{_PRINT_PREFIX} ▶ 재생 시작 | CSV 전체 ~{csv_total:.1f}s | "
        f"배속 {sp:g}x → 실시간 ~{wall_total_est:.0f}s | "
        f"모드: CSV 시각 스케줄 · ATM/VTM 레인 병렬(동일 레인 직렬·대기)",
        flush=True,
    )

    t0 = time.monotonic()
    stopped = False
    clear_csv_play_timeline_highlight()
    lane_coordinator = _CsvPlaybackLaneCoordinator()
    n_json = sum(1 for b in ordered if b.steps)
    _reset_csv_play_progress_snap(
        process_only=False,
        json_total=n_json,
        csv_total=csv_total,
        t0=t0,
        speed_scale=sp,
    )
    _csv_play_progress_stop.clear()
    ticker = threading.Thread(
        target=_csv_play_progress_ticker_loop,
        args=(t0, csv_total, sp),
        daemon=True,
        name="lam-sim-csv-play-progress",
    )
    ticker.start()
    workers: List[threading.Thread] = []
    try:
        for i, block in enumerate(ordered, 1):
            if csv_playback_stop_requested():
                stopped = True
                break
            t = threading.Thread(
                target=_csv_playback_block_worker,
                args=(block, i, registry, scheduler),
                kwargs={
                    "t0": t0,
                    "speed_scale": sp,
                    "lane_coordinator": lane_coordinator,
                },
                daemon=True,
                name=f"lam-csv-play-blk{i:03d}",
            )
            workers.append(t)
            t.start()

        for t in workers:
            if csv_playback_stop_requested():
                stopped = True
            try:
                t.join()
            except Exception:
                pass
    finally:
        _csv_play_progress_stop.set()
        _csv_play_material_test_stop.set()
        clear_csv_play_timeline_highlight()
        try:
            ticker.join(timeout=2.0)
        except Exception:
            pass

    total_wall = time.monotonic() - t0
    if stopped:
        print(
            f"{_PRINT_PREFIX} CSV Play 중지 | 실경과 {total_wall:.1f}s | "
            f"CSV t ~{min(csv_total, total_wall * sp):.1f}s",
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
) -> None:
    """CSV ``eqp_start_tm`` 동기 재생: 시각까지 대기 → 로그 → 이벤트 JSON (``speed_scale`` 배속).

    ``prepared`` 가 있으면 파싱·빌드를 생략한다 (캐시·UI 선행 빌드).
    ``process_only=True`` 이면 공정만보기(배속 1x, CSV 시각·레인 내 빈 구간만 생략).

    Note:
        UI 스레드 안전을 위해 **백그라운드 스레드**에서 호출할 것.
    """
    sp = 1.0 if process_only else float(max(0.01, speed_scale or 1.0))
    clear_csv_playback_stop()
    clear_csv_play_timeline_highlight()
    set_csv_playback_compact_log(True)
    try:
        path = resolve_csv_path(csv_path)
        from_cache = False
        if prepared is not None and prepared.path.resolve() == path.resolve():
            cached = prepared
            from_cache = True
        else:
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

        n_act = len(action_blocks)
        n_all = len(blocks)
        src = "캐시" if from_cache else "빌드"
        print(
            f"{_PRINT_PREFIX} CSV 준비 완료 ({src}): {path.name} | "
            f"dwell {len(cached.dwells)} · 블록 {n_all} (JSON {n_act}) · {cached.build_ms:.0f}ms",
            flush=True,
        )
        if process_only:
            print(
                f"{_PRINT_PREFIX} Play — 공정만보기 (배속 1x, CSV 시각·레인 빈 구간 생략)",
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
        self._schedule_highlight_keys: frozenset = frozenset()
        self._build_progress_model: Any = None
        self._speed_model: Any = None
        self._process_only_model: Any = None
        self._hud_schedule_rows_stack: Any = None
        self._hud_schedule_scroll_frame: Any = None
        self._hud_schedule_row_labels: List[Any] = []
        self._hud_build_progress_model: Any = None
        self._csv_play_thread: Optional[threading.Thread] = None
        self._csv_build_thread: Optional[threading.Thread] = None
        self._prepared_playback: Optional[CachedCsvPlayback] = None
        self._build_ui_ticker: Optional[_SecondsIntervalProgress] = None
        # Viewport HUD 등 ``ui.Window`` 없이 Play API 만 쓸 때 선택 인덱스.
        self._csv_selected_index: int = 0

    def destroy(self) -> None:
        """윈도우·콤보·로그 위젯 참조를 해제한다 (``lam_window`` 종료 시 호출)."""
        request_stop_csv_playback(self._registry, self._scheduler)
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
        set_csv_play_progress_ui_callback(None)
        set_csv_play_timeline_highlight_callback(None)
        unregister_csv_play_timeline_window()
        clear_csv_play_timeline_highlight()
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
        self._hud_schedule_rows_stack = None
        self._hud_schedule_row_labels = []
        self._hud_build_progress_model = None
        self._prepared_playback = None

    def _log(self, msg: str) -> None:
        print(f"{_PRINT_PREFIX} {msg}", flush=True)
        try:
            if self._log_label is not None:
                self._log_label.text = msg
        except Exception:
            pass

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
        if self._process_only_model is None:
            self._process_only_model = SimpleBoolModel(False)
        register_csv_play_timeline_window(self)

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
        self._window = ui.Window(self.WINDOW_TITLE, width=640, height=920)
        register_csv_play_timeline_window(self)
        with self._window.frame:
            with ui.VStack(spacing=6):
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
                    ui.Button("CSV Play", width=90, clicked_fn=self._on_play_clicked)
                    ui.Button(
                        "CSV 중지",
                        width=90,
                        clicked_fn=self._on_csv_stop_clicked,
                        tooltip="진행 중 CSV Play·대기·애니메이션 중지",
                    )
                    try:
                        from omni.ui import SimpleBoolModel  # type: ignore

                        if self._process_only_model is None:
                            self._process_only_model = SimpleBoolModel(False)
                        with ui.HStack(spacing=4, width=0):
                            ui.Label("공정만보기", width=72)
                            ui.CheckBox(
                                model=self._process_only_model,
                                width=22,
                                tooltip=(
                                    "체크 후 Play: CSV 시각(t) 유지, JSON 없는 빈 대기만 생략 "
                                    "(배속 1x). ATM 종료 후 VTM 등 레인 간 빈 텀도 생략. "
                                    "체크 해제 시 기존 시간 재생."
                                ),
                            )
                    except Exception:
                        self._process_only_model = None
                    ui.Spacer()
                with ui.HStack(spacing=6, height=28):
                    ui.Label("재생 배속", width=70)
                    try:
                        from omni.ui import SimpleFloatModel  # type: ignore

                        if self._speed_model is None:
                            self._speed_model = SimpleFloatModel(1.0)
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
                    ui.Label("타임라인 표시 불가 (SimpleStringModel 없음).", height=40)
                self._refresh_csv_schedule_preview(fast_only=True)
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
        return _schedule_entry_match_key(entry) in active_keys

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
            if _schedule_entry_match_key(ent) in active_keys:
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
        with _csv_play_timeline_active_keys_lock:
            if _csv_play_timeline_active_keys:
                return frozenset(_csv_play_timeline_active_keys)
        return frozenset(self._schedule_highlight_keys)

    def _apply_schedule_row_highlight(self, active_keys: frozenset) -> None:
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
        self._scroll_timeline_to_highlighted_rows(merged)

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
        return f"빌드 진행: {pct}% ({done}/{total} 이벤트)"

    def _format_play_progress_line(
        self, csv_t: float, csv_total: float, wall_elapsed: float, wall_total_est: float
    ) -> str:
        snap = get_csv_play_progress_snap()
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

    def _read_speed_scale(self) -> float:
        if self._read_process_only():
            return 1.0
        m = self._speed_model
        if m is None:
            return 1.0
        try:
            return float(max(0.1, min(20.0, float(m.get_value_as_float()))))
        except Exception:
            return 1.0

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
        self._refresh_csv_schedule_preview(fast_only=True)

    def _csv_build_thread_alive(self) -> bool:
        t = self._csv_build_thread
        return t is not None and t.is_alive()

    def _apply_cached_timeline_ui(self, cached: CachedCsvPlayback) -> None:
        sp = self._read_speed_scale()
        self._prepared_playback = cached
        self._rebuild_schedule_timeline_rows(
            cached.schedule,
            speed_scale=sp,
            rebuild_main=self._schedule_rows_stack is not None,
            rebuild_hud=self._hud_schedule_rows_stack is not None,
        )
        self._set_build_progress_text(
            f"준비 완료 (캐시) — dwell {len(cached.dwells)} · "
            f"JSON {sum(1 for e in cached.schedule if e.category != 'dwell')}건"
        )

    def _start_background_csv_build(self, path: Path, *, reason: str = "") -> None:
        """백그라운드에서 plan 빌드 + 캐시 (진행률만 UI 갱신, 빌드 로직은 스로틀)."""
        if self._csv_build_thread_alive():
            return
        hit = get_cached_csv_playback(path)
        if hit is not None:
            self._apply_cached_timeline_ui(hit)
            return

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
            self._set_build_progress_text("(CSV 없음)")
            return
        hit = get_cached_csv_playback(p)
        if hit is not None:
            self._prepared_playback = hit
            self._rebuild_schedule_timeline_rows(
                hit.schedule,
                speed_scale=self._read_speed_scale(),
                rebuild_main=rebuild_main and self._schedule_rows_stack is not None,
                rebuild_hud=rebuild_hud and self._hud_schedule_rows_stack is not None,
            )
            self._set_build_progress_text(
                f"준비 완료 (캐시) — dwell {len(hit.dwells)} · "
                f"JSON {sum(1 for e in hit.schedule if e.category != 'dwell')}건"
            )
            return
        try:
            dwells = load_csv_dwell_timeline(p)
            entries = build_csv_playback_schedule_meta(dwells)
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

    def _csv_play_thread_alive(self) -> bool:
        t = self._csv_play_thread
        return t is not None and t.is_alive()

    def _on_csv_stop_clicked(self) -> None:
        if not self._csv_play_thread_alive():
            self._log("CSV Play 가 실행 중이 아닙니다.")
            return
        request_stop_csv_playback(self._registry, self._scheduler)
        clear_csv_play_timeline_highlight()
        self._log("CSV Play 중지 요청 — 콘솔에서 'CSV Play 중지됨' 확인.")

    def _on_play_clicked(self) -> None:
        if not self._csv_paths:
            self._log("CSV 없음 — lam/csv 에 파일을 추가하세요.")
            return
        if self._csv_play_thread_alive():
            self._log("이미 CSV Play 실행 중 — [CSV 중지] 후 다시 Play 하세요.")
            return
        path = self._selected_csv_path()
        if path is None:
            self._log("CSV 경로 없음")
            return
        process_only = self._read_process_only()
        if process_only:
            self._set_speed_preset(1.0)
        sp = self._read_speed_scale()
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
        self._log(
            f"Play: {path.name} — {mode_ko}, "
            f"스케줄 {n_items}항목 (JSON {n_json})"
            + ("" if prepared is not None else " · 빌드 후 재생")
        )

        def _on_play_ui(csv_t: float, csv_total: float, wall_el: float, wall_tot: float) -> None:
            line = self._format_play_progress_line(csv_t, csv_total, wall_el, wall_tot)

            def _ui() -> None:
                self._set_build_progress_text(line)

            _post_kit_main_thread(_ui)

        def _on_timeline_highlight(active_keys: frozenset) -> None:
            self._apply_schedule_row_highlight(active_keys)

        def _worker() -> None:
            try:
                nonlocal prepared
                set_csv_play_progress_ui_callback(_on_play_ui)
                set_csv_play_timeline_highlight_callback(_on_timeline_highlight)
                if prepared is None:
                    ticker = self._build_ui_ticker

                    def _on_tick(done: int, total: int) -> None:
                        if ticker is not None:
                            ticker.set_done(done)

                    set_csv_playback_compact_log(True)
                    t_build0 = time.perf_counter()
                    try:
                        dwells = load_csv_dwell_timeline(path)
                        total_est = _estimate_csv_build_units(dwells)
                        if ticker is not None:
                            ticker.start(total_est)
                        schedule, blocks = build_csv_playback_plan(
                            dwells,
                            progress=_ThrottledBuildProgress(total_est, _on_tick)
                            if total_est > 0
                            else None,
                        )
                        try:
                            st = path.stat()
                            mtime_ns, size = int(st.st_mtime_ns), int(st.st_size)
                        except OSError:
                            mtime_ns, size = 0, 0
                        prepared = CachedCsvPlayback(
                            path=path,
                            mtime_ns=mtime_ns,
                            size=size,
                            config_tag=_csv_playback_config_tag(),
                            dwells=dwells,
                            schedule=schedule,
                            blocks=blocks,
                            build_ms=(time.perf_counter() - t_build0) * 1000.0,
                        )
                        with _csv_playback_cache_lock:
                            _csv_playback_cache[_csv_cache_key(path)] = prepared
                        build_sec = time.perf_counter() - t_build0
                        print(
                            f"{_PRINT_PREFIX} Play 전 빌드 완료: {path.name} | "
                            f"소요 {build_sec:.1f}s",
                            flush=True,
                        )

                        self._post_apply_cached_timeline_ui(prepared, wait=True)
                    finally:
                        if ticker is not None:
                            ticker.stop()
                        set_csv_playback_compact_log(False)

                run_simulation_from_csv(
                    self._registry,
                    self._scheduler,
                    csv_path=str(path),
                    speed_scale=sp,
                    prepared=prepared,
                    process_only=process_only,
                )
            except Exception as exc:
                print(f"{_PRINT_PREFIX} CSV Play 오류: {exc}", flush=True)
            finally:
                set_csv_play_progress_ui_callback(None)
                set_csv_play_timeline_highlight_callback(None)
                clear_csv_play_timeline_highlight()

                def _ui_clear() -> None:
                    self._apply_schedule_row_highlight(frozenset())

                _post_kit_main_thread(_ui_clear)
                self._csv_play_thread = None

        self._csv_play_thread = threading.Thread(
            target=_worker, daemon=True, name="lam-sim-csv-play"
        )
        self._csv_play_thread.start()

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
    "apply_material_binding_to_prim",
    "start_csv_play_material_binding_test",
    "run_csv_timed_playback",
    "request_stop_csv_playback",
    "clear_csv_playback_stop",
    "csv_playback_stop_requested",
    "set_csv_playback_compact_log",
    "is_csv_playback_compact_log",
    "set_csv_play_progress_ui_callback",
    "set_csv_play_timeline_highlight_callback",
    "register_csv_play_timeline_window",
    "unregister_csv_play_timeline_window",
    "clear_csv_play_timeline_highlight",
    "format_csv_playback_schedule_row",
    "is_csv_bulk_build_active",
    "CachedCsvPlayback",
    "get_cached_csv_playback",
    "clear_csv_playback_cache",
    "prepare_csv_playback",
    "build_and_cache_csv_playback",
    "build_csv_playback_schedule_meta",
    "CsvPlaybackScheduleEntry",
    "CsvTimedPlaybackBlock",
    "build_lot_id_to_foup_index",
    "normalize_csv_timeline",
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
