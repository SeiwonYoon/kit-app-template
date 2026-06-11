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
| 타임테이블 | `[t / total]` + JSON `{}` 한 줄 = 클릭 단위, `t <= sim_now` 인 **마지막 버킷**(동일 `t` 전체) 녹색 — **다음 행 전까지 유지** |
| 타임테이블 창 | 모니터(포트·막대·진행)와 **별도 Window** (`TBS 타임테이블`) |
| Seek | 클릭 행까지 Fast-apply(애니 없음) → prim 초기화·FOUP 보임/숨김 → 해당 시점부터 배속 재생 |
| 되감기 | 없음 (정방향만) |

**TBS 핵심 파일**

| 파일 | 역할 |
|------|------|
| `control_sim_prerun_playback.py` | `SimPreRunResult`, `SimTimelinePlayer`, `prerun_engine_to_timeline`, 막대 빌드, seek |
| `control_sim_timetable_ui.py` | Placer 스크롤, 클릭 Seek, 녹색 하이라이트 (`build_timetable_column_ui`, `refresh_all_timetable_highlights`) |
| `control_window.py` | `build_sim_monitor_window` / `build_sim_timetable_window`, `_rebuild_all_sim_ui_panels`, fast-apply |
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

## 7. TBS 타임테이블·스크롤·하이라이트 — 실패 원인과 해결 (2026-05)

TBS `morph.tbs_control_2`에서 프리런 타임테이블 UI를 붙이는 과정에서 **여러 번 실패한 시도**와 **최종 확정 해결**을 정리합니다.  
LAM에 동일 UX(클릭 Seek + 녹색 하이라이트 + 스크롤 유지)를 포팅할 때 **같은 함정을 피하기 위한 참고**로 씁니다.

### 7.1 증상 요약 (사용자 보고)

| # | 증상 | 기대 동작 |
|---|------|-----------|
| 1 | 이벤트 변경·행 클릭·재생 중 타임테이블 스크롤이 **최상단으로 리셋** | 사용자가 이동한 스크롤 위치 **그대로 유지** |
| 2 | 우측 **세로 스크롤바**가 보이지 않음 | 긴 타임테이블에서 스크롤 가능 표시 |
| 3 | 녹색 하이라이트가 **클릭·시작 순간만 깜빡**였다 사라짐 | `sim_now` 기준 활성 행이 **다음 진행 행 전까지** 녹색 유지 |
| 4 | (부작용) 막대그래프 **시간 진행이 멈춤** | EP 막대 virtual time은 재생 중 계속 전진 |

---

### 7.2 실패했던 접근과 근본 원인

#### A. `ScrollingFrame` + `scroll_y` 저장·복원·watchdog

**시도**

- `scroll_y_changed_fn`으로 사용자 스크롤 위치를 `ext`에 저장
- 이벤트/재생 tick마다 저장값으로 **pin·burst 복원**(8~64프레임)
- `scroll_y==0`이 들어오면 “의도치 않은 리셋”으로 간주해 복원

**실패 원인**

1. Kit 레이아웃이 **형제 UI**(포트 패널·EP 막대·진행현황)를 갱신할 때 타임테이블 `ScrollingFrame`의 `scroll_y`를 **0으로 먼저 씀**
2. 복원 로직이 그 0을 “사용자 의도”로 **저장값에도 반영**해 SSOT가 오염됨
3. watchdog이 사용자 **휠 스크롤과 충돌** — 사용자가 내린 위치와 강제 복원이 겹침

**교훈**: `ScrollingFrame.scroll_y`는 **형제 위젯 갱신에 취약한 파생 상태**이다. 저장·복원만으로는 안정적인 SSOT가 되지 않는다.

---

#### B. 같은 창 안에서 상단/하단 UI만 `Frame`으로 분리

**시도**

- `monitor_upper_frame`(포트·막대·진행)과 `timetable_panel`을 **같은 채널 `VStack` 안에서 분리**
- “상단만 갱신하면 타임테이블 scroll_y는 안 건드려진다”는 가정

**실패 원인**

- 여전히 **한 Window·한 레이아웃 트리** 안에서 EP 막대·진행 라벨·포트 셀이 매 tick/이벤트마다 재배치됨
- Kit은 부모 `VStack` 전체를 재계산하면서 **자식 ScrollingFrame/Placer 모두**에 영향
- 스크롤 튐 빈도는 줄었으나 **근본 해소는 안 됨**

