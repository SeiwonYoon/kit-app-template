# TBS 시뮬 제어창 — 클릭·설정별 코드 추적 가이드

JavaScript에서 `button.onclick → handler → setInterval → DOM 갱신` 을 따라가듯,  
Kit/Python 시뮬도 **「어떤 UI 동작 → 어떤 함수 → 어떤 데이터 → 어떤 파싱/실행」** 순으로 읽는 문서입니다.

**기준 패키지:** `morph.tbs_control_2`  
**핵심 파일:** `control_window.py`, `simulation_engine.py`, `control_sim_prerun_playback.py`

---

## 0. JavaScript 비유 ↔ Python/Kit 대응표

| JS에서 흔한 것 | 이 프로젝트에서 |
|----------------|-----------------|
| `button.addEventListener('click', startSim)` | `ui.Button(..., clicked_fn=lambda: on_sim_start_clicked(ext))` |
| `setInterval(tick, 16)` | `app.get_update_event_stream().create_subscription_to_pop(_tick_playback)` |
| `fetch('/api/rules')` | `_load_event_animation_rules()` → `config/event_animation_rules.json` |
| 이벤트 버스 / Redux dispatch | `_sim_log_queue.put_nowait((kind, payload))` |
| 메인 스레드 UI만 | `_drain_sim_log_queue` (Kit update 콜백에서만 UI touch) |
| 녹화 후 재생 | 프리런 `SimPreRunResult.items` → `SimTimelinePlayer` 재생 |

---

## 1. UI 진입점 (버튼·설정)

### 1-1. 「시작」 버튼

**UI 정의** (`control_window.py` ~2453):

```python
ui.Button("시작", width=72, clicked_fn=lambda: on_sim_start_clicked(ext))
```

**진입 함수:** `on_sim_start_clicked(ext)` — **~6052**

---

### 1-2. 「정지」 / 「리셋」 버튼

| 버튼 | 함수 | 역할 |
|------|------|------|
| 정지 | `on_sim_stop_clicked(ext)` | 프리런 스레드·재생 플레이어·애니 중단 |
| 리셋 | `on_sim_reset_clicked(ext)` | 정지 + prim/포트 UI 초기화 |

---

### 1-3. 공정시간·LOT 수 등 숫자 필드

**별도 “적용” 버튼 없음.** 값은 `ui.SimpleFloatModel` / `SimpleBoolModel`에 즉시 반영되고,  
**「시작」을 누를 때** `_capture_per_screen_sim_settings()` 가 한 번에 읽습니다.

| UI 라벨 | ext 변수 | 스냅샷 dict 키 |
|---------|----------|----------------|
| OHT→EP min~max | `_sim_oht_bp1_min/max_model` | `oht_bp1_min`, `oht_bp1_max` |
| OHT→IN/OUT min~max | `_sim_oht_inout_min/max_model` | `oht_inout_min`, `oht_inout_max` (없으면 `oht_bp1_*` 폴백) |
| IN/OUT→BP | `_sim_bp1_bp_min/max_model` | `bp1_bp_min`, `bp1_bp_max` |
| BP→EP | `_sim_bp_ep_min/max_model` | `bp_ep_min`, `bp_ep_max` |
| EP→OHT | `_sim_ep_oht_min/max_model` | `ep_oht_min`, `ep_oht_max` |
| LOT 수 | `_sim_lot_count_model` | `lot_count` |
| 초기 적재 체크 | `_sim_init_ep1_model` 등 | `init_ep1`, `init_inout`, … |
| 시뮬 속도배율 | `_sim_speed_model` | (스냅샷 제외, 재생 시 직접 읽음) |
| 공정설정 시간 우선 | `_sim_process_time_priority_model` | (전역, `_timing_and_init_from_snapshot`에서 읽음) |

**기본값 생성:** `build_control_window()` ~2079–2089 (`SimpleFloatModel(5.0)` 등)

---

## 2. 「시작」 클릭 후 전체 파이프라인 (요약 다이어그램)

