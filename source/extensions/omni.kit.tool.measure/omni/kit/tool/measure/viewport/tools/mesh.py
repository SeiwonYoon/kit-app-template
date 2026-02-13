# Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.
#

"""
메시 측정 도구 (Mesh Measure Tool)

이 모듈은 메시 프림에 대한 측정 기능을 제공합니다.
메시의 특정 속성이나 지오메트리를 측정하는 데 사용됩니다.
"""

from typing import Any, Dict, List, Optional, Sequence

import omni.kit.raycast.query
import omni.usd as ou
from carb import log_error
from omni import ui
from omni.ui import scene as sc
from pxr import Gf, Usd, UsdGeom

from ...common import MeasureCreationState, MeasureMode, SnapMode
from ...manager import MeasurementManager, ReferenceManager
from ...system import MeasurePayload
from ..manipulator_items import *
from ..snap.manager import MeasureSnapProviderManager
from .viewport_mode_model import ViewportModeModel


# -----------------------------------------------------------------------------
# UI 버튼 클릭 시 호출되는 모듈 수준 함수 (선택된 메시에 대해 BBox X/Y/Z 측정 생성)
# -----------------------------------------------------------------------------

def _collect_mesh_prims(prim: Usd.Prim) -> List[Usd.Prim]:
    """프림과 그 하위의 모든 Mesh 프림을 수집합니다 (통합 바운딩용)."""
    result: List[Usd.Prim] = []
    if prim.IsA(UsdGeom.Mesh):
        result.append(prim)
    for child in prim.GetChildren():
        result.extend(_collect_mesh_prims(child))
    return result


def _compute_combined_bbox(
    bbox_cache: UsdGeom.BBoxCache,
    mesh_prims: List[Usd.Prim],
) -> Optional[tuple]:
    """
    여러 메시의 월드 바운딩 박스를 합쳐 하나의 (min, max)를 반환합니다.

    각 메시의 로컬 bbox를 ou.get_world_transform_matrix(prim)로 월드 변환하여,
    자식 prim의 Translate 등 Kit/Fabric에서 평가되는 변환이 반영되도록 합니다.
    (BBoxCache.ComputeWorldBound는 USD 내부 평가만 사용해 자식 이동이 누락될 수 있음)
    """
    if not mesh_prims:
        return None
    min_p: Optional[Gf.Vec3d] = None
    max_p: Optional[Gf.Vec3d] = None
    for p in mesh_prims:
        local_bbox = bbox_cache.ComputeLocalBound(p)
        r = local_bbox.GetRange()
        mn, mx = r.GetMin(), r.GetMax()
        corners = (
            Gf.Vec3d(mn[0], mn[1], mn[2]),
            Gf.Vec3d(mx[0], mn[1], mn[2]),
            Gf.Vec3d(mn[0], mx[1], mn[2]),
            Gf.Vec3d(mx[0], mx[1], mn[2]),
            Gf.Vec3d(mn[0], mn[1], mx[2]),
            Gf.Vec3d(mx[0], mn[1], mx[2]),
            Gf.Vec3d(mn[0], mx[1], mx[2]),
            Gf.Vec3d(mx[0], mx[1], mx[2]),
        )
        wtm = ou.get_world_transform_matrix(p)
        for c in corners:
            w = wtm.Transform(c)
            if min_p is None:
                min_p = Gf.Vec3d(w)
                max_p = Gf.Vec3d(w)
            else:
                min_p = Gf.Vec3d(
                    min(min_p[0], w[0]),
                    min(min_p[1], w[1]),
                    min(min_p[2], w[2]),
                )
                max_p = Gf.Vec3d(
                    max(max_p[0], w[0]),
                    max(max_p[1], w[1]),
                    max(max_p[2], w[2]),
                )
    return (min_p, max_p) if min_p is not None else None


def _create_point_to_point_measurement_impl(
    prim_path: str,
    start_point: Gf.Vec3d,
    end_point: Gf.Vec3d,
    label_color: Optional[Gf.Vec4f] = None,
) -> None:
    """PointToPoint 측정을 생성합니다 (모듈 수준). label_color가 있으면 사용, 없으면 display_panel.color."""
    display_panel = ReferenceManager().ui_display_panel
    payload = MeasurePayload()
    payload.prim_paths = [prim_path, prim_path]
    payload.points = MeasurePayload.world_to_local_points(
        [start_point, end_point], payload.prim_paths
    )
    payload.tool_mode = MeasureMode.MESH
    payload.axis_display = display_panel.display_axis
    payload.unit_type = display_panel.unit
    payload.precision = display_panel.precision
    payload.label_size = display_panel.text_size
    payload.label_color = label_color if label_color is not None else display_panel.color
    MeasurementManager().create(payload)


