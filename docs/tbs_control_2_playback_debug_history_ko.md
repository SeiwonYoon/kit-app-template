# TBS Control 2 — 프리런 재생(Playback) 디버깅·리팩터 이력 및 실패 원인 정리

> **목적**: 1·2화면 프리런 재생 시 발생했던 증상, 반복된 실패 원인, 적용한 구조 변경을 한곳에 기록한다.  
> 코드를 원복하거나 다시 수정할 때 **동일한 패턴의 실수를 반복하지 않기 위한** 참고 문서이다.  
> **작성 기준일**: 2026-06-16 (대화·커밋 기준 최종 상태)

---

## 0. 현재 상태 요약 (2026-06-18 — 코드 원복 후)

| 구분 | 상태 |
|------|------|
| **코드** | 사용자 **원복 완료** — 2026-06-17 PlaybackChannel·proc_json·dispatch 실험 **미적용** |
| **1화면 프리런** | ✅ **정상** (원복 베이스라인) |
| **2화면 프리런** | ❌ **미해결** — 진행률·JSON 간섭·FOUP 실시간 등 |
| **다음 작업 SSOT** | **[tbs_control_2_playback_structural_redesign_ko.md](tbs_control_2_playback_structural_redesign_ko.md)** — Phase 0→4, 증상 패치 금지 |

아래 §0(2026-06-17)·§16(PlaybackChannel) 등은 **시도 이력**이다. 재구현 시 redesign 문서를 따른다.

---

## 0-legacy. 2026-06-17 — PlaybackChannel 전면 교체 (원복됨)

| 구분 | 상태 |
|------|------|
| **실행 모델** | ``playback_channel.PlaybackChannel`` × N (N=1 동일 클래스 1인스턴스) |
| **N>1 진행률** | 화면별 Kit 구독 ``morph.tbs_playback_prog_scr_{N}`` — **emit 큐와 분리** |
| **N>1 시계** | 채널 BG 스레드 ``morph.tbs_playback_clk_{N}`` |
| **N>1 emit** | 채널 ctx ``dispatch_main`` (진행률과 별도) |
| **N>1 JSON** | ``PlaybackChannel.enqueue_main_job`` |
| **N=1** | Kit tick → ``tick_n1_kit_frame`` (기존 tick_all 순서) |

**SSOT 파일**: ``playback_channel.py`` — ``control_sim_screen_playback.py`` 는 re-export 만.

**원복**: §16.4 파일 목록.

---

| 구분 | 상태 | 비고 |
|------|------|------|
| **1화면 프리런 재생** | ✅ **N=1 경로 유지** | `len(sessions)<=1` 시 기존 `_start_job`·halt·tick 동일 |
| **2화면 프리런 재생** | 🔄 **구조 수정 적용 — 사용자 검증 대기** | 아래 §15 불변 조건 5가지 반영 |
| **Phase A 구조 리팩터** | ✅ + §15 실행층 보완 | tick 채널 + JSON/LAM/FOUP 스코프 |
| **FOUP governor** | ⏸ 보류 | `port_lot_visibility` 화면별 키(`@{screen}:path`) 로 격리만 |

**원복 시**: §15.6 파일 목록 참고. 동일 증상 재발 시 해당 커밋/스냅샷으로 되돌린 뒤 §15 재적용.

**다음**: 2화면 프리런 재생 검증 → 실패 시 원복 후 §15.2 불변 조건 점검.

---

## 0-b. 이전 스냅샷 (2026-06-16 — 작업 중단 시점)

| 구분 | 상태 | 비고 |
|------|------|------|
| **1화면 프리런 재생** | ✅ **사용자 확인 — 대체로 정상** | JSON 순차, `t(sim)`, FOUP 공정 보간(개념 분리 후) |
| **2화면 프리런 재생** | ❌ **미해결 — 다음 작업** | 독립 진행·지연·상호 간섭 이슈 잔존 |
| **Phase A 구조 리팩터** | ✅ 코드 반영됨 | `ScreenPlaybackSession` × N, 단일 `tick_all` |
| **FOUP governor** (`control_sim_playback_foup.py`) | ⏸ 보류·삭제됨 | 1차 패치에서 proc_sec 혼선 → 원복 |
| **display_sim_now / clock·emit 이중 구독** | ⏸ 미적용 | 문서 §5 일부는 **목표 설계**이며 현재 코드와 다름 (§12.9 참고) |

**다음 세션에서 할 일**: 이 문서 **§13** 과 [`tbs_control_2_multi_split_requirements_ko.md`](tbs_control_2_multi_split_requirements_ko.md) 를 먼저 읽고 **2화면만** 집중 수정. **1화면 회귀 테스트 필수.**

---

## 1. 배경 — 무엇을 맞추려 했는가

### 1.1 목표 동작 (요구사항 요약)

| 구간 | 기대 동작 |
|------|-----------|
| 프리런 | 시뮬 엔진을 끝까지 돌려 타임라인(`SimPreRunResult.items`) 생성 |
| 재생 | wall-clock + 사용자 배속으로 `sim_now` 전진, due 항목 emit |
| JSON 애니 | 공정당 JSON 1개, **겹침 금지**; `anim_total > proc_sec` 이면 `eff_sp` 압축 |
| JSON 종료 후 | 공정시간 남으면 진행률만 증가 (객체는 멈춘 상태) |
| FOUP | 엔진과 동일: **전역(화면별) 1슬롯**, 하나 끝나야 다음; 완료 EP 재시작 불가 |
| UI | `t(sim)`·진행률·FOUP 라벨이 **매 프레임 부드럽게** 증가 (멈췄다 점프 X) |
| 1화면 vs N화면 | **동일 코드 경로** (화면당 `ScreenPlaybackSession` 1개) |

