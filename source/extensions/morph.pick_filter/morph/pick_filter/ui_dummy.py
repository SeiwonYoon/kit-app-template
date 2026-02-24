# morph/pick_filter/ui_dummy.py
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import asyncio
from collections import deque
from typing import Deque, Tuple, Any, Dict, Optional, List

import carb
import omni.ui as ui

from .service import PickFilterService

# 더미 UI 전용: 온도 더미 순환 시퀀스
DUMMY_TEMP_SEQ = [25.0, 80.0, 100.0, 120.0]

# ✅ 더미 UI 전용: "그룹"은 UI가 보유하되, 서비스에는 group 정의를 두지 않는다.
#    대신 leaf name 목록을 서비스 API(select_by_leaf_names / set_pickable_by_leaf_names / clear_selection_by_leaf_names)에 전달한다.
DUMMY_GROUPS_BY_LEAF_NAMES: Dict[str, Dict[str, Any]] = {
    "pcb_steps": {
        "label": "PCB Steps",
        "leaf_names": {
            "N_01_PCB_On_Board",
            "N_02_PCB_Router",
            "N_03_Feeder",
            "N_04_PCB_Assembly",
            "N_05_Assembly",
            "N_06_Test",
            "N_07_Laser_Cutting",
        },
    }
}


class PickFilterDummyUI:
    """
    더미 UI (추후 web UI로 대체 예정)
    - raw/정책/더미 로직은 여기서 구현
      (예: 온도 더미 순환 버튼, PCB Steps 버튼의 leaf name 목록 관리)
    - 실제 기능 실행은 PickFilterService API만 호출
      (name 기반 selection/pickable API 사용)

    [추가]
    - Mesh(visibility) 토글 버튼 'M' 제공
    """

    WINDOW_TITLE = "Pick Filter"

    def __init__(self, svc: PickFilterService):
        self._svc = svc

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
        self._pending_pick_bulk: Deque[Tuple[List[str], bool]] = deque()
        self._pending_temp_ops: Deque[str] = deque()

        # ✅ mesh toggle ops
        self._pending_mesh_ops: Deque[Tuple[str, bool]] = deque()  # (path, include_descendants)

        self._processing_task: Optional[asyncio.Task] = None
        self._ui_tick_task: Optional[asyncio.Task] = None

        self._btn_disable_vp_sel = None

        self._list_container = None
        self._list_vstack = None

        # 최초 로드
        self._refresh_items(force=True)
        self._ui_tick_task = asyncio.ensure_future(self._ui_tick())

    # ---------------- attach UI ----------------
    def build(self):
        with ui.VStack(spacing=8):
            self._build_header()
            self._build_tree()

    def shutdown(self):
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

    # ---------------- UI header ----------------
    def _build_header(self):
        with ui.HStack(height=34, spacing=6):
            ui.Button("새로고침", clicked_fn=self._on_click_refresh, width=120)
            ui.Button("전체락", clicked_fn=self._lock_all, width=110)
            ui.Button("전체언락", clicked_fn=self._unlock_all, width=110)

            # ✅ UI는 leaf name 목록만 관리하고, 실행은 서비스 name-based API로 처리
            ui.Button("선택언락(PCB)", clicked_fn=self._unlock_only_pcb_group, width=160)
            ui.Button("그룹선택(PCB)", clicked_fn=self._select_pcb_group, width=160)

            # VP selection toggle은 서비스 API 사용
            self._btn_disable_vp_sel = ui.Button("VP선택:ON", clicked_fn=self._toggle_viewport_selection, width=120)

            ui.Spacer()
            ui.Button("모두펼치기", clicked_fn=self._expand_all, width=120)
            ui.Button("모두접기", clicked_fn=self._collapse_all, width=120)

    def _build_tree(self):
        self._list_container = ui.ScrollingFrame(height=800)
        with self._list_container:
            self._list_vstack = ui.VStack(spacing=2)

        self._render_tree()

    # ---------------- button handlers ----------------
    def _on_click_refresh(self):
        self._request_refresh(force=True)

    def _lock_all(self):
        self._svc.lock_all()
        self._request_refresh(force=True)

    def _unlock_all(self):
        self._svc.unlock_all()
        self._request_refresh(force=True)

    def _get_group_leaf_names(self, group_id: str) -> List[str]:
        meta = (DUMMY_GROUPS_BY_LEAF_NAMES or {}).get(group_id) or {}
        names = list(meta.get("leaf_names") or [])
        # 안정적으로: 문자열 정리 + dedupe
        names = [str(n).strip() for n in names if str(n).strip()]
        return list(dict.fromkeys(names))

    def _select_pcb_group(self):
        leaf_names = self._get_group_leaf_names("pcb_steps")
        r = self._svc.select_by_leaf_names(leaf_names, mode="replace", use_refresh=False, require_unique=False)
        if not r.get("ok", False):
            carb.log_warn(f"[pick_filter] select_by_leaf_names failed: {r}")
        self._request_refresh(force=False)

    def _unlock_only_pcb_group(self):
        leaf_names = self._get_group_leaf_names("pcb_steps")
        r = self._svc.set_pickable_by_leaf_names(leaf_names, True, use_refresh=False, require_unique=False)
        if not r.get("ok", True):
            carb.log_warn(f"[pick_filter] set_pickable_by_leaf_names failed: {r}")
        self._request_refresh(force=True)

    def _toggle_viewport_selection(self):
        cur = self._svc.toggle_viewport_selection()
        if cur is None:
            carb.log_warn("[pick_filter] VP selection toggle failed (no active viewport/window).")
            return
        try:
            if self._btn_disable_vp_sel:
                self._btn_disable_vp_sel.text = ("VP선택:ON" if cur else "VP선택:OFF")
        except Exception:
            pass

    def _expand_all(self):
        for p, hc in (self._has_children or {}).items():
            if hc:
                self._expanded_paths.add(p)
        self._request_render()

    def _collapse_all(self):
        self._expanded_paths.clear()
        self._expanded_paths.add("/World")
        self._request_render()

    # ---------------- dummy temp logic ----------------
    def _cycle_temperature_dummy_local(self, path: str) -> Optional[float]:
        """
        더미 순환 로직은 Public API로 만들지 않고 UI에서 구현
        None/미설정 -> 25 -> 80 -> 100 -> 120 -> 25 ...
        """
        cur = self._svc.get_temperature(path)
        seq = list(DUMMY_TEMP_SEQ)

        if cur is None:
            nxt = seq[0]
        else:
            try:
                idx = min(range(len(seq)), key=lambda i: abs(seq[i] - float(cur)))
                nxt = seq[(idx + 1) % len(seq)]
            except Exception:
                nxt = seq[0]

        self._svc.set_temperature(path, nxt)
        return nxt

    # ---------------- deferred processing ----------------
    def _kick_processing(self):
        if self._processing_task and not self._processing_task.done():
            return
        self._processing_task = asyncio.ensure_future(self._process_deferred())

    async def _process_deferred(self):
        app = __import__("omni.kit.app").kit.app.get_app()
        await app.next_update_async()

        while self._pending_pick_bulk:
            paths, new_val = self._pending_pick_bulk.popleft()
            self._svc.set_pickable_bulk(paths, new_val)

        while self._pending_pick_ops:
            path, new_val = self._pending_pick_ops.popleft()
            self._svc.set_pickable(path, new_val, include_descendants=False)

        while self._pending_temp_ops:
            path = self._pending_temp_ops.popleft()
            self._cycle_temperature_dummy_local(path)

        # ✅ mesh toggle ops
        while self._pending_mesh_ops:
            path, include_desc = self._pending_mesh_ops.popleft()
            st = self._svc.toggle_mesh_enabled(path, include_descendants=bool(include_desc))
            if st is None:
                carb.log_warn(f"[pick_filter] toggle_mesh_enabled failed: {path}")

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

    def _request_pick_ops_bulk(self, paths: List[str], new_val: bool):
        if not paths:
            return
        self._pending_pick_bulk.append((list(paths), bool(new_val)))
        self._request_refresh(force=True)

    def _request_temp_dummy(self, path: str):
        self._pending_temp_ops.append(path)
        self._request_refresh(force=True)

    def _request_mesh_toggle(self, path: str, include_descendants: bool = False):
        self._pending_mesh_ops.append((path, bool(include_descendants)))
        self._request_refresh(force=True)

    # ---------------- refresh loop ----------------
    async def _ui_tick(self):
        app = __import__("omni.kit.app").kit.app.get_app()
        while True:
            await app.next_update_async()
            rev = self._svc.get_revision()
            if rev != self._revision_seen:
                self._request_refresh(force=False)

    # ---------------- data + render ----------------
    def _refresh_items(self, force: bool = False):
        if force:
            self._items = self._svc.refresh_cache()
        else:
            self._items = self._svc.get_items_cached()

        self._revision_seen = self._svc.get_revision()
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
        if not self._list_vstack:
            return

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
                mesh_enabled = it.get("mesh_enabled", None)  # Optional[bool]

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
                if mesh_enabled is not None:
                    label_left = f"{label_left}  |  M={'ON' if bool(mesh_enabled) else 'OFF'}"

                with ui.HStack(height=22):
                    ui.Spacer(width=indent_w)

                    if has_children:
                        ui.Button("▾" if is_expanded else "▸", clicked_fn=_make_toggle_fn(path), width=26)
                    else:
                        ui.Label(" ", width=26)

                    ui.CheckBox(model=pick_model, width=24)
                    ui.Label(label_left, width=700, word_wrap=False)

                    # 더미 온도 순환(⚠)
                    ui.Button("⚠", width=28, clicked_fn=(lambda p=path: self._request_temp_dummy(p)))

                    # ✅ 메쉬 토글(M) - service API 호출
                    ui.Button("M", width=28, clicked_fn=(lambda p=path: self._request_mesh_toggle(p, include_descendants=False)))

                    # 포커스(프레임)
                    ui.Button("F", width=28, clicked_fn=(lambda p=path: self._svc.frame_prim(p)))