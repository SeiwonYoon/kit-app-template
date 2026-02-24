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

# 즐겨찾기 경로 집합/콜백은 커스텀 컬럼(delegate)과 확장 본체가 공유한다.
_FAVORITES_PATHS = set()
_TOGGLE_FAVORITE_FN = None
STAR_ICON_PATH = ""
STAR_EMPTY_ICON_PATH = ""

# Favorite 컬럼 아이콘 배치 상수
FAVORITE_COLUMN_WIDTH = 24
FAVORITE_ROW_HEIGHT = 24
FAVORITE_ICON_SIZE = 16

# 좌측 즐겨찾기 목록 선택 하이라이트 기본색(ARGB)
LEFT_HIGHLIGHT_COLOR = 0xFF424B4D


class FavoriteColumnDelegate(AbstractStageColumnDelegate):
    """우측 Stage 트리의 Favorite(별) 컬럼을 그리는 delegate."""

    @property
    def initial_width(self):
        return ui.Pixel(FAVORITE_COLUMN_WIDTH)

    @property
    def minimum_width(self):
        return ui.Pixel(FAVORITE_COLUMN_WIDTH)

    @property
    def order(self):
        # Name 컬럼보다 왼쪽에 오도록 작은 순서를 사용
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

        # 별 아이콘이 셀 중앙에 오도록 클릭 영역을 감싼다.
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
    """좌측 즐겨찾기 목록 + 우측 StageWidget 1:2 분할 UI를 관리한다."""

    def on_startup(self, _ext_id):
        """윈도우/위젯 생성, Favorite 컬럼 등록, Stage 이벤트 구독."""
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
                # 좌측: 수동 즐겨찾기 목록 영역
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

                # 우측: StageWidget + Favorite/Visibility/Type 컬럼
                with ui.VStack(width=ui.Fraction(2), spacing=0):
                    self._right_stage_widget = StageWidget(
                        None,
                        columns_enabled=["Favorite", "Visibility", "Type"],
                    )

        # 우측 Stage 선택 동기화
        self._right_selection = DefaultSelectionWatch(usd_context=self._usd_context)
        self._right_stage_widget.set_selection_watch(self._right_selection)

        # Stage 열림/닫힘/선택 변경 이벤트 구독
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
        """Stage가 열리면 양쪽 목록을 현재 상태로 동기화한다."""
        stage = self._usd_context.get_stage()
        self._sync_favorites_rows(stage)
        self._rebuild_left_name_list()
        if self._right_stage_widget:
            self._right_stage_widget.open_stage(stage)
            self._refresh_right_favorite_column()

    def _on_stage_closing(self):
        """Stage가 닫힐 때 목록/뷰를 비운다."""
        self._sync_favorites_rows(None)
        self._rebuild_left_name_list()
        if self._right_stage_widget:
            self._right_stage_widget.open_stage(None)

    def _on_stage_selection_changed(self):
        """우측 선택이 바뀌면 좌측 목록 하이라이트를 갱신한다."""
        self._rebuild_left_name_list()

    def _toggle_favorite(self, path_str: str):
        """경로 기준으로 즐겨찾기 추가/삭제를 토글한다."""
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
        """현재 Stage 기준으로 좌측 즐겨찾기 표시용 row 데이터를 재구성한다."""
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
                        "icon_paths": self._resolve_stage_icon_paths(prim),
                    }
                )
        self._favorites_rows = rows

    def _resolve_stage_icon_paths(self, prim):
        """우측 Stage Name 컬럼과 유사하게 다중 아이콘(오버레이) 목록을 만든다."""
        icons = StageIcons()
        node_type = prim.GetTypeName()

        # 기본 타입 아이콘
        if node_type in [
            "DistantLight",
            "SphereLight",
            "RectLight",
            "DiskLight",
            "CylinderLight",
            "DomeLight",
        ]:
            type_icon = icons.get(node_type, "Light")
        else:
            if not node_type:
                node_type = "Class"
            type_icon = icons.get(node_type, "Prim")

        icon_paths = [type_icon]

        # 우측 StageWidget의 NameColumnDelegate와 동일한 보조 아이콘 규칙
        if prim.HasAuthoredReferences():
            icon_paths.append(icons.get("Reference"))
        if prim.HasAuthoredPayloads():
            icon_paths.append(icons.get("Payload"))
        if prim.IsInstanceable():
            icon_paths.append(icons.get("Instance"))
        if prim.HasAuthoredInherits():
            icon_paths.append(icons.get("Inherited"))
        if prim.HasAuthoredSpecializes():
            icon_paths.append(icons.get("Specialized"))

        return icon_paths

    def _refresh_right_favorite_column(self):
        """별 상태 변경 후 우측 트리 위젯을 강제로 다시 그린다."""
        if not self._right_stage_widget:
            return
        tree = getattr(self._right_stage_widget, "_tree_view", None)
        flat = getattr(self._right_stage_widget, "_tree_view_flat", None)
        if tree:
            tree.dirty_widgets()
        if flat:
            flat.dirty_widgets()

    def _rebuild_left_name_list(self):
        """좌측 즐겨찾기 목록 UI를 새로 렌더링한다."""
        if not self._left_list_frame:
            return

        selected_paths = set()
        if self._usd_context and self._usd_context.get_selection():
            # Sdf.Path/str 혼용 대비를 위해 문자열로 통일
            selected_paths = {str(p) for p in self._usd_context.get_selection().get_selected_prim_paths()}

        self._left_list_frame.clear()
        with self._left_list_frame:
            with ui.VStack(spacing=0, style=StageStyles.STAGE_WIDGET):
                selected_bg_color = self._get_left_selected_bg_color()
                if not self._favorites_rows:
                    with ui.HStack(height=20):
                        ui.Spacer(width=8)
                        ui.Label("(empty)", style_type_name_override="TreeView.Item")
                else:
                    for row in self._favorites_rows:
                        self._build_left_row(
                            **row,
                            is_selected=row["path"] in selected_paths,
                            selected_bg_color=selected_bg_color,
                        )

    def _build_left_row(self, path, name, is_default, icon_paths, is_selected, selected_bg_color):
        """좌측 목록 한 줄을 생성하고 클릭/더블클릭 동작을 연결한다."""
        text = f"{name} (defaultPrim)" if is_default else name
        row = ui.ZStack(height=20, width=ui.Fraction(1.0))
        row.set_mouse_pressed_fn(
            lambda _x, _y, button, _m, p=path: self._on_left_row_clicked(p) if button == 0 else None
        )
        row.set_mouse_double_clicked_fn(
            lambda _x, _y, button, _m, p=path: self._on_left_row_double_clicked(p) if button == 0 else None
        )
        with row:
            ui.Rectangle(
                visible=is_selected,
                background_color=selected_bg_color,
                width=ui.Fraction(1.0),
                height=20,
            )
            with ui.HStack(height=20):
                ui.Spacer(width=20)
                with ui.ZStack(width=20, height=20):
                    for icon_path in icon_paths:
                        ui.Image(icon_path, style_type_name_override="TreeView.Image")
                ui.Spacer(width=4)
                ui.Label(text, style_type_name_override="TreeView.Item")
                ui.Spacer()
                # 좌측 목록 우측 끝에 즐겨찾기 토글(별) 버튼을 배치
                star_click_area = ui.ZStack(width=FAVORITE_COLUMN_WIDTH, height=FAVORITE_ROW_HEIGHT)
                with star_click_area:
                    with ui.HStack():
                        ui.Spacer()
                        with ui.VStack(width=0):
                            ui.Spacer()
                            ui.Image(STAR_ICON_PATH, width=FAVORITE_ICON_SIZE, height=FAVORITE_ICON_SIZE)
                            ui.Spacer()
                        ui.Spacer()
                star_click_area.set_mouse_pressed_fn(
                    lambda _x, _y, button, _m, p=path: self._toggle_favorite(p) if button == 0 else None
                )
                ui.Spacer(width=2)

    def _get_left_selected_bg_color(self):
        """우측 Stage와 동일한 선택 배경색을 우선 사용한다."""
        tree = getattr(self._right_stage_widget, "_tree_view", None) if self._right_stage_widget else None
        if tree:
            style = getattr(tree, "style", None)
            if isinstance(style, dict):
                selected_style = style.get("TreeView:selected")
                if isinstance(selected_style, dict):
                    color = selected_style.get("background_color")
                    if color is not None:
                        return color

                direct = style.get("background_selected_color")
                if direct is not None:
                    return direct

                tree_style = style.get("TreeView")
                if isinstance(tree_style, dict):
                    color = tree_style.get("background_selected_color")
                    if color is not None:
                        return color

        stage_style = getattr(StageStyles, "STAGE_WIDGET", None)
        if isinstance(stage_style, dict):
            selected_style = stage_style.get("TreeView:selected")
            if isinstance(selected_style, dict):
                color = selected_style.get("background_color")
                if color is not None:
                    return color

            tree_style = stage_style.get("TreeView")
            if isinstance(tree_style, dict):
                color = tree_style.get("background_selected_color")
                if color is not None:
                    return color

        return LEFT_HIGHLIGHT_COLOR

    def _on_left_row_clicked(self, path_str: str):
        """클릭 시 우측 Stage와 동일하게 선택(selection)만 갱신한다."""
        selection = self._usd_context.get_selection() if self._usd_context else None
        if selection:
            selection.set_selected_prim_paths([path_str], True)

    def _on_left_row_double_clicked(self, path_str: str):
        """더블클릭 시 선택 후 뷰포트를 해당 Prim으로 포커싱한다."""
        self._on_left_row_clicked(path_str)
        self._focus_prim(path_str)

    def _focus_prim(self, path_str: str):
        """활성 뷰포트 카메라를 지정 Prim으로 프레임한다."""
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
        """구독/위젯/참조를 정리하여 확장을 안전하게 종료한다."""
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
