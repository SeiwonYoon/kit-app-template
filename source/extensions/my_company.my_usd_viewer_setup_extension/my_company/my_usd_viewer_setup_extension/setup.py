# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

import asyncio
from pathlib import Path

import carb
import carb.settings
import carb.tokens
import omni.ext
import omni.kit.app
import omni.kit.imgui as _imgui
import omni.ui as ui  # ✅ Workspace.get_window
import omni.usd
from omni.kit.mainwindow import get_main_window
from omni.kit.quicklayout import QuickLayout
from omni.kit.viewport.utility import get_viewport_from_window_name

COMMAND_MACRO_SETTING = "/exts/omni.kit.command_macro.core/"
COMMAND_MACRO_FILE_SETTING = COMMAND_MACRO_SETTING + "macro_file"

# ✅ 레이아웃 로드 이후 "자동으로 띄우고 싶은" 창 타이틀 목록
AUTO_SHOW_WINDOWS = [
    "Section Control (Dummy UI)",
    "USD Loader",
    "Pick Filter",
    "Temp Alarm (hynix:temperature)"
]


async def _load_layout(layout_file: str, windows_to_show=None):
    """Loads a provided layout file and ensures the viewport is set to FILL.
    Additionally can force-show specific UI windows after layout restore.
    """
    await omni.kit.app.get_app().next_update_async()
    QuickLayout.load_file(layout_file)

    # Set viewport to FILL
    viewport_api = get_viewport_from_window_name("Viewport")
    if viewport_api and hasattr(viewport_api, "fill_frame"):
        viewport_api.fill_frame = True

    # ✅ 레이아웃 복원 직후 1프레임 더 기다린 뒤 창을 강제 표시
    if windows_to_show:
        await omni.kit.app.get_app().next_update_async()
        for title in windows_to_show:
            try:
                w = ui.Workspace.get_window(title)
                if w:
                    w.visible = True
                    w.focus()
                else:
                    carb.log_warn(f"SetupExtension: Window not found to show: '{title}'")
            except Exception as ex:
                carb.log_warn(f"SetupExtension: Failed to show window '{title}': {ex}")


class SetupExtension(omni.ext.IExt):
    """Extension that sets up the USD Viewer application."""

    def on_startup(self, _ext_id: str):
        """Called every time the extension is activated."""
        self._settings = carb.settings.get_settings()

        if self._settings and self._settings.get("/app/warmupMode"):
            # warmup 모드면 레이아웃/스테이지 로드 불필요
            return

        # get auto load stage name
        stage_url = self._settings.get_as_string("/app/auto_load_usd")

        # check if setup have benchmark macro file to activate - ignore setup
        # auto_load_usd name, in order to run proper benchmark.
        benchmark_macro_file_name = self._settings.get(COMMAND_MACRO_FILE_SETTING)
        if benchmark_macro_file_name:
            stage_url = None

        # if no benchmark is activated - load provided by setup stage.
        if stage_url:
            stage_url = carb.tokens.get_tokens_interface().resolve(stage_url)
            try:
                path = Path(stage_url)
                if path.exists():
                    stage_url = str(path.resolve())
            except (OSError, RuntimeError):
                # Keep original stage_url - it might be a valid URL or network path
                pass
            asyncio.ensure_future(self.__open_stage(stage_url))

        self._await_layout = asyncio.ensure_future(self._delayed_layout())
        get_main_window().get_main_menu_bar().visible = False

    async def _delayed_layout(self):
        """Delay layout loading until initial setup finishes."""
        main_menu_bar = get_main_window().get_main_menu_bar()
        main_menu_bar.visible = False

        # few frame delay to allow automatic Layout of window that want their own positions
        app = omni.kit.app.get_app()
        for _ in range(4):
            await app.next_update_async()  # type: ignore

        settings = carb.settings.get_settings()

        # setup the Layout for your app
        token = "${my_company.my_usd_viewer_setup_extension}/layouts"
        layouts_path = carb.tokens.get_tokens_interface().resolve(token)

        layout_name = settings.get("/app/layout/name")
        layout_file = Path(layouts_path).joinpath(f"{layout_name}.json")

        # ✅ 레이아웃 로드 후 특정 창 강제 표시
        # asyncio.ensure_future(_load_layout(f"{layout_file}", windows_to_show=AUTO_SHOW_WINDOWS))
        # 특정창 표시 하지 않기
        asyncio.ensure_future(_load_layout(f"{layout_file}", windows_to_show=None))

        # using imgui directly to adjust some color and Variable
        imgui = _imgui.acquire_imgui()

        # DockSplitterSize is the variable that drive the size of the Dock Split connection
        imgui.push_style_var_float(_imgui.StyleVar.DockSplitterSize, 2)

    async def __open_stage(self, url, frame_delay: int = 5):
        """Opens the provided USD stage and loads the render settings."""
        # default 5 frame delay to allow for Layout
        if frame_delay:
            app = omni.kit.app.get_app()
            for _ in range(frame_delay):
                await app.next_update_async()

        usd_context = omni.usd.get_context()

        count = 0
        timed_out = False
        # Wait until we can open the stage
        while not usd_context.can_open_stage():
            await omni.kit.app.get_app().next_update_async()
            count += 1
            if count > 100:
                timed_out = True
                break

        if not timed_out:
            await usd_context.open_stage_async(
                url, omni.usd.UsdContextInitialLoadSet.LOAD_ALL
            )
        else:
            carb.log_warn(f"SetupExtension: Timed out waiting to open stage {url}")
            return

        # If this was the first Usd data opened, explicitly restore render-settings now
        if not bool(self._settings.get("/app/content/emptyStageOnStart")):
            usd_context.load_render_settings_from_stage(usd_context.get_stage_id())

    def on_shutdown(self):
        """Called every time the extension is deactivated."""
        return
