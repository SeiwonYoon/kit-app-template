"""LAM 데이터 모델 — `AnimationInstance` 와 보조 타입.

USD_Timeline_Spec.md REQ-004 의 데이터 모델을 코드로 굳힌 것이다.
이 dataclass 는 L3 Registry 가 단일 진실 원천(SoT)으로 보유한다.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Tuple


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
