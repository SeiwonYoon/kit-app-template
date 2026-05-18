"""LAM 시뮬: CSV dwell → (향후) 시퀀스 스텝 JSON → 재생.

이 파일을 읽는 순서(구조만):
  1) **상단 상수** — ``LOGICAL:...`` 같은 내부 슬롯 이름, CSV 시간 모드, VTM 좌우 스왑 여부.
  2) **``default_lam_sim_virtual_config()``** — USD prim 경로, ATM/VTM **클립 프레임 구간**,
     슬롯별 Z 델타, VTM 목표 Yaw 등 “숫자·경로 설정”의 **한 곳 모음**.
  3) **``MODULE_NM_TO_SLOT_KEY``** — CSV 의 ``module_nm`` 문자열이 위 슬롯 키로 바뀌는 표
     (``build_default_module_nm_to_slot_key()``).
  4) **CSV 파싱** — 행을 읽어 dwell 리스트로 만듦.
  5) **``log_virtual_timeline_from_dwells``** — 콘솔에 이송·머무름 텍스트 로그.
  6) **dwell 간 이송** — ``build_steps_for_dwell_transfer`` 가 ATM/VTM 매크로와 동일 규칙으로
     스텝 JSON 을 만들고 ``run_simulation_from_csv`` 가 ``LamSequenceRunner`` 로 재생한다.
  7) **동작별 함수** — ``atm_arm_to_foup1`` 등 Kit 스크립트 창에서도 단독 호출 가능.

용어를 짧게:
  - **dwell** — CSV 한 줄: 웨이퍼가 어떤 모듈에 **머문 시간 구간**.
  - **slot_key** — 코드 안에서 쓰는 위치 이름 (예: ``foup1_3``, ``chamber2``, ``LOGICAL:ATM_ARM``).
  - **클립** — USD 애니의 timeSamples 구간(in/out 프레임). ``LamAtmStationClips`` / ``LamVtmDualEeStationClips``.

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
# (마스터 스테이지 ``USD_TIMELINE`` 으로 프레임을 스크럽하지 않음 — ``_lam_ts_step``.)
LOGICAL_SLOT_ATM_ARM: str = "LOGICAL:ATM_ARM"
LOGICAL_SLOT_VTM_EE_L: str = "LOGICAL:VTM_EE_L"
LOGICAL_SLOT_VTM_EE_R: str = "LOGICAL:VTM_EE_R"


# ---------------------------------------------------------------------------
# 2) ~ 3) 설정 묶음 — **USD 경로·클립 프레임·Z·Yaw** 는 ``default_lam_sim_virtual_config()`` 안만 고친다.
# ---------------------------------------------------------------------------
# - ``atm_clip_by_slot_key`` / ``vtm_clip_by_slot_key`` : 물리 slot_key 별 in/out (**정식 SSOT**, LAM_Equipment_Model §4.3).
# - ``atm_clip_by_station`` / ``vtm_clip_by_station`` : 해당 슬롯이 위에 없을 때만 쓰는 **폴백** 테이블.
# - ``z_slot_delta_m`` + ``z_table_authored_baseline_m`` + ``atm_z_usd_world_offset_m`` :
#   슬롯마다 ATM 높이 stage 가 갈 절대 Z. (레거시: ``z_baseline_applied_m`` ≠ table 이면 그 차이를 오프셋에 합산.)
# - **동작 이름 함수**(예: ``atm_arm_to_foup1``)는 아래 §「동작별 매크로」에 **실제 def** 로 둔다.
#


@dataclass(frozen=True)
class LamClipInOut:
    """한 번의 **집기** 또는 **내려놓기** 동작에 쓰는 USD timeSamples **in / out** 두 구간.

    ``frames_in``: EE 가 스테이션 쪽으로 진입해 접촉~(그립 또는 릴리스) 까지.
    ``frames_out``: 그 직후 EE 가 빠져나오는 구간(집기 직후면 웨이퍼를 든 상태).
    """

    frames_in: Tuple[int, int]
    frames_out: Tuple[int, int]


@dataclass(frozen=True)
class LamAtmStationClips:
    """ATM 이 **이 물리 슬롯**(또는 폴백 시 스테이션 종류)에 대해 쓰는 클립 쌍.

    ``pick_from``: 해당 슬롯에 웨이퍼가 있을 때 **집어 오는** in/out.
    ``place_to``: ATM 이 그 슬롯에 **내려놓는** in/out.
    """

    pick_from: LamClipInOut
    place_to: LamClipInOut


@dataclass(frozen=True)
class LamVtmDualEeStationClips:
    """VTM 이 **이 물리 슬롯**(챔버 1칸·에어록 슬롯 1칸 등)에서 좌·우 EE 각각 쓰는 클립.

    ``left_*`` = ``LOGICAL:VTM_EE_L`` (TransferChamber-EndEffector1 기본 매핑),
    ``right_*`` = ``LOGICAL:VTM_EE_R`` (EndEffector2).
    """

    left_pick_from: LamClipInOut
    left_place_to: LamClipInOut
    right_pick_from: LamClipInOut
    right_place_to: LamClipInOut


def atm_clip_station_key_for_slot(slot_key: str) -> Optional[str]:
    """물리 ``slot_key`` → ``atm_clip_by_station`` 에 쓸 스테이션 키. ATM 구간이 아니면 None."""
    if slot_key.startswith("foup1_"):
        return "foup1"
    if slot_key.startswith("foup2_"):
        return "foup2"
    if slot_key.startswith("foup3_"):
        return "foup3"
    if slot_key.startswith("buffer3_"):
        return "buffer3"
    if slot_key.startswith("buffer4_"):
        return "buffer4"
    if slot_key == "aligner":
        return "aligner"
    if slot_key.startswith("cooling_"):
        return "cooling"
    if slot_key.startswith("airlock1_"):
        return "airlock1"
    if slot_key.startswith("airlock2_"):
        return "airlock2"
    return None


def vtm_clip_station_key_for_slot(slot_key: str) -> Optional[str]:
    """물리 ``slot_key`` → ``vtm_clip_by_station`` 키. VTM 대상이 아니면 None."""
    if slot_key.startswith("chamber"):
        rest = slot_key[len("chamber") :]
        if rest.isdigit() and 1 <= int(rest) <= 5:
            return slot_key
    if slot_key.startswith("airlock1_"):
        return "airlock1"
    if slot_key.startswith("airlock2_"):
        return "airlock2"
    return None


def build_default_module_nm_to_slot_key() -> Dict[str, str]:
    """CSV ``module_nm`` → 내부 ``slot_key`` (§6) 기본 매핑.

    근거: ``docs/LAM_Equipment_Model.md`` §5.1.5. 추가 문자열(예: ``ATM-FOUP*-iSlot*``)은
    샘플·현장 데이터에 맞춰 이 함수 안에서만 늘린다.
    """
    ee_l = LOGICAL_SLOT_VTM_EE_R if VTM_END_EFFECTOR_SWAP_HANDS else LOGICAL_SLOT_VTM_EE_L
    ee_r = LOGICAL_SLOT_VTM_EE_L if VTM_END_EFFECTOR_SWAP_HANDS else LOGICAL_SLOT_VTM_EE_R
    m: Dict[str, str] = {
        "AtmArm-EndEffector11": LOGICAL_SLOT_ATM_ARM,
        "TransferChamber-EndEffector1": ee_l,
        "TransferChamber-EndEffector2": ee_r,
    }
    for i in (1, 2):
        m[f"AirLock1-iSlot{i}"] = f"airlock1_{i}"
        m[f"AirLock2-iSlot{i}"] = f"airlock2_{i}"
    for i in range(1, 8):
        m[f"CoolStationAL1PML{i}"] = f"cooling_{i}"
    for i in range(1, 26):
        m[f"CoolStationAL3PML{i}"] = f"buffer3_{i}"
        m[f"CoolStationAL4PML{i}"] = f"buffer4_{i}"
    for i in range(1, 6):
        m[f"PM1-PML{i}"] = f"chamber{i}"
    for foup_n in (1, 2, 3):
        for slot in range(1, 26):
            m[f"ATM-FOUP{foup_n}-iSlot{slot}"] = f"foup{foup_n}_{slot}"
    return m


MODULE_NM_TO_SLOT_KEY: Dict[str, str] = build_default_module_nm_to_slot_key()


def rebuild_module_nm_slot_mapping() -> None:
    """``VTM_END_EFFECTOR_SWAP_HANDS`` 등을 바꾼 뒤 VTM EE 매핑을 다시 쓸 때 호출."""
    global MODULE_NM_TO_SLOT_KEY
    MODULE_NM_TO_SLOT_KEY = build_default_module_nm_to_slot_key()


def _vtm_hand_side_for_transfer(prev_sk: str, curr_sk: str, *, pick_into_arm: bool) -> str:
    """이송이 VTM **좌팔/우팔** 중 어느 쪽 클립을 쓸지 ``left`` / ``right`` 문자열로 반환."""
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


@dataclass
class LamSimPlayVirtualConfig:
    """시뮬 가상 타임라인 로그 및 (향후) 시퀀스 스텝 생성이 참조하는 **설정 단일 묶음**.

    값을 바꾼 뒤에는 ``refresh_lam_sim_runtime_tables_from_config()`` 를 호출해
    ``WAFER_PRIM_BY_SLOT_KEY`` / ``SLOT_Z_METERS`` / ``ATM_HEIGHT_PRIM_PATH`` 를 갱신한다.
    """

    # True 이면 ``log_virtual_timeline_from_dwells`` 가 콘솔에 상세 타임라인을 남긴다.
    timeline_log_enabled: bool = True
    # ATM 높이 조절용 USD prim (MOVE_Z 절대값이 적용되는 대상).
    atm_height_prim_path: str = "/World/LAM/ATM/HeightStage"
    # ATM 팔 전체(또는 서브트리) timeSamples 가 정의된 **애니 인스턴스** prim 경로.
    atm_timesample_prim: str = ""
    # VTM 팔 timeSamples 가 정의된 **애니 인스턴스** prim 경로.
    vtm_timesample_prim: str = ""
    # ATM: 정식 SSOT. 물리 ``slot_key`` 마다 ``pick_from`` / ``place_to`` in·out (LAM_Equipment_Model.md §4.3).
    atm_clip_by_slot_key: Dict[str, LamAtmStationClips] = field(default_factory=dict)
    # ATM: 폴백. ``atm_clip_by_slot_key`` 에 없을 때 ``atm_clip_station_key_for_slot`` → 이 dict.
    atm_clip_by_station: Dict[str, LamAtmStationClips] = field(default_factory=dict)
    # VTM: 정식 SSOT. 물리 ``slot_key`` 마다 좌·우 EE 클립 묶음.
    vtm_clip_by_slot_key: Dict[str, LamVtmDualEeStationClips] = field(default_factory=dict)
    # VTM: 폴백. ``vtm_clip_by_slot_key`` 에 없을 때 유닛 키(chamber3, airlock1 …)로 이 dict.
    vtm_clip_by_station: Dict[str, LamVtmDualEeStationClips] = field(default_factory=dict)
    # ``atm_clip_by_station`` 에 없을 때 쓸 폴백 키 (로그·Runner 공통).
    atm_clip_fallback_station_key: str = "buffer3"
    # VTM만 사용. 키=내부 slot_key (chamber*, airlock* 등). 값=그 슬롯을 작업할 때 맞출 **절대 Yaw(도)**.
    # ATM 전용 슬롯(aligner, foup, buffer, cooling 등)은 **넣지 않음**.
    # (호환) 손 구분 없이 한 값만 둘 때 쓰는 flat dict. ``vtm_orient_yaw_by_slot_and_hand`` 에 해당 키가 있으면 그쪽이 우선.
    vtm_orient_yaw_deg_by_target_slot: Dict[str, float] = field(default_factory=dict)
    # VTM: ``slot_key`` → ``{"left": 도, "right": 도}`` 절대 Yaw(Z). 실무에서 좌·우가 다르면 여기만 채운다.
    vtm_orient_yaw_by_slot_and_hand: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # VTM Z축 회전·Z 이동 기준 USD prim (prompt §6·§7). 빈 문자열이면 해당 ROTATE/MOVE 스텝 생략.
    vtm_rotation_prim_path: str = ""
    vtm_position_prim_path: str = ""
    # VTM 높이 stage: ``vtm_z_slot_delta_m[slot] = (문서 절대 Z − vtm_z_table_authored_baseline_m)`` [m].
    vtm_z_table_authored_baseline_m: float = 0.101
    vtm_z_baseline_applied_m: float = 0.101
    # USD 로드 후 ``vtm_position_prim_path`` 기준 Z 가 문서 기준과 다를 때 **한 번에** 더할 차이 [m].
    # 공식: ``vtm_z_total_world_offset_m()`` = 본 필드 + (``vtm_z_baseline_applied_m`` − ``vtm_z_table_authored_baseline_m``).
    vtm_z_usd_world_offset_m: float = 0.0
    vtm_z_slot_delta_m: Dict[str, float] = field(default_factory=dict)
    # VTM 동작 후 ``ROTATE(rotate_from_initial=True)`` 로 돌아갈 절대 Z-Yaw(도). 보통 0.
    vtm_orient_idle_rz_deg: float = 0.0
    # ATM·VTM 슬롯 목표 Z 까지 ``MOVE`` 할 때 **고정** 시간 [s]. ``0`` 이면 ATM 만 기존 ``dz`` 기반 자동 길이.
    lam_sim_z_slot_move_duration_sec: float = 0.5
    # VTM 슬롯 향해 ``ROTATE`` 할 때 지속시간 [s].
    lam_sim_rotate_duration_sec: float = 0.4
    # ``z_slot_delta_m`` 을 작성할 때 기준으로 삼은 문서상 ATM(또는 높이 stage) 기준 Z [m]. 보통 zd() 와 같은 값.
    z_table_authored_baseline_m: float = 0.101
    # (레거시) 예전에는 ``z_baseline_applied_m`` 만 올려 전 슬롯을 보정했다. 신규는 ``atm_z_usd_world_offset_m`` 권장.
    z_baseline_applied_m: float = 0.101
    # USD 로드 후 ``atm_height_prim_path`` 기준 Z 가 문서 기준과 다를 때 **한 번에** 더할 차이 [m].
    # 공식: ``atm_z_total_world_offset_m()`` = 본 필드 + (``z_baseline_applied_m`` − ``z_table_authored_baseline_m``).
    atm_z_usd_world_offset_m: float = 0.0
    # slot_key -> (문서상 해당 슬롯 절대 Z − z_table_authored_baseline_m) [m].
    z_slot_delta_m: Dict[str, float] = field(default_factory=dict)
    # --- 웨이퍼 가시성용 USD prim (논리 EE 는 단일 경로, 나머지는 템플릿으로 슬롯 수만큼 생성) ---
    wafer_prim_atm_arm: str = ""
    wafer_prim_vtm_hand_l: str = ""
    wafer_prim_vtm_hand_r: str = ""
    wafer_tmpl_foup1: str = "/World/LAM/FOUP1/Slot_{i:02d}/Wafer"
    wafer_tmpl_foup2: str = "/World/LAM/FOUP2/Slot_{i:02d}/Wafer"
    wafer_tmpl_foup3: str = "/World/LAM/FOUP3/Slot_{i:02d}/Wafer"
    wafer_tmpl_buffer3: str = "/World/LAM/Buffer3/{slot}/Wafer"
    wafer_tmpl_buffer4: str = "/World/LAM/Buffer4/{slot}/Wafer"
    wafer_tmpl_cooling: str = "/World/LAM/Cooling/slot{i}/Wafer"
    wafer_tmpl_airlock: str = "/World/LAM/Airlock{a}/Slot{s}/Wafer"
    # ``slot_key == "aligner"`` 일 때 사용 (ATM 이 담당, VTM Yaw 테이블과 무관).
    wafer_prim_aligner: str = "/World/LAM/Aligner/Wafer"
    wafer_tmpl_chamber: str = "/World/LAM/VTM/Chamber{i}/Wafer"

    def atm_z_total_world_offset_m(self) -> float:
        """문서 좌표계 대비 Kit/USD 세계에서 ATM HeightStage Z 를 한꺼번에 옮길 양 [m].

        ``atm_z_usd_world_offset_m`` + (레거시) ``z_baseline_applied_m - z_table_authored_baseline_m``.
        문서에서 적은 슬롯 절대 Z·델타는 그대로 두고, **실측 기준 Z − 문서 기준 Z** 만
        ``atm_z_usd_world_offset_m`` 에 넣으면 모든 슬롯 ``effective_slot_z_m`` 이 동일하게 이동한다.
        """
        return float(self.atm_z_usd_world_offset_m) + float(self.z_baseline_applied_m) - float(
            self.z_table_authored_baseline_m
        )

    def vtm_z_total_world_offset_m(self) -> float:
        """VTM position prim 기준 문서 대비 USD 일괄 Z 오프셋 [m].

        ``vtm_z_usd_world_offset_m`` + (레거시) ``vtm_z_baseline_applied_m - vtm_z_table_authored_baseline_m``.
        """
        return float(self.vtm_z_usd_world_offset_m) + float(self.vtm_z_baseline_applied_m) - float(
            self.vtm_z_table_authored_baseline_m
        )

    def effective_slot_z_m(self, slot_key: str) -> Optional[float]:
        """문서 절대 Z + ``atm_z_total_world_offset_m()``. 테이블에 없으면 None.

        문서 절대 Z = ``z_table_authored_baseline_m + z_slot_delta_m[slot_key]``.
        """
        if slot_key not in self.z_slot_delta_m:
            return None
        authored_abs = float(self.z_table_authored_baseline_m + self.z_slot_delta_m[slot_key])
        return authored_abs + self.atm_z_total_world_offset_m()

    def effective_vtm_slot_z_m(self, slot_key: str) -> Optional[float]:
        """VTM 높이 ``vtm_position_prim_path`` 가 갈 **적용 절대 Z** [m].

        문서 절대 Z = ``vtm_z_table_authored_baseline_m + 델타`` 에 ``vtm_z_total_world_offset_m()`` 를 더한다.
        ``vtm_z_slot_delta_m`` 우선. 없으면 ``z_slot_delta_m`` 델타를 VTM 문서 기준에 더해 (한 테이블만 채운 호환).
        """
        off = self.vtm_z_total_world_offset_m()
        if slot_key in self.vtm_z_slot_delta_m:
            return float(self.vtm_z_table_authored_baseline_m + self.vtm_z_slot_delta_m[slot_key]) + off
        if slot_key in self.z_slot_delta_m:
            return float(self.vtm_z_table_authored_baseline_m + self.z_slot_delta_m[slot_key]) + off
        return None

    def build_wafer_prim_by_slot_key(self) -> Dict[str, str]:
        """§6 슬롯 키 및 논리 EE → 웨이퍼 메시(또는 대체) prim **절대 경로** 맵을 만든다."""
        paths: Dict[str, str] = {}

        def set_slot(prefix: str, n: int, tmpl: str) -> None:
            for i in range(1, n + 1):
                paths[f"{prefix}_{i}"] = tmpl.format(slot=f"{prefix}_{i}", i=i)

        for i in range(1, 26):
            paths[f"foup1_{i}"] = self.wafer_tmpl_foup1.format(i=i)
        set_slot("foup2", 25, self.wafer_tmpl_foup2)
        set_slot("foup3", 25, self.wafer_tmpl_foup3)
        set_slot("buffer3", 25, self.wafer_tmpl_buffer3)
        set_slot("buffer4", 25, self.wafer_tmpl_buffer4)
        for i in range(1, 8):
            paths[f"cooling_{i}"] = self.wafer_tmpl_cooling.format(i=i)
        for a in ("1", "2"):
            for s in ("1", "2"):
                paths[f"airlock{a}_{s}"] = self.wafer_tmpl_airlock.format(a=a, s=s)
        paths["aligner"] = self.wafer_prim_aligner
        for i in range(1, 6):
            paths[f"chamber{i}"] = self.wafer_tmpl_chamber.format(i=i)
        paths[LOGICAL_SLOT_ATM_ARM] = self.wafer_prim_atm_arm
        paths[LOGICAL_SLOT_VTM_EE_L] = self.wafer_prim_vtm_hand_l
        paths[LOGICAL_SLOT_VTM_EE_R] = self.wafer_prim_vtm_hand_r
        return paths


def _lam_clip(i: int, j: int, k: int, l: int) -> LamClipInOut:
    """``default_lam_sim_virtual_config`` 전용: in=(i,j) out=(k,l) 클립 한 쌍."""
    return LamClipInOut((i, j), (k, l))


def _atm_slot_clips(base: int) -> LamAtmStationClips:
    """한 **물리 슬롯**용 ATM 클립 한 벌을 ``base`` timeCode 기준으로 데모 생성.

    실장비에서는 **슬롯마다** USD 상 구간이 다르므로, ``default_lam_sim_virtual_config`` 에서
    슬롯마다 다른 ``base``(또는 수동 ``LamClipInOut``)를 넣어 ``atm_clip_by_slot_key`` 를 채운다.
    ``atm_clip_by_station`` 폴백에 동일 헬퍼를 쓸 수 있으나, 그건 “아직 슬롯별 표가 없을 때” 용도다.
    """
    return LamAtmStationClips(
        pick_from=_lam_clip(base, base + 30, base + 40, base + 70),
        place_to=_lam_clip(base + 80, base + 110, base + 120, base + 150),
    )


def _vtm_slot_dual_ee_clips(base: int) -> LamVtmDualEeStationClips:
    """한 **물리 슬롯**(챔버 1칸·에어록 슬롯 1칸 등)용 VTM 좌·우 EE 데모 클립.

    ``vtm_clip_by_slot_key`` 에 슬롯마다 다른 ``base`` 로 채운다. 유닛 단위 폴백은
    ``vtm_clip_by_station`` 만을 위한 것.
    """
    return LamVtmDualEeStationClips(
        left_pick_from=_lam_clip(base, base + 38, base + 48, base + 62),
        left_place_to=_lam_clip(base + 200, base + 235, base + 245, base + 268),
        right_pick_from=_lam_clip(base + 80, base + 118, base + 128, base + 148),
        right_place_to=_lam_clip(base + 300, base + 335, base + 345, base + 368),
    )


def _default_atm_clip_by_slot_key() -> Dict[str, LamAtmStationClips]:
    """§6 ATM 구간 물리 슬롯 전수에 대해 **슬롯별** 데모 클립을 만든다 (프레임 겹침 방지용 간격)."""
    out: Dict[str, LamAtmStationClips] = {}
    step = 220
    bi = 0
    for fq in (1, 2, 3):
        for s in range(1, 26):
            out[f"foup{fq}_{s}"] = _atm_slot_clips(bi * step)
            bi += 1
    for prefix in ("buffer3", "buffer4"):
        for s in range(1, 26):
            out[f"{prefix}_{s}"] = _atm_slot_clips(bi * step)
            bi += 1
    for s in range(1, 8):
        out[f"cooling_{s}"] = _atm_slot_clips(bi * step)
        bi += 1
    for a, b in (("1", "1"), ("1", "2"), ("2", "1"), ("2", "2")):
        out[f"airlock{a}_{b}"] = _atm_slot_clips(bi * step)
        bi += 1
    out["aligner"] = _atm_slot_clips(bi * step)
    return out


def _default_vtm_clip_by_slot_key() -> Dict[str, LamVtmDualEeStationClips]:
    """VTM 물리 슬롯: chamber 5 + 에어록 슬롯 4 — **슬롯마다** 서로 다른 데모 ``base``."""
    out: Dict[str, LamVtmDualEeStationClips] = {}
    for i in range(1, 6):
        out[f"chamber{i}"] = _vtm_slot_dual_ee_clips(i * 620)
    for idx, sk in enumerate(("airlock1_1", "airlock1_2", "airlock2_1", "airlock2_2")):
        out[sk] = _vtm_slot_dual_ee_clips(4000 + idx * 450)
    return out


def default_lam_sim_virtual_config() -> LamSimPlayVirtualConfig:
    """가상 경로·프레임·Z·VTM 목표 Yaw 등 **전부** 이 함수 안에서만 수정한다.

    Z:
        ``z0 = z_table_authored_baseline_m`` 를 문서 기준점으로 두고,
        ``zd(문서에서_쓴_절대Z미터) = 절대Z - z0`` 를 ``z_slot_delta_m`` (ATM) / ``vtm_z_slot_delta_m`` (VTM) 에 넣는다.
        USD 로드 후 기준 prim Z 가 문서와 다르면 ``atm_z_usd_world_offset_m`` / ``vtm_z_usd_world_offset_m`` 에
        **(실측 Z − 문서 ``z_table_*`` 기준 Z)** 만 넣으면 모든 슬롯 ``effective_*_z_m`` 이 같은 만큼 이동한다.
        (레거시) ``z_baseline_applied_m`` / ``vtm_z_baseline_applied_m`` 을 ``*_table`` 과 다르게 두던 방식은
        ``atm_z_total_world_offset_m()`` / ``vtm_z_total_world_offset_m()`` 에 합산되어 기존과 동일하게 동작한다.

    TimeSamples 클립:
        **슬롯별** in/out 은 ``atm_clip_by_slot_key`` / ``vtm_clip_by_slot_key`` 가 SSOT 이다
        (``LAM_Equipment_Model.md`` §4.3). ``*_clip_by_station`` 은 폴백만 채운다.
    """
    z0 = 0.101

    def zd(abs_m: float) -> float:
        return float(abs_m - z0)

    _atm_fb = _atm_slot_clips(0)
    _vtm_fb = _vtm_slot_dual_ee_clips(0)
    _atm_station_keys = (
        "foup1",
        "foup2",
        "foup3",
        "buffer3",
        "buffer4",
        "aligner",
        "cooling",
        "airlock1",
        "airlock2",
    )
    atm_clip_by_station = {k: _atm_fb for k in _atm_station_keys}
    vtm_clip_by_station = {**{f"chamber{i}": _vtm_fb for i in range(1, 6)}, "airlock1": _vtm_fb, "airlock2": _vtm_fb}

    _y_flat = {
        "chamber1": 0.0,
        "chamber2": -18.0,
        "chamber3": -36.0,
        "chamber4": -54.0,
        "chamber5": -72.0,
        "airlock1_1": 25.0,
        "airlock1_2": 25.0,
        "airlock2_1": -25.0,
        "airlock2_2": -25.0,
    }
    _y_by_hand = {k: {"left": float(v), "right": float(v)} for k, v in _y_flat.items()}

    _z_slot_delta = {
        LOGICAL_SLOT_ATM_ARM: zd(0.101),
        LOGICAL_SLOT_VTM_EE_L: zd(0.089),
        LOGICAL_SLOT_VTM_EE_R: zd(0.089),
        "buffer3_2": zd(0.102),
        "airlock1_1": zd(0.098),
        "airlock1_2": zd(0.098),
        "airlock2_1": zd(0.099),
        "airlock2_2": zd(0.099),
        **{f"chamber{i}": zd(0.088) for i in range(1, 6)},
        "cooling_3": zd(0.095),
        "foup1_1": zd(0.091),
    }
    _vtm_z_keys = (
        LOGICAL_SLOT_VTM_EE_L,
        LOGICAL_SLOT_VTM_EE_R,
        *(f"chamber{i}" for i in range(1, 6)),
        "airlock1_1",
        "airlock1_2",
        "airlock2_1",
        "airlock2_2",
    )
    _vtm_z_slot_delta = {k: _z_slot_delta[k] for k in _vtm_z_keys if k in _z_slot_delta}

    return LamSimPlayVirtualConfig(
        timeline_log_enabled=True,
        atm_height_prim_path="/World/LAM/ATM/HeightStage",
        atm_timesample_prim="/World/LAM/_VIRTUAL/ATM/ArmAnim_Instance",
        vtm_timesample_prim="/World/LAM/_VIRTUAL/VTM/ArmAnim_Instance",
        atm_clip_by_slot_key=_default_atm_clip_by_slot_key(),
        atm_clip_by_station=atm_clip_by_station,
        atm_clip_fallback_station_key="buffer3",
        vtm_clip_by_slot_key=_default_vtm_clip_by_slot_key(),
        vtm_clip_by_station=vtm_clip_by_station,
        vtm_orient_yaw_deg_by_target_slot=dict(_y_flat),
        vtm_orient_yaw_by_slot_and_hand=dict(_y_by_hand),
        vtm_rotation_prim_path="/World/LAM/_VIRTUAL/VTM/YawStage",
        vtm_position_prim_path="/World/LAM/_VIRTUAL/VTM/ZStage",
        vtm_z_table_authored_baseline_m=z0,
        vtm_z_baseline_applied_m=z0,
        vtm_z_usd_world_offset_m=0.0,
        vtm_z_slot_delta_m=dict(_vtm_z_slot_delta),
        vtm_orient_idle_rz_deg=0.0,
        lam_sim_z_slot_move_duration_sec=0.5,
        lam_sim_rotate_duration_sec=0.4,
        z_table_authored_baseline_m=z0,
        z_baseline_applied_m=z0,
        atm_z_usd_world_offset_m=0.0,
        z_slot_delta_m=dict(_z_slot_delta),
        wafer_prim_atm_arm="/World/LAM/ATM/RobotEndEffector/Wafer",
        wafer_prim_vtm_hand_l="/World/LAM/VTM/Robot/HandL/Wafer",
        wafer_prim_vtm_hand_r="/World/LAM/VTM/Robot/HandR/Wafer",
        wafer_tmpl_foup1="/World/LAM/FOUP1/Slot_{i:02d}/Wafer",
        wafer_tmpl_foup2="/World/LAM/FOUP2/Slot_{i:02d}/Wafer",
        wafer_tmpl_foup3="/World/LAM/FOUP3/Slot_{i:02d}/Wafer",
        wafer_tmpl_buffer3="/World/LAM/Buffer3/{slot}/Wafer",
        wafer_tmpl_buffer4="/World/LAM/Buffer4/{slot}/Wafer",
        wafer_tmpl_cooling="/World/LAM/Cooling/slot{i}/Wafer",
        wafer_tmpl_airlock="/World/LAM/Airlock{a}/Slot{s}/Wafer",
        wafer_prim_aligner="/World/LAM/Aligner/Wafer",
        wafer_tmpl_chamber="/World/LAM/VTM/Chamber{i}/Wafer",
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
    WAFER_PRIM_BY_SLOT_KEY = LAM_SIM_VIRTUAL_CONFIG.build_wafer_prim_by_slot_key()
    ATM_HEIGHT_PRIM_PATH = LAM_SIM_VIRTUAL_CONFIG.atm_height_prim_path
    SLOT_Z_METERS.clear()
    for sk in LAM_SIM_VIRTUAL_CONFIG.z_slot_delta_m:
        zv = LAM_SIM_VIRTUAL_CONFIG.effective_slot_z_m(sk)
        if zv is not None:
            SLOT_Z_METERS[sk] = zv


refresh_lam_sim_runtime_tables_from_config()


def build_default_wafer_prim_paths() -> Dict[str, str]:
    """호환용 별칭: ``LAM_SIM_VIRTUAL_CONFIG.build_wafer_prim_by_slot_key()`` 와 동일."""
    return LAM_SIM_VIRTUAL_CONFIG.build_wafer_prim_by_slot_key()


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


def _lam_ts_step(
    ref_prim: str,
    start_frame: int,
    end_frame: int,
    *,
    desc: str,
    speed: float = 1.0,
) -> Dict[str, Any]:
    """인스턴스 timeSamples 구간 재생 (**TIMESAMPLES_REPLAY** 만 사용 — USD_TIMELINE 금지).

    ``ref_prim`` 이 비면 Runner 가 재생할 prim 이 없으므로 호출 전에 반드시 채운다.
    """
    rp = (ref_prim or "").strip()
    if not rp:
        _lam_sim_log_build(
            "ts",
            "TIMESAMPLES_REPLAY: ref_prim 이 비었습니다. "
            "`simulation_play.py` → `LamSimPlayVirtualConfig.atm_timesample_prim` / "
            "`vtm_timesample_prim` 또는 `default_lam_sim_virtual_config()` 인자를 확인하세요.",
        )
    return {
        "type": "TIMESAMPLES_REPLAY",
        "ref": {
            "prim_path": rp,
            "guid": "",
            "instance_id": "",
            "source_asset": "",
        },
        "mode": "MANUAL",
        "start_frame": int(start_frame),
        "end_frame": int(end_frame),
        "speed_scale": float(speed),
        "loop": False,
        "offset_correction_enabled": False,
        "offset_correct_prims": "",
        **_lam_step_tail(desc),
    }


def _lam_move_step(
    prim: str, dx: float, dy: float, dz: float, duration: float, desc: str
) -> Dict[str, Any]:
    return {
        "type": "MOVE",
        "prim": prim,
        "duration": float(duration),
        "dx": float(dx),
        "dy": float(dy),
        "dz": float(dz),
        **_lam_step_tail(desc),
    }


def _lam_rotate_step(
    prim: str,
    rx: float,
    ry: float,
    rz: float,
    duration: float,
    desc: str,
    *,
    rotate_from_initial: bool = True,
) -> Dict[str, Any]:
    """``lam_sequence_engine`` ROTATE — ``rotate_from_initial=True`` 이면 (rx,ry,rz) 는 절대 목표각(도)."""
    return {
        "type": "ROTATE",
        "prim": prim,
        "duration": float(duration),
        "rx": float(rx),
        "ry": float(ry),
        "rz": float(rz),
        "rotate_from_initial": bool(rotate_from_initial),
        **_lam_step_tail(desc),
    }


def _lam_vis_step(prim: str, visible: bool, duration: float, desc: str) -> Dict[str, Any]:
    return {
        "type": "SET_PRIM_VISIBILITY",
        "prim": prim,
        "visible": bool(visible),
        "duration": float(duration),
        **_lam_step_tail(desc),
    }


def _lam_delay_step(duration: float, desc: str) -> Dict[str, Any]:
    return {"type": "DELAY", "duration": float(duration), **_lam_step_tail(desc)}


def _lam_estimate_raw_duration_sec(steps: LamSimJsonSteps) -> float:
    from .lam_types import LAM_FIXED_FPS

    tps = float(LAM_FIXED_FPS)
    total = 0.0
    for st in steps:
        t = str(st.get("type") or "").upper()
        if t in ("MOVE", "ROTATE", "DELAY", "SET_PRIM_VISIBILITY"):
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
    """``LamSequenceRunner`` 로 스텝 실행. ``target_duration_sec`` 가 있으면 합산 길이에 맞춰 배속 조정."""
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


def atm_arm_to_atm_slot(
    *,
    slot_key: str,
    duration_sec: float,
    pick_or_place: str,
) -> LamSimJsonSteps:
    """ATM 이 **물리 slot_key** (FOUP/버퍼/에어록 ATM 구간 등)에 접근할 때의 스텝 배열.

    클립은 ``resolve_atm_clips_for_slot_key`` 로 고른다. ``atm_clip_station_key_for_slot`` 이
    None 이고 ``atm_clip_by_slot_key`` 에도 없으면 빈 배열을 반환한다.

    ``duration_sec`` 는 API 호환용으로 받으며, 스텝 길이는 클립 프레임·Z ``MOVE`` 로 결정된다.
    Z ``MOVE`` 시간은 ``lam_sim_z_slot_move_duration_sec``(>0) 이면 그 값, 아니면 ``dz`` 기반 자동.
    """
    sk = (slot_key or "").strip()
    if not sk:
        _lam_sim_log_build(
            "atm",
            "중단: `slot_key` 가 비었습니다. `atm_arm_to_atm_slot(slot_key=...)` 호출 인자를 확인하세요.",
        )
        return []
    refresh_lam_sim_runtime_tables_from_config()
    cfg = LAM_SIM_VIRTUAL_CONFIG
    if atm_clip_station_key_for_slot(sk) is None and sk not in cfg.atm_clip_by_slot_key:
        _lam_sim_log_build(
            "atm",
            f"중단: slot_key={sk!r} 에 대한 ATM 클립이 없습니다. "
            f"`simulation_play.py` 의 `LamSimPlayVirtualConfig.atm_clip_by_slot_key` 에 해당 키를 추가하거나, "
            f"`atm_clip_by_station` 폴백·`atm_clip_station_key_for_slot()` 매핑을 확인하세요. "
            f"(기본값은 `default_lam_sim_virtual_config()` 의 `_default_atm_clip_by_slot_key()` 등.)",
        )
        return []
    _, prof = resolve_atm_clips_for_slot_key(cfg, sk)
    mode = str(pick_or_place or "").strip().lower()
    clip = prof.place_to if mode == "place" else prof.pick_from
    ci, co = clip.frames_in, clip.frames_out
    from .lam_types import LAM_FIXED_FPS as _FPS

    slot_wafer = (WAFER_PRIM_BY_SLOT_KEY.get(sk) or "").strip()
    arm_wafer = (cfg.wafer_prim_atm_arm or "").strip()
    hz = (cfg.atm_height_prim_path or "").strip()
    atm_anim = (cfg.atm_timesample_prim or "").strip()

    if not slot_wafer:
        _lam_sim_log_build(
            "atm",
            f"웨이퍼 prim 없음: slot_key={sk!r} → `WAFER_PRIM_BY_SLOT_KEY` 에 경로 없음. "
            f"`simulation_play.py` → `LamSimPlayVirtualConfig` 의 `wafer_tmpl_*` / `build_wafer_prim_by_slot_key()` "
            f"및 `refresh_lam_sim_runtime_tables_from_config()` 호출 여부를 확인하세요.",
        )
    if not arm_wafer:
        _lam_sim_log_build(
            "atm",
            "ATM 팔 웨이퍼 prim 없음: `wafer_prim_atm_arm` 이 비었습니다. "
            "`default_lam_sim_virtual_config()` 의 `wafer_prim_atm_arm` 인자를 실제 USD 경로로 채우세요.",
        )
    if not atm_anim:
        _lam_sim_log_build(
            "atm",
            "ATM timeSamples 재생 불가 → in/out 구간은 `DELAY` 로만 길이 맞춤. "
            "실제 애니를 돌리려면 `LamSimPlayVirtualConfig.atm_timesample_prim` 에 "
            "timeSamples 인스턴스 prim 절대 경로를 넣으세요 (`default_lam_sim_virtual_config()`). "
            "정책: 프레임 재생은 `TIMESAMPLES_REPLAY` 만 사용(USD_TIMELINE 미사용).",
        )
    z_slot = cfg.effective_slot_z_m(sk)
    z_arm = cfg.effective_slot_z_m(LOGICAL_SLOT_ATM_ARM)
    if z_slot is None:
        _lam_sim_log_build(
            "atm",
            f"슬롯 Z 미정의: slot_key={sk!r} → `z_slot_delta_m` 에 키가 없습니다. "
            f"`LamSimPlayVirtualConfig.z_slot_delta_m` / `z_table_authored_baseline_m` — "
            f"`simulation_play.py` 의 `default_lam_sim_virtual_config()` 내 `_z_slot_delta` 등.",
        )
    if z_arm is None:
        _lam_sim_log_build(
            "atm",
            f"ATM_ARM 슬롯 Z 미정의: `z_slot_delta_m['{LOGICAL_SLOT_ATM_ARM}']` 없음. "
            f"팔 높이 기준을 넣으려면 해당 논리 키를 `z_slot_delta_m` 에 추가하세요.",
        )
    zb = float(cfg.z_table_authored_baseline_m) + float(cfg.atm_z_total_world_offset_m())
    zt = float(z_slot) if z_slot is not None else zb
    za = float(z_arm) if z_arm is not None else zb
    dz = float(zt - za)
    zcfg = float(cfg.lam_sim_z_slot_move_duration_sec or 0.0)
    move_dur = zcfg if zcfg > 1e-9 else min(2.0, max(0.15, abs(dz) * 120.0))

    if not hz and abs(dz) > 1e-6:
        _lam_sim_log_build(
            "atm",
            "ATM Height Z MOVE 생략: `atm_height_prim_path` 가 비어 있어 dz 는 계산됐지만 MOVE 스텝을 넣지 않습니다. "
            "`LamSimPlayVirtualConfig.atm_height_prim_path` — `simulation_play.py` 의 `default_lam_sim_virtual_config()`.",
        )

    if mode == "place":
        v0_slot, v0_arm, v1_slot, v1_arm = False, True, True, False
    else:
        v0_slot, v0_arm, v1_slot, v1_arm = True, False, False, True

    steps: LamSimJsonSteps = []
    if slot_wafer and arm_wafer:
        steps.append(_lam_vis_step(slot_wafer, v0_slot, 0.04, "wafer: ATM 슬롯 초기"))
        steps.append(_lam_vis_step(arm_wafer, v0_arm, 0.04, "wafer: ATM 팔 초기"))
    if hz and abs(dz) > 1e-9:
        steps.append(_lam_move_step(hz, 0.0, 0.0, dz, move_dur, "ATM HeightStage Z"))
    if atm_anim:
        steps.append(_lam_ts_step(atm_anim, ci[0], ci[1], desc="ATM 클립 in"))
    else:
        steps.append(_lam_delay_step(max(0.05, (ci[1] - ci[0]) / float(_FPS)), "ATM 클립 in 대체(DELAY)"))
    if slot_wafer and arm_wafer and mode != "visit":
        steps.append(_lam_vis_step(slot_wafer, v1_slot, 0.04, "wafer: in 직후 교차"))
        steps.append(_lam_vis_step(arm_wafer, v1_arm, 0.04, "wafer: in 직후 교차"))
    if atm_anim:
        steps.append(_lam_ts_step(atm_anim, co[0], co[1], desc="ATM 클립 out"))
    else:
        steps.append(_lam_delay_step(max(0.05, (co[1] - co[0]) / float(_FPS)), "ATM 클립 out 대체(DELAY)"))
    if hz and abs(dz) > 1e-9:
        steps.append(_lam_move_step(hz, 0.0, 0.0, -dz, move_dur, "ATM HeightStage Z 복귀"))

    n_ts = sum(1 for s in steps if str(s.get("type")).upper() == "TIMESAMPLES_REPLAY")
    n_del = sum(1 for s in steps if str(s.get("type")).upper() == "DELAY")
    _lam_sim_log_build(
        "atm",
        f"조립 완료: slot_key={sk!r} mode={mode!r} steps={len(steps)} "
        f"(TIMESAMPLES_REPLAY={n_ts}, DELAY={n_del}, MOVE/ROTATE/VIS 는 로그 생략).",
    )

    _lam_sim_publish_json(steps)
    return steps


def atm_arm_to_foup(
    foup_index: int,
    *,
    slot_index: int,
    duration_sec: float,
    pick_or_place: str,
) -> LamSimJsonSteps:
    """ATM 이 FOUP 슬롯에 접근해 집기/내려놓기 할 때의 스텝 배열.

    스텝은 ``LamSequenceRunner`` 스키마(dict)이며, 실행 직전 직렬화 문자열은
    ``LAM_SIM_LAST_BUILT_JSON`` 에 기록된다.

    내용: ``SET_PRIM_VISIBILITY`` 로 슬롯/팔 웨이퍼 가시성 교차, ``MOVE`` 로 ATM 높이 Z,
    ``TIMESAMPLES_REPLAY`` 로 ``atm_timesample_prim`` 클립(in/out). 애니 prim 이 비어 있으면
    ``DELAY`` 로 구간 길이만 맞춘다.
    """
    if foup_index not in (1, 2, 3) or not (1 <= slot_index <= 25):
        _lam_sim_log_build(
            "atm",
            f"FOUP 호출 인자 오류: foup_index={foup_index!r} slot_index={slot_index!r} "
            f"(허용: foup 1~3, slot 1~25). `atm_arm_to_foup` 호출부 확인.",
        )
        return []
    return atm_arm_to_atm_slot(
        slot_key=f"foup{foup_index}_{slot_index}",
        duration_sec=duration_sec,
        pick_or_place=pick_or_place,
    )


def atm_arm_to_foup1(
    *,
    slot_index: int,
    duration_sec: float,
    pick_or_place: str = "pick",
) -> LamSimJsonSteps:
    """FOUP1 전용 이름. 내용은 ``atm_arm_to_foup(1, ...)`` 와 같다."""
    return atm_arm_to_foup(1, slot_index=slot_index, duration_sec=duration_sec, pick_or_place=pick_or_place)


def atm_arm_to_foup2(
    *,
    slot_index: int,
    duration_sec: float,
    pick_or_place: str = "pick",
) -> LamSimJsonSteps:
    return atm_arm_to_foup(2, slot_index=slot_index, duration_sec=duration_sec, pick_or_place=pick_or_place)


def atm_arm_to_foup3(
    *,
    slot_index: int,
    duration_sec: float,
    pick_or_place: str = "pick",
) -> LamSimJsonSteps:
    return atm_arm_to_foup(3, slot_index=slot_index, duration_sec=duration_sec, pick_or_place=pick_or_place)


def vtm_arm_move_to_chamber(
    *,
    hand: str,
    chamber_index: int,
    duration_sec: float,
    pick_or_place: str = "visit",
    target_slot_key: Optional[str] = None,
) -> LamSimJsonSteps:
    """VTM 한 손이 챔버·에어록 슬롯 등에 접근·집기/내려놓기/방문 시 스텝 배열.

    ``target_slot_key`` 가 있으면 ``chamber_index`` 대신 그 물리 키(예: ``chamber3``, ``airlock1_2``)로
    ``resolve_vtm_clips_for_slot_key`` 및 웨이퍼 prim 을 고른다.

    **실무 설정** (``default_lam_sim_virtual_config``): ``vtm_rotation_prim_path`` +
    ``_vtm_yaw_deg_for_slot_hand`` / ``vtm_orient_yaw_by_slot_and_hand`` → ``ROTATE``.
    ``vtm_position_prim_path`` + ``effective_vtm_slot_z_m`` → 슬롯 Z ``MOVE`` (지속시간
    ``lam_sim_z_slot_move_duration_sec``). 이후 ``vtm_timesample_prim`` in/out 및 가시성.
    끝에서 Z 복귀·``vtm_orient_idle_rz_deg`` 로 Yaw 복귀.
    """
    if hand not in ("left", "right"):
        _lam_sim_log_build("vtm", f"중단: hand={hand!r} — `left` 또는 `right` 만 허용. `vtm_arm_move_to_chamber` 호출부.")
        return []
    refresh_lam_sim_runtime_tables_from_config()
    cfg = LAM_SIM_VIRTUAL_CONFIG
    ts = (target_slot_key or "").strip()
    if ts:
        ck = ts
        if vtm_clip_station_key_for_slot(ck) is None and ck not in cfg.vtm_clip_by_slot_key:
            _lam_sim_log_build(
                "vtm",
                f"중단: target_slot_key={ck!r} 에 VTM 클립 없음. "
                f"`LamSimPlayVirtualConfig.vtm_clip_by_slot_key` 또는 `vtm_clip_by_station` — "
                f"`simulation_play.py` 의 `default_lam_sim_virtual_config()` / `_default_vtm_clip_by_slot_key()`.",
            )
            return []
    else:
        if not (1 <= chamber_index <= 5):
            _lam_sim_log_build(
                "vtm",
                f"중단: chamber_index={chamber_index!r} (1~5) 또는 `target_slot_key` 로 에어록 슬롯 지정.",
            )
            return []
        ck = f"chamber{chamber_index}"
    _, prof = resolve_vtm_clips_for_slot_key(cfg, ck)
    if hand == "left":
        cp_pick, cp_place = prof.left_pick_from, prof.left_place_to
    else:
        cp_pick, cp_place = prof.right_pick_from, prof.right_place_to
    mode = str(pick_or_place or "").strip().lower()
    clip = cp_place if mode == "place" else cp_pick
    ci, co = clip.frames_in, clip.frames_out
    from .lam_types import LAM_FIXED_FPS as _FPS

    dest_w = (WAFER_PRIM_BY_SLOT_KEY.get(ck) or "").strip()
    arm_wafer = (
        (cfg.wafer_prim_vtm_hand_l or "").strip()
        if hand == "left"
        else (cfg.wafer_prim_vtm_hand_r or "").strip()
    )
    vtm_anim = (cfg.vtm_timesample_prim or "").strip()

    if not dest_w:
        _lam_sim_log_build(
            "vtm",
            f"대상 웨이퍼 prim 없음: ck={ck!r} → `WAFER_PRIM_BY_SLOT_KEY`. "
            f"`wafer_tmpl_chamber` / `wafer_tmpl_airlock` 등 `LamSimPlayVirtualConfig` — `default_lam_sim_virtual_config()`.",
        )
    if not arm_wafer:
        _lam_sim_log_build(
            "vtm",
            f"손 웨이퍼 prim 없음: hand={hand!r} → `wafer_prim_vtm_hand_l` 또는 `wafer_prim_vtm_hand_r` "
            f"(`simulation_play.py` `default_lam_sim_virtual_config()`).",
        )
    if not vtm_anim:
        _lam_sim_log_build(
            "vtm",
            "VTM timeSamples 재생 불가 → in/out 은 `DELAY` 로 길이만 맞춤. "
            "`vtm_timesample_prim` 에 인스턴스 prim 절대 경로 설정. "
            "정책: 프레임 재생은 `TIMESAMPLES_REPLAY` 만 사용(USD_TIMELINE 미사용).",
        )

    ee_key = LOGICAL_SLOT_VTM_EE_L if hand == "left" else LOGICAL_SLOT_VTM_EE_R
    z_rest = cfg.effective_vtm_slot_z_m(ee_key)
    z_tgt = cfg.effective_vtm_slot_z_m(ck)
    zb_v = float(cfg.vtm_z_table_authored_baseline_m) + float(cfg.vtm_z_total_world_offset_m())
    zr = float(z_rest) if z_rest is not None else zb_v
    zt = float(z_tgt) if z_tgt is not None else zr
    dz = float(zt - zr)

    rot_prim = (cfg.vtm_rotation_prim_path or "").strip()
    pos_prim = (cfg.vtm_position_prim_path or "").strip()
    yaw_tgt = _vtm_yaw_deg_for_slot_hand(cfg, ck, hand)
    z_dur = float(cfg.lam_sim_z_slot_move_duration_sec or 0.0)
    if z_dur <= 1e-9:
        z_dur = 0.5
    r_dur = float(cfg.lam_sim_rotate_duration_sec or 0.0)
    if r_dur <= 1e-9:
        r_dur = 0.4
    idle_rz = float(cfg.vtm_orient_idle_rz_deg or 0.0)

    if not rot_prim and yaw_tgt is not None:
        _lam_sim_log_build(
            "vtm",
            f"ROTATE 생략: `vtm_rotation_prim_path` 비움. Yaw 목표는 있음(ck={ck!r}, hand={hand!r}). "
            f"`LamSimPlayVirtualConfig.vtm_rotation_prim_path` — `default_lam_sim_virtual_config()`.",
        )
    if yaw_tgt is None and rot_prim:
        _lam_sim_log_build(
            "vtm",
            f"ROTATE 생략: Yaw 테이블 없음 ck={ck!r} hand={hand!r}. "
            f"`vtm_orient_yaw_by_slot_and_hand` / `vtm_orient_yaw_deg_by_target_slot` — `default_lam_sim_virtual_config()`.",
        )
    if z_rest is None:
        _lam_sim_log_build(
            "vtm",
            f"EE Z 미정의: ee_key={ee_key!r} → `vtm_z_slot_delta_m` 또는 `z_slot_delta_m` + `vtm_z_table_authored_baseline_m`. "
            f"`simulation_play.py` `default_lam_sim_virtual_config()`.",
        )
    if z_tgt is None:
        _lam_sim_log_build(
            "vtm",
            f"슬롯 Z 미정의: ck={ck!r} → `vtm_z_slot_delta_m` / `z_slot_delta_m` (`default_lam_sim_virtual_config()`).",
        )
    if not pos_prim and abs(dz) > 1e-6:
        _lam_sim_log_build(
            "vtm",
            "VTM Z MOVE 생략: `vtm_position_prim_path` 비움. `LamSimPlayVirtualConfig.vtm_position_prim_path`.",
        )

    if mode == "place":
        v0c, v0h, v1c, v1h = False, True, True, False
    elif mode == "pick":
        v0c, v0h, v1c, v1h = True, False, False, True
    else:
        v0c, v0h, v1c, v1h = True, False, True, False

    steps: LamSimJsonSteps = []
    if dest_w and arm_wafer:
        steps.append(_lam_vis_step(dest_w, v0c, 0.04, "wafer: VTM 대상 슬롯 초기"))
        steps.append(_lam_vis_step(arm_wafer, v0h, 0.04, "wafer: VTM 손 초기"))
    if rot_prim and yaw_tgt is not None:
        steps.append(
            _lam_rotate_step(
                rot_prim,
                0.0,
                0.0,
                float(yaw_tgt),
                r_dur,
                "VTM Yaw→슬롯",
                rotate_from_initial=True,
            )
        )
    if pos_prim and abs(dz) > 1e-9:
        steps.append(_lam_move_step(pos_prim, 0.0, 0.0, dz, z_dur, "VTM Z→슬롯"))
    if vtm_anim:
        steps.append(_lam_ts_step(vtm_anim, ci[0], ci[1], desc="VTM 클립 in"))
    else:
        steps.append(_lam_delay_step(max(0.05, (ci[1] - ci[0]) / float(_FPS)), "VTM in 대체(DELAY)"))
    if dest_w and arm_wafer and mode != "visit":
        steps.append(_lam_vis_step(dest_w, v1c, 0.04, "wafer: in 직후 교차"))
        steps.append(_lam_vis_step(arm_wafer, v1h, 0.04, "wafer: in 직후 교차"))
    if vtm_anim:
        steps.append(_lam_ts_step(vtm_anim, co[0], co[1], desc="VTM 클립 out"))
    else:
        steps.append(_lam_delay_step(max(0.05, (co[1] - co[0]) / float(_FPS)), "VTM out 대체(DELAY)"))
    if pos_prim and abs(dz) > 1e-9:
        steps.append(_lam_move_step(pos_prim, 0.0, 0.0, -dz, z_dur, "VTM Z 복귀"))
    if rot_prim and yaw_tgt is not None:
        steps.append(
            _lam_rotate_step(
                rot_prim,
                0.0,
                0.0,
                idle_rz,
                r_dur,
                "VTM Yaw→대기",
                rotate_from_initial=True,
            )
        )

    n_ts = sum(1 for s in steps if str(s.get("type")).upper() == "TIMESAMPLES_REPLAY")
    n_del = sum(1 for s in steps if str(s.get("type")).upper() == "DELAY")
    _lam_sim_log_build(
        "vtm",
        f"조립 완료: ck={ck!r} hand={hand!r} mode={mode!r} steps={len(steps)} "
        f"(TIMESAMPLES_REPLAY={n_ts}, DELAY={n_del}).",
    )

    _lam_sim_publish_json(steps)
    return steps


def vtm_arm_right_to_chamber1(*, duration_sec: float, pick_or_place: str = "visit") -> LamSimJsonSteps:
    """우측 팔이 chamber1 으로 in/out (집기/내려놓기/접근만은 ``pick_or_place`` 로 구분 예정)."""
    return vtm_arm_move_to_chamber(hand="right", chamber_index=1, duration_sec=duration_sec, pick_or_place=pick_or_place)


def vtm_arm_right_to_chamber2(*, duration_sec: float, pick_or_place: str = "visit") -> LamSimJsonSteps:
    return vtm_arm_move_to_chamber(hand="right", chamber_index=2, duration_sec=duration_sec, pick_or_place=pick_or_place)


def vtm_arm_right_to_chamber3(*, duration_sec: float, pick_or_place: str = "visit") -> LamSimJsonSteps:
    return vtm_arm_move_to_chamber(hand="right", chamber_index=3, duration_sec=duration_sec, pick_or_place=pick_or_place)


def vtm_arm_right_to_chamber4(*, duration_sec: float, pick_or_place: str = "visit") -> LamSimJsonSteps:
    return vtm_arm_move_to_chamber(hand="right", chamber_index=4, duration_sec=duration_sec, pick_or_place=pick_or_place)


def vtm_arm_right_to_chamber5(*, duration_sec: float, pick_or_place: str = "visit") -> LamSimJsonSteps:
    return vtm_arm_move_to_chamber(hand="right", chamber_index=5, duration_sec=duration_sec, pick_or_place=pick_or_place)


def vtm_arm_left_to_chamber1(*, duration_sec: float, pick_or_place: str = "visit") -> LamSimJsonSteps:
    return vtm_arm_move_to_chamber(hand="left", chamber_index=1, duration_sec=duration_sec, pick_or_place=pick_or_place)


def vtm_arm_left_to_chamber2(*, duration_sec: float, pick_or_place: str = "visit") -> LamSimJsonSteps:
    return vtm_arm_move_to_chamber(hand="left", chamber_index=2, duration_sec=duration_sec, pick_or_place=pick_or_place)


def vtm_arm_left_to_chamber3(*, duration_sec: float, pick_or_place: str = "visit") -> LamSimJsonSteps:
    return vtm_arm_move_to_chamber(hand="left", chamber_index=3, duration_sec=duration_sec, pick_or_place=pick_or_place)


def vtm_arm_left_to_chamber4(*, duration_sec: float, pick_or_place: str = "visit") -> LamSimJsonSteps:
    return vtm_arm_move_to_chamber(hand="left", chamber_index=4, duration_sec=duration_sec, pick_or_place=pick_or_place)


def vtm_arm_left_to_chamber5(*, duration_sec: float, pick_or_place: str = "visit") -> LamSimJsonSteps:
    return vtm_arm_move_to_chamber(hand="left", chamber_index=5, duration_sec=duration_sec, pick_or_place=pick_or_place)


LAM_SIM_MACRO_CALLABLES: Dict[str, Any] = {
    "atm_arm_to_atm_slot": atm_arm_to_atm_slot,
    "atm_arm_to_foup": atm_arm_to_foup,
    "atm_arm_to_foup1": atm_arm_to_foup1,
    "atm_arm_to_foup2": atm_arm_to_foup2,
    "atm_arm_to_foup3": atm_arm_to_foup3,
    "vtm_arm_move_to_chamber": vtm_arm_move_to_chamber,
    "vtm_arm_right_to_chamber1": vtm_arm_right_to_chamber1,
    "vtm_arm_right_to_chamber2": vtm_arm_right_to_chamber2,
    "vtm_arm_right_to_chamber3": vtm_arm_right_to_chamber3,
    "vtm_arm_right_to_chamber4": vtm_arm_right_to_chamber4,
    "vtm_arm_right_to_chamber5": vtm_arm_right_to_chamber5,
    "vtm_arm_left_to_chamber1": vtm_arm_left_to_chamber1,
    "vtm_arm_left_to_chamber2": vtm_arm_left_to_chamber2,
    "vtm_arm_left_to_chamber3": vtm_arm_left_to_chamber3,
    "vtm_arm_left_to_chamber4": vtm_arm_left_to_chamber4,
    "vtm_arm_left_to_chamber5": vtm_arm_left_to_chamber5,
}


def run_lam_sim_script_line(registry: Any, scheduler: Any, line: str) -> None:
    """한 줄 매크로 호출. 키워드 인자만 허용 (``ast.literal_eval``)."""
    line = line.strip()
    if not line or line.startswith("#"):
        return
    tree = ast.parse(line, mode="eval")
    if not isinstance(tree, ast.Expression):
        raise ValueError("표현식 한 개만 허용합니다.")
    call = tree.body
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
        raise ValueError("macro 함수 호출만 허용합니다.")
    fn = LAM_SIM_MACRO_CALLABLES.get(call.func.id)
    if fn is None:
        raise ValueError(f"알 수 없는 매크로: {call.func.id}")
    kwargs: Dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise ValueError("positional 인자는 지원하지 않습니다.")
        kwargs[kw.arg] = ast.literal_eval(kw.value)
    steps = fn(**kwargs)
    dur = kwargs.get("duration_sec")
    run_lam_sim_steps(
        registry,
        scheduler,
        steps,
        target_duration_sec=float(dur) if dur is not None else None,
    )


def run_lam_sim_script_text(registry: Any, scheduler: Any, text: str) -> None:
    """여러 줄 — 주석·빈 줄 제외 후 ``run_lam_sim_script_line`` 순차 실행."""
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        run_lam_sim_script_line(registry, scheduler, ln)


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
    """연속 dwell 사이 이송을 **어느 장비 클립·Z·(VTM) Yaw** 로 묶을지 1차 분류.

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


