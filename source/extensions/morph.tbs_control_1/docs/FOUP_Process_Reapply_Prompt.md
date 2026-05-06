# FOUP 공정 “재적용” 프롬프트 문서 (원복 기반)

이 문서는 **FOUP 공정 기능만**을 “FOUP 추가 전 정상 동작하던 코드”에 다시 적용할 때, **버그(리셋 잔상/포트 전체 소거/막대그래프 깜빡임)** 없이 구현하기 위한 **프롬프트(요구사항+안전 규약+체크리스트)** 입니다.
목표는 “기능 추가”가 아니라 **기능을 넣어도 기존 UI/시뮬 표시가 절대 깨지지 않게 하는 계약(Contract)을 먼저 고정**하는 것입니다.

---

## 0) 핵심 원칙 (이 문서의 3대 계약)

- **계약 A — `ports_occupancy` 필수**
  - UI로 전달되는 **모든** 시뮬 이벤트/프로그레스 payload에는, **항상** `ports_occupancy`가 들어가야 한다.
  - `ports_occupancy`는 “현재 포트 점유 스냅샷”이며 **빈 dict 금지**(UI가 “전포트 EMPTY”로 오해 → 전부 사라짐/깜빡임 원인).

- **계약 B — Reset은 “라벨+타이머+캐시+큐”까지 완전 초기화**
  - 리셋에서 텍스트만 바꾸면 안 된다. **FOUP 전용 라벨**, **FOUP 자동-clear 타이머(구독)**, **마지막 progress payload 캐시**, **playback/큐 잔여**까지 초기화되어야 한다.

- **계약 C — 타임라인(막대그래프)은 destroy/rebuild 금지**
  - FOUP 공정으로 이벤트가 늘면 렌더 빈도도 늘어난다.
  - 막대 위젯을 매 tick마다 `destroy()` 후 생성하면 “정상 동작하던 시절엔 티가 안 나던 깜빡임”이 FOUP 추가 후 폭발한다.
  - 따라서 **동일 위젯 유지 + 내부 clear/rebuild**로만 갱신한다.

---

## 1) FOUP 공정 기능 요구사항(스펙)

### 1.1 공정 시점/동작

- **FOUP가 EP 포트에 안착(ARRIVED_EP\*)한 뒤** FOUP 공정이 시작된다.
- FOUP 공정 시작 시 FOUP prim을
  - **Y +3.2** 만큼 **1.0초** 동안 이동
- FOUP 공정 시간
  - 기본 랜덤 범위 **30~60초**
  - UI에서 min/max 변경 가능
- FOUP 공정 종료 시 FOUP prim을
  - **Y -3.2** 만큼 **1.0초** 동안 이동
- 이 “-3.2 이동까지 완료”된 이후에야 **회수(Removed) 가능 상태**가 된다.

### 1.2 동시성(가장 중요)

- **전역적으로** FOUP 공정은 **동시에 1개만** 진행된다.
  - EP1에서 공정 중이면 EP2/EP3는 공정 시작 불가(대기).
  - 단, **다른 이벤트/애니메이션(예: BP 이동 등)** 은 정상 진행되어야 한다.

### 1.3 Removed 이벤트 대기 규칙

- Removed 이벤트가 발생해도 다음 조건에서는 “즉시 처리”하지 않고 **대기**한다.
  - FOUP 공정이 아직 시작되지 않았거나(대기열)
  - FOUP 공정 진행 중이거나
  - FOUP 공정의 종료 동작(“-3.2 이동”)이 끝나지 않았을 때
- 단, “특정 EP의 Removed 애니메이션이 실행 중”인 상황에서도,
  - 다른 EP에 대기 중인 FOUP가 있다면 **그 EP의 FOUP 공정은 시작 가능**해야 한다.
  - 즉, “Removed 애니메이션”은 FOUP 공정의 전역 락과 **동일 자원으로 묶지 않는다**.

