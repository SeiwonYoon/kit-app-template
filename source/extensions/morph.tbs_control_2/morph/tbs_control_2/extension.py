# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
TBS Control 2 확장 — 기능별 모듈 분리 버전 (진입점)

【extension.py 역할】
- Omni 확장 IExt: on_startup / on_shutdown.
- 창 조립: control_window.build_control_window(상단에 USD Load 포함), SequenceEditorWindow.
- 선택 이벤트·스테이지 스트림 구독 (selection_overlay), 뷰포트 오버레이 재시도.
- 종료 시 모든 애니메이션·타임라인 정지.

【기능을 바꾸려면 어디를 보나】
- 확장 의존성/표시 이름: 상위 폴더 extension.toml (이 모듈과 별개).
- USD 로드: tbs_usd_window.py (SSOT) / tbs_ep_port_visibility.py
- TBS 제어창(타임라인·XML·버튼): control_window.py (+ 필요 시 xml_generator.py 등)
- 시퀀스 스텝 편집/실행: sequence_editor.py + sequence_engine.py
- 뷰포트 3D 정보 패널: selection_overlay.py, viewport_overlay.py
- xform 경고 억제: xform_utils.install_xform_op_order_warning_filter (startup에서 호출)
- 기본 메뉴 숨김 런치 여부: kit_chrome_visibility.KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH (한 곳만 수정)

--------------
import 구조 (요약)
--------------
- tbs_usd_window → Master USD Load
- control_window → TBS 제어창
- selection_overlay → 선택·오버레이
- sequence_editor → 시퀀스 편집기
- on_shutdown → translate/curve/rotate/usd_animation 정지

--------------
유지보수 시나리오
--------------
1) "새 이벤트 타입(EAPEIS_PORT_XXX) 추가"
   - xml_generator.py: SEQ_ 상수/빌더/파서 추가
   - control_window.py: XML 콤보/입력 분기 + SIM_SEQ_ALIAS + rules/map 매핑 확인
   - simulation_engine.py: _emit_event(seq=...) 호출 지점 추가
2) "시뮬레이션 공정 로직 변경"
   - simulation_engine.py: 단계 함수/선택 정책(_find_*) 수정
   - control_window.py: UI 입력 항목 전달(on_sim_start_clicked)과 로그 표기 동기화
