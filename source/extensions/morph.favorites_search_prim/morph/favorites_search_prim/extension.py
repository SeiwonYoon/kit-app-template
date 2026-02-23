# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

from pathlib import Path
import asyncio

import carb
import carb.eventdispatcher
import omni.ext
import omni.kit.app
import omni.kit.viewport.utility as vp_util
import omni.ui as ui
import omni.usd
from omni.kit.widget.stage import (
    AbstractStageColumnDelegate,
    DefaultSelectionWatch,
    StageColumnDelegateRegistry,
    StageIcons,
    StageWidget,
)
from omni.kit.widget.stage.stage_style import Styles as StageStyles

_FAVORITES_PATHS = set()
_TOGGLE_FAVORITE_FN = None
STAR_ICON_PATH = ""
STAR_EMPTY_ICON_PATH = ""

# Favorite 아이콘/컬럼 크기 상수
FAVORITE_COLUMN_WIDTH = 24
FAVORITE_ROW_HEIGHT = 24
FAVORITE_ICON_SIZE = 16
# 좌측 즐겨찾기 행 선택 하이라이트 색상(ARGB)
# 기본값: 0xFF424B4D  (RGB: 42 4B 4D)
LEFT_HIGHLIGHT_COLOR = 0xFF424B4D


class FavoriteColumnDelegate(AbstractStageColumnDelegate):
    """Stage 행의 가장 왼쪽에 표시되는 즐겨찾기(별) 컬럼."""

    @property
    def initial_width(self):
        return ui.Pixel(FAVORITE_COLUMN_WIDTH)

    @property
    def minimum_width(self):
        return ui.Pixel(FAVORITE_COLUMN_WIDTH)

    @property
    def order(self):
        # Name 컬럼보다 더 왼쪽에 배치
        return -200000

    @property
    def resizable(self):
        return False

    async def build_widget(self, _, **kwargs):
        stage_item = kwargs.get("stage_item", None)
        if not stage_item:
            return

        path_str = str(stage_item.path)
        icon_path = STAR_ICON_PATH if path_str in _FAVORITES_PATHS else STAR_EMPTY_ICON_PATH

        # 클릭 영역 + 중앙 정렬 이미지 구성(클리핑 방지)
        click_area = ui.ZStack(width=FAVORITE_COLUMN_WIDTH, height=FAVORITE_ROW_HEIGHT)
        with click_area:
            with ui.HStack():
                ui.Spacer()
                with ui.VStack(width=0):
                    ui.Spacer()
                    ui.Image(icon_path, width=FAVORITE_ICON_SIZE, height=FAVORITE_ICON_SIZE)
                    ui.Spacer()
                ui.Spacer()

        click_area.set_mouse_pressed_fn(
            lambda _x, _y, button, _m, p=path_str: (
                _TOGGLE_FAVORITE_FN and _TOGGLE_FAVORITE_FN(p) if button == 0 else None
            )
        )


def some_public_function(x: int):
    print(f"[morph.favorites_search_prim] some_public_function was called with {x}")
    return x ** x


