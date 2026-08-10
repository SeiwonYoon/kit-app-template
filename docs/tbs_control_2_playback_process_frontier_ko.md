# TBS Control 2 — 재생 공정 경계 (Playback Process Frontier)

> **목적**: gated JSON 재생 중 emit 커서 · `sim_now` · plan/FOUP/진행률 lookup 이 **같은 공정 경계**를 쓰게 한다.  
> **증상 패치 금지**: port sync 만으로 clamp, 화면별 예외, “LOT 잠깐 숨기기” 등.

## 불변식

`json_wall_busy` **또는** `proc_gate`(직전 gated 이벤트 공정 미종료) 인 동안:

```
다음 gated emit     ≤  frontier
sim_now / UI t      ≤  frontier
plan/display lookup =  min(sim_now, frontier)
```

- **frontier** = 현재/직전 gated 이벤트의 **공정 종료** (`t0 + proc_sec` / `t_proc_end`)
- **port sync 시각이 아님**
- JSON wall 이 먼저 풀려도 **proc_wait** 로 다음 ARRIVED/MOVE 는 `sim_now >= frontier` 까지 금지

## 왜 필요한가

1. emit 만 막고 `sim_now` 가 앞서면 plan 이 미emit 점유를 먼저 보여 ARRIVED 가 생략된 것처럼 보임.
2. JSON 만 먼저 끝나 wall 이 풀리면, 진행률은 아직 이전 ARRIVED 인데 다음 ARRIVED 가 나감.
3. 포트 헤더 t 와 FOUP t 가 갈라지면(이중 시계) 같은 버그로 보인다 → UI 도 동일 frontier 축.

## API (SSOT)

| 함수 | 역할 |
|------|------|
| `set_proc_gate_end` / `get_proc_gate_end` | gated emit 시 `t0+proc` 기록 |
| `can_emit_timeline_event` | wall **또는** proc_wait 이면 다음 gated 금지 |
| `playback_process_frontier_sim` | proc_gate ∪ wall active → frontier |
| `apply_playback_frontier` / `resolve_playback_ui_axes` | display·plan·FOUP heartbeat 공통 |
| `advance_clock_only` | `clamp_sim_now_max(frontier)` |

## 요구사항 구분 (오해 금지)

| 현상 | 판정 |
|------|------|
| **ARRIVED EP1 진행 중** EP2 포트가 다음 LOT 으로 참 | **버그** — emit/plan/renewal 이 EP1 공정 경계를 넘김 |
| **EP1 FOUP 진행 중** EP2 ARRIVED 가 진행·포트 반영 | **정상** — FOUP 와 다음 EP ARRIVED 는 겹쳐도 됨 |

FOUP 끝날 때까지 다음 OHT/ARRIVED 를 엔진에서 미룰 필요 **없음**.

## 검증

- [ ] ARRIVED EP1 진행률 < 100% 인데 EP2 에 다음 LOT 이 먼저 안 참
- [ ] ARRIVED EP1 진행 중 다음 ARRIVED JSON 이 시작되지 않음 (proc_wait / frontier)
- [ ] 포트 헤더 t 와 FOUP/진행 t 가 동일 축
- [ ] renewal wall 중 **다른 포트에 미래 LOT** 이 잠깐 떴다 사라지지 않음 (delta hold)
- [ ] EP1 FOUP ∥ EP2 ARRIVED 동시 진행은 허용 (막지 않음)

## LOT 깜빡임 근본 원인 (2026-08-03)

`apply_playback_renewal_from_wall` 이 plan `occ_full` **전체 스냅샷**(sync_t 시점)을 hold 해
`sim_now < sync_t` 동안 패널을 덮어씀 → sync_t 에 이미 반영된 **다른 포트의 이후 LOT** 이
현재 공정에 안 나온 것처럼 보였다가, hold 해제·다음 공정에서 사라짐.

**수정:** hold 는 이벤트 delta(ARRIVED→dest=lot 등)만 저장하고 `ports_at(sim_now)` 위에 병합.
