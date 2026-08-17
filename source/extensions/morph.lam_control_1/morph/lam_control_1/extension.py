# SPDX-FileCopyrightText: Copyright (c) 2026 Morph. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""LAM Control 확장 진입점 — 듀얼 viewport 분할 (TBS extension 패턴 포팅)."""

from __future__ import annotations

import asyncio
from typing import Any

import omni.ext

from .lam_extension_singleton import clear_lam_extension_instance, set_lam_extension_instance
from .kit_main_dispatch import ensure_kit_main_dispatch, shutdown_kit_main_dispatch
from .lam_instance_registry import AnimationInstanceRegistry
from .lam_multi_viewport import detach_stage_visibility_subscription, teardown_lam_multi_viewports
from .lam_playback_scheduler import PlaybackScheduler
from .lam_runtime_evaluator import RuntimeEvaluator
from .lam_viewport_split_notify import notify_lam_composed_usd_ready_for_split
from .lam_window import LamWindow
from .remote_api import LamKitSession, clear_session, set_session

_PRINT_PREFIX = "[LAM]"


def _start_with_dual_screen_enabled() -> bool:
    """화면 표시 개수와 무관하게 화면1·2 런타임은 항상 준비."""
    try:
        from .lam_sim_control_defaults import default_viewport_split_count

        return int(default_viewport_split_count()) >= 2
    except Exception:
        return True


def _trigger_master_autoload_after_dual_layout(ext: Any) -> None:
    """layout-first: 2분할 완료 후 화면1 Master USD 자동 로드."""
    try:
        ext._lam_defer_master_autoload_until_dual_layout = False
    except Exception:
        pass
    try:
        print(f"{_PRINT_PREFIX} START_WITH_DUAL_SCREEN: 화면1 Master USD 로드 시작", flush=True)
    except Exception:
        pass
    win = getattr(ext, "_lam_window", None)
    if win is not None and hasattr(win, "run_master_autoload_now"):
        try:
            win.run_master_autoload_now()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} master autoload after layout failed: {exc}", flush=True)


