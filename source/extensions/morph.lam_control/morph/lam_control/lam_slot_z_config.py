"""LAM 시뮬 — ATM / VTM 슬롯 Z (절대값 + 기준 대비 차이).

**이 파일만 수정**한다. ``simulation_play`` 는 ``load_atm_z_tables()`` / ``load_vtm_z_tables()`` 로
[m] 단위 baseline·delta 를 가져온다.

구조:
  1) ``*_Z_APPLIED_REFERENCE`` — 실제 기준 Z (prompt 의 「기준 z」). **이 값만 바꾸면**
     아래 ``*_SLOT_Z_DELTA`` 가 자동으로 다시 계산된다 (``refresh_slot_z_deltas()``).
  2) ``*_SLOT_Z_ABSOLUTE`` — prompt 에 적은 **절대 Z** (슬롯마다 수동).
  3) ``*_SLOT_Z_DELTA`` — **표시용** ``절대 Z − *_Z_APPLIED_REFERENCE`` (``refresh_slot_z_deltas()``).
     시뮬 ``effective`` 는 ``절대 Z − *_Z_DOCUMENT_REFERENCE + *_Z_APPLIED_REFERENCE``.

단위: ``*_SLOT_Z_ABSOLUTE`` / ``*_Z_*_REFERENCE`` / ``*_SLOT_Z_DELTA`` 는 **mm** (CAD).

- **HeightStage ``MOVE`` ``dz``** (``move_from_initial``): TBS 와 **동일 숫자 = mm** (편집기에 25.928 입력과 같음).
  → ``slot_z_move_target_dz()`` — ``Z_MM_TO_METERS`` **곱하지 않음**.
- ``load_atm_z_tables()`` / ``effective_*_m`` 등 레거시 [m] dict 만 ``Z_MM_TO_METERS`` 사용.

로봇팔(EE) Z 는 **포함하지 않음**.
"""

from __future__ import annotations

from typing import Dict, Tuple

# [m] 변환 — ``load_atm_z_tables()`` / ``effective_*_m`` 전용. MOVE ``dz`` 에는 미사용.
Z_MM_TO_METERS: float = 0.001

# MOVE ``dz`` = ``ATM_SLOT_Z_DELTA`` × 본 값. ``1.0`` → mm 그대로(25.928). ``0.001`` → m(0.025928).
Z_TBS_MOVE_UNIT_PER_MM: float = 1.0

# =============================================================================
# Z 동시 이동 대상 prim — Kit USD 실제 HeightStage/VTM Z 경로 (**여기만 수정**)
# → ``build_steps_for_event`` 자동 MOVE 의 ``prim`` 필드
# =============================================================================
ATM_Z_MOVE_PRIM_PATH: str = "/World/aaa/N_07_Laser_Cutting/_7_Laser_Cutting_Machine/link0"
VTM_Z_MOVE_PRIM_PATH: str = "/World/LAM/_VIRTUAL/VTM/ZStage"

# =============================================================================
# ATM — 기준 Z (prompt: 기준 z = 905.92)
# =============================================================================
ATM_Z_DOCUMENT_REFERENCE: float = 905.92  # prompt 원문 기준 z (절대 Z 테이블 작성 기준)
ATM_Z_APPLIED_REFERENCE: float = 905.92  # 실제 적용 기준 — 변경 시 effective Z 가 (abs - doc + applied) 로 이동


