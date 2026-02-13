# 아이콘 로딩 방식 설명

## 개요

이 문서는 Measure Tool에서 아이콘 파일이 어떻게 로드되고 사용되는지 설명합니다.

## 아이콘 파일 위치

모든 아이콘 파일은 `data/` 디렉토리에 저장됩니다:
```
_build/windows-x86_64/release/extscache/omni.kit.tool.measure-200.0.4+109.0/data/
├── tool_point_to_point.svg
├── tool_multi_point.svg
├── tool_angle.svg
├── tool_diameter.svg
├── tool_area.svg
├── tool_mesh.svg          # 새로 추가된 메시 측정 아이콘
└── ...
```

## 아이콘 로딩 흐름

### 1. 버튼 생성 단계 (`_widgets.py`)

```python
class ToolButton(ui.Button):
    def __init__(self, tool: MeasureMode, clicked_fn: Callable[[MeasureMode], None], enabled: bool = True):
        self._mode: MeasureMode = tool
        self._clicked_fn: Callable[[MeasureMode], None] = clicked_fn

        # 1단계: MeasureMode 열거형의 이름을 소문자로 변환
        name = tool.name.lower()  # 예: MeasureMode.MESH → "mesh"

        # 2단계: 스타일 생성 함수 호출
        style = generate_toolbar_button_style(name)  # "mesh" 전달
```

**예시:**
- `MeasureMode.MESH` → `tool.name` = `"MESH"` → `tool.name.lower()` = `"mesh"`

### 2. 스타일 생성 단계 (`style.py`)

```python
def generate_toolbar_button_style(name: str) -> Dict:
    return {
        "Button": {"margin": 0, "background_color": 0x0, "border_radius": 4},
        # 3단계: 아이콘 경로 생성
        "Button.Image": {
            "image_url": __get_icon(f"tool_{name}"),  # "tool_mesh" 전달
            "color": _CLR_LABEL
        },
        # ... 기타 스타일 설정
    }
```

**예시:**
- `name = "mesh"` → `f"tool_{name}"` = `"tool_mesh"` → `__get_icon("tool_mesh")` 호출

### 3. 아이콘 파일 찾기 단계 (`style.py`)

```python
def __get_icon(name: str, extension: str = "svg") -> str:
    """
    아이콘 파일의 전체 경로를 반환합니다.

    Args:
        name: 아이콘 파일 이름 (확장자 제외)
        extension: 파일 확장자 (기본값: "svg")

    Returns:
        아이콘 파일의 전체 경로
    """
    # 현재 파일의 위치에서 data 디렉토리 경로 계산
    current_path = Path(__file__).parent  # interface/ 디렉토리
    icon_path = current_path.parent.parent.parent.parent.parent.joinpath("data")
    # 결과: .../omni/kit/tool/measure/data/

    # data 디렉토리에서 모든 SVG 파일 검색
    icons = {icon.stem: icon for icon in icon_path.glob(f"*.{extension}")}
    # icon.stem은 파일명에서 확장자를 제거한 것 (예: "tool_mesh.svg" → "tool_mesh")

    # 딕셔너리에서 해당 이름의 파일 찾기
    found = icons.get(name, "")  # "tool_mesh" 키로 검색
    return str(found)  # 전체 경로 반환
```

**예시:**
- `name = "tool_mesh"` → `icon_path.glob("*.svg")` → `tool_mesh.svg` 찾음
- `icons["tool_mesh"]` = `Path(".../data/tool_mesh.svg")`
- `str(found)` = `".../data/tool_mesh.svg"` (전체 경로)

## 전체 흐름 다이어그램

```
MeasureMode.MESH
    ↓
ToolButton.__init__()
    ↓
tool.name.lower() → "mesh"
    ↓
generate_toolbar_button_style("mesh")
    ↓
__get_icon("tool_mesh")
    ↓
data/ 디렉토리에서 "tool_mesh.svg" 검색
    ↓
전체 경로 반환: ".../data/tool_mesh.svg"
    ↓
UI 버튼에 아이콘 표시
```

## 파일 명명 규칙

### 중요 규칙