관련 정책 문서: [`docs/tbs_control_2_json_end_port_update_policy_ko.md`](tbs_control_2_json_end_port_update_policy_ko.md)

### 1.2 아키텍처 (리팩터 후 목표 구조)

> ⚠ **§1.2·§5 아래 다이어그램** 중 clock/emit **이중 구독**, `display_sim_now`, `control_sim_playback_foup.py` 는 **아직 코드에 없음**.  
> **실제 적용된 구조**는 **§12.9 (코드 기준 스냅샷)** 을 따른다.

```
프리런 완료
    └─ bootstrap_playback_after_prerun()  [control_sim_screen_playback.py]
           └─ SimPlaybackRuntime
                  └─ 화면마다 ScreenPlaybackSession
                         ├─ SimTimelinePlayer (results 1화면만)
                         └─ heartbeat (EP 0.1s / 진행 0.2s)

Kit update 구독 (control_window.py) — 현재 단일
    └─ morph.tbs_control_2:sim_playback_tick → _tick_playback_timeline → rt.tick_all()

tick_all 순서 (실제)
    1) 모든 화면 advance_clock_only()
    2) 모든 화면 emit_due_and_sync()
    3) 모든 화면 refresh_playback_ui()

타임라인 emit
    └─ _deliver_playback_timeline_emit()  [동기 — 큐 사용 금지]
           ├─ progress → _sim_ui_sink_progress
           └─ event    → _sim_ui_sink_anim_event → handle_sim_event_for_animation
```

**핵심 파일**

| 파일 | 역할 |
|------|------|
| `control_sim_prerun_playback.py` | 프리런, `SimTimelinePlayer`, `advance_sim_clock`, `emit_due_items` |
| `control_sim_screen_playback.py` | `ScreenPlaybackSession`, `SimPlaybackRuntime`, `tick_all` |
| `control_sim_playback_gate.py` | `json_wall_busy`, `can_emit_timeline_event`, `eff_sp` |
| `control_sim_multi_playback.py` | runtime 위임 (레거시 import 유지) |
| `control_window.py` | UI sink, Kit 구독, FOUP/JSON 애니, 진행현황 패널 |
| `simulation_engine.py` | 실시간 시뮬 FOUP `simpy.Resource(capacity=1)` |
| ~~`control_sim_playback_foup.py`~~ | **삭제·미사용** (1차 패치 후 원복) |

---

## 2. 증상 정리 (사용자 관찰)

### 2.1 진행률 / t(sim) 멈춤·점프

- 하단 `[진행현황] t(sim)=…`, `진행률: N%` 가 초 단위로 매끄럽게 오르지 않고 **얼었다가 한꺼번에 증가**.
- 1화면에서도 재현 (2화면만의 문제가 아님).
- EP 타임라인 막대·포트상태 헤더 `t=` 도 이벤트 간격으로만 바뀌는 경우 있음.

### 2.2 FOUP 공정 규칙 붕괴

- **EP1·EP2가 동시에 `RUNNING`** (엔진은 1개만 공정 가능).
- 공정이 끝난 EP가 **+Y 애니·진행률 0%부터 다시 시작**.
- FOUP 라벨의 `t=` 값이 하단 `t(sim)` 과 불일치 (예: EP1 `t=63.1`, EP2 `t=84.0`, 하단 `84.57`).

### 2.3 2화면에서 더 심해 보인 이유

- 화면1 JSON/emit·로그 부하가 **같은 메인 스레드**에서 화면2 tick까지 지연.
- 증상 패치만으로는 1화면 회귀 vs 2화면 개선을 동시에 맞추기 어려움.

---

## 3. 근본 원인 (왜 계속 “고쳤는데 더 망가졌는가”)

### 3.1 증상 패치 누적 — 구조 불일치

여러 차례 **표면 증상**에만 대응하면서 서로 다른 레이어에 규칙이 중복·충돌했다.

| 패치 성격 | 예 | 문제 |
|-----------|-----|------|
| heartbeat 스로틀 | `_HB_PROG_INTERVAL = 0.10` | UI가 10Hz로만 갱신 → “멈췄다 점프” 체감 |
| 큐 vs 동기 혼용 | 재생 중 progress는 동기, 예전 경로는 큐 | 2화면 로그 폭주 시 한쪽만 지연 |
| 레거시 heartbeat | `_playback_screen_heartbeat_ui` (0.1s) | 새 runtime과 이중 경로 (호출은 제거됐으나 함수 잔존) |
| anchor/lp 우선순위 패치 | 여러 곳에서 `_remember_playback_step_anchor` | 분모·t0 출처 불명확, 2번째 공정 freeze 등 부작용 |
| FOUP governor (1차) | START/END만 게이트 | **`FOUP_PROCESS` progress는 미게이트** → EP별 라벨 동시 RUNNING |

**교훈**: 재생 규칙은 **한 모듈·한 tick 순서·한 시간 소스**에서만 정의할 것. UI 스로틀과 sim 시계를 섞지 말 것.

### 3.2 tick 순서 — UI가 emit 뒤에 있었음

초기 `tick_all` 순서:

```
advance_clock → emit_due (이벤트·동기 포트/JSON 준비) → refresh_playback_ui
```

`emit` 경로의 동기 작업(포트 패널 sync, `_reset_sim_motion_before_json_run`, FOUP material 등)이 **한 프레임을 길게 잡으면**, 그 프레임의 UI 갱신이 뒤로 밀림.

**교훈**: `시계 전진 → UI 갱신 → emit` 순서. 더 나아가 **clock 구독과 emit 구독 분리** (`sim_playback_clock` / `sim_playback_emit`).

### 3.3 sim_now vs display_sim_now — 프레임 드롭 시 점프

