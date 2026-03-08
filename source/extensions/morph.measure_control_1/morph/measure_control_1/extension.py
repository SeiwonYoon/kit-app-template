# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
[이 확장이 하는 일 — 쉽게]
1. 창 하나를 띄워요. "큐브 만들기", "구 만들기", "USD 파일 불러오기" 버튼이 있어요.
2. 만든 오브젝트마다 "위치, 크기, 온도, 압력"을 적을 수 있는 칸이 나와요.
3. 온도에 따라 색이 바뀌어요 (차가우면 파랑, 보통이면 회색, 뜨거우면 빨강).
4. 압력을 넣으면 오브젝트가 "휘어" 보여요.
   - PyAnsys(ANSYS)를 쓸 수 있으면: ANSYS가 진짜 구조 해석을 해서 나온 변위만큼 휘어 보이게 해요.
   - ANSYS를 못 쓰면: 압력이 100 넘을 때만, 간단한 공식으로 휘어 보이게 해요.
"""

import asyncio
from typing import Optional

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


# =============================================================================
# [스테이지·오브젝트 만들기 — 일반 기능]
# 아래 함수들은 "3D 세상(스테이지)"에 오브젝트를 만들고, 속성을 붙이는 일만 해요.
# PyAnsys는 여기서는 안 써요. PyAnsys는 "압력/온도 값이 바뀌었을 때 휨 계산"할 때만 써요.
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
    """
    [쉽게] "Cube_0, Cube_1, ..." 처럼 이름을 붙일 때, 아직 안 쓰인 번호를 찾아요.
    예: Cube_0이 이미 있으면 1을 돌려줘서 Cube_1을 만들 수 있어요.
    """
    i = 0
    while stage.GetPrimAtPath(f"/World/{prefix}_{i}").IsValid():
        i += 1
    return i


def create_cube(stage):
    """
    [쉽게] 3D 세상에 "정육면체" 하나를 만들어요. 경로는 /World/Cube_0, Cube_1, ... 처럼 붙어요.
    만들면서 temperature, pressure, baseScale 속성도 같이 붙여요 (나중에 규칙에서 씀).
    """
    if not stage:
        return None
    _ensure_world_prim(stage)
    idx = _next_index(stage, "Cube")
    path_str = f"/World/Cube_{idx}"
    cube = UsdGeom.Cube.Define(stage, path_str)
    if not cube:
        return None
    prim = cube.GetPrim()
    _ensure_custom_attributes(prim)
    return path_str


def create_sphere(stage):
    """
    [쉽게] 3D 세상에 "구" 하나를 만들어요. 경로는 /World/Sphere_0, Sphere_1, ...
    역시 temperature, pressure, baseScale 속성을 붙여요.
    """
    if not stage:
        return None
    _ensure_world_prim(stage)
    idx = _next_index(stage, "Sphere")
    path_str = f"/World/Sphere_{idx}"
    sphere = UsdGeom.Sphere.Define(stage, path_str)
    if not sphere:
        return None
    prim = sphere.GetPrim()
    _ensure_custom_attributes(prim)
    return path_str


def _ensure_custom_attributes(prim):
    """
    [쉽게] 이 오브젝트에 "온도, 압력, 기준 크기" 칸이 없으면 만들어요.
    UI에서 적는 값이 여기 만든 속성에 저장되고, 시뮬레이션 규칙(색·휨)이 여기서 값을 읽어요.
    """
    if not prim or not prim.IsValid():
        return
    if not prim.HasAttribute(ATTR_TEMPERATURE):
        prim.CreateAttribute(ATTR_TEMPERATURE, Sdf.ValueTypeNames.Float).Set(DEFAULT_TEMP)
    if not prim.HasAttribute(ATTR_PRESSURE):
        prim.CreateAttribute(ATTR_PRESSURE, Sdf.ValueTypeNames.Float).Set(DEFAULT_PRESSURE)
    if not prim.HasAttribute(ATTR_BASE_SCALE):
        prim.CreateAttribute(ATTR_BASE_SCALE, Sdf.ValueTypeNames.Vector3d).Set(Gf.Vec3d(1, 1, 1))


def load_usd(stage, usd_file_path):
    """
    [쉽게] 사용자가 고른 USD 파일을 "참조"로 불러와서 /World/Loaded_0, Loaded_1, ... 아래에 넣어요.
    파일 안에 있는 3D 씬이 그대로 보이고, 이 prim에도 temperature, pressure, baseScale을 붙여요.
    """
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
    if prim.IsValid():
        _ensure_custom_attributes(prim)
    return path_str


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
    """
    [쉽게] 이 오브젝트의 "위치(X,Y,Z)"와 "크기(scale)"를 읽어서 돌려줘요.
    UI에 "위치·크기" 칸을 띄울 때, 현재 값을 채워 넣기 위해 써요. PyAnsys와는 무관해요.
    """
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
        # [PyAnsys] ansys-mapdl-core(및 psutil) 설치. psutil 5.x 호환은 ansys_simulation에서 net_connections 패치로 처리.
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

    def on_shutdown(self):
        """
        [쉽게] 확장이 "꺼질 때" 불러요.
        ANSYS를 끄고(_ansys_manager.shutdown()), 창을 없애고, 목록을 비워요.
        """
        global _ansys_manager
        self._tracked_paths.clear()
        if _ansys_manager is not None:
            _ansys_manager.shutdown()
            _ansys_manager = None
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self._object_list_frame = None

    def _build_window(self):
        """[쉽게] 'Measure Control Simulation' 창을 만들어요. 위에는 버튼 3개, 아래는 스크롤되는 오브젝트 목록이에요. PyAnsys는 여기서 안 써요."""
        self._window = ui.Window(
            title="Measure Control Simulation",
            width=420,
            height=400,
            padding_x=0,
            padding_y=0,
        )
        with self._window.frame:
            with ui.VStack(spacing=0, style={"margin": 0, "padding": 0}):
                # Button row: fixed height so no extra vertical space
                with ui.HStack(spacing=8, height=50):
                    ui.Button("Create Cube", height=50, clicked_fn=self._on_create_cube)
                    ui.Button("Create Sphere", height=50, clicked_fn=self._on_create_sphere)
                    ui.Button("Load USD", height=50, clicked_fn=self._on_load_usd)
                # List sticks to top of scroll area; no padding/margin
                with ui.ScrollingFrame(style={"ScrollingFrame": {"padding": 0, "margin": 0}}):
                    self._object_list_frame = ui.VStack(height=0, alignment=ui.Alignment.LEFT_TOP)
        self._refresh_object_list()

    def _on_create_cube(self):
        """[쉽게] 'Create Cube' 버튼을 누르면: 스테이지에 큐브 하나 만들고, 목록에 넣고, 카메라를 그 큐브에 맞춰요. PyAnsys 안 써요."""
        stage = _get_stage()
        path = create_cube(stage)
        if path:
            self._tracked_paths.append(path)
            self._refresh_object_list()
            _frame_prim_in_viewport(path)

    def _on_create_sphere(self):
        """[쉽게] 'Create Sphere' 버튼을 누르면: 구 하나 만들고, 목록에 넣고, 카메라를 그 구에 맞춰요. PyAnsys 안 써요."""
        stage = _get_stage()
        path = create_sphere(stage)
        if path:
            self._tracked_paths.append(path)
            self._refresh_object_list()
            _frame_prim_in_viewport(path)

    def _on_load_usd(self):
        """[쉽게] 'Load USD' 버튼을 누르면: 파일 고르는 창을 띄워요. 사용자가 파일을 고르고 적용하면, 그 USD를 /World/Loaded_0 같은 경로에 불러와요. PyAnsys 안 써요."""
        try:
            from omni.kit.window.filepicker import FilePickerDialog
        except ImportError:
            return
        stage = _get_stage()
        if not stage:
            return

        def on_apply(dialog, path):
            """파일 선택 후 'Load' 버튼 누르면: load_usd로 불러오고, 목록 갱신, 카메라를 그 오브젝트에 맞춰요."""
            if path:
                added = load_usd(stage, path)
                if added:
                    self._tracked_paths.append(added)
                    self._refresh_object_list()
                    _frame_prim_in_viewport(added)

        try:
            import carb.tokens
            start_dir = carb.tokens.get_tokens_interface().resolve("${root}/data")
        except Exception:
            start_dir = "."
        picker = FilePickerDialog(
            "Load USD from data",
            allow_multi_selection=False,
            apply_button_label="Load",
            click_apply_handler=on_apply,
        )
        picker.show(start_dir)

    def _refresh_object_list(self):
        """[쉽게] 아래쪽 "오브젝트 목록"을 다시 그려요. 이미 삭제된 건 빼고, 남은 건 하나씩 _build_object_panel로 접이식 패널을 만들어요. PyAnsys 안 써요."""
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
        [쉽게] 오브젝트 하나당 "접이식 칸" 하나를 만들어요. 제목은 오브젝트 이름(Cube_0, Sphere_0 등).
        안에는 "위치 X/Y/Z", "크기 X/Y/Z", "온도", "압력" 입력 칸이 있어요.
        사용자가 값을 바꾸면 → 그 값이 prim에 저장되고 → apply_simulation_rules(prim)이 불려요.
        그 안에서 온도면 색이 바뀌고, 압력이면 (PyAnsys 쓸 수 있으면) ANSYS 해석 후 휨이 적용돼요.
        """
        stage = _get_stage()
        prim = stage.GetPrimAtPath(prim_path) if stage else None
        if not prim or not prim.IsValid():
            return
        name = prim.GetName()
        translate, scale = _get_prim_transform(prim)
        temp_attr = prim.GetAttribute(ATTR_TEMPERATURE)
        pressure_attr = prim.GetAttribute(ATTR_PRESSURE)
        temp_val = float(temp_attr.Get()) if temp_attr else DEFAULT_TEMP
        pressure_val = float(pressure_attr.Get()) if pressure_attr else DEFAULT_PRESSURE

        # [UI 모델] 입력 칸의 "현재 값"을 담는 곳이에요. X,Y,Z 세 개씩 있어서 한 번에 읽어서 prim에 넣을 수 있어요.
        pos_models = [
            ui.SimpleFloatModel(translate[0]),
            ui.SimpleFloatModel(translate[1]),
            ui.SimpleFloatModel(translate[2]),
        ]
        scale_models = [
            ui.SimpleFloatModel(scale[0]),
            ui.SimpleFloatModel(scale[1]),
            ui.SimpleFloatModel(scale[2]),
        ]

        def update_prim_xform(model=None):
            """
            [쉽게] 사용자가 "위치"나 "크기" 칸에서 숫자를 바꾸면 이 함수가 불려요.
            칸에 적힌 값을 읽어서 prim의 위치·크기에 넣고, apply_simulation_rules(p)를 불러요.
            그 안에서 압력이 있으면 PyAnsys로 휨을 계산할 수도 있어요.
            """
            stage = _get_stage()
            p = stage.GetPrimAtPath(prim_path) if stage else None
            if not p or not p.IsValid():
                return
            xform = UsdGeom.Xformable(p)
            if not xform:
                return
            # UI에 적힌 "크기"를 baseScale에 저장해요. 나중에 압력 휨(또는 PyAnsys 결과) 계산할 때 기준이 돼요.
            base_scale = Gf.Vec3d(
                scale_models[0].get_value_as_float(),
                scale_models[1].get_value_as_float(),
                scale_models[2].get_value_as_float(),
            )
            if p.HasAttribute(ATTR_BASE_SCALE):
                p.GetAttribute(ATTR_BASE_SCALE).Set(base_scale)
            xform.ClearXformOpOrder()
            t = xform.AddTranslateOp()
            t.Set(Gf.Vec3f(pos_models[0].get_value_as_float(), pos_models[1].get_value_as_float(), pos_models[2].get_value_as_float()))
            s = xform.AddScaleOp()
            s.Set(base_scale)
            # 여기서 규칙 적용. ANSYS 켜져 있으면 run_simulation → apply_result_to_prim 로 휨이 들어가요.
            apply_simulation_rules(p)

        with parent:
            with ui.CollapsableFrame(name, collapsed=False):
                with ui.VStack(spacing=6):
                    ui.Label("Position", height=0)
                    with ui.HStack():
                        for i, label in enumerate(["X", "Y", "Z"]):
                            ui.Label(label, width=24)
                            ui.FloatField(model=pos_models[i])
                    for m in pos_models:
                        m.add_value_changed_fn(update_prim_xform)

                    ui.Spacer(height=2)
                    ui.Label("Scale", height=0)
                    with ui.HStack():
                        for i, label in enumerate(["X", "Y", "Z"]):
                            ui.Label(label, width=24)
                            ui.FloatField(model=scale_models[i])
                    for m in scale_models:
                        m.add_value_changed_fn(update_prim_xform)

                    ui.Spacer(height=2)
                    ui.Label("Temperature", height=0)
                    temp_model = ui.SimpleFloatModel(temp_val)
                    ui.FloatField(model=temp_model)

                    def on_temp_changed(model=None):
                        """[쉽게] '온도' 칸 값을 바꾸면 prim의 temperature에 저장하고 apply_simulation_rules 호출 → 색이 바뀌어요. PyAnsys는 온도로 색만 바꾸고 해석에는 지금은 안 써요."""
                        stage = _get_stage()
                        p = stage.GetPrimAtPath(prim_path) if stage else None
                        if p and p.IsValid():
                            a = p.GetAttribute(ATTR_TEMPERATURE)
                            if a:
                                a.Set(temp_model.get_value_as_float())
                                apply_simulation_rules(p)

                    temp_model.add_value_changed_fn(on_temp_changed)

                    ui.Label("Pressure", height=0)
                    pressure_model = ui.SimpleFloatModel(pressure_val)
                    ui.FloatField(model=pressure_model)

                    def on_pressure_changed(model=None):
                        """[쉽게] '압력' 칸 값을 바꾸면 prim의 pressure에 저장하고 apply_simulation_rules 호출 → 여기서 PyAnsys가 켜져 있으면 run_simulation으로 해석하고 휨이 적용돼요. 안 켜져 있으면 100 넘을 때만 단순 공식으로 휨 적용."""
                        stage = _get_stage()
                        p = stage.GetPrimAtPath(prim_path) if stage else None
                        if p and p.IsValid():
                            a = p.GetAttribute(ATTR_PRESSURE)
                            if a:
                                a.Set(pressure_model.get_value_as_float())
                                apply_simulation_rules(p)

                    pressure_model.add_value_changed_fn(on_pressure_changed)
