# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
sim_multi_view.py — 멀티 시뮼 화면 분할

- **첫 타일(레이아웃상 0번)**: Kit 기본 「Viewport」창만 사용 → **기본 `omni.usd` 컨텍스트의 스테이지**
  (TBS Load 로 연 화면, Stage 패널·타임라인이 따라가는 대상).
- **2~4 타일(보조 Hydra 뷰)**: 채널마다 **이름 있는 USD 컨텍스트 + 별도 뷰포트**로 **독립 3D 화면**을 만든다(기본 동작).
  Hydra ``ViewportTexture_*`` / ``Invalid USD RenderProduct`` 회피를 위해 **메인 Viewport 리사이즈는 보조 뷰 생성 후**에 하고,
  창 위치·카메라 지정은 **한 코루틴에서 순차 ``await next_update``** 로만 적용한다(이중 비동기 레이아웃 없음).
  분할 수를 **4→3** 처럼 바꿀 때는 보조 뷰를 먼저 제거한 뒤 **짧은 유휴·프레임**을 두고 재빌드해 GPU ``device lost`` 를 줄인다.
  그래도 크래시면 **`TBS_SIM_VIEWPORT_SPLIT_3D=0`** 으로 보조 뷰만 끈다.
- **레이아웃**: **기본은 Viewport Dock 안 ``dock_in`` 분할**(메인 Viewport 는 undock 하지 않음).
  ``docked`` 미확인 시에만 좌표 격자 폴백(undock 없이 크기·위치만 조정).
  ``TBS_SIM_VIEWPORT_SPLIT_DOCK=0`` 이면 Dock 없이 격자만.
- **보조 타일 스테이지 분리**: 동일 URL/경로를 ``subLayers`` 로만 묶으면 USD 가 **같은 ``Sdf.Layer``** 를 공유해
  한쪽 편집이 다른 타일에도 보일 수 있다. 기본은 타일마다 **원본 USD 를 임시 경로로 ``omni.client.copy_async`` 복제**한 뒤
  그 **서로 다른 파일**을 연다(``TBS_MULTI_SPLIT_FILE_CLONE=0`` 이면 복제 없이 래퍼 ``.usda`` 만 사용).
  ``TBS_MULTI_SPLIT_SESSION_LAYER``(기본 on)는 복제/래퍼 루트 위에 **session layer** 를 추가로 얹는다.
- **조작(이동)이 항상 메인에만 먹는 경우**: 뷰포트 조작기·명령이 **기본 ``omni.usd`` 컨텍스트**로만 나가는 Kit 동작일 수 있다.
  그때는 보조 타일에서 **직접 조작하지 말고** 채널별 스크립트/엔진으로 변형하거나, 별도 Kit 프로세스가 필요할 수 있다.
- **MDL 텍스처 https 오류**: 재질이 원격 URL 을 참조하면 RTX 가 해석하지 못할 수 있다. 로컬 경로·`omniverse://` 등으로 자산을 정리하는 것이 근본 대응이다.
- **1화면**: `teardown_sim_multi_viewports` 로 보조 뷰·컨텍스트 정리 후 Viewport 복원.

- **제어창 동기화(1단계)**: ``ext._sim_viewport_split_count`` 는 **실제로 적용된** 분할 수만 담는다.
  분할 불가(3D 끔·USD 미준비·경로 없음)·빌드 롤백 시 **1로 되돌리고** ``notify_sim_split_ui_sync`` 로 체크박스를 맞춘다.
- **스냅샷 HUD(뷰포트 인-뷰 2D)**: 각 타일의 ``ViewportWindow.get_frame(ext_id)`` 슬롯에 우측 상단 패널을 올려, **해당 3D 타일 안에만** 시뮼 스냅샷 요약(저장 여부·LOT·EP·간격·고장 등)이 보이게 한다. 제어창 저장·자동 채움·분할 변경 시 갱신된다.

【control_window.py 와의 역할 분담】
- **본 모듈(sim_multi_view)**: Kit 뷰포트/Workspace 창·Dock·보조 ``omni.usd`` 컨텍스트·스테이지 복제/래퍼·
  뷰포트 해상도·카메라·크롬(메뉴) 숨김 후 재레이아웃·인-뷰 HUD 레이어 등 **3D 쪽 분할**만 담당한다.
- **제어창(control_window)**: ``TBSSimulationEngine`` 다중 생성·화면별 tick 스레드·진행/로그 UI·
  스냅샷 dict 저장 등 **시뮼 로직·UI**를 담당한다. 분할 수는 양쪽이 ``ext._sim_viewport_split_count`` 로 동기화한다.

【함수·코루틴 역할 색인 (파일 전체)】
환경/플래그:
- ``_env_flag`` : 문자열 환경변수를 bool 로 해석.
- ``_use_aux_file_clone`` / ``_use_multi_split_session_layer`` : 보조 타일 USD 복제·session layer 사용 여부.

분할 정책·표현:
- ``sim_viewport_split_dock_enabled`` / ``sim_viewport_split_3d_enabled`` : Dock 사용·보조 Hydra 3D 생성 on/off.
- ``channel_count_for_split`` : 1~4 클램프.
- ``split_layout_description`` : 사용자 안내 문구.
- ``_split_window_name`` : 보조 Workspace 창 이름.
- ``_split_cell_layout_fracs`` : 타일별 정규화 사각형(레이아웃 계산).
- ``_split_dock_operations`` : ``dock_in`` 순서(창 이름·기준·DockPosition·비율).

비동기·Dock 보조:
- ``_wait_workspace_windows_ready`` / ``_wait_aux_windows_docked`` : 창 등록/Dock 완료 대기.
- ``_dock_aux_into_target`` / ``_dock_viewport_fill_dockspace`` : Dock API 래퍼.
- ``_reapply_split_dock_in_geometry`` : Dock 기하 재적용.
- ``_sync_viewport_resolution_from_workspace_window`` : 보조 창에 맞춰 뷰포트 해상도 동기화.
- ``set_viewport_fill_frame_for_split_count`` : Viewport fill 프레임 on/off.
- ``_apply_split_dock_layout`` : 코루틴으로 Dock 시퀀스 실행.

USD·스테이지·복제:
- ``_main_usd_path_for_clone`` : 메인 스테이지에서 복제할 USD 경로.
- ``_apply_stage_fps_30`` : 보조 스테이지 FPS 힌트.
- ``_register_session_layer_path`` / ``_unlink_split_session_files`` / ``_unlink_one_session_file`` : session 파일 추적·삭제.
- ``_clone_dest_suffix`` / ``_clone_usd_for_aux_tile`` : 보조 타일용 파일 복제.
- ``_make_aux_wrapper_root_layer`` / ``_export_empty_session_usda`` : 래퍼·빈 session usda 생성.
- ``_ctx_open_stage_path`` / ``_open_aux_stage_with_unique_session`` : 네임드 컨텍스트에 스테이지 오픈.

컨텍스트·뷰포트 생명주기:
- ``_log_split_stage_not_shared_with_main`` : 디버그 로그.
- ``_named_usd_context`` / ``_release_usd_context_names`` : omni.usd 컨텍스트 생성·해제.
- ``_destroy_viewport_window`` / ``_destroy_kit_viewport`` : 뷰포트/윈도우 파괴.
- ``_log_viewport_usd_context_bind`` : 컨텍스트 바인딩 로그.
- ``_workspace_show_named_window`` : Workspace 창 표시 토글.
- ``_restore_main_viewport_layout`` / ``teardown_sim_multi_viewports`` : 1화면 복원·전체 철거.

기하·크롬 연동:
- ``_read_viewport_rect`` / ``_read_dockspace_rect`` / ``_read_split_cluster_union_rect`` / ``_read_split_layout_bbox_for_chrome`` : 픽셀 박스 읽기.
- ``_refresh_docked_multi_split_after_chrome`` / ``_apply_split_geometry_sync`` : 레이아웃 동기 적용.
- ``_menubar_reserved_height_px`` / ``_relayout_single_viewport_fill_available`` : 메뉴바 높이 보정.
- ``relayout_split_views_to_viewport`` / ``schedule_split_layout_refresh_for_chrome_change`` : 외부에서 레이아웃 재요청.

빌드·롤백·진입점:
- ``notify_sim_split_ui_sync`` : 제어창 체크박스를 ``ext._sim_viewport_split_count`` 에 맞춤.
- ``_rollback_split_attempt`` : 실패 시 생성물 되돌리기.
- ``_finalize_split_window_geometry_sequential`` / ``_assign_split_cameras_after_layout`` : 레이아웃 후 창 위치·카메라.
- ``_build_multi_split_async`` / ``_post_teardown_rebuild_split`` : 비동기 빌드·teardown 후 재빌드.
- ``_apply_sim_viewport_split_layout_impl`` / ``apply_sim_viewport_split_layout`` : 분할 적용(동기 래퍼+impl).

스테이지 가시성 구독:
- ``attach_stage_visibility_subscription`` / ``detach_stage_visibility_subscription`` : 로드/가시성 변화 시 콜백.

HUD:
- ``_viewport_window_name_for_screen`` / ``_resolve_viewport_window_for_workspace_name`` / ``_snapshot_hud_frame_slot`` /
  ``_viewport_window_for_screen`` : 뷰포트 윈도우 조회.
- ``detach_sim_screen1_live_hud_subscription`` / ``_sim_screen1_hud_post_tick`` / ``_ensure_sim_screen1_live_hud_subscription`` :
  화면1 라이브 HUD 갱신 틱.
- ``_format_initial_load_ports_line`` / ``_fault_count_from_snapshot_dict`` / ``_describe_snapshot_for_viewport_hud`` : HUD 문자열.
- ``destroy_viewport_snapshot_hud_layers`` / ``sync_viewport_snapshot_hud_layers`` / ``schedule_viewport_snapshot_hud_refresh`` :
  HUD 레이어 파괴·동기·지연 갱신.

보조 뷰는 Stage 패널에 올리지 않는다(메인 스테이지만 편집 UI에 노출).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import omni.client
import omni.kit.app as kit_app
import omni.ui as ui

from .prim_utils import get_stage
from .sim_control_defaults import SIM_CONTROL_DEFAULTS as _SIM_DEF


def _env_flag(name: str) -> bool:
    """환경 변수가 1/true/yes/on 이면 참."""
    try:
        v = str(os.environ.get(name, "") or "").strip().lower()
    except Exception:
        return False
    return v in ("1", "true", "yes", "on")


def _use_aux_file_clone() -> bool:
    """
    보조 타일마다 원본 USD 를 임시 파일로 **완전 복제**한 뒤 연다(서브레이어·레이어 캐시 공유 회피).

    기본 True. 대용량이면 ``TBS_MULTI_SPLIT_FILE_CLONE=0`` 으로 끄고 래퍼 ``.usda`` 만 쓴다(편집 공유 위험 있음).
    """
    try:
        v = str(os.environ.get("TBS_MULTI_SPLIT_FILE_CLONE", "") or "").strip().lower()
    except Exception:
        return True
    if not v:
        return True
    if v in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    return True


def _use_multi_split_session_layer() -> bool:
    """
    보조 타일을 **루트 래퍼 USD** 로 연 뒤, 추가로 session layer 를 쓸지.

    기본 True(래퍼 + session). 래퍼 단독만 쓰려면 ``TBS_MULTI_SPLIT_SESSION_LAYER=0``.
    """
    try:
        v = str(os.environ.get("TBS_MULTI_SPLIT_SESSION_LAYER", "") or "").strip().lower()
    except Exception:
        return True
    if not v:
        return True
    if v in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    return True


def _use_split_composed_export(ext: Any = None) -> bool:
    """
    보조 타일 USD — Flatten 스냅샷 사용 여부.

    분할 **체크 직후** 동기 Flatten 은 수 초 걸린다. TBS Load 시 백그라운드 prewarm 된
    스냅샷이 있을 때만 True (``copy_async`` 만으로 빠르게 복제).
    """
    try:
        from .tbs_split_composed_loader import split_dual_usd_paths_enabled

        if split_dual_usd_paths_enabled(ext):
            return False
    except Exception:
        pass
    try:
        v = str(os.environ.get("TBS_MULTI_SPLIT_COMPOSED_EXPORT", "") or "").strip().lower()
    except Exception:
        v = ""
    if v in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    if ext is not None:
        try:
            from .tbs_split_composed_loader import composed_split_snapshot_ready

            if composed_split_snapshot_ready(ext):
                return True
        except Exception:
            pass
    return False


def _split_layout_usd_key(ext: Any) -> str:
    """분할 레이아웃이 유효한지 판별할 메인 스테이지 지문."""
    try:
        from .tbs_split_composed_loader import _main_stage_cache_key

        key = str(_main_stage_cache_key(ext) or "").strip()
        if key:
            return key
    except Exception:
        pass
    try:
        return str(getattr(ext, "_tbs_last_loaded_usd_path", "") or "").strip()
    except Exception:
        return ""


def invalidate_split_layout_cache(ext: Any) -> None:
    """Master USD 재로드 등으로 기존 분할 타일을 무효화."""
    try:
        ext._tbs_split_layout_usd_key = None
    except Exception:
        pass
    try:
        from .tbs_split_composed_loader import clear_split_composed_export_cache

        clear_split_composed_export_cache(ext)
    except Exception:
        pass


def sim_viewport_split_dock_enabled() -> bool:
    """
    보조 뷰포트를 메인 ``Viewport`` Dock 안에 넣을지(기본 True).

    ``USE_VIEWPORT_WIDGET_SPLIT=True`` 이면 **Dock 미사용** (ViewportWidget get_frame 분할).
    ``TBS_SIM_VIEWPORT_SPLIT_DOCK=0`` 이면 좌표 격자(보조 창만 이동, Viewport Dock 유지)로 폴백.
    """
    try:
        from .sim_multi_view_widget import sim_viewport_split_widget_enabled

        if sim_viewport_split_widget_enabled():
            return False
    except Exception:
        pass
    try:
        v = str(os.environ.get("TBS_SIM_VIEWPORT_SPLIT_DOCK", "") or "").strip().lower()
    except Exception:
        return True
    if not v:
        return True
    if v in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    return True


def sim_viewport_split_3d_enabled() -> bool:
    """
    2~4 분할 시 보조 Hydra 뷰(독립 3D 타일)를 만들지 여부.

    기본 True. Kit 이슈로 보조 뷰만 끄려면 ``TBS_SIM_VIEWPORT_SPLIT_3D=0`` / ``false`` / ``off`` 등.
    """
    try:
        v = str(os.environ.get("TBS_SIM_VIEWPORT_SPLIT_3D", "") or "").strip().lower()
    except Exception:
        return True
    if not v:
        return True
    if v in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    return True


def channel_count_for_split(split_n: int) -> int:
    """활성 시뮼 채널 수(1~``MAX_VIEWPORT_SPLIT_COUNT``)."""
    try:
        from .sim_control_defaults import MAX_VIEWPORT_SPLIT_COUNT

        hi = max(1, int(MAX_VIEWPORT_SPLIT_COUNT))
    except Exception:
        hi = 2
    try:
        n = int(split_n)
    except Exception:
        n = 1
    return min(hi, max(1, n))


def split_layout_description(split_n: int) -> str:
    """사용자 안내용 짧은 레이아웃 설명."""
    n = channel_count_for_split(split_n)
    if n <= 1:
        return "단일 화면(기본)"
    try:
        from .sim_multi_view_widget import sim_viewport_split_widget_enabled

        if sim_viewport_split_widget_enabled() and n == 2:
            return "2분할: ViewportWidget — get_frame HStack (Dock 미사용)"
    except Exception:
        pass
    if n == 2:
        return "2분할: Viewport Dock — TBS_SimSplit_1(우 50%)"
    if n == 3:
        return "3분할: Viewport Dock — 보조 2칸(독립 스테이지)"
    return "4분할: Viewport Dock 2×2(독립 스테이지)"


def _split_window_name(aux_index_1based: int) -> str:
    """보조 타일 전용 Workspace 창 이름(1부터)."""
    return f"TBS_SimSplit_{int(aux_index_1based)}"


def _split_cell_layout_fracs(n: int) -> List[Tuple[float, float, float, float]]:
    """각 타일의 뷰포트 대비 정규화 사각형 (x0, y0, x1, y1), 0~1."""
    if n == 2:
        return [(0.0, 0.0, 0.5, 1.0), (0.5, 0.0, 1.0, 1.0)]
    if n == 3:
        return [(0.0, 0.0, 1.0, 0.5), (0.0, 0.5, 0.5, 1.0), (0.5, 0.5, 1.0, 1.0)]
    if n == 4:
        return [
            (0.0, 0.0, 0.5, 0.5),
            (0.5, 0.0, 1.0, 0.5),
            (0.0, 0.5, 0.5, 1.0),
            (0.5, 0.5, 1.0, 1.0),
        ]
    return [(0.0, 0.0, 1.0, 1.0)]


def _read_viewport_split_area() -> Tuple[int, int, int, int]:
    """
    분할 격자 — **현재 Viewport 창** 영역만 사용(Console 상단에서 자름).

    DockSpace 전체·메인 창 크기를 쓰면 Console/Content 를 덮는다.
    """
    vx, vy, vw, vh = _read_viewport_rect()
    for panel_name in ("Console", "Content"):
        pr = _read_window_rect(panel_name)
        if pr is None:
            continue
        panel_top = int(pr[1])
        if panel_top > int(vy) + _VP_TILE_MIN_PX and int(vy) + int(vh) > panel_top:
            vh = max(_VP_TILE_MIN_PX, panel_top - int(vy))
    return (int(vx), int(vy), max(_VP_TILE_MIN_PX, int(vw)), max(_VP_TILE_MIN_PX, int(vh)))


def _aux_windows_actually_docked(n: int) -> bool:
    """``dock_in`` API 성공과 달리 Workspace ``docked`` 가 False 인 경우가 있어 검증."""
    try:
        sn = channel_count_for_split(int(n))
    except Exception:
        return False
    if sn <= 1:
        return False
    for ti in range(1, sn):
        try:
            w = ui.Workspace.get_window(_split_window_name(ti))
            if w is None or not bool(getattr(w, "docked", False)):
                return False
        except Exception:
            return False
    return True


def _aux_windows_dock_plausible(n: int) -> bool:
    """
    Kit 이 ``docked`` 플래그를 늦게 갱신할 때 — 보조 창이 존재하고 최소 크기면 Dock 성공으로 간주.
    """
    if _aux_windows_actually_docked(n):
        return True
    try:
        sn = channel_count_for_split(int(n))
    except Exception:
        return False
    if sn <= 1:
        return False
    for ti in range(1, sn):
        try:
            w = ui.Workspace.get_window(_split_window_name(ti))
        except Exception:
            return False
        if w is None:
            return False
        try:
            if not bool(getattr(w, "visible", True)):
                return False
        except Exception:
            pass
        try:
            ww = int(getattr(w, "width", 0) or 0)
            wh = int(getattr(w, "height", 0) or 0)
        except Exception:
            return False
        if ww < _VP_TILE_MIN_PX or wh < _VP_TILE_MIN_PX:
            return False
    return True


def viewport_split_user_resize_locked() -> bool:
    """2분할 타일에 사용자 드래그 리사이즈 잠금 on/off."""
    try:
        from .sim_control_defaults import LOCK_VIEWPORT_SPLIT_USER_RESIZE

        if not bool(LOCK_VIEWPORT_SPLIT_USER_RESIZE):
            return False
    except Exception:
        pass
    try:
        v = str(os.environ.get("TBS_SIM_VIEWPORT_SPLIT_LOCK_RESIZE", "") or "").strip().lower()
    except Exception:
        return True
    if v in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    return True


def _viewport_split_lock_window_flags() -> int:
    """Dock 레이아웃 유지 — NO_DOCKING/NO_TITLE_BAR 는 Console·Content 레이아웃을 깨뜨림."""
    flags = 0
    for name in ("WINDOW_FLAGS_NO_RESIZE", "WINDOW_FLAGS_NO_MOVE"):
        bit = getattr(ui, name, None)
        if bit is not None:
            flags |= int(bit)
    return flags


def _apply_viewport_split_tile_lock_flags(wname: str) -> None:
    """화면1·2 — floating 리사이즈·이동만 제한(Dock 50:50 레이아웃은 그대로)."""
    if not viewport_split_user_resize_locked():
        return
    wn = str(wname or "").strip()
    if not wn:
        return
    try:
        wui = ui.Workspace.get_window(wn)
        if wui is None:
            return
        try:
            wui.flags = int(getattr(wui, "flags", 0) or 0) | _viewport_split_lock_window_flags()
        except Exception:
            pass
    except Exception:
        pass


def _split_viewport_tile_names(n: int) -> List[str]:
    sn = channel_count_for_split(int(n))
    if sn <= 1:
        return []
    return ["Viewport"] + [_split_window_name(ti) for ti in range(1, sn)]


def apply_viewport_split_tab_chrome(n: int, ext: Any = None) -> None:
    """화면1(Viewport)·화면2+ Dock 탭 숨김 — Dock·Widget 분할 공통."""
    try:
        from .sim_multi_view_widget import hide_viewport_workspace_tab_chrome, is_split_widget_layout_active
        from .tbs_extension_singleton import get_tbs_extension_instance

        e = ext if ext is not None else get_tbs_extension_instance()
        if e is not None and is_split_widget_layout_active(e):
            hide_viewport_workspace_tab_chrome()
            return
    except Exception:
        pass
    try:
        from .kit_chrome_visibility import apply_viewport_dock_tab_bars_hidden

        apply_viewport_dock_tab_bars_hidden()
    except Exception:
        pass
    try:
        sn = channel_count_for_split(int(n))
    except Exception:
        sn = 0
    names = _split_viewport_tile_names(sn) if sn >= 2 else ["Viewport"]
    for wn in names:
        try:
            wui = ui.Workspace.get_window(str(wn))
            if wui is None:
                continue
            if str(wn) != "Viewport":
                try:
                    wui.title = str(wn)
                except Exception:
                    pass
            try:
                wui.noTabBar = True
            except Exception:
                pass
            for attr in ("dock_tab_bar_enabled", "dock_tab_bar_visible"):
                try:
                    setattr(wui, attr, False)
                except Exception:
                    pass
        except Exception:
            pass


_SPLITTER_GUARD_WIN_TITLE = "TBS_SimSplit_SplitterGuard"
_SPLITTER_GUARD_HIT_PX = 1
# gw(논리 폭)와 실제 칠해짐 폭은 다름 — Kit ui.Window 는 최소 ~8px 수준으로 그릴 수 있어
# 0.5·0.3 으로 줄여도 화면상 띠 폭은 거의 안 줄어듦. 위치는 ORIGIN_LEFT 로 보정.
_SPLITTER_GUARD_CHROME_SLOP = 2.0 / 3.0
# anchor 기준 창 원점 — Kit 실제 칠해짐(~8px) 우측 끝이 접합선에 맞도록.
_SPLITTER_GUARD_ORIGIN_LEFT = 8.0


def _splitter_guard_window_flags() -> int:
    flags = 0
    for name in (
        "WINDOW_FLAGS_NO_TITLE_BAR",
        "WINDOW_FLAGS_NO_SCROLLBAR",
        "WINDOW_FLAGS_NO_DOCKING",
        "WINDOW_FLAGS_NO_COLLAPSE",
        "WINDOW_FLAGS_NO_BACKGROUND",
        "WINDOW_FLAGS_NO_MOVE",
        "WINDOW_FLAGS_NO_RESIZE",
        "WINDOW_FLAGS_NO_FOCUS_ON_APPEARING",
        "WINDOW_FLAGS_NO_BRING_TO_FRONT_ON_FOCUS",
    ):
        bit = getattr(ui, name, None)
        if bit is not None:
            flags |= int(bit)
    return flags


def _consume_splitter_guard_mouse(*_a: Any, **_k: Any) -> bool:
    """Dock 분할선 드래그가 ImGui 로 내려가지 않도록 이벤트를 삼킨다."""
    return True


def _wire_splitter_guard_mouse_targets(*targets: Any) -> None:
    for target in targets:
        if target is None:
            continue
        for fn_name in ("set_mouse_pressed_fn", "set_mouse_released_fn"):
            fn = getattr(target, fn_name, None)
            if callable(fn):
                try:
                    fn(_consume_splitter_guard_mouse)
                except Exception:
                    pass


def _hide_splitter_guard_workspace_duplicate() -> None:
    """동명 Workspace 창 — geometry 이중 적용 시 넓은 띠로 보일 수 있어 숨김."""
    try:
        dup = ui.Workspace.get_window(_SPLITTER_GUARD_WIN_TITLE)
        if dup is not None:
            dup.visible = False
    except Exception:
        pass


def _workspace_window_rect(wname: str) -> Optional[Tuple[float, float, float, float]]:
    try:
        w = ui.Workspace.get_window(str(wname))
    except Exception:
        w = None
    if w is None:
        return None
    try:
        if not bool(getattr(w, "visible", True)):
            return None
    except Exception:
        pass
    try:
        x = float(getattr(w, "position_x", 0.0) or 0.0)
        y = float(getattr(w, "position_y", 0.0) or 0.0)
        ww = float(getattr(w, "width", 0.0) or 0.0)
        wh = float(getattr(w, "height", 0.0) or 0.0)
    except Exception:
        return None
    if ww < 8.0 or wh < 8.0:
        return None
    return (x, y, ww, wh)


def _compute_viewport_split_splitter_rect(n: int) -> Optional[Tuple[float, float, float, float]]:
    """Viewport ↔ 보조 타일 사이 Dock 분할선 히트 영역 (x, y, w, h).

    가로: 두 타일 사이 Dock 갭(또는 겹침/접합부)만. 세로: 두 타일 높이 교집합만.
    """
    if int(n) < 2:
        return None
    if not bool(sim_viewport_split_dock_enabled()):
        return None
    left = _workspace_window_rect("Viewport")
    right = _workspace_window_rect(_split_window_name(1))
    if left is None or right is None:
        return None
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    gap_l = lx + lw
    gap_r = rx
    if gap_r + 1.0 < gap_l:
        return None
    top = max(ly, ry)
    bottom = min(ly + lh, ry + rh)
    gh = bottom - top
    if gh < 1.0:
        return None
    gy = top
    hit = float(_SPLITTER_GUARD_HIT_PX)
    dock_gap = gap_r - gap_l
    if dock_gap >= 1.0:
        gw = max(1.0, min(dock_gap, hit))
        anchor = gap_l
    elif dock_gap < -0.5:
        gw = max(1.0, min(gap_l - gap_r, hit))
        anchor = gap_r
    else:
        gw = hit
        anchor = gap_l
    # Kit 창 원점 — 칠해짐 폭(~CHROME_SLOP)보다 왼쪽에 두어 우측 삐져남 방지.
    gx = anchor - float(_SPLITTER_GUARD_ORIGIN_LEFT)
    if gw < 1.0:
        return None
    return (gx, gy, gw, gh)


