"""L4 — Playback Scheduler.

L3 Registry 의 `AnimationInstance` 들을 대상으로 외부에 단일 진입점을 제공한다.
- start / stop / pause / resume
- set_speed / set_loop / set_offset
- "이어 실행 vs 1회성 reset" 정책

본 모듈은 evaluator 의 update 루프를 대신하지 않는다. 단지 인스턴스의
`state / virtual_time / speed / loop / offset_sec / range` 만 갱신할 뿐.
실제 attribute 변경(reauthor)은 L5 Evaluator 의 책임.
"""

from __future__ import annotations

from typing import Optional, Tuple

from .lam_instance_registry import AnimationInstanceRegistry
from .lam_runtime_evaluator import RuntimeEvaluator
from .lam_types import AnimationInstance


_PRINT_PREFIX = "[LAM/L4]"


class PlaybackScheduler:
    """LAM 에서 외부(시퀀스 엔진/UI 버튼)가 부르는 단일 API."""

    def __init__(self, registry: AnimationInstanceRegistry, evaluator: RuntimeEvaluator) -> None:
        self._registry = registry
        self._evaluator = evaluator

    def _get(self, prim_path: str) -> Optional[AnimationInstance]:
        inst = self._registry.get_by_prim_path(prim_path)
        if inst is None:
            print(f"{_PRINT_PREFIX} unknown prim_path={prim_path}", flush=True)
        return inst

    # ------------------------------------------------------------------ start

    def start(
        self,
        prim_path: str,
        *,
        reset: bool = False,
        speed: float | None = None,
        loop: bool | None = None,
        offset_sec: float | None = None,
        range_mode: str | None = None,
        range_start: float | None = None,
        range_end: float | None = None,
    ) -> bool:
        """인스턴스 재생 시작.

        REQ-004 "이어 실행" 정책 — 기본은 `virtual_time` 을 0 으로 리셋하지 않는다.
        명시적으로 처음부터 시작하고 싶으면 `reset=True`.
        """
        inst = self._get(prim_path)
        if inst is None:
            return False

        if speed is not None:
            inst.speed = max(0.01, float(speed))
        if loop is not None:
            inst.loop = bool(loop)
        if offset_sec is not None:
            inst.offset_sec = float(offset_sec)
        if range_mode is not None:
            mode = (range_mode or "full").strip().lower()
            if mode not in {"full", "frames", "ratio"}:
                mode = "full"
            inst.range = (mode, float(range_start or 0.0), float(range_end or 0.0))

        if reset:
            inst.virtual_time = self._range_start_seconds(inst)
        elif inst.state == "stopped":
            # 처음 켜는 경우엔 자연스러운 시작점부터.
            inst.virtual_time = self._range_start_seconds(inst)

        inst.state = "playing"
        # Hotfix6 — vt 를 직접 seek 했으므로 evaluator 의 LayerOffset mapping 시그니처를
        # 무효화한다(다음 update tick 에서 새 매핑 author).
        try:
            self._evaluator.invalidate_mapping(prim_path)
        except Exception:
            pass
        print(
            f"{_PRINT_PREFIX} start prim={prim_path} reset={reset} "
            f"v_t={inst.virtual_time:.3f}s sp={inst.speed} loop={inst.loop}",
            flush=True,
        )
        return True

    def pause(self, prim_path: str) -> bool:
        inst = self._get(prim_path)
        if inst is None:
            return False
        if inst.state == "playing":
            inst.state = "paused"
            print(f"{_PRINT_PREFIX} pause prim={prim_path}", flush=True)
        return True

    def resume(self, prim_path: str) -> bool:
        inst = self._get(prim_path)
        if inst is None:
            return False
        if inst.state == "paused":
            inst.state = "playing"
            print(f"{_PRINT_PREFIX} resume prim={prim_path}", flush=True)
        return True

    def stop(self, prim_path: str) -> bool:
        inst = self._get(prim_path)
        if inst is None:
            return False
        inst.state = "stopped"
        print(f"{_PRINT_PREFIX} stop prim={prim_path}", flush=True)
        return True

    def stop_all(self) -> None:
        for inst in self._registry.all_instances():
            inst.state = "stopped"
        print(f"{_PRINT_PREFIX} stop_all", flush=True)

    # --------------------------------------------------------------- mutators

    def set_speed(self, prim_path: str, speed: float) -> bool:
        inst = self._get(prim_path)
        if inst is None:
            return False
        inst.speed = max(0.01, float(speed))
        return True

    def set_loop(self, prim_path: str, loop: bool) -> bool:
        inst = self._get(prim_path)
        if inst is None:
            return False
        inst.loop = bool(loop)
        return True

    def set_offset(self, prim_path: str, offset_sec: float) -> bool:
        inst = self._get(prim_path)
        if inst is None:
            return False
        inst.offset_sec = float(offset_sec)
        return True

    # ----------------------------------------------------------------- helper

    def _range_start_seconds(self, inst: AnimationInstance) -> float:
        """현재 range_mode 기준의 시작 초 값 계산."""
        mode, s, e = inst.range
        tps = inst.asset_tps if inst.asset_tps > 0 else 30.0
        if mode == "frames":
            return float(s) / tps
        if mode == "ratio":
            length = max(0.0, inst.asset_end_time - inst.asset_start_time) / tps
            return (inst.asset_start_time / tps) + max(0.0, min(1.0, float(s))) * length
        # "full" — 자산 stage start
        return float(inst.asset_start_time) / tps

    def range_end_seconds(self, inst: AnimationInstance) -> float:
        """L5 가 loop/완료 판정에 사용하는 끝 초 값."""
        mode, s, e = inst.range
        tps = inst.asset_tps if inst.asset_tps > 0 else 30.0
        if mode == "frames":
            return float(e) / tps if e > s else self._range_start_seconds(inst)
        if mode == "ratio":
            length = max(0.0, inst.asset_end_time - inst.asset_start_time) / tps
            return (inst.asset_start_time / tps) + max(0.0, min(1.0, float(e))) * length
        # "full" — 자산 stage end
        return float(inst.asset_end_time) / tps


__all__ = ["PlaybackScheduler"]
