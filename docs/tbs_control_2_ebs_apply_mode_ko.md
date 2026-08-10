# TBS Control 2 — EBS 적용여부 모드 요구사항

> **상태:** 피드백·합의용 초안 (코드 미적용)  
> **목적:** 「EBS 적용여부」체크 해제 시 EP-only 시뮬·UI·USD·막대그래프 동작 확정

---

## 1. 개념

| 용어 | 설명 |
|------|------|
| **EBS (적용 ON, 기본)** | IN/OUT + BP1~4 버퍼 + EP1~3. EP가 찼을 때 OHT→IN/OUT 대기·버퍼 적재·BP→EP 이송 가능 (**현행과 동일**) |
| **EBS 미적용 (체크 해제)** | **EP 포트만** 존재. 버퍼 포트·관련 공정·UI·막대·USD prim 없음 |

**EBS 장비 목적 (참고)**  
EP만 있을 때는 FOUP을 바로 EP에 넣지 못해 대기·병목이 생기므로, IN/OUT·BP로 상단 버퍼를 두어 EP가 차 있어도 수용·효율을 높이는 구조.

---

## 2. UI 요구 (제어창)

### 2.1 신규 컨트롤

- **위치:** `LOT 수` / `EP 개수` 행 **위**
- **라벨:** `EBS 적용여부` (또는 합의 후 문구)
- **기본값:** **체크 ON** (EBS 적용)

### 2.2 EBS 해제 시 숨김·단순화

| 영역 | EBS ON | EBS OFF |
|------|--------|---------|
| 초기 적재 | IN/OUT, BP1~4, EP1~3 | **EP1~3 만** (EP2 기본, EP3는 ep_count=3일 때) |
| 고장(비가동) | IN/OUT, BP1~4, EP1~3 | **EP1~3 만** |
| 공정 시간 | OHT→EP, IN→BP, BP→EP, EP→OHT | **OHT→EP, EP→OHT** (IN→BP, BP→EP·OHT→BP1 경로 숨김) |
| EP3 선택 시 BP4 행 | BP4 초기/고장 체크 표시 | **BP4 행 자체 없음** — EP2/EP3 구분만 |

### 2.3 반영 시점 (확정)

| 대상 | 시점 |
|------|------|
| 제어창·HUD UI (체크박스·필드 숨김·막대·포트상태) | **체크 변경 즉시** |
| 시뮬 엔진·프리런 | **다음 Start부터** (재생 중 변경 없음) |
| USD prim (EBS_SHOW/HIDE) | 해당 화면 **「현재 설정 저장」** 클릭 시 (분할 화면별) |

### 2.4 HUD 동기화 (확정)

- EBS 적용여부 체크박스는 **제어창 + Viewport HUD** 양쪽에 표시·동기화.

---

## 3. 시뮬 목표 (EBS OFF) — 확정

### 3.1 한 줄 요약

**JSON 애니가 매핑된 이벤트는 `arrived_ep1~3`·`removed_ep1~3` 뿐**이고, EP 포트 위 **FOUP 공정은 현행 그대로** 진행한다.  
`oht_to_bp1`·IN/OUT·BP 관련 공정은 **애초에 존재하지 않는다**.

### 3.2 실행되는 것

| 구분 | 이벤트 | JSON 애니 | 비고 |
|------|--------|-----------|------|
| LOT 준비 | `READYTOLOAD` | 없음 | 엔진 내부·로그용 |
| OHT→EP 투입 | `ARRIVED` OHT→EPn | `arrived_ep1~3.json` | **유일한 투입 경로** |
| EP FOUP | `FOUP_PROCESS_START` / `END` | FOUP 전용 (기존과 동일) | JSON MOVE/ARRIVED 와 별개 |
| 회수 준비 | `READYTOUNLOAD` | 없음 | 엔진 내부 |
| EP→OHT 회수 | `REMOVED` EPn | `removed_ep1~3.json` | |

