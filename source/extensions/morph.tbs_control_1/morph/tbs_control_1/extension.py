# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
TBS Control 1 확장 — 기능별 모듈 분리 버전.

extension.py 역할:
- 기본 UI 구성: USD Load 창, TBS 제어창, 시퀀스 편집기 창을 각 모듈에서 빌드하고 연결.
- 확장 수명 주기: on_startup에서 상태 초기화 후 빌드/구독, on_shutdown에서 정리.

--------------
import 구조
--------------
- load_window: build_load_window, get_load_path, on_load_usd, on_resource_combo_changed
  → USD Load 창 UI 및 로드 로직. usd_loader_utils(경로/리소스 목록) 사용.
- control_window: build_control_window, refresh_object_list, on_*(USD/XML/시그널/버튼)
  → TBS 제어창 UI 및 prim 목록/애니메이션 버튼. prim_utils, prim_info, signal_parser, xml_generator,
    translate/curve/rotate_animation, usd_animation_control, selection_overlay.show_prim_info_in_viewport 사용.
- selection_overlay: try_attach_overlay, on_selection_changed, on_post_update, on_close_info_panel,
  add_selection_to_open_paths, apply_selection, show_prim_info_in_viewport, post_update_once
  → 뷰포트 선택과 3D 정보 패널 연동. viewport_overlay.PrimInfoOverlay 사용.
- sequence_editor: SequenceEditorWindow
  → 별도 "TBS 시퀀스 편집기" 창. sequence_engine.SequenceRunner 사용.
- 애니메이션 정지용: translate_animation.stop_prim_translate_animation, curve_animation.stop_*,
  rotate_animation.stop_*, usd_animation_control.stop_usd_animation
  → on_shutdown에서 모든 트랙 애니메이션 중지.
"""

from typing import List, Optional

import omni.ext
import omni.kit.app as app
import omni.ui as ui
import omni.usd as ou
from carb.eventdispatcher import get_eventdispatcher

from .control_window import build_control_window, refresh_object_list
from .curve_animation import stop_prim_curve_animation
from .load_window import build_load_window
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


class Extension(omni.ext.IExt):
    def on_startup(self, ext_id: str) -> None:
        self._ext_id = ext_id
        self._tracked_paths: List[str] = []
        self._open_paths: List[str] = []
        self._overlay: Optional[PrimInfoOverlay] = None
        self._overlay_retry_count = 0
        self._selection_sub = None
        self._stage_stream_sub = None
        self._post_update_sub = None
        self._last_paths: tuple = ()
        self._ignore_selection_until = 0.0
        self._poll_frame = 0
        self._load_window = None
        self._control_window = None
        self._object_list_frame = None
        self._sequence_window = None

        build_load_window(self)
        build_control_window(self)
        self._sequence_window = SequenceEditorWindow()
        try_attach_overlay(self)

        ctx = ou.get_context()
        ed = get_eventdispatcher()
        try:
            event_name = ctx.stage_event_name(ou.StageEventType.SELECTION_CHANGED)
            self._selection_sub = ed.observe_event(
                observer_name="morph.tbs_control_1:SelectionChanged",
                event_name=event_name,
                on_event=lambda e: on_selection_changed(self, e),
            )
        except Exception:
            pass
        try:
            self._stage_stream_sub = ctx.get_stage_event_stream().create_subscription_to_pop(
                lambda e: on_selection_changed(self, e),
                name="morph.tbs_control_1:StageEvents",
            )
        except Exception:
            pass
        try:
            self._post_update_sub = app.get_app().get_post_update_event_stream().create_subscription_to_pop(
                lambda e: on_post_update(self, e),
                name="morph.tbs_control_1:PostUpdate",
            )
        except Exception:
            pass

    def on_shutdown(self) -> None:
        if self._selection_sub is not None and hasattr(self._selection_sub, "release"):
            self._selection_sub.release()
            self._selection_sub = None
        if self._stage_stream_sub is not None:
            try:
                self._stage_stream_sub.unsubscribe()
            except Exception:
                pass
            self._stage_stream_sub = None
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
        if self._load_window is not None:
            self._load_window.destroy()
            self._load_window = None
        if self._control_window is not None:
            self._control_window.destroy()
            self._control_window = None
        self._object_list_frame = None
        if self._sequence_window is not None:
            try:
                self._sequence_window.destroy()
            except Exception:
                pass
            self._sequence_window = None
