# LAM 시뮬 CSV·매크로 — 사용자 입력 값 정리

본 문서는 **시뮬 dwell CSV → 가상 타임라인 / 매크로 스텝** 에서 사용자(또는 통합 담당자)가 **직접 바꿔야 하는 값**이 **어느 파일·어느 심볼**에 있는지, **무엇을 넣으면 되는지**만 압축해 정리한다.

> **원칙 (장비 스펙 SSOT)**
> 장비 도메인·슬롯 정의·`module_nm` 표 등은 `LAM_Equipment_Model.md` 를 따른다.
> **시뮬 코드·기본값 수정** 은 원칙적으로 한 파일만:
> `morph/lam_control/simulation_play.py`

---

## 1. 파일·함수 한눈에

| 목적 | 위치 |
|------|------|
| 숫자·USD 경로·클립·Z·Yaw·웨이퍼 템플릿 등 **대부분의 설정** | `simulation_play.py` → **`default_lam_sim_virtual_config()`** 가 반환하는 `LamSimPlayVirtualConfig(...)` 인자 |
| CSV 시간 모드, 논리 슬롯 문자열, VTM 좌우 스왑 플래그 | `simulation_play.py` 상단 상수 블록 (약 97~111행 근처) |
| CSV `module_nm` → 내부 `slot_key` 표 | `simulation_play.py` → **`build_default_module_nm_to_slot_key()`** |
| dwell CSV 파일 | `lam/csv/*.csv` (또는 환경변수 `LAM_SIM_CSV`) |
| 설정 변경 후 캐시 갱신 | `refresh_lam_sim_runtime_tables_from_config()` 호출 |

---

## 2. 상단 상수 블록 (`simulation_play.py`)

| 심볼 | 넣을 값 |
|------|---------|
| `TIME_PARSE_MODE` | 현재 `"seconds_float"` — CSV 시간 열을 초 단위 float 로 읽음. (향후 다른 모드 추가 시 이 문자열만 확장.) |
| `VTM_END_EFFECTOR_SWAP_HANDS` | `True`/`False` — CSV 의 EE1/EE2 가 물리 좌우와 반대로 매핑될 때 스왑. 바꾼 뒤 **`rebuild_module_nm_slot_mapping()`** 호출. |
| `FOUP1_CASSETTE_ID_MIN` / `FOUP1_CASSETTE_ID_MAX` | 랏·카세트 범위 등 정책 숫자 (현재 1~25). |
| `LOGICAL_SLOT_ATM_ARM` 등 | 내부 논리 슬롯 키 문자열. **일반적으로 바꾸지 않음** (다른 코드·CSV 매핑과 연동). |

---

## 3. `default_lam_sim_virtual_config()` — 넣어야 할 것 전체

아래는 **`LamSimPlayVirtualConfig`** 필드와 “무엇을 넣는지” 요약이다. 실제 기본값은 같은 파일의 `default_lam_sim_virtual_config()` 본문을 연다.

### 3.1 로그·Prim (애니 인스턴스)

| 필드 | 넣을 값 |
|------|---------|
| `timeline_log_enabled` | `True`/`False` — `log_virtual_timeline_from_dwells` 상세 로그 출력 여부. |
| `atm_height_prim_path` | ATM **높이 조절**용 Xformable prim 의 **절대 경로** (`MOVE` 스텝이 TBS_OFFSET 으로 Z 이동할 대상). |
| `atm_timesample_prim` | ATM **timeSamples 애니**가 붙은 인스턴스 prim 경로. `TIMESAMPLES_REPLAY` 가 Registry 에서 이 경로로 인스턴스를 찾는다. |
| `vtm_timesample_prim` | VTM 동일. |

### 3.2 ATM 클립 — **물리 `slot_key` 마다** in/out (SSOT)

- **`atm_clip_by_slot_key`** (정식) — 키: **`foup1_7`**, **`buffer3_12`**, **`airlock1_2`** 등 **물리 `slot_key`**. 값: `LamAtmStationClips(pick_from=..., place_to=...)` 각각 `LamClipInOut(frames_in=(a,b), frames_out=(c,d))` — **USD timeCode 정수 프레임** (LAM 은 재생 시 30fps 로 해석). **슬롯마다 서로 다른 구간**을 두는 것이 원칙이다. 슬롯 키·장비 개념은 `LAM_Equipment_Model.md` 와 `simulation_play.py` 의 `LOGICAL_SLOT_*` 규칙을 함께 본다.
- **`atm_clip_by_station`** (폴백) — 키: `foup1`, `buffer3` 등 스테이션 종류. **`atm_clip_by_slot_key`에 해당 슬롯이 없을 때만** `resolve_atm_clips_for_slot_key` 가 여기로 떨어진다. 신규 슬롯만 잠깐 넣을 때 등에 사용.

