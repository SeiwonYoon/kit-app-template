# TBS Control 구현 가이드

이 문서는 **현재 구현된 내용**을 기준으로, 동일한 수정을 문서만 보고 따라 할 수 있도록 정리한 것입니다.
추후 XML 수신, 장비 시그널 수신 연동, resource 폴더 목록 등 확장 시에도 이 구조를 유지하면 됩니다.

---

## 1. 전체 구조 요약

- **시그널 파서**: JSON/XML 문자열을 파싱하여 공통 구조 `{"objects": [...], "segments": [...]}` 로 반환하는 별도 모듈 (`signal_parser.py`).
- **extension.py**: 파서를 `import`하여 사용. 버튼 클릭 시 또는 **데이터 수신 시** 파싱 결과로 애니메이션 실행.
- **수신 연동**: 장비로부터 JSON/XML 데이터가 들어오면 `receive_signal_data(data, format)` 를 호출하면 파싱 후 애니메이션이 **자동 실행**되도록 되어 있음.
- **USD 로드**: 경로 직접 입력 + **프로젝트 최상단 `resource` 폴더** 내 USD 샘플 목록을 콤보로 표시하고, 선택 시 경로에 반영 후 Load 가능.

---

## 2. signal_parser.py 추가

### 2.1 목적

- JSON / XML 형식의 가상 시그널을 **같은 내부 구조**로 바꿔 주기 위함.
- `extension.py` 에서는 이 모듈만 import 하고, 형식(JSON/XML)에 따른 분기는 파서 내부에서 처리.

### 2.2 파일 위치 및 공개 API

- **경로**: `morph/tbs_control/signal_parser.py`
- **공개 함수**:
  - `parse_signal_json(text: str) -> Optional[dict]`
  - `parse_signal_xml(text: str) -> Optional[dict]`
  - `parse_signal(data: str, format: str = "json") -> Optional[dict]`

### 2.3 반환 공통 구조

모든 파서는 다음 형태의 dict 를 반환합니다.

```python
{
    "objects": ["Mesh_226", "Mesh_567"],   # prim 이름(GetName 기준) 목록
    "segments": [                           # 구간별 이동
        {"duration": 1.0, "delta": (100, 0, 0)},
        {"duration": 1.0, "delta": (0, 100, 0)},
        {"duration": 2.0, "delta": (-100, -100, 0)}
    ]
}
```

- **objects**: 애니메이션할 prim 의 **이름** 목록.
- **segments**: `duration`(초), `delta` (x, y, z) 리스트. 기존 `translate_animation` 세그먼트와 동일.

### 2.4 JSON 형식 (기존과 동일)

```json
{
  "objects": ["Mesh_226", "Mesh_567"],
  "animation": {
    "segments": [
      {"duration": 1.0, "delta": [100, 0, 0]},
      {"duration": 1.0, "delta": [0, 100, 0]},
      {"duration": 2.0, "delta": [-100, -100, 0]}
    ]
  }
}
```

- `_normalize_parsed()` 에서 위 구조를 공통 구조 `{"objects", "segments"}` 로 변환.

### 2.5 XML 형식 (태그 속성값으로 동일 동작)

예시:

```xml
<signal>
  <objects>
    <object name="Mesh_226"/>
    <object name="Mesh_567"/>
  </objects>
  <animation>
    <segment duration="1.0" dx="100" dy="0" dz="0"/>
    <segment duration="1.0" dx="0" dy="100" dz="0"/>
    <segment duration="2.0" dx="-100" dy="-100" dz="0"/>
  </animation>
</signal>
```

- `object` 의 `name` → `objects` 리스트.
- `segment` 의 `duration`, `dx`, `dy`, `dz` → `segments` 리스트 (각 `delta` = (dx, dy, dz)).

`parse_signal_xml()` 에서 `xml.etree.ElementTree` 로 파싱한 뒤 위와 동일한 공통 구조로 반환합니다.

### 2.6 구현 시 따라 할 내용

1. `morph/tbs_control/signal_parser.py` 생성.
2. `json`, `xml.etree.ElementTree` 사용.
3. `parse_signal_json` / `parse_signal_xml` 구현 후, 공통 구조로 정규화.
4. `parse_signal(data, format)` 에서 `format in ("json", "xml")` 에 따라 위 두 함수 중 하나 호출.

---

## 3. extension.py 변경 사항

### 3.1 import 변경

- **제거**: `import json` (파싱은 signal_parser 에서 수행).
- **추가**: `from pathlib import Path`, `from .signal_parser import parse_signal`.

### 3.2 샘플 재생 버튼 동작

- **이전**: 버튼 클릭 시 `_run_generator_from_json(SAMPLE_GENERATOR_JSON)` 에서 JSON 을 직접 파싱하고 애니메이션 실행.
- **이후**:
  - `parse_signal(SAMPLE_GENERATOR_JSON, "json")` 로 파싱.
  - 반환된 dict 를 `_run_generator_from_parsed(parsed)` 에 넘겨 애니메이션 실행.

즉, 버튼은 “JSON 문자열 → 파서 → 공통 구조 → 실행” 한 번만 거치도록 변경.

### 3.3 데이터 수신 시 자동 실행 (receive_signal_data)

- **메서드**: `receive_signal_data(self, data: str, format: str = "json") -> bool`
- **역할**: 장비 등 외부에서 JSON/XML 문자열을 넘기면, 파서로 파싱한 뒤 **같은 애니메이션 로직**으로 자동 실행.
- **내부 동작**:
  1. `parse_signal(data, format)` 호출.
  2. 성공 시 `_run_generator_from_parsed(parsed)` 호출.
  3. 성공 여부에 따라 `True` / `False` 반환.