# X/Y/Z 축별 측정선 색상 (RGBA, 0~1): X=붉은색, Y=연두색, Z=파란색
_AXIS_LABEL_COLORS = {
    0: Gf.Vec4f(1.0, 0.2, 0.2, 1.0),   # X: 붉은색
    1: Gf.Vec4f(0.45, 1.0, 0.35, 1.0), # Y: 연두색
    2: Gf.Vec4f(0.25, 0.5, 1.0, 1.0),  # Z: 파란색
}


def _create_bbox_axis_measurements_impl(root_prim: Usd.Prim) -> None:
    """
    선택 prim과 하위 모든 Mesh를 합친 통합 바운딩으로 X/Y/Z 축 PointToPoint 측정을 생성합니다.

    배치 규칙: 시작점·끝점은 반드시 바운딩 박스 꼭짓점(vertex) 위치와 동일.
    한 꼭짓점 (mn[0], mn[1], mn[2])에서 뻗는 세 모서리를 사용하여,
    모든 선이 prim 바운더리 내부(모서리/표면)에만 그려지도록 함.

    - X축: (mn[0], mn[1], mn[2]) → (mx[0], mn[1], mn[2])
    - Y축: (mn[0], mn[1], mn[2]) → (mn[0], mx[1], mn[2])
    - Z축: (mn[0], mn[1], mn[2]) → (mn[0], mn[1], mx[2])

    색상: X=붉은색, Y=연두색, Z=파란색.
    """
    try:
        mesh_prims = _collect_mesh_prims(root_prim)
        if not mesh_prims:
            return

        bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        combined = _compute_combined_bbox(bbox_cache, mesh_prims)
        if not combined:
            return

        mn = combined[0]
        mx = combined[1]
        prim_path = str(root_prim.GetPath())

        # 공통 꼭짓점 (mn[0], mn[1], mn[2])에서 뻗는 X, Y 모서리. 모든 점이 bbox vertex.
        # 1. X축: Y=mn[1], Z=mn[2] 인 모서리 (min → max)
        start_x = Gf.Vec3d(mx[0], mx[1], mn[2])
        end_x   = Gf.Vec3d(mx[0], mn[1], mn[2])
        if (end_x - start_x).GetLength() >= 1e-9:
            _create_point_to_point_measurement_impl(
                prim_path, start_x, end_x, label_color=_AXIS_LABEL_COLORS.get(0)
            )

        # 2. Y축: X=mn[0], Z=mn[2] 인 모서리 (min → max)
        start_y = Gf.Vec3d(mx[0], mx[1], mn[2])
        end_y   = Gf.Vec3d(mn[0], mx[1], mn[2])
        if (end_y - start_y).GetLength() >= 1e-9:
            _create_point_to_point_measurement_impl(
                prim_path, start_y, end_y, label_color=_AXIS_LABEL_COLORS.get(1)
            )

        # 3. Z축: X, Y축이 만나는 반대편 모서리 (mx[0], mx[1], mn[2]) → (mx[0], mx[1], mx[2])
        start_z = Gf.Vec3d(mn[0], mx[1], mn[2])  # X, Y축 끝점이 만나는 곳
        end_z   = Gf.Vec3d(mn[0], mx[1], mx[2])    # Z축 최대값
        if (end_z - start_z).GetLength() >= 1e-9:
            _create_point_to_point_measurement_impl(
                prim_path, start_z, end_z, label_color=_AXIS_LABEL_COLORS.get(2)
            )
    except Exception as e:
        log_error(f"메시 BBox 측정 생성 중 오류: {e}")


def run_mesh_bbox_measurement_for_selection() -> None:
    """
    현재 선택된 각 프림에 대해, 해당 prim과 하위 모든 Mesh를 합친 통합 바운딩 박스로
    X/Y/Z 축 PointToPoint 측정을 생성합니다. 'BBox 측정' UI 버튼 클릭 시 호출됩니다.
    """
    ctx = ou.get_context()
    stage = ctx.get_stage()
    if not stage:
        return
    for path in ctx.get_selection().get_selected_prim_paths():
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            continue
        _create_bbox_axis_measurements_impl(prim)


