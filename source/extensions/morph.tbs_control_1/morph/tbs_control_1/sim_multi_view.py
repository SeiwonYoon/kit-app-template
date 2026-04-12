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
- **레이아웃**: 기본은 보조 Workspace 창의 ``dock_in(기준_Window, DockPosition, ratio)`` 만 사용한다.
  ``dock_in_window`` / 모듈 ``dock_window_in_window`` 는 deprecated·불안정해 **호출하지 않는다**.
  ``docked`` 플래그가 일정 프레임 안에 True 가 되지 않으면 **좌표 격자**로 폴백한다(멈춤·반쪽 레이아웃 방지).
  ``TBS_SIM_VIEWPORT_SPLIT_DOCK=0`` 이면 Dock 시도 없이 격자만 쓴다.
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


def sim_viewport_split_dock_enabled() -> bool:
    """
    보조 뷰포트를 메인 ``Viewport`` Dock 안에 넣을지(기본 True).

    ``TBS_SIM_VIEWPORT_SPLIT_DOCK=0`` / ``false`` / ``off`` 등이면 좌표 격자만 사용(별도 창에 가깝게 보일 수 있음).
    """
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
    """활성 시뮼 채널 수(1~4)."""
    try:
        n = int(split_n)
    except Exception:
        n = 1
    return min(4, max(1, n))


def split_layout_description(split_n: int) -> str:
    """사용자 안내용 짧은 레이아웃 설명."""
    n = channel_count_for_split(split_n)
    if n <= 1:
        return "단일 화면(기본)"
    if n == 2:
        return "2분할: 메인 Viewport Dock 안 좌/우(기본) — 보조는 독립 스테이지(동일 USD 경로)"
    if n == 3:
        return "3분할: Dock 안 상=메인, 하=보조 2칸(독립 스테이지)"
    return "4분할: Dock 안 2×2(좌상=메인, 나머지=독립 스테이지)"


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
    ext: Any, token: int, names: List[str], max_frames: int = 48
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
    ext: Any, token: int, aux_names: List[str], max_frames: int = 36
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
    """보조 Workspace 창을 기준 ``Window`` 옆 Dock 에 붙인다. ``dock_in`` 만 사용(deprecated API 금지)."""
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
        # 비율 1.0 = 대상(DockSpace) 쪽에 남는 단일 창으로 재배치(LEFT 가 일반적).
        for pos_name in ("LEFT", "TOP", "SAME"):
            pos = getattr(DP, pos_name, None)
            if pos is None:
                continue
            try:
                din(ds, pos, 1.0)
                return True
            except Exception:
                continue
    except Exception:
        pass
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
    ok_any = False
    for child, target, pos, ratio in ops:
        try:
            child_w = ui.Workspace.get_window(str(child))
            target_w = ui.Workspace.get_window(str(target))
        except Exception:
            child_w, target_w = None, None
        if child_w is None or target_w is None:
            continue
        if _dock_aux_into_target(child_w, target_w, pos, float(ratio)):
            ok_any = True
    return ok_any


def _sync_viewport_resolution_from_workspace_window(win_name: str) -> None:
    """Workspace 창의 ``width``/``height`` 에 맞춰 뷰포트 API ``resolution`` 을 맞춘다(렌더 버퍼 크기)."""
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name

        w = ui.Workspace.get_window(str(win_name))
        api = get_viewport_from_window_name(str(win_name))
        if w is None or api is None or not hasattr(api, "resolution"):
            return
        ww = int(getattr(w, "width", 0) or 0)
        hh = int(getattr(w, "height", 0) or 0)
        if ww < 8 or hh < 8:
            return
        api.resolution = (max(1, ww), max(1, hh))
    except Exception:
        pass


def set_viewport_fill_frame_for_split_count(split_n: int, fill: bool) -> None:
    """
    뷰포트 API ``fill_frame`` — 부모 UI(Workspace / Dock 타일) 크기에 맞춰 렌더 해상도를 맞출지.

    ``fill_frame`` 가 꺼져 있으면 ``resolution`` 이 고정된 채로 남아, 메뉴·패널을 숨겨도
    3D 픽셀 영역이 늘지 않는 경우가 있다. ``morph.morph_base_viewer`` 등에서 쓰는 패턴과 같다.
    """
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
            api = get_viewport_from_window_name(str(name))
            if api is not None and hasattr(api, "fill_frame"):
                api.fill_frame = bool(fill)
        except Exception:
            pass


