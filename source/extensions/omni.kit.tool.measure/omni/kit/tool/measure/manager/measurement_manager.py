# Copyright (c) 2023, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.
#

__all__ = ["MeasurementManager"]

import asyncio
from bisect import bisect_left
from functools import partial
from typing import List

import carb.profiler
import omni.kit.commands
import omni.kit.commands as cmds
import omni.kit.undo as undo
import omni.usd as ou
from carb.events import IEvent
from omni.kit.app import get_app
from omni.kit.async_engine import run_coroutine
from omni.kit.usd.layers import LayerEventType
from pxr import Sdf, UsdGeom

from ..common.notification import post_disreguard_future_notification
from ..system import MeasurementModel, MeasurePayload, MeasurePrim
from .reference_manager import ReferenceManager
from .state_machine import StateMachine

# 측정 프림에서 추적하는 속성 목록
# 이 속성들이 변경되면 측정값을 다시 계산해야 합니다.
ATTR_LIST = [
    ".measure:meta:prim_paths",      # 측정 대상 프림 경로들
    # ".measure:meta:prim_paths:relationship",
    ".measure:meta:points",          # 측정 포인트들 (월드 좌표)
    ".measure:meta:local_points",     # 측정 포인트들 (로컬 좌표)
    # ".measure:meta:tool_mode",
    ".measure:meta:tool_sub_mode",   # 측정 도구 서브 모드 (예: DistanceType)
    ".measure:compute:primary",       # 주 측정값 (거리, 각도 등)
    ".measure:compute:secondary",     # 보조 측정값 (각도 측정의 경우 두 번째 각도)
    ".measure:prop:visible",          # 표시 여부
    ".measure:prop:axis_display",     # 축 표시 타입 (WORLD/LOCAL/NONE)
    ".measure:prop:unit",             # 단위 타입
    ".measure:prop:precision",        # 정밀도
    ".measure:prop:label_size",      # 라벨 크기
    ".measure:prop:label_color",      # 라벨 색상
]


