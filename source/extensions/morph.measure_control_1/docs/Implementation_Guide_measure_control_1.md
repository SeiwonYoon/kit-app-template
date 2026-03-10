# measure_control_1 확장 구현 가이드 (처음부터 현재까지)

이 문서는 **처음 버튼 3개(Create Cube, Create Sphere, Load USD)를 만드는 단계**부터 **현재 상태(온도/압력 규칙, PyAnsys 연동, psutil·MAPDL 호환 처리)**까지를 **순서대로** 따라 하면 그대로 구현할 수 있도록 정리한 가이드입니다.
초보자도 단계별로 폴더·파일을 만들고 수정하면 동일한 확장을 만들 수 있습니다.

---

## 사전 요구사항

- **Omniverse Kit 앱 템플릿**(kit-app-template) 프로젝트가 있고, 빌드 및 실행이 가능한 상태여야 합니다.
- **Python 3**와 **Omniverse Kit** 환경이 준비되어 있어야 합니다.
- 확장은 `source/extensions/morph.measure_control_1/` 아래에 두고, 앱에서 이 확장을 로드할 수 있어야 합니다.

---

## 전체 폴더 구조 (목표)

구현이 끝났을 때 구조는 다음과 같습니다.

```
source/extensions/morph.measure_control_1/
├── config/
│   └── extension.toml
├── data/
│   └── (icon.png, preview.png 등 필요 시)
├── docs/
│   ├── CHANGELOG.md
│   ├── Overview.md
│   ├── README.md
│   └── Implementation_Guide_measure_control_1.md  ← 이 문서
├── morph/
│   └── measure_control_1/
│       ├── __init__.py
│       ├── extension.py
│       ├── ansys_simulation.py
│       ├── (기존 service.py, core.py, ui_dummy.py, tests/ 등은 그대로 둠)
│       └── ...
├── premake5.lua
└── requirements-ansys.txt
```

---

## 1단계: 확장 기본 설정 (extension.toml, premake5.lua)

### 1-1. config/extension.toml

`source/extensions/morph.measure_control_1/config/extension.toml` 파일을 아래 내용으로 작성(또는 수정)합니다.

```toml
[package]
title = "measure control"
version = "0.1.0"
description = "Measure control extension scaffold with service/controller/ui split."
category = "Example"
changelog = "docs/CHANGELOG.md"
icon = "data/icon.png"
keywords = ["kit", "extension", "python", "ui"]
preview_image = "data/preview.png"
readme  = "docs/README.md"
repository = "https://github.com/NVIDIA-Omniverse/kit-app-template"

[dependencies]
"omni.kit.uiapp" = {}
"omni.ui" = {}
"omni.usd" = {}
"omni.kit.tool.measure" = {}
"omni.kit.window.filepicker" = {}
"omni.kit.viewport.utility" = {}
"omni.kit.pipapi" = {}

[settings]

# 확장 로드 전에 psutil과 ansys-mapdl-core를 Kit Python 환경에 pip 설치합니다.
[python.pipapi]
requirements = ["psutil", "ansys-mapdl-core"]
modules = ["psutil", "ansys.mapdl.core"]
use_online_index = true

[[python.module]]
name = "morph.measure_control_1"

[documentation]
pages = [
    "docs/Overview.md",
    "docs/CHANGELOG.md",
]

[[test]]
dependencies = [
    "omni.kit.test",
    "omni.kit.ui_test"
]
args = []
```

- **의존성**: `omni.kit.window.filepicker`(파일 선택), `omni.kit.viewport.utility`(카메라 프레이밍), `omni.kit.pipapi`(pip 설치)가 반드시 필요합니다.
- **pipapi**: 확장 로드 시 `psutil`, `ansys-mapdl-core`를 자동 설치합니다(네트워크 필요).

### 1-2. premake5.lua

`source/extensions/morph.measure_control_1/premake5.lua` 내용이 아래와 같은지 확인합니다.

