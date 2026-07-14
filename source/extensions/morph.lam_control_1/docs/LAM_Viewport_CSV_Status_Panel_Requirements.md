## LAM Viewport CSV Status Panel — 요구사항 v1

이 문서는 **코드 수정 전에** 현재 `morph.lam_control` 구조와 대조하여, “3D 뷰포트 위 2D 상태 패널” 기능의 요구사항을 정리한 버전(v1)입니다.  
추가 설명이 들어오면 이 문서를 **v2, v3…로 계속 갱신**합니다.

---

## 1) 목표(Goal)

CSV를 선택하고 `Play` 했을 때, **3D 뷰포트 좌측 상단**에 2D 패널을 띄워 다음을 실시간 표시한다.

- CSV에서 추출 가능한 값: 예) `eqp_id`, `module_nm`, `lot_id`, `cassette_slot` 등
- 재생 상태 값: 예) 현재 진행 시간/실경과/총 시간/배속 등
- 현재 동작을 설명하는 “Current State” 요약 + 상세 설명 문구

또한, 향후 **다른 CSV 정보 추가 표시** 또는 **표시 문구 편집**이 쉽게 가능하도록,  
“값을 가져오고 가공하는 부분”이 **확장/편집이 쉬운 구조**여야 한다.

---

## 1.1 기능 #2 (추가) — FOUP 진행상황 3D 패널(요구사항 v1)

본 섹션은 “첫 번째 기능(뷰포트 좌상단 2D 상태 패널)”과 별개로, **FOUP 모델 옆에 붙는 3D 패널** 요구사항을 정리한다.

### 1.1.1 목표(Goal)

- 특정 FOUP prim(예: `/World/foup1`)을 지정하면, 해당 FOUP 모델의 **왼쪽에** 3D 패널(오버레이 UI)을 띄운다.
- 패널은 **wafer 번호보기(3D 라벨)처럼 대상 객체를 따라다니되**, 객체 중심에 고정된 라벨이 아니라 “왼쪽에 붙는 패널” 형태로 표시한다.
- 패널에는 FOUP 별로 아래 진행 현황이 **실시간 표시**되어야 한다.
  - 현재 wafer 갯수
  - 총 갯수(기본 25)
  - 공정 완료된 wafer 갯수
  - 공정 진행 대기중인 wafer 갯수
  - (추가) **웨이퍼 번호별 상태 목록**(예: “몇 번 wafer가 공정 진행중/완료/대기인지”)이 내부적으로 저장되고,
    Play 진행에 따라 실시간으로 갱신되어야 한다.

### 1.1.2 시뮬레이션 의미(업무 규칙 v1)

- Play 시 FOUP(25장)에서 ATM이 wafer를 하나씩 pick → 여러 슬롯을 이동
- 이후 VTM이 airlock을 거쳐 공정을 진행
- 마지막엔 ATM이 FOUP으로 place 하면서 “해당 wafer가 공정 완료”로 집계됨
- 위의 “완료/대기/현재 수량” 등이 패널에 실시간 반영되어야 한다.
 - (추가) “FOUP에 **몇 번 웨이퍼가 나가서 공정 진행중인지 / 공정 후 다시 안착했는지 / 공정 전인지**”를
   식별할 수 있도록, 웨이퍼 번호별 상태가 저장되어야 한다.

> **모호한 부분(추후 확정 필요)**  
> - “공정 완료”의 판정 기준: FOUP으로 최종 반환(place) 시점인지, 특정 module_nm(예: 특정 chamber dwell 종료)인지
> - “공정 진행 대기중”의 정의: FOUP에 남아 아직 pick되지 않은 wafer인지, 공정 큐에 들어갔으나 처리 전 상태인지 등

### 1.1.2.1 FOUP 집계 규칙(확정) — v1

사용자 설명에 따라, FOUP별 집계는 아래 카운팅 규칙으로 확정한다.

#### 용어

- **총 갯수(total)**: 각 FOUP의 총 웨이퍼 수. **25로 고정**.
- **pick 수(picked_count)**: 해당 FOUP에서 ATM이 웨이퍼를 **pick 해서 FOUP 밖으로 꺼낸 누적 개수**.
- **place 수(placed_back_count / done_count)**: ATM이 공정 완료 후 해당 FOUP에 **최종 place(반환)한 누적 개수**.

#### 상태 카운트(표시값)

- **공정 전(waiting_count)** = `total - picked_count`
  - 의미: **한 번도 pick 안 된 웨이퍼 수**
- **공정 완료(done_count)** = `placed_back_count`
  - 의미: 공정 후 **FOUP에 place 되어 안착 완료된 수**
- **공정 진행중(in_process_count)** = `picked_count - placed_back_count`
  - 의미: FOUP에서 **나갔지만 아직 돌아오지 않은 수**
- **현재 FOUP에서 빠진 수(removed_now)** = `in_process_count`
- **현재 FOUP 내 wafer 수(current_in_foup_now)** = `total - removed_now`
  - 즉, `total - (picked_count - placed_back_count)`

#### 예시(사용자 제공)

- `picked_count=6`, `placed_back_count=2`이면
  - `waiting_count = 25 - 6 = 19`
  - `done_count = 2`
  - `in_process_count = 6 - 2 = 4`
  - `current_in_foup_now = 25 - 4 = 21`

