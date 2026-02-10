# morph/pick_filter/core.py
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import asyncio
from typing import Dict, Any, List, Optional

import carb
import omni.usd
import omni.kit.app
from pxr import Usd

try:
    from omni.usd import StageEventType
except Exception:
    StageEventType = None


def _log_info(msg: str):
    carb.log_info(f"[morph.pick_filter] {msg}")


def _log_warn(msg: str):
    carb.log_warn(f"[morph.pick_filter] {msg}")


class PickFilterService:
    """
    Pickable 제어 서비스.

    ✅ 핵심 수정:
    - refresh_cache 시 override가 없는 prim은 '현재 stage pickable'을 getter로 읽어서 표시
      (초기 전체락 상태와 UI가 싱크되도록)
    - set_pickable 시 set 후 read-back 해서 실제 적용값으로 override/cache를 보정
      (토글 반영 안 되고 되돌아가는 현상 방지)
    """

    def __init__(self):
        self._sub = None
        self._starting_task = None
        self._stopped = False

        self.enabled = True
        self.root_path = "/World"
        self.limit = 50000

        # overrides: path -> pickable(bool)
        self._overrides: Dict[str, bool] = {}

        self._cached_items: List[Dict[str, Any]] = []
        self._revision: int = 0

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

    async def _start_when_usd_ready(self):
        app = omni.kit.app.get_app()
        while not self._stopped:
            ctx = omni.usd.get_context()
            if ctx is not None:
                try:
                    stream = ctx.get_stage_event_stream()
                    self._sub = stream.create_subscription_to_pop(self._on_stage_event)
                    self.refresh_cache()
                    _log_info("USD context ready -> pickability service started")
                    return
                except Exception as e:
                    _log_warn(f"start failed, retry next frame: {e}")
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
        """
        ✅ set 후 read-back으로 실제 적용값을 반영
        - set 실패/무시(다른 시스템이 강제락 등)면 read-back 값이 원래대로일 수 있음
          => UI도 실제 값으로 유지 (되돌아감이 아니라 "적용 실패를 정확히 표시")
        """
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
            # 1) set 시도
            try:
                ctx.set_pickable(p, bool(pickable))
            except Exception as e:
                _log_warn(f"ctx.set_pickable failed: {p} -> {pickable} ({e})")

            # 2) read-back (가능하면)
            actual = self._try_get_pickable(ctx, p)
            if actual is None:
                # getter가 없으면 일단 요청값을 override로
                actual = bool(pickable)

            # 3) override를 실제값으로 저장
            self._overrides[p] = bool(actual)

        # 4) 캐시 갱신(실제 stage/override 기준)
        self.refresh_cache()

    def lock_all(self):
        self._set_all_pickable(False)

    def unlock_all(self):
        self._set_all_pickable(True)

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
        display = ""
        try:
            display = prim.GetDisplayName() or ""
        except Exception:
            display = ""

        if not display:
            try:
                if prim.HasAttribute("displayName"):
                    v = prim.GetAttribute("displayName").Get()
                    display = v if isinstance(v, str) else (str(v) if v is not None else "")
            except Exception:
                pass
        return display or ""

    @staticmethod
    def _try_get_pickable(ctx, path: str) -> Optional[bool]:
        """
        Kit 버전 차이 대응: 가능한 getter를 모두 시도.
        성공하면 bool 반환, 없거나 실패하면 None.
        """
        for fn_name in ("is_pickable", "get_pickable", "get_pickable_state"):
            fn = getattr(ctx, fn_name, None)
            if not fn:
                continue
            try:
                return bool(fn(path))
            except Exception:
                continue
        return None

    def _effective_pickable(self, ctx, path: str) -> bool:
        """
        ✅ UI 표시용 pickable 결정 로직
        1) override 있으면 override (사용자/전체락 반영)
        2) 없으면 stage에서 getter로 읽기 (초기 전체락도 여기서 싱크)
        3) getter도 없으면 True 기본
        """
        if path in self._overrides:
            return bool(self._overrides[path])

        v = self._try_get_pickable(ctx, path)
        if v is None:
            return True
        return bool(v)

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
            name = prim.GetName() or "(no-name)"
            disp = self._get_display_text(prim)
            tname = prim.GetTypeName() or ""

            pickable = self._effective_pickable(ctx, p)

            items.append(
                {
                    "path": p,
                    "name": name,
                    "display": disp,
                    "type": tname,
                    "depth": depth,
                    "pickable": bool(pickable),
                    "overridden": (p in self._overrides),
                }
            )

            count += 1
            if limit > 0 and count >= limit:
                break

        return items

    def _set_all_pickable(self, pickable: bool):
        """
        전체락/전체언락:
        - set 시도
        - read-back 있으면 실제값으로 override 저장
        - 마지막에 refresh_cache
        """
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

            actual = self._try_get_pickable(ctx, p)
            if actual is None:
                actual = bool(pickable)

            self._overrides[p] = bool(actual)

        self.refresh_cache()

    # ---------------- stage events ----------------
    def _on_stage_event(self, event):
        if StageEventType is None:
            return
        if not self.enabled:
            return

        et = int(event.type)

        def _is(ev_name: str) -> bool:
            try:
                return et == int(getattr(StageEventType, ev_name))
            except Exception:
                return False

        # stage가 열리거나 리로드되면: overrides 우선 + stage getter로 싱크되는 캐시 갱신
        if _is("OPENED") or _is("OPENED_STAGE") or _is("ASSETS_LOADED"):
            self.refresh_cache()
            return

        if _is("PRIMS_CHANGED") or _is("HIERARCHY_CHANGED"):
            self.refresh_cache()
            return
