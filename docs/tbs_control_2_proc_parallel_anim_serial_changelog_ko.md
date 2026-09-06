# TBS Control 2 — 공정 병렬 / 애니 직렬 수정 추적 문서

> **목적:** 사이드 버그 시 **원복 후 이 문서만으로 재수정**할 수 있게, 요구사항·As-Is·변경점·파일·검증을 누적 기록한다.  
> **작성 시작:** 2026-09-06  
> **상태:** 구현 진행 중  
> **원복 힌트:** 아래 「변경 파일 목록」의 플래그를 `False`로 돌리면 동작은 직렬(As-Is)에 가깝게 되돌릴 수 있다. 코드 원복 시 git으로 해당 커밋/파일 되돌린 뒤 이 문서 §3~§5를 따라 재적용.

---

## 1. 합의된 요구사항 (NEW)

### 1.1 공정 vs 애니

| 축 | 규칙 |
|----|------|
| **공정 타이머** | `ep1/ep2/ep3/inout` 등 포트가 비면 **순서 없이 병렬**로 즉시 시작 |
| **애니 재생** | 겹치면 **전역 1큐 직렬** — 앞 애니 끝나면 바로 다음 |
| **애니가 밀리면** | 해당 **공정 wall 시간도 같이 연장** (공정만 먼저 끝나지 않음) |
| **타이밍 맞춤** | `proc > anim` → lead 대기 후 재생 / `proc < anim` → 배속 압축 (기존과 동일 취지) |

### 1.2 우선순위

- `INOUT` 또는 `BP1..BPn`에 LOT이 **하나라도** 있으면 빈 EP에 **`OHT→EP` 금지**
- 대신 **`BP→EP` 우선** (BP에 LOT 있을 때)
- INOUT만 있고 BP 비어 있으면 **`INOUT→BP` → `BP→EP`**
- FOUP 종료 후 회수/`OHT→EP` 등은 **상황 되면 바로** 다른 공정과 병렬 시작 가능

### 1.3 프리런 / UI / 웹

- 프리런 일정에 **애니 큐 대기 → 공정 wall 연장** 반영
- 포트 상태·막대그래프는 **같은 일정 SSOT** (사이드 버그 없이)
- 진행 로그: **동시 진행 공정 전부** + **지금 재생 중 애니** 표시
- 웹: **스키마/구조 유지**, 내용만 다중 공정·애니 알기 쉽게

---

## 2. As-Is (수정 전) 요약

| 항목 | 내용 |
|------|------|
| 오케스트레이터 | `_run_serial_flow` — SSOT 순 **1건 yield-until-complete** |
| 병렬 플래그 | `SIM_PARALLEL_NONCONFLICTING_MOVES=False` (2레일 코드는 보류) |
| OHT→EP 가드 | `_can_load_to_ep_direct` = 빈 EP만 봄 (**INOUT/BP LOT 있어도 OHT→EP 가능**) |
| 애니 겹침 | UI 화면 큐 + 재생 게이트. **프리런 SimPy는 `timeout(proc)`만** → 큐 지연 미반영 |
| 진행 로그 | 화면당 주 슬롯 1 + (구병렬 시) MOVE 보조 1 |
| 참고 문서 | `docs/tbs_control_2_parallel_nonconflicting_resume_plan_ko.md` |

---

## 3. 구현 스위치 (원복 1순위)

**파일:** `source/extensions/morph.tbs_control_2/morph/tbs_control_2/sim_control_defaults.py`

```python
# True  = 공정 병렬 + 애니 직렬(NEW)
# False = 기존 완전 직렬(_run_serial_flow 1건씩)
SIM_PROC_PARALLEL_ANIM_SERIAL: bool = True
```

- **원복(동작만):** `SIM_PROC_PARALLEL_ANIM_SERIAL = False`  
- **완전 코드 원복:** git revert / 아래 파일 목록 되돌린 뒤 이 문서 §5 재적용

`SIM_PARALLEL_NONCONFLICTING_MOVES`는 **건드리지 않음**(기존 2레일 보류 유지). NEW는 별도 플래그.

---

## 4. 변경 파일 목록 (누적)