`advance_sim_clock()` 은 **마지막 advance 이후 경과 wall-time**을 한 번에 더함.  
Kit 프레임이 드물면 `sim_now` 자체가 점프한다.

**해결**: `SimTimelinePlayer.display_sim_now()` — 마지막 advance 시점·속도로 **프레임마다 외삽** (emit 판정은 `sim_now`, UI는 `display_sim_now`).

### 3.4 FOUP — 엔진 규칙과 재생 UI 규칙 불일치

| | 시뮬 엔진 (`simulation_engine.py`) | 재생 UI (패치 전) |
|--|--|--|
| 직렬화 | `_ep_foup_process_res` capacity=1 | 없음 |
| 진행 표시 | `FOUP_PROCESS` progress 이벤트 | EP별 라벨에 타임라인 그대로 반영 |
| 완료 후 | phase 종료, REMOVED 대기 | START 재emit 시 +Y 반복 |

1차 `control_sim_playback_foup.py` 는 `FOUP_PROCESS_START/END` 애니만 막았고,  
**`FOUP_PROCESS` progress 라벨 갱신은 `_update_sim_progress` 에서 그대로** EP2·완료된 EP1에도 적용됨.

**교훈**: FOUP 재생 게이트는 **START / END / PROGRESS 세 종류 모두** 엔진 semantics 와 맞출 것.

### 3.5 JSON wall 게이트와 FOUP 예외

- `can_emit_timeline_event`: `json_wall_busy` 이면 다음 **event** 차단 (progress·sim_now는 계속).
- `FOUP_PROCESS_START/END` 는 `_timeline_event_needs_json_gate` 에서 **제외** (JSON 없음).
- FOUP 도중 MOVE 등 다른 이벤트는 타임라인 시각상 허용되나, **FOUP 슬롯**은 별도 governor 로 관리해야 함.

### 3.6 2화면 순차 tick (초기)

`tick_all` 이 `for sess: tick_frame` (시계+emit+UI 한 묶음) 이면,  
화면1 emit이 길어질 때 화면2 시계도 같은 루프에서 지연.

**해결**: 모든 화면 `advance` → 모든 화면 `UI` → 모든 화면 `emit` 단계 분리.

---

## 4. 수정 타임라인 (진행 과정)

### 4.1 1단계 — JSON 겹침·게이트

- `dispatch_main_to_context` 비동기 제거 → event 직전 `set_json_wall_busy`.
- `_deliver_playback_timeline_emit` 에서 재생 중 **동기 sink** (큐 지연 제거).
- `can_emit_timeline_event`: runner busy / pending / json_wall 시 event break.

### 4.2 2단계 — 진행률 분모·anchor

- `proc_sec` 우선, `lookup_playback_step_bounds_from_prerun`.
- `_seed_progress_lp_from_timeline_event`, session `anchor` / `last_progress`.
- heartbeat 에서 `apply_progress_interpolation`.

**부작용**: anchor·lp 가 여러 경로에서 갱신되며 2번째 공정 freeze 등 간헐 버그 → session 으로 수렴 필요.

### 4.3 3단계 — 구조 리팩터 (ScreenPlaybackSession)

- 신규 `control_sim_screen_playback.py`.
- 1·N 화면 동일: 화면당 `SimTimelinePlayer` + `ScreenPlaybackSession`.
- `control_sim_multi_playback.py` → runtime 위임.
- `control_window`: `bootstrap_playback_after_prerun`, 단일 Kit tick.

### 4.4 4단계 — FOUP governor (불완전)

- 신규 `control_sim_playback_foup.py` (phase: idle/active/done, pending START 큐).
- `handle_sim_event_for_animation` FOUP 분기 연동.

**미해결**: PROGRESS 미게이트, 라벨 `t=` 고정, 완료 EP 라벨 잔류.

### 4.5 5단계 — tick·UI·FOUP 보완 (현재 문서 시점)

| 변경 | 내용 |
|------|------|
| `_HB_PROG_INTERVAL = 0` | 진행률 매 프레임 |
| `display_sim_now()` | UI wall-clock 외삽 |
| tick 순서 | clock+UI → emit |
| Kit 구독 2개 | `sim_playback_clock`, `sim_playback_emit` |
| `should_apply_foup_progress` | 활성 슬롯 EP만 PROGRESS 라벨 |
| `_refresh_foup_playback_labels` | heartbeat FOUP `%`·`t=` 보간 |
| END 시 | `_reset_foup_label_now` + pending START drain |

---

## 5. 현재 설계 invariant (원복·재작업 시 반드시 지킬 것)

### 5.1 시간

```
실제 타임라인 커서·emit 판정  → player.sim_now()     (advance_sim_clock 후)
UI 표시·진행률 보간          → display_sim_now()    (프레임마다 외삽)
```

- `playback_time_tick` payload 로 UI만 갱신; `ports_occupancy` 등은 strip (`_PLAYBACK_TIME_TICK_STRIP_KEYS`).

### 5.2 tick 순서 (화면별 루프 내)

```
1) 모든 세션 advance_clock_only()
2) 모든 세션 refresh_playback_ui(display_sim_now)
3) 모든 세션 _refresh_foup_playback_labels (재생 중)
4) 모든 세션 emit_due_and_sync()   ← 무거운 작업; 뒤에 둠
```

### 5.3 JSON·이벤트

- 공정당 JSON 1개: event emit 전 `set_json_wall_busy(True)`, 완료 후 `release_json_wall_if_idle`.
- `eff_sp = user_speed × (est_total/proc_sec)` when `est > proc` (`control_sim_playback_gate.py`).
- `PORT_OCC_REFRESH` 만 포트/visibility/위치 일괄 갱신 (이동 이벤트에서 직접 갱신 X).

### 5.4 FOUP 재생