| 필드 | 넣을 값 |
|------|---------|
| `atm_clip_by_slot_key` | 위 SSOT. `default_lam_sim_virtual_config()` 에서는 `_default_atm_clip_by_slot_key()` 로 FOUP·buffer·cooling·airlock·aligner **전 슬롯**을 채운다(데모용 `base` 간격). |
| `atm_clip_by_station` | 폴백용 소량 dict. 기본값은 동일 데모 클립을 모든 종류 키에 복사해 둔 것. |
| `atm_clip_fallback_station_key` | `atm_clip_by_station` 조회 시 키가 없을 때 쓸 ATM 스테이션 키 (예: `"buffer3"`). |

**실제 숫자가 나오는 코드 위치:**

- `default_lam_sim_virtual_config()` — `atm_clip_by_slot_key=_default_atm_clip_by_slot_key()` 및 폴백 `atm_clip_by_station`.
- 슬롯별 한 벌의 프레임은 **`_atm_slot_clips(base)`** / **`_lam_clip(i,j,k,l)`** (`simulation_play.py`) 로 `(시작,끝)` 네 쌍을 만든다. **실장비**에서는 슬롯마다 USD에서 읽은 정수로 `LamAtmStationClips(...)` 를 직접 채우거나, `_atm_slot_clips` 에 **슬롯마다 다른 `base`** 를 넘기면 된다.

### 3.3 VTM 클립 — **물리 `slot_key` 마다** 좌/우 EE in/out (SSOT)

| 필드 | 넣을 값 |
|------|---------|
| `vtm_clip_by_slot_key` | 키: **`chamber3`**, **`airlock1_1`**, **`airlock1_2`** 등 물리 슬롯. 값: `LamVtmDualEeStationClips` — 좌 `left_pick_from` / `left_place_to`, 우 `right_pick_from` / `right_place_to`, 각각 `LamClipInOut(frames_in, frames_out)`. **에어록은 슬롯 2개가 서로 다른 구간**을 가질 수 있다. `default` 에서는 `_default_vtm_clip_by_slot_key()` 로 chamber 5 + 에어록 4슬롯을 채운다. |
| `vtm_clip_by_station` | 폴백: `chamber1`…`chamber5`, `airlock1`, `airlock2` 유닛 키. 슬롯 dict에 없을 때만 사용. |

매크로 `vtm_arm_move_to_chamber(..., target_slot_key="airlock1_1")` 처럼 **물리 키**를 넘기면 해당 슬롯 기준으로 클립·웨이퍼 prim 을 고른다.

헬퍼 **`_vtm_slot_dual_ee_clips(base)`** 로 데모 한 벌을 만들고, **실장비**에서는 슬롯마다 다른 `base` 또는 수동 `LamVtmDualEeStationClips(...)` 로 `vtm_clip_by_slot_key` 를 채운다.

### 3.4 VTM 회전·높이 stage 및 Yaw (`vtm_arm_move_to_chamber` 스텝)