| # | 파일 | 변경 요약 | 날짜 |
|---|------|-----------|------|
| 1 | `docs/tbs_control_2_proc_parallel_anim_serial_changelog_ko.md` | 본 추적 문서 | 2026-09-06 |
| 2 | `sim_control_defaults.py` | `SIM_PROC_PARALLEL_ANIM_SERIAL` 추가 | 2026-09-06 |
| 3 | `simulation_engine.py` | 우선순위 가드, 애니 Resource, wait wall 연장, multi-wave, concurrent progress | 2026-09-06 |
| 4 | `playback_schedule.py` | `wall_sec` / 큐 지연 반영해 proc_end·json end | 2026-09-06 |
| 5 | `control_window.py` | 다중 공정 표시 + **progress→포트패널 skip SSOT** | 2026-09-06 |
| 6 | (웹 export 경로) | 구조 유지 + 로그 문구 보강 | 2026-09-06 |
| 7 | `simulation_engine.py` | 2차: INOUT/REMOVE 병렬, FOUP→REMOVE 즉시, anim=0 폴백 | 2026-09-06 |
| 8 | `control_window.py` | 2차: `_skip_progress_ports_occ_for_panel` | 2026-09-06 |
| 9 | `docs/tbs_control_2_prerun_ssot_plan_ko.md` | **방향 전환:** 프리런 SSOT+재생 전용, LOT3 초단위 표 | 2026-09-06 |
| 10 | `prerun_plan_ssot.py` | 오프라인 플래너 + `assert_example_lot3_schedule` | 2026-09-06 |
| 11 | `sim_control_defaults.py` | `SIM_PRERUN_PLAN_SSOT=False` (어댑터 연결 전) | 2026-09-06 |
| 12 | `sim_sequence_duration.py` | 공정별 JSON 1배속 길이 추정 | 2026-09-06 |
| 13 | `prerun_plan_adapt.py` | Plan→SimPreRunResult | 2026-09-06 |
| 14 | `prerun_plan_ssot.py` | `anim_from_json` (공정별 JSON 반영) | 2026-09-06 |
| 15 | `control_sim_prerun_playback.py` | SSOT 분기 / legacy tick 분리 | 2026-09-06 |
| 16 | `control_window.py` | SSOT 시 engine.start 생략 · **dead `_tick_loop` 삭제** | 2026-09-06 |
| 17 | `sim_control_defaults.py` | `SIM_PRERUN_PLAN_SSOT=True`, `SIM_PROC_PARALLEL_ANIM_SERIAL=False` | 2026-09-06 |
| 18 | `control_sim_playback_plan.py` | 8차: SSOT lookup 에서 wall remap 제거 (포트 깜빡임) | 2026-09-06 |
| 19 | `prerun_plan_adapt.py` / `control_window.py` / `control_sim_prerun_playback.py` | 8차: wall gate + DONE emit/heartbeat | 2026-09-06 |

*(추가 수정 시 이 표에 행을 계속 붙인다.)*

---

## 5. 재적용 체크리스트 (원복 후)

1. [ ] `SIM_PROC_PARALLEL_ANIM_SERIAL = True`
2. [ ] `_can_load_to_ep_direct`: INOUT/BP LOT 있으면 False
3. [ ] `_anim_play_res = simpy.Resource(capacity=1)` + `_wait_with_progress` lead→request→anim_wall→release, DONE에 `wall_sec`/`anim_queue_delay_sec`
4. [ ] `_run_serial_flow`에서 플래그 True면 `_start_proc_parallel_anim_serial_wave` 루프 (포트 잠금 기준 multi start, 전역 OHT 1레일 잠금 완화)
5. [ ] 프리런 = 동일 엔진 → 스케줄에 wall 반영 (`playback_schedule`)
6. [ ] 진행현황 다중 줄 + 재생 중 애니
7. [ ] 웹 slim/풀 export 필드 깨지지 않는지 확인

---

## 6. 검증 시나리오