```
FOUP_PROCESS_START  → should_apply_foup_playback_event
                    → (prim 확인 후) notify_foup_playback_start_applied
FOUP_PROCESS progress → should_apply_foup_progress (active EP만)
                    → remember_foup_progress_snapshot
FOUP_PROCESS_END    → notify_foup_playback_end_applied
                    → _reset_foup_label_now
                    → drain_pending_foup_playback_starts
```

- 화면별 `_sim_foup_playback_phase_by_screen` / `_active_port` / `_pending` / `_snapshot`.
- 재생 시작·중지: `clear_foup_playback_state()` (`bootstrap` / `stop_playback_runtime`).

### 5.5 1화면 회귀 금지

- 2화면 전용 분기로 1화면 경로를 바꾸지 말 것.
- `is_multi_viewport_sim` 은 **애니 pause·tick worker** 용; 재생 runtime 은 화면 수만큼 세션 생성으로 통일.

---

## 6. 반복해서 하면 안 되는 실수 (체크리스트)

### 6.1 UI·시계

- [ ] 진행률 heartbeat 를 0.1s·0.2s 로만 갱신하고 “부드럽다”고 가정하지 말 것.
- [ ] `emit` 를 `refresh_playback_ui` 보다 앞에 두지 말 것.
- [ ] 재생 중 progress/event 를 `_sim_log_queue` 에 넣었다가 drain 하지 말 것 (2화면 지연 재발).
- [ ] UI 에 `sim_now()` 만 쓰고 `display_sim_now()` 를 빼지 말 것.

### 6.2 FOUP

- [ ] START/END 만 막고 `FOUP_PROCESS` progress 는 방치하지 말 것.
- [ ] EP별 라벨에 타임라인 `sim_time` 을 그대로 쓰지 말 것 (heartbeat 보간 필요).
- [ ] `notify_foup_playback_start_applied` 를 prim_path 검증 **전에** 호출하지 말 것 (슬롯 점유만 하고 애니 실패).
- [ ] 완료 EP 라벨을 END/reset 없이 두지 말 것 (stale RUNNING %).

### 6.3 JSON·게이트

- [ ] event emit 을 비동기 dispatch 하여 `json_wall_busy` 레이스 만들지 말 것.
- [ ] `json_wall_busy` 가 켜진 상태에서 다음 JSON 을 또 시작하지 말 것.
- [ ] FOUP 를 json_wall 에 넣지 말 것 (JSON 없음 — 별도 슬롯).

### 6.4 구조

- [ ] `control_window` 에 playback tick 로직을 다시 통째로 넣지 말 것 → `control_sim_screen_playback.py` 유지.
- [ ] 화면마다 player 없이 전역 player 1개 + screen dict 로 되돌리지 말 것 (2화면 커서 꼬임).
- [ ] anchor/lp 를 ext 전역만 쓰지 말 것 → session + `sync_legacy_ext_state` 로 레거시 호환.

---

## 7. 디버깅 방법 (재발 시)

### 7.1 어느 레이어인지 분리

| 관찰 | 의심 레이어 |
|------|-------------|
| `t(sim)` 만 점프, 이벤트는 맞음 | clock 구독 / `display_sim_now` / tick 순서 |
| 이벤트·JSON 겹침 | `json_wall_busy`, `can_emit_timeline_event`, emit 동기 여부 |
| FOUP 두 EP RUNNING | `should_apply_foup_progress`, phase/snapshot |
| FOUP 재시작 | START 재emit + phase!=done, 또는 seek 후 governor 미초기화 |
| 포트 상태 틀림 | `PORT_OCC_REFRESH` 타이밍, post_anim 큐 |

### 7.2 확인할 ext 상태 (재생 중)

```text
_sim_playback_started
_sim_playback_runtime.sessions[screen]
_sim_json_wall_busy_by_screen
_sim_foup_playback_phase_by_screen
_sim_foup_playback_active_port_by_screen
_sim_foup_playback_pending_by_screen
_sim_playback_step_anchor_by_screen  (레거시 미러)
_sim_progress_last_payload_by_screen
```

### 7.3 Kit 구독

```text
_sim_playback_clock_sub  → _tick_playback_clock_ui
_sim_playback_emit_sub   → _tick_playback_emit
```

둘 다 살아 있는지, `stop_playback_runtime` / `on_sim_stop_clicked` 에서 unsubscribe 되는지 확인.

---

## 8. 관련 함수 빠른 색인

| 함수 | 파일 | 설명 |
|------|------|------|
| `bootstrap_playback_after_prerun` | screen_playback | runtime 기동 |
| `tick_clock_and_ui` / `tick_emit_all` | screen_playback | tick 단계 |
| `advance_sim_clock` | prerun_playback | wall → sim_now |
| `display_sim_now` | prerun_playback | UI 외삽 |
| `emit_due_items` | prerun_playback | 프레임당 event 1개 제한 주의 |
| `can_emit_timeline_event` | playback_gate | JSON wall |
| `should_apply_foup_*` | playback_foup | FOUP 게이트 |
| `_deliver_playback_timeline_emit` | control_window | 동기 emit |
| `_sim_ui_sink_progress` | control_window | 보간·lp·패널 |
| `_refresh_foup_playback_labels` | control_window | FOUP heartbeat |
| `handle_sim_event_for_animation` | control_window | FOUP/JSON 분기 |

---

## 9. 알려진 잔여 리스크 / 미검증

