# LAM 웨이퍼·슬롯 시간별 스냅샷 설계 (초안)

**문서 상태:** 설계·요구사항 정리만 (구현 전)  
**최종 갱신:** 2026-05-28  
**관련 대화:** Viewport FOUP 집계, CSV Play 관측, 추후 프리런(Pre-run) 기능

---

## 0. 한 줄 요약

CSV Play(및 추후 프리런)와 **동작은 분리**하고, **ATM·VTM 이벤트·CSV 시간축**에 맞춰 **모든 `slot_key`의 웨이퍼 개수·번호(복수 매 허용)** 와 **FOUP 1~3 진행 상태**를 **시간순으로 기록**한다.  
기록 데이터만으로 “몇 초에 어디에 몇 번 웨이퍼가 있었는지”, “그때 어떤 JSON이 실행되는지”를 나중에 조회·되감기할 수 있게 한다.

**지금 당장:** 이 문서만 존재. 재생·표시 로직은 변경하지 않는다.

---

## 1. 목표 / 비목표

### 1.1 목표

| # | 목표 |
|---|------|
| G1 | 선택한 **CSV `t`** 기준으로, 그 시각의 **전체 슬롯 점유**를 확인할 수 있다. |
| G2 | 슬롯마다 **웨이퍼 개수** + **카세트 번호(웨이퍼 #) 목록** (2매 이상 슬롯은 리스트 길이 = 개수). |
| G3 | **FOUP 1·2·3** 각각: 남은/전체, 진행중, 완료 (pick/place 규칙은 §4). |
| G4 | **ATM·VTM** 이벤트 실행 시점이 스냅샷에 남는다 (`event_name`, JSON 경로 등). |
| G5 | **시간별 이력(history)** — 프리런·스크러브·중간 재생 등 **추후 UI**의 데이터 기반. |
| G6 | 사용자가 **변수·덤프·조회 API**로 직접 검증 가능. |

### 1.2 비목표 (이번 설계 단계)

- CSV Play **재생 로직·애니·visibility·타임라인 실행 순서** 변경
- 기존 FOUP 3D 패널·웨이퍼 번호 라벨·2D 상태 패널 **동작 변경**
- 프리런 UI·동영상式 스크러버 **구현** (데이터 모델만 프리런을 염두)
- USD/stage prim **물리 위치** 기록 (필요 시 별도; 본 설계는 **논리 slot + 웨이퍼 #** 중심)

---

## 2. 절대 원칙 (기존 동작 무손상)

```
┌─────────────────────────────────────────────────────────┐
│  CSV Play / sequence_engine / lam_sim_actions  (기존)   │
│  — 웨이퍼 이동·JSON 실행·PRIM_VISIBILITY 그대로          │
└──────────────────────────┬──────────────────────────────┘
                           │ 관측만 (read-only hooks)
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Wafer Fab Snapshot Recorder (신규, 관측 전용)           │
│  — live 맵 + history[] append only                      │
│  — 실패해도 Play 중단 금지                               │
└─────────────────────────────────────────────────────────┘
```

- **쓰기:** 기록 모듈 내부 버퍼(및 선택적 파일 덤프)만.
- **읽기:** UI·프리런·디버그가 나중에 history를 읽음.
- **기존 `record_foup_event_from_schedule_entry` / FOUP 3D:** 유지. 기록 레이어는 **동일 이벤트를 관측해 FOUP·슬롯 맵을 함께 스냅샷**하거나, FOUP는 기존 `_foup_counts`를 **복사**만 할 수 있음 (구현 시 결정).

---

## 3. 현재 코드베이스 (2026-05 기준)

| 항목 | 위치 | 있는 것 | 없는 것 |
|------|------|---------|---------|
| FOUP 1~3 실시간 | `lam_viewport_overlay_state._foup_counts` | pick/place 시 picked·placed 누적, 3D 패널 표시 | **시간별 이력** |
| FOUP 관측 시점 | `simulation_play._csv_play_timeline_row_begin_entry` → `record_foup_event_from_schedule_entry` | `atm_foup{n}_pick\|place` JSON **시작** 시 1회 | 슬롯 맵 |
| 웨이퍼 번호(표시) | `lam_wafer_viewport_labels.WaferNumberLabelTracker` | pick/place 후 prim↔번호 (UI용) | slot별 **개수·이력** |
| CSV 진행 시각 | `get_csv_play_progress_snap()` | `csv_t_display`, wall 경과 | 슬롯 스냅샷 |
| 슬롯 SSOT | `lam_wafer_prim_paths.WAFER_PRIM_BY_SLOT_KEY` | 142+ 물리 슬롯 + 논리 ARM/EE | 런타임 점유 맵 |
| 이벤트→슬롯 | `lam_event_sequences.atm_event_name_for_slot`, VTM 쪽 | `atm_foup1_pick` → `foup1_k` 등 | 전 이벤트 자동 기록 |

---

## 4. FOUP 1~3 집계 규칙 (요구사항)

스냅샷·이력에 FOUP별로 아래 필드를 둔다 (`total` 기본 25, config에서 변경 가능).

| 이벤트 | 남은 (FOUP 내 잔량) | 진행중 | 완료 |
|--------|---------------------|--------|------|
| `atm_foup{n}_pick` | **−1** (picked 증가 → `total - in_process` 관점에서 FOUP 잔량 감소) | **+1** | 변화 없음 |
| `atm_foup{n}_place` | **+1** | **−1** | **+1** |

**현재 구현과의 관계:** `FoupCounts`는 `picked_count`, `placed_back_count`로 저장하고,  
`in_process = picked - placed`, `current_in_foup_now = total - in_process` 로 파생한다.  
**이력 기록 시** 위 규칙과 일치하는 **파생값을 스냅샷에 함께 저장**하면 UI·문서·데이터가 맞는다.

**FOUP 3D 전용 설정 (이미 존재):** `lam_viewport_overlay_config.WAFER_LABEL_SHOW_FOUP_SLOT_NUMBERS` — Viewport 번호 라벨용, 본 이력과 무관.

---

## 5. 슬롯 점유 데이터 모델 (설계)

### 5.1 `SlotOccupancy` (슬롯 하나)

| 필드 | 타입 | 설명 |
|------|------|------|
| `slot_key` | `str` | 예: `foup1_7`, `airlock1_1`, `chamber2`, `LOGICAL:ATM_ARM` |
| `wafer_ids` | `list[int]` | 카세트 번호(웨이퍼 #). **순서 유지.** 길이 = 개수. |
| `count` | `int` | `len(wafer_ids)` 와 동일 (편의·검증용) |

- **0개:** `wafer_ids == []`
- **2매 이상:** `wafer_ids == [3, 15]` 등 — 단순 0/1 플래그가 아님.

### 5.2 `FoupSnapshot` (FOUP 1~3)

| 필드 | 설명 |
|------|------|
| `total` | 25 (기본) |
| `remaining` / `in_foup` | FOUP 안에 있는 매수 (`current_in_foup_now` 와 동일 개념) |
| `in_process` | 라인 밖 진행 중 |
| `done` | place 완료 누적 |

### 5.3 `FabTimeSnapshot` (이력 1프레임)

| 필드 | 설명 |
|------|------|
| `csv_t` | CSV 시각 [s] |
| `wall_t` | (선택) 실경과 [s] |
| `event_name` | 예: `atm_foup1_pick`, `vtm_chamber2_left_place` |
| `json_path` | (선택) 절대/상대 JSON 경로 |
| `schedule_row_key` | `(time_sec, sort_order, category, event_name)` — 타임라인 행 매칭용 |
| `cassette_slot` | (선택) 해당 pick/place 웨이퍼 # |
| `lot_id` | (선택) |
| `lane` | (선택) ATM/VTM 레인 |
| `robot` | `"atm"` \| `"vtm"` \| `""` |
| `foup` | `{1: FoupSnapshot, 2: ..., 3: ...}` |
| `slots` | `{slot_key: SlotOccupancy}` — **관심 슬롯 전체** 또는 sparse + 전체 조회 API |

**`slots`에 넣을 키 범위 (구현 시):**

- `lam_wafer_prim_paths` 의 전체 `slot_key`
- 논리 슬롯: `LOGICAL:ATM_ARM`, `LOGICAL:VTM_EE_L`, `LOGICAL:VTM_EE_R`
- 빈 슬롯도 키를 두면 “전체 맵이 채워져 있는지” 한눈에 가능 (sparse vs dense는 구현 선택)

---

## 6. ATM / VTM 이벤트 → 슬롯 갱신 (개념)

이벤트 naming (기존 `lam_event_sequences`):

- ATM: `atm_foup{n}_pick|place`, `atm_airlock{n}_pick|place`, `atm_aligner_*`, `atm_coolstation_*`, `atm_buffer*_*`, …
- VTM: `vtm_chamber{n}_left|right_pick|place`, `vtm_airlock*`, …

**갱신 규칙 (개념):**

| 동작 | source 슬롯 | dest 슬롯 |
|------|-------------|-----------|
| pick | slot에서 wafer_id **제거** | arm/EE에 **추가** |
| place | arm/EE에서 **제거** | slot에 **추가** |

**정확한 source/dest**는 다음을 우선한다 (재생 로직 변경 없이 **이미 있는 메타** 활용):

1. `lam_sequence_engine` 의 `PRIM_VISIBILITY` 직후 `_lam_wafer_label_ctx`  
   (`slot_key`, `slot_wafer_path`, `arm_wafer_path`, `cassette_slot`, `pick_or_place`)
2. 스케줄 엔트리 `event_name` + `build_foup_pick_place_steps` 등에서 알 수 있는 FOUP 인덱스·카세트
3. `atm_event_name_for_slot` / `vtm_event_name_for_slot` 로 이벤트명 → slot_key

**합성 aligner** (`atm_aligner_place` / `atm_aligner_pick`, FOUP pick 직후 삽입):  
FOUP 집계와 별도로 슬롯 맵만 갱신. FOUP 카운트는 `atm_foup*` 에만 적용 (구현 시 규칙 고정).

---

## 7. 시간별 이력 (history)

### 7.1 구조

```text
_recorder
  .live          → FabTimeSnapshot (최신)
  .history       → list[FabTimeSnapshot]  # append-only
  .csv_path      → 현재 세션 CSV
  .session_id    → (선택) Play/프리런 구분
```

### 7.2 append 시점 (후보, 구현 시 확정)

| 시점 | 장점 |
|------|------|
| JSON 블록 **시작** (`_csv_play_timeline_row_begin_entry`) | `event_name`·스케줄 행 확실, FOUP와 동기 |
| JSON 블록 **종료** (성공 시) | 실제 실행 완료 후 상태 |
| `PRIM_VISIBILITY` **배치 후** | 슬롯 prim 기준 **가장 정확** |
| dwell 경계 | CSV 행 “머무름” 보조 |

**권장 (초안):**  
- **FOUP 숫자:** JSON 시작 (기존과 동일) + 이력 1프레임.  
- **슬롯 맵:** visibility ctx 처리 **후** 1프레임 (또는 JSON 종료 후 1프레임).  
→ 동일 `csv_t`에 프레임이 2개일 수 있음. 필드 `phase: "event_start" | "after_visibility"` 로 구분.

### 7.3 확인 방법 (구현 후 사용자 검증)

| 방법 | 설명 |
|------|------|
| `get_fab_history()` | 전체 리스트 반환 |
| `get_fab_snapshot_at_csv_t(t)` | t에 가장 가까운 프레임 |
| `dump_fab_history_json(path)` | CSV 재생 후 파일로 덤프 |
| 콘솔 요약 | `[FAB] t=12.3 foup1 in_process=2 slots_filled=47` |

설정 예 (`lam_viewport_overlay_config` — **플래그·경로만**, 값은 런타임 모듈):

```python
# --- 웨이퍼·슬롯 시간별 스냅샷 (관측 전용, 구현 예정) ---
FAB_SNAPSHOT_ENABLED: bool = True
FAB_SNAPSHOT_HISTORY_MAX: int = 50000   # 0 = 무제한(주의)
FAB_SNAPSHOT_DUMP_ON_STOP: bool = False  # 정지 시 JSON Lines 덤프
FAB_SNAPSHOT_DUMP_DIR: str = ""          # 비우면 기본 logs/
FOUP_SLOTS_TOTAL_DEFAULT: int = 25
```

---

## 8. 추후 프리런 (Pre-run) — 이 데이터가 받쳐야 할 기능

문서화만. **구현은 별 단계.**

| 기능 | 필요 데이터 |
|------|-------------|
| CSV 드롭다운 선택만으로 타임라인 미리보기 | Play 없이 `build_csv_playback_plan` + 동일 스냅샷 생성기 |
| 시간 슬라이더 / “동영상” 스크럽 | `history[].csv_t` + `slots` |
| “이 시각에 실행할 JSON” 표시 | `event_name`, `json_path`, `schedule_row_key` |
| 중간부터 재생 | `csv_t` seek + 기존 Play checkpoint 연동 |
| 슬롯 히트맵 / FOUP 패널 미리보기 | `foup`, `slots` |

**Play 중 기록**과 **프리런 기록**이 **동일 `FabTimeSnapshot` 포맷**이면 UI·검증 코드를 공유한다.

---

## 9. 파일·모듈 배치 (구현 예정, 미확정)

| 역할 | 제안 경로 |
|------|-----------|
| 설정 (on/off, 한도, 덤프) | `lam_viewport_overlay_config.py` |
| 런타임 live + history | **신규** `lam_wafer_fab_snapshot.py` (또는 `lam_viewport_overlay_state` 확장) |
| 관측 훅 (1~2줄) | `simulation_play._csv_play_timeline_row_begin_entry`, `lam_sequence_engine` visibility 후 |
| 기존 FOUP UI | `lam_viewport_foup_status_3d.py` — **변경 없음** 또는 live만 읽기 |

**순환 import 방지:** `schedule_row_key` 규칙은 `overlay_state.schedule_entry_foup_match_key` 와 동일 tuple 유지.

---

## 10. 구현 전 논의할 열린 질문

1. **이력 밀도:** 이벤트마다 1프레임 vs 0.2s tick vs visibility마다 — 용량·정확도 trade-off.  
2. **dense vs sparse `slots`:** 145키 전부 매 프레임 vs 변경된 키만.  
3. **프레임 시점:** JSON 시작 vs visibility 후 vs 둘 다.  
4. **기존 `_foup_counts`와 이력 FOUP:** 단일 SSOT로 통합할지, 이력만 별도 계산할지.  
5. **프리런 1단계:** Play 관측만 먼저 vs plan 빌드만으로 offline history.  
6. **lot_id + cassette_slot** 키: 동일 번호 다른 lot 구분 필요 여부.  
7. **정지(초기화):** history 클리어 vs 세션 파일로 보존.

---

## 11. 관련 문서·코드

| 문서/코드 | 링크 |
|-----------|------|
| Viewport 오버레이 유지보수 | `docs/LAM_Viewport_Overlay_Maintenance_Guide.md` |
| 2D 상태 패널 요구 | `docs/LAM_Viewport_CSV_Status_Panel_Requirements.md` |
| CSV Play 필드 테스트 | `docs/LAM_Simulation_Play_Field_Test_Guide.md` |
| FOUP 실시간 집계 (현재) | `lam_viewport_overlay_state.record_foup_event_from_schedule_entry` |
| 웨이퍼 prim SSOT | `lam_wafer_prim_paths.py` |

---

## 12. 변경 이력

| 날짜 | 내용 |
|------|------|
| 2026-05-28 | 초안 작성 — 요구사항·데이터 모델·비침해 원칙·프리런 방향. **코드 미구현.** |

---

*구현 착수 전 이 문서를 기준으로 대화·수정한 뒤, §9·§10을 확정하고 코딩한다.*