def _schedule_startup_dual_layout_first(ext: Any) -> None:
    """앱 시작: USD 로드 전에 2분할 레이아웃을 먼저 만든다."""
    if not _start_with_dual_screen_enabled():
        return
    if bool(getattr(ext, "_lam_auto_dual_layout_done", False)):
        return
    try:
        ext._lam_auto_dual_layout_done = True
        ext._lam_defer_master_autoload_until_dual_layout = True
        ext._lam_startup_layout_first_active = True
    except Exception:
        pass

    async def _go() -> None:
        try:
            import omni.kit.app as kit_app

            for _ in range(8):
                await kit_app.get_app().next_update_async()
        except Exception:
            pass
        try:
            from . import lam_multi_viewport
            from .lam_sim_control_defaults import default_viewport_split_count

            ext._lam_on_dual_layout_ready_fn = (
                lambda e=ext: _trigger_master_autoload_after_dual_layout(e)
            )
            sn = int(default_viewport_split_count())
            lam_multi_viewport.apply_startup_dual_layout_first(ext, sn)
            print(
                f"{_PRINT_PREFIX} START_WITH_DUAL_SCREEN: {sn}분할 레이아웃 선적용 요청",
                flush=True,
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} START_WITH_DUAL_SCREEN layout-first failed: {exc}", flush=True)

    try:
        asyncio.ensure_future(_go())
    except Exception:
        pass


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
        set_lam_extension_instance(self)
        ensure_kit_main_dispatch()
        try:
            import carb  # type: ignore
            import importlib

            sp = importlib.import_module("morph.lam_control_1.simulation_play")
            st = importlib.import_module("morph.lam_control_1.lam_viewport_overlay_state")
            carb.log_warn(f"[LAM] morph.lam_control_1 loaded from: {__file__}")
            carb.log_warn(f"[LAM] simulation_play from: {getattr(sp, '__file__', '?')}")
            carb.log_warn(f"[LAM] overlay_state from: {getattr(st, '__file__', '?')}")
        except Exception:
            pass

        try:
            import carb.settings  # type: ignore

            settings = carb.settings.get_settings()
            settings.set("/rtx/denoiser/enabled", False)
            settings.set("/rtx/sampling/maxSamples", 1024)
            settings.set("/rtx/sampling/maxAccumulatedFrames", 1)
        except Exception:
            pass

        try:
            teardown_lam_multi_viewports(self, skip_deferred_restore=True)
        except Exception:
            pass

        self._registry = AnimationInstanceRegistry()
        self._evaluator = RuntimeEvaluator(registry=self._registry)
        self._scheduler = PlaybackScheduler(registry=self._registry, evaluator=self._evaluator)

        try:
            from .lam_sim_control_defaults import default_viewport_split_count

            self._sim_viewport_split_count = int(default_viewport_split_count())
        except Exception:
            self._sim_viewport_split_count = 1

        self._lam_registry = self._registry
        self._lam_evaluator = self._evaluator
        self._lam_scheduler = self._scheduler

        try:
            from .lam_aux_kit_window_ui import init_lam_aux_kit_window_models

            init_lam_aux_kit_window_models(self)
        except Exception:
            pass
        try:
            from .lam_screen_visibility import init_screen_visibility_models

            init_screen_visibility_models(self)
        except Exception:
            pass

        if _start_with_dual_screen_enabled():
            try:
                self._lam_defer_master_autoload_until_dual_layout = True
            except Exception:
                pass

        def _on_master_opened_for_split() -> None:
            master_path = ""
            try:
                win = getattr(self, "_lam_window", None)
                if win is not None:
                    master = getattr(win, "_master", None)
                    if master is not None:
                        master_path = str(getattr(master, "master_path", "") or "")
            except Exception:
                pass
            try:
                notify_lam_composed_usd_ready_for_split(self, master_path)
            except Exception as exc:
                print(f"{_PRINT_PREFIX} split notify failed: {exc}", flush=True)
            try:
                win = getattr(self, "_lam_window", None)
                if win is not None:
                    win.refresh_csv_sim_play_runtimes()
            except Exception as exc:
                print(f"{_PRINT_PREFIX} csv play runtime refresh failed: {exc}", flush=True)

        self._window = LamWindow(
            registry=self._registry,
            scheduler=self._scheduler,
            evaluator=self._evaluator,
            ext_id=ext_id,
            kit_ext=self,
        )
        self._lam_window = self._window
        self._window.set_master_open_listener(_on_master_opened_for_split)
        self._window.show()
        # federation auto_show / ui_show 창 표시는 USD 로드 직전(_try_autoload_master_on_startup)
        # 에서 설정 기준으로 한 번에 반영한다. 여기서 미리 열지 않는다(True → USD 직전 표시).
        try:
            from .lam_aux_kit_window_ui import sync_aux_kit_window_visibility

            sync_aux_kit_window_visibility(self)
        except Exception:
            pass

        if _start_with_dual_screen_enabled():
            try:
                _schedule_startup_dual_layout_first(self)
            except Exception as exc:
                print(f"{_PRINT_PREFIX} layout-first dual-screen start failed: {exc}", flush=True)

        try:
            self._evaluator.start()
        except Exception:
            pass

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
        try:
            from .lam_traffic_light_emissive import shutdown_traffic_light_emissive

            shutdown_traffic_light_emissive()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} traffic light shutdown: {exc}", flush=True)
        clear_session()
        try:
            detach_stage_visibility_subscription(self)
        except Exception:
            pass
        try:
            teardown_lam_multi_viewports(self, skip_deferred_restore=True)
        except Exception:
            pass
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} window.destroy failed: {exc}", flush=True)
        self._window = None
        self._lam_window = None
        try:
            if self._evaluator is not None:
                self._evaluator.stop()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} evaluator.stop() failed: {exc}", flush=True)
        self._scheduler = None
        self._evaluator = None
        self._registry = None
        self._lam_registry = None
        self._lam_evaluator = None
        self._lam_scheduler = None
        try:
            self._lam_auto_dual_layout_done = False
        except Exception:
            pass
        try:
            shutdown_kit_main_dispatch()
        except Exception:
            pass
        clear_lam_extension_instance()
