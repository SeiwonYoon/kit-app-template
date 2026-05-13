# LAM 장비 모델 (Equipment Spec) — Wafer 가시성 매핑 기초

> 본 문서는 **LAM 시뮬레이션을 “조합 애니메이션”으로 자동화** 하기 위한 _장비 구조·동작 모델_ 의
> 기초 자료다. 행 스케줄 / 시퀀스 step 자체의 설계는 `LAM_Spec.md` 에 있고, 본 문서는
> 그 위에서 **VTM / ATM 같은 실제 장비가 어떻게 구성되어 있고**, **wafer 가 어디에서 어디로
> 옮겨질 때 어느 prim 을 보이게/숨기게 해야 하는지** 를 정의한다.
>
> 본 문서는 “시작 자료”이며 장비 추가/규칙 추가가 있을 때마다 이 문서에 누적 갱신한다.
> 향후 자동화 매핑(시뮬 결과 JSON → 가시성 + 로봇팔 이동/회전 step) 의 1차 입력 사양이 된다.
>
> 마지막 갱신: 2026-05-14 — VTM 1차 정의 + TBS `port_lot_visibility` 와의 차이 정리.

---

## 1. 핵심 컨셉 — TBS `port_lot_visibility` 와의 차이

### TBS (참고용)
- 모듈: `morph.tbs_control_1/.../port_lot_visibility.py`
- 설정 파일: `morph.tbs_control_1/config/port_lot_prim_paths.json`
  - 형식: `{ "PORT_ID": "<LOT prim 절대경로>" }`  (예: `INOUT`, `BP1~BP4`, `EP1~EP3`)
- 동작 모델: **포트별 LOT 점유 (`ports_occupancy[PORT_ID] == lot_id` / 빈 문자열)** 만 보고
  매핑된 prim 의 `MakeVisible / MakeInvisible` 를 켜고 끈다.
- 상태: 시뮬 이벤트마다 `apply_port_lot_prim_visibility(ports_occupancy)` 한 번에 일괄 동기화.
  → **각 포트가 서로 독립**. 어디서 어디로 이동했는지(transfer) 는 추적하지 않는다.
- 추가 보조 상태: FOUP `_PROCESS_START/END` 의 +Y 320 plateau, baseline authoring 캐시,
  `material` 바인딩(processing / done / default) 동기화 — **LOT 가시성과 별개의 안전망**.

### LAM (본 장비)
- LAM 에서 다루는 것은 **포트 점유의 스냅샷** 이 아니라 **wafer 의 이동 이벤트**.
  즉 `WAFER_PICK(from=EP2, by=ROBOT_LEFT)` 같은 사건이 시간순으로 들어온다.
- 따라서 단순 “포트별 LOT id 매핑” 이 아니라, **“slot 단위 wafer prim 보유 여부”** 를 상태로
  들고, 매 이벤트마다 **한 slot 은 숨김 / 다른 slot 은 표시** 로 동기화한다.
- **slot** = wafer 가 한 번에 1개 놓일 수 있는 위치. VTM 의 chamber 5 개, airlock 2 개,
  로봇팔 양쪽 손(left/right) 모두 slot 으로 본다.
- 각 slot 에는 그 slot 에 wafer 가 “있을 때 보여야 할” **wafer prim 절대 경로** 가 미리
  매핑되어 있다 (TBS 의 PORT→prim 매핑과 동일한 발상).

### 형식 비교

| 항목 | TBS (`port_lot_visibility`) | LAM (본 문서의 모델) |
|---|---|---|
| 매핑 단위 | 포트 ID → LOT prim 1개 | slot ID → wafer prim 1개 |
| 상태 입력 | `ports_occupancy: {port: lot_id}` 스냅샷 | wafer 이동 이벤트 (`pick`/`place`/`swap`) 스트림 |
| 갱신 방식 | 스냅샷 한 번에 전부 동기화 | 이벤트마다 _(from slot 숨김, to slot 표시)_ 1쌍 적용 |
| 자세 복원 | baseline authoring 저장 + 복원 + FOUP plateau | (예정) 시뮬 시작 시 모든 slot 의 wafer 초기 가시성 캐시 |
| 보조 효과 | material 자동 바인딩 (processing/done/default) | 추후 chamber 처리 상태 색상에서 동일 패턴 차용 가능 |

