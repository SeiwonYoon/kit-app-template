# Changelog — `morph.lam_control`

본 변경 이력은 본 확장의 사양(`docs/LAM_Spec.md`) 및 더 큰 설계 사양서(`../../morph.tbs_control_1/docs/USD_Timeline_Spec.md`) 와 함께 본다.

## [0.1.0] - 2026-05-10

### Added — Phase 0 (스켈레톤)

- 신규 확장 `morph.lam_control` 추가.
- 5-Layer 모듈 골격 — `lam_master_stage` / `lam_multi_usd_loader` / `lam_composition_discovery` / `lam_instance_registry` / `lam_playback_scheduler` / `lam_runtime_evaluator`.
- 보조 모듈 — `lam_types` / `lam_id_resolver` / `lam_sequence_engine` / `lam_sequence_editor` / `lam_external_event_runner` / `lam_window`.
- 데이터 폴더 — repo 루트 `lam/lam_event_sequences/event_1~5.json`, `lam/lam_external_results/sample_external_result.json`, `lam/usd/`. (확장 안의 `data/` 폴더가 아니라 repo 루트와 분리.)

### Added — Phase 1

- LAM 전용 Viewport Window 자동 생성(`lam_viewport.py`).
- `LamWindow.show()` 시점에 master USD context(`morph_lam_master`) 자동 보장.
- Save/Open Master 후 root layer EditTarget 강제(REQ-005 P-3).

### Added — Phase 2

- `lam_attribute_reauthor.py` 신설 — 인스턴스별 attribute 캐시 + `attr.Get(timeCode) → attr.Set(val)` 로 root layer default 에 reauthor.
- `RuntimeEvaluator` 가 매 프레임 EditTarget 강제 + per-instance reauthor 호출(`omni.timeline` 미사용).

### Added — Phase 3

- `CompositionDiscovery` 의 R1·R2·R3 모두에서 `read_asset_time_range()` / 폴백 `_stage_local_time_range()` 로 인스턴스의 `asset_start_time/end_time/tps` 자동 채움.

### Added — Phase 4

- `LamExternalEventRunner` 에 `pause / resume / restart / set_speed` 추가. 가상 sim_time 기준의 폴링 sleep.
- `RuntimeEvaluator` 에 `set_global_speed / get_global_speed / invalidate_attr_cache` 추가.
- LAM Window 에 Sim Speed FloatField + Apply Speed 버튼, Pause/Resume/Restart 버튼 추가.

### Changed — 데이터 위치 이동

- 확장 내부 `data/` 폴더에 두던 시퀀스/외부 결과/USD 자산을 **repo 루트의 `lam/`** 폴더로 이동.
  - `lam/lam_event_sequences/event_1~5.json`
  - `lam/lam_external_results/sample_external_result.json`
  - `lam/usd/` (master.usd 저장 권장 위치)
- `lam_window._find_lam_data_root()` 가 `__file__` 부모를 거슬러 올라가며 `lam/` 폴더를 자동 탐지. source 빌드/_build/exts 어느 쪽에서 import 되든 같은 위치를 본다.
- 기본 master path / results path / sequence dir 가 모두 `lam/` 루트 기준으로 채워짐.

### Changed — Phase 5: UX 단순화 + Viewport 정책 + JSON 테스트 창

- **REQ-007 결정 A**: `lam_viewport.py` 를 "별도 LAM Viewport 창 생성" 에서 "default viewport 의 `usd_context_name` 만 LAM master 로 마운트(=`show()`) / 종료 시 이전 컨텍스트로 unmount" 로 재설계. 화면에 viewport 창이 1 개만 보임.
- **REQ-008**: `lam_window.py` 5 평면 섹션 → **가이드형 두 흐름**(`① 새로 시작 — USD 추가해 합성 만들기` / `② 기존 합성 USD 열기`) + `CollapsableFrame` 으로 재구성. "Master USD 라는 단어가 나오지만 파일명은 임의(예: master.usd 는 예시)" 한 줄 안내문구 추가. 모든 경로 입력 옆에 `omni.kit.window.filepicker.FilePickerDialog` 다이얼로그 버튼(USD 추가 / Open Master / Save Master As / Results path). Save 다이얼로그는 `.usd` 확장자 자동 보완. **LAM Window 가 뜨면 `LamSequenceEditor` 도 같이 자동 오픈**.
- **REQ-009**: `lam_json_test_window.py` 신설(`LamJsonTestWindow`). 시퀀스 편집기와 별개의 가벼운 연쇄 실행 테스터 — `+ Add JSON` 으로 `lam/lam_event_sequences/*.json` 드롭다운 선택, `+ Add Delay` 로 사이사이 wall-clock 대기, `Run` 은 별도 데몬 스레드에서 step 들을 순차 처리, `Stop` 은 100ms 폴링으로 즉시 반응. ADD_JSON 은 `LamSequenceRunner.run()` 호출(USD_TIMELINE 은 Scheduler 등록 후 즉시 반환 → 다음 step 진행 가능).
- `extension.toml` 에 `omni.kit.window.filepicker = {}` 의존성 추가.

### Verified

- `morph.tbs_control_1` 코드 변경 행수: **0**.
- `morph.lam_control` 안의 `omni.timeline` import: **0**.
- `morph.lam_control` 안의 `morph.tbs_control_1` import: **0**.