async def _apply_split_dock_layout(ext: Any, token: int, n: int) -> bool:
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
        for _ in range(4):
            if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                return False
            await kit_app.get_app().next_update_async()
    if not await _wait_aux_windows_docked(ext, token, aux_names, max_frames=36):
        try:
            print(
                "[TBS multi-sim] Dock 분할: dock_in 후에도 보조 창 docked=False → 좌표 격자로 폴백합니다",
                flush=True,
            )
        except Exception:
            pass
        return False
    try:
        print("[TBS multi-sim] Viewport Dock 분할 적용 완료 (dock_in)", flush=True)
    except Exception:
        pass
    return True


def _main_usd_path_for_clone(ext: Any) -> Optional[str]:
    """TBS Load 로 연 경로 우선, 없으면 현재 기본 스테이지 루트 레이어에서 추정."""
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
    try:
        await asyncio.wait_for(omni.client.copy_async(usd_path, dest_uri), timeout=300.0)
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
) -> Tuple[bool, str]:
    """
    보조 컨텍스트에서 씬을 연다.

    1) 기본(**``TBS_MULTI_SPLIT_FILE_CLONE``** unset/true): 원본을 **타일별 임시 파일로 복제** 후 그 경로를 연다
       (동일 URL 을 subLayer 로만 묶을 때 생기는 **레이어·씬 공유**를 끊기 위함).
    2) 복제를 끈 경우: 타일 전용 **래퍼 루트 .usda**(subLayer = 원본)로 연다.
    3) ``TBS_MULTI_SPLIT_SESSION_LAYER`` 가 켜져 있으면 위 루트 + session layer, 실패 시 루트만 재시도.
    """
    root_path: Optional[str] = None
    if _use_aux_file_clone():
        clone_path, cerr = await _clone_usd_for_aux_tile(usd_path, ext, token, ti)
        if clone_path:
            root_path = clone_path
        else:
            try:
                print(f"[TBS multi-sim] 보조 USD 복제 실패, 래퍼로 폴백: {cerr}", flush=True)
            except Exception:
                pass

    if root_path is None:
        wrap_path, werr = _make_aux_wrapper_root_layer(usd_path, ext, token, ti)
        if not wrap_path:
            return False, werr
        root_path = wrap_path

    sess_path: Optional[str] = None
    if _use_multi_split_session_layer():
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
        for op in ("close_stage", "unload_stage"):
            fn = getattr(ctx, op, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
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


def _workspace_show_named_window(name: str, visible: bool) -> None:
    """
    이름 있는 Workspace 창의 표시를 바꾼다.

    ``get_window(...).visible`` 이 내부적으로 WindowHandle 경로를 타며
    ``Calling setVisible to WindowHandle will be deprecated`` 가 날 수 있어,
    가능하면 ``Workspace.show_window`` 를 쓴다.
    """
    try:
        fn = getattr(ui.Workspace, "show_window", None)
        if callable(fn):
            fn(str(name), bool(visible))
            return
    except Exception:
        pass
    try:
        w = ui.Workspace.get_window(str(name))
        if w is not None:
            w.visible = bool(visible)
    except Exception:
        pass


def _restore_main_viewport_layout(ext: Any) -> None:
    """분할 전에 저장한 Dock 사각형으로 기본 Viewport 를 되돌린다."""
    r = getattr(ext, "_tbs_split_saved_viewport_rect", None)
    try:
        main_vp = ui.Workspace.get_window("Viewport")
        if r is None or main_vp is None:
            return
        vx, vy, vw, vh = r
        main_vp.position_x = int(vx)
        main_vp.position_y = int(vy)
        main_vp.width = max(64, int(vw))
        main_vp.height = max(64, int(vh))
        _workspace_show_named_window("Viewport", True)
    except Exception:
        pass


def teardown_sim_multi_viewports(ext: Any) -> None:
    """분할 뷰·보조 USD 컨텍스트를 정리하고 기본 Viewport 를 복원한다."""
    destroy_viewport_snapshot_hud_layers(ext)
    entries: List[Dict[str, Any]] = list(getattr(ext, "_sim_multi_viewport_entries", []) or [])
    # 보조 뷰를 먼저 파괴한 뒤 메인 Viewport 를 복원한다(메인만 먼저 키우면 보조와 동시에 그려져 GPU 부담).
    for ent in entries:
        if ent.get("kind") == "main_viewport":
            continue
        if ent.get("kit_vp") is not None:
            _destroy_kit_viewport(ent.get("kit_vp"))
        else:
            _destroy_viewport_window(ent.get("window"))
    for ent in entries:
        if ent.get("kind") == "main_viewport":
            _restore_main_viewport_layout(ext)
            break
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

    _workspace_show_named_window("Viewport", True)


_VP_TILE_MIN_PX = 128

# 분할 수 변경 시: 티어다운 직후 곧바로 Hydra/뷰를 다시 만들면 GPU 가 깨지는 경우가 있어 잠깐 쉰다.
_SPLIT_REBUILD_SETTLE_SEC = 0.88
_SPLIT_REBUILD_SETTLE_FRAMES = 12


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
    Dock 기반 2~4분할: 메인 ``Viewport`` 를 먼저 확장된 ``DockSpace`` 에 한 판으로 붙인 뒤,
    보조 창 ``dock_in`` 을 다시 적용한다.

    보조만 ``dock_in`` 을 반복하면 메인 Viewport 가 옛 Dock 면적에 묶인 채로 남는 경우가 있다.
    """
    if n <= 1:
        return
    _dock_viewport_fill_dockspace()
    _reapply_split_dock_in_geometry(ext)


def _apply_split_geometry_sync(
    win_names: List[str],
    fracs: List[Tuple[float, float, float, float]],
    vx: int,
    vy: int,
    vw: int,
    vh: int,
) -> None:
    """분할 타일 창 크기·위치를 한 번에 적용(토큰 검사 없음). Dock/메뉴 변경 뒤 재맞춤용."""
    for i, (x0, y0, x1, y1) in enumerate(fracs):
        if i >= len(win_names):
            break
        name = win_names[i]
        tw = max(_VP_TILE_MIN_PX, int(vw * (x1 - x0)))
        th = max(_VP_TILE_MIN_PX, int(vh * (y1 - y0)))
        px = vx + int(vw * x0)
        py = vy + int(vh * y0)
        try:
            win = ui.Workspace.get_window(name)
            if win is not None:
                _workspace_show_named_window(name, True)
                win.position_x = int(px)
                win.position_y = int(py)
                win.width = int(tw)
                win.height = int(th)
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
    1분할일 때 ``Viewport`` Workspace 창을 가용 영역에 맞춘다.

    - **메뉴 숨김 ON** (``menus_hidden=True``): 우선 ``Viewport.dock_in(DockSpace, …)`` 로 Dock 을
      한 칸으로 다시 잡은 뒤(절대 좌표는 Dock 에서 무시되는 경우가 많음), 필요 시 ``DockSpace``/메인
      창 사각형으로 폴백한다.
    - **메뉴 숨김 OFF** (``menus_hidden=False``): 메뉴바가 다시 생긴 뒤 ``DockSpace`` 안에서
      상단 메뉴 높이만큼 뺀 영역에 맞춘다(Kit 가 Viewport 탭 크기를 아직 반영 안 한 경우 보정).
    """
    if menus_hidden:
        if _dock_viewport_fill_dockspace():
            try:
                ext._tbs_split_saved_viewport_rect = _read_viewport_rect()
            except Exception:
                pass
            return
        r = _read_dockspace_rect()
        if r is None:
            try:
                mw = int(ui.Workspace.get_main_window_width() or 1280)
                mh = int(ui.Workspace.get_main_window_height() or 720)
                r = (0, 0, max(400, mw), max(300, mh))
            except Exception:
                return
        vx, vy, vw, vh = int(r[0]), int(r[1]), int(r[2]), int(r[3])
        # DockSpace 가 아직 이전 레이아웃 크기일 때가 있어, Viewport 창이 이미 더 크면 그쪽을 반영한다.
        try:
            px, py, pw, ph = _read_viewport_rect()
            if pw >= 64 and ph >= 64:
                vw = max(vw, int(pw))
                vh = max(vh, int(ph))
        except Exception:
            pass
    else:
        r = _read_dockspace_rect()
        if r is not None:
            mbh = _menubar_reserved_height_px()
            vx, vy, vw, vh = int(r[0]), int(r[1]) + mbh, int(r[2]), max(64, int(r[3]) - mbh)
        else:
            vx, vy, vw, vh = _read_viewport_rect()
    if vw < 64 or vh < 64:
        return
    try:
        ext._tbs_split_saved_viewport_rect = (vx, vy, vw, vh)
    except Exception:
        pass
    try:
        w = ui.Workspace.get_window("Viewport")
        if w is not None:
            _workspace_show_named_window("Viewport", True)
            w.position_x = int(vx)
            w.position_y = int(vy)
            w.width = int(vw)
            w.height = int(vh)
    except Exception:
        pass
    if not menus_hidden:
        _sync_viewport_resolution_from_workspace_window("Viewport")


def relayout_split_views_to_viewport(ext: Any, _menus_hidden: bool = False) -> None:
    """
    메뉴바·패널 표시가 바뀐 뒤 Dock 이 안정된 뒤 3D 뷰 영역을 다시 맞춘다.

    - **1분할**: 메뉴 숨김 여부에 따라 ``Viewport`` 를 ``DockSpace``/메뉴바 보정 영역에 맞춘다.
    - **2~4분할(격자)**: 합집합 기준(메뉴 숨김 시에는 ``DockSpace``/메인 창 우선)으로 격자 재배치.
    - **2~4분할(Dock)**: 메뉴 숨김 시 ``Viewport`` 를 ``DockSpace`` 에 다시 채운 뒤 ``dock_in`` 분할을 재적용.

    ``_menus_hidden``: ``True`` = 메뉴·패널 숨김 적용 상태, ``False`` = 복원(다시 보임).

    메뉴 숨김 시에는 뷰포트 API ``fill_frame=True`` 로 두어(고정 ``resolution`` 유지 시
    UI만 넓어지고 3D가 그대로인 현상 완화) Dock/격자 재배치와 함께 적용한다.
    """
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

    if bool(getattr(ext, "_tbs_split_used_dock_layout", False)):
        if mh:
            _refresh_docked_multi_split_after_chrome(ext, n)
        else:
            _reapply_split_dock_in_geometry(ext)
        if not mh:
            for ti in range(0, n):
                nm = "Viewport" if ti == 0 else _split_window_name(ti)
                _sync_viewport_resolution_from_workspace_window(nm)
        return

    entries = list(getattr(ext, "_sim_multi_viewport_entries", []) or [])
    if len(entries) < 2:
        return

    vx, vy, vw, vh = _read_split_layout_bbox_for_chrome(n, mh)
    if vw < _VP_TILE_MIN_PX * 2 or vh < _VP_TILE_MIN_PX * 2:
        return
    try:
        ext._tbs_split_saved_viewport_rect = (vx, vy, vw, vh)
    except Exception:
        pass

    fracs = _split_cell_layout_fracs(n)
    win_names = ["Viewport"] + [_split_window_name(ti) for ti in range(1, n)]
    _apply_split_geometry_sync(win_names, fracs, vx, vy, vw, vh)
    if not mh:
        for nm in win_names:
            _sync_viewport_resolution_from_workspace_window(nm)


def schedule_split_layout_refresh_for_chrome_change(ext: Any, menus_hidden: bool) -> None:
    """Kit 크롬(메뉴·패널) 표시 변경 직후 Dock 이 안정된 뒤 뷰 레이아웃을 다시 맞춘다."""

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
        _apply_split_geometry_sync([name], [frac], vx, vy, vw, vh)
        await kit_app.get_app().next_update_async()


async def _assign_split_cameras_after_layout(
    ext: Any,
    token: int,
    win_names: List[str],
    cam_paths: List[Optional[str]],
) -> None:
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name
    except Exception:
        return

    for _ in range(6):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        missing = False
        for i, name in enumerate(win_names):
            if i >= len(cam_paths) or not cam_paths[i]:
                continue
            api = get_viewport_from_window_name(name)
            if api is None:
                missing = True
                continue
            try:
                api.camera_path = cam_paths[i]
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
    schedule_viewport_snapshot_hud_refresh(ext)


def _rollback_split_attempt(ext: Any, entries: List[Dict[str, Any]], ctx_names: List[str]) -> None:
    _restore_main_viewport_layout(ext)
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


async def _build_multi_split_async(ext: Any, n: int, token: int, usd_path: str) -> None:
    """첫 타일=기본 Viewport, 나머지=보조 컨텍스트+create_viewport_window(usd_context_name=...)."""
    await kit_app.get_app().next_update_async()
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return

    try:
        from omni.kit.viewport.utility import create_viewport_window
    except Exception as e:
        try:
            print(f"[TBS multi-sim] create_viewport_window import 실패: {e}", flush=True)
        except Exception:
            pass
        return

    fracs = _split_cell_layout_fracs(n)
    vx, vy, vw, vh = _read_viewport_rect()
    try:
        ext._tbs_split_saved_viewport_rect = (vx, vy, vw, vh)
    except Exception:
        pass

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
    entries.append({"kind": "main_viewport", "win_name": "Viewport", "cell_index": 0, "viewport_window": None, "kit_vp": None})

    for ti in range(1, n):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            _rollback_split_attempt(ext, entries, ctx_names)
            return

        ctx_name = f"morph_tbs_split_aux_{ti}"
        ctx = _named_usd_context(ctx_name)
        if ctx is None:
            try:
                print(f"[TBS multi-sim] USD 컨텍스트 생성 실패: {ctx_name}", flush=True)
            except Exception:
                pass
            _rollback_split_attempt(ext, entries, ctx_names)
            return
        ctx_names.append(ctx_name)
        ok_open, err_open = await _open_aux_stage_with_unique_session(ctx, usd_path, ext, token, ti)
        if not ok_open:
            try:
                print(f"[TBS multi-sim] 보조 스테이지 열기 실패 ctx={ctx_name} err={err_open}", flush=True)
            except Exception:
                pass
            _rollback_split_attempt(ext, entries, ctx_names)
            return
        try:
            st = ctx.get_stage() if hasattr(ctx, "get_stage") else None
            if st is not None:
                _apply_stage_fps_30(st)
        except Exception:
            pass

        for _ in range(8):
            await kit_app.get_app().next_update_async()

        wname = _split_window_name(ti)
        x0, y0, x1, y1 = fracs[ti]
        pw = max(_VP_TILE_MIN_PX, int(vw * (x1 - x0)))
        ph = max(_VP_TILE_MIN_PX, int(vh * (y1 - y0)))
        px = vx + int(vw * x0)
        py = vy + int(vh * y0)

        vp_obj = None
        try:
            try:
                vp_obj = create_viewport_window(
                    name=wname,
                    usd_context_name=ctx_name,
                    width=int(pw),
                    height=int(ph),
                    position_x=int(px),
                    position_y=int(py),
                )
            except TypeError:
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
                    }
                )
            except Exception as e2:
                try:
                    print(f"[TBS multi-sim] ViewportWindow 폴백도 실패 name={wname} err={e2}", flush=True)
                except Exception:
                    pass
                _rollback_split_attempt(ext, entries, ctx_names)
                return
        else:
            if vp_obj is None:
                _rollback_split_attempt(ext, entries, ctx_names)
                return
            entries.append(
                {
                    "kit_vp": vp_obj,
                    "viewport_window": vp_obj,
                    "context_name": ctx_name,
                    "win_name": wname,
                    "cell_index": ti,
                }
            )

        try:
            wui = ui.Workspace.get_window(wname)
            if wui is not None:
                # 타이틀·스크롤만 끄고 이동/크기 조절은 허용(분할 타일을 손으로 맞출 수 있게).
                wui.flags = ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_SCROLLBAR
                _workspace_show_named_window(wname, True)
        except Exception:
            pass

        for _ in range(4):
            await kit_app.get_app().next_update_async()
        _log_viewport_usd_context_bind(wname, ctx_name)

    try:
        await asyncio.sleep(0.06)
    except Exception:
        pass
    for _ in range(4):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        await kit_app.get_app().next_update_async()

    try:
        ext._sim_multi_viewport_entries = entries
        ext._sim_multi_context_names = ctx_names
    except Exception:
        pass

    _log_split_stage_not_shared_with_main(ctx_names)

    win_names = ["Viewport"] + [_split_window_name(ti) for ti in range(1, n)]
    cam_paths: List[Optional[str]] = [None] + ["/OmniverseKit_Persp"] * (n - 1)

    docked_ok = await _apply_split_dock_layout(ext, token, n)
    try:
        ext._tbs_split_used_dock_layout = bool(docked_ok)
    except Exception:
        pass

    if not docked_ok:
        await _finalize_split_window_geometry_sequential(ext, token, win_names, fracs, vx, vy, vw, vh)
    else:
        for _ in range(10):
            if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
                return
            await kit_app.get_app().next_update_async()

    await _assign_split_cameras_after_layout(ext, token, win_names, cam_paths)

    try:
        print(
            f"[TBS multi-sim] 분할={n} | 첫 타일=메인 Viewport | 보조 컨텍스트 {len(ctx_names)}개 | path={usd_path}",
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
    notify_sim_split_ui_sync(ext)


async def _post_teardown_rebuild_split(ext: Any, n: int, token: int, usd_path: str) -> None:
    """티어다운 직후 GPU/Hydra 정리 시간을 준 뒤 분할 뷰를 다시 만든다(4→3 등 전환 시 크래시 완화)."""
    try:
        await asyncio.sleep(_SPLIT_REBUILD_SETTLE_SEC)
    except Exception:
        pass
    for _ in range(_SPLIT_REBUILD_SETTLE_FRAMES):
        if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
            return
        await kit_app.get_app().next_update_async()
    if int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) != token:
        return
    await _build_multi_split_async(ext, n, token, usd_path)


