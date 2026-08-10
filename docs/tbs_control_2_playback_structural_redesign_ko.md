# TBS Control 2 — 2화면 프리런 구조적 재설계 가이드 (2026-06-18)

> **용도**: 코드 원복 후 **내일 세션부터** 이 문서만 따라 구조적으로 재구현한다.  
> **전제**: **1화면 프리런 = 정상**, **2화면 = 미해결** (사용자 원복 완료, 2026-06-17 밤).  
> **금지**: 증상 패치(wall 보간, active job으로 진행률 덮기, 99% cap, dispatch 재시도만 추가 등).

**관련 문서**

| 문서 | 역할 |
|------|------|
| [tbs_control_2_multi_split_requirements_ko.md](tbs_control_2_multi_split_requirements_ko.md) | 멀티 분할·JSON 독립 **요구사항** |
| [tbs_control_2_playback_debug_history_ko.md](tbs_control_2_playback_debug_history_ko.md) | 과거 실패·시도 이력 (반복 금지 목록) |
| [tbs_control_2_json_end_port_update_policy_ko.md](tbs_control_2_json_end_port_update_policy_ko.md) | JSON 종료·포트 갱신·FOUP ±Y |
| [tbs_control_2_ebs_apply_mode_ko.md](tbs_control_2_ebs_apply_mode_ko.md) | arrived_ep / FOUP 이벤트 구분 |

---

## 0. 한 줄 목표

**화면 2개 = 화면 1개 시뮬을 설정만 다르게 2번 돌린 것과 동일한 품질.**  
진행률·`t(sim)`·JSON 타이밍·JSON 내부 스텝·FOUP 공정이 **화면마다 독립**이며, **1화면 경로는 한 줄도 의미가 바뀌지 않는다.**

---

## 1. 해결할 4가지 문제 (증상 → 구조적 원인)

### 1.1 2화면 진행률 실시간 반영 실패

| 관찰 증상 | 구조적 원인 |
|-----------|-------------|
| `t(sim)`·진행률이 얼었다가 점프 | (a) heartbeat 스로틀 + (b) **emit(동기 sink)이 UI tick보다 뒤** → 한 프레임에 무거운 작업 몰림 |
| 2화면에서 1화면보다 더 심함 | **공유 `dispatch_main` FIFO** — 화면2 JSON/LAM이 화면1 진행률 Kit 갱신을 밀어냄 |
| 진행률 100%인데 JSON 계속 / 반대로 JSON 끝났는데 0% | **서로 다른 시간 축 혼용**: wall 보간, `_sim_anim_active` proc, `ProgressStepState` proc가 한 함수에서 섞임 |
| 이벤트는 ARRIVED인데 연계 JSON은 이전 파일 | **타임라인 `progress`만 gate 밖 emit** → UI 단계는 앞서고 JSON·게이트는 뒤처짐 |

**구조적 해결 방향**

- 진행률 **단일 축**: 시뮬 시간 `[t0, t0 + proc_sec]` (`simulation_engine._wait_with_progress` 와 동일).
- 진행률 **단일 출처**: `ProgressStepState` (`progress_step_state.py`) — heartbeat는 `event_start_sim_time`·`proc_sec`만 보간.
- **`_sim_anim_active_by_screen`으로 진행률 덮어쓰기 금지** (MOVE/ARRIVED/FOUP 공통 — FOUP는 이미 `_apply_foup_playback_progress_from_sim` 으로 분리됨).
- N>1: **진행률 UI 갱신 경로를 emit·JSON main dispatch와 분리** (아래 §3.2).

---

### 1.2 진행 시 JSON이 공정 시간에 맞는 타이밍에 실행되지 않음

| 정책 (엔진 SSOT, 변경 금지) | 프리런 재생에서 맞출 동작 |
|------------------------------|---------------------------|
| 공정시간 우선 = **OFF** | 이벤트 진행 축 = **항상 `proc_sec`** |
| `anim_sec > proc_sec` | `eff_sp = user_sp × (anim/proc)` → wall JSON = `proc/user_sp` |
| `anim_sec ≤ proc_sec` | JSON 평소 배속, 끝난 뒤 **남은 sim 시간은 대기**(객체 정지, 진행률만 증가) |
| 공정·JSON **동시 시작** (`t0`) | 타임라인 event emit 시점 = JSON dispatch 시점 |

