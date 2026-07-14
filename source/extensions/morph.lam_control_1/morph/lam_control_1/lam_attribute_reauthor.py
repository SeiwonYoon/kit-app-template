"""Phase 2 — Attribute Reauthor 핵심 로직.

L5 RuntimeEvaluator 가 매 프레임 호출하는 reauthor 의 실제 구현.

원리:
- 각 LAM AnimationInstance 의 prim 산하에서 `GetNumTimeSamples() > 0` 인 attribute 들을
  1회 캐시한다(인스턴스 첫 play 진입 시). 이 attribute 들이 "애니메이션 대상".
- 매 프레임:
    timeCode = (virtual_time + offset_sec) * asset_tps
    (선택) ``snap_timecode_to_frame`` 가 True 이면 ``timeCode = round(timeCode)`` 로
    정수 프레임에만 샘플 — 타임라인 재생에 가깝게 밟아 Euler 보간 튐을 완화.
    val = attr.Get(timeCode)   ← USD 자체의 evaluation API. omni.timeline 미사용.
    attr.Set(val)              ← root layer (현재 EditTarget) 에 default value 로 author.
- USD value resolution: stronger layer 의 default 는 weaker layer (reference) 의 timeSamples 를
  마스킹한다. 따라서 같은 master stage 안의 두 인스턴스가 서로 다른 timeCode 의 결과를
  동시에 표현 가능 → §3.1 단일 stage 멀티 평가 한계 우회.

본 모듈은 omni.timeline 을 사용하지 않는다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .lam_types import AnimationInstance, LAM_FIXED_FPS


_PRINT_PREFIX = "[LAM/REAUTHOR]"


class _AttrCacheEntry:
    """1 attribute 의 reauthor 메타."""

    __slots__ = ("attr", "min_tc", "max_tc")

    def __init__(self, attr, min_tc: float, max_tc: float) -> None:
        self.attr = attr
        self.min_tc = float(min_tc)
        self.max_tc = float(max_tc)


class AttributeReauthorCache:
    """인스턴스(prim_path) → 애니메이션 대상 attribute 목록 캐시."""

    def __init__(self) -> None:
        # prim_path -> list of _AttrCacheEntry
        self._by_prim: Dict[str, List[_AttrCacheEntry]] = {}

    def invalidate(self, prim_path: str) -> None:
        self._by_prim.pop(prim_path, None)

    def invalidate_all(self) -> None:
        self._by_prim.clear()

    def ensure_built(self, stage, prim_path: str) -> List[_AttrCacheEntry]:
        """캐시가 없으면 prim 산하 traverse 로 빌드."""
        cached = self._by_prim.get(prim_path)
        if cached is not None:
            return cached

        entries: List[_AttrCacheEntry] = []
        if stage is None:
            self._by_prim[prim_path] = entries
            return entries

        try:
            root_prim = stage.GetPrimAtPath(prim_path)
        except Exception:
            self._by_prim[prim_path] = entries
            return entries

        if not root_prim:
            self._by_prim[prim_path] = entries
            return entries

        try:
            iterable = list(root_prim.GetAllChildren()) if hasattr(root_prim, "GetAllChildren") else []
        except Exception:
            iterable = []

        # 본인 + 모든 자손 prim 을 순회.
        try:
            from pxr import Usd  # type: ignore

            stack = [root_prim]
            seen_paths = set()
            while stack:
                p = stack.pop()
                try:
                    pp = str(p.GetPath())
                except Exception:
                    continue
                if pp in seen_paths:
                    continue
                seen_paths.add(pp)
                # children push (composed children 포함)
                try:
                    for ch in p.GetAllChildren():
                        stack.append(ch)
                except Exception:
                    pass

                # attribute 후보 등록
                try:
                    attrs = p.GetAuthoredAttributes()
                except Exception:
                    attrs = []
                for attr in attrs:
                    try:
                        n = attr.GetNumTimeSamples()
                    except Exception:
                        continue
                    if n <= 0:
                        continue
                    try:
                        bracket = attr.GetTimeSamplesInInterval(Usd.Interval.GetFullInterval())
                    except Exception:
                        bracket = []
                    if not bracket:
                        try:
                            mn = attr.GetTimeSamples()[0]
                            mx = attr.GetTimeSamples()[-1]
                        except Exception:
                            mn, mx = 0.0, 0.0
                    else:
                        mn = float(bracket[0])
                        mx = float(bracket[-1])
                    entries.append(_AttrCacheEntry(attr, mn, mx))
        except Exception as exc:
            print(f"{_PRINT_PREFIX} ensure_built failed prim={prim_path}: {exc}", flush=True)

        self._by_prim[prim_path] = entries
        if entries:
            print(
                f"{_PRINT_PREFIX} cache built prim={prim_path} attrs={len(entries)}",
                flush=True,
            )
        return entries

    def reauthor_at(
        self,
        stage,
        inst: AnimationInstance,
        eval_seconds: float,
        *,
        snap_timecode_to_frame: bool = True,
    ) -> int:
        """인스턴스의 모든 애니메이션 attribute 를 평가하고 default 값으로 reauthor.

        반환: 실제로 reauthor 한 attribute 개수.
        """
        if stage is None:
            return 0
        entries = self.ensure_built(stage, inst.prim_path)
        if not entries:
            return 0

        tps = LAM_FIXED_FPS
        timeCode = float(eval_seconds) * tps
        if snap_timecode_to_frame:
            timeCode = float(round(timeCode))

        wrote = 0
        for entry in entries:
            attr = entry.attr
            # 각 attribute 의 자체 시간 범위로 clamp(기본 동작은 USD 가 자동 처리하지만,
            # loop 범위를 벗어난 값이 NaN/Inf 처럼 들어오는 케이스 방어).
            tc = timeCode
            if entry.max_tc >= entry.min_tc:
                if tc < entry.min_tc:
                    tc = entry.min_tc
                elif tc > entry.max_tc:
                    tc = entry.max_tc

            try:
                val = attr.Get(tc)
            except Exception:
                continue
            if val is None:
                continue
            try:
                # default value 로 박는다. EditTarget 이 root layer 라면 root layer 의 strongest
                # opinion 이 되어, reference 안의 timeSamples 를 가린다.
                attr.Set(val)
                wrote += 1
            except Exception:
                continue
        return wrote


__all__ = ["AttributeReauthorCache"]