> **추가 확인 필요(모호한 부분, 추후 확정)**  
> - `picked_count`/`placed_back_count`를 어떤 이벤트로 인식할지(= “FOUP pick/place” 이벤트 판정 규칙)
> - (이하 항목은 사용자 확정으로 해소됨) 재작업/재공정(재반출) 시나리오 존재 여부

### 1.1.2.2 FOUP pick/place 이벤트 판정(확정) — v1

사용자 설명에 따라, FOUP 집계에 사용하는 이벤트는 아래처럼 확정한다.

- **공정 시작(pick 카운트 증가)**:
  - `atm_foup1_pick(slot_number=N)`
  - `atm_foup2_pick(slot_number=N)`
  - `atm_foup3_pick(slot_number=N)`
  - 의미: ATM 로봇팔이 FOUP에서 웨이퍼를 pick 하며 공정 투어가 시작되고, FOUP에서 빠져나감

- **공정 완료(place 카운트 증가)**:
  - `atm_foup1_place(slot_number=N)`
  - `atm_foup2_place(slot_number=N)`
  - `atm_foup3_place(slot_number=N)`
  - 의미: 공정 투어 종료. ATM이 해당 FOUP 슬롯으로 최종 반환(place)하면서 완료로 집계

추가 확정:

- **재반출(완료 후 다시 pick)**: 없음(가능성 없음)
  - 따라서 `done_count`는 단조 증가하며, `picked_count >= placed_back_count`가 항상 유지되어야 한다.

### 1.1.3 API/사용 방법(요구사항)

- 외부에서 아래 형태의 함수를 호출하면 패널이 생성/갱신 대상에 등록된다.
  - 예: `set_foupInfo_3d("/world/foup1", 1)`
    - 첫 인자: FOUP prim 경로(대상 객체)
    - 둘째 인자: FOUP 번호(예: 1,2,3)
 - (추가) FOUP 패널이 따라다닐 “기준 prim” 경로를 **명시적으로 설정**할 수 있어야 하며,
   경로를 바꾸면 바뀐 prim 객체 옆으로 패널이 이동해야 한다.

#### 1.1.3.1 FOUP 패널 앵커 prim 경로(확정)

- **FOUP1**
  - `/LAM_Foup_v01/LAM_Foup_v01/Foup_Root/Foup_01/Foup_01_Body`
- **FOUP2**
  - `/LAM_Foup_v01/LAM_Foup_v01/Foup_Root/Foup_02/Foup_02_Body`
- **FOUP3**
  - `/LAM_Foup_v01/LAM_Foup_v01/Foup_Root/Foup_03/Foup_03_Body`

### 1.1.4 UI 토글(시뮬 재생 창)

- 시뮬 재생 창(기존 CSV Play 창)에 **`foup 상태보기` 체크박스**를 추가한다.
- 기본값은 **체크(ON)**.
- 체크를 끄면 FOUP 3D 패널이 숨겨지고, 켜면 다시 표시된다.

### 1.1.5 데이터 소스 후보(현재 구조 기준)

아직 구현 전이므로, 집계 데이터는 아래 중 하나 또는 혼합으로 설계한다.

1) **CSV 기반 상태 추적(추천)**
- 현재 `simulation_play`가 CSV를 `DwellRecord(lot_id, cassette_slot, slot_key, start_sec, end_sec, ...)`로 모델링하므로
- 재생 중 “현재 시각 t”에서 각 `(lot_id, cassette_slot)`이 **어느 slot_key에 있는지**를 추정/집계할 수 있음

2) **스케줄 엔트리(transfer/pick/place) 기반 상태 추적**
- 현재 실행 중인 스케줄 엔트리(category, event_name, from→to)를 바탕으로
- FOUP에서 빠져나감(pick), FOUP으로 돌아옴(place) 같은 이벤트를 카운팅

3) **USD stage(visibility) 기반 상태 추적(보조)**
- prim visibility/hide/show 상태를 읽어 “현재 FOUP에 보이는 wafer 수” 같은 파생값 계산
- 단, 라벨/가시성은 연출에 의해 순간적으로 어긋날 수 있어 보조 수단으로만 고려

### 1.1.6 작업 방향(문서 단계 v1)

- “FOUP 상태 스냅샷”을 산출하는 단일 함수/구조를 둔다.
  - 입력: 현재 재생 상태(playing/paused), 현재 t, 현재 dwells 또는 스케줄
  - 출력(예시):
    - `foup_index -> {total, in_foup_now, done, waiting, in_process, ...}`
    - `foup_index -> wafer_state_by_number: { "01": "waiting", "02": "in_process", ... }`
- 3D 패널은 위 스냅샷을 표시만 하고, 집계 로직은 별도로 분리한다(확장성 목적).

### 1.1.7 결정이 필요한 질문(추후 Q&A)

1) “총 갯수”는 항상 25로 고정인가? CSV에 따라 변할 수 있는가?
2) “현재 wafer 갯수”의 의미는
   - (a) **FOUP 안에 현재 남아있는 수**
   - (b) **시스템 전체(FOUP+팔+챔버+에어록 등)에서 해당 lot의 활성 wafer 수**
   중 무엇인지?