**핵심**: LAM 도 _“slot 별 prim 경로 사전 매핑” + “시뮬 이벤트 발생 시 가시성 동기화”_ 라는 큰
구조는 TBS 와 같다. 다른 점은 _입력이 스냅샷이 아니라 transfer 이벤트_ 이고, _slot 의 한쪽
끝이 chamber 처럼 정지된 위치가 아니라 회전·이동하는 로봇팔_ 이라는 것뿐이다.

---

## 2. 장비 분류 (현재 사양)

LAM 에서 다룰 장비는 단일 종이 아니다. 같은 wafer 가 두 장비를 넘나든다.

```
┌──────────────────────────────────────────────────────────┐
│  ATM (예정 — 추후 정의)                                  │
│    ├ 로봇팔 #ATM                                         │
│    └ ...                                                 │
│                                                          │
│    ↕ AIRLOCK1 / AIRLOCK2 두 통로로 VTM 과 wafer 주고받음 │
│                                                          │
│  VTM (1차 정의 완료, §3)                                 │
│    ├ Chamber EP1~EP5 (5)                                 │
│    ├ Airlock AL1, AL2  ← ATM 과 공유                     │
│    └ 로봇팔 RBT (좌/우 hand, 좌우 슬라이드 + 회전)       │
└──────────────────────────────────────────────────────────┘
```

| 장비 ID (잠정) | 설명 | 상태 |
|---|---|---|
| `VTM` | Vacuum Transfer Module. 진공 측. 5 chamber + 2 airlock + 양손 로봇팔 1 대. | **§3 정의 완료** |
| `ATM` | Atmosphere Transfer Module. 대기 측. 자체 로봇팔 1 대 + (FOUP/IO?) — **AIRLOCK1, AIRLOCK2** 두 통로로 VTM 과 wafer 교환. | **추후 정의** |

> ATM 정의 추가 시 §4 를 새로 채워 넣고, 두 장비를 잇는 _공유 slot_ 인 `AIRLOCK1/2` 의 양면
> 매핑 규칙을 §5 에 합쳐 둔다.

---

## 3. VTM (Vacuum Transfer Module) — 1차 정의

### 3.1 구성 요소

```
[VTM 평면도 (개념)]

       AIRLOCK1   AIRLOCK2     ← ATM 측 통로 (공유)
           │         │
   ┌───────┴─────────┴───────┐
   │                         │
   │         ROBOT           │  EP1
   │      (회전 중심)        │
   │       ┌──┴──┐           │  EP2
   │       L     R           │
   │       └──┬──┘           │  EP3
   │  ←slide── │ ──slide→    │
   │           │             │  EP4
   │                         │
   └─────────────────────────┘  EP5
```

- **Chamber 5 개** — 임시 ID `EP1, EP2, EP3, EP4, EP5`
  - 각 chamber 는 wafer 1 매 보유 slot (= prim 경로 1 개 매핑).
- **Airlock 2 개** — 임시 ID `AIRLOCK1, AIRLOCK2`
  - **VTM ↔ ATM 공유 slot**. 양측에서 모두 pick/place 가능.
- **Robot 1 대** — 임시 ID `RBT`
  - 양쪽 _hand_ 두 개: `HAND_L`, `HAND_R` (각각 wafer 1 매 보유 slot).
  - **이동(slide)** : 좌/우 가로 이동.
  - **회전** : 중심축 기준 회전 (chamber 5 + airlock 2 = 총 7 개 위치 중 하나로 정렬).
  - _허수아비 자세_ 로 양손이 항상 펴진 상태. 손이 도달하는 위치는 정렬 회전각 + slide 위치로 결정.

### 3.2 Slot ID — wafer prim 매핑 (형식)

향후 도입할 설정 파일(잠정 이름 `lam/config/vtm_wafer_prim_paths.json`) 의 스키마.
값이 빈 문자열이면 “이 slot 은 가시성 자동 제어 없음”.

