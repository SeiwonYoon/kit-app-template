# `morph.tbs_control_1` 제어창 리팩터링 단계 문서

목표: **동작·기능을 바꾸지 않고** `control_window.py` 비대화를 줄여, 수정 지점을 찾기 쉽게 한다.

---

## 전체 원칙

- 한 단계씩 끝낼 때마다 **아래 검증 체크리스트**를 수행하고, 문제 없을 때만 다음 단계로 진행한다.
- 단계마다 **이 문서의 상태 표**를 갱신한다 (`[ ]` → `[x]`).

---

## 상태 요약 (진행 표)

| 단계 | 설명 | 코드 위치(이동 후) | 상태 | 완료일 |
|------|------|---------------------|------|--------|
| **0** | 본 문서·CHANGELOG 반영 | `docs/refactor_tbs_control_1_phases.md` | [x] | 2026-04-19 |
| **1** | 시뮼 UI 큐: 엔거·enqueue·post API | `control_sim_ui_posts.py` | [x] | 2026-04-19 |
| **2** | 이벤트→JSON 규칙(맵/rules/resolve/경로 추정) | `control_sim_event_rules.py` + `control_paths.py` | [x] | 2026-04-19 |
| **3** | 멀티 tick 워커·pause 보조 | `control_sim_tick_workers.py` | [x] | 2026-04-19 |
| **4** | 시뮼 모니터 UI(포트/EP 타임라인/이력 렌더) | `control_sim_monitor_ui.py` | [x] | 2026-04-19 |
| **5** | `handle_sim_event_for_animation` + `_execute_mapped_sequence_stub` | (예정) `control_sim_anim_pipeline.py` 등 | [ ] | |
| **6** | `on_sim_start_clicked` / stop / reset / `_detach_sim_update` | (예정) `control_sim_lifecycle.py` | [ ] | |

---

## 단계 1 — 시뮼 UI 큐 (`control_sim_ui_posts.py`)

### 옮긴 것

- `SimUiQueueKind`, `SimUiControlAction`, `SimLogPanelMode`
- `_enqueue_sim_log`, `_enqueue_anim_event`, `_enqueue_control_action`, `_enqueue_gate_request`, `_enqueue_sim_progress`
- `post_sim_history_line`, `post_sim_anim_event`, `post_sim_progress_update`
- `_coerce_sim_ui_queue_kind`

### 바뀐 것(외부 관점)

- **공개 API는 동일**: `control_window`가 위 심볼을 **import 후 그대로 노출**하므로, `simulation_engine` 콜백·다른 모듈은 기존처럼 `control_window.post_sim_*` 를 쓰면 된다.

### 검증 체크리스트 (다음 단계 전 필수)

- [ ] 시뮬 시작 후 **진행현황**이 갱신되는지
- [ ] **이력 로그**에 스토리/이벤트 줄이 쌓이는지(단일·분할)
- [ ] **공정 확인(게이트)** 창이 뜨는 시나리오에서 큐가 소비되는지
- [ ] 시뮬 종료 시 **XLSX export** 액션이 큐를 타는지(멀티 tick 종료 경로)

---

## 단계 2 — 경로 + 이벤트 규칙 (`control_paths.py`, `control_sim_event_rules.py`)

### 옮긴 것

- `control_paths.py`: `_extension_root_dir`, `_sequence_json_search_roots`
- `control_sim_event_rules.py`: `SIM_SEQ_ALIAS`, `EVENT_JSON_CASE_MAP`, 애니 맵/rules 캐시·로더, `_matches_occupancy_rule`, `_resolve_*`, `_normalize_json_path`, `_estimate_*`(로그용 시퀀스 길이 추정; `_group_end_index`는 `sequence_engine`에서 import)

### 바뀐 것(외부 관점)

- `EVENT_JSON_CASE_MAP` 등을 **직접 패치**하던 경우 import 경로가 `control_sim_event_rules`로 바뀌었는지 확인(대부분은 `control_window` re-export로 무관).

