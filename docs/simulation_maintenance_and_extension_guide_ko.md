# 시뮬레이션 유지보수/확장 가이드 (개발자용)

이 문서는 `morph.tbs_control_1` 확장에서 **시뮬레이션 로직을 수정**하거나 **다른 장비(또는 다른 라인/설비)의 시뮬레이션을 추가**할 때, 무엇을 어디서 어떻게 손대야 하는지 “한 문서”로 빠르게 찾을 수 있게 정리한 가이드입니다.

---

## 전체 구조 한눈에 보기 (추천 읽는 순서)

아래 흐름은 “공정 판단 → 이벤트(payload) 생성 → UI에서 애니 매핑/실행”의 표준 파이프라인입니다.

1) **시뮬레이션 엔진(공정 판단/시간 진행)**
- 파일: `source/extensions/morph.tbs_control_1/morph/tbs_control_1/simulation_engine.py`
- 핵심: `TBSSimulationEngine`
  - 공정 흐름 오케스트레이터: `_run_serial_flow()` + `_step_*` 함수들
  - 상태 로그 정책: `_StatusLogPolicy`
  - 진행현황 emit 정책: `_ProgressEmitPolicy`
  - 이벤트 발행: `_emit_event(payload)`

2) **UI/이벤트 수신/애니 실행 파이프라인**
- 파일: `source/extensions/morph.tbs_control_1/morph/tbs_control_1/control_window.py`
- 핵심: 시뮬 이벤트 수신 → `handle_sim_event_for_animation()` → JSON 매핑 → 시퀀스 실행
- 공정확인(게이트) 메시지: `_build_sim_gate_request_payload()` (표시 순서/내용 유지보수 지점)

3) **이벤트 표준(XML)**
- 파일: `source/extensions/morph.tbs_control_1/morph/tbs_control_1/xml_generator.py`
- 핵심: `build_xml_string()` / `parse_xml_string()`
- 목적: 이벤트를 XML로 “표준화”했다가 역파싱해 포트/시퀀스 정보를 일관되게 얻음

4) **애니메이션 JSON(시퀀스)**
- 편집/생성: `source/extensions/morph.tbs_control_1/morph/tbs_control_1/sequence_editor.py`
- 실행 엔진: `source/extensions/morph.tbs_control_1/morph/tbs_control_1/sequence_engine.py`

5) **이벤트→JSON 매핑 규칙**
- 규칙(권장): `source/extensions/morph.tbs_control_1/config/event_animation_rules.json`
- 단순 맵(fallback): `source/extensions/morph.tbs_control_1/config/event_animation_map.json`

---

## “시뮬레이션 로직을 수정하고 싶다” 체크리스트

### A. 공정 우선순위/흐름을 바꾸고 싶을 때
수정 지점은 **한 곳**입니다.

- 파일: `simulation_engine.py`
- 함수: `TBSSimulationEngine._run_serial_flow()`
  여기에서 아래 스텝들이 어떤 순서로 실행되는지 결정합니다.
  - `_step_bp1_to_buffer()`
  - `_step_pickup_to_oht()`
  - `_step_oht_input()`
  - `_step_buffer_to_ep()`
  - `_step_idle_wait()`

**예시 요구사항과 매칭**
- “회수를 항상 우선 처리”: `_step_pickup_to_oht()`를 상단에 유지/강화
- “OHT direct input을 끄고 항상 BP1 경유”: `_step_oht_input()`에서 direct 조건 제거
- “버퍼→EP 정책을 바꾸고 싶다”: `_step_buffer_to_ep()`에서 선택 로직 변경(= `_find_*` 조정 포함)

### B. 포트 선택 규칙을 바꾸고 싶을 때
아래 helper들이 “정책”입니다.

- 빈 버퍼 선택: `_find_oldest_empty_buffer()`
- 버퍼 LOT 선택: `_find_oldest_bp()`
- 빈 EP 선택: `_find_empty_ep()`
- 회수 대상 EP 선택: `_find_ep_awaiting_pickup()` (FIFO 정책)

### C. “이동 완료 시점에만 포트 상태 반영” 같은 동시성/표시 문제
이런 문제는 대개 “상태를 언제 바꾸느냐”에 있습니다.

- BP→EP: `_move_bp_to_ep()`
  - 포트 잠금: `_locked_ports` 기반(`_lock_port/_unlock_port`)
  - 완료 시점에만 출발 EMPTY/도착 FULL 반영
- BP1→BUFFER: `_move_bp1_to_buffer()`
- OHT→EP direct: `_load_lot_to_ep_direct()`
  - 완료 직후 포트상태 갱신용 이벤트: `seq="PORT_OCC_REFRESH"` (애니/게이트로는 보내지지 않음)

### D. 로그/진행현황 출력 정책만 바꾸고 싶을 때
“어디를 보면 되는지”가 한 곳으로 모여 있습니다.

- 상태 로그(HEARTBEAT/WAIT): `_StatusLogPolicy`
  - 중복 방지, interval 제어
- 진행현황(UI progress): `_ProgressEmitPolicy` + `_wait_with_progress()`
  - interval 정규화, percent/시간 포맷

