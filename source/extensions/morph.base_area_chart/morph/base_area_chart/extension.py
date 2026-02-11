# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

import math
from typing import List, Tuple

import omni.ext
import omni.ui as ui

class BaseAreaChartExtension(omni.ext.IExt):
    """베이스 UI에서 사용할 Area Chart 확장입니다."""

    def on_startup(self, _ext_id: str):
        print("[morph.base_area_chart] Extension startup")

    def on_shutdown(self):
        print("[morph.base_area_chart] Extension shutdown")
        self._window = None
