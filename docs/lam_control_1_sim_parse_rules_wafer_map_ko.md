# LAM Control 1 — 시뮬 파싱 · 규칙 JSON 재배치 · Wafer 번호 맵 (실무 AI용)

> 작성: 2026-08-04 · **갱신: 2026-08-05**  
> 대상: Cursor 없이 일반 AI만 쓰는 실무 환경  
> 목적: **어느 파일의 어느 함수를 고치면 되는지** 바로 찾을 수 있게 현재 구현을 상세 기록  
> 확장 루트: `source/extensions/morph.lam_control_1/morph/lam_control_1/`  
> 관련 체크리스트: `docs/lam_control_1_sim_plan_structural_fix_checklist_ko.md`

이 문서를 AI에 붙여 넣고 “OO 증상 → 어느 파일을 보라”고 물으면 아래 표·파이프라인을 근거로 답하게 한다.

---

## 0. 한 줄 정책 (절대)

1. **실무 CSV/EAP 데이터는 버그가 없는 한 파싱 결과 순서 그대로 재생**한다.
2. **고정 규칙만** plan에 얹는다 (그 외 ATM/VTM 점유·순서 “보정”은 기본 OFF).
   - **Aligner** (EAP에 없음 → 합성 + 절대규칙)
   - **투어 끝 AtmArm → FOUP place** (파싱 dwell 투어 마지막이 `LOGICAL:ATM_ARM`이면 place 합성으로 wafer 공정 종료. airlock/buffer/cooling 등 pick 출처 무관)
   - ~~Buffer pick → FOUP place 강제 삽입~~ **폐지 (2026-08-05)** — 위 AtmArm 끝 규칙으로 통일. `_ensure_buffer_to_foup_absolute_rules` 는 no-op.
3. 기본 plan 모드: `CSV_PLAYBACK_PLAN_MODE = "aligner_fix"`  
   (`lam_sim_control_defaults.py`)  
   → Aligner ON, occupancy swap / visibility time-shift **OFF**.  
   `full_occ_correct` 는 **쓰지 않는 것**이 원칙(번호 꼬임·순서 변조 원인).
4. Wafer 번호는 visibility 실행 시 `_lam_wafer_label_ctx.wafer_label`(cassette) 가 SSOT.  
   평면도와 3D 라벨은 **같은 버스**를 본다.

---

## 1. 전체 파이프라인 (어디가 무엇을 하나)

```
CSV / Federation / GET URL
        │
        ▼
[A] 파싱 → DwellRecord[]          simulation_play.py
        │
        ▼
[B] build_csv_playback_plan       simulation_play.py
   · dwell / FOUP pick / dwell간 transfer
   · 투어 끝 AtmArm → FOUP place 합성   ← SSOT (2026-08-05)
   · Aligner 합성 (FOUP pick 직후)
        │
        ▼
[C] _apply_csv_playback_arm_serial_rules
   · aligner_fix / raw → **no-op**
   · full_occ_correct 만 swap
        │
        ▼
[D] _enforce_aligner_absolute_rules
   · Aligner pick 직후 비-airlock 이면 pick 제거
        │
        ▼
[E] _ensure_buffer_to_foup_absolute_rules   ← **no-op (2026-08-05)**
   · 예전 Buffer→FOUP 강제 삽입/제거는 폐지
   · FOUP 반환은 [B] 투어 끝 AtmArm 합성만
        │
        ▼
[F] (선택) _maybe_apply_occupancy_scheduler
   · full_occ_correct 만 — aligner_fix 에서는 **호출되어도 즉시 return**
        │
        ▼
CachedCsvPlayback (schedule + blocks)
        │
        ▼
[G] JSON 블록 실행 (lam_sequence_engine)
   PRIM_VISIBILITY → notify_wafer_visibility_applied
        │
        ├─► 평면도 점유     lam_floorplan_occupancy.py
        └─► 3D 번호 라벨   lam_wafer_viewport_labels.py
        │
        ▼
FOUP 상태보기 집계         lam_viewport_overlay_state.record_foup_event_…
```

---

## 2. 파일 SSOT 표 (수정할 때 여기부터)