### 1.4 FOUP prim 경로

- FOUP prim path는 `config/port_lot_prim_paths.json`(프로젝트 구조에 맞는 실제 파일명)에서 얻는다.
  - 키: EP 포트 ID (예: `EP1`)
  - 값: 해당 EP에 올라간 FOUP prim 경로(USD prim path)

---

## 2) 이벤트/Progress payload 계약 (UI 안전성)

### 2.1 공통 필드(모든 payload에 필수)

아래 키는 “UI로 전달되는 모든 이벤트/진행현황” payload에 **반드시 포함**한다.

- `tbs_sim_screen`: 화면 인덱스(1~N)
- `sim_time`: 시뮬 시간 문자열 혹은 float(일관된 포맷 권장)
- `ports_occupancy`: **필수**(dict)
  - 예: `{"EP1": "LOT_001", "EP2": "", "BP1": "..."}`
  - 빈 문자열/None은 EMPTY 의미
  - **빈 dict 자체는 금지**

> 이유: UI는 `ports_occupancy`를 기준으로 포트 박스/EP 타임라인 색을 갱신한다.
> FOUP 추가 후 버그의 대부분은 “일부 payload가 `ports_occupancy`를 누락/빈 dict로 보내면서 UI가 전부 EMPTY로 덮어쓰는 현상”에서 시작된다.

### 2.2 FOUP 공정 Progress 필드(권장)

FOUP 진행상황을 별도 라벨에 고정 표시하려면 아래 detail을 추가한다.

- `event_seq = "FOUP_PROCESS"`
- `detail`(문자열 or dict; 가능하면 dict 권장):
  - `ep_hint`: `"EP1"` 등
  - `status`: `"WAITING" | "RUNNING" | "DONE"`
  - `percent`: 0~100
  - `elapsed`: 경과 초
  - `total`: 총 초
  - `waiting_n`: 대기열 길이(전역 자원 queue 길이)

### 2.3 이벤트명(애니메이션 훅용)

FOUP Y 이동 애니메이션을 트리거하는 이벤트는 아래 2개로 고정한다.

- `FOUP_PROCESS_START`
- `FOUP_PROCESS_END`

각 이벤트 payload는 **반드시** `ports_occupancy` 포함(위 계약 A).

---

## 3) 구현 가이드(원복 코드에 “안전하게” 붙이는 방식)

### 3.1 시뮬 엔진 레벨(FOUP 공정 코루틴)

권장 구조(개념):

- 전역 자원: `foup_process_res = simpy.Resource(env, capacity=1)`
- EP에 로트가 올라간 직후:
  - `env.process(run_ep_foup_process(ep_port, lot))`

`run_ep_foup_process` 내부:

- `with foup_process_res.request() as req: yield req`
- 이벤트 emit: `FOUP_PROCESS_START` (+ `ports_occupancy`)
- Y +3.2 이동 애니메이션은 “UI 애니메이션 훅”에서 처리(시뮬엔진은 이벤트만)
- 공정시간만큼 대기(대기 중 progress emit)
  - progress emit은 **항상 ports_occupancy 포함**
- 이벤트 emit: `FOUP_PROCESS_END` (+ `ports_occupancy`)
- Y -3.2 이동 애니메이션은 UI 훅에서 처리
- 이후에야 EP를 “pickup 가능” 상태로 변경(removed가 진행될 수 있게)

### 3.2 Removed 대기(정확한 자원 분리)

- Removed 대기 조건은 **FOUP 공정 완료 플래그**(예: `_ep_awaiting_pickup[ep]=True` 같은 상태)가 켜질 때까지 기다리게 한다.
- Removed 애니메이션 자체는 FOUP 공정 전역 자원에 묶지 않는다.
  - 그래야 “Removed 애니 중에도 다른 EP에서 FOUP 공정 시작 가능”이 된다.

---

