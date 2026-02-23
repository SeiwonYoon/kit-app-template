# Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.
#

__all__ = [
    "EXPORT_FOLDER",
    "EXTENSION_PATH",
    "SELECTION_LINE_COLOR",
    "SELECTION_LINE_WIDTH",
    "VISIBILITY_PATH",
    "UserSettings",
]

from dataclasses import dataclass
from typing import List

import omni.ui as ui
import omni.usd as ou
from carb.dictionary import get_dictionary
from carb.settings import ISettings, get_settings
from omni.kit.window.preferences import PreferenceBuilder, register_page, unregister_page

from .constant import EXTENSION_NAME, MeasureMode

APP_CURRENT_TOOL_PATH = "/app/viewport/currentTool"
SETTINGS_STARTUP_TOOL = "/exts/omni.kit.tool.measure/startup_tool"
SETTINGS_STARTUP_TOOL_PERSISTENT = f"/persistent{SETTINGS_STARTUP_TOOL}"
VISIBILITY_PATH = "/persistent/exts/omni.kit.tool.measure/visibility"
EXTENSION_PATH = "/persistent/exts/omni.kit.tool.measure"
EXPORT_FOLDER = "/persistent/exts/omni.kit.tool.measure/exportFolder"
SELECTION_LINE_COLOR = "/persistent/app/viewport/outline/color"
SELECTION_LINE_WIDTH = "/persistent/app/viewport/outline/width"

SETTINGS_HOTKEY_PATH = "/exts/omni.kit.tool.measure/"
SETTINGS_MEASURE_ENABLE_HOTKEYS = SETTINGS_HOTKEY_PATH + "enable_hotkeys"
SETTINGS_MEASURE_OPEN_HOTKEY = SETTINGS_HOTKEY_PATH + "hotkeys/open"
SETTINGS_MEASURE_NEXT_TOOL_HOTKEY = SETTINGS_HOTKEY_PATH + "hotkeys/next-tool"
SETTINGS_MEASURE_PREVIOUS_TOOL_HOTKEY = SETTINGS_HOTKEY_PATH + "hotkeys/previous-tool"
SETTINGS_MEASURE_NEXT_SNAP_HOTKEY = SETTINGS_HOTKEY_PATH + "hotkeys/next-snap"


class MeasurePreferences(PreferenceBuilder):
    def __init__(self):
        super().__init__("Measure")

    def __del__(self):
        pass

    def destroy(self):
        self.__del__()

    def build(self):
        omitted = [MeasureMode.SELECTED, MeasureMode.VOLUME]
        enum_list = [enum.name for enum in MeasureMode if enum not in omitted]

        with ui.VStack(height=0):
            with self.add_frame("Measure Settings"):
                with ui.VStack():
                    self.create_setting_widget_combo(
                        "Default Measure tool on Application Start", SETTINGS_STARTUP_TOOL_PERSISTENT, enum_list
                    )


@dataclass
class _SessionSettings:
    """
    Class for keeping track of session settings
    """

    # FRAME::OPTIONS
    startup_enabled: bool = True
    constrain_axis: int = 4
    color = [0.0, 1.0, 1.0, 0.0]
    display_axis: int = 0
    units: int = 3
    snapping_mode: int = 0
    # FRAME::DIMENSION
    distance: int = 2
    world_axis = [False, False, False]  # X, Y, Z
    # FRAME::DISPLAY
    label_precision: int = 2
    label_position: int = 0
    label_size: int = 1
    # SNAP::OPTIONS
    snap_center: bool = False
    snap_edge: bool = False
    snap_midpoint: bool = False
    snap_pivot: bool = False
    snap_vertex: bool = True
    snap_surface: bool = False
    # STARTUP TOOL
    startup_tool: MeasureMode = MeasureMode.POINT_TO_POINT

    def get_color_a(self) -> float:
        return self.color[0]

    def get_color_b(self) -> float:
        return self.color[1]

    def get_color_g(self) -> float:
        return self.color[2]

    def get_color_r(self) -> float:
        return self.color[3]

    def set_color_rgba(self, value: List[float]):
        value_count = len(value)
        if value_count > 0:
            self.color[3] = value[0]
        if value_count > 1:
            self.color[2] = value[1]
        if value_count > 2:
            self.color[1] = value[2]
        if value_count > 3:
            self.color[0] = value[3]