3) “공정 완료” 판정 기준(FOUP 반환? 특정 공정 dwell 완료?)
4) 3D 패널 위치 규칙: “FOUP의 왼쪽”을 월드 좌표 기준으로 고정할지, 카메라/FOUP 방향 기준으로 항상 왼쪽에 보이게 할지?
5) 웨이퍼 번호별 상태 분류 규칙(필수 확정):
   - **waiting(공정 전)**: FOUP에 남아 있고 아직 공정 투어를 시작하지 않은 상태?
   - **in_process(공정 진행중)**: FOUP에서 빠져나가 ATM/VTM 경로로 이동 중이거나 챔버 dwell 중인 상태?
   - **done(공정 완료/안착)**: 공정 후 FOUP에 최종 place되어 안착한 상태?
   - “공정 후 다시 안착”을 **어떤 이벤트/조건으로 판정**할지?
6) 웨이퍼 번호(01~25) 기준은 `cassette_slot`(CSV 컬럼)로 고정인가?
   - 다른 컬럼명/규칙으로 바뀔 수 있으므로 “웨이퍼 번호 컬럼명”도 설정화가 필요한가?

### 1.1.7.1 질문 정리(업데이트)

위 사용자 설명으로 아래 항목은 **확정**으로 변경된다.

- 총 갯수: **25 고정**
- 공정 전(waiting): **한 번도 pick 안된 웨이퍼 수** = `25 - picked_count`
- 공정 진행중(in_process): **pick 누적 − place 누적**
- 공정 완료(done): **FOUP 최종 place 누적**
- 현재 FOUP 내 wafer 수: `25 - (picked_count - placed_back_count)`

따라서 남은 질문은 주로 “이벤트 판정 규칙” 및 “재공정 시 정책”으로 좁혀진다.

위 사용자 설명으로 “이벤트 판정 규칙”도 **확정**되어, 남은 질문은 주로 “표시/UI 위치 규칙”으로 좁혀진다.

---

## 1.2 기능 #3 (추가) — 장비/슬롯 이름 3D 패널(“기기정보보기”) 요구사항 v1

### 1.2.1 목표(Goal)

USD를 로드한 후, 장비/슬롯(예: 챔버, 에어록, 버퍼 등)의 **이름(label)**을 3D 뷰포트 상에 표시한다.

- 각 “슬롯 정보”는
  - 대상 **prim 경로**
  - 표시할 **이름(name)**
  - 표시 위치(좌/우/중앙 등) 및 오프셋
  - 글자 크기/색 등 스타일
  를 입력하면, 해당 prim 주변에 3D 라벨로 표시되어야 한다.
- 위치는 함수 호출 시 숫자를 조정해가며(예: 좌/우 선택 + 오프셋 값) 적절한 위치를 찾을 수 있어야 한다.

### 1.2.2 UI 토글(체크박스)

- 시뮬/제어 UI에 **`기기정보보기` 체크박스**를 둔다.
- 체크를 끄면 모든 장비/슬롯 라벨이 숨겨지고, 켜면 다시 표시된다.

### 1.2.3 슬롯(라벨) 대상 범위 v1

초기 대상 슬롯 종류(추가/변경 가능해야 함):

- `ATMArm`
- `VtmArm`
- `Chamber1` ~ `Chamber5`
- `CoolStation`
- `AirLock1`, `AirLock2`
- `Buffer3`, `Buffer4`
- `Aligner`
- `Foup1`, `Foup2`, `Foup3`

각 항목은 “경로를 입력하면 해당 prim 객체 좌측에 이름이 뜨는 것”을 기본으로 시작한다.

### 1.2.4 설정 파일(별도 저장) 요구사항

- “각 slot의 위치/경로/이름/스타일”을 저장하는 **별도 파일**을 하나 둔다.
- 이 파일은 다음을 만족해야 한다.
  - 경로/이름/스타일을 **수정 가능**
  - 항목을 **추가 가능**
  - 좌/우/중앙 등 앵커 및 오프셋을 조정 가능

예시 경로(쿨링 스테이션):

- `/LAM_Machanical_v01/LAM_Machanical_v01/MechanicalEquipment_Root/MechanicalEquipment/LoadPort_Root/LoadPort/Cooling_Station`

### 1.2.5 표시/스타일 편집 요구사항 v1

각 라벨 항목별로 아래 속성들을 조정 가능해야 한다.

- **표시 위치**: 중앙/좌측/우측(및 필요 시 상/하)
- **오프셋**: prim 기준 좌표에서의 이동량(숫자 입력)
- **글자 크기**
- **글자 색**

> 사용자 의도: “초기 구현 후 실제로 보면서 피드백하며 조정”하는 워크플로를 지원해야 한다.

### 1.2.6 작업 방향(문서 단계 v1)

- 1) 설정 파일로부터 `[{name, prim_path, anchor, offset, style...}]` 목록을 로드한다.
- 2) “기기정보보기”가 ON일 때, 뷰포트 SceneView(3D UI)에 라벨들을 마운트한다.
- 3) 각 prim의 월드 위치를 기준으로 라벨을 배치하고, 위치/스타일 오버라이드를 적용한다.