3) "이벤트별 JSON 애니메이션 연결 변경"
   - control_window.EVENT_JSON_CASE_MAP(최우선) 또는 config/event_animation_rules.json / event_animation_map.json
   - data/sim_sequences/*.json 파일명은 포트 ID(INOUT, BP1~4, EP1~3)와 이벤트 키와 일치시킴
4) "종료/정리 누락 이슈"
   - 본 파일 on_shutdown에서 스레드/구독/애니메이션 정리 순서 확인
"""

import asyncio
import os
from typing import Any, List, Optional

import omni.ext
import omni.kit.app as app
import omni.ui as ui
import omni.usd as ou
from carb.eventdispatcher import get_eventdispatcher

from .control_window import build_control_window, on_sim_stop_clicked, refresh_object_list
from .tbs_instance_registry import AnimationInstanceRegistry
from .tbs_playback_scheduler import PlaybackScheduler
from .tbs_runtime_evaluator import RuntimeEvaluator
from .tbs_usd_window import TbsUsdWindow
from .sim_multi_view import detach_stage_visibility_subscription, teardown_sim_multi_viewports
from .kit_chrome_visibility import (
    KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH,
    apply_kit_chrome_hidden,
    apply_viewport_dock_tab_bars_hidden,
    is_kit_chrome_hidden,
    is_streaming_deployment,
)
from .hyview_stream import (
    apply_streaming_livestream_settings,
    enable_hyview_stream_layout_lock,
    install_streaming_window_resize_hooks,
    teardown_streaming_window_hooks,
)
from .curve_animation import stop_prim_curve_animation
from .rotate_animation import stop_prim_rotate_animation
from .selection_overlay import (
    on_selection_changed,
    on_post_update,
    try_attach_overlay,
)
from .sequence_editor import SequenceEditorWindow
from .translate_animation import stop_prim_translate_animation
from . import usd_animation_control
from .viewport_overlay import PrimInfoOverlay
from .xform_utils import install_xform_op_order_warning_filter
from .kit_main_dispatch import ensure_kit_main_dispatch, shutdown_kit_main_dispatch
from .tbs_extension_singleton import clear_tbs_extension_instance, set_tbs_extension_instance

_PRINT_PREFIX = "[TBS]"


def _start_with_dual_screen_enabled() -> bool:
    """앱 시작 시 2분할로 시작할지 (``sim_control_defaults.START_WITH_DUAL_SCREEN``)."""
    try:
        from .sim_control_defaults import START_WITH_DUAL_SCREEN

        return bool(START_WITH_DUAL_SCREEN)
    except Exception:
        return False


def _maybe_start_with_dual_screen(ext: Any) -> None:
    """
    (레거시) Master USD 로드 후 2분할 — ``START_WITH_DUAL_SCREEN`` 이 layout-first 가 아닐 때만.

    layout-first 경로는 ``_schedule_startup_dual_layout_first`` 를 사용한다.
    """
    if bool(getattr(ext, "_tbs_defer_master_autoload_until_dual_layout", False)):
        return
    if bool(getattr(ext, "_tbs_auto_dual_layout_done", False)):
        return
    if not _start_with_dual_screen_enabled():
        return
    try:
        ext._tbs_auto_dual_layout_done = True
    except Exception:
        pass

    async def _go() -> None:
        kit_app = app.get_app()
        try:
            for _ in range(30):
                await kit_app.next_update_async()
        except Exception:
            pass
        try:
            from . import sim_multi_view

            sim_multi_view.apply_sim_viewport_split_layout(ext, 2)
            print(f"{_PRINT_PREFIX} START_WITH_DUAL_SCREEN: 2분할 자동 적용", flush=True)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} START_WITH_DUAL_SCREEN apply failed: {exc}", flush=True)
            return
        try:
            for _ in range(60):
                await kit_app.next_update_async()
        except Exception:
            pass
        try:
            from . import sim_multi_view
            from .kit_chrome_visibility import is_kit_chrome_hidden

            sim_multi_view.schedule_split_layout_refresh_for_chrome_change(
                ext, bool(is_kit_chrome_hidden(ext))
            )
            print(f"{_PRINT_PREFIX} START_WITH_DUAL_SCREEN: 레이아웃 균등 재배치 요청", flush=True)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} START_WITH_DUAL_SCREEN relayout failed: {exc}", flush=True)

    try:
        asyncio.ensure_future(_go())
    except Exception:
        try:
            from . import sim_multi_view

            sim_multi_view.apply_sim_viewport_split_layout(ext, 2)
        except Exception:
            pass


def _trigger_master_autoload_after_dual_layout(ext: Any) -> None:
    """layout-first: 2분할 Dock 완료 후 화면1 Master USD 자동 로드."""
    try:
        ext._tbs_defer_master_autoload_until_dual_layout = False
    except Exception:
        pass
    try:
        print(f"{_PRINT_PREFIX} START_WITH_DUAL_SCREEN: 화면1 Master USD 로드 시작", flush=True)
    except Exception:
        pass
    win = getattr(ext, "_tbs_usd_window", None)
    if win is not None and hasattr(win, "run_master_autoload_now"):
        try:
            win.run_master_autoload_now()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} master autoload after layout failed: {exc}", flush=True)


def _schedule_startup_dual_layout_first(ext: Any) -> None:
    """
    앱 시작: USD 로드 **전에** 최종 2분할 Dock 레이아웃을 먼저 만든다.

    레이아웃 완료 콜백에서 화면1 Master → Master 열림 콜백에서 화면2 USD 순으로 로드.
    """
    if not _start_with_dual_screen_enabled():
        return
    if bool(getattr(ext, "_tbs_auto_dual_layout_done", False)):
        return
    try:
        ext._tbs_auto_dual_layout_done = True
        ext._tbs_defer_master_autoload_until_dual_layout = True
        ext._tbs_startup_layout_first_active = True
    except Exception:
        pass

    async def _go() -> None:
        kit_app = app.get_app()
        try:
            for _ in range(8):
                await kit_app.next_update_async()
        except Exception:
            pass
        try:
            from . import sim_multi_view

            ext._tbs_on_dual_layout_ready_fn = (
                lambda e=ext: _trigger_master_autoload_after_dual_layout(e)
            )
            sim_multi_view.apply_startup_dual_layout_first(ext, 2)
            print(f"{_PRINT_PREFIX} START_WITH_DUAL_SCREEN: 2분할 레이아웃 선적용 요청", flush=True)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} START_WITH_DUAL_SCREEN layout-first failed: {exc}", flush=True)

    try:
        asyncio.ensure_future(_go())
    except Exception:
        pass


def _log_extension_load_paths(ext_id: str) -> None:
    """hot-reload 진단: 실제 import 경로와 확장 루트를 콘솔에 남긴다."""
    try:
        import importlib

        import carb  # type: ignore

        usd = importlib.import_module("morph.tbs_control_2.tbs_usd_window")
        ctrl = importlib.import_module("morph.tbs_control_2.control_window")
        em = app.get_app().get_extension_manager()
        ext_path = em.get_extension_path(ext_id) if em else None
        carb.log_warn(f"{_PRINT_PREFIX} morph.tbs_control_2 loaded from: {__file__}")
        carb.log_warn(f"{_PRINT_PREFIX} extension id={ext_id} kit_path={ext_path}")
        carb.log_warn(f"{_PRINT_PREFIX} tbs_usd_window from: {getattr(usd, '__file__', '?')}")
        carb.log_warn(f"{_PRINT_PREFIX} control_window from: {getattr(ctrl, '__file__', '?')}")
    except Exception as exc:
        print(f"{_PRINT_PREFIX} load-path diagnostic failed: {exc}", flush=True)


async def _deferred_apply_streaming_viewport_polish(ext: Any) -> None:
    """스트리밍 Kit: 뷰포트 fill_frame + Dock 탭 바 숨김 (초기 레이아웃 안정 후 1회)."""
    if not is_streaming_deployment():
        return
    kit_app = app.get_app()
    for _ in range(48):
        await kit_app.next_update_async()
    try:
        from . import sim_multi_view

        for _ in range(360):
            if not sim_multi_view.startup_dual_orchestration_active(ext):
                break
            await kit_app.next_update_async()
    except Exception:
        pass
    try:
        apply_viewport_dock_tab_bars_hidden()
        from . import sim_multi_view as smv

        sn = int(getattr(ext, "_sim_viewport_split_count", 1) or 1)
        smv.set_viewport_fill_frame_for_split_count(sn, True)
        smv.schedule_split_layout_refresh_for_chrome_change(ext, True)
        smv.apply_viewport_split_tab_chrome(sn)
        apply_viewport_dock_tab_bars_hidden()
        from .hyview_stream import enable_hyview_stream_layout_lock

        enable_hyview_stream_layout_lock(ext)
    except Exception:
        pass


async def _deferred_apply_kit_chrome_hide(ext: Any) -> None:
    """메인 메뉴 등이 준비된 뒤 기본 숨김 적용 (KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH 가 True 일 때만)."""
    if not KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH:
        return
    kit_app = app.get_app()
    for _ in range(5):
        await kit_app.next_update_async()
    try:
        from . import sim_multi_view

        for _ in range(360):
            if not sim_multi_view.startup_dual_orchestration_active(ext):
                break
            await kit_app.next_update_async()
    except Exception:
        pass
    if bool(getattr(ext, "_tbs_startup_dual_orchestration_active", False)):
        return
    try:
        from .kit_chrome_visibility import is_kit_chrome_hidden

        if not is_kit_chrome_hidden(ext):
            apply_kit_chrome_hidden(ext, True)
    except Exception:
        pass


def _apply_stage_default_fps_30() -> None:
    """
    런치/스테이지 로드 시 기본 FPS(TPS)를 30으로 맞춘다.

    - USD 기준: stage timeCodesPerSecond / framesPerSecond
    - 타임라인/프레임↔시간 변환(usm_animation_control 등)은 tl.get_time_codes_per_seconds()를 참조하므로,
      스테이지/타임라인 쪽의 기본 TPS가 30이면 자동으로 30 기준으로 재생 시간이 계산된다.
    """
    try:
        ctx = ou.get_context()
        stage = ctx.get_stage() if ctx else None
        if stage is None:
            return
        try:
            stage.SetTimeCodesPerSecond(30.0)
        except Exception:
            pass
        try:
            stage.SetFramesPerSecond(30.0)
        except Exception:
            pass
    except Exception:
        pass


async def _deferred_apply_stage_default_fps_30() -> None:
    """초기화 직후/스테이지 로드 타이밍을 고려해 몇 프레임 뒤 한 번 더 적용."""
    kit_app = app.get_app()
    for _ in range(5):
        await kit_app.next_update_async()
    _apply_stage_default_fps_30()


class Extension(omni.ext.IExt):
    """Omni 확장 진입점: 창 생성·선택/스테이지 구독·종료 시 애니/타임라인 정리."""

    def on_startup(self, ext_id: str) -> None:
        """확장 로드 시: xform 경고 필터, TBS 제어창(USD Load 포함)/시퀀스 창, 오버레이, 이벤트 구독."""
        print(f"{_PRINT_PREFIX} on_startup ext_id={ext_id}", flush=True)
        set_tbs_extension_instance(self)
        ensure_kit_main_dispatch()
        _log_extension_load_paths(ext_id)
        install_xform_op_order_warning_filter()
        self._ext_id = ext_id
        self._tracked_paths: List[str] = []
        self._open_paths: List[str] = []
        self._overlay: Optional[PrimInfoOverlay] = None
        self._overlay_retry_count = 0
        self._selection_sub = None
        self._stage_stream_sub = None
        self._fps_stage_sub = None
        self._post_update_sub = None
        self._last_paths: tuple = ()
        self._ignore_selection_until = 0.0
        self._poll_frame = 0
        self._control_window = None
        self._object_list_frame = None
        self._sequence_window = None
        self._tbs_registry = None
        self._tbs_scheduler = None
        self._tbs_evaluator = None
        self._tbs_usd_window = None
        self._kit_chrome_startup_task = None
        self._streaming_viewport_task = None

        # 이전 비정상 종료 등으로 남은 보조 ViewportWindow / 보조 USD 컨텍스트 정리
        try:
            teardown_sim_multi_viewports(self, skip_deferred_restore=True)
        except Exception:
            pass

        # LAM-style USD / sequence infrastructure (TBS 독립 복사)
        self._tbs_registry = AnimationInstanceRegistry()
        self._tbs_evaluator = RuntimeEvaluator(registry=self._tbs_registry)
        self._tbs_scheduler = PlaybackScheduler(registry=self._tbs_registry, evaluator=self._tbs_evaluator)

        build_control_window(self)
        try:
            from .tbs_viewport_control_hud import attach_tbs_viewport_control_hud

            attach_tbs_viewport_control_hud(self)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} viewport control HUD attach failed: {exc}", flush=True)
        from .ebs_control_panel_ui import get_sim_ep_count_idx
        from .tbs_ep_port_visibility import (
            ep_count_from_combo_idx,
            schedule_apply_ep_port_layout,
        )

        def _on_master_opened_for_ep() -> None:
            master_path = ""
            try:
                win = getattr(self, "_tbs_usd_window", None)
                if win is not None:
                    master = getattr(win, "_master", None)
                    if master is not None:
                        master_path = str(getattr(master, "master_path", "") or "")
            except Exception:
                pass
            try:
                from .control_window import notify_tbs_composed_usd_ready_for_split

                notify_tbs_composed_usd_ready_for_split(self, master_path)
            except Exception:
                pass
            try:
                idx = int(get_sim_ep_count_idx(self))
            except Exception:
                idx = 0
            schedule_apply_ep_port_layout(
                self,
                ep_count_from_combo_idx(idx),
                delay_frames=12,
                reason="master_opened",
            )
            try:
                hud = getattr(self, "_tbs_viewport_control_hud", None)
                if hud is not None and hasattr(hud, "sync_layers"):
                    from .sim_multi_view import startup_dual_orchestration_active

                    if not startup_dual_orchestration_active(self):
                        hud.sync_layers(delay_frames=12)
            except Exception:
                pass

        if _start_with_dual_screen_enabled():
            try:
                self._tbs_defer_master_autoload_until_dual_layout = True
            except Exception:
                pass

        self._tbs_usd_window = TbsUsdWindow(
            self._tbs_registry,
            self._tbs_scheduler,
            self._tbs_evaluator,
            ext_id=ext_id,
            kit_ext=self,
        )
        self._tbs_usd_window.set_master_open_listener(_on_master_opened_for_ep)
        self._tbs_usd_window.show()
        if _start_with_dual_screen_enabled():
            try:
                _schedule_startup_dual_layout_first(self)
            except Exception as exc:
                print(f"{_PRINT_PREFIX} layout-first dual-screen start failed: {exc}", flush=True)
        try:
            self._tbs_evaluator.start()
        except Exception:
            pass
        self._sequence_window = SequenceEditorWindow(
            self._tbs_registry,
            self._tbs_scheduler,
            evaluator=self._tbs_evaluator,
        )
        self._sequence_window.show()
        try:
            from .ebs_control_panel_ui import sync_aux_kit_window_visibility

            sync_aux_kit_window_visibility(self)
        except Exception:
            pass

        if KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH:
            self._kit_chrome_startup_task = asyncio.ensure_future(_deferred_apply_kit_chrome_hide(self))

        if is_streaming_deployment():
            apply_streaming_livestream_settings()
            install_streaming_window_resize_hooks(self)
            self._streaming_viewport_task = asyncio.ensure_future(
                _deferred_apply_streaming_viewport_polish(self)
            )

        # -------------------------------------------------------------------
        # 타임라인 기본 FPS(TPS) = 30 강제
        # -------------------------------------------------------------------
        # - 확장 실행 직후, 또는 open_stage()로 스테이지가 교체될 때 24로 돌아가는 것을 방지한다.
        # - 스테이지 이벤트 스트림에 붙어, 스테이지가 열릴 때마다 30을 재적용한다.
        try:
            ctx = ou.get_context()
            if ctx is not None:
                self._fps_stage_sub = ctx.get_stage_event_stream().create_subscription_to_pop(
                    lambda _e: _apply_stage_default_fps_30(),
                    name="morph.tbs_control_2:DefaultFPS30",
                )
        except Exception:
            self._fps_stage_sub = None
        _apply_stage_default_fps_30()
        asyncio.ensure_future(_deferred_apply_stage_default_fps_30())

        # --- 뷰포트 객체 클릭 시 3D 정보 패널(PrimInfoOverlay) 비활성화 ---
        # 다시 쓰려면 아래 try_attach_overlay + 세 구독 블록의 주석을 해제하세요.
        # (제어창의「3D 정보 보기」버튼은 control_window → show_prim_info_in_viewport 경로로
        #  여전히 패널을 띄울 수 있음. 그 버튼까지 끄려면 해당 버튼도 주석 처리 필요.)
        # try_attach_overlay(self)
        #
        # ctx = ou.get_context()
        # ed = get_eventdispatcher()
        # try:
        #     event_name = ctx.stage_event_name(ou.StageEventType.SELECTION_CHANGED)
        #     self._selection_sub = ed.observe_event(
        #         observer_name="morph.tbs_control_2:SelectionChanged",
        #         event_name=event_name,
        #         on_event=lambda e: on_selection_changed(self, e),
        #     )
        # except Exception:
        #     pass
        # try:
        #     self._stage_stream_sub = ctx.get_stage_event_stream().create_subscription_to_pop(
        #         lambda e: on_selection_changed(self, e),
        #         name="morph.tbs_control_2:StageEvents",
        #     )
        # except Exception:
        #     pass
        # try:
        #     self._post_update_sub = app.get_app().get_post_update_event_stream().create_subscription_to_pop(
        #         lambda e: on_post_update(self, e),
        #         name="morph.tbs_control_2:PostUpdate",
        #     )
        # except Exception:
        #     pass

    def on_shutdown(self) -> None:
        """확장 언로드 시: 시뮬 정지, 구독 해제, translate/curve/rotate/usd 애니 정지, 창 destroy."""
        print(f"{_PRINT_PREFIX} on_shutdown", flush=True)
        t = getattr(self, "_kit_chrome_startup_task", None)
        if t is not None and not t.done():
            try:
                t.cancel()
            except Exception:
                pass
            self._kit_chrome_startup_task = None
        t = getattr(self, "_streaming_viewport_task", None)
        if t is not None and not t.done():
            try:
                t.cancel()
            except Exception:
                pass
            self._streaming_viewport_task = None
        try:
            teardown_streaming_window_hooks(self)
        except Exception:
            pass
        try:
            if is_kit_chrome_hidden(self):
                apply_kit_chrome_hidden(self, False)
        except Exception:
            pass
        try:
            on_sim_stop_clicked(self)
        except Exception:
            pass
        if self._selection_sub is not None and hasattr(self._selection_sub, "release"):
            self._selection_sub.release()
            self._selection_sub = None
        if self._stage_stream_sub is not None:
            try:
                self._stage_stream_sub.unsubscribe()
            except Exception:
                pass
            self._stage_stream_sub = None
        if self._fps_stage_sub is not None:
            try:
                self._fps_stage_sub.unsubscribe()
            except Exception:
                pass
            self._fps_stage_sub = None
        if self._post_update_sub is not None:
            try:
                self._post_update_sub.unsubscribe()
            except Exception:
                pass
            self._post_update_sub = None
        for path in list(self._tracked_paths):
            stop_prim_translate_animation(path)
            stop_prim_curve_animation(path)
            stop_prim_rotate_animation(path)
        self._tracked_paths.clear()
        self._open_paths.clear()
        if self._overlay:
            self._overlay.destroy()
            self._overlay = None
        usd_animation_control.stop_usd_animation()
        try:
            from .tbs_ep_port_visibility import teardown_ep_port_visibility

            teardown_ep_port_visibility(self)
        except Exception:
            pass
        try:
            teardown_sim_multi_viewports(self, skip_deferred_restore=True)
        except Exception:
            pass
        try:
            self._tbs_auto_dual_layout_done = False
        except Exception:
            pass
        try:
            detach_stage_visibility_subscription(self)
        except Exception:
            pass
        try:
            from .tbs_viewport_control_hud import destroy_tbs_viewport_control_hud

            destroy_tbs_viewport_control_hud(self)
        except Exception:
            pass
        if self._control_window is not None:
            self._control_window.destroy()
            self._control_window = None
        try:
            from .control_window import _iter_sim_monitor_windows, _iter_sim_timetable_windows

            for mon in list(_iter_sim_monitor_windows(self)):
                if mon is not None:
                    try:
                        mon.destroy()
                    except Exception:
                        pass
            self._sim_monitor_window = None
            self._sim_monitor_windows_by_screen = {}
        except Exception:
            pass
        try:
            fp = getattr(self, "_fix_proc_window", None)
            if fp is not None:
                fp.destroy()
                self._fix_proc_window = None
        except Exception:
            pass
        try:
            from .control_window import _iter_sim_timetable_windows

            for tt in list(_iter_sim_timetable_windows(self)):
                if tt is not None:
                    try:
                        tt.destroy()
                    except Exception:
                        pass
            self._sim_timetable_window = None
            self._sim_timetable_windows_by_screen = {}
        except Exception:
            pass
        self._object_list_frame = None
        if self._sequence_window is not None:
            try:
                self._sequence_window.destroy()
            except Exception:
                pass
            self._sequence_window = None
        if self._tbs_usd_window is not None:
            try:
                self._tbs_usd_window.destroy()
            except Exception:
                pass
            self._tbs_usd_window = None
        if self._tbs_evaluator is not None:
            try:
                self._tbs_evaluator.stop()
            except Exception:
                pass
            self._tbs_evaluator = None
        self._tbs_scheduler = None
        self._tbs_registry = None
        clear_tbs_extension_instance()
        shutdown_kit_main_dispatch()