수신 모듈(소켓, HTTP, 파일 감시 등)에서는 데이터를 받은 뒤 이 메서드만 호출하면 됩니다.

```python
# 예: 수신부에서 호출 예시
ext = get_tbs_control_extension()  # 확장 인스턴스 취득 방식은 프로젝트에 맞게 구현
ext.receive_signal_data(received_string, format="json")  # 또는 "xml"
```

### 3.4 실행 로직 통합 (_run_generator_from_parsed)

- **이전**: `_run_generator_from_json(json_str)` 에서 JSON 파싱 + 객체/세그먼트 추출 + 애니메이션 실행.
- **이후**: `_run_generator_from_parsed(parsed)` 하나만 두고, `parsed` 는 항상 `{"objects", "segments"}` 형태로 가정.
  - `parsed["objects"]` 로 prim 이름 목록.
  - `parsed["segments"]` 로 기존처럼 `run_prim_translate_animation(path, segments, loop=False)` 호출.

버튼/수신 모두 “파싱 → _run_generator_from_parsed” 로 통일됩니다.

---

## 4. resource 폴더 USD 목록 및 선택 로드

### 4.1 목적

- **경로 입력** 외에, 프로젝트 **최상단 경로의 `resource` 폴더** 안에 있는 USD 샘플을 목록으로 보여 주고, 선택한 항목을 로드할 수 있게 함.

### 4.2 resource 경로 계산

- **규칙**: **launch 실행 최상단 경로** 아래의 `resource` 폴더를 사용. 동일하게 `morph.measure_control_1` 확장에서 쓰는 방식을 따름.
- **우선순위**:
  1. **carb.tokens** `tokens.resolve("${root}")` 로 루트를 구한 뒤 `${root}/resource` 가 디렉터리면 사용 (launch 기준 최상단).
  2. 실패 시 **extension 파일 기준** 상위 최대 10단계 탐색, `current / "resource"` 가 디렉터리면 사용.
  3. 그도 실패 시 **현재 작업 디렉터리** `Path.cwd() / "resource"` 가 디렉터리면 사용.
- **함수**: `_get_resource_folder_path() -> Optional[Path]`

### 4.3 USD 파일 목록

- **함수**: `_get_resource_usd_list() -> List[tuple]`
- **반환**: `[(파일명, 절대경로), ...]`
  - 확장자: `.usd`, `.usda`, `.usdc`
  - 파일명 기준 정렬.

### 4.4 USD Load 창 UI 변경

- **추가 UI**:
  - “resource 폴더 샘플 (선택 시 경로에 반영)” 라벨.
  - `resource` 에 USD 가 있으면 **ComboBox** 로 파일명 목록 표시.
  - 콤보 선택 시 `_on_resource_combo_changed(model, *args)` 호출 → 현재 선택 인덱스로 `self._path_model.set_value_as_string(선택한_절대경로)` 로 경로 필드에 설정.
- **기존**: “경로 (직접 입력 또는 위에서 선택)” + StringField + Load 버튼은 유지.
- **동작**: 콤보에서 선택하면 경로만 채워지고, Load 버튼을 눌러야 실제 로드 (기존과 동일).

### 4.5 구현 시 따라 할 내용

1. `_get_resource_folder_path()`, `_get_resource_usd_list()` 구현.
2. `_build_load_window()` 에서:
   - `_get_resource_usd_list()` 로 목록 취득.
   - 목록이 있으면 ComboBox 추가, `add_item_changed_fn` 으로 `_on_resource_combo_changed` 연결.
3. `_on_resource_combo_changed(model, *args)` 에서 현재 선택 인덱스를 구한 뒤, 해당 인덱스의 절대 경로를 `_path_model` 에 설정.

---

## 5. 추후 확장 시 참고

### 5.1 XML 전용 경로 추가

- 이미 `signal_parser.parse_signal_xml` / `parse_signal(..., "xml")` 이 있으므로, 수신 포맷만 `"xml"` 로 넘기면 됨.
- XML 스키마를 바꾸면 `parse_signal_xml()` 내부만 수정하고, extension 쪽은 그대로 사용 가능.

### 5.2 다른 이름 규칙/우선순위

- 현재는 “우선 표시 이름 규칙 (접두사)” 로 목록에 표시할 prim 의 우선순위를 정함.
- 다른 USD 에서 다른 규칙을 쓰려면, 동일 UI 의 접두사 필드를 비우거나 다른 접두사로 바꾸면 됨.
- 파서/시그널 형식과는 독립적.

### 5.3 수신 연동

- 실제 수신(소켓/HTTP/파일)은 별도 모듈에서 구현.
- 수신한 문자열과 포맷(`"json"` / `"xml"`)만 정해진 뒤, **TBS Control 확장 인스턴스의 `receive_signal_data(data, format)`** 를 호출하면 애니메이션이 자동 실행되도록 현재 구조가 잡혀 있음.

---

## 6. 파일 목록 정리

| 파일 | 역할 |
|------|------|
| `morph/tbs_control/signal_parser.py` | JSON/XML 파싱, 공통 구조 반환. |
| `morph/tbs_control/extension.py` | `parse_signal` import, `_run_generator_from_parsed`, `receive_signal_data`, resource 목록/콤보/경로 반영. |
| `docs/Implementation_Guide_TBS_Control.md` | 본 문서. 위 내용만 보고 동일하게 구현/수정 가능하도록 정리. |

이 순서대로 구현하면 “JSON/XML 파서 분리, 수신 시 자동 실행, resource 폴더 목록 선택 로드”까지 동일하게 재현할 수 있습니다.