```
on_sim_start_clicked(ext)
  ├─ [준비] on_sim_stop_clicked, UI/타임라인 상태 초기화
  ├─ [설정] _capture_per_screen_sim_settings() → dict
  │         _timing_and_init_from_snapshot() → SimulationTimingConfig + SimulationInitConfig
  ├─ [엔진] TBSSimulationEngine(...).start()     ← SimPy 프로세스 시작
  ├─ [프리런 스레드] _prerun_thread_body
  │         prerun_engine_to_timeline(engine)    ← tick(1e6) 반복, items 수집
  │         ext._sim_prerun_results_by_screen = {1: SimPreRunResult, ...}
  └─ [메인 스레드] _drain_sim_log_queue (매 프레임)
            프리런 완료 감지 → SimTimelinePlayer.start()
            _tick_playback (매 프레임) → player.tick()
              kind=event → post_sim_anim_event → handle_sim_event_for_animation → JSON 실행
              kind=progress → post_sim_progress_update → _update_sim_progress → 막대그래프
              kind=log → post_sim_history_line
```

---

## 3. Phase A — `on_sim_start_clicked` 상세 (~6052)

### Step A-1. 이전 실행 정리

```python
on_sim_stop_clicked(ext)
_restore_sim_prim_motion_to_initial(ext)
ext._sim_run_gen += 1          # 이전 큐 이벤트 무시용 세대 토큰
_rebuild_sim_monitor_split_ui(ext)
```

### Step A-2. UI → 스냅샷 dict

**함수:** `_capture_per_screen_sim_settings(ext)` — **~1213**

**데이터 예시 (`snap_1`):**

```json
{
  "ep_count_idx": 0,
  "lot_count": 6,
  "spawn_min": 15.0,
  "spawn_max": 40.0,
  "pue_min": 50.0,
  "pue_max": 70.0,
  "oht_bp1_min": 5.0,
  "oht_bp1_max": 10.0,
  "bp1_bp_min": 5.0,
  "bp1_bp_max": 10.0,
  "bp_ep_min": 5.0,
  "bp_ep_max": 10.0,
  "ep_oht_min": 5.0,
  "ep_oht_max": 10.0,
  "foup_proc_min": 30.0,
  "foup_proc_max": 60.0,
  "init_inout": false,
  "init_bp1": false,
  "init_ep1": true,
  "fault_ep2": false
}
```

### Step A-3. 스냅샷 → 설정 객체

**함수:** `_timing_and_init_from_snapshot(ext, snap_1)` — **~1461**

**출력 1 — `SimulationTimingConfig`** (`simulation_engine.py` ~134):

- 역할: **구간별 소요시간 난수 범위만** 보관
- 엔진에서 `self._timing.rand_oht_to_bp1()` 등으로 사용

**출력 2 — `SimulationInitConfig`** (`simulation_engine.py` ~230):

- 역할: **시작 조건** (EP 개수, 초기 적재 포트, LOT 목표 수, 공정시간 우선)
- 예: `initial_full_ports=["EP1"]`, `max_oht_lots=6`, `ep_count=2`

### Step A-4. 엔진 생성·시작

**함수:** `TBSSimulationEngine(...)` — `simulation_engine.py` ~345  
**시작:** `engine.start()` — **~804**

`start()` 내부에서 하는 일:

1. `self._presample_fill()` — 난수 풀 미리 채움
2. `_apply_initial_full_ports()` — 체크된 포트에 LOT 올림 (이벤트 없음)
3. `env.process(_lot_spawn_timer())`, `_pickup_event_timer()`, `_run_serial_flow()` — SimPy 코루틴 등록

**엔진 메모리 상태 예시 (`self.ports`):**

```python
{
  "INOUT": None,           # EMPTY
  "BP1": None,
  "EP1": Lot(lot_id="LOT_A1", foup_id="FOUP_A1", ...),  # init_ep1 체크 시
  ...
}
```

### Step A-5. 프리런용 콜백 (UI 안 감)

엔진 생성 시 (~6579):

```python
TBSSimulationEngine(
    ...
    on_log=lambda _line: None,      # 프리런 중 UI 로그 안 보냄
    on_event=lambda _payload: None,
    on_progress=lambda _payload: None,
    on_gate=lambda payload: float(_estimate_anim_duration_for_gate_payload(ext, payload)),
)
```

`on_gate`만 살아 있음 → **JSON 길이 추정**은 프리런에서도 수행 (대기시간 `max(공정,애니)` 계산용).

