# morph/pick_filter/extension.py
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import asyncio

import omni.ext
import omni.ui as ui
import carb

from .core import PickFilterService

_SERVICE = None
WINDOW_TITLE = "Pick Filter"


def get_service() -> PickFilterService:
    return _SERVICE


class MyExtension(omni.ext.IExt):
    """
    UI:
    - 상단: 새로고침 / 전체락 / 전체언락 / 모두펼치기 / 모두접기
    - 트리: 접기/펼치기 + pickable 토글
      ✅ 체크 ON = 선택/픽업 가능 (pickable True)
      ✅ 체크 OFF = 선택/픽업 불가 (pickable False)
    """

    def on_startup(self, ext_id):
        global _SERVICE
        carb.log_info("[morph.pick_filter] Extension startup (Tree + Pickable Toggle)")

        _SERVICE = PickFilterService()
        _SERVICE.start()

        self._expanded_paths: set[str] = {"/World"}
        self._items = []
        self._revision_seen = -1
        self._ui_tick_task = None
        self._has_children: dict[str, bool] = {}

        self._window = ui.Window(WINDOW_TITLE, width=820, height=860)
        self._window.visible = True
        with self._window.frame:
            with ui.VStack(spacing=8):
                self._build_header()
                self._build_tree()

        self._refresh_items(force=True)
        self._ui_tick_task = asyncio.ensure_future(self._ui_tick())

    def on_shutdown(self):
        global _SERVICE
        carb.log_info("[morph.pick_filter] Extension shutdown")
        try:
            if self._ui_tick_task:
                self._ui_tick_task.cancel()
        except Exception:
            pass

        if _SERVICE:
            _SERVICE.stop()
        _SERVICE = None
        self._window = None

    # ---------------- UI ----------------
    def _build_header(self):
        with ui.HStack(height=34, spacing=6):
            ui.Button("새로고침", clicked_fn=lambda: self._refresh_items(force=True), width=120)
            ui.Button("전체락", clicked_fn=self._lock_all, width=110)
            ui.Button("전체언락", clicked_fn=self._unlock_all, width=110)
            ui.Spacer()
            ui.Button("모두펼치기", clicked_fn=self._expand_all, width=120)
            ui.Button("모두접기", clicked_fn=self._collapse_all, width=120)

    def _build_tree(self):
        self._list_container = ui.ScrollingFrame(height=800)
        with self._list_container:
            self._list_vstack = ui.VStack(spacing=2)

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
                self._refresh_items(force=False)

    def _refresh_items(self, force: bool = False):
        svc = get_service()
        if not svc:
            return

        self._items = svc.refresh_cache() if force else svc.get_items_cached()
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

    # ---------------- tree visibility ----------------
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

    # ---------------- render ----------------
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

                # ✅ 체크박스는 pickable 그 자체
                pickable = bool(it.get("pickable", True))
                pick_model = ui.SimpleBoolModel(pickable)

                has_children = bool(self._has_children.get(path, False))
                is_expanded = (path in self._expanded_paths)
                indent_w = min(depth * 16, 320)

                def _toggle_expand(p: str):
                    if p in self._expanded_paths:
                        self._expanded_paths.remove(p)
                    else:
                        self._expanded_paths.add(p)
                    self._render_tree()

                def _make_toggle_fn(p: str):
                    def _fn():
                        _toggle_expand(p)
                    return _fn

                def _make_pick_changed_fn(p: str):
                    def _changed(m):
                        svc = get_service()
                        if not svc:
                            return
                        # ✅ 사용자가 바꾼 체크값을 그대로 pickable로 적용
                        svc.set_pickable(p, bool(m.as_bool), include_descendants=False)
                        # service 내부에서 refresh_cache까지 수행하므로 UI도 즉시 동기화
                        self._refresh_items(force=False)
                    return _changed

                label_left = name or "(no-name)"
                if disp:
                    label_left = f"{label_left} ({disp})"
                if tname:
                    label_left = f"{label_left} [{tname}]"

                with ui.HStack(height=22):
                    ui.Spacer(width=indent_w)

                    if has_children:
                        ui.Button("▾" if is_expanded else "▸", clicked_fn=_make_toggle_fn(path), width=26)
                    else:
                        ui.Label(" ", width=26)

                    ui.CheckBox(model=pick_model, changed_fn=_make_pick_changed_fn(path), width=24)

                    ui.Label(label_left, width=740, word_wrap=False)

    # ---------------- header actions ----------------
    def _lock_all(self):
        svc = get_service()
        if not svc:
            return
        svc.lock_all()
        self._refresh_items(force=False)

    def _unlock_all(self):
        svc = get_service()
        if not svc:
            return
        svc.unlock_all()
        self._refresh_items(force=False)

    def _expand_all(self):
        for p, hc in (self._has_children or {}).items():
            if hc:
                self._expanded_paths.add(p)
        self._render_tree()

    def _collapse_all(self):
        self._expanded_paths.clear()
        self._expanded_paths.add("/World")
        self._render_tree()