def _vtm_yaw_deg_for_slot_hand(
    cfg: LamSimPlayVirtualConfig, slot_key: str, hand: str
) -> Optional[float]:
    """VTM 이 ``slot_key`` 를 ``hand``(``left``/``right``)로 작업할 때 맞출 절대 Yaw Z(도)."""
    hm = (hand or "left").strip().lower()
    if hm not in ("left", "right"):
        hm = "left"
    by_hand = cfg.vtm_orient_yaw_by_slot_and_hand.get(slot_key)
    if isinstance(by_hand, dict):
        for key in (hm, "left", "right"):
            if key in by_hand and by_hand[key] is not None:
                return float(by_hand[key])
    legacy = cfg.vtm_orient_yaw_deg_by_target_slot.get(slot_key)
    if legacy is not None:
        return float(legacy)
    return None


def _vtm_orient_yaw_deg_for_slot(slot_key: str) -> Optional[float]:
    """가상 타임라인 로그용: ``left`` 손 기준, 없으면 flat legacy."""
    return _vtm_yaw_deg_for_slot_hand(LAM_SIM_VIRTUAL_CONFIG, slot_key, "left")


def _resolve_atm_station_profile(cfg: LamSimPlayVirtualConfig, station_key: Optional[str]) -> Tuple[str, LamAtmStationClips]:
    """``atm_clip_by_station`` 조회. 없으면 ``atm_clip_fallback_station_key``."""
    key = station_key or cfg.atm_clip_fallback_station_key
    prof = cfg.atm_clip_by_station.get(key)
    if prof is None:
        key = cfg.atm_clip_fallback_station_key
        prof = cfg.atm_clip_by_station[key]
    return key, prof