**교훈**: 스크롤 SSOT를 지키려면 **갱신 빈도가 높은 UI와 물리적으로 분리**(별도 Window)하는 편이 낫다.

---

#### C. `Placer` + `offset_y` 수동 스크롤 (같은 창 유지)

**시도**

- `ScrollingFrame` 제거 → `timetable_viewport` + `Placer(offset_y = -_tt_scroll_y)`
- `_tt_scroll_y`를 채널 dict의 **단일 SSOT**로 유지
- 휠: `set_mouse_wheel_fn(x, wheel_y, modifier)` (**3인자** — Kit omni.ui 규약)
- 우측 12px 트랙 + thumb Rectangle

**부분 성공 / 한계**

- ScrollingFrame 대비 **제어권은 확보**됨 (복원 로직 없이도 원칙적으로 사용자 위치 유지 가능)
- 그러나 **모니터 창 안**에 두면 EP 막대 `destroy`+재생성·진행 텍스트 갱신 시 Placer 자식 트리가 흔들리며 스크롤이 여전히 튈 수 있음

**교훈**: Placer 방식은 맞는 방향이나, **갱신 원인과 같은 창에 두지 말 것**.

---

#### D. EP 막대 VStack “재사용” (자식만 교체)

**시도**

- 막대 갱신 시 `ep_timeline_host`를 유지하고 **내부 자식만** 교체
- `with host:` **밖에서** Placer/막대 위젯 재구성
- 프리런 중 `skip_render` + `dt=0` 프리런 tick으로 막대 갱신 억제

**실패 원인**

1. `with host:` 밖에서 조립한 위젯은 Kit 규칙상 **부모에 attach되지 않아 화면 미갱신**
2. `skip_render`가 `dt <= 1e-9`만 보면 프리런 구간에서 **virtual time(`t_bar`) 변화도 막아** 재생 시작 후 막대 시간이 **멈춘 것처럼** 보임

**해결 (막대 쪽)**

- 막대는 반드시 `with ch["ep_timeline_host"]:` **안에서** `destroy` 후 재생성
- `skip_render` 판단은 `dt`가 아니라 **`t_bar`(막대 시각) 변화 여부** 기준으로 변경

**교훈**: 타임테이블 문제를 고치다 **막대 tick 경로를 건드릴 때**는 `skip_render` 조건을 분리해 검증할 것.

---

#### E. 녹색 하이라이트 — `lbl.style` 런타임 변경

**시도**

- 단일 `ui.Label`에 `lbl.style = {color: 녹색}` 로 활성 행 표시
- 깜빡임 방지를 위해 `_tick_playback`에서 highlight 호출 **제거** → Seek/시작 시에만 `refresh_timetable_row_highlight` 1회

**실패 원인**

1. 재생 중 highlight가 없어 **다음 행으로 넘어가도** 녹색이 갱신되지 않음
2. Seek/시작 시 1회만 칠하면 **잠깐 보였다 사라짐** (이후 tick·레이아웃이 style을 초기화)
3. `Label.style` 런타임 변경은 EP 막대·진행 패널 갱신 시 **Kit이 레이아웃 패스에서 리셋**하는 경우가 많음

**교훈**: 하이라이트는 **스타일 변경이 아니라 visible 토글**로, 그리고 **재생 tick마다** 재적용해야 한다.

---

#### F. 이중 Label `visible` 토글 + 캐시 skip

**시도**

- 행마다 `lbl_idle` / `lbl_active` 두 Label (스타일은 mount 시 1회만 설정)
- `_timetable_row_style_cache`로 “이미 active면 skip” 최적화

**실패 원인**

- Kit 레이아웃 리셋 후 `visible`이 idle로 돌아가도 캐시는 `active=True` → **재적용을 건너뜀** → 녹색 소실

**해결**

- 캐시 skip **제거** — `refresh_timetable_row_highlight` 호출마다 모든 행에 `visible`·배경색 **강제 재적용**
- `Rectangle` 배경색도 idle/active에 맞게 같이 변경

---

### 7.3 최종 해결 (TBS As-Built, 2026-05)

#### 1) 타임테이블 **별도 Window**