### 3.3 실행되지 않는 것 (발생·애니·공정 시간 모두 없음)

- `oht_to_bp1` / OHT→IN/OUT / IN→BP / BP→EP 전 경로
- `arrived_inout.json`, `move_inout_bp*.json`, `move_bp*_ep*.json` 매핑 이벤트
- `_step_bp1_to_buffer`, `_step_buffer_to_ep`, `_load_lot_to_inout`, `_can_load_to_bp1`

### 3.4 엔진 동작 (A안)

- `SimulationInitConfig.ebs_enabled=False` → 포트: `EP1`, `EP2` [, `EP3`] 만
- INOUT·BP 스냅샷 값 **무시**
- OHT 투입: 빈 EP → `ARRIVED` OHT→EPn (`arrived_ep*.json`)
- EP 전부 FULL → OHT **대기만**
- FOUP·pickup·회수: **현행 유지**

---

## 4. 현행 시뮬 규칙 (코드 기준 As-Is)

### 4.1 포트 구성

```
INOUT (고정) + BP1, BP2, BP3 [, BP4 if ep_count=3] + EP1 [, EP2] [, EP3]
```

- `SimulationInitConfig.ep_count` → 2 또는 3
- `max_oht_lots` ← UI `lot_count`

### 4.2 직렬 오케스트레이터 우선순위 (`_run_serial_flow`)

매 루프에서 **위에서부터 1건** 실행 후 `continue` (**2026-08-03 체인 분리 이후**):

| 순위 | 단계 | 함수 | 설명 |
|------|------|------|------|
| 1 | 버퍼→EP | `_step_buffer_to_ep` | 가장 오래된 BP → 빈 EP, `MOVE_REQ` |
| 2 | EP→OHT 회수 | `_step_pickup_to_oht` | pickup 티켓 소비, `READYTOUNLOAD`→`REMOVED` |
| 3 | IN/OUT→버퍼 | `_step_bp1_to_buffer` | IN/OUT에 LOT 있고 빈 BP 있으면 `MOVE_TRANSFERING` |
| 4 | OHT 투입 | `_step_oht_input` | **빈 EP 있으면 직접 투입**, 없으면 **IN/OUT 경유** |
| 5 | idle | `_step_idle_wait` | 대기 로그 + 짧은 sleep |

### 4.3 OHT 투입 분기 (`_step_oht_input`)

1. `READYTOLOAD` 미확인 LOT은 투입 안 함  
2. **Direct:** `_can_load_to_ep_direct()` → `_load_lot_to_ep_direct` → `ARRIVED` OHT→EPn  
3. **Buffer 경로:** `_can_load_to_bp1()` → `_load_lot_to_inout` → `ARRIVED` INOUT **후 종료**  
   → 오케스트레이터가 우선순위에 따라 IN/OUT→BP (`_step_bp1_to_buffer`) 수행 (**OHT→IN/OUT과 비연쇄**)

병렬 True 재개 계획: `docs/tbs_control_2_parallel_nonconflicting_resume_plan_ko.md`

### 4.4 이벤트 ↔ JSON 매핑 우선순위

1. `EVENT_JSON_CASE_MAP` (코드 내장, 최우선)  
2. `config/event_animation_rules.json` (priority 숫자, 조건 when)  
3. `config/event_animation_map.json` (fallback)

**시퀀스 별칭 (`SIM_SEQ_ALIAS`)**

| 엔진 seq | XML 시퀀스 |
|----------|------------|
| READYTOLOAD | EAPEIS_PORT_READYTOLOAD 계열 |
| ARRIVED | EAPEIS_PORT_ARRIVED |
| MOVE_TRANSFERING | EAPEIS_PORT_MOVE_TRANSFERING |
| MOVE_REQ | EISEAP_PORT_MOVE_REQ |
| READYTOUNLOAD | … |
| REMOVED | EAPEIS_PORT_REMOVED |

**JSON 매핑 요약 (`EVENT_JSON_CASE_MAP`)**