### 1.2.7 모호한 부분(추후 확정 필요)

- 설정 파일 포맷: JSON/YAML/TOML 중 어떤 포맷을 쓸지
- 위치(anchor)의 정의:
  - “prim의 월드 중심” 기준인지
  - “바운딩 박스의 좌측면” 같은 의미론적 anchor인지
- 오프셋 단위: 월드 단위(m)인지, 스크린 픽셀 기반인지
- 체크박스를 어디에 둘지:
  - CSV Play 창 내부인지
  - LAM 메인 창(tool 영역)인지

---

## 1.3 기능 #4 (추가) — Viewport 선택 제한(화이트리스트) 요구사항 v1

### 1.3.1 목표(Goal)

- **Viewport에서만** prim 클릭/선택을 제한한다.
  - Stage 창(트리)에서의 선택은 **제한하지 않는다**.
- 사용자가 입력한 “허용 prim 경로(루트)”만 선택 가능하게 한다.
- 허용 루트 아래에 자식 prim들이 여러 개 있어도,
  - 자식을 클릭하더라도 **항상 허용 루트 prim(그룹) 자체만 선택**되도록 한다.

### 1.3.2 UI 토글(체크박스)

- 시뮬 재생창과 2D 패널에 체크박스를 추가한다(기존 `공정만보기`, `wafer 번호보기` 체크박스 라인에 연달아 추가).
- 체크박스(가칭): `선택 제한(Whitelist)`
  - **ON**: 허용 경로 이외의 prim은 Viewport에서 클릭/선택 불가
  - **OFF**: 기존처럼 모든 prim 선택 가능

### 1.3.3 허용 경로 입력(설정) — v1 방향

- 사용자가 “허용 prim 경로 루트 목록”을 입력/수정할 수 있어야 한다.
- §8.4의 “설정 중앙화(py 1개)”에 다음 값을 둔다.
  - `VIEWPORT_PICK_WHITELIST_ROOTS = ["/Path/A", "/Path/B", ...]`

### 1.3.4 동작 규칙(선택 치환) — v1

- Viewport에서 사용자가 prim을 클릭해 선택이 발생했을 때:
  1) 선택된 prim path가 허용 루트 중 하나의 **하위 경로**이면
     - 선택을 “클릭된 자식 prim”이 아니라 **해당 허용 루트 prim**으로 **치환**한다.
  2) 허용 루트 하위가 아니면
     - Viewport 선택을 **해제(빈 selection)** 한다.

이 규칙으로 “루트 그룹만 선택”이 보장된다.

### 1.3.5 작업 방향(문서 단계 v1)

- 선택 변경 이벤트(Selection Changed)를 구독한다.
- 토글 ON일 때만 필터를 적용한다.
- 필터 적용 시:
  - 클릭/선택이 허용 루트 하위인지 검사
  - 허용이면 루트로 치환, 불허면 selection clear

> 메모: Stage 트리에서의 선택은 제한하지 않으므로,
> “뷰포트 클릭만 막기”가 목표일 때는 “뷰포트 입력/선택 이벤트 경로”에만 필터를 적용해야 한다.

### 1.3.6 모호한 부분(추후 확정 필요)

- 멀티 셀렉트(CTRL/Shift) 지원 여부
- 허용 루트가 여러 개일 때, 다중 선택을 허용할지(일단은 단일 선택 기준으로 시작 권장)

### 1.3.7 멀티 선택 정책(확정) — v1

- 멀티 선택(CTRL/Shift 등)은 **v1에서 미지원**.
- 단, 향후 필요 시 확장 가능하도록 구현부에 선택적으로 켤 수 있는 형태(예: 주석/플래그)로 남긴다.

### 1.3.8 “루트만 선택” 정책(재강조) — v1

- 사용자가 허용 루트 prim 경로를 설정한 경우, 그 하위 prim을 클릭하더라도
  - **하위 prim이 선택되면 안 되고**
  - **항상 허용 루트 prim(그룹)만 선택**되어야 한다.

---

## 2) UI 요구사항(레이아웃/행 구조)

---

## 2.4 유지보수/확장 가이드라인(필수) — v1

이 섹션은 “상태 표시” 기능(2D/3D 패널들)을 앞으로 수정/추가/유지보수하기 위한 **구조 가이드**이다.

### 2.4.1 절대 제약(가장 중요)

- 본 문서의 4개 기능은 모두 “상태 표시(관측/표시)”이며,
  **CSV Play의 재생 로직(이송/애니/타임키핑/스케줄 실행)은 절대 수정하지 않는다.**
- 구현은 반드시 “기존 재생 로직이 내보내는 상태”를 **구독(subscribe)·스냅샷 조회**해서 UI에 반영하는 방식으로만 한다.

### 2.4.2 단일 설정 파일(SSOT) 정책 — v1

사용자 요구에 따라, 수정/추가/유지보수의 1차 진입점은 **설정 `py` 파일 1개**로 통일한다.