- **메인 스레드 장시간 블로킹**: clock/emit 구독이 같아도, 한 callback 안에서 USD 동기 작업이 수 초면 그 동안 Kit 전체가 멈출 수 있음. 근본적으로는 무거운 USD 작업 비동기화 필요.
- **FOUP pending START**: 타임라인 cursor 는 원래 START 시각을 지나간 뒤 슬롯이 비면 시각적으로 늦게 시작 (엔진 직렬화와 시각 동기는 타협).
- **포트상태 헤더 `t=`**: heartbeat `timeline_only` 로 EP 막대는 갱신되나, 포트 그리드 헤더는 `ports_occupancy` 포함 progress 에 의존할 수 있음 — 필요 시 clock tick 에서 별도 갱신 검토.
- **원복 후 재적용 순서**: (1) screen session runtime → (2) gate 동기 emit → (3) tick 순서·display_sim_now → (4) FOUP triple gate.

---

## 10. 권장 재구현 순서 (원복 후 다시 할 때)

1. `SimTimelinePlayer` + 프리런 타임라인만 재생 (emit 로그만).
2. `json_wall_busy` + event 1개/틱 + `eff_sp` 적용.
3. `ScreenPlaybackSession` + 1화면 재생 검증.
4. N화면 세션 복제 + tick 단계 분리 (advance → UI → emit).
5. `display_sim_now` + clock/emit 구독 분리.
6. FOUP: START/END governor → PROGRESS gate → label heartbeat → END reset.

각 단계에서 **1화면 회귀 테스트** 후 다음 단계로 진행.

---

## 11. 대화·작업 로그 참조

- Agent transcript (상세 패치 논의):  
  `C:\Users\ptK\.cursor\projects\c-Users-ptK-Documents-kit-app-template-mine\agent-transcripts\e05e2bf1-5e27-4714-9a3e-333ca17c5dcd\e05e2bf1-5e27-4714-9a3e-333ca17c5dcd.jsonl`

## 12. 재구현 로그 (2026-06-16 — 원복 후 2차 구조 작업)

### 12.1 전제

- 사용자가 **1화면 정상 동작** 코드로 원복 완료.
- 이번 작업은 **증상 패치(FOUP governor·display_sim_now 등) 없이** 구조만 다시 적용.
- 목표: `N화면 = ScreenPlaybackSession × N`, 1화면도 세션 1개로 **동일 코드 경로**.

### 12.2 완료된 변경 (Phase A)

| # | 파일 | 내용 | 1화면 영향 |
|---|------|------|------------|
| A1 | `control_sim_prerun_playback.py` | `advance_sim_clock()` / `emit_due_items()` 분리, `tick()` 은 둘 호출 (동작 동일) | 없음 (기존 tick semantics 유지) |
| A2 | `control_sim_screen_playback.py` | **신규** — `ScreenPlaybackSession`, `SimPlaybackRuntime`, `bootstrap_playback_after_prerun`, `stop_playback_runtime` | 1화면 = 세션 1개 |
| A3 | `control_sim_multi_playback.py` | runtime 위임만 (2화면 전용 tick 로직 **삭제**) | 1화면과 동일 `tick_all` |
| A4 | `control_window.py` | 프리런 bootstrap → 항상 `bootstrap_playback_after_prerun` (multi if 분기 제거) | 단일 player 직접 생성 제거 |
| A5 | `control_window.py` | `_deliver_playback_timeline_emit` — 재생 중 progress/event **동기 sink** (큐 지연 방지) | 1화면도 동기 (큐보다 즉시) |
| A6 | `control_window.py` | `_tick_playback_timeline` → `rt.tick_all` 단일 경로 | 구 `_tick_playback` 150줄 제거 |
| A7 | session heartbeat | EP **0.10s**, 진행률 **0.20s** — 원복 1화면과 **동일 주기** | 의도적 동일 |

### 12.3 tick 순서 (이번 구조의 핵심)

```
모든 화면: advance_clock_only()
모든 화면: emit_due_and_sync()      ← 2026-06-16 수정: UI보다 먼저 (1화면 player.tick 과 동일)
모든 화면: refresh_playback_ui()
on_after_tick: timetable highlight + _finalize_playback_if_done
```

### 12.7 버그: 시계만 흐르고 이벤트·포트·애니 없음 (2026-06-16)

**증상**: `t(sim)` 증가, 포트상태 `t=0.00`, 진행률 `대기` 0%, `애니메이션 파일: 재생 없음`.

**원인 (구조 리팩터 자체가 아님)**:

1. `emit_due_items` 가 **첫 `kind=event`** 에서 `can_emit_timeline_event` 게이트에 막히면 **커서를 전진시키지 못함** (`break`).
2. 시계는 `advance_clock_only` 로 계속 증가 → “시간만 흐름”.
3. 게이트 차단 요인: 프리런 직전 **잔류 `json_wall_busy` / `SequenceRunner.is_running()`** (이전 JSON·프리런 잔여).
4. 부가: 재생 emit 시 `json_wall_busy=True` 후 매핑 실패·`_run_gen` 불일치로 sink 가 조용히 return 하면 wall 이 영구 True.

**수정 (전체 원복 불필요)**:

| 항목 | 내용 |
|------|------|
| `_prepare_playback_emit_environment` | bootstrap 직전 화면별 `_halt_screen_json_anim` + gate clear |
| tick 순서 | emit → UI refresh (레거시 `player.tick` 과 동일) |
| `_deliver_playback_timeline_emit` | `_run_gen` 제거, JSON 미기동 시 wall 자동 해제 |
| `_sim_ui_sink_anim_event` | 재생 중 gen 불일치 시 `json_wall_busy` 해제 |
| `get_playback_runtime` | 핫리로드 시 `isinstance` 실패 duck-typing |
| `_tick_playback_timeline` | runtime 없을 때 legacy `player.tick()` 폴백 |

### 12.8 FOUP 공정 끊김 (2026-06-16)

**증상**: FOUP %·t 가 멈췄다가 타임라인 emit 때만 점프. 메인 `t(sim)` 과 FOUP 라벨 `t` 불일치.

