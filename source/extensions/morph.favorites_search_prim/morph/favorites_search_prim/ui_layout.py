# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

from pathlib import Path

import omni.ui as ui
from omni.kit.widget.stage import StageWidget
from omni.kit.widget.stage.stage_style import Styles as StageStyles

# Favorite 컬럼(별 버튼) UI 배치 상수
# - 폭/높이를 고정해 두면 좌/우 패널 모두 같은 시각적 크기를 유지할 수 있다.
FAVORITE_COLUMN_WIDTH = 24
FAVORITE_ROW_HEIGHT = 24
FAVORITE_ICON_SIZE = 16

# 좌측 즐겨찾기 목록 스타일 상수
# - LEFT_HIGHLIGHT_COLOR: 선택된 row 배경색
# - LEFT_PANEL_OUTER_MARGIN: 좌측 패널 바깥 여백(우측 Stage 패널과 균형)
LEFT_HIGHLIGHT_COLOR = 0xFF4B4A42
LEFT_PANEL_OUTER_MARGIN = 2

"""
    메인 윈도우를 생성하고 extension에서 사용할 주요 위젯 참조를 반환한다.

    반환값:
    - window: 최상위 ui.Window
    - left_list_frame: 좌측 목록을 재구성할 때 clear/rebuild 대상 Frame
    - right_stage_widget: 우측 StageWidget

    레이아웃 설계:
    - 최상위는 HStack: 좌/우 패널을 가로로 배치
    - 좌측은 VStack/HStack 조합: 바깥 여백 + 상단 툴바 + 헤더 + 목록
    - 우측은 StageWidget 단일 배치
"""
def build_main_window(load_fn, clear_fn, favorites_json_path: Path):

    window = ui.Window(
        "Favorites Search Prim",
        width=600,
        height=800,
        flags=ui.WINDOW_FLAGS_NO_SCROLLBAR,
    )

    left_list_frame = None
    right_stage_widget = None

    with window.frame:
        # 좌/우 패널을 가로로 나누므로 최상위 컨테이너는 HStack이 적합하다.
        with ui.HStack(spacing=2, height=ui.Fraction(1.0)):
            # 좌측 패널: 세로 구조(상단 여백/본문/하단 여백)라서 VStack 사용
            with ui.VStack(width=ui.Fraction(1.0), spacing=0):
                ui.Spacer(height=LEFT_PANEL_OUTER_MARGIN)

                # 좌우 바깥 여백을 넣기 위해 HStack으로 감싸고 양쪽에 Spacer 배치
                with ui.HStack(spacing=0, height=ui.Fraction(1.0)):
                    ui.Spacer(width=LEFT_PANEL_OUTER_MARGIN)

                    # 실제 좌측 콘텐츠 본문
                    with ui.VStack(spacing=0, style=StageStyles.STAGE_WIDGET):
                        # 상단 버튼 행: 버튼들을 가로로 배치해야 하므로 HStack 사용
                        with ui.HStack(height=24, spacing=4):
                            load_btn = ui.Button("Load", width=54, clicked_fn=load_fn)
                            load_btn.tooltip = f"Load favorites from:\n{favorites_json_path}"

                            clear_btn = ui.Button("AllClear", width=72, clicked_fn=clear_fn)
                            clear_btn.tooltip = "Clear all favorites and save immediately."

                            # 오른쪽 잔여 공간 채움
                            ui.Spacer()

                        # 버튼행과 헤더 사이 시각적 간격
                        ui.Spacer(height=7)

                        # 헤더 배경 + 텍스트를 겹쳐 그리기 위해 ZStack 사용
                        # 1) Rectangle (배경)
                        # 2) HStack/Label (전경 텍스트)
                        with ui.ZStack(height=7):
                            ui.Rectangle(style_type_name_override="TreeView.Header")
                            with ui.HStack():
                                ui.Spacer(width=10)
                                ui.Label("Name", style_type_name_override="TreeView.Header")

                        # 목록 본문 스크롤 영역
                        # 실제 row 렌더는 left_list_frame을 clear/rebuild 하며 수행
                        with ui.ScrollingFrame(
                            style_type_name_override="TreeView.ScrollingFrame",
                            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                            height=ui.Fraction(1.0),
                        ):
                            left_list_frame = ui.Frame()

                    ui.Spacer(width=LEFT_PANEL_OUTER_MARGIN)

                ui.Spacer(height=LEFT_PANEL_OUTER_MARGIN)

            # 우측 패널: 비율 2 영역. StageWidget은 자체적으로 내부 레이아웃을 구성한다.
            with ui.VStack(width=ui.Fraction(2.0), spacing=0):
                right_stage_widget = StageWidget(
                    None,
                    columns_enabled=["Favorite", "Visibility", "Type"],
                )

    return window, left_list_frame, right_stage_widget


