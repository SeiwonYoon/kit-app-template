# TBS Control 2 — 비충돌 병렬·포트상태 꼬임 조사 정리

> **상태:** 구현 진행 중 (P0~P2 코드 반영, Kit 실기 검증 대기) / 병렬 True(P3) 보류  
> **작성:** 2026-08-03  
> **갱신:** 2026-08-03 (수정 반영)  
> **대상:** `morph.tbs_control_2`  
> **관련 플래그:** `sim_control_defaults.SIM_PARALLEL_NONCONFLICTING_MOVES`

이 문서는 사용자 제보를 항목별로 정리하고, **현재 코드베이스에서 재현 가능한지**, **유력 원인**, **수정 방향**을 고정한다.  
§4·§6 합의에 따라 **코드 수정을 진행** 중이며, 아래 **§7 진행 상태**를 본다.

---

## 0. 한 줄 요약

| 구분 | 결론 |
|------|------|
| **의도** | 기기가 안 겹치는 공정만 **동시 기동**. LOT/포트 상태를 **복제**하라는 뜻이 아님 |
| **현재 플래그** | **`False` (기본·실무)** — 완전 직렬. True(병렬)는 포트/2화면/점유 버그 수정 후 재개 |
| **가장 큰 엔진 버그** | True일 때 OHT→INOUT(내부 INOUT→BP)와 오케스트레이터 `_step_bp1_to_buffer`가 **배타가 아님** → **같은 LOT이 두 BP에 동시 점유** 가능 |
| **포트 UI 혼란** | 엔진 점유 / JSON 종료 predict / renewal / `PORT_OCC_REFRESH` / 2화면 라우팅이 **다중 SSOT** |
| **False에서 “겹침”** | 엔진 `_run_serial_flow` False 분기는 **INOUT→BP ‖ arrived_inout을 허용하지 않음**. 착시·플래그 실제값·재생 계층을 먼저 확인 |

---

## 1. 사용자 제보 → 이슈 맵

| ID | 제보 현상 (요약) | 분류 |
|----|------------------|------|
| **A** | INOUT→BP 시 대상에 이미 같은 LOT이 있음. 애니로 BP2에 옮기는데 BP1·BP2에 같은 LOT이 동시에 참 | 점유 복제 / 이중 이송 |
| **B** | 겹침 허용 ≠ 복제. 동시 실행만 허용해야 함 | 설계 의도 확인 |
| **C** | `SIM_PARALLEL=False`인데도 INOUT→BP와 arrived_inout이 같이 실행됨 (허용한 적 없음) | False 직렬 위반 의 / 착시 |
| **D** | `True`일 때 동시 허용 공정이 제대로 안 돌거나, 돌아도 **포트 상태가 애니 “놓는/집는” 타이밍과 안 맞음** | 병렬 + 포트 sync |
| **E** | EP1이 비어 보이는데 BP→EP1이 안 일어남 (`False`에서도). 실제로는 안 비었는데 UI만 EMPTY인 듯 | UI/엔진 점유 불일치 |
| **F** | 2화면 포트상태·LOT 번호가 수시로 바뀜 (007~009 ↔ 011~013 등). 화면 간 동기화처럼 보임 | 멀티스크린 라우팅 |
| **G** | REMOVED: JSON 끝 숨김/보임 + **집는 모션 순간** 포트상태 반영이었는데, 간헐적으로 깨짐 | REMOVED sync 회귀 |
| **H** | REMOVED 시작 직후(집기 전) 포트·객체 사라지고, JSON 집는 순간에 객체만 다시 나타남. 포트상태는 빈 채로 유지 | REMOVED 조기 clear |

---

## 2. 설계 의도 (어제 수정·합의 내용)

### 2.1 `SIM_PARALLEL_NONCONFLICTING_MOVES`

| 값 | 의도 |
|----|------|
| **False** | 기존과 동일 — 매 공정 `yield process` **완전 직렬** |
| **True** | 기기 비충돌 시 **완료를 기다리지 않고** 다음 공정 **기동** |

**True에서 허용한 쌍 (의도):**