**원인**: 프리런 재생에서 FOUP 라벨은 ``FOUP_PROCESS`` progress emit 때만 갱신. 메인 진행현황만 ``playback_time_tick`` 보간.

**수정**: ``_sim_foup_playback_last_by_screen`` + ``_refresh_foup_playback_heartbeat`` — EP 타임라인 heartbeat(0.1s)·진행 heartbeat(0.2s) 에서 ``event_start_sim_time``·``proc_sec`` 기준 보간.

**개념 수정 (2026-06-16)**: FOUP 보간에 ``_apply_playback_step_progress_from_sim``(현재 JSON 애니 ``_sim_anim_active`` 반영)을 쓰면 안 됨. FOUP는 ``_apply_foup_playback_progress_from_sim`` — 타임라인 ``FOUP_PROCESS`` 의 설정 공정시간만 사용.

### 12.4 의도적으로 아직 넣지 않은 것 (다음 Phase)

- `control_sim_playback_foup.py` (FOUP governor) — FOUP proc_sec 혼선 이슈 때문에 보류
- `display_sim_now` wall-clock 외삽 — 1화면 검증 후 필요 시만
- clock/emit Kit 구독 이중화 — 단일 `_tick_playback_timeline` 유지
- heartbeat 0s 스로틀 (매 프레임) — 1화면 동작과 다를 수 있어 보류

### 12.5 검증 체크리스트 (사용자 테스트)

**1화면** — 2026-06-16 사용자 확인: **대체로 통과**

- [x] 프리런 → 재생 → JSON 1개씩, 겹침 없음 (대체로)
- [x] `t(sim)`·진행률 동작 (시계만 흐르던 버그 수정 후)
- [x] FOUP 30~60초 공정 — **FOUP 전용 보간** (`_apply_foup_playback_progress_from_sim`) 적용 후 양호
- [ ] 완료 시 DONE + xlsx export (미재확인)

**2화면** — **미해결, 다음 작업**

- [ ] 화면1·화면2 **독립** 진행 (한쪽 JSON이 다른쪽 시계를 멈추지 않음)
- [ ] 화면2만의 로그 폭주 시 화면1 진행률 지연 없음 (동기 sink만으로 충분한지 미검증)
- [ ] FOUP 화면별 1슬롯 (엔진과 동일 — governor 없이 타임라인만으로 맞는지 미검증)
- [ ] 화면2 포트상태·막대·타임테이블이 1화면과 동일 품질
- [ ] `set_multi_instance_dispatch_mode(True)` 시 main dispatch 간섭 없음

### 12.6 회귀 시 되돌릴 파일

```
control_sim_screen_playback.py          (삭제 가능 — 신규)
control_sim_prerun_playback.py          (advance/emit 분리 부분만 revert)
control_sim_multi_playback.py           (이전 2화면 전용 tick 복원)
control_window.py                       (bootstrap + _tick_playback + FOUP heartbeat 블록)
```

### 12.9 코드 기준 스냅샷 (Phase A 실제 — §5와 차이 주의)

| 항목 | 문서 §5 (목표) | **현재 코드** |
|------|----------------|---------------|
| Kit 구독 | `sim_playback_clock` + `sim_playback_emit` 2개 | **1개** `sim_playback_tick` → `tick_all` |
| UI sim 시간 | `display_sim_now()` 외삽 | **`sim_now()`** 만 (`advance_sim_clock`) |
| tick 순서 | advance → UI → emit | **advance → emit → UI** |
| FOUP 재생 게이트 | `control_sim_playback_foup.py` | **없음** — `_sim_foup_playback_last_by_screen` + heartbeat 보간만 |
| FOUP vs 이벤트 | 별도 슬롯 governor | **`_apply_foup_playback_progress_from_sim`** (FOUP `proc_sec`만, 애니 `_sim_anim_active` 무시) |
| 재생 emit | 동기 sink | ✅ `_deliver_playback_timeline_emit` |
| 2화면 dispatch | 세션 N개 | ✅ `set_multi_instance_dispatch_mode(True)` when len(sessions)>1 |

**bootstrap 직전 필수**: `_prepare_playback_emit_environment` (runner halt + gate clear + FOUP snapshot reset).

---

## 13. 미해결 — 2화면 프리런 재생 (다음 작업)

> **요구사항 SSOT**: [`tbs_control_2_multi_split_requirements_ko.md`](tbs_control_2_multi_split_requirements_ko.md) — 「화면 2개 = 화면 1개를 설정만 다르게 2번」

### 13.1 아직 안 되는 것 (사용자·세션 관찰)

- 화면1은 Phase A + 버그픽스 후 **동작 양호**.
- **화면2** (또는 2분할 동시 재생) 에서는 여전히:
  - 한쪽 재생이 다른쪽 **시계·진행률·JSON** 에 간섭하는 체감
  - 로그/emit 부하로 **지연** (동기 sink 적용했으나 메인 스레드 블로킹은 잔존 가능)
  - 포트·막대·FOUP 라벨 **비대칭** 가능성
- **2화면 전용 tick 로직** 은 Phase A에서 **삭제**됨 → 1·N 동일 경로만 남음. 구조는 맞으나 **격리 불충분** 시 증상 재발.

### 13.2 의심 원인 (수정 우선순위)

