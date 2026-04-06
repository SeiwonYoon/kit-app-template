# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
Kit 기본 크롬(메뉴바·툴바·콘솔 등) 표시 제어.

TBS 제어창·시퀀스 편집기·Viewport 는 숨기지 않는다.

런치 시 기본으로 메뉴 숨김을 켤지는 아래 상수 한 곳만 바꾸면 됨
(True: 체크됨 + 시작 후 자동 적용 / False: 체크 해제·기본 Kit UI).
"""

from __future__ import annotations

# 런치 시 「기본 메뉴·패널 숨기기」 체크 상태 및 자동 적용 여부.
# 추후 기본을 "숨기지 않음"으로 바꿀 때는 False 로만 바꾸면 됨.
KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH = False

from typing import Any, Dict, List, Set

import carb.settings
import omni.ui as ui

_PROTECTED_TITLES = frozenset(
    {
        "TBS 제어창",
        "TBS 시퀀스 편집기",
        "Viewport",
    }
)

# Dock/레이아웃 골격은 건드리지 않음
_DOCK_SKIP_SUBSTR = ("dockspace", "dock", "main dock")

# 이름으로 직접 숨길 기본 Kit 창(있을 때만)
_DEFAULT_PANEL_NAMES = (
    "Console",
    "Toolbar",
    "Status Bar",
    "Stage",
    "Property",
    "Content",
    "Layer",
    "Statistics",
    "Render Settings",
    "Content Browser",
    "USD Composer",
)


def _window_label(w: Any) -> str:
    try:
        t = (getattr(w, "title", None) or "").strip()
        if t:
            return t
        n = (getattr(w, "name", None) or "").strip()
        return n or ""
    except Exception:
        return ""


def _should_protect_window(label: str) -> bool:
    if not label:
        return True
    low = label.lower()
    if label in _PROTECTED_TITLES:
        return True
    for s in _DOCK_SKIP_SUBSTR:
        if s in low:
            return True
    return False


def _get_main_menu_bar():
    try:
        from omni.kit.mainwindow import get_main_window

        mw = get_main_window()
        if mw is None:
            return None
        return mw.get_main_menu_bar()
    except Exception:
        return None


def _iter_workspace_windows() -> List[Any]:
    out: List[Any] = []
    seen: Set[int] = set()
    try:
        if hasattr(ui.Workspace, "get_windows"):
            wins = ui.Workspace.get_windows()
            if wins:
                for w in wins:
                    try:
                        i = id(w)
                        if i not in seen:
                            seen.add(i)
                            out.append(w)
                    except Exception:
                        pass
    except Exception:
        pass
    for name in _DEFAULT_PANEL_NAMES:
        try:
            w = ui.Workspace.get_window(name)
            if w is not None:
                i = id(w)
                if i not in seen:
                    seen.add(i)
                    out.append(w)
        except Exception:
            pass
    return out


def apply_kit_chrome_hidden(ext: Any, hidden: bool) -> None:
    """
    hidden=True: 기본 메뉴바·상태줄·알려진 패널 창을 숨김. TBS/시퀀스/Viewport 유지.
    hidden=False: 직전 백업으로 복원(없으면 메뉴만 보이게 시도).
    """
    key = "_kit_chrome_visibility_backup"
    flag = "_kit_chrome_hide_active"
    if hidden:
        backup: Dict[str, Any] = {}
        mb = _get_main_menu_bar()
        if mb is not None:
            try:
                backup["__menubar_visible__"] = bool(mb.visible)
                mb.visible = False
            except Exception:
                pass

        try:
            settings = carb.settings.get_settings()
            if settings:
                try:
                    backup["__statusbar_setting__"] = settings.get("/app/window/showStatusBar")
                except Exception:
                    backup["__statusbar_setting__"] = None
                settings.set("/app/window/showStatusBar", False)
        except Exception:
            pass

        for w in _iter_workspace_windows():
            label = _window_label(w)
            if _should_protect_window(label):
                continue
            try:
                k = f"win:{label}" if label else f"id:{id(w)}"
                backup[k] = bool(w.visible)
                w.visible = False
            except Exception:
                pass

        setattr(ext, key, backup)
        return

    backup = getattr(ext, key, None)
    if not isinstance(backup, dict):
        backup = {}

    mb = _get_main_menu_bar()
    if mb is not None:
        try:
            if "__menubar_visible__" in backup:
                mb.visible = bool(backup["__menubar_visible__"])
            else:
                mb.visible = True
        except Exception:
            pass

    try:
        settings = carb.settings.get_settings()
        if settings and "__statusbar_setting__" in backup:
            v = backup["__statusbar_setting__"]
            if v is not None:
                settings.set("/app/window/showStatusBar", v)
            else:
                settings.set("/app/window/showStatusBar", True)
    except Exception:
        pass

    for w in _iter_workspace_windows():
        label = _window_label(w)
        if _should_protect_window(label):
            continue
        k = f"win:{label}" if label else f"id:{id(w)}"
        if k in backup:
            try:
                w.visible = bool(backup[k])
            except Exception:
                pass

    try:
        delattr(ext, key)
    except Exception:
        setattr(ext, key, None)
    try:
        delattr(ext, flag)
    except Exception:
        setattr(ext, flag, False)


def is_kit_chrome_hidden(ext: Any) -> bool:
    return bool(getattr(ext, "_kit_chrome_hide_active", False))
