# morph/pick_filter/core.py
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import asyncio
from typing import Dict, Any, List, Optional

import omni.usd
import omni.kit.app
from pxr import Usd, Sdf, UsdGeom

try:
    from omni.usd import StageEventType
except Exception:
    StageEventType = None


TEMP_ATTR = "hynix:temperature"


# -------------------------------------------------------------------------
# Backward-compat shims (TEMP)
# - 과거 morph.temp_alarm이 아래 심볼을 import 하던 관성 때문에 로딩이 깨질 수 있음.
# - 현재 설계에서는 사용하지 않음(no-op).
# - temp_alarm이 완전히 정리되면 제거 가능.
# -------------------------------------------------------------------------
def register_temperature_listener(fn):
    """
    [DEPRECATED / NO-OP]
    과거 temp_alarm 호환용. 현재 설계에서는 사용하지 않음.
    """
    return


def unregister_temperature_listener(fn):
    """
    [DEPRECATED / NO-OP]
    과거 temp_alarm 호환용. 현재 설계에서는 사용하지 않음.
    """
    return


class PickFilterCore:
    """
    Pickable/Temperature Core
    - stage_event 폭주로 refresh_cache 연속 호출되는 문제를 디바운스로 완화
    - overrides를 source-of-truth로 사용 (ctx getter 부재 환경 대응)
    - prim 온도 어트리뷰트(hynix:temperature) read/write + cache 노출
      (알람/이벤트/버스 발행은 하지 않음)

    [추가] Mesh(Visibility) read/write + cache 노출
      - UsdGeom.Imageable visibility로 ON/OFF 처리
      - ON: inherited, OFF: invisible
    """

    def __init__(self):
        self._sub = None
        self._starting_task: Optional[asyncio.Task] = None
        self._stopped = False

        self.enabled = True
        self.root_path = "/World"
        self.limit = 50000

        # source-of-truth (ctx getter가 불가한 환경에서도 일관성 유지)
        self._overrides: Dict[str, bool] = {}
        self._cached_items: List[Dict[str, Any]] = []
        self._revision: int = 0

        # stage refresh 디바운스
        self._debounce_task: Optional[asyncio.Task] = None
        self._debounce_requested: bool = False

    # ---------------- lifecycle ----------------
    def start(self):
        if self._starting_task:
            return
        self._stopped = False
        self._starting_task = asyncio.ensure_future(self._start_when_usd_ready())

    def stop(self):
        self._stopped = True
        if self._sub:
            try:
                self._sub.unsubscribe()
            except Exception:
                pass
            self._sub = None
        self._starting_task = None

        if self._debounce_task:
            try:
                self._debounce_task.cancel()
            except Exception:
                pass
        self._debounce_task = None

    async def _start_when_usd_ready(self):
        app = omni.kit.app.get_app()
        while not self._stopped:
            ctx = omni.usd.get_context()
            if ctx is not None:
                try:
                    stream = ctx.get_stage_event_stream()
                    self._sub = stream.create_subscription_to_pop(self._on_stage_event)
                    self.refresh_cache()
                    return
                except Exception:
                    pass
            await app.next_update_async()

    # ---------------- public API ----------------
    def get_revision(self) -> int:
        return self._revision

    def get_items_cached(self) -> List[Dict[str, Any]]:
        return list(self._cached_items)

    def refresh_cache(self) -> List[Dict[str, Any]]:
        self._cached_items = self._scan_stage_flat(self.root_path, limit=self.limit)
        self._revision += 1
        return list(self._cached_items)

    # ---------------- pickable ops ----------------
    def set_pickable(self, path: str, pickable: bool, include_descendants: bool = False):
        if not self.enabled:
            return

        path = (path or "").strip()
        if not path:
            return

        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not ctx or not stage:
            return

        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            return

        targets = [path]
        if include_descendants:
            targets = self._expand_with_descendants(prim)

        for p in targets:
            try:
                ctx.set_pickable(p, bool(pickable))
            except Exception:
                pass
            self._overrides[p] = bool(pickable)

        self.refresh_cache()

    def set_pickable_bulk(self, paths: List[str], pickable: bool):
        """
        bulk 적용 (refresh 1회)
        """
        if not self.enabled:
            return
        if not paths:
            return

        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not ctx or not stage:
            return

        for p in paths:
            p = (p or "").strip()
            if not p:
                continue
            try:
                ctx.set_pickable(p, bool(pickable))
            except Exception:
                pass
            self._overrides[p] = bool(pickable)

        self.refresh_cache()

    def lock_all(self):
        self._set_all_pickable(False)

    def unlock_all(self):
        self._set_all_pickable(True)

    # ---------------- temperature ops ----------------
    def get_temperature(self, path: str) -> Optional[float]:
        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return None
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            return None
        return self._read_temperature(prim)

    def set_temperature(self, path: str, value: Optional[float]) -> bool:
        """
        hynix:temperature 를 생성/갱신.
        value=None이면 attribute 제거(삭제) 시도.
        리턴: 변경 시도 성공 여부(대략적인 성공 여부)
        """
        path = (path or "").strip()
        if not path:
            return False

        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return False

        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            return False

        if value is None:
            attr = prim.GetAttribute(TEMP_ATTR)
            if attr and attr.IsValid():
                try:
                    prim.RemoveProperty(TEMP_ATTR)
                except Exception:
                    try:
                        attr.Clear()
                    except Exception:
                        pass
            self.refresh_cache()
            return True

        try:
            attr = prim.GetAttribute(TEMP_ATTR)
            if not attr or not attr.IsValid():
                attr = prim.CreateAttribute(TEMP_ATTR, Sdf.ValueTypeNames.Float, custom=True)
            attr.Set(float(value))
        except Exception:
            return False

        self.refresh_cache()
        return True

    # ---------------- mesh visibility ops ----------------
    def get_mesh_enabled(self, path: str) -> Optional[bool]:
        """
        path prim의 visibility 기반 mesh enabled 상태를 반환.
        - True  : visible(inherited)
        - False : invisible
        - None  : stage/prim invalid 또는 Imageable이 아님
        """
        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return None
        prim = stage.GetPrimAtPath((path or "").strip())
        if not prim or not prim.IsValid():
            return None
        return self._read_visibility_enabled(prim)

    def set_mesh_enabled(self, path: str, enabled: bool, include_descendants: bool = False) -> bool:
        """
        path prim의 visibility를 ON/OFF.
        - ON  -> visibility = inherited
        - OFF -> visibility = invisible
        include_descendants=True면 하위 prim까지 동일 적용.
        """
        path = (path or "").strip()
        if not path:
            return False

        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return False

        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            return False

        targets = [prim]
        if include_descendants:
            targets = [stage.GetPrimAtPath(p) for p in self._expand_with_descendants(prim)]
            targets = [t for t in targets if t and t.IsValid()]

        ok_any = False
        for tprim in targets:
            if self._write_visibility_enabled(tprim, bool(enabled)):
                ok_any = True

        self.refresh_cache()
        return bool(ok_any)

    def toggle_mesh_enabled(self, path: str, include_descendants: bool = False) -> Optional[bool]:
        """
        현재 상태를 읽어서 반전시킨 뒤 적용.
        리턴:
          - True/False: 토글 후 최종 상태
          - None: 토글 불가(Imageable 아님/prim invalid 등)
        """
        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return None
        prim = stage.GetPrimAtPath((path or "").strip())
        if not prim or not prim.IsValid():
            return None

        cur = self._read_visibility_enabled(prim)
        if cur is None:
            return None

        nxt = (not bool(cur))
        ok = self.set_mesh_enabled(path, nxt, include_descendants=include_descendants)
        if not ok:
            return None
        return bool(nxt)

    def set_mesh_enabled_bulk(self, paths: List[str], enabled: bool) -> bool:
        """
        bulk 적용 (refresh 1회)
        - include_descendants는 bulk에서는 별도 제공하지 않음(필요 시 caller에서 expand 후 전달)
        """
        if not paths:
            return True

        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return False

        ok_any = False
        for p in paths:
            p = (p or "").strip()
            if not p:
                continue
            prim = stage.GetPrimAtPath(p)
            if not prim or not prim.IsValid():
                continue
            if self._write_visibility_enabled(prim, bool(enabled)):
                ok_any = True

        self.refresh_cache()
        return bool(ok_any)

    # ---------------- internals ----------------
    @staticmethod
    def _expand_with_descendants(root_prim) -> List[str]:
        out: List[str] = []
        for prim in Usd.PrimRange(root_prim):
            out.append(prim.GetPath().pathString)
        rp = root_prim.GetPath().pathString
        if rp not in out:
            out.insert(0, rp)
        return out

    @staticmethod
    def _depth_from_path(path: str) -> int:
        if not path or path == "/":
            return 0
        return len([x for x in path.split("/") if x])

    @staticmethod
    def _get_display_text(prim) -> str:
        try:
            return prim.GetDisplayName() or ""
        except Exception:
            return ""

    def _effective_pickable(self, path: str) -> bool:
        if path in self._overrides:
            return bool(self._overrides[path])
        return True

    @staticmethod
    def _read_temperature(prim) -> Optional[float]:
        try:
            attr = prim.GetAttribute(TEMP_ATTR)
            if not attr or not attr.IsValid():
                return None
            v = attr.Get()
            if v is None:
                return None
            return float(v)
        except Exception:
            return None

    @staticmethod
    def _read_visibility_enabled(prim) -> Optional[bool]:
        """
        UsdGeom.Imageable visibility를 기준으로 enabled 판정.
        - visibility == invisible -> False
        - 그 외(inherited 등) -> True
        - Imageable이 아니면 None
        """
        try:
            img = UsdGeom.Imageable(prim)
            if not img:
                return None
            attr = img.GetVisibilityAttr()
            if not attr or not attr.IsValid():
                # attribute가 없으면 기본은 visible(inherited)로 간주
                return True
            v = attr.Get()
            if v is None:
                return True
            return (v != UsdGeom.Tokens.invisible)
        except Exception:
            return None

    @staticmethod
    def _write_visibility_enabled(prim, enabled: bool) -> bool:
        """
        visibility 설정
        - enabled=True  -> inherited
        - enabled=False -> invisible
        """
        try:
            img = UsdGeom.Imageable(prim)
            if not img:
                return False
            attr = img.GetVisibilityAttr()
            if not attr or not attr.IsValid():
                # CreateVisibilityAttr는 존재 버전에 따라 다를 수 있어 CreateAttribute로 보수적으로 처리
                try:
                    attr = prim.CreateAttribute("visibility", Sdf.ValueTypeNames.Token, custom=False)
                except Exception:
                    attr = img.GetVisibilityAttr()
            if not attr or not attr.IsValid():
                return False

            attr.Set(UsdGeom.Tokens.inherited if bool(enabled) else UsdGeom.Tokens.invisible)
            return True
        except Exception:
            return False

    def _scan_stage_flat(self, root_path: str, limit: int = 50000) -> List[Dict[str, Any]]:
        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not ctx or not stage:
            return []

        root = (root_path or "/World").strip() or "/World"
        root_prim = stage.GetPrimAtPath(root) if root != "/" else stage.GetPseudoRoot()
        if not root_prim or not root_prim.IsValid():
            return []

        root_depth = self._depth_from_path(root)
        items: List[Dict[str, Any]] = []
        count = 0

        for prim in Usd.PrimRange(root_prim):
            p = prim.GetPath().pathString
            depth = max(0, self._depth_from_path(p) - root_depth)

            temp = self._read_temperature(prim)
            mesh_enabled = self._read_visibility_enabled(prim)

            items.append(
                {
                    "path": p,
                    "name": prim.GetName() or "(no-name)",
                    "display": self._get_display_text(prim),
                    "type": prim.GetTypeName() or "",
                    "depth": depth,
                    "pickable": bool(self._effective_pickable(p)),
                    "overridden": (p in self._overrides),
                    "temperature": temp,
                    # ✅ mesh visibility cache
                    "mesh_enabled": mesh_enabled,  # Optional[bool]
                }
            )

            count += 1
            if limit > 0 and count >= limit:
                break

        return items

    def _set_all_pickable(self, pickable: bool):
        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not ctx or not stage:
            return

        root = stage.GetPseudoRoot()

        for prim in Usd.PrimRange(root):
            p = prim.GetPath().pathString
            try:
                ctx.set_pickable(p, bool(pickable))
            except Exception:
                pass
            self._overrides[p] = bool(pickable)

        self.refresh_cache()

    # ---------------- stage events (debounced) ----------------
    def _request_debounced_refresh(self, delay_sec: float = 0.15):
        if self._stopped:
            return
        self._debounce_requested = True

        if self._debounce_task and not self._debounce_task.done():
            return

        async def _run():
            app = omni.kit.app.get_app()
            await app.next_update_async()
            await asyncio.sleep(delay_sec)
            self._debounce_requested = False
            self.refresh_cache()

        self._debounce_task = asyncio.ensure_future(_run())

    def _on_stage_event(self, event):
        if StageEventType is None or not self.enabled:
            return

        et = int(event.type)

        def _is(ev_name: str) -> bool:
            try:
                return et == int(getattr(StageEventType, ev_name))
            except Exception:
                return False

        if _is("OPENED") or _is("OPENED_STAGE") or _is("ASSETS_LOADED"):
            self.refresh_cache()
            return

        self._request_debounced_refresh(delay_sec=0.15)