| 필드 | 넣을 값 |
|------|---------|
| `vtm_rotation_prim_path` | 슬롯을 향할 때 `ROTATE`(절대 Z-Yaw, `rotate_from_initial=True`) 대상 prim. **비우면** 회전 스텝 생략. |
| `vtm_position_prim_path` | 슬롯 높이까지 `MOVE` 의 `dz` 가 적용될 prim. **비우면** VTM Z 스텝 생략. |
| `vtm_z_table_authored_baseline_m` | `vtm_z_slot_delta_m` 작성 시 문서 기준 Z [m] (`zd()` 의 VTM 쪽 `z0` 와 맞출 것). |
| `vtm_z_baseline_applied_m` | (레거시) 예전 방식에서 Kit 적용 기준 Z [m]. **`vtm_z_table_authored_baseline_m` 과 같게 두고** USD 보정은 `vtm_z_usd_world_offset_m` 에만 넣는 것을 권장한다. |
| `vtm_z_usd_world_offset_m` | **(Kit 에서 잰 `vtm_position_prim_path` 기준 Z) − `vtm_z_table_authored_baseline_m`** [m]. 문서에 적은 슬롯 절대 Z·델타는 그대로 두고, USD 좌표계만 어긋날 때 **이 값 하나**로 전 슬롯 `effective_vtm_slot_z_m` 이 같은 만큼 이동한다. |
| `vtm_z_slot_delta_m` | 키: `chamber1`…`chamber5`, `airlock1_1`…, `LOGICAL:VTM_EE_L` / `R` 등. 값: (문서 절대 Z − `vtm_z_table_authored_baseline_m`) [m]. **적용 절대 Z** = `effective_vtm_slot_z_m` = `vtm_z_table_authored_baseline_m + 델타 + vtm_z_total_world_offset_m()` (`vtm_z_total_world_offset_m()` = `vtm_z_usd_world_offset_m` + 레거시 `vtm_z_baseline_applied_m − vtm_z_table_authored_baseline_m`). `vtm_z_slot_delta_m` 에 없으면 **같은 키의** `z_slot_delta_m` 델타를 VTM 문서 기준에 더한 값으로 호환. |
| `lam_sim_z_slot_move_duration_sec` | ATM HeightStage·VTM position prim 의 슬롯 맞춤 `MOVE` 지속시간 [s] (기본 `0.5`). **`0`** 이면 ATM 만 기존 `dz` 기반 자동 길이, VTM 은 여전히 `0.5` 폴백. |
| `lam_sim_rotate_duration_sec` | VTM `ROTATE` 지속시간 [s] (기본 `0.4`). |
| `vtm_orient_yaw_by_slot_and_hand` | `slot_key` → `{"left": 도, "right": 도}` 절대 Yaw. **우선** 사용. |
| `vtm_orient_yaw_deg_by_target_slot` | (호환) 손 구분 없이 한 값 — 위 dict 에 키가 없을 때만 사용. |
| `vtm_orient_idle_rz_deg` | in/out 클립 후 **복귀** `ROTATE` 절대 Z-Yaw (도). 보통 `0`. |

매크로 `vtm_arm_move_to_chamber` 스텝 순서: (설정이 있으면) **Yaw→슬롯** → **Z→슬롯** → timeSamples in → 가시성 → out → **Z 복귀** → **Yaw 대기각**.

### 3.5 Z (ATM — `atm_height_prim_path` / `z_slot_delta_m`)

| 필드 | 넣을 값 |
|------|---------|
| `z_table_authored_baseline_m` | 문서/테이블 작성 시 ATM HeightStage 기준 Z [m] (`zd()` 계산의 기준). |
| `z_baseline_applied_m` | (레거시) 예전 방식에서 Kit 적용 기준 Z [m]. **`z_table_authored_baseline_m` 과 같게 두고** USD 보정은 `atm_z_usd_world_offset_m` 에만 넣는 것을 권장한다. |
| `atm_z_usd_world_offset_m` | **(Kit 에서 잰 `atm_height_prim_path` 기준 Z) − `z_table_authored_baseline_m`** [m]. 문서에 적은 슬롯 절대 Z·델타는 그대로 두고, USD 좌표계만 어긋날 때 **이 값 하나**로 전 슬롯 `effective_slot_z_m` 이 같은 만큼 이동한다. |
| `z_slot_delta_m` | 키: **slot_key** (`foup1_3`, `LOGICAL:ATM_ARM` 등). 값: **(문서상 그 슬롯의 절대 Z − `z_table_authored_baseline_m`)** [m]. |

**적용 절대 Z (ATM)** 는 `effective_slot_z_m(slot_key) = z_table_authored_baseline_m + z_slot_delta_m[slot_key] + atm_z_total_world_offset_m()` 이다. 여기서 `atm_z_total_world_offset_m()` = `atm_z_usd_world_offset_m` + (레거시) `z_baseline_applied_m − z_table_authored_baseline_m`. `atm_arm_to_atm_slot` / `atm_arm_to_foup1` 등은 `lam_sim_z_slot_move_duration_sec > 0` 이면 해당 시간으로 HeightStage `MOVE` 를 넣는다.

