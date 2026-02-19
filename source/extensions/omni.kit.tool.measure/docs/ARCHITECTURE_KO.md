# Measure Tool 아키텍처 문서

## 개요

Measure Tool은 Omniverse Kit 기반의 3D 뷰포트에서 측정 기능을 제공하는 확장 프로그램입니다. 이 문서는 전체 시스템의 아키텍처, 컴포넌트 구조, 데이터 흐름, 주요 클래스 및 모듈에 대한 상세한 설명을 제공합니다.

## 목차

1. [시스템 개요](#시스템-개요)
2. [디렉토리 구조](#디렉토리-구조)
3. [핵심 컴포넌트](#핵심-컴포넌트)
4. [데이터 모델](#데이터-모델)
5. [상태 관리](#상태-관리)
6. [뷰포트 통합](#뷰포트-통합)
7. [이벤트 처리](#이벤트-처리)
8. [주요 워크플로우](#주요-워크플로우)

---

## 시스템 개요

### 목적

Measure Tool은 USD 스테이지 내의 프림(prim)들 사이의 거리, 각도, 면적 등을 측정하고 시각화하는 도구입니다. 측정 데이터는 USD 프림으로 저장되어 파일과 함께 보존됩니다.

### 주요 기능

- **점 대 점 측정 (Point-to-Point)**: 두 점 사이의 거리 측정
- **다중 점 측정 (Multi-Point)**: 여러 점을 연결한 총 거리 측정
- **각도 측정 (Angle)**: 세 점으로 이루어진 각도 측정
- **직경 측정 (Diameter)**: 원형 객체의 직경 측정
- **면적 측정 (Area)**: 다각형 영역의 면적 측정
- **선택된 프림 간 측정 (Selected)**: 두 프림 사이의 최소/최대/중심 거리 측정
- **메시 바운딩 박스 측정 (Mesh)**: 메시 프림의 바운딩 박스 X/Y/Z 축 측정

### 기술 스택

- **Omniverse Kit**: 확장 프로그램 프레임워크
- **USD (Universal Scene Description)**: 씬 데이터 저장 및 관리
- **Python 3.12**: 주요 프로그래밍 언어
- **Omni UI**: 사용자 인터페이스
- **Omni UI Scene**: 3D 뷰포트 렌더링

---

## 디렉토리 구조

```
omni/kit/tool/measure/
├── extension.py              # 확장 프로그램 진입점 (싱글톤)
├── common/                   # 공통 유틸리티 및 상수
│   ├── constant.py           # 모든 열거형 및 상수 정의
│   ├── commands.py           # USD 명령어 정의
│   ├── settings.py           # 사용자 설정 관리
│   ├── utils.py              # 유틸리티 함수
│   └── notification.py       # 알림 관리
├── interface/                # UI 인터페이스
│   ├── panel.py              # 메인 측정 패널
│   ├── sub_panel.py          # 서브 패널들 (Global, Placement, Display, Manage)
│   ├── _widgets.py           # 재사용 가능한 UI 위젯
│   ├── _property.py          # 속성 위젯
│   └── style.py              # UI 스타일 정의
├── manager/                  # 관리자 클래스들 (싱글톤)
│   ├── measurement_manager.py    # 측정 데이터 관리
│   ├── reference_manager.py      # 컴포넌트 간 참조 관리
│   ├── state_machine.py          # 상태 머신
│   ├── selection_state_manager.py # 선택 상태 관리
│   └── hotkeys.py                 # 핫키 관리
├── system/                   # 데이터 시스템
│   ├── _models.py            # 데이터 모델 (MeasurementModel)
│   ├── _measure_prim.py      # MeasurePrim 클래스
│   ├── _measure_payload.py   # MeasurePayload 클래스
│   ├── _measure_compute.py   # 측정값 계산 로직
│   └── export.py             # CSV 내보내기
├── viewport/                 # 뷰포트 통합
│   ├── scene.py              # MeasureScene 클래스
│   ├── manipulator.py        # 조작기 (Manipulator)
│   ├── manipulator_items.py  # 조작기 아이템들
│   ├── _drawing.py           # 측정선 그리기
│   ├── _measurement_items.py # 측정 아이템 관리
│   ├── _model.py             # 뷰포트 모델
│   ├── gesture_manager.py    # 제스처 관리
│   ├── scene_overlay.py      # 씬 오버레이
│   └── tools/                # 측정 도구 구현
│       ├── point_to_point.py # 점 대 점 도구
│       ├── multi_point.py    # 다중 점 도구
│       ├── angle.py          # 각도 도구
│       ├── diameter.py       # 직경 도구
│       ├── area.py           # 면적 도구
│       ├── mesh.py           # 메시 바운딩 박스 측정 도구
│       └── viewport_mode_model.py # 뷰포트 모드 모델
│   └── snap/                 # 스냅 시스템
│       ├── manager.py        # 스냅 관리자
│       ├── provider.py       # 스냅 제공자 인터페이스
│       ├── registry.py       # 스냅 제공자 등록
│       ├── mesh_provider.py # 메시 스냅 제공자
│       └── attribute_value_cache.py # 속성 값 캐시
└── tests/                    # 테스트 코드
    ├── test_measure_extension.py
    ├── test_measure_tools.py
    ├── test_measure_modes.py
    └── ...
```

---

## 핵심 컴포넌트

### 1. Extension (extension.py)

**역할**: 확장 프로그램의 진입점 및 생명주기 관리

**주요 책임**:
- 확장 프로그램 초기화 및 종료
- 싱글톤 패턴 구현
- UI 패널 및 뷰포트 씬 생성
- 메뉴 항목 등록
- 핫키 등록
- 속성 위젯 등록

**주요 메서드**:
- `on_startup()`: 확장 프로그램 시작 시 초기화
- `on_shutdown()`: 확장 프로그램 종료 시 정리
- `_register_hotkeys()`: 핫키 등록

### 2. MeasurementManager (manager/measurement_manager.py)

**역할**: 측정 데이터의 중앙 관리

**주요 책임**:
- 측정 프림의 CRUD 작업 (Create, Read, Update, Delete)
- USD 스테이지 이벤트 구독 및 처리
- 측정값 자동 업데이트 (프림 변환 변경 시)
- 라이브 세션 지원

**주요 메서드**:
- `create()`: 새 측정 생성
- `delete()`: 측정 삭제
- `read()`: 측정 조회
- `_populate_model_from_stage()`: 스테이지에서 기존 측정 로드
- `_process_pending_changed_path()`: 변경된 경로 배치 처리

**데이터 저장 위치**:
- 모든 측정 프림은 `/Viewport_Measure` 루트 프림 하위에 저장됨
- 각 측정은 고유한 UUID를 가짐

### 3. StateMachine (manager/state_machine.py)

**역할**: 측정 도구의 상태 관리

**주요 상태**:
- `MeasureState`: NONE, CREATE, EDIT
- `MeasureMode`: POINT_TO_POINT, MULTI_POINT, ANGLE, DIAMETER, AREA, SELECTED
- `MeasureCreationState`: START_SELECTION, INTERMEDIATE_SELECTION, END_SELECTION, FINALIZE

**주요 책임**:
- 상태 전환 관리
- 이벤트 구독 및 발행
- 스테이지 이벤트 처리
- 레이어 이벤트 처리
- 키보드 입력 처리

### 4. ReferenceManager (manager/reference_manager.py)

**역할**: 컴포넌트 간 참조 관리 (싱글톤)

**주요 참조**:
- `measure_scene`: MeasureScene 인스턴스
- `ui_panel`: MeasurePanel 인스턴스
- `ui_placement_panel`: PlacementPanel 인스턴스
- `ui_display_panel`: DisplayPanel 인스턴스
- `selection_state`: SelectionStateManager 인스턴스

**목적**: 컴포넌트 간 순환 참조를 방지하고 중앙 집중식 참조 관리

### 5. MeasureScene (viewport/scene.py)

**역할**: 뷰포트에서 측정선을 그리는 씬 관리

**주요 책임**:
- 측정선, 포인트, 라벨 렌더링
- 측정 선택 및 호버 상태 관리
- 조작기(Manipulator) 관리

**주요 메서드**:
- `create()`: 측정선 생성
- `update()`: 측정선 업데이트
- `delete()`: 측정선 삭제
- `select()`: 측정 선택
- `set_hovered()`: 호버 상태 설정

### 6. MeasurePanel (interface/panel.py)

**역할**: 측정 도구의 메인 UI 패널

**서브 패널**:
- **GlobalPanel**: Measure Selected 기능 및 전역 설정
- **MeshPanel**: 메시 바운딩 박스 측정 (BBox 버튼)
- **PlacementPanel**: 스냅 모드 등 측정 포인트 배치 설정
- **DisplayPanel**: 단위, 정밀도, 색상 등 표시 설정
- **ManagePanel**: 측정 목록 관리 및 편집

---

## 데이터 모델

### MeasurePrim (system/_measure_prim.py)

측정 데이터를 USD 프림으로 표현하는 클래스입니다.

**주요 속성**:
- `uuid`: 고유 식별자
- `path`: USD 프림 경로
- `payload`: MeasurePayload 객체
- `mode`: 측정 모드 (MeasureMode)
- `name`: 측정 이름

**주요 메서드**:
- `from_prim()`: USD 프림에서 MeasurePrim 생성
- `refresh_payload()`: 프림에서 최신 데이터로 페이로드 갱신
- `frame()`: 뷰포트를 이 측정으로 프레임

### MeasurePayload (system/_measure_payload.py)

측정 데이터를 담는 데이터 클래스입니다.

**주요 속성**:
- `uuid`: 고유 식별자
- `tool_mode`: 측정 모드
- `tool_sub_mode`: 서브 모드 (예: DistanceType)
- `prim_paths`: 측정 대상 프림 경로 목록
- `points`: 측정 포인트 목록 (월드 좌표)
- `local_points`: 측정 포인트 목록 (로컬 좌표)
- `primary`: 주 측정값
- `secondary`: 보조 측정값 (각도 측정의 경우)
- `visible`: 표시 여부
- `axis_display`: 축 표시 타입
- `unit_type`: 단위 타입
- `precision`: 정밀도
- `label_size`: 라벨 크기
- `label_color`: 라벨 색상

### MeasurementModel (system/_models.py)

측정 데이터를 관리하는 UI 모델입니다.

**주요 기능**:
- 측정 목록 관리
- 검색 및 필터링
- 선택 상태 관리
- UI 업데이트 알림

---

## 상태 관리

### 상태 머신 구조

```
MeasureState (전체 상태)
├── NONE: 측정 도구 비활성
├── CREATE: 측정 생성 중
└── EDIT: 측정 편집 중

MeasureMode (측정 모드)
├── MESH: 메시 바운딩 박스 측정
├── POINT_TO_POINT: 점 대 점
├── MULTI_POINT: 다중 점
├── ANGLE: 각도
├── DIAMETER: 직경
├── AREA: 면적
└── SELECTED: 선택된 프림 간

MeasureCreationState (생성 상태)
├── START_SELECTION: 시작점 선택
├── INTERMEDIATE_SELECTION: 중간점 선택
├── END_SELECTION: 끝점 선택
└── FINALIZE: 완료
```

### 상태 전환 흐름

1. **측정 생성 시작**:
   - `MeasureState.NONE` → `MeasureState.CREATE`
   - `MeasureMode` 설정 (예: POINT_TO_POINT)
   - `MeasureCreationState.START_SELECTION`

2. **포인트 선택**:
   - `MeasureCreationState.START_SELECTION` → `INTERMEDIATE_SELECTION` 또는 `END_SELECTION`
   - 각 포인트 선택 시 상태 업데이트

3. **측정 완료**:
   - `MeasureCreationState.FINALIZE`
   - `MeasureState.CREATE` → `MeasureState.NONE`
   - USD 프림 생성

---

## 뷰포트 통합

### MeasureScene 구조

```
MeasureScene
├── MeasureDrawManipulator: 기존 측정선 그리기
├── MeasureCreateManipulator: 측정 생성 중 미리보기
└── MeasureSceneOverlay: 오버레이 UI
```

### 측정 도구 (viewport/tools/)

각 측정 모드마다 별도의 도구 클래스가 있습니다:

- **PointToPointModel**: 점 대 점 측정 로직
- **MultiPointModel**: 다중 점 측정 로직
- **AngleModel**: 각도 측정 로직
- **DiameterModel**: 직경 측정 로직
- **AreaModel**: 면적 측정 로직
- **Mesh BBox 측정**: `mesh.py`의 `run_mesh_bbox_measurement_for_selection()` 함수로 구현

각 모델은 `ViewportModeModel`을 상속받아 공통 인터페이스를 구현합니다.

### 스냅 시스템 (viewport/snap/)

스냅 시스템은 측정 포인트를 정확하게 배치하기 위한 기능입니다.

**스냅 모드**:
- `NONE`: 스냅 없음
- `SURFACE`: 표면에 스냅
- `VERTEX`: 정점에 스냅
- `PIVOT`: 피벗 포인트에 스냅
- `EDGE`: 엣지에 스냅
- `MIDPOINT`: 중점에 스냅
- `CENTER`: 중심점에 스냅

**구조**:
- `MeasureSnapProviderManager`: 스냅 제공자 관리
- `SnapProvider`: 스냅 제공자 인터페이스
- 각 스냅 모드마다 별도의 Provider 구현

---

## 이벤트 처리

### 스테이지 이벤트

- **OPENED**: 스테이지가 열릴 때
  - 기존 측정 데이터 로드
  - 모델 초기화

- **CLOSED**: 스테이지가 닫힐 때
  - 모델 초기화
  - 뷰포트 씬 정리

### 레이어 이벤트

- **PRIM_SPECS_CHANGED**: 프림 스펙이 변경될 때
  - 새 측정 프림 감지
  - 삭제된 측정 프림 감지

- **EDIT_TARGET_CHANGED**: 편집 타겟이 변경될 때
  - 라이브 세션 진입/퇴장 감지

### 객체 변경 이벤트

- **USD 객체 변경**: 프림의 변환이 변경될 때
  - 관련된 측정값 자동 업데이트
  - 배치 처리로 성능 최적화

### 명령어 콜백

- **DeletePrims (PRE_DO)**: 프림 삭제 전
  - 측정 프림이 삭제되면 모델에서 제거
  - 언두를 위해 측정 데이터 저장

- **DeletePrims (POST_UNDO)**: 프림 삭제 언두 후
  - 측정 프림 복원

---

## 주요 워크플로우

### 1. 측정 생성 워크플로우

```
1. 사용자가 측정 도구 선택 (예: Point-to-Point)
   └─> StateMachine.set_creation_state(MeasureMode.POINT_TO_POINT)

2. 뷰포트에서 첫 번째 포인트 클릭
   └─> MeasureCreateManipulator가 포인트 배치
   └─> MeasureCreationState.START_SELECTION → END_SELECTION

3. 뷰포트에서 두 번째 포인트 클릭
   └─> MeasureCreateManipulator가 두 번째 포인트 배치
   └─> MeasureCreationState.FINALIZE

4. 측정 완료
   └─> MeasurePayload 생성
   └─> MeasurementManager.create(payload)
   └─> CreateMeasurementCommand 실행
   └─> USD 프림 생성 (/Viewport_Measure/measurement_point_to_point_0)
   └─> MeasureScene.create()로 측정선 렌더링
```

### 2. 측정 업데이트 워크플로우

```
1. 측정 대상 프림의 변환이 변경됨
   └─> USD 객체 변경 이벤트 발생

2. MeasurementManager.__on_objects_changed() 호출
   └─> 변경된 경로를 __pending_changed_paths에 추가

3. _process_pending_changed_path() 비동기 실행
   └─> 변경된 경로와 관련된 측정 프림 찾기
   └─> MeasurementModel.update() 호출
   └─> MeasurePrim.refresh_payload()로 측정값 재계산
   └─> MeasureScene.update()로 측정선 업데이트
```

### 3. 측정 삭제 워크플로우

```
1. 사용자가 측정 삭제 요청
   └─> MeasurementManager.delete(uuid) 호출

2. 모델에서 측정 제거
   └─> MeasurementModel.remove(uuid)

3. 뷰포트에서 측정선 제거
   └─> MeasureScene.delete(uuid)

4. USD 명령어 실행
   └─> RemoveMeasurementCommand 실행
   └─> USD 프림 삭제
   └─> 언두/리두 지원
```

### 4. 스테이지 로드 워크플로우

```
1. USD 파일 열기
   └─> StageEventType.OPENED 이벤트 발생

2. MeasurementManager.__reset() 호출
   └─> 모델 초기화
   └─> 뷰포트 씬 초기화

3. _populate_model_from_stage() 실행
   └─> /Viewport_Measure 하위의 모든 프림 순회
   └─> 각 프림을 MeasurePrim.from_prim()으로 변환
   └─> 모델에 추가
   └─> MeasureScene.create()로 측정선 렌더링
```

### 5. 메시 바운딩 박스 측정 워크플로우

```
1. 사용자가 MeasureMode.MESH 선택
   └─> MeshPanel 표시 (DisplayPanel 상위)
   └─> BBox 버튼 활성화 (선택된 프림에 Mesh가 있는 경우)

2. 사용자가 BBox 버튼 클릭
   └─> run_mesh_bbox_measurement_for_selection() 호출

3. 선택된 프림의 모든 Mesh 수집
   └─> _collect_mesh_prims()로 프림과 하위의 모든 Mesh 수집

4. 통합 바운딩 박스 계산
   └─> _compute_combined_bbox()로 모든 Mesh의 통합 바운딩 박스 계산
   └─> 각 Mesh의 로컬 바운딩 박스를 월드 좌표로 변환
   └─> 자식 프림의 Translate 값 반영 (omni.usd.get_world_transform_matrix 사용)

5. X/Y/Z축 측정선 생성
   └─> X축: (mx[0], mx[1], mn[2]) → (mx[0], mn[1], mn[2])
   └─> Y축: (mx[0], mx[1], mn[2]) → (mn[0], mx[1], mn[2])
   └─> Z축: (mn[0], mx[1], mn[2]) → (mn[0], mx[1], mx[2])
   └─> 각 측정선은 MeasureMode.MESH로 태그됨

6. 측정선 렌더링
   └─> LinearMeasurementItem에서 MESH 모드 감지
   └─> 아이콘/배경 제거, 텍스트만 표시 (흰색 텍스트, 검은색 스트로크)
   └─> 텍스트와 겹치는 선분 부분 투명 처리
```

## MESH 모드 특수 렌더링

### 개요

`MeasureMode.MESH`로 생성된 측정선은 일반 측정선과 다른 렌더링 방식을 사용합니다.

### 렌더링 특징

1. **아이콘 및 배경 제거**
   - 일반 모드의 아이콘(`sc.Image`) 및 배경(`sc.Rectangle`)이 표시되지 않음
   - 텍스트만 표시됨

2. **텍스트 스타일**
   - 텍스트 색상: 흰색 (`[1.0, 1.0, 1.0, 1.0]`)
   - 스트로크 색상: 검은색 (`[0.0, 0.0, 0.0, 1.0]`)
   - 스트로크는 8방향 오프셋으로 시뮬레이션 (좌상, 좌, 좌하, 상, 하, 우상, 우, 우하)

3. **선분 투명 처리**
   - 텍스트와 겹치는 선분 부분이 투명하게 처리됨
   - `_label_position`을 기준으로 선분을 세 부분으로 나눔:
     - 시작점 → 투명 영역 시작
     - 투명 영역 (알파 0.0)
     - 투명 영역 끝 → 끝점
   - 텍스트가 선분 위에 완전히 보이도록 보장

### 구현 위치

- **측정선 생성**: `viewport/tools/mesh.py`
  - `_create_bbox_axis_measurements_impl()`: 바운딩 박스 측정선 생성
  - `_compute_combined_bbox()`: 통합 바운딩 박스 계산

- **렌더링**: `viewport/_measurement_items.py`
  - `LinearMeasurementItem._draw()`: 선분 투명 처리
  - `LinearMeasurementItem._draw_label()`: 텍스트 및 스트로크 렌더링

### UI 패널

- **MeshPanel**: `interface/sub_panel.py`
  - `MeasureMode.MESH` 선택 시에만 표시
  - `DisplayPanel` 상위에 위치
  - BBox 버튼: 선택된 프림에 Mesh가 있을 때 활성화

## Stage Up Axis와 치수선 방향

### 축 방향이 달라지는 원인

앱마다 **Stage Up Axis** 설정이 다르면, "지면에 수직인 축"이 달라집니다.

| 앱 | 설정 파일 | Up Axis | 지면 수직 축 |
|----|-----------|---------|--------------|
| **My Editor** | `my_company.my_editor.kit` | (기본값 Y) | **Y축** |
| **My USD Explorer** | `my_company.my_usd_explorer.kit` | `upAxis = 'Z'` | **Z축** |

**설정 위치:**
- `[settings.app.viewport.defaults.hud.stage]` → `upAxis = 'Z'`
- `[settings.persistent.app.stage]` → `upAxis = 'Z'`

USD Explorer는 CAD/건축 도면에서 많이 쓰이는 Z-up을 사용합니다.

### 치수선/연장선 방향 자동 조정

`LinearMeasurementItem._draw()`에서 `UsdGeom.GetStageUpAxis(stage)`로 up axis를 읽고, 그에 맞게 연장선 방향을 바꿉니다.

- **Y-up**: X축 → X, Y축 → Y, Z축 → -X 방향 연장선
- **Z-up**: X축 → Z, Y축 → Z, Z축 → -X 방향 연장선 (수평 치수는 Z로, 높이 치수는 X로 연장)

---

### 1. 배치 처리

- USD 객체 변경 이벤트를 배치로 처리하여 성능 향상
- `_process_pending_changed_path()`에서 한 프레임에 여러 변경 사항을 모아 처리

### 2. 속성 값 캐싱

- `AttributeValueCache`를 사용하여 자주 접근하는 속성 값을 캐싱
- 불필요한 USD 쿼리 감소

### 3. 조건부 업데이트

- 측정이 보이지 않을 때는 업데이트 스킵
- `payload.visible` 플래그로 제어

### 4. 비동기 처리

- 측정값 계산을 비동기로 처리하여 UI 블로킹 방지
- `asyncio`를 사용한 비동기 작업

---

## 확장성

### 새로운 측정 모드 추가

1. `MeasureMode` 열거형에 새 모드 추가
2. `viewport/tools/`에 새 모델 클래스 생성 (ViewportModeModel 상속)
3. `StateMachine`에 상태 전환 로직 추가
4. 측정값 계산 로직 구현

### 새로운 스냅 모드 추가

1. `SnapMode` 열거형에 새 모드 추가
2. `viewport/snap/`에 새 Provider 클래스 생성 (SnapProvider 상속)
3. `MeasureSnapProviderRegistry`에 등록

---

## 참고 자료

- [Omniverse Kit 문서](https://docs.omniverse.nvidia.com/kit/docs/kit-manual/)
- [USD 문서](https://openusd.org/)
- [Omni UI 문서](https://docs.omniverse.nvidia.com/kit/docs/omni.ui/latest/)

---

## 버전 정보

- **버전**: 200.0.4+109.0
- **Kit 버전**: 109.0
- **Python 버전**: 3.12

---

## 작성자

이 문서는 Measure Tool의 아키텍처를 이해하고 확장하기 위한 참고 자료입니다.
문의사항이나 개선 제안이 있으면 이슈를 등록해 주세요.
