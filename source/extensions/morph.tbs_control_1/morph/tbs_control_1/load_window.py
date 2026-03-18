# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
load_window.py — USD Load 창 UI 및 로드 로직.

기능:
- build_load_window(ext): "USD Load" ui.Window 생성, 경로 입력/ resource 콤보/ Load 버튼.
  ext에 _load_window, _path_model, _resource_combo, _resource_paths, _resource_names, _load_status_label 설정.
- get_load_path(ext): Load 시 사용할 경로 (콤보 선택 시 resource 경로, 아니면 입력 필드 값).
- on_resource_combo_changed(ext): 콤보 변경 시 경로 필드에 선택한 resource 경로 반영.
- on_load_usd(ext): async. stat_async 검증 후 open_stage(path). 상태 라벨 갱신.

사용처: extension.py on_startup에서 build_load_window(self) 호출, Load 버튼에서 on_load_usd(self) 호출.
"""

import asyncio
from typing import Any

import omni.client
import omni.ui as ui

from .usd_loader_utils import (
    get_resource_usd_list,
    path_has_supported_stage_extension,
)

# 기본 URL (extension에서 덮어쓸 수 있음)
DEFAULT_USD_URL = (
    "https://restme.morph.kr/~jh.park2/DirTest/"
    "PhysicalAI_SceneAssembly_Start/SceneAssembly.usd"
)


def build_load_window(ext: Any) -> None:
    """USD Load 창을 만들고 ext에 위젯/모델 참조를 저장."""
    ext._load_window = ui.Window("USD Load", width=480, height=200)
    resource_items = get_resource_usd_list()
    ext._resource_names = ["선택안함"] + [name for name, _ in resource_items]
    ext._resource_paths = [""] + [path for _, path in resource_items]
    with ext._load_window.frame:
        with ui.VStack(padding=10, spacing=8):
            ui.Label("resource 폴더 샘플 (선택안함 = 아래 경로로 로드)", height=0)
            ext._resource_combo = ui.ComboBox(0, *ext._resource_names)
            ext._resource_combo.model.add_item_changed_fn(lambda m, *a: on_resource_combo_changed(ext))
            ui.Spacer(height=4)
            ui.Label("경로 (직접 입력 또는 위에서 선택)", height=0)
            ext._path_model = ui.SimpleStringModel(getattr(ext, "DEFAULT_USD_URL", DEFAULT_USD_URL))
            ui.StringField(model=ext._path_model)
            ext._load_status_label = ui.Label("", style={"color": 0xFF888888})
            ui.Button(
                "Load",
                clicked_fn=lambda: asyncio.ensure_future(on_load_usd(ext)),
            )


def get_load_path(ext: Any) -> str:
    """Load 시 사용할 경로. 선택안함(인덱스 0)이면 경로 필드 값, 그 외에는 콤보에서 선택한 resource 경로."""
    path = (ext._path_model.get_value_as_string() or "").strip()
    if getattr(ext, "_resource_paths", None) and getattr(ext, "_resource_combo", None):
        try:
            index = ext._resource_combo.model.get_item_value_model().as_int
            if 0 <= index < len(ext._resource_paths):
                if index == 0:
                    return path
                return ext._resource_paths[index] or path
        except Exception:
            pass
    return path


def on_resource_combo_changed(ext: Any) -> None:
    """resource 콤보 선택 시: 선택안함(0)이면 경로 필드 유지, 그 외에는 해당 USD 경로로 설정."""
    try:
        index = ext._resource_combo.model.get_item_value_model().as_int
        if 0 <= index < len(ext._resource_paths) and index != 0:
            ext._path_model.set_value_as_string(ext._resource_paths[index])
    except Exception:
        pass


async def on_load_usd(ext: Any) -> None:
    """stat_async(path) 검증 후 open_stage(path). 경로는 get_load_path(ext) 사용."""
    path = get_load_path(ext)
    ext._load_status_label.text = ""

    if not path or not path_has_supported_stage_extension(path):
        ext._load_status_label.text = "Error: Invalid URL or File extension."
        return

    try:
        result, _ = await asyncio.wait_for(
            omni.client.stat_async(path), timeout=1.5
        )
        if result != omni.client.Result.OK:
            ext._load_status_label.text = "Error: This URL does not exist."
            return
    except Exception:
        ext._load_status_label.text = "Error: Connection timeout (Wrong Domain)."
        return

    ext._load_status_label.text = "로드 중..."
    import omni.usd as ou
    ou.get_context().open_stage(path)
    ext._load_status_label.text = "로드 완료. TBS 제어창에서 '목록 새로고침'을 눌러 주세요."