```
build_control_window(ext)
  ├─ build_sim_monitor_window(ext)     # "TBS 시뮬 모니터" — FOUP·포트·EP막대·진행
  ├─ build_sim_timetable_window(ext)   # "TBS 타임테이블" — 프리런 JSON 행만
  └─ _rebuild_all_sim_ui_panels(ext)   # 분할 1~4화면 동시 재조립
```

| 항목 | 내용 |
|------|------|
| 모니터 창 | `ext._sim_monitor_split_host` — 채널당 포트·막대·진행만 |
| 타임테이블 창 | `ext._sim_timetable_split_host` — 동일 `_sim_monitor_channels` dict에 `timetable_host` 장착 |
| 분할 변경 | `sim_multi_view` → `_rebuild_sim_monitor_split_ui_fn` = `_rebuild_all_sim_ui_panels` |
| Start 시 | `_rebuild_all_sim_ui_panels` 로 위젯 누적 방지 |
| 프리런 완료 | `_scroll_sim_monitor_to_timetable` → 타임테이블 창 `visible` + focus |

**효과**: EP 막대·포트가 매 tick 갱신되어도 타임테이블 Placer는 **다른 Window 트리**라 스크롤·행 UI가 형제 갱신에 끌려가지 않음.

---

#### 2) Placer 스크롤 SSOT (`control_sim_timetable_ui.py`)

| 요소 | 역할 |
|------|------|
| `ch["_tt_scroll_y"]` | 스크롤 위치 **유일한 SSOT** (복원/pin/watchdog **없음**) |
| `timetable_placer.offset_y` | `-_tt_scroll_y` |
| `bind_timetable_wheel` | viewport `set_mouse_wheel_fn` 3인자 |
| `timetable_scroll_track` + thumb | 수동 스크롤바 표시 |
| `mount_interactive_timetable` | remount 시 `saved_scroll` 보존 후 `_apply_scroll_y` |

**원칙**: 사용자가 휠로 이동한 위치를 **절대 자동으로 되돌리지 않음** (LAM의 `scroll_here_y` 자동 추적과 정책이 다름 — 아래 7.4 참고).

---

#### 3) 녹색 하이라이트 규칙

**활성 버킷 결정** (`resolve_active_timetable_bucket`):

```
metas를 t 오름차순 순회
  t_row <= sim_now 이면 active_t = t_row 갱신
  t_row > sim_now 이면 break
→ active_t와 동일한 t를 가진 모든 행 인덱스가 녹색
```

| 시점 | 호출 |
|------|------|
| 재생 tick | `_tick_playback` → `refresh_all_timetable_highlights(ext)` |
| 행 클릭 Seek | `_on_timetable_row_seek` → `refresh_timetable_row_highlight(..., sim_now=t_target)` |
| UI remount 후 | `_remount_interactive_timetables` 끝 → `refresh_all_timetable_highlights` |
| 프리런 완료 mount | `_finalize_prerun_ui_assets` 내 mount 직후 `sim_now=0.0` |

**행 UI 구조** (행당):

```
ZStack(height=22)
  Rectangle  — 배경 (idle / active 색)
  Label idle   — 기본 텍스트, visible=True/False
  Label active — 녹색 텍스트, visible=False/True
```

스타일은 **mount 시 1회**만 설정. 런타임에는 `visible`과 배경 `Rectangle.style`만 변경.

---

#### 4) 데이터·채널 참조 (변경 없음 + 추가)

| `ext` 필드 | 용도 |
|------------|------|
| `_sim_monitor_channels` | 화면별 통합 채널 dict (포트·막대·타임테이블 위젯 참조 공유) |
| `_sim_timetable_channels` | 화면 키 → 채널 (highlight lookup) |
| `_sim_timetable_row_metas_by_screen` | remount·Seek용 `TimetableRowMeta` |
| `_sim_timetable_window` / `_sim_timetable_split_host` | 타임테이블 전용 창 |

---

### 7.4 LAM 포팅 시 권장 매핑

