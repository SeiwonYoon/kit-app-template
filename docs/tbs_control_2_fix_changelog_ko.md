# TBS Control 2 — 수정 작업 기록

실무 이슈(1~7) 대응을 이 파일에 누적한다.  
코드 수정 시마다 **날짜·이슈 번호·요약·주요 파일**을 추가한다.

---

## 이슈 목록 (요구 요약)

| # | 요구 |
|---|------|
| 1 | REMOVE만 JSON **종료** 시 prim 숨김/보임. 나머지 JSON은 **renewal** |
| 2 | 프리런/웹 데이터: 공정 시작 행 + JSON 시작 행(`{EVENT} 동작중`). 기본·`_temp`·웹 slim 동일 |
| 3 | 첫 JSON 앞 공백 제거 플래그 |
| 4 | INOUT 점유 시 BP→EP 미진행이면 INOUT→BP 즉시 / 공정 즉시·애니만 직렬 (미착수) |
| 5 | 화면1·2 막대 싱크 |
| 6 | REMOVE wall 시간 / 공정 wall 팽창 |
| 7 | 위치 초기화 완료 후 애니 시작 |

---

## 2026-09-07 — #1 REMOVE prim 숨김 SSOT 복구

### 원인
JSON emit이 `anim_play_start`로 바뀐 뒤, 스케줄이 progress(`공정 t0`)와 event(`play_start`)를 **같은 t로만** 페어링해 `t_json_end`/play 필드가 빠짐.  
REMOVED hold가 `공정시작+anim`으로 붕괴 → renewal 전에 hold 만료 → prim 조기 소실.

### 수정
- `playback_schedule.py`: event↔progress 페어링을 `event_seq` / `event_start` / `anim_play_start`로 복구. SSOT `port_sync` 유지.
- `control_sim_playback_plan.py`: hide 시각 SSOT `_resolve_removed_prim_hide_end_sim` (`t_json_end` / `anim_play_end`).
- `find_scheduled_step`: sync None 시 step 폐기 제거 + play_start 매칭.
- `control_window.py`: REMOVED JSON 시작 시 hold에 `anim_play_end`·port 보강.

### 정책 (불변)
- REMOVE 3D prim: **JSON 종료**까지 보임  
- 그 외 JSON: **renewal** 시 숨김/보임  
- 패널 EMPTY: renewal (기존)

---

## 2026-09-07 — #2 타임테이블 JSON 시작 행 (`… 동작중`)

### 요구
프리런 결과의 **기본 JSON / `_temp` / 웹 slim** 모두:

| 시각 | `event` 표기 (예) |
|------|-------------------|
| 공정 시작 `t0` | `ARRIVED` |
| JSON 시작 `anim_play_start` | `ARRIVED 동작중` |

### 수정
- `control_sim_prerun_playback.py` — `build_timetable_row_metas`:
  - JSON dispatch event → `format_json_playing_event_label` (`{SEQ} 동작중`)
  - anim 있는 RUNNING progress → 공정 시작을 `kind=event` 행으로
- `control_sim_bar_graph.py` — `build_prerun_export_document_web_slim`:  
  ARRIVED/REMOVED/MOVE* 및 `… 동작중` event 행을 slim에 **유지** (기존엔 FOUP·step+anim만 통과)
- `control_window.py` — `_build_prerun_timetable_text` 도 metas SSOT 사용 (콘솔/텍스트 동일)

### 데이터 경로
`build_timetable_row_metas` → export 문서 `timeline.timetable_rows`  
→ 디스크 기본 / `_temp`(web_slim) / 웹 전송 slim 이 동일 소스.

### 비고
재생 타임라인 `seq` 자체는 그대로(ARRIVED 등). **표기/export 행만** `동작중`을 붙임 → JSON 매핑·게이트 부작용 없음.

---

## 2026-09-07 — #4 공정 즉시 기동 (예약 정합 + INOUT→BP / REMOVE)