class MyExtension(omni.ext.IExt):
    """좌측 즐겨찾기 목록 + 우측 Stage 위젯 분할 UI를 관리."""

    def on_startup(self, _ext_id):
        """UI 생성, 커스텀 컬럼 등록, Stage 이벤트 구독을 수행."""
        print("[morph.favorites_search_prim] Extension startup")
        global STAR_ICON_PATH, STAR_EMPTY_ICON_PATH, _TOGGLE_FAVORITE_FN

        if StageStyles.STAGE_WIDGET is None:
            StageStyles.on_startup()

        data_dir = Path(__file__).resolve().parents[2] / "data"
        STAR_ICON_PATH = str(data_dir / "star.png")
        STAR_EMPTY_ICON_PATH = str(data_dir / "star_empty.png")

        self._usd_context = omni.usd.get_context()
        self._right_selection = None
        self._right_stage_widget = None
        self._left_list_frame = None
        self._stage_subscription = None
        self._favorite_delegate_sub = None
        self._favorite_paths = []
        self._favorites_rows = []
        _FAVORITES_PATHS.clear()
        _TOGGLE_FAVORITE_FN = self._toggle_favorite

        registry = StageColumnDelegateRegistry()
        if not registry.get_column_delegate("Favorite"):
            self._favorite_delegate_sub = registry.register_column_delegate("Favorite", FavoriteColumnDelegate)

        self._window = ui.Window(
            "Favorites Search Prim",
            width=600,
            height=800,
            flags=ui.WINDOW_FLAGS_NO_SCROLLBAR,
        )

        with self._window.frame:
            with ui.HStack(spacing=2, height=ui.Fraction(1.0)):
                # 좌측: 즐겨찾기 수동 목록
                with ui.VStack(width=ui.Fraction(1), spacing=4):
                    with ui.VStack(spacing=0, style=StageStyles.STAGE_WIDGET):
                        ui.Spacer(height=37)
                        with ui.ZStack(height=13):
                            ui.Rectangle(style_type_name_override="TreeView.Header")
                            with ui.HStack():
                                ui.Spacer(width=10)
                                ui.Label("Name", style_type_name_override="TreeView.Header")
                        with ui.ScrollingFrame(
                            style_type_name_override="TreeView.ScrollingFrame",
                            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                            height=ui.Fraction(1.0),
                        ):
                            self._left_list_frame = ui.Frame()

                # 우측: StageWidget + 즐겨찾기 컬럼
                with ui.VStack(width=ui.Fraction(2), spacing=0):
                    self._right_stage_widget = StageWidget(
                        None,
                        columns_enabled=["Favorite", "Visibility", "Type"],
                    )

        # 우측 Stage 선택 동기화
        self._right_selection = DefaultSelectionWatch(usd_context=self._usd_context)
        self._right_stage_widget.set_selection_watch(self._right_selection)

        # Stage 열림/닫힘 동기화
        self._stage_subscription = [
            carb.eventdispatcher.get_eventdispatcher().observe_event(
                observer_name="morph.favorites_search_prim",
                event_name=self._usd_context.stage_event_name(event),
                on_event=callback,
            )
            for event, callback in (
                (omni.usd.StageEventType.OPENED, lambda _: self._on_stage_opened()),
                (omni.usd.StageEventType.CLOSING, lambda _: self._on_stage_closing()),
                (omni.usd.StageEventType.SELECTION_CHANGED, lambda _: self._on_stage_selection_changed()),
            )
        ]
        self._on_stage_opened()

    def _on_stage_opened(self):
        stage = self._usd_context.get_stage()
        self._sync_favorites_rows(stage)
        self._rebuild_left_name_list()
        if self._right_stage_widget:
            self._right_stage_widget.open_stage(stage)
            self._refresh_right_favorite_column()

    def _on_stage_closing(self):
        self._sync_favorites_rows(None)
        self._rebuild_left_name_list()
        if self._right_stage_widget:
            self._right_stage_widget.open_stage(None)

    def _on_stage_selection_changed(self):
        """선택이 바뀌면 좌측 즐겨찾기의 선택 하이라이트도 갱신."""
        self._rebuild_left_name_list()

    def _toggle_favorite(self, path_str: str):
        """prim path 기준 즐겨찾기 토글."""
        if path_str in _FAVORITES_PATHS:
            _FAVORITES_PATHS.remove(path_str)
            self._favorite_paths = [p for p in self._favorite_paths if p != path_str]
        else:
            _FAVORITES_PATHS.add(path_str)
            self._favorite_paths.append(path_str)

        self._sync_favorites_rows(self._usd_context.get_stage())
        self._rebuild_left_name_list()
        self._refresh_right_favorite_column()

    def _sync_favorites_rows(self, stage):
        """현재 Stage 기준으로 좌측 표시용 행 데이터 구성."""
        rows = []
        if stage:
            for path_str in self._favorite_paths:
                prim = stage.GetPrimAtPath(path_str)
                if not prim or not prim.IsValid():
                    continue
                rows.append(
                    {
                        "path": path_str,
                        "name": prim.GetName(),
                        "is_default": prim == stage.GetDefaultPrim(),
                        "icon_path": self._resolve_stage_icon_path(prim),
                    }
                )
        self._favorites_rows = rows

    def _resolve_stage_icon_path(self, prim):
        """우측 Stage Name 컬럼과 동일한 규칙으로 대표 아이콘 선택."""
        icons = StageIcons()
        node_type = prim.GetTypeName()

        if node_type in [
            "DistantLight",
            "SphereLight",
            "RectLight",
            "DiskLight",
            "CylinderLight",
            "DomeLight",
        ]:
            return icons.get(node_type, "Light")

        if not node_type:
            node_type = "Class"

        return icons.get(node_type, "Prim")

    def _refresh_right_favorite_column(self):
        """별 상태가 즉시 반영되도록 우측 트리 강제 갱신."""
        if not self._right_stage_widget:
            return
        tree = getattr(self._right_stage_widget, "_tree_view", None)
        flat = getattr(self._right_stage_widget, "_tree_view_flat", None)
        if tree:
            tree.dirty_widgets()
        if flat:
            flat.dirty_widgets()

    def _rebuild_left_name_list(self):
        """좌측 즐겨찾기 목록을 다시 렌더링."""
        if not self._left_list_frame:
            return

        selected_paths = set()
        if self._usd_context and self._usd_context.get_selection():
            selected_paths = set(self._usd_context.get_selection().get_selected_prim_paths())

        self._left_list_frame.clear()
        with self._left_list_frame:
            with ui.VStack(spacing=0, style=StageStyles.STAGE_WIDGET):
                if not self._favorites_rows:
                    with ui.HStack(height=20):
                        ui.Spacer(width=8)
                        ui.Label("(empty)", style_type_name_override="TreeView.Item")
                else:
                    for row in self._favorites_rows:
                        self._build_left_row(
                            **row,
                            is_selected=row["path"] in selected_paths,
                        )

    def _build_left_row(self, path, name, is_default, icon_path, is_selected):
        """좌측 1행 렌더 + 클릭/더블클릭 동작 연결."""
        text = f"{name} (defaultPrim)" if is_default else name
        row = ui.ZStack(height=20)
        row.set_mouse_pressed_fn(
            lambda _x, _y, button, _m, p=path: self._on_left_row_clicked(p) if button == 0 else None
        )
        row.set_mouse_double_clicked_fn(
            lambda _x, _y, button, _m, p=path: self._on_left_row_double_clicked(p) if button == 0 else None
        )
        with row:
            # Stage TreeView selected 색상과 유사한 하이라이트
            ui.Rectangle(visible=is_selected, background_color=LEFT_HIGHLIGHT_COLOR)
            with ui.HStack(height=20):
                ui.Spacer(width=20)
                with ui.ZStack(width=20, height=20):
                    ui.Image(icon_path, style_type_name_override="TreeView.Image")
                ui.Spacer(width=4)
                ui.Label(text, style_type_name_override="TreeView.Item")

    def _on_left_row_clicked(self, path_str: str):
        """클릭: 우측 Stage와 동일하게 selection 갱신."""
        selection = self._usd_context.get_selection() if self._usd_context else None
        if selection:
            selection.set_selected_prim_paths([path_str], True)

    def _on_left_row_double_clicked(self, path_str: str):
        """더블클릭: selection + viewport focus(frame)."""
        self._on_left_row_clicked(path_str)
        self._focus_prim(path_str)

    def _focus_prim(self, path_str: str):
        """활성 뷰포트 카메라를 대상 prim으로 프레이밍."""
        if not path_str:
            return

        async def _do_focus():
            await omni.kit.app.get_app().next_update_async()

            viewport_api = None
            if hasattr(vp_util, "get_active_viewport"):
                viewport_api = vp_util.get_active_viewport()
            elif hasattr(vp_util, "get_active_viewport_window"):
                win = vp_util.get_active_viewport_window()
                viewport_api = win.viewport_api if win else None

            if not viewport_api:
                carb.log_warn(f"[morph.favorites_search_prim] Focus failed: no active viewport. path={path_str}")
                return

            vp_util.frame_viewport_prims(viewport_api, prims=[path_str])

        asyncio.ensure_future(_do_focus())

    def on_shutdown(self):
        """구독/위젯/전역 콜백 정리."""
        global _TOGGLE_FAVORITE_FN
        if self._right_selection:
            self._right_selection.destroy()
            self._right_selection = None
        if self._right_stage_widget:
            self._right_stage_widget.destroy()
            self._right_stage_widget = None
        self._favorite_delegate_sub = None
        self._left_list_frame = None
        self._stage_subscription = None
        self._usd_context = None
        _TOGGLE_FAVORITE_FN = None
        self._window = None
        print("[morph.favorites_search_prim] Extension shutdown")
