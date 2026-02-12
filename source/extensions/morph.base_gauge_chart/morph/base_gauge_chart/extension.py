import omni.ext
import omni.ui as ui
import omni.ui.scene as sc
import math


class ArcGaugeSampleExtension(omni.ext.IExt):
    """
    omni.ui.scene.Arc 를 이용해서
    초록 / 노랑 / 빨강 3구간 반원 게이지를 그리는 최소 샘플입니다.
    """

    def on_startup(self, ext_id: str) -> None:
        print("[morph.base_gauge_chart] Arc gauge sample startup")

        # 게이지 값 범위와 현재 값 설정
        self._min_value = 0.0
        self._max_value = 100.0
        self._current_value = 97.7  # 원하는 초기 값

        # 간단한 윈도우
        self._window = ui.Window("Arc Gauge Sample", width=500, height=320)

        with self._window.frame:
            with ui.VStack(spacing=0, height=ui.Percent(90)):
                ui.Spacer(height=ui.Percent(10))
                # === 게이지(Arc 3개) + 중앙 값 라벨을 한 영역(ZStack) 안에 겹쳐서 배치 ===
                with ui.ZStack(height=ui.Percent(90)):
                    # 배경: SceneView 에 Arc 들을 그림
                    scene_view = sc.SceneView(
                        aspect_ratio_policy=sc.AspectRatioPolicy.PRESERVE_ASPECT_FIT,
                        height=ui.Percent(100),
                    )

                    # SceneView 내부에 Arc 3개를 배치
                    with scene_view.scene:
                        def on_arc_clicked(shape):
                            """클릭 시 호출. 인자는 클릭된 shape(Arc)입니다."""
                            print("[Arc Gauge] Arc clicked!")


                        radius = 1.0
                        thickness = 20  # 게이지 두께

                        # 바탕탕 구간
                        sc.Arc(
                            radius,
                            begin=-0.1,
                            end=math.pi * 2 /2 + 0.1,
                            thickness=thickness * 2,
                            color=(0.2, 0.2, 0.2, 1.0),
                            wireframe=True,
                            sector=False,
                        )

                        # 파랑 구간
                        sc.Arc(
                            radius,
                            begin=-0.1,
                            end=math.pi * 2 / 3 /2,
                            thickness=thickness,
                            color=(0.0, 0.0, 1.0, 1.0),
                            wireframe=True,      # 선(링) 형태
                            sector=False,
                            intersection_thickness=5,
                            gesture=sc.ClickGesture(on_ended_fn=on_arc_clicked),
                        )

                        # 빨강 구간
                        sc.Arc(
                            radius,
                            begin=math.pi* 2 / 3 /2,
                            end=math.pi * 4 / 3 /2,
                            thickness=thickness,
                            color=(1.0, 0.0, 0.0, 1.0),
                            wireframe=True,
                            sector=False,
                            intersection_thickness=5,
                            gesture=sc.ClickGesture(on_ended_fn=on_arc_clicked),
                        )

                        # 초록 구간
                        sc.Arc(
                            radius,
                            begin=math.pi * 4 / 3 /2,
                            end=math.pi * 2 /2 + 0.1,
                            thickness=thickness,
                            color=(0.0, 1.0, 0.0, 1.0),
                            wireframe=True,
                            sector=False,
                            intersection_thickness=5,
                            gesture=sc.ClickGesture(on_ended_fn=on_arc_clicked),
                        )


                    # 전경: 중앙 값 라벨을 Arc 바로 아래쪽에 겹쳐서 배치
                    with ui.VStack():
                        ui.Spacer(height=ui.Percent(0))  # 값 라벨을 반원에 가깝게 내림
                        ui.Label(
                            f"{self._current_value:0.2f}",
                            alignment=ui.Alignment.CENTER,
                            style={"font_size": 20},
                        )

                    # === 좌/우 최소·최대 값 라벨: ZStack 맨 아래로 내리기 ===
                    with ui.VStack(height=ui.Percent(100)):
                        ui.Spacer(height=ui.Percent(60))
                        with ui.HStack(height=0):
                            ui.Spacer(width=ui.Percent(28))
                            with ui.HStack(height=0, width=ui.Percent(45)):
                                ui.Label(
                                    f"{self._min_value:0.2f}",
                                    alignment=ui.Alignment.LEFT,
                                )
                                ui.Label(
                                    f"{self._max_value:0.2f}",
                                    alignment=ui.Alignment.RIGHT,
                                )


        self._scene_view = scene_view

    def on_shutdown(self) -> None:
        print("[morph.base_gauge_chart] Arc gauge sample shutdown")
        self._scene_view = None
        self._window = None