def _apply_sim_viewport_split_layout_impl(ext: Any, n: int) -> None:
    """메인 스레드(권장: post-update 이후)에서 호출."""
    n = channel_count_for_split(n)
    teardown_sim_multi_viewports(ext)
    try:
        if n <= 1:
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
            return

        if not sim_viewport_split_3d_enabled():
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
        if not usd_path:
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
        try:
            asyncio.ensure_future(_post_teardown_rebuild_split(ext, n, tok, usd_path))
        except Exception:
            pass
    finally:
        notify_sim_split_ui_sync(ext)


def apply_sim_viewport_split_layout(ext: Any, split_n: int) -> None:
    """분할 수 변경 시: 다음 업데이트 이후 레이아웃 적용(경합 방지)."""
    n = channel_count_for_split(split_n)
    # ``_sim_viewport_split_count`` 는 impl/빌드 성공 여부에 맞춰만 갱신(단일 소스).
    tok = int(getattr(ext, "_sim_multi_view_apply_token", 0) or 0) + 1
    try:
        ext._sim_multi_view_apply_token = tok
    except Exception:
        pass

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
            name="morph.tbs_control_1:sim_multi_split_visibility",
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
        from omni.kit.viewport.utility import get_viewport_from_window_name
    except Exception:
        return None
    try:
        api = get_viewport_from_window_name(str(wname))
    except Exception:
        api = None
    if api is None:
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
    return None