## 4) UI 구현 가이드(FOUP 전용 고정 표시 + 리셋)

### 4.1 FOUP 진행상황은 progress_label과 분리(고정 영역)

- 화면별 모니터 채널에 `foup_progress_label`을 둔다.
- `event_seq == "FOUP_PROCESS"` 인 progress는
  - **오직 `foup_progress_label`만 업데이트**
  - 일반 `progress_label`은 건드리지 않는다(교차 깜빡임 방지)

### 4.2 Reset 계약(FOUP 잔상 방지 체크리스트)

Reset 시 아래가 “모두” 초기화되어야 한다.

- `foup_progress_label.text` 즉시 초기화
- FOUP 자동-clear용 update 구독(예: `_sim_foup_clear_sub`) unsubscribe + None
- `_sim_foup_clear_deadline_by_screen` 등 타이머 상태 dict 초기화
- `_sim_progress_last_payload_by_screen` 같은 “마지막 progress 캐시” 초기화
- (playback/큐 구조가 있으면) reset 이후 들어오는 잔여 payload를 무시하는 gen/token 가드

> 리셋 후에도 FOUP 문구가 남는 현상은 보통 “라벨은 초기화했지만, 다음 frame에 이전 타이머/캐시가 다시 써버리는” 구조에서 발생한다.

---

## 5) 포트 전체가 사라지는(깜빡이는) 버그 방지 규칙

아래 둘 중 하나는 **반드시** 선택해서 적용한다. (둘 다 하면 더 안전)

- **규칙 1(강제)**: 엔진이 emit하는 모든 payload에 `ports_occupancy`를 넣는다(계약 A).
- **규칙 2(보강)**: UI는 `ports_occupancy`가 비어있으면
  - “전부 EMPTY로 갱신”하지 말고
  - **마지막 스냅샷으로 폴백**하거나
  - **포트패널 업데이트 자체를 skip**

FOUP 공정 추가로 이벤트 종류/빈도가 증가하면, “누락 payload”가 훨씬 눈에 띄게 된다.
따라서 FOUP 기능을 다시 넣을 때는 **먼저 이 규칙부터 고정**해야 한다.

---

## 6) 막대그래프(EP timeline) 깜빡임 방지 규칙

- 매 tick마다 `destroy()` 후 위젯을 새로 만들지 않는다.
- 권장:
  - 동일 위젯을 유지하고 내부를 `clear()`한 뒤 다시 child만 구성한다.
  - 또는 “상태는 계속 누적하되 렌더는 5Hz throttle” 같은 정책을 둔다.

FOUP 공정은 진행 emit이 늘어 UI 갱신이 많아지므로, FOUP 전에는 드러나지 않던 깜빡임이 바로 드러난다.

---

## 7) 재적용(구현) 순서 — 실패를 줄이는 방법

1) **엔진 쪽에 FOUP 공정 코루틴 + 전역 Resource(capacity=1)** 추가
2) **이벤트/Progress payload에 `ports_occupancy` “항상 포함”**을 먼저 보장
3) UI: `foup_progress_label` 분리 + `event_seq=="FOUP_PROCESS"` 분기 적용
4) Reset 계약(B) 구현(라벨+타이머+캐시+큐/generation 가드)
5) 애니 훅: `FOUP_PROCESS_START/END` → port_lot prim 경로 → Y 이동 애니메이션
6) 막대그래프는 destroy 금지(계약 C) 확인

---

## 8) 최소 테스트 시나리오(재현/검증 체크리스트)

### 8.1 FOUP 공정 단일 진행 검증

- EP1 도착 → FOUP 공정 시작(라벨에 EP1 표시, percent 증가)
- EP2 도착 → FOUP 공정은 “대기”로 표시(대기열 +1), EP2 공정은 즉시 시작하지 않음

### 8.2 Removed 대기 검증

- EP1에 Removed 이벤트가 들어와도,
  - FOUP 공정(+3.2 → proc → -3.2) 완료 전에는 Removed가 진행되지 않아야 함
