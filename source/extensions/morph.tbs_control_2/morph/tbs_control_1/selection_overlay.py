# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
selection_overlay.py — 뷰포트 선택 연동 및 3D 정보 오버레이 연결.

기능:
- post_update_once(callback): 다음 post_update에서 callback 한 번 실행 후 구독 해제.
- try_attach_overlay(ext): 활성 뷰포트에 PrimInfoOverlay 연결. 뷰포트 없으면 다음 프레임 재시도.
- on_close_info_panel(ext, path_str): 3D 패널 X 클릭 시 해당 경로 제거, 선택 복원, 패널 갱신.
- on_post_update(ext, event): X 클릭 직후에는 선택 유지, 그 외 N프레임마다 뷰포트 선택 반영.
- on_selection_changed(ext, event): 뷰포트 선택 변경 시 _open_paths 반영 및 패널 갱신.
- add_selection_to_open_paths(ext, paths): 기존 패널 제거, 선택한 객체 1개만 3D 패널에 표시.
- apply_selection(ext): _open_paths 기준으로 오버레이 패널 갱신.
- show_prim_info_in_viewport(ext, prim_path): 기존 패널 제거, 해당 prim 1개만 3D 패널 표시.

【수정 가이드】
- 뷰포트 재시도 횟수: VIEWPORT_RETRY_FRAMES
- 선택 폴링: POLL_FRAME_INTERVAL
- 패널 내용/레이아웃: viewport_overlay.PrimInfoOverlay + prim_info 상수

사용처: extension.py

【유지보수 시나리오】
1) 선택 동기화가 늦거나 튀는 경우
   - on_post_update의 _poll_frame 주기/POLL_FRAME_INTERVAL 조정
   - on_selection_changed와 add_selection_to_open_paths 호출 순서 확인
2) 패널 닫기/선택 복원 이슈
   - on_close_info_panel 내부 _open_paths/_last_paths 갱신 로직 확인
   - viewport_overlay의 on_close 콜백 연결 지점(try_attach_overlay) 확인
3) 다중 선택 정책 변경(현재는 1개 중심)
   - add_selection_to_open_paths / show_prim_info_in_viewport 로직 확장
   - control_window의 prim 선택 동작과 UX 일관성 검토