### 요구
- 빈 EP + BP LOT → BP→EP 먼저 (유지)
- EP가 안 비었는데도 INOUT→BP가 안 뜨는 문제 수정
- FOUP 완료 → 해당 EP REMOVE가 바로 기동되어야 함
- 조건 충족 공정은 즉시 기동, 애니만 직렬

### 원인
포트 `_ep_reserved` / `_bp_reserved` / `_inout_reserved` 가 **이미 끝난 공정 uid**에 묶인 채로 남으면,  
REMOVE·INOUT→BP 조건이 되어도 `reserved` 때문에 영구 스킵됨.

### 수정 (`prerun_plan_ssot.py` 만)
- `_reconcile_reservations()`: `_try_start_all` 진입 시 활성 공정과 예약 동기화
- `_finish_process`: 해당 uid 예약 해제 + INOUT 예약 정리
- INOUT→BP: 사후 `prefer_bp` 보류 제거 (BP→EP는 이미 위에서 선점; 남은 빈 BP면 공정 병렬 기동)
- `assert_process_start_rules()` 회귀 테스트 추가

### 비고
SSOT 프리런 플래너만 변경. 레거시 `simulation_engine` 경로는 건드리지 않음.

---

## 2026-09-07 — #4 보완 철회: OHT→INOUT→INOUT→BP 는 다시 renewal 기동

후속 공정은 **renewal(`port_sync`) 후 `_try_start_all`** 이 기존 모델로 유지.
OHT→INOUT port_sync 에서 `_inout_reserved=False` 복구 (INOUT→BP 가 renewal 에 기동).

---

## 2026-09-07 — #5 화면1·2 막대 싱크

### 요구
분할 시 **화면1·화면2 모두** 막대그래프가 각자 `sim_now`/포트와 동일 규칙으로 싱크. (실무: 화면1만 맞고 화면2 어긋남)

### 원인
1. 모니터 채널을 `chans[scr-1]` 위치 인덱스로만 조회 → per-screen 창에서 리스트가 비거나 어긋나면 화면2 막대/포트가 스킵·오타깃
2. 멀티 `tick_all` 이 UI를 **emit 전에** 갱신 → 1화면(emit 후 UI)과 계약이 다름
3. `get_sim_playback_player` 레거시 폴백이 화면2에도 화면1 플레이어를 빌려줌
4. FOUP 만 전화면 after_tick 보정, EP 막대는 없음

### 수정
- `_resolve_monitor_channel_for_screen`: `ch['screen']` 키 조회 (막대·포트·프리런 paint·timeline_only)
- `tick_all`: 1·N 화면 모두 **emit → refresh**
- 플레이어 조회: 레거시 단일 플레이어는 **화면1만**
- `_refresh_all_ep_bar_playback_heartbeats` after_tick (FOUP 과 대칭)

### 파일
- `control_window.py`
- `control_sim_screen_playback.py`
- `control_sim_playback_plan.py`

---

## 2026-09-07 — #6 공정시간 wall 팽창 / 假 애니 큐 점유

### 요구
빈/없는 JSON fallback 으로 애니 큐가 **假점유**되어 공정이 90초+로 부푸는 것만 제거.  
**실제 JSON 직렬 대기 → wall 자동 연장은 유지** (기존 정상 동작).

### 원인
1. 빈/0초 JSON 에도 `anim_sec_fallback`(10초)을 넣어 글로벌 애니 큐를 假점유
2. (과도 수정, 이후 복구) wall 을 proc 만으로 고정해 실애니 직렬 연장까지 끊김

### 수정 (최종)
- `resolve_process_anim_sec`: 빈 JSON·파일 없음 → **0초** (fallback 假초 금지). `tbs_control_1` 시퀀스 경로도 탐색
- 플래너: **`t_wall_end = max(proc_end, anim_end)`** 복구 (실애니 직렬 밀림 시 공정 wall 연장)
- `t_hold_end = t_wall_end`
- 회귀 E: 두 OHT→EP 실애니 → 뒤 슬롯 play 가 앞 종료 이후 + wall > proc

