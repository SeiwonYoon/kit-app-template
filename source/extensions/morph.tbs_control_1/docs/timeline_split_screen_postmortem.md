# 분할 화면(화면2) USD_TIMELINE 문제: 원인/회귀/수정 원칙 정리 (Postmortem)

작성일: 2026-04-14
대상 프로젝트: `morph.tbs_control_1` (Omniverse Kit 기반)
핵심 이슈: **분할 화면(보조 USD 컨텍스트)에서만 `USD_TIMELINE` 애니메이션이 보이지 않음**

---

## 요약(결론)

- **문제의 본질**: 화면2는 “다른 USD 컨텍스트/스테이지”를 쓰는데, Kit의 `omni.timeline` 재생이 **보조 뷰포트의 평가(렌더) 시간 소스에 연결되지 않는 환경**이 있음.
- `USD_TIMELINE`이 XformOp 키가 아니라 Skel/기타 속성 키로 저장된 경우, “XformOp 베이크”로는 대상이 0개가 되어 **절대 움직일 수 없음**.
- 화면2 타임라인만 고치려다 화면1/전체 공정이 망가진 가장 큰 이유는 다음 2가지:
  - **전역 상태/전역 stop API가 멀티 화면을 끊어먹음** (특히 `stop_all_*` 류)
  - **tick pause(배속 동기) 로직이 화면별 상태와 맞물려 교착(0%)을 재발**시킴

이 문서는 “원복 후 재시도”를 할 때, **절대로 다시 밟으면 안 되는 지뢰**(전역 stop, 잘못된 pause, 마지막 그룹 완료 처리)와, **진단→수정 순서**를 남기기 위한 것입니다.

---

## 시스템 구조(문제 이해를 위한 최소 배경)

### 1) 멀티 화면 구조

- 화면1: 기본 `omni.usd` 컨텍스트(이하 “기본 컨텍스트”)
- 화면2+: `sim_multi_view.py`에서 `morph_tbs_split_aux_1` 같은 **네임드 USD 컨텍스트**를 만들고, 해당 컨텍스트로 `create_viewport_window(usd_context_name=...)`로 보조 뷰포트를 생성

### 2) 이벤트→JSON→애니 실행 흐름

1. 시뮬 엔진이 이벤트 발생(`ARRIVED`, `REMOVED`, `MOVE_TRANSFERING` 등)
2. UI 스레드에서 `_sim_ui_sink_anim_event()`가 이를 받아 JSON 매핑 실행
3. `_execute_mapped_sequence_stub()`에서 `SequenceRunner.run(parsed_steps, usd_context_name=...)` 호출
4. `SequenceRunner`가 step들을 실행
   - `MOVE/ROTATE`: 코드로 xform op 조작
   - `USD_TIMELINE`: 저장된 타임라인(키프레임) 구간 재생

---

## 왜 “화면2에서만 USD_TIMELINE이 안 보이는가”

### 관찰 1) 컨텍스트별 타임라인 인터페이스 분리는 환경 따라 실패할 수 있음

시도했던 접근:
- `ctx.set_timeline(ctx_name)` / `_get_timeline(usd_context_name)`에서 `get_timeline_interface(name)` 우선 사용

하지만 특정 Kit 환경에서는:
- **보조 뷰포트가 참조하는 시간 소스가 `omni.timeline`과 직접 연결되지 않음**
- 따라서 `tl.play()`로는 화면2의 렌더 평가가 갱신되지 않고 “안 움직임”

### 관찰 2) `arrived_ep1.json`은 USD_TIMELINE만 있고 대상 prim 힌트가 없음

`arrived_ep1.json`처럼 `USD_TIMELINE`만 있는 JSON은 prim 경로가 JSON에 없다.
즉 “무엇을 움직일지”는 **런타임에 로드된 USD 스테이지를 보고 판단**해야 함.

하지만 저장 애니메이션이 아래 중 무엇인지에 따라 접근이 달라진다:
- XformOp(translate/rotate/scale) 키 → Xform 기반 우회(베이크) 가능
- Skel/mesh deformation/visibility/instancer 등 → XformOp 샘플링으로는 대상 0개(불가)

실제 로그에서 확인된 사실:
- 화면2에서 `GetTimeSamplesInInterval`로 XformOp를 스캔했을 때 **targets=0**
  - 즉, `USD_TIMELINE`이 **XformOp가 아닌 속성에 키가 있는 케이스**

---

## “화면2 타임라인 고치려다 화면1/전체 공정이 망가진” 핵심 원인들

### 원인 A) 전역 stop API가 멀티 화면을 끊어먹음

`SequenceRunner.pause()`(및 stop 경로)에서
- `stop_all_translate_animations()`
- `stop_all_rotate_animations()`
- `stop_all_curve_animations()`