# --- ATM: 슬롯별 절대 Z [mm] (prompt 그대로) ---
ATM_SLOT_Z_ABSOLUTE: Dict[str, float] = {
    "foup1_1": 931.848,

    "foup2_1": 931.848,

    "foup3_1": 931.848,

    "foup1_2": 941.288,

    "foup2_2": 941.288,

    "foup3_2": 941.288,

    "foup1_3": 950.729,

    "foup2_3": 950.729,

    "foup3_3": 950.729,

    "foup1_4": 960.168,

    "foup2_4": 960.168,

    "foup3_4": 960.168,

    "foup1_5": 969.608,

    "foup2_5": 969.608,

    "foup3_5": 969.608,

    "foup1_6": 979.048,

    "foup2_6": 979.048,

    "foup3_6": 979.048,

    "foup1_7": 988.488,

    "foup2_7": 988.488,

    "foup3_7": 988.488,

    "foup1_8": 997.928,

    "foup2_8": 997.928,

    "foup3_8": 997.928,

    "foup1_9": 1007.368,

    "foup2_9": 1007.368,

    "foup3_9": 1007.368,

    "foup1_10": 1016.808,

    "foup2_10": 1016.808,

    "foup3_10": 1016.808,

    "foup1_11": 1026.248,

    "foup2_11": 1026.248,

    "foup3_11": 1026.248,

    "foup1_12": 1035.688,

    "foup2_12": 1035.688,

    "foup3_12": 1035.688,

    "foup1_13": 1045.128,

    "foup2_13": 1045.128,

    "foup3_13": 1045.128,

    "foup1_14": 1054.568,

    "foup2_14": 1054.568,

    "foup3_14": 1054.568,

    "foup1_15": 1064.008,

    "foup2_15": 1064.008,

    "foup3_15": 1064.008,

    "foup1_16": 1073.448,

    "foup2_16": 1073.448,

    "foup3_16": 1073.448,

    "foup1_17": 1082.888,

    "foup2_17": 1082.888,

    "foup3_17": 1082.888,

    "foup1_18": 1092.328,

    "foup2_18": 1092.328,

    "foup3_18": 1092.328,

    "foup1_19": 1101.768,

    "foup2_19": 1101.768,

    "foup3_19": 1101.768,

    "foup1_20": 1111.208,

    "foup2_20": 1111.208,

    "foup3_20": 1111.208,

    "foup1_21": 1120.648,

    "foup2_21": 1120.648,

    "foup3_21": 1120.648,

    "foup1_22": 1130.088,

    "foup2_22": 1130.088,

    "foup3_22": 1130.088,

    "foup1_23": 1139.528,

    "foup2_23": 1139.528,

    "foup3_23": 1139.528,

    "foup1_24": 1148.968,

    "foup2_24": 1148.968,

    "foup3_24": 1148.968,

    "foup1_25": 1158.408,

    "foup2_25": 1158.408,

    "foup3_25": 1158.408,

    "buffer3_1": 1128.257,
    "buffer3_2": 1139.817,
    "buffer3_3": 1151.377,
    "buffer3_4": 1162.937,
    "buffer3_5": 1174.497,
    "buffer3_6": 1186.057,
    "buffer3_7": 1197.617,
    "buffer3_8": 1209.177,
    "buffer3_9": 1220.737,
    "buffer3_10": 1232.297,
    "buffer3_11": 1243.857,
    "buffer3_12": 1255.417,
    "buffer3_13": 1266.977,
    "buffer3_14": 1278.537,
    "buffer3_15": 1290.097,
    "buffer3_16": 1301.657,
    "buffer3_17": 1313.217,
    "buffer3_18": 1324.777,
    "buffer3_19": 1336.337,
    "buffer3_20": 1347.897,
    "buffer3_21": 1359.457,
    "buffer3_22": 1371.017,
    "buffer3_23": 1382.577,
    "buffer3_24": 1394.137,
    "buffer3_25": 1405.697,

    "buffer4_1": 917.538,
    "buffer4_2": 929.095,
    "buffer4_3": 940.652,
    "buffer4_4": 952.209,
    "buffer4_5": 963.766,
    "buffer4_6": 975.323,
    "buffer4_7": 986.88,
    "buffer4_8": 998.437,
    "buffer4_9": 1009.994,
    "buffer4_10": 1021.551,
    "buffer4_11": 1033.108,
    "buffer4_12": 1044.665,
    "buffer4_13": 1056.222,
    "buffer4_14": 1067.779,
    "buffer4_15": 1079.336,
    "buffer4_16": 1090.893,
    "buffer4_17": 1102.45,
    "buffer4_18": 1114.007,
    "buffer4_19": 1125.564,
    "buffer4_20": 1137.121,
    "buffer4_21": 1148.678,
    "buffer4_22": 1160.235,
    "buffer4_23": 1171.792,
    "buffer4_24": 1183.349,
    "buffer4_25": 1194.906,

    "cooling_1": 1297.812,
    "cooling_2": 1309.526,
    "cooling_3": 1321.24,
    "cooling_4": 1332.955,
    "cooling_5": 1344.669,
    "cooling_6": 1356.383,
    "cooling_7": 1368.098,
    "aligner": 940.327,

    "airlock1_1": 1086.071,
    "airlock2_1": 1086.071,

    "airlock1_2": 1105.249,
    "airlock2_2": 1105.249,
}


def _compute_slot_z_delta(abs_map: Dict[str, float], applied_ref: float) -> Dict[str, float]:
    """표시·MOVE용 Δ [mm] = 절대 Z − 적용 기준 (예: 931.848 − 905.92 = 25.928)."""
    ref = float(applied_ref)
    return {k: float(v) - ref for k, v in abs_map.items()}


# --- ATM: 기준 대비 차이 (절대 Z − ATM_Z_APPLIED_REFERENCE) — 자동 계산 ---
ATM_SLOT_Z_DELTA: Dict[str, float] = _compute_slot_z_delta(ATM_SLOT_Z_ABSOLUTE, ATM_Z_APPLIED_REFERENCE)