def _apply_splitter_guard_geometry(ext: Any, rect: Tuple[float, float, float, float]) -> None:
    gx, gy, gw, gh = rect
    win = getattr(ext, "_tbs_split_splitter_guard_win", None)
    if win is None:
        return
    _hide_splitter_guard_workspace_duplicate()
    try:
        win.flags = _splitter_guard_window_flags()
    except Exception:
        pass
    for attr, val in (
        ("position_x", gx),
        ("position_y", gy),
        ("width", gw),
        ("height", gh),
        ("visible", True),
    ):
        try:
            setattr(win, attr, val)
        except Exception:
            pass
    try:
        top_modal = getattr(win, "set_top_modal", None)
        if callable(top_modal):
            top_modal()
    except Exception:
        pass


def _ensure_splitter_guard_window(ext: Any) -> Any:
    win = getattr(ext, "_tbs_split_splitter_guard_win", None)
    if win is not None:
        return win
    try:
        win = ui.Window(
            _SPLITTER_GUARD_WIN_TITLE,
            width=int(_SPLITTER_GUARD_HIT_PX),
            height=100,
            flags=_splitter_guard_window_flags(),
        )
    except Exception:
        return None
    try:
        ext._tbs_split_splitter_guard_win = win
    except Exception:
        pass
    try:
        with win.frame:
            block = ui.Frame(style={"background_color": 0x00000000, "border_width": 0})
            with block:
                ui.Spacer()
        _wire_splitter_guard_mouse_targets(win.frame, block)
    except Exception:
        pass
    return win


def _hide_splitter_guard_window(ext: Any) -> None:
    win = getattr(ext, "_tbs_split_splitter_guard_win", None)
    if win is not None:
        try:
            win.visible = False
        except Exception:
            pass
    _hide_splitter_guard_workspace_duplicate()


def _tick_viewport_split_splitter_guard(ext: Any) -> None:
    if not viewport_split_user_resize_locked():
        _hide_splitter_guard_window(ext)
        return
    try:
        n = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 1) or 1))
    except Exception:
        n = 1
    if n < 2 or not bool(getattr(ext, "_tbs_split_used_dock_layout", False)):
        _hide_splitter_guard_window(ext)
        return
    rect = _compute_viewport_split_splitter_rect(n)
    if rect is None:
        _hide_splitter_guard_window(ext)
        return
    if _ensure_splitter_guard_window(ext) is None:
        return
    _apply_splitter_guard_geometry(ext, rect)


def _install_viewport_split_splitter_guard(ext: Any) -> None:
    """Dock 분할선 위 투명 창 — 드래그 입력 선점(비율 되돌림 없음)."""
    teardown_viewport_split_resize_lock(ext)
    if not viewport_split_user_resize_locked():
        return
    try:
        n = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 1) or 1))
    except Exception:
        return
    if n < 2:
        return
    _ensure_splitter_guard_window(ext)
    _tick_viewport_split_splitter_guard(ext)

    sub_ref: List[Any] = [None]

    def _on_tick(_ev: Any) -> None:
        _tick_viewport_split_splitter_guard(ext)

    try:
        sub_ref[0] = kit_app.get_app().get_post_update_event_stream().create_subscription_to_pop(
            _on_tick,
            name="morph.tbs_control_2.split_splitter_guard",
        )
        ext._tbs_split_splitter_guard_sub = sub_ref[0]
    except Exception:
        pass


def apply_viewport_split_user_resize_lock(ext: Any) -> None:
    """Dock 50:50 유지 — floating 리사이즈·이동 제한 + 분할선 드래그 입력 차단."""
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active

        if is_split_widget_layout_active(ext):
            return
    except Exception:
        pass
    if not viewport_split_user_resize_locked():
        teardown_viewport_split_resize_lock(ext)
        return
    try:
        n = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 1) or 1))
    except Exception:
        return
    if n < 2:
        teardown_viewport_split_resize_lock(ext)
        return
    for nm in _split_viewport_tile_names(n):
        _apply_viewport_split_tile_lock_flags(nm)
    _install_viewport_split_splitter_guard(ext)


def teardown_viewport_split_resize_lock(ext: Any) -> None:
    """분할선 가드 구독·창 정리."""
    sub = getattr(ext, "_tbs_split_splitter_guard_sub", None)
    try:
        ext._tbs_split_splitter_guard_sub = None
    except Exception:
        pass
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
    _hide_splitter_guard_window(ext)


def _apply_aux_window_chrome_flags(wname: str) -> None:
    """보조 타일 — Workspace 탭 제목 표시(스크롤바만 끔). 타이틀바 제거는 조작·제목 문제를 유발."""
    try:
        wui = ui.Workspace.get_window(str(wname))
        if wui is None:
            return
        try:
            wui.title = str(wname)
        except Exception:
            pass
        try:
            wui.flags = ui.WINDOW_FLAGS_NO_SCROLLBAR
        except Exception:
            pass
    except Exception:
        pass


async def _enforce_equal_split_grid_async(
    ext: Any, token: int, n: int, *, preserve_main_viewport: bool = False
) -> Tuple[int, int, int, int]:
    """
    보조 타일(및 필요 시 메인) 격자 배치.

    ``preserve_main_viewport=True`` — **메인 Viewport Dock 은 유지**, 보조 창만 배치(Dock 폴백).
    """
    fracs = _split_cell_layout_fracs(n)
    vx, vy, vw, vh = _get_split_tile_bbox(ext)
    if vw < _VP_TILE_MIN_PX * 2 or vh < _VP_TILE_MIN_PX:
        vx, vy, vw, vh = _read_viewport_split_area()
    bottom_cap = int(vy) + int(vh)
    win_names = ["Viewport"] + [_split_window_name(ti) for ti in range(1, n)]
    for _ in range(3):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return (vx, vy, vw, vh)
        await kit_app.get_app().next_update_async()
    await _finalize_split_window_geometry_sequential(
        ext,
        token,
        win_names,
        fracs,
        vx,
        vy,
        vw,
        vh,
        max_bottom_y=bottom_cap,
        preserve_main_viewport=preserve_main_viewport,
    )
    try:
        ext._tbs_split_saved_viewport_rect = (int(vx), int(vy), int(vw), int(vh))
    except Exception:
        pass
    try:
        print(
            f"[TBS multi-sim] 동일 크기 격자 배치 OK area=({vx},{vy},{vw}x{vh}) tiles={n}",
            flush=True,
        )
    except Exception:
        pass
    return (vx, vy, vw, vh)


def _apply_viewport_clipped_split_grid(
    n: int, ext: Any = None, *, preserve_main_viewport: bool = False
) -> None:
    """동기 격자 — Dock 폴백 시 보조 창만(또는 전체) 배치."""
    try:
        sn = channel_count_for_split(int(n))
    except Exception:
        return
    if sn <= 1:
        return
    if ext is not None:
        vx, vy, vw, vh = _get_split_tile_bbox(ext)
    else:
        vx, vy, vw, vh = _read_viewport_split_area()
    bottom_cap = int(vy) + int(vh)
    fracs = _split_cell_layout_fracs(sn)
    win_names = ["Viewport"] + [_split_window_name(ti) for ti in range(1, sn)]
    _apply_split_geometry_sync(
        win_names,
        fracs,
        vx,
        vy,
        vw,
        vh,
        max_bottom_y=bottom_cap,
        preserve_main_viewport=preserve_main_viewport,
    )
    for ti in range(sn):
        if preserve_main_viewport and ti == 0:
            _sync_viewport_resolution_from_workspace_window("Viewport")
            continue
        nm = "Viewport" if ti == 0 else _split_window_name(ti)
        _sync_viewport_resolution_from_workspace_window(str(nm))


async def _apply_viewport_clipped_split_grid_async(ext: Any, token: int, n: int) -> None:
    """보조 창 생성·USD open 직후 Workspace rect 가 안정된 뒤 격자를 적용한다."""
    for _ in range(4):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        await kit_app.get_app().next_update_async()
    _apply_viewport_clipped_split_grid(n, ext=ext)
    try:
        sn = channel_count_for_split(int(n))
    except Exception:
        sn = int(n)
    for _ in range(12):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        await kit_app.get_app().next_update_async()
    for ti in range(0, max(1, int(sn))):
        nm = "Viewport" if ti == 0 else _split_window_name(ti)
        _sync_viewport_resolution_from_workspace_window(nm)


def _split_dock_operations(n: int) -> List[Tuple[str, str, Any, float]]:
    """
    보조 창 이름·기준 창 이름·``DockPosition``·비율을 **순서대로** ``dock_in`` 에 넘긴다.

    2: 좌=메인 ``Viewport``, 우=보조1. 3: 상=메인, 하좌·하우=보조. 4: 2×2 격자(메인 좌상).
    """
    DP = getattr(ui, "DockPosition", None)
    if DP is None:
        return []
    if n == 2:
        return [(_split_window_name(1), "Viewport", DP.RIGHT, 0.5)]
    if n == 3:
        return [
            (_split_window_name(1), "Viewport", DP.BOTTOM, 0.5),
            (_split_window_name(2), _split_window_name(1), DP.RIGHT, 0.5),
        ]
    if n == 4:
        return [
            (_split_window_name(1), "Viewport", DP.RIGHT, 0.5),
            (_split_window_name(2), "Viewport", DP.BOTTOM, 0.5),
            (_split_window_name(3), _split_window_name(1), DP.BOTTOM, 0.5),
        ]
    return []


async def _wait_workspace_windows_ready(
    ext: Any, token: int, names: List[str], max_frames: int = 16
) -> bool:
    """보조 창 이름이 ``Workspace`` 에 등록될 때까지 잠깐 대기."""
    for _ in range(max(1, int(max_frames))):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return False
        try:
            if all(ui.Workspace.get_window(n) is not None for n in names):
                return True
        except Exception:
            pass
        await kit_app.get_app().next_update_async()
    return False


async def _wait_aux_windows_docked(
    ext: Any, token: int, aux_names: List[str], max_frames: int = 12
) -> bool:
    """보조 창마다 ``docked`` 가 True 가 될 때까지 잠깐 대기(Kit 가 한두 프레임 늦게 갱신할 수 있음)."""
    for _ in range(max(1, int(max_frames))):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return False
        try:
            all_docked = True
            for nm in aux_names:
                w = ui.Workspace.get_window(nm)
                if w is None or not bool(getattr(w, "docked", False)):
                    all_docked = False
                    break
            if all_docked:
                return True
        except Exception:
            pass
        await kit_app.get_app().next_update_async()
    return False


def _dock_aux_into_target(child_w: Any, target_w: Any, pos: Any, ratio: float) -> bool:
    """보조 Workspace 창을 기준 ``Window`` 옆 Dock 에 붙인다. undock 없이 ``dock_in`` 만."""
    if child_w is None or target_w is None:
        return False
    din = getattr(child_w, "dock_in", None)
    if not callable(din):
        return False
    try:
        din(target_w, pos, float(ratio))
        return True
    except Exception:
        return False


def _dock_viewport_fill_dockspace() -> bool:
    """
    메인 ``Viewport`` Workspace 창을 ``DockSpace`` 안에서 다시 붙여 한 덩어리로 채운다.

    메뉴·패널을 숨긴 뒤에도 Dock 트리가 옛 비율을 유지하면 3D 영역이 늘지 않는다.
    Kit UI 테스트 패턴(``Viewport.dock_in(DockSpace, …)``)과 동일하게 ``dock_in`` 으로 갱신한다.
    """
    return _dock_viewport_to_dockspace(1.0)


def _dock_viewport_to_dockspace(ratio: float = 0.62) -> bool:
    """
    ``Viewport`` 를 ``DockSpace`` 에 붙인다.

    ``ratio=1.0`` 은 Viewport 가 DockSpace 전체를 차지(Stage 등 패널 소실).
    분할 teardown 복원에는 **0.62** 등 Kit 기본에 가까운 값을 쓴다.
    """
    try:
        ds = ui.Workspace.get_window("DockSpace")
        vp = ui.Workspace.get_window("Viewport")
        if ds is None or vp is None:
            return False
        DP = getattr(ui, "DockPosition", None)
        if DP is None:
            return False
        din = getattr(vp, "dock_in", None)
        if not callable(din):
            return False
        r = max(0.05, min(1.0, float(ratio)))
        for pos_name in ("LEFT", "TOP", "SAME"):
            pos = getattr(DP, pos_name, None)
            if pos is None:
                continue
            try:
                din(ds, pos, r)
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


_VP_LAYOUT_SYNC_MIN_DELTA_PX = 2
_VP_RES_SYNC_CACHE: Dict[str, Tuple[int, int]] = {}
_WS_RECT_SYNC_CACHE: Dict[str, Tuple[int, int, int, int]] = {}


def _clear_viewport_layout_sync_caches() -> None:
    """분할 teardown·재빌드 시 rect/resolution 동기화 캐시를 비운다."""
    try:
        _VP_RES_SYNC_CACHE.clear()
        _WS_RECT_SYNC_CACHE.clear()
    except Exception:
        pass


def _rect_delta_exceeds(
    a: Tuple[int, ...], b: Tuple[int, ...], threshold: int = _VP_LAYOUT_SYNC_MIN_DELTA_PX
) -> bool:
    if len(a) != len(b):
        return True
    return any(abs(int(x) - int(y)) >= int(threshold) for x, y in zip(a, b))


def _all_aux_split_windows_docked(n: int) -> bool:
    """보조 분할 창이 모두 Dock 에 붙어 있으면 True."""
    try:
        sn = channel_count_for_split(int(n))
    except Exception:
        sn = 1
    if sn <= 1:
        return True
    for ti in range(1, sn):
        try:
            w = ui.Workspace.get_window(_split_window_name(ti))
        except Exception:
            return False
        if w is None or not bool(getattr(w, "docked", False)):
            return False
    return True


def _workspace_set_rect_if_changed(
    win: Any, win_name: str, px: int, py: int, tw: int, th: int
) -> bool:
    """Workspace rect — 미세 변경이면 position/size 를 건드리지 않아 테두리 떨림을 줄인다."""
    wn = str(win_name or "").strip()
    if not wn or win is None:
        return False
    target = (int(px), int(py), int(tw), int(th))
    try:
        current = (
            int(getattr(win, "position_x", 0) or 0),
            int(getattr(win, "position_y", 0) or 0),
            int(getattr(win, "width", 0) or 0),
            int(getattr(win, "height", 0) or 0),
        )
    except Exception:
        current = None
    cached = _WS_RECT_SYNC_CACHE.get(wn)
    if current is not None and not _rect_delta_exceeds(current, target):
        if cached is None:
            _WS_RECT_SYNC_CACHE[wn] = target
        return False
    if cached is not None and not _rect_delta_exceeds(cached, target):
        return False
    try:
        win.position_x = target[0]
        win.position_y = target[1]
        win.width = target[2]
        win.height = target[3]
        _WS_RECT_SYNC_CACHE[wn] = target
        return True
    except Exception:
        return False


def _reapply_split_dock_in_geometry(ext: Any) -> bool:
    """
    이미 Dock 분할이 성공한 상태에서 ``dock_in`` 을 같은 순서·비율로 다시 호출한다.

    크롬(메뉴·패널) 숨김/복원 뒤 Dock 이 가용 면적을 다시 나누도록 할 때 사용한다.
    """
    if not sim_viewport_split_dock_enabled():
        return False
    try:
        n = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 1) or 1))
    except Exception:
        n = 1
    if n <= 1:
        return False
    ops = _split_dock_operations(n)
    if not ops:
        return False
    if _all_aux_split_windows_docked(n):
        return True
    ok_any = False
    for child, target, pos, ratio in ops:
        try:
            child_w = ui.Workspace.get_window(str(child))
            target_w = ui.Workspace.get_window(str(target))
        except Exception:
            child_w, target_w = None, None
        if child_w is None or target_w is None:
            continue
        if bool(getattr(child_w, "docked", False)):
            ok_any = True
            continue
        _undock_workspace_window(str(child))
        if _dock_aux_into_target(child_w, target_w, pos, float(ratio)):
            ok_any = True
    return ok_any


def _split_viewport_api(win_name: str) -> Any:
    """Widget 분할 또는 Workspace 뷰포트 API."""
    try:
        from .sim_multi_view_widget import get_split_viewport_api, is_split_widget_layout_active
        from .tbs_extension_singleton import get_tbs_extension_instance

        ext = get_tbs_extension_instance()
        if ext is not None and is_split_widget_layout_active(ext):
            return get_split_viewport_api(ext, str(win_name))
    except Exception:
        pass
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        return get_viewport_from_window_name(str(win_name))
    except Exception:
        return None


def _sync_entries_from_widget_tiles(ext: Any) -> None:
    """Widget 분할 후 ``entries`` 의 HUD·뷰포트 참조를 타일 레코드와 맞춘다."""
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active

        if not is_split_widget_layout_active(ext):
            return
    except Exception:
        return
    tiles = getattr(ext, "_tbs_split_widget_tiles", None)
    if not isinstance(tiles, dict):
        return
    entries = list(getattr(ext, "_sim_multi_viewport_entries", []) or [])
    for ent in entries:
        wn = str(ent.get("win_name") or "")
        if wn == "Viewport":
            try:
                vw = _resolve_viewport_window_for_workspace_name("Viewport")
                if vw is not None:
                    ent["viewport_window"] = vw
            except Exception:
                pass
            continue
        rec = tiles.get(wn)
        if not isinstance(rec, dict):
            continue
        if rec.get("widget") is None:
            if ent.get("kit_vp") is not None or ent.get("viewport_window") is not None:
                continue
        vpw = rec.get("viewport_window")
        wgt = rec.get("widget")
        if vpw is not None:
            ent["viewport_window"] = vpw
            ent["kit_vp"] = vpw
        elif wgt is not None:
            ent["kit_vp"] = wgt
        if bool(rec.get("_uses_viewport_window", False)):
            ent["kind"] = "aux_viewport"
        if rec.get("api") is not None:
            ent["viewport_api"] = rec.get("api")
        if rec.get("context_name") is not None:
            ent["context_name"] = rec.get("context_name")
    try:
        ext._sim_multi_viewport_entries = entries
    except Exception:
        pass


def _sync_viewport_resolution_from_workspace_window(win_name: str) -> None:
    """Workspace 창의 ``width``/``height`` 에 맞춰 뷰포트 API ``resolution`` 을 맞춘다(렌더 버퍼 크기)."""
    wn = str(win_name or "").strip()
    if not wn:
        return
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active
        from .tbs_extension_singleton import get_tbs_extension_instance

        ext = get_tbs_extension_instance()
        if ext is not None and is_split_widget_layout_active(ext):
            from .sim_multi_view_widget import sync_split_widget_fill_frame

            sync_split_widget_fill_frame(ext, 2)
            return
    except Exception:
        pass
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        w = ui.Workspace.get_window(wn)
        api = _split_viewport_api(wn) or get_viewport_from_window_name(wn)
        if w is None or api is None or not hasattr(api, "resolution"):
            return
        if bool(getattr(api, "fill_frame", False)):
            return
        ww = int(getattr(w, "width", 0) or 0)
        hh = int(getattr(w, "height", 0) or 0)
        if ww < 8 or hh < 8:
            return
        target = (max(1, ww), max(1, hh))
        prev = _VP_RES_SYNC_CACHE.get(wn)
        if prev is not None and not _rect_delta_exceeds(prev, target):
            return
        try:
            cur_res = api.resolution
            if cur_res is not None:
                cur_pair = (int(cur_res[0]), int(cur_res[1]))
                if not _rect_delta_exceeds(cur_pair, target):
                    _VP_RES_SYNC_CACHE[wn] = target
                    return
        except Exception:
            pass
        api.resolution = target
        _VP_RES_SYNC_CACHE[wn] = target
    except Exception:
        pass


def _split_tile_index_from_win_name(win_name: str) -> int:
    """``Viewport`` → 0, ``TBS_SimSplit_1`` → 1 …"""
    wn = str(win_name or "").strip()
    if wn == "Viewport":
        return 0
    if wn.startswith("TBS_SimSplit_"):
        try:
            return int(wn.rsplit("_", 1)[-1])
        except Exception:
            return 1
    return 0


