"""LAM 자산 자동 진단 — `add_usd` 직후 자산을 스캔해 종류를 분류한다.

사용자 요구 (2026-05-11 후반):
  "USD 를 로드한 후 내부 데이터를 점검해서 timeSamples 데이터가 존재하면 bake 과정 생략"

본 모듈은 **다음 두 함수만 외부 노출**한다.

- `scan_asset_kind(asset_path) -> Tuple[str, AssetDiag]`
    자산 USD 를 `Usd.Stage.Open()` 으로 열어 traverse 한다. 각 prim 의 attribute 중
    `timeSamples` 가 박힌 것을 종류별 (`xformOp:*` / SkelAnimation.* / Mesh.points 등)
    로 카운트하고, OmniGraph(PushGraph / OmniGraph*) prim 의 path 도 함께 수집한다.
    카운트 조합으로 `AssetKind` 를 결정해 반환.

- `discover_animated_attrs(asset_path) -> List[Tuple[str, str]]`
    Bake 의 `_collect_targets` 가 사용하는 "자산 기반 자동 탐지" 의 핵심.
    자산의 default prim 기준 상대 path 와 attribute name 페어를 반환.
    Bake 가 master 안 인스턴스 prim 산하의 동일 (sub_path, attr_name) 페어를 찾아
    capture 하도록 한다. 기존 `xformOp:* / visibility` 만 보던 좁은 필터를 대체.

본 모듈은 **`morph.tbs_control_1` 의 어떤 심볼도 import 하지 않는다**. 또한 master stage 에
영향을 주지 않는다 — 자산 USD 만 독립적으로 연다.
"""

from __future__ import annotations

import os
from typing import List, Tuple

from .tbs_types import (
    ASSET_KIND_MIXED,
    ASSET_KIND_OMNIGRAPH,
    ASSET_KIND_STATIC,
    ASSET_KIND_TIMESAMPLES_MESH,
    ASSET_KIND_TIMESAMPLES_SKEL,
    ASSET_KIND_TIMESAMPLES_XFORM,
    ASSET_KIND_UNKNOWN,
    AssetDiag,
)


_PRINT_PREFIX = "[TBS/Diag]"


# attribute name 분류 — `AssetDiag` 카운트 + `discover_animated_attrs` 에서 공용.

# xformOp 류 — translate / rotate{X,Y,Z,XYZ,YXZ,...} / scale / orient / transform / 사용자 정의 op.
_PREFIX_XFORM_OP = "xformOp:"

# SkelAnimation 의 표준 attribute 이름.
_NAMES_SKEL_ANIM = frozenset({
    "translations", "rotations", "scales",
    "blendShapeWeights",
})

# Mesh 의 vertex anim attribute 이름.
_NAMES_MESH_POINTS = frozenset({
    "points", "normals", "extent", "velocities", "accelerations",
})

# Visibility (xformOp 와 함께 다니지만 분류 분리).
_NAMES_VISIBILITY = frozenset({
    "visibility",
})

# OmniGraph 류 typeName 분류 (Bake 모듈과 동일 규칙).
_OMNIGRAPH_TYPENAMES = frozenset({
    "OmniGraph", "PushGraph", "OmniGraphFunction",
})


def _classify_attr_name(name: str) -> str:
    """timeSamples 가 박힌 attribute 의 이름을 분류 — `AssetDiag` 의 어느 카운터에 더할지 결정."""
    if name.startswith(_PREFIX_XFORM_OP):
        return "xform"
    if name in _NAMES_SKEL_ANIM:
        return "skel"
    if name in _NAMES_MESH_POINTS:
        return "mesh"
    if name in _NAMES_VISIBILITY:
        return "visibility"
    return "other"


def _is_omnigraph_prim(type_name: str) -> bool:
    if type_name in _OMNIGRAPH_TYPENAMES:
        return True
    # `OG` 접두 type 도 OmniGraph 류로 본다 (Kit 내부 schema 일부가 OG* 사용).
    if type_name.startswith("OG"):
        return True
    return False