### 검증 체크리스트

- [ ] 임의 시뮼 이벤트에서 **규칙 JSON / case map**에 매핑된 애니 JSON이 실행되는지
- [ ] `data/sim_sequences` 가 소스 트리·빌드 산출물 등 **후보 루트**에서 찾아지는지(경로 해석)
- [ ] 게이트용 **애니 예상 길이** 추정이 이전과 같이 동작하는지(해당 경로 사용 시)

---

## 단계 3 — 멀티 tick 워커 (`control_sim_tick_workers.py`)

### 옮긴 것

- `_sim_active_anim_owner_screen`, `_ensure_tick_pause_map_for_multi`, `_is_multi_viewport_sim`, `_pause_event_for_screen`, `_multi_tick_should_skip_for_screen`, `_sim_multi_engine_tick_worker`

### 바뀐 것(외부 관점)

- `on_sim_start_clicked` 등은 여전히 `control_window`에 있으며, 워커만 분리됨.

### 검증 체크리스트

- [ ] **2~4 분할 + 멀티 엔진**에서 tick이 돌고 시뮼 시간이 진행되는지
- [ ] 한 화면 종료 후 **export 한 번**만 트리거되는지(`export_lock` 경로)
- [ ] 배속·pause 관련 이상(한 화면만 멈춤 등) 없는지

---

## 단계 4 — 시뮼 모니터 UI (`control_sim_monitor_ui.py`)

### 옮긴 것

- `_sim_monitor_channel_count`, `_snapshot_monitor_channel_texts`, `_ep_occ_timeline_layout_dims`, `_ep_timeline_host_horizontal_scroll_policy`
- `_create_sim_monitor_channel_column`, `_rebuild_sim_monitor_split_ui`
- `_append_sim_log_channel`, `_append_sim_log`, `_format_history_line`, `_with_history_color_icon`
- `_port_cell_text`, `_compact_cell_value`, `_ep_count_idx_for_port_panel`, `_sync_ep3_port_cell_visibility_for_channel`, `_sync_ep3_port_cell_visibility`, `_set_port_box_style`, `_update_port_occupancy_panel`
- `_update_ep_timeline_under_port_state`, `_sync_all_ep_occ_timelines_from_engines`
- `_update_progress_ep_timeline_widget`

### 바뀐 것(외부 관점)

- `kit_remote_http_bridge` 등이 `control_window`에서 가져오던 `_ep_count_idx_for_port_panel` 등은 **여전히 `control_window` 경유로 동일**하다(`control_window`가 재import).

### 검증 체크리스트

- [ ] 분할 1~4에서 모니터 열·포트·EP 막대·이력이 이전과 같이 보이는지
- [ ] 멀티 시 `[화면N]` 이력 라우팅·진행현황 EP 막대 갱신이 정상인지

---

## 다음 단계(5~)에서 할 일 (요약)

- **5**: `handle_sim_event_for_animation` + `_execute_mapped_sequence_stub` → 애니 실행 파이프라인 전용 모듈
- **6**: 시작/정지/리셋·스레드 join → `control_sim_lifecycle.py`

각 단계 완료 시 **본 문서 표·`docs/CHANGELOG.md` Unreleased**에 한 줄 추가한다.

---

## 이번에 적용한 뒤 바뀐 것(요약)

| 항목 | 내용 |
|------|------|
| 새 파일 | `control_paths.py`, `control_sim_ui_posts.py`, `control_sim_event_rules.py`, `control_sim_tick_workers.py`, `control_sim_monitor_ui.py` |
| `control_window.py` | 위 모듈에서 import; 단계 4 이후 **약 4.1k줄** 수준(모니터 UI 약 1k줄 분리) |
| 외부 import | `extension.py` / `kit_remote_http_bridge.py` 등은 여전히 **`control_window`만** 보면 됨(공개 API 유지) |
| 다음 단계 전 | 본 문서 **단계 1~3 검증 체크리스트**를 Kit에서 확인하고 `[x]` 처리할 것 |
