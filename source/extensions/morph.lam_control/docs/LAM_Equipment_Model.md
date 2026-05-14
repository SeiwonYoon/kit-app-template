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
> **부록(구현 동기화): 2026-05-14** — 시뮬 CSV/매크로 재생은 `simulation_play.py` 의 **물리 `slot_key`**
> 및 `LamSimPlayVirtualConfig` 가 SSOT 이다. 본 문서 §3.2 의 `lam/config/*.json` 은 **장기 스키마
> 안내** 로 남기고, 당장 현장 입력은 `LAM_Simulation_Play_User_Config.md` 를 따른다.

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
| `ATM` | Atmosphere Transfer Module. 대기 측. 자체 로봇팔 1 대 + FOUP/버퍼/쿨링/에어록(대기측) 등 — **AIRLOCK1, AIRLOCK2** 두 통로로 VTM 과 wafer 교환. | **§4 문장 스펙은 미완** — 다만 시뮬용 **클립·슬롯 키·가시성** 은 이미 `simulation_play.py` (`atm_clip_by_slot_key`, `foup*`, `buffer*`, `cooling*`, `airlock*`, `aligner` …) 에 반영됨 |

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

#### 설정 파일 위치 — 현재 결정 + 이주 옵션

**현재 결정 (A 안 유지, 2026-05-14)** : 본 사전 파일은
**repo 루트의 `lam/config/` 아래** 둔다 (예: `lam/config/vtm_wafer_prim_paths.json`).
이유는 다음과 같다.

- LAM 의 데이터 자산 (`lam/usd/`, `lam/lam_event_sequences/`, `lam/lam_external_results/`) 과
  같은 레이어에서 관리되어, 매핑이 “자산/시뮬 결과와 함께 변경되는 데이터” 라는 성격을 그대로 반영한다.
- 코드의 `_find_lam_data_root()` 가 이미 `lam/` 폴더를 자동 탐지하므로 별도 경로 설정 없이
  `os.path.join(_find_lam_data_root(), "config", "vtm_wafer_prim_paths.json")` 한 줄로 읽힌다.
- 사용자별/사이트별 prim 경로가 자주 바뀌어도 확장 패키지를 재배포할 필요가 없다.

**향후 이주 옵션 (B 안, 보류)** : 필요해지면 TBS 와 동일한 위치인
`source/extensions/morph.lam_control/config/vtm_wafer_prim_paths.json` 로 옮긴다.
TBS 가 `port_lot_prim_paths.json` 을 이 자리에 두고 있으므로, “기본값을 확장 패키지에 함께
배포해야 한다 / 사이트별 override 가 더 이상 필요 없다” 는 판단이 서면 이주가 자연스럽다.

이주가 발생할 경우 따라야 할 단계:

1. **로더 이중화** — 코드의 사전 로더가 `(1) <ext>/config/vtm_wafer_prim_paths.json` → `(2) lam/config/vtm_wafer_prim_paths.json` 순서로 찾도록 변경 (한 릴리스 동안 둘 다 지원).
2. **본 문서 §3.2 의 경로 표기**를 B 안으로 갱신하고, §6 변경 이력에 이주 날짜 기록.
3. 한 릴리스 뒤 `lam/config/` 폴백 제거.

> 동일 정책이 ATM 사전(`atm_wafer_prim_paths.json` 잠정) 및 §3.5 의 _로봇팔 7-위치 사전_
> (잠정 이름 `vtm_robot_positions.json`) 에도 그대로 적용된다.

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

## 4. ATM (Atmosphere Transfer Module) — 문서 스펙 vs 코드

**문서 관점(여전히 채울 것):** ATM 로봇 자유도·치수·IO 상세·공유 에어록 prim 단일화 규칙을 §4 에
풀어 쓸 여지가 있다.

**코드 관점(이미 있는 것):** 시뮬 파이프라인에서는 ATM 이 **물리 `slot_key`** 단위로 이미 모델링된다.