def _decide_kind(diag: AssetDiag) -> str:
    """`AssetDiag` 카운트를 보고 `AssetKind` 결정.

    분류 규칙:
      1. timeSamples 가 1 개도 없음:
            OmniGraph prim 이 있으면 `OMNIGRAPH`, 없으면 `STATIC`.
      2. timeSamples 있음 + OmniGraph 도 있음 → `MIXED`.
      3. timeSamples 있음 + OmniGraph 없음 → 가장 우세한 timeSamples 종류로 결정.
            - xform 우세 → `TIMESAMPLES_XFORM`
            - skel 우세 → `TIMESAMPLES_SKEL`
            - mesh 우세 → `TIMESAMPLES_MESH`
            - 우열을 가리기 어려우면 (xform > 0) 우선 `TIMESAMPLES_XFORM` 으로 부름.
              (LAM Option E 가 xformOp 평가 경로는 검증 완료, 나머지는 P2 검증 항목)
    """
    has_omni = diag.n_omnigraph_prims > 0
    total_ts = diag.total_ts_attrs()

    if total_ts == 0:
        return ASSET_KIND_OMNIGRAPH if has_omni else ASSET_KIND_STATIC

    if has_omni:
        return ASSET_KIND_MIXED

    # timeSamples 만 있는 경우 — 종류 우세 판정.
    n_xform = diag.n_xform_op_ts
    n_skel = diag.n_skel_anim_ts
    n_mesh = diag.n_mesh_points_ts

    # xformOp 이 1 개라도 있으면 가장 안정적인 경로이므로 우선 분류.
    if n_xform > 0 and n_xform >= max(n_skel, n_mesh):
        return ASSET_KIND_TIMESAMPLES_XFORM
    if n_skel > 0 and n_skel >= n_mesh:
        return ASSET_KIND_TIMESAMPLES_SKEL
    if n_mesh > 0:
        return ASSET_KIND_TIMESAMPLES_MESH

    # 모두 0 인데 other > 0 인 케이스 — xform 으로 폴백 (Option E 가 일반 attribute 로 처리).
    return ASSET_KIND_TIMESAMPLES_XFORM


def scan_asset_kind(asset_path: str) -> Tuple[str, AssetDiag]:
    """자산 USD 를 스캔해 `(kind, diag)` 를 반환.

    실패 시 `(ASSET_KIND_UNKNOWN, AssetDiag())` 반환.

    본 함수는 **master stage 와 무관** — `Usd.Stage.Open(asset_path)` 로 자산 파일만 연다.
    """
    diag = AssetDiag()

    if not asset_path or not os.path.isfile(asset_path):
        return (ASSET_KIND_UNKNOWN, diag)

    try:
        from pxr import Usd, UsdGeom  # type: ignore
    except Exception as exc:
        print(f"{_PRINT_PREFIX} scan: pxr import failed: {exc}", flush=True)
        return (ASSET_KIND_UNKNOWN, diag)

    try:
        asset_stage = Usd.Stage.Open(asset_path)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} scan: Stage.Open failed path={asset_path} exc={exc}", flush=True)
        return (ASSET_KIND_UNKNOWN, diag)

    if asset_stage is None:
        return (ASSET_KIND_UNKNOWN, diag)

    # 자산 stage 메타 (참고용).
    try:
        diag.asset_native_tps = float(asset_stage.GetTimeCodesPerSecond())
    except Exception:
        diag.asset_native_tps = 0.0
    try:
        diag.asset_start_tc = float(asset_stage.GetStartTimeCode())
        diag.asset_end_tc = float(asset_stage.GetEndTimeCode())
    except Exception:
        diag.asset_start_tc = 0.0
        diag.asset_end_tc = 0.0
    try:
        diag.asset_up_axis = str(UsdGeom.GetStageUpAxis(asset_stage)).upper()
    except Exception:
        diag.asset_up_axis = ""
    try:
        dp = asset_stage.GetDefaultPrim()
        if dp and dp.IsValid():
            diag.asset_default_prim_path = str(dp.GetPath())
    except Exception:
        diag.asset_default_prim_path = ""

    # 전체 prim traverse.
    omnigraph_paths: List[str] = []
    try:
        prims_iter = asset_stage.Traverse()
    except Exception as exc:
        print(f"{_PRINT_PREFIX} scan: stage.Traverse failed exc={exc}", flush=True)
        return (ASSET_KIND_UNKNOWN, diag)

    for prim in prims_iter:
        # type 검사 → OmniGraph 여부.
        try:
            tn = str(prim.GetTypeName())
        except Exception:
            tn = ""
        if _is_omnigraph_prim(tn):
            diag.n_omnigraph_prims += 1
            try:
                omnigraph_paths.append(str(prim.GetPath()))
            except Exception:
                pass

        # 모든 attribute 의 timeSamples 보유 여부 검사.
        try:
            attrs = prim.GetAttributes()
        except Exception:
            continue
        for attr in attrs:
            try:
                n_ts = attr.GetNumTimeSamples()
            except Exception:
                continue
            if n_ts <= 0:
                continue
            try:
                name = attr.GetName()
            except Exception:
                continue
            cls = _classify_attr_name(name)
            if cls == "xform":
                diag.n_xform_op_ts += 1
            elif cls == "skel":
                diag.n_skel_anim_ts += 1
            elif cls == "mesh":
                diag.n_mesh_points_ts += 1
            elif cls == "visibility":
                diag.n_visibility_ts += 1
            else:
                diag.n_other_ts += 1

    if omnigraph_paths:
        # tuple 로 보관 — `AssetDiag` 가 dataclass 이므로 mutable list 보다 안전.
        diag.omnigraph_prim_paths = tuple(omnigraph_paths)

    kind = _decide_kind(diag)
    return (kind, diag)