| 관심사 | 파일 | 핵심 심볼 | AI에게 시킬 일 |
|--------|------|-----------|----------------|
| Plan 모드 스위치 | `lam_sim_control_defaults.py` | `CSV_PLAYBACK_PLAN_MODE` | 기본값 `aligner_fix` 유지. `full_occ_correct`로 바꾸면 안 됨(보정 켜짐). |
| 모드 해석 | `lam_csv_occupancy_scheduler.py` | `csv_playback_plan_mode`, `occupancy_scheduler_enabled`, `aligner_synthesis_enabled` | `occupancy_scheduler_enabled()`는 **full만 True**. Aligner는 raw만 OFF. |
| CSV→dwell 파싱 | `simulation_play.py` | `load_csv_dwell_timeline`, `ParsedCsvRow`, `DwellRecord`, `parse_module_nm_to_slot_key`, `build_lot_id_to_foup_index` | module_nm→slot_key, lot→FOUP 번호, cassette 번호. **여기 틀리면 이후 전부 꼬임.** |
| Plan 빌드 | `simulation_play.py` | `build_csv_playback_plan`, `build_csv_playback_schedule` | dwell/transfer/FOUP/Aligner 블록 생성. 끝에서 D→E 호출. |
| dwell 간 이송 JSON | `simulation_play.py` | `build_steps_for_dwell_transfer` | prev→curr dwell 홉 → `atm_*`/`vtm_*` 이벤트. **데이터 그대로.** |
| 이벤트 JSON 조립 | `lam_event_sequences.py` | `build_steps_for_event`, `_normalize_pick_place_visibility_modes` | Z MOVE + JSON 스텝 + visibility hide/show 정규화 + label ctx 부착. |
| Aligner 규칙 SSOT | `lam_aligner_process_rules.py` | `resolve_aligner_pick_schedule`, `find_first_airlock_place_time_in_tour`, `is_airlock_place_event` | **규칙 문구 변경은 이 파일만.** |
| Aligner 삽입 | `simulation_play.py` | `_append_aligner_after_foup_pick`, `_resolve_synth_aligner_pick_time`, `build_aligner_after_foup_pick_steps` | FOUP pick 직후 place, airlock 직전 pick. |
| Aligner enforce | `simulation_play.py` | `_enforce_aligner_absolute_rules` | pick 직후 금지 place면 **pick 제거**. |
| **투어 끝 AtmArm → FOUP place** | `simulation_play.py` | `build_csv_playback_plan` / `build_csv_playback_schedule` 내 `last.slot_key == LOGICAL_SLOT_ATM_ARM`, `_place_schedule_entry`, `build_foup_pick_place_steps` | **파싱 투어 마지막이 AtmArm이면 place 합성 (공정 종료 SSOT).** 시각=`last.end_sec`. |
| Buffer→FOUP (레거시) | `lam_buffer_return_rules.py` | `is_buffer_pick_event`, `is_foup_place_event`, … | 헬퍼만 유지. **강제 삽입 규칙 문구는 폐기.** |
| Buffer→FOUP enforce | `simulation_play.py` | `_ensure_buffer_to_foup_absolute_rules` | **Deprecated no-op (2026-08-05).** |
| cassette stamp | `lam_wafer_viewport_labels.py` + `simulation_play.py` | `stamp_wafer_cassette_label_on_steps` | transfer/FOUP/Aligner 스텝에 `wafer_label=02d`. |
| 재생 중 visibility | `lam_sequence_engine.py` | PRIM_VISIBILITY 처리, `notify_wafer_visibility_applied` | ctx 없으면 번호/평면 동기화 안 됨. |
| 버스 | `lam_visibility_occupancy_bus.py` | `notify_wafer_visibility_applied` | 평면도 + 라벨 트래커 fan-out. **시뮬 시각/스케줄 변경 금지.** |
| 3D 번호 | `lam_wafer_viewport_labels.py` | `WaferNumberLabelTracker.on_visibility` | pick hide slot / show arm, place 반대. |
| 평면도 점유 | `lam_floorplan_occupancy.py` | `FloorplanOccupancy.on_visibility` | 버스와 동일 의미. |
| FOUP 완료 카운트 | `lam_viewport_overlay_state.py` | `record_foup_event_from_schedule_entry` | `atm_foup{n}_place` 실행 시작 시 done +1. |
| FOUP 3D 패널 | `lam_viewport_foup_status_3d.py` | `get_foup_counts` 표시 | 집계 로직 없음. |
| 슬롯→wafer prim | `lam_wafer_prim_paths.py` | `load_wafer_prim_by_slot_key` | prim 경로 틀리면 hide/show·번호 유실. |
| 점유 swap (비활성) | `simulation_play.py` | `_apply_slot_occupancy_order_swaps`, `_apply_arm_holding_order_swaps`, `_swap_action_order` | **full만**. 건드리면 번호/순서 꼬임. |
| 캐시 무효화 태그 | `simulation_play.py` | `_csv_playback_config_tag` | plan 모드·`atm_end_foup=1` 포함. 규칙 바꾸면 태그 bump. |