### 3.5.1 요약: 문서 절대 Z 를 그대로 두고 USD 만 맞출 때

1. 문서에 나온 **기준 Z** 를 `z_table_authored_baseline_m` / `vtm_z_table_authored_baseline_m` 에 넣고, 각 슬롯의 **문서 절대 Z** 로부터 `zd(절대Z)` 를 계산해 `z_slot_delta_m` / `vtm_z_slot_delta_m` 를 채운다 (기존과 동일).
2. Kit 에서 같은 기준 prim 의 **실측 Z** 를 잰 뒤, **`atm_z_usd_world_offset_m` = 실측 − `z_table_authored_baseline_m`**, **`vtm_z_usd_world_offset_m` = 실측 − `vtm_z_table_authored_baseline_m`** 만 입력한다.
3. `z_baseline_applied_m` / `vtm_z_baseline_applied_m` 은 `*_table` 과 **동일**하게 두면, 총 오프셋은 `*_usd_world_offset_m` 과 일치한다. (예전에 baseline 만 올리던 설정은 그대로 두어도 `*_total_world_offset_m()` 에 합산되어 동작한다.)

### 3.6 웨이퍼 prim 경로 (가시성·매크로)
**한 슬롯씩 100줄 넣는 구조가 아니다.** 템플릿 문자열로 자동 생성한다 (`build_wafer_prim_by_slot_key()`).

| 필드 | 생성되는 slot_key 예 | 넣을 값 |
|------|------------------------|---------|
| `wafer_tmpl_foup1` | `foup1_1` … `foup1_25` | 패턴 문자열. 기본 `{i:02d}` 슬롯 번호. |
| `wafer_tmpl_foup2` / `wafer_tmpl_foup3` | `foup2_*`, `foup3_*` | 동일. |
| `wafer_tmpl_buffer3` / `wafer_tmpl_buffer4` | `buffer3_1` …, `buffer4_1` … | `{slot}` 예: `buffer3_7` 전체가 `{slot}` 로 들어감. |
| `wafer_tmpl_cooling` | `cooling_1` … `cooling_7` | `{i}` |
| `wafer_tmpl_airlock` | `airlock1_1`, `airlock1_2`, `airlock2_1`, `airlock2_2` | `{a}`, `{s}` |
| `wafer_tmpl_chamber` | `chamber1` … `chamber5` | `{i}` |
| `wafer_prim_aligner` | `aligner` | 단일 절대 경로. |
| `wafer_prim_atm_arm` | `LOGICAL:ATM_ARM` | ATM 팔 끝 웨이퍼 prim. |
| `wafer_prim_vtm_hand_l` / `wafer_prim_vtm_hand_r` | `LOGICAL:VTM_EE_L` / `R` | VTM 좌·우 손 웨이퍼 prim. |

**장면 USD 경로가 데모와 다르면** 위 문자열만 실제 prim 경로에 맞게 수정한다.

---

## 4. `build_default_module_nm_to_slot_key()` — CSV `module_nm`

실장비 CSV 의 **`module_nm` 문자열**이 내부 **`slot_key`** 로 바뀌는 표이다. 새 모듈명이 생기면 **이 함수 안에만** 행을 추가한다. (장비 도메인·슬롯 개념은 `LAM_Equipment_Model.md` — 표준 문자열 목록은 코드 `build_default_module_nm_to_slot_key()` 가 사실상의 SSOT.)

`VTM_END_EFFECTOR_SWAP_HANDS` 를 바꾼 뒤에는 **`rebuild_module_nm_slot_mapping()`** 을 호출해야 `MODULE_NM_TO_SLOT_KEY` 가 갱신된다.

---

## 5. CSV 데이터 (`lam/csv`)

| 항목 | 설명 |
|------|------|
| 경로 | 기본 `lam/csv/` (확장 내 `get_lam_csv_dir()`). |
| 기본 파일 | `lam/csv/wafer01_tour_v1.csv` 가 있으면 우선, 없으면 폴더 내 첫 `*.csv`. |
| 환경변수 | `LAM_SIM_CSV` — 절대 또는 상대 경로로 다른 CSV 지정 가능. |
| 열 | `eqp_id`, `module_nm`, `cassette_id`, `eqp_start_tm`, `eqp_end_tm`, `process_tm` (상세는 `read_csv_rows` docstring). |
| **Play 재생** | `run_simulation_from_csv` 는 dwell 을 시간순으로 읽은 뒤, **인접 dwell 쌍**(동일 `cassette_id`)마다 `build_steps_for_dwell_transfer` → `atm_arm_to_atm_slot` / `vtm_arm_move_to_chamber` 와 동일 규칙의 스텝을 합쳐 `LamSequenceRunner.run` 으로 USD 를 구동한다. dwell 이 1개뿐이면 이송 스텝이 없다. |