1. **시작 동시 투입:** EBS ON, EP2, INOUT·EP 전부 EMPTY → `OHT→EP1`, `OHT→EP2`, `OHT→INOUT` 공정 타이머 동시 시작. 애니는 겹치면 직렬.
2. **큐 지연:** 공정 35/31/40·애니 10 → EP2 애니 중 EP1 애니 대기, EP1 wall > 35.
3. **버퍼 우선:** INOUT 또는 BP에 LOT 있을 때 빈 EP → `OHT→EP` 안 함, `BP→EP` 또는 `INOUT→BP`.
4. **프리런 vs 라이브:** 막대·포트 sync 시각이 프리런 timetable과 맞는지.
5. **로그:** 동시 RUNNING 여러 줄 + 현재 애니 JSON명.
6. **플래그 OFF:** 직렬 회귀.

---

## 7. 알려진 리스크 / 사이드 버그 감시

- 포트 이중 점유 (과거 2레일 이슈) — 포트 `_lock_port` + LOT pop 원자성 확인
- UI 애니 큐와 엔진 `_anim_play_res` **이중 직렬** → wall이 과도하게 늘 수 있음 → 재생 게이트가 엔진 일정을 존중하는지 확인
- FOUP(+Y/−Y)도 anim Resource 통과 시 이송 애니와 줄 섬
- 진행현황 디듀프 키가 다중 슬롯을 덮어쓰지 않는지

---

## 8. 변경 상세 로그 (시간순)

### 2026-09-06 — 12차: 재생 live 재계산 경로 추가 차단

**남아 있던 live 경로 (재생 중 스케줄 재계산)**
- `can_emit_timeline_event`: wall/proc/runner 로 emit 재직렬화
- `enrich_ssot_playback_progress`: live 파일명 우선 + JSON 길이 재추정
- job/start: `renewal_info_from_steps` 재파싱, lead 재계산
- `playback_schedule` bake: `timing_from_progress` + `predict_ports`
- `_step_playback_sync_t`: lead+offset 재계산
- progress emit: wall busy 매칭 게이트

**수정 (SSOT)**
- emit: `can_emit` 항상 True (시각은 프리런 고정; 애니 기동은 `anim_play_start`)
- progress 반영: wall 매칭 게이트 생략
- enrich: 플랜 시각 창만 / `estimate_json_file_duration` 제거
- event/progress 에 `has_renewal`·`renewal_offset_sec`·`port_sync_sim_time` 프리런 기록
- job/start: 프리런 renewal 우선, steps 재파싱 금지
- schedule bake / sync_t: play_start·port_sync 있으면 predict·lead 재계산 스킵

**의도적으로 남은 것 (실행이지 스케줄 재계산 아님)**
- JSON 러너 실제 재생·`set_json_wall_busy`(하드웨어 충돌 방지)
- `sim_now` 시계 전진 + 키프레임 lookup
- 러너 busy 시 pending 큐 (동일 레일 물리 직렬)

### 2026-09-06 — 13차: FOUP=애니종료 / REMOVE 조기 기동·포트·애니 싱크

**진단 (스크린 t≈203)**
- EP1 FOUP 라벨은 대기, 동시공정에 REMOVE EP1 + FOUP EP2 → **플랜상 EP1 FOUP는 이미 끝난 상태**일 수 있음.
- 다만 프리런 버그: renewal(`t_port_sync`)에서 `_on_anim_end`가 FOUP까지 기동 → **이송 애니 중에 FOUP→REMOVE가 당겨짐**.
- 포트 헤더 t= 가 마지막 occ 변경 시각에 고정 → sim_now 와 어긋나 보임.
- MOVE 누락: lead 중 active 덮어쓰기 (12b에서 큐 수정).

**수정**
- 프리런: `port_sync`(점유 키프레임)와 `anim_end`(FOUP 대기열) **분리**
- 재생: occ 불변이어도 포트 헤더 t= 는 `sim_now` 갱신
- (유지) JSON 슬롯 lead 점유 시 pending 큐, anim_play_start SSOT

### 2026-09-06 — 15b: 첫 애니 후 2번째 JSON 미재생

**원인 (15차 회귀)**
- `is_json_anim_slot_held` 에 `_json_sequence_started` hold 를 넣음.
- 러너가 끝나 idle 이 되어도 active 에 started 잔상이 있으면 슬롯 점유 유지.
- `on_done` 이 pending 다음을 **슬롯 해제 전에** dispatch → multi drain/QUEUE 경로에서 영구 차단.