```json
{
  "_comment": "VTM slot ID → 해당 slot 에 wafer 가 있을 때 보여야 할 USD prim 절대 경로. wafer 없으면 숨김, 있으면 표시. Robot hand 2 개도 slot 으로 취급.",
  "EP1":      "/Root/VTM/Chamber_EP1/Wafer",
  "EP2":      "/Root/VTM/Chamber_EP2/Wafer",
  "EP3":      "/Root/VTM/Chamber_EP3/Wafer",
  "EP4":      "/Root/VTM/Chamber_EP4/Wafer",
  "EP5":      "/Root/VTM/Chamber_EP5/Wafer",
  "AIRLOCK1": "/Root/VTM/Airlock1/Wafer",
  "AIRLOCK2": "/Root/VTM/Airlock2/Wafer",
  "HAND_L":   "/Root/VTM/Robot/HandL/Wafer",
  "HAND_R":   "/Root/VTM/Robot/HandR/Wafer"
}
```

> 실제 prim 경로는 USD asset 에 맞춰 채워야 한다. 위 값은 _자리표시_ 다. ATM 정의가 들어오면
> ATM 의 robot hand 와 (혹시 있을) FOUP/IO slot 도 같은 사전에 추가하거나, 장비별 사전을 분리한다.

### 3.3 초기 상태 (시뮬 시작 시)

- 모든 slot 에 wafer 가 “있다/없다” 가 정해진다. 두 가지 후보 입력:
  - (a) 시뮬 결과 JSON 의 첫 이벤트 직전 “initial occupancy” 스냅샷.
  - (b) 별도 초기 상태 파일 (e.g. `lam/config/vtm_initial_wafers.json`).
- 시뮬 첫 step 실행 직전에 모든 slot 의 wafer prim 을 그 초기 점유에 맞춰 _MakeVisible /
  MakeInvisible_ 일괄 적용. (TBS 의 `apply_port_lot_prim_visibility` 와 동일 위치.)

### 3.4 Wafer 이동 이벤트 → 가시성/애니메이션 동기화 규칙

LAM 의 시뮬 결과 JSON 은 시간 순서대로 이런 이벤트를 흘려준다 (이름은 잠정):

| 이벤트 | 의미 | 가시성 효과 | 로봇팔 효과 |
|---|---|---|---|
| `WAFER_PICK` (from_slot, hand) | hand 가 from_slot 의 wafer 를 집는다. | `from_slot` wafer prim **숨김**, `hand` wafer prim **표시** | 로봇이 from_slot 정렬 위치로 회전·slide → 손 진입 |
| `WAFER_PLACE` (to_slot, hand) | hand 가 wafer 를 to_slot 에 놓는다. | `hand` wafer prim **숨김**, `to_slot` wafer prim **표시** | 로봇이 to_slot 정렬 위치로 회전·slide → 손 진입 후 후퇴 |
| `WAFER_SWAP` (slot, hand_left, hand_right) | 한쪽 손이 slot 에서 빼고, 다른 손이 같은 slot 에 다른 wafer 를 넣는 동시 동작. | (slot wafer 숨김 → 새 hand wafer 표시) 의 2-페어 처리 | 양쪽 손 이동/회전 시퀀스 묶음 |

**가시성 규칙은 “slot 1 개당 prim 1 개” 이므로 TBS 와 동일한 단순 분기**:
- slot occupancy 에 wafer 가 채워지면 `MakeVisible(slot.prim_path)`.
- 비워지면 `MakeInvisible(slot.prim_path)`.

이는 `apply_port_lot_prim_visibility` 와 정확히 동일한 형태이므로, LAM 에선 비슷한 모듈
`lam_wafer_visibility.py` (예정) 가 “slot → prim 사전” 을 읽고 동일한 패턴을 적용하면 된다.

### 3.5 로봇팔 7-위치 모델

로봇은 _회전각 + slide 위치_ 의 7 개 도달 가능 지점 (5 chamber + 2 airlock) 중 하나로 정렬한다.

