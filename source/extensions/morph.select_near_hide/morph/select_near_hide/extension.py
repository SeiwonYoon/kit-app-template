# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Main extension entrypoint for hide/semi-transparent/occlusion modes."""

import asyncio
import time

import carb
import omni.ext
import omni.kit.app
import omni.ui as ui
import omni.usd
from carb.eventdispatcher import get_eventdispatcher

try:
    from omni.kit.viewport.utility import frame_viewport_selection, get_active_viewport
except ImportError:
    frame_viewport_selection = None
    get_active_viewport = None

from morph.select_near_hide.hide import (
    HIDE_RADIUS_METERS,
    apply_hide,
    filter_paths_by_distance,
    get_occlusion_candidates_up_to_common_parent,
    get_sibling_paths_with_ancestor_fallback,
    restore_prims,
)
from morph.select_near_hide.occlusion_hide import (
    collect_occlusion_prim_paths_sibling,
    get_camera_world_position,
)
from morph.select_near_hide.semi_transparent import (
    TRANSPARENT_RADIUS_METERS,
    apply_transparent,
)


MODE_HIDE = "hide"
MODE_TRANSPARENT = "transparent"
MODE_OCCLUSION_HIDE = "occlusion_hide"
OCCLUSION_UPDATE_INTERVAL_SEC = 0.2


def _log_info(message: str):
    carb.log_info(message)
    print(message, flush=True)


def _log_warn(message: str):
    carb.log_warn(message)
    print(message, flush=True)


def some_public_function(x: int):
    """Public sample function used by extension tests."""
    return x**x


