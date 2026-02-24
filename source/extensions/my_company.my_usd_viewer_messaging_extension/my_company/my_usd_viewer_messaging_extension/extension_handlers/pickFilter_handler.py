
import carb
import carb.events
import omni.usd
from pxr import UsdGeom
from morph.pick_filter.service import ensure_service
from typing import Dict, Callable, List

from .base_handler import BaseHandler

class PickFilterHandler(BaseHandler):
    """pick_filter 익스텐션과의 메시지 통신을 처리하는 클래스"""
    def __init__(self):
        print("[PickFilterHandler] initialized")
        self._pick_filter_service = ensure_service()
        super().__init__()

    def get_outgoing_events(self) -> List[str]:
        """클라이언트로 보낼 이벤트 리스트"""
        return [
            # Children
            "getChildrenResponse",
            # Cache
            "getCacheRevisionResponse",
            "getCachedItemsResponse",
            "refreshCacheResponse",
            # Pickable
            "setPickableResponse",
            "setPickableBulkResponse",
            "lockAllResponse",
            "unlockAllResponse",
            # Temperature
            "getTemperatureResponse",
            "setTemperatureResponse",
            # Viewport Selection
            "getViewportSelectionEnabledResponse",
            "setViewportSelectionEnabledResponse",
            "toggleViewportSelectionResponse",
            # Frame
            "framePrimResponse",
            "framePrimsResponse",
            # Selection
            "getSelectionResponse",
            "clearSelectionResponse",
            "setSelectionResponse",
            "addToSelectionResponse",
            # Group
            "listGroupsResponse",
            "getGroupMembersResponse",
            "selectGroupResponse",

            # Leaf Name
            "selectByLeafNamesResponse",
            "clearSelectionByLeafNamesResponse",
            "setPickableByLeafNamesResponse",

            # Visibility
            "setVisibilityResponse",
            "getVisibilityResponse"
        ]

    def get_event_handlers(self) -> Dict[str, Callable]:
        """이벤트 핸들러 맵 반환"""
        return {
            # Children
            'requestGetChildren': self._on_get_children,
            # Pickable
            'setPickable': self._on_set_pickable,
            'setPickableBulk': self._on_set_pickable_bulk,
            'lockAll': self._on_lock_all,
            'unlockAll': self._on_unlock_all,
            # Cache
            'getCacheRevision': self._on_get_cache_revision,
            'getCachedItems': self._on_get_cached_items,
            'refreshCache': self._on_refresh_cache,
            # Temperature
            'getTemperature': self._on_get_temperature,
            'setTemperature': self._on_set_temperature,
            # Viewport Selection
            'getViewportSelectionEnabled': self._on_get_viewport_selection_enabled,
            'setViewportSelectionEnabled': self._on_set_viewport_selection_enabled,
            'toggleViewportSelection': self._on_toggle_viewport_selection,
            # Frame
            'framePrim': self._on_frame_prim,
            'framePrims': self._on_frame_prims,
            # Selection
            'getSelection': self._on_get_selection,
            'clearSelection': self._on_clear_selection,
            'setSelection': self._on_set_selection,
            'addToSelection': self._on_add_to_selection,
            # Group
            'listGroups': self._on_list_groups,
            'getGroupMembers': self._on_get_group_members,
            'selectGroup': self._on_select_group,
            # Leaf Name
            'selectByLeafNames': self._on_select_by_leaf_names,
            'clearSelectionByLeafNames': self._on_clear_selection_by_leaf_names,
            'setPickableByLeafNames': self._on_set_pickable_by_leaf_names,

            # Visibility
            'setVisibility': self._on_set_visibility,
            'getVisibility': self._on_get_visibility,
        }

    # ──────────────────────────────────────────
    # Visibility
    # ──────────────────────────────────────────
    def _on_set_visibility(self, event: carb.events.IEvent) -> None:
        """Handler for the `setVisibility` event – set visibility state for a single prim."""
        print(f"[PickFilterHandler] _on_set_visibility event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        p = event.payload
        path = p.get("path")
        visible = bool(p.get("visible", True))
        include_descendants = bool(p.get("include_descendants", True))

        svc.set_mesh_enabled(path, visible, include_descendants=include_descendants)
        self.dispatch_event("setVisibilityResponse", {"path": path, "visible": visible, "success": True})

    def _on_get_visibility(self, event: carb.events.IEvent) -> None:
        """Handler for the `getVisibility` event – get visibility state for a single prim."""
        print(f"[PickFilterHandler] _on_get_visibility event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        path = event.payload.get("path") or event.payload.get("prim_path", "")
        visible = svc.get_mesh_enabled(path)
        self.dispatch_event("getVisibilityResponse", {"path": path, "visible": visible})

    # ──────────────────────────────────────────
    # Children
    # ──────────────────────────────────────────

    def _on_get_children(self, event: carb.events.IEvent) -> None:
        """
        Handler for the `requestGetChildren` event.
        Collects a filtered collection of a given primitive's children.
        """
        carb.log_info("Received message to return list of a prim's children")
        max_depth = event.payload.get("max_depth", None)
        print(f"[PickFilterHandler] event_payload: {event.payload}")
        children = self.get_children(
            prim_path=event.payload["prim_path"],
            filters=event.payload.get("filters"),
            max_depth=max_depth
        )
        payload = {
            "prim_path": event.payload["prim_path"],
            "children": children
        }
        print(f"[PickFilterHandler] children: {children}")
        self.dispatch_event("getChildrenResponse", payload=payload)
        print(f"[PickFilterHandler] dispatched event")

    def get_children(self, prim_path, filters=None, max_depth=None):
        """
        Collect any children of the given `prim_path`, potentially filtered by `filters`.

        Args:
            prim_path: Path to the prim to get children from
            filters: Optional filter types to apply
            max_depth: Optional maximum depth to recurse. If None, only returns 1 depth.
                      If specified, recursively collects children up to that depth.

        Returns:
            List of child primitives with their information
        """
        print(f"max_depth: {max_depth}")
        return self._get_children_recursive(prim_path, filters, max_depth, current_depth=0)

    def _get_children_recursive(self, prim_path, filters=None, max_depth=1, current_depth=0):
        """
        Recursively collect children up to max_depth.

        Args:
            prim_path: Path to the prim to get children from
            filters: Optional filter types to apply
            max_depth: Maximum depth to recurse
            current_depth: Current recursion depth (internal use)

        Returns:
            List of child primitives with their nested children
        """
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(prim_path)
        if not prim:
            return []

        filter_types = {
            "USDGeom": UsdGeom.Mesh,
            "mesh": UsdGeom.Mesh,
            "xform": UsdGeom.Xform,
            "scope": UsdGeom.Scope,
        }

        children = []
        for child in prim.GetChildren():
            if filters is not None:
                if isinstance(filters, carb.dictionary.Item):
                    filters = filters.get_dict()
                if not any(child.IsA(filter_types[filt]) for filt in filters if filt in filter_types):
                    continue

            child_name = child.GetName()
            child_path = str(prim.GetPath())
            if child_name.startswith('OmniverseKit_'):
                continue
            if prim_path == '/' and child_name == 'Render':
                continue
            child_path = child_path if child_path != '/' else ''
            carb.log_info(f'child_path: {child_path}')
            child_full_path = f'{child_path}/{child_name}'
            info = {"name": child_name, "path": child_full_path}

            if child.GetChildren() and current_depth < max_depth - 1:
                info["children"] = self._get_children_recursive(
                    child_full_path, filters, max_depth, current_depth + 1
                )
            elif child.GetChildren():
                info["children"] = []

            children.append(info)

        return children

    # ──────────────────────────────────────────
    # Pickable API
    # ──────────────────────────────────────────

    def _on_set_pickable(self, event: carb.events.IEvent) -> None:
        """Handler for the `setPickable` event – set pickable state for a single prim."""
        print(f"[PickFilterHandler] _on_set_pickable event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        p = event.payload
        path = p.get("path") or p.get("prim_path", "")
        pickable = bool(p.get("pickable", True))
        include_descendants = bool(p.get("include_descendants", True))

        svc.set_pickable(path, pickable, include_descendants=include_descendants)
        self.dispatch_event("setPickableResponse", {"path": path, "pickable": pickable, "success": True})

    def _on_set_pickable_bulk(self, event: carb.events.IEvent) -> None:
        """Handler for the `setPickableBulk` event – set pickable state for multiple prims."""
        print(f"[PickFilterHandler] _on_set_pickable_bulk event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        p = event.payload
        paths = p.get("paths", [])
        if isinstance(paths, carb.dictionary.Item):
            paths = paths.get_dict()
        pickable = bool(p.get("pickable", True))

        svc.set_pickable_bulk(paths, pickable)
        self.dispatch_event("setPickableBulkResponse", {"paths": paths, "pickable": pickable, "success": True})

    def _on_lock_all(self, event: carb.events.IEvent) -> None:
        """Handler for the `lockAll` event – disable pick for all prims."""
        print(f"[PickFilterHandler] _on_lock_all")

        svc = self._ensure_service()
        if svc is None:
            return

        svc.lock_all()
        self.dispatch_event("lockAllResponse", {"success": True})

    def _on_unlock_all(self, event: carb.events.IEvent) -> None:
        """Handler for the `unlockAll` event – enable pick for all prims."""
        print(f"[PickFilterHandler] _on_unlock_all")

        svc = self._ensure_service()
        if svc is None:
            return

        svc.unlock_all()
        self.dispatch_event("unlockAllResponse", {"success": True})

    # ──────────────────────────────────────────
    # Cache API
    # ──────────────────────────────────────────

    def _on_get_cache_revision(self, event: carb.events.IEvent) -> None:
        """Handler for the `getCacheRevision` event – return current cache revision."""
        print(f"[PickFilterHandler] _on_get_cache_revision")

        svc = self._ensure_service()
        if svc is None:
            return

        revision = svc.get_revision()
        self.dispatch_event("getCacheRevisionResponse", {"revision": revision})

    def _on_get_cached_items(self, event: carb.events.IEvent) -> None:
        """Handler for the `getCachedItems` event – return cached prim list."""
        print(f"[PickFilterHandler] _on_get_cached_items")

        svc = self._ensure_service()
        if svc is None:
            return

        items = svc.get_items_cached()
        self.dispatch_event("getCachedItemsResponse", {"items": items})

    def _on_refresh_cache(self, event: carb.events.IEvent) -> None:
        """Handler for the `refreshCache` event – rescan stage and refresh cache."""
        print(f"[PickFilterHandler] _on_refresh_cache")

        svc = self._ensure_service()
        if svc is None:
            return

        items = svc.refresh_cache()
        self.dispatch_event("refreshCacheResponse", {"items": items})

    # ──────────────────────────────────────────
    # Temperature API
    # ──────────────────────────────────────────

    def _on_get_temperature(self, event: carb.events.IEvent) -> None:
        """Handler for the `getTemperature` event – retrieve temperature metadata."""
        print(f"[PickFilterHandler] _on_get_temperature event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        path = event.payload.get("path") or event.payload.get("prim_path", "")
        value = svc.get_temperature(path)
        self.dispatch_event("getTemperatureResponse", {"path": path, "value": value})

    def _on_set_temperature(self, event: carb.events.IEvent) -> None:
        """Handler for the `setTemperature` event – set or remove temperature metadata."""
        print(f"[PickFilterHandler] _on_set_temperature event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        p = event.payload
        path = p.get("path") or p.get("prim_path", "")
        value = p.get("value", None)
        svc.set_temperature(path, value)
        self.dispatch_event("setTemperatureResponse", {"path": path, "value": value, "success": True})

    # ──────────────────────────────────────────
    # Viewport Selection API
    # ──────────────────────────────────────────

    def _on_get_viewport_selection_enabled(self, event: carb.events.IEvent) -> None:
        """Handler for the `getViewportSelectionEnabled` event."""
        print(f"[PickFilterHandler] _on_get_viewport_selection_enabled")

        svc = self._ensure_service()
        if svc is None:
            return

        enabled = svc.get_viewport_selection_enabled()
        self.dispatch_event("getViewportSelectionEnabledResponse", {"enabled": enabled})

    def _on_set_viewport_selection_enabled(self, event: carb.events.IEvent) -> None:
        """Handler for the `setViewportSelectionEnabled` event."""
        print(f"[PickFilterHandler] _on_set_viewport_selection_enabled event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        enabled = bool(event.payload.get("enabled", True))
        result = svc.set_viewport_selection_enabled(enabled)
        self.dispatch_event("setViewportSelectionEnabledResponse", {"enabled": enabled, "success": result})

    def _on_toggle_viewport_selection(self, event: carb.events.IEvent) -> None:
        """Handler for the `toggleViewportSelection` event."""
        print(f"[PickFilterHandler] _on_toggle_viewport_selection")

        svc = self._ensure_service()
        if svc is None:
            return

        new_state = svc.toggle_viewport_selection()
        self.dispatch_event("toggleViewportSelectionResponse", {"enabled": new_state})

    # ──────────────────────────────────────────
    # Frame API
    # ──────────────────────────────────────────

    def _on_frame_prim(self, event: carb.events.IEvent) -> None:
        """Handler for the `framePrim` event – focus viewport on a single prim."""
        print(f"[PickFilterHandler] _on_frame_prim event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        path = event.payload.get("path") or event.payload.get("prim_path", "")
        result = svc.frame_prim(path)
        self.dispatch_event("framePrimResponse", {"path": path, "success": result})

    def _on_frame_prims(self, event: carb.events.IEvent) -> None:
        """Handler for the `framePrims` event – fit multiple prims into the viewport."""
        print(f"[PickFilterHandler] _on_frame_prims event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        paths = event.payload.get("paths", [])
        if isinstance(paths, carb.dictionary.Item):
            paths = paths.get_dict()
        result = svc.frame_prims(paths)
        self.dispatch_event("framePrimsResponse", {"paths": paths, "success": result})

    # ──────────────────────────────────────────
    # Selection API
    # ──────────────────────────────────────────

    def _on_get_selection(self, event: carb.events.IEvent) -> None:
        """Handler for the `getSelection` event – return currently selected prims."""
        print(f"[PickFilterHandler] _on_get_selection")

        svc = self._ensure_service()
        if svc is None:
            return

        paths = svc.get_selection()
        self.dispatch_event("getSelectionResponse", {"paths": paths})

    def _on_clear_selection(self, event: carb.events.IEvent) -> None:
        """Handler for the `clearSelection` event – clear all selections."""
        print(f"[PickFilterHandler] _on_clear_selection")

        svc = self._ensure_service()
        if svc is None:
            return

        result = svc.clear_selection()
        self.dispatch_event("clearSelectionResponse", {"success": result})

    def _on_set_selection(self, event: carb.events.IEvent) -> None:
        """Handler for the `setSelection` event – replace current selection."""
        print(f"[PickFilterHandler] _on_set_selection event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        p = event.payload
        paths = p.get("paths", [])
        if isinstance(paths, carb.dictionary.Item):
            paths = paths.get_dict()
        expand_descendants = bool(p.get("expand_descendants", True))
        result = svc.set_selection(paths, expand_descendants=expand_descendants)
        self.dispatch_event("setSelectionResponse", {"paths": paths, "success": result})

    def _on_add_to_selection(self, event: carb.events.IEvent) -> None:
        """Handler for the `addToSelection` event – append prims to current selection."""
        print(f"[PickFilterHandler] _on_add_to_selection event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        p = event.payload
        paths = p.get("paths", [])
        if isinstance(paths, carb.dictionary.Item):
            paths = paths.get_dict()
        expand_descendants = bool(p.get("expand_descendants", False))
        result = svc.add_to_selection(paths, expand_descendants=expand_descendants)
        self.dispatch_event("addToSelectionResponse", {"paths": paths, "success": result})

    # ──────────────────────────────────────────
    # Group API
    # ──────────────────────────────────────────

    def _on_list_groups(self, event: carb.events.IEvent) -> None:
        """Handler for the `listGroups` event – return all defined groups."""
        print(f"[PickFilterHandler] _on_list_groups")

        svc = self._ensure_service()
        if svc is None:
            return

        groups = svc.list_groups()
        print(f"[PickFilterHandler] groups: {groups}")
        self.dispatch_event("listGroupsResponse", {"groups": groups})

    def _on_get_group_members(self, event: carb.events.IEvent) -> None:
        """Handler for the `getGroupMembers` event – resolve group members to stage paths."""
        print(f"[PickFilterHandler] _on_get_group_members event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        group_id = event.payload.get("group_id", "")
        members = svc.get_group_members(group_id)
        self.dispatch_event("getGroupMembersResponse", {"group_id": group_id, "members": members})

    def _on_select_group(self, event: carb.events.IEvent) -> None:
        """Handler for the `selectGroup` event – apply group members to the selection."""
        print(f"[PickFilterHandler] _on_select_group event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        p = event.payload
        group_id = p.get("group_id", "")
        mode = p.get("mode", "replace")
        expand_descendants = bool(p.get("expand_descendants", False))
        result = svc.select_group(group_id, mode=mode, expand_descendants=expand_descendants)
        self.dispatch_event("selectGroupResponse", result)

    # ──────────────────────────────────────────
    # Leaf Name API
    # ──────────────────────────────────────────

    def _on_select_by_leaf_names(self, event: carb.events.IEvent) -> None:
        """Handler for the `selectByLeafNames` event – select prims by leaf name list."""
        print(f"[PickFilterHandler] _on_select_by_leaf_names event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        p = event.payload
        leaf_names = p.get("leaf_names", [])
        if isinstance(leaf_names, carb.dictionary.Item):
            leaf_names = leaf_names.get_dict()
        mode = p.get("mode", "replace")
        expand_descendants = bool(p.get("expand_descendants", False))
        use_refresh = bool(p.get("use_refresh", False))
        require_unique = bool(p.get("require_unique", False))

        result = svc.select_by_leaf_names(
            leaf_names,
            mode=mode,
            expand_descendants=expand_descendants,
            use_refresh=use_refresh,
            require_unique=require_unique,
        )
        self.dispatch_event("selectByLeafNamesResponse", result)

    def _on_clear_selection_by_leaf_names(self, event: carb.events.IEvent) -> None:
        """Handler for the `clearSelectionByLeafNames` event – remove prims from selection by leaf name."""
        print(f"[PickFilterHandler] _on_clear_selection_by_leaf_names event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        p = event.payload
        leaf_names = p.get("leaf_names", [])
        if isinstance(leaf_names, carb.dictionary.Item):
            leaf_names = leaf_names.get_dict()
        use_refresh = bool(p.get("use_refresh", False))
        require_unique = bool(p.get("require_unique", False))

        result = svc.clear_selection_by_leaf_names(
            leaf_names,
            use_refresh=use_refresh,
            require_unique=require_unique,
        )
        self.dispatch_event("clearSelectionByLeafNamesResponse", result)

    def _on_set_pickable_by_leaf_names(self, event: carb.events.IEvent) -> None:
        """Handler for the `setPickableByLeafNames` event – bulk apply pickable state by leaf name."""
        print(f"[PickFilterHandler] _on_set_pickable_by_leaf_names event_payload: {event.payload}")

        svc = self._ensure_service()
        if svc is None:
            return

        p = event.payload
        leaf_names = p.get("leaf_names", [])
        if isinstance(leaf_names, carb.dictionary.Item):
            leaf_names = leaf_names.get_dict()
        pickable = bool(p.get("pickable", True))
        use_refresh = bool(p.get("use_refresh", False))
        require_unique = bool(p.get("require_unique", False))

        result = svc.set_pickable_by_leaf_names(
            leaf_names,
            pickable,
            use_refresh=use_refresh,
            require_unique=require_unique,
        )
        self.dispatch_event("setPickableByLeafNamesResponse", result)

    # ──────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────

    def _ensure_service(self):
        """서비스 인스턴스를 반환하며, 없으면 재시도합니다."""
        if self._pick_filter_service is None:
            self._pick_filter_service = ensure_service()
        if self._pick_filter_service is None:
            carb.log_error("[PickFilterHandler] PickFilterService is not available")
        return self._pick_filter_service