class MeasurementManager:
    """
    측정 데이터 관리자 (싱글톤)

    측정 프림의 생성, 수정, 삭제, 조회를 담당하는 중앙 관리 클래스입니다.
    USD 스테이지의 변경 사항을 감지하고 측정값을 자동으로 업데이트합니다.

    주요 기능:
    - 측정 프림의 CRUD 작업 (Create, Read, Update, Delete)
    - USD 스테이지 이벤트 구독 및 처리
    - 측정값 자동 업데이트 (프림 변환 변경 시)
    - 라이브 세션 지원
    """
    # 모든 측정 프림의 루트 경로
    __root_prim_path: Sdf.Path = Sdf.Path("/Viewport_Measure")

    def __singleton_init__(self):
        """
        싱글톤 초기화 메서드

        이벤트 구독, 모델 초기화, 명령어 콜백 등록을 수행합니다.
        """
        # 읽기 전용 경고 표시 여부
        self.__read_only_dismissed: bool = False
        # 측정 데이터 모델 초기화
        self._model = MeasurementModel()

        # 스테이지 이벤트 구독
        # 스테이지가 닫힐 때 모델 초기화
        self._closed_id: int = StateMachine().subscribe_to_stage_event(self._model.clear, ou.StageEventType.CLOSED)
        # 스테이지가 열릴 때 리셋 수행
        self._opened_id: int = StateMachine().subscribe_to_stage_event(self.__reset, ou.StageEventType.OPENED)

        # 라이브 세션 상태 추적
        self._in_live_session: bool = False
        # 레이어 이벤트 구독: 프림 스펙 변경 감지
        self.__layers_sub = StateMachine().subscribe_to_layer_event(
            self.__on_prim_spec_event, LayerEventType.PRIM_SPECS_CHANGED
        )
        # 편집 타겟 변경 감지 (라이브 세션 진입/퇴장 시)
        self.__edit_target_sub = StateMachine().subscribe_to_layer_event(
            self.__on_edit_target_changed, LayerEventType.EDIT_TARGET_CHANGED
        )

        # 변경된 경로들을 배치로 처리하기 위한 대기 목록
        self.__pending_changed_paths: set[Sdf.Path] = set()
        # 비동기 작업 태스크
        self.__objects_changed_task: asyncio.Task = None
        # USD 변경 무시 플래그 (자체 변경으로 인한 재귀 호출 방지)
        self.__ignore_usd_changes = False
        # 등록된 명령어 콜백 ID 목록
        self._command_callback_ids = []

        # 스테이지 객체 변경 리스너 구독
        self.__stage_sub = StateMachine().subscribe_to_stage_listener(self.__on_objects_changed)

        # 프림 삭제 명령어에 대한 콜백 등록
        # 삭제 전: 측정 프림을 모델에서 제거하고 언두를 위해 저장
        # 언두 후: 측정 프림 복원
        for command in ["DeletePrims"]:
            command_callback_id = omni.kit.commands.register_callback(
                command, omni.kit.commands.PRE_DO_CALLBACK, self.__on_pre_remove_prim_do
            )
            self._command_callback_ids.append(command_callback_id)

            command_callback_id = omni.kit.commands.register_callback(
                command, omni.kit.commands.POST_UNDO_CALLBACK, self.__on_post_remove_prim_undo
            )
            self._command_callback_ids.append(command_callback_id)

    def __del__(self):
        if self.__objects_changed_task and not self.__objects_changed_task.done():
            self.__objects_changed_task.cancel()
        self.__objects_changed_task = None

        for command_callback_id in self._command_callback_ids:
            omni.kit.commands.unregister_callback(command_callback_id)

        if self._closed_id is not None:
            StateMachine().unsubscribe_to_stage_event(self._closed_id, ou.StageEventType.CLOSED)
            self._closed_id = None

        if self._opened_id is not None:
            StateMachine().unsubscribe_to_stage_event(self._opened_id, ou.StageEventType.OPENED)
            self._opened_id = None

        if self.__layers_sub is not None:
            StateMachine().unsubscribe_to_layer_event(self.__layers_sub, LayerEventType.PRIM_SPECS_CHANGED)
            self.__layers_sub = None

        if self.__edit_target_sub is not None:
            StateMachine().unsubscribe_to_layer_event(self.__edit_target_sub, LayerEventType.EDIT_TARGET_CHANGED)
            self.__edit_target_sub = None

        if self.__stage_sub is not None:
            StateMachine().unsubscribe_to_stage_listener(self.__stage_sub)
            self.__stage_sub = None

    def destroy(self):
        self.__del__()

    @classmethod
    def deinit(cls):
        cls._instance.destroy()
        del cls._instance

    @property
    def selected(self) -> List[MeasurePrim]:
        return self._model.get_selected()

    def __new__(cls):
        if not hasattr(cls, "_instance"):
            cls._instance = super().__new__(cls)
            cls._instance.__singleton_init__()
        return cls._instance

    def __reset(self) -> None:
        """
        스테이지가 열릴 때 호출되는 리셋 메서드

        새로운 스테이지에 대해 모든 측정 데이터를 초기화하고
        스테이지에서 기존 측정 프림을 로드합니다.
        """
        # 진행 중인 비동기 작업 취소
        if self.__objects_changed_task and not self.__objects_changed_task.done():
            self.__objects_changed_task.cancel()
        self.__objects_changed_task = None
        # 대기 중인 변경 경로 목록 초기화
        self.__pending_changed_paths.clear()
        # 모델 초기화
        self._model.clear()
        # 읽기 전용 경고 플래그 리셋
        self.__read_only_dismissed = False
        # 뷰포트 씬 초기화
        ReferenceManager().measure_scene.clear()
        # 스테이지에서 기존 측정 프림 로드
        self._populate_model_from_stage()

        # 루트 프림에 삭제 방지 메타데이터 설정
        root_prim = ou.get_context().get_stage().GetPrimAtPath(self.__root_prim_path)
        if root_prim:
            root_prim.SetMetadata("no_delete", True)
            # 모든 측정 프림에 삭제 방지 메타데이터 설정
            # (라이브 세션 진입/퇴장 시 잠금 해제됨)
            for measure_prim in self._model.get_items():
                measure_prim._prim.SetMetadata("no_delete", True)

    def __on_read_only_notif(self):
        self.__read_only_dismissed = True

    def __on_edit_target_changed(self, payload, in_session: bool):
        if self._in_live_session == in_session:
            return
        self._in_live_session = in_session
        self.__reset()

    def __on_pre_remove_prim_do(self, params):
        paths = params.get("paths", None)  # List[Union[str, Sdf.Path]]

        # dedup the measure prims
        delete_measure_prims: set["MeasurPrim"] = set()
        prim_to_measure = self._model.prim_paths_to_measure_map

        for path in paths:
            if Sdf.Path(path) != self.__root_prim_path:
                for i in range(len(prim_to_measure)):
                    measure_prim_path, measure_prim = prim_to_measure[i]
                    if measure_prim_path == Sdf.Path(path):
                        delete_measure_prims.add(measure_prim)

        for measure in delete_measure_prims:
            measure_prim: MeasurePrim = self.read(measure.uuid)
            if self._model.remove(measure.uuid):
                ReferenceManager().measure_scene.delete(measure.uuid)
        if delete_measure_prims:
            omni.kit.commands.execute("_RestoreMeasurementOnUndo", measurements=list(delete_measure_prims))

    def __on_post_remove_prim_undo(self, params):
        paths = params.get("paths", None)  # List[Union[str, Sdf.Path]]

        if not paths:
            return

    def __on_prim_spec_event(self, payload, in_session: bool):
        if not payload:
            return
        dirty_specs = []
        for _, specs in payload.layer_spec_paths.items():
            for spec in specs:
                spec_path = Sdf.Path(spec)
                if spec_path.GetParentPath() == self.__root_prim_path:
                    prim = ou.get_context().get_stage().GetPrimAtPath(spec_path.GetPrimPath())
                    if not prim:
                        # Check to see if path is in any of the model items
                        for m_prim in self._model.get_item_children(None):
                            if spec_path == m_prim.path:
                                self.remove_measure_prim(m_prim.uuid)
                                break
                    else:
                        asyncio.ensure_future(self.add_measure_prim(spec_path))

            dirty_specs.extend([Sdf.Path(spec) for spec in specs if spec.startswith("/Viewport_Measure")])

    def __on_objects_changed(self, notice) -> None:
        if not notice:
            return

        if self.__ignore_usd_changes:
            return

        self.__pending_changed_paths.update(notice.GetChangedInfoOnlyPaths())

        # collect all changed paths in this frame and process them in batch
        if not self.__objects_changed_task or self.__objects_changed_task.done():
            self.__objects_changed_task = run_coroutine(self._process_pending_changed_path())

    # TODO: Clean up the logic here to try and simplify
    @carb.profiler.profile
    async def _process_pending_changed_path(self):
        await get_app().next_update_async()  # Allow for fabric to settle before updating measurements

        changed_paths = self.__pending_changed_paths.copy()
        self.__pending_changed_paths.clear()
        stage = ou.get_context().get_stage()

        # dedup the measure prims
        updated_measure_prims: set["MeasurePrim"] = set()
        prim_to_measure = self._model.prim_paths_to_measure_map

        for path in changed_paths:
            prim_path = path.GetPrimPath()
            if prim_path.GetParentPath() != self.__root_prim_path:
                # Check if the prim is a property with transformation attribute change
                if path.IsPropertyPath() and UsdGeom.Xformable.IsTransformationAffectedByAttrNamed(path.name):
                    # This is to emulate the std::set::lower_bound to find all measure_prims who's prim_paths or their
                    # ancestor paths has changed
                    index = bisect_left(prim_to_measure, prim_path, key=lambda r: r[0])
                    # If we've found a index, it means the prim_path or its descendants can affect a measure_prim.
                    # iterate through all of them until measure_prim_path is no longer a descendent of prim_path
                    for i in range(index, len(prim_to_measure)):
                        measure_prim_path, measure_prim = prim_to_measure[i]
                        if measure_prim_path.HasPrefix(prim_path):
                            updated_measure_prims.add(measure_prim)
                        else:
                            break
            else:
                prim = stage.GetPrimAtPath(prim_path)
                if not prim.IsValid():
                    continue

                measure_prim = self._model.get_item(prim.GetAttribute("measure:uuid").Get())
                if measure_prim is None:
                    continue  # Catches a new measurement before its stored in the model.

                elif path.IsPropertyPath():
                    # Update the measurement from the prim. This means:
                    # A) It exists in stage as a child of the root, B) It exists in the model
                    updated_measure_prims.add(measure_prim)

        # _model.update triggers USD changes since it writes to USD
        # don't double handle the changes we made just now
        was_ignoring_usd_changes = self.__ignore_usd_changes
        self.__ignore_usd_changes = True

        for measure_prim in updated_measure_prims:
            self._model.update(measure_prim)
        self.__ignore_usd_changes = was_ignoring_usd_changes

    def _populate_model_from_stage(self) -> None:
        """
        스테이지에서 기존 측정 프림을 로드하여 모델에 추가합니다.

        USD 파일을 열 때 기존에 저장된 측정 데이터를 복원하기 위해 호출됩니다.
        """
        # 스테이지 확인
        stage = ou.get_context().get_stage()

        if stage == None:
            return

        # 루트 프림 확인
        root_prim = stage.GetPrimAtPath(self.__root_prim_path)
        if not root_prim.IsValid():
            return

        # 루트 프림의 모든 자식 프림을 순회하며 측정 프림 복원
        for child in root_prim.GetChildrenNames():
            # MeasurePrim 객체로 재구성
            measure_prim = MeasurePrim.from_prim(f"{self.__root_prim_path}/{child}")
            if measure_prim == None:
                continue
            # 모델에 추가
            self._model.add(measure_prim.uuid, measure_prim)
            # 뷰포트 씬에 측정선 생성
            ReferenceManager().measure_scene.create(measure_prim)

        # TODO: rebuild_bbox_wireframes_from_measurements - 흰색 AABB 와이어프레임 복원 (추후 구현)

    def _create_internal(self, measure_payload: MeasurePayload):
        with ReferenceManager().edit_context:
            stage = ou.get_context().get_stage()
            root_prim = stage.GetPrimAtPath("/Viewport_Measure")
            if root_prim == None:
                cmds.execute("CreatePrimCommand", prim_type="", prim_path="/Viewport_Measure")
                root_prim = stage.GetPrimAtPath("/Viewport_Measure")
                root_prim.SetMetadata("no_delete", True)

            name = f"measurement_{measure_payload.tool_mode.name.lower()}"

            prim_path = ou.get_stage_next_free_path(stage, f"/Viewport_Measure/{name}", False)
            measure_prim: MeasurePrim = MeasurePrim(prim_path, measure_payload)
            self._model.add(measure_payload.uuid, measure_prim)
            ReferenceManager().measure_scene.create(measure_prim)

    def create(self, measure_payload: MeasurePayload):
        """
        새로운 측정을 생성합니다.

        측정 페이로드를 받아 USD 명령어를 통해 측정 프림을 생성합니다.
        언두/리두를 지원합니다.

        Args:
            measure_payload: 측정 데이터를 담고 있는 페이로드 객체
        """
        # 읽기 전용 레이어 체크는 주석 처리됨 (현재는 모든 레이어에 쓰기 허용)
        # ctx = ou.get_context()
        # if not ctx.is_writable():
        #     if not self.__read_only_dismissed:
        #         layer = ctx.get_stage().GetEditTarget().GetLayer()
        #         post_disreguard_future_notification(
        #             f"{layer.GetDisplayName()} is not writable. The measurement can not be saved.",
        #             self.__on_read_only_notif,
        #         )
        #     return
        # 측정 생성 명령어 실행 (언두/리두 지원)
        cmds.execute("CreateMeasurementCommand", measure_payload=measure_payload)

    async def add_measure_prim(self, spec_path: str):
        await omni.kit.app.get_app().next_update_async()
        await omni.kit.app.get_app().next_update_async()
        measure_prim = MeasurePrim.from_prim(spec_path)
        # payload.name은 measure:prop:name에서 로드됨. 덮어쓰지 않음 (참조 Mesh prim 이름 유지)
        self._model.add(measure_prim.uuid, measure_prim)
        ReferenceManager().measure_scene.create(measure_prim)

    def remove_measure_prim(self, uuid: int):
        ReferenceManager().measure_scene.delete(uuid)
        self._model.remove(uuid)

    def set_visibility_all(self, visible: bool):
        for measure_prim in self._model.get_items():
            measure_prim.payload.visible = visible
            ReferenceManager().measure_scene.update(measure_prim.payload)

    def set_visibility(self, uuid: int, visible: bool):
        measure_prim: MeasurePrim = self.read(uuid)
        measure_prim.payload.visible = visible
        ReferenceManager().measure_scene.update(measure_prim.payload)

    def frame_measurement(self, uuid: int):
        measure_prim: MeasurePrim = self.read(uuid)
        measure_prim.frame()

    def read(self, uuid: int) -> MeasurePrim:
        return self._model.get_item(uuid)

    def rename(self, uuid: int, name: str) -> MeasurePrim:
        old_measure_prim: MeasurePrim = MeasurementManager().read(uuid)
        old_path = old_measure_prim.path
        new_path = Sdf.Path(old_path.ReplaceName(name))
        cmds.execute("MovePrim", path_from=old_path, path_to=new_path)

    def update(self, uuid: int):
        return NotImplementedError

    def delete(self, uuid: int) -> bool:
        """
        측정을 삭제합니다.

        Args:
            uuid: 삭제할 측정의 고유 ID

        Returns:
            bool: 삭제 성공 여부
        """
        measure_prim: MeasurePrim = self.read(uuid)
        if self._model.remove(uuid):
            # 뷰포트 씬에서 측정선 제거
            ReferenceManager().measure_scene.delete(uuid)
            # USD 명령어를 통해 프림 삭제 (언두/리두 지원)
            cmds.execute("RemoveMeasurementCommand", measure_prim=measure_prim)
            return True
        return False

    def delete_all(self):
        """
        모든 측정을 삭제합니다.

        언두 그룹으로 묶어서 한 번에 실행 취소할 수 있도록 합니다.
        """
        uuids = [measure_prim.uuid for measure_prim in self._model._measurements.values()]
        with undo.group():
            for uuid in uuids:
                self.delete(uuid)
