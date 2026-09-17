# TBS Control 2 — JSON 애니 시작/스텝/화면 격리 유지보수 (2026-09-18)

> **대상:** 프리런 SSOT 재생 중 화면1·화면2 JSON 애니메이션  
> **기간:** 2026-09-17 밤 ~ 2026-09-18 00:15 (약 4시간)  
> **코드 루트:** `source/extensions/morph.tbs_control_2/morph/tbs_control_2/`  
> **이 문서만으로** 증상 → 파일/함수를 찾고, 잘못된 방향으로 고치지 않기 위한 실무 메모.

관련 배경(막대 시계·프리런 SSOT·공정 병렬/애니 직렬)은 아래를 본다. **이 문서는 뷰포트 JSON 애니만.**

- `docs/tbs_control_2_prerun_ssot_plan_ko.md`
- `docs/tbs_control_2_proc_parallel_anim_serial_changelog_ko.md`
- `docs/tbs_control_2_fix_changelog_ko.md`

---

## 0. 절대 규칙 (SSOT) — 이것부터 확인

실무 요구는 두 줄이다. 타이밍 맞추려고 스텝을 건너뛰거나, 배속을 “끊김 수정”으로 쓰지 않는다.

| # | 규칙 | 의미 |
|---|------|------|
| A | **JSON 시작 = 이번 파일 prim 무조건 최초 자세** | 이전 JSON 끝 자세, 잘린 중간 좌표, 현재 화면 자세를 이어 받지 않음. TBS_OFFSET=0 + TIMESAMPLES `start_frame`. |
| B | **JSON 안의 스텝은 설정값대로, 서로 간섭 없이** | `duration` / 이동량 / `step_delay_ms` 만. 다음 스텝·다른 화면이 중간에 `stop` 하거나 TIMESAMPLES writer 가 MOVE 를 덮지 않음. |
| C | **화면1과 화면2는 독립** | 한쪽 JSON 시작이 다른 쪽 MOVE/TIMESAMPLES 를 끄거나 한 틱에 점프시키지 않음. |
| D | **배속은 별도 레이어** | UI 배속·지연 시작 catch-up 만. 시계 동기용 강제 슬립/게이트로 스텝을 늦추지 않음. |

`SIM_PRERUN_PLAN_SSOT` + `_sim_playback_started` 이면 `_is_ssot_playback()` True.

---

## 1. 금지 — 이 세션에서 틀렸던 수정 방향

문제가 “끊김”으로 보여도 **아래를 다시 넣지 말 것.**

| 하지 말 것 | 왜 |
|------------|----|
| `compute_late_start_catchup_mul` 을 1.0 으로 고정 | 배속 점프가 원인이 아님. 사용자는 “JSON 이 부드럽게 이동하지 않고 끊김”이라고 정정함. |
| TIMESAMPLES duration 대기를 건너뛰고 다음 스텝 즉시 | 2초 스텝은 2초 재생 후 순차. “텀이 길다”고 해서 duration 을 없애면 안 됨. |
| 그룹마다 `_gate_wall_anim_to_sim_now` | 막대 시계에 맞추려던 **스텝 사이 강제 대기**. JSON 내부 텀의 원인. JSON **종료 후**에만 남을 수 있음(비 SSOT). |
| 그룹 사이 고정 `+0.05s` | 타이밍 패딩. |
| `SET_PRIM_VISIBILITY` duration 기본 `0.02` + 메인 5초 wait | 설정에 없는 텀. |
| JSON 시작 때 `begin_replay_mode` 를 켜 둔 채 MOVE | 매 틱 시작 프레임을 다시 써서 MOVE 보간이 싸움. |
| `stop_all_translate/rotate` / `stop_prim_*_all_contexts` | 화면1 `ctx=None` 또는 동일 prim_path 가 화면2 를 끊음. |
| occupancy vis 뒤에 `restore_port_lot_prims_to_authoring` (재생 중) | MOVE/ROTATE stop + authoring 스냅 → JSON 도중 끊김. |
| 보이는 매핑 prim 을 리셋 시 FULL 병합 (`merge_visible_mapping_prims_as_occupied`) | 최초 시작에 적재 포트 외 prim 이 다 보임. |

---

## 2. 증상 → 찾을 위치