- 가칭(확정): `morph/lam_control/lam_viewport_overlay_config.py`
- 이 파일이 담당하는 것(SSOT):
  - 기능 #1: 2D 상태 패널의 기본 행 구성/라벨/수동 기본값(EQ MODEL 등)
  - 기능 #1: CSV 컬럼명 매핑(예: `lot_id` 식별자 컬럼명, `eqp_id` 컬럼명 등)
  - 기능 #2: FOUP1~3 앵커 prim 경로 + 오프셋/스타일(필요 시)
  - 기능 #3: “기기정보보기” 라벨 엔트리 목록(prim_path + name + offset + style)
    - v1 초기값: CoolStation 1개만 채우고 나머지는 비움
  - 기능 #4: Viewport 선택 제한 허용 루트 목록(whitelist roots)

### 2.4.3 런타임 상태 저장(실시간 변화 데이터) 가이드

상태 표시가 필요한 값들은 “정적 설정”과 “실시간 런타임 상태”가 분리된다.

- **정적 설정**: 위 `lam_viewport_overlay_config.py` (SSOT)
- **실시간 런타임 상태**(권장 구조):
  - 한 모듈(예: `lam_viewport_overlay_state.py` 같은 런타임 상태 모듈)을 두고
  - “현재 스냅샷”을 딕셔너리/데이터클래스로 보관한다.

런타임 상태에 저장될 대표 값(예):

- CSV 선택 상태(선택된 파일명/경로)
- CSV Play 진행 스냅샷(현재 CSV t, wall elapsed, total 등)
- 스케줄 highlight(active_keys) 스냅샷(= “현재 실행 중 JSON 행”)
- 기능 #2 FOUP 집계 상태(FOUP별 pick/place 누적 및 derived counts)
- 기능 #4 선택 제한 ON/OFF, 허용 루트 목록(설정 참조 + 런타임 토글)

중요:

- 실시간 값은 여러 파일에 흩어져 global로 만들지 말고,
  **런타임 상태 모듈 1곳에 모아** “get_snapshot()” 같은 함수로 읽게 한다.

### 2.4.4 “재생 로직을 건드리지 않고” 상태를 얻는 방법(현재 구조 기반)

현재 `simulation_play.py`에는 UI/상태 표시를 위해 이미 제공되는 스냅샷/콜백들이 있다.
이것들을 재사용하면 재생 로직을 변경하지 않고도 표시를 구현할 수 있다.

- **Time(진행시간/실경과)**
  - `get_csv_play_progress_snap()` : UI 표시용 진행 스냅샷 dict 반환
  - (또는) `set_csv_play_progress_ui_callback(cb)` : Play 중 주기적으로 콜백 받기

- **Current State(녹색 JSON 행)**
  - `set_csv_play_timeline_highlight_callback(cb)` : JSON 실행 중인 스케줄 행 key 집합(active_keys) 콜백
  - `clear_csv_play_timeline_highlight()` : 종료/정지 시 강조 해제 (참고)

### 2.4.5 UI 바인딩 가이드(2D/3D 패널 공통)

UI는 “상태 스냅샷”을 읽어 화면에 그리는 역할만 맡는다.

- 2D 패널(뷰포트 좌상단):
  - `[(label, value)]` 행 리스트를 생성하는 “resolver/formatter”를 둔다.
  - 모델(SimpleStringModel 등) 또는 label.text를 갱신한다.

- 3D 패널/라벨(FOUP/기기정보):
  - prim의 월드 중심을 계산한 뒤(wafer 번호보기와 동일 패턴),
  - 설정 오프셋(x,y,z)을 더해 라벨/패널을 배치한다.
  - 표시 텍스트는 런타임 상태 스냅샷(집계 결과)을 그대로 사용한다.

### 2.4.6 체크박스 동기화(확정) — v1

- 시뮬 재생창과 2D 패널창에 존재하는 체크박스들은 상태가 **동기화**되어야 한다.
- 권장: 런타임 상태 모듈에 토글 값을 저장하고, 두 UI가 동일 값을 읽고/쓴다.

### 2.1 패널 위치/형태

- **Viewport Overlay UI**로, 뷰포트 위에 겹쳐 표시
- 위치: **좌측 상단**
- 형태: “CSV 선택/배속 설정 등이 있는 기존 2D 패널”과 비슷한 스타일/동작을 목표로 함

**모호한 부분(추후 확정 필요)**

- “좌측 상단에 우측에”라는 표현의 정확한 의미:
  - (a) 좌상단 코너에 붙되 가로로 우측으로 길게
  - (b) 좌상단 작은 박스
  - (c) 기존 HUD 옆에 나란히 배치
  - 중 무엇인지 확정 필요

### 2.2 행(row) 기반 표시

- 패널은 여러 개의 “행(row)”로 구성된다.
- 각 행은 2열 구조:
  - **좌측(label)**: 사용자 정의 제목(예: `EQ Model`, `EQP ID`, `Time`)
  - **우측(value)**: 해당 label에 대응하는 값(문자열)

예시

- 좌: `EQ Model` / 우: `KIYO_FXE` 또는 CSV 컬럼에서 추출된 값
- 좌: `EQP ID` / 우: `4EKFA417` (CSV에서 추출)
- 좌: `Time` / 우: `CSV t=... | wall=... | total=... | speed=...` 형태 가능

### 2.3 초기 프리셋(확정) — v1

요구사항으로 **초기 표시 행**을 아래처럼 고정 프리셋으로 시작한다.

