# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
Kit 기본 크롬(메뉴바·툴바·콘솔 등) 표시 제어.

TBS 제어창·시퀀스 편집기·Viewport·멀티 시뮼 분할 보조 창(``TBS_SimSplit_*``)은 숨기지 않는다.

표시/복원 직후 ``schedule_split_layout_refresh_for_chrome_change(ext, hidden)`` 로
Dock 이 안정된 뒤 1분할은 ``Viewport.dock_in(DockSpace, …)``(또는 사각형 폴백)으로 채우고,
Dock 분할(2~4)은 기존 ``dock_in`` 비율을 다시 적용한 뒤 각 타일 ``resolution`` 을 맞춘다.
격자(비 Dock) 분할은 타일 합집합 기준으로 다시 맞춘다.

【시뮼 분할·제어창과의 연동】
- 메뉴/툴바 등 표시 상태가 바뀌면 제어창 쪽에서 ``sim_multi_view.schedule_split_layout_refresh_for_chrome_change`` 를 호출해
  뷰포트 Dock 분할(2~4)의 기하·해상도를 다시 맞춘다. 시뮼 **엔진·tick 독립** 로직은 ``control_window`` 에 있다.

런치 시 기본으로 메뉴 숨김을 켤지는 아래 상수 한 곳만 바꾸면 됨
(True: 체크됨 + 시작 후 자동 적용 / False: 체크 해제·기본 Kit UI).