def _compute_split_tile_pixel_rect(
    n: int, tile_index: int, ext: Any = None
) -> Tuple[int, int, int, int]:
    """격자 기준 타일 (px, py, tw, th). hydrate 후 Workspace 크기가 틀어져도 기준값."""
    if ext is not None:
        vx, vy, vw, vh = _get_split_tile_bbox(ext)
    else:
        vx, vy, vw, vh = _read_viewport_split_area()
    fracs = _split_cell_layout_fracs(n)
    ti = max(0, min(int(tile_index), len(fracs) - 1))
    bottom_cap = int(vy) + int(vh)
    if n == 2 and ti < 2 and int(vw) >= _VP_TILE_MIN_PX * 2:
        half_w = max(_VP_TILE_MIN_PX, int(vw) // 2)
        th = max(_VP_TILE_MIN_PX, int(vh))
        py = int(vy)
        if py + th > bottom_cap:
            th = max(_VP_TILE_MIN_PX, bottom_cap - py)
        if ti == 0:
            return (int(vx), py, half_w, th)
        return (int(vx) + half_w, py, int(vw) - half_w, th)
    x0, y0, x1, y1 = fracs[ti]
    tw = max(_VP_TILE_MIN_PX, int(vw * (x1 - x0)))
    th = max(_VP_TILE_MIN_PX, int(vh * (y1 - y0)))
    px = int(vx) + int(vw * x0)
    py = int(vy) + int(vh * y0)
    if py + th > bottom_cap:
        th = max(_VP_TILE_MIN_PX, bottom_cap - py)
    return (px, py, tw, th)


def refresh_split_viewport_resolution_from_grid(
    win_name: str,
    split_n: int,
    *,
    ext: Any = None,
    force_window_rect: bool = False,
) -> None:
    """
    격자 기준 해상도·(선택) Workspace rect.

    hydrate·``aux display activate`` 직후 Kit 이 보조 창을 1280×720 등 기본값으로 되돌리는 경우,
    Workspace ``width``/``height`` 대신 **격자 계산값**으로 맞춘다.
    """
    wn = str(win_name or "").strip()
    if not wn:
        return
    try:
        sn = channel_count_for_split(int(split_n))
    except Exception:
        sn = 2
    if sn <= 1:
        _sync_viewport_resolution_from_workspace_window(wn)
        return
    ti = _split_tile_index_from_win_name(wn)
    px, py, tw, th = _compute_split_tile_pixel_rect(sn, ti, ext)
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        if force_window_rect:
            win = ui.Workspace.get_window(wn)
            if win is not None:
                _workspace_show_named_window(wn, True)
                _workspace_set_rect_if_changed(win, wn, int(px), int(py), int(tw), int(th))
        api = get_viewport_from_window_name(wn)
        if api is not None:
            if hasattr(api, "fill_frame"):
                api.fill_frame = True
            if hasattr(api, "resolution") and not bool(getattr(api, "fill_frame", False)):
                target = (max(1, int(tw)), max(1, int(th)))
                prev = _VP_RES_SYNC_CACHE.get(wn)
                if prev is None or _rect_delta_exceeds(prev, target):
                    api.resolution = target
                    _VP_RES_SYNC_CACHE[wn] = target
    except Exception:
        pass


def _kit_panel_is_user_hidden(win_name: str) -> bool:
    """Workspace 패널이 이미 숨김(visible=False)이면 레이아웃 조정으로 다시 띄우지 않는다."""
    wn = str(win_name or "").strip()
    if not wn:
        return True
    try:
        w = ui.Workspace.get_window(wn)
        if w is not None and not bool(getattr(w, "visible", True)):
            return True
    except Exception:
        pass
    return False


def _should_skip_kit_panel_auto_show(win_name: str, ext: Any = None) -> bool:
    """배포 숨김·사용자 숨김 상태의 Console/Content 등은 자동 표시 대상에서 제외."""
    if _kit_panel_is_user_hidden(win_name):
        return True
    if ext is None:
        return False
    try:
        from .kit_chrome_visibility import is_kit_chrome_hidden

        if is_kit_chrome_hidden(ext):
            return True
    except Exception:
        pass
    return False


def _bring_kit_chrome_visible(ext: Any = None) -> None:
    """
    Console/Content 등 — 분할 후 가려짐 완화용으로 **이미 보이는** 패널만 앞으로 올린다.

    배포·``kit_chrome_hide`` 로 숨긴 패널(``visible=False``)은 건드리지 않는다.
    """
    if ext is not None:
        try:
            from .kit_chrome_visibility import is_kit_chrome_hidden

            if is_kit_chrome_hidden(ext):
                return
        except Exception:
            pass
    for nm in ("Console", "Content", "Stage", "Property"):
        if _should_skip_kit_panel_auto_show(str(nm), ext):
            continue
        _workspace_show_named_window(str(nm), True)


def reapply_split_layout_sync(ext: Any, n: int) -> None:
    """분할 레이아웃 재적용 — Widget / Dock / 격자 중 활성 경로만."""
    if _startup_split_relayout_suppressed(ext):
        return
    try:
        sn = channel_count_for_split(int(n))
    except Exception:
        return
    if sn <= 1:
        return
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active, sync_split_widget_fill_frame

        if is_split_widget_layout_active(ext):
            sync_split_widget_fill_frame(ext, sn)
            try:
                from .sim_multi_view_widget import ensure_viewport_workspace_tab_visible

                ensure_viewport_workspace_tab_visible()
            except Exception:
                pass
            try:
                set_viewport_fill_frame_for_split_count(sn, True)
            except Exception:
                pass
            _bring_kit_chrome_visible(ext)
            return
    except Exception:
        pass
    if bool(getattr(ext, "_tbs_split_used_dock_layout", False)):
        if not _all_aux_split_windows_docked(sn):
            _reapply_split_dock_in_geometry(ext)
        for ti in range(sn):
            nm = "Viewport" if ti == 0 else _split_window_name(ti)
            _sync_viewport_resolution_from_workspace_window(nm)
    else:
        _apply_viewport_clipped_split_grid(
            sn, ext=ext, preserve_main_viewport=True
        )
    try:
        set_viewport_fill_frame_for_split_count(sn, True)
    except Exception:
        pass
    _bring_kit_chrome_visible(ext)
    if sn >= 2:
        apply_viewport_split_tab_chrome(sn)


async def reapply_split_layout_after_hydrate_async(ext: Any, token: int, n: int) -> None:
    """hydrate 직후 레이아웃 재동기화 — Dock/격자 중 활성 경로만."""
    for _ in range(4):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        await kit_app.get_app().next_update_async()
    if _startup_split_relayout_suppressed(ext):
        return
    reapply_split_layout_sync(ext, n)
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active

        if is_split_widget_layout_active(ext):
            return
    except Exception:
        pass
    if bool(getattr(ext, "_tbs_split_used_dock_layout", False)):
        for _ in range(8):
            if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                return
            await kit_app.get_app().next_update_async()
        if not _all_aux_split_windows_docked(n):
            _reapply_split_dock_in_geometry(ext)
    else:
        await _enforce_equal_split_grid_async(
            ext, token, n, preserve_main_viewport=True
        )
    try:
        set_viewport_fill_frame_for_split_count(n, True)
    except Exception:
        pass
    _bring_kit_chrome_visible(ext)
    if n >= 2:
        apply_viewport_split_tab_chrome(n)
        if viewport_split_user_resize_locked():
            apply_viewport_split_user_resize_lock(ext)


def set_viewport_fill_frame_for_split_count(split_n: int, fill: bool) -> None:
    """
    뷰포트 API ``fill_frame`` — 부모 UI(Workspace / Dock 타일) 크기에 맞춰 렌더 해상도를 맞출지.

    ``fill_frame`` 가 꺼져 있으면 ``resolution`` 이 고정된 채로 남아, 메뉴·패널을 숨겨도
    3D 픽셀 영역이 늘지 않는 경우가 있다. ``morph.morph_base_viewer`` 등에서 쓰는 패턴과 같다.
    """
    try:
        from .hyview_stream import is_hyview_stream_layout_locked, bridge_stream_skip

        if is_hyview_stream_layout_locked():
            bridge_stream_skip(
                "fill_frame",
                "layout_locked",
                split_n=int(split_n),
                fill=bool(fill),
            )
            return
    except Exception:
        pass
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name
    except Exception:
        return
    try:
        sn = channel_count_for_split(int(split_n))
    except Exception:
        sn = 1
    for ti in range(sn):
        name = "Viewport" if ti == 0 else _split_window_name(ti)
        try:
            api = _split_viewport_api(str(name))
            if api is not None and hasattr(api, "fill_frame"):
                api.fill_frame = bool(fill)
        except Exception:
            pass


async def _apply_split_dock_layout(
    ext: Any, token: int, n: int, *, warn_on_dock_miss: bool = True
) -> bool:
    """보조 뷰를 메인 ``Viewport`` Dock 트리 안으로 넣는다. 실패 시 False(좌표 격자로 폴백)."""
    if not sim_viewport_split_dock_enabled():
        return False
    ops = _split_dock_operations(n)
    if not ops:
        return False
    aux_names = [_split_window_name(ti) for ti in range(1, n)]
    if not await _wait_workspace_windows_ready(ext, token, aux_names):
        try:
            print("[TBS multi-sim] Dock 분할: 보조 창 Workspace 등록 대기 시간 초과", flush=True)
        except Exception:
            pass
        return False
    for child, target, pos, ratio in ops:
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return False
        try:
            child_w = ui.Workspace.get_window(str(child))
            target_w = ui.Workspace.get_window(str(target))
        except Exception:
            child_w, target_w = None, None
        if child_w is None:
            try:
                print(f"[TBS multi-sim] Dock 분할: 보조 창 없음 name={child!r}", flush=True)
            except Exception:
                pass
            return False
        if target_w is None:
            try:
                print(f"[TBS multi-sim] Dock 분할: 기준 창 없음 name={target!r}", flush=True)
            except Exception:
                pass
            return False
        ok = _dock_aux_into_target(child_w, target_w, pos, float(ratio))
        if not ok:
            try:
                print(
                    f"[TBS multi-sim] Dock 분할 실패 child={child!r} target={target!r} ratio={ratio} "
                    f"(dock_in 미지원·예외 또는 Dock 거부)",
                    flush=True,
                )
            except Exception:
                pass
            return False
        for _ in range(2):
            if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                return False
            await kit_app.get_app().next_update_async()
    if not await _wait_aux_windows_docked(ext, token, aux_names, max_frames=72):
        if not _aux_windows_dock_plausible(n):
            if warn_on_dock_miss:
                try:
                    print(
                        "[TBS multi-sim] Dock: docked 플래그 미확인 — 격자 폴백 또는 재시도",
                        flush=True,
                    )
                except Exception:
                    pass
            return False
    if not _aux_windows_dock_plausible(n):
        return False
    for nm in aux_names:
        hidden = False
        for ent in list(getattr(ext, "_sim_multi_viewport_entries", []) or []):
            if str(ent.get("win_name") or "") == str(nm) and ent.get("aux_hidden_until_load"):
                hidden = True
                break
        if hidden:
            continue
        _workspace_show_named_window(str(nm), True)
        _sync_viewport_resolution_from_workspace_window(str(nm))
    try:
        print("[TBS multi-sim] Viewport Dock 분할 적용 완료 (dock_in)", flush=True)
    except Exception:
        pass
    try:
        from .kit_chrome_visibility import apply_viewport_dock_tab_bars_hidden

        apply_viewport_dock_tab_bars_hidden()
        apply_viewport_split_tab_chrome(n)
    except Exception:
        pass
    return True


async def _retry_split_dock_layout(ext: Any, token: int, n: int, *, attempts: int = 4) -> bool:
    """``dock_in`` 이 floating 좌표 때문에 실패할 때 undock 후 재시도."""
    last = max(1, int(attempts))
    for attempt in range(last):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return False
        if await _apply_split_dock_layout(
            ext, token, n, warn_on_dock_miss=(attempt >= last - 1)
        ):
            return True
        if attempt + 1 < last:
            for ti in range(1, n):
                _undock_workspace_window(_split_window_name(ti))
            for _ in range(4):
                await kit_app.get_app().next_update_async()
    return False


def _main_usd_path_for_clone(ext: Any) -> Optional[str]:
    """TBS Load 로 연 경로 우선, 없으면 현재 기본 스테이지 루트 레이어에서 추정."""
    try:
        from .tbs_split_composed_loader import resolve_split_master_usd_path

        return resolve_split_master_usd_path(ext)
    except Exception:
        pass
    try:
        lp = str(getattr(ext, "_tbs_last_loaded_usd_path", "") or "").strip()
        if lp:
            return lp
    except Exception:
        pass
    st = get_stage()
    if st is None:
        return None
    try:
        lyr = st.GetRootLayer()
        if lyr is None:
            return None
        p = getattr(lyr, "realPath", None) or lyr.identifier
        s = str(p or "").strip()
        return s or None
    except Exception:
        return None


def _layout_first_shell_pass(ext: Any, *, skip_hydrate: bool) -> bool:
    """layout-first: USD 로드 전 Dock·격자만 먼저 적용하는 경로인지."""
    if not skip_hydrate:
        return False
    if startup_layout_first_active(ext):
        return True
    if startup_dual_orchestration_active(ext):
        return True
    if bool(getattr(ext, "_tbs_split_deferred_aux_load_pending", False)):
        return True
    return False


def startup_layout_first_active(ext: Any) -> bool:
    """앱 시작 시 USD 로드 전 2분할 레이아웃만 먼저 적용 중인지."""
    return bool(getattr(ext, "_tbs_startup_layout_first_active", False))


def _startup_split_relayout_suppressed(ext: Any) -> bool:
    """layout-first 시작 중에는 중간 relayout·navigation 재적용을 막는다."""
    if startup_dual_orchestration_active(ext):
        return True
    if bool(getattr(ext, "_tbs_startup_aux_load_inflight", False)):
        return True
    return False


def startup_dual_layout_settled(ext: Any) -> bool:
    """layout-first: Dock 50:50 배치가 끝났고 USD 로드 전/중 재배치를 막을 때."""
    return bool(getattr(ext, "_tbs_startup_dual_layout_settled", False))


def preserve_split_layout_during_startup(ext: Any) -> bool:
    """시작 직후 분할 UI 동기화가 2분할을 1로 되돌리지 않도록 한다."""
    if startup_layout_first_active(ext):
        return True
    if startup_dual_orchestration_active(ext):
        return True
    if bool(getattr(ext, "_tbs_split_deferred_aux_load_pending", False)):
        return True
    if bool(getattr(ext, "_tbs_startup_aux_load_inflight", False)):
        return True
    if bool(getattr(ext, "_tbs_defer_master_autoload_until_dual_layout", False)):
        return True
    return False


def startup_dual_orchestration_active(ext: Any) -> bool:
    """layout-first 2화면 시작이 끝날 때까지 중복 relayout 을 막는다."""
    return bool(getattr(ext, "_tbs_startup_dual_orchestration_active", False))


def _begin_startup_dual_orchestration(ext: Any) -> None:
    try:
        ext._tbs_startup_dual_orchestration_active = True
        ext._tbs_startup_pending_chrome_relayout = None
    except Exception:
        pass


async def _ensure_split_layout_geometry_ready(ext: Any, token: int, n: int) -> None:
    """layout-first: 보조 창을 표시하기 전 Dock 검증·재시도, 실패 시 격자 폴백."""
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active, sync_split_widget_fill_frame

        if is_split_widget_layout_active(ext):
            sync_split_widget_fill_frame(ext, n)
            return
    except Exception:
        pass
    if not sim_viewport_split_dock_enabled():
        await _enforce_equal_split_grid_async(ext, token, n, preserve_main_viewport=True)
        try:
            ext._tbs_split_used_dock_layout = False
        except Exception:
            pass
        return
    for _ in range(4):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        await kit_app.get_app().next_update_async()
    if _aux_windows_actually_docked(n):
        return
    docked_ok = await _retry_split_dock_layout(ext, token, n, attempts=6)
    try:
        ext._tbs_split_used_dock_layout = bool(docked_ok)
    except Exception:
        pass
    if _aux_windows_actually_docked(n):
        return
    try:
        print("[TBS multi-sim] layout-first: Dock 미확인 → 동일 크기 격자 폴백", flush=True)
    except Exception:
        pass
    for ti in range(1, n):
        _undock_workspace_window(_split_window_name(ti))
    for _ in range(4):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        await kit_app.get_app().next_update_async()
    await _enforce_equal_split_grid_async(ext, token, n, preserve_main_viewport=True)
    try:
        ext._tbs_split_used_dock_layout = False
    except Exception:
        pass


async def _finish_startup_dual_orchestration(ext: Any) -> None:
    """화면1·2 USD 로드 후 chrome·해상도만 맞춤 — Dock/격자 재배치는 하지 않는다."""
    try:
        from .kit_chrome_visibility import (
            KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH,
            apply_kit_chrome_hidden,
            is_kit_chrome_hidden,
        )

        if KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH and not is_kit_chrome_hidden(ext):
            apply_kit_chrome_hidden(ext, True, schedule_layout_refresh=False)
    except Exception:
        pass
    try:
        ext._tbs_startup_dual_orchestration_active = False
        ext._tbs_startup_pending_chrome_relayout = None
        ext._tbs_startup_dual_layout_settled = False
    except Exception:
        pass
    for _ in range(4):
        await kit_app.get_app().next_update_async()
    try:
        sn = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 2) or 2))
    except Exception:
        sn = 2
    if sn > 1:
        tok = int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0)
        try:
            from .sim_multi_view_widget import is_split_widget_layout_active, sync_split_widget_fill_frame

            if is_split_widget_layout_active(ext):
                sync_split_widget_fill_frame(ext, sn)
                try:
                    from .sim_multi_view_widget import finalize_widget_split_startup

                    tok = int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0)
                    await finalize_widget_split_startup(ext, tok, sn)
                except Exception:
                    pass
                try:
                    set_viewport_fill_frame_for_split_count(sn, True)
                except Exception:
                    pass
                return
        except Exception:
            pass
        if not _aux_windows_actually_docked(sn):
            if bool(getattr(ext, "_tbs_split_used_dock_layout", False)):
                _reapply_split_dock_in_geometry(ext)
            if not _aux_windows_actually_docked(sn):
                await _retry_split_dock_layout(ext, tok, sn, attempts=4)
            if not _aux_windows_actually_docked(sn):
                await _enforce_equal_split_grid_async(
                    ext, tok, sn, preserve_main_viewport=True
                )
                try:
                    ext._tbs_split_used_dock_layout = False
                except Exception:
                    pass
        try:
            set_viewport_fill_frame_for_split_count(sn, True)
        except Exception:
            pass
        for ti in range(sn):
            nm = "Viewport" if ti == 0 else _split_window_name(ti)
            _sync_viewport_resolution_from_workspace_window(nm)
        apply_viewport_split_tab_chrome(sn)
        if viewport_split_user_resize_locked():
            apply_viewport_split_user_resize_lock(ext)


def _dual_path_split_defer_skip_shell(ext: Any) -> bool:
    """layout-first + dual-path: shell placeholder 생략(1회 open 만)."""
    try:
        from .tbs_split_composed_loader import split_dual_usd_paths_enabled

        return bool(split_dual_usd_paths_enabled(ext))
    except Exception:
        return False


def _startup_split_usd_path(ext: Any) -> Optional[str]:
    """레이아웃 선적용 시 Master 아직 없을 때 default_load_usd_path 로 복제 경로를 확보."""
    p = _main_usd_path_for_clone(ext)
    if p:
        return p
    try:
        from .tbs_usd_window import default_load_usd_path
        from .tbs_data_paths import resolve_local_data_path

        resolved = resolve_local_data_path(default_load_usd_path)
        return str(resolved).strip() if resolved else None
    except Exception:
        return None


def apply_startup_dual_layout_first(ext: Any, split_n: int = 2) -> None:
    """앱 시작: 최종 2분할 Dock 레이아웃을 먼저 만든 뒤 USD 는 콜백에서 로드."""
    try:
        ext._tbs_startup_layout_first_active = True
    except Exception:
        pass
    _begin_startup_dual_orchestration(ext)
    apply_sim_viewport_split_layout(ext, split_n)


def schedule_deferred_aux_usd_load_after_master(ext: Any) -> None:
    """layout-first: 화면1 Master 로드 완료 후 화면2 USD 를 백그라운드로 연다."""
    if not bool(getattr(ext, "_tbs_split_deferred_aux_load_pending", False)):
        return
    try:
        ext._tbs_split_deferred_aux_load_pending = False
    except Exception:
        pass

    async def _go() -> None:
        try:
            ext._tbs_startup_aux_load_inflight = True
        except Exception:
            pass
        try:
            for _ in range(_STARTUP_AUX_LOAD_SETTLE_FRAMES):
                await kit_app.get_app().next_update_async()
            try:
                n = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 1) or 1))
            except Exception:
                n = 1
            if n <= 1:
                return
            usd_path = _main_usd_path_for_clone(ext) or _startup_split_usd_path(ext)
            if not usd_path:
                return
            tok = int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0)
            try:
                print("[TBS multi-sim] 레이아웃 완료 → 화면2 USD 로드 시작", flush=True)
            except Exception:
                pass
            await _load_aux_split_stages_background(
                ext, n, tok, usd_path, prev_n=1, serialize_gpu=True
            )
        finally:
            try:
                ext._tbs_startup_aux_load_inflight = False
            except Exception:
                pass
            try:
                await _finish_startup_dual_orchestration(ext)
            except Exception:
                pass

    try:
        asyncio.ensure_future(_go())
    except Exception:
        pass


def _apply_stage_fps_30(st: Any) -> None:
    try:
        st.SetTimeCodesPerSecond(30.0)
    except Exception:
        pass
    try:
        st.SetFramesPerSecond(30.0)
    except Exception:
        pass


def _register_session_layer_path(ext: Any, path: str) -> None:
    """teardown 시 삭제할 보조 임시 USD(session / 루트 래퍼 등) 경로를 누적한다."""
    if not path:
        return
    try:
        lst = list(getattr(ext, "_tbs_split_session_layer_paths", []) or [])
        if path not in lst:
            lst.append(path)
        ext._tbs_split_session_layer_paths = lst
    except Exception:
        pass


def _unlink_split_session_files(ext: Any) -> None:
    for p in list(getattr(ext, "_tbs_split_session_layer_paths", []) or []):
        try:
            if p and os.path.isfile(p):
                os.unlink(p)
        except Exception:
            pass
    try:
        ext._tbs_split_session_layer_paths = []
    except Exception:
        pass


def _unlink_one_session_file(ext: Any, path: str) -> None:
    """등록 목록에서 제거한 뒤 해당 임시 파일만 삭제한다."""
    if not path:
        return
    try:
        lst = list(getattr(ext, "_tbs_split_session_layer_paths", []) or [])
        if path in lst:
            lst.remove(path)
        ext._tbs_split_session_layer_paths = lst
    except Exception:
        pass
    try:
        if path and os.path.isfile(path):
            os.unlink(path)
    except Exception:
        pass


def _clone_dest_suffix(usd_path: str) -> str:
    low = str(usd_path or "").split("?", 1)[0].lower()
    for suf in (".usdc", ".usda", ".usd"):
        if low.endswith(suf):
            return suf
    return ".usd"


async def _clone_usd_for_aux_tile(usd_path: str, ext: Any, token: int, ti: int) -> Tuple[Optional[str], str]:
    """원본을 타일 전용 임시 파일로 복사한다. 성공 시 경로, 실패 시 (None, err)."""
    suf = _clone_dest_suffix(usd_path)
    dest = os.path.normpath(
        os.path.join(
            tempfile.gettempdir(),
            f"morph_tbs_clone_aux_{ti}_{token}_{os.getpid()}{suf}",
        )
    )
    dest_uri = Path(dest).as_uri()
    src_uri = usd_path
    try:
        from .tbs_split_composed_loader import usd_path_for_omni_client

        src_uri = usd_path_for_omni_client(usd_path)
    except Exception:
        src_uri = usd_path
    try:
        await asyncio.wait_for(omni.client.copy_async(src_uri, dest_uri), timeout=300.0)
    except Exception as e:
        try:
            if os.path.isfile(dest):
                os.unlink(dest)
        except Exception:
            pass
        return None, str(e)
    try:
        if not os.path.isfile(dest) or os.path.getsize(dest) < 16:
            try:
                if os.path.isfile(dest):
                    os.unlink(dest)
            except Exception:
                pass
            return None, "복사 결과 파일이 없거나 너무 작음"
    except Exception:
        return None, "복사 결과 확인 실패"
    _register_session_layer_path(ext, dest)
    try:
        print(f"[TBS multi-sim] 보조 타일 {ti}: 분할용 USD 복제 완료 ({os.path.getsize(dest)} bytes)", flush=True)
    except Exception:
        pass
    return dest, ""


def _make_aux_wrapper_root_layer(usd_path: str, ext: Any, token: int, ti: int) -> Tuple[Optional[str], str]:
    """
    원본 ``usd_path`` 를 subLayer 로만 가리키는 **고유 루트 .usda** 를 만든다.
    메인이 직접 연 원본과 **다른 루트 파일 식별자**를 갖게 해 Kit 의 Stage 공유를 피한다.
    """
    ident = str(usd_path or "").strip().replace("\\", "/")
    try:
        from .tbs_data_paths import resolve_local_data_path

        resolved = resolve_local_data_path(usd_path) or usd_path
        ident = str(resolved or "").strip().replace("\\", "/")
    except Exception:
        pass
    if not ident:
        return None, "빈 usd 경로"
    wrap_path = os.path.normpath(
        os.path.join(
            tempfile.gettempdir(),
            f"morph_tbs_wrap_aux_{ti}_{token}_{os.getpid()}.usda",
        )
    )
    try:
        from pxr import Sdf

        lyr = Sdf.Layer.CreateAnonymous("morph_tbs_wrap")
        lyr.subLayerPaths.append(ident)
        if lyr.Export(wrap_path) is False:
            return None, "wrapper Sdf.Layer.Export returned False"
        if Sdf.Layer.FindOrOpen(wrap_path) is None:
            return None, "wrapper FindOrOpen failed after Export"
    except Exception as e:
        return None, f"wrapper usd 생성 실패: {e}"
    _register_session_layer_path(ext, wrap_path)
    return wrap_path, ""


def _export_empty_session_usda(sess_path: str) -> Tuple[bool, str]:
    try:
        from pxr import Sdf

        anon = Sdf.Layer.CreateAnonymous("morph_tbs_aux_sess")
        if anon.Export(sess_path) is False:
            return False, "Sdf.Layer.Export(session) returned False"
        if Sdf.Layer.FindOrOpen(sess_path) is None:
            return False, "session layer FindOrOpen failed after Export"
    except Exception as e:
        return False, f"session layer 파일 생성 실패: {e}"
    return True, ""


def _make_aux_shell_stage_path(ext: Any, token: int, ti: int) -> Tuple[Optional[str], str]:
    """분할 레이아웃만 먼저 보여줄 때 쓰는 최소 빈 스테이지(즉시 open)."""
    path = os.path.normpath(
        os.path.join(
            tempfile.gettempdir(),
            f"morph_tbs_shell_aux_{ti}_{token}_{os.getpid()}.usda",
        )
    )
    try:
        from pxr import Usd

        stage = Usd.Stage.CreateNew(path)
        stage.DefinePrim("/World", "Xform")
        stage.GetRootLayer().Save()
    except Exception as exc:
        return None, f"shell stage 생성 실패: {exc}"
    _register_session_layer_path(ext, path)
    return path, ""


async def _ctx_open_stage_path(ctx: Any, root: str, sess_path: Optional[str]) -> Tuple[bool, str]:
    """보조 컨텍스트에서 ``root`` 스테이지를 연다. ``sess_path`` 가 있으면 session layer 와 함께."""
    sess_uri = Path(sess_path).as_uri() if sess_path else None

    oa = getattr(ctx, "open_stage_async", None)
    if callable(oa):
        res: Any = None
        if sess_uri:
            oa_tried = False
            try:
                try:
                    res = await oa(root, session_layer_url=sess_uri)
                except TypeError:
                    res = await oa(root, session_layer_url=sess_path)
                oa_tried = True
            except TypeError:
                pass
            except Exception as e:
                if not oa_tried:
                    pass
                else:
                    return False, str(e)
            if oa_tried:
                ok_async = False
                if isinstance(res, tuple) and len(res) >= 2:
                    ok_async = bool(res[0])
                else:
                    ok_async = bool(res)
                if ok_async:
                    return True, ""
        else:
            try:
                res = await oa(root)
            except Exception as e:
                return False, str(e)
            if isinstance(res, tuple) and len(res) >= 2:
                ok, err = bool(res[0]), str(res[1] or "")
                return (True, "") if ok else (False, err or "open_stage_async failed")
            if res:
                return True, ""
            return False, "open_stage_async failed"

    osw = getattr(ctx, "open_stage_with_session_layer", None)
    if sess_path and callable(osw):
        ev = asyncio.Event()
        err_holder: List[str] = []

        def _cb(*cb_args: Any) -> None:
            ok = bool(cb_args[0]) if cb_args else False
            msg = str(cb_args[1]) if len(cb_args) > 1 else ""
            if not ok:
                err_holder.append(str(msg or "open_stage_with_session_layer failed"))
            ev.set()

        try:
            try:
                osw(root, sess_uri, _cb)
            except TypeError:
                osw(root, sess_path, _cb)
        except Exception as e:
            return False, str(e)
        for _ in range(240):
            if ev.is_set():
                break
            await kit_app.get_app().next_update_async()
        if err_holder:
            return False, err_holder[0]
        return True, ""

    try:
        if hasattr(ctx, "open_stage"):
            ctx.open_stage(root)
            return True, ""
    except Exception as e:
        return False, str(e)
    return False, "open_stage 불가"


async def _open_aux_stage_with_unique_session(
    ctx: Any,
    usd_path: str,
    ext: Any,
    token: int,
    ti: int,
    *,
    skip_file_clone: bool = False,
) -> Tuple[bool, str]:
    """
    보조 컨텍스트에서 씬을 연다.

    1) **타일 전용 복제/스냅샷**(``morph_tbs_clone_aux_*`` / ``morph_tbs_composed_aux_*``)은 바로 연다.
    2) 그 외: 원본 USD 를 타일별 임시 파일로 **반드시** 복제(``TBS_MULTI_SPLIT_FILE_CLONE=0`` 이면 실패).
    3) **래퍼 .usda(subLayer)** 는 메인과 ``Sdf.Layer`` 를 공유해 Hydra 동시 렌더 시 GPU crash → 분할 3D 에서 사용 안 함.
    4) session layer 는 복제 파일 open 시 생략(가속).
    """
    root_path: Optional[str] = None
    p_in = str(usd_path or "").strip()
    if _is_preopened_aux_stage_path(p_in, ext, ti):
        try:
            from .tbs_data_paths import resolve_local_data_path
            from .tbs_split_composed_loader import aux_usd_path_is_direct_open, resolve_split_aux_usd_path

            if aux_usd_path_is_direct_open(p_in, ext, ti):
                root_path = str(resolve_split_aux_usd_path(ext, ti) or resolve_local_data_path(p_in) or p_in)
            else:
                root_path = p_in
        except Exception:
            root_path = p_in
    elif not skip_file_clone and _use_aux_file_clone():
        clone_path, cerr = await _clone_usd_for_aux_tile(usd_path, ext, token, ti)
        if clone_path:
            root_path = clone_path
        else:
            try:
                print(
                    f"[TBS multi-sim] 보조 USD 복제 실패 tile={ti} (래퍼 폴백 금지 — subLayer 공유 crash): {cerr}",
                    flush=True,
                )
            except Exception:
                pass
            return False, str(cerr or "보조 USD 복제 실패")

    if root_path is None:
        if sim_viewport_split_3d_enabled():
            return False, "분할 3D: 타일별 USD 복제 필수(래퍼 subLayer 공유는 GPU crash 유발)"
        wrap_path, werr = _make_aux_wrapper_root_layer(usd_path, ext, token, ti)
        if not wrap_path:
            return False, werr
        root_path = wrap_path

    composed_open = _is_split_aux_stage_file(str(root_path or usd_path), ext, ti)
    sess_path: Optional[str] = None
    if _use_multi_split_session_layer() and not composed_open:
        sess_path = os.path.normpath(
            os.path.join(
                tempfile.gettempdir(),
                f"morph_tbs_sess_aux_{ti}_{token}_{os.getpid()}.usda",
            )
        )
        ok_s, err_s = _export_empty_session_usda(sess_path)
        if not ok_s:
            _unlink_one_session_file(ext, root_path)
            return False, err_s
        _register_session_layer_path(ext, sess_path)
        ok, err = await _ctx_open_stage_path(ctx, root_path, sess_path)
        if ok:
            return True, ""
        try:
            print(f"[TBS multi-sim] 보조: 루트+session 실패 → 루트 단독 재시도 ({err})", flush=True)
        except Exception:
            pass
        _unlink_one_session_file(ext, sess_path)
        sess_path = None

    ok2, err2 = await _ctx_open_stage_path(ctx, root_path, None)
    if ok2:
        return True, ""
    return False, err2 or "보조 스테이지 열기 실패"


