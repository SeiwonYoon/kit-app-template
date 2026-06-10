# LAM Control — TBS 프리런·Seek·막대·타임테이블 포팅 가이드

TBS `morph.tbs_control_2`에 구현된 **프리런 타임라인 재생 · EP 막대 프리컴pute · 클릭 Seek · 결과 미리보기** 기능을  
`morph.lam_control`에 적용할 때 참고하는 문서입니다.

> LAM은 fab CSV 재생 중심이라 **구조가 다릅니다**. 아래 “TBS ↔ LAM 매핑”을 보고 동일 UX를 LAM 데이터 모델에 맞게 옮깁니다.

---

## 1. TBS에서의 확정 UX (SSOT)

| 기능 | 동작 |
|------|------|
| Start | 백그라운드 프리런 → 완료 후 `SimTimelinePlayer` wall-clock 재생 |
| 프리런 중 UI | 막대·타임테이블 영역 “계산 중…”, Start 비활성 |
| EP 막대 | 프리런 `progress`로 **완성 rows 사전 계산** → 재생 시 virtual time 슬라이스(부드러운 채움) |
| 결과 미리보기 | 토글 ON=완성 막대 교체, OFF=시간에 따라 채움 |
| 타임테이블 | `[t / total]` + JSON `{}` 한 줄 = 클릭 단위, `sim_now==t` 인 **모든 행** 녹색 |
| Seek | 클릭 행까지 Fast-apply(애니 없음) → prim 초기화·FOUP 보임/숨김 → 해당 시점부터 배속 재생 |
| 되감기 | 없음 (정방향만) |

**TBS 핵심 파일**

| 파일 | 역할 |
|------|------|
| `control_sim_prerun_playback.py` | `SimPreRunResult`, `SimTimelinePlayer`, `prerun_engine_to_timeline`, 막대 빌드, seek |
| `control_sim_timetable_ui.py` | 클릭 가능 타임테이블 행 UI |
| `control_window.py` | Start/Stop, 포트·막대·이력, fast-apply, prim reset 연동 |
| `simulation_engine.py` | `TBSSimulationEngine`, `_emit_event` / `_emit_progress` |

---

## 2. LAM 현재 구조 (As-Is)

| TBS 개념 | LAM 대응 | 파일 |
|----------|----------|------|
| SimPy 엔진 프리런 | **없음** | — |
| `SimPreRunResult.items` | CSV `CsvPlaybackScheduleEntry` / dwell 계획 | `simulation_play.py` |
| `SimTimelinePlayer` | `run_csv_timed_playback` wall-clock 루프 | `simulation_play.py` |
| 타임테이블 JSON 행 | 스케줄 행 + `_rebuild_schedule_timeline_rows` 녹색 하이라이트 | `simulation_play.py` (`LamSimulationCsvPlayWindow`) |
| EP 막대 (EMPTY/FULL) | **없음** | — |
| 포트상태 패널 (BP/EP/OHT) | **없음** — FOUP 3D·웨이퍼 라벨·Status HUD | `lam_viewport_foup_status_3d.py`, `lam_viewport_status_panel.py` |
| progress snap | `_csv_play_progress_snap`, `update_progress_snap` | `simulation_play.py`, `lam_viewport_overlay_state.py` |
| 이력 스크롤 | 콘솔 + `_log_label` 한 줄 | `LamSimulationCsvPlayWindow` |

**LAM Play 진입**

```
LamSimulationCsvPlayWindow._on_play_clicked()
  → run_simulation_from_csv()
  → run_csv_timed_playback()   # wall-clock, LamSequenceRunner
```

---

## 3. 포팅 전략 (권장)

### 3.1 “프리런”에 해당하는 LAM 단계

LAM에는 SimPy 엔진이 없으므로, **CSV plan 빌드 완료 시점**을 TBS의 “프리런 완료”에 매핑합니다.

| TBS | LAM |
|-----|-----|
| `prerun_engine_to_timeline()` | `build_csv_playback_plan()` / `build_and_cache_csv_playback()` 결과를 **타임라인 아이템 리스트**로 직렬화 |
| `SimTimelineItem(kind=event\|progress)` | `{ wall_t, csv_row_key, json_path, event_name, ... }` |
| `SimPreRunResult.final_sim_time` | CSV 총 재생 시간(마지막 dwell 종료 시각) |

**신규 모듈 제안 (LAM)**  
`lam_control/lam_csv_prerun_playback.py` — TBS `control_sim_prerun_playback.py` 축소 포팅:

- `CsvTimelineItem`, `CsvPreRunResult`
- `build_timeline_from_csv_plan(plan) -> CsvPreRunResult` (애니 실행 없이 계획만)
- `CsvTimelinePlayer` — wall-clock × 배속 emit (TBS `SimTimelinePlayer`와 동일 인터페이스)

