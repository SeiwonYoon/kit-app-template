# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
[measure_control_1 — USD 로드 및 prim 제어]
1. 확장 시작 시 하나의 USD를 로드합니다. path가 외부 경로면 그 경로에서, resource 내부 경로면 resource 폴더에서 로드합니다.
2. 로드된 USD 안의 모든 prim이 UI 창에 드롭다운으로 나열되고, 각 prim별로 X/Y/Z 좌표 표시·수정으로 이동할 수 있습니다.
3. 각 객체마다 move_0(1초 동안 x축 3 이동), move_1(포물선 곡선 이동) 버튼이 있습니다.
"""

import asyncio
import os
from typing import Optional, List

import omni.ext
import omni.ui as ui
import omni.usd as ou
from pxr import Gf, Sdf, Usd, UsdGeom

# ---------------------------------------------------------------------------
# [PyAnsys 연동]
# ansys_simulation(및 ansys-mapdl-core)은 on_startup에서 pip 설치 직후에 불러요.
# 그래서 extension.toml의 [python.pipapi]로 설치한 패키지를 확실히 쓸 수 있어요.
# 전역으로 "ANSYS 관리자"만 둡니다. 타입은 런타임에 로드되는 클래스라서 Any로 둡니다.
# ---------------------------------------------------------------------------
_ansys_manager = None  # AnsysSimulationManager 인스턴스 또는 None


# -----------------------------------------------------------------------------
# [오브젝트에 붙는 "이름"과 "기준 숫자"]
# 각 3D 오브젝트(prim)에는 temperature, pressure, baseScale 같은 "속성"이 붙어요.
# UI에서 바꾼 값이 이 이름으로 저장되고, 시뮬레이션 규칙(색 바꾸기, 휨)에서 읽어 써요.
# -----------------------------------------------------------------------------
ATTR_TEMPERATURE = "temperature"   # 온도 속성 이름
ATTR_PRESSURE = "pressure"         # 압력 속성 이름
ATTR_BASE_SCALE = "baseScale"      # "원래 크기" — 휨 계산할 때 기준이 되는 스케일

DEFAULT_TEMP = 0.0
DEFAULT_PRESSURE = 0.0

# [온도 → 색상 규칙]
# 0도 미만: 파란색 / 0~30도: 회색 / 30도 초과: 빨간색
TEMP_LOW_THRESHOLD = 0.0
TEMP_HIGH_THRESHOLD = 30.0
LOW_TEMP_COLOR = Gf.Vec3f(0.0, 0.0, 1.0)   # 파랑 (R,G,B)
DEFAULT_COLOR = Gf.Vec3f(0.7, 0.7, 0.7)    # 회색
HIGH_TEMP_COLOR = Gf.Vec3f(1.0, 0.0, 0.0)  # 빨강

# [압력 → 휨 규칙]
# ANSYS 쓸 때: pressure > 0 이면 run_simulation() 부르고 결과를 scale에 반영해요.
# ANSYS 안 쓸 때: pressure > 100 이면 단순 공식(scale_x 키우고 scale_y 줄이기)으로 휨 표현해요.
PRESSURE_THRESHOLD_ANSYS = 0.0       # ANSYS 사용 기준 (0보다 크면 해석 실행)
PRESSURE_THRESHOLD_FALLBACK = 100.0  # 단순 규칙 기준 (이 값 넘으면 휨 적용)
PRESSURE_BEND_RANGE = 100.0          # 100 초과분을 이걸로 나눠서 0~1 비율(t) 만듦
PRESSURE_SCALE_Y_MAX = 0.8           # 최대 휨일 때 Y 스케일 (작아짐)
PRESSURE_SCALE_X_MAX = 1.1           # 최대 휨일 때 X 스케일 (커짐)

# [시작 시 USD 로드] 동일 함수 load_usd_by_path(stage, path, ext_id) 로 처리.
# - path가 None/빈 문자열: 로드하지 않음.
# - 외부 path (절대 경로 또는 carb 토큰): 해당 경로의 USD 로드. 예: "C:/data/scene.usd", "${root}/data/sample.usd"
# - 내부 path (resource): resource 폴더 안의 USD 로드. 예: "resource/scene.usd", "scene.usd"
# 현재 resource에 usd가 없으므로 외부 경로를 넣어 두면 시작 시 그 경로로 로드됩니다.
STARTUP_USD_PATH: Optional[str] = None  # 필요 시 예: "C:/path/to/your.scene.usd" 또는 "resource/내파일.usd"


# =============================================================================
# [스테이지·USD 로드 — 공통]
# =============================================================================

def _get_stage():
    """
    [쉽게] 지금 열려 있는 "3D 세상(USD 스테이지)"을 가져와요.
    스테이지가 없으면 None. 큐브/구 만들기, USD 로드할 때 "세상이 있어?" 확인할 때 써요.
    """
    ctx = ou.get_context()
    return ctx.get_stage() if ctx else None


def _ensure_world_prim(stage):
    """
    [쉽게] "/World"라는 이름의 "뿌리 오브젝트"가 있게 해요. 없으면 하나 만들어요.
    큐브, 구, 불러온 USD는 전부 /World 아래에 붙어요 (예: /World/Cube_0, /World/Sphere_0).
    """
    world = stage.GetPrimAtPath("/World")
    if not world.IsValid():
        stage.DefinePrim("/World", "Xform")
    return stage.GetPrimAtPath("/World")


def _next_index(stage, prefix):
    """/World/{prefix}_0, _1, ... 중 비어 있는 인덱스를 반환합니다."""
    i = 0
    while stage.GetPrimAtPath(f"/World/{prefix}_{i}").IsValid():
        i += 1
    return i


def load_usd(stage, usd_file_path):
    """USD 파일을 참조로 /World/Loaded_0, Loaded_1, ... 에 넣습니다."""
    if not stage or not usd_file_path:
        return None
    _ensure_world_prim(stage)
    idx = _next_index(stage, "Loaded")
    path_str = f"/World/Loaded_{idx}"
    prim = stage.OverridePrim(path_str)
    if not prim:
        return None
    prim.GetReferences().AddReference(usd_file_path)
    prim = stage.GetPrimAtPath(path_str)
    return path_str if prim.IsValid() else None


def get_resource_dir(ext_id: Optional[str] = None) -> Optional[str]:
    """
    최상단 resource 폴더 경로를 반환합니다.
    먼저 프로젝트 루트(${root})/resource 를 찾고, 없으면 확장 data 폴더를 씁니다.
    """
    try:
        import carb
        tokens = carb.tokens.get_tokens_interface()
        root = tokens.resolve("${root}")
        if root:
            resource = os.path.join(root, "resource")
            if os.path.isdir(resource):
                return resource
        if ext_id:
            em = omni.kit.app.get_app().get_extension_manager()
            ext_path = em.get_extension_path(ext_id)
            if ext_path:
                for sub in ("resource", "data", "data/resource"):
                    candidate = os.path.join(ext_path, sub)
                    if os.path.isdir(candidate):
                        return candidate
    except Exception:
        pass
    return None


def list_usd_in_dir(dir_path: str) -> List[str]:
    """디렉터리 안의 .usd, .usda 파일 경로 리스트를 반환합니다."""
    if not dir_path or not os.path.isdir(dir_path):
        return []
    out = []
    for name in sorted(os.listdir(dir_path)):
        low = name.lower()
        if low.endswith(".usd") or low.endswith(".usda"):
            out.append(os.path.join(dir_path, name))
    return out


def collect_all_prim_paths(stage, root_prim_path: str) -> List[str]:
    """
    로드된 USD 루트 아래의 모든 prim(Xform/Gprim/Scope) 경로를 수집합니다.
    UI에서 각 prim을 드롭다운으로 표시할 때 사용합니다.
    """
    root = stage.GetPrimAtPath(root_prim_path)
    if not root or not root.IsValid():
        return []
    paths = [root_prim_path]

    def visit(prim, base_path: str) -> None:
        for child in prim.GetChildren():
            child_path = base_path + "/" + child.GetName()
            if child.IsA(UsdGeom.Xform) or child.IsA(UsdGeom.Gprim) or child.GetTypeName() == "Scope":
                paths.append(child_path)
            visit(child, child_path)

    visit(root, root_prim_path)
    return paths


def _is_internal_path(path: str) -> bool:
    """path가 resource 내부 경로인지 판별 (resource/ 로 시작하거나 절대경로가 아님)."""
    if not path or not path.strip():
        return False
    p = path.strip().replace("\\", "/")
    if p.startswith("resource/") or p.startswith("resource"):
        return True
    if os.path.isabs(p):
        return False
    if len(p) > 1 and p[1] == ":":
        return False
    return True


def load_usd_by_path(stage, path: Optional[str], ext_id: str):
    """
    하나의 함수로 외부 path / 내부(resource) path 모두 처리합니다.
    - path가 None이거나 빈 문자열: 로드하지 않음, (None, []) 반환.
    - path가 내부 경로(resource/파일.usd 또는 파일.usd): get_resource_dir(ext_id) 기준으로 resource 폴더 안의 USD 로드.
    - path가 외부 경로(절대 경로 등): 해당 경로의 USD 파일을 로드 (show_info 등에서 쓰는 외부 path와 동일한 방식).
    반환: (root_prim_path, [모든 prim 경로]) — 실패 시 (None, []).
    """
    if not path or not path.strip():
        return None, []
    path = path.strip().replace("\\", "/")
    usd_file_path = None
    if _is_internal_path(path):
        resource_dir = get_resource_dir(ext_id)
        if not resource_dir:
            return None, []
        name = path.split("/")[-1] if "resource" in path.split("/")[0].lower() else path
        if name == path and "/" not in path:
            name = path
        else:
            name = path.replace("resource/", "").replace("resource", "").lstrip("/")
        usd_file_path = os.path.join(resource_dir, name)
        if not os.path.isfile(usd_file_path):
            candidates = list_usd_in_dir(resource_dir)
            usd_file_path = candidates[0] if candidates else None
    else:
        try:
            import carb
            usd_file_path = carb.tokens.get_tokens_interface().resolve(path)
            if not usd_file_path or not os.path.isfile(usd_file_path):
                usd_file_path = path
        except Exception:
            usd_file_path = path
        if not os.path.isfile(usd_file_path):
            return None, []
    root_path = load_usd(stage, usd_file_path)
    if not root_path:
        return None, []
    prim_paths = collect_all_prim_paths(stage, root_path)
    return root_path, prim_paths


def apply_simulation_rules(prim):
    """
    [쉽게] 이 오브젝트의 "온도"와 "압력" 값을 읽어서,
    1) 온도에 따라 색을 바꾸고 (차가우면 파랑, 보통 회색, 뜨거우면 빨강)
    2) 압력에 따라 "휘어 보이게" 크기(scale)를 바꿔요.

    [PyAnsys가 여기서 어떻게 쓰이나요]
    - 압력이 0보다 크고, ANSYS가 켜져 있으면(_ansys_manager가 있고 _available):
      _ansys_manager.run_simulation(pressure, temperature) 를 불러요.
      → ANSYS가 블록 만들어서 힘 넣고 해석하고 "Y방향 변위"를 돌려줘요.
      그 다음 _ansys_manager.apply_result_to_prim(prim, deformation, base_scale) 로
      그 변위를 오브젝트의 "크기"에 반영해서 휘어 보이게 해요.
    - ANSYS를 못 쓰면: 압력이 100 넘을 때만, 간단한 공식(scale_x 키우고 scale_y 줄이기)으로 휨을 표현해요.
    """
    global _ansys_manager
    if not prim or not prim.IsValid():
        return

    # ----- 규칙 1: 온도 → 색상 (PyAnsys와 무관, 그냥 prim의 temperature 값으로 색만 바꿈) -----
    gprim = UsdGeom.Gprim(prim)
    if gprim:
        temp_attr = prim.GetAttribute(ATTR_TEMPERATURE)
        temp = float(temp_attr.Get()) if temp_attr else DEFAULT_TEMP
        color_attr = gprim.CreateDisplayColorAttr()
        if temp < TEMP_LOW_THRESHOLD:
            color_attr.Set([LOW_TEMP_COLOR])
        elif temp > TEMP_HIGH_THRESHOLD:
            color_attr.Set([HIGH_TEMP_COLOR])
        else:
            color_attr.Set([DEFAULT_COLOR])

    # ----- 규칙 2: 압력 → 휨 (여기서 PyAnsys 사용 여부가 갈려요) -----
    pressure_attr = prim.GetAttribute(ATTR_PRESSURE)
    pressure = float(pressure_attr.Get()) if pressure_attr else DEFAULT_PRESSURE
    base_attr = prim.GetAttribute(ATTR_BASE_SCALE)
    base_scale = base_attr.Get() if base_attr else Gf.Vec3d(1, 1, 1)
    if base_scale is None:
        base_scale = Gf.Vec3d(1, 1, 1)

    # 이 오브젝트의 "크기(Scale)"를 바꿀 수 있게 xformOp을 찾거나 추가해요.
    xform = UsdGeom.Xformable(prim)
    if not xform:
        return
    scale_op = None
    for op in xform.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeScale:
            scale_op = op
            break
    if not scale_op:
        scale_op = xform.AddScaleOp()

    # [PyAnsys 사용 경로] ANSYS가 있고, 압력이 0보다 크면 → ANSYS한테 해석 시키고 결과를 scale에 반영해요.
    if _ansys_manager is not None and _ansys_manager._available and pressure > PRESSURE_THRESHOLD_ANSYS:
        temp_attr = prim.GetAttribute(ATTR_TEMPERATURE)
        temperature = float(temp_attr.Get()) if temp_attr else DEFAULT_TEMP
        # 여기서 PyAnsys(ANSYS)가 실제로 돌아요: pressure, temperature 넣고 변위 받아요.
        deformation = _ansys_manager.run_simulation(pressure, temperature)
        # 받은 변위를 이 prim의 scale로 바꿔서 휘어 보이게 해요.
        _ansys_manager.apply_result_to_prim(prim, deformation, base_scale)
    elif pressure > PRESSURE_THRESHOLD_FALLBACK:
        # [단순 규칙] ANSYS 없을 때: 압력 100 넘으면 공식으로 scale_x 키우고 scale_y 줄여서 휨처럼 보이게 해요.
        excess = pressure - PRESSURE_THRESHOLD_FALLBACK
        t = min(1.0, excess / PRESSURE_BEND_RANGE)
        scale_x = base_scale[0] * (1.0 + (PRESSURE_SCALE_X_MAX - 1.0) * t)
        scale_y = base_scale[1] * (1.0 - (1.0 - PRESSURE_SCALE_Y_MAX) * t)
        scale_op.Set(Gf.Vec3d(scale_x, scale_y, base_scale[2]))
    else:
        # 압력이 기준 이하: 휨 없이 원래 크기(base_scale)만 써요.
        scale_op.Set(base_scale)


def _frame_prim_in_viewport(prim_path: str) -> None:
    """
    [쉽게] 3D 화면(뷰포트)의 카메라를 "이 오브젝트가 보이게" 맞춰 줘요.
    큐브/구 만들기나 USD 불러온 직후에 부르면, 새로 만든 게 화면 한가운데 보여요.
    PyAnsys와는 무관해요. 그냥 카메라 위치만 바꾸는 기능이에요.
    """
    if not prim_path:
        return
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


def _get_prim_transform(prim):
    """prim의 월드 위치(translate)와 스케일을 반환합니다."""
    translate = Gf.Vec3f(0, 0, 0)
    scale = Gf.Vec3d(1, 1, 1)
    if not prim or not prim.IsValid():
        return translate, scale
    if prim.HasAttribute(ATTR_BASE_SCALE):
        scale = prim.GetAttribute(ATTR_BASE_SCALE).Get() or scale
    xform = UsdGeom.Xformable(prim)
    if xform:
        world = xform.ComputeLocalToWorldTransform(0)
        translate = Gf.Vec3f(world.ExtractTranslation())
        if not prim.HasAttribute(ATTR_BASE_SCALE):
            scale = world.ExtractScale()
    return translate, scale


def _get_prim_local_translate(prim) -> Gf.Vec3f:
    """prim의 로컬 translate(op) 값을 반환합니다. 애니메이션/이동 시 사용."""
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


def _set_prim_translate_only(prim, position: Gf.Vec3f) -> None:
    """prim의 translate op만 설정합니다 (scale 등은 건드리지 않음)."""
    if not prim or not prim.IsValid():
        return
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


# =============================================================================
# [확장의 "진입점" — Extension 클래스]
# Kit이 이 확장을 켤 때 on_startup을 부르고, 끌 때 on_shutdown을 불러요.
# 여기서 "창 띄우기", "ANSYS 한 번만 켜기", "버튼/목록 UI"를 모두 처리해요.
# PyAnsys는 on_startup에서 한 번만 켜고(_ansys_manager.initialize_solver()),
# on_shutdown에서 끄고(_ansys_manager.shutdown()), 실제 해석은 apply_simulation_rules 안에서 불러요.
# =============================================================================

class Extension(omni.ext.IExt):
    """
    [쉽게] 이 확장의 "메인"이에요. Kit이 확장을 켜면 이 클래스의 on_startup이 실행되고,
    창이 뜨고, 버튼을 누르면 큐브/구 만들기·USD 로드·속성 편집이 되고,
    온도/압력 값이 바뀔 때마다 apply_simulation_rules가 불리면서 (필요하면 PyAnsys로 휨 계산이 돼요).
    """

    def on_startup(self, ext_id):
        """
        [쉽게] 확장이 "켜질 때" 딱 한 번 불러요.
        1) ANSYS를 "한 번만" 켜요 (PyAnsys: launch_mapdl). 여러 번 켜면 안 되니까 여기서만 켜요.
        2) 추적할 오브젝트 목록, 창, UI 목록을 비워 두고
        3) _build_window()로 "Measure Control Simulation" 창을 만들어요.
        """
        global _ansys_manager
        self._tracked_paths = []
        self._ext_id = ext_id
        self._window = None
        self._object_list_frame = None
        try:
            import omni.kit.pipapi
            omni.kit.pipapi.install("ansys-mapdl-core")
        except Exception as e:
            print(f"[measure_control_1] pip install 실패: {e}")
        try:
            from .ansys_simulation import AnsysSimulationManager
            _ansys_manager = AnsysSimulationManager()
            _ansys_manager.initialize_solver()
        except Exception as e:
            print(f"[measure_control_1] PyAnsys 로드/초기화 실패: {e}")
            _ansys_manager = None
        self._build_window()
        if STARTUP_USD_PATH:
            self._schedule_startup_usd_load()

    def _schedule_startup_usd_load(self):
        """한 프레임 뒤 스테이지를 확보한 뒤 STARTUP_USD_PATH로 USD를 로드합니다."""
        def do_load():
            stage = _get_stage()
            if not stage:
                return
            root_path, prim_paths = load_usd_by_path(stage, STARTUP_USD_PATH, self._ext_id)
            if not root_path:
                return
            self._tracked_paths.clear()
            self._tracked_paths.extend(prim_paths)
            self._refresh_object_list()
            _frame_prim_in_viewport(root_path)

        async def next_tick():
            await omni.kit.app.get_app().next_update_async()
            do_load()

        asyncio.ensure_future(next_tick())

    def on_shutdown(self):
        global _ansys_manager
        from .translate_animation import stop_prim_translate_animation
        from .curve_animation import stop_prim_curve_animation
        for path in list(self._tracked_paths):
            stop_prim_translate_animation(path)
            stop_prim_curve_animation(path)
        self._tracked_paths.clear()
        if _ansys_manager is not None:
            _ansys_manager.shutdown()
            _ansys_manager = None
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self._object_list_frame = None

    def _build_window(self):
        """USD 로드 시 나오는 prim 목록 + Animation 확장 테스트 영역."""
        self._window = ui.Window(
            title="Measure Control Simulation",
            width=420,
            height=400,
            padding_x=0,
            padding_y=0,
        )
        with self._window.frame:
            with ui.VStack(spacing=0, style={"margin": 0, "padding": 0}):
                # Animation Clips / Timeline 확장 테스트 (접이식)
                with ui.CollapsableFrame("Animation 확장 테스트", collapsed=True):
                    with ui.VStack(spacing=6):
                        self._anim_status_label = ui.Label("확인 중...", height=0)
                        ui.Button("Animation·타임라인 상태 새로고침", height=28, clicked_fn=self._on_refresh_animation_status)
                ui.Spacer(height=4)
                with ui.ScrollingFrame(style={"ScrollingFrame": {"padding": 0, "margin": 0}}):
                    self._object_list_frame = ui.VStack(height=0, alignment=ui.Alignment.LEFT_TOP)
        self._refresh_object_list()
        self._on_refresh_animation_status()

    def _refresh_object_list(self):
        """로드된 USD의 prim 목록을 드롭다운(접이식) 패널로 다시 그립니다."""
        if self._object_list_frame is None:
            return
        self._object_list_frame.clear()
        stage = _get_stage()
        if not stage:
            return
        self._tracked_paths[:] = [p for p in self._tracked_paths if stage.GetPrimAtPath(p).IsValid()]
        with self._object_list_frame:
            for path in self._tracked_paths:
                self._build_object_panel(self._object_list_frame, path)

    def _build_object_panel(self, parent, prim_path):
        """
        로드된 USD의 prim 하나당 접이식 칸 하나. X/Y/Z 좌표 표시·수정, move_0(1초 동안 x+3), move_1(포물선 이동).
        """
        stage = _get_stage()
        prim = stage.GetPrimAtPath(prim_path) if stage else None
        if not prim or not prim.IsValid():
            return
        name = prim.GetName()
        # 로컬 translate로 표시·편집 (수정 시 즉시 이동)
        local = _get_prim_local_translate(prim)
        pos_models = [
            ui.SimpleFloatModel(local[0]),
            ui.SimpleFloatModel(local[1]),
            ui.SimpleFloatModel(local[2]),
        ]

        def update_prim_position(model=None):
            stage = _get_stage()
            p = stage.GetPrimAtPath(prim_path) if stage else None
            if p and p.IsValid():
                _set_prim_translate_only(p, Gf.Vec3f(
                    pos_models[0].get_value_as_float(),
                    pos_models[1].get_value_as_float(),
                    pos_models[2].get_value_as_float(),
                ))

        with parent:
            with ui.CollapsableFrame(name, collapsed=False):
                with ui.VStack(spacing=6):
                    ui.Label("Position (X, Y, Z)", height=0)
                    with ui.HStack():
                        for i, label in enumerate(["X", "Y", "Z"]):
                            ui.Label(label, width=24)
                            ui.FloatField(model=pos_models[i])
                    for m in pos_models:
                        m.add_value_changed_fn(update_prim_position)

                    ui.Spacer(height=4)
                    with ui.HStack(spacing=8):
                        ui.Button("move_0", width=0, clicked_fn=lambda p=prim_path: self._on_move_0(p))
                        ui.Button("move_1", width=0, clicked_fn=lambda p=prim_path: self._on_move_1(p))

    def _on_move_0(self, prim_path: str):
        """move_0: 해당 객체를 x축으로 3만큼 1초 동안 이동."""
        from .translate_animation import run_prim_translate_animation
        run_prim_translate_animation(prim_path, [{"duration": 1.0, "delta": (3.0, 0.0, 0.0)}], loop=False)

    def _on_move_1(self, prim_path: str):
        """move_1: 해당 객체가 포물선 곡선을 그리며 이동."""
        stage = _get_stage()
        prim = stage.GetPrimAtPath(prim_path) if stage else None
        if not prim or not prim.IsValid():
            return
        from .curve_animation import make_parabolic_path, run_prim_curve_animation
        start = _get_prim_local_translate(prim)
        start_t = (start[0], start[1], start[2])
        end_t = (start[0] + 5.0, start[1], start[2] + 2.0)
        path_points = make_parabolic_path(start=start_t, end=end_t, arc_height=2.0, num_points=24)
        run_prim_curve_animation(prim_path, path_points, duration_sec=2.0, loop=False)

    def _on_refresh_animation_status(self):
        """Animation Clips·Timeline 확장 사용 가능 여부와 타임라인 상태를 확인해 UI에 표시합니다."""
        lines = []
        # 1) omni.anim.clips 사용 가능 여부 (의존성으로 추가된 확장)
        try:
            import omni.anim.clips
            lines.append("[Animation Clips] omni.anim.clips: 로드됨 (클립 적용 등 사용 가능)")
        except ImportError:
            try:
                em = omni.kit.app.get_app().get_extension_manager()
                for ext_id in ("omni.anim.clips", "omni.anim.clips.bundle"):
                    try:
                        if em.is_extension_enabled(ext_id):
                            lines.append(f"[Animation Clips] {ext_id}: 활성화됨")
                            break
                    except Exception:
                        pass
                else:
                    lines.append("[Animation Clips] omni.anim.clips: 앱에 미포함 (USD Composer 등에서 사용 가능)")
            except Exception as e:
                lines.append(f"[Animation Clips] 확인 실패: {e}")

        # 2) omni.timeline 사용 가능 여부 및 현재 재생 시간
        try:
            import omni.timeline
            timeline = omni.timeline.get_timeline_interface()
            t = timeline.get_current_time()
            tps = timeline.get_time_codes_per_seconds()
            lines.append(f"[Timeline] 현재 시간: {t:.2f} sec (tps={tps})")
        except Exception as e:
            lines.append(f"[Timeline] 확인 실패: {e}")

        if self._anim_status_label:
            self._anim_status_label.text = "\n".join(lines) if lines else "확인할 수 없음"