### Changed — Phase 5.1: REQ-007 결정 A → A' 재설정 + REQ-010 upAxis 자동 보정

- **REQ-007 결정 A → A' 재설정**: 사용자 환경의 일부 Kit 빌드에서 `viewport.usd_context_name` setter 가 silent 하게 무시되어, 별도 LAM 컨텍스트(`morph_lam_master`) 의 prim 이 default Viewport·Stage 패널·Property 패널 어디에도 안 보이는 문제 확인. → `lam_master_stage.py` 의 `LAM_MASTER_CONTEXT_NAME = ""` 로 변경해 **LAM 도 default 컨텍스트를 사용**. `ensure_context()` 는 default context 의 stage 가 비어 있을 때만 새 stage 를 만들고 upAxis 를 Z 로 명시. `lam_viewport.show()` 는 ctx="" 일 때 mount/폴백 둘 다 no-op (자동 가시). `is_default_visible()` 의미 확장. LAM Window 에 안내 라벨 2 줄(default 컨텍스트 사용 + TBS USD Load 주의) 추가.
- **REQ-010 upAxis 자동 보정**: 자산 USD 의 `UsdGeom.GetStageUpAxis` 와 master stage 의 upAxis 가 다르면 reference prim 에 `RotateX(±90)` 자동 author. 헬퍼 `read_asset_up_axis` / `_stage_up_axis` / `_author_up_axis_fix` 신설. `opSuffix="lamUpAxisFix"` 로 같은 prim 에 두 번 author 되지 않도록 보장. customData 에 `lam:asset_up_axis` / `lam:master_up_axis` 기록(다음 세션 재로드 시 디버깅 + 추적).

### Verified (Phase 5.1)

- `morph.tbs_control_1` 코드 변경 행수: **0**.
- `morph.lam_control` 안의 `omni.timeline` import: **0**.
- `morph.lam_control` 안의 `morph.tbs_control_1` import: **0**.

### Added — Phase 6: REQ-011 Sequence Editor TBS 동등 4 종 step

- **`lam_translate_animation.py`** (신설) — TBS `translate_animation.py` 와 동일 의미의 LAM MOVE animator. `_OFFSET_SUFFIX="TBS_OFFSET"` TranslateOp 에 누적 보간. `omni.kit.app` update event stream 사용.
- **`lam_rotate_animation.py`** (신설) — TBS `rotate_animation.py` 와 동일 의미의 ROTATE animator. 3 모드(simple / lock_world_center / world_pivot_euler).
- **`lam_offset_correction.py`** (신설) — TBS `_apply_world_space_offset_correction` 와 동일 수식. USD_TIMELINE 시작 직전 `TBS_OFFSET` 두 op 재계산. `omni.timeline` 미사용 — start_seconds × asset_tps 로 `Usd.TimeCode` 직접 생성.
- **`lam_hide_helper.py`** (신설) — TBS hide refcount + delayed unhide(0.2s) 동등.
- **`lam_sequence_engine.py`** (재구성) — 4 종 step 분기 실코드(`USD_TIMELINE` / `MOVE` / `ROTATE` / `DELAY`) + `run_with_previous` 그룹(leader 즉시, follower step_delay_ms 만큼 background thread 에서 sleep 후 시작, anchor 종료까지 wait) + `step_delay_ms` (initial wait, follower offset, group-to-group) + `_start_from_current` / `_start_snapshot` 메타 처리(m16→TBS_OFFSET 두 op 분해 author) + `hide_enabled` step 시작/종료 처리 + USD_TIMELINE `offset_correction_enabled` 호출. Run/Stop API.
- **`lam_sequence_editor.py`** (전면 재작성) — `STEP_TYPES = ["USD_TIMELINE", "MOVE", "ROTATE", "DELAY"]` ComboBox + 각 종 UI 행(prim/duration/dx-dz/rx-rz/auto_pivot_world_center/user_axis_rotate/pivot_w*/start-end_frame/speed_scale/loop) + USD_TIMELINE 한 곳에만 LAM 인스턴스 드롭다운/상태 배지/Re-bind + `prim/guid/instance_id/source` 표시 행 + 'Stage 선택에서 prim 가져오기' 버튼(MOVE/ROTATE) + 첫 step 의 `_start_from_current` 메타 UI(Snapshot 캡처/비우기) + `hide_enabled/hide_prims` UI + `run_with_previous/step_delay_ms` UI + JSON Save/Load (`omni.kit.window.filepicker.FilePickerDialog`, TBS 와 동일 schema 보존) + Run/Stop background thread.
- **`lam_window.py`** (3 줄 변경) — `LamSequenceEditor` 생성 시 `default_dir = lam/lam_event_sequences` 전달.

### Verified (Phase 6)

- `morph.tbs_control_1` 코드 변경 행수: **0** (TBS 의 `.py` / `.toml` 파일 변경 없음, `git status` 의 Modified 는 모두 data/JSON 또는 docs).
- `morph.lam_control` 안의 `morph.tbs_control_1.*` import: **0**.
- `morph.lam_control` 안의 `omni.timeline` import: **0**.
- `morph.lam_control/morph/lam_control/` 의 모든 .py 파일 lint: **0 errors**.
