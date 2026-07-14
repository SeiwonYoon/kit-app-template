## LAM Viewport Overlay 유지보수 가이드 (v1)

본 문서는 “상태 표시(관측/표시)” 기능 4종의 **유지보수/확장 방법**을 정리합니다.

가장 중요한 원칙은 아래 한 줄입니다.

- **CSV Play 재생 로직(애니/스케줄 실행/타임키핑)은 절대 수정하지 않는다.**  
  오버레이는 이미 존재하는 스냅샷/콜백을 “읽어서” UI만 갱신한다.

---

## 1) 파일 구조(SSOT/런타임/UI)

### 1.1 단일 설정 SSOT (수정은 여기서 시작)

- `morph/lam_control/lam_viewport_overlay_config.py`

여기서 다음을 한 번에 수정합니다.

- **앱 시작 시 체크박스 기본값**(파일 최상단): `STARTUP_CHECK_PROCESS_ONLY`, `STARTUP_CHECK_WAFER_LABELS`, `STARTUP_CHECK_FOUP_STATUS`, `STARTUP_CHECK_DEVICE_LABELS`, `STARTUP_CHECK_PICK_WHITELIST` (`True` = 체크됨)
- **CSV 컬럼명 매핑**: `CSV_COL_EQP_ID`, `CSV_COL_LOT_ID`, …
- **EQ MODEL 기본 수동값**: `DEFAULT_EQ_MODEL_MANUAL`
- **FOUP 앵커 prim**: `FOUP_ANCHOR_PRIM_BY_INDEX`
- **FOUP 패널 오프셋**: `FOUP_PANEL_OFFSET_XYZ_M`
- **기기정보보기 라벨 목록**: `DEVICE_LABEL_SPECS` (`DeviceLabelSpec`)
- **Viewport 선택 제한 whitelist 루트**: `VIEWPORT_PICK_WHITELIST_ROOTS`

> 실무 워크플로: 경로/오프셋/표시명/색/크기는 이 파일만 편집해 조정합니다.

### 1.2 런타임 상태 저장소(실시간 변화 데이터 SSOT)

- `morph/lam_control/lam_viewport_overlay_state.py`

여기서 저장/공유되는 값:

- 토글(체크박스) 상태: `toggle_foup_status`, `toggle_device_labels`, `toggle_pick_whitelist`
- 2D 패널 수동 입력: `manual_eq_model`
- 진행 스냅샷 캐시: `progress` ( `simulation_play.get_csv_play_progress_snap()` 결과 )
- 타임라인 활성 키 캐시: `active_schedule_keys` (녹색 행 keys)
- FOUP 집계: `foup_counts` (picked/place 누적 및 파생 count)

UI는 가능한 한 이 모듈의 getter를 통해 상태를 읽고, setter로 상태를 변경합니다.

---

## 2) 오버레이가 상태를 “읽는” 지점 (재생 로직 수정 없이)

### 2.1 Time/진행시간

소스:

- `simulation_play.get_csv_play_progress_snap()`

사용처:

- `morph/lam_control/lam_viewport_status_panel.py` (2D 패널)

### 2.2 Current State(“녹색” JSON 실행 행)

소스:

- `simulation_play.get_csv_play_timeline_active_keys_snap()`  
  - 내부 `_csv_play_timeline_active_keys` set을 lock으로 읽어 스냅샷을 반환(조회 전용)

타임라인 UI 자체는 아래 로직으로 녹색 표시를 합니다.

- `simulation_play._schedule_entry_match_key(entry) in active_keys`