**구조적 해결 방향**

- 타이밍 SSOT 모듈 1개 (예: `playback_proc_json.py` — **재도입 시**):
  - `parse_step_timing(proc, anim, t0, user_sp)` → `eff_sp`, `proc_end_sim`
  - `proc_elapsed_sim(tnow, t0, proc)` — 진행률만
  - `proc_wait` — JSON 종료 후 `sim_now < proc_end` 이면 다음 timeline emit 차단
- **금지**: wall-clock으로 `elapsed`/`percent` 채우기, JSON 세션 끝날 때까지 임의 cap.
- **금지**: `emit_due_items`는 `event`만 막고 `progress`는 흘려보내기 — **gate 닫힐 때 log 제외 전 항목 emit 중단** (event·progress·anim 동기 유지).

**검증 시나리오**

- [ ] `anim=13s`, `proc=9.4s` → JSON 압축 후 **~9.4s wall** 안 종료, 진행률은 sim 축으로 0→100%
- [ ] `anim=5s`, `proc=9.4s` → JSON 5s wall 종료 후 **4.4s sim 대기**, 진행률은 계속 증가, 다음 event는 `t0+proc` 이후만

---

### 1.3 JSON 실행 시 내부 스텝(MOVE 등) 중간 끊김·미실행·간섭

| 관찰 증상 | 구조적 원인 |
|-----------|-------------|
| MOVE 거리 부족·끊김 | `dispatch_main_wait` **타임아웃** → 다음 step `stop_prim` |
| 2화면만 발생 | N>1: LAM bg 2개 + **공유 update 1개**가 컨텍스트별 큐를 프레임당 잘라 처리 |
| 한 화면 JSON 시작 시 다른 화면 JSON 끊김 | `stop_all_translate_animations()` 등 **전역 stop** (Phase 1 미적용·회귀 시 재발) |
| 워커 스레드에서 USD | `_sim_anim_workers_by_screen` → **메인 스레드만** USD write |

**구조적 해결 방향 (실행층 — N>1만)**

1. **USD 컨텍스트 스코프 stop** (`sim_channel_scope.py` — `multi_split_requirements` Phase 1).
2. **화면별 JSON main 직렬 큐** — `_sim_json_main_queues_by_screen` + `dispatch_main` (워커 스레드 `_start_job` 금지).
3. **화면별 isolated dispatch** (N>1만):
   - USD 컨텍스트마다 **독립 Kit update 구독** + 큐 **전량 drain** (`playback_dispatch.py` 패턴).
   - `set_playback_isolated_ctx_dispatch(True)` 시 공유 구독은 multi 큐 미처리.
4. **json_wall 해제 조건**: runner idle **AND** 해당 ctx `is_channel_motion_busy` — 둘 중 하나라도 busy면 다음 event 금지.
5. **금지**: dispatch 타임아웃만 늘리기, 배치 128만 키우기, `stop_prim` 재시도 래퍼만 추가.

**검증 시나리오**

- [ ] 2화면 동시 MOVE JSON — 각각 끝까지, 상대 화면 끊김 없음
- [ ] TIMESAMPLES_REPLAY 포함 전 step 종류
- [ ] `sim_multi_diag.log_motion_drain_timeout` 0건 (또는 허용 임계치 이하)

---

### 1.4 FOUP 공정 진행 실시간 미반영

| 관찰 증상 | 구조적 원인 |
|-----------|-------------|
| FOUP %·`t=` 멈췄다 점프 | FOUP 라벨은 **`FOUP_PROCESS` progress emit 때만** 갱신, 메인만 heartbeat 보간 |
| EP1·EP2 동시 RUNNING | 엔진: 화면별 `simpy.Resource(1)` vs UI: **FOUP progress 미게이트** |
| FOUP `proc_sec`에 MOVE 분모 표시 | FOUP heartbeat에 `_apply_playback_step_progress_from_sim`(애니 active) 오적용 |

**구조적 해결 방향**

- FOUP 진행률 **전용 보간**: `_apply_foup_playback_progress_from_sim` — 타임라인 `FOUP_PROCESS` payload의 `event_start_sim_time`·`proc_sec`만 (1화면에서 **이미 검증됨**, 유지).
- FOUP 재생 **삼중 게이트** (신규 `control_sim_playback_foup.py` 재도입 시):
  - `FOUP_PROCESS_START` — 슬롯 점유, 다른 EP START 보류
  - `FOUP_PROCESS` progress — **active EP만** 라벨 갱신
  - `FOUP_PROCESS_END` — 슬롯 해제, DONE 스냅샷
