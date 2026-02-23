# Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.
#

"""
측정 도구에서 사용하는 상수 및 열거형 정의

이 모듈에는 측정 도구의 모든 상수, 열거형(Enum) 값들이 정의되어 있습니다.
측정 모드, 단위, 스냅 모드, 상태 머신 상태 등을 포함합니다.
"""

from enum import Enum

# ===== 확장 프로그램 이름 =====
EXTENSION_NAME = "Measure"  # 확장 프로그램의 표시 이름


# ===== 전역 상수 및 열거형 =====

class DisplayAxisSpace(Enum):
    """
    축 표시 공간 타입

    측정값을 표시할 때 사용하는 좌표계를 정의합니다.
    """
    NONE = 0      # 축 표시 안 함
    WORLD = 1     # 월드 좌표계 (전역 좌표)
    LOCAL = 2     # 로컬 좌표계 (프림의 로컬 좌표)


class MeasureAxis(Enum):
    """
    측정 축 타입

    X, Y, Z 축 중 어느 축의 거리를 표시할지 정의합니다.
    """
    NONE = 0  # 축 없음
    X = 1     # X축
    Y = 2     # Y축
    Z = 3     # Z축


class UnitType(str, Enum):
    """
    측정 단위 타입

    미터법 및 영국 단위계를 지원합니다.
    """
    CENTIMETERS = "cm"    # 센티미터
    MILLIMETERS = "mm"    # 밀리미터
    DECIMETERS = "dm"     # 데시미터
    METERS = "m"          # 미터
    KILOMETERS = "km"     # 킬로미터
    INCHES = "in"         # 인치
    FEET = "ft"           # 피트
    MILES = "mi"          # 마일


# ===== 상태 머신 관련 열거형 =====

class MeasureEditState(Enum):
    """
    측정 편집 상태

    측정을 편집할 때 어떤 요소를 편집 중인지 나타냅니다.
    """
    SELECTED = 0  # 선택됨
    POINT = 1     # 포인트 편집 중
    LABEL = 2     # 라벨 편집 중
    POSITION = 3  # 위치 편집 중


class MeasureCreationState(Enum):
    """
    측정 생성 상태

    측정을 생성하는 과정의 단계를 나타냅니다.
    """
    NONE = -1                  # 생성 중이 아님
    START_SELECTION = 0        # 시작점 선택 중
    INTERMEDIATE_SELECTION = 1 # 중간점 선택 중 (Multi-Point, Area 등)
    END_SELECTION = 2          # 끝점 선택 중
    FINALIZE = 3               # 완료 단계


class MeasureMode(Enum):
    """
    측정 모드 타입
    
    사용 가능한 모든 측정 도구 모드를 정의합니다.
    """
    NONE = -1          # 모드 없음
    MESH = -2          # 메시 측정 (새로운 기능)
    POINT_TO_POINT = 0 # 점 대 점 측정 (두 점 사이의 거리)
    MULTI_POINT = 1    # 다중 점 측정 (여러 점을 연결한 총 거리)
    ANGLE = 2          # 각도 측정 (세 점으로 이루어진 각도)
    DIAMETER = 3       # 직경 측정 (원형 객체의 직경)
    AREA = 4           # 면적 측정 (다각형 영역의 면적)
    VOLUME = 5         # 부피 측정 (미구현)
    SELECTED = 6       # 선택된 프림 간 측정 (두 프림 사이의 최소/최대/중심 거리)


class MeasureState(Enum):
    """
    측정 도구의 전체 상태

    측정 도구가 현재 어떤 작업을 수행 중인지 나타냅니다.
    """
    NONE = 0   # 비활성 상태
    CREATE = 1 # 측정 생성 중
    EDIT = 2   # 측정 편집 중


# ===== UI 관련 열거형 =====

class ConstrainAxis(Enum):
    """
    제약 축 타입 (Area 측정에서 사용)

    Area 측정 시 측정이 제한될 평면을 정의합니다.
    """
    X = 0        # X축 평면
    Y = 1        # Y축 평면
    Z = 2        # Z축 평면
    STAGE_UP = 3 # 스테이지의 Up 축 (일반적으로 Y 또는 Z)
    DYNAMIC = 4  # 동적 (첫 세 점으로 평면 결정)


class ConstrainMode(Enum):
    """
    제약 모드 타입

    측정이 제한되는 방식을 정의합니다.
    """
    DEFAULT = 0    # 기본 모드
    VIEW_PLANE = 1 # 뷰 평면에 제한


class DistanceType(Enum):
    """
    거리 계산 타입 (SELECTED 모드에서 사용)

    두 프림 사이의 거리를 계산하는 방식을 정의합니다.
    """
    MIN = 0    # 최소 거리 (가장 가까운 두 점 사이)
    MAX = 1    # 최대 거리 (가장 먼 두 점 사이)
    CENTER = 2 # 중심 거리 (두 프림의 중심점 사이)


class LabelSize(Enum):
    """
    라벨 크기 타입

    측정값을 표시하는 라벨의 크기를 정의합니다.
    """
    SMALL = 12        # 작음 (12pt)
    MEDIUM = 15       # 중간 (15pt)
    LARGE = 18        # 큼 (18pt)
    EXTRA_LARGE = 21  # 매우 큼 (21pt)


# 라벨 크기에 따른 스케일 매핑 (뷰포트에서의 실제 크기 조정에 사용)
LABEL_SCALE_MAPPING = {
    LabelSize.SMALL: 1,
    LabelSize.MEDIUM: 1.25,
    LabelSize.LARGE: 1.75,
    LabelSize.EXTRA_LARGE: 2.25
}


class Precision(str, Enum):
    """
    측정값 정밀도 타입

    측정값을 표시할 때 소수점 이하 몇 자리까지 표시할지 정의합니다.
    """
    INTEGER = "#"              # 정수 (소수점 없음)
    TENTH = "#.0"              # 소수점 첫째 자리
    HUNDRETH = "#.00"           # 소수점 둘째 자리
    THOUSANDTH = "#.000"        # 소수점 셋째 자리
    TEN_THOUSANDTH = "#.0000"    # 소수점 넷째 자리
    HUNDRED_THOUSANDTH = "#.00000"  # 소수점 다섯째 자리


class SnapMode(Enum):
    """
    스냅 모드 타입

    측정 포인트를 배치할 때 스냅할 대상 요소를 정의합니다.
    """
    NONE = 0      # 스냅 없음
    SURFACE = 1   # 표면에 스냅
    VERTEX = 2    # 정점에 스냅
    PIVOT = 3     # 피벗 포인트에 스냅
    EDGE = 4      # 엣지에 스냅
    MIDPOINT = 5  # 중점에 스냅
    CENTER = 6    # 중심점에 스냅


class SnapTo(Enum):
    """
    스냅 대상 타입

    특수한 스냅 방식을 정의합니다.
    """
    CUSTOM = 0        # 사용자 정의
    PERPENDICULAR = 1 # 수직 스냅 (Point-to-Point 모드에서 사용)


# ===== 스냅 관련 상수 =====
SNAP_DISTANCE = 100  # 스냅 감지 거리 (픽셀 단위)


# ===== 핫키 컨텍스트 =====
MEASURE_WINDOW_VISIBLE_CONTEXT = "omni.kit.tool.measure-window-visible"  # 측정 창이 보일 때 활성화되는 핫키 컨텍스트