```lua
local ext = get_current_extension_info()
project_ext (ext)
repo_build.prebuild_link {
    { "data", ext.target_dir.."/data" },
    { "docs", ext.target_dir.."/docs" },
    { "morph", ext.target_dir.."/morph" },
}
```

### 1-3. morph/measure_control_1/__init__.py

`morph/measure_control_1/__init__.py`에서 확장 진입점을 불러오도록 합니다.

```python
from .extension import *
```

---

## 2단계: extension.py — 창과 버튼 3개

`morph/measure_control_1/extension.py`를 새로 만들거나, 기존 내용을 지우고 아래부터 순서대로 작성합니다.

### 2-1. 상단 주석·import·전역

```python
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
[이 확장이 하는 일]
1. 창에 "큐브 만들기", "구 만들기", "USD 불러오기" 버튼 3개를 띄웁니다.
2. 만든 오브젝트마다 위치·크기·온도·압력을 편집할 수 있는 목록을 보여줍니다.
3. 온도에 따라 색이 바뀌고, 압력에 따라 휘어 보이게 합니다.
   - PyAnsys(ANSYS) 사용 가능 시: ANSYS 구조 해석 결과로 휨 표현.
   - 불가 시: 압력 100 초과 시 단순 공식으로 휨 표현.
"""

import asyncio
from typing import Optional

import omni.ext
import omni.ui as ui
import omni.usd as ou
from pxr import Gf, Sdf, Usd, UsdGeom

# ANSYS 관리자는 on_startup에서 생성합니다.
_ansys_manager = None
```

### 2-2. 상수 정의

```python
ATTR_TEMPERATURE = "temperature"
ATTR_PRESSURE = "pressure"
ATTR_BASE_SCALE = "baseScale"
DEFAULT_TEMP = 0.0
DEFAULT_PRESSURE = 0.0

TEMP_LOW_THRESHOLD = 0.0
TEMP_HIGH_THRESHOLD = 30.0
LOW_TEMP_COLOR = Gf.Vec3f(0.0, 0.0, 1.0)
DEFAULT_COLOR = Gf.Vec3f(0.7, 0.7, 0.7)
HIGH_TEMP_COLOR = Gf.Vec3f(1.0, 0.0, 0.0)

PRESSURE_THRESHOLD_ANSYS = 0.0
PRESSURE_THRESHOLD_FALLBACK = 100.0
PRESSURE_BEND_RANGE = 100.0
PRESSURE_SCALE_Y_MAX = 0.8
PRESSURE_SCALE_X_MAX = 1.1
```

### 2-3. Extension 클래스 — 창 + 버튼 3개만

```python
class Extension(omni.ext.IExt):
    def on_startup(self, ext_id):
        global _ansys_manager
        self._tracked_paths = []
        self._ext_id = ext_id
        self._window = None
        self._object_list_frame = None

        # PyAnsys: pip 설치 후 로드·초기화 (2단계에서는 생략 가능, 10~12단계에서 추가)
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
        self._window = ui.Window(
            title="Measure Control Simulation",
            width=420,
            height=400,
            padding_x=0,
            padding_y=0,
        )
        with self._window.frame:
            with ui.VStack(spacing=0, style={"margin": 0, "padding": 0}):
                with ui.HStack(spacing=8, height=50):
                    ui.Button("Create Cube", height=50, clicked_fn=self._on_create_cube)
                    ui.Button("Create Sphere", height=50, clicked_fn=self._on_create_sphere)
                    ui.Button("Load USD", height=50, clicked_fn=self._on_load_usd)
                with ui.ScrollingFrame(style={"ScrollingFrame": {"padding": 0, "margin": 0}}):
                    self._object_list_frame = ui.VStack(height=0, alignment=ui.Alignment.LEFT_TOP)
        self._refresh_object_list()

    def _on_create_cube(self):
        stage = _get_stage()
        path = create_cube(stage)
        if path:
            self._tracked_paths.append(path)
            self._refresh_object_list()
            _frame_prim_in_viewport(path)

    def _on_create_sphere(self):
        stage = _get_stage()
        path = create_sphere(stage)
        if path:
            self._tracked_paths.append(path)
            self._refresh_object_list()
            _frame_prim_in_viewport(path)

    def _on_load_usd(self):
        # 3단계에서 구현
        pass

    def _refresh_object_list(self):
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
        # 5단계에서 구현
        pass
```

