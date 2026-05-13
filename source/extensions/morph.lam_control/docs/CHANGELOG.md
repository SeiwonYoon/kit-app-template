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

## [0.x] - 2026-05-14 — Empty + drag&drop · Extract · Bake 일관성 정리

사용자가 Stage panel 에서 **[Empty] 후 viewport 에 USD 를 직접 drag&drop** 하는
워크플로(= 자산이 ``/World/<inst>/<중간>/<자산루트>/...`` 형태로 박힘) 에서 Extract /
Bake 가 master 트리에 자산을 한 단계 더 복제하던 회귀를 모두 잡고, drag&drop 으로
만들어진 계층을 **그대로 유지한 채** Option E mirror 와 Bake 가 동작하도록 정리.

### Added

- **`lam_types.AnimationInstance.mirror_root_prim_path: str = ""`** —
  drag&drop 으로 자산이 인스턴스 직속이 아닌 자식 경로(예: ``/World/aaa/test1``)에
  박혔을 때 Option E mirror 가 OverridePrim 을 그 prim 아래에만 만들도록 가리키는
  **앵커 prim path**. 비어 있으면 기존처럼 ``inst.prim_path`` 와 동일 동작([USD 추가]
  로 인스턴스 prim 자체에 reference 가 박힌 정상 흐름).
- **`lam_extract_from_master.discover_drag_drop_asset_root_prim(stage, inst, asset)`**
  (신설) — master 인스턴스 산하 모든 prim 의 PrimStack 의 reference / payload
  ``assetPath`` 를 인스턴스 루트 + master 루트 layer 기준으로 절대 경로화한 뒤
  **``asset`` 파일과 같은 파일을 가리키는 가장 깊은 prim** 을 앵커로 반환. ref 매칭이
  없을 때만 레거시 “자산 default prim 이름과 같은 prim” fallback. 로그
  ``[LAM/Extract] discover_drag_drop_asset_root_prim(ref-match) ...`` /
  ``... (name-fallback) ...`` / ``... MISS ...`` 으로 결과 추적 가능.
- **`lam_extract_from_master.normalize_asset_uri_to_path(raw)`** — Kit drop handler 가
  박은 ``file:/C:/...`` / ``file:///...`` URI / URL-encoded 문자열을 일반 OS 경로로
  정규화. 모든 자산 경로 비교 / ``os.path.isfile`` / Bake 입력 / mirror_root 탐지
  단일 진입점.
- **`lam_extract_from_master._discover_asset_path_from_master(stage, root)`** —
  ``inst.source_asset`` 이 비어 있을 때 master ``PrimRange`` 에서 첫 ref/payload
  assetPath 를 회수해 ``inst.source_asset`` 자동 갱신에 사용 (Extract / Bake / runtime
  공통).
- **`lam_instance_runtime.sync_mirror_root_prim_path_from_master(asset_path_hint)`** —
  Option E offscreen stage 를 열기 직전 호출. ``asset_path_hint`` → ``inst.source_asset``
  → master 합성 스캔 순서로 fallback 하며 ``mirror_root_prim_path`` 를 채운다. 비어
  있을 때도 한 줄 진단 로그를 남겨 콘솔에서 즉시 확인 가능 (`[LAM/Runtime]
  mirror_root_prim_path=...` / `=(empty)`).
- **`lam_runtime_evaluator._walk_prim_stack_first_ref_or_payload(stage, prim)`** —
  주어진 prim 의 PrimStack 에서 ``lam_inst_`` 사본을 제외한 첫 ref/payload 추출
  헬퍼. ``_extract_source_ref_template`` 의 인스턴스 prim 직접 ref / drag anchor 두
  경로 모두에서 재사용.
- **`lam_bake_omnigraph.BakeResult.effective_inst_prim_path`** — bake 결과에 drag&drop
  앵커(``discover_drag_drop_asset_root_prim``) 또는 인스턴스 prim 자체를 함께 반환해
  baked layer author path 와 사용자 안내 / mirror 매핑이 일관되도록 함.

### Changed