def rebuild_left_list(
    frame,
    favorites_rows,
    selected_paths,
    on_row_clicked,
    on_row_double_clicked,
    on_toggle_favorite,
    star_icon_path,
):
    """
    좌측 즐겨찾기 목록 전체를 다시 렌더링한다.

    전체 재렌더링을 택한 이유:
    - 선택 변경/토글 시 여러 row 상태가 동시에 바뀔 수 있어
      부분 업데이트보다 clear -> rebuild가 단순하고 안정적이다.
    """
    if not frame:
        return

    frame.clear()
    with frame:
        # 우측 Stage와 시각 톤을 맞추기 위해 Stage 스타일 재사용
        with ui.VStack(spacing=0, style=StageStyles.STAGE_WIDGET):
            if not favorites_rows:
                with ui.HStack(height=24):
                    ui.Spacer(width=8)
                    ui.Label("(empty)", style_type_name_override="TreeView.Item")
            else:
                for row in favorites_rows:
                    _build_left_row(
                        path=row["path"],
                        name=row["name"],
                        is_default=row["is_default"],
                        icon_paths=row["icon_paths"],
                        is_selected=row["path"] in selected_paths,
                        on_row_clicked=on_row_clicked,
                        on_row_double_clicked=on_row_double_clicked,
                        on_toggle_favorite=on_toggle_favorite,
                        star_icon_path=star_icon_path,
                    )


def _build_left_row(
    path,
    name,
    is_default,
    icon_paths,
    is_selected,
    on_row_clicked,
    on_row_double_clicked,
    on_toggle_favorite,
    star_icon_path,
):
    """
    좌측 목록의 단일 row를 렌더링한다.

    동작:
    - row 좌클릭: 선택 갱신
    - row 더블클릭: 선택 + 포커스 이동
    - 별 좌클릭: 즐겨찾기 토글
    """
    text = f"{name} (defaultPrim)" if is_default else name

    # 배경 하이라이트와 전경 콘텐츠를 겹쳐야 하므로 ZStack 사용
    row = ui.ZStack(height=20, width=ui.Fraction(1.0))

    # row 자체 클릭/더블클릭 이벤트 연결
    row.set_mouse_pressed_fn(
        lambda _x, _y, button, _m, p=path: on_row_clicked(p) if button == 0 else None
    )
    row.set_mouse_double_clicked_fn(
        lambda _x, _y, button, _m, p=path: on_row_double_clicked(p) if button == 0 else None
    )

    with row:
        # 선택 상태 배경
        ui.Rectangle(
            visible=is_selected,
            style={"Rectangle": {"background_color": LEFT_HIGHLIGHT_COLOR}},
            width=ui.Fraction(1.0),
            height=24,
        )

        # row 콘텐츠는 가로 정보(아이콘/텍스트/버튼)라 HStack 사용
        with ui.HStack(height=24, spacing=0):
            ui.Spacer(width=8)

            # 아이콘 수직 중앙 정렬을 위해 VStack으로 감싼다.
            with ui.VStack(width=0):
                ui.Spacer()

                # 타입 + 보조 아이콘(참조/페이로드 등)을 겹쳐 그리기 위해 ZStack 사용
                with ui.ZStack(width=20, height=24):
                    for icon_path in icon_paths:
                        ui.Image(icon_path, style_type_name_override="TreeView.Image")

                ui.Spacer()

            ui.Spacer(width=4)

            # 선택 시 텍스트를 흰색으로 강조
            if is_selected:
                ui.Label(text, style_type_name_override="TreeView.Item", style={"color": 0xFFFFFFFF})
            else:
                ui.Label(text, style_type_name_override="TreeView.Item")

            # 별 버튼을 우측 끝으로 밀기 위한 가변 공간
            ui.Spacer()

            # 별 버튼 클릭 영역: row 클릭 이벤트와 분리하기 위해 별도 위젯 사용
            star_click_area = ui.ZStack(width=FAVORITE_COLUMN_WIDTH, height=FAVORITE_ROW_HEIGHT)
            with star_click_area:
                # 별 아이콘 중앙 정렬(HStack + VStack)
                with ui.HStack():
                    ui.Spacer()
                    with ui.VStack(width=0):
                        ui.Spacer()
                        ui.Image(star_icon_path, width=FAVORITE_ICON_SIZE, height=FAVORITE_ICON_SIZE)
                        ui.Spacer()
                    ui.Spacer()

            star_click_area.set_mouse_pressed_fn(
                lambda _x, _y, button, _m, p=path: on_toggle_favorite(p) if button == 0 else None
            )

            # 우측 경계와 붙지 않게 미세 여백
            ui.Spacer(width=2)
