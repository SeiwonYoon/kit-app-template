# Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.
#

__all__ = ["Extension", "get_instance"]

from functools import partial
from typing import Optional

import omni.ext
import omni.ui as ui
from carb import log_error
from omni.kit.menu.utils import MenuItemDescription, add_menu_items, remove_menu_items
from omni.kit.viewport.utility import get_active_viewport_window

from .common import (
    EXTENSION_NAME,
    MEASURE_WINDOW_VISIBLE_CONTEXT,
    SETTINGS_MEASURE_NEXT_SNAP_HOTKEY,
    SETTINGS_MEASURE_NEXT_TOOL_HOTKEY,
    SETTINGS_MEASURE_OPEN_HOTKEY,
    SETTINGS_MEASURE_PREVIOUS_TOOL_HOTKEY,
    VISIBILITY_PATH,
    MeasureMode,
    UserSettings,
    commands,
)
from .interface import MeasurementPropertyWidget, MeasurePanel
from .manager import Hotkey, HotkeyManager, MeasurementManager, ReferenceManager, SelectionStateManager, StateMachine
from .viewport import MeasureScene
from .viewport.snap.attribute_value_cache import AttributeValueCache
from .viewport.tools.viewport_mode_model import CameraManipModeWatcher


class Extension(omni.ext.IExt):
    """
    Measure Tool 확장 프로그램의 메인 클래스

    이 클래스는 Omni Kit 확장 프로그램 인터페이스를 구현하며,
    측정 도구의 초기화, UI 패널 관리, 뷰포트 통합, 핫키 등록 등의
    전반적인 생명주기를 관리합니다.

    싱글톤 패턴을 사용하여 애플리케이션 전체에서 단일 인스턴스만 존재하도록 보장합니다.
    """

    def __new__(cls, *args, **kwargs):
        """
        싱글톤 패턴 구현: 클래스 인스턴스가 이미 존재하는지 확인하고,
        없으면 새로 생성하여 반환합니다.

        Returns:
            Extension: 확장 프로그램의 싱글톤 인스턴스
        """
        if not hasattr(cls, "_instance"):
            cls._instance = super().__new__(cls, *args, **kwargs)
        return cls._instance

    @property
    def panel(self) -> Optional[MeasurePanel]:
        """
        측정 도구 UI 패널을 반환합니다.

        Returns:
            Optional[MeasurePanel]: 측정 패널 인스턴스 또는 None
        """
        return self._measure_panel or None

    @property
    def viewport(self) -> Optional[MeasureScene]:
        """
        뷰포트 측정 씬을 반환합니다.

        Returns:
            Optional[MeasureScene]: 뷰포트 씬 인스턴스 또는 None
        """
        return self._viewport or None

    def on_startup(self, ext_id) -> None:
        """
        확장 프로그램이 시작될 때 호출되는 초기화 메서드

        주요 작업:
        1. 명령어 등록
        2. 속성 위젯 등록
        3. 사용자 설정, 참조 관리자, 측정 관리자, 핫키 관리자 초기화
        4. 측정 패널 및 뷰포트 씬 생성
        5. 메뉴 항목 추가
        6. 디스플레이 설정 등록
        7. 핫키 등록

        Args:
            ext_id: 확장 프로그램 식별자 (예: "omni.kit.tool.measure-200.0.4+109.0")
        """
        # 확장 프로그램 이름 추출 (예: "omni.kit.tool.measure")
        sections = ext_id.split("-")
        self._ext_name = sections[0]

        # 명령어 등록: 측정 도구에서 사용하는 모든 명령어를 시스템에 등록
        commands.register()

        # 속성 위젯 등록: USD 속성 창에서 측정 관련 속성을 표시하기 위한 위젯 등록
        self.__register_property_widget()

        # 싱글톤 매니저들 초기화
        # - UserSettings: 사용자 설정 관리 (단위, 정밀도, 색상 등)
        # - ReferenceManager: 컴포넌트 간 참조를 관리하는 중앙 관리자
        # - MeasurementManager: 측정 데이터의 생성, 수정, 삭제를 관리
        # - AttributeValueCache: 속성 값 캐싱으로 성능 최적화
        # - HotkeyManager: 핫키 등록 및 관리
        UserSettings()
        ReferenceManager()
        MeasurementManager()
        AttributeValueCache()
        HotkeyManager()

        # 측정 패널 초기화 및 UI 작업공간에 등록
        self._measure_panel: Optional[MeasurePanel] = MeasurePanel()
        ui.Workspace.set_show_window_fn(EXTENSION_NAME, partial(self._show_window, None))
        self._visibility_sub = ui.Workspace.set_window_visibility_changed_callback(self._on_visibility_changed)

        # Tools 메뉴에 측정 도구 항목 추가
        self._menu_entry = [
            MenuItemDescription(
                name=EXTENSION_NAME,
                ticked=False,
                ticked_fn=self._is_visible,
                onclick_fn=self._toggle_window,
            )
        ]

        add_menu_items(self._menu_entry, name="Tools")

        # 활성 뷰포트 창 가져오기 및 측정 씬 생성
        viewport_window = get_active_viewport_window()
        if not viewport_window:
            log_error(f"No Viewport Window to add {ext_id} scene to.")
            return

        # 선택 상태 관리자 초기화 (뷰포트에서 측정 도구 사용 시 선택 상태 보존)
        ReferenceManager().selection_state = SelectionStateManager(viewport_window)

        # 뷰포트에 측정 씬 생성 (측정선, 포인트 등을 그리는 씬)
        self._viewport: Optional[MeasureScene] = MeasureScene(viewport_window, ext_id)

        # 핫 리로딩을 위한 스테이지에서 기존 측정 데이터 로드
        MeasurementManager()._populate_model_from_stage()

        # 뷰포트 메뉴바에 측정 표시 옵션 등록
        self.__register_display_setting()

        # 핫키 등록 (창 열기, 다음 도구, 이전 도구, 다음 스냅 모드 등)
        self._register_hotkeys(self._ext_name)

    def on_shutdown(self) -> None:
        """
        확장 프로그램이 종료될 때 호출되는 정리 메서드

        모든 리소스를 해제하고 등록된 콜백, 메뉴 항목, 핫키 등을 제거합니다.
        사용자 설정을 저장하고 싱글톤 인스턴스를 정리합니다.
        """
        # UI 작업공간 콜백 제거
        ui.Workspace.set_show_window_fn(EXTENSION_NAME, None)
        if self._visibility_sub is not None:
            ui.Workspace.remove_window_visibility_changed_callback(self._visibility_sub)
            self._visibility_sub = None

        # Tools 메뉴에서 측정 도구 항목 제거
        remove_menu_items(self._menu_entry, name="Tools")
        self._menu_entry = None
        ui.Workspace.set_show_window_fn(EXTENSION_NAME, None)

        # 선택 상태 복원: 측정 도구 사용 전의 뷰포트 선택 상태로 복원
        ReferenceManager().selection_state.restore()

        # 측정 관리자 모델 초기화 및 해제
        MeasurementManager()._model.clear()
        MeasurementManager.deinit()

        # 상태 머신 해제 (측정 모드를 None으로 설정)
        # MeasurementManager 해제 후에 실행되어야 함
        StateMachine().deinit()

        # 속성 값 캐시 해제
        AttributeValueCache.deinit()

        # 핫키 관리자 해제
        HotkeyManager.deinit()

        # 등록된 명령어 제거
        commands.unregister()

        # 속성 위젯 등록 해제
        self.__unregister_property_widget()

        # 메뉴바 디스플레이 설정 등록 해제
        self.__unregister_display_setting()

        # 사용자 설정을 user.config.json 파일에 저장
        UserSettings().serialize()
        # 설정 패널 등록 해제
        UserSettings().unregister_preferences()

        # 사용자 설정 싱글톤 해제
        UserSettings.deinit()

        # 뷰포트 씬 정리 및 해제
        if self._viewport:
            self._viewport.destroy()
            self._viewport = None

        # 메뉴 및 패널 정리
        self._menu = None
        if self._measure_panel:
            self._measure_panel.destroy()
            self._measure_panel = None

        # 카메라 조작 모드 감시자 인스턴스 삭제
        CameraManipModeWatcher.delete_instance()

        # 싱글톤 인스턴스 정리
        Extension._instance = None

    def _is_visible(self) -> bool:
        """
        측정 패널의 가시성 상태를 반환합니다.

        Returns:
            bool: 패널이 존재하고 보이는 경우 True, 그렇지 않으면 False
        """
        return False if self._measure_panel is None else self._measure_panel.visible

    def _show_window(self, menu: Optional[str], value: bool):
        """
        측정 패널의 표시/숨김을 제어합니다.

        Args:
            menu: 메뉴 이름 (사용되지 않음)
            value: True면 패널 표시, False면 숨김
        """
        if self._measure_panel:
            self._measure_panel.visible = value
            if not value:
                # 패널이 숨겨질 때 상태 머신을 기본 상태로 리셋
                StateMachine().reset_state_to_default(is_current_tool=False)

    def _toggle_window(self):
        """
        측정 패널의 표시/숨김을 토글합니다.
        """
        if self._measure_panel:
            self._show_window(None, not self._measure_panel.visible)

    def _on_visibility_changed(self, name: str, value: bool) -> None:
        """
        창 가시성 변경 콜백

        측정 패널이 표시될 때, 사용자 설정에 시작 도구가 지정되어 있으면
        해당 도구를 활성화합니다.

        Args:
            name: 창 이름
            value: 표시 여부 (True/False)
        """
        if name == EXTENSION_NAME and value:
            # 시작 도구가 설정되어 있고 활성화되어 있으면 해당 도구로 상태 설정
            if UserSettings().session.startup_tool != MeasureMode.NONE and UserSettings().session.startup_enabled:
                StateMachine().set_creation_state(UserSettings().session.startup_tool)

    def __register_display_setting(self):
        """
        뷰포트 메뉴바에 측정 표시 옵션을 등록합니다.

        "Show By Type" 메뉴에 "Measurements" 항목을 추가하여
        측정선의 표시/숨김을 제어할 수 있도록 합니다.
        """
        try:
            from omni.kit.viewport.menubar.core import CategoryStateItem
            from omni.kit.viewport.menubar.display import get_instance as get_display_instance

            self._category_item = CategoryStateItem("Measurements", setting_path=VISIBILITY_PATH)
            get_display_instance().register_custom_category_item("Show By Type", self._category_item)

        except ImportError as e:
            log_error(e)

    def __unregister_display_setting(self):
        """
        뷰포트 메뉴바에서 측정 표시 옵션 등록을 해제합니다.
        """
        try:
            from omni.kit.viewport.menubar.display import get_instance as get_display_instance

            get_display_instance().deregister_custom_category_item("Show By Type", self._category_item)
        except ImportError as e:
            log_error(e)

    def __register_property_widget(self):
        """
        USD 속성 창에 측정 관련 속성 위젯을 등록합니다.

        측정 프림을 선택했을 때 속성 창에서 측정 관련 속성을
        편집할 수 있도록 위젯을 등록합니다.
        """
        try:
            from omni.kit.window.property import get_window as get_property_window

            p_window = get_property_window()
            if p_window:
                p_window.register_widget("prim", "measurement", MeasurementPropertyWidget())
        except ImportError as e:
            log_error(e)

    def __unregister_property_widget(self):
        """
        USD 속성 창에서 측정 관련 속성 위젯 등록을 해제합니다.
        """
        try:
            from omni.kit.window.property import get_window as get_property_window

            p_window = get_property_window()
            if p_window:
                p_window.unregister_widget("prim", "measurement")
        except ImportError as e:
            log_error(e)

    def _register_hotkeys(self, ext_name: str):
        """
        측정 도구에서 사용하는 핫키를 등록합니다.

        등록되는 핫키:
        - 열기: 측정 패널 열기 (기본값 없음, 사용자 설정)
        - 다음 도구: PAGE_UP (기본값)
        - 이전 도구: PAGE_DOWN (기본값)
        - 다음 스냅: ALT + S (기본값)

        Args:
            ext_name: 확장 프로그램 이름
        """
        HotkeyManager().extension_name = ext_name
        # 측정 패널 열기 핫키 (기본값 없음)
        key = HotkeyManager().get_key(SETTINGS_MEASURE_OPEN_HOTKEY, default=None)
        if key:
            HotkeyManager().add_hotkey(Hotkey("open", self._open_window, key))
        # 다음 도구로 전환 (기본값: PAGE_UP)
        key = HotkeyManager().get_key(SETTINGS_MEASURE_NEXT_TOOL_HOTKEY, default="PAGE_UP")
        HotkeyManager().add_hotkey(Hotkey("next-tool", self._next_tool, key, MEASURE_WINDOW_VISIBLE_CONTEXT))
        # 이전 도구로 전환 (기본값: PAGE_DOWN)
        key = HotkeyManager().get_key(SETTINGS_MEASURE_PREVIOUS_TOOL_HOTKEY, default="PAGE_DOWN")
        HotkeyManager().add_hotkey(Hotkey("previous-tool", self._previous_tool, key, MEASURE_WINDOW_VISIBLE_CONTEXT))
        # 다음 스냅 모드로 전환 (기본값: ALT + S)
        key = HotkeyManager().get_key(SETTINGS_MEASURE_NEXT_SNAP_HOTKEY, default="ALT + S")
        HotkeyManager().add_hotkey(Hotkey("next-snap", self._next_snap, key, MEASURE_WINDOW_VISIBLE_CONTEXT))

    def _open_window(self) -> None:
        """
        핫키 콜백: 측정 패널을 엽니다.
        """
        if not self.panel.visible:
            self.panel.visible = True

    def _next_tool(self) -> None:
        """
        핫키 콜백: 다음 측정 도구로 전환합니다.

        도구 순서: Point-to-Point -> Multi-Point -> Angle -> Diameter -> Area
        """
        sm = StateMachine()
        if sm:
            sm.set_next_creation_state()
            self._refresh_window()

    def _previous_tool(self) -> None:
        """
        핫키 콜백: 이전 측정 도구로 전환합니다.

        도구 순서: Area -> Diameter -> Angle -> Multi-Point -> Point-to-Point
        """
        sm = StateMachine()
        if sm:
            sm.set_previous_creation_state()
            self._refresh_window()

    def _next_snap(self) -> None:
        """
        핫키 콜백: 다음 스냅 모드로 전환합니다.

        스냅 모드 순서: None -> Surface -> Vertex -> Pivot -> Edge -> Midpoint -> Center
        """
        rm = ReferenceManager()
        if rm:
            placement_panel = rm.ui_placement_panel
            if placement_panel:
                snap_group = placement_panel.snap_group
                snap_group.set_next_snap()
                self._refresh_window()

    def _refresh_window(self) -> None:
        """
        측정 패널 창을 새로고침하여 UI 업데이트를 반영합니다.
        """
        window = ui.Workspace.get_window(EXTENSION_NAME)
        if window:
            window.frame.invalidate_raster()


def get_instance() -> Extension:
    """
    확장 프로그램의 싱글톤 인스턴스를 반환합니다.

    Returns:
        Extension: 확장 프로그램 인스턴스
    """
    return Extension()
