# LAM 시뮬 — 실무(실 USD·실 데이터) 테스트 가이드

본 문서는 **`simulation_play.py`를 수정해 `default_lam_sim_virtual_config()` 등에 값을 넣은 뒤**, Kit 에서 **실제 USD 를 로드한 상태**로 **지금 코드가 무엇까지 재생·검증할 수 있는지**만 정리한다.
입력 필드의 의미·위치는 **`LAM_Simulation_Play_User_Config.md`** 가 SSOT 이다.

---

## 1. 구조 확인 — “CSV / 매크로로 모델이 재생되는가?”

**맞다.** 두 경로 모두 최종적으로 **`lam_sequence_engine.LamSequenceRunner.run(steps)`** 에 스텝 dict 리스트를 넘기고, 스텝 타입에 따라 USD 를 구동한다.

| 경로 | 진입점 | 스텝 생성 | 실행 |
|------|--------|-----------|------|
| **CSV 시뮬 재생** | `run_simulation_from_csv(registry, scheduler, …)` | dwell ≥2 이면 인접 dwell 쌍마다 `build_steps_for_dwell_transfer` → 내부에서 `atm_arm_to_atm_slot` / `vtm_arm_move_to_chamber` 와 동일 규칙 | 위 표와 같이 `LamSequenceRunner.run` |
| **매크로 스크립트** | `run_lam_sim_script_line` / `run_lam_sim_script_text` | `LAM_SIM_MACRO_CALLABLES` 에 등록된 함수가 스텝 리스트 반환 | `run_lam_sim_steps` → `LamSequenceRunner.run` |

**전제 (공통):**

- `registry`·`scheduler` 가 확장(UI)에서 Kit 용으로 **유효하게 주입**되어 있어야 한다 (`LamSimulationCsvPlayWindow` 등).
- 스텝에 들어가는 **prim 경로**는 현재 스테이지에 **존재**해야 MOVE/ROTATE/가시성이 실제로 적용된다. 없으면 엔진이 스킵·로그만 남길 수 있다.
- **timeSamples 애니**는 `TIMESAMPLES_REPLAY` 스텝의 `ref.prim_path` 가 Registry 에서 인스턴스로 해석되어야 재생된다. `atm_timesample_prim` / `vtm_timesample_prim` 이 비면 **DELAY** 로 길이만 맞추고 애니는 돌지 않는다(콘솔 `[build:atm]` / `[build:vtm]` 참고).

**주의:** dwell 이 **1개뿐**이면 이송 구간이 없어 Runner 를 호출하지 않는다.

---

## 2. 실무 전 준비 체크리스트 (데이터 입력 후)

`simulation_play.py` 의 **`default_lam_sim_virtual_config()`** (또는 런타임 `LAM_SIM_VIRTUAL_CONFIG` 수정 후 **`refresh_lam_sim_runtime_tables_from_config()`**) 기준으로 다음을 맞춘다.

1. **경로**
   - `atm_height_prim_path`, `atm_timesample_prim`, `vtm_timesample_prim`, `vtm_position_prim_path`, `vtm_rotation_prim_path`
   - 웨이퍼: `wafer_prim_atm_arm`, `wafer_prim_vtm_hand_l` / `r`, `wafer_tmpl_*`, `wafer_prim_aligner`
2. **클립 (프레임)**
   - `atm_clip_by_slot_key`, `vtm_clip_by_slot_key` (또는 폴백 `*_clip_by_station`)
3. **Z / Yaw**
   - ATM: `z_table_authored_baseline_m`, `z_slot_delta_m`, `atm_z_usd_world_offset_m` 등 (`LAM_Simulation_Play_User_Config.md` §3.5)
   - VTM: `vtm_z_*`, `vtm_orient_yaw_by_slot_and_hand` 등
4. **CSV 매핑**
   - `build_default_module_nm_to_slot_key()` — 생산 `module_nm` 이 없으면 해당 행은 dwell 에서 **스킵**된다.

---

## 3. 현재 코드로 가능한 테스트 종류 (권장 순서)

### T1 — 설정만 검증 (Kit USD 로드 후, 재생 최소)

- **목적:** 경로·클립·Z 가 문서/측정값과 일치하는지, 콘솔 진단이 깨끗한지.
- **방법:** `simulation_play` 창에서 CSV 선택 후 Play **한 번** (또는 매크로 한 줄).
- **볼 로그:** `[LAM/SIMPLAY]`, `[build:atm]`, `[build:vtm]`, `[build:transfer]`, `[LAM/SEQ]` 등.
- **판정:** `TIMESAMPLES_REPLAY` 개수가 0이면 `[build:csv_play]` 안내에 따라 `atm_timesample_prim` / `vtm_timesample_prim` 부터 확인.