### Step A-6. 프리런 스레드 기동 (~6777)

```python
th_pr = threading.Thread(target=_prerun_thread_body, ...)
th_pr.start()
return   # ← 실시간 tick 루프는 여기서 return 으로 도달하지 않음
```

### Step A-7. UI 큐 드레인 구독 (~6760)

```python
ext._sim_log_ui_sub = app.get_update_event_stream().create_subscription_to_pop(
    lambda e: _drain_sim_log_queue(ext),
    name="morph.tbs_control_2:sim_log_ui_drain",
)
```

**JS 비유:** `requestAnimationFrame` / `setInterval` 로 큐를 비우는 루프.

---

## 4. Phase B — 프리런 (시뮬 미리 돌리기)

### 진입

**함수:** `_prerun_thread_body` → `prerun_engine_to_timeline(screen, engine)`  
**파일:** `control_sim_prerun_playback.py` ~141

### B-1. 콜백을 “녹화기”로 교체

```python
items: List[SimTimelineItem] = []

def on_event(payload: dict):
    items.append(SimTimelineItem(
        t=float(payload["sim_time"]),
        kind="event",
        payload=dict(payload),
    ))

engine._on_event = on_event
engine._on_log = on_log
engine._on_progress = on_progress
```

**중요:** 프리런 중에는 `handle_sim_event_for_animation` / JSON **실행 안 함**. 리스트에만 쌓음.

### B-2. `engine.tick(1e6)` 루프

```python
while not engine.is_done:
    engine.tick(1_000_000)
```

**`tick` 내부** (`simulation_engine.py` ~976):

1. `_sim_budget_sec += 1e6` (시간 예산 추가)
2. `while budget >= next_event: env.step()` — SimPy가 `env.now` 전진
3. step 처리 중 `_emit_event`, `_wait_with_progress` 등이 **녹화 콜백** 호출

### B-3. 엔진이 이벤트를 만드는 예 (OHT→IN/OUT)

**함수:** `_load_lot_to_inout` — `simulation_engine.py` ~1318

```python
oht_time = self._timing.rand_oht_to_bp1()          # 예: 7.3
anim_wait = self._request_gate({                   # on_gate → 8.0초 (JSON 추정)
    "seq": "ARRIVED", "port_id": "INOUT", ...
})
aw_u, total_wait, _ = self._proc_anim_pair(7.3, 8.0)  # → total_wait = 8.0

self._emit_event({"seq": "ARRIVED", "port_id": "INOUT", "lot_id": "LOT_1"})
# → on_event 녹화: SimTimelineItem(t=7.3, kind="event", payload={...})

yield self.env.process(self._wait_with_progress(total_sec=8.0, ...))
# → on_progress 녹화: RUNNING/DONE progress 여러 건

self._set_port("INOUT", "ARRIVED", "FULL", lot)    # ports["INOUT"]=lot (메모리만)
```

### B-4. 프리런 결과 저장

**타입:** `SimPreRunResult` (`control_sim_prerun_playback.py` ~19)

```python
SimPreRunResult(
    screen=1,
    final_sim_time=342.5,
    total_est_sec=350.0,
    items=(
        SimTimelineItem(t=0.0,  kind="progress", payload={"sim_time":"0.00", "timeline_only":"1", ...}),
        SimTimelineItem(t=7.3,  kind="event",    payload={"seq":"ARRIVED", "port_id":"INOUT", "lot_id":"LOT_1", "ports_occupancy":{...}}),
        SimTimelineItem(t=7.3,  kind="log",      payload="[SIM] ..."),
        SimTimelineItem(t=16.5, kind="event",    payload={"seq":"MOVE_TRANSFERING", "from_port_id":"INOUT", "to_port_id":"BP1", ...}),
        ...
    ),
)
```

**저장 위치:**

```python
ext._sim_prerun_results_by_screen = {1: result, 2: result2, ...}
ext._sim_prerun_done_evt.set()
```

### B-5. 이벤트 payload 예시 (녹화되는 dict)

```json
{
  "seq": "ARRIVED",
  "port_id": "INOUT",
  "lot_id": "LOT_1",
  "sim_time": "7.30",
  "tbs_sim_screen": "1",
  "ports_occupancy": {
    "INOUT": "",
    "BP1": "",
    "EP1": "LOT_A1",
    "EP2": "",
    ...
  },
  "foup_proc_active_ep": ""
}
```

