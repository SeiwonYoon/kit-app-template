# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
TBS Control 1 확장 — 기능별 모듈 분리 버전 (진입점)

【extension.py 역할】
- Omni 확장 IExt: on_startup / on_shutdown.
- 창 조립: control_window.build_control_window(상단에 USD Load 포함), SequenceEditorWindow.
- 선택 이벤트·스테이지 스트림 구독 (selection_overlay), 뷰포트 오버레이 재시도.
- HTTP 브리지(기본 켜짐): 브라우저 원격 패널 ↔ Kit (kit_remote_http_bridge). 끄려면 TBS_REMOTE_UI=0 등(아래 주석).
- 종료 시 모든 애니메이션·타임라인 정지.

【기능을 바꾸려면 어디를 보나】
- 확장 의존성/표시 이름: 상위 폴더 extension.toml (이 모듈과 별개).
- USD 로드 창만: load_window.py / usd_loader_utils.py
- TBS 제어창(타임라인·XML·버튼): control_window.py (+ 필요 시 xml_generator.py 등)
- 시퀀스 스텝 편집/실행: sequence_editor.py + sequence_engine.py
- 뷰포트 3D 정보 패널: selection_overlay.py, viewport_overlay.py
- xform 경고 억제: xform_utils.install_xform_op_order_warning_filter (startup에서 호출)
- 기본 메뉴 숨김 런치 여부: kit_chrome_visibility.KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH (한 곳만 수정)

--------------
import 구조 (요약)
--------------
- load_window → USD Load
- control_window → TBS 제어창
- selection_overlay → 선택·오버레이
- sequence_editor → 시퀀스 편집기
- on_shutdown → translate/curve/rotate/usd_animation 정지

--------------
유지보수 시나리오
--------------
1) "새 이벤트 타입(EAPEIS_PORT_XXX) 추가"
   - xml_generator.py: SEQ_ 상수/빌더/파서 추가
   - control_window.py: XML 콤보/입력 분기 + SIM_SEQ_ALIAS + rules/map 매핑 확인
   - simulation_engine.py: _emit_event(seq=...) 호출 지점 추가
2) "시뮬레이션 공정 로직 변경"
   - simulation_engine.py: 단계 함수/선택 정책(_find_*) 수정
   - control_window.py: UI 입력 항목 전달(on_sim_start_clicked)과 로그 표기 동기화
3) "이벤트별 JSON 애니메이션 연결 변경"
   - config/event_animation_rules.json(권장) 또는 event_animation_map.json 수정
   - control_window.py의 handle_sim_event_for_animation에서 매핑/실행 로그 확인
4) "종료/정리 누락 이슈"
   - 본 파일 on_shutdown에서 스레드/구독/애니메이션 정리 순서 확인
