# EBS 시뮬레이션 프로그램(tbs_control_2) 웹 연동 이벤트 명세

`tbs_control_2` 확장은 HyView livestream 메시징(T2V/V2T)을 통해 웹과 통신한다.
웹 → Kit 요청은 `T2V_*`, Kit → 웹 응답은 `V2T_*` 이벤트로 주고받는다.

## 구성 요소

| 계층 | 파일 | 역할 |
|---|---|---|
| 이벤트/키 SSOT | `morph.hyview_messaging/hyview_event_contract.py` | 이벤트명·payload/응답 키 상수 |
| 메시징 핸들러 | `morph.hyview_messaging/extension_handlers/ebs_handler.py` | T2V 수신·payload 파싱·V2T envelope 조립 |
| Kit 실행 브리지 | `morph.hyview_messaging/tbs_sim_bridge.py` | 메인 스레드 마샬링 및 TBS 시뮬레이션 실행 위임 |
| Kit 시뮬레이션 | `morph.tbs_control_2/control_window.py` 등 | 화면별 설정·프리런·재생·seek·타임테이블 처리 |
| 메인 스레드 디스패치 | `morph.tbs_control_2/kit_main_dispatch.py` | 메시징 스레드 → Kit main(UI) 스레드 큐잉 |

## 공통 규칙

- **화면(case) 매핑**: `case: 0` → 화면1(CASE A), `case: 1` → 화면2(CASE B)
- **비동기 처리**: Kit UI·USD·시뮬레이션 작업은 메인 스레드에 큐잉된다. V2T 응답은 실제 작업 완료 콜백에서 전송된다.
- **공통 성공 응답 envelope**:

```json
{ "code": 0, "message": "success", "data": {} }
```

- **공통 실패 응답 envelope**:

```json
{ "code": 1, "message": "오류 내용", "data": {} }
```

- 시뮬레이션 시작 응답의 `timetable_rows`는 행 전체가 아니라 **`t` 숫자 배열**이다.
- 타임테이블 행 전체는 `T2V_request_time_table`로 한 행씩 조회한다.
- 구 `V2T_response_simulation_timeline` chunk 전송 방식은 사용하지 않는다.

---

## 1. EP 포트 개수 변경

### 요청 — `T2V_request_eqp_change`

```json
{
  "case": 0,
  "eqp_id": "SPW1102",
  "ep_count": 2
}
```

| 키 | 타입 | 설명 |
|---|---|---|
| `case` | int | 대상 화면. `0`=화면1, `1`=화면2 |
| `eqp_id` | string | 웹·MES 식별 정보. 현재 Kit 동작에서는 사용하지 않음 |
| `ep_count` | int | EP 포트 개수. `2` 또는 `3` |

### 응답 — `V2T_response_eqp_change`

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "case": 0,
    "ep_count": 2
  }
}
```

### 동작

- 지정 화면의 EP 개수 설정을 변경한다.
- EP 콤보 모델, EP prim 표시/숨김 및 시뮬레이션 포트 레이아웃을 갱신한다.

---

## 2. EBS 적용 여부 변경

### 요청 — `T2V_request_ebs_enable`

```json
{
  "case": 0,
  "ebs_enable": true
}
```

| 키 | 타입 | 설명 |
|---|---|---|
| `case` | int | 대상 화면. `0`=화면1, `1`=화면2 |
| `ebs_enable` | bool | `true`=EBS 적용, `false`=EBS 미적용 |

### 응답 — `V2T_response_ebs_enable`

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "case": 0,
    "ebs_enable": true
  }
}
```

### 동작

- 지정 화면의 EBS 설정과 관련 포트 레이아웃을 즉시 갱신한다.
- 시뮬레이션 시작 설정의 `configs[n].ebs_enabled`로도 동일 설정을 전달할 수 있다.

---

## 3. 시뮬레이션 시작

### 요청 — `T2V_request_start_simulation`

```json
{
  "configs": [
    {
      "fab_id": "FAB01",
      "model_id": "MODEL01",
      "eqp_id": "EQP01",
      "lot_count": 6,
      "ep_count_idx": 1,
      "ebs_enabled": true
    },
    {
      "fab_id": "FAB01",
      "model_id": "MODEL02",
      "eqp_id": "EQP02",
      "lot_count": 4,
      "ep_count_idx": 0,
      "ebs_enabled": false
    }
  ]
}
```

- `configs`는 화면별 설정 배열이다.
- `configs[0]`은 case 0(화면1), `configs[1]`은 case 1(화면2) 설정이다.
- 각 원소는 `settings_snapshot`과 같은 flat object이며 원소 내부에 `case`를 넣지 않는다.
- 주요 설정에는 LOT 수, 생성·이동·공정 시간 범위, 초기 적재, 고장 포트, EP 개수 및 EBS 적용 여부 등이 포함된다.
- `fab_id`, `model_id`, `eqp_id`는 응답의 `sim` 객체에 echo 된다.