이때 `_get_stage`, `create_cube`, `create_sphere`, `_frame_prim_in_viewport`는 아직 정의되지 않았으므로, **3·4단계**에서 추가합니다.
먼저 **3단계**의 스테이지·오브젝트 생성 함수들을 `Extension` 클래스 **위쪽**에 넣습니다.

---

## 3단계: 스테이지·오브젝트 생성 함수

`extension.py`에서 **상수 정의 바로 아래**, `class Extension` **위**에 다음 함수들을 추가합니다.

### 3-1. 스테이지·World·인덱스

```python
def _get_stage():
    ctx = ou.get_context()
    return ctx.get_stage() if ctx else None

def _ensure_world_prim(stage):
    world = stage.GetPrimAtPath("/World")
    if not world.IsValid():
        stage.DefinePrim("/World", "Xform")
    return stage.GetPrimAtPath("/World")

def _next_index(stage, prefix):
    i = 0
    while stage.GetPrimAtPath(f"/World/{prefix}_{i}").IsValid():
        i += 1
    return i
```

### 3-2. create_cube, create_sphere, 커스텀 속성

```python
def _ensure_custom_attributes(prim):
    if not prim or not prim.IsValid():
        return
    if not prim.HasAttribute(ATTR_TEMPERATURE):
        prim.CreateAttribute(ATTR_TEMPERATURE, Sdf.ValueTypeNames.Float).Set(DEFAULT_TEMP)
    if not prim.HasAttribute(ATTR_PRESSURE):
        prim.CreateAttribute(ATTR_PRESSURE, Sdf.ValueTypeNames.Float).Set(DEFAULT_PRESSURE)
    if not prim.HasAttribute(ATTR_BASE_SCALE):
        prim.CreateAttribute(ATTR_BASE_SCALE, Sdf.ValueTypeNames.Vector3d).Set(Gf.Vec3d(1, 1, 1))

def create_cube(stage):
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
```

### 3-3. load_usd

```python
def load_usd(stage, usd_file_path):
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
```

### 3-4. _on_load_usd 구현 (파일 선택)

`Extension` 클래스의 `_on_load_usd`를 아래로 교체합니다.

```python
def _on_load_usd(self):
    try:
        from omni.kit.window.filepicker import FilePickerDialog
    except ImportError:
        return
    stage = _get_stage()
    if not stage:
        return

    def on_apply(dialog, path):
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
```

---

## 4단계: 뷰포트 프레이밍

`_frame_prim_in_viewport`를 **3단계 함수들 아래**, `class Extension` 위에 추가합니다.

```python
def _frame_prim_in_viewport(prim_path: str) -> None:
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
```

- `extension.toml`에 `"omni.kit.viewport.utility" = {}`가 있어야 합니다(1단계에서 이미 추가됨).

---

## 5단계: 오브젝트 패널 UI (위치·스케일·온도·압력)

### 5-1. _get_prim_transform

`_frame_prim_in_viewport` 위쪽에 다음을 추가합니다.

```python
def _get_prim_transform(prim):
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
```

### 5-2. apply_simulation_rules (온도·압력 규칙)

같은 위치(클래스 위)에 시뮬레이션 규칙 함수를 추가합니다. (6·7단계 내용을 한 번에 넣습니다.)