---

## 3. 파싱 (A) — 무엇을 확인하고 어디를 고치나

### 3.1 진입

- **함수**: `load_csv_dwell_timeline(path)` (`simulation_play.py`)
- **출력**: `List[DwellRecord]`
  - `lot_id`, `cassette_slot`, `foup_index`, `module_nm`, `slot_key`, `start_sec`, `end_sec`, …

### 3.2 module_nm → slot_key

- `build_default_module_nm_to_slot_key` / `parse_module_nm_to_slot_key`
- CoolStationAL3/4 → `buffer3_*` / `buffer4_*` 등 매핑.
- **증상**: 잘못된 스테이션으로 이송 JSON이 잡힘 → **이 매핑만** 수정. transfer 쪽 “보정” 금지.

### 3.3 lot → FOUP 번호

- `build_lot_id_to_foup_index` — lot 등장 순 → FOUP1..3
- FOUP place 합성 시 투어의 `foup_index`(첫 dwell) 사용.

### 3.4 투어 그룹

- `_group_dwell_tours(dwells)` — `(lot_id, cassette_slot)` 별 시간순 dwell
- 투어 첫 dwell이 `LOGICAL:ATM_ARM` → FOUP pick 합성
- **투어 끝이 `LOGICAL:ATM_ARM` → FOUP place 합성 (wafer 공정 종료 SSOT, 2026-08-05 통일)**  
  - 의미: 파싱 결과상 ATM이 해당 wafer를 들고 끝나고, 그 wafer의 후속 dwell이 없음 → FOUP 반환  
  - pick 출처(airlock/buffer/cooling 등)와 무관  
  - 삽입 시각 = `last.end_sec` (직전 AtmArm dwell 끝 = 이전 공정 직후)
- 연속 dwell 사이 → `build_steps_for_dwell_transfer`

**AI 지시 예**: “cassette가 다른 wafer로 붙는다” → dwell 파싱·`cassette_slot` 컬럼 매핑·투어 키를 먼저 검증. occupancy swap부터 보지 말 것.  
**증상 “ATM이 wafer 든 채 다음 동작”**: 해당 wafer 투어 `last.slot_key`가 AtmArm인지 확인. AtmArm이 아니면 FOUP place가 안 붙음 → 파싱/CSV 행(AtmArm module_nm)부터 볼 것.

---

## 4. Plan 빌드 & JSON 재배치 (B~E)

### 4.1 `build_csv_playback_plan` 끝부분 호출 순서 (중요)

파일: `simulation_play.py`

1. `_apply_csv_playback_arm_serial_rules` — **aligner_fix이면 즉시 return**
2. `_enforce_aligner_absolute_rules`
3. `_ensure_buffer_to_foup_absolute_rules` — **no-op (2026-08-05)**. FOUP 반환은 빌드 루프의 투어 끝 AtmArm 합성.

동일 호출 골격이 `build_csv_playback_schedule`(메타)와, `full_occ_correct`일 때 `_maybe_apply_occupancy_scheduler` 안에도 있다 (E는 no-op).

### 4.2 Aligner (합성 + enforce)

**규칙 문서/코드 단일 출처**: `lam_aligner_process_rules.py`

| 규칙 | 구현 위치 |
|------|-----------|
| FOUP pick 직후 Aligner place 세트 | `_append_aligner_after_foup_pick` |
| Aligner pick = airlock place 직전 (끼어듦 허용) | `resolve_aligner_pick_schedule` + `_other_wafer_atm_action_times_in_window` |
| airlock 홉 없으면 pick 미삽입 | `find_first_airlock_place_time_in_tour` → None |
| pick 직후 buffer/FOUP/cooling 등 → pick 제거 | `_enforce_aligner_absolute_rules` |

**고칠 때**