---

## 6. 설정을 바꾼 뒤 반드시 (또는 권장)

| 작업 | 함수 |
|------|------|
| `LAM_SIM_VIRTUAL_CONFIG` 의 필드를 코드에서 수정한 뒤 | `refresh_lam_sim_runtime_tables_from_config()` — `WAFER_PRIM_BY_SLOT_KEY`, `SLOT_Z_METERS`, `ATM_HEIGHT_PRIM_PATH` 갱신. |
| `VTM_END_EFFECTOR_SWAP_HANDS` 변경 후 | `rebuild_module_nm_slot_mapping()` |

---

## 7. 매크로·스크립트 (Kit UI)

| 항목 | 위치 |
|------|------|
| CSV 재생 창 + 스크립트 에디터 | `LamSimulationCsvPlayWindow` (`simulation_play.py` 하단). |
| 한 줄 매크로 호출 실행 | `run_lam_sim_script_line` / `run_lam_sim_script_text`. |
| 스텝만 실행 | `run_lam_sim_steps(registry, scheduler, steps, target_duration_sec=...)`. |
| 마지막으로 빌드된 스텝 JSON 문자열(참고용) | 전역 `LAM_SIM_LAST_BUILT_JSON` (`_lam_sim_publish_json` 이 갱신). |

허용되는 매크로 이름은 `LAM_SIM_MACRO_CALLABLES` dict 에 등록된 것만이다.

**프레임 재생 정책:** `simulation_play.py` 가 만드는 스텝의 애니 구간은 **`TIMESAMPLES_REPLAY`** 만 사용한다 (`_lam_ts_step`). 마스터 스테이지 `USD_TIMELINE` 으로 프레임을 스크럽하지 않는다(엔진의 타임라인 기능 자체를 막는 것은 아님). 수동 JSON 에 `USD_TIMELINE` 이 있으면 `run_lam_sim_steps` 가 경고 로그를 남긴다.

---

## 8. 시퀀스 러너와의 관계

- 스텝 dict 리스트 형식은 **`LamSequenceRunner.run(steps)`** 및 시퀀스 에디터 JSON 과 **같은 계열**이다.
- 새 스텝 종류 **`SET_PRIM_VISIBILITY`** 는 `lam_sequence_engine.py` 에 정의되어 있다.

---

## 9. 관련 문서

- `LAM_Equipment_Model.md` — 장비 개념·VTM/ATM 구조·TBS 비교. **시뮬 숫자·경로·클립 SSOT** 는 `simulation_play.py` (`LAM_Simulation_Play_User_Config.md` 참고).
- `USD_Timeline_Spec.md` / 시퀀스 스키마 — `ref` 의 `prim_path`·Registry 매칭 등 (REQ-006).
- **`LAM_Simulation_Play_Field_Test_Guide.md`** — 실 USD·실 데이터 기준 **현재 코드로 할 수 있는 테스트** 절차·전제 조건.

---

## 10. 사용자 요구 원문 (prompt) 및 `simulation_play.py` 대응 위치

아래 **번호 목록은 `prompt1.txt` (45–66행) 원문을 그대로** 옮긴 것이다.
실제 값은 **`simulation_play.py`** 안에서만 수정한다. 관례상 **`LamSimPlayVirtualConfig` 데이터클래스 필드 선언**과 **`default_lam_sim_virtual_config()` 가 `LamSimPlayVirtualConfig(...)` 에 넘기는 인자** 두 곳을 보면 된다. (런타임에 `LAM_SIM_VIRTUAL_CONFIG.xxx = ...` 로 바꿀 수도 있으나, 기준은 이 함수다.)
줄 번호는 **현재 저장소 기준**이며, 편집으로 밀릴 수 있으니 **함수명·필드명**으로 찾는 것을 권장한다.

### 10.1 원문 (그대로)

