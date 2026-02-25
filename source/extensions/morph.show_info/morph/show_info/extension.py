# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""Show Prim Info: 3D panel next to selected prims with full prim information."""

# 모듈 로드 시점 로그 (이 줄도 안 보이면 확장이 아예 로드되지 않는 것)
import sys
print("[morph.show_info] extension.py 모듈 로드됨", flush=True)
sys.stdout.flush()
sys.stderr.flush()

import time
from typing import List, Optional

import carb
# stdout이 앱에서 보이지 않을 수 있으므로 Kit 로그에도 기록 (확장 로드 여부 확인용)
carb.log_info("[morph.show_info] extension.py 모듈 로드됨")
import omni.ext
import omni.kit.app as app
import omni.usd as ou
from carb.eventdispatcher import get_eventdispatcher
from omni.kit.viewport.utility import get_active_viewport_window

from .viewport_overlay import PrimInfoOverlay

# 뷰포트 창이 아직 없을 수 있으므로 재시도할 최대 프레임 수
_VIEWPORT_RETRY_FRAMES = 180  # 약 3초
# 폴링: N프레임마다 선택 상태 확인 (이벤트가 안 오는 환경 대비)
_POLL_FRAME_INTERVAL = 30  # 약 0.5초


def _post_update_once(callback):
    """다음 post_update 한 번에 callback을 실행한 뒤 구독 해제. (post_update_call 대체용)
    뷰포트가 아직 없을 때 다음 프레임에 재시도하거나, UI 스레드에서 패널을 갱신할 때 사용."""
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
        _on_event, name="morph.show_info:PostUpdateOnce"
    )
    return sub_ref[0]


_extension_instance: Optional["Extension"] = None


def get_instance() -> Optional["Extension"]:
    """싱글톤: 현재 로드된 확장 인스턴스를 반환. 다른 확장·웹 핸들러에서 공개 API 호출 시 사용. 비활성 시 None."""
    return _extension_instance


