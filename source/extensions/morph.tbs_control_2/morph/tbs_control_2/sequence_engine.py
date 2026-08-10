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
        self._last_usd_context_name: Optional[str] = None
        self._on_renewal_step: Optional[Callable[[int, dict], None]] = None
        self._sim_rail: str = ""
        self._diag_ext: Any = None
        self._diag_screen: int = 1

    def _peer_rail_busy(self) -> bool:
        """병렬 타 레일이 살아 있으면 True — 채널 전체 stop 억제."""
        try:
            from .control_sim_playback_gate import is_twin_rail_occupying
            from .sim_parallel_rails import parallel_moves_enabled

            if not parallel_moves_enabled():
                return False
            rail = str(getattr(self, "_sim_rail", "") or "").strip().lower()
            if rail not in ("oht", "move"):
                return False
            ext = getattr(self, "_diag_ext", None)
            if ext is None:
                return False
            scr = int(getattr(self, "_diag_screen", 1) or 1)
            return bool(is_twin_rail_occupying(ext, scr, rail))
        except Exception:
            return False

    @property
    def on_renewal_step(self) -> Optional[Callable[[int, dict], None]]:
        return self._on_renewal_step

    @on_renewal_step.setter
    def on_renewal_step(self, cb: Optional[Callable[[int, dict], None]]) -> None:
        self._on_renewal_step = cb
        lam = self._lam_runner
        if lam is not None:
            try:
                lam.on_renewal_step = cb
            except Exception:
                pass

    def is_running(self) -> bool:
        if self._lam_running:
            return True
        if self._lam_thread is not None and self._lam_thread.is_alive():
            return True
        # 병렬 레일 runner: 타 레일 채널 motion 을 자기 busy 로 보지 않음
        try:
            from .sim_parallel_rails import parallel_moves_enabled

            rail = str(getattr(self, "_sim_rail", "") or "").strip().lower()
            if parallel_moves_enabled() and rail in ("oht", "move"):
                return super().is_running()
        except Exception:
            pass
        ctx = self._last_usd_context_name
        if self._lam_runner is not None:
            ctx = getattr(self._lam_runner, "_usd_context_name", ctx)
        try:
            from .sim_channel_scope import is_channel_motion_busy

            if is_channel_motion_busy(ctx, self._tbs_registry):
                return True
        except Exception:
            pass
        return super().is_running()

    def _halt_lam_runner(self) -> None:
        if self._lam_runner is not None:
            try:
                self._lam_runner.stop(cancel_all_move_rotate=not self._peer_rail_busy())
            except TypeError:
                try:
                    self._lam_runner.stop()
                except Exception:
                    pass
            except Exception:
                pass
        self._lam_running = False
        self._lam_thread = None

    def pause(self, *, cancel_all_move_rotate: bool = True) -> None:
        """애니만 중단 — prim 위치는 유지 (시뮬 **정지**)."""
        cancel = bool(cancel_all_move_rotate) and (not self._peer_rail_busy())
        if self._lam_runner is not None:
            try:
                self._lam_runner.stop(cancel_all_move_rotate=cancel)
            except Exception:
                pass
        self._lam_running = False
        self._lam_thread = None
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
        return True

    def run(
        self,
        steps: List[Dict[str, Any]],
        *,
        usd_context_name: Optional[str] = None,
        speed_scale: float = 1.0,
        wait_until_done: bool = False,
    ) -> None:
        normalized = _normalize_steps(list(steps or []))
        if not self._use_lam_engine(normalized, usd_context_name):
            super().run(normalized, usd_context_name=usd_context_name, speed_scale=speed_scale)
            return

        from .tbs_lam_sequence_engine import TbsLamSequenceRunner

        ctx_nm = str(usd_context_name or "").strip() or None
        self._last_usd_context_name = ctx_nm
        peer_busy = self._peer_rail_busy()

        if self._lam_thread is not None and self._lam_thread.is_alive():
            try:
                diag_ext = getattr(self, "_diag_ext", None)
                diag_scr = int(getattr(self, "_diag_screen", 1) or 1)
                if self._lam_runner is not None:
                    try:
                        from . import sim_multi_diag as _mdiag

                        _mdiag.log_runner_preempt(
                            diag_ext,
                            screen=diag_scr,
                            ctx=ctx_nm,
                        )
                    except Exception:
                        pass
                    self._lam_runner.stop(cancel_all_move_rotate=not peer_busy)
                try:
                    self._lam_thread.join(timeout=10.0)
                except Exception:
                    pass
                self._lam_running = False
            except Exception:
                pass

        self._lam_last_steps = list(normalized)
        self._steps = list(normalized)
        self._lam_runner = TbsLamSequenceRunner(
            self._tbs_registry,
            self._tbs_scheduler,
            on_renewal_step=self._on_renewal_step,
            usd_context_name=ctx_nm,
        )
        self._lam_runner._diag_ext = getattr(self, "_diag_ext", None)  # type: ignore[attr-defined]
        try:
            self._lam_runner._diag_screen = int(getattr(self, "_diag_screen", 1) or 1)  # type: ignore[attr-defined]
            self._lam_runner._sim_rail = str(getattr(self, "_sim_rail", "") or "")  # type: ignore[attr-defined]
        except Exception:
            pass
        self._lam_running = True
        cb = self.on_sequence_completed
        sp = max(0.01, float(speed_scale or 1.0))

        def _bg() -> None:
            try:
                # 이번 JSON steps 의 prim 만 TBS_OFFSET 0 (peer 경로 미포함).
                # 병렬에서도 True — pre_json 과 이중으로 자기 JSON 만 초기화해
                # OHT/MOVE 시작 위치 누락을 막는다. 채널 전체 stop 은 하지 않음.
                self._lam_runner.run(
                    normalized,
                    speed_scale=sp,
                    quiet=True,
                    reset_each_start=True,
                )
            except Exception as exc:
                print(f"[TBS/SEQ] lam runner failed: {exc}", flush=True)
            finally:
                # LAM 스텝 종료 — 잔류 MOVE/TIMESAMPLES 정리 후 완료 콜백.
                self._lam_running = False
                try:
                    from .sim_channel_scope import (
                        drain_channel_motion_complete,
                        stop_channel_animations,
                        stop_channel_animations_for_paths,
                    )
                    from .tbs_lam_sequence_engine import _collect_prim_paths_for_reset
                    from .sim_parallel_rails import parallel_moves_enabled

                    rail = str(getattr(self, "_sim_rail", "") or "").strip().lower()
                    scoped = bool(
                        parallel_moves_enabled() and rail in ("oht", "move")
                    )
                    peer_now = self._peer_rail_busy()
                    if scoped or peer_now:
                        # 병렬 레일: 이번 JSON prim 만 정리 (채널 전체 stop/drain 금지)
                        stop_channel_animations_for_paths(
                            self._last_usd_context_name,
                            _collect_prim_paths_for_reset(list(self._lam_last_steps or [])),
                            diag_reason="lam_run_end_peer_preserve",
                        )
                    else:
                        stop_channel_animations(
                            self._last_usd_context_name,
                            diag_reason="lam_run_end",
                        )
                        drain_channel_motion_complete(
                            self._last_usd_context_name,
                            self._tbs_registry,
                            max_sec=4.0,
                            stable_ticks=2,
                        )
                except Exception:
                    pass
                if callable(cb):
                    try:
                        cb()
                    except Exception:
                        pass

        self._lam_thread = threading.Thread(target=_bg, name="tbs_lam_sequence_run", daemon=True)
        self._lam_thread.start()
        if wait_until_done:
            try:
                self._lam_thread.join()
            except Exception:
                pass

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