```python
def apply_simulation_rules(prim):
    global _ansys_manager
    if not prim or not prim.IsValid():
        return

    # 규칙 1: 온도 → 색상
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

    # 규칙 2: 압력 → 휨
    pressure_attr = prim.GetAttribute(ATTR_PRESSURE)
    pressure = float(pressure_attr.Get()) if pressure_attr else DEFAULT_PRESSURE
    base_attr = prim.GetAttribute(ATTR_BASE_SCALE)
    base_scale = base_attr.Get() if base_attr else Gf.Vec3d(1, 1, 1)
    if base_scale is None:
        base_scale = Gf.Vec3d(1, 1, 1)

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

    if _ansys_manager is not None and _ansys_manager._available and pressure > PRESSURE_THRESHOLD_ANSYS:
        temp_attr = prim.GetAttribute(ATTR_TEMPERATURE)
        temperature = float(temp_attr.Get()) if temp_attr else DEFAULT_TEMP
        deformation = _ansys_manager.run_simulation(pressure, temperature)
        _ansys_manager.apply_result_to_prim(prim, deformation, base_scale)
    elif pressure > PRESSURE_THRESHOLD_FALLBACK:
        excess = pressure - PRESSURE_THRESHOLD_FALLBACK
        t = min(1.0, excess / PRESSURE_BEND_RANGE)
        scale_x = base_scale[0] * (1.0 + (PRESSURE_SCALE_X_MAX - 1.0) * t)
        scale_y = base_scale[1] * (1.0 - (1.0 - PRESSURE_SCALE_Y_MAX) * t)
        scale_op.Set(Gf.Vec3d(scale_x, scale_y, base_scale[2]))
    else:
        scale_op.Set(base_scale)
```

### 5-3. _build_object_panel 전체 구현

`Extension` 클래스 안의 `_build_object_panel(self, parent, prim_path):`를 아래 전체로 교체합니다.

- **주의**: `omni.ui.SimpleFloatModel` 값은 `get_value_as_float()`로 읽고, 콜백은 `model=None`처럼 인자 하나를 받도록 합니다(Kit UI 콜백 규약).

```python
def _build_object_panel(self, parent, prim_path):
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
        stage = _get_stage()
        p = stage.GetPrimAtPath(prim_path) if stage else None
        if not p or not p.IsValid():
            return
        xform = UsdGeom.Xformable(p)
        if not xform:
            return
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
                    stage = _get_stage()
                    p = stage.GetPrimAtPath(prim_path) if stage else None
                    if p and p.IsValid():
                        a = p.GetAttribute(ATTR_PRESSURE)
                        if a:
                            a.Set(pressure_model.get_value_as_float())
                            apply_simulation_rules(p)

                pressure_model.add_value_changed_fn(on_pressure_changed)
```

여기까지 하면 **버튼 3개 + 오브젝트 목록 + 위치/스케일/온도/압력 편집 + 온도 색상 + 압력 휨(단순 규칙)** 이 동작합니다.
PyAnsys를 쓰려면 **ansys_simulation.py**가 필요하므로 다음 단계에서 파일을 추가합니다.

---

## 6단계: ansys_simulation.py 생성 (PyAnsys 연동)

`morph/measure_control_1/ansys_simulation.py` 파일을 새로 만들고 아래 내용을 넣습니다.

- **psutil 5.x** 호환: `Process.net_connections`가 없으면 `Process.connections`를 사용하도록 패치합니다.
- **MAPDL 경로**: `get_default_ansys_path()` 또는 `get_default_ansys()`로 경로를 먼저 구하고, 없으면 `launch_mapdl()`을 호출하지 않아 Kit 앱에서 "경로 입력" 프롬프트가 뜨지 않도록 합니다.

