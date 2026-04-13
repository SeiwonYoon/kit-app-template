# 시뮬레이션 진행 흐름 요약 (이벤트→XML→역파싱→애니 실행)

## 1) 한 줄 요약

이벤트 발생 → 이벤트에 따른 **XML(TIB) 생성**(실제 장비에서 보내줄 TIB 메세지 에시 형식과 동일한 방식으로 구성) → XML **역파싱** → 추출 데이터로 **애니메이션 JSON 선택** → JSON 시퀀스 **실행** → 진행현황(%) 갱신됨.
(tib메시지 이미지 첨부부)
---

## 2) 공통 처리 흐름

- 공정 단계 변화 시 “이벤트” 발생함
- 이벤트 내용 기반으로 표준 메시지(XML) 생성됨
- 생성된 XML을 역파싱하여 핵심 데이터 추출됨
  - 추출 데이터 예: 이벤트 종류(시퀀스명), 포트 정보(포트/출발/도착), LOT 식별자 등
- 추출 데이터 기준으로 실행할 애니메이션 JSON 매칭됨
- 매칭된 JSON 시퀀스 실행됨(해당 이벤트가 애니 대상이 아니면 실행 생략됨)
- 공정 진행 중 진행현황(%) 주기적으로 갱신됨

---

## 3) XML 생성 예시 + 역파싱 데이터(무엇을 뽑아 쓰는지)

이벤트 발생 시 아래와 같은 형태의 XML이 생성됨. 이후 XML을 역파싱하여 “애니메이션 JSON 선택에 필요한 값”을 추출함.

### 3.1 `ARRIVED`(안착) XML 예시

```xml
<?xml version="1.0" encoding="utf-8"?>
<Envelop>
  <HEADER>
    <FACILITY />
    <ENVIRONMENT />
    <SENDERNODE />
  </HEADER>
  <BODY SEQUENCE_NAME="EAPEIS_PORT_ARRIVED">
    <EAPEIS_PORT_EVENT CONTROL_JOB_ID="">
      <PROCESS_JOB PORT_ID="5">
        <CARRIER CARRIER_ID="">
          <LOT LOT_ID="" OPERATOR="" OPERATION="">
            <WAFER WAFER_ID="" />
          </LOT>
        </CARRIER>
      </PROCESS_JOB>
    </EAPEIS_PORT_EVENT>
  </BODY>
</Envelop>
```

- 역파싱으로 추출됨
  - `sequence_name = EAPEIS_PORT_ARRIVED`
  - `port_id = 5` (예: IN/OUT)
- 추출 데이터로 애니 JSON 선택됨
  - 예: `ARRIVED + port=INOUT` → `arrived_inout.json` 실행됨

### 3.2 `MOVE_TRANSFERING`(이송) XML 예시

```xml
<?xml version="1.0" encoding="utf-8"?>
<Envelop>
  <HEADER>
    <FACILITY />
    <ENVIRONMENT />
    <SENDERNODE />
  </HEADER>
  <BODY SEQUENCE_NAME="EAPEIS_PORT_MOVE_TRANSFERING">
    <EAPEIS_PORT_EVENT CONTROL_JOB_ID="">
      <PROCESS_JOB PORT_ID="6">
        <CARRIER CARRIER_ID="">
          <LOT LOT_ID="" OPERATOR="" OPERATION="">
            <WAFER WAFER_ID="" />
          </LOT>
        </CARRIER>
      </PROCESS_JOB>
      <FROM_INFO FROM_EQP_ID="" FROM_PORT_ID="5" />
      <TO_INFO TO_EQP_ID="" TO_PORT_ID="6" />
    </EAPEIS_PORT_EVENT>
  </BODY>
</Envelop>
```

- 역파싱으로 추출됨
  - `sequence_name = EAPEIS_PORT_MOVE_TRANSFERING`
  - `from_port_id = 5` (예: IN/OUT)
  - `to_port_id = 6` (예: BP1)
- 추출 데이터로 애니 JSON 선택됨
  - 예: `MOVE_TRANSFERING + from=INOUT + to=BP1` → `move_inout_bp1.json` 실행됨

### 3.3 `REMOVED`(회수/반출) XML 예시