def _resolve_vtm_station_profile(cfg: LamSimPlayVirtualConfig, station_key: Optional[str]) -> Tuple[str, LamVtmDualEeStationClips]:
    """``vtm_clip_by_station`` 조회. 없으면 ``chamber3``."""
    key = station_key or "chamber3"
    prof = cfg.vtm_clip_by_station.get(key)
    if prof is None:
        key = "chamber3"
        prof = cfg.vtm_clip_by_station[key]
    return key, prof


def resolve_atm_clips_for_slot_key(
    cfg: LamSimPlayVirtualConfig, slot_key: str
) -> Tuple[str, LamAtmStationClips]:
    """물리 ``slot_key`` 기준 ATM 클립(``pick_from`` / ``place_to``).

    ``atm_clip_by_slot_key[slot_key]`` 가 있으면 그것을 쓴다 (SSOT). 없으면
    ``atm_clip_station_key_for_slot`` → ``atm_clip_by_station`` (및 ``atm_clip_fallback_station_key``).
    """
    ovr = cfg.atm_clip_by_slot_key.get(slot_key)
    if ovr is not None:
        return slot_key, ovr
    station = atm_clip_station_key_for_slot(slot_key)
    return _resolve_atm_station_profile(cfg, station)


def resolve_vtm_clips_for_slot_key(
    cfg: LamSimPlayVirtualConfig, slot_key: str
) -> Tuple[str, LamVtmDualEeStationClips]:
    """물리 ``slot_key`` 기준 VTM 좌·우 EE 클립 묶음.

    ``vtm_clip_by_slot_key[slot_key]`` 가 있으면 그것을 쓴다 (SSOT). 없으면
    ``vtm_clip_station_key_for_slot`` → ``vtm_clip_by_station`` (및 ``chamber3`` 폴백).
    """
    ovr = cfg.vtm_clip_by_slot_key.get(slot_key)
    if ovr is not None:
        return slot_key, ovr
    station = vtm_clip_station_key_for_slot(slot_key)
    return _resolve_vtm_station_profile(cfg, station)


