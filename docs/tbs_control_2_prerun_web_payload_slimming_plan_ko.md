# TBS Control 2 — 프리런 결과(Web 전송용) 간소화 계획

작성일: 2026-07-08  
상태: 초안 (요구사항/스키마 피드백 후 구현 예정)

---

## 1) 목적

현행 프리런(prerun) 결과를 웹으로 전달할 때 데이터가 너무 커서(실무 환경) 웹에서 수신/처리 실패가 발생한다.  
따라서 **웹 전송용 payload만** “필요없는 부분 제거 + 구조 변경”을 통해 **최소 크기**로 줄여 전달한다.

---

## 2) 반드시 유지할 것(비기능 요구)

- **Kit 내부 동작은 그대로 유지**
  - 프리런 계산 결과의 원본 구조(메모리) 유지
  - (옵션에 따라) `data/sim_prerun/*.json` 디스크 저장 구조 유지
  - 해당 원본 데이터를 기반으로 하는 시뮬레이션/재생/바 그래프/seek 등 기존 기능 유지
- 변경 범위는 **웹 전송 응답**에 한정한다.
- 즉, “원본 프리런 결과(SSOT)”와 “웹 전송용 slim payload”를 **분리**한다.

---

## 3) 현행 데이터 흐름(전송 지점 확인)

### 3.1 이벤트 흐름

- Web → Kit: `T2V_request_start_simulation`
- Kit 내부:
  - `morph.hyview_messaging.extension_handlers.ebs_handler.EBSHandler._on_req_start_simulation()`
  - `morph.hyview_messaging.tbs_sim_bridge.handle_start_simulation()`
    - `on_sim_start_clicked(ext)` 호출
    - 프리런 완료 이벤트 대기 (`_wait_prerun_done`)
    - 완료 후 **Web 콜백 전송**:
      - 이벤트명: `V2T_response_start_simulation`
      - body: `data.results`에 case0/case1 프리런 결과 dict 배열

### 3.2 “전송 payload가 커지는” 현재 구조

- `V2T_response_start_simulation`의 `data.results`에는 **프리런 결과 원본(혹은 원본에 준하는 큰 dict)**가 포함된다.
- 이 결과는 웹에서 바로 사용하기 쉽도록 다양한 정보(타임라인/바그래프/메타/스냅샷 등)가 한 번에 들어가며,
  실무 환경에서는 payload가 커져 전송/수신에 실패할 수 있다.

---

## 4) 변경 전략(핵심)

### 4.1 원본 SSOT는 유지, 전송용만 변환

- 원본(Kit 내부용) 프리런 결과는 그대로 두고,
- `V2T_response_start_simulation`을 보내기 직전에
  - `results`를 **slim 변환 함수**에 통과시켜
  - `results_slim`(혹은 기존 `results` 자리에 slim)을 전송한다.

### 4.2 변환 훅(Hook) 포인트

변환을 넣을 수 있는 후보 지점(우선순위 순):

1. `morph.hyview_messaging.tbs_sim_bridge.handle_start_simulation()`  
   - `dispatch("V2T_response_start_simulation", body)` 호출 직전
2. `morph.hyview_messaging.extension_handlers.ebs_handler.EBSHandler._dispatch_start_simulation_response()`  
   - bridge 응답을 최종 `dispatch_event()`로 넘기기 직전

권장: **(2) ebs_handler 쪽에서 최종 전송 직전에 변환**  
이유: “bridge가 만든 원본 결과”를 **그대로 보존**하면서, “웹 전송 규격”만 담당하기 쉽다.

---

## 5) 버전/호환성 설계(권장)

웹과 Kit가 동시에 배포/업데이트되지 않을 수 있으므로, 전송 스키마에 버전을 둔다.

- 예: `data.schema = "tbs_prerun_slim_v1"`
- 또는 `data.version = 1`

웹은 version에 따라 파서를 선택하거나, 최소한 “이 버전을 지원하지 않으면 오류를 명확히 표시”한다.

---