- CSV `module_nm` → `slot_key` : `simulation_play.build_default_module_nm_to_slot_key()` (본 저장소의 `MODULE_NM_TO_SLOT_KEY` 와 동기).
- ATM **in/out 프레임(SSOT)** : `LamSimPlayVirtualConfig.atm_clip_by_slot_key` (`LamAtmStationClips`).
- 웨이퍼 prim : `wafer_tmpl_*`, `wafer_prim_atm_arm`, `wafer_prim_aligner` 등 — 전부 **`simulation_play.py`**
  의 `default_lam_sim_virtual_config()` 한곳에서 수정하는 정책(`LAM_Simulation_Play_User_Config.md`).

장기적으로 §3.2 처럼 `lam/config/atm_wafer_prim_paths.json` 으로 빼도 되지만, **현재 단일 SSOT 는 코드**다.

- VTM 과는 **AIRLOCK1, AIRLOCK2** 두 통로를 공유한다는 전제는 유지.
- 두 장비의 _공유 slot_ `AIRLOCK1/2` 는 가시성 사전에서 한 번만 정의되고, VTM 측·ATM 측이 같은
  prim 을 본다는 **목표** — USD 쪽 prim 배치가 맞는지는 현장에서 확인 필요.

> ATM 동작·치수·로봇팔 자유도 등 **서술형 스펙** 을 더 넣어 주시면 본 §4 상단 문단을 확장한다.

---

## 5. 자동화 매핑 — 시뮬 입력 → LAM 시퀀스

**이미 구현된 경로 (CSV dwell):** `simulation_play.run_simulation_from_csv` 가 dwell 타임라인을 읽고,
인접 dwell 간 이송을 `build_steps_for_dwell_transfer` → `atm_arm_to_atm_slot` / `vtm_arm_move_to_chamber`
로 스텝화한 뒤 `LamSequenceRunner.run` 으로 재생한다. 상세·테스트 절차는
`LAM_Simulation_Play_Field_Test_Guide.md` 참고.

### 5.1 이야기로 보는 웨이퍼 한 매 (샘플 흐름)

아래는 **개념 설명용 샘플**이다. 이름·순서는 데모 CSV(`wafer01_tour_v1` 류)와 `simulation_play.py` 의
`module_nm` → `slot_key` 매핑을 염두에 두었고, 실장비 CSV·USD 경로는 현장 값으로 바뀐다.

---

**등장인물**

- **웨이퍼 #1** — 같은 랏에서 추적하는 한 매. 시뮬 CSV 에서는 보통 `cassette_id=1` 로 고정되어
  “이 웨이퍼의 여행”만 시간순으로 읽는다.
- **슬롯(slot)** — 웨이퍼가 “지금 이 자리에 있다”고 말할 수 있는 최소 단위. 코드에서는 문자열
  `slot_key` 로 부른다. 예: `foup1_1`(FOUP1 의 1번 슬롯), `buffer3_2`, `airlock1_1`,
  `chamber3`, 그리고 로봇 손끝만을 위한 논리 슬롯 `LOGICAL:ATM_ARM`, `LOGICAL:VTM_EE_L` 등.
- **각 슬롯의 웨이퍼 prim** — 그 자리에 웨이퍼가 **보여야 할 때만** 켜 두는 USD 메시(또는 대체 prim).
  슬롯마다 경로 하나가 미리 매핑되어 있다. 로봇이 집어 오면 “슬롯에서는 끄고, 손에서는 켠다” 식으로
  가시성이 바뀐다.
- **ATM** — 대기 측 로봇. FOUP·버퍼·쿨링·(대기측) 에어록 슬롯에 손을 넣어 집고·내려놓는다.
  팔 애니는 **인스턴스 timeSamples** 로 재생하고, 높이 맞춤은 별도 prim 의 **Z MOVE** 로 한다.
- **VTM** — 진공 측 로봇. 챔버·(진공측) 에어록 슬롯을 좌·우 손으로 다룬다. 회전·Z·애니·가시성이
  한 덩어리로 묶여 시퀀스 스텝에 올라간다.

---

**아침: FOUP 에서 꺼내기**

웨이퍼 #1은 처음에 **FOUP1 의 1번 슬롯** `foup1_1` 에 얹혀 있다. CSV 한 줄이 “지금부터 잠시 동안
ATM 팔 끝에 머문다”고 적혀 있으면, 내부적으로는 `LOGICAL:ATM_ARM` 슬롯에 dwell 이 잡힌다.
그다음 줄이 “버퍼 3 의 2번에 머문다”면 `buffer3_2` — 이렇게 **한 줄한 줄이 곧 ‘어디에 머물렀는지’의 기록**이다.

