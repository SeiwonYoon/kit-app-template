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
from omni import ui
from omni.ui import scene as sc
from pxr import Gf, Sdf, Usd, UsdGeom

from ...common import (
    DisplayAxisSpace,
    LabelSize,
    MeasureCreationState,
    MeasureMode,
    Precision,
    SnapMode,
    UnitType,
)
from ...common.utils import get_stage_meters_per_unit
from ...manager import MeasurementManager, ReferenceManager
from ...system import MeasurePayload
from ..manipulator_items import *
from ..snap.manager import MeasureSnapProviderManager
from .viewport_mode_model import ViewportModeModel


# -----------------------------------------------------------------------------
# UI 버튼 클릭 시 호출되는 모듈 수준 함수 (선택된 메시에 대해 BBox X/Y/Z 측정 생성)
# -----------------------------------------------------------------------------

def _collect_mesh_prims(prim: Usd.Prim, root_path: Optional[Sdf.Path] = None) -> List[Usd.Prim]:
    """
    프림과 그 하위의 모든 Mesh 프림을 수집합니다 (통합 바운딩용). Camera 프림은 제외합니다.

    - root_path: BBox 기준이 되는 루트 prim 경로.
      이 루트의 자식 중에서 별도 usd 에셋(Reference/Payload) 루트인 경우는
      상위 에셋 BBox 계산 시 포함하지 않기 위해 재귀 탐색을 막습니다.
      예) Automatic_riveting_and_brushing_machine 아래의 Automatic_4_Station_* 은
          별도 에셋이므로, Automatic_riveting_and_brushing_machine BBox에는 포함되지 않도록 함.
    """
    if root_path is None:
        root_path = prim.GetPath()

    result: List[Usd.Prim] = []
    if prim.IsA(UsdGeom.Camera):
        return result
    if prim.IsA(UsdGeom.Mesh):
        result.append(prim)

    for child in prim.GetChildren():
        if child.IsA(UsdGeom.Camera):
            continue
        # 루트 바로 아래에 있는 별도 usd 에셋(Reference/Payload) 루트는 상위 에셋 BBox에서 제외
        if (
            child.GetPath() != root_path
            and (child.HasAuthoredReferences() or child.HasAuthoredPayloads())
        ):
            continue
        result.extend(_collect_mesh_prims(child, root_path))
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
    axis_index: int = -1,
    dimension_level: int = 0,
) -> None:
    """PointToPoint 측정을 생성합니다. dimension_level: 0=전체(가장 바깥), 1+=하위프림(안쪽으로 겹치지 않게)."""
    display_panel = ReferenceManager().ui_display_panel
    payload = MeasurePayload()
    payload.prim_paths = [prim_path, prim_path]
    payload.points = MeasurePayload.world_to_local_points(
        [start_point, end_point], payload.prim_paths
    )
    payload.tool_mode = MeasureMode.MESH
    # tool_sub_mode: axis_index(0,1,2) + dimension_level*10 → 겹치지 않는 오프셋 레벨
    payload.tool_sub_mode = axis_index + dimension_level * 10
    payload.axis_display = display_panel.display_axis if display_panel else DisplayAxisSpace.NONE
    payload.unit_type = UnitType.CENTIMETERS  # BBox 측정은 항상 cm 단위로 표시
    # 수치선 이름: 참조한 Mesh prim 이름 + 축 (트리뷰에서 확인 가능)
    prim_name = Sdf.Path(prim_path).name
    payload.name = f"{prim_name} ({'XYZ'[axis_index]})" if axis_index >= 0 else prim_name
    payload.precision = display_panel.precision if display_panel else Precision.HUNDRETH
    payload.label_size = display_panel.text_size if display_panel else LabelSize.MEDIUM
    payload.label_color = label_color if label_color is not None else (display_panel.color if display_panel else Gf.Vec4f(0.15, 0.15, 0.15, 1.0))
    MeasurementManager().create(payload)


