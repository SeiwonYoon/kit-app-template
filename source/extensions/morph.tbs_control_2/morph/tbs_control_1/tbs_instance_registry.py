"""L3 — Animation Instance Registry.

`AnimationInstance` 의 단일 진실 원천(SoT).

핵심 규칙(USD_Timeline_Spec.md):
- REQ-002 부록 A·B  : `instance_id` 자동 suffix (`CharA`, `CharA_1`, `CharA_2` …)
- REQ-002 부록 B    : `instance_id` slugify 후 prim 이름으로도 사용 가능
- REQ-004           : `prim_path` 가 1차 키. 동일 `prim_path` 는 단 1개만 등록.
- REQ-005           : customData 메타(`lam:guid` 등)는 prim 자체에 박힘. 본 Registry 는
                      그 메타 dict 를 받아 객체로 복원하는 `from_metadata()` 도 제공.

본 모듈은 USD 자체를 만지지 않는다(load/discovery 가 가져온 정보만 보관).
"""

from __future__ import annotations

import re
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional

from .tbs_types import AnimationInstance, make_guid


_PRINT_PREFIX = "[TBS/L3]"

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_]")


def slugify_instance_id(raw: str) -> str:
    """USD prim 이름 안전 문자(`A-Za-z0-9_`)만 남긴다.

    빈 문자열이 되면 `Asset` 으로 폴백. 첫 글자가 숫자면 앞에 `_` 부여.
    """
    s = (raw or "").strip()
    if not s:
        return "Asset"
    s = _SAFE_NAME_RE.sub("_", s)
    if not s:
        return "Asset"
    if s[0].isdigit():
        s = "_" + s
    return s


class AnimationInstanceRegistry:
    """`AnimationInstance` 들을 들고 있는 thread-safe 컨테이너."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_prim: Dict[str, AnimationInstance] = {}
        self._listeners: List[Callable[[], None]] = []

    # ------------------------------------------------------------------ public

    def add_listener(self, fn: Callable[[], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def all_instances(self) -> List[AnimationInstance]:
        with self._lock:
            return list(self._by_prim.values())

    def get_by_prim_path(self, prim_path: str) -> Optional[AnimationInstance]:
        with self._lock:
            return self._by_prim.get(prim_path)

    def get_by_instance_id(self, instance_id: str) -> Optional[AnimationInstance]:
        with self._lock:
            for inst in self._by_prim.values():
                if inst.instance_id == instance_id:
                    return inst
        return None

    def reserve_instance_id(self, requested: str) -> str:
        """REQ-002 결정 4(b) — 자동 suffix.

        이미 같은 `instance_id` 가 존재하면 `_1`, `_2` … 로 회피. 없으면 그대로.
        """
        base = slugify_instance_id(requested)
        with self._lock:
            existing = {inst.instance_id for inst in self._by_prim.values()}
        if base not in existing:
            return base
        n = 1
        while True:
            cand = f"{base}_{n}"
            if cand not in existing:
                return cand
            n += 1

    def register(
        self,
        *,
        prim_path: str,
        instance_id: str,
        source_asset: str = "",
        guid: str = "",
        discovered_by: str = "user_register",
        asset_start_time: float = 0.0,
        asset_end_time: float = 0.0,
        asset_tps: float = 30.0,
        asset_kind: Optional[str] = None,
        asset_diag: Optional[Any] = None,
    ) -> AnimationInstance:
        """`prim_path` 를 단 1개만 가지는 새 인스턴스 등록.

        Q2 — 2026-05-12: `asset_kind` / `asset_diag` 가 주어지면 `_notify()` 호출 *전*에
        인스턴스에 박는다. 이 두 값을 register() 밖에서 박으면 listener (예: lam_window
        `_refresh_instances`) 가 인스턴스 생성 즉시 동기 호출되어 UI 가 `UNKNOWN` 으로
        먼저 렌더되는 회귀가 발생한다. 호출자가 분류 결과를 이미 알고 있다면 반드시
        여기로 함께 넘겨야 한다.
        """
        if not prim_path:
            raise ValueError("prim_path is required")

        # instance_id 자동 suffix (이미 충돌 없는 이름이 들어오면 그대로 사용)
        final_id = self.reserve_instance_id(instance_id)
        final_guid = guid or make_guid()

        inst = AnimationInstance(
            prim_path=prim_path,
            guid=final_guid,
            instance_id=final_id,
            source_asset=source_asset,
            discovered_by=discovered_by,
            asset_start_time=asset_start_time,
            asset_end_time=asset_end_time,
            asset_tps=asset_tps,
        )

        # 분류 결과를 _notify 전에 먼저 박는다 (Q2). 실패해도 등록 자체는 계속.
        if asset_kind is not None:
            try:
                inst.asset_kind = asset_kind
            except Exception as _ak_exc:
                print(
                    f"{_PRINT_PREFIX} WARN: asset_kind 박기 실패 prim={prim_path} exc={_ak_exc}",
                    flush=True,
                )
        if asset_diag is not None:
            try:
                inst.asset_diag = asset_diag
            except Exception as _ad_exc:
                print(
                    f"{_PRINT_PREFIX} WARN: asset_diag 박기 실패 prim={prim_path} exc={_ad_exc}",
                    flush=True,
                )

        with self._lock:
            if prim_path in self._by_prim:
                raise ValueError(f"prim_path already registered: {prim_path}")
            self._by_prim[prim_path] = inst

        print(
            f"{_PRINT_PREFIX} register prim_path={prim_path} id={final_id} guid={final_guid[:8]}… "
            f"asset_kind={inst.asset_kind!r}",
            flush=True,
        )
        self._notify()
        return inst

    def unregister(self, prim_path: str) -> Optional[AnimationInstance]:
        with self._lock:
            inst = self._by_prim.pop(prim_path, None)
        if inst is not None:
            print(f"{_PRINT_PREFIX} unregister prim_path={prim_path}", flush=True)
            self._notify()
        return inst

    def clear_all(self) -> int:
        """등록된 인스턴스를 모두 제거(master 재오픈 시 stale 항목 방지)."""
        with self._lock:
            n = len(self._by_prim)
            self._by_prim.clear()
        if n:
            print(f"{_PRINT_PREFIX} clear_all n={n}", flush=True)
            self._notify()
        return n

    def from_metadata(
        self,
        *,
        prim_path: str,
        metadata: dict,
        discovered_by: str = "composition_discovery",
    ) -> AnimationInstance:
        """REQ-005 customData 로 직렬화된 메타에서 인스턴스를 복원."""
        instance_id = str(metadata.get("lam:instance_id") or "Asset")
        guid = str(metadata.get("lam:guid") or "")
        source_asset = str(metadata.get("lam:source_asset") or "")
        return self.register(
            prim_path=prim_path,
            instance_id=instance_id,
            source_asset=source_asset,
            guid=guid,
            discovered_by=discovered_by,
        )

    # ----------------------------------------------------------------- private

    def _notify(self) -> None:
        # 외부 리스너에게 변경 알림(메인 창 목록 갱신용).
        listeners: Iterable[Callable[[], None]]
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn()
            except Exception as exc:  # pragma: no cover
                print(f"{_PRINT_PREFIX} listener error: {exc}", flush=True)


__all__ = ["AnimationInstanceRegistry", "slugify_instance_id"]