"""

import time
from typing import Any, List

import omni.kit.app as app
import omni.ui as ui
import omni.usd as ou
from omni.kit.viewport.utility import get_active_viewport_window

from .viewport_overlay import PrimInfoOverlay

# 뷰포트 연결 재시도 횟수
VIEWPORT_RETRY_FRAMES = 180
# 선택 폴링 주기 (SELECTION_CHANGED 없는 환경 대비)
POLL_FRAME_INTERVAL = 30


def post_update_once(callback):
    """다음 post_update에서 callback 한 번 실행 후 구독 해제."""
    sub_ref = [None]
    def _on_event(_event):
        try:
            callback()
        finally:
            if sub_ref[0] is not None:
                sub_ref[0].unsubscribe()
                sub_ref[0] = None
    stream = app.get_app().get_post_update_event_stream()
    sub_ref[0] = stream.create_subscription_to_pop(
        _on_event, name="morph.tbs_control_1:PostUpdateOnce"
    )
    return sub_ref[0]


def try_attach_overlay(ext: Any) -> None:
    """활성 뷰포트에 3D 정보 오버레이 연결. 뷰포트 없으면 다음 프레임 재시도."""
    viewport_window = get_active_viewport_window()
    if viewport_window:
        if ext._overlay is None:
            ext._overlay = PrimInfoOverlay(viewport_window, ext._ext_id)
            ext._overlay.set_on_close(lambda path_str: on_close_info_panel(ext, path_str))
            ext._overlay.set_open_paths(ext._open_paths)
            ext._overlay.build_scene()
            ext._overlay.update_panels()
        return
    ext._overlay_retry_count = getattr(ext, "_overlay_retry_count", 0) + 1
    if ext._overlay_retry_count < VIEWPORT_RETRY_FRAMES:
        post_update_once(lambda: try_attach_overlay(ext))


def on_close_info_panel(ext: Any, path_str: str) -> None:
    """3D 패널 X 클릭 시 해당 경로 제거, 뷰포트 선택을 _open_paths로 복원, 패널 갱신."""
    ext._ignore_selection_until = time.time() + 0.2
    if path_str in ext._open_paths:
        ext._open_paths.remove(path_str)
    try:
        sel = ou.get_context().get_selection()
        sel.set_selected_prim_paths(ext._open_paths, True)
    except Exception:
        pass
    ext._last_paths = tuple(ext._open_paths)
    if ext._overlay:
        ext._overlay.set_open_paths(ext._open_paths)
        ext._overlay.update_panels()


def on_post_update(ext: Any, _event) -> None:
    """매 프레임: X 클릭 직후에는 선택을 _open_paths로 유지, 그 외 N프레임마다 뷰포트 선택 반영."""
    if time.time() < ext._ignore_selection_until:
        try:
            ou.get_context().get_selection().set_selected_prim_paths(ext._open_paths, True)
        except Exception:
            pass
        ext._last_paths = tuple(ext._open_paths)
        if ext._overlay:
            ext._overlay.set_open_paths(ext._open_paths)
            ext._overlay.update_panels()
        return
    ext._poll_frame = getattr(ext, "_poll_frame", 0) + 1
    if ext._poll_frame % POLL_FRAME_INTERVAL != 0:
        return
    try:
        paths = tuple(ou.get_context().get_selection().get_selected_prim_paths() or [])
    except Exception:
        paths = ()
    if paths != ext._last_paths:
        ext._last_paths = paths
        add_selection_to_open_paths(ext, paths)
        apply_selection(ext)


def add_selection_to_open_paths(ext: Any, paths: List) -> None:
    """뷰포트에서 객체 클릭 시 기존 패널 제거, 선택한 객체 1개만 3D 패널에 표시."""
    path_strs = [str(p) for p in (paths or []) if p is not None and str(p).strip()]
    if path_strs:
        ext._open_paths.clear()
        ext._open_paths.append(path_strs[0])


def on_selection_changed(ext: Any, _event) -> None:
    """뷰포트 선택 변경 시 _open_paths 반영 및 3D 패널 갱신."""
    if time.time() < ext._ignore_selection_until:
        try:
            ou.get_context().get_selection().set_selected_prim_paths(ext._open_paths, True)
        except Exception:
            pass
        ext._last_paths = tuple(ext._open_paths)
        if ext._overlay:
            ext._overlay.set_open_paths(ext._open_paths)
            ext._overlay.update_panels()
        return
    try:
        paths = ou.get_context().get_selection().get_selected_prim_paths()
    except Exception:
        paths = []
    ext._last_paths = tuple(paths or [])
    add_selection_to_open_paths(ext, paths or [])
    apply_selection(ext)


def apply_selection(ext: Any) -> None:
    """_open_paths 기준으로 오버레이에 3D 패널 갱신."""
    if ext._overlay is None:
        post_update_once(lambda: try_attach_overlay(ext))
    if ext._overlay:
        ext._overlay.set_open_paths(ext._open_paths)
        ext._overlay.update_panels()


def show_prim_info_in_viewport(ext: Any, prim_path: str) -> None:
    """기존 3D 패널 제거, 해당 prim 1개만 3D 패널에 표시."""
    ext._open_paths.clear()
    ext._open_paths.append(prim_path)
    try:
        sel = ou.get_context().get_selection()
        sel.set_selected_prim_paths([prim_path], True)
    except Exception:
        pass
    if ext._overlay is None:
        def _attach_and_update():
            try_attach_overlay(ext)
            if ext._overlay:
                ext._overlay.set_open_paths(ext._open_paths)
                ext._overlay.update_panels()
        post_update_once(_attach_and_update)
    else:
        ext._overlay.set_open_paths(ext._open_paths)
        ext._overlay.update_panels()
