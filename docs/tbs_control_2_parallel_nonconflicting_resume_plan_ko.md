# TBS Control 2 — 비충돌 병렬(`SIM_PARALLEL_NONCONFLICTING_MOVES`) 재개 계획

> **상태:** 구현 보류 · 추후 수정 참고용  
> **작성:** 2026-08-03  
> **대상:** `morph.tbs_control_2` / `simulation_engine.py`  
> **현재 플래그:** `sim_control_defaults.SIM_PARALLEL_NONCONFLICTING_MOVES = False` (완전 직렬)

이 문서는 **병렬 True를 다시 켜기 전에** 무엇을 고치고, 어떤 순서로 검증할지 고정한다.  
직렬 쪽 **체인 분리(OHT→IN/OUT 후 IN/OUT→BP 비연쇄)** 는 별도로 이미 반영된 전제다.

---

## 0. 왜 병렬이 필요한가

EBS(버퍼)의 이득은 “이동 hop이 많다”가 아니라:

- EP가 FOUP(~100s) 도는 **동안** OHT가 다음 LOT을 IN/OUT·BP에 **미리** 쌓고
- EP가 비는 순간 **BP→EP만** 시작하고
- 가능하면 **BP→EP ∥ OHT 다음 투입 ∥ (다른 EP) 회수**처럼 기기 비충돌 공정을 겹치는 것

직렬만으로는 한 공정 `yield`가 끝나는 동안 다른 이송을 못 해서, EBS ON이 OFF보다 느려질 수 있다.  
체인 분리는 “한 줄로 60~70초 묶이던 것”을 줄이는 **최소 조치**이고, **본래 EBS 이득 회복은 병렬 재개**가 담당한다.

---

## 1. 현재 As-Is (코드)

| 항목 | 내용 |
|------|------|
| 플래그 | `False` — `_run_serial_flow` 직렬 분기만 실무 사용 |
| True 시 | `_start_parallel_nonconflicting_wave()` — BP→EP / 회수 / OHT **nofollow** 기동 |
| 동시 제한 | `_bp_to_ep_inflight`, `_oht_path_inflight` (회수와 OHT 투입은 동시 기동 안 함) |
| IN/OUT→BP | 직렬·병렬 모두 오케스트레이터 `_step_bp1_to_buffer` (OHT→IN/OUT과 **비연쇄**, 2026-08-03~) |

### 의도상 허용 쌍 (True)

- BP→EP ∥ EP→OHT 회수 (서로 다른 EP·포트)
- BP→EP ∥ OHT→EP / OHT→IN/OUT (EP·포트 충돌 없을 때)

### 의도상 비허용

- 회수 ∥ OHT 투입 (같은 OHT 경로 → `_oht_path_inflight`)
- 같은 LOT을 두 포트에 **복제**하는 것 (버그)

---

## 2. True에서 발견된 / 남아 있는 리스크

### 2.1 LOT 이중 점유 (심각 — 재개 차단 사유)

**증상:** 같은 LOT이 BP1·BP2 등에 동시에 FULL.

**경로(요약):**

1. True: OHT→IN/OUT을 nofollow로 기동 (`_load_lot_to_inout_parallel`)
2. (과거) `_load_lot_to_inout` 안에서 IN/OUT→BP까지 체인 + 오케스트레이터 `_step_bp1_to_buffer` 재진입  
   → 동일 LOT으로 `_move_bp1_to_buffer`가 두 번 돌며 서로 다른 BP에 `_set_port`

**이미 넣은 완화 (False/예방):**

- `_step_bp1_to_buffer`: `_oht_loading_bp1` / INOUT lock 가드
- OHT→IN/OUT과 IN/OUT→BP **체인 제거** (안착 후 루프 재평가)

**True 재개 시 추가 확인:**

- nofollow OHT→IN/OUT 진행 중 `_step_bp1_to_buffer` / 다른 INOUT→BP 기동이 **절대** 같은 LOT을 두 번 집지 않는지
- `_oht_loading_bp1` 해제 타이밍(안착 직후)과 parallel wave 타이밍 race
- `_can_load_to_bp1` 완화(잠금+점유 버퍼 = “곧 빈 슬롯”)가 OHT를 너무 일찍 기동하지 않는지

### 2.2 포트 UI / 2화면 SSOT

병렬과 무관하게도 재생·REFRESH·screen 태그로 LOT이 출렁일 수 있음.  
P0(화면 라우팅·ctx fallback·REFRESH drop) 안정 후 True를 켜는 것을 권장.  
→ `docs/tbs_control_2_parallel_port_state_investigation_ko.md`

### 2.3 포트 점유 타이밍 vs 애니

엔진은 이동 **완료** 시 `_set_port`. UI는 renewal / JSON end / plan milestone.  
병렬이면 “놓는/집는” 체감과 패널이 더 어긋나 보이기 쉬움 → 재생·renewal SSOT 유지한 채 True 검증.

