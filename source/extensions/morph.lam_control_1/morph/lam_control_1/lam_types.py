"""LAM shared data model — `AnimationInstance` and helper types (REQ-004).

- `AnimationInstance`, `StepRef`, `ResolveResult`, asset-kind 상수·bake 분류 헬퍼 정의.
- `LAM_FIXED_FPS` (30) 로 timeCode↔초 변환 고정 — 자산 tps 와 무관하게 LAM 전역 정책.
- Registry·evaluator·sequence step binding 이 본 타입 필드를 단일 SoT 로 공유.
- 수정 시 영향 / related: `lam_instance_registry`, `lam_id_resolver`, `lam_runtime_evaluator`,
  `lam_attribute_reauthor`, `lam_playback_scheduler`, `lam_asset_diagnostics`.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Tuple


# ---------------------------------------------------------------- 전역 정책 상수

# LAM 의 모든 timeCode↔초 변환은 30 fps 로 고정한다(사용자 요구 2026-05-11).
#  - 자산 USD 의 `GetTimeCodesPerSecond()` 가 24 / 60 등 다른 값이어도 LAM 은 30 으로 해석.
#  - 즉 자산 frame range [0, 30] = 1 초, [0, 60] = 2 초.
#  - inst.asset_tps 는 본 상수로 강제 정규화된다(`AnimationInstance.__post_init__`).
#  - 모든 모듈(evaluator / runtime / scheduler / sequence / attribute_reauthor /
#    offset_correction)은 본 상수만 사용해야 하고, 자산 측 tps 는 진단 출력 외에는
#    의사결정에 쓰지 않는다.
LAM_FIXED_FPS: float = 30.0


def make_guid() -> str:
    """REQ-005 메타데이터 규약에 사용되는 영구 고유 ID 발급."""
    return str(uuid.uuid4())


def utc_now_iso() -> str:
    """REQ-005 `lam:added_at` 용 ISO 8601 표기."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# REQ-006 Q-2 권장값. range_mode 의 가능한 값은 {"full", "frames", "ratio"}.
DEFAULT_RANGE: Tuple[str, float, float] = ("full", 0.0, 0.0)


# ----------------------------------------------------------------- 자산 종류 분류
# 사용자 요구(2026-05-11 후반): add_usd 시 자산을 자동 스캔해 다음 중 하나로 분류.
# `[Bake]` 의 조건부 분기 와 `TIMESAMPLES_REPLAY` step 의 동작 결정에 사용.
#
# 분류 규칙 (`lam_asset_diagnostics.scan_asset_kind` 참고):
#   - timeSamples 가 박힌 attribute 수를 종류별로 카운트
#       (xformOp:* / SkelAnimation / Mesh points / 기타)
#   - OmniGraph(PushGraph/OmniGraph*) prim 개수 카운트
#   - 두 카운트 조합으로 최종 kind 결정.

ASSET_KIND_UNKNOWN: str = "UNKNOWN"            # 스캔 실패 / 미수행
ASSET_KIND_STATIC: str = "STATIC"              # 시간 데이터 0
ASSET_KIND_TIMESAMPLES_XFORM: str = "TIMESAMPLES_XFORM"  # xformOp:* timeSamples 만 또는 우세
ASSET_KIND_TIMESAMPLES_SKEL: str = "TIMESAMPLES_SKEL"    # SkelAnimation.* timeSamples 우세
ASSET_KIND_TIMESAMPLES_MESH: str = "TIMESAMPLES_MESH"    # Mesh.points 등 vertex anim 우세
ASSET_KIND_OMNIGRAPH: str = "OMNIGRAPH"        # PushGraph 만 있고 timeSamples 없음
ASSET_KIND_MIXED: str = "MIXED"                # OmniGraph + timeSamples 둘 다 있음

# Bake 가 필요한가? — UI 의 [Bake] 조건부 분기 / Sequence Editor 의 자동 안내에 사용.
_KINDS_BAKE_REQUIRED = frozenset({ASSET_KIND_OMNIGRAPH, ASSET_KIND_MIXED})
_KINDS_BAKE_OPTIONAL = frozenset({ASSET_KIND_TIMESAMPLES_SKEL, ASSET_KIND_TIMESAMPLES_MESH})
_KINDS_BAKE_NOT_NEEDED = frozenset({ASSET_KIND_TIMESAMPLES_XFORM, ASSET_KIND_STATIC})


def asset_kind_needs_bake(kind: str) -> bool:
    """해당 kind 의 자산이 멀티 인스턴스 독립 재생을 위해 bake 가 필수인가."""
    return kind in _KINDS_BAKE_REQUIRED