# -----------------------------------------------------------------------------
# MeshModel (뷰포트용, 클릭 시 측정은 수행하지 않음)
# -----------------------------------------------------------------------------


class MeshModel(ViewportModeModel):
    """
    메시 측정 모델 클래스

    메시 프림에 대한 측정 기능을 구현합니다.
    ViewportModeModel을 상속받아 공통 인터페이스를 구현합니다.
    """
    _mode = MeasureMode.MESH

    def __init__(self, viewport_api):
        """
        메시 측정 모델 초기화

        Args:
            viewport_api: 뷰포트 API 인스턴스
        """
        super().__init__(viewport_api, mode=self._mode)

        # 스냅 데이터 저장
        self._snap_data: Dict[str, Any] = {}

        # 측정 포인트들
        self._points: List[PositionItem] = []
        self._prims: List[PrimRefItem] = []

        # 씬 UI 요소들
        self._color = [0, 1, 1, 1]  # 기본 색상 (청록색)
        self._ui_points: Optional[sc.Points] = None
        self._ui_lines: List[sc.Line] = []

        # 라벨 (나중에 구현)
        with self._label_root:
            self._ui_scene_label = sc.Label("", color=[1, 1, 1, 1], visible=False)

    def reset(self):
        """
        측정 상태를 초기화합니다.
        """
        super().reset()
        self._root.clear()

        # 스냅 데이터 초기화
        self._snap_data = {}

        # 포인트 및 프림 초기화
        self._points.clear()
        self._prims.clear()

        # UI 요소 초기화
        self._ui_points = None
        self._ui_lines.clear()
        self._ui_scene_label.visible = False

        # 상태 초기화
        self.creation_state = MeasureCreationState.START_SELECTION

    def draw(self):
        """
        뷰포트에 측정선과 포인트를 그립니다.
        """
        self._color = self._get_display_color()

        self._root.clear()
        self._ui_lines.clear()  # 이전 선들 제거

        with self._root:
            # 포인트가 2개 이상일 때만 선 그리기
            if len(self._points) >= 2:
                positions = [point.value for point in self._points]
                self._ui_points = sc.Points(
                    positions,
                    sizes=[5] * len(positions),
                    colors=[self._color] * len(positions)
                )

                # 포인트들을 연결하는 선 그리기
                for i in range(len(positions) - 1):
                    line = sc.Line(
                        positions[i],
                        positions[i + 1],
                        color=self._color,
                        thickness=3
                    )
                    self._ui_lines.append(line)

    # Input Handling
    def _on_moved(self, coords: Sequence[float], result: omni.kit.raycast.query.RayQueryResult):
        """
        마우스 이동 시 호출되는 콜백

        Args:
            coords: 마우스 좌표
            result: 레이캐스트 결과
        """
        if self.creation_state in [MeasureCreationState.START_SELECTION, MeasureCreationState.END_SELECTION]:
            # 스냅 위치 가져오기
            self._snap_data: Optional[Dict[str, Any]] = MeasureSnapProviderManager().get_snap_position(coords, result)

            if not self._snap_data:
                self._set_snap_marker_position(None)
                return

            # 스냅 마커 위치 업데이트
            snap_type: SnapMode = self._snap_data["type"]
            snap_position: List[float] = [*self._snap_data["position"]]
            self._set_snap_marker_position(Gf.Vec3d(*snap_position), snap_type)

    def _on_clicked(self, coords: Sequence[float], mouse_button: int = 0):
        """
        뷰포트 클릭 시 콜백. 측정은 생성하지 않음.
        BBox 측정은 'BBox 측정' UI 버튼 클릭으로만 수행됩니다.
        """
        if mouse_button != 0:
            self.reset()

    def _on_save(self):
        """
        측정을 저장합니다.
        """
        if len(self._points) < 2:
            return

        display_panel = ReferenceManager().ui_display_panel

        payload: MeasurePayload = MeasurePayload()
        payload.prim_paths = [prim.path for prim in self._prims if prim.path]
        payload.points = MeasurePayload.world_to_local_points(
            [point.vector for point in self._points],
            payload.prim_paths
        )
        payload.tool_mode = MeasureMode.MESH
        payload.axis_display = display_panel.display_axis
        payload.unit_type = display_panel.unit
        payload.precision = display_panel.precision
        payload.label_size = display_panel.text_size
        payload.label_color = display_panel.color

        # 측정값 계산 (나중에 구현)
        # payload.primary = self._calculate_mesh_measurement()

        MeasurementManager().create(payload)