- **`lam_extract_from_master.extract_subtree_to_anonymous_layer`**
  - flatten subtree 를 anonymous layer 의 ``/Root`` 아래로 복사한 뒤 anonymous layer
    에 ``defaultPrim = "Root"`` 명시 author + ``framesPerSecond /
    timeCodesPerSecond = LAM_FIXED_FPS`` 보강. ``_build_attr_cache`` 가 다양한 sibling
    구조에서도 명시적으로 ``/Root`` 를 잡도록 함.
  - ``discovered_asset_path`` 필드는 URI 정규화된 절대 경로로 반환.

- **`lam_instance_runtime`**
  - ``setup_offscreen_stage_from_layer(..., asset_path, mirror_asset_path_hint)`` /
    ``setup_offscreen_stage(asset_path)`` 모두 **offscreen 을 열기 전에**
    ``sync_mirror_root_prim_path_from_master`` 를 호출하도록 변경. Extract / Bake /
    파일 직접 로드 세 경로 모두 첫 ``_build_attr_cache`` 부터 mirror_root 가 박힌
    상태에서 진입.
  - ``_build_attr_cache``
    - ``master_root_prim`` 결정에 ``mirror_root_prim_path`` 우선 사용.
    - **(2026-05-14 보강)** offscreen 측에서도 동일 ``delta`` (= mirror_root 경로
      에서 ``inst.prim_path`` 를 자른 나머지) 가 존재하면 offscreen ``/Root/<delta>``
      로 진입해 master 의 mirror_root 와 1:1 매핑. Extract 의 anonymous layer
      (``/Root/test1/N_07.../...``) 와 Bake 의 baked layer (default prim = 자산 root)
      두 가지 다른 구조에서 모두 정확히 매핑.
    - cache map 로그가 ``self.prim_path`` 대신 실제 master_root_prim 의 경로를 출력
      (``[LAM/Runtime] cache map off_root=... -> master=...``).

- **`lam_runtime_evaluator`**
  - **`_extract_source_ref_template(stage, prim_path)`**
    - 반환 5-튜플로 확장 — ``(kind, assetPath, primPathInAsset, customData,
      author_prim_path)``.
    - 인스턴스 prim 에 ref 가 없으면 ``discover_drag_drop_asset_root_prim`` 으로 drag
      앵커 prim 을 찾아 **그 PrimStack 에서 ref 읽기**. ``author_prim_path`` 도 앵커로
      반환.
    - ``registry.source_asset`` fallback 은 위 두 경로가 모두 실패할 때만, ``author_prim_path
      = inst.prim_path`` 로 사용 ([USD 추가] 정상 등록 경로 호환).
  - **`_set_prim_layer_offset(stage, prim_path, offset, scale)`**
    - 위 ``author_prim_path`` 로 sublayer 의 prim spec 을 만들어 ``Sdf.LayerOffset``
      을 author. drag&drop 시 인스턴스 루트 (``/World/aaa``) 에 ``source_asset``
      전체를 한 번 더 reference 해서 viewport 에 자산이 **이중 합성**되던 회귀를
      차단.
    - 진단 로그를 ``sublayer mapping authored inst=<inst> sublayer_spec=<author>
      ...`` 형식으로 변경. ``sublayer_spec`` 이 ``/World/aaa/test1`` 같은 앵커이면
      drag&drop 케이스, ``/World/aaa`` 면 [USD 추가] 정상 등록 케이스.
  - **`begin_bake_mode(prim_path)`**
    - bake 시작 시 ``_src_ref_tmpl_cache[prim_path]`` 를 무조건 제거 — drag&drop
      구조 변경 후에도 이전 세션의 잘못된 “인스턴스 루트 + source_asset” 템플릿이
      재사용되지 않게 함.
  - **`_clear_inst_sublayer_attr_defaults(prim_path)`**
    - **재귀 청소** 로 전환. drag&drop 자식 prim spec (``/World/aaa/test1/N_07.../Geom/...``)
      하위 attribute default + 빈 OverridePrim spec 까지 모두 비움 (reference /
      payload / typeName 이 있는 spec 은 보존). end_replay_mode / 재 Extract /
      재 Bake 직전 master 트리가 깨끗한 상태로 시작되도록 함.
    - **(2026-05-14 추가 보강)** sublayer 에 ``/World/aaa`` PrimSpec 이 없고
      ``/World/aaa/test1`` 만 있는 케이스도 ``pseudoRoot`` 에서 ``inst.prim_path``
      prefix 의 자식 spec 을 찾아 청소.
  - **`extract_and_attach_from_master(prim_path)`**
    - attach 직전에 ``result.discovered_asset_path`` → ``inst.source_asset`` 사전
      갱신. 동시에 ``attach_memory_baked_layer(..., mirror_asset_path_hint=...)``
      로 자산 절대 경로를 명시 전달 → 첫 attr_cache 빌드 시점에 drag&drop 앵커가
      정확히 인식.
  - **`attach_memory_baked_layer(prim_path, baked_layer, *, source_asset_for_log,
    mirror_asset_path_hint)`** — ``mirror_asset_path_hint`` 인자 신규. 호출자가
    자산 절대 경로를 알 때 직접 전달해 mirror 매핑 지연/실패를 회피.

