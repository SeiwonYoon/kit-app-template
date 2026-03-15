# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""3D overlay: 선택된 prim 옆에 prim 정보 패널 표시 (show_info와 동일한 방식)."""

from typing import Any, Callable, List, Optional

import carb
import omni.kit.app as app
import omni.ui as ui
from omni.ui import scene as sc

from .prim_info import (
    CHAR_WIDTH,
    LINE_HEIGHT,
    PADDING_H,
    PADDING_V,
    get_prim_display_lines,
    get_prim_world_center,
)


def _post_update_once(callback):
    """다음 post_update 한 번에 callback 실행 후 구독 해제. 메인 스레드에서 패널을 다시 그릴 때 사용."""
    sub_ref = [None]

    def _on_event(_event):
        try:
            callback()
        finally:
            if sub_ref[0] is not None:
                sub_ref[0].unsubscribe()
                sub_ref[0] = None

    stream = app.get_app().get_post_update_event_stream()
    sub_ref[0] = stream.create_subscription_to_pop(
        _on_event, name="morph.tbs_control:ViewportOverlayPostUpdateOnce"
    )
    return sub_ref[0]


# Panel layout (show_info와 동일)
PANEL_OFFSET_SCREEN_PX = 400
FONT_SIZE_TITLE = 14
FONT_SIZE_BODY = 11
BG_COLOR = (0.12, 0.12, 0.14, 0.92)
BORDER_COLOR = (0.35, 0.35, 0.4, 1.0)
TEXT_COLOR = (0.95, 0.95, 0.95, 1.0)
TITLE_COLOR = (0.6, 0.85, 1.0, 1.0)
X_BUTTON_COLOR = (0.5, 0.25, 0.25, 0.95)
MAX_PANEL_WIDTH_PX = 420
MAX_PANEL_HEIGHT_PX = 380
CLOSE_BUTTON_SIZE = 22
CLOSE_BUTTON_OFFSET = 4
TITLE_BOTTOM_MARGIN = 10
CONTENT_AREA_HEIGHT_PX = MAX_PANEL_HEIGHT_PX - PADDING_V * 2 - CLOSE_BUTTON_SIZE
MAX_VISIBLE_LINES = max(1, CONTENT_AREA_HEIGHT_PX // LINE_HEIGHT)


class PrimInfoOverlay:
    """뷰포트에 선택된 prim 옆 3D 정보 패널을 그리며 갱신하는 오버레이 (show_info와 동일)."""

    def __init__(self, viewport_window: Any, ext_id: str):
        self._viewport_window = viewport_window
        self._ext_id = ext_id
        self._scene_view: Optional[sc.SceneView] = None
        self._panels_root: Optional[sc.Transform] = None
        self._built = False
        self._open_paths: List[str] = []
        self._on_close_cb: Optional[Callable[[str], None]] = None

    def set_open_paths(self, paths: List[str]) -> None:
        self._open_paths = list(paths)

    def set_on_close(self, cb: Callable[[str], None]) -> None:
        self._on_close_cb = cb

    def build_scene(self) -> None:
        with self._viewport_window.get_frame(self._ext_id):
            with ui.ZStack():
                self._scene_view = sc.SceneView()
                with self._scene_view.scene:
                    self._panels_root = sc.Transform()
            self._viewport_window.viewport_api.add_scene_view(self._scene_view)
        self._built = True

    def _rebuild_panels(self) -> None:
        if not self._built or not self._panels_root:
            return
        self._panels_root.clear()

        try:
            import omni.usd as ou
            ctx = ou.get_context()
        except Exception as e:
            carb.log_warn(f"[morph.tbs_control] _rebuild_panels: get_context 실패: {e}")
            return
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return

        paths = self._open_paths
        if not paths:
            return

        with self._panels_root:
            for path_str in paths:
                if not path_str:
                    continue
                prim = stage.GetPrimAtPath(path_str)
                if not prim or not prim.IsValid():
                    continue
                pos = get_prim_world_center(prim)
                if pos is None:
                    continue
                try:
                    lines = get_prim_display_lines(prim)
                except Exception:
                    continue
                if not lines:
                    continue
                self._build_one_panel(pos, path_str, lines)

    def _build_one_panel(self, world_pos: tuple, path_str: str, lines: List[str]) -> None:
        content_area_w = MAX_PANEL_WIDTH_PX - PADDING_H * 2
        max_chars_per_line = max(1, content_area_w // CHAR_WIDTH)
        if len(lines) > MAX_VISIBLE_LINES:
            lines = lines[: MAX_VISIBLE_LINES - 1] + [
                f"... ({len(lines) - (MAX_VISIBLE_LINES - 1)} more lines)"
            ]
        truncated_lines: List[str] = []
        for line in lines:
            if len(line) <= max_chars_per_line:
                truncated_lines.append(line)
            else:
                truncated_lines.append(line[: max_chars_per_line - 3] + "...")
        lines = truncated_lines
        num_lines = len(lines)
        content_w = min(max_chars_per_line * CHAR_WIDTH, content_area_w)
        content_h = num_lines * LINE_HEIGHT + TITLE_BOTTOM_MARGIN
        panel_w = min(content_w + PADDING_H * 2, MAX_PANEL_WIDTH_PX)
        panel_h = min(
            PADDING_V * 2 + CLOSE_BUTTON_SIZE + content_h,
            MAX_PANEL_HEIGHT_PX,
        )

        on_close = self._on_close_cb
        path_to_close = path_str

        def _close(_sender=None):
            if on_close:
                on_close(path_to_close)

        close_gesture = sc.ClickGesture(name="morph.tbs_control:ClosePanel", on_ended_fn=_close)

        root = sc.Transform(
            look_at=sc.Transform.LookAt.CAMERA,
            transform=sc.Matrix44.get_translation_matrix(*world_pos),
        )
        with root:
            with sc.Transform(scale_to=sc.Space.SCREEN):
                with sc.Transform(transform=sc.Matrix44.get_translation_matrix(PANEL_OFFSET_SCREEN_PX, 0, 0)):
                    sc.Rectangle(
                        width=panel_w,
                        height=panel_h,
                        color=BG_COLOR,
                        wireframe=False,
                    )
                    sc.Rectangle(
                        width=panel_w,
                        height=panel_h,
                        color=BORDER_COLOR,
                        wireframe=True,
                    )
                    btn_x = panel_w // 2 - CLOSE_BUTTON_OFFSET - CLOSE_BUTTON_SIZE // 2
                    btn_y = panel_h // 2 - CLOSE_BUTTON_OFFSET - CLOSE_BUTTON_SIZE // 2
                    with sc.Transform(transform=sc.Matrix44.get_translation_matrix(btn_x, btn_y, 0)):
                        sc.Rectangle(
                            width=CLOSE_BUTTON_SIZE,
                            height=CLOSE_BUTTON_SIZE,
                            color=X_BUTTON_COLOR,
                            wireframe=False,
                            gesture=close_gesture,
                        )
                        sc.Label("X", size=12, color=(1, 1, 1, 1), alignment=ui.Alignment.CENTER)
                    content_left = -panel_w // 2 + PADDING_H
                    top_y = panel_h // 2 - PADDING_V - CLOSE_BUTTON_SIZE
                    for i, line in enumerate(lines):
                        y = top_y - (i + 0.5) * LINE_HEIGHT
                        if i >= 1:
                            y -= TITLE_BOTTOM_MARGIN
                        with sc.Transform(transform=sc.Matrix44.get_translation_matrix(content_left, y, 0)):
                            is_title = i == 0
                            size = FONT_SIZE_TITLE if is_title else FONT_SIZE_BODY
                            color = TITLE_COLOR if is_title else TEXT_COLOR
                            sc.Label(
                                line,
                                size=size,
                                color=color,
                                alignment=ui.Alignment.LEFT_CENTER,
                            )

    def update_panels(self) -> None:
        _post_update_once(self._rebuild_panels)

    def destroy(self) -> None:
        if self._scene_view and self._viewport_window:
            try:
                self._viewport_window.viewport_api.remove_scene_view(self._scene_view)
            except Exception:
                pass
        self._scene_view = None
        self._panels_root = None
        self._built = False
