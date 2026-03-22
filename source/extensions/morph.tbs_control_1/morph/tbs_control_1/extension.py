# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
TBS Control 1 확장 — 기능별 모듈 분리 버전 (진입점)

【extension.py 역할】
- Omni 확장 IExt: on_startup / on_shutdown.
- 창 조립: load_window.build_load_window, control_window.build_control_window, SequenceEditorWindow.
- 선택 이벤트·스테이지 스트림 구독 (selection_overlay), 뷰포트 오버레이 재시도.
- 종료 시 모든 애니메이션·타임라인 정지.

【기능을 바꾸려면 어디를 보나】
- 확장 의존성/표시 이름: 상위 폴더 extension.toml (이 모듈과 별개).
- USD 로드 창만: load_window.py / usd_loader_utils.py
- TBS 제어창(타임라인·XML·버튼): control_window.py (+ 필요 시 xml_generator.py 등)
- 시퀀스 스텝 편집/실행: sequence_editor.py + sequence_engine.py
- 뷰포트 3D 정보 패널: selection_overlay.py, viewport_overlay.py
- xform 경고 억제: xform_utils.install_xform_op_order_warning_filter (startup에서 호출)

--------------
import 구조 (요약)
--------------
- load_window → USD Load
- control_window → TBS 제어창
- selection_overlay → 선택·오버레이
- sequence_editor → 시퀀스 편집기
- on_shutdown → translate/curve/rotate/usd_animation 정지
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
from .xform_utils import install_xform_op_order_warning_filter


class Extension(omni.ext.IExt):
    def on_startup(self, ext_id: str) -> None:
        install_xform_op_order_warning_filter()
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