**수정**
- 슬롯 hold = `runner_busy` **또는** 유효 lead(`pending`+`run_fn`) 만
- `on_done`: **먼저 슬롯·wall 해제 → 그 다음** pending 을 `_sim_json_start_fn` 으로 START

---

### 2026-09-06 — 15차: 애니 미재생 = 슬롯 잔류 고착 (구조 수정)

**증상**
- 재생 시작 후 JSON 애니가 전혀 돌지 않음 (진행·시계만 흐름)

**근본 원인 (증상 패치가 아님)**
1. SSOT `is_json_anim_slot_held` 가 lead(`_json_pending_sim_start`) 도 점유로 본다.
2. Start/프리런 재생 준비에서 **`_sim_anim_active_by_screen` / pending 을 비우지 않음**
   (legacy `_sim_anim_active` 만 clear).
3. 이전 재생의 pending active 가 남으면 **모든 새 job 이 QUEUE 만** 되고
   `_json_run_fn` 없는 stale 슬롯은 기동도 안 됨 → 애니 영구 미실행.

**구조 수정**
- `clear_playback_anim_slots()` — active/pending/레일키/멀티 job 큐 일괄 정리
- `_prepare_playback_emit_environment` + Start(부분/전체) 에서 호출
- `is_json_anim_slot_held`: `run_fn` 없는 lead 는 stale 로 슬롯 해제
- `_start_job_impl`: `_json_pending_sim_start` 는 **`_json_run_fn` 연결 후**에만 True
- gated freeze 중 `PORT_OCC_REFRESH` 특별 emit 원복 (emit 예산 고갈 위험)

**애니 슬롯 수명 (SSOT)**
`clear → acquire(active+run_fn+pending) → poll/sim_now≥play_start → run → on_done → release/next queue`

---

### 2026-09-06 — 14차: renewal 타이밍 포트·막대 동기

**증상**
- JSON renewal 마커 순간에 포트상태·막대그래프가 프리런 `port_sync` 와 안 맞음

**원인**
1. SSOT 인데도 `eff_sp` 가 짧은 `proc_sec` 기준 → 애니 압축 → renewal wall 이 `port_sync_sim_time` 보다 앞섬
2. wall sync 가 압축 재계산이면 plan 키프레임(비압축) 이전 occ lookup
3. `PORT_OCC_REFRESH` emit 이 재생 중 no-op

**수정**
- `_start_job_impl` SSOT: `eff_sp` span = `wall_sec` / `event_end` / anim 창
- `_resolve_renewal_sync_t_for_wall`: 프리런 sync 우선, SSOT 압축 재계산 금지
- renewal wall + `PORT_OCC_REFRESH` → plan `explicit` refresh / floor
- gated freeze 중 SSOT `PORT_OCC_REFRESH` emit 허용
- 막대: explicit 시 `honor_explicit_sim_time`

---

### 2026-09-06 — 12b: 첫 애니 후 후속 애니 미실행 수정

**원인:** SSOT `can_emit=True` 로 다음 공정 event 가 lead 대기 중에 들어옴.
이때 runner 는 idle 이라 다음 job 이 **active 를 덮어써** 첫 슬롯/`_json_run_fn` 이 소실 → 이후 큐 붕괴.

**수정:** `is_json_anim_slot_held` — `_json_pending_sim_start`(lead) 도 점유로 보고 QUEUE.
emit fail-safe / wall hold 가 `_sim_anim_pending_by_screen` 도 인식.

---

### 2026-09-06 — 11차: 애니 시작 시각 = 프리런 SSOT only

**요구:** 재생 중 lead/wall/JSON길이로 애니 시작을 **재계산하지 않음**. 프리런 `t_anim_start`만.

**수정**
- `prerun_plan_adapt`: event/progress 에 `anim_play_start_sim_time` / `anim_play_end_sim_time`
- job 빌드 시 위 필드 + `anim_sec` 전달
- `_start_job_impl` (SSOT+재생): `_json_run_start_sim = anim_play_start`, `est_total = prerun anim_sec` (`json_lead_sec` 재계산 금지)
- `playback_schedule`: progress 의 play_start/end 있으면 lead·큐 재가산 없이 그 시각 사용
- poll: `sim_now >= _json_run_start_sim` 만으로 기동

---

### 2026-09-06 — 10차: 재생중 애니=본문 진행률 + renewal 키프레임 + FOUP 완료

