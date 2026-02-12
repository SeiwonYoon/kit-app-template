# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# 실행 시 뷰포트만 보이도록 레이아웃을 로드합니다.
# Property 창과 Render Settings 창은 레이아웃에 포함되지 않아 표시되지 않습니다.

import asyncio
from pathlib import Path

import carb.tokens
import omni.ext
import omni.kit.app
import omni.ui as ui
from omni.kit.mainwindow import get_main_window
from omni.kit.quicklayout import QuickLayout
from omni.kit.viewport.utility import get_viewport_from_window_name


async def _load_viewport_only_layout(layout_file: str):
    """뷰포트만 포함한 레이아웃을 로드하고 뷰포트를 FILL로 설정합니다."""
    await omni.kit.app.get_app().next_update_async()
    QuickLayout.load_file(layout_file)

    viewport_api = get_viewport_from_window_name("Viewport")
    if viewport_api and hasattr(viewport_api, "fill_frame"):
        viewport_api.fill_frame = True


class SetupExtension(omni.ext.IExt):
    """Base 에디터 설정: 시작 시 뷰포트 전용 레이아웃 로드, 상단 메뉴 바 숨김."""

    def on_startup(self, _ext_id: str):
        # 상단 메뉴 바(File, Edit, Create, Window 등) 숨김
        try:
            get_main_window().get_main_menu_bar().visible = False
        except Exception:
            pass
        self._layout_task = asyncio.ensure_future(self._delayed_layout())

    async def _delayed_layout(self):
        """초기화가 끝난 뒤 레이아웃을 로드해 Property / Render Settings 창이 뜨지 않게 합니다."""
        app = omni.kit.app.get_app()
        for _ in range(5):
            await app.next_update_async()

        # 레이아웃 로드 후에도 메뉴 바 숨김 유지
        try:
            get_main_window().get_main_menu_bar().visible = False
        except Exception:
            pass

        token = "${morph.morph_base_viewer}/layouts"
        layouts_path = carb.tokens.get_tokens_interface().resolve(token)
        layout_file = Path(layouts_path).joinpath("viewport_only.json")
        await _load_viewport_only_layout(str(layout_file))

        # 하단 콘솔 창 숨김 (excluded만으로 안 숨겨질 때 런타임에 적용)
        try:
            console_win = ui.Workspace.get_window("Console")
            if console_win is not None:
                console_win.visible = False
        except Exception:
            pass

    def on_shutdown(self):
        if self._layout_task and not self._layout_task.done():
            self._layout_task.cancel()
        return