FOUP 집계(기능 #2)는 이 메커니즘을 그대로 따라 “현재 실행 중 엔트리”를 추정합니다.

---

## 3) UI 바인딩(어떤 모듈이 어디에 그리는지)

### 3.1 기능 #1 — Viewport 좌상단 2D 상태 패널

- UI 모듈: `morph/lam_control/lam_viewport_status_panel.py`
- 마운트 지점: `lam_window.LamWindow._sync_csv_viewport_hud()`에서 생성/`sync_layers()`
- 갱신 방식: post_update 폴링(약 5Hz) → progress snap 읽고 `SimpleStringModel` 갱신

현재 v1 구현 상태:

- EQ MODEL, EQP ID, Time, Current State UI 틀 생성
- Time은 progress snap 기반으로 갱신
- EQP ID(dwell 기준) / Current State(녹색 스케줄 기준) 텍스트 조립은 v1 규칙대로 점진 확장 가능

### 3.2 기능 #2 — FOUP 진행상황 3D 패널(카운트)

- UI 모듈: `morph/lam_control/lam_viewport_foup_status_3d.py`
- 앵커/오프셋: `lam_viewport_overlay_config.FOUP_ANCHOR_PRIM_BY_INDEX`, `FOUP_PANEL_OFFSET_XYZ_M`
- **표(패널) 스타일/줄간격/배경 크기**: `lam_viewport_overlay_config.py`의 아래 값들을 수정
  - `FOUP_PANEL_WIDTH_PX`, `FOUP_PANEL_HEIGHT_PX`
  - `FOUP_PANEL_LINE_HEIGHT_PX`, `FOUP_PANEL_FONT_SIZE`
  - `FOUP_PANEL_BG_RGBA`, `FOUP_PANEL_BORDER_RGBA`
- 집계 저장: `lam_viewport_overlay_state.set_foup_counts()`
- 표시: prim 월드 중심 + 오프셋 위치에 `SceneView` 라벨 출력

집계 규칙(v1):

- `atm_foup{n}_pick` 실행 시작 시: picked_count + 1
- `atm_foup{n}_place` 실행 시작 시: placed_back_count + 1

표시 텍스트(v1):
- `lam_viewport_foup_status_3d.py`의 `_update_ui()`에서 3줄을 구성한다.
  - 예: `FOUP{n}  {current}/{total}`, `진행중 x`, `완료 y`
  - 향후 텍스트 포맷을 설정화하려면 `overlay_config.py`에 템플릿(함수/문자열)을 추가하면 된다.

### 3.3 기능 #3 — 기기정보보기 3D 라벨

- UI 모듈: `morph/lam_control/lam_viewport_device_labels_3d.py`
- 라벨 목록: `lam_viewport_overlay_config.DEVICE_LABEL_SPECS`
- 표시: prim 월드 중심 + spec.offset_xyz_m 위치에 `SceneView` 라벨 출력

v1 기본:

- CoolStation 1개만 채움. 나머지는 설정 파일에서 추가.

### 3.4 기능 #4 — Viewport 선택 제한(화이트리스트)

- 로직 모듈: `morph/lam_control/lam_viewport_pick_whitelist.py`
- 토글: `lam_viewport_overlay_state.set_toggle_pick_whitelist(True/False)`

동작:

- viewport frame mouse_pressed로 “방금 viewport를 클릭했는지” 타임스탬프 기록
- selection changed 이벤트 발생 시, 그 이벤트가 viewport 클릭 직후에만 필터 적용
- 허용 루트 하위 클릭이면 루트로 치환, 아니면 selection clear

---

## 4) 체크박스 위치/동기화

체크박스는 아래 두 UI에 **동일하게** 존재하며, 상태는 런타임 상태 저장소로 동기화합니다.

- 시뮬 재생창: `simulation_play.LamSimulationCsvPlayWindow.show()`
- Viewport CSV HUD: `lam_csv_viewport_hud.py`

추가된 체크박스(v1):

- `FOUP상태보기`
- `기기정보보기`
- `선택제한`

---

## 5) 유지보수 팁(자주 바꾸는 것)

- **FOUP/기기 라벨 위치가 어긋남**:  
  `lam_viewport_overlay_config.py`의 offset 값을 조정
- **CoolStation 경로 변경**:  
  `DEVICE_LABEL_SPECS`의 `prim_path` 수정
- **선택 허용 루트 추가**:  
  `VIEWPORT_PICK_WHITELIST_ROOTS.append("/Your/Root")`

---

## 6) CSV Play prim 숨김 (설정 SSOT · 구현 예정)

**설정 파일:** `morph/lam_control/lam_viewport_overlay_config.py` — **「CSV Play 시 prim 숨김/보임」** 섹션

| 항목 | 변수 |
|------|------|
| 숨길 prim 목록 | `PLAY_HIDE_PRIM_SPECS` (`PlayHidePrimSpec` — `prim_path` + 항목별 fade) |
| 정지(초기화) 시 복구 | `PLAY_HIDE_RESTORE_VISIBLE_ON_STOP_RESET` |
| 전역 fade | `PLAY_HIDE_FADE_ENABLED`, `PLAY_HIDE_FADE_DURATION_SEC`, `PLAY_HIDE_FADE_HIDE_IN`, `PLAY_HIDE_FADE_SHOW_IN` |
| 앱 시작 체크 | `STARTUP_CHECK_PLAY_PRIM_HIDE` |

**동작(구현 후):** Play 시작 / 정지(초기화) / Viewport 「prim숨김」체크 — 공통 함수 + phase 플래그.  
Fade 미구현 시 `PLAY_HIDE_FADE_ENABLED=False` 로 즉시 hide/show.

---

## 7) (설계만) 웨이퍼·슬롯 시간별 스냅샷 / 추후 프리런

**구현 전** 요구사항·데이터 모델은 아래 문서에 정리되어 있다. CSV Play 동작은 변경하지 않고 관측 레이어만 추가하는 방향이다.

- `docs/LAM_Wafer_Fab_TimeSeries_Snapshot_Design.md`