1. 각 인스턴스 timesamples 용 instance 경로 (지금은 vtm, atm두개)
2. 각 슬롯에 위치한 모든 wafer 들의 prim 경로 및 로봇팔에 위치한 wafer prim 경로
3. atm 기기가 각 슬롯으로 갔다가 원위치 하는 모든 frame 시작과 끝 범위
   (foup 3개, aligner, coolstation, buffer3과 buffer4, 그리고 airlock 두개  전부 in/out 프레임
   이 구분되어있고 사용자가 입력가능해야함)
4. vtm 기기가 각 슬롯으로 갔다가 원위치 하는 모든 frame 시작과 끝 범위
   (5개 chamber 와 2개의 airlock으로 총 14개의 in/out 프레임을 입력하지만 로봇팔의 우측, 좌측까지
   있으므로 총 28개의 in/out 프레임을 입력해야함)
5. vtm 기기가 각 슬롯으로 넣을 수 있는 위치까지 회전하는 절대 각도
   (마찬가지로 5개 chamber 및 2개의 airlock 을 각각 좌 우로 넣을 위치로 이동하는 개념이므로 총 14개의 절대 각도 입력이 필요)
6. z축 회전하는 축이 되어주는 vtm 기기의 rotation prim path (5번 내용과 연관)
7. z축 이동하는 기준이 되는 vtm 기기의 position prim path
8. 최초 vtm 의 position prim path 객체의 최초 높이 z축 절대값
9. vtm 연결된 airlock z축값(각 2개씩 총 4개),  5개의 chamber 축 절대값
   (8번을 기준으로 각 슬롯으로 로봇팔이 회전할 때 함께 z축 값을 각 슬롯의 z축 위치까지 절대값으로 이동(0.5초))
10. z축 이동하는 기준이 되는 atm 기기의 position prim path
11. 최초 atm 의 position prim path 객체의 최초 높이 z축 절대값
12. atm 연결된 airlock z축값(각 2개씩 총 4개),  buffer3, 4 (각 25개), coolstation(7개), foup1, 2, 3(각 25개)
    (11번을 기준으로 각 슬롯으로 로봇팔이 이동하는 timesamples 재생을 할 때 z축 값을 각 슬롯의 z축 위치까지 절대값으로 이동(0.5초))

### 10.2 번호별 — `simulation_play.py` 어디를 보면 되는지