- 규칙 문구 변경 → `lam_aligner_process_rules.py`만
- 삽입 시각 상수 → `simulation_play.py`의 `FOUP_PICK_SYNTH_ALIGNER_*_SEC`
- enforce 조건 → `_enforce_aligner_absolute_rules` + rules의 `is_*` helper

**하지 말 것**: Aligner를 occupancy swap으로 “맞추기”.

### 4.3 투어 끝 AtmArm → FOUP place (SSOT, 2026-08-05)

**규칙**

1. `_group_dwell_tours`로 wafer별 투어를 만든다.
2. `tour[-1].slot_key == LOGICAL:ATM_ARM` 이면 `build_foup_pick_place_steps(..., pick_or_place="place")` 로 FOUP place를 **합성**.
3. 스케줄 시각 = `last.end_sec` (AtmArm dwell 종료 직후).
4. airlock / buffer / cooling 등 **어디서 pick했는지와 무관** — 마지막이 팔에 있으면 반환.

**수정 위치**

- `simulation_play.build_csv_playback_plan` / `build_csv_playback_schedule` 의 `if last.slot_key == LOGICAL_SLOT_ATM_ARM`
- 메타/본문 문구: `_place_schedule_entry` / `_place_schedule_entry_meta`

**하지 말 것**: Buffer pick 전용으로 다시 강제 삽입·사이 place 제거 로직을 부활시키기 (폐지됨).

### 4.3b Buffer → FOUP (폐지 · 레거시)

**2026-08-05 폐지.** 예전 동작(문서 보존용):

1. ~~`atm_buffer*_pick` 후 같은 wafer FOUP place 검색~~
2. ~~없으면 pick+0.05s에 강제 삽입~~
3. ~~Buffer~FOUP 사이 비-FOUP place 제거~~

코드: `_ensure_buffer_to_foup_absolute_rules` = **Deprecated no-op**.  
`lam_buffer_return_rules.py` = 이벤트명 헬퍼만 유지.

### 4.4 Occupancy / arm serial (기본 끔)

- `_apply_csv_playback_arm_serial_rules` → `occupancy_scheduler_enabled()` False면 no-op
- `_maybe_apply_occupancy_scheduler` → 동일
- 내부: `_apply_slot_occupancy_order_swaps`, `_apply_arm_holding_order_swaps`

**증상 “보정 켠 뒤에만 번호 꼬임”** → `CSV_PLAYBACK_PLAN_MODE`가 `full_occ_correct`인지 먼저 확인. 실무는 `aligner_fix`.

---

## 5. Wafer 번호 동기화 (G) — 꼬이면 보는 순서

### 5.1 번호가 붙는 시점 (빌드)

1. `lam_event_sequences.build_steps_for_event`  
   → `make_wafer_label_step_context`로 `_lam_wafer_label_ctx` 생성  
   → `_wafer_label_from_event_slot`로 임시 라벨(슬롯 번호)
2. `simulation_play`에서 호출 직후  
   → `stamp_wafer_cassette_label_on_steps(steps, cassette_slot)`  
   → **CSV cassette를 `wafer_label`로 덮어씀** (진짜 SSOT)

적용되는 빌드 경로:

- `build_steps_for_dwell_transfer`
- `build_foup_pick_place_steps` (투어 끝 AtmArm FOUP place 포함)
- `build_aligner_after_foup_pick_steps`

**스케줄 행 식별**: `CsvPlaybackScheduleEntry.lot_id` / `cassette_slot`  
(pick/place/transfer/Aligner/meta 모두 필드 채움. title 파싱은 fallback — `_schedule_entry_lot_cassette`)

### 5.2 재생 시

1. `lam_sequence_engine`이 PRIM_VISIBILITY 적용 후  
   `notify_wafer_visibility_applied(paths, visible, label_ctx, screen=…)`
2. 버스가
   - `lam_floorplan_occupancy.FloorplanOccupancy.on_visibility`
   - `lam_wafer_viewport_labels.WaferNumberLabelTracker.on_visibility`
   에 **동일 ctx** 전달

### 5.3 꼬임 진단 체크리스트 (AI용)

