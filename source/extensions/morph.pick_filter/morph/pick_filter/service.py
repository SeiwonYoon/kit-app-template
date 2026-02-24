# morph/pick_filter/service.py
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import asyncio
from typing import Optional, List, Dict, Any, Set

import carb
import omni.usd
import omni.kit.app
import omni.kit.viewport.utility as vp_util

from .core import PickFilterCore

# ---------------- singleton ----------------
_SERVICE: Optional["PickFilterService"] = None


def get_service() -> Optional["PickFilterService"]:
    return _SERVICE


def ensure_service() -> "PickFilterService":
    """
    외부 익스텐션에서 안전하게 서비스 확보용.
    (extension lifecycle에서 start/stop 관리하는 전제지만, 방어적으로 제공)
    """
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = PickFilterService()
        _SERVICE.start()
    return _SERVICE


class PickFilterService:
    """
    Public API(Facade)
    - 외부 익스텐션/추후 web UI가 이 클래스만 호출하도록 설계
    - raw/더미 로직(예: 온도 더미 순환)은 Dummy UI에서 구현
    - ❌ 알람/이벤트/버스 발행은 service에 두지 않음 (요구사항 반영)
    - VP selection disable / focus(frame) / selection / group selection 제공
    """

    # ---------------- group definitions ----------------
    # group_id -> leaf prim names (현재 요구사항 기반)
    _GROUPS_BY_LEAF_NAMES: Dict[str, Dict[str, Any]] = {
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

    def __init__(self):
        self._core = PickFilterCore()
        self._started = False

        # viewport selection disable handle (keep alive while disabled)
        self._vp_sel_disabled: bool = False
        self._vp_sel_disable_handle = None

    # ---------------- lifecycle ----------------
    def start(self):
        if self._started:
            return
        self._started = True
        self._core.start()

    def stop(self):
        if not self._started:
            return
        self._started = False
        self._core.stop()

        # restore VP selection if disabled
        self._vp_sel_disable_handle = None
        self._vp_sel_disabled = False

    # ---------------- cache/state ----------------
    def get_revision(self) -> int:
        return self._core.get_revision()

    def get_items_cached(self) -> List[Dict[str, Any]]:
        return self._core.get_items_cached()

    def refresh_cache(self) -> List[Dict[str, Any]]:
        return self._core.refresh_cache()

    # ---------------- pickable ----------------
    def set_pickable(self, path: str, pickable: bool, include_descendants: bool = False):
        return self._core.set_pickable(path, pickable, include_descendants)

    def set_pickable_bulk(self, paths: List[str], pickable: bool):
        return self._core.set_pickable_bulk(paths, pickable)

    def lock_all(self):
        return self._core.lock_all()

    def unlock_all(self):
        return self._core.unlock_all()

    def set_pickable_for_group(self, group_id: str, pickable: bool) -> Dict[str, Any]:
        """
        그룹 멤버들에 대해 pickable bulk 적용 (semantic API)
        """
        members = self.get_group_members(group_id)
        if not members:
            return {"group_id": group_id, "updated": 0, "missing": [], "error": "group_empty_or_not_found"}

        # 존재 확인(캐시 기준 best-effort)
        cached = self.get_items_cached() or self.refresh_cache()
        stage_paths = {it.get("path") for it in cached if it.get("path")}
        missing = [p for p in members if p not in stage_paths]
        targets = [p for p in members if p in stage_paths]

        if targets:
            self.set_pickable_bulk(targets, bool(pickable))

        return {"group_id": group_id, "updated": len(targets), "missing": missing}

    # ---------------- temperature ----------------
    def get_temperature(self, path: str):
        return self._core.get_temperature(path)

    def set_temperature(self, path: str, value):
        """
        온도 설정/삭제
        - ✅ 알람/이벤트/버스 발행 없음(요구사항 반영)
        """
        return self._core.set_temperature(path, value)

    # ---------------- viewport selection enable/disable ----------------
    def get_viewport_selection_enabled(self) -> Optional[bool]:
        """
        True: viewport 클릭 selection 가능
        False: selection disabled
        None: active viewport/window 확보 실패(상태 판별 불가)
        """
        if not self._get_active_viewport_or_window():
            return None
        return (not self._vp_sel_disabled)

    def set_viewport_selection_enabled(self, enabled: bool) -> bool:
        """
        enabled=True  -> restore(선택 가능)
        enabled=False -> disable(선택 비활성)
        리턴: 최종 enabled 상태
        """
        want_disable = (not bool(enabled))
        vw = self._get_active_viewport_or_window()
        if not vw:
            carb.log_warn("[pick_filter] set_viewport_selection_enabled failed: no active viewport/window.")
            return bool(enabled)

        if want_disable and not self._vp_sel_disabled:
            try:
                self._vp_sel_disable_handle = vp_util.disable_selection(vw, disable_click=True)
                self._vp_sel_disabled = True
            except Exception as e:
                carb.log_error(f"[pick_filter] disable_selection exception: {e}")
        elif (not want_disable) and self._vp_sel_disabled:
            self._vp_sel_disable_handle = None
            self._vp_sel_disabled = False

        return (not self._vp_sel_disabled)

    def toggle_viewport_selection(self) -> Optional[bool]:
        cur = self.get_viewport_selection_enabled()
        if cur is None:
            return None
        return self.set_viewport_selection_enabled(enabled=not cur)

    def _get_active_viewport_or_window(self):
        """
        disable_selection에 전달할 대상 확보 (Kit 버전 차이 대응)
        """
        try:
            if hasattr(vp_util, "get_active_viewport"):
                vp = vp_util.get_active_viewport()
                if vp:
                    return vp
        except Exception:
            pass

        try:
            if hasattr(vp_util, "get_active_viewport_window"):
                win = vp_util.get_active_viewport_window()
                if win:
                    return win
        except Exception:
            pass

        return None

    # ---------------- focus/frame ----------------
    def frame_prim(self, path: str) -> bool:
        if not path:
            return False
        return self.frame_prims([path])

    def frame_prims(self, paths: List[str]) -> bool:
        """
        활성 viewport 카메라를 prims에 맞게 frame.
        - UI/Web에서 호출해도 안전하도록 1프레임 defer 포함
        """
        paths = [p for p in (paths or []) if p]
        if not paths:
            return False

        async def _do():
            app = omni.kit.app.get_app()
            await app.next_update_async()

            try:
                viewport_api = None

                if hasattr(vp_util, "get_active_viewport"):
                    viewport_api = vp_util.get_active_viewport()
                elif hasattr(vp_util, "get_active_viewport_window"):
                    win = vp_util.get_active_viewport_window()
                    viewport_api = win.viewport_api if win else None

                if not viewport_api:
                    carb.log_warn("[pick_filter] frame_prims failed: no active viewport.")
                    return False

                vp_util.frame_viewport_prims(viewport_api, prims=list(paths))
                return True
            except Exception as e:
                carb.log_error(f"[pick_filter] frame_prims exception: {e}")
                return False

        asyncio.ensure_future(_do())
        return True

    # ---------------- selection (raw) ----------------
    def get_selection(self) -> List[str]:
        ctx = omni.usd.get_context()
        if not ctx:
            return []
        try:
            sel = ctx.get_selection()
        except Exception:
            sel = None
        if not sel:
            return []
        try:
            if hasattr(sel, "get_selected_prim_paths"):
                return [str(p) for p in (sel.get_selected_prim_paths() or [])]
        except Exception:
            pass
        try:
            if hasattr(sel, "get_selected_prim_path_strings"):
                return [str(p) for p in (sel.get_selected_prim_path_strings() or [])]
        except Exception:
            pass
        return []

    def clear_selection(self) -> bool:
        ctx = omni.usd.get_context()
        if not ctx:
            return False
        try:
            sel = ctx.get_selection()
        except Exception:
            sel = None
        if not sel:
            return False
        try:
            if hasattr(sel, "clear_selected_prim_paths"):
                sel.clear_selected_prim_paths()
                return True
        except Exception:
            pass
        try:
            return self.set_selection([])
        except Exception:
            return False

    def set_selection(self, paths: List[str], expand_descendants: bool = False) -> bool:
        """
        selection을 교체(replace)
        """
        ctx = omni.usd.get_context()
        if not ctx:
            return False
        try:
            sel = ctx.get_selection()
        except Exception:
            sel = None
        if not sel:
            return False

        paths = [p for p in (paths or []) if p]
        try:
            if hasattr(sel, "set_selected_prim_paths"):
                try:
                    sel.set_selected_prim_paths(paths, bool(expand_descendants))
                except TypeError:
                    sel.set_selected_prim_paths(paths)
                return True
        except Exception:
            pass

        try:
            if hasattr(sel, "set_selected_prim_path_strings"):
                try:
                    sel.set_selected_prim_path_strings(paths, bool(expand_descendants))
                except TypeError:
                    sel.set_selected_prim_path_strings(paths)
                return True
        except Exception:
            pass

        return False

    def add_to_selection(self, paths: List[str], expand_descendants: bool = False) -> bool:
        """
        selection에 추가(append)
        """
        cur = self.get_selection()
        add = [p for p in (paths or []) if p]
        if not add:
            return True
        merged = list(dict.fromkeys(cur + add))
        return self.set_selection(merged, expand_descendants=expand_descendants)

    # ---------------- groups (semantic) ----------------
    def list_groups(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for gid, meta in (self._GROUPS_BY_LEAF_NAMES or {}).items():
            names = set(meta.get("leaf_names") or [])
            out.append({"group_id": gid, "label": meta.get("label", gid), "count_hint": len(names)})
        return out

    def get_group_members(self, group_id: str) -> List[str]:
        """
        group_id -> stage path list (캐시 기반 resolve)
        - leaf name 기준 그룹을 현재 캐시에서 path로 해석
        """
        meta = (self._GROUPS_BY_LEAF_NAMES or {}).get(group_id)
        if not meta:
            return []

        names: Set[str] = set(meta.get("leaf_names") or [])
        if not names:
            return []

        items = self.get_items_cached() or self.refresh_cache()
        paths: List[str] = []
        for it in items:
            n = it.get("name") or ""
            p = it.get("path") or ""
            if n in names and p:
                paths.append(p)

        return list(dict.fromkeys(paths))

    def select_group(self, group_id: str, mode: str = "replace", expand_descendants: bool = False) -> Dict[str, Any]:
        """
        group_id에 해당하는 prim들을 selection으로 반영
        mode: "replace" | "append" | "toggle"
        """
        mode = (mode or "replace").strip().lower()
        members = self.get_group_members(group_id)

        if not members:
            return {"group_id": group_id, "mode": mode, "requested": 0, "selected": 0, "missing": [], "error": "group_empty_or_not_found"}

        cached = self.get_items_cached() or self.refresh_cache()
        stage_paths = {it.get("path") for it in cached if it.get("path")}
        missing = [p for p in members if p not in stage_paths]
        targets = [p for p in members if p in stage_paths]

        ok = False
        if mode == "append":
            ok = self.add_to_selection(targets, expand_descendants=expand_descendants)
        elif mode == "toggle":
            cur = set(self.get_selection())
            tgt_set = set(targets)
            if tgt_set.issubset(cur):
                new_sel = [p for p in self.get_selection() if p not in tgt_set]
                ok = self.set_selection(new_sel, expand_descendants=expand_descendants)
            else:
                ok = self.add_to_selection(targets, expand_descendants=expand_descendants)
        else:
            ok = self.set_selection(targets, expand_descendants=expand_descendants)

        return {"group_id": group_id, "mode": mode, "requested": len(members), "selected": len(targets), "missing": missing, "ok": bool(ok)}