시뮬이 이 **연속된 두 줄**을 읽을 때, “앞선 dwell 에서 다음 dwell 로 웨이퍼가 옮겨졌다”는 **이송 한 번**으로 본다.
ATM 팔이 관여하는 전형적인 패턴이면, 코드는 “팔이 비어 있는 상태에서 FOUP 슬롯으로 가서 집어 온다”는
식으로 클립·Z·가시성 스텝을 조합한다. 이때 **집기(pick)** 구간의 timeSamples in/out 과,
슬롯 웨이퍼 prim 을 끄고 팔 끝 웨이퍼 prim 을 켜는 순서가 맞물리면, 눈으로는 “슬롯에서 사라져
팔에 붙는” 것처럼 보인다.

---

**낮: 대기측을 가로지르기**

웨이퍼 #1이 ATM 팔에 붙은 채로 CSV 가 “쿨링 3번”, “다시 ATM 팔”, “에어록 1-1번 슬롯”처럼 이어지면,
매번 **이전 슬롯 → 다음 슬롯** 이송이 쌓인다. 버퍼에 **내려놓기(place)** 할 때는 반대로,
팔 끝 prim 을 끄고 해당 슬롯 prim 을 켠다. 높이가 다른 슬롯이면 HeightStage 같은 prim 이
먼저 Z 로 내려갔다 올라가고, 그 사이 timeSamples 가 “손이 들어갔다 나온다”를 연출한다.

여기까지는 모두 **ATM 쪽 규칙**으로 분류되는 경우가 많다. 즉 “지금 움직이는 연출 주체가 대기 로봇이다”
라고 시뮬이 판단하는 쪽에 가깝다.

---

**경계: 에어록을 건너면**

`airlock1_1` 처럼 **에어록 슬롯**은 ATM 과 VTM 이 **같은 물리 자리**를 공유한다는 상정을 둔다.
CSV 가 “에어록에 있다”가 끝나고 “이제 진공 측 트랜스퍼 챔버의 손(예: `LOGICAL:VTM_EE_L`)에 있다”로
바뀌면, 이 한 번의 이송은 **VTM 쪽 클립·Yaw·Z** 로 묶일 수 있다. 즉 같은 웨이퍼 #1 이지만,
**어느 로봇이 ‘주인’인지**가 바뀌는 구간이다.

---

**오후: 챔버에서 공정을 보고**

`chamber3` 에 dwell 이 잡혀 있으면 VTM 이 그 앞으로 회전하고, 손 높이를 맞춘 뒤,
timeSamples 로 in/out 을 밟는다. 공정 시간이 길면 CSV 상 dwell 구간이 길어지고,
그동안 재생기는 “이번 이송 블록”이 아니라 **그 자리에 머문 시간**만큼 타임라인이 흐른다.
( dwell **내부**에서 챔버 램프·가스 등 추가 애니를 넣는 것은 향후 확장 여지다. )
다른 챔버나 에어록으로 이어지면 또 한 번씩 **이송 묶음**이 붙는다.

---

**저녁: 돌아오거나 다음 랏으로**

투어 CSV 가 다시 FOUP 슬롯이나 다른 위치로 이어지면 같은 규칙으로 **연속 dwell = 이송**이 반복된다.
`cassette_id` 가 바뀌는 행이 끼면, 지금 시뮬 코드는 “다른 웨이퍼의 이야기”로 보고 **그 사이 이송
스텝은 생략**할 수 있다 — 한 랏만 따라갈 때의 안전장치에 가깝다.

---

**한 줄로 요약**

> **CSV 한 줄 = 웨이퍼가 그 장비 슬롯에 머문 기록(dwell)** 이고,
> **연속된 두 줄 = 그 사이에 한 번의 이송**이 있었다고 가정해 ATM 또는 VTM 매크로와 같은 스텝 꾸러미로 풀어 재생한다.
> **각 슬롯·손끝에는 웨이퍼 prim 하나**가 매핑되어 있고, 집기·내려놓기 때마다 **누가 켜지고 꺼지는지**가
> 가시성으로 맞춰진다.