**증상**
- move_inout→bp 재생 중인데 본문이 arrived_inout DONE 고착
- renewal 타이밍에 포트 미갱신 (키프레임이 anim_end 만)
- FOUP 100% 후에도 「진행」 잔존

**수정**
1. 플래너: JSON renewal offset 시점에 `t_port_sync` 키프레임 (arrived_ep2@+7s 등)
2. `enrich_ssot_playback_progress`: **live 재생 파일/플랜 재생 슬롯**으로 본문 공정·% 교체
3. FOUP 라벨: 플랜 `wall_end` 지나면 즉시 **대기**
4. 애니 footer: 실제 `current_file` 우선

---

### 2026-09-06 — 9차: renewal early wipe + 진행초 0 + 동시공정/애니큐

**원인**
1. `arrived_ep2.json` 에 `renewal` 마커 → renewal 콜백이 **early sync_t** 로 `explicit` 포트 refresh → EP1 키프레임 전(빈 맵)으로 패널 wipe
2. 진행률 DONE 판정이 `event_end <= t0`(스냅샷 불일치) 일 때 `100% (0.0/…)` 고정; wall remap 도 잔존
3. SSOT 는 엔진 `_active_progress` 가 없어 `concurrent_summary` 비어 있음

**수정 (프리런 시계 재생만)**
- SSOT 포트: `sim_now` 키프레임만 (renewal delta/early explicit 금지)
- SSOT 진행률: `elapsed = clamp(sim_now - t0)` , `end > t0` 일 때만 DONE
- `enrich_ssot_playback_progress`: 플랜에서 [동시공정]·[재생중애니]·[애니대기큐] 채움

---

### 2026-09-06 — 8차: 포트 깜빡임 + 「REMOVE 없이 BP→EP」 착시

**재현 파악**
1. **포트 소실→복귀:** 오프라인 플랜 `@36.9` 는 `EP1=LOT001` 인데, 재생 lookup 이 `playback_sync_sim_t` 로 활성 JSON 의 `[t0,t0+proc]` 에 되감김. 병렬 ARRIVED 가 모두 `t0=0` 이라 mid-anim 에 빈 키프레임을 그림.
2. **세 번째 화면 BP→EP:** 플래너에는 `REMOVE EP2 LOT002 wall=149.5` → `BP→EP LOT004 start=162.5` 순서가 **이미 있음**. UI 진행현황이 다음 gated freeze 때문에 `DONE` emit 이 막히고 heartbeat 가 옛 MOVE 를 93%로 붙잡아 「REMOVE 없이 이동」처럼 보임. 포트 `EP2=LOT004` 는 FOUP 구간 키프레임과 일치.

**수정**
- `playback_plan_lookup_sim_t` (SSOT): `playback_sync_sim_t` 호출 제거 → **sim_now 로만** 키프레임 lookup
- `prerun_plan_adapt`: event 에 `wall_sec`/`event_end_sim_time` 포함, DONE 시각=wall
- emit: `set_proc_gate_end` 가 wall/end 사용 (SSOT)
- emit_due: gate freeze 중에도 progress `DONE` 통과
- heartbeat: `event_end` 지나면 해당 단계 `DONE` 고정

---

### 2026-09-06 — 7차: 재생 포트 sticky renewal 제거

**답변:** 프리런 플래너 키프레임은 맞았음 (t=13 EP1, t=26 EP1+EP2…).  
재생이 `pre_occ+delta` hold 로 포트를 고정해 계획을 덮어씀 → EP FOUP 중 빔, 애니/막대 불일치.

**수정**
- `_playback_ports_at_sim` (SSOT): 항상 `plan.ports_at(t)` + 희소 renewal delta (sticky pre_occ 금지)
- `playback_plan_lookup_sim_t` (SSOT): gated proc cap 제거 → sim_now 로 키프레임 lookup
- OHT→EP/INOUT 기본값 30~35 (기존 5~10이 공정 6.57s 원인이었음)
- 모니터 anim_sec=0 이면 JSON 길이로 표기 보강

---

### 2026-09-06 — 6차: 포트/막대 SSOT 복구 (병렬 occ 덮어쓰기)

