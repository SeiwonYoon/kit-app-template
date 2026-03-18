# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
TBS Control 확장: my_company.usd_loader와 동일한 방식으로 USD 로드(open_stage), 장비 prim 제어창, 가상 이벤트 애니메이션.

- USD 로드: usd_loader 참고 — 경로 입력, stat_async 검증 후 open_stage(path) 호출.
- 제어 창: '목록 새로고침'으로 현재 스테이지에서 prim 수집 후 드롭다운 표시. 객체 정보, X/Y/Z, button_0/button_1.
"""

import asyncio
import time
from pathlib import Path
from typing import List, Optional

import omni.client
import omni.ext
import omni.kit.app as app
import omni.ui as ui
import omni.usd as ou
from carb.eventdispatcher import get_eventdispatcher
from omni.kit.viewport.utility import get_active_viewport_window
from pxr import Gf, Usd, UsdGeom, Sdf

from .prim_info import get_prim_display_name, safe_str
from .translate_animation import (
    run_prim_translate_animation,
    stop_prim_translate_animation,
)
from .curve_animation import (
    make_parabolic_path,
    run_prim_curve_animation,
    stop_prim_curve_animation,
)
from .rotate_animation import (
    run_prim_rotate_animation,
    stop_prim_rotate_animation,
)
from .viewport_overlay import PrimInfoOverlay
from .signal_parser import parse_signal
from . import usd_animation_control
from . import xml_generator
from .sequence_editor import SequenceEditorWindow

# usd_loader와 동일 기본 URL
DEFAULT_USD_URL = (
    "https://restme.morph.kr/~jh.park2/DirTest/"
    "PhysicalAI_SceneAssembly_Start/SceneAssembly.usd"
)

# prim이 많을 때 창에 전부 넣으면 UI/GPU 버퍼 과다로 경고·멈춤 발생. 표시 개수 상한.
MAX_PRIMS_DISPLAY = 80
# 우선 표시할 prim 이름 접두사 (다른 USD에서는 비우거나 다른 규칙으로 변경 가능)
DEFAULT_PRIORITY_NAME_PREFIX = "Mesh_"

# 가상 제너레이터: JSON 샘플 (Mesh_226, Mesh_567 → x+100 1초, y+100 1초, 2초에 원위치)
SAMPLE_GENERATOR_JSON = """{
  "objects": ["Mesh_308", "Mesh_561", "WalkwayEndA_01"],
  "animation": {
    "segments": [
      {"duration": 1.0, "delta": [100, 0, 0]},
      {"duration": 1.0, "delta": [0, 100, 0]},
      {"duration": 2.0, "delta": [-100, -100, 0]}
    ]
  }
}"""

# 3D 정보 패널 오버레이: 뷰포트 연결 재시도 횟수
_VIEWPORT_RETRY_FRAMES = 180


_SUPPORTED_STAGE_EXTS: Optional[set] = None


def _get_supported_stage_extensions() -> set:
    """
    현재 Kit 환경에서 open_stage()로 직접 열 수 있는(USD FileFormat 지원) 확장자 집합을 반환.
    실패 시 보수적 fallback을 사용.
    """
    global _SUPPORTED_STAGE_EXTS
    if _SUPPORTED_STAGE_EXTS is not None:
        return _SUPPORTED_STAGE_EXTS
    exts = set()
    try:
        for fmt in Sdf.FileFormat.FindAllFileFormats():
            for e in fmt.GetFileExtensions() or []:
                if not e:
                    continue
                exts.add("." + str(e).lower())
    except Exception:
        exts = set()
    if not exts:
        exts = {".usd", ".usda", ".usdc", ".usdz", ".sdf", ".sda", ".sdc"}
    _SUPPORTED_STAGE_EXTS = exts
    return exts


def _path_has_supported_stage_extension(path: str) -> bool:
    """URL query/fragment 제거 후 확장자 체크."""
    if not path:
        return False
    p = path.strip().lower()
    if not p:
        return False
    p = p.split("#", 1)[0].split("?", 1)[0]
    return any(p.endswith(ext) for ext in _get_supported_stage_extensions())


def _get_resource_folder_path() -> Optional[Path]:
    """launch 실행 최상단 경로(${root}) 아래의 resource 폴더. carb.tokens → __file__ 상위 → cwd."""
    try:
        import carb
        tokens = carb.tokens.get_tokens_interface()
        if tokens:
            root = tokens.resolve("${root}")
            if root:
                resource_dir = Path(root) / "resource"
                if resource_dir.is_dir():
                    return resource_dir
    except Exception:
        pass
    try:
        current = Path(__file__).resolve()
        for _ in range(10):
            current = current.parent
            if not current:
                break
            resource_dir = current / "resource"
            if resource_dir.is_dir():
                return resource_dir
    except Exception:
        pass
    try:
        cwd_resource = Path.cwd() / "resource"
        if cwd_resource.is_dir():
            return cwd_resource
    except Exception:
        pass
    return None


def _get_resource_usd_list() -> List[tuple]:
    """resource 폴더 내 '스테이지로 직접 로드 가능한' 확장자 목록. [(이름, 절대경로), ...]"""
    resource_dir = _get_resource_folder_path()
    if not resource_dir:
        return []
    exts = _get_supported_stage_extensions()
    result: List[tuple] = []
    try:
        for p in sorted(resource_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in exts:
                result.append((p.name, str(p)))
    except Exception:
        pass
    return result
# 뷰포트 선택 폴링 주기 (SELECTION_CHANGED 이벤트가 없는 환경 대비)
_POLL_FRAME_INTERVAL = 30


def _post_update_once(callback):
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
        _on_event, name="morph.tbs_control:PostUpdateOnce"
    )
    return sub_ref[0]


def _get_stage():
    ctx = ou.get_context()
    return ctx.get_stage() if ctx else None


def _is_utf8_safe(s: str) -> bool:
    if not s:
        return True
    try:
        s.encode("utf-8")
        return True
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False


def _collect_prim_paths_safe(stage: Usd.Stage) -> List[str]:
    """open_stage 후 스테이지 전체에서 prim 경로 수집. UTF-8 안전 경로만. 예외 나는 prim/자식은 모두 건너뜀."""
    paths: List[str] = []

    def visit(prim: Usd.Prim) -> None:
        try:
            path = str(prim.GetPath())
        except Exception:
            return
        if path == "/":
            try:
                for ch in prim.GetChildren():
                    visit(ch)
            except Exception:
                pass
            return
        try:
            if not _is_utf8_safe(path):
                for ch in prim.GetChildren():
                    visit(ch)
                return
            if prim.IsA(UsdGeom.Xform) or prim.IsA(UsdGeom.Gprim) or prim.GetTypeName() == "Scope":
                paths.append(path)
            for ch in prim.GetChildren():
                visit(ch)
        except Exception:
            pass

    try:
        root = stage.GetPseudoRoot()
        if root:
            visit(root)
    except Exception:
        pass
    return paths


def _find_prim_path_by_name(stage: Usd.Stage, name: str) -> Optional[str]:
    """이름 또는 경로가 일치하는 첫 번째 prim 경로 반환."""
    paths = _find_all_prim_paths_by_name(stage, name)
    return paths[0] if paths else None


def _find_all_prim_paths_by_name(stage: Usd.Stage, name: str) -> List[str]:
    """해당 이름(GetName()) 또는 경로와 일치하는 모든 prim 경로. 동일 이름 여러 개 시 전부."""
    result: List[str] = []
    name_s = name.strip()
    if not name_s:
        return result
    try:
        if name_s.startswith("/"):
            prim = stage.GetPrimAtPath(name_s)
            if prim and prim.IsValid():
                result.append(name_s)
                return result
        else:
            prim = stage.GetPrimAtPath("/" + name_s)
            if prim and prim.IsValid():
                result.append("/" + name_s)
                return result
    except Exception:
        pass

    def visit(prim: Usd.Prim) -> None:
        if prim.GetPath().pathString == "/":
            for ch in prim.GetChildren():
                visit(ch)
            return
        try:
            if safe_str(prim.GetName()) == name_s:
                result.append(str(prim.GetPath()))
        except Exception:
            pass
        for ch in prim.GetChildren():
            visit(ch)

    try:
        root = stage.GetPseudoRoot()
        if root:
            visit(root)
    except Exception:
        pass
    return result


def _get_prim_local_translate(prim: Usd.Prim) -> Gf.Vec3f:
    if not prim or not prim.IsValid():
        return Gf.Vec3f(0, 0, 0)
    xform = UsdGeom.Xformable(prim)
    if not xform:
        return Gf.Vec3f(0, 0, 0)
    for op in xform.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            val = op.Get()
            return Gf.Vec3f(val[0], val[1], val[2]) if val is not None else Gf.Vec3f(0, 0, 0)
    return Gf.Vec3f(0, 0, 0)


def _set_prim_translate_only(prim: Usd.Prim, position: Gf.Vec3f) -> None:
    if not prim or not prim.IsValid():
        return
    # XformCommonAPI 우선: scale op가 있는 prim에서도 호환되는 xformOpOrder 유지
    try:
        api = UsdGeom.XformCommonAPI(prim)
        if api:
            api.SetTranslate(Gf.Vec3d(float(position[0]), float(position[1]), float(position[2])))
            return
    except Exception:
        pass
    xform = UsdGeom.Xformable(prim)
    if not xform:
        return
    translate_op = None
    for op in xform.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            translate_op = op
            break
    if translate_op is None:
        translate_op = xform.AddTranslateOp()
    translate_op.Set(Gf.Vec3f(position[0], position[1], position[2]))


def _frame_prim_in_viewport(prim_path: str) -> None:
    try:
        from omni.kit.viewport.utility import frame_viewport_prims, get_active_viewport
    except ImportError:
        return

    async def _do_frame():
        await omni.kit.app.get_app().next_update_async()
        viewport_api = get_active_viewport()
        if not viewport_api:
            try:
                from omni.kit.viewport.utility import get_active_viewport_window
                win = get_active_viewport_window()
                viewport_api = win.viewport_api if win else None
            except Exception:
                pass
        if viewport_api:
            frame_viewport_prims(viewport_api, prims=[prim_path])

    asyncio.ensure_future(_do_frame())


class Extension(omni.ext.IExt):
    def on_startup(self, ext_id: str) -> None:
        self._ext_id = ext_id
        self._tracked_paths: List[str] = []
        self._open_paths: List[str] = []  # 3D 정보 패널로 표시 중인 prim 경로 (show_info와 동일)
        self._overlay: Optional[PrimInfoOverlay] = None
        self._overlay_retry_count = 0
        self._selection_sub = None
        self._stage_stream_sub = None
        self._post_update_sub = None
        self._last_paths: tuple = ()
        self._ignore_selection_until = 0.0  # X 클릭 직후 0.2초간 뷰포트 선택 이벤트 무시
        self._poll_frame = 0
        self._priority_prefix_model = ui.SimpleStringModel(DEFAULT_PRIORITY_NAME_PREFIX)
        self._usd_anim_start_frame = ui.SimpleIntModel(200)
        self._usd_anim_end_frame = ui.SimpleIntModel(300)
        self._usd_anim_loop = ui.SimpleBoolModel(False)
        self._usd_anim_range_mode = ui.SimpleIntModel(0)  # 0:수동 1:자동
        self._usd_anim_auto_range_text = ui.SimpleStringModel("AUTO RANGE: (미확인)")
        self._xml_seq_model = ui.SimpleIntModel(0)  # 0:a 1:b 2:c 3:d
        self._xml_from_port_model = ui.SimpleIntModel(1)
        self._xml_to_port_model = ui.SimpleIntModel(6)
        self._last_generated_xml: str = ""
        self._load_window = None
        self._control_window = None
        self._object_list_frame = None
        self._sequence_window = None
        self._build_load_window()
        self._build_control_window()
        self._build_sequence_window()
        self._try_attach_overlay()

        ctx = ou.get_context()
        ed = get_eventdispatcher()
        try:
            event_name = ctx.stage_event_name(ou.StageEventType.SELECTION_CHANGED)
            self._selection_sub = ed.observe_event(
                observer_name="morph.tbs_control:SelectionChanged",
                event_name=event_name,
                on_event=self._on_selection_changed,
            )
        except Exception:
            pass
        try:
            self._stage_stream_sub = ctx.get_stage_event_stream().create_subscription_to_pop(
                self._on_stage_event,
                name="morph.tbs_control:StageEvents",
            )
        except Exception:
            pass
        try:
            self._post_update_sub = app.get_app().get_post_update_event_stream().create_subscription_to_pop(
                self._on_post_update,
                name="morph.tbs_control:PostUpdate",
            )
        except Exception:
            pass

    def on_shutdown(self) -> None:
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
        if self._load_window is not None:
            self._load_window.destroy()
            self._load_window = None
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

    def _build_sequence_window(self) -> None:
        """별도 시퀀스 편집기 창 생성 (기존 TBS 제어창과 별개)."""
        self._sequence_window = SequenceEditorWindow()

    def _try_attach_overlay(self) -> None:
        """활성 뷰포트에 3D 정보 오버레이 연결 (show_info와 동일). 뷰포트가 없으면 다음 프레임에 재시도."""
        viewport_window = get_active_viewport_window()
        if viewport_window:
            if self._overlay is None:
                self._overlay = PrimInfoOverlay(viewport_window, self._ext_id)
                self._overlay.set_on_close(self._on_close_info_panel)
                self._overlay.set_open_paths(self._open_paths)
                self._overlay.build_scene()
                self._overlay.update_panels()
            return
        self._overlay_retry_count += 1
        if self._overlay_retry_count < _VIEWPORT_RETRY_FRAMES:
            _post_update_once(self._try_attach_overlay)

    def _on_close_info_panel(self, path_str: str) -> None:
        """3D 패널 X 버튼 클릭 시 해당 경로 제거, 뷰포트 선택을 _open_paths로 복원, 패널 갱신 (show_info와 동일)."""
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
            self._overlay.set_open_paths(self._open_paths)
            self._overlay.update_panels()

    def _on_post_update(self, _event) -> None:
        """매 프레임: X 클릭 직후에는 선택을 _open_paths로 유지, 그 외에는 N프레임마다 뷰포트 선택 반영."""
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
            self._apply_selection()

    def _on_stage_event(self, event) -> None:
        """스테이지 이벤트(선택 변경 등) 시 선택 변경 핸들러 호출."""
        self._on_selection_changed(event)

    def _add_selection_to_open_paths(self, paths) -> None:
        """뷰포트에서 객체 클릭 시 기존 패널은 모두 지우고, 선택한 객체 1개만 3D 패널에 표시."""
        path_strs = [str(p) for p in (paths or []) if p is not None and str(p).strip()]
        if path_strs:
            self._open_paths.clear()
            self._open_paths.append(path_strs[0])

    def _on_selection_changed(self, _event) -> None:
        """뷰포트에서 객체 클릭 시 선택 경로를 _open_paths에 반영하고 3D 패널 갱신."""
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
        except Exception:
            paths = []
        self._last_paths = tuple(paths or [])
        self._add_selection_to_open_paths(paths or [])
        self._apply_selection()

    def _apply_selection(self) -> None:
        """_open_paths 기준으로 오버레이에 3D 패널 갱신."""
        if self._overlay is None:
            _post_update_once(self._try_attach_overlay)
        if self._overlay:
            self._overlay.set_open_paths(self._open_paths)
            self._overlay.update_panels()

    def _show_prim_info_in_viewport(self, prim_path: str) -> None:
        """기존 3D 패널은 모두 지우고, 해당 prim 1개만 3D 패널에 표시."""
        self._open_paths.clear()
        self._open_paths.append(prim_path)
        try:
            sel = ou.get_context().get_selection()
            sel.set_selected_prim_paths([prim_path], True)
        except Exception:
            pass
        if self._overlay is None:
            def _attach_and_update():
                self._try_attach_overlay()
                if self._overlay:
                    self._overlay.set_open_paths(self._open_paths)
                    self._overlay.update_panels()
            _post_update_once(_attach_and_update)
        else:
            self._overlay.set_open_paths(self._open_paths)
            self._overlay.update_panels()

    def _build_load_window(self) -> None:
        """경로 입력 + resource 폴더 샘플(선택안함 포함), stat_async 검증 후 open_stage(path)."""
        self._load_window = ui.Window("USD Load", width=480, height=200)
        resource_items = _get_resource_usd_list()
        self._resource_names = ["선택안함"] + [name for name, _ in resource_items]
        self._resource_paths = [""] + [path for _, path in resource_items]
        with self._load_window.frame:
            with ui.VStack(padding=10, spacing=8):
                ui.Label("resource 폴더 샘플 (선택안함 = 아래 경로로 로드)", height=0)
                self._resource_combo = ui.ComboBox(0, *self._resource_names)
                self._resource_combo.model.add_item_changed_fn(self._on_resource_combo_changed)
                ui.Spacer(height=4)
                ui.Label("경로 (직접 입력 또는 위에서 선택)", height=0)
                self._path_model = ui.SimpleStringModel(DEFAULT_USD_URL)
                ui.StringField(model=self._path_model)
                self._load_status_label = ui.Label("", style={"color": 0xFF888888})
                ui.Button(
                    "Load",
                    clicked_fn=lambda: asyncio.ensure_future(self._on_load_usd()),
                )

    def _on_resource_combo_changed(self, model, *args) -> None:
        """resource 콤보 선택 시: 선택안함(0)이면 경로 필드 유지, 그 외에는 해당 USD 경로로 설정."""
        try:
            index = model.get_item_value_model().as_int
            if 0 <= index < len(self._resource_paths) and index != 0:
                self._path_model.set_value_as_string(self._resource_paths[index])
        except Exception:
            pass

    def _get_load_path(self) -> str:
        """Load 시 사용할 경로. 선택안함(인덱스 0)이면 경로 필드 값, 그 외에는 콤보에서 선택한 resource 경로."""
        path = (self._path_model.get_value_as_string() or "").strip()
        if getattr(self, "_resource_paths", None) and getattr(self, "_resource_combo", None):
            try:
                index = self._resource_combo.model.get_item_value_model().as_int
                if 0 <= index < len(self._resource_paths):
                    if index == 0:
                        return path
                    return self._resource_paths[index] or path
            except Exception:
                pass
        return path

    def _build_control_window(self) -> None:
        self._control_window = ui.Window("TBS 제어창", width=460, height=640)
        with self._control_window.frame:
            with ui.VStack(spacing=0):
                ui.Label("USD 파일 애니메이션 (타임라인)", height=0)
                with ui.HStack(spacing=8, height=28):
                    ui.Label("범위", width=50, height=28)
                    # NOTE: 일부 Kit 버전에서는 ComboBox가 height= 인자를 받지 않습니다.
                    # 높이는 HStack(height=28)로 맞추고 ComboBox에는 height를 넘기지 않습니다.
                    self._usd_anim_mode_combo = ui.ComboBox(0, "수동", "자동")
                    self._usd_anim_mode_combo.model.add_item_changed_fn(self._on_usd_anim_mode_changed)
                    ui.Label("", width=0)  # spacer
                self._usd_anim_manual_frame_row = ui.HStack(spacing=8, height=30)
                with self._usd_anim_manual_frame_row:
                    ui.Label("시작 프레임", width=70, height=30)
                    ui.IntField(model=self._usd_anim_start_frame, width=60, height=30)
                    ui.Label("끝 프레임", width=70, height=30)
                    ui.IntField(model=self._usd_anim_end_frame, width=60, height=30)
                self._usd_anim_auto_range_row = ui.HStack(spacing=8, height=22)
                with self._usd_anim_auto_range_row:
                    ui.Label("AUTO", width=50, height=22)
                    ui.Label("", model=self._usd_anim_auto_range_text, height=22)
                # 초기 상태: 수동
                self._usd_anim_manual_frame_row.visible = True
                self._usd_anim_auto_range_row.visible = False
                with ui.HStack(spacing=8, height=20):
                    ui.CheckBox(model=self._usd_anim_loop)
                    ui.Label("루프", height=0)
                ui.Button(
                    "USD 파일 애니메이션 재생",
                    height=28,
                    clicked_fn=self._on_play_usd_animation,
                )
                ui.Button(
                    "USD 애니메이션 정지",
                    height=24,
                    clicked_fn=lambda: usd_animation_control.stop_usd_animation(),
                )
                ui.Spacer(height=6)
                ui.Button(
                    "가상 시그널 재생 (JSON 샘플)",
                    height=28,
                    clicked_fn=self._on_play_generator_sample,
                )
                ui.Spacer(height=6)

                # ---------------------------
                # XML 제너레이터 생성기
                # ---------------------------
                ui.Rectangle(height=2, style={"background_color": 0xFF3A3A3A})
                ui.Spacer(height=6)
                with ui.Frame(style={"background_color": 0xFF23262B}):
                    with ui.VStack(padding=8, spacing=6):
                        with ui.HStack(spacing=8, height=28):
                            ui.Label("XML 제너레이터 생성기", width=150, height=28, style={"color": 0xFFDDDDDD})
                            # sequence_name: a~d
                            # NOTE: 일부 Kit 버전에서는 ComboBox가 height= 인자를 받지 않습니다.
                            self._xml_seq_combo = ui.ComboBox(0, "A", "B", "C", "D")
                            self._xml_seq_combo.model.add_item_changed_fn(self._on_xml_seq_changed)
                            ui.Button("OK", width=60, height=28, clicked_fn=self._on_xml_ok_clicked)
                        # a/b 선택 시에만 포트 입력칸 표시
                        self._xml_ab_inputs_frame = ui.HStack(spacing=8, height=28)
                        with self._xml_ab_inputs_frame:
                            # 초기값은 A(0)로 간주: 입력칸 표시
                            ui.Label("FROM_PORT_ID", width=110, height=28)
                            ui.IntField(model=self._xml_from_port_model, width=60, height=28)
                            ui.Label("TO_PORT_ID", width=90, height=28)
                            ui.IntField(model=self._xml_to_port_model, width=60, height=28)
                        # 초기 표시 상태 반영 (기본 A)
                        self._xml_ab_inputs_frame.visible = True
                        ui.Button(
                            "제너레이터 실행(역파싱)",
                            height=28,
                            clicked_fn=self._on_xml_run_clicked,
                        )
                ui.Spacer(height=6)
                ui.Rectangle(height=2, style={"background_color": 0xFF3A3A3A})
                ui.Spacer(height=8)

                ui.Label("우선 표시 이름 규칙 (접두사, 비우면 순서대로 표시)", height=0)
                ui.StringField(model=self._priority_prefix_model, height=22)
                ui.Spacer(height=4)
                ui.Label("로드된 USD 내 장비 prim (드롭다운)", height=0)
                ui.Button("목록 새로고침", height=28, clicked_fn=self._on_refresh_prim_list)
                ui.Spacer(height=4)
                with ui.ScrollingFrame(style={"ScrollingFrame": {"padding": 0, "margin": 0}}):
                    self._object_list_frame = ui.VStack(height=0, alignment=ui.Alignment.LEFT_TOP)
        self._refresh_object_list()

    def _on_usd_anim_mode_changed(self, model, *args) -> None:
        """USD 애니메이션 재생 범위 모드 변경: 수동(프레임 입력) / 자동(USD 저장 범위)."""
        try:
            idx = model.get_item_value_model().as_int
        except Exception:
            idx = 0
        is_auto = idx == 1
        if self._usd_anim_manual_frame_row:
            self._usd_anim_manual_frame_row.visible = not is_auto
        if self._usd_anim_auto_range_row:
            self._usd_anim_auto_range_row.visible = is_auto
        if is_auto:
            rng = usd_animation_control.resolve_saved_animation_frame_range()
            if rng:
                self._usd_anim_auto_range_text.set_value(f"AUTO RANGE: {rng[0]} ~ {rng[1]}")
            else:
                self._usd_anim_auto_range_text.set_value("AUTO RANGE: (감지 실패)")

    def _on_xml_seq_changed(self, model, *args) -> None:
        """
        sequence_name 드롭다운 변경 핸들러.
        - A/B: from/to port 입력칸 표시
        - C/D: 입력칸 숨김 (향후 build_body_for_sequence_cd 구현 시 UI 확장 가능)
        """
        try:
            idx = model.get_item_value_model().as_int
        except Exception:
            idx = 0
        show_ab = idx in (0, 1)
        if self._xml_ab_inputs_frame:
            self._xml_ab_inputs_frame.visible = show_ab

    def _on_xml_ok_clicked(self) -> None:
        """현재 선택된 sequence_name과 입력값으로 XML 문자열을 생성하고 print/log로 출력."""
        try:
            idx = self._xml_seq_combo.model.get_item_value_model().as_int if self._xml_seq_combo else 0
        except Exception:
            idx = 0
        seq = ["A", "B", "C", "D"][idx] if 0 <= idx <= 3 else "A"

        try:
            if seq in ("A", "B"):
                from_port = self._xml_from_port_model.get_value_as_int()
                to_port = self._xml_to_port_model.get_value_as_int()
                xml = xml_generator.build_xml_string(seq, from_port_id=from_port, to_port_id=to_port)
            else:
                # C/D는 body 구조가 달라질 예정 → xml_generator.build_body_for_sequence_cd()를 수정하면 됨
                xml = xml_generator.build_xml_string(seq)
            self._last_generated_xml = xml
            print(xml, flush=True)  # noqa: T201
        except Exception as e:
            print(f"[morph.tbs_control][xml_generator] XML 생성 실패: {e}", flush=True)  # noqa: T201

    def _on_xml_run_clicked(self) -> None:
        """OK로 저장된 XML을 역파싱하여 속성값들을 추출하고 로그 출력."""
        xml_text = (self._last_generated_xml or "").strip()
        if not xml_text:
            print("[morph.tbs_control][xml_generator] 저장된 XML이 없습니다. 먼저 OK로 XML을 생성하세요.", flush=True)  # noqa: T201
            return
        parsed = xml_generator.parse_xml_string(xml_text)
        if not parsed:
            print("[morph.tbs_control][xml_generator] XML 역파싱 실패.", flush=True)  # noqa: T201
            return
        lines = ["[XML PARSE RESULT]"]
        for k in (
            "sequence_name",
            "destination",
            "origination",
            "tid",
            "facility",
            "equipment_id",
            "foup",
            "from_eqp_id",
            "from_port_id",
            "to_eqp_id",
            "to_port_id",
        ):
            lines.append(f"{k} = {parsed.get(k, '')}")
        msg = "\n".join(lines)
        print(msg, flush=True)  # noqa: T201

    def _on_play_usd_animation(self) -> None:
        """저장된 USD 내 타임라인 애니메이션을 지정 프레임 구간(기본 200~300)으로 재생. 루프 옵션 적용."""
        loop = self._usd_anim_loop.get_value_as_bool()
        # 모드: 0 수동, 1 자동
        try:
            mode = self._usd_anim_mode_combo.model.get_item_value_model().as_int if getattr(self, "_usd_anim_mode_combo", None) else 0
        except Exception:
            mode = 0
        if mode == 1:
            rng = usd_animation_control.resolve_saved_animation_frame_range()
            if not rng:
                print("[USD ANIM] 자동 범위 감지 실패: Stage/Timeline에서 start/end를 찾지 못했습니다.", flush=True)  # noqa: T201
                return
            start, end = int(rng[0]), int(rng[1])
            self._usd_anim_auto_range_text.set_value(f"AUTO RANGE: {start} ~ {end}")
        else:
            start = self._usd_anim_start_frame.get_value_as_int()
            end = self._usd_anim_end_frame.get_value_as_int()
        usd_animation_control.play_usd_animation(
            start_frame=start,
            end_frame=end,
            loop=loop,
            on_completed=(lambda: print(f"[USD ANIM] 완료: {start}~{end}", flush=True)) if not loop else None,
        )

    def _on_play_generator_sample(self) -> None:
        """JSON 샘플을 읽어 파서로 파싱 후 가상 시그널 애니메이션 재생."""
        parsed = parse_signal(SAMPLE_GENERATOR_JSON, "json")
        if parsed:
            self._run_generator_from_parsed(parsed)

    def receive_signal_data(self, data: str, format: str = "json") -> bool:
        """장비로부터 수신한 시그널 데이터를 파싱하여 애니메이션 자동 실행. JSON/XML."""
        parsed = parse_signal(data, format)
        if not parsed:
            return False
        self._run_generator_from_parsed(parsed)
        return True

    def _run_generator_from_parsed(self, parsed: dict) -> None:
        """파서가 반환한 공통 구조 {"objects": [...], "segments": [...]}로 세그먼트 애니메이션 적용. 동일 이름 전부 적용."""
        stage = _get_stage()
        if not stage:
            return
        objects = parsed.get("objects") or []
        segments = parsed.get("segments") or []
        if not objects or not segments:
            return
        for name in objects:
            if not isinstance(name, str):
                continue
            paths = _find_all_prim_paths_by_name(stage, name)
            for path in paths:
                if not path:
                    continue
                stop_prim_translate_animation(path)
                stop_prim_curve_animation(path)
                run_prim_translate_animation(path, segments, loop=False)

    async def _on_load_usd(self) -> None:
        """stat_async(path) 검증 후 open_stage(path). 경로는 resource 콤보 선택 우선, 없으면 입력 필드."""
        path = self._get_load_path()
        self._load_status_label.text = ""

        if not path or not _path_has_supported_stage_extension(path):
            self._load_status_label.text = "Error: Invalid URL or File extension."
            return

        try:
            result, _ = await asyncio.wait_for(
                omni.client.stat_async(path), timeout=1.5
            )
            if result != omni.client.Result.OK:
                self._load_status_label.text = "Error: This URL does not exist."
                return
        except Exception:
            self._load_status_label.text = "Error: Connection timeout (Wrong Domain)."
            return

        self._load_status_label.text = "로드 중..."
        omni.usd.get_context().open_stage(path)
        self._load_status_label.text = "로드 완료. TBS 제어창에서 '목록 새로고침'을 눌러 주세요."

    def _on_refresh_prim_list(self) -> None:
        """현재 스테이지에서 prim 경로를 수집하고 제어창 목록만 갱신. (뷰포트 frame 호출 없음 → 대형 씬에서 GPU 버퍼 과다 사용 방지)"""
        stage = _get_stage()
        if not stage:
            if self._load_status_label:
                self._load_status_label.text = "스테이지가 없습니다. USD를 먼저 로드하세요."
            return
        self._tracked_paths = _collect_prim_paths_safe(stage)
        self._refresh_object_list()

    def _refresh_object_list(self) -> None:
        if self._object_list_frame is None:
            return
        self._object_list_frame.clear()
        stage = _get_stage()
        if not stage:
            with self._object_list_frame:
                ui.Label("USD를 먼저 로드하세요.")
            return

        def _valid_path(p: str) -> bool:
            try:
                return stage.GetPrimAtPath(p).IsValid()
            except (UnicodeDecodeError, UnicodeEncodeError):
                return False

        valid_paths = [p for p in self._tracked_paths if _valid_path(p)]
        total = len(valid_paths)
        priority_prefix = (
            self._priority_prefix_model.get_value_as_string().strip()
            if getattr(self, "_priority_prefix_model", None)
            else ""
        )

        if priority_prefix:
            priority_paths: List[str] = []
            rest_paths: List[str] = []
            for p in valid_paths:
                try:
                    prim = stage.GetPrimAtPath(p)
                    if not prim or not prim.IsValid():
                        rest_paths.append(p)
                        continue
                    name = safe_str(prim.GetName())
                    if name.startswith(priority_prefix):
                        priority_paths.append(p)
                    else:
                        rest_paths.append(p)
                except Exception:
                    rest_paths.append(p)
            need = max(0, MAX_PRIMS_DISPLAY - len(priority_paths))
            display_paths = priority_paths[:MAX_PRIMS_DISPLAY] + rest_paths[:need]
        else:
            display_paths = valid_paths[:MAX_PRIMS_DISPLAY]

        with self._object_list_frame:
            if total > MAX_PRIMS_DISPLAY:
                ui.Label(
                    f"총 {total}개 prim 중 {len(display_paths)}개만 표시됩니다. (창/GPU 부담 방지)",
                    height=0,
                )
                ui.Spacer(height=4)
            if priority_prefix:
                n_priority = min(len(priority_paths), MAX_PRIMS_DISPLAY)
                n_rest = len(display_paths) - n_priority
                ui.Label(
                    f"접두사 '{priority_prefix}' 우선: {n_priority}개, 나머지 순서대로 {n_rest}개",
                    height=0,
                )
                ui.Spacer(height=4)
            for idx, prim_path in enumerate(display_paths):
                self._build_object_panel(self._object_list_frame, prim_path, idx + 1)

    def _build_object_panel(self, parent: ui.VStack, prim_path: str, index: int) -> None:
        """드롭다운 한 칸: displayName/title/name으로 제목 표시, Position X/Y/Z, button_0/1."""
        try:
            stage = _get_stage()
            prim = stage.GetPrimAtPath(prim_path) if stage else None
            if not prim or not prim.IsValid():
                return
            title = get_prim_display_name(prim, index)
            local = _get_prim_local_translate(prim)
            pos_models = [
                ui.SimpleFloatModel(local[0]),
                ui.SimpleFloatModel(local[1]),
                ui.SimpleFloatModel(local[2]),
            ]

            def update_prim_position():
                s = _get_stage()
                p = s.GetPrimAtPath(prim_path) if s else None
                if p and p.IsValid():
                    _set_prim_translate_only(p, Gf.Vec3f(
                        pos_models[0].get_value_as_float(),
                        pos_models[1].get_value_as_float(),
                        pos_models[2].get_value_as_float(),
                    ))

            with parent:
                with ui.CollapsableFrame(title, collapsed=False):
                    with ui.VStack(spacing=6):
                        ui.Label("Position (X, Y, Z)", height=0)
                        with ui.HStack():
                            for i, label in enumerate(["X", "Y", "Z"]):
                                ui.Label(label, width=24)
                                ui.FloatField(model=pos_models[i])
                        for m in pos_models:
                            m.add_value_changed_fn(lambda _: update_prim_position())
                        ui.Spacer(height=4)
                        ui.Button(
                            "3D 정보 보기",
                            height=24,
                            clicked_fn=lambda p=prim_path: self._show_prim_info_in_viewport(p),
                        )
                        ui.Spacer(height=4)
                        with ui.HStack(spacing=8):
                            ui.Button("button_0", width=0, clicked_fn=lambda p=prim_path: self._on_button_0(p))
                            ui.Button("button_1", width=0, clicked_fn=lambda p=prim_path: self._on_button_1(p))
                            ui.Button("button_2", width=0, clicked_fn=lambda p=prim_path: self._on_button_2(p))
        except (UnicodeDecodeError, UnicodeEncodeError):
            return

    def _on_button_0(self, prim_path: str) -> None:
        """x축 100 → z축 100 순차 이동 (1초씩). 같은 객체 기존 애니메이션은 즉시 중단 후 현재 위치부터."""
        stop_prim_translate_animation(prim_path)
        stop_prim_curve_animation(prim_path)
        stop_prim_rotate_animation(prim_path)
        run_prim_translate_animation(
            prim_path,
            [
                {"duration": 1.0, "delta": (100.0, 0.0, 0.0)},
                {"duration": 1.0, "delta": (0.0, 0.0, 100.0)},
            ],
            loop=False,
        )

    def _on_button_1(self, prim_path: str) -> None:
        """x축 100만큼 포물선 곡선 이동 (1초). 같은 객체 기존 애니메이션 즉시 중단 후 현재 위치부터."""
        stop_prim_translate_animation(prim_path)
        stop_prim_curve_animation(prim_path)
        stop_prim_rotate_animation(prim_path)
        stage = _get_stage()
        prim = stage.GetPrimAtPath(prim_path) if stage else None
        if not prim or not prim.IsValid():
            return
        start = _get_prim_local_translate(prim)
        start_t = (start[0], start[1], start[2])
        end_t = (start[0] + 100.0, start[1], start[2])
        path_points = make_parabolic_path(start=start_t, end=end_t, arc_height=30.0, num_points=24)
        run_prim_curve_animation(prim_path, path_points, duration_sec=1.0, loop=False)

    def _on_button_2(self, prim_path: str) -> None:
        """예시: 3초 동안 Y축으로 90도 회전. 같은 객체 기존 애니메이션 즉시 중단 후 현재 회전부터."""
        stop_prim_translate_animation(prim_path)
        stop_prim_curve_animation(prim_path)
        stop_prim_rotate_animation(prim_path)
        run_prim_rotate_animation(
            prim_path,
            [
                {"duration": 3.0, "delta": (0.0, 90.0, 0.0)},
            ],
            loop=False,
        )