- BP→EP ‖ EP→OHT 회수 (서로 다른 EP)
- BP→EP ‖ OHT→EP / OHT→INOUT (EP·포트 충돌 없을 때)

**의도상 비허용:**

- 회수 ‖ OHT 투입 (같은 OHT 경로)
- **INOUT→BP ‖ arrived_inout(OHT→INOUT)** — “INOUT→BP는 True/False 모두 직렬”이라고 주석에 적혀 있음  
  (다만 구현은 OHT→INOUT 프로세스 **내부**에서 arrived 후 INOUT→BP를 이어서 돌리고, True일 때 오케스트레이터가 OHT를 fire-and-forget 함 → §3)

**절대 아닌 것:** 같은 LOT을 두 포트에 **복제**하거나, 점유 스냅샷을 두 번 적용하는 것.

### 2.2 포트상태 vs 애니 (기존 요구)

| 이벤트 | 기대 |
|--------|------|
| 일반 MOVE/ARRIVED | 공정·애니 정책에 맞는 시점에 점유 반영 (엔진은 완료 시 `_set_port` / `_remove_from_port`) |
| **REMOVED** | 3D: JSON 끝까지 또는 renewal 규칙에 따른 숨김/보임. **포트 패널은 “집는 모션(renewal/픽)” 시점에 EMPTY**, 시작 직후 즉시 지우면 안 됨 (간헐 회귀 제보) |

---

## 3. 코드 사실 확인 (수정 없음)

### 3.1 플래그·오케스트레이터

- 파일: `morph/tbs_control_2/sim_control_defaults.py`  
  - `SIM_PARALLEL_NONCONFLICTING_MOVES: bool = **False**` (합의·코드 반영 완료)
- 파일: `morph/tbs_control_2/simulation_engine.py` — `_run_serial_flow`
  - 매 루프 **최상단** 항상: `_step_bp1_to_buffer()` (`yield`로 INOUT→BP 1회)
  - `parallel=True`: `_start_parallel_nonconflicting_wave()` → BP→EP / 회수 / OHT **nofollow**
  - `parallel=False`: `_step_buffer_to_ep` → pickup → oht **각각 yield**

### 3.2 이슈 A — 같은 LOT이 BP1·BP2에 동시 (코드상 **실재**)

**경로:**

1. True: `_try_start_oht_input_nofollow` → `env.process(_load_lot_to_inout_parallel)` (**완료 대기 없음**)
2. `_load_lot_to_inout`: ARRIVED wait → `_set_port(INOUT)` → `yield _move_bp1_to_buffer()`  
   - `_move_bp1_to_buffer`가 빈 슬롯을 고를 때 BP→EP로 “곧 빌” 예정이던 슬롯이 아직 FULL이면 **다른 EMPTY BP**를 고르거나, 실패 후 재시도
3. 동시에 오케스트레이터 루프가 `_step_bp1_to_buffer`를 다시 돌림  
   - 가드: `ports[INOUT] is not None` + empty buffer만 검사  
   - **`_oht_loading_bp1` / INOUT lock / “이미 INOUT→BP 진행 중” 미검사**
4. 같은 LOT으로 `_move_bp1_to_buffer`가 **두 번** 돌면 finally마다 `_set_port(서로 다른 BP)` → **복제**

추가로 `_can_load_to_bp1` (True 전용 완화): 잠금+점유 버퍼를 “빈 슬롯 예정”으로 쳐 OHT→INOUT를 기동 → 빈 슬롯이 실제로 생기기 전에 arrived가 끝나 INOUT→BP가 끼어들 여지.

**애니 BP2 / 포트 BP1+BP2:**  
엔진이 이미 이중 점유인 상태에서 UI predict(`_predict_ports_occupancy_after_anim`)가 MOVE 종료 시 from 비우고 to에 LOT → 한쪽 애니·양쪽 패널이 엇갈려 보일 수 있음.

| 코드 실재? | **예 (True 경로, 심각)** |
|------------|--------------------------|
| False에서도? | 엔진상 `_load_lot_to_inout`를 yield로 끝까지 기다리므로 **동일 재진입은 막힘**. False에서 보이면 UI/재생·다른 원인 |

