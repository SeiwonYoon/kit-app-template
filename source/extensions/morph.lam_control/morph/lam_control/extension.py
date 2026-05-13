# SPDX-FileCopyrightText: Copyright (c) 2026 Morph. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""LAM Control 확장 진입점 (Phase 0).

이 모듈이 하는 일은 다음 4가지뿐이다.
  1. 메인 창(`LamWindow`) 1개를 열고
  2. L3 Instance Registry / L4 Playback Scheduler / L5 Runtime Evaluator 의
     싱글턴 인스턴스 1개씩을 생성하여
  3. 메인 창과 시퀀스 편집기/외부 이벤트 러너에게 주입하고
  4. 종료 시 위 모두를 안전하게 종료한다.

본 모듈은 어떤 경우에도 `morph.tbs_control_1.*` 를 import 하지 않는다.
(USD_Timeline_Spec.md REQ-002 0줄 변경 원칙, §12 절대 보호 영역)
"""

from __future__ import annotations

import omni.ext

from .lam_instance_registry import AnimationInstanceRegistry
from .lam_playback_scheduler import PlaybackScheduler
from .lam_runtime_evaluator import RuntimeEvaluator
from .lam_window import LamWindow


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

        self._registry = AnimationInstanceRegistry()
        self._evaluator = RuntimeEvaluator(registry=self._registry)
        self._scheduler = PlaybackScheduler(registry=self._registry, evaluator=self._evaluator)

        self._window = LamWindow(
            registry=self._registry,
            scheduler=self._scheduler,
            evaluator=self._evaluator,
        )
        self._window.show()
        self._evaluator.start()

    def on_shutdown(self) -> None:
        print(f"{_PRINT_PREFIX} on_shutdown", flush=True)
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