def _log_split_stage_not_shared_with_main(ctx_names: List[str]) -> None:
    """메인 스테이지와 보조 스테이지가 동일 Python 객체인지(공유 버그) 검사."""
    try:
        import omni.usd as ou

        main = ou.get_context().get_stage()
        if main is None:
            return
        for nm in ctx_names:
            c = ou.get_context(nm)
            if c is None:
                continue
            st = c.get_stage() if hasattr(c, "get_stage") else None
            if st is None:
                continue
            if st is main:
                print(
                    f"[TBS multi-sim] 경고: 보조 컨텍스트 {nm!r} 가 메인과 동일 Usd.Stage 객체를 공유합니다.",
                    flush=True,
                )
    except Exception:
        pass


def _named_usd_context(name: str) -> Optional[Any]:
    """이름 있는 USD 컨텍스트 생성 또는 획득."""
    try:
        import omni.usd as ou
    except Exception:
        return None
    try:
        ctx = ou.get_context(name)
        if ctx is not None:
            return ctx
    except Exception:
        ctx = None
    try:
        creator = getattr(ou, "create_context", None)
        if callable(creator):
            return creator(name)
    except Exception:
        pass
    return None


def _close_usd_context_stage(ctx: Any) -> None:
    """같은 USD 컨텍스트에 다른 루트를 열기 전에 기존 스테이지를 닫는다."""
    if ctx is None:
        return
    for op in ("close_stage", "unload_stage"):
        fn = getattr(ctx, op, None)
        if callable(fn):
            try:
                fn()
                return
            except Exception:
                pass


async def _prepare_aux_context_for_stage_swap(ctx: Any, *, frame_wait: int = 4) -> None:
    """shell·이전 스테이지가 Hydra 에 묶인 채 재오픈하면 GPU crash → 닫고 몇 프레임 대기."""
    _close_usd_context_stage(ctx)
    for _ in range(max(1, int(frame_wait))):
        await kit_app.get_app().next_update_async()


def _release_usd_context_names(names: List[str]) -> None:
    if not names:
        return
    try:
        import omni.usd as ou
    except Exception:
        return
    for nm in names:
        try:
            ctx = ou.get_context(nm)
        except Exception:
            ctx = None
        if ctx is None:
            continue
        _close_usd_context_stage(ctx)
        rel = getattr(ou, "release_context", None)
        if callable(rel):
            try:
                rel(nm)
            except Exception:
                pass


def _destroy_viewport_window(win: Any) -> None:
    if win is None:
        return
    candidates: List[Any] = [win]
    try:
        aw = getattr(win, "active_window", None)
        if aw is not None and aw not in candidates:
            candidates.append(aw)
    except Exception:
        pass
    try:
        vw = getattr(win, "viewport_widget", None)
        if vw is not None and vw not in candidates:
            candidates.append(vw)
    except Exception:
        pass
    for obj in candidates:
        for meth in ("destroy", "close"):
            fn = getattr(obj, meth, None)
            if callable(fn):
                try:
                    fn()
                    return
                except Exception:
                    pass


def _destroy_kit_viewport(vp: Any) -> None:
    if vp is None:
        return
    fn = getattr(vp, "destroy", None)
    if callable(fn):
        try:
            fn()
        except Exception:
            pass


def _is_tbs_composed_snapshot_path(path: str) -> bool:
    """``export_main_composed_stage_to_temp`` / ``get_or_export_main_composed_stage`` 결과 경로."""
    try:
        base = os.path.basename(str(path or ""))
    except Exception:
        return False
    return base.startswith("morph_tbs_composed_aux_")


def _is_tbs_clone_aux_path(path: str) -> bool:
    """``_clone_usd_for_aux_tile`` 로 만든 타일 전용 복제 파일."""
    try:
        base = os.path.basename(str(path or ""))
    except Exception:
        return False
    return base.startswith("morph_tbs_clone_aux_")


def _is_preopened_aux_stage_path(path: str, ext: Any = None, ti: int = 0) -> bool:
    """이미 타일별로 복제·Export 된 경로 — 재복제·래퍼(subLayer 공유) 없이 바로 연다."""
    if _is_tbs_composed_snapshot_path(path) or _is_tbs_clone_aux_path(path):
        return True
    if ext is not None:
        try:
            from .tbs_split_composed_loader import aux_usd_path_is_direct_open

            if aux_usd_path_is_direct_open(path, ext, ti):
                return True
        except Exception:
            pass
    return False


def _is_split_aux_stage_file(path: str, ext: Any = None, ti: int = 0) -> bool:
    """분할 보조 타일용 stage — session layer 생략으로 open 가속."""
    if _is_preopened_aux_stage_path(path, ext, ti):
        return True
    try:
        base = os.path.basename(str(path or ""))
    except Exception:
        return False
    return base.startswith("morph_tbs_composed_aux_") or base.startswith("morph_tbs_clone_aux_")


def _log_viewport_usd_context_bind(win_name: str, expect_ctx: str) -> None:
    """뷰포트 API가 기대한 USD 컨텍스트 이름에 묶였는지 한 번 확인(조작 경로 디버그)."""
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        api = get_viewport_from_window_name(win_name)
        if api is None:
            return
        got = getattr(api, "usd_context_name", None)
        if got and expect_ctx and str(got) != str(expect_ctx):
            print(
                f"[TBS multi-sim] 뷰포트 컨텍스트 불일치: 창={win_name!r} api={got!r} 기대={expect_ctx!r}",
                flush=True,
            )
    except Exception:
        pass


_DISABLE_NAV_KEYS = (
    "disable_pan",
    "disable_zoom",
    "disable_tumble",
    "disable_look",
    "disable_move",
    "disable_fly",
)


def _is_viewport_camera_manipulator_model(obj: Any) -> bool:
    if obj is None or type(obj).__name__ == "CameraModel":
        return False
    get_as_floats = getattr(obj, "get_as_floats", None)
    set_floats = getattr(obj, "set_floats", None)
    if not callable(get_as_floats) or not callable(set_floats):
        return False
    try:
        transform = get_as_floats("transform")
        return bool(transform and len(transform) >= 16)
    except Exception:
        return False


def _collect_camera_manipulator_models_for_window(win_name: str) -> List[Any]:
    """지정 Workspace 뷰포트 창의 camera manipulator model 만 수집(active viewport 에 의존하지 않음)."""
    models: List[Any] = []
    seen: set[int] = set()

    def _try_add(obj: Any) -> None:
        if obj is None or not _is_viewport_camera_manipulator_model(obj):
            return
        oid = id(obj)
        if oid in seen:
            return
        seen.add(oid)
        models.append(obj)

    api: Any = None
    vp_win: Any = None
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        api = get_viewport_from_window_name(str(win_name))
    except Exception:
        api = None
    if api is not None:
        for attr in (
            "camera_manipulator",
            "_camera_manipulator",
            "manipulator",
            "camera_model",
            "_camera_model",
        ):
            _try_add(getattr(api, attr, None))
        for attr in ("viewport_window", "window", "_viewport_window", "_window"):
            cand = getattr(api, attr, None)
            if cand is not None and callable(getattr(cand, "get_frame", None)):
                vp_win = cand
                break
    if vp_win is None:
        try:
            w = ui.Workspace.get_window(str(win_name))
        except Exception:
            w = None
        if w is not None:
            for attr in ("viewport_window", "viewport", "_viewport_window"):
                cand = getattr(w, attr, None)
                if cand is not None:
                    vp_win = cand
                    break
    if vp_win is not None:
        for attr in (
            "camera_manipulator",
            "_camera_manipulator",
            "manipulator",
            "viewport_widget",
            "_viewport_widget",
            "viewport_frame",
            "_viewport_frame",
        ):
            w = getattr(vp_win, attr, None)
            _try_add(w)
            if w is not None:
                _try_add(getattr(w, "model", None))
                _try_add(getattr(w, "camera_manipulator", None))
                cm = getattr(w, "camera_manipulator", None)
                if cm is not None:
                    _try_add(getattr(cm, "model", None))
                for sv_attr in ("scene_view", "_scene_view"):
                    sv = getattr(w, sv_attr, None)
                    if sv is not None:
                        _try_add(getattr(sv, "model", None))
    return models


def _set_model_navigation_enabled(model: Any, enabled: bool) -> None:
    flag = 0 if enabled else 1
    try:
        model.set_ints("disable_undo", [1])
    except Exception:
        pass
    for key in _DISABLE_NAV_KEYS:
        try:
            model.set_ints(key, [flag])
        except Exception:
            pass


def _ensure_viewport_camera_navigation_enabled(win_name: str) -> None:
    """보조 분할 타일에서 Alt+드래그 orbit 등 카메라 조작이 가능하도록 navigation 을 켠다."""
    models = _collect_camera_manipulator_models_for_window(str(win_name))
    for model in models:
        _set_model_navigation_enabled(model, True)
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        api = get_viewport_from_window_name(str(win_name))
        if api is not None:
            for attr in ("enable_input", "inputs_enabled"):
                if hasattr(api, attr):
                    try:
                        setattr(api, attr, True)
                    except Exception:
                        pass
    except Exception:
        pass


def _schedule_split_viewport_input_ready(win_name: str, *, frames: int = 8) -> None:
    """뷰포트 생성 직후 manipulator 가 붙을 때까지 몇 프레임 뒤 navigation 을 다시 켠다."""

    async def _go() -> None:
        for _ in range(max(1, int(frames))):
            await kit_app.get_app().next_update_async()
        _ensure_viewport_camera_navigation_enabled(str(win_name))

    try:
        asyncio.ensure_future(_go())
    except Exception:
        pass


def _cancel_split_aux_navigation_hold(ext: Any) -> None:
    """이전 분할 빌드의 post_update navigation 재적용 구독을 해제한다."""
    sub = getattr(ext, "_sim_split_nav_hold_sub", None)
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
    try:
        ext._sim_split_nav_hold_sub = None
    except Exception:
        pass


def _apply_split_navigation_to_aux(ext: Any, n: int, token: int, *, hold_ticks: int = 48) -> None:
    """보조 타일 카메라 orbit/pan — Dock·레이아웃 변경 직후 호출."""
    try:
        from .sim_multi_view_widget import (
            apply_split_widget_navigation,
            is_split_widget_layout_active,
            refresh_split_widget_tiles_after_stage,
        )

        if is_split_widget_layout_active(ext):
            apply_split_widget_navigation(ext, n, token, hold_ticks=int(hold_ticks))
            return
    except Exception:
        pass
    try:
        sn = channel_count_for_split(int(n))
    except Exception:
        sn = 1
    if sn <= 1:
        return
    for ti in range(1, sn):
        wn = _split_window_name(ti)
        _ensure_viewport_camera_navigation_enabled(wn)
        _wire_split_viewport_click_focus(wn)
    _schedule_split_aux_navigation_hold(ext, n, token, ticks=int(hold_ticks))


def _schedule_split_aux_navigation_hold(ext: Any, n: int, token: int, *, ticks: int = 36) -> None:
    """
    Dock·레이아웃 직후 manipulator 가 재생성되며 navigation 이 꺼지는 경우(특히 3·4분할 2번째 보조 타일)를
    post_update 로 몇 프레임 동안 재적용한다.
    """
    _cancel_split_aux_navigation_hold(ext)
    try:
        sn = channel_count_for_split(int(n))
    except Exception:
        sn = 1
    if sn <= 1:
        return
    win_names = [_split_window_name(ti) for ti in range(1, sn)]
    remaining = [max(6, int(ticks))]
    sub_ref: List[Any] = [None]

    def _tick(_e: Any = None) -> None:
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != int(token):
            if sub_ref[0] is not None:
                try:
                    sub_ref[0].unsubscribe()
                except Exception:
                    pass
            try:
                ext._sim_split_nav_hold_sub = None
            except Exception:
                pass
            return
        if remaining[0] <= 0:
            if sub_ref[0] is not None:
                try:
                    sub_ref[0].unsubscribe()
                except Exception:
                    pass
            try:
                ext._sim_split_nav_hold_sub = None
            except Exception:
                pass
            return
        if remaining[0] % 2 == 0:
            for wn in win_names:
                _ensure_viewport_camera_navigation_enabled(str(wn))
        remaining[0] -= 1

    try:
        sub_ref[0] = kit_app.get_app().get_post_update_event_stream().create_subscription_to_pop(
            _tick,
            name="morph.tbs_control_2.split_aux_nav_hold",
        )
        try:
            ext._sim_split_nav_hold_sub = sub_ref[0]
        except Exception:
            pass
    except Exception:
        for wn in win_names:
            _ensure_viewport_camera_navigation_enabled(str(wn))


def _clear_split_viewport_input_hook(win_name: str) -> None:
    """보조 타일 파괴·재생성 시 이전 ``set_mouse_pressed_fn`` 잔여를 제거."""
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        api = get_viewport_from_window_name(str(win_name))
    except Exception:
        api = None
    for frame in _split_viewport_input_frames(str(win_name), api):
        for fn_name in ("set_mouse_pressed_fn", "set_mouse_released_fn"):
            fn = getattr(frame, fn_name, None)
            if callable(fn):
                try:
                    fn(None)
                except Exception:
                    pass


def _split_viewport_input_frames(win_name: str, api: Any = None) -> List[Any]:
    frames: List[Any] = []
    if api is None:
        try:
            from omni.kit.viewport.utility import get_viewport_from_window_name

            api = get_viewport_from_window_name(str(win_name))
        except Exception:
            api = None
    if api is not None:
        for attr in ("viewport_window", "window", "_viewport_window", "_window"):
            cand = getattr(api, attr, None)
            if cand is not None:
                fr = getattr(cand, "frame", None)
                if fr is not None:
                    frames.append(fr)
    try:
        w = ui.Workspace.get_window(str(win_name))
        fr = getattr(w, "frame", None) if w is not None else None
        if fr is not None:
            frames.append(fr)
    except Exception:
        pass
    out: List[Any] = []
    seen: set[int] = set()
    for fr in frames:
        if id(fr) not in seen:
            seen.add(id(fr))
            out.append(fr)
    return out


def _wire_split_viewport_click_focus(win_name: str) -> None:
    """보조 타일 클릭 시 해당 뷰포트를 active 로 — Workspace frame 이 아닌 viewport widget 에만 연결."""
    _clear_split_viewport_input_hook(str(win_name))
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        api = get_viewport_from_window_name(str(win_name))
    except Exception:
        api = None
    frames = _split_viewport_input_frames(str(win_name), api)
    if not frames:
        return

    def _on_press(_x: float, _y: float, _btn: int, _mod: int) -> bool:
        try:
            from omni.kit.viewport.utility import get_viewport_from_window_name

            api2 = get_viewport_from_window_name(str(win_name))
            if api2 is not None:
                focus_fn = getattr(api2, "focus", None)
                if callable(focus_fn):
                    focus_fn()
                _ensure_viewport_camera_navigation_enabled(str(win_name))
        except Exception:
            pass
        return False

    for frame in frames:
        fn_set = getattr(frame, "set_mouse_pressed_fn", None)
        if callable(fn_set):
            try:
                fn_set(_on_press)
            except Exception:
                pass


def _workspace_show_named_window(name: str, visible: bool) -> None:
    """
    이름 있는 Workspace 창의 표시를 바꾼다.

    ``get_window(...).visible`` 이 내부적으로 WindowHandle 경로를 타며
    ``Calling setVisible to WindowHandle will be deprecated`` 가 날 수 있어,
    가능하면 ``Workspace.show_window`` 를 쓴다.
    """
    wn = str(name or "").strip()
    if bool(visible) and wn.startswith("TBS_SimSplit"):
        try:
            from .sim_multi_view_widget import sim_viewport_split_widget_enabled

            if sim_viewport_split_widget_enabled():
                return
        except Exception:
            pass
    try:
        fn = getattr(ui.Workspace, "show_window", None)
        if callable(fn):
            fn(wn, bool(visible))
            return
    except Exception:
        pass
    try:
        w = ui.Workspace.get_window(wn)
        if w is not None:
            w.visible = bool(visible)
    except Exception:
        pass


def _restore_main_viewport_layout(ext: Any) -> None:
    """
    분할 전 저장한 Viewport 사각형으로 되돌린다.

    ``_tbs_split_saved_viewport_rect``(분할 중 캡처)는 Dock 분할 후 값이라
    1화면 복귀 시 Console/Content 를 덮을 수 있어 **사용하지 않는다**.
    """
    panels: Dict[str, Tuple[int, int, int, int]] = dict(
        getattr(ext, "_tbs_split_saved_panel_layout", None) or {}
    )
    r = panels.get("Viewport")
    if r is not None:
        _apply_window_rect("Viewport", r)
        return
    try:
        main_vp = ui.Workspace.get_window("Viewport")
        if main_vp is not None:
            _workspace_show_named_window("Viewport", True)
    except Exception:
        pass


def _undock_workspace_window(win_name: str) -> bool:
    """Dock 트리에 붙은 Workspace 창을 분리한다(幽靈 dock pane·collapsible Viewport 방지)."""
    wn = str(win_name or "").strip()
    if not wn:
        return False
    try:
        w = ui.Workspace.get_window(wn)
        if w is None:
            return False
        undock = getattr(w, "undock", None)
        if callable(undock):
            try:
                undock()
                return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def _aux_viewport_api_healthy(win_name: str) -> bool:
    """보조 타일 Workspace 이름에 대응하는 Viewport API 가 살아 있는지."""
    wn = str(win_name or "").strip()
    if not wn:
        return False
    try:
        from .sim_multi_view_widget import get_split_viewport_api, is_split_widget_layout_active
        from .tbs_extension_singleton import get_tbs_extension_instance

        ext = get_tbs_extension_instance()
        if ext is not None and is_split_widget_layout_active(ext):
            api = get_split_viewport_api(ext, wn)
            if api is not None:
                return True
            if wn != "Viewport":
                return False
    except Exception:
        pass
    try:
        if ui.Workspace.get_window(wn) is None:
            return False
    except Exception:
        return False
    try:
        api = _split_viewport_api(wn)
        if api is None:
            return False
        for attr in ("viewport_window", "window", "_viewport_window", "_window"):
            cand = getattr(api, attr, None)
            if cand is not None and callable(getattr(cand, "get_frame", None)):
                return True
        return callable(getattr(api, "get_frame", None))
    except Exception:
        return False


def _restore_single_viewport_after_dock_teardown(ext: Any) -> None:
    """
    Dock 2~4분할 → 1화면: Console/Content/Viewport **절대 좌표는 건드리지 않는다**.

    보조 ``dock_in`` 타일만 제거된 뒤 Kit Dock 이 Viewport 를 다시 채우게 둔다.
    """
    _workspace_show_named_window("Viewport", True)
    try:
        set_viewport_fill_frame_for_split_count(1, False)
    except Exception:
        pass
    _sync_viewport_resolution_from_workspace_window("Viewport")
    _bring_kit_chrome_visible(ext)


def _bump_split_teardown_restore_generation(ext: Any) -> int:
    """지연 Viewport 1화면 복원 코루틴을 무효화한다(확장 핫리로드 시 ext 인스턴스 무관)."""
    global _split_viewport_restore_gen
    try:
        _split_viewport_restore_gen = int(_split_viewport_restore_gen) + 1
    except Exception:
        _split_viewport_restore_gen = 1
    try:
        ext._tbs_split_restore_gen = int(_split_viewport_restore_gen)
    except Exception:
        pass
    return int(_split_viewport_restore_gen)


def _cancel_pending_split_viewport_restore(ext: Any) -> None:
    _bump_split_teardown_restore_generation(ext)


def _restore_viewport_after_split_teardown(ext: Any, *, schedule_deferred_restore: bool = True) -> None:
    """1분할 복귀 — Dock 분할이었으면 좌표 복원 없이, 격자였으면 Viewport 만 복원."""
    used_dock = bool(getattr(ext, "_tbs_split_used_dock_layout", False))
    if used_dock:
        _restore_single_viewport_after_dock_teardown(ext)
    else:
        _restore_main_viewport_layout(ext)
        _bring_kit_chrome_visible(ext)
    try:
        ext._tbs_split_used_dock_layout = False
    except Exception:
        pass
    if schedule_deferred_restore:
        _schedule_deferred_single_viewport_restore(ext, used_dock=used_dock)


def _schedule_deferred_single_viewport_restore(ext: Any, *, used_dock: bool) -> None:
    """보조 창 제거·Dock 재배치가 끝난 뒤 Viewport 해상도만 다시 맞춘다."""
    gen = _bump_split_teardown_restore_generation(ext)

    async def _go() -> None:
        for _ in range(14):
            await kit_app.get_app().next_update_async()
        if int(_split_viewport_restore_gen) != int(gen):
            return
        if used_dock:
            _restore_single_viewport_after_dock_teardown(ext)
        else:
            _restore_main_viewport_layout(ext)
            _bring_kit_chrome_visible(ext)

    try:
        asyncio.ensure_future(_go())
    except Exception:
        pass


def _schedule_deferred_kit_layout_restore(ext: Any) -> None:
    """(레거시) — ``_schedule_deferred_single_viewport_restore`` 로 위임."""
    used_dock = bool(getattr(ext, "_tbs_split_used_dock_layout", False))
    _schedule_deferred_single_viewport_restore(ext, used_dock=used_dock)


def _destroy_stale_split_workspace_window(win_name: str) -> None:
    """이전 분할 빌드가 남긴 동명 Workspace/Viewport 를 제거(undock 없음)."""
    wn = str(win_name or "").strip()
    if not wn:
        return
    _clear_split_viewport_input_hook(wn)
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        api = get_viewport_from_window_name(wn)
        if api is not None:
            _destroy_kit_viewport(api)
    except Exception:
        pass
    try:
        w = ui.Workspace.get_window(wn)
        if w is not None:
            for ent_attr in ("viewport_window", "window", "_viewport_window"):
                cand = getattr(w, ent_attr, None)
                if cand is not None:
                    _destroy_viewport_window(cand)
            _workspace_show_named_window(wn, False)
    except Exception:
        pass


def teardown_sim_multi_viewports(ext: Any, *, skip_deferred_restore: bool = False) -> None:
    """분할 뷰·보조 USD 컨텍스트를 정리하고 기본 Viewport 를 복원한다."""
    teardown_viewport_split_resize_lock(ext)
    _cancel_pending_split_viewport_restore(ext)
    _clear_viewport_layout_sync_caches()
    _cancel_split_aux_navigation_hold(ext)
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active, teardown_split_widget_host

        if is_split_widget_layout_active(ext):
            teardown_split_widget_host(ext)
    except Exception:
        pass
    used_dock = bool(getattr(ext, "_tbs_split_used_dock_layout", False))
    for ti in range(1, 5):
        wname = _split_window_name(ti)
        try:
            if ui.Workspace.get_window(wname) is not None:
                _undock_workspace_window(wname)
        except Exception:
            pass
        _destroy_stale_split_workspace_window(wname)
    try:
        from .tbs_split_composed_loader import release_aux_split_runtimes

        release_aux_split_runtimes(ext, keep_screen_1=True)
    except Exception:
        pass
    try:
        ext._sim_runners_by_screen = {}
    except Exception:
        pass
    destroy_viewport_snapshot_hud_layers(ext)
    entries: List[Dict[str, Any]] = list(getattr(ext, "_sim_multi_viewport_entries", []) or [])
    # 보조 뷰를 먼저 파괴한 뒤 메인 Viewport 를 복원한다(메인만 먼저 키우면 보조와 동시에 그려져 GPU 부담).
    for ent in entries:
        if ent.get("kind") in ("main_viewport", "widget_main"):
            continue
        wn = str(ent.get("win_name") or "")
        if wn:
            _clear_split_viewport_input_hook(wn)
            if used_dock and ent.get("kind") not in ("widget_aux",):
                _undock_workspace_window(wn)
        if ent.get("kind") in ("widget_aux",):
            continue
        if ent.get("kit_vp") is not None:
            _destroy_kit_viewport(ent.get("kit_vp"))
        else:
            _destroy_viewport_window(ent.get("window"))
    for ent in entries:
        if ent.get("kind") in ("main_viewport", "widget_main"):
            _restore_viewport_after_split_teardown(
                ext,
                schedule_deferred_restore=not bool(skip_deferred_restore),
            )
            break
    else:
        _restore_viewport_after_split_teardown(
            ext,
            schedule_deferred_restore=not bool(skip_deferred_restore),
        )
    try:
        ext._sim_multi_viewport_entries = []
    except Exception:
        pass

    names: List[str] = list(getattr(ext, "_sim_multi_context_names", []) or [])
    _release_usd_context_names(names)
    try:
        ext._sim_multi_context_names = []
    except Exception:
        pass

    try:
        ext._tbs_split_saved_viewport_rect = None
    except Exception:
        pass

    _unlink_split_session_files(ext)

    try:
        ext._tbs_split_main_viewport_window = None
    except Exception:
        pass

    try:
        ext._tbs_split_layout_usd_key = None
    except Exception:
        pass

    _workspace_show_named_window("Viewport", True)


def split_layout_needs_reapply(ext: Any) -> bool:
    """Dock·보조 타일이 실제로 살아 있지 않으면 분할 레이아웃 전체 재적용이 필요하다."""
    try:
        n = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 1) or 1))
    except Exception:
        n = 1
    if n <= 1:
        try:
            from .sim_control_defaults import default_viewport_split_count

            n = channel_count_for_split(int(default_viewport_split_count()))
        except Exception:
            n = 1
    if n <= 1:
        return False
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active, split_widget_layout_healthy

        if is_split_widget_layout_active(ext):
            return not split_widget_layout_healthy(ext, n)
    except Exception:
        pass
    if not _split_aux_layout_healthy(ext, n):
        return True
    if bool(getattr(ext, "_tbs_split_used_widget_layout", False)):
        return False
    if not bool(getattr(ext, "_tbs_split_used_dock_layout", False)):
        return True
    return False


def schedule_split_rebuild_after_master_reload(ext: Any) -> None:
    """
    Master USD 재로드 후 분할 수>1 이면 **뷰포트는 유지**하고 스테이지만 빠르게 갱신한다.

    Dock/타일이 깨져 있으면 ``apply_sim_viewport_split_layout`` 로 전체 재적용한다.
    """
    if _startup_split_relayout_suppressed(ext):
        return
    if bool(getattr(ext, "_tbs_startup_aux_load_inflight", False)):
        return
    if bool(getattr(ext, "_tbs_split_deferred_aux_load_pending", False)):
        return
    if split_layout_needs_reapply(ext):
        try:
            n = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 1) or 1))
        except Exception:
            n = 1
        if n <= 1:
            try:
                from .sim_control_defaults import default_viewport_split_count

                n = channel_count_for_split(int(default_viewport_split_count()))
            except Exception:
                n = 1
        if n > 1:
            apply_sim_viewport_split_layout(ext, n)
            return
    invalidate_split_layout_cache(ext)
    try:
        n = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 1) or 1))
    except Exception:
        n = 1
    if n <= 1:
        return

    async def _go() -> None:
        for _ in range(2):
            await kit_app.get_app().next_update_async()
        usd_path = _main_usd_path_for_clone(ext)
        if not usd_path:
            return
        tok = int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0)
        await _refresh_aux_split_stages_async(ext, n, tok, usd_path)

    try:
        asyncio.ensure_future(_go())
    except Exception:
        pass