같은 **전역 정리**를 무조건 호출하면:
- 화면2 러너가 멈추는 순간 화면1에서 돌고 있던 MOVE/ROTATE도 같이 끊길 수 있음
- 결과: “첫 실행만 되고 그 다음부터는 MOVE/타임라인이 안 돈다” 같은 회귀가 발생 가능

해결 원칙:
- 보조 컨텍스트(이름 있는 컨텍스트)에서는 **전역 stop 금지**
- “현재 화면/현재 그룹 스텝만” 정리하도록 제한

### 원인 B) 배속 동기(pause_evt) 로직이 교착을 만들고 ‘0% 정지’를 재발

배속(예: 5x)에서 공정이 애니를 앞지르지 않게 하기 위해 tick을 멈추는 로직이 있었고,
이 pause 이벤트가 다음과 얽히면 교착이 생김:
- runner가 이미 완료/예외/리셋되었는데 pause만 남아있음
- 화면별 active/pending 상태가 꼬여 실제로는 애니가 안 도는데 pause는 계속 유지됨

증상:
- 양쪽 진행률이 0%에서 멈춘 것처럼 보임
- “공정만 진행/애니 없음” 또는 “한 화면만 멈춤” 등 비결정적

해결 원칙:
- 멀티 tick 워커에 **fail-safe** 필요:
  - pause가 켜져 있는데 runner가 안 돌고 active도 비어 있으면 pause를 자동 clear
- `USD_TIMELINE only` 같은 케이스에서는 pause를 걸지 않는 것이 안전할 수 있음(환경별 평가 타이밍 차이)

### 원인 C) 시퀀스 종료 시점 버그로 폴백 tick이 ‘시작도 전에’ 죽음

화면2 `USD_TIMELINE` 우회(업데이트 스트림 구독)에서
- tick 콜백이 들어오기도 전에 runner가 `_complete_sequence()`를 호출해 `_running=False`가 되면
- 폴백 tick이 즉시 return → 시간 구동이 1프레임도 진행되지 않음

해결 원칙:
- 마지막 그룹(마지막 스텝)의 종료는 “스텝 완료 콜백”을 기준으로 해야 함
- 마지막 그룹에 들어갔다고 즉시 `_complete_sequence()`를 호출하면 안 됨

### 원인 D) 리셋/정지에서 화면별 상태를 완전히 초기화하지 않으면 재시작이 깨짐

멀티 화면은 화면별로 다음 상태가 분리되어야 한다:
- `SequenceRunner` 인스턴스(화면별)
- `pause_evt`(화면별)
- active/pending 큐(화면별)

정지/리셋에서 이들을 일부만 정리하면:
- 다음 실행에 이전 실행 잔여 상태가 남아 애니가 안 돌거나 tick이 멈춤

해결 원칙:
- `on_sim_stop_clicked()`에서 **화면별 runner/pause/pending/active/until_wall**을 모두 clear

---

## 화면2 타임라인 “우회 시도”의 진행과 한계

### 1) XformOp 기반 베이크(샘플링→TBS_OFFSET 적용)

아이디어:
- 보조 화면은 `omni.timeline`이 안 붙으니,
- 프레임 t에서 prim의 변환을 샘플링해서 TBS_OFFSET에 적용하면 “움직이는 것처럼” 만들 수 있다.

한계:
- `arrived_ep1`은 **XformOp 샘플이 없는 케이스(targets=0)** → 이 방식은 불가

### 2) 보조 viewport 시간 소스 구동(Viewport API의 time)

아이디어:
- 보조 viewport가 내부적으로 갖는 시간 소스를 직접 조작하면 Skel 등도 재생될 수 있다.

실제 관찰:
- `viewport_api`에는 `set_time` 같은 공개 메서드가 없고,
- 내부적으로 `_ViewportAPI__time` 같은 속성이 존재하는 환경이 있음
- 단, 단위가 “초”인지 “프레임”인지 환경마다 다를 수 있고,
- 시간만 바꿔도 렌더가 갱신되지 않으면 `fill_frame/freeze_frame/wait_for_rendered_frames` 같은 트리거가 필요할 수 있다.

중요:
- 이 접근은 **Kit/viewport 구현 세부에 강하게 의존**함(버전·환경별 차이 큼)

---

## 재발 방지 체크리스트(원복 후 다시 시도할 때)

### 절대 금지

- **보조 컨텍스트(화면2)에서 전역 stop 호출** (`stop_all_translate_animations` 등)
- 마지막 그룹 진입 시점에 **즉시 `_complete_sequence()` 호출**
- 배속>1 동기(pause)에서 **fail-safe 없는 pause 유지**

### 반드시 포함