---

## “다른 장비의 시뮬레이션을 추가하고 싶다” 가이드

여기서 말하는 “다른 장비”는 다음 중 하나를 의미할 수 있습니다.
- 포트/설비 구성이 다른 장비(예: BP/EP 개수 다름)
- 공정 단계가 다른 장비(예: PROCESS 단계 추가/삭제)
- 이벤트 종류가 다른 장비(새로운 `EAPEIS_PORT_*` 또는 타 표준 이벤트)

아래 2가지 접근 중 하나를 선택합니다.

### 접근 1) **현재 구조를 “복제”해서 새 엔진 클래스를 만든다 (가장 명확)**

1) 새 엔진 파일 생성
- 예: `source/extensions/morph.tbs_control_1/morph/tbs_control_1/simulation_engine_<equipment>.py`
- 클래스명: `<Equipment>SimulationEngine`
- 구현: `TBSSimulationEngine`의 구조(오케스트레이터 + step 함수 + 정책)를 그대로 가져가되, 포트/단계만 변경

2) UI 연결
- `control_window.py`의 시뮬 시작 핸들러(`on_sim_start_clicked`)에서
  - 콤보/토글로 장비 타입 선택
  - 선택된 타입에 따라 엔진 인스턴스를 생성하도록 분기

3) 이벤트 표준화/매핑은 “재사용”
- 엔진이 `_emit_event(payload)`로 동일한 payload 키(`seq`, `port_id`, `from_port_id`, `to_port_id`, `lot_id`)만 맞추면,
  - `xml_generator` / `rules.json` / `sequence_engine` 파이프라인을 그대로 재사용 가능

**장점**
- 어떤 장비의 로직인지 파일 1개만 보면 됨(유지보수 가시성 최고)
- 기존 장비(TBS)와 기능 충돌 최소

**단점**
- 공통 로직이 중복될 수 있음(추후 공통 베이스 클래스 도입 가능)

### 접근 2) 공통 엔진 + 장비별 “정책/구성” 객체로 분리한다 (확장성 좋음)

- 공통 엔진은 “오케스트레이션 프레임”만 제공
- 장비별로:
  - 포트 집합
  - 선택 규칙
  - 단계 정의(어떤 이벤트를 언제 emit 할지)
  - 시간 분포(`SimulationTimingConfig` 확장)
  를 “구성 객체”로 주입

**장점**
- 여러 장비를 빠르게 추가 가능

**단점**
- 추상화 수준이 높아지면 처음 진입자가 오히려 어려울 수 있음
  (현재 목표가 “가시성”이라면 접근 1을 먼저 권장)

---

## 새 장비/새 공정 추가 시 반드시 같이 체크할 파일들

### 1) 이벤트 표준(XML) 관련
- `xml_generator.py`
  - 새 시퀀스 상수(SEQ_*) 추가
  - `FROM_TO_SEQS / PORT_ID_ONLY_SEQS` 분류
  - `build_xml_string()` 분기 추가
  - `parse_xml_string()`는 대부분 자동 호환되지만, 새 속성이 있으면 확장

### 2) 이벤트→애니 JSON 매핑
- `config/event_animation_rules.json`
  - `when.sequence/from_port/to_port/port/ports_occupancy` 조건 확장
  - `use.json` 경로만 바꾸면 동작 연결 가능
- (fallback) `config/event_animation_map.json`

### 3) 애니 JSON(시퀀스)
- `sequence_editor.py`: JSON 만들기/편집/스냅샷
- `sequence_engine.py`: 실제 실행(MOVE/ROTATE/USD_TIMELINE/DELAY)

---

## 디버깅/검증 방법 (권장)

### A. “이 이벤트가 왜 이 JSON을 탔지?” 확인
- `control_window.py`의 공정확인 창:
  - `[EVENT]` / `[ACTION]` / `[ANIM] file=... (존재/EMPTY)`를 확인
  - `TIMER`로 시뮬 시간 흐름 확인
  - `XML:` 원문 확인

### B. 포트 상태가 ‘늦게’ 갱신되는지 확인
- 포트상태 패널은 “이벤트 수신 시점”에 갱신됨
- direct input 같은 경우 완료 시점에 별도 이벤트가 필요할 수 있음(현재는 `PORT_OCC_REFRESH`로 해결)

### C. 로그/엑셀 산출물
- 종료 시 `data/sim_logs/sim_logs_YYYYmmdd_HHMMSS.xlsx` 생성
- 시트: 진행현황/이력로그/애니메이션실행이력

---

## (선택) 신규 장비를 위한 “최소 작업” 템플릿

1) 시뮬 엔진 복제: `simulation_engine_new.py`
2) 이벤트 seq 정의/표준화: `xml_generator.py`에 SEQ 추가
3) 애니 JSON 생성: 시퀀스 편집기에서 생성 → `data/sim_sequences/*.json` 저장
4) rules에 매핑: `event_animation_rules.json`에 규칙 추가
5) UI에서 엔진 선택 분기: `control_window.py` on_sim_start_clicked