_VP_TILE_MIN_PX = 128
_split_viewport_restore_gen: int = 0

# 분할 수 변경 시: 티어다운 직후 Hydra/뷰를 다시 만들기 전 짧은 settle (GPU 크래시 완화).
_SPLIT_REBUILD_SETTLE_SEC_FIRST = 0.05
_SPLIT_REBUILD_SETTLE_FRAMES_FIRST = 1
_SPLIT_REBUILD_SETTLE_SEC = 0.05
_SPLIT_REBUILD_SETTLE_FRAMES = 1

# layout-first startup: Master→보조 로드 구간 GPU 직렬화용 프레임 대기(UX 동일, 내부 settle 만 증가).
_STARTUP_AUX_LOAD_SETTLE_FRAMES = 16
_STARTUP_STAGE_SWAP_SETTLE_FRAMES = 12
_STARTUP_POST_OPEN_SETTLE_FRAMES = 8
_STARTUP_HYDRATE_SETTLE_FRAMES = 8
_STARTUP_POST_HYDRATE_SETTLE_FRAMES = 8


def _read_viewport_rect() -> Tuple[int, int, int, int]:
    """기본 Viewport 창의 화면상 사각형. 실패 시 메인 창 기준."""
    try:
        vp = ui.Workspace.get_window("Viewport")
        if vp is not None:
            return (
                int(getattr(vp, "position_x", 0) or 0),
                int(getattr(vp, "position_y", 0) or 0),
                int(getattr(vp, "width", 800) or 800),
                int(getattr(vp, "height", 600) or 600),
            )
    except Exception:
        pass
    try:
        mw = int(ui.Workspace.get_main_window_width() or 1280)
        mh = int(ui.Workspace.get_main_window_height() or 720)
        return (0, 0, max(400, mw), max(300, mh))
    except Exception:
        return (0, 0, 1280, 720)


def _read_window_rect(name: str) -> Optional[Tuple[int, int, int, int]]:
    try:
        w = ui.Workspace.get_window(str(name))
        if w is None:
            return None
        return (
            int(getattr(w, "position_x", 0) or 0),
            int(getattr(w, "position_y", 0) or 0),
            int(getattr(w, "width", 0) or 0),
            int(getattr(w, "height", 0) or 0),
        )
    except Exception:
        return None


def _apply_window_rect(name: str, rect: Tuple[int, int, int, int]) -> None:
    try:
        w = ui.Workspace.get_window(str(name))
        if w is None:
            return
        x, y, ww, hh = rect
        w.position_x = int(x)
        w.position_y = int(y)
        w.width = max(64, int(ww))
        w.height = max(64, int(hh))
        _workspace_show_named_window(str(name), True)
    except Exception:
        pass


def _capture_kit_panel_layout(ext: Any) -> None:
    """Console/Content/Viewport 등 Kit 패널 위치·크기 저장."""
    panels: Dict[str, Tuple[int, int, int, int]] = {}
    for nm in ("Viewport", "Console", "Content", "Stage", "Property"):
        r = _read_window_rect(nm)
        if r is not None and int(r[2]) >= 8 and int(r[3]) >= 8:
            panels[str(nm)] = r
    try:
        ext._tbs_split_saved_panel_layout = panels
    except Exception:
        pass


def _ensure_kit_chrome_panels_on_top(ext: Any) -> None:
    """
    분할 중 Console·Content 등이 Viewport 뒤로 가려지지 않도록 위치 복원 + 표시.

    Viewport 크기는 **건드리지 않는다**(Dock 분할이 Viewport 내부에서 처리).
    """
    panels: Dict[str, Tuple[int, int, int, int]] = dict(
        getattr(ext, "_tbs_split_saved_panel_layout", None) or {}
    )
    for nm in ("Console", "Content", "Stage", "Property"):
        if _should_skip_kit_panel_auto_show(str(nm), ext):
            continue
        r = panels.get(str(nm))
        if r is not None:
            _apply_window_rect(str(nm), r)
        _workspace_show_named_window(str(nm), True)


def _schedule_ensure_kit_chrome_panels_on_top(ext: Any) -> None:
    """Dock 재배치 직후 Kit 가 레이아웃을 다시 그린 뒤 Console/Content 를 앞으로."""

    async def _go() -> None:
        for _ in range(10):
            await kit_app.get_app().next_update_async()
        _ensure_kit_chrome_panels_on_top(ext)

    try:
        asyncio.ensure_future(_go())
    except Exception:
        pass


def _capture_kit_layout_before_split(ext: Any) -> None:
    """2~4분할 직전 현재 Kit 레이아웃 저장(1화면 복귀 시 Console/Content·Viewport 복원용)."""
    _capture_kit_panel_layout(ext)


def _restore_kit_full_layout_after_split(ext: Any) -> None:
    """
    1화면 복귀 — **Viewport 만** 분할 전 rect 로 복원.

    Console/Content 는 Kit Dock 탭 그룹이라 절대 좌표 복원 시 서로 덮는다.
    """
    panels: Dict[str, Tuple[int, int, int, int]] = dict(
        getattr(ext, "_tbs_split_saved_panel_layout", None) or {}
    )
    if not panels:
        _restore_single_viewport_after_dock_teardown(ext)
        return
    r = panels.get("Viewport")
    if r is not None:
        _apply_window_rect("Viewport", r)
    try:
        set_viewport_fill_frame_for_split_count(1, False)
    except Exception:
        pass
    _sync_viewport_resolution_from_workspace_window("Viewport")
    _bring_kit_chrome_visible(ext)


def _split_tile_bbox_top_cap_y(ext: Any, vy: int, vh: int) -> int:
    """분할 타일 하단 한계 — Console/Content **저장 위치** 기준(읽기만, 창은 이동하지 않음)."""
    cap = int(vy) + int(vh)
    panels: Dict[str, Tuple[int, int, int, int]] = dict(
        getattr(ext, "_tbs_split_saved_panel_layout", None) or {}
    )
    for panel_name in ("Console", "Content"):
        pr = panels.get(str(panel_name))
        if pr is None:
            pr = _read_window_rect(panel_name)
        if pr is None:
            continue
        if int(pr[2]) < 8 or int(pr[3]) < 8:
            continue
        panel_top = int(pr[1])
        if panel_top > int(vy) + _VP_TILE_MIN_PX and int(vy) + int(vh) > panel_top:
            cap = min(cap, panel_top)
    return max(int(vy) + _VP_TILE_MIN_PX, int(cap))


def _restore_kit_bottom_panels(ext: Any) -> None:
    """
    (1화면 복귀 전용) Console·Content 저장 rect 복원.

    **2~4분할 중에는 호출하지 않는다** — Kit Dock 탭(Console|Content) 관계가 깨져
    Content 가 Console 을 덮는다.
    """
    panels: Dict[str, Tuple[int, int, int, int]] = dict(
        getattr(ext, "_tbs_split_saved_panel_layout", None) or {}
    )
    for nm in ("Console", "Content"):
        if _should_skip_kit_panel_auto_show(str(nm), ext):
            continue
        r = panels.get(str(nm))
        if r is not None:
            _apply_window_rect(str(nm), r)
        _workspace_show_named_window(str(nm), True)


def _capture_split_tile_bbox(ext: Any) -> Tuple[int, int, int, int]:
    """
    분할 격자에 쓸 영역 — **분할 직전 저장한 Viewport** rect 를 우선 사용하고,
    Console/Content 상단까지만(읽기 전용) 자른다.

    Viewport·TBS_SimSplit_* 만 이후 단계에서 리사이즈한다.
    """
    panels: Dict[str, Tuple[int, int, int, int]] = dict(
        getattr(ext, "_tbs_split_saved_panel_layout", None) or {}
    )
    saved_vp = panels.get("Viewport")
    if (
        saved_vp is not None
        and int(saved_vp[2]) >= _VP_TILE_MIN_PX
        and int(saved_vp[3]) >= _VP_TILE_MIN_PX
    ):
        vx, vy, vw, vh = (
            int(saved_vp[0]),
            int(saved_vp[1]),
            int(saved_vp[2]),
            int(saved_vp[3]),
        )
    else:
        vx, vy, vw, vh = _read_viewport_rect()
    bottom = _split_tile_bbox_top_cap_y(ext, vy, vh)
    vh = max(_VP_TILE_MIN_PX, bottom - int(vy))
    if vw < _VP_TILE_MIN_PX or vh < _VP_TILE_MIN_PX:
        ds = _read_dockspace_rect()
        if ds is not None:
            dx, dy, dw, dh = ds
            if dw >= _VP_TILE_MIN_PX and dh >= _VP_TILE_MIN_PX:
                vx, vy, vw, vh = int(dx), int(dy), int(dw), int(dh)
                bottom = _split_tile_bbox_top_cap_y(ext, vy, vh)
                vh = max(_VP_TILE_MIN_PX, bottom - int(vy))
    try:
        ext._tbs_split_saved_viewport_rect = (int(vx), int(vy), int(vw), int(vh))
    except Exception:
        pass
    return (int(vx), int(vy), int(vw), int(vh))


def _get_split_tile_bbox(ext: Any) -> Tuple[int, int, int, int]:
    """분할 빌드 중 저장된 Viewport bbox (없으면 1회 캡처)."""
    r = getattr(ext, "_tbs_split_saved_viewport_rect", None)
    if r is not None and len(r) == 4:
        try:
            vx, vy, vw, vh = (int(r[0]), int(r[1]), int(r[2]), int(r[3]))
            if vw >= _VP_TILE_MIN_PX and vh >= _VP_TILE_MIN_PX:
                return (vx, vy, vw, vh)
        except Exception:
            pass
    return _capture_split_tile_bbox(ext)


def _read_dockspace_rect() -> Optional[Tuple[int, int, int, int]]:
    """중앙 Dock 골격 사각형(있으면). 메뉴 숨김 후 가용 영역 추정에 사용."""
    try:
        ds = ui.Workspace.get_window("DockSpace")
        if ds is None:
            return None
        return (
            int(getattr(ds, "position_x", 0) or 0),
            int(getattr(ds, "position_y", 0) or 0),
            int(getattr(ds, "width", 800) or 800),
            int(getattr(ds, "height", 600) or 600),
        )
    except Exception:
        return None


def _read_split_cluster_union_rect(n: int) -> Tuple[int, int, int, int]:
    """
    분할 타일(``Viewport`` + ``TBS_SimSplit_*``) 화면 좌표의 합집합.

    ``Viewport`` 만 읽으면 이미 줄어든 첫 타일 크기만 잡혀, 메뉴 숨김마다 절반씩 줄어드는
    피드백이 생긴다.
    """
    if n <= 1:
        r = _read_dockspace_rect()
        if r is not None:
            return r
        return _read_viewport_rect()

    names = ["Viewport"] + [_split_window_name(ti) for ti in range(1, n)]
    xs: List[int] = []
    ys: List[int] = []
    xe: List[int] = []
    ye: List[int] = []
    for nm in names:
        try:
            w = ui.Workspace.get_window(nm)
        except Exception:
            w = None
        if w is None:
            continue
        try:
            x = int(getattr(w, "position_x", 0) or 0)
            y = int(getattr(w, "position_y", 0) or 0)
            ww = int(getattr(w, "width", 0) or 0)
            hh = int(getattr(w, "height", 0) or 0)
        except Exception:
            continue
        if ww < 8 or hh < 8:
            continue
        xs.append(x)
        ys.append(y)
        xe.append(x + ww)
        ye.append(y + hh)
    if len(xs) < 2:
        r = _read_dockspace_rect()
        if r is not None:
            return r
        return _read_viewport_rect()
    vx = min(xs)
    vy = min(ys)
    vw = max(1, max(xe) - vx)
    vh = max(1, max(ye) - vy)
    return (vx, vy, vw, vh)


def _read_split_layout_bbox_for_chrome(n: int, menus_hidden: bool) -> Tuple[int, int, int, int]:
    """
    2~4분할 격자 재배치에 쓸 바운딩 사각형.

    메뉴·패널 숨김 직후에는 타일별 ``width``/``height`` 가 아직 이전 레이아웃일 수 있어
    합집합만 쓰면 확장된 영역을 못 쓴다. ``DockSpace``(없으면 메인 창) 전체를 우선한다.
    """
    if menus_hidden and n > 1:
        r = _read_dockspace_rect()
        if r is not None:
            return (int(r[0]), int(r[1]), int(r[2]), int(r[3]))
        try:
            mw = int(ui.Workspace.get_main_window_width() or 1280)
            mh = int(ui.Workspace.get_main_window_height() or 720)
            return (0, 0, max(400, mw), max(300, mh))
        except Exception:
            pass
    return _read_split_cluster_union_rect(n)


def _refresh_docked_multi_split_after_chrome(ext: Any, n: int) -> None:
    """
    Dock 기반 2~4분할: 보조 창 ``dock_in`` 만 다시 적용한다.

    ``Viewport.dock_in(DockSpace, 1.0)`` 은 Console·Content 를 밀거나 Viewport 가
    전체를 덮는 부작용이 있어 **메인 Viewport 는 건드리지 않는다**.
    """
    if n <= 1:
        return
    _reapply_split_dock_in_geometry(ext)


def _apply_split_geometry_sync(
    win_names: List[str],
    fracs: List[Tuple[float, float, float, float]],
    vx: int,
    vy: int,
    vw: int,
    vh: int,
    *,
    max_bottom_y: Optional[int] = None,
    ext: Any = None,
    preserve_main_viewport: bool = False,
) -> None:
    """분할 타일 창 크기·위치를 한 번에 적용(토큰 검사 없음). Dock/메뉴 변경 뒤 재맞춤용."""
    bottom_cap = int(max_bottom_y) if max_bottom_y is not None else int(vy) + int(vh)
    n_tiles = min(len(win_names), len(fracs))
    if n_tiles == 2 and int(vw) >= _VP_TILE_MIN_PX * 2:
        half_w = max(_VP_TILE_MIN_PX, int(vw) // 2)
        th = max(_VP_TILE_MIN_PX, int(vh))
        py = int(vy)
        if py + th > bottom_cap:
            th = max(_VP_TILE_MIN_PX, bottom_cap - py)
        placements = [
            (win_names[0], int(vx), py, half_w, th),
            (win_names[1], int(vx) + half_w, py, int(vw) - half_w, th),
        ]
        for name, px, py2, tw, th2 in placements:
            if preserve_main_viewport and str(name) == "Viewport":
                continue
            try:
                win = ui.Workspace.get_window(str(name))
                if win is not None:
                    _workspace_show_named_window(str(name), True)
                    if _workspace_set_rect_if_changed(win, str(name), int(px), int(py2), int(tw), int(th2)):
                        _sync_viewport_resolution_from_workspace_window(str(name))
            except Exception:
                pass
        return
    for i, (x0, y0, x1, y1) in enumerate(fracs):
        if i >= len(win_names):
            break
        name = win_names[i]
        if preserve_main_viewport and str(name) == "Viewport":
            continue
        tw = max(_VP_TILE_MIN_PX, int(vw * (x1 - x0)))
        th = max(_VP_TILE_MIN_PX, int(vh * (y1 - y0)))
        px = vx + int(vw * x0)
        py = vy + int(vh * y0)
        if py + th > bottom_cap:
            th = max(_VP_TILE_MIN_PX, bottom_cap - py)
        try:
            win = ui.Workspace.get_window(name)
            if win is not None:
                _workspace_show_named_window(name, True)
                if _workspace_set_rect_if_changed(win, str(name), int(px), int(py), int(tw), int(th)):
                    _sync_viewport_resolution_from_workspace_window(str(name))
        except Exception:
            pass


def _menubar_reserved_height_px() -> int:
    """메뉴바가 보일 때 위쪽에 예약되는 높이(픽셀). DockSpace 전체를 쓸 때 겹침을 줄인다."""
    try:
        from omni.kit.mainwindow import get_main_window

        mw = get_main_window()
        if mw is None:
            return 0
        mb = mw.get_main_menu_bar()
        if mb is None:
            return 0
        if not bool(getattr(mb, "visible", True)):
            return 0
        h = getattr(mb, "height", None)
        if h is not None and int(h) > 0:
            return int(h)
    except Exception:
        pass
    return 32


def _relayout_single_viewport_fill_available(ext: Any, menus_hidden: bool) -> None:
    """
    1분할 — Viewport/Console/Content **절대 좌표는 건드리지 않는다**.

    ``dock_in(DockSpace)``·DockSpace 크기 맞춤은 1화면 복귀 시 Console/Content 가
    Viewport 뒤로 가려지는 주된 원인이었다.
    """
    try:
        set_viewport_fill_frame_for_split_count(1, bool(menus_hidden))
    except Exception:
        pass
    _sync_viewport_resolution_from_workspace_window("Viewport")
    if not menus_hidden:
        _bring_kit_chrome_visible(ext)


def relayout_split_views_to_viewport(ext: Any, _menus_hidden: bool = False) -> None:
    """
    메뉴바·패널 표시가 바뀐 뒤 Dock 이 안정된 뒤 3D 뷰 영역을 다시 맞춘다.
    """
    if startup_dual_layout_settled(ext) and startup_dual_orchestration_active(ext):
        try:
            sn = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 2) or 2))
        except Exception:
            sn = 2
        if sn > 1:
            try:
                set_viewport_fill_frame_for_split_count(sn, True)
            except Exception:
                pass
            for ti in range(sn):
                nm = "Viewport" if ti == 0 else _split_window_name(ti)
                _sync_viewport_resolution_from_workspace_window(nm)
        return

    try:
        n = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 1) or 1))
    except Exception:
        n = 1

    mh = bool(_menus_hidden)
    if mh:
        set_viewport_fill_frame_for_split_count(n, True)

    if n <= 1:
        _relayout_single_viewport_fill_available(ext, mh)
        return

    try:
        if bool(getattr(ext, "_tbs_split_used_dock_layout", False)):
            _reapply_split_dock_in_geometry(ext)
        else:
            asyncio.ensure_future(
                _enforce_equal_split_grid_async(
                    ext,
                    int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0),
                    n,
                    preserve_main_viewport=True,
                )
            )
    except Exception:
        _apply_viewport_clipped_split_grid(n, ext=ext, preserve_main_viewport=True)
    if not mh:
        _bring_kit_chrome_visible(ext)
    if not mh:
        for ti in range(0, n):
            nm = "Viewport" if ti == 0 else _split_window_name(ti)
            _sync_viewport_resolution_from_workspace_window(nm)
    return


def schedule_split_layout_refresh_for_chrome_change(ext: Any, menus_hidden: bool) -> None:
    """Kit 크롬(메뉴·패널) 표시 변경 직후 Dock 이 안정된 뒤 뷰 레이아웃을 다시 맞춘다."""
    if startup_dual_orchestration_active(ext):
        try:
            ext._tbs_startup_pending_chrome_relayout = bool(menus_hidden)
        except Exception:
            pass
        return

    async def _go() -> None:
        # Dock/Workspace 가 크롬 숨김·복원 직후에야 실제 사각형을 반영하는 경우가 있어
        # 숨김 ON/OFF 모두 첫 패스 + 추가 대기 후 두 번째 패스로 맞춘다.
        nwait_first = 22
        for _ in range(nwait_first):
            try:
                await kit_app.get_app().next_update_async()
            except Exception:
                return
        try:
            relayout_split_views_to_viewport(ext, _menus_hidden=menus_hidden)
        except Exception:
            pass
        for _ in range(14):
            try:
                await kit_app.get_app().next_update_async()
            except Exception:
                return
        try:
            relayout_split_views_to_viewport(ext, _menus_hidden=menus_hidden)
        except Exception:
            pass
        try:
            from .kit_chrome_visibility import apply_viewport_dock_tab_bars_hidden, is_streaming_deployment

            if is_streaming_deployment():
                apply_viewport_dock_tab_bars_hidden()
        except Exception:
            pass

    try:
        from omni.kit.async_engine import run_coroutine

        run_coroutine(_go())
    except Exception:
        try:
            asyncio.ensure_future(_go())
        except Exception:
            try:
                relayout_split_views_to_viewport(ext, _menus_hidden=menus_hidden)
            except Exception:
                pass


async def _finalize_split_window_geometry_sequential(
    ext: Any,
    token: int,
    win_names: List[str],
    fracs: List[Tuple[float, float, float, float]],
    vx: int,
    vy: int,
    vw: int,
    vh: int,
    *,
    max_bottom_y: Optional[int] = None,
    preserve_main_viewport: bool = False,
) -> None:
    """
    타일 창 크기/위치를 한 코루틴 안에서만 순차 적용한다.
    메인 Viewport 를 먼저 줄인 뒤 보조 뷰를 만들고, 또 다른 태스크가 곧바로 다시 움직이면
    Hydra ``ViewportTexture_0`` / render graph 오류로 이어지는 경우가 있어 이 경로로 통일한다.
    """
    for _ in range(3):
        await kit_app.get_app().next_update_async()
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return

    for i, frac in enumerate(fracs):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        if i >= len(win_names):
            break
        name = win_names[i]
        if preserve_main_viewport and str(name) == "Viewport":
            continue
        _apply_split_geometry_sync(
            [name],
            [frac],
            vx,
            vy,
            vw,
            vh,
            max_bottom_y=max_bottom_y,
            preserve_main_viewport=preserve_main_viewport,
        )
        await kit_app.get_app().next_update_async()


async def _assign_split_cameras_after_layout(
    ext: Any,
    token: int,
    win_names: List[str],
    cam_paths: List[Optional[str]],
) -> None:
    """보조 타일 카메라 — Widget 분할은 타일별 독립 카메라, Dock 은 기존 경로."""
    try:
        from .sim_multi_view_widget import assign_widget_split_cameras, is_split_widget_layout_active

        if is_split_widget_layout_active(ext):
            await assign_widget_split_cameras(ext, token, win_names)
            return
    except Exception:
        pass
    main_api = _split_viewport_api("Viewport")
    main_cam = ""
    if main_api is not None:
        try:
            main_cam = str(getattr(main_api, "camera_path", "") or "").strip()
        except Exception:
            main_cam = ""
    if not main_cam:
        main_cam = "/OmniverseKit_Persp"

    for _ in range(8):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        missing = False
        for i, name in enumerate(win_names):
            if i == 0 or str(name) == "Viewport":
                continue
            want = main_cam
            if i < len(cam_paths) and cam_paths[i]:
                want = str(cam_paths[i])
            api = _split_viewport_api(str(name))
            if api is None:
                missing = True
                continue
            try:
                api.camera_path = want
            except Exception:
                missing = True
        if not missing:
            return
        await kit_app.get_app().next_update_async()


def notify_sim_split_ui_sync(ext: Any) -> None:
    """제어창 분할 체크박스를 ``ext._sim_viewport_split_count`` 와 맞춘다(등록된 콜백이 있을 때만)."""
    fn = getattr(ext, "_sync_sim_multi_split_ui_fn", None)
    if callable(fn):
        try:
            fn()
        except Exception:
            pass
    fn2 = getattr(ext, "_sync_sim_per_screen_rows_fn", None)
    if callable(fn2):
        try:
            fn2()
        except Exception:
            pass
    fn3 = getattr(ext, "_rebuild_sim_monitor_split_ui_fn", None)
    if callable(fn3):
        try:
            fn3()
        except Exception:
            pass
    if not _widget_split_startup_in_progress(ext):
        schedule_viewport_snapshot_hud_refresh(ext)


def _widget_split_startup_in_progress(ext: Any) -> bool:
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active

        if not is_split_widget_layout_active(ext):
            return False
    except Exception:
        return False
    return startup_dual_orchestration_active(ext) or not bool(
        getattr(ext, "_tbs_widget_split_ready", False)
    )


def _rollback_split_attempt(ext: Any, entries: List[Dict[str, Any]], ctx_names: List[str]) -> None:
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active, teardown_split_widget_host

        if is_split_widget_layout_active(ext):
            teardown_split_widget_host(ext)
    except Exception:
        pass
    _restore_kit_full_layout_after_split(ext)
    for ent in entries:
        if ent.get("kind") == "main_viewport":
            continue
        if ent.get("kit_vp") is not None:
            _destroy_kit_viewport(ent.get("kit_vp"))
        else:
            _destroy_viewport_window(ent.get("window"))
    _release_usd_context_names(ctx_names)
    _unlink_split_session_files(ext)
    _workspace_show_named_window("Viewport", True)
    try:
        ext._sim_viewport_split_count = 1
    except Exception:
        pass
    notify_sim_split_ui_sync(ext)


def _split_layout_already_active(ext: Any, n: int) -> bool:
    """동일 분할 수·보조 컨텍스트·USD 지문·런타임이 살아 있으면 재빌드를 생략한다."""
    try:
        sn = channel_count_for_split(int(n))
        cur = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 1) or 1))
        if cur != sn or sn <= 1:
            return False
        built_key = str(getattr(ext, "_tbs_split_layout_usd_key", "") or "").strip()
        current_key = _split_layout_usd_key(ext)
        if not built_key or not current_key or built_key != current_key:
            return False
        ctx = list(getattr(ext, "_sim_multi_context_names", None) or [])
        entries = list(getattr(ext, "_sim_multi_viewport_entries", None) or [])
        if len(ctx) != sn - 1:
            return False
        if len(entries) < sn:
            return False
        try:
            from .tbs_split_composed_loader import get_split_runtime_for_screen
        except Exception:
            get_split_runtime_for_screen = None  # type: ignore
        for ti in range(1, sn):
            wname = _split_window_name(ti)
            try:
                if ui.Workspace.get_window(wname) is None:
                    return False
            except Exception:
                return False
            if not _aux_viewport_api_healthy(wname):
                return False
            if get_split_runtime_for_screen is not None:
                if get_split_runtime_for_screen(ext, ti + 1) is None:
                    return False
        return True
    except Exception:
        return False


async def _hydrate_aux_split_tile_background(
    ext: Any,
    token: int,
    ctx_name: str,
    screen_1based: int,
    *,
    fast_visual: bool,
    settle_frames: int = 3,
) -> None:
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return
    try:
        from .tbs_split_composed_loader import hydrate_split_screen_composed_stage_async

        await hydrate_split_screen_composed_stage_async(
            ext,
            ctx_name,
            screen_1based,
            settle_frames=max(1, int(settle_frames)),
            fast_visual=fast_visual,
        )
    except Exception as exc:
        try:
            print(
                f"[TBS multi-sim] 보조 합성 USD hydrate(백그라운드) 실패 screen={screen_1based}: {exc}",
                flush=True,
            )
        except Exception:
            pass