**증상 (실기):** FOUP 중 EP1/EP2 패널 공백, BP 막대가 끝에서 load 고정, ARRIVED anim=0 표기, OHT→INOUT 공정이 7.5s(기본 5~10 중앙값)

**원인**
1. 병렬 공정 시작 event/RUNNING 에 **빈 전체 occ** → 패널·schedule predict 가 EP를 지움
2. `PORT_OCC_REFRESH` 가 milestone 수집에서 **무시**됨
3. `SIM_PROC_PARALLEL=False` 후 progress occ skip 가 약해짐
4. OHT→INOUT 기본 타이밍 5~10 이 EP 30대와 불일치

**수정**
- 어댑터: occ 는 키프레임 `PORT_OCC_REFRESH` 만 / event·progress 에 occ 제거, event에 anim_sec 포함
- `playback_plan`: SSOT 시 refresh 키프레임만 포트·막대 SSOT (schedule predict 제외)
- `_skip_progress_ports_occ_for_panel`: `SIM_PRERUN_PLAN_SSOT` 이면 항상 skip
- OHT→INOUT 가 기본(≤12s)이고 EP≥20 이면 EP 시간으로 정렬
- 플래너 idle 종료 시 포트 leftover 재기동

---

### 2026-09-06 — 5차: SSOT 프리런 연결 + JSON 애니시간

- **확인:** 예시의 애니 10초는 설명용. 실제 프리런은 `sim_sequences/*.json` 길이 사용 (현재 arrived/move/removed ≈ 13s).
- `SIM_PRERUN_PLAN_SSOT=True` → `prerun_ssot_to_timeline` (엔진 tick 없음, start 생략)
- `SIM_PROC_PARALLEL_ANIM_SERIAL=False` (엔진 multi-wave는 SSOT가 대체)
- 도달 불가 live `_tick_loop` 블록 삭제
- 레거시: `SIM_PRERUN_PLAN_SSOT=False` 로 엔진 tick 프리런 복구 가능

**다음:** `simulation_engine` multi-wave 사다리 코드 본격 삭제 / 재생기 occ live 덮어쓰기 잔여 정리.

---

### 2026-09-06 — 4차: 프리런 SSOT 재작성 착수

**판단:** live 엔진+UI 다중 SSOT 패치로는 포트/우선순위 사이드 버그가 계속남 →  
**프리런이 전체 일정 확정, 실행은 재생만** 으로 전환.

- 문서: `docs/tbs_control_2_prerun_ssot_plan_ko.md` (LOT3 초단위 정답 표)
- 코드: `prerun_plan_ssot.py` — 공정 병렬 / 애니 직렬 / EP1>EP2>INOUT 우선 / FOUP→REMOVE
- 예시 검증: `assert_example_lot3_schedule()` 통과 (애니: 20 EP1 →30 EP2 →40 INOUT →60 REM1 →70 REM2 →80 INOUT→BP →…)
- 플래그: `SIM_PRERUN_PLAN_SSOT=False` (PlaybackSchedule 어댑터·재생기 연결 후 True)

**다음:** 플래너 → 기존 `PlaybackSchedule`/`SimPreRunResult` 어댑터 → 재생기 plan-only · live occ 덮어쓰기 제거.

---

### 2026-09-06 — 3차 수정 (포트 SSOT + BP→EP 우선)

**증상**
- ARRIVED INOUT 시 다른 LOT(EP)이 포트패널에서 사라졌다 복귀
- 빈 EP + BP LOT 있는데 INOUT→BP 가 먼저 실행
- 이동 중 포트가 from/to 둘 다 비어 보이는 등 갱신 엉망

**구조 원인**
1. 포트 패널에 **불완전(sparse) occ** 가 들어오면 누락 키를 `-`로 그려 **엉뚱한 LOT 소실**
2. 애니 이벤트 emit 시점에 **엔진 전체 occ**로 패널을 덮음 (ARRIVED는 `_set_port` 전이라 타이밍도 틀림)
3. INOUT→BP 가드가 `_find_oldest_bp()`(잠금 제외)만 봐서, BP→EP 진행 중/가능 상황에서도 INOUT→BP 기동