| 위치 ID | 정렬 회전각 (자리표시 — USD asset 에 맞춰 채움) | slide 위치 (자리표시) |
|---|---|---|
| `EP1` | `+72°` | `+200mm` |
| `EP2` | `+36°` | `+200mm` |
| `EP3` |   `0°` | `+200mm` |
| `EP4` | `-36°` | `+200mm` |
| `EP5` | `-72°` | `+200mm` |
| `AIRLOCK1` | `+144°` | `+220mm` |
| `AIRLOCK2` | `-144°` | `+220mm` |

> **위 값은 모두 자리표시** 다. 실제 USD/시뮬 데이터로 채워야 한다. LAM_Sequence_Engine 의
> `MOVE`/`ROTATE` step 이 동일 prim (로봇 베이스 + 양 손) 에 누적 적용되므로, 이벤트 → step
> 자동화 매핑에서 “해당 위치로의 회전·slide step 을 사전에 등록” 하는 식으로 표현한다.

### 3.6 안전망 / 일관성 규칙 (TBS 에서 차용)

- **baseline authoring 캐시** : 시뮬 시작 직전에 모든 robot/wafer prim 의 현재 translate/rotate 를
  저장. Stop/Reset 시 그 자세로 일괄 복원 (TBS `_PORT_LOT_AUTHORING` 와 동일 패턴).
- **진행 중 보호 집합** : pick/place 도중인 wafer 는 baseline 복원에서 제외 (TBS
  `_FOUP_IN_PROGRESS_PATHS` 와 동일 발상).
- **material 동기화 (선택)** : chamber 가 처리 중 / 처리 완료 / idle 등의 상태를 따로 받게 되면,
  TBS 의 `MATERIAL_PATH_FOUP_*` 와 같이 “slot 별 material override 사전” 으로 확장.

---

## 4. ATM (Atmosphere Transfer Module) — 추후 정의

- VTM 과는 **AIRLOCK1, AIRLOCK2** 두 통로를 공유한다.
- 자체 로봇팔 1 대 (구조는 VTM 과 비슷할 가능성이 큼) + FOUP/IO 슬롯들 (정의 필요).
- 슬롯 매핑은 같은 형식의 사전 (`atm_wafer_prim_paths.json` 잠정) 으로 추가한다.
- 두 장비의 _공유 slot_ `AIRLOCK1/2` 는 가시성 사전에서 한 번만 정의되고, VTM 측·ATM 측이 같은
  prim 을 본다. (둘 중 한 쪽이 wafer 를 “들고 있을 때” 만 그 prim 이 보이는 일관성을 유지.)

> 사용자께서 ATM 동작·치수·로봇팔 자유도 등을 추가로 설명해 주시면 §4 를 채워 넣겠습니다.

---

## 5. 자동화 매핑(다음 단계) — 시뮬 JSON → LAM 시퀀스

장기 목표는 “시뮬 결과 JSON 1 개를 던지면 LAM 이 알아서 가시성 + 로봇팔 동작 step 을 합성해
실시간 재생” 이다. 본 문서는 그 매핑의 1차 입력이다.

대략적인 파이프라인 (앞으로 본 문서가 1차 입력 사양 역할):

```
시뮬 JSON
  └→ 이벤트 분류 (WAFER_PICK / PLACE / SWAP / CHAMBER_PROCESS_…)
        ├→ slot.occupancy 갱신 → lam_wafer_visibility.apply()
        └→ 이벤트별 step 생성기
              ├ ROTATE/MOVE (로봇 정렬 step)
              ├ TIMESAMPLES_REPLAY (asset 자체 손 진입/후퇴 애니)
              └ DELAY (실시간 보정)
LAM Sequence Engine.run(steps)
```

위 파이프라인의 _각 변환기_ 는 별도 모듈(예정: `lam_event_to_steps.py`) 로 들어가며,
**slot ↔ prim 사전** + **위치별 회전/slide 자리표시 값** + **이벤트별 step 생성 규칙** 의
세 가지 사전 파일을 본 문서가 정의한다.

---

## 6. 변경 이력

| 날짜 | 변경 |
|---|---|
| 2026-05-14 | 최초 작성. VTM 1차 정의(§3) + TBS `port_lot_visibility` 와의 비교(§1) + ATM/매핑 자리표시(§4, §5). |