1) **EQ MODEL**
- 좌(label): `EQ MODEL`
- 우(value): 사용자 **수동 입력 값** (초기값 예: `KIYO_FXE`)
- 비고: 추후 CSV 컬럼 연동/우선순위 정책 추가 가능(§3 참조)

2) **EQP ID**
- 좌(label): `EQP ID`
- 우(value): `{eqp_id}`
  - 선택된 CSV의 “현재 진행 중인 행(current row)”에서 `eqp_id` 컬럼 값을 추출
  - Play 전이라면 `eqp_start_tm`이 가장 빠른 행(최초 행)에서 추출

3) **Time**
- 좌(label): `Time`
- 우(value):
  - 기본(Play 전): `0s`
  - Play 중: **실시간**으로 아래 항목을 표시(포맷은 추후 확정)
    - 총 걸리는 시간(total)
    - 현재 진행시간(current)
    - 실경과시간(wall elapsed)

4) **Current State**
- 좌(label): `Current State`
- 우(value): 2단 구성(요약 1줄 + 상세 1~N줄)
  - 요약 예: `TAKQ012 (Buffer4 -> ATM pick)`
  - 추가로 `lot_id` 정보 포함
  - 상세: “현재 어떤 동작이 이루어지고 있는지” 설명 문구
    - 재생 타임라인(스케줄)에서 `lot_id`, `cassette_slot(웨이퍼 번호)`,
      `vtm 우측 EE -> 챔버5` 같은 정보를 활용해 문장 편집 가능해야 함

#### 2.3.1 표시 타이밍(확정) — v1

- 2D 상태 패널은 **앱 시작 시 기본으로 표시**되어야 한다(내용이 비어 있어도 표시).
- CSV 드롭다운에서 파일을 선택하면, Play 전에는 “시간상 최초 행” 기준으로 일부 항목(EQP ID 등)을 채운다.
- Play 중에는 “현재 진행중인 행/엔트리” 기준으로 값이 실시간 갱신된다.
- `Time`, `Current State`는 Play 전/초기화 시 기본값으로 둔다.
  - `Time`: `0s`
  - `Current State`: OFF 또는 빈 텍스트

#### 2.3.2 “현재 기준” 고정(확정) — v1

- `EQP ID({eqp_id})`: **dwell(체류 row) 기준**
  - Play 전: 최초 dwell 행(최소 `eqp_start_tm`) 기준
  - Play 중: 현재 CSV t가 포함되는 dwell 행( `start_sec <= t < end_sec` ) 기준
- `Current State`: **스케줄(녹색 강조: JSON 실행 중 행) 기준**
  - “녹색 강조”는 `simulation_play`의 타임라인 highlight(active_keys) 메커니즘을 따른다.

---

## 3) 값(value) 생성 요구사항 — “CSV 컬럼 기반 + 수동 입력” 동시 지원

### 3.1 EQ Model 같은 항목은 두 경로를 모두 지원해야 함

`eqp_model = KIYO_FXE`는

- CSV에 컬럼이 있을 수도 있고
- 없으면 사용자가 수동 입력해야 할 수도 있음

또한 상황에 따라 **컬럼값을 바꾸거나, 수동값을 바꾸거나**, 혹은 우선순위를 바꿀 수 있어야 한다.

### 3.2 value의 소스(source) 타입(정의)

각 행의 value는 아래 소스 중 하나 또는 혼합을 지원한다.

1) **CSV 컬럼 값**

- 특정 컬럼명(예: `eqp_id`)을 지정하면 해당 값을 표시

2) **수동 입력 값**

- 사용자가 텍스트 입력으로 값을 지정

3) **혼합(우선순위 정책 포함)**

- 예) “CSV에 컬럼이 있으면 CSV 사용, 없으면 수동값 사용”
- 예) “항상 수동값 강제” / “항상 CSV값 강제” 같은 모드도 가능

**모호한 부분(추후 확정 필요)**

- “컬럼값을 바꿀 수 있어야 한다”의 의미가
  - (a) 표시할 컬럼명을 UI에서 바꾸기(예: `eqp_id` 대신 다른 컬럼)
  - (b) CSV 데이터 자체를 수정/저장까지 하기
  - (c) 패널 표시용으로만 override(내부 값 덮어쓰기, CSV 파일은 그대로)
  - 중 무엇인지 확정 필요

---

## 4) “현재 진행 중인 행” 정의(Play 중 / Play 전)

### 4.1 CSV 기반 값 추출 규칙

요구사항에 따라, CSV 컬럼 기반 value를 가져올 때 기준은 다음을 목표로 함:

- **Play 중**: “현재 진행 중인 행(current row)”에서 값을 추출
- **Play 전**: 시간이 가장 빠른 행(=최초 행)의 값을 추출하여 표시

**모호한 부분(추후 확정 필요)**  
“현재 진행 중인 행(current row)”의 정의가 여러 가지가 될 수 있음

- **A안(스케줄 엔트리 기준)**: 타임라인에서 현재 실행 중인 1개 entry(transfer/pick/place 포함)
- **B안(dwell row 기준)**: 현재 CSV t가 포함되는 dwell 구간의 row(eqp_start_tm~eqp_end_tm)
- **C안(A,B 둘 다 제공)**하고 행마다 어떤 기준을 쓸지 선택