`ports_occupancy`: 포트명 → LOT ID 문자열. **빈 문자열 = EMPTY**.

---

## 5. Phase C — 재생 (녹화 테이프 → UI·애니)

### C-1. 프리런 완료 감지

**함수:** `_drain_sim_log_queue(ext)` — **~4065** (매 Kit update)

```python
if ext._sim_prerun_done_evt.is_set() and not ext._sim_playback_started:
    results = ext._sim_prerun_results_by_screen
    # 타임테이블 텍스트 출력: _build_prerun_timetable_text(results)
    player = SimTimelinePlayer(results, emit_fn=_emit, speed_supplier=_speed)
    player.start()
    ext._sim_playback_ui_sub = update_stream.subscribe(_tick_playback)
```

### C-2. 매 프레임 재생 tick (≈ setInterval)

**함수:** `_tick_playback(ext)` — **~4304**  
→ `player.tick()` — `control_sim_prerun_playback.py` ~103

```python
t_sim = wall_elapsed * speed_multiplier   # 재생 시계
t_sim = min(t_sim, final_sim_time)

while cursor < len(items) and items[cursor].t <= t_sim:
    emit_fn(items[cursor].kind, items[cursor].payload, screen)
    cursor += 1
```

### C-3. `emit_fn` 분기 (~4144)

| kind | 호출 | 다음 단계 |
|------|------|-----------|
| `log` | `post_sim_history_line` | [SIM] 이력 라벨 |
| `event` | `post_sim_anim_event` | **§6 JSON 매핑·실행** + **§7 포트 패널** |
| `progress` | `post_sim_progress_update` | **§8 진행현황·막대그래프** |

### C-4. 10Hz 막대 전용 heartbeat

`_tick_playback` (~4347): 0.1초마다 `timeline_only` progress emit → 막대만 전진.

---

## 6. Phase D — 이벤트 → JSON 매핑 → 실행

### D-1. 큐 적재

**함수:** `post_sim_anim_event(ext, payload)` — **~3402**

```python
_sim_log_queue.put_nowait((SimUiQueueKind.ANIM_EVENT, payload))
```

### D-2. 큐 소비

**함수:** `_drain_sim_log_queue` → `_dispatch_sim_ui_queue_item` → `_sim_ui_sink_anim_event` — **~3682**

순서:

1. `apply_port_lot_prim_visibility(occ)` — 3D LOT prim show/hide
2. `_update_port_occupancy_panel(ext, occ, sim_time)` — **§7**
3. `handle_sim_event_for_animation(ext, payload)` — **~5593**

### D-3. JSON 경로 결정 (`handle_sim_event_for_animation`)

```
seq_raw = payload["seq"]                    # 예: "ARRIVED"
seq = SIM_SEQ_ALIAS.get(seq_raw, seq_raw)   # → "EAPEIS_PORT_ARRIVED"

xml_text = xml_generator.build_xml_string(seq, port_id=...)
parsed = xml_generator.parse_xml_string(xml_text)
# parsed["sequence_name"] == "EAPEIS_PORT_ARRIVED"
# parsed["port_id"] == "INOUT"

mapping_payload = { ...payload, seq, from, to, port 정규화 ... }

mapped_json, meta, rule, source = _resolve_event_animation_entry(seq_for_mapping, mapping_payload)
```

**매핑 함수:** `_resolve_event_animation_entry` — **~569**

1. `_resolve_rule_entry` → `config/event_animation_rules.json` (~519)
2. 실패 시 `_resolve_event_case_map_entry` → `event_animation_map.json`

**rules 매칭 입력 예:**

```python
seq = "EAPEIS_PORT_ARRIVED"
mapping_payload = {
  "seq": "EAPEIS_PORT_ARRIVED",
  "port_id": "INOUT",
  "from_port_id": "",
  "to_port_id": "",
  "ports_occupancy": {"INOUT": "", "EP1": "LOT_A1", ...},
}
```

**rules 파일에서 매칭되는 한 줄:**