| 순서 | 확인 | 파일 |
|------|------|------|
| 1 | plan 모드가 `aligner_fix`인가? | `lam_sim_control_defaults.py` |
| 2 | 해당 이벤트 스텝에 `_lam_wafer_label_ctx.wafer_label`이 cassette인가? | 빌드 로그 / stamp 호출 여부 |
| 3 | JSON visibility mode가 pick=hide SLOT/show ARM 인가? | `lam_event_sequences._normalize_pick_place_visibility_modes` |
| 4 | prim 경로가 슬롯맵과 맞는가? | `lam_wafer_prim_paths.py` |
| 5 | 버스 예외로 한쪽만 갱신되나? | `lam_visibility_occupancy_bus.py` (예외 swallow) |
| 6 | FOUP 완료만 문제? | place 이벤트명 `atm_foup{n}_place` + `record_foup_event_from_schedule_entry` |

**원칙**: 번호 꼬임을 occupancy swap으로 “고치지” 말 것. 파싱·stamp·visibility·prim 경로를 고친다.

---

## 6. FOUP 상태보기 (완료 카운트)

- 집계: `lam_viewport_overlay_state.record_foup_event_from_schedule_entry`
- 호출: CSV 재생 JSON 블록 시작 시 (`simulation_play` 쪽, schedule entry 전달)
- 패턴: `^atm_foup([1-3])_(pick|place)$`
- Buffer 강제 삽입 place도 동일 이벤트명이므로 **실행되면 done +1**

표시만: `lam_viewport_foup_status_3d.py`

---

## 7. 캐시

- 키: `_csv_cache_key` = path + mtime + size + `_csv_playback_config_tag()`
- 태그 예: `plan=aligner_fix|buf_foup=1`
- 규칙/모드 바꾼 뒤 옛 plan이 재생되면 → Kit 재로드 또는 CSV 캐시 무효(파일 touch / 모드 태그 변경).

---

## 8. 실무 AI에게 붙일 짧은 프롬프트 템플릿

아래를 이 문서와 함께 넣고 질문한다.

```
프로젝트: morph.lam_control_1
정책: CSV 파싱 결과 순서 유지. 보정은 Aligner + Buffer→FOUP 만.
모드: CSV_PLAYBACK_PLAN_MODE=aligner_fix (full_occ_correct 금지).
맵 문서: docs/lam_control_1_sim_parse_rules_wafer_map_ko.md

증상: <여기에 증상>
요청: (1) 관련 파일·함수를 맵 문서 표에서 집어 주고
      (2) Aligner/Buffer/파싱/visibility 중 어디 계층 문제인지 구분
      (3) occupancy swap 패치 제안 금지
      (4) 수정 후보 diff 범위만 제안
```

---

## 9. 화면 2개 독립 시뮬 (듀얼 스크린)

실무에서 화면1·화면2가 **서로 다른 CSV/API 파싱 결과**로 동시에 Play 해도
간섭하지 않도록 설계되어 있다. AI가 듀얼 버그를 고칠 때 **이 절을 먼저** 본다.

### 9.1 모델 한 줄

| 화면 | USD | Registry/Scheduler | CSV 창 | Play 세션 |
|------|-----|--------------------|--------|-----------|
| 1 | default context / main stage | `lam_window._registry/_scheduler` | `_csv_sim_windows[1]` | `CsvPlayScreenSession(screen=1)` |
| 2+ | aux context (`morph_lam_split_aux_*`) | `SplitScreenRuntime` per screen | `_csv_sim_windows[si]` | `CsvPlayScreenSession(screen=si)` |

파싱·Aligner·Buffer→FOUP plan 빌드는 **프로세스 공용 순수 함수**이다.
화면별로 “다른 plan”이 생기는 이유는 **창마다 다른 CSV 경로/캐시 키로
`build_and_cache_csv_playback`를 호출**하기 때문이지, 파서가 screen을 몰라도 된다.

### 9.2 격리되어 있는 것 (수정 시 screen= 유지)