- FOUP는 `json_wall`에 넣지 않음 (`_timeline_event_needs_json_gate` 제외 유지).
- FOUP prim 상태 키: `@{screen}:{prim_path}` (`port_lot_visibility`).
- N>1 FOUP heartbeat: 메인 `t(sim)` 과 **동일 `sim_now` 소스**로 EP별 라벨 보간 (`_refresh_foup_playback_heartbeat`).

**검증 시나리오**

- [ ] 화면별 EP1 공정 중 EP2 라벨은 대기 (동시 RUNNING 라벨 없음)
- [ ] FOUP 30~60s 구간 %·`t=` 가 초 단위로 연속 증가 (1화면과 동일 체감)
- [ ] FOUP 중 EP ±Y lift 유지 ([json_end_port_update_policy_ko.md §5](tbs_control_2_json_end_port_update_policy_ko.md))

---

## 2. 불변 조건 (매 PR·매 커밋 전 확인)

| # | 조건 | 검증 |
|---|------|------|
| B1 | **N=1 동작 변경 없음** | `len(channels)<=1` 또는 `not _is_multi_viewport_sim(ext)` 분기에서 기존 코드 경로 그대로 |
| B2 | **한 PR = 한 축** | dispatch / proc 타이밍 / FOUP / 진행률 UI 분리 |
| B3 | **진행률 = sim 축만** | wall·active job 참조 없음 |
| B4 | **타임라인 gate** | gate 닫힘 = `event`+`progress` 함께 보류 (log만 예외) |
| B5 | **전역 LAM stop 금지** (N>1) | `stop_*_for_context` 만 |
| B6 | **USD write = main thread** | JSON·LAM dispatch 경로 |
| B7 | **1화면 회귀 먼저** | 2화면 수정 전·후 항상 1화면 프리런 통과 |

---

## 3. 목표 아키텍처 (레이어 분리)