| # | 의심 지점 | 파일·심볼 | 설명 |
|---|-----------|-------------|------|
| 1 | 메인 스레드 직렬화 | `_tick_playback_timeline`, `tick_all` | 화면1 `emit` 동기 JSON/USD 가 길면 화면2 `advance`·UI 도 같은 프레임에서 지연 |
| 2 | 전역 ext 상태 | `_sim_progress_last_payload_by_screen`, `_sim_anim_active_by_screen` | 화면 키 누락·덮어쓰기 시 크로스톡 |
| 3 | 애니 워커·러너 | `_sim_runners_by_screen`, `_enqueue_anim_screen_job` | 화면별 분리됐으나 `can_emit_timeline_event`·wall busy 가 화면 간 오염 여부 재확인 |
| 4 | multi dispatch | `set_multi_instance_dispatch_mode` | 2화면 시 main 콜백이 어느 USD 컨텍스트를 치는지 |
| 5 | FOUP 전역 슬롯 (재생) | `_update_sim_progress` FOUP 분기 | governor 없이 **타임라인만** 믿을 때 2화면 EP 동시 RUNNING 가능성 |
| 6 | heartbeat 스로틀 | `_HB_EP_INTERVAL=0.1`, `_HB_PROG_INTERVAL=0.2` | 2화면에서 “멈춤” 체감 악화 가능 — 1화면 OK 후 **2화면만** 완화 검토 |

### 13.3 다음 세션 권장 순서

1. **1화면 회귀** (§12.5) — 어떤 2화면 수정 전후에도 필수.
2. **2화면 재현 시나리오 고정** — 동일 USD·동일 Start·프리런 완료 후 재생, 스크린샷/로그.
3. **프레임 분리 실험** (1화면 깨지 않게):
   - 옵션 A: `tick_all` 을 **화면별 round-robin emit 1 event** (이미 `max_emits_per_screen` 있음 — 화면2 starvation 로그 추가)
   - 옵션 B: clock/UI 구독과 emit 구독 **분리** (§5 목표 — 1화면 회귀 주의)
   - 옵션 C: 2화면일 때만 무거운 USD를 워커로 (범위 큼)
4. **FOUP 2화면**: 엔진 semantics(화면별 1슬롯)와 UI 일치 필요 시 `control_sim_playback_foup.py` **재도입** — 단, proc_sec·MOVE 혼선 재발 방지 (§14.2 참고).
5. 검증 후 §12.5 2화면 체크리스트 갱신.

### 13.4 디버그 시 즉시 볼 것

```text
len(rt.sessions)                          # 2 이어야 함
rt.sessions[1].player.sim_now(1)          # 화면1 시계
rt.sessions[2].player.sim_now(2)          # 화면2 시계 — 서로 독립 증가하는지
_sim_json_wall_busy_by_screen             # 화면별 분리
_sim_runners_by_screen[str(scr)].is_running()
_sim_foup_playback_last_by_screen         # 화면·EP별 FOUP 스냅샷
```

---

## 14. 실패·원복 타임라인 (전체 세션)

### 14.1 1차 — 증상 패치 누적 (실패 → 사용자 원복)

| 시도 | 내용 | 결과 |
|------|------|------|
| FOUP governor | `control_sim_playback_foup.py` START/END 게이트 | `FOUP_PROCESS` progress 미게이트 → EP 동시 RUNNING |
| display_sim_now | UI 외삽 | `t(sim)` 점프·라벨 꼬임 |
| clock/emit 이중 구독 | tick 분리 | 1화면까지 악화 |
| heartbeat 0s | 매 프레임 진행률 | 부작용 불명, 원복에 포함 |
| anchor/lp 패치 | 여러 경로 `_remember_playback_step_anchor` | 2번째 공정 freeze |
| FOUP + MOVE proc_sec | 진행률 분모 혼선 | FOUP 30~60s인데 MOVE `proc_sec` 표시 |

**결과**: 사용자가 **1화면 정상 코드로 전체 원복**. `control_sim_playback_foup.py` 삭제.

### 14.2 2차 — Phase A 구조 리팩터 (2026-06-16)

| 단계 | 내용 | 결과 |
|------|------|------|
| A | `ScreenPlaybackSession` × N, `tick_all` | 구조 반영 ✅ |
| B | 동기 `_deliver_playback_timeline_emit` | 2화면 지연 이론상 개선, **2화면 미검증** |
| C | 첫 배포 직후 | **시계만 흐름** — emit 게이트·runner 잔류 (§12.7) |
| D | `_prepare_playback_emit_environment` 등 | 1화면 이벤트·포트 복구 ✅ |
| E | FOUP heartbeat | 처음엔 **현재 이벤트 proc_sec** 오적용 — 사용자 지적 |
| F | `_apply_foup_playback_progress_from_sim` | FOUP·이벤트 **개념 분리** — 사용자 **양호** 확인 |

**결과**: **1화면 OK**, **2화면 미착수**.

### 14.3 교훈 (2화면 작업 시)

- 1화면 고치려다 2화면 패치 넣지 말 것 — **동일 경로** 유지하되 **상태 키·워커·dispatch** 격리부터 검증.
- FOUP governor 재도입 시 **progress·START·END 세 경로** 동시 설계.
- 문서 §5 전부 한 번에 구현하지 말고 §13.3 순서대로.

---

## 15. 실행층 독립 채널 수정 (2026-06-17)

> **배경**: tick·gate·채널 스레드만 나눠도 JSON 끊김·화면 간 간섭이 남았음. 원인은 **LAM `stop_all`**, **JSON 워커 스레드 USD**, **json_wall 조기 해제**, **FOUP 전역 prim 키** 등 실행층 결합.  
> **목표**: N>1 에서 화면별 완결 파이프라인. **N=1 은 `len(sessions)<=1` 한 갈래만** — 동작 변경 없음.

### 15.1 불변 조건 (검증·재작업 SSOT)