def discover_animated_attrs(asset_path: str) -> List[Tuple[str, str]]:
    """자산 USD 에서 `timeSamples` 가 박힌 (sub_path, attr_name) 페어를 수집.

    Bake 모듈의 `_collect_targets` 가 본 결과를 사용해 master 안 인스턴스 prim 산하의
    동일 위치 attribute 를 capture 대상으로 삼는다.

    반환 페어의 `sub_path` 는 자산의 default prim 기준 상대 path.
      - 예: 자산 default prim = `/Root`, 자산 안 timeSamples 가 박힌 prim = `/Root/Geom/Mesh1`
            → sub_path = `Geom/Mesh1`. 자산 default prim 자체에 박혀있으면 sub_path = ``.

    `inst_prim` 가 master 의 `/World/aaa` 라면, master 의
    `/World/aaa/Geom/Mesh1.<attr_name>` 가 capture 대상이 된다.

    실패 / 발견 0 이면 `[]` 반환. Bake 는 이 때 기존 xformOp 필터로 폴백.
    """
    out: List[Tuple[str, str]] = []

    if not asset_path or not os.path.isfile(asset_path):
        return out

    try:
        from pxr import Usd  # type: ignore
    except Exception as exc:
        print(f"{_PRINT_PREFIX} discover: pxr import failed: {exc}", flush=True)
        return out

    try:
        asset_stage = Usd.Stage.Open(asset_path)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} discover: Stage.Open failed path={asset_path} exc={exc}", flush=True)
        return out
    if asset_stage is None:
        return out

    try:
        dp = asset_stage.GetDefaultPrim()
        if not dp or not dp.IsValid():
            # default prim 없으면 pseudo-root 첫 자식.
            pseudo = asset_stage.GetPseudoRoot()
            for ch in pseudo.GetAllChildren():
                dp = ch
                break
            if not dp or not dp.IsValid():
                return out
    except Exception:
        return out

    try:
        default_prim_path_str = str(dp.GetPath())
    except Exception:
        return out

    # default prim 부터 그 산하까지 traverse 하며 timeSamples attr 수집.
    try:
        prims_iter = Usd.PrimRange(dp)
    except Exception:
        return out

    prefix = default_prim_path_str.rstrip("/")  # 보통 "/Root" 형태.

    for prim in prims_iter:
        try:
            pp = str(prim.GetPath())
        except Exception:
            continue

        # default prim 자체 → sub_path = ""
        if pp == default_prim_path_str:
            sub_path = ""
        elif pp.startswith(prefix + "/"):
            sub_path = pp[len(prefix) + 1:]  # "/Root/Geom/Mesh1" → "Geom/Mesh1"
        else:
            # default prim 의 산하가 아닌 경우 (예: sibling 에 anim 이 흩어진 자산).
            # 그래도 누락하지 않도록 절대 path 그대로 보관 — bake 가 master 측에서
            # /World/aaa + 절대path 매핑을 시도할 수 있도록.
            sub_path = pp.lstrip("/")

        try:
            attrs = prim.GetAttributes()
        except Exception:
            continue
        for attr in attrs:
            try:
                n_ts = attr.GetNumTimeSamples()
            except Exception:
                continue
            if n_ts <= 0:
                continue
            try:
                name = attr.GetName()
            except Exception:
                continue
            out.append((sub_path, name))

    return out


def kind_to_user_label(kind: str) -> str:
    """`AssetKind` 를 사용자가 읽기 좋은 짧은 라벨로 변환 (UI / 로그용)."""
    if kind == ASSET_KIND_TIMESAMPLES_XFORM:
        return "TIMESAMPLES_XFORM (bake 불필요)"
    if kind == ASSET_KIND_TIMESAMPLES_SKEL:
        return "TIMESAMPLES_SKEL (Skel 평가 경로 검증 필요)"
    if kind == ASSET_KIND_TIMESAMPLES_MESH:
        return "TIMESAMPLES_MESH (Mesh-deform 검증 필요)"
    if kind == ASSET_KIND_OMNIGRAPH:
        return "OMNIGRAPH (bake 필수)"
    if kind == ASSET_KIND_MIXED:
        return "MIXED (bake 권장 — OmniGraph 비활성 + 기존 timeSamples 통과)"
    if kind == ASSET_KIND_STATIC:
        return "STATIC (시간 데이터 0)"
    return "UNKNOWN"