(구현 세부·입력 필드 위치는 `LAM_Simulation_Play_User_Config.md` 와 `simulation_play.py` 를 본다.)

### 5.2 CSV 샘플 데이터와 파싱·애니 동작 원리

저장소에 포함된 **`lam/csv/wafer01_tour_v1.csv`** 는 “실생산 CSV 와 **열 이름·형식만** 같다”는 가정의 **데모 투어**다.
한 웨이퍼(`cassette_id=1`)가 시간축을 따라 여러 `module_nm` 에 **머문 구간**을 적어 두었다.

#### 5.2.1 열이 의미하는 것 (샘플 기준)

| 열 | 샘플에서의 역할 |
|----|-----------------|
| `eqp_id` | 장비/호기 식별(로그·추후 확장용). **dwell → 이송 분기에는 직접 쓰지 않는다.** |
| `module_nm` | 생산 시스템이 부르는 **모듈 이름 문자열**. 코드가 이것을 내부 **`slot_key`** 로 바꾼다. |
| `cassette_id` | 같은 랏 안 웨이퍼 번호(샘플은 전부 `1`). **연속 dwell 이송은 같은 `cassette_id` 일 때만** 스텝으로 만든다. |
| `eqp_start_tm` / `eqp_end_tm` | 그 모듈에 **들어온 시각** / **나간 시각** [s]. 샘플은 `0~2`, `2~4` 처럼 **끝이 다음의 시작과 맞닿게** 적혀 있다. |
| `process_tm` | 공정/체류 관련 열(현재는 주로 로그·메타). **이송 스텝 길이의 단일 소스는 아니다.** |

시간 모드는 `simulation_play.py` 상단 `TIME_PARSE_MODE` (현재 `"seconds_float"`) 에 따른다.

#### 5.2.2 한 줄이 dwell 로 바뀌는 과정

1. CSV 파일을 읽어 각 행을 `ParsedCsvRow` 로 만든다 (`read_csv_rows`).
2. `module_nm` 을 `MODULE_NM_TO_SLOT_KEY` 로 조회해 **`slot_key`** 를 얻는다 (`slot_key_for_module_nm` / `build_default_module_nm_to_slot_key`).
   - 표에 없는 이름이면 **그 행은 건너뛰고** 콘솔에 skip 로그만 남긴다.
3. `eqp_end_tm < eqp_start_tm` 이면 비정상으로 보고 스킵한다.
4. 남은 행은 `DwellRecord` 가 되며, `start_sec`·`cassette_id`·`module_nm` 순으로 **정렬**된다 (`sort_dwells_for_playback`).

**샘플에서의 매핑 예 (일부):**
`AtmArm-EndEffector11` → `LOGICAL:ATM_ARM` · `CoolStationAL3PML2` → `buffer3_2` · `AirLock1-iSlot1` → `airlock1_1` ·
`TransferChamber-EndEffector1` → VTM 손(기본 좌 `LOGICAL:VTM_EE_L`, `VTM_END_EFFECTOR_SWAP_HANDS` 시 반대) ·
`PM1-PML3` → `chamber3` · `CoolStationAL1PML3` → `cooling_3` · `ATM-FOUP1-iSlot1` → `foup1_1`
(전체 표는 코드 `build_default_module_nm_to_slot_key()` 가 SSOT.)

#### 5.2.3 “애니가 왜 그렇게 움직여야 하는지” — 이송 한 번의 해석

dwell 리스트가 `…, A, B, …` 순이면, **한 번의 이송**은 “웨이퍼가 A 에서 B 로 옮겨졌다”는 뜻으로 본다.

1. **어느 로봇 클립을 쓸지** — `_classify_transfer_robot(prev.slot_key, curr.slot_key)` 가 `ATM` / `VTM` 중 하나를 고른다.
   (ATM 팔 논리 슬롯·VTM 손·챔버 키가 끼면 각각 해당 장비 쪽으로 기울어진다.)
