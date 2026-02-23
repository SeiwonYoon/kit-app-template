# Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.
#

from pathlib import Path

import omni.kit.app
import omni.kit.ui_test as ui_test
import omni.usd
import pxr.Gf
from omni.kit.viewport.utility import get_active_viewport_window

from ..common import MeasureMode, SnapMode, UserSettings
from ..interface.panel import MeasurePanel
from .test_util import PRIM_PATHS, TestMeasureBase, prim_path_world_pos, select_test_objects

CURRENT_PATH = Path(__file__).parent
TEST_DATA_PATH = CURRENT_PATH

MANAGER_UI_PATH = "Measure//Frame/**/ManagePanel"
DISPLAY_UI_PATH = "Measure//Frame/**/DisplayPanal"


async def create_one_measure():
    from omni.kit import commands

    world_pos = [prim_path_world_pos(path) for path in PRIM_PATHS]

    prim_paths = [PRIM_PATHS[0], PRIM_PATHS[1]]
    points = [world_pos[0], world_pos[1]]
    commands.execute(
        "CreateMeasurementPointToPointCommand",
        prim_paths=prim_paths,
        points=points,
    )
    await ui_test.human_delay(5)


class TestMeasureUI(TestMeasureBase):

    async def test_reset_ui(self):
        panel_ui = self._extension._measure_panel
        self.assertIsNotNone(panel_ui)

        # Global Panel
        global_pn = panel_ui._pn_global
        self.assertIsNotNone(global_pn)

        # Display Panel
        display_pn = panel_ui._pn_display
        self.assertIsNotNone(display_pn)

        display_pn._on_display_axis_reset(0)
        self.assertEqual(display_pn._cb_display_axis.model.get_item_value_model().as_int, 0)

        display_pn._on_precision_reset(2)
        self.assertEqual(display_pn._precision_combo.model.get_item_value_model().as_int, 2)

        display_pn._on_units_reset(0)
        self.assertEqual(display_pn._cb_units.model.get_item_value_model().as_int, 3)

        display_pn._on_size_reset(1)
        self.assertEqual(display_pn._size_combo.model.get_item_value_model().as_int, 1)

        display_pn._on_color_reset([1, 1, 1])
        sub_models = display_pn._color_widget.model.get_item_children()
        self.assertEqual(display_pn._color_widget.model.get_item_value_model(sub_models[0]).as_float, 1.0)
        self.assertEqual(display_pn._color_widget.model.get_item_value_model(sub_models[1]).as_float, 1.0)
        self.assertEqual(display_pn._color_widget.model.get_item_value_model(sub_models[2]).as_float, 1.0)

    async def test_panel_placement(self):
        """
        Test if measure panel is placed at the right side of viewport. (OMFP-2054)
        """
        measure_window: MeasurePanel = omni.ui.Workspace.get_window("Measure")

        # Undock measure panel and force it to hide and show, so it can place itself to viewport
        measure_window.undock()
        measure_window.visible = False

        measure_window._window_updated = False
        measure_window.visible = True

        # Wait one frame because panel will try to dock first
        await omni.kit.app.get_app().next_update_async()

        viewport_window = get_active_viewport_window()
        viewport_position_x = viewport_window.position_x
        viewport_position_y = viewport_window.position_y
        viewport_width = viewport_window.width

        measure_window_x = measure_window.position_x
        measure_window_y = measure_window.position_y

        measure_window_width = measure_window.width

        expected_x = viewport_position_x + viewport_width - measure_window_width - MeasurePanel.SPACING
        # self.assertEqual(expected_x, measure_window_x)

        expected_y = viewport_position_y + MeasurePanel.SPACING + MeasurePanel.VIEWPORT_MAIN_MENUBAR_HEIGHT
        if viewport_window.docked and viewport_window.dock_tab_bar_visible:
            # 18 is the dock bar height
            expected_y += 18

        # self.assertEqual(expected_y, measure_window_y)

    async def test_viewport_ui(self):
        viewport = self._extension.viewport
        self.assertIsNotNone(viewport)

        model = viewport._manipulator._model
        self.assertIsNotNone(model)

        # Create a measurement to operate with
        ##  Select objects and test to be sure both objects are selected
        select_test_objects(PRIM_PATHS)
        await ui_test.human_delay(5)

        selection = omni.usd.get_context().get_selection()
        self.assertEqual(len(selection.get_selected_prim_paths()), 2)

        ## Run the measure tool, simulating the button press in the UI
        self._extension._measure_panel._pn_global._on_measure_selected()
        await ui_test.human_delay(10)

        # get uuid from measurement
        item = list(model.measurements.values())[0]
        self.assertTrue(isinstance(item.uuid, int))

        # Check item properties
        self.assertFalse(item.selected)
        item.selected = True
        self.assertTrue(item.selected)
        model.deselect_all()
        self.assertFalse(item.selected)
        self.assertIsNotNone(item.payload)

        viewport.delete(item.uuid)
        self.assertEqual(len(model._measurements), 0)

        viewport.destroy()
        self.assertIsNone(viewport._manipulator)

    async def test_snaps_ui(self):
        panel_ui = self._extension._measure_panel
        self.assertIsNotNone(panel_ui)

        placement_pn = panel_ui._pn_placement
        self.assertIsNotNone(placement_pn)

        snap_group = placement_pn.snap_group
        self.assertIsNotNone(snap_group)

        # set snaps one at a time, check if snap list updates correctly
        self.assertEqual(len(placement_pn.snap_mode), 0)

        # - surface
        snap_group._snap_collection.model.as_int = snap_group._display_order.index(SnapMode.SURFACE)
        self.assertEqual(len(placement_pn.snap_mode), 1)
        # - pivot
        snap_group._snap_collection.model.as_int = snap_group._display_order.index(SnapMode.PIVOT)
        self.assertEqual(len(placement_pn.snap_mode), 1)
        # - center
        snap_group._snap_collection.model.as_int = snap_group._display_order.index(SnapMode.CENTER)
        self.assertEqual(len(placement_pn.snap_mode), 1)
        # - vertex
        snap_group._snap_collection.model.as_int = snap_group._display_order.index(SnapMode.VERTEX)
        self.assertEqual(len(placement_pn.snap_mode), 1)
        # - mid point
        snap_group._snap_collection.model.as_int = snap_group._display_order.index(SnapMode.MIDPOINT)
        self.assertEqual(len(placement_pn.snap_mode), 1)
        # - edge
        snap_group._snap_collection.model.as_int = snap_group._display_order.index(SnapMode.EDGE)
        self.assertEqual(len(placement_pn.snap_mode), 1)

    async def test_user_change_units(self):
        """Unit change on the panel updates user settings"""
        from omni.kit.tool.measure.common import UserSettings

        panel_ui = self._extension._measure_panel
        self.assertIsNotNone(panel_ui)

        # Display Panel
        display_pn = panel_ui._pn_display
        self.assertIsNotNone(display_pn)

        unit_model = display_pn._cb_units.model.get_item_value_model()

        before = UserSettings().session.units
        unit_model.set_value(1)
        self.assertNotEqual(UserSettings().session.units, before)

    async def test_toggle_visablity_from_manager(self):
        from ..manager import MeasurementManager

        await create_one_measure()

        items = MeasurementManager()._model.get_items()
        self.assertEqual(len(items), 1)

        visiblity_btn = ui_test.find(MANAGER_UI_PATH + "/**/VisibilityBtn")
        self.assertIsNotNone(visiblity_btn)
        # Check the measure is visible by default
        self.assertEqual(items[0].payload.visible, True)
        # Click the button to make it not visible
        await visiblity_btn.click()
        await ui_test.wait_n_updates(3)
        self.assertEqual(items[0].payload.visible, False)
        # Click again to make it visible
        await visiblity_btn.click()
        await ui_test.wait_n_updates(3)
        self.assertEqual(items[0].payload.visible, True)

    async def test_delete_from_manager(self):
        await create_one_measure()
        from ..manager import MeasurementManager

        self.assertEqual(len(MeasurementManager()._model.get_items()), 1)
        delete_btn = ui_test.find(MANAGER_UI_PATH + "/**/DeleteBtn")
        await delete_btn.click()
        await ui_test.wait_n_updates(3)
        self.assertEqual(len(MeasurementManager()._model.get_items()), 0)

    async def test_goto_from_manager(self):
        from omni.kit.viewport.utility import get_active_viewport

        await create_one_measure()

        viewport = get_active_viewport()
        active_camera_path = viewport.camera_path.pathString
        cam = self._ctx.get_stage().GetPrimAtPath(active_camera_path)
        old_cam_pos = cam.GetAttribute("xformOp:translate").Get()
        goto_btn = ui_test.find(MANAGER_UI_PATH + "/**/GotoBtn")
        await goto_btn.click()
        await ui_test.wait_n_updates(3)
        new_cam_pos = cam.GetAttribute("xformOp:translate").Get()
        self.assertNotEqual(new_cam_pos, old_cam_pos)

    async def test_filter_bar_from_manager(self):
        await create_one_measure()

        filter_bar = ui_test.find(MANAGER_UI_PATH + "/**/Search")
        await filter_bar.input("some test text bla bla bla boobar")
        await ui_test.wait_n_updates(3)
        visiblity_btn = ui_test.find(MANAGER_UI_PATH + "/**/VisibilityBtn")
        self.assertIsNone(visiblity_btn)
        # Clear the search bar so it does not affect later tests
        filter_bar.widget.model.set_value("")
        visiblity_btn = ui_test.find(MANAGER_UI_PATH + "/**/VisibilityBtn")
        self.assertIsNotNone(visiblity_btn)

    async def test_click_to_select_from_manager(self):
        await create_one_measure()
        from ..manager import MeasurementManager

        # Find the "Value" label and move cursor as selecting an item is not implemented as a
        # UI element but mouse move + click event
        name_label = ui_test.find(MANAGER_UI_PATH + "/**/ValueLabel")
        self.assertIsNotNone(name_label)
        pos = name_label.position
        pos = pos + ui_test.Vec2(0, 20)
        self.assertEqual(len(MeasurementManager().selected), 0)
        await ui_test.emulate_mouse_move(pos)
        await ui_test.wait_n_updates(3)
        await ui_test.emulate_mouse_click()
        await ui_test.wait_n_updates(3)
        self.assertEqual(len(MeasurementManager().selected), 1)
        pos = pos + ui_test.Vec2(0, 45)
        await ui_test.emulate_mouse_move(pos)
        await ui_test.wait_n_updates(3)
        await ui_test.emulate_mouse_click()
        await ui_test.wait_n_updates(3)
        self.assertEqual(len(MeasurementManager().selected), 0)

    async def test_reset_measure_properties(self):
        await create_one_measure()

        # Force the property UI show correctly else clicking does not work
        property_window = omni.ui.Workspace.get_window("Property")
        dock_space = omni.ui.Workspace.get_window("DockSpace")
        self.assertNotEqual(property_window, None)
        self.assertNotEqual(dock_space, None)
        property_window.dock_in(dock_space, omni.ui.DockPosition.LEFT, 0.35)
        await ui_test.wait_n_updates(1)

        property_ui = ui_test.find("Property//Frame/**/CollapsableFrame[*].title=='Measurement'")
        # use 2nd VStack
        property_ui = property_ui.find_all("**/VStack[*].identifier=='frame_v_stack'")[1]
        self.assertNotEqual(property_ui, None)

        # NOTE: This test relies on the assumption that we start in the default statte
        elems = (
            ("Axis Display", "control_state_measure:prop:axis_display", "WORLD"),
            ("Unit", "control_state_measure:prop:unit", "FEET"),
            ("Precision", "control_state_measure:prop:precision", "TENTH"),
            ("Label_Size", "control_state_measure:prop:label_size", "LARGE"),
        )
        for idc, prop_name, change_to in elems:
            combobox = property_ui.find(f"**/ComboBox[*].identifier=='{idc}'")
            reset_button = property_ui.find(f"**/ImageWithProvider[*].identifier=='{prop_name}'")
            self.assertNotEqual(combobox, None)
            self.assertNotEqual(reset_button, None)

            old_value = combobox.widget.model.get_value()
            combobox.widget.model.set_value(change_to)
            await ui_test.wait_n_updates(1)
            self.assertEqual(change_to, combobox.widget.model.get_value())
            await reset_button.click()
            await ui_test.wait_n_updates(1)
            self.assertEqual(old_value, combobox.widget.model.get_value())

        property_window.undock()

    async def test_reset_measure_color(self):
        await create_one_measure()

        measure_widget = ui_test.find("Property//Frame/**/CollapsableFrame[*].title=='Measurement'")
        color_widget = measure_widget.find("**/ColorWidget[*]")
        self.assertNotEqual(color_widget, None)
        old_value = color_widget.widget.model.get_value()
        color_widget.widget.scroll_here_y()  # make the button visible
        color_widget.widget.model.set_value(pxr.Gf.Vec4f(0.5, 0.5, 0.5, 1))
        rst_button = measure_widget.find("**/control_state_measure:prop:label_color")
        self.assertNotEqual(rst_button, None)
        await rst_button.click()
        await ui_test.wait_n_updates(1)
        self.assertEqual(old_value, color_widget.widget.model.get_value())

    async def test_reset_measure_display(self):
        from ..manager import StateMachine

        StateMachine().set_creation_state(MeasureMode.NONE)

        elems = (("Axis", 1), ("Units", 1), ("Precision", 1), ("LabelSize", 2))

        for name, val in elems:
            print(DISPLAY_UI_PATH + f"/**/{name}ResetBtn")
            rst_button = ui_test.find(f"{DISPLAY_UI_PATH}/**/{name}ResetBtn")
            self.assertNotEqual(rst_button, None)
            select = ui_test.find(f"{DISPLAY_UI_PATH}/**/{name}Combo")
            self.assertNotEqual(select, None)
            select.model.get_item_value_model().set_value(val)
            await rst_button.click()
            await ui_test.wait_n_updates(1)
            self.assertNotEqual(select.model.get_item_value_model().as_int, val)

        rst_button = ui_test.find(f"{DISPLAY_UI_PATH}/**/ColorResetBtn")
        self.assertNotEqual(rst_button, None)
        our_color = [0.5, 0.5, 0.5, 0.1]
        UserSettings().session.set_color_rgba(our_color)
        await ui_test.wait_n_updates(1)
        await rst_button.click()
        reset_color = UserSettings().session.color
        self.assertNotEqual(pxr.Gf.Vec4f(our_color), pxr.Gf.Vec4f(reset_color))