### 3.3 이슈 B — 복제 vs 동시 기동

코드 버그(A)가 “동시 허용”을 **점유 복제**로 깨뜨린 상태.  
수정 목표는 **동시 기동만** 유지하고 **점유는 단일 SSOT**로 유지.

### 3.4 이슈 C — False인데 INOUT→BP ‖ arrived_inout

**실무 재현 조건:** 사용자 확인 — 현상 제보는 **`SIM_PARALLEL=False` + 실무 데이터** 기준.

| 계층 | False일 때 |
|------|-----------|
| 엔진 `_run_serial_flow` | OHT는 `_step_oht_input` → `yield _load_lot_to_inout` → 그 안에서 arrived **후** INOUT→BP. **오케스트레이터가 둘을 병렬 기동하지 않음** |
| 타임라인(프리런) | 직렬로 기록되는 것이 정상 |
| 그런데도 “같이 실행”으로 보이는 경우 | ① **재생 계층**에서 화면1·2 JSON 동시 재생 ② FOUP/`PORT_OCC_REFRESH` non-gated emit ③ 포트/애니 타이밍이 어긋나 두 공정이 겹쳐 보임 ④ 진행현황 1줄 착시 |

→ False에서도 **재생·포트 UI·2화면** 때문에 겹쳐 보일 수 있음. “엔진이 False인데 병렬 기동한다”와 “화면에서 겹쳐 보인다”를 분리해서 수정한다.

| 엔진이 False에서 arrived‖INOUT→BP nofollow? | **설계상 없음** |
| False 실무에서 겹침·꼬임 제보? | **있음 → 재생/포트/2화면 쪽 우선** |

### 3.5 이슈 D — True 동시 허용 + 포트 타이밍

- BP→EP ‖ OHT nofollow는 **구현되어 있음**. 다만 A 버그·inflight·empty 조건 때문에 “제대로 안 도는” 체감 가능.
- 엔진 점유는 이동 **완료** 시 반영. UI는 JSON 종료 predict / renewal sync / REFRESH가 **더 이르거나 늦을 수 있음**.
- 기대: “놓는/집는” 모션 순간에 포트 반영 → **재생·renewal 경로와 엔진 finally가 어긋나면** D·G·H로 나타남.

### 3.6 이슈 E — EP EMPTY인데 BP→EP 안 함

가능 원인 (코드상 모두 실재):

1. **이동 중:** `ports[EP]=None` + `_dispatching_to_ep` + lock → UI EMPTY, 엔진은 “예약됨” → 다른 BP→EP는 그 EP 스킵  
2. **UI만 EMPTY:** REMOVED/predict가 패널을 먼저 비움, 엔진은 wait 끝까지 FULL → `_find_empty_ep` 실패  
3. FOUP 전역 `capacity=1`은 “다른 EP 공정 대기”이지 EMPTY 표시와는 별개

| 코드 실재? | **예 (UI↔엔진 불일치 + 이동 중 EMPTY 표시)** |

### 3.7 이슈 F — 2화면 LOT 번호 출렁임

- 엔진 `ports` / `_sim_last_ports_occupancy_by_screen` / 모니터 채널은 **화면별 분리 설계**
- 위험: `tbs_sim_screen` 누락 시 **기본 화면 1**, USD visibility가 **기본 ctx fallback**, 재생 plan·heartbeat가 잘못된 화면 last-occ를 덮음

| 코드 실재 (완전 공유 SSOT)? | 설계상 분리, **라우팅/fallback 버그로 섞일 여지 있음** |

### 3.8 이슈 G·H — REMOVED 타이밍

**정답(사용자 확인):**

- 포트 상태 → **집는 순간** EMPTY  
- LOT 객체 → **JSON 종료** 시 숨김  
- 현재도 대체로 그렇게 되어 있으나 **간헐적으로** 어긋남 (시작 직후 포트·객체 선소실 등)

| 코드 실재? | **예 (다중 갱신 시점 + 간헐 race) — 설계 변경이 아니라 회귀 수정** |