def asset_kind_bake_optional(kind: str) -> bool:
    """bake 가 필수는 아니지만 별도 평가 경로(Skel/Mesh) 검증 필요한 kind."""
    return kind in _KINDS_BAKE_OPTIONAL


def asset_kind_bake_unnecessary(kind: str) -> bool:
    """bake 가 의미 없는 kind (이미 native timeSamples 가 있음 / 시간 데이터 없음)."""
    return kind in _KINDS_BAKE_NOT_NEEDED


@dataclass
class AssetDiag:
    """`lam_asset_diagnostics.scan_asset_kind` 가 채우는 진단 결과.

    `AnimationInstance.asset_diag` 에 저장되어 UI / Bake 분기 / 사용자 로그에 사용.

    카운트는 자산 stage 의 모든 prim 을 traverse 하며 attribute 별로 누적.
    """

    # 종류별 timeSamples 보유 attribute 수.
    n_xform_op_ts: int = 0                    # xformOp:* (translate/rotate/scale/orient/transform/...)
    n_skel_anim_ts: int = 0                   # SkelAnimation 의 translations/rotations/scales/blendShapeWeights
    n_mesh_points_ts: int = 0                 # Mesh 의 points/normals/extent/velocities
    n_visibility_ts: int = 0                  # visibility (xformOp 와 함께 다니지만 분리 카운트)
    n_other_ts: int = 0                       # 위 분류 외 timeSamples (primvars:* 등)

    # OmniGraph 류 prim 수 (PushGraph / OmniGraph* / OG*).
    n_omnigraph_prims: int = 0

    # 자산 stage 정보 (참고용).
    asset_default_prim_path: str = ""
    asset_up_axis: str = ""
    asset_start_tc: float = 0.0
    asset_end_tc: float = 0.0
    asset_native_tps: float = 0.0             # 자산이 선언한 timeCodesPerSecond (LAM 30 으로 정규화 전).

    # OmniGraph prim 의 path 목록 (best-effort, baked.usd 의 비활성 대상 + 진단 출력용).
    omnigraph_prim_paths: Tuple[str, ...] = field(default_factory=tuple)

    def total_ts_attrs(self) -> int:
        return (
            self.n_xform_op_ts
            + self.n_skel_anim_ts
            + self.n_mesh_points_ts
            + self.n_visibility_ts
            + self.n_other_ts
        )

    def to_log_line(self) -> str:
        """사용자 콘솔에 한 줄로 표시되는 요약 (add_usd 직후 출력)."""
        return (
            f"xform={self.n_xform_op_ts} skel={self.n_skel_anim_ts} "
            f"mesh={self.n_mesh_points_ts} vis={self.n_visibility_ts} "
            f"other={self.n_other_ts} | omnigraph_prims={self.n_omnigraph_prims} "
            f"| range=[{self.asset_start_tc},{self.asset_end_tc}]@{self.asset_native_tps}fps "
            f"up={self.asset_up_axis}"
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "n_xform_op_ts": int(self.n_xform_op_ts),
            "n_skel_anim_ts": int(self.n_skel_anim_ts),
            "n_mesh_points_ts": int(self.n_mesh_points_ts),
            "n_visibility_ts": int(self.n_visibility_ts),
            "n_other_ts": int(self.n_other_ts),
            "n_omnigraph_prims": int(self.n_omnigraph_prims),
            "asset_default_prim_path": str(self.asset_default_prim_path),
            "asset_up_axis": str(self.asset_up_axis),
            "asset_start_tc": float(self.asset_start_tc),
            "asset_end_tc": float(self.asset_end_tc),
            "asset_native_tps": float(self.asset_native_tps),
            "omnigraph_prim_paths": list(self.omnigraph_prim_paths),
        }