```
┌─────────────────────────────────────────────────────────────┐
│  UI Sink (control_window._sim_ui_sink_*)                     │
│  · progress / anim_event / history — 변경 최소              │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  Playback Runtime (화면당 1세션)                             │
│  · SimTimelinePlayer — sim_now, cursor, emit_due_items       │
│  · tick 순서 (N=1 유지 / N>1만 UI-first 가능)                │
└──────┬────────────────────┬────────────────────┬────────────┘
       │                    │                    │
       ▼                    ▼                    ▼
┌──────────────┐   ┌─────────────────┐   ┌──────────────────┐
│ Clock        │   │ Timeline Gate    │   │ Progress SSOT     │
│ advance_sim  │   │ can_emit_*       │   │ ProgressStepState │
│ _clock       │   │ json_wall        │   │ + proc_json timing│
└──────────────┘   │ proc_wait        │   └──────────────────┘
                   └─────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  Execution (N>1 격리)                                        │
│  · playback_dispatch — ctx별 update, 큐 drain                │
│  · _sim_json_main_queues_by_screen                           │
│  · sim_channel_scope — stop/motion per ctx                   │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  FOUP Playback Governor (화면별 1슬롯)                       │
│  START / PROGRESS / END — 엔진 semantics 동형                 │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 시간·진행률 SSOT

| 개념 | 소유 모듈 | 비고 |
|------|-----------|------|
| `sim_now` | `SimTimelinePlayer` | wall × user_sp, 상한 `final_sim_time` |
| 단계 `t0`, `proc_sec`, `anim_sec` | 타임라인 progress payload → `ProgressStepState` | 엔진 녹화값 |
| `eff_sp` | `control_sim_playback_gate.compute_json_effective_speed` | runner `speed_scale` |
| `elapsed`, `percent` | heartbeat → `proc_elapsed_sim` | FOUP는 별도 함수 |
| 다음 event 허용 | `can_emit_timeline_event` | runner∨motion∨json_wall∨proc_wait |

### 3.2 N>1 진행률 실시간 반영 (구조)

**원칙**: 진행률 패널 갱신이 **JSON dispatch·타임라인 emit 큐와 같은 FIFO에서 기다리지 않는다.**

| N | 구현 (권장) |
|---|-------------|
| 1 | 기존: Kit `sim_playback_tick` → `tick_all` → `refresh_playback_ui` (0.2s 스로틀 유지) |
| >1 | **화면별 Kit 구독** `morph.tbs_playback_prog_scr_{N}` — `refresh_playback_ui`만, emit/JSON 없음 |

동시에 N>1 tick worker에서 **emit 전에 UI heartbeat** (`ui_before_emit=True`) 검토 — 단, 1화면 tick 순서는 변경하지 않는다.

---

## 4. 구현 Phase (반드시 이 순서)

> 각 Phase 완료 후 **§7 체크리스트 A + B1(1화면)** 통과 후 다음 Phase.

### Phase 0 — 베이스라인 고정

- [ ] 원복 상태에서 1화면 프리런 전 구간 OK 기록 (스크린샷·`t(sim)` 구간)
- [ ] 2화면 동일 시나리오 **실패 증상** 기록 (끊김 시점·파일명)
- [ ] `bootstrap` 직전 `_prepare_playback_emit_environment` (runner halt, gate clear, FOUP snapshot reset) 동작 확인

### Phase 1 — JSON 실행층 격리 (진행률·FOUP **미접촉**)

**목표**: 2화면 JSON 내부 스텝 끊김·화면 간 간섭 제거.

| 작업 | 파일 |
|------|------|
| ctx 스코프 stop | `sim_channel_scope.py`, `tbs_lam_sequence_engine.py`, `sequence_engine.py` |
| 화면별 JSON main 큐 | `control_window.py` (`_submit_screen_json_on_main`) |
| isolated dispatch (N>1만) | `playback_dispatch.py`, `tbs_main_dispatch.py` |
| json_wall + motion gate | `control_sim_playback_gate.py` |
| N=1 가드 | `_is_multi_viewport_sim` / `len(sessions)<=1` everywhere |

**완료 기준**: §1.3 검증 시나리오 통과, **1화면 회귀 없음**.

### Phase 2 — 타임라인 gate·proc/json 타이밍 SSOT

**목표**: 공정·JSON 동시 시작, 진행률 sim 축, 다음 event 순서 보장.

| 작업 | 파일 |
|------|------|
| `playback_proc_json.py` (신규) | 타이밍·proc_wait |
| `emit_due_items` gate 확장 | `control_sim_prerun_playback.py` — log 제외 전 항목 |
| `_start_job` / `_finish_on_main` | `control_window.py` — timing 필드, proc_wait 분기 |
| gate 연동 | `control_sim_playback_gate.py` — `is_playback_step_gate_closed` |
| heartbeat | `_apply_playback_step_progress_from_sim` — **p3만**, active 금지 |

**완료 기준**: §1.2 검증 시나리오, **이전 JSON 파일명이 다음 이벤트 UI에 남지 않음**.

### Phase 3 — 2화면 진행률 실시간 UI

**목표**: §1.1 — 멈춤·점프·엇갈림 제거.

| 작업 | 파일 |
|------|------|
| N>1 진행률 전용 Kit 구독 | `playback_channel.py` 또는 `control_sim_screen_playback.py` |
| tick 순서 (N>1만 UI-first) | `tick_all` / `tick_session` |
| linked_anim 표시 | `progress_step_state.py`, `_update_sim_progress` — payload 우선, 단계 불일치 시 “이전 단계 종료 중” |

**완료 기준**: 2화면 `t(sim)`·진행률이 1화면과 **동일 체감** (0.2s 스로틀 허용).

### Phase 4 — FOUP 재생 governor

**목표**: §1.4 — EP별 실시간·1슬롯 semantics.

| 작업 | 파일 |
|------|------|
| `control_sim_playback_foup.py` (신규) | START/PROGRESS/END 게이트 |
| FOUP heartbeat | `control_window._refresh_foup_playback_heartbeat` |
| `handle_sim_event_for_animation` | FOUP 분기 연동 |
| prim 스코프 | `port_lot_visibility.py` |

**완료 기준**: §1.4 검증, FOUP proc_sec ≠ MOVE proc_sec 혼선 없음.

---

## 5. 절대 하지 말 것 (실패 이력 SSOT)

| 패턴 | 왜 실패했는가 |
|------|----------------|
| wall-clock으로 진행률 보간 | 100%인데 JSON 재생, sim·UI 불일치 |
| `_sim_anim_active`로 heartbeat proc 덮기 | 이전 JSON(7.5s)이 다음 ARRIVED(9.4s) UI에 100% 표시 |
| `progress`만 gate 밖 emit | 진행현황·JSON·타임라인 순서 꼬임 |
| PlaybackChannel + proc_wait + wall 한꺼번에 | 1화면 회귀, 원인 분리 불가 |
| FOUP governor가 START/END만 | EP 동시 RUNNING |
| FOUP에 MOVE `proc_sec` 사용 | 30~60s FOUP에 7.5s 분모 |
| dispatch 타임아웃·배치만 증가 | MOVE 끊김 근본 미해결 |
| N>1 전용 우회를 N=1 경로에 합류 | 회귀 |

---

## 6. 주요 파일 색인 (작업 시)

| 파일 | Phase | 역할 |
|------|-------|------|
| `control_sim_prerun_playback.py` | 2 | `SimTimelinePlayer`, `emit_due_items`, `advance_sim_clock` |
| `control_sim_screen_playback.py` | 1,3 | `ScreenPlaybackSession`, `tick_all` |
| `control_sim_playback_gate.py` | 1,2 | `json_wall`, `can_emit`, `eff_sp` |
| `playback_proc_json.py` | 2 | proc·anim·proc_wait SSOT (신규) |
| `playback_dispatch.py` | 1 | N>1 ctx별 dispatch (신규) |
| `tbs_main_dispatch.py` | 1 | isolated 모드, per-ctx 구독 |
| `control_window.py` | 1–4 | sink, `_start_job`, FOUP UI, bootstrap |
| `progress_step_state.py` | 2,3 | `ProgressStepState`, `apply_engine_progress_payload` |
| `tbs_lam_sequence_engine.py` | 1 | LAM step dispatch, motion wait |
| `sim_channel_scope.py` | 1 | per-ctx stop, motion busy |
| `control_sim_playback_foup.py` | 4 | FOUP governor (신규) |
| `simulation_engine.py` | 참조만 | `_proc_anim_pair`, `_wait_with_progress` 정책 |

---

## 7. 통합 테스트 체크리스트 (전 Phase 완료 후)

### A. 1화면 회귀 (필수)

- [ ] 프리런 Start → `t(sim)` 연속 증가
- [ ] MOVE·arrived_ep·removed_ep JSON 순차, 끊김 없음
- [ ] 진행률 = 공정 `proc_sec` 축, 100% = 공정 sim 종료 시점
- [ ] FOUP 1슬롯·% 연속 증가
- [ ] JSON 종료 후 포트 상태 ([json_end_port_update_policy_ko.md](tbs_control_2_json_end_port_update_policy_ko.md))

### B. 2화면 독립성

- [ ] 화면1·2 `t(sim)` 독립 (서로 멈추지 않음)
- [ ] 화면1 JSON 중 화면2 진행률·JSON 정상
- [ ] 동시 MOVE — 양쪽 끝까지
- [ ] 연계 JSON·진행현황 이벤트명 일치 (ep2 재실행 표시 없음)

### C. 타이밍

- [ ] `anim > proc` — JSON wall ≈ `proc/user_sp`
- [ ] `anim < proc` — JSON 후 sim 대기 구간 진행률만 증가

### D. 레이아웃 회귀

- [ ] Console/Content/Dock ([multi_split_requirements §2.4](tbs_control_2_multi_split_requirements_ko.md))

---

## 8. 내일 세션 시작 절차 (5분)

1. 이 문서 **§4 Phase 0** 체크리스트 실행.
2. [playback_debug_history_ko.md §5–§6](tbs_control_2_playback_debug_history_ko.md) **금지 패턴** 훑기.
3. **Phase 1만** 착수 — `control_window` 진행률·proc_wait **건드리지 않음**.
4. Phase 1 완료 후 사용자에게 2화면 JSON 끊김만 먼저 확인 요청.
5. 통과 시 Phase 2 → 3 → 4 순.

---

## 9. 문서 이력

| 날짜 | 내용 |
|------|------|
| 2026-06-18 | 원복 후 구조적 재설계 SSOT 초안 — 4대 문제·Phase 0–4·불변 조건 |