【본 파일 함수 역할】
- ``_window_label`` / ``_should_protect_window`` : Workspace 창 라벨 추출·TBS/Viewport/분할 보조 창 보호 판단.
- ``_get_main_menu_bar`` : 메인 메뉴바 핸들 조회.
- ``_as_window`` / ``_unwrap_window_handle`` / ``_set_window_visible`` : omni.ui 윈도우 래핑·표시 토글.
- ``_iter_workspace_windows`` : 워크스페이스 등록 창 순회.
- ``apply_kit_chrome_hidden`` : 숨김 정책 적용 후 분할 레이아웃 재스케줄(``ext`` 필요).
- ``is_kit_chrome_hidden`` : 현재 숨김 상태 조회(모델 기준).
"""

from __future__ import annotations

# 런치 시 「기본 메뉴·패널 숨기기」 체크 상태 및 자동 적용 여부.
# 추후 기본을 "숨기지 않음"으로 바꿀 때는 False 로만 바꾸면 됨.
KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH = False

# extension 인스턴스 속성 슬롯 (비밀키 아님 — SAST 하드코딩 오탐 방지용 모듈 상수)
_EXT_ATTR_CHROME_BACKUP = "_kit_chrome_visibility_backup"
_EXT_ATTR_CHROME_HIDE_ACTIVE = "_kit_chrome_hide_active"

from typing import Any, Dict, List, Set

import carb.settings
import omni.ui as ui

_PROTECTED_TITLES = frozenset(
    {
        "TBS 제어창",
        "EBS제어창(CASE A)",
        "EBS제어창(CASE B)",
        "EBS 제어창",
        "TBS 시퀀스 편집기",
        "Viewport",
    }
)

# sim_multi_view._split_window_name → Workspace 창 이름 ``TBS_SimSplit_1`` …
_PROTECTED_NAME_PREFIXES = ("TBS_SimSplit", "tbs_simsplit")

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
    stripped = label.strip()
    for pref in _PROTECTED_NAME_PREFIXES:
        if stripped.startswith(pref):
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


def _as_window(obj: Any) -> Any:
    """
    Workspace API가 버전에 따라 Window 또는 WindowHandle을 반환할 수 있어,
    가능하면 'Window(실체)'를 얻어 visible을 토글한다.
    """
    if obj is None:
        return None
    # 이미 Window처럼 보이면 그대로 사용
    try:
        if hasattr(obj, "visible") and (hasattr(obj, "title") or hasattr(obj, "name")):
            return obj
    except Exception:
        pass
    # WindowHandle → window / get_window 패턴들 시도
    try:
        w = getattr(obj, "window", None)
        if w is not None:
            try:
                if callable(w):
                    w = w()
            except Exception:
                pass
            if w is not None and hasattr(w, "visible"):
                return w
    except Exception:
        pass
    try:
        gw = getattr(obj, "get_window", None)
        if callable(gw):
            w = gw()
            if w is not None and hasattr(w, "visible"):
                return w
    except Exception:
        pass
    return obj


def _unwrap_window_handle(w: Any) -> Any:
    """WindowHandle → 실제 Window 등 visible 안전 대상으로 한 단계 더 풀기."""
    if w is None:
        return None
    try:
        tn = type(w).__name__
        if "Handle" not in tn:
            return w
        for attr in ("window", "ui_window"):
            ch = getattr(w, attr, None)
            if callable(ch):
                try:
                    ch = ch()
                except Exception:
                    ch = None
            if ch is not None and hasattr(ch, "visible") and "Handle" not in type(ch).__name__:
                return ch
    except Exception:
        pass
    return w


def _set_window_visible(obj: Any, visible: bool) -> bool:
    """
    가시성 토글을 'Window(실체)' 기준으로 적용.
    - 가능한 경우 .visible 에 직접 대입 (Window API)
    - 그래도 안 되면 fallback으로 setVisible/set_visible을 시도
    """
    w = _as_window(obj)
    if w is None:
        return False
    w = _unwrap_window_handle(w) or w
    try:
        w.visible = bool(visible)
        return True
    except Exception:
        pass
    try:
        fn = getattr(w, "set_visible", None)
        if callable(fn):
            fn(bool(visible))
            return True
    except Exception:
        pass
    try:
        if "Handle" not in type(w).__name__:
            fn = getattr(w, "setVisible", None)
            if callable(fn):
                fn(bool(visible))
                return True
    except Exception:
        pass
    return False


def _iter_workspace_windows() -> List[Any]:
    out: List[Any] = []
    seen_labels: Set[str] = set()
    try:
        if hasattr(ui.Workspace, "get_windows"):
            wins = ui.Workspace.get_windows()
            if wins:
                for w in wins:
                    ww = _as_window(w)
                    label = _window_label(ww)
                    if not label:
                        continue
                    if label not in seen_labels:
                        seen_labels.add(label)
                        out.append(ww)
    except Exception:
        pass
    for name in _DEFAULT_PANEL_NAMES:
        try:
            w = ui.Workspace.get_window(name)
            ww = _as_window(w)
            label = _window_label(ww)
            if label and label not in seen_labels:
                seen_labels.add(label)
                out.append(ww)
        except Exception:
            pass
    return out


def apply_kit_chrome_hidden(ext: Any, hidden: bool, *, schedule_layout_refresh: bool = True) -> None:
    """
    hidden=True: 기본 메뉴바·상태줄·알려진 패널 창을 숨김. TBS/시퀀스/Viewport·TBS_SimSplit_* 유지.
    hidden=False: 직전 백업으로 복원(없으면 메뉴만 보이게 시도).
    """
    try:
        if hidden:
            backup: Dict[str, Any] = {"__wins__": {}}
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
                    # label 기반으로 저장 (WindowHandle 교체/재생성에 강하게)
                    wins = backup.get("__wins__", {})
                    if isinstance(wins, dict) and label:
                        wins[label] = bool(getattr(w, "visible", True))
                    _set_window_visible(w, False)
                except Exception:
                    pass

            setattr(ext, _EXT_ATTR_CHROME_BACKUP, backup)
            setattr(ext, _EXT_ATTR_CHROME_HIDE_ACTIVE, True)
        else:
            backup = getattr(ext, _EXT_ATTR_CHROME_BACKUP, None)
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
                wins = backup.get("__wins__", {})
                if isinstance(wins, dict) and label in wins:
                    try:
                        _set_window_visible(w, bool(wins[label]))
                    except Exception:
                        pass

            try:
                delattr(ext, _EXT_ATTR_CHROME_BACKUP)
            except Exception:
                setattr(ext, _EXT_ATTR_CHROME_BACKUP, None)
            try:
                delattr(ext, _EXT_ATTR_CHROME_HIDE_ACTIVE)
            except Exception:
                setattr(ext, _EXT_ATTR_CHROME_HIDE_ACTIVE, False)
    finally:
        try:
            from . import sim_multi_view as _smv

            if hidden:
                # Dock/Workspace 재배치 전에도 fill_frame 을 켜 두면 고정 resolution 3D가
                # UI 확장을 따라가기 시작한다(지연 레이아웃 태스크와 병행).
                try:
                    _smv.set_viewport_fill_frame_for_split_count(
                        int(getattr(ext, "_sim_viewport_split_count", 1) or 1), True
                    )
                except Exception:
                    pass
            if schedule_layout_refresh:
                _smv.schedule_split_layout_refresh_for_chrome_change(ext, bool(hidden))
        except Exception:
            pass


def is_kit_chrome_hidden(ext: Any) -> bool:
    return bool(getattr(ext, _EXT_ATTR_CHROME_HIDE_ACTIVE, False))


def is_streaming_deployment() -> bool:
    """``morph.editor_streaming`` 등 livestream 배포 Kit 인지."""
    try:
        settings = carb.settings.get_settings()
        if settings and bool(settings.get("/app/morph/streamingUi")):
            return True
    except Exception:
        pass
    try:
        import omni.kit.app as kit_app

        em = kit_app.get_app().get_extension_manager()
        if em is not None and em.is_extension_enabled("omni.kit.livestream.app"):
            return True
    except Exception:
        pass
    return False


def ensure_streaming_window_resize_enabled() -> None:
    """legacy — windowed streaming kit 은 livestream.app 을 로드하지 않음."""
    pass


def _is_viewport_or_split_window(label: str) -> bool:
    if not label:
        return False
    if label.strip() == "Viewport":
        return True
    stripped = label.strip()
    for pref in _PROTECTED_NAME_PREFIXES:
        if stripped.startswith(pref):
            return True
    return False


def _set_dock_tab_bar_hidden_on_window(w: Any) -> None:
    ww = _as_window(w)
    if ww is None:
        return
    for attr in ("dock_tab_bar_enabled", "dock_tab_bar_visible"):
        try:
            if hasattr(ww, attr):
                setattr(ww, attr, False)
        except Exception:
            pass
    try:
        ww.noTabBar = True
    except Exception:
        pass


def apply_viewport_dock_tab_bars_hidden() -> None:
    """Viewport·TBS_SimSplit_* 의 Dock 탭 바(뷰포트 1/2·화면 탭) 숨김."""
    seen: Set[str] = set()
    for w in _iter_workspace_windows():
        label = _window_label(w)
        if not _is_viewport_or_split_window(label):
            continue
        if label in seen:
            continue
        seen.add(label)
        _set_dock_tab_bar_hidden_on_window(w)
    for name in ("Viewport", "TBS_SimSplit_1", "TBS_SimSplit_2", "TBS_SimSplit_3"):
        try:
            w = ui.Workspace.get_window(name)
            if w is not None:
                _set_dock_tab_bar_hidden_on_window(w)
        except Exception:
            pass