```xml
<?xml version="1.0" encoding="utf-8"?>
<Envelop>
  <HEADER>
    <FACILITY />
    <ENVIRONMENT />
    <SENDERNODE />
  </HEADER>
  <BODY SEQUENCE_NAME="EAPEIS_PORT_REMOVED">
    <EAPEIS_PORT_EVENT CONTROL_JOB_ID="">
      <PROCESS_JOB PORT_ID="1">
        <CARRIER CARRIER_ID="">
          <LOT LOT_ID="" OPERATOR="" OPERATION="">
            <WAFER WAFER_ID="" />
          </LOT>
        </CARRIER>
      </PROCESS_JOB>
    </EAPEIS_PORT_EVENT>
  </BODY>
</Envelop>
```

- 역파싱으로 추출됨
  - `sequence_name = EAPEIS_PORT_REMOVED`
  - `port_id = 1` (예: EP1)
- 추출 데이터로 애니 JSON 선택됨
  - 예: `REMOVED + port=EP1` → `removed_ep1.json` 실행됨

---

## 4) 이벤트 예시로 보는 시뮬레이션 진행 흐름(대표 5가지)

아래는 시뮬레이션이 실제로 어떻게 흘러가는지 이해하기 위한 대표 예시.

### 예시 1) `READYTOLOAD` (LOT 생성/준비)

- 이벤트 발생함(LOT 준비 상태 표시됨)
- 해당 이벤트 기반 XML 생성됨 → 역파싱됨
- 역파싱 데이터는 “준비 상태” 기록/표시에 사용됨
- 보통 애니메이션 JSON 실행 없음(상태/기록 중심 이벤트임)

### 예시 2) `ARRIVED` (OHT가 포트에 안착)

- 이벤트 발생함(안착 발생함)
- 해당 이벤트에 맞는 XML 생성됨 → 역파싱됨
- 역파싱 데이터(포트/LOT 등)로 실행할 애니 JSON 선택됨
- 선택된 안착 애니 JSON 실행됨
- 공정 대기 시간 동안 진행현황(%) 갱신됨

### 예시 3) `MOVE_TRANSFERING` (IN/OUT → 버퍼 이동)

- 이벤트 발생함(출발 포트/도착 포트 포함됨)
- 이동 정보를 포함하는 XML 생성됨 → 역파싱됨
- 역파싱 데이터(from/to 포트)로 이송 애니 JSON 선택됨
- 선택된 이송 애니 JSON 실행됨
- 공정 진행 중 진행현황(%) 갱신됨

### 예시 4) `MOVE_REQ` (버퍼 → 공정포트(EP) 투입)

- 이벤트 발생함(BP→EP 이동 정보 포함됨)
- XML 생성됨 → 역파싱됨
- 역파싱 데이터로 투입/이송 애니 JSON 선택됨
- 선택된 애니 JSON 실행됨
- 공정 진행 중 진행현황(%) 갱신됨

### 예시 5) `READYTOUNLOAD` → `REMOVED` (회수 준비 → 실제 반출)

- `READYTOUNLOAD` 발생함(회수 준비 상태 기록됨)
  - XML 생성/역파싱됨
  - 보통 애니 JSON 실행은 생략됨(상태/기록 중심)
- 이후 `REMOVED` 발생함(실제 반출 단계임)
  - XML 생성됨 → 역파싱됨
  - 역파싱 데이터로 회수 애니 JSON 선택됨
  - 선택된 회수 애니 JSON 실행됨
  - 공정 진행/대기 중 진행현황(%) 갱신됨

---

## 5) 이벤트 처리 우선순위/대기 구조(핵심)

이벤트는 발생할 때마다 바로 처리 “요청”되나, 애니메이션은 동시에 여러 개를 한꺼번에 실행하지 않고 **대기열(큐)** 에 쌓아 순서대로 실행됨.
즉, “이벤트 발생 순서”와 “애니메이션 실제 실행 순서”가 항상 1:1로 즉시 맞지 않을 수 있음.

### 5.1 기본 원칙

