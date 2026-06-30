# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
포트 점유(ports_occupancy)에 따라, 포트별로 매핑된 LOT 표현 prim의 보임/숨김을 맞춘다.

설정: config/port_lot_prim_paths.json (확장 루트 기준)
- 키: INOUT, BP1~BP4, EP1~EP3 등 시뮬과 동일한 포트 ID
- 값: 해당 포트의 LOT을 묘사하는 prim 절대 경로. 빈 문자열이면 이 포트는 처리하지 않음.

규칙(예외 규칙 없음):
- 매핑 경로가 비어 있으면 아무 것도 하지 않음.
- ports_occupancy[포트]가 비어 있으면(LOT 없음) → 해당 prim 숨김.
- LOT id가 있으면 → 해당 prim 표시.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import omni.usd as ou
from pxr import Gf, Sdf, UsdGeom, UsdShade

from .rotate_animation import stop_prim_rotate_animation
from .sim_control_defaults import SIM_CONTROL_DEFAULTS
from .translate_animation import stop_prim_translate_animation, stop_prim_translate_animation_all_contexts

_CONFIG_FILENAME = "port_lot_prim_paths.json"
_CACHE: Optional[Dict[str, str]] = None
_MTIME: Optional[float] = None

# 포트별 LOT 표현 prim의 "기준 자세"(최초 캡처). 애니 시작 시 이 값으로 복원한다(가시성 로직은 별도).
# - baseline(원위치)·lift 크기는 모든 화면에서 동일(같은 USD authoring)하므로 prim 경로 단일 키로 둔다.
_PORT_LOT_AUTHORING: Dict[str, Tuple[Gf.Vec3f, Gf.Vec3f]] = {}

# ─────────────────────────────────────────────────────────────────────────────
# FOUP 공정 "상태 플래그"는 화면(USD 컨텍스트)별로 독립이어야 한다.
#   화면1·화면2 가 동일 prim 경로 문자열을 공유하므로(별도 stage), 경로만 키로 쓰면
#   한 화면의 공정중 lift 상태가 다른 화면 동일 포트로 전이된다(위치 초기화 시 +Y 누적).
#   → (컨텍스트, prim 경로) 로 분리한다. 화면1 = "" (기본 컨텍스트), 화면2+ = 명명 컨텍스트.
# 단, baseline(_PORT_LOT_AUTHORING)·lift 크기·material 은 화면 독립이라 그대로 둔다.
# ─────────────────────────────────────────────────────────────────────────────
# FOUP 공정이 진행 중인 prim 경로 집합(컨텍스트별).
# - FOUP_PROCESS_START 시 등록(+Y 오프셋 보호 시작)
# - FOUP_PROCESS_END 의 -Y 복귀 애니가 끝난 뒤(약 1초 후) 해제
# - 이 집합에 포함된 path 는 restore_port_lot_prims_to_authoring() 에서 baseline 복원을 건너뛴다.
_FOUP_IN_PROGRESS_BY_CTX: Dict[str, Set[str]] = {}

# FOUP plateau(=+Y 1초 애니가 끝난 시점 ~ -Y 시작 직전) prim 경로 집합(컨텍스트별).
# - 의미: prim 이 baseline+(0, foup_proc_y_lift, 0) 자리에 머물러야 하는 구간.
_FOUP_LIFTED_BY_CTX: Dict[str, Set[str]] = {}
# +Y 진행(+1) / -Y 진행(-1). 애니가 끊긴 뒤 restore 가 어느 쪽으로 맞출지 결정(컨텍스트별).
_FOUP_LIFT_SIGN_BY_CTX: Dict[str, Dict[str, int]] = {}


def _ctx_key(usd_context_name: Optional[str]) -> str:
    """USD 컨텍스트 이름 정규화 키 (None/빈 문자열 → 기본 컨텍스트 "")."""
    return str(usd_context_name or "").strip()


def _foup_in_progress_set(usd_context_name: Optional[str]) -> Set[str]:
    return _FOUP_IN_PROGRESS_BY_CTX.setdefault(_ctx_key(usd_context_name), set())


def _foup_lifted_set(usd_context_name: Optional[str]) -> Set[str]:
    return _FOUP_LIFTED_BY_CTX.setdefault(_ctx_key(usd_context_name), set())


def _foup_lift_sign_map(usd_context_name: Optional[str]) -> Dict[str, int]:
    return _FOUP_LIFT_SIGN_BY_CTX.setdefault(_ctx_key(usd_context_name), {})


def _is_foup_in_progress_any_ctx(prim_path: str) -> bool:
    """어느 화면 컨텍스트에서든 공정 진행중이면 True.

    ``_PORT_LOT_AUTHORING`` baseline 은 화면 공통이므로, 어느 한 화면에서라도
    +Y lift 된 상태면 그 stage 의 현재 위치를 baseline 으로 잘못 캡처하지 않도록 보호한다.
    """
    p = str(prim_path or "").strip()
    if not p:
        return False
    for s in _FOUP_IN_PROGRESS_BY_CTX.values():
        if p in s:
            return True
    return False