예측:

- `EQP ID`, `module_nm` 같은 값은 **B(dwell)**가 자연스러울 수 있고
- `Current State`는 **A(스케줄/이벤트)**가 자연스러울 가능성이 큼

### 4.2 시간 정렬 기준

- 기본은 `eqp_start_tm` 오름차순
- CSV에 `eqp_start_iso`가 있으면 이를 우선시할 수도 있음(현재 파서가 iso도 지원)

**모호한 부분(추후 확정 필요)**

- `eqp_start_tm` vs `eqp_start_iso` 우선순위 규칙 확정 필요

---

## 5) Time 표시 요구사항(Time Row)

`Time` 행은 단순 컬럼이 아니라 **계산/조합된 값**을 표시할 수 있어야 한다.

표시 후보(예)

- 현재 CSV 진행 시간(재생 기준 t)
- 실경과(wall elapsed)
- 총 시간(total duration / end time)
- 배속(speed scale)
- 상태(playing/paused/stopped)

**모호한 부분(추후 확정 필요)**

- 최소 표시 집합: “진행시간/실경과”만인지, total/speed/state까지 포함할지

---

## 6) Current State 요구사항(상태 요약 + 상세)

CSV 재생 중 현재 어떤 동작이 이루어지고 있는지 표시:

- **Current State** 1줄 요약:
  - 예: `TAKQ012 (Buffer4 -> ATM pick)` 같은 형태
  - `lot_id` 포함
- **그 아래 상세 설명(1~N줄)**:
  - 현재 무슨 동작인지 설명(이송/픽/플레이스/공정 대기 등)
  - 타임라인에서 확보 가능한 정보(웨이퍼 번호, 손(EE), 챔버, from→to)로 편집 가능해야 함

데이터 소스 후보(현재 구조 기준)

- `simulation_play`에서 생성하는 “타임라인/스케줄 엔트리”의 `title_ko`, `meaning_ko`, `exec_ko`, `event_name` 등
- `lot_id`, `cassette_slot(=wafer 번호)`은 스케줄 텍스트에도 포함되어 있음

### 6.0 Current State 식별자(확정) — v1

- `TAKQ012` 등 “상태 식별자”로 보이는 값은, **우선 CSV 컬럼 `lot_id` 값을 사용**한다.
- 단, 컬럼명이 바뀌거나(예: `lot_id` → `job_id`) 다른 식별자를 쓰고 싶을 수 있으므로,
  “식별자 컬럼명”은 고정 상수가 아니라 **설정/매핑으로 교체 가능한 구조**를 목표로 한다.

### 6.1 구현 가능 여부(현재 구조 기준) 및 작업 방향 — v1

**가능 여부**

- 결론: **가능**. 현재 `simulation_play`는 CSV를 파싱해 “타임라인/스케줄 엔트리”를 만들고,
  각 엔트리에 사람이 읽을 수 있는 한국어 문구(`title_ko`, `meaning_ko`, `exec_ko`)와
  `event_name`, `json_path` 등의 메타를 이미 포함한다.
- 또한 “공정만보기 HUD”처럼 뷰포트 위에 2D UI를 얹는 패턴이 이미 존재하여(예: `lam_csv_viewport_hud.py`),
  동일 방식으로 “상태 패널”을 추가하는 것이 구조적으로 자연스럽다.

**작업 방향(문서 단계)**

- 1) **상태 스냅샷(Playback Status Snapshot)**을 1회에 모아주는 함수/구조를 정의한다.
  - 입력: 선택된 CSV, 현재 재생 상태, 현재 시각, 현재 스케줄 엔트리/현재 dwell 행
  - 출력: `eqp_id`, `lot_id`, `cassette_slot`, 시간 정보, current state 요약/상세 등 패널 표시용 데이터
- 2) **Current State 텍스트는 스케줄 엔트리를 기반으로 생성**한다.
  - 1줄 요약: 스케줄 엔트리의 `title_ko`를 기본으로 사용하고, 필요 시 규칙으로 편집
  - 상세: `exec_ko`, `meaning_ko`, `event_name` 및 slot_key/hand(가능하면) 정보를 조합
- 3) 패널의 각 행은 “label + value resolver”로 관리해 확장성을 확보한다(§8.2 참조).

**모호한 부분(추후 확정 필요)**

- `TAKQ012`의 의미(=lot_id인지, 다른 식별자인지)
- “Buffer4 -> ATM pick” 같은 문장을 어떤 규칙으로 생성할지
- “현재 진행 중인 행(current row)” 기준을 스케줄(A)로 할지 dwell(B)로 할지(§4.1)

### 6.2 타임라인에서 `lot_id` 표시 여부(확인 결과)

현재 `simulation_play`의 타임라인(스케줄 엔트리) 텍스트 생성은 **`lot_id`를 이미 포함**한다.

- `transfer` 엔트리 예:
  - `title_ko`에 `lot={curr.lot_id!r}` 포함
  - 예: `[재생] 이송(...) · lot='...' · 웨이퍼#... · ... → ...`