# =============================================================================
# VTM — 기준 Z (prompt: 초기 장비 기준 z = 959.99)
# =============================================================================
VTM_Z_DOCUMENT_REFERENCE: float = 959.99
VTM_Z_APPLIED_REFERENCE: float = 959.99


# --- VTM: 슬롯별 절대 Z [mm] ---
VTM_SLOT_Z_ABSOLUTE: Dict[str, float] = {
    "chamber1": 978.81,
    "chamber2": 978.81,
    "chamber3": 978.81,
    "chamber4": 978.81,
    "chamber5": 956.482,

    "airlock1_1": 976.291,
    "airlock1_2": 995.468,
    "airlock2_1": 976.291,
    "airlock2_2": 995.468,
}


# --- VTM: 기준 대비 차이 — 자동 계산 ---
VTM_SLOT_Z_DELTA: Dict[str, float] = _compute_slot_z_delta(VTM_SLOT_Z_ABSOLUTE, VTM_Z_APPLIED_REFERENCE)


def refresh_slot_z_deltas() -> None:
    """``*_Z_APPLIED_REFERENCE`` 변경 후 호출 — DELTA dict 재계산."""
    global ATM_SLOT_Z_DELTA, VTM_SLOT_Z_DELTA
    ATM_SLOT_Z_DELTA = _compute_slot_z_delta(ATM_SLOT_Z_ABSOLUTE, ATM_Z_APPLIED_REFERENCE)
    VTM_SLOT_Z_DELTA = _compute_slot_z_delta(VTM_SLOT_Z_ABSOLUTE, VTM_Z_APPLIED_REFERENCE)


def _delta_m_from_document(abs_map: Dict[str, float], doc_ref: float) -> Dict[str, float]:
    """시뮬용 delta [m] = (절대 Z − 문서 기준). ``적용 기준 + delta`` = ``절대 − 문서 + 적용``."""
    doc = float(doc_ref)
    scale = float(Z_MM_TO_METERS)
    return {k: (float(v) - doc) * scale for k, v in abs_map.items()}


def load_atm_z_tables() -> Tuple[float, Dict[str, float]]:
    """ATM → (baseline_m, z_slot_delta_m).

    ``effective_slot_z_m`` = ``baseline_m + delta_m[slot]``
    = ``(적용기준 + 절대Z − 문서기준)`` [m].
    ``ATM_SLOT_Z_DELTA``(표시용) = ``절대Z − 적용기준`` [mm] — ``refresh_slot_z_deltas()``."""
    refresh_slot_z_deltas()
    ref_m = float(ATM_Z_APPLIED_REFERENCE) * Z_MM_TO_METERS
    delta_m = _delta_m_from_document(ATM_SLOT_Z_ABSOLUTE, ATM_Z_DOCUMENT_REFERENCE)
    return ref_m, delta_m


def load_vtm_z_tables() -> Tuple[float, Dict[str, float]]:
    """VTM → (baseline_m, vtm_z_slot_delta_m). 공식은 ATM 과 동일."""
    refresh_slot_z_deltas()
    ref_m = float(VTM_Z_APPLIED_REFERENCE) * Z_MM_TO_METERS
    delta_m = _delta_m_from_document(VTM_SLOT_Z_ABSOLUTE, VTM_Z_DOCUMENT_REFERENCE)
    return ref_m, delta_m


def effective_atm_slot_z_mm(slot_key: str) -> float | None:
    """적용 절대 Z [mm] = 문서 절대 Z − 문서 기준 + 적용 기준."""
    if slot_key not in ATM_SLOT_Z_ABSOLUTE:
        return None
    return (
        float(ATM_SLOT_Z_ABSOLUTE[slot_key])
        - float(ATM_Z_DOCUMENT_REFERENCE)
        + float(ATM_Z_APPLIED_REFERENCE)
    )


def effective_atm_slot_z_m(slot_key: str) -> float | None:
    z = effective_atm_slot_z_mm(slot_key)
    return None if z is None else z * Z_MM_TO_METERS


def effective_vtm_slot_z_mm(slot_key: str) -> float | None:
    """VTM 적용 절대 Z [mm]."""
    if slot_key not in VTM_SLOT_Z_ABSOLUTE:
        return None
    return (
        float(VTM_SLOT_Z_ABSOLUTE[slot_key])
        - float(VTM_Z_DOCUMENT_REFERENCE)
        + float(VTM_Z_APPLIED_REFERENCE)
    )


def effective_vtm_slot_z_m(slot_key: str) -> float | None:
    z = effective_vtm_slot_z_mm(slot_key)
    return None if z is None else z * Z_MM_TO_METERS