- **`lam_bake_omnigraph.bake_prim_to_timesamples_async`**
  - ``drag_drop_prefix`` 를 ``discover_drag_drop_asset_root_prim`` 단일 진입점에서
    계산하고, **discovery 모드와 무관하게** ``_collect_targets`` / OmniGraph 자동
    deactivate 매핑 / baked layer author 매핑 (``effective_inst_for_map``) 에 모두
    사용. 결과 ``BakeResult.effective_inst_prim_path`` 로 호출자에 전달.

- **`lam_multi_usd_loader.clear_instance_contents`**
  - Empty 후 ``inst.baked / source_asset / asset_kind`` 와 함께
    ``inst.mirror_root_prim_path = ""`` 도 초기화 — 다음 drag&drop / [USD 추가] 시
    잘못된 mirror_root 가 살아남지 않게 보장.

- **`lam_window`**
  - **`_on_extract_instance`** — Extract 성공 / 실패 분기 모두 ``result.discovered_asset_path``
    를 URI 정규화 후 ``inst.source_asset`` 에 반영. UI 행 라벨도 새 ``kind`` 에 맞춰
    refresh.
  - **`_on_bake_instance`** — bake 시작 시 ``self._evaluator.end_replay_mode(prim_path)``
    를 호출해 이전 TIMESAMPLES_REPLAY 의 inst sublayer 잔재(default + 빈 over spec)
    를 청소. baked layer attach 시 ``mirror_asset_path_hint=abs_path`` 명시 전달.
  - ``raw='file:/C:/...'`` 같은 URI 자산 경로도 ``normalize_asset_uri_to_path`` 로
    정규화 후 ``os.path.isfile`` 검사 → Bake 의 자산 경로 해석 실패 회귀 차단.

### Fixed

- **Empty + drag&drop → [Extract] / [Bake] 시 “내부 자산이 인스턴스 바로 아래로
  복제”되는 회귀** — Option E mirror author 가 ``mirror_root_prim_path`` 미설정
  상태에서 인스턴스 prim 직속에 OverridePrim 으로 자식 트리를 다시 만들던 문제
  해결. Extract / Bake / 파일 직접 로드 세 경로 모두 mirror_root 가 박힌 상태에서
  ``_build_attr_cache`` 가 시작.
- **Bake 직후 master 트리에 자산이 이중으로 보이던 회귀** — ``begin_bake_mode`` 의
  ``_set_prim_layer_offset`` 이 인스턴스 prim 에 ref 가 없으면 ``registry.source_asset``
  로 인스턴스 루트에 전체 자산을 다시 reference 하던 폴백을 drag anchor 경로로
  대체. 인스턴스 루트에는 자산이 추가 합성되지 않음.
- **Extract → Bake 연속 실행 시 master 에 default opinion 누적**으로 “재복제”처럼
  보이던 회귀 — ``_clear_inst_sublayer_attr_defaults`` 재귀 청소 + Bake 직전
  ``end_replay_mode`` 호출로 차단.
- ``file:/C:/...`` URI 형태 ``source_asset`` 에 대한 ``os.path.isfile`` 실패로 Bake
  자산 경로 해석이 깨지던 회귀 — 모든 진입점에서 ``normalize_asset_uri_to_path``
  적용.
- ``mirror_root_prim_path`` 정의가 없는 자산(default prim 이름과 master 자식 이름이
  다른 케이스) — ref 매칭 기반 ``discover_drag_drop_asset_root_prim`` 으로 탐지
  성공률 상승. 진단 로그로 ref-match / name-fallback / MISS 추적 가능.

### Verified (2026-05-14)