async def _finalize_aux_split_tile_after_open(
    ext: Any,
    token: int,
    ctx_name: str,
    screen_1based: int,
    *,
    fast_visual: bool,
    hydrate_settle_frames: int = 3,
) -> None:
    """Dock 이후 백그라운드: runtime hydrate + 화면별 EP2/EP3 레이아웃."""
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return
    await _hydrate_aux_split_tile_background(
        ext,
        token,
        ctx_name,
        screen_1based,
        fast_visual=fast_visual,
        settle_frames=hydrate_settle_frames,
    )
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return
    try:
        from .tbs_ep_port_visibility import apply_ep_port_layout_for_context

        apply_ep_port_layout_for_context(
            ext,
            ctx_name,
            int(screen_1based),
            reason="split_finalize",
        )
    except Exception as exc:
        try:
            print(
                f"[TBS multi-sim] 보조 EP 레이아웃 적용 실패 screen={screen_1based}: {exc}",
                flush=True,
            )
        except Exception:
            pass
    try:
        from .tbs_split_composed_loader import (
            _activate_aux_split_display,
            get_split_runtime_for_screen,
        )

        rt = get_split_runtime_for_screen(ext, int(screen_1based))
        main_rt = get_split_runtime_for_screen(ext, 1)
        if rt is not None:
            si = max(2, int(screen_1based))
            _activate_aux_split_display(
                rt.evaluator,
                main_rt.evaluator if main_rt is not None else None,
                aux_win_name=_split_window_name(si - 1),
                ext=ext,
            )
    except Exception:
        pass
    try:
        si = max(2, int(screen_1based))
        wn = _split_window_name(si - 1)
        try:
            from .sim_multi_view_widget import is_split_widget_layout_active, request_aux_widget_materialize

            if is_split_widget_layout_active(ext):
                sn = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 1) or 1))
                request_aux_widget_materialize(ext, token, sn)
            else:
                _ensure_viewport_camera_navigation_enabled(wn)
                _wire_split_viewport_click_focus(wn)
        except Exception:
            _ensure_viewport_camera_navigation_enabled(wn)
            _wire_split_viewport_click_focus(wn)
    except Exception:
        pass
    try:
        sn = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 1) or 1))
        if sn > 1:
            try:
                from .sim_multi_view_widget import is_split_widget_layout_active

                if not is_split_widget_layout_active(ext):
                    reapply_split_layout_sync(ext, sn)
                    asyncio.ensure_future(reapply_split_layout_after_hydrate_async(ext, token, sn))
            except Exception:
                reapply_split_layout_sync(ext, sn)
                asyncio.ensure_future(reapply_split_layout_after_hydrate_async(ext, token, sn))
    except Exception:
        pass


def _split_aux_layout_healthy(ext: Any, split_n: int) -> bool:
    """보조 타일·컨텍스트가 요청 분할 수와 일치하는지(부분 리사이즈 가능 여부)."""
    try:
        sn = channel_count_for_split(int(split_n))
    except Exception:
        return False
    if sn <= 1:
        return False
    try:
        from .sim_multi_view_widget import (
            is_split_widget_layout_active,
            sim_viewport_split_widget_enabled,
            split_widget_layout_healthy,
        )

        if is_split_widget_layout_active(ext):
            return split_widget_layout_healthy(ext, sn)
        if sim_viewport_split_widget_enabled():
            ctx = list(getattr(ext, "_sim_multi_context_names", None) or [])
            if len(ctx) != sn - 1:
                return False
            entries = list(getattr(ext, "_sim_multi_viewport_entries", None) or [])
            if len(entries) < sn:
                return False
            for ti in range(1, sn):
                wname = _split_window_name(ti)
                found = False
                for ent in entries:
                    if str(ent.get("win_name") or "") == wname and ent.get("kind") == "widget_aux":
                        found = True
                        break
                if not found:
                    return False
            return True
    except Exception:
        pass
    ctx = list(getattr(ext, "_sim_multi_context_names", None) or [])
    if len(ctx) != sn - 1:
        return False
    entries = list(getattr(ext, "_sim_multi_viewport_entries", None) or [])
    if len(entries) < sn:
        return False
    for ti in range(1, sn):
        wname = _split_window_name(ti)
        try:
            if ui.Workspace.get_window(wname) is None:
                return False
        except Exception:
            return False
        if not _aux_viewport_api_healthy(wname):
            return False
    return True


def _destroy_aux_split_tile(ext: Any, ti: int, entries: List[Dict[str, Any]], ctx_names: List[str]) -> None:
    """보조 타일 하나(``ti``=1..)만 제거 — 뷰포트·USD 컨텍스트·런타임."""
    wname = _split_window_name(ti)
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active, teardown_split_widget_host

        if is_split_widget_layout_active(ext):
            teardown_split_widget_host(ext)
    except Exception:
        pass
    _undock_workspace_window(wname)
    _clear_split_viewport_input_hook(wname)
    ctx_name = f"morph_tbs_split_aux_{ti}"
    for ent in list(entries):
        try:
            ci = int(ent.get("cell_index", -999))
        except Exception:
            ci = -999
        if ent.get("win_name") != wname and ci != ti:
            continue
        if ent.get("kind") == "widget_aux":
            try:
                entries.remove(ent)
            except ValueError:
                pass
            continue
        if ent.get("kit_vp") is not None:
            _destroy_kit_viewport(ent.get("kit_vp"))
        else:
            _destroy_viewport_window(ent.get("window"))
        try:
            entries.remove(ent)
        except ValueError:
            pass
    _workspace_show_named_window(wname, False)
    _release_usd_context_names([ctx_name])
    if ctx_name in ctx_names:
        ctx_names.remove(ctx_name)
    try:
        from .tbs_split_composed_loader import release_split_runtime_for_screen

        release_split_runtime_for_screen(ext, ti + 1)
    except Exception:
        pass


def _spawn_aux_tile_hydrate(
    ext: Any,
    token: int,
    ctx_name: str,
    screen_1based: int,
    *,
    fast_visual: bool = True,
    stagger_frames: int = 0,
) -> None:
    """합성 인스턴스 hydrate — Dock·뷰포트 안정 후 백그라운드 실행."""

    async def _go() -> None:
        delay = max(0, int(stagger_frames))
        for _ in range(delay):
            if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                return
            await kit_app.get_app().next_update_async()
        await _finalize_aux_split_tile_after_open(
            ext,
            token,
            ctx_name,
            screen_1based,
            fast_visual=fast_visual,
        )

    try:
        asyncio.ensure_future(_go())
    except Exception:
        pass


def _collect_pending_aux_tile_hydrates(
    ext: Any, n: int
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, int]]]:
    """hydrate_pending 보조 타일 목록을 수집하고 플래그를 내린다."""
    try:
        sn = channel_count_for_split(int(n))
    except Exception:
        return [], []
    if sn <= 1:
        return list(getattr(ext, "_sim_multi_viewport_entries", []) or []), []

    entries = list(getattr(ext, "_sim_multi_viewport_entries", []) or [])
    pending: List[Tuple[str, int]] = []
    for ent in entries:
        if ent.get("stage_load_pending"):
            continue
        if not ent.get("hydrate_pending"):
            continue
        try:
            ci = int(ent.get("cell_index", -1))
        except Exception:
            ci = -1
        if ci < 1:
            continue
        ctx_name = str(ent.get("context_name") or f"morph_tbs_split_aux_{ci}").strip()
        if ctx_name:
            pending.append((ctx_name, ci + 1))
            ent["hydrate_pending"] = False
    pending.sort(key=lambda x: x[1])
    return entries, pending


async def _await_pending_aux_tile_hydrates(
    ext: Any,
    n: int,
    token: int,
    *,
    fast_visual: bool,
    hydrate_settle_frames: int = 3,
    on_all_done: Optional[Callable[[], None]] = None,
) -> None:
    """보조 타일 hydrate 를 순차 await (startup GPU 직렬화)."""
    entries, pending = _collect_pending_aux_tile_hydrates(ext, n)
    try:
        ext._sim_multi_viewport_entries = entries
    except Exception:
        pass
    if not pending:
        if callable(on_all_done):
            try:
                on_all_done()
            except Exception:
                pass
        return

    for idx, (ctx_name, screen_i) in enumerate(pending):
        for _ in range(max(0, int(idx) * 2)):
            if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                return
            await kit_app.get_app().next_update_async()
        try:
            await _finalize_aux_split_tile_after_open(
                ext,
                token,
                ctx_name,
                screen_i,
                fast_visual=fast_visual,
                hydrate_settle_frames=hydrate_settle_frames,
            )
        except Exception:
            pass

    if callable(on_all_done):
        try:
            on_all_done()
        except Exception:
            pass


def _spawn_pending_aux_tile_hydrates(
    ext: Any,
    n: int,
    token: int,
    *,
    on_all_done: Optional[Callable[[], None]] = None,
) -> None:
    """Dock 완료 후 hydrate_pending 보조 타일에 인스턴스 hydrate 를 순차 시작."""
    entries, pending = _collect_pending_aux_tile_hydrates(ext, n)
    try:
        ext._sim_multi_viewport_entries = entries
    except Exception:
        pass
    if not pending:
        if callable(on_all_done):
            try:
                on_all_done()
            except Exception:
                pass
        return

    remaining = [len(pending)]

    def _one_done() -> None:
        remaining[0] -= 1
        if remaining[0] <= 0 and callable(on_all_done):
            try:
                on_all_done()
            except Exception:
                pass

    for idx, (ctx_name, screen_i) in enumerate(pending):

        async def _go(cn: str = ctx_name, si: int = screen_i, delay: int = idx) -> None:
            for _ in range(max(0, int(delay) * 2)):
                if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                    return
                await kit_app.get_app().next_update_async()
            try:
                await _finalize_aux_split_tile_after_open(
                    ext,
                    token,
                    cn,
                    si,
                    fast_visual=True,
                )
            finally:
                _one_done()

        try:
            asyncio.ensure_future(_go())
        except Exception:
            _one_done()


async def _apply_aux_bg_load_layout_finish(
    ext: Any,
    n: int,
    token: int,
    entries: List[Dict[str, Any]],
    *,
    show_all_aux_tiles: bool = False,
    skip_dock_reapply: bool = False,
) -> None:
    """보조 스테이지·hydrate 완료 후 카메라·Dock·창 표시를 한 번만 적용."""
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return
    win_names = ["Viewport"] + [_split_window_name(ti) for ti in range(1, n)]
    cam_paths: List[Optional[str]] = [None] + ["/OmniverseKit_Persp"] * (n - 1)
    if not skip_dock_reapply:
        await _assign_split_cameras_after_layout(ext, token, win_names, cam_paths)
        if bool(getattr(ext, "_tbs_split_used_dock_layout", False)):
            _reapply_split_dock_in_geometry(ext)
    else:
        for ti in range(1, n):
            nm = _split_window_name(ti)
            _sync_viewport_resolution_from_workspace_window(nm)
    if startup_dual_layout_settled(ext) and skip_dock_reapply:
        try:
            ext._sim_multi_viewport_entries = entries
        except Exception:
            pass
        return
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active, _destroy_all_aux_workspace_windows

        if is_split_widget_layout_active(ext):
            _destroy_all_aux_workspace_windows(ext)
            for ent in entries:
                ent["aux_hidden_until_load"] = False
            try:
                ext._sim_multi_viewport_entries = entries
            except Exception:
                pass
            return
    except Exception:
        pass
    for ent in entries:
        wn = str(ent.get("win_name") or "").strip()
        if not wn:
            continue
        if show_all_aux_tiles:
            try:
                ci = int(ent.get("cell_index", -1))
            except Exception:
                ci = -1
            if ci < 1:
                continue
        elif not ent.get("aux_hidden_until_load"):
            continue
        _apply_aux_window_chrome_flags(wn)
        _workspace_show_named_window(wn, True)
        ent["aux_hidden_until_load"] = False
        _sync_viewport_resolution_from_workspace_window(wn)
    try:
        ext._sim_multi_viewport_entries = entries
    except Exception:
        pass


def _show_split_layout_shell_windows(ext: Any, n: int) -> None:
    """layout-first: Dock 50:50 직후 메인·보조 Viewport 를 **한 번만** 분할 위치에 표시(USD 전)."""
    try:
        sn = channel_count_for_split(int(n))
    except Exception:
        sn = 1
    if sn <= 1:
        return
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active, sync_split_widget_fill_frame

        if is_split_widget_layout_active(ext):
            sync_split_widget_fill_frame(ext, sn)
            for ent in list(getattr(ext, "_sim_multi_viewport_entries", []) or []):
                ent["aux_hidden_until_layout"] = False
            return
    except Exception:
        pass
    try:
        set_viewport_fill_frame_for_split_count(sn, True)
    except Exception:
        pass
    for ti in range(sn):
        wn = "Viewport" if ti == 0 else _split_window_name(ti)
        if ti >= 1:
            _apply_aux_window_chrome_flags(wn)
        _workspace_show_named_window(wn, True)
        _sync_viewport_resolution_from_workspace_window(wn)
        if ti == 0:
            _ensure_viewport_camera_navigation_enabled(wn)
        else:
            _schedule_split_viewport_input_ready(wn, frames=8)
        if ti >= 1:
            for ent in list(getattr(ext, "_sim_multi_viewport_entries", []) or []):
                if str(ent.get("win_name") or "") == wn:
                    ent["aux_hidden_until_layout"] = False
                    break


async def wake_main_viewport_after_master_open(ext: Any, n: int = 0) -> None:
    """Master open 직후 메인 Hydra 를 깨운다(격자·Dock 재적용 없음)."""
    try:
        sn = channel_count_for_split(int(n or getattr(ext, "_sim_viewport_split_count", 2) or 2))
    except Exception:
        sn = 2
    try:
        from .sim_multi_view_widget import connect_widget_tile_main_stage, is_split_widget_layout_active

        if is_split_widget_layout_active(ext):
            tok = int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0)
            await connect_widget_tile_main_stage(ext, tok)
    except Exception:
        pass
    for _ in range(6):
        await kit_app.get_app().next_update_async()
    try:
        set_viewport_fill_frame_for_split_count(sn, True)
    except Exception:
        pass
    if not startup_dual_layout_settled(ext):
        _workspace_show_named_window("Viewport", True)
    _sync_viewport_resolution_from_workspace_window("Viewport")
    _ensure_viewport_camera_navigation_enabled("Viewport")


async def refresh_main_viewport_after_master_open(ext: Any, n: int = 0) -> None:
    """호환 래퍼 — startup 중에는 relayout 없이 wake 만."""
    await wake_main_viewport_after_master_open(ext, n)


async def _clone_aux_paths_parallel(
    usd_path: str,
    ext: Any,
    token: int,
    tile_indices: List[int],
    *,
    use_composed: bool,
    composed_shared: Optional[str],
) -> Optional[Dict[int, Tuple[str, bool]]]:
    """보조 타일 USD 경로를 **병렬** copy_async 로 준비한다."""
    out: Dict[int, Tuple[str, bool]] = {}

    async def _one(ti: int) -> Tuple[int, Optional[str], bool, str]:
        path, composed, err = await _resolve_aux_tile_usd_path(
            usd_path,
            ext,
            token,
            ti,
            use_composed=use_composed,
            composed_shared=composed_shared,
        )
        return ti, path, composed, err

    try:
        results = await asyncio.gather(*[_one(ti) for ti in tile_indices])
    except Exception:
        return None
    for ti, path, composed, err in results:
        if not path:
            try:
                print(f"[TBS multi-sim] 보조 타일 {ti} USD 병렬 복제 실패: {err}", flush=True)
            except Exception:
                pass
            return None
        out[int(ti)] = (str(path), bool(composed))
    return out


async def _refresh_aux_split_stages_async(ext: Any, n: int, token: int, usd_path: str) -> None:
    """
    Master 재로드 후: 기존 Viewport/Workspace 창은 유지하고 보조 **스테이지만** 교체+hydrate.

    전체 teardown·Flatten 없이 수백 ms~1초 수준으로 끝나도록 한다.
    """
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return
    try:
        sn = channel_count_for_split(int(n))
    except Exception:
        sn = 1
    if sn <= 1:
        return

    entries: List[Dict[str, Any]] = list(getattr(ext, "_sim_multi_viewport_entries", []) or [])
    ctx_names: List[str] = list(getattr(ext, "_sim_multi_context_names", []) or [])
    if len(ctx_names) < sn - 1 or len(entries) < sn:
        try:
            print("[TBS multi-sim] 분할 빠른 갱신 불가 → 전체 재빌드", flush=True)
        except Exception:
            pass
        teardown_sim_multi_viewports(ext)
        await _post_teardown_rebuild_split(ext, sn, token, usd_path, prev_n=1)
        return

    try:
        print(f"[TBS multi-sim] 분할 빠른 갱신: reopen+hydrate (n={sn})", flush=True)
    except Exception:
        pass

    try:
        from .tbs_split_composed_loader import resolve_split_aux_usd_path, split_dual_usd_paths_enabled

        dual_path = split_dual_usd_paths_enabled(ext)
    except Exception:
        dual_path = False
        resolve_split_aux_usd_path = None  # type: ignore

    use_composed = False if dual_path else _use_split_composed_export(ext)
    composed_shared: Optional[str] = None
    if use_composed:
        try:
            from .tbs_split_composed_loader import get_or_export_main_composed_stage

            composed_shared = get_or_export_main_composed_stage(ext, token, 0)
        except Exception:
            composed_shared = None
        if not composed_shared:
            use_composed = False

    clone_map = await _clone_aux_paths_parallel(
        usd_path,
        ext,
        token,
        list(range(1, sn)),
        use_composed=use_composed,
        composed_shared=composed_shared,
    )
    if clone_map is None:
        teardown_sim_multi_viewports(ext)
        await _post_teardown_rebuild_split(ext, sn, token, usd_path, prev_n=1)
        return

    for ti in range(1, sn):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        ctx_name = f"morph_tbs_split_aux_{ti}"
        ctx = _named_usd_context(ctx_name)
        if ctx is None:
            continue
        aux_usd, _composed = clone_map[ti]
        try:
            from .tbs_split_composed_loader import release_split_runtime_for_screen

            release_split_runtime_for_screen(ext, ti + 1)
        except Exception:
            pass
        await _prepare_aux_context_for_stage_swap(ctx, frame_wait=4)
        ok_open, _err = await _open_aux_stage_with_unique_session(
            ctx,
            aux_usd,
            ext,
            token,
            ti,
            skip_file_clone=True,
        )
        if not ok_open:
            continue
        for ent in entries:
            try:
                if int(ent.get("cell_index", -1)) == ti:
                    ent["hydrate_pending"] = True
                    ent["context_name"] = ctx_name
            except Exception:
                pass

    try:
        ext._sim_multi_viewport_entries = entries
    except Exception:
        pass

    for _ in range(2):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        await kit_app.get_app().next_update_async()

    def _nav_after_hydrate() -> None:
        try:
            from .sim_multi_view_widget import is_split_widget_layout_active

            if is_split_widget_layout_active(ext):
                return
        except Exception:
            pass
        try:
            reapply_split_layout_sync(ext, sn)
            asyncio.ensure_future(reapply_split_layout_after_hydrate_async(ext, token, sn))
        except Exception:
            pass
        _apply_split_navigation_to_aux(ext, sn, token, hold_ticks=96)

    _spawn_pending_aux_tile_hydrates(ext, sn, token, on_all_done=_nav_after_hydrate)
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active

        if not is_split_widget_layout_active(ext):
            _apply_split_navigation_to_aux(ext, sn, token, hold_ticks=48)
    except Exception:
        _apply_split_navigation_to_aux(ext, sn, token, hold_ticks=48)

    try:
        ext._tbs_split_layout_usd_key = _split_layout_usd_key(ext)
    except Exception:
        pass


async def _resolve_aux_tile_usd_path(
    usd_path: str,
    ext: Any,
    token: int,
    ti: int,
    *,
    use_composed: bool,
    composed_shared: Optional[str],
) -> Tuple[Optional[str], bool, str]:
    """
    타일별 **독립 USD 파일** 경로를 만든다. 실패 시 (None, False, err).
    composed_shared 를 타일 간·Hydra 간 공유하지 않는다(clone 필수).
    """
    try:
        from .tbs_split_composed_loader import resolve_split_aux_usd_path

        aux_pre = resolve_split_aux_usd_path(ext, ti)
        if aux_pre:
            try:
                print(
                    f"[TBS multi-sim] 보조 타일 {ti}: dual-path USD (복제 생략) path={aux_pre}",
                    flush=True,
                )
            except Exception:
                pass
            return aux_pre, False, ""
    except Exception:
        pass
    if use_composed and composed_shared:
        tile_copy, cerr = await _clone_usd_for_aux_tile(composed_shared, ext, token, ti)
        if not tile_copy:
            return None, True, str(cerr or "composed 스냅샷 복제 실패")
        _register_session_layer_path(ext, tile_copy)
        return tile_copy, True, ""

    clone_path, cerr = await _clone_usd_for_aux_tile(usd_path, ext, token, ti)
    if not clone_path:
        return None, False, str(cerr or "Master USD 복제 실패")
    _register_session_layer_path(ext, clone_path)
    return clone_path, False, ""


async def _materialize_dock_windows_for_widget_aux_entries(
    ext: Any, token: int, n: int
) -> bool:
    """Widget 분할 실패 시 ``widget_aux`` entry 를 Dock 용 Workspace 뷰포트 창으로 승격."""
    entries = list(getattr(ext, "_sim_multi_viewport_entries", []) or [])
    pending = [e for e in entries if e.get("kind") == "widget_aux"]
    if not pending:
        return True
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return False
    fracs = _split_cell_layout_fracs(n)
    vx, vy, vw, vh = _capture_split_tile_bbox(ext)
    try:
        from omni.kit.viewport.utility import create_viewport_window
    except Exception:
        create_viewport_window = None  # type: ignore

    ok_all = True
    for ent in pending:
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return False
        try:
            ti = int(ent.get("cell_index", 0) or 0)
        except Exception:
            ti = 0
        ctx_name = str(ent.get("context_name") or f"morph_tbs_split_aux_{ti}")
        wname = str(ent.get("win_name") or _split_window_name(ti))
        _destroy_stale_split_workspace_window(wname)
        x0, y0, x1, y1 = fracs[ti]
        pw = max(_VP_TILE_MIN_PX, int(vw * (x1 - x0)))
        ph = max(_VP_TILE_MIN_PX, int(vh * (y1 - y0)))
        vp_obj = None
        if create_viewport_window is not None:
            try:
                vp_obj = create_viewport_window(
                    name=wname,
                    usd_context_name=ctx_name,
                    width=int(pw),
                    height=int(ph),
                )
            except Exception:
                vp_obj = None
        if vp_obj is None:
            try:
                from omni.kit.viewport.window import ViewportWindow

                win = ViewportWindow(
                    name=wname,
                    usd_context_name=ctx_name,
                    width=int(pw),
                    height=int(ph),
                )
                ent.update(
                    {
                        "kind": "aux_viewport",
                        "window": win,
                        "viewport_window": win,
                        "kit_vp": None,
                    }
                )
            except Exception:
                ok_all = False
                continue
        else:
            ent.update(
                {
                    "kind": "aux_viewport",
                    "kit_vp": vp_obj,
                    "viewport_window": vp_obj,
                    "window": None,
                }
            )
        _apply_aux_window_chrome_flags(wname)
        _workspace_show_named_window(wname, False)
        await kit_app.get_app().next_update_async()
    try:
        ext._sim_multi_viewport_entries = entries
    except Exception:
        pass
    return ok_all