```python
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
ANSYS MAPDL을 파이썬에서 부르고, 압력/온도에 따른 구조 해석 결과를
3D 오브젝트의 스케일(휨)로 반영하는 모듈입니다.
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from pxr import Gf, Usd, UsdGeom

DEFORMATION_TO_SCALE_FACTOR = 100.0


class AnsysSimulationManager:
    def __init__(self) -> None:
        self._mapdl = None
        self._available = False

    def initialize_solver(self) -> bool:
        if self._mapdl is not None:
            return self._available
        try:
            # psutil 5.x: net_connections 없으면 connections 사용
            try:
                import psutil
                if not getattr(psutil.Process, "net_connections", None) and getattr(
                    psutil.Process, "connections", None
                ):
                    psutil.Process.net_connections = psutil.Process.connections
            except Exception:
                pass

            from ansys.mapdl.core import launch_mapdl
            exec_file = None
            try:
                from ansys.mapdl.core.launcher import get_default_ansys_path
                exec_file = get_default_ansys_path()
            except Exception:
                try:
                    from ansys.mapdl.core.launcher import get_default_ansys
                    _path_ver = get_default_ansys()
                    if _path_ver:
                        exec_file = _path_ver[0] if isinstance(_path_ver, (list, tuple)) else _path_ver
                except Exception:
                    pass

            if not exec_file:
                self._mapdl = None
                self._available = False
                print("pyansys 안켜짐: ANSYS MAPDL 실행 파일을 찾을 수 없습니다. PC에 ANSYS Mechanical APDL이 설치되어 있어야 합니다.")
                return False

            self._mapdl = launch_mapdl(exec_file=exec_file, mode="grpc", start_timeout=60)
            self._available = True
            print("pyansys 잘 켜짐========================")
            return True
        except Exception as e:
            import traceback
            self._mapdl = None
            self._available = False
            print("pyansys 안켜짐========================")
            print(f"  사유: {e}")
            traceback.print_exc()
            return False

    def run_simulation(self, pressure: float, temperature: float) -> float:
        if not self._available or self._mapdl is None:
            return 0.0
        try:
            self._mapdl.clear()
            self._mapdl.prep7()
            self._mapdl.block(0, 1, 0, 1, 0, 1)
            self._mapdl.mp("EX", 1, 200e9)
            self._mapdl.mp("NUXY", 1, 0.3)
            self._mapdl.et(1, "SOLID185")
            self._mapdl.vsel("ALL")
            self._mapdl.esize(0.5)
            self._mapdl.vmesh("ALL")
            self._mapdl.nsel("S", "LOC", "Z", 0)
            self._mapdl.d("ALL", "ALL", 0)
            self._mapdl.allsel()
            self._mapdl.f("ALL", "FY", pressure)
            self._mapdl.run("/SOLU")
            self._mapdl.antype("STATIC")
            self._mapdl.solve()
            self._mapdl.finish()
            self._mapdl.post1()
            self._mapdl.set(1, 1)
            disp_y = self._mapdl.post_processing.nodal_displacement("Y")
            if disp_y is None or len(disp_y) == 0:
                return 0.0
            import numpy as np
            return float(np.abs(np.asarray(disp_y)).max())
        except Exception:
            return 0.0

    def apply_result_to_prim(self, prim: "Usd.Prim", deformation: float, base_scale: "Gf.Vec3d") -> None:
        from pxr import Gf, UsdGeom
        if prim is None or not prim.IsValid():
            return
        xform = UsdGeom.Xformable(prim)
        if not xform:
            return
        k = DEFORMATION_TO_SCALE_FACTOR * deformation
        scale_x = base_scale[0] * (1.0 + k)
        scale_y = base_scale[1] * (1.0 - k)
        scale_z = base_scale[2]
        scale = Gf.Vec3d(scale_x, scale_y, scale_z)
        scale_op = None
        for op in xform.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeScale:
                scale_op = op
                break
        if not scale_op:
            scale_op = xform.AddScaleOp()
        scale_op.Set(scale)

    def shutdown(self) -> None:
        if self._mapdl is not None:
            try:
                self._mapdl.exit()
            except Exception:
                pass
            self._mapdl = None
        self._available = False
```

- **ANSYS MAPDL 미설치**: `exec_file`이 없으면 `launch_mapdl()`을 호출하지 않고, 확장은 압력 100 초과 시 단순 휨 규칙만 사용합니다.

---

## 7단계: requirements-ansys.txt (선택)

확장 루트에 `requirements-ansys.txt`를 두고, 수동 설치 시 참고할 수 있게 합니다.

```
# Optional: for ANSYS MAPDL structural simulation (pressure -> deformation).
# Package name for pip:
ansys-mapdl-core

# 확장 로드 시 omni.kit.pipapi가 자동 설치 시도 (extension.toml [python.pipapi], 네트워크 필요).

# 수동 설치 예 (Kit Python 사용):
#   _build\windows-x86_64\release\python.exe -m pip install ansys-mapdl-core
```