| 증상 | 먼저 볼 함수 | 파일 |
|------|----------------|------|
| JSON 시작이 이전 끝 자세 / 중간 좌표에서 출발 | `_ensure_pre_json_restore` 계열, `_restore_sim_prim_motion_to_initial`, `TbsLamSequenceRunner.run` 의 `reset_each_start` | `control_window.py`, `tbs_lam_sequence_engine.py` |
| MOVE 300 이 덜 가고 다음 스텝 | `_start_move` kickoff wait, `_execute_group` `_wait_for_motion_complete`, `stop_prim_translate_animation` (잔여 미스냅) | `tbs_lam_sequence_engine.py`, `tbs_lam_translate_animation.py` |
| JSON 안에서 이동이 틱틱 끊김 (배속 점프 아님) | `RuntimeEvaluator.suspend_replay_tick` / `begin_replay_mode` 가 MOVE 중에 켜져 있는지 | `tbs_runtime_evaluator.py`, `tbs_lam_sequence_engine.py` `_execute_group` |
| TIMESAMPLES 앞뒤로 긴 공백 | `_execute_group` 의 duration 대기 vs 예전 10s `_q_main_wait`, `_gate_wall_anim_to_sim_now` 재삽입 여부 | `tbs_lam_sequence_engine.py` |
| TIMESAMPLES 2초를 안 채우고 다음 스텝 | `_start_usd_timeline` 의 `wait_sec` (`duration` 우선) | `tbs_lam_sequence_engine.py` |
| 화면1 재생 중 화면2 JSON 시작 순간 뚝뚝 | `tbs_main_dispatch` 틱 예산, TIMESAMPLES 스냅을 한 틱에 몰지 않는지, `stop_*_all_contexts` | `tbs_main_dispatch.py`, `control_window.py` restore, `port_lot_visibility.py` |
| BP→EP renewal 에 포트 숨김/보임 안 됨 | occupancy delta + plan, layout hide 가 occupied EP 를 다시 끄는지 | `control_sim_playback_plan.py`, `port_lot_visibility.py` |
| 시뮬 최초 시작 때 적재 포트 말고 전부 보이다가 나중에 숨김 | `from_reset` 에서 visible prim FULL 병합 금지, t=0 plan occ | `control_window.py` `_apply_sim_event_state_only`, `port_lot_visibility.py` |
| 재생 중 prim 이 막대 시각에 먼저 숨김 | skip_prim + renewal hold (`_RENEWAL_PRIM_HOLD_SLACK_SEC`) | `control_sim_playback_plan.py` |

---

## 3. 호출 흐름 (화면 공통)

```
프리런 emit (anim_play_start)
  → control_window JSON 워커
  → 이번 job.parsed prim 만 restore (_restore_sim_prim_motion_to_initial, motion_only)
       · 이 ctx + 이 path 만 stop
       · TBS_OFFSET 0 (FOUP 매핑이어도 이번 JSON prim 은 0)
       · TIMESAMPLES start_frame 스냅은 prim 당 별도 dispatch 후 suspend_replay_tick
  → SequenceRunner.run(..., reset_each_start=True)
  → TbsLamSequenceRunner.run
       · SSOT: reset 강제, _start_from_current / snapshot 무시
       · TBS_OFFSET 다시 0 (TIMESAMPLES evaluate 는 SSOT 에서 생략 — restore 가 이미 함)
       · 그룹 순차: kickoff 완료 후 duration, MOVE/ROTATE 잔여 폴링, TIMESAMPLES 는 duration 후 suspend
  → JSON 끝: 자기 prim motion complete → (비 SSOT만 sim_now gate) → 자기 path stop
```

화면 키:

- 화면1 USD ctx = `None` (default)
- 화면2 = `morph_tbs_split_aux_*` (`_usd_context_name_for_sim_screen`)
- MOVE 키 = `anim_key(ctx, prim_path)` (`tbs_usd_stage_context.py`)
- TIMESAMPLES registry/evaluator = `get_split_runtime_for_screen` / `get_split_runtime_for_usd_context`
- fallback `_tbs_registry` / `_tbs_evaluator` 는 **화면1과 섞이면 안 됨**. split runtime 이 None 이면 교차 pause 위험.

---

## 4. 파일 지도

경로 접두: `source/extensions/morph.tbs_control_2/morph/tbs_control_2/`

### 4.1 `tbs_lam_sequence_engine.py` — JSON 스텝 러너 (화면1·2 공통)