### 파일
- `sim_sequence_duration.py`, `sim_sequence_json.py`
- `prerun_plan_ssot.py`, `prerun_plan_adapt.py`

---

## 2026-09-07 — #6 후속: 동시공정 hold 누락

### 증상
공정 wall이 끝난 뒤에도 애니가 재생 중인데 `[동시공정]` 에서 해당 공정이 사라지고, 본문이 `단계 완료`로 고착.
(과도하게 wall=proc 만 쓰던 동안의 증상; wall 복구 후에도 hold 폴백은 유지)

### 수정
- 동시공정·본문 RUNNING 판정 = `t_hold_end` / wall (애니 연장 포함)
- 동시공정 빈 목록이면 payload 잔상 클리어

### 파일
- `prerun_plan_adapt.py`

---

## 2026-09-07 — #7 JSON 시작 전 위치초기화 (연달아 시작 포함)

### 요구
JSON이 큐로 밀려 **끝나자마자 다음 JSON**이 바로 시작돼도, 애니는 **위치초기화가 끝난 뒤**에만 시작.

### 원인
1. `_run_json_sequence` 가 reset **직후** live 경로에서 `_halt_screen_json_anim` 을 다시 호출 → 맞춘 자세를 다시 건드림
2. 연속 시작(on_done→main→다음 job)에서 runner thread 가 아직 alive 인데 pause+join 시도 → 교착/초기화 누락 위험
3. restore 직후 dispatch settle 없이 `run` 진입

### 수정 (위치초기화만, 공정/직렬 큐 로직 미변경)
- reset **이후 halt 제거** — 순서: restore → dispatch settle → (필요 시 짧은 motion drain) → `run`
- main 스레드 연속 시작: pause/join 생략, `_restore_sim_prim_motion_to_initial` 강제
- `_reset_sim_motion_before_json_run` → 완료 bool 반환

### 파일
- `control_window.py`

---

## 2026-09-07 — 진행현황 끊김 / JSON 체감 지연 완화

### 증상
JSON 시작·종료 타이밍에 진행현황 창이 잠깐 멈추고, 애니가 늦게 붙으며 점프처럼 보임.

### 원인
1. #7 settle 이 **메인 스레드에서 sleep** (`wait_context_dispatch_idle` 1.5s + drain 0.35s) → UI 틱 정지
2. 프리런 `tick_all` 이 JSON start(reset) 를 **UI 갱신보다 먼저** 수행

### 수정 (공정/직렬/위치초기화 동작 유지)
- 메인: restore 동기 완료 후 **sleep settle 제거** (백그라운드만 짧은 dispatch idle)
- `tick_all`: `emit → UI refresh → JSON poll/drain` 순서
- emit 경로 프리런: 즉시 run 대신 pending → UI 후 poll 기동
- `on_done` 연속 JSON: `_start_json_now` 로 즉시 기동 유지

### 파일
- `control_window.py`, `control_sim_screen_playback.py`

---

## 다음 예정

- (잔여 이슈 없음 — #4 표기 정리 여부는 별도)

---

## 2026-09-07 — #3 첫 JSON lead 공백 제거 플래그

### 요구
`SIM_PRERUN_STRIP_FIRST_JSON_LEAD=True` 일 때, **첫 JSON 시작 시각**만큼 프리런 전체 일정을 앞당겨
시뮬 시작과 함께 첫 애니가 나오게 함. False 면 기존 lead(back-align) 유지.

### 수정
- `sim_control_defaults.SIM_PRERUN_STRIP_FIRST_JSON_LEAD` (기본 **False**)
- `shift_plan_strip_first_json_lead`: 첫 애니 `t_start` = shift → 공정/애니/포트KF/final 동일 차감
- `build_prerun_plan` 끝에서 플래그 ON 시 적용 → adapt/웹/재생이 같은 플랜 사용
- 회귀 F: strip 후 첫 애니@0, REMOVE 등 상대 간격 유지

### 파일
- `sim_control_defaults.py`, `prerun_plan_ssot.py`

---