```json
{
  "name": "arrived_inout",
  "when": { "sequence": "EAPEIS_PORT_ARRIVED", "port": "INOUT" },
  "use": { "json": "data/sim_sequences/arrived_inout.json" }
}
```

**반환값:**

```python
mapped_json = "data/sim_sequences/arrived_inout.json"
meta = {"runner": "sequence_editor", "description": "IN/OUT 안착"}
rule = "arrived_inout"
source = "rules"
```

### D-4. JSON 파일 파싱·실행

**함수:** `_execute_mapped_sequence_stub(ext, seq, payload, mapped_json, ...)` — **~781**

```python
parsed_steps = json.loads(Path(mapped_json).read_text())   # list[dict]
est_total = _estimate_sequence_total_duration_sec_for_log(parsed_steps)

# 큐에 job 적재 (동시 run 방지)
_sim_anim_pending.append({
    "steps": parsed_steps,
    "json_path": mapped_json,
    "tbs_sim_screen": "1",
    ...
})
_start_job(job)  # 내부에서 SequenceRunner.run(steps, speed_scale=...)
```

**JSON step 예시 (`arrived_inout.json` 일부):**

```json
[
  {
    "type": "TIMESAMPLES_REPLAY",
    "prim": "/World/...",
    "duration": 2.0,
    "play": { "start_frame": 0, "end_frame": 60 }
  },
  {
    "type": "MOVE",
    "prim": "/World/Arm",
    "duration": 5.0,
    "dx": 500, "dy": 0, "dz": 0
  }
]
```

**실제 재생 엔진:** `sequence_engine.SequenceRunner` → (LAM registry 있으면) `tbs_lam_sequence_engine.TbsLamSequenceRunner`

### D-5. 프리런 시 JSON 길이만 쓰는 경로 (실행 X)

**함수:** `_estimate_anim_duration_for_gate_payload` — **~1151**

프리런 `on_gate`에서 동일하게 `_resolve_event_animation_entry` + `_estimate_sequence_total_duration_sec_for_log` 호출 → **float 초** 반환.

---

## 7. Phase E — 포트 상태 갱신

### E-1. 진실의 원천 (프리런 중, 메모리)

| 함수 | 파일 | 효과 |
|------|------|------|
| `_set_port(port, ..., lot)` | `simulation_engine.py` ~1777 | `self.ports[port] = lot` |
| `_remove_from_port(port)` | ~1793 | `self.ports[port] = None` |
| `_emit_event(payload)` | ~1999 | payload에 `ports_occupancy` 스냅샷 추가 |

**`_emit_event`가 붙이는 데이터:**

```python
payload["ports_occupancy"] = {
    "INOUT": "LOT_1" if lot else "",   # LOT ID 또는 ""
    "BP1": "",
    "EP1": "LOT_A1",
    ...
}
```

### E-2. UI 반영 (재생 중, 메인 스레드)

**함수:** `_update_port_occupancy_panel(ext, occ, sim_time, screen)` — **~2915**

```python
cells["INOUT"].text = f"IN/OUT:{lot_id or '-'}"
_set_port_box_style(ext, "INOUT", lot_id, boxes)   # EMPTY/FULL 색
_update_ep_timeline_under_port_state(ext, ch, occ, sim_time)  # §8
```

**마지막 스냅샷 캐시:**

```python
ext._sim_last_ports_occupancy_by_screen["1"] = dict(occ)
```

---

## 8. Phase F — 막대그래프 (시간 증가 + 그리기)

### F-1. “setInterval” 에 해당하는 것

| 역할 | 코드 |
|------|------|
| 재생 프레임 루프 | `ext._sim_playback_ui_sub` → `_tick_playback` |
| 막대 10Hz 보조 | `_tick_playback` 내부 `timeline_only` progress |
| 큐 드레인 | `_sim_log_ui_sub` → `_drain_sim_log_queue` |

### F-2. 상태 누적 (데이터)

**저장소:** `ext._sim_ep_occ_timeline_state_by_screen["1"]`

```python
{
  "t_last": 12.5,
  "total_est_fixed": 342.5,
  "rows": {
    "EP1": [
      {"empty": true,  "dur": 7.3},
      {"empty": false, "dur": 5.2}
    ],
    "EP2": [...],
    "ALL_EP": [...]
  }
}
```