"""

import asyncio
import os
from typing import Any, List, Optional

import omni.ext
import omni.kit.app as app
import omni.ui as ui
import omni.usd as ou
from carb.eventdispatcher import get_eventdispatcher

from .control_window import build_control_window, on_sim_stop_clicked, refresh_object_list
from .kit_chrome_visibility import (
    KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH,
    apply_kit_chrome_hidden,
    is_kit_chrome_hidden,
)
from .curve_animation import stop_prim_curve_animation
from .rotate_animation import stop_prim_rotate_animation
from .selection_overlay import (
    on_selection_changed,
    on_post_update,
    try_attach_overlay,
)
from .sequence_editor import SequenceEditorWindow
from .translate_animation import stop_prim_translate_animation
from . import usd_animation_control
from .viewport_overlay import PrimInfoOverlay
from .xform_utils import install_xform_op_order_warning_filter

# ---------------------------------------------------------------------------
# 웹 원격 UI(HTTP 브리지) — 선택적 모듈
# ---------------------------------------------------------------------------
# kit_remote_http_bridge: Kit 프로세스 안에서 작은 HTTP 서버를 띄워, 브라우저의
#   정적 패널(web/tbs_kit_remote/)이 REST/JSON으로 TBS 제어창·USD 로드와 동일한
#   동작(시뮬 시작/정지, XML 적용 등)을 호출할 수 있게 한다.
#   omni/확장 로딩 순서나 환경에 따라 import 가 실패할 수 있으므로 try/except 로
#   감싸고, 실패 시 start/stop 을 None 으로 두어 확장 전체가 죽지 않게 한다.
# ---------------------------------------------------------------------------
try:
    from .kit_remote_http_bridge import start_tbs_remote_http_bridge, stop_tbs_remote_http_bridge
except Exception:
    start_tbs_remote_http_bridge = None  # type: ignore[misc, assignment]
    stop_tbs_remote_http_bridge = None  # type: ignore[misc, assignment]


def _want_tbs_remote_http_bridge() -> bool:
    """브라우저 HTTP 브리지를 기동할지. 기본 True; 명시적으로 끌 때만 False."""
    v = os.environ.get("TBS_REMOTE_UI", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    return True


async def _deferred_apply_kit_chrome_hide(ext: Any) -> None:
    """메인 메뉴 등이 준비된 뒤 기본 숨김 적용 (KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH 가 True 일 때만)."""
    if not KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH:
        return
    kit_app = app.get_app()
    for _ in range(5):
        await kit_app.next_update_async()
    try:
        apply_kit_chrome_hidden(ext, True)
    except Exception:
        pass


def _apply_stage_default_fps_30() -> None:
    """
    런치/스테이지 로드 시 기본 FPS(TPS)를 30으로 맞춘다.

    - USD 기준: stage timeCodesPerSecond / framesPerSecond
    - 타임라인/프레임↔시간 변환(usm_animation_control 등)은 tl.get_time_codes_per_seconds()를 참조하므로,
      스테이지/타임라인 쪽의 기본 TPS가 30이면 자동으로 30 기준으로 재생 시간이 계산된다.
    """
    try:
        ctx = ou.get_context()
        stage = ctx.get_stage() if ctx else None
        if stage is None:
            return
        try:
            stage.SetTimeCodesPerSecond(30.0)
        except Exception:
            pass
        try:
            stage.SetFramesPerSecond(30.0)
        except Exception:
            pass
    except Exception:
        pass


async def _deferred_apply_stage_default_fps_30() -> None:
    """초기화 직후/스테이지 로드 타이밍을 고려해 몇 프레임 뒤 한 번 더 적용."""
    kit_app = app.get_app()
    for _ in range(5):
        await kit_app.next_update_async()
    _apply_stage_default_fps_30()


class Extension(omni.ext.IExt):
    """Omni 확장 진입점: 창 생성·선택/스테이지 구독·종료 시 애니/타임라인 정리."""

    def on_startup(self, ext_id: str) -> None:
        """확장 로드 시: xform 경고 필터, TBS 제어창(USD Load 포함)/시퀀스 창, 오버레이, 이벤트 구독."""
        install_xform_op_order_warning_filter()
        self._ext_id = ext_id
        self._tracked_paths: List[str] = []
        self._open_paths: List[str] = []
        self._overlay: Optional[PrimInfoOverlay] = None
        self._overlay_retry_count = 0
        self._selection_sub = None
        self._stage_stream_sub = None
        self._fps_stage_sub = None
        self._post_update_sub = None
        self._last_paths: tuple = ()
        self._ignore_selection_until = 0.0
        self._poll_frame = 0
        self._control_window = None
        self._object_list_frame = None
        self._sequence_window = None
        self._kit_chrome_startup_task = None

        build_control_window(self)
        self._sequence_window = SequenceEditorWindow()

        if KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH:
            self._kit_chrome_startup_task = asyncio.ensure_future(_deferred_apply_kit_chrome_hide(self))

        # -------------------------------------------------------------------
        # 타임라인 기본 FPS(TPS) = 30 강제
        # -------------------------------------------------------------------
        # - 확장 실행 직후, 또는 open_stage()로 스테이지가 교체될 때 24로 돌아가는 것을 방지한다.
        # - 스테이지 이벤트 스트림에 붙어, 스테이지가 열릴 때마다 30을 재적용한다.
        try:
            ctx = ou.get_context()
            if ctx is not None:
                self._fps_stage_sub = ctx.get_stage_event_stream().create_subscription_to_pop(
                    lambda _e: _apply_stage_default_fps_30(),
                    name="morph.tbs_control_1:DefaultFPS30",
                )
        except Exception:
            self._fps_stage_sub = None
        _apply_stage_default_fps_30()
        asyncio.ensure_future(_deferred_apply_stage_default_fps_30())

        # --- 뷰포트 객체 클릭 시 3D 정보 패널(PrimInfoOverlay) 비활성화 ---
        # 다시 쓰려면 아래 try_attach_overlay + 세 구독 블록의 주석을 해제하세요.
        # (제어창의「3D 정보 보기」버튼은 control_window → show_prim_info_in_viewport 경로로
        #  여전히 패널을 띄울 수 있음. 그 버튼까지 끄려면 해당 버튼도 주석 처리 필요.)
        # try_attach_overlay(self)
        #
        # ctx = ou.get_context()
        # ed = get_eventdispatcher()
        # try:
        #     event_name = ctx.stage_event_name(ou.StageEventType.SELECTION_CHANGED)
        #     self._selection_sub = ed.observe_event(
        #         observer_name="morph.tbs_control_1:SelectionChanged",
        #         event_name=event_name,
        #         on_event=lambda e: on_selection_changed(self, e),
        #     )
        # except Exception:
        #     pass
        # try:
        #     self._stage_stream_sub = ctx.get_stage_event_stream().create_subscription_to_pop(
        #         lambda e: on_selection_changed(self, e),
        #         name="morph.tbs_control_1:StageEvents",
        #     )
        # except Exception:
        #     pass
        # try:
        #     self._post_update_sub = app.get_app().get_post_update_event_stream().create_subscription_to_pop(
        #         lambda e: on_post_update(self, e),
        #         name="morph.tbs_control_1:PostUpdate",
        #     )
        # except Exception:
        #     pass

        # -------------------------------------------------------------------
        # 웹 원격 UI 시작 (기본 켜짐 — 환경 변수로만 끔)
        # -------------------------------------------------------------------
        # TBS_REMOTE_UI 가 "0", "false", "no", "off" 이면 브리지를 기동하지 않는다.
        #   (비어 있거나 그 외 값이면 기동. 예전처럼 =1 을 안 넣어도 동작한다.)
        # start_tbs_remote_http_bridge(self):
        #   - 확장 인스턴스(self)를 넘겨 UI 위젯·상태에 메인 스레드에서 접근한다.
        #   - 포트: TBS_REMOTE_UI_PORT (미설정 시 kit_remote_http_bridge 기본값, 보통 8720).
        #   - 바인드: TBS_REMOTE_UI_BIND (기본 127.0.0.1; 원격 PC 브라우저면 0.0.0.0 등).
        #   - 정적 파일: 확장 내 web/tbs_kit_remote/ (index.html, tbs_panel.js 등).
        # import 실패로 start_tbs_remote_http_bridge 가 None 이면 아무 것도 하지 않음.
        # 기동 예외는 로그 없이 삼켜 확장 로딩을 막지 않는다(필요 시 브리지 모듈에서 로깅).
        # -------------------------------------------------------------------
        if _want_tbs_remote_http_bridge() and start_tbs_remote_http_bridge is not None:
            try:
                start_tbs_remote_http_bridge(self)
            except Exception:
                pass

    def on_shutdown(self) -> None:
        """확장 언로드 시: 시뮬 정지, 구독 해제, translate/curve/rotate/usd 애니 정지, 창 destroy."""
        t = getattr(self, "_kit_chrome_startup_task", None)
        if t is not None and not t.done():
            try:
                t.cancel()
            except Exception:
                pass
            self._kit_chrome_startup_task = None
        try:
            if is_kit_chrome_hidden(self):
                apply_kit_chrome_hidden(self, False)
        except Exception:
            pass
        # 웹 브리지를 먼저 내린다: 백그라운드 HTTP 스레드·구독을 정리해 포트 점유와
        # 언로드 후에도 요청이 Kit 쪽으로 들어오는 것을 막는다. (시뮬 정지·창 destroy 보다 앞.)
        if stop_tbs_remote_http_bridge is not None:
            try:
                stop_tbs_remote_http_bridge()
            except Exception:
                pass
        try:
            on_sim_stop_clicked(self)
        except Exception:
            pass
        if self._selection_sub is not None and hasattr(self._selection_sub, "release"):
            self._selection_sub.release()
            self._selection_sub = None
        if self._stage_stream_sub is not None:
            try:
                self._stage_stream_sub.unsubscribe()
            except Exception:
                pass
            self._stage_stream_sub = None
        if self._fps_stage_sub is not None:
            try:
                self._fps_stage_sub.unsubscribe()
            except Exception:
                pass
            self._fps_stage_sub = None
        if self._post_update_sub is not None:
            try:
                self._post_update_sub.unsubscribe()
            except Exception:
                pass
            self._post_update_sub = None
        for path in list(self._tracked_paths):
            stop_prim_translate_animation(path)
            stop_prim_curve_animation(path)
            stop_prim_rotate_animation(path)
        self._tracked_paths.clear()
        self._open_paths.clear()
        if self._overlay:
            self._overlay.destroy()
            self._overlay = None
        usd_animation_control.stop_usd_animation()
        if self._control_window is not None:
            self._control_window.destroy()
            self._control_window = None
        self._object_list_frame = None
        if self._sequence_window is not None:
            try:
                self._sequence_window.destroy()
            except Exception:
                pass
            self._sequence_window = None