**수정 (SSOT)**
- `_coerce_full_port_occ` / `_store_full_port_occ`: 항상 INOUT~EP3 완전 맵
- `_update_port_occupancy_panel` / post-anim apply: **완전 맵 + from/to 델타만**
- 애니 이벤트 emit → 엔진 occ 패널 sync **제거** (갱신 = renewal / JSON end 만)
- `_should_prefer_bp_to_ep_over_inout`: 빈 EP + BP LOT(잠금 포함)이면 INOUT→BP 금지

---

**증상**
- OHT→INOUT 가 안 도는 것처럼 보임
- 포트상태가 애니와 무관하게 사라졌다 나타남 / t 불일치
- FOUP 후 REMOVE 즉시 미기동
- 동시공정에 `EP 타임라인` 잡음

**원인**
1. `_should_hold_for_removed` 가 NEW 모드에서도 OHT→INOUT 를 막음
2. `_can_load_to_bp1` 이 BP→EP 잠금 슬롯을 “곧 빈 슬롯”으로 안 봄
3. FOUP 종료 시 회수 티켓/wave 가 NEW multi-wave 로 안 연결
4. progress RUNNING 의 엔진 `ports_occupancy` 가 포트 패널을 mid-process 로 덮음
5. gate `anim_sec=0` → 애니 큐/포트 sync 경로가 비활성

**수정**
- OHT→INOUT / INOUT→BP: NEW 모드에서 회수 홀드 해제 (애니만 직렬)
- `_can_load_to_bp1`: NEW 도 locked BP = soon-empty 허용
- FOUP end → chain 티켓 + `_start_proc_parallel_anim_serial_wave`
- `_parallel_schedule_wave`: NEW 면 multi-wave
- `_skip_progress_ports_occ_for_panel`: NEW 시 progress→포트패널 금지 (애니 milestone SSOT)
- `_estimate_anim_sec_from_json_name`: anim=0 폴백
- concurrent 에서 timeline_only / `타임라인` 라벨 제외

**포트 SSOT (유지보수 포인트)**
- 포트 패널 갱신 = renewal / JSON end(post-anim) / plan replay 만
- 공정 progress 의 엔진 occ 로 패널을 덮지 않음

---

- 요구사항 문서화 + 플래그 `SIM_PROC_PARALLEL_ANIM_SERIAL=True` 도입
- 엔진:
  - `_can_load_to_ep_direct`: INOUT/BP LOT 있으면 OHT→EP 금지
  - `_anim_play_res` + `_wait_with_progress` lead→애니큐→재생, DONE에 `wall_sec`/`anim_queue_delay_sec`/`event_end_sim_time`
  - `_start_proc_parallel_anim_serial_wave` 다중 기동 (OHT→EP 복수 EP, OHT→INOUT, BP→EP, 회수, INOUT→BP)
  - `_active_progress` / `concurrent_summary` / `anim_playing_json`
- `playback_schedule.py`: wall·큐 지연 반영
- `control_window.py`: 진행현황에 동시공정·재생중애니·wall 표시
- **원복:** `SIM_PROC_PARALLEL_ANIM_SERIAL = False` 또는 본 문서 §4 파일 git checkout

### 감시 포인트 (1차 후)

- UI JSON 큐와 엔진 `_anim_play_res` 이중 대기 여부
- FOUP ±Y 가 애니 Resource 를 잡아 이송이 과도히 밀리는지
- 막대/포트 sync 가 wall 연장 후 프리런과 라이브 일치하는지
- 웹 timetable 행에 wall/큐 정보가 보이는지 (구조는 유지, 필드는 progress 경유)

---

## 9. 빠른 원복 명령 (참고)

```text
# 동작만 끄기
sim_control_defaults.SIM_PROC_PARALLEL_ANIM_SERIAL = False

# 파일 단위 되돌리기 예 (경로는 저장소 루트 기준)
git checkout HEAD -- source/extensions/morph.tbs_control_2/morph/tbs_control_2/simulation_engine.py
git checkout HEAD -- source/extensions/morph.tbs_control_2/morph/tbs_control_2/sim_control_defaults.py
git checkout HEAD -- source/extensions/morph.tbs_control_2/morph/tbs_control_2/playback_schedule.py
git checkout HEAD -- source/extensions/morph.tbs_control_2/morph/tbs_control_2/control_window.py
# 재수정은 본 문서 §5
```