class MyExtension(omni.ext.IExt):
    """Hide sibling prims or apply GhostPBR around selected prims."""

    def on_startup(self, _ext_id):
        self._subscriptions = []
        self._window = None
        self._window_task = None
        self._selection_task = None

        self._enabled = False
        self._mode = MODE_HIDE
        self._last_affected_saved: dict[str, dict] = {}
        self._bound_material_cache: dict[str, str] = {}
        self._root_bbox_cache: dict[str, object] = {}
        self._last_selection_key = ()
        self._last_mode_key = self._mode
        self._occlusion_update_sub = None
        self._last_occlusion_camera_pos: tuple[float, float, float] | None = None
        self._last_occlusion_update_time: float = 0.0

        usd_context = omni.usd.get_context()
        ed = get_eventdispatcher()
        self._subscriptions.append(
            ed.observe_event(
                observer_name="SelectNearHide:SelectionChanged",
                event_name=usd_context.stage_event_name(omni.usd.StageEventType.SELECTION_CHANGED),
                on_event=self._on_selection_changed,
            )
        )
        self._subscriptions.append(
            ed.observe_event(
                observer_name="SelectNearHide:StageOpened",
                event_name=usd_context.stage_event_name(omni.usd.StageEventType.OPENED),
                on_event=self._on_stage_opened,
            )
        )

        self._window_task = asyncio.ensure_future(self._create_window_late())
        _log_info("[morph.select_near_hide] Extension startup")

    def _on_stage_opened(self, _event):
        self._stop_occlusion_camera_tracking()
        self._last_affected_saved.clear()
        self._bound_material_cache.clear()
        self._root_bbox_cache.clear()
        self._last_selection_key = ()

    def _on_selection_changed(self, _event):
        if self._selection_task and not self._selection_task.done():
            self._selection_task.cancel()
        self._selection_task = asyncio.ensure_future(self._process_selection_changed_debounced())

    async def _process_selection_changed_debounced(self):
        try:
            await asyncio.sleep(0.12)
        except asyncio.CancelledError:
            return
        self._process_selection_changed()

    def _process_selection_changed(self):
        if not self._enabled:
            return

        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return
        sel = ctx.get_selection() if ctx else None
        if not sel:
            return
        session_layer = stage.GetSessionLayer()
        if not session_layer:
            return

        selected_paths = sel.get_selected_prim_paths()
        if not selected_paths:
            self._stop_occlusion_camera_tracking()
            if self._last_affected_saved:
                _log_info(
                    f"[morph.select_near_hide] restoring previous overrides: {len(self._last_affected_saved)} item(s)"
                )
                restore_prims(stage, session_layer, self._last_affected_saved)
                self._last_affected_saved.clear()
            _log_info("[morph.select_near_hide] selection empty -> nothing to apply")
            self._last_selection_key = ()
            return

        selection_key = tuple(sorted(selected_paths))
        mode_changed = self._mode != self._last_mode_key
        if selection_key == self._last_selection_key and not mode_changed:
            return

        if mode_changed:
            self._stop_occlusion_camera_tracking()
        if mode_changed and self._last_affected_saved:
            _log_info(
                f"[morph.select_near_hide] mode changed -> restoring previous overrides: {len(self._last_affected_saved)} item(s)"
            )
            restore_prims(stage, session_layer, self._last_affected_saved)
            self._last_affected_saved.clear()

        if self._mode == MODE_HIDE:
            if self._last_affected_saved:
                _log_info(
                    f"[morph.select_near_hide] restoring previous overrides: {len(self._last_affected_saved)} item(s)"
                )
                restore_prims(stage, session_layer, self._last_affected_saved)
                self._last_affected_saved.clear()

            siblings = get_occlusion_candidates_up_to_common_parent(stage, selected_paths)
            if not siblings:
                _log_info("[morph.select_near_hide] no candidates found (up to common parent)")
                return

            near_only = filter_paths_by_distance(stage, siblings, selected_paths, HIDE_RADIUS_METERS)
            _log_info(
                f"[morph.select_near_hide] selection changed: mode={self._mode}, "
                f"selected={len(selected_paths)}, siblings={len(siblings)}, "
                f"within {HIDE_RADIUS_METERS}m={len(near_only)}"
            )
            self._last_affected_saved = apply_hide(stage, session_layer, near_only)
            _log_info(
                f"[morph.select_near_hide] Applied hide to {len(self._last_affected_saved)} sibling(s) "
                f"(distant prims excluded)"
            )
        elif self._mode == MODE_OCCLUSION_HIDE:
            if self._last_affected_saved:
                _log_info(
                    f"[morph.select_near_hide] restoring previous overrides: {len(self._last_affected_saved)} item(s)"
                )
                restore_prims(stage, session_layer, self._last_affected_saved)
                self._last_affected_saved.clear()

            candidates = get_occlusion_candidates_up_to_common_parent(stage, selected_paths)
            if not candidates:
                _log_info("[morph.select_near_hide] no occlusion candidates found (up to common parent)")
                return

            paths = collect_occlusion_prim_paths_sibling(stage, selected_paths, candidates)
            if not paths:
                _log_info("[morph.select_near_hide] no occlusion prims found (camera or selected center unavailable)")
                return

            _log_info(
                f"[morph.select_near_hide] selection changed: mode={self._mode}, "
                f"selected={len(selected_paths)}, occlusion_prims={len(paths)}"
            )
            self._last_affected_saved = apply_hide(stage, session_layer, paths)
            _log_info(
                f"[morph.select_near_hide] Applied occlusion hide to {len(self._last_affected_saved)} prim(s)"
            )
            cam_pos = get_camera_world_position(stage)
            self._last_occlusion_camera_pos = (
                (float(cam_pos[0]), float(cam_pos[1]), float(cam_pos[2])) if cam_pos else None
            )
            self._start_occlusion_camera_tracking()
        else:
            siblings = get_sibling_paths_with_ancestor_fallback(stage, selected_paths)
            if not siblings:
                _log_info("[morph.select_near_hide] no sibling/ancestor-sibling found for current selection")
                return

            _log_info(
                f"[morph.select_near_hide] selection changed: mode={self._mode}, "
                f"selected={len(selected_paths)}, siblings={len(siblings)}, radius={TRANSPARENT_RADIUS_METERS}"
            )
            self._last_affected_saved = apply_transparent(
                stage,
                session_layer,
                selected_paths,
                siblings,
                self._bound_material_cache,
                self._root_bbox_cache,
                self._last_affected_saved if not mode_changed else {},
            )
            _log_info(
                f"[morph.select_near_hide] Applied transparent(within radius) "
                f"to {len(self._last_affected_saved)} prim(s)"
            )

        self._last_selection_key = selection_key
        self._last_mode_key = self._mode

    def _start_occlusion_camera_tracking(self):
        if self._occlusion_update_sub is not None:
            return
        update_stream = omni.kit.app.get_app().get_update_event_stream()
        self._occlusion_update_sub = update_stream.create_subscription_to_pop(
            self._on_update_for_occlusion,
            name="SelectNearHide:OcclusionCameraUpdate",
        )

    def _stop_occlusion_camera_tracking(self):
        if self._occlusion_update_sub is None:
            return
        try:
            self._occlusion_update_sub.unsubscribe()
        except Exception:
            pass
        self._occlusion_update_sub = None
        self._last_occlusion_camera_pos = None
        self._last_occlusion_update_time = 0.0

    def _on_update_for_occlusion(self, _event):
        if not self._enabled or self._mode != MODE_OCCLUSION_HIDE:
            return
        now = time.perf_counter()
        if now - self._last_occlusion_update_time < OCCLUSION_UPDATE_INTERVAL_SEC:
            return
        self._last_occlusion_update_time = now

        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return
        sel = ctx.get_selection() if ctx else None
        if not sel:
            return

        selected_paths = sel.get_selected_prim_paths()
        if not selected_paths:
            return

        cam_pos = get_camera_world_position(stage)
        if cam_pos is None:
            return

        cam_tuple = (float(cam_pos[0]), float(cam_pos[1]), float(cam_pos[2]))
        eps = 1e-5
        if self._last_occlusion_camera_pos is not None:
            if all(abs(cam_tuple[i] - self._last_occlusion_camera_pos[i]) < eps for i in range(3)):
                return
        self._last_occlusion_camera_pos = cam_tuple

        session_layer = stage.GetSessionLayer()
        if not session_layer:
            return

        if self._last_affected_saved:
            restore_prims(stage, session_layer, self._last_affected_saved)
            self._last_affected_saved.clear()

        candidates = get_occlusion_candidates_up_to_common_parent(stage, selected_paths)
        if not candidates:
            return
        paths = collect_occlusion_prim_paths_sibling(stage, selected_paths, candidates)
        if not paths:
            return
        self._last_affected_saved = apply_hide(stage, session_layer, paths)

    def _frame_selection(self):
        if frame_viewport_selection is None or get_active_viewport is None:
            _log_warn("[morph.select_near_hide] frame_viewport_selection not available")
            return
        viewport = get_active_viewport()
        if viewport:
            frame_viewport_selection(viewport)

    def _restore_all(self):
        self._stop_occlusion_camera_tracking()
        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return
        session_layer = stage.GetSessionLayer()
        if not session_layer or not self._last_affected_saved:
            _log_info("[morph.select_near_hide] restore_all skipped: nothing to restore")
            return
        _log_info(f"[morph.select_near_hide] restore_all: {len(self._last_affected_saved)} item(s)")
        restore_prims(stage, session_layer, self._last_affected_saved)
        self._last_affected_saved.clear()
        _log_info("[morph.select_near_hide] Restored all affected prims")

    async def _create_window_late(self):
        app = omni.kit.app.get_app()
        for _ in range(15):
            await app.next_update_async()

        self._window = ui.Window(
            title="Select Near Hide",
            width=380,
            height=160,
            dock_preference=ui.DockPreference.RIGHT_TOP,
        )

        with self._window.frame:
            with ui.VStack(spacing=4, style={"margin": 2}, height=0):
                def on_enabled_changed(model):
                    self._enabled = model.get_value_as_bool()
                    if not self._enabled:
                        self._stop_occlusion_camera_tracking()
                        if self._last_affected_saved:
                            self._restore_all()

                enabled_model = ui.SimpleBoolModel(False)
                enabled_model.add_value_changed_fn(on_enabled_changed)
                with ui.HStack(height=20, spacing=0):
                    ui.CheckBox(model=enabled_model, name="Enable Select Near Hide")
                    ui.Label("Enable Select Near Hide", style={"font_size": 12}, width=0)
                    ui.Spacer()

                with ui.HStack(height=20, spacing=4):
                    ui.Label("Mode:", width=44, style={"font_size": 12})

                    def set_mode_hide():
                        self._mode = MODE_HIDE
                        self._refresh_mode_buttons()

                    def set_mode_transparent():
                        self._mode = MODE_TRANSPARENT
                        self._refresh_mode_buttons()

                    def set_mode_occlusion_hide():
                        self._mode = MODE_OCCLUSION_HIDE
                        self._refresh_mode_buttons()

                    self._btn_hide = ui.Button("Hide", clicked_fn=set_mode_hide, width=70)
                    self._btn_transparent = ui.Button("Semi-Transparent", clicked_fn=set_mode_transparent, width=110)
                    self._btn_occlusion_hide = ui.Button("Occlusion Hide", clicked_fn=set_mode_occlusion_hide, width=100)
                    ui.Spacer()

                def refresh_mode_buttons():
                    sel = {"background_color": 0xFF4A90E2}
                    unsel = {"background_color": 0xFF2B2B2B}
                    self._btn_hide.style = sel if self._mode == MODE_HIDE else unsel
                    self._btn_transparent.style = sel if self._mode == MODE_TRANSPARENT else unsel
                    self._btn_occlusion_hide.style = sel if self._mode == MODE_OCCLUSION_HIDE else unsel

                self._refresh_mode_buttons = refresh_mode_buttons
                self._refresh_mode_buttons()

                ui.Label(
                    f"Transparent range: within {int(TRANSPARENT_RADIUS_METERS)} from selected prim",
                    style={"font_size": 11},
                    height=18,
                )

                with ui.HStack(spacing=4, height=22):
                    ui.Button(
                        "Restore All",
                        clicked_fn=lambda: self._restore_all(),
                        tooltip="Restore hidden/transparent prims to original state",
                        width=0,
                    )
                    ui.Button(
                        "Frame",
                        clicked_fn=lambda: self._frame_selection(),
                        tooltip="Move camera to selection center (same as F key)",
                        width=70,
                    )

        try:
            if hasattr(self._window, "undock"):
                self._window.undock()
        except Exception as e:
            _log_warn(f"[morph.select_near_hide] undock failed: {e}")

    def on_shutdown(self):
        self._stop_occlusion_camera_tracking()
        if self._selection_task is not None:
            self._selection_task.cancel()
            self._selection_task = None
        if self._window_task is not None:
            self._window_task.cancel()
            self._window_task = None

        if self._last_affected_saved:
            try:
                ctx = omni.usd.get_context()
                stage = ctx.get_stage() if ctx else None
                if stage:
                    session_layer = stage.GetSessionLayer()
                    if session_layer:
                        restore_prims(stage, session_layer, self._last_affected_saved)
            except Exception:
                pass

        for sub in self._subscriptions:
            if sub is not None and hasattr(sub, "release"):
                sub.release()
        self._subscriptions = []

        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None

        _log_info("[morph.select_near_hide] Extension shutdown")