### 응답 — `V2T_response_start_simulation`

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "results": [
      {
        "case": 0,
        "sim": {
          "fab_id": "FAB01",
          "model_id": "MODEL01",
          "eqp_id": "EQP01",
          "ep_count": 3,
          "ebs_enable": true,
          "speed": 1.0
        },
        "timeline": {
          "timetable_rows": [5.74, 9.96, 19.59, 19.59, 27.21]
        },
        "bar_graph": {
          "empty_pct": {
            "all_ep_empty_pct": 30.25,
            "ep1_empty_pct": 40.1,
            "ep2_empty_pct": 35.2,
            "ep3_empty_pct": 42.0
          }
        }
      },
      {
        "case": 1,
        "sim": {},
        "timeline": {
          "timetable_rows": [4.82, 13.14, 21.03]
        },
        "bar_graph": {
          "empty_pct": {
            "all_ep_empty_pct": 28.5,
            "ep1_empty_pct": 37.2,
            "ep2_empty_pct": 33.8
          }
        }
      }
    ]
  }
}
```

### 응답 데이터 규칙

- `data.results[0]`은 화면1, `data.results[1]`은 화면2 프리런 결과이다.
- `timeline.timetable_rows`에는 **시간 `t` 값만 number 배열로 전달**한다.
- 같은 시간의 행이 여러 개면 같은 `t`가 배열에 중복될 수 있다.
- 타임테이블 행 object/string 및 별도 chunk는 시작 응답에서 보내지 않는다.
- `bar_graph.empty_pct`에는 전체 시뮬레이션 시간 대비 막대별 empty 비율(%)이 포함된다.
- 실패하면 `code: 1`이며 `data.results`는 빈 object 두 칸(`[{}, {}]`)이다.

### 동작

1. `configs[0]`, `configs[1]`을 각 화면 설정에 적용한다.
2. Kit UI의 시뮬레이션 시작과 동일한 경로로 2화면 프리런·재생을 시작한다.
3. 프리런 완료를 비동기로 기다린다.
4. 웹 전송용 slim 결과를 만들고 `timetable_rows`를 `t` 배열로 변환한다.
5. 개별 타임테이블 행 object는 case별로 보관해 이후 시간별 조회에 사용한다.

---

## 4. 재생·일시정지·배속 제어

### 요청 — `T2V_request_control_simulation`

```json
{
  "action": "play",
  "speed": 2.0
}
```

| 키 | 타입 | 설명 |
|---|---|---|
| `action` | string | `play` 또는 `pause` |
| `speed` | number | 적용할 재생 배속. 생략 시 `1.0` |

### 응답 — `V2T_response_control_simulation`

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "active": "play",
    "speed": 2.0
  }
}
```

### 동작

- `play`: 배속 적용 후 Kit 시뮬레이션 시작 경로를 실행한다.
- 이미 재생 중인데 다시 `play`를 요청하면 실패 응답을 반환한다.
- `pause`: 배속 적용 후 Kit 시뮬레이션 정지 경로를 실행한다.
- 지원하지 않는 `action`은 `code: 1`로 응답한다.
- 이 이벤트에는 `case`가 없으며 TBS 공용 시뮬레이션 제어로 동작한다.

---

## 5. 시뮬레이션 시간 seek

### 요청 — `T2V_request_seek_simulation`

```json
{
  "case": 0,
  "t": 120.0
}
```

| 키 | 타입 | 설명 |
|---|---|---|
| `case` | int | 대상 화면. `0`=화면1, `1`=화면2 |
| `t` | number | 이동할 시뮬레이션 시간(초) |

### 응답 — `V2T_response_seek_simulation`

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "case": 0,
    "t": 118.5,
    "t_requested": 120.0,
    "row_index": 12
  }
}
```

| 응답 키 | 설명 |
|---|---|
| `case` | 요청한 화면 |
| `t_requested` | 웹이 요청한 원래 시간 |
| `t` | 실제 적용된 시간(타임테이블 행 기준으로 스냅된 시간) |
| `row_index` | 적용된 타임테이블 행 인덱스 |

### 동작

- Kit 막대그래프 시간축 클릭과 동일한 seek 경로를 실행한다.
- 해당 화면의 프리런 결과가 준비된 뒤에만 사용할 수 있다.
- 타임테이블 행, 막대그래프, 포트 상태 및 재생 커서를 해당 시점으로 맞춘다.

---

## 6. 시간별 타임테이블 행 조회

### 요청 — `T2V_request_time_table`

```json
{
  "case": 0,
  "time": 6.09
}
```

| 키 | 타입 | 설명 |
|---|---|---|
| `case` | int | 조회 화면. `0`=화면1, `1`=화면2 |
| `time` | number | 시작 응답의 `timetable_rows`에서 선택한 시간 |

### 응답 — `V2T_response_time_table`

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "time": 235.53,
    "case": 0,
    "time_table": {
      "t": 235.53,
      "event": "REMOVED",
      "lot_id": "LOT_003",
      "label": "EP1->OHT LOT_003",
      "anim": "removed_ep1.json",
      "proc_sec": 30.1,
      "anim_sec": 13.0,
      "detail": "LOT_003 EP1->OHT 회수(출발포트=EP1, 도착포트=OHT) | 공정=30.1s 애니=13.0s",
      "all_ep_empty_pct": 5.22
    }
  }
}
```