| 심볼 | 역할 |
|------|------|
| `TbsLamSequenceRunner.run` | JSON 실행. SSOT 이면 `reset_each_start=True`. `_start_from_current` / `_start_snapshot` 무시. |
| `_reset_tbs_offset_ops_for_paths` | **지정 path만** MOVE/ROTATE stop + TBS_OFFSET 0. 채널 전체 stop 금지. |
| `_collect_prim_paths_for_reset` | 이번 JSON 의 MOVE/ROTATE/vis/TIMESAMPLES prim. |
| `_execute_group` | 그룹 duration 대기. `step_delay_ms` 만 그룹 사이 슬립. `_gate_wall_anim_to_sim_now` **호출 안 함**. MOVE/ROTATE 만 `_wait_for_motion_complete` (replay 리스트 빈 배열). 이후 `suspend_replay_tick`. |
| `_start_move` / `_start_rotate` | `_q_main_wait(..., timeout=5.0, priority=True)` 로 **kickoff 끝난 뒤** duration 반환. fire-and-forget 이면 duration 시계가 애니 시작 전에 돌아 목표가 잘림. |
| `_start_usd_timeline` | TIMESAMPLES: `begin_replay_mode` + `start(reset=True)`. `wait_sec` = JSON `duration`(>0.05) 아니면 프레임 추정. 2초면 2초. |
| `_start_set_prim_visibility` | duration 키 없으면 tail=0. `_q_main` fire-and-forget. |
| `_wait_for_motion_complete` | SSOT 타임아웃 연장 0회. JSON **종료** 시 전체 스텝 잔여에도 사용. |
| `_gate_wall_anim_to_sim_now` | 스텝 사이에 쓰지 말 것. JSON 끝 비 SSOT 만. |
| `pause_timesample_replays_for_paths` | **이 runner registry + 이 path** 만 pause. |

### 4.2 `tbs_runtime_evaluator.py` / `tbs_playback_scheduler.py`

| 심볼 | 역할 |
|------|------|
| `begin_replay_mode` | prim 을 `_evaluator_active_prims` 에 넣음 → **매 틱** xform default write. |
| `suspend_replay_tick` | active set 에서만 제거. default 는 유지 (끝/시작 자세 홀드). `end_replay_mode` 는 default 를 지워 leftover 끝 자세로 돌아감 → JSON 시작 스냅에 쓰지 말 것. |
| `_on_update_option_e` | playing 이 아니어도 active prim 은 evaluate. MOVE 중 active 면 싸움. |
| `_on_update` dt 상한 | hitch 시 `0.08s` 클램프 (예전 0.25 는 한 틱 점프가 큼). |

MOVE 구간에는 TIMESAMPLES writer 가 **꺼져 있어야** 한다. TIMESAMPLES **스텝이 실제로 시작**할 때만 `begin_replay_mode`.

### 4.3 `control_window.py`

| 심볼 | 역할 |
|------|------|
| JSON 워커 / `_usd_context_name_for_sim_screen` | 화면별 ctx. |
| pre-json restore 호출부 (`preserve_peer_channel`, `motion_only=True`, `include_registry_paths=False`) | **이번 JSON(+동일 레일 last_steps) path 만**. 타 화면 runner 경로 금지. |
| `_restore_sim_prim_motion_to_initial` | 재생 중 `stop_channel_animations_for_paths` (채널 전체 stop 아님). `motion_only` 이면 FOUP 매핑이어도 **이번 JSON prim TBS_OFFSET 0**. TIMESAMPLES 스냅은 `ts_snap_jobs` 로 **prim 당 dispatch**. |
| `_apply_sim_event_state_only` | 재생 중 occupancy vis 뒤 `sync_port_lot_positions_after_visibility` **금지** (reset/seek 만). visible FULL 병합 금지. |

### 4.4 `tbs_main_dispatch.py`

| 심볼 | 역할 |
|------|------|
| `set_multi_instance_dispatch_mode` | 분할 재생 시 True (`control_sim_screen_playback.py`). |
| `_ctx_queues` | ctx 별 큐. `priority=True` 는 **그 ctx 큐 앞**이지 타 ctx 를 빼앗지 않음. |
| `_on_update` | 멀티: ctx 당 1건 + `_TICK_BUDGET_SEC=0.006`. 한 화면 JSON 시작 evaluate 가 32건을 한 틱에 실행하지 않음. |

한 `fn()` 이 수백 ms 짜리 evaluate 면 그 한 건은 여전히 메인를 잡는다. 그래서 TIMESAMPLES 스냅을 prim 단위로 쪼갰다.