| seq | 키 예 | JSON |
|-----|-------|------|
| ARRIVED | `INOUT` | arrived_inout.json |
| ARRIVED | `OHT->EP1~3` | arrived_ep1~3.json |
| MOVE_TRANSFERING | `INOUT->BP1~4` | move_inout_bp*.json |
| MOVE_REQ | `BP1~4->EP1~3` | move_bp*_ep*.json |
| REMOVED | `EP1~3` | removed_ep1~3.json |
| READYTOLOAD / READYTOUNLOAD | — | 애니 없음 |

**FOUP 공정**  
`FOUP_PROCESS_START` / `END` — EP 안착 후 simpy 프로세스, 회수 대기(`_ep_awaiting_pickup`) 전제.

### 4.5 프리런·재생

- Start → 백그라운드 프리런 → 타임라인 재생 (고정 sim 시간)
- EBS OFF 시에도 **동일 프리런→재생 경로**, 엔진이 내는 **이벤트 집합만 축소**

### 4.6 막대그래프 행 (`bar_graph_row_order`)

**EBS ON (현행)**  
EP2: `EP1, EP2, ALL_EP, INOUT, BP1, BP2, BP3`  
EP3: `EP1, EP2, EP3, ALL_EP, INOUT, BP1, BP2, BP3, BP4`

**EBS OFF (확정)**  
EP2: `EP1, EP2, ALL_EP`  
EP3: `EP1, EP2, EP3, ALL_EP`

### 4.7 USD prim (`tbs_ep_port_visibility`)

- `EP2_PORT_LAYOUT` / `EP3_PORT_LAYOUT` — EP 개수별 show/hide
- 적용 순서(목표): **EP 레이아웃 → EBS 레이아웃**

---

## 5. USD prim 레이아웃 (신규)

` tbs_usd_window.py` 에 `EpPortLayout` 형식으로 추가:

```python
EBS2_SHOW_LAYOUT = EpPortLayout(hide_prims=(...), show_prims=(...))   # EP2 + EBS ON
EBS2_HIDE_LAYOUT = EpPortLayout(hide_prims=(...), show_prims=(...))  # EP2 + EBS OFF
EBS3_SHOW_LAYOUT = EpPortLayout(hide_prims=(...), show_prims=(...))   # EP3 + EBS ON
EBS3_HIDE_LAYOUT = EpPortLayout(hide_prims=(...), show_prims=(...))  # EP3 + EBS OFF
```

**적용 순서 (확정)**

```
EP2_PORT_LAYOUT 또는 EP3_PORT_LAYOUT
        ↓
EP2 + EBS ON  → EBS2_SHOW_LAYOUT
EP2 + EBS OFF → EBS2_HIDE_LAYOUT
EP3 + EBS ON  → EBS3_SHOW_LAYOUT
EP3 + EBS OFF → EBS3_HIDE_LAYOUT
```

예: EP2 + EBS OFF → `EP2_PORT_LAYOUT` 적용 후 `EBS2_HIDE_LAYOUT` 추가 적용.

**분할 화면 (확정)**  
- EBS 적용여부는 **화면별 스냅샷**에 포함 (`_sim_per_screen_snapshots`).
- 화면2~4에서 **「현재 설정 저장」** 클릭 시, 그 화면의 `ep_count` + `ebs_enabled` 기준으로  
  `EP2/3_PORT_LAYOUT` → `EBS_SHOW` 또는 `EBS_HIDE` 를 **해당 USD 컨텍스트에만** 적용.

---

## 6. 확정 사항 (피드백 반영)