| 번호 | 대응 필드·구조 | 파일에서 찾는 방법 (대략 줄 번호) |
|------|----------------|-------------------------------------|
| **1** | ATM / VTM timeSamples **인스턴스 prim 경로** | 클래스 필드 `atm_timesample_prim`, `vtm_timesample_prim` (`LamSimPlayVirtualConfig` 근처 **264–266행**). 기본값 문자열은 **`default_lam_sim_virtual_config()`** 의 `LamSimPlayVirtualConfig(...)` 인자 **501–502행** 부근. |
| **2** | 슬롯별 wafer prim + 팔 끝 wafer prim | 클래스 **`wafer_tmpl_*`**, **`wafer_prim_aligner`**, **`wafer_prim_atm_arm`**, **`wafer_prim_vtm_hand_l` / `r`** (**306–315행** 및 **303–305행**). 기본 경로는 **`default_lam_sim_virtual_config()`** **521–532행** 부근. 최종 맵은 메서드 **`build_wafer_prim_by_slot_key()`** (**335행** 이후). |
| **3** | ATM 슬롯마다 in/out **프레임** (pick/place 각 in·out) | 정식 테이블: **`atm_clip_by_slot_key`** (**268행**). 슬롯별 값 타입: **`LamAtmStationClips`** / **`LamClipInOut`** (정의 **124–145행**). 데모 전수 생성: **`_default_atm_clip_by_slot_key()`** (**395행** 이후), 한 슬롯 분량 헬퍼 **`_atm_slot_clips`**, 프레임 네 쌍 **`_lam_clip`** (**364–377행**). `default` 에서는 **`atm_clip_by_slot_key=_default_atm_clip_by_slot_key()`** (**503행**). |
| **4** | VTM 슬롯마다 in/out 프레임 (좌·우 EE) | 정식: **`vtm_clip_by_slot_key`** (**272행**). 타입 **`LamVtmDualEeStationClips`** (**147행** 이후): 슬롯마다 `left_pick_from` / `left_place_to` / `right_pick_from` / `right_place_to` 각각 `LamClipInOut`(frames_in, frames_out). 데모: **`_default_vtm_clip_by_slot_key()`** (**418행** 이후), 헬퍼 **`_vtm_slot_dual_ee_clips`** (**381–392행**). `default` 의 **`vtm_clip_by_slot_key=_default_vtm_clip_by_slot_key()`** (**506행**). 원문의 “14 / 28”은 **슬롯·손 조합 세는 방식**과 다를 수 있음 — 코드 SSOT는 **물리 `slot_key`당 위 네 클립** (기본 데모는 chamber 5 + 에어록 슬롯 4). |
| **5** | VTM 목표까지 **절대 Yaw**(손별) | **`vtm_orient_yaw_by_slot_and_hand`** (**282행**, `slot_key` → `{"left","right"}`). 호환용 flat: **`vtm_orient_yaw_deg_by_target_slot`** (**280행**). 기본 dict: **`default_lam_sim_virtual_config()`** 내 **`_y_flat` / `_y_by_hand`** 및 인자 **`vtm_orient_yaw_*`** (**461–472행**, **508–509행**). 매크로에서 읽는 함수: **`_vtm_yaw_deg_for_slot_hand`** (**1116행** 이후). |
| **6** | VTM **회전축** prim path | **`vtm_rotation_prim_path`** (**284–285행**). 기본값 **510행** 부근. |
| **7** | VTM **Z 이동** 기준 prim path | **`vtm_position_prim_path`** (**285행**). 기본값 **511행** 부근. |
| **8** | VTM position 기준 **초기 Z** (문서·적용 baseline) | **`vtm_z_table_authored_baseline_m`**, **`vtm_z_baseline_applied_m`** (레거시), **`vtm_z_usd_world_offset_m`** — `LamSimPlayVirtualConfig` **약 287–292행**. 기본값 **`default_lam_sim_virtual_config()`** **548–551행** 부근. 통합 오프셋 메서드 **`vtm_z_total_world_offset_m()`** **약 335–342행**. `zd()` 는 **460행** 이후. |
| **9** | 에어록·chamber 등 **VTM 쪽 슬롯 Z** + 0.5초 이동 | 슬롯 델타 **`vtm_z_slot_delta_m`**, 적용 **`effective_vtm_slot_z_m`** (**약 354–365행**). 기본 **`_vtm_z_slot_delta`** / 인자 **551행**. **`lam_sim_z_slot_move_duration_sec`**, **`vtm_arm_move_to_chamber`** (**883행** 이후). |
| **10** | ATM **Z 이동** 기준 prim path | **`atm_height_prim_path`** (`LamSimPlayVirtualConfig` 상단, **약 262행**). 기본 **538행** 부근. |
| **11** | ATM **초기 Z** (문서·적용 baseline) | **`z_table_authored_baseline_m`**, **`z_baseline_applied_m`** (레거시), **`atm_z_usd_world_offset_m`** — **약 300–306행**. 기본 **554–556행** 및 **`z0`**, **`zd()``** (**474행** 이후). 통합 오프셋 **`atm_z_total_world_offset_m()`** **약 324–332행**. |
| **12** | ATM 구간 슬롯별 Z (에어록·buffer·cooling·FOUP …) + 0.5초 | **`z_slot_delta_m`** (**308행**). 적용 **`effective_slot_z_m`** (**344행** 이후). 기본 **`_z_slot_delta` → `z_slot_delta_m`** (**557행**). Z `MOVE` 시간 **`lam_sim_z_slot_move_duration_sec`**. 스텝 **`atm_arm_to_atm_slot`** (**760행** 이후), FOUP **`atm_arm_to_foup1`** (**855행** 근처). |

**공통:** 설정을 코드 밖에서 바꾼 뒤 런타임 캐시를 맞추려면 **`refresh_lam_sim_runtime_tables_from_config()`** (**582행** 이후) 호출. (문서 §6 표 참고.)

**VTM 회전 시간(초):** **`lam_sim_rotate_duration_sec`** (**299행**, 기본 **`default_lam_sim_virtual_config()`** **553행** 부근).

---

*문서 생성: 시뮬 사용자 입력 위치 정리. 코드 변경 시 본 문서의 섹션 번호·필드명·줄 번호를 `simulation_play.py` 와 맞춰 갱신할 것.*