- 멀티 tick 워커에서 pause 교착 방지 fail-safe
- stop/reset에서 화면별 runner/pause/pending/active 상태 전부 정리
- 화면2 `USD_TIMELINE`의 “targets=0인지 여부”를 먼저 확인(=XformOp 베이크 가능/불가 즉시 판정)

---

## 권장 “재시도 절차”(원복 후)

1. **원복 상태에서** 화면1 MOVE/ROTATE/curve가 정상인지 확인(회귀 없는 베이스라인 확보)
2. 분할 2화면에서 `arrived_ep1` 실행 시, 화면2에서 `targets=0` 여부를 로그로 확인
3. `targets=0`이면:
   - Xform 베이크는 버리고,
   - viewport 시간 소스 구동 or 프로세스 분리(화면별 Kit 프로세스) 중 하나를 선택
4. 어떤 접근을 하더라도:
   - 전역 stop 금지
   - pause 교착 fail-safe
   - 마지막 스텝 완료 기반 종료

---

## (추가) “원복 후 현재 상태” 점검 메모 (2026-04-14)

원복 후 동일한 수정 요청이 들어왔을 때, 문제를 키우지 않기 위한 **사전 점검**을 기록한다.

### 확인된 사실(원복 직후)

- 코드(.py) 기준으로는 **변경 사항이 없고**, 변경된 것은 주로 아래와 같은 **데이터/산출물**이었다.
  - `resource/sample_1.usd`
  - `data/sim_sequences/*.json` (arrived/removed 관련)
  - `data/sim_logs/*.xlsx` (시뮬 로그 export)
  - 본 문서(`docs/timeline_split_screen_postmortem.md`) 및 기타 문서/프롬프트 파일

따라서 “원복했는데도 동작이 이상하다”가 나오면, 다음 중 하나를 먼저 의심해야 한다.

- **(A) 실제로 실행 중인 확장/모듈 경로가 예상과 다름**(동일 이름의 다른 확장/패키지 로드)
- **(B) JSON/리소스(USD) 변경으로 인해 시퀀스 구성이 달라짐**(예: USD_TIMELINE only / MOVE 포함 여부)
- **(C) 사용자 조작(배속/공정확인/리셋)로 상태가 꼬인 것처럼 보이나, 코드 회귀는 아님**

### 원복 상태에서 “문제 키우지 않는” 최소 진단 순서(코드 수정 전)

1. **작업 트리 확인**
   - “코드(.py)가 바뀌었는지/데이터만 바뀌었는지”를 먼저 확인한다.
2. **재현 JSON 분류**
   - `USD_TIMELINE only`인지, `MOVE/ROTATE/DELAY`가 섞여 있는지 분류한다.
   - `USD_TIMELINE only`는 prim 힌트가 없고 XformOp 키가 아닐 수 있어, “베이크/우회” 접근이 달라진다.
3. **멀티 화면 지뢰 체크(아래 4개)**
   - 전역 stop 호출 유무
   - pause 교착(fail-safe) 유무
   - 마지막 스텝 종료 타이밍(러너가 먼저 죽지 않는지)
   - stop/reset에서 화면별 상태 정리 유무

이 3단계를 통과하기 전에는 “화면2 USD_TIMELINE만 고치자”라는 목표를 그대로 진행하면,
과거처럼 화면1/전체 공정까지 망가지는 회귀가 다시 발생할 가능성이 높다.

---

## 현재까지 코드 변경이 닿았던 주요 파일(추적용)

- `morph/tbs_control_1/sim_multi_view.py`
  - 보조 컨텍스트 생성/스테이지 오픈/보조 뷰포트 생성
- `morph/tbs_control_1/usd_animation_control.py`
  - 타임라인 인터페이스 조회/재생/완료 콜백
- `morph/tbs_control_1/sequence_engine.py`
  - `USD_TIMELINE` 실행 분기 및 보조 화면 우회(베이크/viewport time) 시도
- `morph/tbs_control_1/control_window.py`
  - 화면별 runner/pause/pending/active 관리, stop/reset 정리, 멀티 tick 워커 fail-safe

---

## 부록: 로그에서 원인을 빠르게 판별하는 키워드

- `targets=0 (no time-sampled XformOps)`
  → Xform 베이크 불가(스켈/기타 속성 키 가능성 큼)
- `viewport hooks vw=[] api=[]`
  → set_time 류 메서드 없음(속성 기반/내부 time 소스일 가능성)
- `viewport time drive method=api.__time=frame|sec`
  → 내부 time 소스는 바뀜(하지만 렌더 갱신 트리거가 추가로 필요할 수 있음)
- `running=False`로 tick enter
  → 마지막 그룹 완료 처리 버그(러너가 먼저 종료됨)