### 2.4 FOUP `capacity=1`

전역 FOUP 1개 제약은 True/False 공통. 병렬이 EP **이송**을 겹쳐도 FOUP 자체는 직렬.  
EBS 효율 목표와 혼동하지 말 것 (수송 겹침 ≠ FOUP 병렬).

---

## 3. 재개 시 수정 체크리스트 (구현 순서 제안)

### Step A — 안전 가드 (필수)

1. [ ] INOUT→BP **단일 실행자** 명시  
   - 예: `_inout_to_bp_inflight` 또는 “INOUT에 LOT 있고 잠금이면 재기동 금지”
2. [ ] OHT nofollow finally에서 `_oht_loading_bp1` / `_oht_path_inflight` / 포트 잠금 정리 누락 없는지
3. [ ] `_move_bp1_to_buffer` 시작 시 INOUT lot id를 캡처하고, 완료 시 동일 lot만 이동(중간에 바뀌면 abort)
4. [ ] `_can_load_to_bp1` 완화 조건 재검토 — “곧 빔” 가정으로 이중 투입이 나지 않게

### Step B — 우선순위·wave 정렬

1. [ ] True 분기에서도 **BP→EP · 회수**가 IN/OUT→BP보다 급하면 먼저 기동되는지 (체인 분리 이후 직렬 순서와 철학 일치)
2. [ ] wave: 회수 성공 시 같은 tick에 OHT 투입 금지(현행 유지)
3. [ ] IN/OUT→BP를 nofollow로 올릴지, wave 실패 시에만 직렬로 둘지 결정·문서화

### Step C — 관측·로그

1. [ ] 시작 로그 `오케스트레이터=병렬(비충돌)` 확인
2. [ ] 동일 `lot_id`가 두 포트 FULL인 heartbeat/스냅샷 감지 시 즉시 `_log` + 가능하면 assert/가드
3. [ ] 이중 점유 회귀 테스트(시나리오): EP full → OHT→INOUT nofollow + 오케스트레이터 INOUT→BP 재시도

### Step D — 플래그 ON

1. [ ] `SIM_PARALLEL_NONCONFLICTING_MOVES = True` (기본 또는 실험 토글)
2. [ ] CASE A(EBS ON) vs CASE B(EBS OFF) 동일 시드·동일 LOT 수에서 **완료 sim time** 비교  
   - 기대: EBS ON ≤ OFF (최소 동등, 이상적으로 ON이 더 빠름)
3. [ ] 2화면 동시 재생에서 포트 LOT 출렁임 재발 없는지

---

## 4. 검증 시나리오 (수동)

| ID | 설정 | 확인 |
|----|------|------|
| V1 | 직렬 False, EBS ON | 체인 분리 유지: IN/OUT 안착 로그 후 BP→EP/회수가 IN/OUT→BP보다 먼저 가능한지 |
| V2 | True, EP 2, LOT 충분 | 같은 LOT 이중 BP 점유 **0건** |
| V3 | True, EP full 중 OHT→INOUT | FOUP/회수와 시간 겹침이 로그·간트에 보이는지 |
| V4 | True, A=EBS ON / B=EBS OFF | 완료 시각 A ≤ B |
| V5 | True + 2화면 | 화면별 ports_occupancy 독립 |

---

## 5. 관련 파일

| 파일 | 역할 |
|------|------|
| `sim_control_defaults.py` | `SIM_PARALLEL_NONCONFLICTING_MOVES` |
| `simulation_engine.py` | `_run_serial_flow`, `_start_parallel_nonconflicting_wave`, `_*_nofollow`, `_load_lot_to_inout`, `_step_bp1_to_buffer`, `_can_load_to_bp1` |
| `docs/tbs_control_2_parallel_port_state_investigation_ko.md` | 포트/2화면/REMOVED 조사·P0~P2 |
| `docs/tbs_control_2_ebs_apply_mode_ko.md` | EBS ON/OFF 모드 요구 |

---

## 6. 합의 메모

| 날짜 | 내용 |
|------|------|
| 2026-08-03 | 실무 기본 = **직렬 False**. 병렬은 포트·2화면·점유 안정 후 재개 |
| 2026-08-03 | **체인 분리 선행** 적용: `_load_lot_to_inout` 끝에서 `_move_bp1_to_buffer` 호출 제거. 직렬 우선순위 = BP→EP → 회수 → IN/OUT→BP → OHT |
| (추후) | 이 문서 §3 체크리스트 통과 후 True |

---

## 7. 한 줄 요약

**병렬 재개 = EBS가 OFF보다 빨라지기 위한 본게임.**  
그 전에 이중 점유 가드·wave/INOUT→BP 단일 실행자·완료 시각 A≤B 검증을 끝낼 것.  
체인 분리는 직렬에서도 EP 공회전을 줄이는 **선행 패치**이며, 병렬을 대체하지 않는다.