- EP1 Removed 애니메이션이 실행 중인 동안,
  - EP2가 대기 중이면 EP2 FOUP 공정은 시작 가능해야 함

### 8.3 Reset 잔상 검증

- FOUP 라벨이 RUNNING 상태일 때 Reset 클릭
  - 즉시 “FOUP 공정: 없음”으로 바뀌고,
  - 다음 프레임에도 이전 문구가 다시 써지지 않아야 함(타이머/캐시/큐 잔여 방지)

### 8.4 포트 전체 소거/깜빡임 검증

- FOUP 진행 중/다른 이벤트 중에도 EP 포트 점유 표시가 갑자기 전부 EMPTY로 바뀌지 않아야 함
- 이때 로그로 payload를 확인했을 때 `ports_occupancy`가 누락/빈 dict로 오는지 반드시 확인

### 8.5 EP timeline 깜빡임 검증

- USD_TIMELINE 재생 + FOUP 진행이 동시에 있을 때도
  - 막대 영역이 전체적으로 번쩍이며 깜빡이지 않아야 함

---

## 9) “프롬프트” 템플릿 (다시 구현할 때 그대로 붙여넣기)

아래 프롬프트를 그대로 사용하면, “FOUP 공정만” 안전하게 재적용하도록 유도할 수 있다.

"""
현재 저장소를 FOUP 공정 추가 전 정상 동작 커밋으로 원복한 상태에서, FOUP 공정 기능만 다시 추가해라.

필수 요구사항:
- FOUP가 EP에 도착한 뒤 공정 시작
- 시작: FOUP prim을 Y +3.2, 1.0초 이동
- 공정시간: UI에서 min/max(기본 30~60초) 랜덤 대기
- 종료: FOUP prim을 Y -3.2, 1.0초 이동
- 전역적으로 동시에 1개 FOUP 공정만 진행(simpy.Resource capacity=1)
- Removed는 공정 완료(+ -3.2 이동 완료) 전까지 대기
- 단, Removed 애니메이션 중에도 다른 EP의 FOUP 공정은 시작 가능(FOUP 공정 자원과 Removed 애니 자원을 분리)
- FOUP prim 경로는 port_lot_prim_paths.json에서 EP별로 얻어 이동 애니메이션 적용

버그 방지 계약(반드시 지킬 것):
- UI로 전달되는 모든 payload에 ports_occupancy를 항상 포함(빈 dict 금지)
- Reset에서 FOUP 라벨/타이머 구독/캐시/큐 잔여까지 완전히 초기화
- EP timeline 막대그래프는 destroy/rebuild 금지(위젯 유지 + clear/rebuild 또는 throttle)

검증:
- Reset 후 FOUP 문구 잔상 없음
- FOUP 진행 중에도 포트 전체가 사라지는(EMPTY로 덮임) 현상 없음
- USD_TIMELINE 재생 중에도 막대그래프가 깜빡이지 않음
"""

---

## 10) 참고(왜 FOUP 추가가 기존 버그를 “만들어낸 것처럼” 보이는가)

FOUP 공정은 구조적으로

- “긴 대기(30~60s) 동안 progress emit이 늘어남”
- “새 이벤트(START/END) 추가”
- “Removed를 ‘대기’시키는 새로운 상태머신 추가”

를 동반한다.
따라서 기존에 숨어있던 약한 규약(예: 어떤 이벤트는 `ports_occupancy`가 없는데 UI가 괜찮았던 것)이 FOUP 추가 후에는 **빈도 상승으로 바로 눈에 보이는 버그로 증폭**된다.

그래서 FOUP를 재적용할 때는 기능부터 넣지 말고,
먼저 **payload 계약(ports_occupancy 필수)** 과 **reset 계약(타이머/캐시/큐 초기화)** 을 고정하는 것이 가장 중요하다.