| TBS 최종 | LAM 현재 (`simulation_play.py`) | LAM 권장 |
|----------|----------------------------------|----------|
| 별도 `TBS 타임테이블` Window | `LamSimulationCsvPlayWindow` 안 `ScrollingFrame` 스케줄 | **타임라인만 별도 Window** 분리 검토 (Play 제어창과 분리) |
| Placer + `_tt_scroll_y` SSOT | `ScrollingFrame` + `scroll_y` | 장시간 재생·Seek 반복 시 Placer 방식이 유리. 단순 프리뷰만이면 ScrollingFrame 유지 가능 |
| 스크롤 **자동 추적 없음** | `_scroll_timeline_label_into_view` → `label.scroll_here_y()` | LAM은 “재생 중 현재 행이 보이게”가 UX 목표일 수 있음 → **자동 스크롤 ON/OFF 토글**로 분리 권장 |
| `t <= sim_now` 마지막 버킷 녹색 | `_apply_schedule_row_highlight` / `active_keys` | 동일 규칙으로 Seek cursor·`virtual_time`과 통합 |
| idle/active 이중 Label | (구현에 따라 style 변경 가능) | **visible 토글** 방식으로 통일 — style 런타임 변경 지양 |
| tick마다 highlight | CSV 재생 루프·UI ticker | `run_csv_timed_playback` tick 또는 overlay 갱신 시 **매번** highlight 재적용 |
| `_rebuild_all_sim_ui_panels` | 분할 뷰 없음 (단일 EQP) | CSV Play Window 재오픈 시 스케줄 스택만 remount, **스크롤 SSOT 보존** |

**LAM에서 그대로 가져올 코드 패턴**

1. `build_timetable_column_ui` — Placer·휠·thumb (TBS `control_sim_timetable_ui.py`)
2. `refresh_timetable_row_highlight` / `refresh_all_timetable_highlights`
3. `mount_interactive_timetable` + Seek 콜백 → `CsvTimelinePlayer.seek` (신규 `lam_csv_prerun_playback.py`)

**LAM에서 의도적으로 다를 수 있는 점**

- LAM CSV 타임라인은 **재생 중 현재 JSON 행을 자동으로 뷰포트에 넣는** `scroll_here_y`가 이미 있음 (TBS는 사용자 스크롤 존중이 우선).
- 포팅 시: **클릭 Seek 직후**에는 highlight만 맞추고 스크롤은 유지, **연속 재생 중**에만 선택적으로 auto-scroll — 두 정책을 분기하는 것이 안전함.

---

### 7.5 타임테이블 Seek 중 애니 재생 — 추가 수정 (2026-05)

**증상**: 애니 JSON 재생 중 타임테이블 행 클릭 시 prim 초기화 없이 이어지는 경우.

**원인**: `_fast_apply_prerun_seek` 가 `stop_all_*` + `_restore` 만 호출하고 `SequenceRunner.pause()`·pending 큐·UI `ANIM_EVENT` 큐 정리를 하지 않음.

**수정** (`control_window.py`):

| 함수 | 역할 |
|------|------|
| `_halt_anim_for_prerun_seek` | 화면별 `runner.pause()`, pending/active 클리어, tick pause 해제 |
| `_purge_anim_events_from_sim_queue` | Seek 화면의 잔여 `ANIM_EVENT` 큐 폐기 |
| `_seek_extra_steps_for_restore` | 재생 중 job `parsed` step → `_restore` `extra_steps` |
| `_fast_apply_prerun_seek` | 재생 중이면 `player.stop()` → 위 정리 → `_restore(..., usd_context_name=ctx)` → state-only → `player.seek()` |

---

### 7.6 문제 재발 시 체크리스트

- [ ] 타임테이블이 **모니터/막대와 같은 Window**에 다시 합쳐지지 않았는가?
- [ ] `_tick_playback`(또는 LAM 재생 루프)에서 **`refresh_all_timetable_highlights`가 호출**되는가?
- [ ] highlight가 **`lbl.style`만** 바꾸고 있지 않은가? (→ 이중 Label + visible)
- [ ] remount 후 **`saved_scroll` + highlight 재적용**이 있는가?
- [ ] EP 막대 `skip_render`가 **`dt==0`만** 보고 `t_bar` 정지를 유발하지 않는가?
- [ ] 위젯 재생성이 **`with host:` 컨텍스트 안**에서 이루어지는가?

---

### 7.7 관련 TBS 문서

- `morph.tbs_control_2/docs/TBS_Prerun_Seek_UI_Implementation.md` — 모듈·데이터 필드 요약 (본 절의 실패/해결 상세는 **이 문서 §7**이 SSOT)
- `morph.tbs_control_2/docs/sim_control_architecture_guide.md` — 전체 시뮬 UI 아키텍처

---

*작성 기준: morph.tbs_control_2 프리런·Seek 명세 v3 + 타임테이블 UI 안정화 (2026-05)*