### T2 — 매크로 단일 동작 (원인 분리에 가장 유리)

- **목적:** CSV·dwell 순서와 무관하게 **ATM 한 슬롯** 또는 **VTM 한 목표**만 검증.
- **방법:** 스크립트 창에 예시 한 줄씩 실행 (키워드 인자만 허용).

```text
atm_arm_to_atm_slot(slot_key="foup1_1", duration_sec=15.0, pick_or_place="pick")
vtm_arm_move_to_chamber(hand="left", chamber_index=3, duration_sec=20.0, pick_or_place="visit", target_slot_key="chamber3")
```

- **판정:** Height Z MOVE, Yaw ROTATE, 클립 in/out, 웨이퍼 visibility 가 기대와 같으면 통과.

### T3 — CSV 전체 투어 (dwell 간 이송 연쇄)

- **목적:** `module_nm` → `slot_key` → 이송 분류(ATM/VTM) → 연속 매크로 호출이 **한 번의 Runner.run** 으로 이어지는지.
- **방법:** `lam/csv/*.csv` 또는 `LAM_SIM_CSV` 로 실데이터 형식 CSV 지정 후 Play.
- **전제:** dwell ≥2, 동일 `cassette_id` 구간만 이송 스텝 생성(다른 카세트는 스킵 로그).

### T4 — 배속·시간 압축

- **목적:** `run_simulation_from_csv(..., speed_scale=…)` 또는 `run_lam_sim_steps(..., target_duration_sec=…)` 로 wall-clock 을 줄여 반복 테스트.
- **한계:** 스텝 내부 `duration`·프레임 구간과의 상호작용은 `lam_sequence_engine` 정책을 따른다.

### T5 — (선택) 마지막 빌드 JSON 확인

- **목적:** 실제로 Runner 에 넘긴 스텝을 파일로 남기고 싶을 때.
- **방법:** 매크로/CSV Play 직후 전역 **`LAM_SIM_LAST_BUILT_JSON`** (마지막으로 조립된 한 블록) 또는 로그에 찍힌 step 요약을 참고.

---

## 4. 아직 “자동으로” 되지 않는 것 (기대치 조절)

- **dwell 내부 공정 애니** (챔버 안에서 장시간 가동 등): `build_steps_for_dwell` 은 현재 빈 리스트 — **이송 사이**만 스텝으로 나간다.
- **다중 웨이퍼 동시 이송:** `build_steps_for_dwell_transfer` 는 **연속 dwell 의 `cassette_id` 가 같을 때만** 이송 스텝을 만든다.
- **ATM/VTM 이외 장비·복합 시나리오:** `_classify_transfer_robot` 휴리스틱이 틀리면 로그와 실제 동작이 어긋날 수 있어, CSV·매핑·분기 규칙을 데이터에 맞게 조정해야 한다.

---

## 5. 문제 발생 시 로그 검색 키워드

| 키워드 | 의미 |
|--------|------|
| `[build:atm]` | ATM 매크로 조립 — prim/클립/Z/timeSamples 누락 |
| `[build:vtm]` | VTM 매크로 조립 — prim/클립/Yaw/Z/timeSamples 누락 |
| `[build:transfer]` | CSV dwell 간 이송에서 스텝 0개 |
| `[build:csv_play]` | CSV 전체 런에 `TIMESAMPLES_REPLAY` 0개 |
| `[build:runner]` | 스텝 목록에 `USD_TIMELINE` 포함(수동 JSON 의심) |
| `[LAM/SEQ]` | `lam_sequence_engine` 실행 — prim 미존재·스킵 등 |

---

## 6. 관련 문서

- `LAM_Simulation_Play_User_Config.md` — 필드별 입력 위치.
- `LAM_Equipment_Model.md` — `slot_key`, `module_nm`, 클립 SSOT.
- `lam_sequence_engine.py` 상단 주석 — 스레드·스텝 종류·MOVE 동작 개요.

문서 갱신 시 본 파일의 **§1 구조 표**와 `simulation_play.py` 의 `run_simulation_from_csv` / `run_lam_sim_steps` 시그니처가 어긋나지 않게 맞출 것.