async def _provision_aux_split_tile(
    ext: Any,
    ti: int,
    token: int,
    usd_path: str,
    *,
    use_composed: bool,
    composed_shared: Optional[str],
    fracs: List[Tuple[float, float, float, float]],
    vx: int,
    vy: int,
    vw: int,
    vh: int,
    entries: List[Dict[str, Any]],
    ctx_names: List[str],
    pre_cloned: Optional[Tuple[str, bool]] = None,
    defer_stage_load: bool = False,
    show_before_usd_load: bool = False,
) -> bool:
    """보조 타일 1개: USD 컨텍스트·스테이지·뷰포트 창 생성(hydrate 는 호출측에서 spawn)."""
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return False

    ctx_name = f"morph_tbs_split_aux_{ti}"
    ctx = _named_usd_context(ctx_name)
    if ctx is None:
        try:
            print(f"[TBS multi-sim] USD 컨텍스트 생성 실패: {ctx_name}", flush=True)
        except Exception:
            pass
        return False
    ctx_names.append(ctx_name)
    try:
        ext._sim_multi_context_names = list(ctx_names)
    except Exception:
        pass

    aux_skip_shell_defer = False
    if defer_stage_load:
        if _dual_path_split_defer_skip_shell(ext):
            aux_skip_shell_defer = True
            try:
                print(
                    f"[TBS multi-sim] 보조 타일 {ti}: dual-path defer (shell 생략, 1회 open)",
                    flush=True,
                )
            except Exception:
                pass
            ok_open, err_open = True, ""
            composed_used = False
        else:
            shell_path, shell_err = _make_aux_shell_stage_path(ext, token, ti)
            if not shell_path:
                try:
                    print(f"[TBS multi-sim] 보조 타일 {ti} shell stage 실패: {shell_err}", flush=True)
                except Exception:
                    pass
                _release_usd_context_names([ctx_name])
                if ctx_name in ctx_names:
                    ctx_names.remove(ctx_name)
                return False
            ok_open, err_open = await _ctx_open_stage_path(ctx, shell_path, None)
            composed_used = False
    elif pre_cloned is not None:
        aux_usd, composed_used = str(pre_cloned[0]), bool(pre_cloned[1])
        ok_open, err_open = await _open_aux_stage_with_unique_session(
            ctx,
            aux_usd,
            ext,
            token,
            ti,
            skip_file_clone=True,
        )
    else:
        aux_usd, composed_used, usd_err = await _resolve_aux_tile_usd_path(
            usd_path,
            ext,
            token,
            ti,
            use_composed=use_composed,
            composed_shared=composed_shared,
        )
        if not aux_usd:
            try:
                print(f"[TBS multi-sim] 보조 타일 {ti} USD 준비 실패: {usd_err}", flush=True)
            except Exception:
                pass
            _release_usd_context_names([ctx_name])
            if ctx_name in ctx_names:
                ctx_names.remove(ctx_name)
            return False
        ok_open, err_open = await _open_aux_stage_with_unique_session(
            ctx,
            aux_usd,
            ext,
            token,
            ti,
            skip_file_clone=True,
        )

    if not ok_open:
        try:
            print(f"[TBS multi-sim] 보조 스테이지 열기 실패 ctx={ctx_name} err={err_open}", flush=True)
        except Exception:
            pass
        _release_usd_context_names([ctx_name])
        if ctx_name in ctx_names:
            ctx_names.remove(ctx_name)
        return False

    try:
        st = ctx.get_stage() if hasattr(ctx, "get_stage") else None
        if st is not None:
            _apply_stage_fps_30(st)
    except Exception:
        pass

    await kit_app.get_app().next_update_async()
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return False

    hidden_until_load = bool(defer_stage_load) and not bool(show_before_usd_load)

    try:
        from .sim_multi_view_widget import sim_viewport_split_widget_enabled

        use_widget = sim_viewport_split_widget_enabled()
    except Exception:
        use_widget = False

    if use_widget:
        wname = _split_window_name(ti)
        entries.append(
            {
                "kind": "widget_aux",
                "win_name": wname,
                "cell_index": ti,
                "context_name": ctx_name,
                "viewport_window": None,
                "kit_vp": None,
                "composed_hydrate": composed_used,
                "hydrate_pending": True,
                "stage_load_pending": bool(defer_stage_load),
                "aux_skip_shell_defer": aux_skip_shell_defer,
                "aux_hidden_until_load": hidden_until_load,
                "aux_hidden_until_layout": True,
            }
        )
        await kit_app.get_app().next_update_async()
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return False
        _log_viewport_usd_context_bind(wname, ctx_name)
        return True

    wname = _split_window_name(ti)
    _destroy_stale_split_workspace_window(wname)
    x0, y0, x1, y1 = fracs[ti]
    pw = max(_VP_TILE_MIN_PX, int(vw * (x1 - x0)))
    ph = max(_VP_TILE_MIN_PX, int(vh * (y1 - y0)))
    hidden_until_load = bool(defer_stage_load) and not bool(show_before_usd_load)

    try:
        from omni.kit.viewport.utility import create_viewport_window
    except Exception:
        create_viewport_window = None  # type: ignore

    vp_obj = None
    if create_viewport_window is not None:
        try:
            vp_obj = create_viewport_window(
                name=wname,
                usd_context_name=ctx_name,
                width=int(pw),
                height=int(ph),
            )
        except Exception as e:
            try:
                print(f"[TBS multi-sim] create_viewport_window 실패 name={wname} err={e}", flush=True)
            except Exception:
                pass

    if vp_obj is None:
        try:
            from omni.kit.viewport.window import ViewportWindow

            win = ViewportWindow(
                name=wname,
                usd_context_name=ctx_name,
                width=int(pw),
                height=int(ph),
            )
            entries.append(
                {
                    "window": win,
                    "viewport_window": win,
                    "context_name": ctx_name,
                    "win_name": wname,
                    "cell_index": ti,
                    "kit_vp": None,
                    "composed_hydrate": composed_used,
                    "hydrate_pending": True,
                    "stage_load_pending": bool(defer_stage_load),
                    "aux_skip_shell_defer": aux_skip_shell_defer,
                    "aux_hidden_until_load": hidden_until_load,
                    "aux_hidden_until_layout": True,
                }
            )
        except Exception as e2:
            try:
                print(f"[TBS multi-sim] ViewportWindow 폴백도 실패 name={wname} err={e2}", flush=True)
            except Exception:
                pass
            _release_usd_context_names([ctx_name])
            if ctx_name in ctx_names:
                ctx_names.remove(ctx_name)
            return False
    else:
        entries.append(
            {
                "kit_vp": vp_obj,
                "viewport_window": vp_obj,
                "context_name": ctx_name,
                "win_name": wname,
                "cell_index": ti,
                "composed_hydrate": composed_used,
                "hydrate_pending": True,
                "stage_load_pending": bool(defer_stage_load),
                "aux_skip_shell_defer": aux_skip_shell_defer,
                "aux_hidden_until_load": hidden_until_load,
                "aux_hidden_until_layout": True,
            }
        )

    _apply_aux_window_chrome_flags(wname)
    hide_until_layout = bool(
        defer_stage_load
        and (
            startup_layout_first_active(ext)
            or startup_dual_orchestration_active(ext)
        )
    )
    _workspace_show_named_window(wname, False if hide_until_layout else bool(show_before_usd_load))

    await kit_app.get_app().next_update_async()
    _log_viewport_usd_context_bind(wname, ctx_name)
    for _ in range(2):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return False
        await kit_app.get_app().next_update_async()
    return True


async def _load_aux_split_stages_background(
    ext: Any,
    n: int,
    token: int,
    usd_path: str,
    *,
    prev_n: int = 1,
    serialize_gpu: bool = False,
) -> None:
    """
    분할 레이아웃 표시 **이후** 보조 타일에 합성 USD 를 연다.

    prewarm 스냅샷이 있으면 ``copy_async`` 만(빠름), 없으면 백그라운드 Flatten 후 연다.
    ``serialize_gpu=True`` (layout-first startup): hydrate·레이아웃을 직렬 await.
    """
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return

    swap_settle = _STARTUP_STAGE_SWAP_SETTLE_FRAMES if serialize_gpu else 6

    dual_path = False
    try:
        from .tbs_split_composed_loader import (
            ext_has_composed_instances,
            resolve_composed_snapshot_for_split_async,
            split_dual_usd_paths_enabled,
        )

        dual_path = split_dual_usd_paths_enabled(ext)
    except Exception:
        ext_has_composed_instances = None  # type: ignore
        resolve_composed_snapshot_for_split_async = None  # type: ignore

    use_composed = False if dual_path else _use_split_composed_export(ext)
    composed_shared: Optional[str] = None
    if use_composed:
        try:
            need_composed = bool(ext_has_composed_instances(ext)) if ext_has_composed_instances else False
            if need_composed and resolve_composed_snapshot_for_split_async is not None:
                composed_shared = await resolve_composed_snapshot_for_split_async(ext, token)
                use_composed = bool(composed_shared)
        except Exception:
            use_composed = False
            composed_shared = None
    try:
        mode = "dual-path" if dual_path else ("composed" if use_composed else "clone")
        print(
            f"[TBS multi-sim] bg load: mode={mode} "
            f"snapshot={'ready' if composed_shared else 'none'}",
            flush=True,
        )
    except Exception:
        pass

    clone_map = await _clone_aux_paths_parallel(
        usd_path,
        ext,
        token,
        list(range(1, n)),
        use_composed=use_composed,
        composed_shared=composed_shared,
    )
    if clone_map is None:
        try:
            print("[TBS multi-sim] bg load: USD 복제 실패", flush=True)
        except Exception:
            pass
        return

    entries: List[Dict[str, Any]] = list(getattr(ext, "_sim_multi_viewport_entries", []) or [])
    for ti in range(1, n):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        pre = clone_map.get(ti)
        if not pre:
            continue
        aux_usd, composed_used = str(pre[0]), bool(pre[1])
        ctx_name = f"morph_tbs_split_aux_{ti}"
        ctx = _named_usd_context(ctx_name)
        if ctx is None:
            continue
        wname = _split_window_name(ti)
        stage_pending = False
        skip_shell_defer = False
        for ent in entries:
            try:
                if int(ent.get("cell_index", -1)) == ti:
                    stage_pending = bool(ent.get("stage_load_pending"))
                    skip_shell_defer = bool(ent.get("aux_skip_shell_defer"))
                    break
            except Exception:
                pass
        if stage_pending:
            if not skip_shell_defer:
                try:
                    _workspace_show_named_window(wname, False)
                except Exception:
                    pass
            await _prepare_aux_context_for_stage_swap(ctx, frame_wait=swap_settle)
        ok_open, err_open = await _open_aux_stage_with_unique_session(
            ctx,
            aux_usd,
            ext,
            token,
            ti,
            skip_file_clone=True,
        )
        if not ok_open:
            try:
                print(
                    f"[TBS multi-sim] bg load: tile={ti} open FAIL err={err_open}",
                    flush=True,
                )
            except Exception:
                pass
            if stage_pending and not serialize_gpu:
                try:
                    _workspace_show_named_window(wname, True)
                except Exception:
                    pass
            continue
        for ent in entries:
            try:
                ci = int(ent.get("cell_index", -1))
            except Exception:
                ci = -1
            if ci != ti:
                continue
            ent["stage_load_pending"] = False
            ent["composed_hydrate"] = composed_used
            ent["hydrate_pending"] = True
            break
        if stage_pending and not serialize_gpu and not skip_shell_defer:
            try:
                _workspace_show_named_window(wname, True)
                _sync_viewport_resolution_from_workspace_window(wname)
            except Exception:
                pass
        if serialize_gpu:
            for _ in range(_STARTUP_POST_OPEN_SETTLE_FRAMES):
                if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                    return
                await kit_app.get_app().next_update_async()
        else:
            await kit_app.get_app().next_update_async()

    try:
        ext._sim_multi_viewport_entries = entries
    except Exception:
        pass

    on_done = getattr(ext, "_tbs_split_nav_after_hydrate_fn", None)

    if serialize_gpu:
        await _await_pending_aux_tile_hydrates(
            ext,
            n,
            token,
            fast_visual=False,
            hydrate_settle_frames=_STARTUP_HYDRATE_SETTLE_FRAMES,
            on_all_done=on_done if callable(on_done) else None,
        )
        for _ in range(_STARTUP_POST_HYDRATE_SETTLE_FRAMES):
            if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                return
            await kit_app.get_app().next_update_async()
        await _apply_aux_bg_load_layout_finish(
            ext,
            n,
            token,
            entries,
            show_all_aux_tiles=False,
            skip_dock_reapply=True,
        )
        try:
            ext._tbs_split_layout_usd_key = _split_layout_usd_key(ext)
            ext._tbs_layout_first_aux_load_done = True
        except Exception:
            pass
        return

    _spawn_pending_aux_tile_hydrates(
        ext,
        n,
        token,
        on_all_done=on_done if callable(on_done) else None,
    )

    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return
    await _apply_aux_bg_load_layout_finish(ext, n, token, entries)


async def _complete_startup_layout_first_async(ext: Any, n: int, token: int) -> None:
    """layout-first: Dock 안정화 후 Master USD 자동 로드를 트리거한다."""
    for _ in range(6):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        await kit_app.get_app().next_update_async()

    if bool(getattr(ext, "_tbs_split_used_dock_layout", False)):
        for ti in range(n):
            nm = "Viewport" if ti == 0 else _split_window_name(ti)
            _sync_viewport_resolution_from_workspace_window(nm)

    try:
        set_viewport_fill_frame_for_split_count(n, True)
    except Exception:
        pass

    try:
        print(
            "[TBS multi-sim] layout-first: 2분할 Dock·격자 배치 완료 — USD 로드 대기",
            flush=True,
        )
    except Exception:
        pass

    fn = getattr(ext, "_tbs_on_dual_layout_ready_fn", None)
    if callable(fn):
        try:
            ext._tbs_on_dual_layout_ready_fn = None
        except Exception:
            pass
        try:
            fn(ext)
        except Exception as exc:
            try:
                print(f"[TBS multi-sim] layout-first ready callback failed: {exc}", flush=True)
            except Exception:
                pass

    try:
        ext._tbs_startup_layout_first_active = False
    except Exception:
        pass


async def _finish_split_layout_after_tiles(
    ext: Any, n: int, token: int, *, prev_n: int = 1, skip_hydrate: bool = False
) -> None:
    """타일 생성/제거 후 Dock·카메라·navigation 공통 마무리."""
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return

    notify_sim_split_ui_sync(ext)

    for _ in range(2):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        await kit_app.get_app().next_update_async()

    ctx_names = list(getattr(ext, "_sim_multi_context_names", []) or [])
    _log_split_stage_not_shared_with_main(ctx_names)

    win_names = ["Viewport"] + [_split_window_name(ti) for ti in range(1, n)]
    cam_paths: List[Optional[str]] = [None] + ["/OmniverseKit_Persp"] * (n - 1)

    layout_first_shell = _layout_first_shell_pass(ext, skip_hydrate=skip_hydrate)
    if layout_first_shell:
        for ti in range(1, n):
            _workspace_show_named_window(_split_window_name(ti), False)

    widget_ok = False
    widget_only = False
    try:
        from .sim_multi_view_widget import (
            _destroy_all_aux_workspace_windows,
            apply_split_widget_layout,
            sim_viewport_split_widget_enabled,
        )

        widget_only = sim_viewport_split_widget_enabled()
        if widget_only:
            widget_ok = await apply_split_widget_layout(ext, token, n)
            if widget_ok:
                _sync_entries_from_widget_tiles(ext)
                try:
                    ext._tbs_split_used_dock_layout = False
                except Exception:
                    pass
            else:
                try:
                    print(
                        "[TBS multi-sim] ViewportWidget 분할 실패 — "
                        "Dock/create_viewport_window 폴백 금지 (단일 Viewport 탭 유지)",
                        flush=True,
                    )
                except Exception:
                    pass
                _destroy_all_aux_workspace_windows(ext)
    except Exception:
        widget_ok = False

    docked_ok = False
    if not widget_ok and not widget_only:
        if sim_viewport_split_dock_enabled():
            docked_ok = await _apply_split_dock_layout(ext, token, n)
            if not docked_ok:
                docked_ok = await _retry_split_dock_layout(ext, token, n)
        try:
            ext._tbs_split_used_dock_layout = bool(docked_ok)
            if docked_ok:
                ext._tbs_split_used_widget_layout = False
        except Exception:
            pass

    if widget_ok:
        for _ in range(2):
            if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                return
            await kit_app.get_app().next_update_async()
        try:
            print("[TBS multi-sim] 분할 레이아웃: ViewportWidget shell (Dock 미사용)", flush=True)
        except Exception:
            pass
    elif docked_ok:
        for _ in range(8):
            if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                return
            await kit_app.get_app().next_update_async()
        for ti in range(n):
            nm = "Viewport" if ti == 0 else _split_window_name(ti)
            _sync_viewport_resolution_from_workspace_window(nm)
        try:
            print("[TBS multi-sim] 분할 레이아웃: Viewport Dock (Console/Content 유지)", flush=True)
        except Exception:
            pass
    else:
        await _enforce_equal_split_grid_async(
            ext, token, n, preserve_main_viewport=True
        )

    if not bool(getattr(ext, "_kit_chrome_hide_active", False)):
        if not layout_first_shell:
            _bring_kit_chrome_visible(ext)

    try:
        set_viewport_fill_frame_for_split_count(n, True)
    except Exception:
        pass
    if n >= 2 and not bool(getattr(ext, "_tbs_split_used_widget_layout", False)):
        apply_viewport_split_tab_chrome(n)
        if viewport_split_user_resize_locked():
            apply_viewport_split_user_resize_lock(ext)

    await _assign_split_cameras_after_layout(ext, token, win_names, cam_paths)
    if not layout_first_shell:
        _apply_split_navigation_to_aux(ext, n, token, hold_ticks=96)

    if layout_first_shell:
        await _ensure_split_layout_geometry_ready(ext, token, n)
        _show_split_layout_shell_windows(ext, n)
        try:
            ext._tbs_startup_dual_layout_settled = True
        except Exception:
            pass
        for _ in range(6):
            if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                return
            await kit_app.get_app().next_update_async()
        try:
            print(
                "[TBS multi-sim] layout-first: 2분할 Dock·격자 배치 완료 — USD 로드 대기",
                flush=True,
            )
        except Exception:
            pass
        fn = getattr(ext, "_tbs_on_dual_layout_ready_fn", None)
        if callable(fn):
            try:
                ext._tbs_on_dual_layout_ready_fn = None
            except Exception:
                pass
            try:
                fn(ext)
            except Exception as exc:
                try:
                    print(f"[TBS multi-sim] layout-first ready callback failed: {exc}", flush=True)
                except Exception:
                    pass
        try:
            ext._tbs_startup_layout_first_active = False
        except Exception:
            pass
    else:
        try:
            from .sim_multi_view_widget import is_split_widget_layout_active

            widget_active = is_split_widget_layout_active(ext)
        except Exception:
            widget_active = False
        if not widget_active:
            for ti in range(1, n):
                wname = _split_window_name(ti)
                hidden = False
                for ent in list(getattr(ext, "_sim_multi_viewport_entries", []) or []):
                    if str(ent.get("win_name") or "") == wname:
                        if ent.get("aux_hidden_until_load"):
                            hidden = True
                        ent["aux_hidden_until_layout"] = False
                        break
                if not hidden:
                    _apply_aux_window_chrome_flags(wname)
                    _workspace_show_named_window(wname, True)
                    _sync_viewport_resolution_from_workspace_window(wname)
                    _schedule_split_viewport_input_ready(wname, frames=12)
        else:
            try:
                from .sim_multi_view_widget import _destroy_all_aux_workspace_windows

                _destroy_all_aux_workspace_windows(ext)
            except Exception:
                pass
            for ti in range(1, n):
                wname = _split_window_name(ti)
                _workspace_show_named_window(wname, False)
            for ent in list(getattr(ext, "_sim_multi_viewport_entries", []) or []):
                ent["aux_hidden_until_layout"] = False

    def _nav_after_all_hydrate() -> None:
        if _startup_split_relayout_suppressed(ext):
            return
        try:
            from .sim_multi_view_widget import is_split_widget_layout_active

            if is_split_widget_layout_active(ext):
                return
        except Exception:
            pass
        try:
            reapply_split_layout_sync(ext, n)
            asyncio.ensure_future(reapply_split_layout_after_hydrate_async(ext, token, n))
        except Exception:
            pass
        _apply_split_navigation_to_aux(ext, n, token, hold_ticks=96)

    if skip_hydrate:
        if not layout_first_shell:
            try:
                print(
                    "[TBS multi-sim] 분할 레이아웃 완료 — 보조 스테이지 로드는 백그라운드",
                    flush=True,
                )
            except Exception:
                pass
    else:
        _spawn_pending_aux_tile_hydrates(ext, n, token, on_all_done=_nav_after_all_hydrate)

    try:
        ext._tbs_split_nav_after_hydrate_fn = _nav_after_all_hydrate
    except Exception:
        pass

    try:
        ext._tbs_split_layout_usd_key = _split_layout_usd_key(ext)
    except Exception:
        pass

    try:
        print(
            f"[TBS multi-sim] 분할={n} | 첫 타일=메인 Viewport | 보조 컨텍스트 {len(ctx_names)}개 | "
            f"partial_from={prev_n}",
            flush=True,
        )
    except Exception:
        pass
    try:
        ext._tbs_split_main_viewport_window = _resolve_viewport_window_for_workspace_name("Viewport")
    except Exception:
        try:
            ext._tbs_split_main_viewport_window = None
        except Exception:
            pass
    try:
        from .tbs_split_composed_loader import register_main_composed_runtime

        register_main_composed_runtime(ext)
    except Exception:
        pass
    try:
        ext._sim_runners_by_screen = {}
    except Exception:
        pass

    if n >= 2:
        try:
            from .sim_multi_view_widget import is_split_widget_layout_active

            if not is_split_widget_layout_active(ext):
                apply_viewport_split_tab_chrome(n)
                if viewport_split_user_resize_locked():
                    apply_viewport_split_user_resize_lock(ext)
        except Exception:
            apply_viewport_split_tab_chrome(n)
            if viewport_split_user_resize_locked():
                apply_viewport_split_user_resize_lock(ext)


async def _shrink_split_async(ext: Any, n: int, prev_n: int, token: int) -> None:
    """분할 수 축소(예: 3→2) — 남는 보조 타일은 유지해 재빌드·조작 리셋을 피한다."""
    await kit_app.get_app().next_update_async()
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return

    _cancel_split_aux_navigation_hold(ext)
    entries: List[Dict[str, Any]] = list(getattr(ext, "_sim_multi_viewport_entries", []) or [])
    ctx_names: List[str] = list(getattr(ext, "_sim_multi_context_names", []) or [])

    for ti in range(n, prev_n):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        _destroy_aux_split_tile(ext, ti, entries, ctx_names)

    try:
        ext._sim_multi_viewport_entries = entries
        ext._sim_multi_context_names = ctx_names
        ext._sim_viewport_split_count = n
    except Exception:
        pass

    await _finish_split_layout_after_tiles(ext, n, token, prev_n=prev_n)


async def _grow_split_async(ext: Any, n: int, prev_n: int, token: int, usd_path: str) -> None:
    """분할 수 확대(예: 2→3) — 기존 보조 타일은 유지하고 새 타일만 추가."""
    await kit_app.get_app().next_update_async()
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return

    _cancel_split_aux_navigation_hold(ext)
    use_composed = _use_split_composed_export(ext)
    composed_shared: Optional[str] = None
    if use_composed:
        try:
            from .tbs_split_composed_loader import get_or_export_main_composed_stage

            composed_shared = get_or_export_main_composed_stage(ext, token, 0)
        except Exception:
            composed_shared = None

    fracs = _split_cell_layout_fracs(n)
    vx, vy, vw, vh = _get_split_tile_bbox(ext)
    entries: List[Dict[str, Any]] = list(getattr(ext, "_sim_multi_viewport_entries", []) or [])
    ctx_names: List[str] = list(getattr(ext, "_sim_multi_context_names", []) or [])

    for ti in range(prev_n, n):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        ok = await _provision_aux_split_tile(
            ext,
            ti,
            token,
            usd_path,
            use_composed=use_composed,
            composed_shared=composed_shared,
            fracs=fracs,
            vx=vx,
            vy=vy,
            vw=vw,
            vh=vh,
            entries=entries,
            ctx_names=ctx_names,
        )
        if not ok:
            try:
                print(f"[TBS multi-sim] 분할 확대 실패 tile={ti} → 전체 재빌드", flush=True)
            except Exception:
                pass
            teardown_sim_multi_viewports(ext)
            await _post_teardown_rebuild_split(ext, n, token, usd_path, prev_n=prev_n)
            return

    try:
        ext._sim_multi_viewport_entries = entries
        ext._sim_multi_context_names = ctx_names
        ext._sim_viewport_split_count = n
    except Exception:
        pass

    await _finish_split_layout_after_tiles(ext, n, token, prev_n=prev_n)


async def _build_multi_split_async(ext: Any, n: int, token: int, usd_path: str, *, prev_n: int = 1) -> None:
    """
    첫 타일=기본 Viewport, 나머지=보조 컨텍스트+create_viewport_window.

    shell/빈 스테이지 없이 **실제 USD 를 연 뒤** Dock 분할(메인 카메라와 동일 시점).
    """
    await kit_app.get_app().next_update_async()
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return

    _cancel_pending_split_viewport_restore(ext)
    _capture_kit_layout_before_split(ext)
    fracs = _split_cell_layout_fracs(n)
    vx, vy, vw, vh = _capture_split_tile_bbox(ext)

    entries: List[Dict[str, Any]] = []
    ctx_names: List[str] = []

    main_vp = ui.Workspace.get_window("Viewport")
    if main_vp is None:
        try:
            print("[TBS multi-sim] Workspace 에 'Viewport' 창이 없어 분할을 적용하지 않습니다.", flush=True)
        except Exception:
            pass
        return

    _workspace_show_named_window("Viewport", True)
    try:
        from .sim_multi_view_widget import sim_viewport_split_widget_enabled

        main_kind = "widget_main" if sim_viewport_split_widget_enabled() else "main_viewport"
    except Exception:
        main_kind = "main_viewport"
    entries.append(
        {
            "kind": main_kind,
            "win_name": "Viewport",
            "cell_index": 0,
            "viewport_window": None,
            "kit_vp": None,
        }
    )

    if startup_layout_first_active(ext) or startup_dual_orchestration_active(ext):
        try:
            print(f"[TBS multi-sim] 분할 빌드: layout-first shell (n={n})", flush=True)
        except Exception:
            pass
        for ti in range(1, n):
            if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                _rollback_split_attempt(ext, entries, ctx_names)
                return
            ok = await _provision_aux_split_tile(
                ext,
                ti,
                token,
                usd_path,
                use_composed=False,
                composed_shared=None,
                fracs=fracs,
                vx=vx,
                vy=vy,
                vw=vw,
                vh=vh,
                entries=entries,
                ctx_names=ctx_names,
                defer_stage_load=True,
                show_before_usd_load=False,
            )
            if not ok:
                _rollback_split_attempt(ext, entries, ctx_names)
                return
        try:
            ext._sim_multi_viewport_entries = entries
            ext._sim_multi_context_names = ctx_names
            ext._sim_viewport_split_count = n
            ext._tbs_split_deferred_aux_load_pending = True
        except Exception:
            pass
        await _finish_split_layout_after_tiles(
            ext, n, token, prev_n=max(1, int(prev_n)), skip_hydrate=True
        )
        return

    use_composed = _use_split_composed_export(ext)
    composed_shared: Optional[str] = None
    if use_composed:
        try:
            from .tbs_split_composed_loader import get_or_export_main_composed_stage

            composed_shared = get_or_export_main_composed_stage(ext, token, 0)
        except Exception:
            composed_shared = None
        if not composed_shared:
            use_composed = False
            try:
                from .tbs_split_composed_loader import (
                    ext_has_composed_instances,
                    resolve_composed_snapshot_for_split_async,
                )

                if ext_has_composed_instances(ext):
                    composed_shared = await resolve_composed_snapshot_for_split_async(ext, token)
                    use_composed = bool(composed_shared)
            except Exception:
                pass

    try:
        from .tbs_split_composed_loader import split_dual_usd_paths_enabled

        if split_dual_usd_paths_enabled(ext):
            mode = "dual-path+hydrate"
        else:
            mode = "composed-clone" if use_composed else "master-clone+hydrate"
        print(f"[TBS multi-sim] 분할 빌드: {mode} (n={n})", flush=True)
    except Exception:
        pass

    clone_map = await _clone_aux_paths_parallel(
        usd_path,
        ext,
        token,
        list(range(1, n)),
        use_composed=use_composed,
        composed_shared=composed_shared,
    )
    if clone_map is None:
        _rollback_split_attempt(ext, entries, ctx_names)
        return

    for ti in range(1, n):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            _rollback_split_attempt(ext, entries, ctx_names)
            return
        ok = await _provision_aux_split_tile(
            ext,
            ti,
            token,
            usd_path,
            use_composed=use_composed,
            composed_shared=composed_shared,
            fracs=fracs,
            vx=vx,
            vy=vy,
            vw=vw,
            vh=vh,
            entries=entries,
            ctx_names=ctx_names,
            pre_cloned=clone_map.get(ti),
            defer_stage_load=False,
        )
        if not ok:
            _rollback_split_attempt(ext, entries, ctx_names)
            return

    try:
        ext._sim_multi_viewport_entries = entries
        ext._sim_multi_context_names = ctx_names
        ext._sim_viewport_split_count = n
    except Exception:
        pass

    await _finish_split_layout_after_tiles(
        ext, n, token, prev_n=max(1, int(prev_n)), skip_hydrate=False
    )


