"""TBS 시퀀스 실행 — legacy API 유지 + LAM 엔진(registry 있을 때).

control_window 는 ``SequenceRunner.run(steps, usd_context_name=, speed_scale=)``,
``stop()``, ``is_running()``, ``on_sequence_completed`` 를 기대한다.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from .sequence_engine_legacy import (  # noqa: F401 — re-export helpers
    SequenceRunner as _LegacySequenceRunner,
    capture_composed_local_start_snapshot_for_paths,
    resolve_prim_paths,
    resolve_prim_paths_multi,
    _get_rotate_xyz,
    _get_stage,
    _get_stage_for_context,
    _get_translate,
    _set_rotate_xyz,
    _set_translate,
)
from .tbs_lam_sequence_editor import _coerce_loaded_step


def _normalize_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in steps or []:
        if not isinstance(raw, dict):
            continue
        coerced = _coerce_loaded_step(raw)
        if coerced is not None:
            out.append(coerced)
        else:
            out.append(dict(raw))
    return out


class SequenceRunner(_LegacySequenceRunner):
    """Legacy tick runner + LAM ``TbsLamSequenceRunner`` (default USD context)."""

    def __init__(
        self,
        registry: Any = None,
        scheduler: Any = None,
        evaluator: Any = None,
        on_sequence_completed: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(on_sequence_completed=on_sequence_completed)
        self._tbs_registry = registry
        self._tbs_scheduler = scheduler
        self._tbs_evaluator = evaluator
        self._lam_runner: Any = None
        self._lam_thread: Optional[threading.Thread] = None
        self._lam_running = False
        self._lam_last_steps: List[Dict[str, Any]] = []

    def is_running(self) -> bool:
        if self._lam_running:
            return True
        return super().is_running()

    def _halt_lam_runner(self) -> None:
        if self._lam_runner is not None:
            try:
                self._lam_runner.stop()
            except Exception:
                pass
        self._lam_running = False
        self._lam_thread = None

    def pause(self) -> None:
        """애니만 중단 — prim 위치는 유지 (시뮬 **정지**)."""
        self._halt_lam_runner()
        super().pause()

    def stop(self) -> None:
        """애니 중단 + baseline 복원 (시뮬 **리셋**·명시적 초기화)."""
        self._halt_lam_runner()
        steps = list(getattr(self, "_lam_last_steps", None) or [])
        if steps:
            self._steps = steps
        super().stop()

    def _use_lam_engine(self, steps: List[Dict[str, Any]], usd_context_name: Optional[str]) -> bool:
        if self._tbs_registry is None or self._tbs_scheduler is None:
            return False
        if (usd_context_name or "").strip():
            return False
        return True

    def run(
        self,
        steps: List[Dict[str, Any]],
        *,
        usd_context_name: Optional[str] = None,
        speed_scale: float = 1.0,
    ) -> None:
        normalized = _normalize_steps(list(steps or []))
        if not self._use_lam_engine(normalized, usd_context_name):
            super().run(normalized, usd_context_name=usd_context_name, speed_scale=speed_scale)
            return

        from .tbs_lam_sequence_engine import TbsLamSequenceRunner

        if self._lam_thread is not None and self._lam_thread.is_alive():
            try:
                if self._lam_runner is not None:
                    self._lam_runner.stop()
            except Exception:
                pass

        self._lam_last_steps = list(normalized)
        self._steps = list(normalized)
        self._lam_runner = TbsLamSequenceRunner(self._tbs_registry, self._tbs_scheduler)
        self._lam_running = True
        cb = self.on_sequence_completed
        sp = max(0.01, float(speed_scale or 1.0))

        def _bg() -> None:
            try:
                self._lam_runner.run(
                    normalized,
                    speed_scale=sp,
                    quiet=True,
                    reset_each_start=True,
                )
            except Exception as exc:
                print(f"[TBS/SEQ] lam runner failed: {exc}", flush=True)
            finally:
                self._lam_running = False
                if callable(cb):
                    try:
                        cb()
                    except Exception:
                        pass

        self._lam_thread = threading.Thread(target=_bg, name="tbs_lam_sequence_run", daemon=True)
        self._lam_thread.start()


__all__ = [
    "SequenceRunner",
    "capture_composed_local_start_snapshot_for_paths",
    "resolve_prim_paths",
    "resolve_prim_paths_multi",
    "_get_rotate_xyz",
    "_get_stage",
    "_get_stage_for_context",
    "_get_translate",
    "_set_rotate_xyz",
    "_set_translate",
]