# CAD 스타일: 얇은 선과 시인성 좋은 진한 회색 (밝은 배경에서 명확히 보임)
_AXIS_LABEL_COLORS = {
    0: Gf.Vec4f(0.3, 0.3, 0.3, 1.0),  # X: 진한 회색
    1: Gf.Vec4f(0.3, 0.3, 0.3, 1.0),  # Y: 진한 회색
    2: Gf.Vec4f(0.3, 0.3, 0.3, 1.0),  # Z: 진한 회색
}


def _create_bbox_measurement_single_prim(
    prim_path: str,
    mn: Gf.Vec3d,
    mx: Gf.Vec3d,
    dimension_level: int,
) -> None:
    """한 prim에 X/Y/Z 3축을 담은 BBox 측정 1개 생성. Manage 패널에서 하위 탭 X,Y,Z로 표시됨."""
    # 6 points: X(start,end), Y(start,end), Z(start,end) — MeshBBoxCompute와 순서 일치
    x_start = Gf.Vec3d(mx[0], mx[1], mn[2])
    x_end = Gf.Vec3d(mx[0], mn[1], mn[2])
    y_start = Gf.Vec3d(mx[0], mx[1], mn[2])
    y_end = Gf.Vec3d(mn[0], mx[1], mn[2])
    z_start = Gf.Vec3d(mn[0], mx[1], mn[2])
    z_end = Gf.Vec3d(mn[0], mx[1], mx[2])
    world_pts = [x_start, x_end, y_start, y_end, z_start, z_end]
    prim_paths = [prim_path] * 6
    local_pts = MeasurePayload.world_to_local_points(world_pts, prim_paths)
    if len(local_pts) != 6:
        return
    display_panel = ReferenceManager().ui_display_panel
    payload = MeasurePayload()
    payload.prim_paths = prim_paths
    payload.points = local_pts
    payload.tool_mode = MeasureMode.MESH
    payload.tool_sub_mode = dimension_level * 10  # 오프셋 레벨만 (축은 0,1,2가 6 points에 내장)
    payload.axis_display = display_panel.display_axis if display_panel else DisplayAxisSpace.NONE
    payload.unit_type = UnitType.CENTIMETERS
    prim_name = Sdf.Path(prim_path).name
    payload.name = prim_name
    payload.precision = display_panel.precision if display_panel else Precision.HUNDRETH
    payload.label_size = display_panel.text_size if display_panel else LabelSize.MEDIUM
    payload.label_color = display_panel.color if display_panel else Gf.Vec4f(0.15, 0.15, 0.15, 1.0)
    MeasurementManager().create(payload)

# 하위 프림 치수선: 전체와 다르면서 큼직한 부분만 표시 (세부 치수 제외)
# _SUB_PRIM_MAX_RATIO = 0.95   # 95% 이상이면 전체와 동일로 간주, 생략
# _SUB_PRIM_MIN_RATIO = 0.05   # 5% 미만이면 너무 작은 세부 부분, 생략
# _SUB_PRIM_MIN_ABSOLUTE_CM = 10  # 10cm 이하 치수는 표시 안 함 (cm 단위 기준)


def _get_sub_prims_with_bbox(
    root_prim: Usd.Prim,
    bbox_cache: UsdGeom.BBoxCache,
    max_depth: int = 1,  # 0이면 자식 탐색 안 함, 1 이상이면 해당 깊이까지 탐색
    depth: int = 0,
) -> List[tuple]:
    """
    Mesh를 가진 프림과 그 bbox (mn, mx) 목록 반환.
    max_depth가 0이면 자식 탐색 없이 빈 목록 반환.
    max_depth >= 1이면 직접 자식만(또는 해당 깊이까지) 탐색.
    반환: [(child_prim, mn, mx), ...]
    """
    if max_depth == 0:
        return []
    if depth >= max_depth:
        return []
    result: List[tuple] = []
    for child in root_prim.GetChildren():
        if child.IsA(UsdGeom.Camera):
            continue
        meshes = _collect_mesh_prims(child, root_prim.GetPath())
        if not meshes:
            continue
        bbox = _compute_combined_bbox(bbox_cache, meshes)
        if bbox:
            result.append((child, bbox[0], bbox[1]))
    if len(result) >= 2:
        return result
    if len(result) == 1 and depth + 1 < max_depth:
        sub = _get_sub_prims_with_bbox(result[0][0], bbox_cache, max_depth, depth + 1)
        if sub:
            return sub
    return result