# ─────────────────────────────────────────────────────────────────────────────
# FOUP material 경로 (한 곳에서만 수정하면 전체에 반영됩니다)
#   - PROCESSING : FOUP 공정 진행 중(FOUP_PROCESS_START 직후~END 직전)
#   - DONE       : 공정 종료(-Y 복귀, 회수 대기) 상태(FOUP_PROCESS_END 직후~REMOVED 전까지)
#   - DEFAULT    : LOT 이 비어 있을 때(포트상태 초기화 = MakeInvisible 시점)와
#                  Stop/Reset 등 안전망 복원 시 사용하는 기본 material
# 경로는 반드시 절대 경로(`/` 로 시작)여야 합니다.
# ─────────────────────────────────────────────────────────────────────────────
# MATERIAL_PATH_FOUP_PROCESSING: str = "/Root/World/Looks/CASE_02"
# MATERIAL_PATH_FOUP_DONE: str = "/Root/World/Looks/CASE_03"
# MATERIAL_PATH_FOUP_DEFAULT: str = "/Root/World/Looks/phong1"
MATERIAL_PATH_FOUP_PROCESSING: str = "/Root/Looks/case_02"
MATERIAL_PATH_FOUP_DONE: str = "/Root/Looks/case_03"
MATERIAL_PATH_FOUP_DEFAULT: str = "/Root/Looks/case_01"


def _config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / _CONFIG_FILENAME


def load_port_lot_prim_paths() -> Dict[str, str]:
    """JSON에서 포트→prim 경로 맵을 읽는다. mtime이 같으면 캐시 사용."""
    global _CACHE, _MTIME
    p = _config_path()
    if not p.exists():
        _CACHE = {}
        _MTIME = None
        return {}
    try:
        mtime = p.stat().st_mtime
        if _CACHE is not None and _MTIME == mtime:
            return dict(_CACHE)
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            out: Dict[str, str] = {}
        else:
            out = {}
            for k, v in raw.items():
                if str(k).startswith("_"):
                    continue
                out[str(k).strip().upper()] = str(v).strip() if v is not None else ""
        _CACHE = out
        _MTIME = mtime
        return dict(out)
    except Exception:
        _CACHE = {}
        _MTIME = None
        return {}


def _set_prim_visible_on_stage(stage: Any, path: str, visible: bool) -> None:
    if not stage or not path:
        return
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
    except Exception:
        pass


def _set_prim_visible(path: str, visible: bool) -> None:
    try:
        ctx = ou.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return
        _set_prim_visible_on_stage(stage, path, visible)
    except Exception:
        pass