async def _post_teardown_rebuild_split(ext: Any, n: int, token: int, usd_path: str, *, prev_n: int = 1) -> None:
    """티어다운 직후 GPU/Hydra 정리 시간을 준 뒤 분할 뷰를 다시 만든다(4→3 등 전환 시 크래시 완화)."""
    try:
        pn = max(1, int(prev_n))
    except Exception:
        pn = 1
    if pn <= 1 and n > 1:
        settle_sec = _SPLIT_REBUILD_SETTLE_SEC_FIRST
        settle_frames = _SPLIT_REBUILD_SETTLE_FRAMES_FIRST
    elif pn > n:
        settle_sec = 0.10
        settle_frames = 2
    else:
        settle_sec = _SPLIT_REBUILD_SETTLE_SEC
        settle_frames = _SPLIT_REBUILD_SETTLE_FRAMES
    try:
        await asyncio.sleep(settle_sec)
    except Exception:
        pass
    for _ in range(settle_frames):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        await kit_app.get_app().next_update_async()
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return
    await _build_multi_split_async(ext, n, token, usd_path, prev_n=pn)


def _apply_sim_viewport_split_layout_impl(ext: Any, n: int) -> None:
    """메인 스레드(권장: post-update 이후)에서 호출."""
    n = channel_count_for_split(n)
    try:
        prev_n = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 1) or 1))
    except Exception:
        prev_n = 1
    # UI·defaults 만 2이고 실제 타일이 없으면 1→n 최초 분할 경로를 탄다.
    if n > 1 and not _split_aux_layout_healthy(ext, n):
        prev_n = 1

    if n <= 1:
        teardown_sim_multi_viewports(ext)
        try:
            ext._sim_viewport_split_count = 1
        except Exception:
            pass
        try:
            ext._tbs_split_3d_disabled_notice = False
        except Exception:
            pass
        try:
            print(f"[TBS multi-sim] 분할=1 | {split_layout_description(1)}", flush=True)
        except Exception:
            pass
        notify_sim_split_ui_sync(ext)
        return

    if not sim_viewport_split_3d_enabled():
        teardown_sim_multi_viewports(ext)
        try:
            ext._sim_viewport_split_count = 1
        except Exception:
            pass
        try:
            if not getattr(ext, "_tbs_split_3d_disabled_notice", False):
                ext._tbs_split_3d_disabled_notice = True
                print(
                    "[TBS multi-sim] 보조 3D 뷰가 꺼져 있습니다(TBS_SIM_VIEWPORT_SPLIT_3D=0 등). "
                    "독립 타일을 보려면 해당 변수를 지우거나 1/true 로 두세요. "
                    "보조 타일은 루트 래퍼 USD 로 분리(TBS_MULTI_SPLIT_SESSION_LAYER=0 이면 래퍼만).",
                    flush=True,
                )
        except Exception:
            pass
        try:
            print(f"[TBS multi-sim] 분할={n} | {split_layout_description(n)} (3D 타일 미생성)", flush=True)
        except Exception:
            pass
        return

    if not getattr(ext, "_tbs_multi_split_usd_ready", False):
        if not preserve_split_layout_during_startup(ext):
            teardown_sim_multi_viewports(ext)
            try:
                ext._sim_viewport_split_count = 1
            except Exception:
                pass
            try:
                print("[TBS multi-sim] TBS 제어창 Load 로 연 스테이지가 있을 때만 분할 뷰를 씁니다.", flush=True)
            except Exception:
                pass
            return

    usd_path = _main_usd_path_for_clone(ext)
    if not usd_path and preserve_split_layout_during_startup(ext):
        usd_path = _startup_split_usd_path(ext)
    if not usd_path:
        teardown_sim_multi_viewports(ext)
        try:
            ext._sim_viewport_split_count = 1
        except Exception:
            pass
        try:
            print("[TBS multi-sim] 복제할 USD 경로를 찾지 못해 분할 뷰를 만들지 않습니다.", flush=True)
        except Exception:
            pass
        return

    try:
        ext._sim_viewport_split_count = n
    except Exception:
        pass
    tok = int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0)

    if _split_layout_already_active(ext, n):
        _apply_split_navigation_to_aux(ext, n, tok, hold_ticks=72)
        notify_sim_split_ui_sync(ext)
        return

    # 재로드 등으로 USD 지문만 바뀐 경우: Viewport 유지 + clone/reopen/hydrate (전체 재빌드 생략).
    if (
        n > 1
        and prev_n == n
        and _split_aux_layout_healthy(ext, n)
        and str(getattr(ext, "_tbs_split_layout_usd_key", "") or "").strip() != _split_layout_usd_key(ext)
    ):
        if bool(getattr(ext, "_tbs_startup_aux_load_inflight", False)):
            notify_sim_split_ui_sync(ext)
            return
        if bool(getattr(ext, "_tbs_layout_first_aux_load_done", False)) and _split_layout_already_active(
            ext, n
        ):
            try:
                ext._tbs_split_layout_usd_key = _split_layout_usd_key(ext)
            except Exception:
                pass
            notify_sim_split_ui_sync(ext)
            return
        try:
            asyncio.ensure_future(_refresh_aux_split_stages_async(ext, n, tok, usd_path))
        except Exception:
            pass
        notify_sim_split_ui_sync(ext)
        return

    # 분할 수만 바뀌고 보조 타일이 건강하면 전체 teardown 없이 부분 리사이즈(3→2 조작 유지·속도 개선).
    if (
        n > 1
        and prev_n > 1
        and n != prev_n
        and _split_aux_layout_healthy(ext, prev_n)
        and str(getattr(ext, "_tbs_split_layout_usd_key", "") or "").strip() == _split_layout_usd_key(ext)
    ):
        try:
            if n < prev_n:
                asyncio.ensure_future(_shrink_split_async(ext, n, prev_n, tok))
            else:
                asyncio.ensure_future(_grow_split_async(ext, n, prev_n, tok, usd_path))
        except Exception:
            teardown_sim_multi_viewports(ext, skip_deferred_restore=True)
            try:
                asyncio.ensure_future(_post_teardown_rebuild_split(ext, n, tok, usd_path, prev_n=prev_n))
            except Exception:
                pass
        return

    # 1→2 최초 분할: 메인 Viewport 레이아웃을 건드리지 않고 바로 빌드(체크 직후 화면 깨짐 방지).
    if (
        n > 1
        and prev_n <= 1
        and not list(getattr(ext, "_sim_multi_viewport_entries", []) or [])
    ):
        for ti in range(1, 5):
            _destroy_stale_split_workspace_window(_split_window_name(ti))
        try:
            asyncio.ensure_future(_build_multi_split_async(ext, n, tok, usd_path, prev_n=prev_n))
        except Exception:
            teardown_sim_multi_viewports(ext, skip_deferred_restore=True)
            try:
                asyncio.ensure_future(_post_teardown_rebuild_split(ext, n, tok, usd_path, prev_n=prev_n))
            except Exception:
                pass
        notify_sim_split_ui_sync(ext)
        return

    teardown_sim_multi_viewports(ext, skip_deferred_restore=True)
    try:
        asyncio.ensure_future(_post_teardown_rebuild_split(ext, n, tok, usd_path, prev_n=prev_n))
    except Exception:
        pass


def apply_sim_viewport_split_layout(ext: Any, split_n: int) -> None:
    """분할 수 변경 시: 다음 업데이트 이후 레이아웃 적용(경합 방지)."""
    teardown_viewport_split_resize_lock(ext)
    n = channel_count_for_split(split_n)
    # ``_sim_viewport_split_count`` 는 impl/빌드 성공 여부에 맞춰만 갱신(단일 소스).
    _cancel_pending_split_viewport_restore(ext)
    tok = int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) + 1
    try:
        ext._sim_multi_view_apply_token = tok
    except Exception:
        pass
    _clear_viewport_layout_sync_caches()

    async def _deferred() -> None:
        await kit_app.get_app().next_update_async()
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != tok:
            return
        _apply_sim_viewport_split_layout_impl(ext, n)

    try:
        asyncio.ensure_future(_deferred())
    except Exception:
        _apply_sim_viewport_split_layout_impl(ext, n)


def attach_stage_visibility_subscription(ext: Any, sync_fn: Callable[[], None]) -> None:
    """스테이지 열림/닫힘 시 sync_fn 호출(분할 UI 표시 동기화)."""
    detach_stage_visibility_subscription(ext)
    try:
        import omni.usd as ou
    except Exception:
        return
    ctx = ou.get_context()
    if ctx is None:
        return
    try:
        ext._sim_split_stage_sub = ctx.get_stage_event_stream().create_subscription_to_pop(
            lambda _e: sync_fn(),
            name="morph.tbs_control_2:sim_multi_split_visibility",
        )
    except Exception:
        ext._sim_split_stage_sub = None


def detach_stage_visibility_subscription(ext: Any) -> None:
    sub: Optional[Any] = getattr(ext, "_sim_split_stage_sub", None)
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
    try:
        ext._sim_split_stage_sub = None
    except Exception:
        pass


def _viewport_window_name_for_screen(screen_1based: int) -> str:
    """화면 인덱스(1~)에 대응하는 Workspace 뷰포트 창 이름."""
    si = int(screen_1based)
    if si <= 1:
        return "Viewport"
    return _split_window_name(si - 1)


def _resolve_viewport_window_for_workspace_name(wname: str) -> Optional[Any]:
    """
    ``Workspace`` 창 이름(예: ``Viewport``, ``TBS_SimSplit_1``)에 대응하는 ``ViewportWindow`` 를 찾는다.

    ``get_viewport_from_window_name`` 이 반환하는 API 객체에 ``viewport_window`` 등이 붙는 Kit 버전이 있다.
    """
    try:
        from .sim_multi_view_widget import get_split_hud_mount, is_split_widget_layout_active
        from .tbs_extension_singleton import get_tbs_extension_instance

        ext = get_tbs_extension_instance()
        if ext is not None and is_split_widget_layout_active(ext) and str(wname) != "Viewport":
            tiles = getattr(ext, "_tbs_split_widget_tiles", None)
            if isinstance(tiles, dict):
                rec = tiles.get(str(wname))
                if isinstance(rec, dict) and bool(rec.get("_uses_viewport_window", False)):
                    vpw = rec.get("viewport_window")
                    if vpw is not None and callable(getattr(vpw, "get_frame", None)):
                        return vpw
            mount = get_split_hud_mount(ext, str(wname))
            if mount is not None and callable(getattr(mount, "get_frame", None)):
                return mount
    except Exception:
        pass
    try:
        api = _split_viewport_api(str(wname))
    except Exception:
        api = None
    if api is None:
        # 일부 Kit 환경에서는 get_viewport_from_window_name("Viewport")가 None을 반환한다.
        # 이 경우 활성 뷰포트 윈도우를 폴백으로 사용한다(주로 화면1).
        try:
            if str(wname) == "Viewport":
                from omni.kit.viewport.utility import get_active_viewport_window

                win = get_active_viewport_window()
                if win is not None and callable(getattr(win, "get_frame", None)):
                    return win
        except Exception:
            pass
        return None
    for attr in ("viewport_window", "window", "_viewport_window", "_window"):
        try:
            cand = getattr(api, attr, None)
        except Exception:
            cand = None
        if cand is not None and callable(getattr(cand, "get_frame", None)):
            return cand
    if callable(getattr(api, "get_frame", None)):
        return api
    # 마지막 폴백: 화면1의 활성 뷰포트 윈도우
    try:
        if str(wname) == "Viewport":
            from omni.kit.viewport.utility import get_active_viewport_window

            win = get_active_viewport_window()
            if win is not None and callable(getattr(win, "get_frame", None)):
                return win
    except Exception:
        pass
    return None


def _snapshot_hud_frame_slot(ext: Any) -> str:
    """``ViewportWindow.get_frame`` 슬롯 ID(확장 인스턴스별로 분리)."""
    eid = str(getattr(ext, "_ext_id", "") or "").strip()
    if not eid:
        eid = "morph.tbs_control_2"
    return f"{eid}:sim_snapshot_hud"


def _viewport_window_for_screen(ext: Any, screen_1based: int) -> Optional[Any]:
    """화면 인덱스(1~)에 붙일 ``ViewportWindow`` (또는 ``get_frame`` 제공 객체)."""
    si = int(screen_1based)
    entries = list(getattr(ext, "_sim_multi_viewport_entries", None) or [])
    if si <= 1:
        vw = getattr(ext, "_tbs_split_main_viewport_window", None)
        if vw is not None and callable(getattr(vw, "get_frame", None)):
            return vw
        return _resolve_viewport_window_for_workspace_name("Viewport")
    for ent in entries:
        try:
            ci = int(ent.get("cell_index", -999))
        except Exception:
            ci = -999
        if ci != si - 1:
            continue
        vw = ent.get("viewport_window") or ent.get("kit_vp") or ent.get("window")
        if vw is not None and callable(getattr(vw, "get_frame", None)):
            return vw
    return _resolve_viewport_window_for_workspace_name(_viewport_window_name_for_screen(si))


def detach_sim_screen1_live_hud_subscription(ext: Any) -> None:
    """화면1 실시간 HUD 갱신 구독 해제."""
    sub = getattr(ext, "_tbs_sim_hud_screen1_live_sub", None)
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
    try:
        ext._tbs_sim_hud_screen1_live_sub = None
    except Exception:
        pass
    try:
        ext._tbs_sim_hud_screen1_label = None
    except Exception:
        pass


def _sim_screen1_hud_post_tick(ext: Any, _e: Any = None) -> None:
    """제어창 값 변경 시 화면1 패널 텍스트만 저빈도로 갱신한다."""
    try:
        ext._tbs_sim_hud_live_ctr = int(getattr(ext, "_tbs_sim_hud_live_ctr", 0) or 0) + 1
    except Exception:
        return
    if ext._tbs_sim_hud_live_ctr % 5 != 0:
        return
    lbl = getattr(ext, "_tbs_sim_hud_screen1_label", None)
    if lbl is None:
        return
    cap_fn = getattr(ext, "_capture_sim_settings_dict_for_hud_fn", None)
    if not callable(cap_fn):
        return
    try:
        cap = cap_fn()
        if not isinstance(cap, dict):
            return
    except Exception:
        return
    try:
        snaps = list(getattr(ext, "_sim_per_screen_snapshots", None) or [None, None, None, None])
        slot0_saved = len(snaps) > 0 and isinstance(snaps[0], dict)
    except Exception:
        slot0_saved = False
    try:
        txt = _describe_snapshot_for_viewport_hud(
            1,
            dict(cap),
            slot_saved=slot0_saved,
            live_control_panel=True,
        )
    except Exception:
        return
    try:
        lbl.text = txt
    except Exception:
        pass


def _ensure_sim_screen1_live_hud_subscription(ext: Any) -> None:
    if getattr(ext, "_tbs_sim_hud_screen1_live_sub", None) is not None:
        return
    try:
        ext._tbs_sim_hud_live_ctr = 0
    except Exception:
        pass
    try:
        ext._tbs_sim_hud_screen1_live_sub = kit_app.get_app().get_post_update_event_stream().create_subscription_to_pop(
            lambda e: _sim_screen1_hud_post_tick(ext, e),
            name="morph.tbs_control_2:sim_hud_screen1_live",
        )
    except Exception:
        try:
            ext._tbs_sim_hud_screen1_live_sub = None
        except Exception:
            pass


def _ep2_layout_from_snap(snap: Dict[str, Any]) -> bool:
    """True → EP2 구성. ``ep_count``(2|3)만 사용."""
    if "ep_count" in snap:
        try:
            return int(snap.get("ep_count", 2) or 2) < 3
        except Exception:
            return True
    try:
        return int(_SIM_DEF.ep_count()) < 3
    except Exception:
        return True


def _format_initial_load_ports_line(snap: Dict[str, Any]) -> str:
    """스냅샷/캡처 dict 기준 초기 적재(풀) 포트 목록."""
    ep2 = _ep2_layout_from_snap(snap)
    pairs = (
        ("INOUT", "init_inout"),
        ("BP1", "init_bp1"),
        ("BP2", "init_bp2"),
        ("BP3", "init_bp3"),
        ("BP4", "init_bp4"),
        ("EP1", "init_ep1"),
        ("EP2", "init_ep2"),
        ("EP3", "init_ep3"),
    )
    acc: List[str] = []
    for lab, key in pairs:
        if ep2 and lab in ("BP4", "EP3"):
            continue
        try:
            if bool(snap.get(key)):
                acc.append(lab)
        except Exception:
            pass
    return ",".join(acc) if acc else "(없음)"


def _fault_count_from_snapshot_dict(d: Dict[str, Any]) -> int:
    n = 0
    for k in (
        "fault_inout",
        "fault_bp1",
        "fault_bp2",
        "fault_bp3",
        "fault_bp4",
        "fault_ep1",
        "fault_ep2",
        "fault_ep3",
    ):
        try:
            if bool(d.get(k)):
                n += 1
        except Exception:
            pass
    return n


def _describe_snapshot_for_viewport_hud(
    screen_1based: int,
    snap: Dict[str, Any],
    *,
    slot_saved: bool,
    live_control_panel: bool = False,
) -> str:
    """
    뷰포트 인-뷰 패널 텍스트.

    - ``live_control_panel=True`` (화면1): 제어창과 동기 표시. 글리프 깨짐 방지로 ``|`` 구분·``시뮬`` 표기.
    """
    ep2 = _ep2_layout_from_snap(snap)
    ep_s = "EP2구성" if ep2 else "EP3구성"
    try:
        lots = max(1, int(snap.get("lot_count", _SIM_DEF.lot_count) or _SIM_DEF.lot_count))
    except Exception:
        lots = int(_SIM_DEF.lot_count)
    try:
        smn = float(snap.get("spawn_min", _SIM_DEF.lot_spawn_min))
        smx = float(snap.get("spawn_max", _SIM_DEF.lot_spawn_max))
    except Exception:
        smn, smx = float(_SIM_DEF.lot_spawn_min), float(_SIM_DEF.lot_spawn_max)
    try:
        pmn = float(snap.get("pue_min", _SIM_DEF.pickup_min))
        pmx = float(snap.get("pue_max", _SIM_DEF.pickup_max))
    except Exception:
        pmn, pmx = float(_SIM_DEF.pickup_min), float(_SIM_DEF.pickup_max)
    fc = _fault_count_from_snapshot_dict(snap)
    init_line = _format_initial_load_ports_line(snap)
    if live_control_panel:
        tag = "제어창 실시간"
    elif slot_saved:
        tag = "저장됨"
    else:
        tag = "미저장 | 제어창값"
    title = f"화면{int(screen_1based)} | 시뮬 설정 ({tag})"
    line_a = f"{ep_s} | LOT {lots} | 고장 {fc}개"
    line_b = f"초기 적재: {init_line}"
    line_c = f"생성 {smn:.0f}-{smx:.0f}s | 회수 {pmn:.0f}-{pmx:.0f}s"
    return f"{title}\n{line_a}\n{line_b}\n{line_c}"


def _hud_panel_size_for_text(body: str, *, font_size: int = 13, padding: int = 8) -> tuple[int, int]:
    """
    HUD 패널을 텍스트 크기에 맞춰 타이트하게 감싸는 폭/높이를 계산한다.
    (Kit UI 폰트/레이아웃은 완전한 auto-size가 환경별로 달라, 보수적인 근사치를 사용)
    """
    lines = [str(ln) for ln in str(body or "").splitlines()]
    if not lines:
        lines = [""]
    max_chars = max((len(ln) for ln in lines), default=0)
    # 경험치 기반 근사치(13px 폰트): 글자폭 ~7px, 줄높이 ~18px
    char_w = 7
    line_h = max(16, int(font_size * 1.35))
    text_w = max(120, min(520, max_chars * char_w))
    text_h = max(line_h, len(lines) * line_h)
    panel_w = text_w + padding * 2
    panel_h = text_h + padding * 2
    return int(panel_w), int(panel_h)


def destroy_viewport_snapshot_hud_layers(ext: Any) -> None:
    """``get_frame`` 슬롯에 넣었던 스냅샷 패널 루트 위젯을 제거한다."""
    detach_sim_screen1_live_hud_subscription(ext)
    roots = getattr(ext, "_tbs_sim_snapshot_hud_roots", None)
    if isinstance(roots, dict):
        for _k, w in list(roots.items()):
            if w is None:
                continue
            try:
                w.destroy()
            except Exception:
                pass
    try:
        ext._tbs_sim_snapshot_hud_roots = {}
    except Exception:
        pass
    try:
        ext._tbs_sim_snapshot_hud_windows = []
    except Exception:
        pass


def _snapshot_hud_placer_offset_x(ext: Any, screen_1based: int, panel_w: int, margin: int = 8) -> int:
    """HUD 패널을 우측 상단에 두되, 전체 뷰포트를 덮지 않게 Placer offset 을 계산한다."""
    si = int(screen_1based)
    wname = "Viewport" if si <= 1 else _split_window_name(si - 1)
    try:
        from .sim_multi_view_widget import is_split_widget_layout_active

        if si <= 1 and ext is not None and is_split_widget_layout_active(ext):
            w = ui.Workspace.get_window("Viewport")
            ww = int(getattr(w, "width", 0) or 0)
            half = max(1, ww // 2)
            if half > panel_w + margin:
                return max(0, half - panel_w - margin)
    except Exception:
        pass
    try:
        w = ui.Workspace.get_window(str(wname))
        ww = int(getattr(w, "width", 0) or 0)
        if ww > panel_w + margin:
            return max(0, ww - panel_w - margin)
    except Exception:
        pass
    return max(0, 360 - panel_w - margin)


def sync_viewport_snapshot_hud_layers(ext: Any) -> None:
    """
    각 분할 타일의 ``ViewportWindow.get_frame`` 레이어에 우측 상단 2D 패널을 붙인다(별도 ``ui.Window`` 없음).

    - **화면1**: 항상 ``_capture_sim_settings_dict_for_hud_fn()`` 제어창 값으로 표시하고, post_update 로 저빈도 텍스트 갱신.
    - **화면2~**: ``_sim_per_screen_snapshots`` 가 있으면 해당 dict, 없으면 제어창 캡처로 표시.
    """
    destroy_viewport_snapshot_hud_layers(ext)
    try:
        n = channel_count_for_split(int(getattr(ext, "_sim_viewport_split_count", 1) or 1))
    except Exception:
        n = 1

    try:
        snaps = list(getattr(ext, "_sim_per_screen_snapshots", None) or [None, None, None, None])
    except Exception:
        snaps = [None, None, None, None]
    while len(snaps) < 4:
        snaps.append(None)
    snaps = snaps[:4]

    cap: Dict[str, Any] = {}
    cap_fn = getattr(ext, "_capture_sim_settings_dict_for_hud_fn", None)
    if callable(cap_fn):
        try:
            raw = cap_fn()
            if isinstance(raw, dict):
                cap = raw
        except Exception:
            cap = {}

    slot = _snapshot_hud_frame_slot(ext)
    new_roots: Dict[int, Any] = {}

    for si in range(1, n + 1):
        if si >= 2:
            # 보조 타일: get_frame 2D HUD 가 마우스(Alt+orbit)를 가로채므로 화면1만 HUD 표시.
            continue
        vw = _viewport_window_for_screen(ext, si)
        if vw is None:
            continue
        raw_snap = snaps[si - 1] if si - 1 < len(snaps) else None
        slot_saved = isinstance(raw_snap, dict)
        if si == 1:
            snap_d = dict(cap or {})
            body = _describe_snapshot_for_viewport_hud(
                si,
                snap_d,
                slot_saved=slot_saved,
                live_control_panel=True,
            )
        else:
            snap_d = dict(raw_snap) if isinstance(raw_snap, dict) else dict(cap or {})
            body = _describe_snapshot_for_viewport_hud(si, snap_d, slot_saved=slot_saved)
        try:
            pw, ph = _hud_panel_size_for_text(body, font_size=13, padding=8)
            # 최소/최대 (너무 작은 패널/너무 큰 패널 방지)
            pw = max(160, min(560, int(pw)))
            ph = max(56, min(260, int(ph)))
        except Exception:
            pw, ph = 300, 120
        root: Optional[Any] = None
        body_lbl: Optional[Any] = None
        try:
            ox = _snapshot_hud_placer_offset_x(ext, si, pw)
            with vw.get_frame(slot):
                with ui.Placer(offset_x=int(ox), offset_y=8):
                    root = ui.Frame(
                        width=pw,
                        height=ph,
                        style={
                            "border_width": 1,
                            "border_color": 0xFF5A6A80,
                            "border_radius": 4,
                            "padding": 8,
                        },
                    )
                    with root:
                        with ui.ZStack():
                            ui.Rectangle(style={"background_color": 0xCC1A1A1A})
                            body_lbl = ui.Label(
                                body,
                                word_wrap=True,
                                width=max(1, pw - 16),
                                height=max(1, ph - 16),
                                style={"color": 0xFFFFFFFF, "font_size": 13},
                            )
        except Exception:
            root = None
            body_lbl = None
        if root is not None:
            new_roots[si] = root
        if si == 1 and body_lbl is not None:
            try:
                ext._tbs_sim_hud_screen1_label = body_lbl
            except Exception:
                pass

    try:
        ext._tbs_sim_snapshot_hud_roots = new_roots
    except Exception:
        pass
    if getattr(ext, "_tbs_sim_hud_screen1_label", None) is not None:
        _ensure_sim_screen1_live_hud_subscription(ext)


def schedule_viewport_snapshot_hud_refresh(ext: Any) -> None:
    """Dock/뷰포트 레이아웃이 잡힌 뒤 HUD 를 다시 붙이기 위해 몇 프레임 뒤에 실행한다."""
    if _widget_split_startup_in_progress(ext):
        return
    try:
        ext._tbs_sim_snapshot_hud_sched_token = int(getattr(ext, "_tbs_sim_snapshot_hud_sched_token", 0) or 0) + 1
        tok = int(ext._tbs_sim_snapshot_hud_sched_token)
    except Exception:
        tok = 0

    async def _go() -> None:
        for _ in range(4):
            try:
                await kit_app.get_app().next_update_async()
            except Exception:
                return
        try:
            if int(getattr(ext, "_tbs_sim_snapshot_hud_sched_token", 0) or 0) != tok:
                return
        except Exception:
            return
        try:
            sync_viewport_snapshot_hud_layers(ext)
        except Exception:
            pass

    try:
        asyncio.ensure_future(_go())
    except Exception:
        pass