| 관심사 | 파일 | 심볼 | 비고 |
|--------|------|------|------|
| Play 세션 | `lam_csv_play_screen.py` | `_sessions`, `CsvPlayScreenSession`, `csv_play_screen_binding` | stop/pause/progress/epoch/workers |
| ContextVar | 동상 | `_csv_play_screen_ctx`, `current_csv_play_screen` | worker 스레드에 화면 번호 |
| Stage/VP | 동상 · `lam_csv_screen_runtime.py` | `get_stage_for_screen`, `resolve_csv_screen_runtime` | 화면2+는 화면1 stage로 **폴백 금지** |
| Split runtime | `lam_split_composed_loader.py` | `get_split_runtime_for_screen` | aux registry/scheduler |
| SequenceRunner | `lam_sequence_engine.py` | `play_screen`, `_usd_context_name` | visibility bus에도 `screen=si` |
| Occupancy | `lam_floorplan_occupancy.py` | `get_floorplan_occupancy(screen)` | |
| Wafer 번호 | `lam_wafer_viewport_labels.py` | `get_wafer_label_tracker(screen)` | |
| FOUP 집계 | `lam_viewport_overlay_state.py` | `_foup_counts_by_screen`, `record_foup_*(..., screen=)` | |
| Overlay UI | `lam_window.py` | `_foup_status_3d_by_screen` 등 | 패널 인스턴스 맵 |
| Anim stop (정상 경로) | `lam_csv_play_execution.py` | `stop_csv_play_motion_for_screen` | **context별** 중지 |
| Stop 가드 | `simulation_play.py` | `_other_csv_play_screens_active`, `request_stop_csv_playback` | 다른 화면 살아 있으면 `scheduler.stop_all` 억제 |

### 9.3 Play / Stop / Pause 흐름 (AI용)

```
창 N 의 Play
  → csv_play_screen_binding(N)          # ContextVar=N
  → resolve_csv_screen_runtime(..., N) # stage/reg/sch/vp for N
  → build/캐시 plan (CSV 경로 별)
  → run_csv_timed_playback(..., play_screen=N)
  → SequenceRunner(play_screen=N, usd_context_name=aux|None)

창 N 의 Stop
  → request_stop_csv_playback(screen=N)
  → stop_csv_play_motion_for_screen(N)   # stop_all 금지
  → reset_csv_play_stop_initial_state(screen=N)
  → reset_all_foup_counts(screen=N) 등
```

**금지**: `screen=` 생략으로 `current_csv_play_screen()` default=1에 기대기.
듀얼 핫패스에서는 항상 `screen=si` / `self._screen` / `play_screen`을 넘긴다.

### 9.4 간섭이 날 수 있던 지점과 현재 가드 (2026-08-04)

| 위험 | 가드 | 파일 |
|------|------|------|
| `_dispatch_main_wait`가 화면1 stop만 봐 화면2 wait를 잘못 단축 | Runner ContextVar + `play_screen` 인자로 **해당 화면**만 검사 | `lam_sequence_engine.py` |
| progress JSON 카운트가 `csv_play_screen_session()`로 화면1에 기록 | `screen=` 명시 | `simulation_play._csv_play_progress_mark_json_*` |
| `SequenceRunner.stop` / Init `stop_all_*`가 양 화면 모션 절단 | CSV Play 활성 시 `stop_all` 생략, context 스코프만 | `lam_sequence_engine`, `reset_lam_sim_to_initial_state`, `lam_channel_scope` |
| 신호등이 한 화면 Pause에 꺼짐 | 기본 `TRAFFIC_LIGHT_EMISSIVE_ONLY_DURING_PLAYBACK=False` → **Play 무관 상시** | `lam_viewport_overlay_config.py` |

### 9.5 신호등 (참고)

- 장면의 Red/Green/Yellow shader `inputs:enable_emission`을 30~45초 랜덤 토글.
- **장식 효과**이며 wafer/CSV 데이터와 무관.
- 기본: stage 준비 후 상시 동작 (`ONLY_DURING_PLAYBACK=False`).
- `True`로 바꾸면 Play start/stop에 묶이므로 듀얼에서 간섭 가능 → **실무는 False 유지**.

### 9.6 Plan/캐시와 듀얼

- `_csv_playback_cache`는 **path+mtime+config_tag** 공용.
- 화면1·2가 **같은 파일**을 쓰면 plan을 공유(의도). **다른 파일**이면 키가 달라 독립.
- 한쪽만 규칙을 바꿨다면 config_tag(`plan=…|buf_foup=1`) bump / 재빌드.

### 9.7 남은 주의 (고치지 말고 문서화)

