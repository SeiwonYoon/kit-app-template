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


def _generate_wave_values(num_points: int = 256) -> List[float]:
    """불규칙한 물결 모양의 1D 데이터 생성 (0~1 범위 정규화)."""
    values: List[float] = []

    for i in range(num_points):
        t = i / float(num_points - 1)
        v = (
            0.55 * math.sin(2.0 * math.pi * t * 0.7)
            + 0.25 * math.sin(2.0 * math.pi * t * 3.3 + 0.4)
            + 0.15 * math.sin(2.0 * math.pi * t * 9.7 + 1.3)
        )
        # -1 ~ 1 -> 0 ~ 1 으로 정규화
        y = (v + 1.0) * 0.5
        values.append(y)

    return values


class GraphJungExtension(omni.ext.IExt):
    """베이스 UI에서 사용할 불규칙 파형 + 영역 색상 그래프 확장입니다."""

    def on_startup(self, _ext_id: str):
        print("[morph.graph_jung] Extension startup")

        self._window = ui.Window("Jung Graph", width=500, height=260)

        with self._window.frame:
            with ui.VStack(spacing=8, height=0):
                ui.Label("Performance Trend", style={"font_size": 16})
                ui.Label(
                    "샘플 데이터로 구성된 라인 그래프입니다.",
                    style={"color": ui.color("#A0A0A0")},
                )

                # Plot 위젯 + 배경 Rectangle 으로
                #  - irregular wave 라인
                #  - 그래프 아래쪽 영역 색상
                # 을 함께 표현.
                with ui.Frame(height=200):
                    with ui.ZStack():
                        # 전체 배경
                        ui.Rectangle(
                            style={
                                "background_color": ui.color(0.09, 0.09, 0.09, 1.0),
                                "border_color": ui.color(0.25, 0.25, 0.25, 1.0),
                                "border_width": 1,
                                "corner_flag": ui.CornerFlag.ALL,
                            }
                        )

                        # 그래프 "아래" 영역 색상 (하단 60% 정도를 살짝 채움)
                        with ui.VStack():
                            ui.Spacer(height=ui.Percent(35))
                            ui.Rectangle(
                                height=ui.Percent(65),
                                style={
                                    "background_color": ui.color(0.08, 0.25, 0.45, 0.35),
                                },
                            )

                        # 실제 라인 그래프 (불규칙 파형)
                        # 공식 시그니처: Plot(type, scale_min, scale_max, valueList, **kwargs)
                        # ref: omni.ui.Plot docs (https://docs.omniverse.nvidia.com/kit/docs/omni.ui/latest/omni.ui/omni.ui.Plot.html)
                        plot_data = _generate_wave_values()
                        plot = ui.Plot(
                            ui.Type.LINE,
                            0.0,   # scale_min
                            1.0,   # scale_max
                            plot_data,  # valueList
                            height=ui.Percent(100),
                        )

    def on_shutdown(self):
        print("[morph.graph_jung] Extension shutdown")
        self._window = None