- `pick/place/aligner` 엔트리도 `title_ko`에 `lot={lot_id!r}` 형태로 포함

즉, “Current State에서 lot_id를 활용”하는 것은 **현재 구조와 일치**한다.

**모호한 부분(추후 확정 필요)**

- `TAKQ012`가 정확히 무엇인지(=lot_id? 다른 식별자?)
- “Buffer4 -> ATM pick” 같은 표현을 어떤 규칙으로 만들지(카테고리/이벤트명/slot_key를 어떻게 문장화할지)

---

## 7) 확장/유지보수 요구사항(중요)

향후 요구:

- CSV 내부의 다른 정보를 더 표시
- 표시 문구를 더 편집/가공
- 표시 항목을 추가/삭제/순서 변경

이를 위해 패널은 다음을 만족해야 함:

- 값 생성 로직이 여기저기 흩어지지 않고,
- “스냅샷 생성(데이터 수집)”과 “표시/포맷팅(UI 렌더링)”이 분리되어,
- 새로운 row 추가가 쉬운 구조

---

## 8) 구현 관점(현재 구조와 연결) — 설계 제안(문서 단계)

이 섹션은 “현재 구조에 맞춰 어떻게 설계하면 좋겠다”는 제안이며, 추후 확정 후 코드화한다.

### 8.1 Playback Status Snapshot 단일 진입점

패널이 필요로 하는 값은 매 프레임/주기마다 다음과 같은 **스냅샷 함수**에서 가져오는 구조를 권장:

- CSV 선택 상태(파일명/경로)
- 재생 상태(playing/paused/stopped)
- 시간들(CSV t, wall elapsed, total)
- “현재 row/entry” (dwell 기준/스케줄 기준 중 확정된 기준)
- 현재 lot/wafer/모듈(from/to/hand) 정보

### 8.2 Row 정의는 “label + value_spec(규칙)”로 관리

각 행을 데이터로 정의(확장 용이):

- label: `EQP ID`
- value_spec:
  - type: csv_column/manual/computed/template
  - source_policy: when_playing/current_row vs when_idle/earliest_row
  - fallback/override 정책(컬럼 없음 → 수동값 등)

### 8.3 수동 입력 UI 위치(정책 필요)

수동 입력/편집을 어디서 할지 정책이 필요:

- (a) 뷰포트 패널 자체에 입력 UI 포함
- (b) 기존 CSV Play 창에 설정 섹션 추가
- (c) 표시는 패널, 편집은 창

### 8.4 설정(경로/오프셋/컬럼 매핑) 중앙화 — v1 방향

사용자 요구에 따라, “설정 파일”은 JSON/YAML 같은 외부 포맷이 아니라 **별도 `py` 모듈**로 둔다.

목표:

- 기능 #1(2D 상태 패널), 기능 #2(FOUP 진행상황 3D 패널), 기능 #3(기기정보보기 3D 라벨) 모두에서
  - prim 경로
  - 표시 label/name
  - X/Y/Z 오프셋(객체 중심 기준)
  - 스타일(색/크기 등)
  - CSV 컬럼명 매핑(`lot_id` 같은 식별자 컬럼명 등)
  을 **한 곳에서 수정/추가**할 수 있는 구조를 지향한다.

제안(문서 단계):

- `morph/lam_control/lam_viewport_overlay_config.py` (가칭) 같은 파일 1개를 만들고,
  - FOUP 앵커 prim 경로(FOUP1~3)
  - “기기정보보기” 슬롯 라벨 목록(prim_path + name + offset + style)
  - 상태 패널의 컬럼명 매핑(예: lot_id 컬럼명)
  - 기본값(예: EQ MODEL 기본 수동값)
  을 정의/관리한다.

결정(확정):

- 초기 구현은 **설정 `py` 파일 1개에 모두 모아서 시작**한다.
- 향후 규모가 커지면 기능별 파일로 분리할 수 있으나, v1에서는 단일 파일을 SSOT로 사용한다.

> 메모: 이 방식은 “코드 수정 없이 값을 바꾸기”는 불가능하지만,
> 사용자가 원한 “py 파일에서 빠르게 경로/오프셋/항목을 편집하며 맞추기” 워크플로에는 최적화된다.

---

## 9) 결정이 필요한 질문 목록(추후 Q&A로 확정)

1) “현재 진행 중인 행” 기준: 스케줄 엔트리(A) vs dwell row(B) vs 둘 다(C)
2) 시간 우선순위: `eqp_start_iso`가 있으면 우선인지, 항상 `eqp_start_tm`인지
3) 수동 입력 UI 위치: 패널 내/기존 창/분리
4) row 구성: 고정 프리셋 vs 사용자 편집(추가/삭제/정렬/저장)까지 지원할지
5) CSV “컬럼값을 바꿀 수 있어야”의 범위: 표시용 override인지, CSV 파일 수정까지인지
6) `TAKQ012` 의미 정의 + Current State 문구 생성 규칙 확정

---

## 10) 다음 단계(문서 v2로 업데이트할 준비)

추가 설명을 받으면, 위 **모호한 부분/질문**을 하나씩 확정해가며

- “확정된 규칙”
- “해야 할 일 체크리스트(구현 단계)”

로 문서를 업데이트한다.