### 4.5 `tbs_lam_translate_animation.py` / `tbs_lam_rotate_animation.py`

| 심볼 | 역할 |
|------|------|
| `_animations` 키 | `anim_key(ctx, path)` |
| `stop_prim_translate_animation(path, ctx)` | **그 ctx 만**. 잔여 거리를 목표로 스냅하지 않음 → 다음 스텝이 먼저 stop 하면 300이 덜 감. |
| `stop_prim_*_all_contexts` | 포트 LOT 복원 등에서 **쓰지 말 것** (화면 교차). |
| `_on_update` dt | `>0.05` 클램프. 타 화면 hitch 한 틱이 보간 점프하지 않게. |

### 4.6 `sim_channel_scope.py`

| 심볼 | 역할 |
|------|------|
| `stop_channel_animations` | **한 ctx** 의 MOVE/ROTATE. 레거시 전역 `stop_all` 호출 금지. |
| `stop_channel_animations_for_paths` | 재생/병렬 JSON 시작·종료. |

### 4.7 `port_lot_visibility.py`

| 심볼 | 역할 |
|------|------|
| `_stop_mapped_prim_motion_for_ctx` | legacy + LAM translate/rotate 를 **해당 ctx 만** stop. |
| `apply_port_lot_prim_visibility_for_context` | 그 화면 stage 만 vis. 재생 중 델타면 레이아웃 hide 재적용 안 함. |
| `restore_port_lot_prims_to_authoring` | 재생 occupancy 경로에서 호출 금지. |

### 4.8 기타 (이 세션에서 건드린 주변)

| 파일 | 메모 |
|------|------|
| `sequence_engine.py` | LAM runner `reset_each_start=True`. JSON 끝은 자기 path 만 stop (재생/병렬). |
| `control_sim_playback_plan.py` / `gate.py` | 막대·renewal prim 보류. 애니 스텝 duration 을 바꾸지 말 것. |
| `tbs_split_composed_loader.py` | 화면별 registry/evaluator. restore 가 여기 실패하면 fallback 공통 evaluator → 교차 pause. |

---

## 5. 규칙 A — JSON 시작 최초 자세 (구현)

순서 (둘 다 이번 JSON path 만):

1. **pre-json** `_restore_sim_prim_motion_to_initial(..., motion_only=True)`  
   - path-scoped stop  
   - TBS_OFFSET 0 (`preserve_foup_offsets` 여도 **motion_only 는 매핑 FOUP 제외하지 않음**)  
   - TIMESAMPLES: `begin_replay` + `start(reset=True)` + `stop` + `suspend_replay_tick` (prim 당 큐)
2. **러너** `reset_each_start`  
   - SSOT: 강제 True, `_start_from_current`/`_start_snapshot` 비움  
   - TBS_OFFSET 0 다시  
   - SSOT 는 TIMESAMPLES evaluate **생략** (1에서 이미 함. 한 틱에 두 번 evaluate 하면 타 화면 끊김)

`_start_from_current` 를 SSOT 에서 다시 켜면 **잔류 자세 재생**이 돌아온다.

---

## 6. 규칙 B — 스텝이 설정값대로 (구현)

한 그룹:

1. MOVE/ROTATE: 메인 kickoff 완료를 기다린 뒤 JSON `duration/speed` 만큼 `_wait_for`
2. 같은 그룹 MOVE/ROTATE 가 아직 busy 면 짧게 `_wait_for_motion_complete` (replay 대기 없음, 연장 없음)
3. TIMESAMPLES: JSON duration (예: 2s) 만큼 대기한 뒤 `suspend_replay_tick`
4. 다음 그룹 앞 슬립 = 다음 스텝 `step_delay_ms` 뿐
5. DELAY 스텝 = 그 `duration`

다음 스텝이 같은 prim 에 `stop_prim_*` 를 걸기 **전에** 이전 MOVE 가 끝나야 한다. stop 은 중간 좌표를 목표로 스냅하지 않는다.

TIMESAMPLES writer 는 해당 스텝 재생 중에만 active. 그 앞 MOVE / 그 뒤 MOVE 와 동시에 켜 두지 말 것.

---

## 7. 규칙 C — 화면 격리 (구현)

이미 막은 것:

- 재생 JSON 시작: `stop_channel_animations_for_paths` + 자기 ctx
- `stop_translate_animations_for_context(None)` 는 빈 ctx 만 (화면1). 화면2 는 named ctx.
- occupancy vis 재생 중 authoring restore 스킵
- 포트 모션 stop: `_stop_mapped_prim_motion_for_ctx`