def log_virtual_timeline_from_dwells(dwells: List[DwellRecord]) -> None:
    """dwell 리스트를 시간 순으로 훑어 **가상** 이송·머무름을 콘솔에 상세 출력한다.

    사용 데이터:
        ``LAM_SIM_VIRTUAL_CONFIG`` — ``atm_clip_by_slot_key`` / ``vtm_clip_by_slot_key`` 오버라이드 및
        ``atm_clip_by_station`` / ``vtm_clip_by_station`` 폴백 (목적지·좌우 EE별 in/out),
        Prim, Z, ``vtm_orient_yaw_deg_by_target_slot``.

    Note:
        USD 를 쓰지 않는다. ``timeline_log_enabled`` 가 False면 아무 것도 출력하지 않는다.
    """
    if not LAM_SIM_VIRTUAL_CONFIG.timeline_log_enabled or not dwells:
        return

    refresh_lam_sim_runtime_tables_from_config()
    cfg = LAM_SIM_VIRTUAL_CONFIG
    print(f"{_PRINT_PREFIX} ========== VIRTUAL TIMELINE (no USD write) ==========", flush=True)
    print(
        f"{_PRINT_PREFIX} Z: atm_z_usd_world_offset_m={cfg.atm_z_usd_world_offset_m!r} "
        f"atm_z_total_world_offset_m={cfg.atm_z_total_world_offset_m()!r} "
        f"z_table_authored_baseline_m={cfg.z_table_authored_baseline_m!r} "
        f"z_baseline_applied_m(legacy)={cfg.z_baseline_applied_m!r} "
        f"(effective_z=문서절대Z+atm_z_total_world_offset_m)",
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
            prev = dwells[i - 1]
            _log_virtual_transfer(prev, d)

        wp = _virtual_wafer_prim(d.slot_key)
        z = _virtual_slot_z_m(d.slot_key)
        z_s = f"z_abs_m={z!r}" if z is not None else "z_abs_m=(none)"

        print(
            f"{_PRINT_PREFIX} [t={t0:.3f}s..{t1:.3f}s] DWELL#{i} cassette={d.cassette_id} "
            f"slot={d.slot_key} module={d.module_nm!r}",
            flush=True,
        )
        print(
            f"{_PRINT_PREFIX}   visibility: wafer ON-SLOT prim={wp!r} (others hidden for this wafer)",
            flush=True,
        )
        print(f"{_PRINT_PREFIX}   hold {z_s} process_tm={d.process_tm!r}s eqp_id={d.eqp_id!r}", flush=True)

        if _is_logical_arm_slot(d.slot_key):
            robot = "ATM" if d.slot_key == LOGICAL_SLOT_ATM_ARM else "VTM"
            anim = cfg.atm_timesample_prim if robot == "ATM" else cfg.vtm_timesample_prim
            print(
                f"{_PRINT_PREFIX}   idle_on_arm: robot={robot} TIMESAMPLES idle/hold prim={anim!r} "
                f"(no slot Z target)",
                flush=True,
            )
            if robot == "VTM" and i > 0:
                pv = dwells[i - 1]
                yaw = _vtm_orient_yaw_deg_for_slot(pv.slot_key)
                if yaw is not None:
                    print(
                        f"{_PRINT_PREFIX}   (hint) VTM_ORIENT_YAW_deg={yaw!r} toward_physical={pv.slot_key}",
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
    """두 dwell 사이(``curr.start_sec``) 가상 이송 로그.

    ATM: 이송에 관여한 **물리** ``slot_key`` 로 ``resolve_atm_clips_for_slot_key`` → ``pick_from`` / ``place_to``.
    VTM: 동일하게 **물리** ``slot_key`` 로 ``resolve_vtm_clips_for_slot_key`` + 좌/우 EE 로 구간 선택.
    """
    cfg = LAM_SIM_VIRTUAL_CONFIG
    t = curr.start_sec
    robot = _classify_transfer_robot(prev.slot_key, curr.slot_key)
    prev_p = _virtual_wafer_prim(prev.slot_key)
    curr_p = _virtual_wafer_prim(curr.slot_key)
    z_prev = _virtual_slot_z_m(prev.slot_key)
    z_curr = _virtual_slot_z_m(curr.slot_key)

    print(
        f"{_PRINT_PREFIX} ----- TRANSFER @ t={t:.3f}s robot={robot} "
        f"{prev.slot_key} -> {curr.slot_key} (cassette={curr.cassette_id}) -----",
        flush=True,
    )
    print(
        f"{_PRINT_PREFIX}   wafer_prim prev={prev_p!r} next={curr_p!r}",
        flush=True,
    )

    if robot == "ATM":
        going_to_arm = curr.slot_key == LOGICAL_SLOT_ATM_ARM
        leaving_arm = prev.slot_key == LOGICAL_SLOT_ATM_ARM
        station_raw: Optional[str] = None
        if going_to_arm:
            slot_for_clip = prev.slot_key
            sk_eff, prof = resolve_atm_clips_for_slot_key(cfg, slot_for_clip)
            active = prof.pick_from
            phase = "ATM_PICK_FROM_STATION"
        elif leaving_arm:
            slot_for_clip = curr.slot_key
            sk_eff, prof = resolve_atm_clips_for_slot_key(cfg, slot_for_clip)
            active = prof.place_to
            phase = "ATM_PLACE_TO_STATION"
        else:
            slot_for_clip = curr.slot_key
            sk_eff, prof = resolve_atm_clips_for_slot_key(cfg, slot_for_clip)
            active = prof.place_to
            phase = "ATM_INTER_SLOT_FALLBACK_USE_dst_PLACE_CLIP"
        station_raw = atm_clip_station_key_for_slot(slot_for_clip)
        print(
            f"{_PRINT_PREFIX}   {phase}: TIMESAMPLES prim={cfg.atm_timesample_prim!r} "
            f"clip_table={sk_eff!r} (station_map={station_raw!r} slot={slot_for_clip!r}) "
            f"active_in={active.frames_in!r} active_out={active.frames_out!r} | "
            f"pick_from_in={prof.pick_from.frames_in!r} pick_from_out={prof.pick_from.frames_out!r} | "
            f"place_to_in={prof.place_to.frames_in!r} place_to_out={prof.place_to.frames_out!r}",
            flush=True,
        )
        print(
            f"{_PRINT_PREFIX}   visibility_hint: sync wafer prim hide/show to active_in / active_out",
            flush=True,
        )
        if z_curr is not None or z_prev is not None:
            print(
                f"{_PRINT_PREFIX}   MOVE_Z_ABSOLUTE prim={ATM_HEIGHT_PRIM_PATH!r} "
                f"target_z_m={z_curr!r} (prev_z_m={z_prev!r}) "
                f"z_table={cfg.z_table_authored_baseline_m!r} "
                f"atm_z_total={cfg.atm_z_total_world_offset_m()!r}",
                flush=True,
            )

    else:  # VTM
        pick_into_arm = curr.slot_key in (LOGICAL_SLOT_VTM_EE_L, LOGICAL_SLOT_VTM_EE_R)
        place_from_arm = prev.slot_key in (LOGICAL_SLOT_VTM_EE_L, LOGICAL_SLOT_VTM_EE_R)
        z_prev = _virtual_slot_z_m_vtm(prev.slot_key)
        z_curr = _virtual_slot_z_m_vtm(curr.slot_key)
        if pick_into_arm:
            slot_for_clip = prev.slot_key
            sk_eff, dual = resolve_vtm_clips_for_slot_key(cfg, slot_for_clip)
            hand = _vtm_hand_side_for_transfer(prev.slot_key, curr.slot_key, pick_into_arm=True)
            if hand == "left":
                active = dual.left_pick_from
            else:
                active = dual.right_pick_from
            phase = "VTM_PICK_FROM_STATION"
        elif place_from_arm:
            slot_for_clip = curr.slot_key
            sk_eff, dual = resolve_vtm_clips_for_slot_key(cfg, slot_for_clip)
            hand = _vtm_hand_side_for_transfer(prev.slot_key, curr.slot_key, pick_into_arm=False)
            if hand == "left":
                active = dual.left_place_to
            else:
                active = dual.right_place_to
            phase = "VTM_PLACE_TO_STATION"
        else:
            slot_for_clip = (
                curr.slot_key
                if vtm_clip_station_key_for_slot(curr.slot_key)
                else (prev.slot_key if vtm_clip_station_key_for_slot(prev.slot_key) else curr.slot_key)
            )
            sk_eff, dual = resolve_vtm_clips_for_slot_key(cfg, slot_for_clip)
            hand = "left"
            active = dual.left_pick_from
            phase = "VTM_FALLBACK_NO_EE_DWELL_USE_LEFT_PICK_CLIP"
        station_raw = vtm_clip_station_key_for_slot(slot_for_clip)
        yaw_curr = _vtm_yaw_deg_for_slot_hand(cfg, curr.slot_key, hand)
        yaw_prev = _vtm_yaw_deg_for_slot_hand(cfg, prev.slot_key, hand)
        print(
            f"{_PRINT_PREFIX}   {phase}: TIMESAMPLES prim={cfg.vtm_timesample_prim!r} "
            f"clip_table={sk_eff!r} (station_map={station_raw!r} slot={slot_for_clip!r}) ee_hand={hand!r} "
            f"active_in={active.frames_in!r} active_out={active.frames_out!r}",
            flush=True,
        )
        if yaw_curr is not None or yaw_prev is not None:
            print(
                f"{_PRINT_PREFIX}   VTM_ORIENT_YAW_deg (hand={hand!r}): toward_curr={yaw_curr!r} "
                f"prev_target={yaw_prev!r}",
                flush=True,
            )
        else:
            print(
                f"{_PRINT_PREFIX}   VTM_ORIENT_YAW_deg: (no entry - add ``vtm_orient_yaw_by_slot_and_hand`` or "
                f"``vtm_orient_yaw_deg_by_target_slot`` in default_lam_sim_virtual_config)",
                flush=True,
            )
        print(
            f"{_PRINT_PREFIX}   visibility_hint: per-hand clip; chamber motion may be baked in USD",
            flush=True,
        )
        if z_curr is not None or z_prev is not None:
            print(
                f"{_PRINT_PREFIX}   (optional) slot Z ref prev={z_prev!r} curr={z_curr!r} "
                f"(VTM chamber path often baked in clip)",
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
    """연속 dwell (동일 ``cassette_id``) 사이 이송을 시퀀스 스텝 JSON 으로 만든다.

    ATM/VTM 분기·클립 선택은 ``_log_virtual_transfer`` 와 같은 규칙을 따른다.
    """
    if prev.cassette_id != curr.cassette_id:
        _lam_sim_log_build(
            "transfer",
            f"이송 생략(cassette 불일치): {prev.cassette_id} -> {curr.cassette_id} "
            f"{prev.slot_key!r} -> {curr.slot_key!r}. 동일 웨이퍼 투어만 한 번에 재생합니다.",
        )
        return []
    refresh_lam_sim_runtime_tables_from_config()
    dur = _csv_transfer_target_duration_sec(prev, curr)
    robot = _classify_transfer_robot(prev.slot_key, curr.slot_key)
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
        steps = atm_arm_to_atm_slot(slot_key=sk, duration_sec=dur, pick_or_place=po)
        if not steps:
            _lam_sim_log_build(
                "transfer",
                f"ATM 이송 스텝 0개: {prev.slot_key!r}->{curr.slot_key!r} slot_key={sk!r} mode={po!r}. "
                f"위 `[build:atm]` 로그 및 `atm_clip_by_slot_key` / `atm_timesample_prim` / `atm_height_prim_path` "
                f"(`simulation_play.py` `default_lam_sim_virtual_config()`).",
            )
        return steps
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
            else (prev.slot_key if vtm_clip_station_key_for_slot(prev.slot_key) else curr.slot_key)
        )
        hand = "left"
        po = "visit"
    cix = _vtm_chamber_index_for_move_slot(target)
    steps = vtm_arm_move_to_chamber(
        hand=hand,
        chamber_index=cix,
        duration_sec=dur,
        pick_or_place=po,
        target_slot_key=target,
    )
    if not steps:
        _lam_sim_log_build(
            "transfer",
            f"VTM 이송 스텝 0개: {prev.slot_key!r}->{curr.slot_key!r} target={target!r} hand={hand!r} mode={po!r}. "
            f"위 `[build:vtm]` 로그 및 `vtm_clip_by_slot_key` / `vtm_timesample_prim` "
            f"(`simulation_play.py` `default_lam_sim_virtual_config()`).",
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
            f"{_PRINT_PREFIX} Play: 이송 스텝이 비어 있음 (dwell={len(dwells)}, 설정·클립·prim 경로 확인).",
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
    n_vis = sum(1 for s in all_steps if str(s.get("type")).upper() == "SET_PRIM_VISIBILITY")
    print(
        f"{_PRINT_PREFIX} Play: LamSequenceRunner.run total_steps={len(all_steps)} "
        f"TIMESAMPLES_REPLAY={n_ts_all} DELAY={n_del_all} MOVE={n_move} ROTATE={n_rot} VIS={n_vis} "
        f"speed_scale={speed_scale!r}",
        flush=True,
    )
    if n_ts_all == 0:
        _lam_sim_log_build(
            "csv_play",
            "이 CSV 런에 TIMESAMPLES_REPLAY 스텝이 없습니다. 애니가 안 돌면 "
            "`atm_timesample_prim` / `vtm_timesample_prim` 이 비었는지 확인하세요 "
            "(`simulation_play.py` → `default_lam_sim_virtual_config()`).",
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
    """Kit ``omni.ui`` 창: CSV dwell 재생 + **매크로 스크립트** 편집·실행."""

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
        self._window = ui.Window(self.WINDOW_TITLE, width=580, height=520)
        with self._window.frame:
            with ui.VStack(spacing=6):
                ui.Label(
                    "CSV: ``lam/csv`` dwell 선택 후 [Play]. "
                    "아래 스크립트는 ``atm_arm_to_foup1(...)`` 등 **한 줄에 매크로 호출 하나**만 적는다.",
                    word_wrap=True,
                    height=40,
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
                ui.Label("매크로 스크립트 (한 줄 = 호출 하나, ``#`` 주석 가능)", height=18)
                try:
                    from omni.ui import SimpleStringModel  # type: ignore
                except Exception:
                    SimpleStringModel = None  # type: ignore
                if SimpleStringModel is not None:
                    self._script_model = SimpleStringModel(
                        'atm_arm_to_foup1(slot_index=1, duration_sec=3.0, pick_or_place="pick")\n'
                        "# vtm_arm_right_to_chamber1(duration_sec=4.0, pick_or_place=\"visit\")\n"
                    )
                    try:
                        ui.StringField(
                            model=self._script_model,
                            height=140,
                            multiline=True,
                        )
                    except TypeError:
                        ui.StringField(model=self._script_model, height=140)
                else:
                    ui.Label("SimpleStringModel 없음 — Kit 버전 확인.", height=40)
                with ui.HStack(spacing=6, height=28):
                    ui.Button("스크립트 실행", width=120, clicked_fn=self._on_script_run_clicked)
                    ui.Spacer()
                self._log_label = ui.Label("(대기)", height=64, word_wrap=True)

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
    "LamClipInOut",
    "LamAtmStationClips",
    "LamVtmDualEeStationClips",
    "atm_clip_station_key_for_slot",
    "vtm_clip_station_key_for_slot",
    "resolve_atm_clips_for_slot_key",
    "resolve_vtm_clips_for_slot_key",
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
    "atm_arm_to_atm_slot",
    "atm_arm_to_foup",
    "atm_arm_to_foup1",
    "atm_arm_to_foup2",
    "atm_arm_to_foup3",
    "vtm_arm_move_to_chamber",
    "vtm_arm_right_to_chamber1",
    "vtm_arm_right_to_chamber2",
    "vtm_arm_right_to_chamber3",
    "vtm_arm_right_to_chamber4",
    "vtm_arm_right_to_chamber5",
    "vtm_arm_left_to_chamber1",
    "vtm_arm_left_to_chamber2",
    "vtm_arm_left_to_chamber3",
    "vtm_arm_left_to_chamber4",
    "vtm_arm_left_to_chamber5",
    "ParsedCsvRow",
    "DwellRecord",
    "_find_lam_data_root",
    "get_lam_csv_dir",
    "list_lam_csv_paths",
    "build_default_module_nm_to_slot_key",
    "rebuild_module_nm_slot_mapping",
    "build_default_wafer_prim_paths",
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
    "LamSimulationCsvPlayWindow",
]


if __name__ == "__main__":
    # python -m morph.lam_control.simulation_play  (repo PYTHONPATH 설정 시)
    import sys

    p = sys.argv[1] if len(sys.argv) > 1 else None
    dwells = dry_run_print_dwells(p)
    log_virtual_timeline_from_dwells(dwells)