### 데이터 및 선택 규칙

- `time_table`은 JSON 문자열이 아닌 **object 한 개**이며, 보관된 web-slim 행의 **실제 데이터가 필드 가공 없이 그대로** 들어간다.
- 시작 응답 생성 시 보관한 web-slim 타임테이블 행을 조회한다.
- 행 타입에 따라 필드 구성이 다르다.
  - 동작 시작(step) 행: `t`, `event`, `port_id`, `label`, `anim`, `proc_sec`, `anim_sec`, `detail` (`detail`은 값이 있을 때만 포함)
  - FOUP 공정(event) 행(`FOUP_PROCESS_START`/`FOUP_PROCESS_END`): `t`, `event` + 있으면 `port_id`, `from_port_id`, `to_port_id`, `lot_id`, `foup_id`, `lot_seq`
- 모든 행에 `all_ep_empty_pct`가 포함된다 — 해당 행의 `t`까지 진행된 시간 대비 ALL_EP empty 누적 비율(%).
- web-slim 규칙으로 삭제된 `screen`, `kind`, `process_time_priority` 필드는 포함되지 않는다.
- 요청 `time`과 행 `t`의 허용 오차는 `0.005초`이다.
- 같은 `t`에 여러 행이 있으면 `FOUP_PROCESS_START`, `FOUP_PROCESS_END`가 아닌 행을 우선한다.
- 일치 행이 없거나 시작 프리런이 완료되지 않았으면 `code: 1`, `time_table: {}`로 응답한다.

---

## 7. 웹·Kit 진행시간 동기화

### 요청 — `T2V_request_time_sync`

```json
{}
```

### 응답 — `V2T_response_time_sync`

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "time": 6.09
  }
}
```

### 동작

- 웹의 진행 시간이 Kit과 어긋났을 때 Kit의 현재 시뮬레이션 시간으로 보정하기 위한 이벤트이다.
- `data.time`은 Kit 화면1 재생 시계를 기준으로 한 현재 시뮬레이션 진행 시간(초)이다.
- 현재 시간은 소수 둘째 자리로 반올림한다.
- 재생 플레이어가 없으면 Kit EP 타임라인의 현재 시각을 사용하고, 확인 가능한 시각이 없으면 `0.0`을 반환한다.

---

## 이벤트 요약

| 방향 | 이벤트명 | 요청 핵심 키 | 성공 응답 `data` |
|---|---|---|---|
| T2V → | `T2V_request_eqp_change` | `case`, `eqp_id`, `ep_count` | — |
| → V2T | `V2T_response_eqp_change` | — | `case`, `ep_count` |
| T2V → | `T2V_request_ebs_enable` | `case`, `ebs_enable` | — |
| → V2T | `V2T_response_ebs_enable` | — | `case`, `ebs_enable` |
| T2V → | `T2V_request_start_simulation` | `configs[2]` | — |
| → V2T | `V2T_response_start_simulation` | — | `results[2]` |
| T2V → | `T2V_request_control_simulation` | `action`, `speed` | — |
| → V2T | `V2T_response_control_simulation` | — | `active`, `speed` |
| T2V → | `T2V_request_seek_simulation` | `case`, `t` | — |
| → V2T | `V2T_response_seek_simulation` | — | `case`, `t`, `t_requested`, `row_index` |
| T2V → | `T2V_request_time_table` | `case`, `time` | — |
| → V2T | `V2T_response_time_table` | — | `case`, `time`, `time_table` |
| T2V → | `T2V_request_time_sync` | 빈 `{}` | — |
| → V2T | `V2T_response_time_sync` | — | `time` |

## 유지보수 기준

이벤트명이나 payload/응답 키를 변경할 때는 다음 순서로 맞춘다.

1. `morph.hyview_messaging/hyview_event_contract.py`
2. `morph.hyview_messaging/extension_handlers/ebs_handler.py`
3. `morph.hyview_messaging/tbs_sim_bridge.py`
4. 웹 클라이언트의 송수신 이벤트 타입 및 payload 모델
5. 이 문서와 로컬 디버그 문서