2. **ATM 이면** — `atm_arm_to_atm_slot` 이 `slot_key` 와 `pick`/`place` 를 정하고,
   `atm_clip_by_slot_key` 에서 **in/out 프레임**을 읽어 `TIMESAMPLES_REPLAY` 두 번(in, out),
   필요 시 HeightStage `MOVE`, 슬롯/팔 `SET_PRIM_VISIBILITY` 를 꼬아 넣는다.
3. **VTM 이면** — `vtm_arm_move_to_chamber` 가 대상 `slot_key`(챔버·에어록 슬롯 등)와 손(`left`/`right`)을 정하고,
   `vtm_clip_by_slot_key` 에서 좌/우 **pick 또는 place** 클립을 고른 뒤,
   `ROTATE` → `MOVE`(Z) → `TIMESAMPLES_REPLAY`(in/out) → 복귀 … 순으로 쌓는다.

즉 **CSV는 “어디에 있었는지”만 말하고**, **어떤 클립·몇 프레임인지**는 전부 **`LamSimPlayVirtualConfig`** 의 클립 테이블에 있다.
CSV 숫자만 바꿔서는 팔 애니 구간이 바뀌지 않는다 — **클립·prim 경로·Z·Yaw** 를 `simulation_play.py` 의 기본 설정에서 맞춰야 한다.

#### 5.2.4 애니가 “안 도는 것처럼” 보일 때 파악 순서

1. **dwell 이 2개 미만인가** — 이송이 없으면 Runner 자체를 부르지 않는다.
2. **`TIMESAMPLES_REPLAY` 스텝 개수** — Play 직전 로그에 타입별 개수가 찍힌다. `0` 이면 `atm_timesample_prim` / `vtm_timesample_prim` 이 비었거나, 이송 블록이 비어 있다.
3. **`[build:atm]` / `[build:vtm]`** — 클립 없음·prim 비움·Z 테이블 누락 등 **어느 필드를 채워야 하는지** 안내한다.
4. **스테이지에 prim 이 있는가** — 경로가 USD 와 한 글자라도 다르면 MOVE/가시성/Registry 매칭이 실패할 수 있다.

#### 5.2.5 샘플 CSV의 앞부분을 한 줄씩 따라가 보면 (개념)

- **0~2s `AtmArm-…`** → dwell 위치 `LOGICAL:ATM_ARM`. (이전 dwell 이 없으면 이 줄만으로는 이송 스텝이 없다.)
- **2~4s `CoolStationAL3PML2`** → `buffer3_2`. 이전이 ATM 팔이면 “팔에서 버퍼로 내려놓기” 쪽 ATM 클립·Z·가시성 묶음이 된다.
- **6~8s `AirLock1-iSlot1`** → `airlock1_1`. 그 전 dwell 이 ATM 구간이면 ATM 이 에어록 슬롯에 맞추는 이송.
- **8~10s `TransferChamber-…`** → VTM 손 논리 슬롯. 에어록에서 진공 손으로 넘어가는 이송이면 **VTM** 분기·VTM 클립이 쓰인다.
- 이후 **`PM1-PML3` → `chamber3`** 등은 같은 방식으로 “이전 슬롯 → 현재 슬롯” 이 연출된다.

---

**장기 목표(본 절 원안):** “시뮬 결과 JSON 1 개를 던지면 LAM 이 알아서 가시성 + 로봇팔 동작 step 을 합성해
실시간 재생” — 본 문서는 그 매핑의 1차 **개념** 입력이다.

대략적인 파이프라인 (JSON 기반 자동화를 완성할 때의 그림):

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
| 2026-05-14 | §3.2 에 _설정 파일 위치_ 결정 추가 — **현재 A 안 (`lam/config/`) 유지**, B 안 (`source/extensions/morph.lam_control/config/`) 은 이주 옵션으로 보류. |
| 2026-05-14 | §5.1 **서사 예시**(CSV dwell·이송·ATM/VTM·가시성) 추가 — 개념 설명용 샘플 흐름. |
| 2026-05-14 | §5.2 **CSV 샘플·파싱·애니 원리**(`wafer01_tour_v1`) — 열 의미, dwell 생성, 이송→클립→TIMESAMPLES, 트러블슈팅 순서. |