def _snapshot_hud_frame_slot(ext: Any) -> str:
    """``ViewportWindow.get_frame`` 슬롯 ID(확장 인스턴스별로 분리)."""
    eid = str(getattr(ext, "_ext_id", "") or "").strip()
    if not eid:
        eid = "morph.tbs_control_1"
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
            name="morph.tbs_control_1:sim_hud_screen1_live",
        )
    except Exception:
        try:
            ext._tbs_sim_hud_screen1_live_sub = None
        except Exception:
            pass


def _format_initial_load_ports_line(snap: Dict[str, Any]) -> str:
    """스냅샷/캡처 dict 기준 초기 적재(풀) 포트 목록."""
    try:
        ep2 = int(snap.get("ep_count_idx", 0) or 0) == 0
    except Exception:
        ep2 = True
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
    try:
        ep2 = int(snap.get("ep_count_idx", 0) or 0) == 0
    except Exception:
        ep2 = True
    ep_s = "EP2구성" if ep2 else "EP3구성"
    try:
        lots = max(1, int(snap.get("lot_count", 6) or 6))
    except Exception:
        lots = 6
    try:
        smn = float(snap.get("spawn_min", 15.0))
        smx = float(snap.get("spawn_max", 40.0))
    except Exception:
        smn, smx = 15.0, 40.0
    try:
        pmn = float(snap.get("pue_min", 50.0))
        pmx = float(snap.get("pue_max", 70.0))
    except Exception:
        pmn, pmx = 50.0, 70.0
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
        root: Optional[Any] = None
        body_lbl: Optional[Any] = None
        try:
            ra = getattr(ui, "Alignment", None)
            rt = getattr(ra, "RIGHT_TOP", None) if ra is not None else None
            with vw.get_frame(slot):
                root = ui.ZStack(alignment=rt) if rt is not None else ui.ZStack()
                with root:
                    ui.Spacer()
                    with ui.Frame(
                        width=278,
                        style={
                            "background_color": 0xE8121824,
                            "border_width": 1,
                            "border_color": 0xFF5A6A80,
                            "border_radius": 4,
                            "padding": 8,
                        },
                    ):
                        body_lbl = ui.Label(
                            body,
                            word_wrap=True,
                            width=260,
                            style={"color": 0xFFE8F4FF, "font_size": 13},
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
    try:
        ext._tbs_sim_snapshot_hud_sched_token = int(getattr(ext, "_tbs_sim_snapshot_hud_sched_token", 0) or 0) + 1
        tok = int(ext._tbs_sim_snapshot_hud_sched_token)
    except Exception:
        tok = 0

    async def _go() -> None:
        for _ in range(10):
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