---

## 4. 수정 방향 (구현은 문서 합의 후)

**합의된 작업 순서:** False 직렬 고정 → **2화면 독립·포트상태(LOT 출렁/미갱신)** → REMOVED 간헐 회귀 → (안정 후) 병렬 True.

### P0 — 2화면 독립성 + 포트상태 SSOT (이슈 F, 실무 False 최우선)

1. 모든 event/progress/REFRESH에 `tbs_sim_screen` 필수; 누락 시 drop 또는 로그
2. 화면2 visibility가 default USD ctx로 fallback하지 않게
3. `_sim_last_ports_occupancy_by_screen` / 모니터 채널 / playback plan이 **같은 screen 키**만 읽고 쓰기
4. 포트 패널 LOT이 다른 화면 값으로 바뀌었다가 되돌아오는 덮어쓰기 경로 제거
5. 포트 실시간 미갱신 경로(REFRESH 스킵·predict race) 정리

### P1 — REMOVED 간헐 회귀 (이슈 G·H) — 설계 유지

1. 포트 EMPTY = **집는 순간(renewal)** 만
2. 3D hide = **JSON 종료**
3. 시작 직후 조기 clear가 renewal보다 먼저 패널을 비우는 race 제거

### P2 — 직렬에서도 점유/표시 꼬임 (이슈 A·C·D·E)

1. 엔진 점유와 패널 갱신 시각 정렬
2. EP "비어 보이는데 안 채워짐" = 패널↔엔진 불일치 해소

### P3 — 병렬 True (보류)

`_step_bp1_to_buffer` 재진입 가드 등 복제 방지는 **P0~P2 안정 후** True 재개 시 적용.

### 검증 체크리스트 (수정 후)

- [ ] False 직렬: 시작 로그 `오케스트레이터=직렬`
- [ ] 2화면: 화면1 LOT이 화면2 공정만으로 바뀌었다가 되돌아오지 않음
- [ ] 포트 패널이 공정 진행에 맞춰 실시간·화면별로만 갱신
- [ ] REMOVED: 집기 전 유지 → 집는 순간 포트 EMPTY, JSON 끝 객체 숨김 (간헐 포함)
- [ ] 동일 LOT이 두 BP에 동시 FULL 없음

---

## 5. 관련 파일 목록

| 파일 | 역할 |
|------|------|
| `sim_control_defaults.py` | `SIM_PARALLEL_NONCONFLICTING_MOVES` |
| `simulation_engine.py` | `_run_serial_flow`, `_step_bp1_to_buffer`, `_can_load_to_bp1`, `_*_nofollow`, `_load_lot_to_inout`, `_move_bp1_to_buffer`, `_execute_pickup`, `_set_port` |
| `control_window.py` | `_predict_ports_occupancy_after_anim`, `_try_apply_port_state_after_json_anim`, `_update_port_occupancy_panel`, 화면 라우팅 |
| `control_sim_playback_plan.py` | renewal / removed prim hold / playback occ |
| `control_sim_prerun_playback.py` | 프리런 타임라인·predict |
| `playback_renewal_ports.py` / `json_playback_timing.py` | renewal sync 시각 |

---

## 6. 합의·결정 (2026-08-03 사용자 확인)

1. **플래그 기본값 = False**  
   - 실무 테스트·재현도 **False(완전 직렬)** 기준이었다.  
   - 병렬(True)은 **포트상태·2화면 독립·점유 버그를 먼저 고친 뒤** 다시 켠다.  
   - 코드: `SIM_PARALLEL_NONCONFLICTING_MOVES = False` 로 반영됨.

2. **REMOVED 기대 동작 (정답 유지, 간헐 회귀만 수정)**  
   - **포트 상태:** 집는 모션(renewal) 순간에 EMPTY  
   - **LOT 3D 객체:** JSON 종료 시 숨김  
   - 대부분 이렇게 동작하나 **가끔** 어긋남 → 버그로 수정 대상 (설계 변경 아님).

