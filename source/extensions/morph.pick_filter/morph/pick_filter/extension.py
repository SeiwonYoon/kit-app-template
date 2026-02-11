# morph/pick_filter/extension.py
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import asyncio
from collections import deque
from typing import Deque, Tuple, Any, Dict, Optional

import omni.ext
import omni.ui as ui

from .core import PickFilterService

_SERVICE = None
WINDOW_TITLE = "Pick Filter"


def get_service() -> PickFilterService:
    return _SERVICE


class MyExtension(omni.ext.IExt):
    """
    - UI 리렌더링 시에도 체크박스 모델을 path별로 유지
    - 이벤트/드로우 콜백에서 clear() 금지 -> defer(next frame)에서만 렌더
    - [추가] 각 prim 우측 '⚠' 버튼으로 hynix:temperature 더미 순환
            (core에서 temp 변경 신호를 즉시 발행하므로 temp_alarm이 즉시 반영 가능)
    """

    def on_startup(self, ext_id):
        global _SERVICE

        _SERVICE = PickFilterService()
        _SERVICE.start()

        self._expanded_paths: set[str] = {"/World"}
        self._items = []
        self._revision_seen = -1
        self._has_children: dict[str, bool] = {}

        self._pick_models: Dict[str, ui.SimpleBoolModel] = {}
        self._pick_model_subs: Dict[str, Any] = {}
        self._suppress_model_events: bool = False

        self._pending_ui_render: bool = False
        self._pending_refresh: bool = False
        self._pending_refresh_force: bool = False
        self._pending_pick_ops: Deque[Tuple[str, bool]] = deque()
        self._pending_temp_ops: Deque[str] = deque()
        self._processing_task: Optional[asyncio.Task] = None

        self._window = ui.Window(WINDOW_TITLE, width=880, height=860)
        self._window.visible = True
        with self._window.frame:
            with ui.VStack(spacing=8):
                self._build_header()
                self._build_tree()

        self._refresh_items(force=True)
        self._ui_tick_task = asyncio.ensure_future(self._ui_tick())

    def on_shutdown(self):
        global _SERVICE
        try:
            if self._ui_tick_task:
                self._ui_tick_task.cancel()
        except Exception:
            pass
        try:
            if self._processing_task:
                self._processing_task.cancel()
        except Exception:
            pass

        for _, sub in list(self._pick_model_subs.items()):
            try:
                if hasattr(sub, "unsubscribe"):
                    sub.unsubscribe()
            except Exception:
                pass
        self._pick_model_subs.clear()
        self._pick_models.clear()

        if _SERVICE:
            _SERVICE.stop()
        _SERVICE = None
        self._window = None

    # ---------------- UI header ----------------
    def _build_header(self):
        with ui.HStack(height=34, spacing=6):
            ui.Button("새로고침", clicked_fn=self._on_click_refresh, width=120)
            ui.Button("전체락", clicked_fn=self._lock_all, width=110)
            ui.Button("전체언락", clicked_fn=self._unlock_all, width=110)
            ui.Spacer()
            ui.Button("모두펼치기", clicked_fn=self._expand_all, width=120)
            ui.Button("모두접기", clicked_fn=self._collapse_all, width=120)

    def _build_tree(self):
        self._list_container = ui.ScrollingFrame(height=800)
        with self._list_container:
            self._list_vstack = ui.VStack(spacing=2)

    # ---------------- button handlers ----------------
    def _on_click_refresh(self):
        self._request_refresh(force=True)

    def _lock_all(self):
        svc = get_service()
        if not svc:
            return
        svc.lock_all()
        self._request_refresh(force=True)

    def _unlock_all(self):
        svc = get_service()
        if not svc:
            return
        svc.unlock_all()
        self._request_refresh(force=True)

    def _expand_all(self):
        for p, hc in (self._has_children or {}).items():
            if hc:
                self._expanded_paths.add(p)
        self._request_render()

    def _collapse_all(self):
        self._expanded_paths.clear()
        self._expanded_paths.add("/World")
        self._request_render()

    # ---------------- deferred processing ----------------
    def _kick_processing(self):
        if self._processing_task and not self._processing_task.done():
            return
        self._processing_task = asyncio.ensure_future(self._process_deferred())

    async def _process_deferred(self):
        app = __import__("omni.kit.app").kit.app.get_app()
        await app.next_update_async()

        svc = get_service()
        if not svc:
            return

        while self._pending_pick_ops:
            path, new_val = self._pending_pick_ops.popleft()
            svc.set_pickable(path, new_val, include_descendants=False)

        while self._pending_temp_ops:
            path = self._pending_temp_ops.popleft()
            svc.cycle_temperature_dummy(path)

        if self._pending_refresh:
            force = bool(self._pending_refresh_force)
            self._pending_refresh = False
            self._pending_refresh_force = False
            self._refresh_items(force=force)
            return

        if self._pending_ui_render:
            self._pending_ui_render = False
            self._render_tree()
            return

    def _request_render(self):
        self._pending_ui_render = True
        self._kick_processing()

    def _request_refresh(self, force: bool):
        self._pending_refresh = True
        self._pending_refresh_force = bool(force)
        self._kick_processing()

    def _request_pick_op(self, path: str, new_val: bool):
        self._pending_pick_ops.append((path, bool(new_val)))
        self._request_refresh(force=True)

    def _request_temp_dummy(self, path: str):
        self._pending_temp_ops.append(path)
        self._request_refresh(force=True)

    # ---------------- refresh loop ----------------
    async def _ui_tick(self):
        app = __import__("omni.kit.app").kit.app.get_app()
        while True:
            await app.next_update_async()
            svc = get_service()
            if not svc:
                return
            rev = svc.get_revision()
            if rev != self._revision_seen:
                self._request_refresh(force=False)

    # ---------------- data + render ----------------
    def _refresh_items(self, force: bool = False):
        svc = get_service()
        if not svc:
            return

        if force:
            self._items = svc.refresh_cache()
        else:
            self._items = svc.get_items_cached()

        self._revision_seen = svc.get_revision()
        self._rebuild_has_children()
        self._render_tree()

    def _rebuild_has_children(self):
        self._has_children.clear()
        items = self._items or []
        for i, it in enumerate(items):
            p = it.get("path") or ""
            if not p:
                continue
            d = int(it.get("depth", 0))
            hc = False
            if i + 1 < len(items):
                nd = int(items[i + 1].get("depth", 0))
                if nd > d:
                    hc = True
            self._has_children[p] = hc

    def _iter_visible_tree_items(self):
        items = self._items or []
        hidden_from_depth = None

        for it in items:
            path = (it.get("path") or "")
            if not path:
                continue

            depth = int(it.get("depth", 0))

            if hidden_from_depth is not None and depth < hidden_from_depth:
                hidden_from_depth = None

            if hidden_from_depth is not None and depth >= hidden_from_depth:
                continue

            yield it

            has_children = bool(self._has_children.get(path, False))
            if not has_children:
                continue

            if path not in self._expanded_paths:
                hidden_from_depth = depth + 1

    def _get_or_create_pick_model(self, path: str, initial: bool) -> ui.SimpleBoolModel:
        m = self._pick_models.get(path)
        if m is None:
            m = ui.SimpleBoolModel(bool(initial))
            self._pick_models[path] = m

            def _on_model_changed(model):
                if self._suppress_model_events:
                    return
                new_val = bool(model.get_value_as_bool())
                self._request_pick_op(path, new_val)

            try:
                sub = m.add_value_changed_fn(_on_model_changed)
                self._pick_model_subs[path] = sub
            except Exception:
                pass

        return m

    def _render_tree(self):
        items = list(self._iter_visible_tree_items())

        self._list_vstack.clear()
        with self._list_vstack:
            if not self._items:
                ui.Label("Stage가 없거나 /World가 유효하지 않습니다.", height=24)
                return

            for it in items:
                path = it.get("path", "")
                name = it.get("name", "")
                disp = it.get("display", "")
                tname = it.get("type", "")
                depth = int(it.get("depth", 0))

                temp = it.get("temperature", None)

                svc_pickable = bool(it.get("pickable", True))
                pick_model = self._get_or_create_pick_model(path, svc_pickable)

                cur_ui = bool(pick_model.get_value_as_bool())
                if cur_ui != svc_pickable:
                    self._suppress_model_events = True
                    try:
                        pick_model.set_value(bool(svc_pickable))
                    finally:
                        self._suppress_model_events = False

                has_children = bool(self._has_children.get(path, False))
                is_expanded = (path in self._expanded_paths)
                indent_w = min(depth * 16, 320)

                def _toggle_expand_request(p: str):
                    if p in self._expanded_paths:
                        self._expanded_paths.remove(p)
                    else:
                        self._expanded_paths.add(p)
                    self._request_render()

                def _make_toggle_fn(p: str):
                    def _fn():
                        _toggle_expand_request(p)
                    return _fn

                label_left = name or "(no-name)"
                if disp:
                    label_left = f"{label_left} ({disp})"
                if tname:
                    label_left = f"{label_left} [{tname}]"
                if temp is not None:
                    try:
                        label_left = f"{label_left}  |  T={float(temp):.1f}"
                    except Exception:
                        pass

                with ui.HStack(height=22):
                    ui.Spacer(width=indent_w)

                    if has_children:
                        ui.Button("▾" if is_expanded else "▸", clicked_fn=_make_toggle_fn(path), width=26)
                    else:
                        ui.Label(" ", width=26)

                    ui.CheckBox(model=pick_model, width=24)
                    ui.Label(label_left, width=740, word_wrap=False)

                    # 경고 테스트(더미 온도 순환)
                    ui.Button("⚠", width=28, clicked_fn=(lambda p=path: self._request_temp_dummy(p)))