class UserSettings:
    def __singleton_init__(self):
        self.__dict = get_dictionary()
        self._preferences: MeasurePreferences = MeasurePreferences()
        self._persistent_settings: ISettings = get_settings()
        self._session_settings: _SessionSettings = self._create_session()
        self._default_session_settings = _SessionSettings()
        register_page(self._preferences)

        # Check to see if this is a new session with no user settings.
        # If so Serialize new settings and set visibility to 1
        if not self._persistent_settings.get(VISIBILITY_PATH):
            self._persistent_settings.set(VISIBILITY_PATH, 1)
            self.serialize()

        self.__visibility_sub = self._persistent_settings.subscribe_to_node_change_events(
            VISIBILITY_PATH, self.__on_visibility_changed
        )

        self.__state_sync_sub: int = self._persistent_settings.subscribe_to_node_change_events(
            APP_CURRENT_TOOL_PATH, self.__on_app_state_sync_changed
        )

    # singleton model, set once - use everywhere
    def __new__(cls, *args, **kwargs):
        if not hasattr(cls, "_instance"):
            cls._instance = super().__new__(cls, *args, **kwargs)
            cls._instance.__singleton_init__()
        return cls._instance

    @classmethod
    def deinit(cls):
        if hasattr(cls, "_instance"):
            cls._instance.destroy()
            del cls._instance

    def __del__(self):
        if self._preferences:
            self._preferences.destroy()
            self._preferences = None

        if self.__visibility_sub:
            self._persistent_settings.unsubscribe_to_change_events(self.__visibility_sub)
            self.__visibility_sub = None

        if self.__state_sync_sub:
            self._persistent_settings.unsubscribe_to_change_events(self.__state_sync_sub)
            self.__state_sync_sub = None

    def destroy(self):
        self.__del__()

    @property
    def default_session(self) -> _SessionSettings:
        if not self._default_session_settings:
            self._default_session_settings = _SessionSettings()
        return self._default_session_settings

    @property
    def persistent(self) -> ISettings:
        if not self._persistent_settings:
            self._persistent_settings = get_settings()
        return self._persistent_settings

    @property
    def session(self) -> _SessionSettings:
        if not self._session_settings:
            self._session_settings = self._create_session()
        return self._session_settings

    @property
    def visible(self) -> bool:
        visibility_prop = self._persistent_settings.get_as_bool(VISIBILITY_PATH)
        return visibility_prop

    def __on_visibility_changed(self, item, *_):
        stage = ou.get_context().get_stage()
        if not stage:
            return False

        from ..manager import MeasurementManager

        MeasurementManager().set_visibility_all(self.visible)

    def __on_app_state_sync_changed(self, item, *_):
        # Get the value from the node change
        sync_state: str = self.__dict.get(item)
        if sync_state == EXTENSION_NAME:
            return

        from ..manager import StateMachine

        StateMachine().reset_state_to_default()

    def set_app_current_tool(self, measure_enabled: bool = True) -> None:
        # Do not continue setting the value if it is already current.
        # No need to trigger other tools state sync callbacks multiple times when the tool mode changes
        if measure_enabled:
            if self._persistent_settings.get_as_string(APP_CURRENT_TOOL_PATH) == EXTENSION_NAME:
                return
            self._persistent_settings.set_string(APP_CURRENT_TOOL_PATH, EXTENSION_NAME)
        else:
            self._persistent_settings.set_string(APP_CURRENT_TOOL_PATH, "navigation")

    def _construct_property_path(self, property_name: str) -> str:
        """
        Validates property name string and returns full property path.

        Args:
            property_name: Name of settings property.
        Returns:
            Full property path.
        """
        property_name = property_name if not property_name.startswith("/") else property_name[1:]
        return f"{EXTENSION_PATH}/{property_name}"

    def save_property(self, property_name: str, property_value) -> None:
        """
        Saves the property through the ISettings interface.

        Args:
            property_name: Name of the settings property.
            property_value: Value to set for the property.
        """
        property_path: str = self._construct_property_path(property_name)
        self.persistent.set(property_path, property_value)

    def get_property(self, property_name: str, default=None):
        """
        Gets the property through the ISettings interface.

        Args:
            property_name: Name of the settings property.
        Returns:
            specified property value.
        """
        property_path: str = self._construct_property_path(property_name)
        return self.persistent.get(property_path) or default

    def _create_session(self) -> _SessionSettings:
        """
        Creates a new SessionSettings object and attempts to load
        persistant values serialized from the previous session.

        Returns:
            _SessionSettings object
        """
        settings = _SessionSettings()

        default_startup_tool_name = settings.startup_tool.name
        valid_measure_modes = [enum.name for enum in MeasureMode]
        for setting_path in [SETTINGS_STARTUP_TOOL_PERSISTENT, SETTINGS_STARTUP_TOOL]:
            startup_tool_name = self._persistent_settings.get_as_string(setting_path)
            if startup_tool_name in valid_measure_modes and startup_tool_name != "NONE":
                default_startup_tool_name = startup_tool_name
                break
        self._persistent_settings.set_default_string(SETTINGS_STARTUP_TOOL_PERSISTENT, default_startup_tool_name)

        # Load previous persistent settings into current Session Settings
        settings.constrain_axis = self.get_property("constrain_axis", settings.constrain_axis)
        settings.color = self.get_property("color", settings.color)
        settings.display_axis = self.get_property("display_axis", settings.display_axis)
        settings.units = self.get_property("units", settings.units)
        settings.snapping_mode = self.get_property("snapping_mode", settings.snapping_mode)
        settings.distance = self.get_property("distance", settings.distance)
        settings.world_axis = self.get_property("world_axis", settings.world_axis)
        settings.label_precision = self.get_property("label_precision", settings.label_precision)
        settings.label_position = self.get_property("label_position", settings.label_position)
        settings.label_size = self.get_property("label_size", settings.label_size)
        settings.snap_center = self.get_property("snap_center", settings.snap_center)
        settings.snap_edge = self.get_property("snap_edge", settings.snap_edge)
        settings.snap_midpoint = self.get_property("snap_midpoint", settings.snap_midpoint)
        settings.snap_pivot = self.get_property("snap_pivot", settings.snap_pivot)
        settings.snap_vertex = self.get_property("snap_vertex", settings.snap_vertex)

        startup_tool_name = self.get_property("startup_tool", default_startup_tool_name)
        if startup_tool_name and startup_tool_name in valid_measure_modes and startup_tool_name != "NONE":
            settings.startup_tool = MeasureMode[startup_tool_name]

        return settings

    def serialize(self) -> None:
        """
        Serializes last used interface settings to the persistant user.config.json
        """
        self.save_property("constrain_axis", self._session_settings.constrain_axis)
        self.save_property("color", self._session_settings.color)
        self.save_property("display_axis", self._session_settings.display_axis)
        self.save_property("units", self._session_settings.units)
        self.save_property("snapping_mode", self._session_settings.snapping_mode)
        self.save_property("distance", self._session_settings.distance)
        self.save_property("world_axis", self._session_settings.world_axis)
        self.save_property("label_precision", self._session_settings.label_precision)
        self.save_property("label_position", self._session_settings.label_position)
        self.save_property("label_size", self._session_settings.label_size)
        self.save_property("snap_center", self._session_settings.snap_center)
        self.save_property("snap_edge", self._session_settings.snap_edge)
        self.save_property("snap_midpoint", self._session_settings.snap_midpoint)
        self.save_property("snap_pivot", self._session_settings.snap_pivot)
        self.save_property("snap_vertex", self._session_settings.snap_vertex)
        self.save_property("startup_tool", self._session_settings.startup_tool.name)

    def unregister_preferences(self) -> None:
        unregister_page(self._preferences)

    def reset_to_default(self) -> _SessionSettings:
        """
        Resets the current session settings to default values

        Returns:
            Default SessionSettings object
        """
        self._session_settings = _SessionSettings()
        return self._session_settings