@dataclass
class AnimationInstance:
    """master stage 안의 1개 재생 단위.

    필드 의미는 REQ-004 데이터 모델 표를 그대로 따른다.

    참고: `asset_tps` 는 `__post_init__` 에서 **항상 `LAM_FIXED_FPS`(=30)** 로 정규화된다.
    외부 코드(loader / discovery / json 입력 등)가 어떤 값을 전달해도 30 으로 강제 — 사용자
    요구(2026-05-11) "FPS=30 고정, timeCode 30 = 1 초" 정책을 단일 진입점에서 보장한다.
    """

    prim_path: str
    guid: str
    instance_id: str
    source_asset: str = ""
    discovered_by: str = "user_register"  # "user_register" | "composition_discovery"

    # 런타임 상태. L4 Scheduler 가 갱신, L5 Evaluator 가 reauthor 시 참조.
    virtual_time: float = 0.0
    speed: float = 1.0
    loop: bool = False
    state: str = "stopped"   # "stopped" | "playing" | "paused"
    offset_sec: float = 0.0
    range: Tuple[str, float, float] = DEFAULT_RANGE

    # 자산이 가진 stage time (timeCode) 범위 — discovery 시 1회 채움.
    # range_mode == "full" 일 때 evaluator 가 이 값을 사용한다.
    asset_start_time: float = 0.0
    asset_end_time: float = 0.0
    asset_tps: float = LAM_FIXED_FPS

    # 자산 종류 자동 분류 결과 (W1 — 2026-05-11 후반). `add_usd` 가 자산을 스캔해 채움.
    #   - kind: ASSET_KIND_* 중 하나
    #   - diag: 종류별 timeSamples 카운트 + OmniGraph prim 수 등 상세
    # Discovery / JSON load 경로로 만들어진 인스턴스는 처음에는 UNKNOWN 으로 두고,
    # 필요 시 lam_asset_diagnostics.refresh_instance_kind() 로 채울 수 있다.
    asset_kind: str = ASSET_KIND_UNKNOWN
    asset_diag: AssetDiag = field(default_factory=lambda: AssetDiag())

    # In-memory bake 상태 (W5 — 2026-05-12 후반).
    # `RuntimeEvaluator.attach_memory_baked_layer` 가 성공하면 True 로 박힌다. UI 가 이
    # 값을 보고 [Bake] 버튼 라벨/색을 [BAKED ✓ / Re-bake] 로 전환한다. Kit 종료 시 in-memory
    # baked layer 가 휘발 → 다음 세션은 다시 False 로 시작 (D13 정책).
    baked: bool = False

    # Option E mirror 루트 (2026-05-14) — drag&drop 으로 자산이 `/World/inst/test1/...`
    # 처럼 한 단계 더 깊게 박힌 경우, offscreen `/Root` 트리를 master 의 **이 경로** 아래에
    # 매핑한다. 비어 있으면 `prim_path` 와 동일하게 동작(기존 add_usd 직접 reference).
    mirror_root_prim_path: str = ""

    def __post_init__(self) -> None:
        # FPS 30 고정 정책 — 입력값이 어떤 값이든 항상 LAM_FIXED_FPS 로 정규화.
        # asset_start_time / asset_end_time 은 timeCode (frame 번호) 그대로 — LAM 은
        # 이 frame 범위를 30 fps 기준으로 초 변환한다.
        self.asset_tps = LAM_FIXED_FPS

    def added_at(self) -> str:  # pragma: no cover - 직렬화용 헬퍼
        return utc_now_iso()

    def to_metadata_dict(self) -> dict:
        """REQ-005 customData 직렬화용 dict."""
        return {
            "lam:instance": True,
            "lam:guid": self.guid,
            "lam:instance_id": self.instance_id,
            "lam:source_asset": self.source_asset,
        }


@dataclass
class StepRef:
    """REQ-006 시퀀스 step 안의 4-튜플 참조 블록."""

    prim_path: str = ""
    guid: str = ""
    instance_id: str = ""
    source_asset: str = ""

    def to_dict(self) -> dict:
        return {
            "prim_path": self.prim_path,
            "guid": self.guid,
            "instance_id": self.instance_id,
            "source_asset": self.source_asset,
        }

    @classmethod
    def from_dict(cls, raw: dict | None) -> "StepRef":
        raw = raw or {}
        return cls(
            prim_path=str(raw.get("prim_path", "") or ""),
            guid=str(raw.get("guid", "") or ""),
            instance_id=str(raw.get("instance_id", "") or ""),
            source_asset=str(raw.get("source_asset", "") or ""),
        )


# REQ-006 Resolver 결과 표시 배지.
RESOLVE_OK = "OK"          # 1·2 순위 (guid / prim_path)
RESOLVE_AUTO = "AUTO"      # 3·4 순위 (instance_id / source_asset) — 자동 갱신 발생
RESOLVE_MISSING = "MISSING"  # 매칭 실패


@dataclass
class ResolveResult:
    """`lam_id_resolver.resolve_step_ref()` 의 반환 객체."""

    status: str           # RESOLVE_OK | RESOLVE_AUTO | RESOLVE_MISSING
    matched_by: str = ""  # "guid" | "prim_path" | "instance_id" | "source_asset" | ""
    instance: "AnimationInstance | None" = field(default=None)
    updated_ref: StepRef | None = field(default=None)  # 매칭 성공 시 자동 갱신된 새 ref