남은 물리 한계:

- Kit 메인 스레드는 하나. 한쪽 `evaluate_instance_now` 가 길면 반대 화면은 그 프레임 동안 멈춘 뒤 `dt` 클램프(0.05)로 점프를 줄인다.
- split runtime lookup 실패 → `_tbs_evaluator` 공유 → 같은 `prim_path` TIMESAMPLES pause 가 교차될 수 있음. **화면2 JSON 이 화면1 registry 를 쓰는지 먼저 확인.**

---

## 8. 배속 (규칙 D)

- 스텝 `duration / live_speed`
- SSOT `_sleep` → `_live_speed_sleep` (UI 배속)
- `compute_late_start_catchup_mul` (`control_sim_playback_gate.py`) — 시작이 밀렸을 때만. 끊김 수정으로 끄지 말 것.
- MOVE 세그먼트 duration 도 `/sp`. animator `get_csv_play_anim_dt_scale` 는 stub 1.0 (이중 배속 없음).

---

## 9. 이번 작업에서 손댄 파일 (git working tree)

| 파일 | 이 문서 범위에서의 요지 |
|------|--------------------------|
| `tbs_lam_sequence_engine.py` | 시작 자세, 스텝 순차, MOVE kickoff wait, TIMESAMPLES duration, suspend, 게이트 제거 |
| `tbs_runtime_evaluator.py` | `suspend_replay_tick`, dt 0.08 |
| `tbs_playback_scheduler.py` | `suspend_replay_tick` 위임 |
| `control_window.py` | pre-json 초기화, TIMESAMPLES 스냅 분할, occupancy restore 스킵, FOUP path 도 JSON 시작 0 |
| `tbs_main_dispatch.py` | ctx 큐 + 틱 예산 |
| `tbs_lam_translate_animation.py` / `tbs_lam_rotate_animation.py` | dt 0.05 |
| `port_lot_visibility.py` | ctx 스코프 stop, visible FULL 병합 삭제 |
| `sequence_engine.py` | 자기 path 만 정리, reset_each_start True |
| `sim_channel_scope.py` | path/ctx 스코프 |
| `control_sim_playback_plan.py` / `gate.py` | 막대·renewal prim (애니 duration 과 분리) |

---

## 10. 재발 시 점검 순서 (AI)

1. 증상이 §2 표 어느 줄인지 고른다.  
2. **금지 표(§1)를 먼저 본다.** catch-up 끄기 / TIMESAMPLES 스킵 / 그룹 sim_now 게이트를 제안하지 말 것.  
3. 해당 함수가 규칙 A/B/C 를 깨는지 읽는다.  
   - A: 이번 JSON path 만 0 + start_frame 스냅 + writer suspend  
   - B: kickoff 후 duration, MOVE 완료 후 다음, TIMESAMPLES 는 자기 duration  
   - C: ctx+path, split runtime, dispatch 예산, all_contexts 금지  
4. 화면2 이면 `get_split_runtime_for_usd_context(ext, aux_name)` 이 None 인지 확인.  
5. 막대/포트 패널 시각은 `sim_now`+프리런 plan. 애니 스텝 wall 과 맞추려고 JSON 내부를 늘리지 말 것.

---

## 11. 아직 남는 한계

- Omniverse Kit 뷰포트는 이 환경에서 실행 검증하지 못함. 회귀는 앱에서 화면1 재생 중 화면2 JSON 기동, 그 반대를 눈으로 본다.
- 메인 스레드 단일. 한 prim 의 `evaluate_instance_now` 가 길면 반대 화면이 그 프레임만 멈출 수 있다 (점프는 dt 클램프로 완화).
- `move_bp*_ep*.json` 이 빈 `[]` 이면 occupancy delta 가 숨김/보임 경로다. JSON vis 스텝이 없음.

---

## 12. 빠른 검색 키워드

```
_is_ssot_playback
suspend_replay_tick
begin_replay_mode
_reset_tbs_offset_ops_for_paths
_restore_sim_prim_motion_to_initial
ts_snap_jobs
_wait_for_motion_complete
_gate_wall_anim_to_sim_now
stop_channel_animations_for_paths
_stop_mapped_prim_motion_for_ctx
set_multi_instance_dispatch_mode
_TICK_BUDGET_SEC
anim_key
```