| # | 조건 | 구현 위치 |
|---|------|-----------|
| 1 | 화면1(`ctx=None`) 포함 **전역 `stop_all_*` LAM 경로 금지** (N>1) | `tbs_lam_sequence_engine.stop()` → `stop_*_for_context` 만 |
| 2 | N>1 JSON `_start_job`·halt·runner → **메인 스레드 화면별 직렬 큐** | `_submit_screen_json_on_main` + `dispatch_main` |
| 3 | `json_wall` 해제 = runner idle **+** 해당 ctx motion idle | `try_release_json_wall_when_idle`, `can_emit_timeline_event` motion 체크 |
| 4 | FOUP in-progress/lifted = **`(screen, prim_path)`** | `port_lot_visibility._foup_scope_key`, `screen=` 인자 |
| 5 | N=1 | `_is_multi_viewport_sim` / `len(sessions)<=1` 가드 — 기존 halt·동기 `_start_job` |

### 15.2 변경 파일

| 파일 | 내용 |
|------|------|
| `tbs_lam_sequence_engine.py` | `stop()`: ctx 비어도 `stop_all` 대신 `stop_*_for_context(None)` |
| `control_sim_playback_gate.py` | `can_emit_timeline_event` + motion busy; `try_release_json_wall_when_idle` |
| `control_window.py` | JSON 메인 큐; N>1 조건부 halt; wall 해제 drain+retry; FOUP `screen=` |
| `port_lot_visibility.py` | `screen_from_usd_context_name`, 화면별 FOUP 키 |
| `translate_animation.py` | `is_foup_in_progress_for_context` |

### 15.3 제거·대체된 패턴

| 이전 (문제) | 이후 |
|-------------|------|
| `_sim_anim_workers_by_screen` 에서 `_start_job` 직접 실행 | `_sim_json_main_queues_by_screen` → main dispatch |
| 매 JSON `_halt_screen_json_anim` (N>1) | runner/motion busy 일 때만 halt |
| `set_json_wall_busy(False)` 즉시 | `try_release_json_wall_when_idle` + dispatch 재시도 |
| `_FOUP_IN_PROGRESS_PATHS` prim 경로만 | N>1: `@{screen}:{prim}` |

### 15.4 2화면 검증 체크리스트 (이번 수정 후)

- [ ] 화면1 JSON 시작 시 화면2 LAM/MOVE **끊기지 않음**
- [ ] 화면2 JSON가 끝까지 실행 (중간 halt 없음)
- [ ] 진행률·JSON wall: motion 끝난 뒤 다음 event (점프 완화 여부 관찰)
- [ ] FOUP EP1(화면1) / EP2(화면2) 동시 RUNNING 라벨 **오염 없음**
- [ ] **1화면 회귀** — §12.5 동일

### 15.5 여전히 공유되는 것 (알려진 한계)

- **메인 dispatch**: emit·JSON·UI 가 같은 Kit 프레임에서 경쟁 (화면별 큐로 USD 스레드 안전만 확보)
- **타임라인 시계 vs 커서**: gate 중 `sim_now` 는 흐를 수 있음 — progress heartbeat 설계는 별도 Phase
- **합성 USD 동일 prim 경로**: 시각적 중복은 정상; 상태는 screen 키로 분리

### 15.7 진행률 멈춤·간섭 추가 수정 (2026-06-17 b)

**증상**: 2화면 모두 진행률이 실시간 증가하지 않고 멈췄다 점프; 화면 간 간섭 지속.

**근본 원인**:

1. ``_isolated_channel_tick_worker`` 가 ``dispatch_main`` 호출 시 **USD 컨텍스트 미설정** → 화면1·2 작업이 **legacy 단일 큐**에 몰림.
2. ``tick_session`` 순서가 **emit → UI heartbeat** → 무거운 emit 이 진행률 갱신을 밀어냄.
3. N>1 도 heartbeat 스로틀 **0.2s** (1화면과 동일) → 체감 멈춤.

**수정** (``control_sim_screen_playback.py``):

| 항목 | 내용 |
|------|------|
| ``_dispatch_playback_main`` | 화면별 ``usd_context_for_screen`` push 후 dispatch |
| N>1 tick 순서 | **UI heartbeat → emit** (``ui_before_emit=True``) |
| N>1 진행률 주기 | ``_HB_PROG_INTERVAL_MULTI = 0`` (매 채널 tick 보간) |

**N=1**: ``tick_all`` 순서·0.2s 스로틀 **변경 없음**.

---

```
tbs_lam_sequence_engine.py
control_sim_playback_gate.py
control_window.py          (_submit_screen_json_on_main 블록, _finish_on_main, FOUP screen=)
port_lot_visibility.py
translate_animation.py
```

---

## 16. PlaybackChannel 전면 교체 (2026-06-17)

**목표**: 패치 반복 종료 — 화면별 완결 번들 1회 교체.

### 16.1 PlaybackChannel (``playback_channel.py``)

| 소유 | N>1 동작 |
|------|----------|
| ``SimTimelinePlayer`` | 타임라인 |
| ``morph.tbs_playback_clk_{N}`` | BG 시계 |
| ``morph.tbs_playback_prog_scr_{N}`` | Kit 구독 — **진행률만** (emit/JSON 없음) |
| ctx ``dispatch_main`` | 타임라인 emit |
| ``enqueue_main_job`` | JSON 직렬 큐 |

N=1: 동일 클래스 1인스턴스 — Kit tick ``tick_n1_kit_frame`` (기존 tick_all 순서).

### 16.2 bootstrap

``bootstrap_playback_with_sinks(..., sinks=ChannelTickSinks(...))`` — N>1 루프 자동 기동.  
``start_isolated_playback_channels`` **폐기** (no-op).

### 16.3 원복 파일

```
playback_channel.py
control_sim_screen_playback.py
control_window.py
control_sim_multi_playback.py
```

---

*이 문서는 코드와 함께 유지보수한다. 재생 아키텍처를 바꿀 때는 **§0** → **§16** → **§12.9** 순으로 읽을 것.*
