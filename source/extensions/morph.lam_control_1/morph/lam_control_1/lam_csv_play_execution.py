"""CSV 시뮬 재생 — 화면별 registry/scheduler/USD context 실행 바인딩 (TBS ``SequenceRunner`` 패턴)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

_PRINT_PREFIX = "[LAM/CsvPlayExec]"


@dataclass
class CsvPlayExecutionBinding:
    """화면 1개분 JSON 실행에 필요한 registry·scheduler·USD context."""

    screen: int
    registry: Any
    scheduler: Any
    usd_context_name: Optional[str]
    stage: Any
    master: Any
    kit_ext: Any
    lam_window: Any


def resolve_csv_play_execution(
    lam_window: Any,
    screen: int,
    *,
    kit_ext: Any = None,
    require_aux: bool = False,
) -> Optional[CsvPlayExecutionBinding]:
    """화면별 실행 바인딩. 화면2+ 에서 split runtime 이 없으면 ``None`` (화면1 fallback 금지)."""
    si = max(1, int(screen))
    if lam_window is None:
        return None
    ext = kit_ext if kit_ext is not None else getattr(lam_window, "_kit_ext", None)
    try:
        from .lam_csv_play_screen import (
            get_registry_scheduler_for_lam_screen,
            get_stage_for_screen,
            usd_context_name_for_screen,
        )

        reg, sch = get_registry_scheduler_for_lam_screen(lam_window, si, allow_fallback=False)
        if reg is None or sch is None:
            if require_aux and si > 1:
                print(
                    f"{_PRINT_PREFIX} screen{si} — registry/scheduler 없음 "
                    "(분할 viewport·USD hydrate 후 다시 시도)",
                    flush=True,
                )
            return None
        cn = usd_context_name_for_screen(ext, si) if ext is not None else None
        if si <= 1:
            cn = None
        st = get_stage_for_screen(ext, si) if ext is not None else None
        if st is None and si <= 1:
            try:
                from .lam_prim_utils import get_stage

                st = get_stage()
            except Exception:
                st = None
        master = getattr(lam_window, "_master", None)
        if si > 1 and ext is not None:
            try:
                from .lam_split_composed_loader import get_split_runtime_for_screen

                rt = get_split_runtime_for_screen(ext, si)
                if rt is not None:
                    if getattr(rt, "master", None) is not None:
                        master = rt.master
                    if getattr(rt, "context_name", None):
                        cn = str(rt.context_name).strip() or cn
            except Exception:
                pass
        if require_aux and si > 1:
            if not cn or st is None:
                print(
                    f"{_PRINT_PREFIX} screen{si} execution incomplete — "
                    f"ctx={cn!r} stage={st is not None}",
                    flush=True,
                )
                return None
        return CsvPlayExecutionBinding(
            screen=si,
            registry=reg,
            scheduler=sch,
            usd_context_name=cn,
            stage=st,
            master=master,
            kit_ext=ext,
            lam_window=lam_window,
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} resolve screen={si} failed: {exc}", flush=True)
        return None


def get_csv_sequence_runner(lam_window: Any, binding: CsvPlayExecutionBinding) -> Any:
    """화면별 ``LamSequenceRunner`` (lazy, registry/ctx 변경 시 재생성)."""
    from .lam_sequence_engine import LamSequenceRunner

    si = binding.screen
    runners: Dict[str, Any] = getattr(lam_window, "_csv_sequence_runners_by_screen", None)  # type: ignore
    if not isinstance(runners, dict):
        runners = {}
        lam_window._csv_sequence_runners_by_screen = runners
    key = str(si)
    sig = (
        id(binding.registry),
        id(binding.scheduler),
        str(binding.usd_context_name or ""),
    )
    cached = runners.get(key)
    if (
        cached is not None
        and getattr(cached, "_binding_sig", None) == sig
    ):
        return cached
    runner = LamSequenceRunner(
        binding.registry,
        binding.scheduler,
        usd_context_name=binding.usd_context_name,
    )
    runner._binding_sig = sig  # type: ignore[attr-defined]
    runners[key] = runner
    return runner


def run_lam_sim_steps_for_screen(
    lam_window: Any,
    screen: int,
    steps: Any,
    *,
    kit_ext: Any = None,
    speed_scale: float = 1.0,
    lane: Optional[str] = None,
    lane_coordinator: Any = None,
) -> bool:
    """화면 N 전용 registry + USD context 로 JSON steps 실행."""
    binding = resolve_csv_play_execution(
        lam_window,
        screen,
        kit_ext=kit_ext,
        require_aux=(int(screen) > 1),
    )
    if binding is None:
        return False
    from .simulation_play import _run_lam_sim_steps_cancellable

    _run_lam_sim_steps_cancellable(
        binding.registry,
        binding.scheduler,
        steps,
        speed_scale=speed_scale,
        lane=lane,
        lane_coordinator=lane_coordinator,
        usd_context_name=binding.usd_context_name,
        lam_window=lam_window,
        play_screen=binding.screen,
    )
    return True


def stop_csv_play_motion_for_screen(
    screen: int,
    *,
    kit_ext: Any = None,
    lam_window: Any = None,
) -> None:
    """지정 화면의 MOVE/ROTATE 만 중지 (다른 화면 재생 유지)."""
    si = max(1, int(screen))
    cn: Optional[str] = None
    if kit_ext is not None:
        try:
            from .lam_csv_play_screen import usd_context_name_for_screen

            cn = usd_context_name_for_screen(kit_ext, si)
            if si <= 1:
                cn = None
        except Exception:
            cn = None
    try:
        from . import lam_rotate_animation as _lrx
        from . import lam_translate_animation as _ltx

        if cn:
            _ltx.stop_translate_animations_for_context(cn)
            _lrx.stop_rotate_animations_for_context(cn)
        elif si <= 1:
            _ltx.stop_all_translate_animations()
            _lrx.stop_all_rotate_animations()
    except Exception as exc:
        print(f"{_PRINT_PREFIX} stop motion screen={si}: {exc}", flush=True)
    if lam_window is not None:
        runners = getattr(lam_window, "_csv_sequence_runners_by_screen", None)
        if isinstance(runners, dict):
            runners.pop(str(si), None)


def resolve_registry_scheduler_for_play(
    lam_window: Any,
    screen: int,
    *,
    kit_ext: Any = None,
) -> Tuple[Any, Any, Optional[str]]:
    """Play 경로용 (registry, scheduler, usd_context_name). 실패 시 (None, None, None)."""
    binding = resolve_csv_play_execution(
        lam_window,
        screen,
        kit_ext=kit_ext,
        require_aux=(int(screen) > 1),
    )
    if binding is None:
        return None, None, None
    return binding.registry, binding.scheduler, binding.usd_context_name


__all__ = [
    "CsvPlayExecutionBinding",
    "get_csv_sequence_runner",
    "resolve_csv_play_execution",
    "resolve_registry_scheduler_for_play",
    "run_lam_sim_steps_for_screen",
    "stop_csv_play_motion_for_screen",
]
