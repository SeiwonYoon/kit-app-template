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
    - ❌ 알람/이벤트/버스 발행은 service에 두지 않음 (요구사항 반영)
    - VP selection disable / frame / selection / pickable / temperature 제공
    - ✅ Name(leaf) 기반 resolve + selection/pickable API 제공
      (그룹 정의/정책 데이터는 service가 보유하지 않음)

    [추가] Mesh 활성/비활성(visibility) API 제공
      - get_mesh_enabled / set_mesh_enabled / toggle_mesh_enabled / bulk
      - leaf-name 기반 set/get/toggle 유사 패턴 제공
    """

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

    # ---------------- temperature ----------------
    def get_temperature(self, path: str):
        return self._core.get_temperature(path)

    def set_temperature(self, path: str, value):
        """
        온도 설정/삭제
        - ✅ 알람/이벤트/버스 발행 없음(요구사항 반영)
        """
        return self._core.set_temperature(path, value)

    # ---------------- mesh visibility ----------------
    def get_mesh_enabled(self, path: str) -> Optional[bool]:
        """
        prim visibility 기반 mesh enabled 상태
        - True/False/None(Imageable 아님 등)
        """
        return self._core.get_mesh_enabled(path)

    def set_mesh_enabled(self, path: str, enabled: bool, include_descendants: bool = False) -> bool:
        """
        prim visibility ON/OFF
        include_descendants=True면 하위 prim까지 동일 적용
        """
        return bool(self._core.set_mesh_enabled(path, enabled, include_descendants=include_descendants))

    def toggle_mesh_enabled(self, path: str, include_descendants: bool = False) -> Optional[bool]:
        """
        현재 상태를 반전시킴
        리턴: 토글 후 최종 상태(True/False) 또는 None(토글 불가)
        """
        return self._core.toggle_mesh_enabled(path, include_descendants=include_descendants)

    def set_mesh_enabled_bulk(self, paths: List[str], enabled: bool) -> bool:
        """
        bulk 적용 (refresh 1회)
        """
        return bool(self._core.set_mesh_enabled_bulk(paths, enabled))

    def set_mesh_enabled_by_leaf_names(
        self,
        leaf_names: List[str],
        enabled: bool,
        include_descendants: bool = False,
        *,
        use_refresh: bool = False,
        require_unique: bool = False,
    ) -> Dict[str, Any]:
        """
        leaf name 목록에 해당하는 prim들에 대해 mesh enabled bulk 적용
        include_descendants=True면, resolve된 각 path의 하위 prim까지 같이 적용
        """
        r = self._resolve_paths_by_leaf_names(leaf_names, use_refresh=use_refresh, require_unique=require_unique)

        if not r.get("ok", False):
            r.update({"updated": 0})
            return r

        targets = list(r.get("resolved_paths") or [])
        if not targets:
            r.update({"updated": 0, "ok": True})
            return r

        if include_descendants:
            # include_descendants는 core의 단일 API를 반복 호출하는 방식으로 안전하게 처리
            updated = 0
            for p in targets:
                if self.set_mesh_enabled(p, bool(enabled), include_descendants=True):
                    updated += 1
            r.update({"updated": updated, "ok": True, "enabled": bool(enabled), "include_descendants": True})
            return r

        ok_any = self.set_mesh_enabled_bulk(targets, bool(enabled))
        r.update({"updated": len(targets), "ok": bool(ok_any) or True, "enabled": bool(enabled), "include_descendants": False})
        return r

    def toggle_mesh_by_leaf_names(
        self,
        leaf_names: List[str],
        include_descendants: bool = False,
        *,
        use_refresh: bool = False,
        require_unique: bool = False,
    ) -> Dict[str, Any]:
        """
        leaf name 목록으로 mesh enabled 토글.
        - 각 prim을 개별 토글(현재 상태가 서로 다를 수 있으므로 bulk toggle은 미제공)
        리턴에 toggled_paths, final_states를 함께 제공.
        """
        r = self._resolve_paths_by_leaf_names(leaf_names, use_refresh=use_refresh, require_unique=require_unique)

        if not r.get("ok", False):
            r.update({"toggled": 0, "toggled_paths": [], "final_states": {}})
            return r

        targets = list(r.get("resolved_paths") or [])
        if not targets:
            r.update({"toggled": 0, "ok": True, "toggled_paths": [], "final_states": {}})
            return r

        toggled_paths: List[str] = []
        final_states: Dict[str, Any] = {}

        for p in targets:
            st = self.toggle_mesh_enabled(p, include_descendants=include_descendants)
            if st is None:
                final_states[p] = None
                continue
            toggled_paths.append(p)
            final_states[p] = bool(st)

        r.update(
            {
                "toggled": len(toggled_paths),
                "toggled_paths": toggled_paths,
                "final_states": final_states,
                "ok": True,
                "include_descendants": bool(include_descendants),
            }
        )
        return r

    def get_mesh_enabled_by_leaf_names(
        self,
        leaf_names: List[str],
        *,
        use_refresh: bool = False,
        require_unique: bool = False,
    ) -> Dict[str, Any]:
        """
        leaf name 목록에 해당하는 prim들의 mesh enabled 상태 조회.
        - 상태는 path->Optional[bool]로 반환
        """
        r = self._resolve_paths_by_leaf_names(leaf_names, use_refresh=use_refresh, require_unique=require_unique)

        targets = list(r.get("resolved_paths") or [])
        states: Dict[str, Any] = {}
        for p in targets:
            states[p] = self.get_mesh_enabled(p)

        r.update({"states": states, "count": len(targets), "ok": bool(r.get("ok", True))})
        return r

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

    # ---------------- leaf-name based resolve + ops ----------------
    def _resolve_paths_by_leaf_names(
        self,
        leaf_names: List[str],
        *,
        use_refresh: bool = False,
        require_unique: bool = False,
    ) -> Dict[str, Any]:
        """
        leaf name 목록을 stage path 목록으로 resolve (캐시 기반 best-effort)
        리턴:
          {
            "requested_names": [...],
            "resolved_paths": [...],          # dedup, keep order(by cache scan order)
            "missing_names": [...],
            "ambiguous": {name: [paths...]},  # 동일 name이 복수 path에 매칭될 때
            "ok": bool,                       # require_unique 위반 시 False
          }
        """
        req_names = [str(n).strip() for n in (leaf_names or []) if str(n).strip()]
        # dedupe while preserving order
        req_names = list(dict.fromkeys(req_names))

        items = (self.refresh_cache() if use_refresh else self.get_items_cached()) or (self.refresh_cache() if not use_refresh else [])
        name_to_paths: Dict[str, List[str]] = {}

        for it in (items or []):
            n = (it.get("name") or "").strip()
            p = (it.get("path") or "").strip()
            if not n or not p:
                continue
            if n not in name_to_paths:
                name_to_paths[n] = []
            name_to_paths[n].append(p)

        missing: List[str] = []
        ambiguous: Dict[str, List[str]] = {}
        resolved_paths: List[str] = []

        for n in req_names:
            paths = name_to_paths.get(n) or []
            if not paths:
                missing.append(n)
                continue
            if len(paths) > 1:
                ambiguous[n] = list(paths)
            resolved_paths.extend(paths)

        # dedupe resolved paths keep order
        resolved_paths = list(dict.fromkeys([p for p in resolved_paths if p]))

        ok = True
        if require_unique and ambiguous:
            ok = False

        return {
            "requested_names": req_names,
            "resolved_paths": resolved_paths,
            "missing_names": missing,
            "ambiguous": ambiguous,
            "ok": bool(ok),
        }

    def select_by_leaf_names(
        self,
        leaf_names: List[str],
        mode: str = "replace",
        expand_descendants: bool = False,
        *,
        use_refresh: bool = False,
        require_unique: bool = False,
    ) -> Dict[str, Any]:
        """
        leaf name 목록으로 selection 반영
        mode: "replace" | "append" | "toggle"
        """
        mode = (mode or "replace").strip().lower()
        r = self._resolve_paths_by_leaf_names(leaf_names, use_refresh=use_refresh, require_unique=require_unique)

        if not r.get("ok", False):
            r.update({"mode": mode, "selected": 0})
            return r

        targets = list(r.get("resolved_paths") or [])
        if not targets:
            r.update({"mode": mode, "selected": 0})
            return r

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

        r.update({"mode": mode, "selected": len(targets), "ok": bool(ok)})
        return r

    def clear_selection_by_leaf_names(
        self,
        leaf_names: List[str],
        *,
        use_refresh: bool = False,
        require_unique: bool = False,
    ) -> Dict[str, Any]:
        """
        leaf name 목록에 해당하는 prim들을 현재 selection에서 제거
        """
        r = self._resolve_paths_by_leaf_names(leaf_names, use_refresh=use_refresh, require_unique=require_unique)

        if not r.get("ok", False):
            r.update({"removed": 0})
            return r

        targets = set(r.get("resolved_paths") or [])
        if not targets:
            r.update({"removed": 0, "ok": True})
            return r

        cur = self.get_selection()
        new_sel = [p for p in cur if p not in targets]
        removed = len(cur) - len(new_sel)

        ok = self.set_selection(new_sel, expand_descendants=False)
        r.update({"removed": removed, "ok": bool(ok)})
        return r

    def set_pickable_by_leaf_names(
        self,
        leaf_names: List[str],
        pickable: bool,
        *,
        use_refresh: bool = False,
        require_unique: bool = False,
    ) -> Dict[str, Any]:
        """
        leaf name 목록에 해당하는 prim들에 대해 pickable bulk 적용
        """
        r = self._resolve_paths_by_leaf_names(leaf_names, use_refresh=use_refresh, require_unique=require_unique)

        if not r.get("ok", False):
            r.update({"updated": 0})
            return r

        targets = list(r.get("resolved_paths") or [])
        if not targets:
            r.update({"updated": 0, "ok": True})
            return r

        self.set_pickable_bulk(targets, bool(pickable))
        r.update({"updated": len(targets), "ok": True, "pickable": bool(pickable)})
        return r