| 항목 | 설명 |
|------|------|
| HUD Play/Pause/Stop | `lam_csv_viewport_hud` → 모든 화면 창에 브로드캐스트일 수 있음 — **의도적 동시 제어**인지 UX 확인 |
| Overlay 전역 토글 | `get_toggle_foup_status` 등은 프로세스 전역. 화면2는 local checkbox 경로를 우선하지만, 전역 API를 직접 켜면 s1과 의미가 섞일 수 있음 |
| 메인 dispatch 큐 | USD write는 메인 스레드 직렬 — latency 간섭은 가능, **상태 교차와는 별개** |

### 9.8 듀얼 증상 → AI 수정 체크리스트

1. 두 창이 서로 다른 CSV를 골랐는가?
2. 화면2 Play 시 콘솔에 `ctx/stage 미준비`가 있는가? → `resolve_csv_screen_runtime` / split loader.
3. 한쪽 Stop에 다른 쪽 팔/로보트가 멈추는가? → `stop_all_*` 호출부 검색 (`lam_translate_animation`, `reset_lam_sim`, `lam_channel_scope`).
4. 한쪽 웨이퍼 번호/평면도가 다른 쪽과 섞이는가? → bus `screen=`, tracker dict, stage 폴백 금지.
5. FOUP 완료 카운트가 반대로 올라가는가? → `record_foup_event_from_schedule_entry(..., screen=si)`.
6. `screen=` 빠진 `csv_playback_stop_requested()` / `csv_play_screen_session()` 호출이 있는가?

### 9.9 실무 AI 프롬프트 (듀얼)

```
문서: docs/lam_control_1_sim_parse_rules_wafer_map_ko.md §9
증상: 화면1과 화면2가 서로 다른 CSV로 Play 중 <증상>
요청:
- screen-keyed 세션/stage/tracker 를 우선 검사
- stop_all_* / screen= 누락 / stage 폴백 을 먼저 의
- Aligner·투어끝 AtmArm→FOUP / occupancy swap 으로 “맞추기” 금지
- 수정 시 반드시 screen= / usd_context_name 명시
```

---

## 10. 현재 구현 상태 요약 (2026-08-05 갱신)

| 항목 | 상태 |
|------|------|
| `aligner_fix` 기본 | ✅ |
| ATM/VTM occupancy swap 기본 OFF | ✅ |
| Aligner 합성 + airlock 규칙 + enforce | ✅ `lam_aligner_process_rules` / `_enforce_aligner_*` |
| **투어 끝 AtmArm → FOUP place 합성** | ✅ `build_csv_playback_plan` (`last.slot_key == AtmArm`) |
| Buffer pick → FOUP 강제 삽입 | ❌ **폐지** (`_ensure_buffer_*` no-op) |
| Buffer~FOUP 사이 비-FOUP place 제거 | ❌ **폐지** |
| pick/place/transfer lot+cassette 필드 | ✅ |
| visibility mode 정규화 | ✅ |
| 평면도↔3D 동일 버스 | ✅ |
| 듀얼 스크린 세션/stage/tracker 격리 | ✅ (§9) |
| 듀얼 stop_all / dispatch wait / progress screen 가드 | ✅ (2026-08-04) |
| 정지(초기화) 시 prepared+path 캐시 무효 → 재파싱 | ✅ |
| 웹 start: force stop + 세대토큰 + 재 fetch/parse | ✅ |
| 캐시 태그 | `atm_end_foup=1` (구 `buf_foup=1` 대체) |
| full_occ dry-run 대체 엔진 | ⏳ 미사용 권장(후속) |

---

## 11. 자주 하는 실수 (하지 말 것)

1. `full_occ_correct`로 켜서 순서를 맞추려 하기 → 번호 꼬임 재발.
2. Aligner를 `simulation_play`에만 하드코딩하고 `lam_aligner_process_rules` SSOT를 무시하기.
3. 평면도만 따로 “추정 보정”하기 — 반드시 visibility bus ctx.
4. FOUP 완료 카운트만 UI에서 +1 하기 — **`atm_foup*_place` JSON 실행이 SSOT**.
5. Buffer pick 전용 강제 FOUP place를 다시 넣기 — **투어 끝 AtmArm 규칙으로 통일됨**.
6. `build_steps_for_dwell_transfer`에서 데이터와 다른 목적지로 “고쳐” 쓰기 — 파싱/규칙을 고칠 것.
7. 듀얼에서 `stop_all_*` / `screen=` 생략 / 화면2 stage→화면1 폴백 — 다른 화면 CSV 재생을 절단하거나 오염시킨다.
