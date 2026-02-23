# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

import omni.ext
import omni.ui as ui
import omni.usd
import carb.eventdispatcher
from omni.kit.widget.stage import StageWidget, DefaultSelectionWatch, StageIcons
from omni.kit.widget.stage.stage_style import Styles as StageStyles

LEFT_LIST_STYLE = {}


# Functions and vars are available to other extensions as usual in python:
# `morph.favorites_search_prim.some_public_function(x)`
def some_public_function(x: int):
    """This is a public function that can be called from other extensions."""
    print(f"[morph.favorites_search_prim] some_public_function was called with {x}")
    return x ** x


# Any class derived from `omni.ext.IExt` in the top level module (defined in
# `python.modules` of `extension.toml`) will be instantiated when the extension
# gets enabled, and `on_startup(ext_id)` will be called. Later when the
# extension gets disabled on_shutdown() is called.
class MyExtension(omni.ext.IExt):
    """Stage UI host extension."""

    def on_startup(self, _ext_id):
        """This is called every time the extension is activated."""
        print("[morph.favorites_search_prim] Extension startup")

        # StageWidget 공용 스타일 초기화(최초 1회)
        if StageStyles.STAGE_WIDGET is None:
            StageStyles.on_startup()

        # USD 컨텍스트/우측 Stage 위젯/좌측 수동 리스트 상태
        self._usd_context = omni.usd.get_context()
        self._right_selection = None
        self._right_stage_widget = None
        self._left_list_frame = None
        self._stage_subscription = None
        # 좌측 즐겨찾기 목록(현재는 수동 데이터)
        self._favorites_rows = []

        self._window = ui.Window(
            "Favorites Search Prim",
            width=600,
            height=800,
            flags=ui.WINDOW_FLAGS_NO_SCROLLBAR,
        )

        with self._window.frame:
            with ui.HStack(spacing=2, height=ui.Fraction(1.0)):
                # 좌측: 즐겨찾기 수동 목록 패널
                with ui.VStack(width=ui.Fraction(1), spacing=4):
                    with ui.VStack(spacing=0, style=StageStyles.STAGE_WIDGET):
                        # 우측 Stage 헤더와 Y 시작점을 맞추기 위한 오프셋
                        ui.Spacer(height=37)
                        with ui.ZStack(height=13):
                            ui.Rectangle(style_type_name_override="TreeView.Header")
                            with ui.HStack():
                                ui.Spacer(width=10)
                                ui.Label("Name (Old to New)", style_type_name_override="TreeView.Header")
                        with ui.ScrollingFrame(
                            style_type_name_override="TreeView.ScrollingFrame",
                            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                            height=ui.Fraction(1.0),
                        ):
                            self._left_list_frame = ui.Frame()
                # 우측: StageWidget 원본 UI
                with ui.VStack(width=ui.Fraction(2), spacing=0):
                    self._right_stage_widget = StageWidget(None, columns_enabled=["Visibility", "Type"])

        # 우측 StageWidget 선택 동기화(Kit selection <-> TreeView)
        self._right_selection = DefaultSelectionWatch(usd_context=self._usd_context)
        self._right_stage_widget.set_selection_watch(self._right_selection)
        # Stage OPEN/CLOSE 이벤트 구독
        self._stage_subscription = [
            carb.eventdispatcher.get_eventdispatcher().observe_event(
                observer_name="morph.favorites_search_prim",
                event_name=self._usd_context.stage_event_name(event),
                on_event=callback,
            )
            for event, callback in (
                (omni.usd.StageEventType.OPENED, lambda _: self._on_stage_opened()),
                (omni.usd.StageEventType.CLOSING, lambda _: self._on_stage_closing()),
            )
        ]
        self._on_stage_opened()

    def _on_stage_opened(self):
        # Stage가 열리면 우측 위젯과 좌측 즐겨찾기 목록을 갱신
        stage = self._usd_context.get_stage()
        self._rebuild_left_name_list()
        if self._right_stage_widget:
            self._right_stage_widget.open_stage(stage)

    def _on_stage_closing(self):
        # Stage가 닫히면 우측 위젯 연결 해제, 좌측은 수동 목록 그대로 재렌더
        self._rebuild_left_name_list()
        if self._right_stage_widget:
            self._right_stage_widget.open_stage(None)

    def _rebuild_left_name_list(self):
        # 좌측 수동 목록 UI 전체를 다시 그림
        if not self._left_list_frame:
            return

        self._left_list_frame.clear()
        with self._left_list_frame:
            with ui.VStack(spacing=0, style=StageStyles.STAGE_WIDGET):
                if not self._favorites_rows:
                    with ui.HStack(height=20):
                        ui.Spacer(width=8)
                        ui.Label("(empty)", style_type_name_override="TreeView.Item")
                else:
                    for row in self._favorites_rows:
                        self._build_left_row(**row)

    def _build_left_row(self, name, is_default):
        # defaultPrim 표시는 Stage 창 표기 방식과 동일하게 유지
        text = f"{name} (defaultPrim)" if is_default else name
        with ui.HStack(height=20):
            # Stage NameColumnDelegate 레이아웃과 동일 간격:
            # branch 영역(20) + icon 영역(20) + spacing(4) + label
            ui.Spacer(width=20)
            with ui.ZStack(width=20, height=20):
                ui.Image(StageIcons().get("Xform"), style_type_name_override="TreeView.Image")
            ui.Spacer(width=4)
            ui.Label(text, style_type_name_override="TreeView.Item")


    def on_shutdown(self):
        """This is called every time the extension is deactivated. It is used
        to clean up the extension state."""
        # 구독/위젯/참조 정리
        if self._right_selection:
            self._right_selection.destroy()
            self._right_selection = None
        if self._right_stage_widget:
            self._right_stage_widget.destroy()
            self._right_stage_widget = None
        self._left_list_frame = None
        self._stage_subscription = None
        self._usd_context = None
        self._window = None
        print("[morph.favorites_search_prim] Extension shutdown")
