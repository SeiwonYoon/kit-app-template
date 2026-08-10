# TBS Control 2 — 프리런·재생 규칙 정리 및 구조 개편 합의 (2026-06)

> **목적**: 일괄 구조 수정 전, 시뮬 규칙·예외·목표 아키텍처에 대한 **합의 문서**.  
> **Phase 0** (`playback_schedule.py`): 프리런 직후 JSON·타이밍 **사전계산만** 추가 — 재생 동작은 아직 기존 경로.

---

## 1. 사용자 제안 요약 (동의 여부)

| 제안 | 판단 |
|------|------|
| 화면당 **sim 시간축 1개** | **동의** — `SimTimelinePlayer.sim_now(screen)` 가 SSOT 여야 함 |
| 프리런에 **어떤 JSON·언제·배속·총 재생시간**까지 계산 | **동의** — pose 녹화 없이 **스케줄**만 확정 (`playback_schedule.py`) |
| 재생은 그 결과만 따라 **이벤트 디스패치** | **동의** — 단, JSON **실행**(LAM/USD)은 런타임 필수 |
| FOUP·진행률·막대·포트도 같은 축 | **동의** — payload/스냅샷은 이미 프리런에 있음 |

---

## 2. 엔진(sim) 규칙 — 코드 기준 확인

### 2.1 공정 시간 축 (변경 금지)

- `_proc_anim_pair`: **이벤트 진행 시간 = 항상 `proc_sec`**
- `_wait_with_progress`: sim 축에서 `total_sec`(≈proc) 동안 progress emit
- `process_time_priority`: UI 설정이지만 현재 **OFF 고정** (`control_window` 주석)
- progress 첫 emit: `event_start_sim_time = env.now`, `elapsed=0`, `status=RUNNING`
- 필드: `proc_sec`, `anim_sec`, `linked_anim_json`, `event_seq`, `ports_occupancy` 등

### 2.2 JSON 타이밍 (UI 재생 측)

| 조건 | 동작 |
|------|------|
| `anim_sec > proc_sec` | `eff_sp = user_sp × (anim/proc)` → wall JSON ≈ `proc/user_sp` |
| `anim_sec ≤ proc_sec` | JSON는 1×에 가깝게 재생, 끝난 뒤 **sim 남은 구간 대기** (객체 정지, 진행률만 증가) |
| 공정·JSON 시작 | **동시 `t0`** (event emit = progress RUNNING 첫 emit) |

구현: `compute_json_effective_speed`, `json_wall_duration_sec` (`control_sim_playback_gate.py`)

### 2.3 이벤트 → JSON 매핑

우선순위 (`_resolve_event_animation_entry`):

1. `EVENT_JSON_CASE_MAP` (코드 상단)
2. `config/event_animation_rules.json` (ports_occupancy 조건)
3. `config/event_animation_map.json`

엔진은 `linked_anim_json` 파일명을 progress에 넣어 두므로, **프리런 스케줄은 이를 1차 SSOT**로 사용하고, 없을 때만 rules 재해석.

### 2.4 JSON 없는/특수 이벤트

| seq | JSON | 비고 |
|-----|------|------|
| `READYTOLOAD`, `READYTOUNLOAD` | 없음 | 큐 의미만 |
| `PORT_OCC_REFRESH` | 없음 | 포트 prim 동기화 |
| `FOUP_PROCESS_START/END` | 없음 | ±Y 1s translate, material |
| `FOUP_PROCESS` | 없음 | progress만 — EP 공정 % |
| 일반 MOVE/ARRIVED/REMOVED | 있음 | SequenceRunner |

`json_wall_busy` 게이트: `_timeline_event_needs_json_gate` — FOUP·OCC 제외.

### 2.5 FOUP

- EP당 `simpy.Resource(1)` — 엔진에서 동시 공정 불가
- START: +Y, processing material, `foup_proc_active_ep`
- END: -Y(≈1.05s 후 unmark), done material
- 진행률: `event_seq=FOUP_PROCESS` progress 구간 — **MOVE 분모에 섞지 않음**

### 2.6 멀티 화면