1. **파일명 형식**: `tool_{모드명}.svg`
   - `{모드명}`은 `MeasureMode` 열거형의 이름을 소문자로 변환한 것
   - 예: `MeasureMode.MESH` → `tool_mesh.svg`
   - 예: `MeasureMode.POINT_TO_POINT` → `tool_point_to_point.svg`

2. **파일 위치**: 반드시 `data/` 디렉토리에 있어야 함

3. **파일 확장자**: `.svg` (대소문자 구분 없음)

### 파일명 예시

| MeasureMode | tool.name.lower() | 파일명 |
|------------|-------------------|--------|
| `MESH` | `"mesh"` | `tool_mesh.svg` |
| `POINT_TO_POINT` | `"point_to_point"` | `tool_point_to_point.svg` |
| `MULTI_POINT` | `"multi_point"` | `tool_multi_point.svg` |
| `ANGLE` | `"angle"` | `tool_angle.svg` |
| `DIAMETER` | `"diameter"` | `tool_diameter.svg` |
| `AREA` | `"area"` | `tool_area.svg` |

## SVG 파일 요구사항

### 크기 및 viewBox

모든 도구 아이콘은 다음 형식을 따라야 합니다:

```xml
<svg version="1.1"
     xmlns="http://www.w3.org/2000/svg"
     width="40px"
     height="40px"
     viewBox="0 0 40 40">
    <!-- 아이콘 경로 -->
</svg>
```

**중요:**
- `width`와 `height`는 `40px`로 설정
- `viewBox`는 `"0 0 40 40"`으로 설정 (또는 원본 크기에 맞게 조정)
- `viewBox`가 있으면 SVG가 자동으로 스케일링됨

### 색상

- 아이콘의 `fill` 색상은 `#FFFFFF` (흰색)로 설정
- 실제 표시 색상은 스타일에서 `color` 속성으로 제어됨:
  - 기본: `_CLR_LABEL` (회색)
  - 활성화: `_CLR_ACTIVE` (노란색)
  - 비활성화: `_CLR_DISABLED` (어두운 회색)

## 문제 해결

### 아이콘이 표시되지 않는 경우

1. **파일명 확인**
   - `MeasureMode`의 이름과 파일명이 일치하는지 확인
   - 예: `MeasureMode.MESH` → `tool_mesh.svg` (소문자)

2. **파일 위치 확인**
   - 파일이 `data/` 디렉토리에 있는지 확인
   - 경로: `omni/kit/tool/measure/data/tool_mesh.svg`

3. **파일 확장자 확인**
   - `.svg` 확장자가 정확한지 확인
   - 대소문자 구분 없음

4. **SVG 형식 확인**
   - XML 형식이 올바른지 확인
   - `width`, `height`, `viewBox` 속성 확인

### 아이콘이 너무 크거나 작게 표시되는 경우

1. **viewBox 확인**
   - `viewBox` 속성이 올바르게 설정되었는지 확인
   - 원본 크기가 75x75라면 `viewBox="0 0 75 75"`로 설정하고 `width="40px" height="40px"`로 설정하면 자동 스케일링됨

2. **width/height 확인**
   - `width="40px" height="40px"`로 설정되어 있는지 확인

## 예제: 새 아이콘 추가하기

새로운 측정 모드를 추가할 때:

1. **MeasureMode 열거형에 추가**
   ```python
   class MeasureMode(Enum):
       MESH = -2  # 새 모드 추가
   ```

2. **아이콘 파일 생성**
   - 파일명: `data/tool_mesh.svg`
   - 형식: 40x40px, viewBox 설정

3. **버튼 생성 코드 확인**
   - `sub_panel.py`에서 버튼 생성 순서 확인
   - `ToolButton(MeasureMode.MESH, ...)` 호출 확인

## 참고 코드 위치

- **아이콘 로딩**: `omni/kit/tool/measure/interface/style.py`
  - `__get_icon()` 함수
  - `generate_toolbar_button_style()` 함수

- **버튼 생성**: `omni/kit/tool/measure/interface/_widgets.py`
  - `ToolButton` 클래스

- **모드 정의**: `omni/kit/tool/measure/common/constant.py`
  - `MeasureMode` 열거형

- **UI 패널**: `omni/kit/tool/measure/interface/sub_panel.py`
  - `GlobalPanel._draw()` 메서드
  - `MeshPanel`: MESH 모드 전용 패널 (BBox 버튼 포함)