- ``morph.tbs_control_1`` 코드 변경 행수: **0**.
- ``morph.lam_control`` 안의 ``omni.timeline`` import: **0**.
- ``morph.lam_control`` 안의 ``morph.tbs_control_1`` import: **0**.
- ``morph.lam_control/morph/lam_control/`` 의 모든 .py 파일 lint: **0 errors** (편집
  대상: ``lam_extract_from_master.py`` / ``lam_instance_runtime.py`` /
  ``lam_runtime_evaluator.py`` / ``lam_bake_omnigraph.py`` /
  ``lam_multi_usd_loader.py`` / ``lam_types.py`` / ``lam_window.py``).
- 사용자 실측 회귀 시나리오 OK (Kit 콘솔 로그 기준):
  - Empty → drag&drop (test1.usd, OmniGraph 자산) → [Extract] →
    ``kind=OMNIGRAPH`` 안내 + ``inst.source_asset`` 자동 갱신 +
    ``mirror_root_prim_path=/World/aaa/test1`` 채워짐.
  - 이어서 [Bake] →
    ``sublayer_spec=/World/aaa/test1`` 로 author, master 트리에 인스턴스 직속 중복
    합성 없음, ``effective_inst_prim_path=/World/aaa/test1``, baked layer
    attach OK.

### 참고 — bake 가 필요 없는 자산(timeSamples 보유) 흐름

- 본 변경은 “Extract / Bake 시 mirror author 위치” 에 한정. ``kind=TIMESAMPLES_*``
  자산은 viewport drag&drop 후 [Extract] 한 번이면 ``ok=True`` 로 attach 되어,
  ``TIMESAMPLES_REPLAY`` step / Sequence Editor 에서 **[USD 추가] 로 정상 등록한
  경우와 동일하게** 애니메이션 작업·시퀀스 등록이 가능. Bake 가 필요한 자산에서만
  본 [Bake] 흐름 + ``begin_bake_mode`` / ``end_bake_mode`` / drag anchor 매핑 로직이
  타임라인에 등장한다.

## [0.x] - 2026-05-14 — JSON Chain Tester 창 재구성 (시뮬 자동 매핑 입력 저작)

JSON Chain Tester 창의 운용 목적을 “여러 JSON 의 단순 연쇄 실행 테스터” 에서
“**시뮬레이션 자동 재생기 입력용 통합 JSON 저작 도구**” 로 확장. 시퀀스 에디터와
schema 자체는 동일(step 배열) 이지만, **입력 단위가 step 이 아니라 JSON 파일** 인
점이 본 창의 핵심 차이.

### Added

- **행 = JSON 파일 단위 sticky 드롭다운** — 행 추가 / 순서 변경 / 삭제 시 다른 행의
  파일 선택값이 보존되도록 ``ui.ComboBox.model.get_item_value_model()`` 의
  ``add_value_changed_fn`` 콜백으로 ``_Row.file_index`` 를 즉시 갱신. rebuild 시
  보존값을 ``default_idx`` 로 사용.
- **위 / 아래 순서 바꾸기** — 모든 행에 ``↑`` / ``↓`` 버튼. 첫/마지막 행은 해당
  버튼 비활성. Editor / Result 모드 양쪽에서 동작.
- **시간 컨트롤 3-state** — JSON 행 안에 ``[X] time [모드 토글 버튼] [____] sec``.
  - ``OFF`` : 이전 행 종료 직후 순차 진행
  - ``+sec`` : 이전 행 종료 후 N 초 대기
  - ``@sec`` : Run 시작(t=0) 기준 절대 N 초
  - 모드 토글 버튼 클릭 시 OFF → +sec → @sec → OFF 순환. 체크박스로 별도 ON/OFF
    가능.
- **`Save Merged` / `Load Merged` 버튼** — ``omni.kit.window.filepicker.FilePickerDialog``
  로 저장 / 불러오기.
  - Save: 현재 Editor 행 구성을 inline 평탄화한 **step 배열 JSON 1개** 저장. 시뮬
    자동 재생기 입력 형식 (= 시퀀스 엔진과 동일 schema). 시간 모드는 ``DELAY``
    step 으로 환산해 끼움 (``OFF`` → 미 삽입, ``+sec`` → 그 자리에 DELAY, ``@sec``
    → ``max(0, sec - baseline)`` DELAY).
  - Load: step 배열 JSON 을 불러와 **Result 모드** 에 step 단위 행으로 표시.
    원래의 “JSON 파일 행” 으로 양방향 복원은 하지 않음 (사용자 결정 2026-05-14
    — 필요해지면 별도 schedule 메타 파일로 추가 예정).