def _create_axis_measurement(
    prim_path: str,
    axis_index: int,
    start_pt: Gf.Vec3d,
    end_pt: Gf.Vec3d,
    dimension_level: int,
) -> None:
    """단일 축 치수선 생성 (X=0, Y=1, Z=2)."""
    if (end_pt - start_pt).GetLength() < 1e-9:
        return
    _create_point_to_point_measurement_impl(
        prim_path,
        start_pt,
        end_pt,
        label_color=_AXIS_LABEL_COLORS.get(axis_index),
        axis_index=axis_index,
        dimension_level=dimension_level,
    )


def _create_bbox_axis_measurements_impl(root_prim: Usd.Prim, max_depth: int = 0) -> None:
    """
    전체 바운딩 박스 + 하위 프림별 치수선을 생성합니다.
    선택 prim이 Mesh이면 부모 Xform을 보지 않고 해당 Mesh만 BBox 측정 (단일 큐브/복잡한 USD 하위 Mesh 동일).
    Xform 등이면 max_depth가 0일 때 루트 전체 bbox만, max_depth >= 1이면 직접 자식까지 탐색합니다.
    """
    prim_path = str(root_prim.GetPath())
    try:
        # 선택 prim이 Mesh인 경우: 부모 Xform을 보지 않고 해당 Mesh만 BBox 측정 (단일 큐브 등 동일 동작)
        if root_prim.IsA(UsdGeom.Mesh):
            bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
            combined = _compute_combined_bbox(bbox_cache, [root_prim])
            if not combined:
                return
            mn, mx = combined[0], combined[1]
            stage = root_prim.GetStage()
            mpu = UsdGeom.GetStageMetersPerUnit(stage) if stage else get_stage_meters_per_unit()
            if mpu is None or mpu <= 0:
                mpu = 0.01
            overall_extent = (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])
            overall_ext_cm = [
                overall_extent[i] * mpu * 100.0 if overall_extent[i] >= 1e-9 else 0.0
                for i in (0, 1, 2)
            ]
            _create_bbox_measurement_single_prim(prim_path, mn, mx, 0)
            return

        mesh_prims = _collect_mesh_prims(root_prim, root_prim.GetPath())
        if not mesh_prims:
            return
        bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        combined = _compute_combined_bbox(bbox_cache, mesh_prims)
        if not combined:
            return
        mn, mx = combined[0], combined[1]
        overall_extent = (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])

        # 하위 프림 목록을 먼저 구함 (실질 메시가 한 자식에만 있을 때 상위 치수선 제외 판단용)
        sub_prims = _get_sub_prims_with_bbox(root_prim, bbox_cache, max_depth)
        # 실질 Mesh가 한 개의 직접 자식(예: Default)에만 있으면 상위 prim 치수선은 생성하지 않음
        skip_overall = False
        if len(sub_prims) == 1:
            _, smn, smx = sub_prims[0]
            tol = 1e-6
            if (
                abs(smn[0] - mn[0]) <= tol and abs(smn[1] - mn[1]) <= tol and abs(smn[2] - mn[2]) <= tol
                and abs(smx[0] - mx[0]) <= tol and abs(smx[1] - mx[1]) <= tol and abs(smx[2] - mx[2]) <= tol
            ):
                skip_overall = True

        stage = root_prim.GetStage()
        mpu = UsdGeom.GetStageMetersPerUnit(stage) if stage else get_stage_meters_per_unit()
        if mpu is None or mpu <= 0:
            mpu = 0.01  # cm 기본값
        overall_ext_cm = [
            overall_extent[i] * mpu * 100.0 if overall_extent[i] >= 1e-9 else 0.0
            for i in (0, 1, 2)
        ]

        # 1. 전체 바운딩 박스 치수선. 실질 메시가 한 자식에만 있으면 상위는 제외.
        if not skip_overall:
            # if not any(ecm <= _SUB_PRIM_MIN_ABSOLUTE_CM for ecm in overall_ext_cm if ecm > 0):
            _create_bbox_measurement_single_prim(prim_path, mn, mx, 0)

        # 2. 하위 프림별 치수선. max_depth==0이면 _SUB_PRIM_* 조건 없이 모두 표시
        apply_filters = max_depth != 0
        for level, (child_prim, smn, smx) in enumerate(sub_prims):
            child_path = str(child_prim.GetPath())
            sub_extent = (smx[0] - smn[0], smx[1] - smn[1], smx[2] - smn[2])
            dim_level = level + 1

            # if apply_filters:
            #     # 한 축이라도 10cm 이하면 해당 하위 프림 전체 생략 (비율과 무관하게 sub의 모든 축 검사)
            #     skip_this_sub = False
            #     for axis in (0, 1, 2):
            #         if sub_extent[axis] < 1e-9:
            #             continue
            #         ext_cm = sub_extent[axis] * mpu * 100.0
            #         if ext_cm <= _SUB_PRIM_MIN_ABSOLUTE_CM:
            #             skip_this_sub = True
            #             break
            #     if skip_this_sub:
            #         continue

            # 표시할 축 후보 수집 (max_depth==0이면 비율/최소길이 조건 없이 전체 축)
            candidate_axes: List[tuple] = []
            for axis in (0, 1, 2):
                if overall_extent[axis] < 1e-9 or sub_extent[axis] < 1e-9:
                    continue
                ext = sub_extent[axis]
                ext_cm = ext * mpu * 100.0
                # if apply_filters:
                #     ratio = ext / overall_extent[axis]
                #     if ratio >= _SUB_PRIM_MAX_RATIO:
                #         continue
                #     if ratio < _SUB_PRIM_MIN_RATIO:
                #         continue
                #     if ext_cm <= _SUB_PRIM_MIN_ABSOLUTE_CM:
                #         continue
                candidate_axes.append((axis, ext_cm))

            # if apply_filters and any(ecm <= _SUB_PRIM_MIN_ABSOLUTE_CM for _, ecm in candidate_axes):
            #     continue
            # 조건 만족(또는 max_depth==0) → 하위 탭 1 prim (X,Y,Z) 생성
            _create_bbox_measurement_single_prim(child_path, smn, smx, dim_level)

        # TODO: 흰색 AABB 와이어프레임 박스 - 추후 구현 예정 (BBoxWireframeOverlayItem)
    except Exception:
        pass


def run_mesh_bbox_measurement_for_selection() -> None:
    """
    현재 선택된 각 프림에 대해, 해당 prim과 하위 모든 Mesh를 합친 통합 바운딩 박스로
    X/Y/Z 축 PointToPoint 측정을 생성합니다. 'BBox 측정' UI 버튼 클릭 시 호출됩니다.
    """
    ctx = ou.get_context()
    stage = ctx.get_stage()
    if not stage:
        return
    selected_paths = ctx.get_selection().get_selected_prim_paths()
    if not selected_paths:
        return
    for path in selected_paths:
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            continue
        if prim.IsA(UsdGeom.Camera):
            continue
        _create_bbox_axis_measurements_impl(prim, max_depth=0)  # 직접 자식만 하위 치수선으로 표시


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
        payload.unit_type = UnitType.CENTIMETERS  # BBox 측정은 항상 cm 단위로 표시
        payload.precision = display_panel.precision
        payload.label_size = display_panel.text_size
        payload.label_color = display_panel.color

        # 측정값 계산 (나중에 구현)
        # payload.primary = self._calculate_mesh_measurement()

        MeasurementManager().create(payload)
