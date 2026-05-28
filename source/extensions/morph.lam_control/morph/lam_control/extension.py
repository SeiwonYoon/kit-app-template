# SPDX-FileCopyrightText: Copyright (c) 2026 Morph. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""LAM Control 확장 진입점 (Phase 0).

이 모듈이 하는 일은 다음 4가지뿐이다.
  1. 메인 창(`LamWindow`) 1개를 열고
  2. L3 Instance Registry / L4 Playback Scheduler / L5 Runtime Evaluator 의
     싱글턴 인스턴스 1개씩을 생성하여
  3. 메인 창과 시퀀스 편집기/외부 이벤트 러너에게 주입하고
  4. 종료 시 위 모두를 안전하게 종료한다.

웹 HTTP 브리지는 ``morph.lam_web_bridge`` 확장이 담당한다 (``remote_api`` 세션).

본 모듈은 어떤 경우에도 `morph.tbs_control_1.*` 를 import 하지 않는다.
(USD_Timeline_Spec.md REQ-002 0줄 변경 원칙, §12 절대 보호 영역)
"""

from __future__ import annotations

import omni.ext

from .lam_instance_registry import AnimationInstanceRegistry
from .lam_playback_scheduler import PlaybackScheduler
from .lam_runtime_evaluator import RuntimeEvaluator
from .lam_window import LamWindow
from .remote_api import LamKitSession, clear_session, set_session

_PRINT_PREFIX = "[LAM]"


class LamControlExtension(omni.ext.IExt):
    """LAM Control 확장 라이프사이클."""

    def __init__(self) -> None:
        super().__init__()
        self._registry: AnimationInstanceRegistry | None = None
        self._scheduler: PlaybackScheduler | None = None
        self._evaluator: RuntimeEvaluator | None = None
        self._window: LamWindow | None = None

    def on_startup(self, ext_id: str) -> None:  # noqa: D401
        print(f"{_PRINT_PREFIX} on_startup ext_id={ext_id}", flush=True)
        try:
            import carb  # type: ignore
            import importlib

            sp = importlib.import_module("morph.lam_control.simulation_play")
            st = importlib.import_module("morph.lam_control.lam_viewport_overlay_state")
            carb.log_warn(f"[LAM] morph.lam_control loaded from: {__file__}")
            carb.log_warn(f"[LAM] simulation_play from: {getattr(sp, '__file__', '?')}")
            carb.log_warn(f"[LAM] overlay_state from: {getattr(st, '__file__', '?')}")
        except Exception:
            pass

        self._registry = AnimationInstanceRegistry()
        self._evaluator = RuntimeEvaluator(registry=self._registry)
        self._scheduler = PlaybackScheduler(registry=self._registry, evaluator=self._evaluator)

        self._window = LamWindow(
            registry=self._registry,
            scheduler=self._scheduler,
            evaluator=self._evaluator,
            ext_id=ext_id,
        )
        self._window.show()
        self._evaluator.start()

        assert self._registry is not None and self._scheduler is not None
        assert self._window is not None
        set_session(
            LamKitSession(
                registry=self._registry,
                scheduler=self._scheduler,
                open_master_at_path=self._window._open_master_at_path,
            )
        )

    def on_shutdown(self) -> None:
        print(f"{_PRINT_PREFIX} on_shutdown", flush=True)
        clear_session()
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} window.destroy failed: {exc}", flush=True)
        self._window = None
        try:
            if self._evaluator is not None:
                self._evaluator.stop()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} evaluator.stop() failed: {exc}", flush=True)
        self._scheduler = None
        self._evaluator = None
        self._registry = None