## 6) 문서화된 비목표(이번 작업에서 하지 않음)

- 프리런 계산 알고리즘 변경
- 프리런 디스크 저장 포맷 변경(`data/sim_prerun/*.json`)
- Kit 내부 playback/seek/bar_graph가 참조하는 원본 데이터 구조 변경
- Dock/Widget 분할 등 UI 로직 변경

---

## 7) (추후 채움) Slim 규칙/스키마 초안

> 이 섹션은 사용자 피드백으로 “어떤 데이터를 남기고 무엇을 버릴지”가 확정되면 구체화한다.

### 7.0 현재 합의된 규칙(진행 중)

#### 7.0.1 최상단(Top-level) 제거

- `version` 제거
- `sim.ep_count_idx` 제거 (웹 전송에서는 불필요)

#### 7.0.2 `timeline` 제거

- `timeline.item_count` 제거
- `timeline.final_sim_time_sec` 제거 (상위 `sim.final_sim_time_sec`에 존재)
- `timeline.total_est_sec` 제거 (상위 `sim.total_est_sec`에 존재)
- `timeline.seek_snapshots_count` 제거

#### 7.0.3 `timeline.timetable_rows` 간소화(가장 중요)

원본의 `timeline.timetable_rows`는 행 메타(`row_index`, `display_line`, `through_item_index` 등)를 포함해 매우 크다.  
웹 전송에서는 아래 규칙으로 최소화한다.

- **행 필터링**
  - `event`가 `FOUP_PROCESS_START` 또는 `FOUP_PROCESS_END`인 event만 유지
  - `kind="step"` 이고 `anim`이 존재하는 행만 유지 (애니메이션 실행에 필요한 행)
  - 그 외 모든 행은 제거
- **필드 축소**
  - 유지된 행에서는 `screen` 제거 (상위의 `case`로 대체 가능)
  - 유지된 행에서는 `kind` 제거
  - 유지된 행에서는 `process_time_priority` 제거
- **문자열화(stringify)**
  - 최종 전송에서는 `timetable_rows`를 `object[]`가 아니라 **`string[]`**로 보낸다.
  - 각 원소는 “필요 필드만 포함한 JSON object를 minify한 문자열”이다.
  - 목적: 키 중복/공백 제거로 payload 크기를 최대한 낮춤.

#### 7.0.4 `bar_graph.segments` 간소화

`bar_graph.segments`는 행(`ALL_EP`, `EP1` 등)별로 세그먼트 배열을 가지며, 각 세그먼트가 dict 형태로 중복 키/색상 정보를 포함한다.  
웹 전송에서는 **세그먼트 1개를 2원소 배열로 축소**한다.

- 변환 전(예):

```json
{ "state": "empty", "dur_sec": 13.83, "color": "#FFFF00" }
```

- 변환 후:

```json
["empty", 13.83]
```

- 규칙:
  - `state`, `dur_sec`만 유지
  - `color` 및 기타 키는 제거

### 7.1 제거 후보(예시)

- 디버그용 원시 타임라인 아이템 전체
- 중복 가능한 메타/스냅샷
- 색상/스타일 정보 등 웹에서 재계산 가능한 항목

### 7.2 유지 후보(예시)

- 웹 UI가 반드시 필요로 하는 요약 정보(케이스별 총 시간, 핵심 진행 이벤트, 필요한 행/포트 상태 요약 등)
- 웹이 렌더링에 필요한 최소 타임라인(압축/구간화된 형태)

### 7.3 전송 데이터 예시(placeholder)

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "schema": "tbs_prerun_slim_v1",
    "results": [
      { "case": 0, "..." : "..." },
      { "case": 1, "..." : "..." }
    ]
  }
}
```

---

## 8) 검증/테스트 계획(피드백 후 확정)

- payload 크기(바이트) 측정: 변경 전/후 비교
- 웹 수신 성공(실무 환경) 재현
- Kit 내부(원본) 프리런/재생/seek/bar_graph 기존 동작 회귀 테스트