def slot_z_move_target_dz(slot_key: str, *, robot: str = "atm") -> float | None:
    """적용 기준=0 일 때 MOVE ``dz`` — **mm**, 편집기 수동 입력과 동일.

    ``foup1_1`` → ``25.928`` (``0.025928`` 아님).
    """
    sk = (slot_key or "").strip()
    r = (robot or "atm").strip().lower()
    delta_map = VTM_SLOT_Z_DELTA if r == "vtm" else ATM_SLOT_Z_DELTA
    abs_map = VTM_SLOT_Z_ABSOLUTE if r == "vtm" else ATM_SLOT_Z_ABSOLUTE
    if sk not in abs_map:
        return None
    return float(delta_map[sk]) * float(Z_TBS_MOVE_UNIT_PER_MM)


def slot_z_offset_from_applied_baseline_m(slot_key: str, *, robot: str = "atm") -> float | None:
    """적용 기준 대비 오프셋 [m] — ``load_atm_z_tables`` / 로그용. MOVE ``dz`` 는 ``slot_z_move_target_dz``."""
    z = slot_z_move_target_dz(slot_key, robot=robot)
    return None if z is None else float(z) * float(Z_MM_TO_METERS)


def slot_z_diagnostic(slot_key: str, *, robot: str = "atm") -> Dict[str, float | str | bool | None]:
    """슬롯 Z 조회·로그용 — ``lam_slot_z_config.py`` 가 유일한 SSOT.

    시뮬 ``MOVE`` ``dz`` = ``slot_z_move_target_dz`` [mm]. CAD 절대 mm 는 ``effective_absolute_mm``.
    """
    sk = (slot_key or "").strip()
    r = (robot or "atm").strip().lower()
    if r == "vtm":
        abs_map = VTM_SLOT_Z_ABSOLUTE
        doc_ref = float(VTM_Z_DOCUMENT_REFERENCE)
        applied_ref = float(VTM_Z_APPLIED_REFERENCE)
        delta_map = VTM_SLOT_Z_DELTA
        eff_mm_fn = effective_vtm_slot_z_mm
        eff_m_fn = effective_vtm_slot_z_m
    else:
        abs_map = ATM_SLOT_Z_ABSOLUTE
        doc_ref = float(ATM_Z_DOCUMENT_REFERENCE)
        applied_ref = float(ATM_Z_APPLIED_REFERENCE)
        delta_map = ATM_SLOT_Z_DELTA
        eff_mm_fn = effective_atm_slot_z_mm
        eff_m_fn = effective_atm_slot_z_m
    if sk not in abs_map:
        return {
            "slot_key": sk,
            "robot": r,
            "defined": False,
        }
    abs_mm = float(abs_map[sk])
    delta_doc_mm = abs_mm - doc_ref
    delta_applied_mm = float(delta_map.get(sk, abs_mm - applied_ref))
    eff_mm = eff_mm_fn(sk)
    eff_m = eff_m_fn(sk)
    move_dz = slot_z_move_target_dz(sk, robot=r)
    return {
        "slot_key": sk,
        "robot": r,
        "defined": True,
        "document_absolute_mm": abs_mm,
        "document_reference_mm": doc_ref,
        "applied_reference_mm": applied_ref,
        "delta_from_document_mm": delta_doc_mm,
        "delta_from_applied_mm": delta_applied_mm,
        "effective_absolute_mm": eff_mm,
        "move_target_dz": move_dz,
        "move_target_offset_m": None if move_dz is None else float(move_dz) * float(Z_MM_TO_METERS),
        "effective_absolute_m": eff_m,
        "source": "lam_slot_z_config.py",
    }


refresh_slot_z_deltas()

__all__ = [
    "Z_MM_TO_METERS",
    "Z_TBS_MOVE_UNIT_PER_MM",
    "ATM_Z_MOVE_PRIM_PATH",
    "VTM_Z_MOVE_PRIM_PATH",
    "ATM_Z_APPLIED_REFERENCE",
    "ATM_SLOT_Z_ABSOLUTE",
    "ATM_SLOT_Z_DELTA",
    "VTM_Z_APPLIED_REFERENCE",
    "VTM_SLOT_Z_ABSOLUTE",
    "VTM_SLOT_Z_DELTA",
    "refresh_slot_z_deltas",
    "load_atm_z_tables",
    "load_vtm_z_tables",
    "effective_atm_slot_z_m",
    "effective_vtm_slot_z_m",
    "slot_z_move_target_dz",
    "slot_z_offset_from_applied_baseline_m",
    "slot_z_diagnostic",
]