3. **§4 P2의 “RESERVED” 제안**  
   - 사용자가 이해하기 어렵다고 함 → 아래 **쉬운 설명** 참고.  
   - 필수 요구가 아니라 “UI가 비었다고 오해하는 경우”를 줄이려던 **선택안**.  
   - 실무 우선은 **False 직렬 + 포트/2화면 SSOT 수정**.

4. **2화면 독립성 = 최우선**  
   - 여전히 화면 간 포트상태·LOT 번호가 섞이거나, 바뀌었다가 되돌아오는 현상 존재.  
   - 포트 실시간 갱신 실패도 동일 축(라우팅·last-occ·playback/REFRESH)으로 조사·수정.

### 이슈 3(EP EMPTY) 쉬운 설명

포트 패널에 **EP1이 비어 있다**고 보이는데, 엔진 입장에서는 아직 그 EP로 **들어가는 중**이거나 **아직 LOT이 있다고 기억**하는 경우가 있습니다.

- 예: BP→EP1 이동 JSON이 돌아가는 동안, 엔진은 “EP1에 아직 안 도착”이라 `ports[EP1]=비움`으로 둘 수 있음 → 패널도 빈칸.  
  그런데 사용자는 “비었으니 다음 BP→EP1이 되어야지”라고 보지만, 엔진은 “이미 EP1으로 보내는 중”이라 같은 EP를 다시 안 고름.
- 또는 반대로: 패널만 REMOVED/predict로 먼저 비웠는데, 엔진은 회수가 안 끝나 FULL → “비어 보이는데 BP→EP가 안 됨”.

즉 **화면(포트상태)과 엔진 속마음이 다른 것**이 원인 후보입니다.  
“RESERVED”는 빈칸 대신 “이동중”처럼 보여 주자는 아이디어일 뿐이고, **먼저 고칠 것은 패널과 엔진 점유를 같은 시각에 맞추는 것**입니다.

---

## 7. 구현 진행 상태 (2026-08-03)

| 항목 | 상태 | 요지 |
|------|------|------|
| 플래그 False | **완료** | `SIM_PARALLEL_NONCONFLICTING_MOVES = False` |
| P0 화면≥2 USD fallback | **완료** | `_usd_context_name_for_sim_screen` — 기본 ctx 폴백 금지 |
| P0 screen 태그 drop | **완료** | ANIM_EVENT / PROGRESS / heartbeat / 진행 UI / FOUP — 멀티에서 `tbs_sim_screen` 없으면 drop |
| P0 재생 중 PORT_OCC_REFRESH | **완료** | last-occ·패널 미반영 (plan milestone만) |
| P0 bare visibility fallback | **완료** | ctx 전용 `apply_port_lot_prim_visibility_for_context` |
| P1 REMOVED 조기 EMPTY | **완료** | sync 시각 없으면 predict 유지; offset=0 REMOVED는 anim 집기 추정; hide-hold=json end |
| P2 `_step_bp1_to_buffer` 재진입 | **완료(예방)** | `_oht_loading_bp1`·INOUT lock 가드 (True 재개 대비) |
| P2 패널↔엔진 EP 착시 | **부분** | RESERVED UI 미도입. 포트 sync/predict 정렬로 완화 — Kit 실기 확인 필요 |
| P3 병렬 True | **보류** | 참고: `docs/tbs_control_2_parallel_nonconflicting_resume_plan_ko.md` |
| 체인 분리 (직렬) | **완료** | OHT→IN/OUT 후 IN/OUT→BP 비연쇄. 우선순위: BP→EP → 회수 → IN/OUT→BP → OHT |

### 검증 체크리스트 (실기)

- [ ] False 직렬: 시작 로그 `오케스트레이터=직렬`
- [ ] 2화면: 화면1 LOT이 화면2 공정만으로 바뀌었다가 되돌아오지 않음
- [ ] 포트 패널이 공정 진행에 맞춰 실시간·화면별로만 갱신
- [ ] REMOVED: 집기 전 유지 → 집는 순간 포트 EMPTY, JSON 끝 객체 숨김 (간헐 포함)
- [ ] 동일 LOT이 두 BP에 동시 FULL 없음
