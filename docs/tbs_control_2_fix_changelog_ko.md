# TBS Control 2 — 수정 작업 기록

실무 이슈(1~7) 대응을 이 파일에 누적한다.  
코드 수정 시마다 **날짜·이슈 번호·요약·주요 파일**을 추가한다.

---

## 이슈 목록 (요구 요약)

| # | 요구 |
|---|------|
| 1 | REMOVE만 JSON **종료** 시 prim 숨김/보임. 나머지 JSON은 **renewal** |
| 2 | 프리런/웹 데이터: 공정 시작 행 + JSON 시작 행(`{EVENT} 동작중`). 기본·`_temp`·웹 slim 동일 |
| 3 | 첫 JSON 앞 공백 제거 플래그 (미착수) |
| 4 | INOUT 점유 시 BP→EP 미진행이면 INOUT→BP 즉시 / 공정 즉시·애니만 직렬 (미착수) |
| 5 | 화면1·2 막대 싱크 (미착수) |
| 6 | REMOVE wall 시간 설명/기대 정리 (미착수) |
| 7 | 위치 초기화 완료 후 애니 시작 (미착수) |

---

## 2026-09-07 — #1 REMOVE prim 숨김 SSOT 복구

### 원인
JSON emit이 `anim_play_start`로 바뀐 뒤, 스케줄이 progress(`공정 t0`)와 event(`play_start`)를 **같은 t로만** 페어링해 `t_json_end`/play 필드가 빠짐.  
REMOVED hold가 `공정시작+anim`으로 붕괴 → renewal 전에 hold 만료 → prim 조기 소실.

### 수정
- `playback_schedule.py`: event↔progress 페어링을 `event_seq` / `event_start` / `anim_play_start`로 복구. SSOT `port_sync` 유지.
- `control_sim_playback_plan.py`: hide 시각 SSOT `_resolve_removed_prim_hide_end_sim` (`t_json_end` / `anim_play_end`).
- `find_scheduled_step`: sync None 시 step 폐기 제거 + play_start 매칭.
- `control_window.py`: REMOVED JSON 시작 시 hold에 `anim_play_end`·port 보강.

### 정책 (불변)
- REMOVE 3D prim: **JSON 종료**까지 보임  
- 그 외 JSON: **renewal** 시 숨김/보임  
- 패널 EMPTY: renewal (기존)

---

## 2026-09-07 — #2 타임테이블 JSON 시작 행 (`… 동작중`)

### 요구
프리런 결과의 **기본 JSON / `_temp` / 웹 slim** 모두:

| 시각 | `event` 표기 (예) |
|------|-------------------|
| 공정 시작 `t0` | `ARRIVED` |
| JSON 시작 `anim_play_start` | `ARRIVED 동작중` |

### 수정
- `control_sim_prerun_playback.py` — `build_timetable_row_metas`:
  - JSON dispatch event → `format_json_playing_event_label` (`{SEQ} 동작중`)
  - anim 있는 RUNNING progress → 공정 시작을 `kind=event` 행으로
- `control_sim_bar_graph.py` — `build_prerun_export_document_web_slim`:  
  ARRIVED/REMOVED/MOVE* 및 `… 동작중` event 행을 slim에 **유지** (기존엔 FOUP·step+anim만 통과)
- `control_window.py` — `_build_prerun_timetable_text` 도 metas SSOT 사용 (콘솔/텍스트 동일)

### 데이터 경로
`build_timetable_row_metas` → export 문서 `timeline.timetable_rows`  
→ 디스크 기본 / `_temp`(web_slim) / 웹 전송 slim 이 동일 소스.

### 비고
재생 타임라인 `seq` 자체는 그대로(ARRIVED 등). **표기/export 행만** `동작중`을 붙임 → JSON 매핑·게이트 부작용 없음.

---

## 다음 예정

- #3 첫 JSON 공백 제거 플래그  
- #4 공정 즉시 / INOUT→BP 규칙  
- #5 화면2 막대 싱크  
- #6 wall 표시 기대 정리  
- #7 리셋 후 애니  