- **Result 보기 모드** — Editor / Result 토글. Result 는 Load 한 step 배열 (또는
  Save 직후 결과) 을 ``[idx] TYPE  summary  ↑ ↓ Remove`` 형식으로 표시. 동일한
  창 안에서 ``Run`` 으로 직접 실행 가능.
- **Run 동작 로직 분리** —
  - Editor mode : ``_run_loop_editor`` 가 행 단위로 시간 모드를 해석해 행마다
    별도의 ``LamSequenceRunner.run`` 호출. ``Stop`` 은 100ms 단위 sleep
    인터럽트.
  - Result mode : ``_run_loop_merged`` 가 step 배열을 ``LamSequenceRunner.run``
    한 번에 흘림 (= 시퀀스 에디터 [Run] 과 동일 흐름).

### Changed

- ``_Step`` → ``_Row`` 로 리네임 + 속성 확장 (``file_index`` / ``time_enabled`` /
  ``time_mode`` / ``time_value`` / ``delay_sec``). 기존 ``SimpleIntModel`` /
  ``SimpleFloatModel`` 직접 보관 대신 **데이터는 일반 Python 속성에 두고**, UI
  모델은 rebuild 마다 새로 만들어 ``add_value_changed_fn`` 으로 데이터에 즉시
  동기. 행 순서 변경/삭제 시 stale 모델 참조로 인한 버그 가능성을 원천 차단.
- 창 크기 720x520 → 820x620 (액션 바 / 모드 / Save·Load 버튼 추가 반영).
- 헤더 안내 문구 2 줄로 갱신 (시간 체크박스 / Save Merged / Load Merged 워크플로
  설명).
- ``[+ Add JSON]`` / ``[+ Add Delay]`` 는 ``Result`` 모드에서 눌리면 자동으로 ``Editor``
  로 전환된 뒤 행 추가 — 사용자 컨텍스트 혼동 방지.

### Schema 정리

- **JSON Chain Tester schedule schema** : 본 창에서 행 단위로 보유하는 내부 모델
  (현재 메모리 only). 저장하지 않음.
- **Merged JSON schema** : 시퀀스 엔진과 동일한 step 배열
  (``[{type, ...}, {type:"DELAY", duration: ...}, ...]``). 시뮬 자동 재생기 입력으로
  그대로 사용. 본 창의 ``Save Merged`` 산출물이 이 schema.

### Run 동작 모델 (기존 정책 유지)

- ``USD_TIMELINE`` / ``TIMESAMPLES_REPLAY`` step 은 ``LamSequenceRunner`` 가 ``Scheduler.start()``
  후 estimated duration 만큼 wall-clock wait → ``runner.run(...)`` 반환 시점이 “그
  JSON 이 끝난 시점” 의 의미가 된다. 따라서 Editor mode 의 ``+sec`` / ``@sec`` 시간
  계산이 자연스럽게 동작.
- ``Stop`` 은 ``self._stop_flag`` 만 세움 — 현재 진행 중인 ``LamSequenceRunner.run``
  내부 sleep / wait 은 호출자(본 창의 ``_sleep_stoppable``) 단계에서만 끊긴다. 한
  번의 ``runner.run`` 이 진행 중이면 그 시퀀스 안의 step 들은 끝까지 진행될 수
  있다 (시퀀스 에디터 Stop 과 동일 동작 — 향후 ``runner.stop()`` 까지 호출하도록
  확장 가능).

### Verified (2026-05-14)

- ``lam_json_test_window.py`` lint : **0 errors**.
- 기존 ``LamJsonTestWindow(registry, scheduler, sequence_dir)`` 시그니처 / ``show()`` /
  ``destroy()`` 보존 → ``lam_window.py`` 측 호출 흐름 변경 0.
- ``LamSequenceRunner`` 호출 인터페이스 변경 0 (기존 ``runner.run(steps)`` 그대로).
- ``omni.timeline`` import 0 / ``morph.tbs_control_1`` import 0 (기존 격리 정책 유지).