**누적 함수:** `_update_ep_timeline_under_port_state` — **~3009**

- `occ["EP1"]` 비었으면 `empty=true` 세그먼트에 `dt` 가산
- `total_est` = 프리런 `final_sim_time`

### F-3. omni.ui 그리기 (~3233)

```python
for seg in rows_state["EP1"]:
    w = int((seg["dur"] / total_est) * BAR_W)
    ui.Rectangle(width=w, style={"background_color": 빨강 if seg["empty"] else 초록})
```

### F-4. 진행현황 텍스트

**함수:** `_update_sim_progress` — **~4761**

`timeline_only=1` 이면 텍스트는 건너뛰고 **막대만** `_update_ep_timeline_under_port_state` 호출 (~4772).

---

## 9. 역할 → 파일 빠른 색인

| 역할 | 파일 | 핵심 함수/심볼 |
|------|------|----------------|
| 시작 버튼 | `control_window.py` | `on_sim_start_clicked` |
| UI 스냅샷 | `control_window.py` | `_capture_per_screen_sim_settings` |
| 타이밍/초기 설정 클래스 | `simulation_engine.py` | `SimulationTimingConfig`, `SimulationInitConfig` |
| 공정 SimPy 엔진 | `simulation_engine.py` | `TBSSimulationEngine`, `tick`, `_load_lot_to_inout` |
| 프리런 녹화 | `control_sim_prerun_playback.py` | `prerun_engine_to_timeline`, `SimPreRunResult` |
| 프리런 스레드 | `control_window.py` | `_prerun_thread_body` |
| 재생 플레이어 | `control_sim_prerun_playback.py` | `SimTimelinePlayer.tick` |
| 재생 tick | `control_window.py` | `_tick_playback`, `_drain_sim_log_queue` |
| UI 큐 | `control_window.py` | `SimUiQueueKind`, `post_sim_anim_event` |
| 이벤트→JSON 규칙 | `config/event_animation_rules.json` | — |
| 규칙 파싱·매칭 | `control_window.py` | `_load_event_animation_rules`, `_resolve_event_animation_entry` |
| XML 생성 | `xml_generator.py` | `build_xml_string`, `parse_xml_string` |
| 이벤트→애니 | `control_window.py` | `handle_sim_event_for_animation` |
| JSON 실행 | `control_window.py` | `_execute_mapped_sequence_stub` |
| 시퀀스 러너 | `sequence_engine.py` / `tbs_lam_sequence_engine.py` | `SequenceRunner.run` |
| 애니 JSON 데이터 | `data/sim_sequences/*.json` | — |
| 포트 prim 경로 | `config/port_lot_prim_paths.json` | — |
| 포트 3D 가시성 | `port_lot_visibility.py` | `apply_port_lot_prim_visibility` |
| 포트 UI 패널 | `control_window.py` | `_update_port_occupancy_panel` |
| EP 막대그래프 | `control_window.py` | `_update_ep_timeline_under_port_state` |
| JSON 길이 추정 (게이트) | `control_window.py` | `_estimate_anim_duration_for_gate_payload` |
| 공정 vs 애니 대기 | `simulation_engine.py` | `_proc_anim_pair`, `_wait_with_progress` |

---

## 10. FAQ (이 문서 기준)

**Q. 공정시간 5~10 바꾸고 Start — 어디에 반영?**  
A. Start 시 `_capture` → `SimulationTimingConfig.oht_to_bp1_min/max` → 프리런 `rand_oht_to_bp1()`.

**Q. JSON은 언제 실행?**  
A. 프리런 X. 재생 `player.tick()`이 `kind=event` emit → `handle_sim_event_for_animation` → `_execute_mapped_sequence_stub`.

**Q. 포트는 언제 바뀌어 보임?**  
A. 재생 시 `event` payload의 `ports_occupancy` → `_update_port_occupancy_panel`. (프리런에서 엔진 메모리는 이미 바뀌어 있으나 UI는 재생 전까지 안 보임.)

**Q. 막대는?**  
A. `_update_ep_timeline_under_port_state` — `progress`/`timeline_only` + `ports_occupancy`로 `dt` 누적 후 `ui.Rectangle`.

---

*관련: `docs/event_to_animation_flow.md` (공정시간 우선 정책 상세)*
