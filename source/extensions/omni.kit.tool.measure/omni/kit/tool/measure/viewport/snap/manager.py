# Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.
#

from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import omni.kit.raycast.query
from omni.kit.viewport.utility import get_active_viewport_window
from pxr import Sdf

from ...common import MeasureMode, MeasureState, SnapMode, SnapTo
from ...manager import ReferenceManager, StateMachine
from .mesh_provider import (
    CenterSnapProvider,
    EdgeSnapProvider,
    MidPointSnapProvider,
    PivotSnapProvider,
    SurfaceSnapProvider,
    VertexSnapProvider,
)
from .provider import MeasureSnapProvider
from .registry import MeasureSnapProviderRegistry


class MeasureSnapProviderManager:
    def __singleton_init__(self):
        self._enabled: bool = False
        self._window = get_active_viewport_window()
        self._api = self._window.viewport_api if self._window else None

        self._registry: MeasureSnapProviderRegistry = MeasureSnapProviderRegistry()

        self._providers: Dict[str, MeasureSnapProvider] = {}
        self._enabled_providers: List[MeasureSnapProvider] = []
        self._provider_registry_sub = self._registry.add_on_registry_changed_fn(self._on_registry_changed)
        self._enabled_providers_sub = None  # TODO Create event for when providers change in the UI panel

        self._registry.register(CenterSnapProvider)
        self._registry.register(PivotSnapProvider)
        self._registry.register(EdgeSnapProvider)
        self._registry.register(MidPointSnapProvider)
        self._registry.register(SurfaceSnapProvider)
        self._registry.register(VertexSnapProvider)

        self._on_registry_changed()

        # State machine Callbacks
        self._state_sub = StateMachine().add_tool_state_changed_fn(self._on_state_changed)

        # UI panel Callbacks
        placement_panel = ReferenceManager().ui_placement_panel
        if placement_panel and placement_panel.snap_group:
            placement_panel.snap_group.add_on_snaps_changed_fn(self._on_ui_snaps_changed)

    def __new__(cls, *args, **kwargs):
        if not hasattr(cls, "_instance"):
            cls._instance = super().__new__(cls, *args, **kwargs)
            cls._instance.__singleton_init__()
        return cls._instance

    def __del__(self):
        self.destroy()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value
        snaps = ReferenceManager().ui_placement_panel.snap_group.snaps
        self._on_ui_snaps_changed(snaps)

    def destroy(self):
        if self._provider_registry_sub:
            self._registry.remove_on_registry_changed_fn(self._provider_registry_sub)
            self._provider_registry_sub = None
        if self._enabled_providers_sub:
            # TODO: Unsubscribe to event tracking enabled providers in the UI panel
            self._enabled_providers_sub = None
        if self._value_cache is not None:
            self._value_cache.destroy()
            self._value_cache = None

    def _on_state_changed(self, state: MeasureState, mode: MeasureMode):
        self.enabled = state != MeasureState.NONE

    def _on_ui_snaps_changed(self, snap_modes: List[SnapMode]) -> None:
        self._update_enabled_providers(snap_modes)

    def _on_registry_changed(self):
        for provider in self._providers.values():
            provider.destroy()
        self._providers.clear()

        providers = self._registry.providers
        for name, provider_class in providers.items():
            self._providers[name] = provider_class(viewport_api=self._api)

        # self._update_enabled_providers()

    def on_began(self, excluded_paths: List[Union[str, Sdf.Path]], **kwargs):
        for provider in self._enabled_providers:
            provider.on_began(excluded_paths, **kwargs)

    def on_ended(self, **kwargs):
        for provider in self._enabled_providers:
            provider.on_ended(**kwargs)

    def get_snap_position(
        self, ndc_location: Sequence[float], result: omni.kit.raycast.query.RayQueryResult
    ) -> Optional[Dict[str, Any]]:
        """
        Runs through all the snap providers and returns first valid snap data payload or None.

        Args:
            ndc_location (Sequence[float]): Location of the cursor in NDC space.

        Returns:
            (Dict[str, Any] or None): Snap data payload or None if no valid snap found.
        """

        for provider in self._enabled_providers:
            # To allow for only the surface snap to go through if PERPENDICUALR mode is active
            snap_to_mode = ReferenceManager().ui_placement_panel.snap_to
            if snap_to_mode == SnapTo.PERPENDICULAR and not isinstance(provider, SurfaceSnapProvider):
                continue

            # Initialize updated raycast rate
            provider.on_began([])

            snap_data: Tuple[bool, Optional[Dict[str, Any]]] = provider.on_snap(
                ndc_location, result, want_orient=isinstance(provider, SurfaceSnapProvider), want_keep_spacing=True
            )

            # Reset raycast rate to original value
            provider.on_ended()
            if snap_data[0]:  # If the snap returns TRUE, as it has found an element to snap to
                # return at first provider results
                return snap_data[1]
        return None

    def _on_enabled_providers_changed(self):
        pass
        # self._update_enabled_providers()

    def _update_enabled_providers(self, snap_modes: List[SnapMode]) -> None:
        if not self.enabled:
            self._enabled_providers = []
            return

        mode_names = [snap.name.title() for snap in snap_modes]
        self._enabled_providers = [p for p in self._providers.values() if p and p.get_display_name() in mode_names]
        self._enabled_providers.sort(key=lambda provider: provider.get_order())