---

## 8단계: 빌드 및 실행 확인

1. **빌드**
   - kit-app-template 프로젝트에서 확장이 포함되도록 빌드합니다.

2. **실행**
   - 앱을 실행하고 `morph.measure_control_1` 확장이 로드되는지 확인합니다.
   - "Measure Control Simulation" 창에 버튼 3개가 보여야 합니다.

3. **동작 확인**
   - **Create Cube** / **Create Sphere**: `/World/Cube_0`, `/World/Sphere_0` 등이 생성되고 목록에 표시되며, 뷰포트가 해당 prim에 맞춰집니다.
   - **Load USD**: 파일 선택 후 `/World/Loaded_0` 등으로 참조 로드.
   - 목록에서 **Position / Scale / Temperature / Pressure** 수정 시 즉시 반영되고, 온도에 따라 색상, 압력 100 초과 시 휨(단순 규칙 또는 ANSYS 사용 시 해석 기반)이 적용되는지 확인합니다.

---

## 9단계: Animation Clips·Timeline 확장 테스트 추가

이 단계는 **애니메이션 클립 테스트가 없는 상태**에서, 의존성 추가와 UI·확인 로직만 따라 하면 테스트 기능을 붙일 수 있도록 정리한 것입니다.
(이미 구현되어 있다면 해당 부분을 참고용으로만 활용하면 됩니다.)

### 9-1. extension.toml에 Animation 의존성 추가

`config/extension.toml`의 `[dependencies]` 블록에 아래 두 줄을 **기존 의존성 아래** 추가합니다.

```toml
[dependencies]
# ... 기존 omni.kit.uiapp, omni.ui 등 ...

# Animation Clips: 클립 형태로 객체 애니메이션 적용 (USD 스켈레톤 등). 앱에 포함된 경우 사용 가능.
"omni.anim.clips" = {}
# Timeline: 키프레임·재생 제어. 확장 테스트에서 재생/시간 확인용.
"omni.timeline" = {}
```

- **참고**: 앱에 `omni.anim.clips` 또는 `omni.timeline`이 없으면 확장 로드 시 의존성 오류가 날 수 있습니다. 그런 앱에서는 위 두 줄을 제거하면 measure_control_1만 로드됩니다.

### 9-2. _build_window에 Animation 확장 테스트 UI 넣기

`extension.py`의 `Extension` 클래스 안에 있는 `_build_window` 메서드를 수정합니다.

**수정 전**: 창 프레임 안에 `ScrollingFrame`과 `_object_list_frame`만 있는 구조라고 가정합니다.

**수정 후**: `with self._window.frame:` 안의 `ui.VStack` **맨 위**에, 스크롤 영역 **위에** 다음을 넣습니다.

1. 접이식 프레임 **"Animation 확장 테스트"** (기본 접힌 상태)
2. 그 안에 상태를 표시할 **Label** 하나 (속성으로 `self._anim_status_label`에 담음)
3. **"Animation·타임라인 상태 새로고침"** 버튼 하나 (클릭 시 `self._on_refresh_animation_status` 호출)
4. 창을 연 직후 한 번 `_on_refresh_animation_status()` 호출

예시는 아래와 같습니다. 기존 `_build_window` 내용을 다음처럼 **바꾸거나**, 같은 위치에 해당 블록만 **추가**하면 됩니다.

```python
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
```

- `self._anim_status_label`: 나중에 `_on_refresh_animation_status`에서 텍스트를 갱신할 때 사용합니다.
- `_on_refresh_animation_status`는 다음 9-3에서 정의합니다.

### 9-3. _on_refresh_animation_status 메서드 추가

`Extension` 클래스 안에 **새 메서드** `_on_refresh_animation_status`를 추가합니다.
(다른 메서드들, 예: `_on_move_0` / `_on_move_1` 뒤에 두면 됩니다.)