- 화면별 `TBSSimulationEngine` + `SimPreRunResult` + `sim_now`
- N>1: `tbs_main_dispatch` 컨텍스트별 큐 — **화면 간 FIFO 간섭**이 2화면 버그의 주 원인
- 목표: 화면 = 독립 `PlaybackSession` (시계+스케줄+실행)

---

## 3. 현재 구조가 복잡한 이유 (회귀가 나는 지점)

1. **이중 경로**: 재생 동기 emit vs `_sim_log_queue` drain
2. **이중 시계**: `sim_now`는 계속 가는데 `json_wall`은 event만 막음 → 진행률·JSON 어긋남  
   → **완화(구현됨)**: [재생 공정 경계 SSOT](tbs_control_2_playback_process_frontier_ko.md) — busy 중 `sim_now`·plan lookup 을 현재 이벤트 `t_proc_end` 로 묶음
3. **재생 시 rules 재평가**: 프리런과 동일 JSON인데 XML/rules 다시 탐
4. **상태 산재**: `ext._sim_*` 100곳+, `ProgressStepState`·active job·heartbeat 혼재

---

## 4. 목표 구조 (일괄 개편안)

```
화면 S:
  PlaybackSchedule  ← 프리런 직후 1회 빌드 (playback_schedule.py) [Phase 0 완료]
  SimClock(S)       ← sim_now 만
  Player(S)         ← sim_now 에 따라 schedule step 적용
```

**Player 루프 (목표)**

```
advance sim_now (user_sp)
for step in schedule where step.t_event <= sim_now and not applied:
    apply_event(step)      # 포트 가시성 등
    apply_progress_start(step)
    if step.needs_json: start_json(step.eff_sp, step.json_wall)
gate: 다음 step until json idle AND sim_now >= step.t_proc_end (proc_wait)
heartbeat: progress/FOUP/막대 = f(sim_now, current step) only
```

**제거/축소 대상 (Phase 2+)**

- 재생 중 ANIM/PROGRESS 큐 enqueue
- `json_wall_busy`와 `sim_now` 불일치
- heartbeat가 active job을 참조하는 경로

---

## 5. 구현 단계 (회귀 방지)

| Phase | 내용 | 1화면 회귀 |
|-------|------|------------|
| **0** | `playback_schedule.py` 빌드, `ext._sim_playback_schedule_by_screen` | **변경 없음** (현재) |
| **1** | 재생 중 큐 우회 확정, 스케줄 vs 타임라인 **검증 로그**만 | 필수 통과 |
| **2** | `PlaybackSession` — Player가 schedule+cursor로 emit | 필수 통과 |
| **3** | proc_wait: sim_now·emit 동일 게이트 | 1화면 후 2화면 |
| **4** | N>1 dispatch 격리 | 2화면 |

**불변 조건**: [tbs_control_2_playback_structural_redesign_ko.md](tbs_control_2_playback_structural_redesign_ko.md) B1–B7

---

## 6. 확인이 필요한 항목 (사용자 피드백 요청)

1. **`process_time_priority`**: 앞으로도 항상 OFF인가? (현재 코드는 OFF 고정에 가깝게 동작)
2. **재생 배속 변경**: 프리런 중 사용자가 속도를 바꾸면 `eff_sp`/`json_wall`만 재계산하면 되는지?
3. **Seek**: 타임테이블 클릭 시 schedule step 인덱스 + `SeekSnapshot` 동기 — 기존 `_fast_apply_prerun_seek` 유지 OK?
4. **빈 JSON `[]`**: 실행 스킵 + 포즈 유지 — 스케줄에 `json_est_sec=0`으로 기록하면 OK?
5. **2화면 일괄 개편 시점**: Phase 2(1화면 Player) 안정 후 진행해도 되는지?

---

## 7. Phase 0 사용법 (개발자)

프리런 완료 후:

```python
sched = ext._sim_playback_schedule_by_screen[1]
for st in sched.steps:
    # st.t_event, st.t_proc_end, st.json_basename, st.json_est_sec,
    # st.eff_sp_at_1x, st.json_wall_sec_at_1x
    ...
```

재생 로직은 **아직** `SimTimelinePlayer` + 기존 gate — 스케줄은 검증·다음 Phase 준비용.