| # | 항목 | 결정 |
|---|------|------|
| 1 | 적용 시점 | 재생 중 변경 없음. **다음 Start부터** 시뮬 반영. UI/HUD는 즉시 |
| 2 | EBS OFF 포트 | INOUT·BP1~4 **개념 자체 없음** — 무시 |
| 3 | OHT→BP1 | EBS OFF 시 **공정 없음** (UI·엔진 모두) |
| 4 | EP 전부 FULL | 버퍼 없이 **대기만** |
| 5 | 포트 상태 | EP 관련만 표시 |
| 6 | 막대그래프 | EP1·EP2(기본) + ep_count=3일 때 EP3 + ALL_EP |
| 7 | HUD | 제어창과 **동기화** |
| 8 | 분할 화면 | 화면별 「현재 설정 저장」 시 해당 화면에만 적용 (시뮬·USD prim) |

### 6.1 미확정 (구현 시 기본안)

| # | 항목 | 기본안 |
|---|------|--------|
| 9 | EBS OFF 시 OHT→EP 시간 | UI 「OHT→EP」필드 유지, 직접 투입에 사용 |
| 10 | EBS_SHOW / EBS_HIDE | 둘 다 정의. OFF=HIDE, ON=SHOW (prim 경로는 placeholder 후 사용자 채움) |
| 11 | 웹 API | 화면별 스냅샷에 `ebs_enabled` 포함 |

### 6.2 엔진 구현 방향 (12번 — 쉬운 설명)

**질문 요지:** EBS OFF를 코드에 넣을 때, **기존 엔진을 조금 고칠지** vs **새 엔진 파일을 따로 만들지**.

**A안 (권장) — 기존 엔진에 스위치 하나 추가**

- `ebs_enabled=True/False` 플래그를 Start 스냅샷에 넣음.
- OFF이면 엔진이 **처음부터** INOUT·BP 포트를 만들지 않고, 버퍼 관련 단계(0·2번)를 건너뜀.
- OHT 투입은 **빈 EP로만** 직행.
- 프리런·재생·JSON 매핑 등 **나머지 파이프라인은 그대로** — 이벤트 종류만 줄어듦.

비유: 같은 공장 라인에 **「버퍼 구역 사용/미사용」** 토글만 달아, 미사용이면 그 구역 문을 닫고 EP 직통만 돌리는 방식.

**B안 — EBS OFF 전용 엔진 파일을 새로 작성**

- EP-only 로직만 있는 별도 `simulation_engine_no_ebs.py` 등.
- 중복 코드·버그 동기화 부담이 큼.

→ **A안 확정** (최소 변경, fix 공정·배속과 같은 패턴).

**EBS OFF 시뮬 본질 (재확인)**  
- JSON 애니: **`arrived_ep1~3` + `removed_ep1~3` 만**  
- FOUP 공정: EP 안착 후 **기존과 동일**  
- `oht_to_bp1` 포함 버퍼 계열 공정: **없음**

---

## 7. 구현 체크리스트 (합의 후)

- [x] `_sim_ebs_enabled_model` + 제어창/HUD UI
- [x] `_sync_ebs_control_visibility` — 초기/고장/시간 필드·BP4 행 visibility
- [x] `simulation_engine` — `ebs_enabled` + EBS OFF 포트·직렬 단계
- [x] 스냅샷 `_capture_per_screen_sim_settings` + `_timing_and_init_from_snapshot`
- [x] `bar_graph_row_order` EBS 분기
- [x] 포트 상태·진행현황 EP-only 필터
- [x] `EBS2/3_SHOW/HIDE_LAYOUT` + `tbs_ep_port_visibility` 연동
- [ ] (선택) 웹 API·Operator Guide

---

## 8. 변경 이력

| 일자 | 내용 |
|------|------|
| 2026-06-16 | 초안 — EBS 적용여부 요구·현행 시뮬 규칙·질문 |
| 2026-06-16 | 피드백 반영 — Start 시점·화면별 저장·HUD·막대·엔진 A안 확정 |
| 2026-06-16 | EBS OFF 본질 확정 — arrived_ep/removed_ep JSON만 + FOUP 현행 |
| 2026-06-16 | 구현 완료 — 엔진·UI·HUD·막대·포트·USD 레이아웃 |
