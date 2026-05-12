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
from .lam_types import AnimationInstance, LAM_FIXED_FPS


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
        # B-3 — Option E 의 1회 진단 플래그와 attribute 캐시를 RUN 마다 reset 하여
        # 매 RUN 시작 시 `init / cache map / cache built / diag dump / first evaluate`
        # 진단이 다시 출력되도록 한다(Kit 재시작 없이도 새 진단 코드의 동작을 확인 가능).
        try:
            fn1 = getattr(self._evaluator, "reset_option_e_diag", None)
            if callable(fn1):
                fn1(prim_path)
            fn2 = getattr(self._evaluator, "force_rebuild_attr_cache", None)
            if callable(fn2):
                fn2(prim_path)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} option_e diag reset failed prim={prim_path}: {exc}",
                flush=True,
            )
        print(
            f"{_PRINT_PREFIX} start prim={prim_path} reset={reset} "
            f"v_t={inst.virtual_time:.3f}s sp={inst.speed} loop={inst.loop}",
            flush=True,
        )
        return True

    def begin_master_timeline_mode(self, prim_path: str) -> bool:
        """`RuntimeEvaluator.begin_master_timeline_mode` 위임 (USD_TIMELINE 테스트용)."""
        try:
            return bool(self._evaluator.begin_master_timeline_mode(prim_path))
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} begin_master_timeline_mode EXC prim={prim_path}: {exc}",
                flush=True,
            )
            return False

    def begin_bake_mode(self, prim_path: str) -> bool:
        """`RuntimeEvaluator.begin_bake_mode` 위임 — bake 진행 중 author 가드."""
        try:
            return bool(self._evaluator.begin_bake_mode(prim_path))
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} begin_bake_mode EXC prim={prim_path}: {exc}",
                flush=True,
            )
            return False

    def end_bake_mode(self, prim_path: str) -> None:
        """`RuntimeEvaluator.end_bake_mode` 위임."""
        try:
            self._evaluator.end_bake_mode(prim_path)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} end_bake_mode EXC prim={prim_path}: {exc}",
                flush=True,
            )

    def set_omnigraph_active_for_instance(
        self, prim_path: str, active: bool
    ) -> int:
        """`RuntimeEvaluator.set_omnigraph_active_for_instance` 위임.

        bake 시작 전 ``active=True`` 로 호출하여 PushGraph 등 OmniGraph 가 평가되도록
        한 뒤 bake 를 수행하고, bake 완료 후에는 ``attach_memory_baked_layer`` 가 표식을
        reset 하므로 다음 update tick 에서 자동으로 다시 deactivate 된다.
        """
        try:
            return int(
                self._evaluator.set_omnigraph_active_for_instance(prim_path, bool(active))
            )
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} set_omnigraph_active_for_instance EXC prim={prim_path} "
                f"active={active}: {exc}",
                flush=True,
            )
            return 0

    def end_master_timeline_mode(
        self,
        prim_path: str,
        *,
        freeze_at_tc: Optional[float] = None,
    ) -> None:
        """`RuntimeEvaluator.end_master_timeline_mode` 위임.

        Args:
            freeze_at_tc: 지정 시 LayerOffset(freeze_at_tc, 1e-9) 로 freeze author →
                USD_TIMELINE step 종료 후 viewport 가 해당 timeCode 시점에 머문다.
        """
        try:
            self._evaluator.end_master_timeline_mode(
                prim_path, freeze_at_tc=freeze_at_tc
            )
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} end_master_timeline_mode EXC prim={prim_path}: {exc}",
                flush=True,
            )

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
        # FPS 30 고정 정책 — 자산 tps 무시.
        tps = LAM_FIXED_FPS
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
        tps = LAM_FIXED_FPS
        if mode == "frames":
            return float(e) / tps if e > s else self._range_start_seconds(inst)
        if mode == "ratio":
            length = max(0.0, inst.asset_end_time - inst.asset_start_time) / tps
            return (inst.asset_start_time / tps) + max(0.0, min(1.0, float(e))) * length
        # "full" — 자산 stage end
        return float(inst.asset_end_time) / tps


__all__ = ["PlaybackScheduler"]