def _iter_unique_mapped_prim_paths() -> List[str]:
    """mapping 값 중 비어 있지 않은 prim 경로(중복 제거, 순서 유지)."""
    mapping = load_port_lot_prim_paths()
    if not mapping:
        return []
    seen = set()
    out: List[str] = []
    for _port, prim_path in mapping.items():
        p = str(prim_path or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def is_port_lot_mapped_prim(prim_path: str) -> bool:
    """``port_lot_prim_paths.json`` 에 등록된 prim 경로인지."""
    p = str(prim_path or "").strip()
    if not p:
        return False
    return p in set(_iter_unique_mapped_prim_paths())


def should_skip_port_lot_baseline_reset(
    prim_path: str,
    *,
    usd_context_name: Optional[str] = None,
    foup_proc_active_ep: str = "",
) -> bool:
    """JSON 전환·baseline 복원 시 포트 LOT prim 을 baseline 으로 되돌리지 말아야 하면 True."""
    p = str(prim_path or "").strip()
    if not p or not is_port_lot_mapped_prim(p):
        return False
    if is_foup_port_lot_active(
        p, foup_proc_active_ep=foup_proc_active_ep, usd_context_name=usd_context_name
    ):
        return True
    try:
        from .translate_animation import is_prim_translate_animation_running

        if is_prim_translate_animation_running(p, usd_context_name):
            return True
    except Exception:
        pass
    try:
        if is_prim_at_foup_lifted_position(
            p,
            usd_context_name=usd_context_name,
            foup_proc_active_ep=foup_proc_active_ep,
        ):
            mark_foup_lifted(p, True, usd_context_name=usd_context_name)
            return True
    except Exception:
        pass
    return False


def clear_port_lot_authoring_cache() -> None:
    """시뮬 리셋 등에서 다음 애니 시작 시 authoring을 다시 잡을 수 있게 캐시를 비운다.
    함께 FOUP 진행중 보호 집합도 초기화하여, 잔여 플래그로 인해 다음 시뮬에서 복원이 막히지 않게 한다.
    (시작/리셋 안전망 — 모든 화면 컨텍스트 일괄 초기화)
    """
    _PORT_LOT_AUTHORING.clear()
    _FOUP_IN_PROGRESS_BY_CTX.clear()
    _FOUP_LIFTED_BY_CTX.clear()
    _FOUP_LIFT_SIGN_BY_CTX.clear()


def mark_foup_in_progress(
    prim_path: str, in_progress: bool, *, usd_context_name: Optional[str] = None
) -> None:
    """
    특정 prim 경로의 FOUP 공정 진행 여부를 (화면 컨텍스트별로) 등록/해제한다.
    이 집합에 포함된 prim 은 ``restore_port_lot_prims_to_authoring()`` 에서 baseline 복원을 건너뛴다.

    - FOUP_PROCESS_START → True 로 등록(+Y 오프셋 유지)
    - FOUP_PROCESS_END 후 -Y 복귀 애니가 끝난 뒤 False 로 해제
    """
    p = str(prim_path or "").strip()
    if not p:
        return
    s = _foup_in_progress_set(usd_context_name)
    if in_progress:
        s.add(p)
    else:
        s.discard(p)


def clear_foup_in_progress(*, usd_context_name: Optional[str] = None) -> None:
    """FOUP 진행중 표시를 비운다(시뮬 시작/리셋/정지 시 안전망용).

    ``usd_context_name`` 미지정(None) → 모든 화면 컨텍스트 일괄 초기화(기존 동작).
    지정 시 해당 컨텍스트만 초기화.
    """
    if usd_context_name is None:
        _FOUP_IN_PROGRESS_BY_CTX.clear()
        _FOUP_LIFTED_BY_CTX.clear()
        _FOUP_LIFT_SIGN_BY_CTX.clear()
        return
    key = _ctx_key(usd_context_name)
    _FOUP_IN_PROGRESS_BY_CTX.pop(key, None)
    _FOUP_LIFTED_BY_CTX.pop(key, None)
    _FOUP_LIFT_SIGN_BY_CTX.pop(key, None)


def is_foup_in_progress(prim_path: str, *, usd_context_name: Optional[str] = None) -> bool:
    """디버그/조회용. 해당 path 가 (해당 컨텍스트에서) FOUP 진행중으로 표시되어 있는지 반환."""
    return str(prim_path or "").strip() in _foup_in_progress_set(usd_context_name)


def mark_foup_lifted(
    prim_path: str, lifted: bool, *, usd_context_name: Optional[str] = None
) -> None:
    """
    FOUP plateau 표시(+Y lift 자리에 머물러야 하는 상태)를 (화면 컨텍스트별로) 등록/해제.
    - +Y 1초 애니가 끝났을 때 True
    - FOUP_PROCESS_END 이벤트 진입 즉시 False(이후 -Y 복귀 애니는 in-progress skip 로 보호)
    """
    p = str(prim_path or "").strip()
    if not p:
        return
    s = _foup_lifted_set(usd_context_name)
    if lifted:
        s.add(p)
    else:
        s.discard(p)


def clear_foup_lifted(*, usd_context_name: Optional[str] = None) -> None:
    """plateau 표시를 비운다(시뮬 시작/리셋/정지 시 안전망용).

    ``usd_context_name`` 미지정(None) → 모든 컨텍스트 일괄 초기화(기존 동작).
    """
    if usd_context_name is None:
        _FOUP_LIFTED_BY_CTX.clear()
        _FOUP_LIFT_SIGN_BY_CTX.clear()
        return
    key = _ctx_key(usd_context_name)
    _FOUP_LIFTED_BY_CTX.pop(key, None)
    _FOUP_LIFT_SIGN_BY_CTX.pop(key, None)


def is_foup_lifted(prim_path: str, *, usd_context_name: Optional[str] = None) -> bool:
    """해당 path 가 (해당 컨텍스트에서) FOUP plateau(+Y lift 자리) 상태인지 반환."""
    return str(prim_path or "").strip() in _foup_lifted_set(usd_context_name)


def foup_proc_y_lift() -> float:
    """FOUP 공정 Y 오프셋 — ``sim_control_defaults.SimControlDefaults.foup_proc_y_lift`` SSOT."""
    try:
        return float(SIM_CONTROL_DEFAULTS.foup_proc_y_lift)
    except Exception:
        return 30.0


def foup_y_position_epsilon() -> float:
    """lift/baseline 위치 비교 허용 오차(스테이지 단위)."""
    return max(0.05, abs(float(foup_proc_y_lift())) * 0.025)


def _read_prim_translate(stage: Any, prim_path: str) -> Optional[Gf.Vec3f]:
    if not stage or not prim_path:
        return None
    try:
        from .sequence_engine import _get_translate

        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            return None
        t = _get_translate(prim)
        return Gf.Vec3f(float(t[0]), float(t[1]), float(t[2]))
    except Exception:
        return None


def is_prim_at_foup_baseline_position(
    prim_path: str,
    *,
    usd_context_name: Optional[str] = None,
) -> bool:
    """authoring baseline Y 와 현재 Y 가 거의 같으면 True."""
    p = str(prim_path or "").strip()
    if not p:
        return False
    stage = _get_stage_for_context(usd_context_name)
    ensure_port_lot_authoring_captured(stage)
    rec = _PORT_LOT_AUTHORING.get(p)
    if rec is None or stage is None:
        return False
    cur = _read_prim_translate(stage, p)
    if cur is None:
        return False
    eps = foup_y_position_epsilon()
    try:
        return abs(float(cur[1]) - float(rec[0][1])) <= eps
    except Exception:
        return False


def is_prim_at_foup_lifted_position(
    prim_path: str,
    *,
    usd_context_name: Optional[str] = None,
    foup_proc_active_ep: str = "",
) -> bool:
    """baseline + foup_proc_y_lift Y 와 현재 Y 가 거의 같으면 True."""
    p = str(prim_path or "").strip()
    if not p:
        return False
    target = get_foup_restore_translate(
        p, foup_proc_active_ep=foup_proc_active_ep, usd_context_name=usd_context_name
    )
    if target is None and is_foup_lifted(p, usd_context_name=usd_context_name):
        rec = _PORT_LOT_AUTHORING.get(p)
        if rec is None:
            return False
        lift = float(foup_proc_y_lift())
        target = Gf.Vec3f(float(rec[0][0]), float(rec[0][1]) + lift, float(rec[0][2]))
    if target is None:
        return False
    stage = _get_stage_for_context(usd_context_name)
    if stage is None:
        return False
    cur = _read_prim_translate(stage, p)
    if cur is None:
        return False
    eps = foup_y_position_epsilon()
    try:
        return abs(float(cur[1]) - float(target[1])) <= eps
    except Exception:
        return False


def snap_foup_prim_to_lifted(
    prim_path: str,
    *,
    usd_context_name: Optional[str] = None,
    foup_proc_active_ep: str = "",
) -> bool:
    """애니 없이 baseline + lift 위치로 즉시 스냅(공정 중 외부 초기화용)."""
    p = str(prim_path or "").strip()
    if not p:
        return False
    active_ep = str(foup_proc_active_ep or "").strip().upper()
    port_id = _port_id_for_prim_path(p)
    if active_ep and port_id == active_ep:
        mark_foup_lifted(p, True, usd_context_name=usd_context_name)
    target = get_foup_restore_translate(
        p, foup_proc_active_ep=active_ep, usd_context_name=usd_context_name
    )
    if target is None:
        rec = _PORT_LOT_AUTHORING.get(p)
        if rec is None:
            stage0 = _get_stage_for_context(usd_context_name)
            ensure_port_lot_authoring_captured(stage0)
            rec = _PORT_LOT_AUTHORING.get(p)
        if rec is None:
            return False
        lift = float(foup_proc_y_lift())
        target = Gf.Vec3f(float(rec[0][0]), float(rec[0][1]) + lift, float(rec[0][2]))
        mark_foup_lifted(p, True, usd_context_name=usd_context_name)
    stage = _get_stage_for_context(usd_context_name)
    ensure_port_lot_authoring_captured(stage)
    if not stage:
        return False
    try:
        from .sequence_engine import _set_rotate_xyz, _set_translate

        stop_prim_translate_animation_all_contexts(p)
        stop_prim_rotate_animation(p)
        prim = stage.GetPrimAtPath(p)
        if not prim or not prim.IsValid():
            return False
        base_r = _PORT_LOT_AUTHORING.get(p, (target, Gf.Vec3f(0, 0, 0)))[1]
        _set_translate(prim, target)
        _set_rotate_xyz(prim, base_r)
        mark_foup_lifted(p, True, usd_context_name=usd_context_name)
        return True
    except Exception:
        return False


def snap_foup_prim_to_baseline(
    prim_path: str,
    *,
    usd_context_name: Optional[str] = None,
) -> bool:
    """애니 없이 authoring baseline 으로 즉시 스냅."""
    p = str(prim_path or "").strip()
    if not p:
        return False
    stage = _get_stage_for_context(usd_context_name)
    ensure_port_lot_authoring_captured(stage)
    rec = _PORT_LOT_AUTHORING.get(p)
    if not stage or rec is None:
        return False
    try:
        from .sequence_engine import _set_rotate_xyz, _set_translate

        stop_prim_translate_animation_all_contexts(p)
        stop_prim_rotate_animation(p)
        prim = stage.GetPrimAtPath(p)
        if not prim or not prim.IsValid():
            return False
        t, r = rec
        _set_translate(prim, t)
        _set_rotate_xyz(prim, r)
        mark_foup_lifted(p, False, usd_context_name=usd_context_name)
        return True
    except Exception:
        return False


def set_foup_lift_sign(
    prim_path: str, sign: int, *, usd_context_name: Optional[str] = None
) -> None:
    p = str(prim_path or "").strip()
    if not p:
        return
    m = _foup_lift_sign_map(usd_context_name)
    s = int(sign)
    if s > 0:
        m[p] = 1
    elif s < 0:
        m[p] = -1
    else:
        m.pop(p, None)


def get_foup_lift_sign(prim_path: str, *, usd_context_name: Optional[str] = None) -> int:
    m = _foup_lift_sign_map(usd_context_name)
    return int(m.get(str(prim_path or "").strip(), 0) or 0)


def is_foup_port_lot_active(
    prim_path: str,
    *,
    foup_proc_active_ep: str = "",
    usd_context_name: Optional[str] = None,
) -> bool:
    """공정 중(plateau·±Y·active EP)인 FOUP prim 인지 (해당 컨텍스트 기준)."""
    p = str(prim_path or "").strip()
    if not p:
        return False
    if p in _foup_in_progress_set(usd_context_name) or p in _foup_lifted_set(
        usd_context_name
    ):
        return True
    active_ep = str(foup_proc_active_ep or "").strip().upper()
    if active_ep and _port_id_for_prim_path(p) == active_ep:
        return True
    return False


def foup_authoring_baseline_translate(prim_path: str) -> Optional[Gf.Vec3f]:
    p = str(prim_path or "").strip()
    if not p:
        return None
    rec = _PORT_LOT_AUTHORING.get(p)
    if rec is None:
        return None
    try:
        t = rec[0]
        return Gf.Vec3f(float(t[0]), float(t[1]), float(t[2]))
    except Exception:
        return None


def foup_lifted_target_translate(prim_path: str) -> Optional[Gf.Vec3f]:
    base = foup_authoring_baseline_translate(prim_path)
    if base is None:
        return None
    lift = float(foup_proc_y_lift())
    return Gf.Vec3f(float(base[0]), float(base[1]) + lift, float(base[2]))


def run_foup_smooth_y_anim(
    prim_path: str,
    *,
    usd_context_name: Optional[str] = None,
    toward_lifted: bool,
    duration: float = 1.0,
    on_completed: Any = None,
    foup_proc_active_ep: str = "",
) -> bool:
    """현재 Y → baseline(+lift) 목표까지 1초(기본) 부드럽게 이동. 이미 도달 시 애니 생략."""
    p = str(prim_path or "").strip()
    if not p:
        return False
    try:
        from .translate_animation import (
            is_prim_translate_animation_running,
            run_prim_translate_animation,
        )
    except Exception:
        return False
    if is_prim_translate_animation_running(p, usd_context_name):
        return False
    stage = _get_stage_for_context(usd_context_name)
    ensure_port_lot_authoring_captured(stage)
    if not stage:
        return False
    base = foup_authoring_baseline_translate(p)
    if base is None:
        try:
            prim0 = stage.GetPrimAtPath(p)
            if prim0 and prim0.IsValid():
                from .sequence_engine import _get_rotate_xyz, _get_translate

                _PORT_LOT_AUTHORING[p] = (_get_translate(prim0), _get_rotate_xyz(prim0))
                base = foup_authoring_baseline_translate(p)
        except Exception:
            base = None
    if base is None:
        return False
    lift = float(foup_proc_y_lift())
    target = (
        Gf.Vec3f(float(base[0]), float(base[1]) + lift, float(base[2]))
        if toward_lifted
        else Gf.Vec3f(float(base[0]), float(base[1]), float(base[2]))
    )
    cur = _read_prim_translate(stage, p)
    if cur is None:
        return False
    eps = foup_y_position_epsilon()
    dy = float(target[1]) - float(cur[1])
    if abs(dy) <= eps:
        if toward_lifted:
            mark_foup_lifted(p, True, usd_context_name=usd_context_name)
            set_foup_lift_sign(p, 0, usd_context_name=usd_context_name)
        else:
            mark_foup_lifted(p, False, usd_context_name=usd_context_name)
            set_foup_lift_sign(p, 0, usd_context_name=usd_context_name)
        if callable(on_completed):
            try:
                on_completed()
            except Exception:
                pass
        return False
    if toward_lifted:
        mark_foup_in_progress(p, True, usd_context_name=usd_context_name)
        set_foup_lift_sign(p, 1, usd_context_name=usd_context_name)
        active_ep = str(foup_proc_active_ep or "").strip().upper()
        if active_ep and _port_id_for_prim_path(p) == active_ep:
            mark_foup_lifted(p, True, usd_context_name=usd_context_name)
    else:
        mark_foup_lifted(p, False, usd_context_name=usd_context_name)
        set_foup_lift_sign(p, -1, usd_context_name=usd_context_name)

    def _done() -> None:
        if toward_lifted:
            mark_foup_lifted(p, True, usd_context_name=usd_context_name)
        set_foup_lift_sign(p, 0, usd_context_name=usd_context_name)
        if callable(on_completed):
            try:
                on_completed()
            except Exception:
                pass

    try:
        stop_prim_translate_animation_all_contexts(p)
    except Exception:
        pass
    try:
        run_prim_translate_animation(
            p,
            [{"duration": max(0.05, float(duration)), "delta": (0.0, dy, 0.0)}],
            loop=False,
            on_completed=_done,
            usd_context_name=usd_context_name,
        )
        return True
    except Exception:
        return False


def prim_path_for_port(port_id: str) -> str:
    """포트 ID → ``port_lot_prim_paths.json`` 매핑 prim 경로."""
    pid = str(port_id or "").strip().upper()
    if not pid:
        return ""
    return str((load_port_lot_prim_paths() or {}).get(pid, "") or "").strip()


def _port_id_for_prim_path(prim_path: str) -> str:
    p = str(prim_path or "").strip()
    if not p:
        return ""
    for port, mapped in (load_port_lot_prim_paths() or {}).items():
        if str(mapped or "").strip() == p:
            return str(port).strip().upper()
    return ""


def get_foup_restore_translate(
    prim_path: str,
    *,
    foup_proc_active_ep: str = "",
    usd_context_name: Optional[str] = None,
) -> Optional[Gf.Vec3f]:
    """
    포트 위치 복원 시 공정 중 EP 는 baseline+Y lift, 그 외는 ``None``(통상 baseline 복원).
    공정 상태(in-progress/lifted/sign)는 **화면 컨텍스트별**로 판단한다.

    - +Y/-Y 애니 진행 중(lifted 아님): ``None`` — 애니가 끝날 때까지 건드리지 않음.
    - plateau 플래그 또는 엔진 ``foup_proc_active_ep`` 와 매칭되는 EP prim: offset 위치 반환.
    """
    p = str(prim_path or "").strip()
    if not p:
        return None
    active_ep = str(foup_proc_active_ep or "").strip().upper()
    port_id = _port_id_for_prim_path(p)
    in_progress = p in _foup_in_progress_set(usd_context_name)
    lifted = p in _foup_lifted_set(usd_context_name)
    should_offset = False
    if in_progress and not lifted:
        try:
            from .translate_animation import is_prim_translate_animation_running

            if is_prim_translate_animation_running(p, usd_context_name):
                return None
        except Exception:
            pass
        sign = get_foup_lift_sign(p, usd_context_name=usd_context_name)
        if sign > 0 or (active_ep and port_id == active_ep):
            should_offset = True
        elif sign < 0:
            return None
        else:
            return None
    if not should_offset:
        should_offset = lifted
    if not should_offset and active_ep and port_id == active_ep:
        should_offset = True
        mark_foup_lifted(p, True, usd_context_name=usd_context_name)
    if not should_offset:
        return None
    rec = _PORT_LOT_AUTHORING.get(p)
    if rec is None:
        return None
    base_t = rec[0]
    lift = float(foup_proc_y_lift())
    try:
        return Gf.Vec3f(
            float(base_t[0]),
            float(base_t[1]) + lift,
            float(base_t[2]),
        )
    except Exception:
        return None


def get_foup_lifted_translate(
    prim_path: str, *, usd_context_name: Optional[str] = None
) -> Optional[Gf.Vec3f]:
    """레거시 호출부 — plateau 플래그만 보고 offset 위치를 반환."""
    return get_foup_restore_translate(
        prim_path, foup_proc_active_ep="", usd_context_name=usd_context_name
    )


def ensure_port_lot_authoring_captured(stage: Any = None) -> None:
    """
    매핑 prim마다 최초 1회 현재 transform을 authoring으로 저장한다.
    (이후 애니로 움직인 뒤에는 restore만으로 이 자세로 되돌린다.)

    중요(잘못된 baseline 캡처 방지):
    - FOUP 공정이 진행 중인 prim 은 +Y lift 이동된 상태일 수 있다.
      이 시점에 baseline 으로 잡히면 이후 plateau 복원 시 잘못된 기준으로 점프할 수 있다.
    - 따라서 어느 화면에서든 공정 진행중(_FOUP_IN_PROGRESS_BY_CTX)인 prim 은 이번 캡처 호출에서 건너뛴다.
      (시뮬 시작 직전에 강제 캡처를 미리 해두면, 공정 진행 중에는 이 분기를 거치지 않는다.)
    """
    try:
        from .sequence_engine import _get_rotate_xyz, _get_translate, _get_stage
    except Exception:
        return
    if stage is None:
        stage = _get_stage()
    if not stage:
        return
    for path in _iter_unique_mapped_prim_paths():
        if path in _PORT_LOT_AUTHORING:
            continue
        if _is_foup_in_progress_any_ctx(path):
            # 공정 중인 prim 의 현재 위치는 baseline 이 아니다(잘못 잡으면 lift 누적 위험).
            continue
        try:
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsValid():
                continue
            _PORT_LOT_AUTHORING[path] = (_get_translate(prim), _get_rotate_xyz(prim))
        except Exception:
            continue


def restore_port_lot_prims_to_authoring(
    *,
    usd_context_name: Optional[str] = None,
    foup_proc_active_ep: str = "",
) -> None:
    """
    포트 매핑 prim의 위치/회전을 authoring 기준으로 복원한다.
    보임/숨김은 건드리지 않는다(apply_port_lot_prim_visibility 타이밍 유지).

    공정 중 prim 처리 규칙:
    - plateau / ``foup_proc_active_ep`` 매칭 EP: baseline + (0, foup_proc_y_lift, 0)
    - +Y/-Y 애니 진행 중(lifted 아님): 그대로 둔다.
    - 그 외: baseline 복원.
    """
    try:
        from .sequence_engine import _get_rotate_xyz, _get_translate, _set_rotate_xyz, _set_translate
    except Exception:
        return
    stage = _get_stage_for_context(usd_context_name)
    ensure_port_lot_authoring_captured(stage)
    if not stage:
        return
    active_ep = str(foup_proc_active_ep or "").strip().upper()
    for path in _iter_unique_mapped_prim_paths():
        lifted_t = get_foup_restore_translate(
            path, foup_proc_active_ep=active_ep, usd_context_name=usd_context_name
        )
        if lifted_t is not None:
            try:
                prim = stage.GetPrimAtPath(path)
                if not prim or not prim.IsValid():
                    continue
                cur = _read_prim_translate(stage, path)
                if cur is not None:
                    eps = foup_y_position_epsilon()
                    if (
                        abs(float(cur[0]) - float(lifted_t[0])) <= eps
                        and abs(float(cur[1]) - float(lifted_t[1])) <= eps
                        and abs(float(cur[2]) - float(lifted_t[2])) <= eps
                    ):
                        continue
                try:
                    from .translate_animation import is_prim_translate_animation_running

                    if is_prim_translate_animation_running(path, usd_context_name):
                        continue
                except Exception:
                    pass
            except Exception:
                pass
            try:
                stop_prim_translate_animation_all_contexts(path)
                stop_prim_rotate_animation(path)
            except Exception:
                pass
            try:
                prim = stage.GetPrimAtPath(path)
                if not prim or not prim.IsValid():
                    continue
                _set_translate(prim, lifted_t)
                base_r = _PORT_LOT_AUTHORING[path][1]
                _set_rotate_xyz(prim, base_r)
            except Exception:
                continue
            continue
        if path in _foup_in_progress_set(usd_context_name):
            try:
                from .translate_animation import is_prim_translate_animation_running

                if is_prim_translate_animation_running(path, usd_context_name):
                    continue
            except Exception:
                pass
            sign = get_foup_lift_sign(path, usd_context_name=usd_context_name)
            if sign > 0 or (active_ep and _port_id_for_prim_path(path) == active_ep):
                tgt = foup_lifted_target_translate(path)
                if tgt is not None:
                    try:
                        prim = stage.GetPrimAtPath(path)
                        if prim and prim.IsValid():
                            _set_translate(prim, tgt)
                            mark_foup_lifted(path, True, usd_context_name=usd_context_name)
                    except Exception:
                        pass
            elif sign < 0:
                base = foup_authoring_baseline_translate(path)
                if base is not None:
                    try:
                        prim = stage.GetPrimAtPath(path)
                        if prim and prim.IsValid():
                            _set_translate(prim, base)
                    except Exception:
                        pass
            continue
        if should_skip_port_lot_baseline_reset(
            path,
            usd_context_name=usd_context_name,
            foup_proc_active_ep=active_ep,
        ):
            continue
        try:
            stop_prim_translate_animation_all_contexts(path)
            stop_prim_rotate_animation(path)
        except Exception:
            pass
        try:
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsValid():
                continue
            if path not in _PORT_LOT_AUTHORING:
                _PORT_LOT_AUTHORING[path] = (_get_translate(prim), _get_rotate_xyz(prim))
            t, r = _PORT_LOT_AUTHORING[path]
            _set_translate(prim, t)
            _set_rotate_xyz(prim, r)
        except Exception:
            continue


def sync_port_lot_positions_after_visibility(
    usd_context_name: Optional[str],
    *,
    foup_proc_active_ep: str = "",
) -> None:
    """
    다음 이벤트의 ``ports_occupancy`` 가시성 갱신 직후 호출.
    JSON 종료·이벤트 전환 시 포트 LOT 위치를 맞추되, 공정 중 EP 는 +Y lift 를 유지한다.
    """
    restore_port_lot_prims_to_authoring(
        usd_context_name=usd_context_name,
        foup_proc_active_ep=foup_proc_active_ep,
    )


def _normalized_ports_occupancy(ports_occupancy: Any) -> Dict[str, str]:
    if not isinstance(ports_occupancy, dict):
        return {}
    occ: Dict[str, str] = {}
    for k, v in ports_occupancy.items():
        occ[str(k).strip().upper()] = str(v).strip() if v is not None else ""
    return occ


# ─────────────────────────────────────────────────────────────────────────────
# Material 바인딩 헬퍼
#   - bind_material_to_prim: 단일 prim 에 material 바인딩
#   - apply_port_lot_prim_material_for_context: 화면별 컨텍스트 안전 호출 래퍼
#   - restore_port_lot_prims_to_default_material: Stop/Reset 안전망(전체 phong1 복원)
# 모두 실패해도 조용히 무시(시뮬 흐름을 깨지 않도록).
# ─────────────────────────────────────────────────────────────────────────────


def _get_stage_for_context(usd_context_name: Optional[str]) -> Any:
    """이름 있는 USD 컨텍스트(없으면 기본 컨텍스트)에서 stage 를 안전하게 가져온다."""
    try:
        nm = (usd_context_name or "").strip()
        ctx = ou.get_context(nm) if nm else ou.get_context()
        return ctx.get_stage() if ctx else None
    except Exception:
        return None


def bind_material_to_prim(stage: Any, prim_path: str, material_path: str) -> bool:
    """
    주어진 stage 에서 ``prim_path`` 에 ``material_path`` 의 material 을 바인딩한다.
    성공 시 True, 실패 시 False 반환(예외는 삼킨다).
    """
    if not stage:
        return False
    p = str(prim_path or "").strip()
    m = str(material_path or "").strip()
    if not p or not m:
        return False
    try:
        prim = stage.GetPrimAtPath(Sdf.Path(p))
        if not prim or not prim.IsValid():
            return False
        mat_prim = stage.GetPrimAtPath(Sdf.Path(m))
        if not mat_prim or not mat_prim.IsValid():
            return False
        mat = UsdShade.Material(mat_prim)
        if not mat:
            return False
        UsdShade.MaterialBindingAPI(prim).Bind(mat)
        return True
    except Exception:
        return False


def apply_port_lot_prim_material_for_context(
    usd_context_name: Optional[str],
    prim_path: str,
    material_path: str,
) -> bool:
    """
    화면별 USD 컨텍스트(없으면 기본 컨텍스트)에서 prim 에 material 바인딩.
    FOUP_PROCESS_START/END, 포트 점유 비움 등에서 공통으로 사용한다.
    """
    stage = _get_stage_for_context(usd_context_name)
    return bind_material_to_prim(stage, prim_path, material_path)


def restore_port_lot_prims_to_default_material(usd_context_name: Optional[str] = None) -> None:
    """
    Stop/Reset 안전망: 매핑에 등록된 모든 LOT prim 을 기본 material(phong1)로 일괄 복원.
    어떤 비정상 종료 후에도 다음 시뮬 시작 상태가 깨끗해지도록 한다.
    """
    stage = _get_stage_for_context(usd_context_name)
    if not stage:
        return
    for path in _iter_unique_mapped_prim_paths():
        try:
            bind_material_to_prim(stage, path, MATERIAL_PATH_FOUP_DEFAULT)
        except Exception:
            continue


def apply_port_lot_prim_visibility_for_context(usd_context_name: Optional[str], ports_occupancy: Any) -> None:
    """
    지정 USD 컨텍스트(이름)의 스테이지에 포트 LOT prim 가시성을 적용한다.
    ``usd_context_name`` 이 None/빈 문자열이면 기본 컨텍스트(``get_context()``)와 동일.

    추가 동작(요청 사양):
    - LOT 이 비어 있을 때(포트 상태 초기화 = 숨김 처리)에는 동시에 material 을
      ``MATERIAL_PATH_FOUP_DEFAULT`` (예: ``/Root/World/Looks/phong1``) 로 되돌린다.
      이렇게 하면 다음에 prim 이 다시 visible 될 때 깨끗한 기본 외형으로 시작한다.
    """
    occ = _normalized_ports_occupancy(ports_occupancy)
    if not occ:
        return
    mapping = load_port_lot_prim_paths()
    if not mapping:
        return
    try:
        nm = (usd_context_name or "").strip()
        ctx = ou.get_context(nm) if nm else ou.get_context()
        stage = ctx.get_stage() if ctx else None
    except Exception:
        stage = None
    if not stage:
        return
    for port, prim_path in mapping.items():
        if not prim_path:
            continue
        lot_id = occ.get(str(port).strip().upper(), "")
        has_lot = bool(lot_id)
        path_s = str(prim_path).strip()
        # LOT 이 사라진(=포트상태 초기화) 시점에는 외형도 기본 material 로 되돌린다.
        # (가시성 변경과 같은 호흡으로 처리해 시각적 잔상 없이 다음 visible 진입에 대비)
        if not has_lot:
            try:
                bind_material_to_prim(stage, path_s, MATERIAL_PATH_FOUP_DEFAULT)
            except Exception:
                pass
        _set_prim_visible_on_stage(stage, path_s, has_lot)


def apply_port_lot_prim_visibility(ports_occupancy: Any) -> None:
    """
    시뮬 이벤트의 ports_occupancy(dict: 포트→LOT id 또는 빈 문자열)에 맞춰 매핑 prim 가시성 적용.
    """
    apply_port_lot_prim_visibility_for_context(None, ports_occupancy)