이 메서드에서 다음을 수행합니다.

1. **Animation Clips**: `import omni.anim.clips` 시도
   - 성공하면 `"[Animation Clips] omni.anim.clips: 로드됨 (클립 적용 등 사용 가능)"`
   - 실패하면 확장 매니저로 `omni.anim.clips` / `omni.anim.clips.bundle` 활성화 여부 확인 후, 없으면 `"앱에 미포함 (USD Composer 등에서 사용 가능)"` 등으로 표시
2. **Timeline**: `omni.timeline.get_timeline_interface()`로 현재 재생 시간(초)과 tps를 읽어 한 줄로 표시
3. 위에서 만든 문자열들을 줄바꿈으로 이어서 `self._anim_status_label.text`에 넣음

아래 코드를 그대로 복사해 `Extension` 클래스 안에 넣으면 됩니다.

```python
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
```

### 9-4. 동작 확인

1. 앱을 빌드·실행하고 measure_control_1 확장이 로드되는지 확인합니다.
2. "Measure Control Simulation" 창에서 **"Animation 확장 테스트"** 접이식을 펼칩니다.
3. **"Animation·타임라인 상태 새로고침"** 버튼을 눌러 봅니다.
4. 라벨에 다음이 나오는지 확인합니다.
   - **Animation Clips**: `로드됨` / `활성화됨` 또는 `앱에 미포함` / `확인 실패`
   - **Timeline**: `현재 시간: ... sec (tps=...)` 또는 `확인 실패`

의존성이 포함된 앱(예: USD Composer)에서는 Animation Clips·Timeline이 모두 표시되고, 포함되지 않은 앱에서는 위와 같이 미포함/실패 메시지로 테스트 가능 여부를 확인할 수 있습니다.

---

## 요약 체크리스트

| 단계 | 내용 |
|------|------|
| 1 | extension.toml (의존성 + pipapi), premake5.lua, __init__.py |
| 2 | extension.py: Extension, on_startup/on_shutdown, _build_window, 버튼 3개, _refresh_object_list |
| 3 | _get_stage, _ensure_world_prim, _next_index, create_cube, create_sphere, load_usd, _ensure_custom_attributes, _on_load_usd (FilePicker) |
| 4 | _frame_prim_in_viewport (viewport.utility) |
| 5 | _get_prim_transform, apply_simulation_rules, _build_object_panel (위치/스케일/온도/압력, get_value_as_float, model=None 콜백) |
| 6 | ansys_simulation.py: AnsysSimulationManager, psutil 패치, get_default_ansys_path/exec_file, run_simulation, apply_result_to_prim, shutdown |
| 7 | requirements-ansys.txt (선택) |
| 8 | 빌드·실행·동작 확인 |
| 9 | Animation Clips·Timeline 의존성 추가, _build_window에 테스트 UI, _on_refresh_animation_status 추가 및 동작 확인 |

이 순서대로 적용하면 **처음 버튼 3개부터 현재 구현(온도/압력 규칙, PyAnsys 연동, psutil·MAPDL 호환, Animation 확장 테스트)**까지 문서만 보고 동일하게 구현할 수 있습니다.

---

## 전체 코드 참고

- **extension.py**
  이 가이드의 2~5단계를 모두 반영한 최종 코드는 프로젝트의
  `morph/measure_control_1/extension.py` 에 있습니다.
  복사·붙여넣기로 한 번에 적용하려면 해당 파일을 참고하세요.
  **9단계(Animation 확장 테스트)**는 동일 파일의 `_build_window`와 `_on_refresh_animation_status`를 참고하면 됩니다.

- **ansys_simulation.py**
  PyAnsys 연동 전체 코드는
  `morph/measure_control_1/ansys_simulation.py` 에 있습니다.
  주석이 더 많은 버전이 필요하면 해당 파일을 참고하세요.

- **config/extension.toml**
  9단계에서 추가하는 Animation 의존성(`omni.anim.clips`, `omni.timeline`)은
  `config/extension.toml`의 `[dependencies]`에 있습니다.