### 3.2 타임테이블 + 클릭 Seek

| TBS | LAM |
|-----|-----|
| `_build_prerun_timetable_text` | CSV 스케줄 행 → JSON 라인 (`t`, `event`, `json`, `slot` 등) |
| `control_sim_timetable_ui.py` | `LamSimulationCsvPlayWindow` 스케줄 스크롤을 **행 단위 Button**으로 교체 또는 병행 |
| Seek Fast-apply | `t` 이전 스케줄 항목을 **애니 없이** prim hide/show·FOUP 상태만 적용 (`lam_play_prim_hide`, FOUP 3D 패널) |
| Visible 재생 | `LamSequenceRunner.run` 해당 JSON부터 순차 실행 |

기존 `_apply_schedule_row_highlight` / `get_csv_play_timeline_active_keys_snap` 패턴을 **Seek cursor**와 통합합니다.

### 3.3 막대그래프 (LAM에 없음 → 신규 또는 대체)

fab 도메인에 EP EMPTY/FULL 막대가 없다면:

- **옵션 A**: 슬롯/FOUP 점유 타임라인을 새로 정의 (`lam_slot_occ_timeline.py`)
- **옵션 B**: Status HUD / FOUP 3D 카운트만 Seek 동기화 (막대 UI 생략)

TBS `build_ep_bar_from_progress_items()` 로직을 **“슬롯 EMPTY/FULL”** 또는 **“FOUP in-chamber”** 로 치환해 재사용합니다.

### 3.4 상태 복원 (Seek 시)

TBS `control_window._fast_apply_prerun_seek()` 순서를 LAM에 맞게 복제:

1. 재생·러너 중단 (`request_stop_csv_playback`, `LamSequenceRunner.stop`)
2. prim/인스턴스 초기화 (`lam_play_start_sequence` 역순 또는 전용 reset)
3. 타임라인 `0..clicked` Fast-apply — visibility·FOUP·overlay state만
4. `CsvTimelinePlayer.seek(t, cursor)`
5. 이후 JSON 애니 정상 재생 + 배속

**LAM 상태 저장소 후보**

| TBS `ext._…` | LAM |
|--------------|-----|
| `_sim_last_ports_occupancy_by_screen` | `lam_viewport_overlay_state` FOUP/slot snap |
| `_sim_ep_bar_prerun_by_screen` | (신규) slot 타임라인 rows |
| `_csv_play_progress_snap` | 기존 유지 + `seek_t`, `seek_cursor` 필드 추가 |

---

## 4. 구현 체크리스트 (LAM)

- [ ] `lam_csv_prerun_playback.py` — 타임라인 아이템·플레이어·seek API
- [ ] CSV plan → 타임테이블 행 메타 (`t`, `total`, `item_index`, JSON 텍스트)
- [ ] `LamSimulationCsvPlayWindow` — 프리런 중 “계산 중…”, Play 비활성
- [ ] 클릭 가능 스케줄/타임테이블 UI + `sim_now==t` 다중 녹색
- [ ] Seek: fast-apply + prim reset + `LamSequenceRunner` 재개
- [ ] (선택) 슬롯/FOUP 막대 프리컴pute + 미리보기 토글
- [ ] `lam_viewport_status_panel` — seek 후 progress snap 동기화
- [ ] 멀티 EQP / 웹 — 추후 (TBS와 동일하게 후보)

---

## 5. TBS 코드 읽기 순서 (LAM 개발자용)

1. `control_sim_prerun_playback.py` — 데이터 모델·막대 빌드·`SimTimelinePlayer.seek`
2. `control_sim_timetable_ui.py` — 행 UI·하이라이트·클릭 콜백
3. `control_window.py` — `_fast_apply_prerun_seek`, `_set_sim_prerun_ui_busy`, 미리보기 체크박스
4. `sim_control_architecture_guide.md` (tbs_control_2) — Phase B~F 흐름

---

## 6. 주의사항

- LAM **웨이퍼**는 TBS FOUP/OHT 포트 모델과 다름 — Seek 시 “모든 상태”는 LAM overlay state + scheduler `virtual_time` 기준으로 정의할 것.
- LAM은 **USD_TIMELINE / TIMESAMPLES_REPLAY** 혼용 — Fast-apply 시 master timeline scrub 없이 visibility만 맞추고, Visible 구간에서 runner가 재생하도록 분리.
- TBS `handle_sim_event_for_animation` ↔ LAM `LamSequenceRunner.run` — Seek 경계에서 **step 시작 = RUNNING elapsed 0** 규칙을 동일하게 맞출 것.

---

*작성 기준: morph.tbs_control_2 프리런·Seek 명세 v3 (2026-05)*