- 애니메이션 실행 중 새 이벤트가 발생하면 **대기열에 적재됨**
- 현재 실행 중인 애니메이션은 기본적으로 **중간에 끊지 않고 완료까지 실행됨**
- 완료 시점에 대기열에서 다음 애니메이션을 꺼내 **바로 실행됨**

### 5.1-1 공정시간 우선 옵션(요청사항 반영 가능)

애니메이션을 “끝까지 재생”하는 방식 외에, 설정에 따라 아래 방식도 가능함.

- **공정시간 우선 OFF(기본 개념)**
  - 공정시간과 애니메이션시간 중 **긴 쪽을 기준으로** 다음 단계로 넘어감
  - 즉, 애니메이션이 길면 공정이 끝나도 **애니 종료까지 대기**하는 형태가 됨
- **공정시간 우선 ON(요청사항)**
  - 공정시간이 끝났는데 애니메이션이 더 길면, 애니메이션을 **즉각 종료(중단)하고 다음 단계로 진행**될 수 있음
  - 즉, 이 옵션이 켜진 상태에서는 “기본 원칙(끝까지 재생)”이 항상 보장되지 않음
  - “현실 공정 시간”을 우선해 시뮬 진행이 멈추지 않게 하는 목적임
  - 이벤트가 대기열에 쌓여 있더라도, 공정이 다음 단계로 넘어가면 **이전 단계 애니는 중단될 수 있음**

#### 간단 예시(공정시간 vs 애니시간)

- 상황: 어떤 단계의 공정시간=5초, 연결된 애니메이션=9초임
  - 공정시간 우선 OFF → 9초까지 애니 종료 대기 후 다음 단계 진행됨
  - 공정시간 우선 ON → 5초 시점에 애니 즉시 종료되고 다음 단계 진행됨

### 5.2 우선순위(중요)

- **생성/투입 성격 이벤트**와 **회수(반출) 이벤트**가 상대적으로 우선됨
  - 예: `ARRIVED`(특히 “생성/투입” 상황), `REMOVED`(회수/반출)
- 그 외 이동 이벤트(예: `MOVE_TRANSFERING`, `MOVE_REQ`)는 기본 우선순위로 대기열에 쌓임
- 우선 이벤트가 들어오면, 대기열에서 **더 앞쪽에 배치되어 다음 실행 대상으로 먼저 선택됨**
  - 단, “현재 재생 중인 애니메이션”을 즉시 끊고 선점하는 방식은 아님
    (단, 공정시간 우선 ON 설정 시에는 공정 종료 시점에 애니메이션이 중단될 수 있음)

### 5.3 이해를 돕는 간단 예시(큐 동작)

#### 예시 A) 이동 애니 실행 중 회수 이벤트가 들어오는 경우

- 상황: `MOVE_REQ` 애니 실행 중임
- 그 사이 `REMOVED` 이벤트 발생함(회수)
- 결과:
  - `REMOVED`는 대기열에 들어가되 **우선순위가 높아 다음 실행 순서에서 먼저 처리됨**
  - 현재 `MOVE_REQ` 애니는 끝까지 실행됨 → 끝난 직후 `REMOVED` 애니가 실행됨

#### 예시 B) 여러 이벤트가 연속으로 몰리는 경우

- 상황: 짧은 시간에 `MOVE_TRANSFERING` → `MOVE_REQ` → `ARRIVED`(투입) 순으로 이벤트 발생함
- 결과(개념):
  - `MOVE_TRANSFERING`, `MOVE_REQ`는 대기열에 기본 순서로 들어감
  - `ARRIVED`는 우선순위가 높아 대기열의 앞쪽으로 들어가 **다음 실행에서 먼저 처리됨**

---

## 6) 보고서 증빙(첨부 권장)

- **첨부 1 (스크린샷)**: 포트상태/진행현황/이력로그가 한 화면에 보이는 상태에서, 이벤트가 발생한 장면
- **첨부 2 (영상, 10~20초)**: `ARRIVED` 또는 `MOVE_TRANSFERING` 또는 `REMOVED`가 발생하고 애니메이션이 실행되는 장면
- **첨부 3 (스크린샷)**: 진행현황에서 %가 증가하는 장면(공정이 실제로 진행 중임을 보여주는 증거)