class Extension(omni.ext.IExt):
    def on_startup(self, ext_id: str) -> None:
        """확장 활성화 시 호출. 선택 이벤트 구독, 뷰포트 오버레이 연결, 싱글톤 등록."""
        global _extension_instance
        _extension_instance = self

        # 터미널/콘솔에서 바로 보이도록 print + carb 동시 사용
        print("[morph.show_info] Extension on_startup")  # noqa: T201
        carb.log_info("[morph.show_info] Extension on_startup")

        self._ext_id = ext_id
        self._overlay: Optional[PrimInfoOverlay] = None
        self._open_paths: List[str] = []  # prim paths to show panels for (persist until user closes with X)
        self._selection_sub = None
        self._stage_stream_sub = None
        self._post_update_sub = None
        self._retry_count = 0
        self._poll_frame = 0
        self._last_paths: tuple = ()  # previous selection for change detection
        self._ignore_selection_until = 0.2  # X 클릭 직후 0.2초간 선택 이벤트 무시 (뷰포트 픽 되돌리기)

        ctx = ou.get_context()

        # 방법 1: observe_event 로 SELECTION_CHANGED 구독
        ed = get_eventdispatcher()
        try:
            event_name = ctx.stage_event_name(ou.StageEventType.SELECTION_CHANGED)
            print(f"[morph.show_info] 구독 이벤트명: {event_name}")  # noqa: T201
            carb.log_info(f"[morph.show_info] 구독 이벤트명: {event_name}")
            self._selection_sub = ed.observe_event(
                observer_name="morph.show_info:SelectionChanged",
                event_name=event_name,
                on_event=self._on_selection_changed,
            )
        except Exception as e:
            carb.log_warn(f"[morph.show_info] observe_event 실패: {e}")

        # 방법 2: 스테이지 이벤트 스트림 전체 구독
        try:
            self._stage_stream_sub = ctx.get_stage_event_stream().create_subscription_to_pop(
                self._on_stage_event,
                name="morph.show_info:StageEvents",
            )
        except Exception as e:
            carb.log_warn(f"[morph.show_info] stage_event_stream 구독 실패: {e}")

        # 방법 3: post_update 폴링 — SELECTION_CHANGED가 안 와도 주기적으로 선택 확인
        try:
            self._post_update_sub = app.get_app().get_post_update_event_stream().create_subscription_to_pop(
                self._on_post_update,
                name="morph.show_info:PostUpdate",
            )
        except Exception as e:
            carb.log_warn(f"[morph.show_info] post_update 구독 실패: {e}")

        self._try_attach_overlay()

    def _on_post_update(self, event) -> None:
        """매 프레임 호출. N프레임마다 현재 선택 경로를 읽어 이벤트로 선택 변경가 안 오는 환경에서도 패널을 갱신하고, X 클릭 직후 무시 구간에서는 선택을 _open_paths로 되돌림."""
        if time.time() < self._ignore_selection_until:
            try:
                ou.get_context().get_selection().set_selected_prim_paths(self._open_paths, True)
            except Exception:
                pass
            self._last_paths = tuple(self._open_paths)
            if self._overlay:
                self._overlay.set_open_paths(self._open_paths)
                self._overlay.update_panels()
            return
        self._poll_frame += 1
        if self._poll_frame % _POLL_FRAME_INTERVAL != 0:
            return
        try:
            paths = tuple(ou.get_context().get_selection().get_selected_prim_paths() or [])
        except Exception:
            paths = ()
        if paths != self._last_paths:
            self._last_paths = paths
            self._add_selection_to_open_paths(paths)
            if paths:
                print(f"[morph.show_info] (폴링) 선택됨: {len(paths)}개 — {[str(p) for p in paths]}")  # noqa: T201
                carb.log_info(f"[morph.show_info] (폴링) 선택됨: {len(paths)}개 — {paths}")
            self._apply_selection(paths)

    def _on_stage_event(self, event) -> None:
        """스테이지 이벤트 스트림에서 온 이벤트(선택 변경 등)를 선택 변경 핸들러로 넘김."""
        self._on_selection_changed(event)

    def _try_attach_overlay(self) -> None:
        """활성 뷰포트 창을 찾아 PrimInfoOverlay를 생성·연결. 창이 없으면 다음 프레임에 재시도(최대 _VIEWPORT_RETRY_FRAMES)."""
        viewport_window = get_active_viewport_window()
        if viewport_window:
            if self._overlay is None:
                print("[morph.show_info] 뷰포트 창 연결됨, 오버레이 생성")  # noqa: T201
                carb.log_info("[morph.show_info] 뷰포트 창 연결됨, 오버레이 생성")
                self._overlay = PrimInfoOverlay(viewport_window, self._ext_id)
                self._overlay.set_on_close(self._on_close_panel)
                self._overlay.set_open_paths(self._open_paths)
                self._overlay.build_scene()
                self._overlay.update_panels()
            return
        self._retry_count += 1
        if self._retry_count < _VIEWPORT_RETRY_FRAMES:
            _post_update_once(self._try_attach_overlay)
        elif self._retry_count == _VIEWPORT_RETRY_FRAMES:
            print("[morph.show_info] 뷰포트 창을 찾지 못함 (재시도 한도 도달)")  # noqa: T201
            carb.log_warn("[morph.show_info] 뷰포트 창을 찾지 못함 (재시도 한도 도달)")

    def _add_selection_to_open_paths(self, paths) -> None:
        """현재 선택된 prim 경로를 열린 패널 목록(_open_paths)에 추가. 중복 제외. 드래그로 여러 개 선택 시 첫 번째만 추가해 패널 1개만 표시."""
        path_strs = [str(p) for p in (paths or []) if p is not None]
        if len(path_strs) > 1:
            path_strs = path_strs[:1]  # 다중 선택 시 첫 번째 프림만 패널로 표시
        for p in path_strs:
            if p and p not in self._open_paths:
                self._open_paths.append(p)

    def _on_close_panel(self, path_str: str) -> None:
        """패널 X 버튼 클릭 시 호출. 해당 경로를 _open_paths에서 제거하고 선택·오버레이 갱신. X 클릭 직후 0.2초간 선택 이벤트 무시로 뒤 프림 선택 방지."""
        # X 클릭 직후 0.2초간 선택 변경 무시 — 뷰포트가 그 위치 프림을 선택해도 _open_paths로 되돌림
        self._ignore_selection_until = time.time() + 0.2

        if path_str in self._open_paths:
            self._open_paths.remove(path_str)
        try:
            sel = ou.get_context().get_selection()
            sel.set_selected_prim_paths(self._open_paths, True)
        except Exception:
            pass
        self._last_paths = tuple(self._open_paths)
        if self._overlay:
            self._overlay.update_panels()

    def _apply_selection(self, paths) -> None:
        """오버레이가 없으면 연결 시도 후, _open_paths 기준으로 오버레이에 열린 경로를 넘기고 3D 패널을 다시 그림."""
        if self._overlay is None:
            _post_update_once(self._try_attach_overlay)
        if self._overlay:
            self._overlay.set_open_paths(self._open_paths)
            self._overlay.update_panels()

    def _on_selection_changed(self, event) -> None:
        """뷰포트 선택 변경 시 호출. 무시 구간이면 선택을 _open_paths로 되돌리고, 아니면 현재 선택을 _open_paths에 반영한 뒤 패널 갱신."""
        if time.time() < self._ignore_selection_until:
            try:
                ou.get_context().get_selection().set_selected_prim_paths(self._open_paths, True)
            except Exception:
                pass
            self._last_paths = tuple(self._open_paths)
            if self._overlay:
                self._overlay.set_open_paths(self._open_paths)
                self._overlay.update_panels()
            return
        try:
            paths = ou.get_context().get_selection().get_selected_prim_paths()
        except Exception as e:
            carb.log_warn(f"[morph.show_info] get_selected_prim_paths 실패: {e}")
            paths = []
        if paths:
            print(f"[morph.show_info] (이벤트) 선택 변경됨: {len(paths)}개 — {paths}")  # noqa: T201
            carb.log_info(f"[morph.show_info] (이벤트) 선택 변경됨: {len(paths)}개 — {paths}")
        self._last_paths = tuple(paths or [])
        self._add_selection_to_open_paths(paths or [])
        self._apply_selection(paths)

    def on_shutdown(self) -> None:
        """확장 비활성화 시 호출. 싱글톤 해제, 선택·스테이지·post_update 구독 해제, 오버레이 제거."""
        global _extension_instance
        _extension_instance = None

        if self._selection_sub is not None and hasattr(self._selection_sub, "release"):
            self._selection_sub.release()
            self._selection_sub = None
        if self._stage_stream_sub is not None:
            try:
                self._stage_stream_sub.unsubscribe()
            except Exception:
                pass
            self._stage_stream_sub = None
        if self._post_update_sub is not None:
            try:
                self._post_update_sub.unsubscribe()
            except Exception:
                pass
            self._post_update_sub = None
        if self._overlay:
            self._overlay.destroy()
            self._overlay = None
