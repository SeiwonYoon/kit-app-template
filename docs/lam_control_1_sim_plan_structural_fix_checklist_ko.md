# LAM Control 1 — 실무 시뮬 구조 수정 설계 체크리스트

> 작성: 2026-08-04 · **갱신: 2026-08-05**  
> 목적: 증상 패치가 아닌 **재생 plan SSOT** 기준으로 꼬임·UI·평면/3D 불일치를 구조적으로 제거  
> 기본 모드(합의): **`aligner_fix`** — Aligner 합성 ON, ATM/VTM occupancy order-swap OFF  
> 구현 상태: **1차 반영 완료** + **2026-08-05 FOUP 반환 규칙 통일** (아래 §구현 요약·Buffer 절 갱신)

---

## 구현 반영 요약 (1차 + 2026-08-05)

| 항목 | 상태 | 비고 |
|------|------|------|
| Plan 모드 `CSV_PLAYBACK_PLAN_MODE` | ✅ | 기본 `aligner_fix` |
| ATM/VTM greedy swap 기본 OFF | ✅ | `build_csv_playback_plan` 끝 + scheduler 공통 게이트 |
| Aligner 합성 raw 제외 | ✅ | `aligner_synthesis_enabled()` |
| **투어 끝 AtmArm → FOUP place** | ✅ | `last.slot_key == LOGICAL:ATM_ARM` → place 합성 (`last.end_sec`) |
| Buffer→FOUP 강제 삽입/사이 place 제거 | ❌ 폐지 | `_ensure_buffer_to_foup_absolute_rules` = no-op · 캐시 `atm_end_foup=1` |
| pick/place visibility 모드 정규화 | ✅ | `lam_event_sequences._normalize_pick_place_visibility_modes` |
| 평면도 ↔ visibility SSOT | ✅ | visible+역할 규칙 라벨과 동일 |
| 타임라인 soft 매칭 lot/cassette | ✅ | 시간 무시 광역 매칭 제거 |
| Overlay remount/로그 1회화 | ✅ | FOUP / wafer / device / HUD |
| 실무 AI용 파일 맵 문서 | ✅ | `docs/lam_control_1_sim_parse_rules_wafer_map_ko.md` |
| wafer-ID dry-run 신규 엔진 | ⏳ 후속 | full 모드에서 greedy swap 대체 예정 |
| 진단 UI 한 줄 요약 패널 | ⏳ 후속 | 콘솔 `[swap]` 로그만 1차 |

---

## Aligner 절대 규칙 (2026-08-04 확정 · 코드 SSOT)

모듈: `morph/lam_control_1/lam_aligner_process_rules.py`

1. FOUP pick → **Aligner place** 이어서 세트 (항상).
2. **Aligner pick** 은 같은 wafer 의 **ATM→Airlock1/2 place 직전**에만.
   - 끼어든 다른 wafer ATM 동작이 있으면 그쪽 우선 후 pick.
   - 투어에 ATM→airlock 홉이 없으면 place 만, pick 보류.
3. Aligner pick 직후 buffer / FOUP / cooling / 비-airlock place → **plan 에서 pick 제거**
   (`_enforce_aligner_absolute_rules`).
4. cassette 번호 stamp + pick/place visibility 정규화로 hide/show 번호 일치.

---

## 투어 끝 AtmArm → FOUP place (2026-08-05 확정 · 코드 SSOT)

**규칙 (Aligner와 별개, wafer 공정 종료)**

1. `_group_dwell_tours` — `(lot_id, cassette_slot)` 투어.
2. **마지막 dwell** 이 `LOGICAL:ATM_ARM` 이면 → `atm_foup*_place` **합성**으로 해당 wafer 공정 종료.
3. 삽입 시각 = `last.end_sec` (직전 공정 직후).
4. pick 출처(airlock / buffer / cooling / …) **무관** — 파싱상 팔에 들고 끝나면 반환.

**수정 위치**

| 파일 | 심볼 |
|------|------|
| `simulation_play.py` | `build_csv_playback_plan` / `build_csv_playback_schedule` 내 `if last.slot_key == LOGICAL_SLOT_ATM_ARM` |
| `simulation_play.py` | `_place_schedule_entry`, `_place_schedule_entry_meta`, `build_foup_pick_place_steps` |
| `simulation_play.py` | `_csv_playback_config_tag` → `atm_end_foup=1` |

**증상**: ATM이 wafer 든 채 다음 동작 → 해당 투어 `last.slot_key`가 AtmArm인지 먼저 확인 (파싱/CSV AtmArm 행).

---

## Buffer→FOUP 절대 규칙 — **폐지 (2026-08-05)**

~~모듈: `lam_buffer_return_rules.py` + `_ensure_buffer_to_foup_absolute_rules`~~

- 강제 삽입·Buffer~FOUP 사이 비-FOUP place 제거는 **중단**.
- `_ensure_buffer_to_foup_absolute_rules` = **Deprecated no-op**.
- `lam_buffer_return_rules.py` = 이벤트명 헬퍼만 유지.
- FOUP 반환은 **위 「투어 끝 AtmArm」 규칙으로 통일**.

---


```
CSV / Federation / GET
        ↓ 파싱 1회
   DwellRecord[]
        ↓ build_csv_playback_plan
   schedule + blocks
        ├─ [항상·aligner_fix] FOUP pick 후 Aligner 합성 + 절대규칙 enforce
        ├─ [항상] 투어 끝 AtmArm → FOUP place 합성
        ├─ [E] Buffer→FOUP enforce = no-op
        ├─ [모드 full_occ_correct 만] 점유·팔홀딩 국소 swap (기본 OFF)
        └─ [full 만] visibility 시각 오프셋 후처리
# 전체 파일/함수 맵: docs/lam_control_1_sim_parse_rules_wafer_map_ko.md
        ↓
   CachedCsvPlayback  (애니·타임라인·진단의 단일 입력)
        ↓ JSON 실행 시 PRIM_VISIBILITY
   visibility bus → 3D 라벨 트래커 + 평면도 점유  (동일 규칙)
```

**원칙**

1. 통째 타임라인 밀기/당기기 금지 → **충돌 구간의 국소 재배치만**.
2. wafer **번호(ID)** 까지 dry-run 한 뒤에만 swap.
3. 평면도 / 3D 번호 / 애니 hide·show 는 **같은 visibility 이벤트·같은 ctx** 를 본다.
4. 진단 UI 는 “몇 초에 무엇을 바꿨는지” 한 줄 요약.
5. FOUP 반환 보정은 **투어 끝 AtmArm만** (Buffer 전용 강제 금지).

---

## 2. Plan 모드 플래그

| 모드 | Aligner 합성 | ATM/VTM occupancy·arm swap | visibility 시각 shift |
|------|--------------|----------------------------|------------------------|
| `raw` | OFF | OFF | OFF |
| `aligner_fix` (**기본**) | ON | OFF | OFF\* |
| `full_occ_correct` | ON | ON | ON (기존 scheduler) |

\* `aligner_fix` 에서도 향후 Aligner 전용 dry-run 경고만 진단에 남을 수 있음 (재생 순서는 그대로).

설정 위치: `lam_sim_control_defaults.py`

- `CSV_PLAYBACK_PLAN_MODE: str = "aligner_fix"`
- 기존 `CSV_PLAYBACK_OCCUPANCY_SCHEDULER_ENABLED` 는 모드에서 파생 (호환 유지).

---

## 3. 파일별 수정 체크리스트

### 3.1 Overlay remount / 로그 폭주 (P0 — 실무 조작성)

| 파일 | 수정 포인트 | 구조적 내용 |
|------|-------------|-------------|
| `lam_viewport_foup_status_3d.py` | `sync_layers` / `destroy` / `_mount` | 이미 mount·동일 토글이면 no-op. destroy 는 **실제로 해제할 때만** 1회 로그 |
| `lam_wafer_viewport_labels.py` | `sync_layers` / `_ensure_scene` | `SceneView mounted` 는 **신규 mount 1회만**. 반복 sync 시 silent |
| `lam_viewport_device_labels_3d.py` | `sync_layers` / destroy 로그 | 동일 |
| `lam_csv_viewport_hud.py` | `mount`/`sync_layers` | `Viewport 패널 표시` 는 최초 mount 또는 viewport id 변경 시만 |
| `lam_window.py` | `_on_overlay_toggle_changed` / `_apply_overlay_toggles` | 이중 호출이 remount 폭주하지 않도록 토글 listener 와 post_update apply 가 **같은 idempotent sync** 만 호출 |

### 3.2 Plan 모드 · Aligner / 점유 보정 분리 (P1)

| 파일 | 수정 포인트 | 구조적 내용 |
|------|-------------|-------------|
| `lam_sim_control_defaults.py` | `CSV_PLAYBACK_PLAN_MODE` | 기본 `aligner_fix` |
| `lam_csv_occupancy_scheduler.py` | `occupancy_scheduler_enabled` / 모드 헬퍼 | full 모드에서만 후처리 |
| `simulation_play.py` | `_maybe_apply_occupancy_scheduler` / `_apply_csv_playback_arm_serial_rules` / `_csv_playback_config_tag` | swap 은 full 모드에서만. Aligner `_append_aligner_after_foup_pick` 는 raw 가 아니면 유지 |
| `simulation_play.py` | swap 시 진단 | `t=… [swap] A ↔ B` 요약 줄을 occupancy diagnostics 에 적재 |

### 3.3 pick/place visibility 모드 정규화 (P1 — 애니↔라벨↔평면 SSOT)

| 파일 | 수정 포인트 | 구조적 내용 |
|------|-------------|-------------|
| `lam_event_sequences.py` | `build_steps_for_event` 직후 | 이벤트명 `_pick` / `_place` 에 맞게 SLOT/ARM `PRIM_VISIBILITY.mode` 를 **강제 정규화**. JSON 스캐폴드 오류가 애니·라벨을 뒤집지 못하게 함 |
| `lam_floorplan_occupancy.py` | `on_visibility` | **visible 플래그 + pick/place** 를 라벨 트래커와 동일한 의미로 맞춤 (모드 정규화 전제) |
| `lam_visibility_occupancy_bus.py` | (유지) | 단일 fan-out 유지 |

정규화 규칙:

- `*_pick` → SLOT `hide`, ARM `show`
- `*_place` → SLOT `show`, ARM `hide`
- 해당 prim 토큰/경로로 role 판별

### 3.4 타임라인 녹색 soft 매칭 (P1)

| 파일 | 수정 포인트 | 구조적 내용 |
|------|-------------|-------------|
| `simulation_play.py` | `_schedule_entry_soft_match_key` / `_schedule_entry_matches_active` | soft 키에 **lot_id + cassette_slot** 포함. 시간 무시 exact 매칭 제거 또는 lot 일치 필수 → 초기고 Aligner/ATM 타 행 녹색 오점 방지 |

### 3.5 (후속) wafer-ID dry-run 국소 교정 엔진

| 파일 | 수정 포인트 | 구조적 내용 |
|------|-------------|-------------|
| 신규 `lam_csv_plan_occupancy_corrector.py` (후속) | dry-run + local reorder | “다음 안꼬이는 지점까지”만 앞으로 가져와 재생. 현재 greedy swap 대체 |
| 진단 패널 | swap 요약만 | 긴 visibility 오프셋 로그보다 reorder 요약 우선 |

> 이번 구현에서는 모드 분리로 **잘못된 greedy swap 을 기본 OFF** 하고, dry-run 엔진은 인터페이스·진단 포맷을 맞춰 둔 뒤 full 모드에서만 기존 swap 유지.

---

## 4. 구현 완료 기준

- [x] 토글/클릭 시 Overlay 로그가 상태 변화당 최대 1회
- [x] 기본 `aligner_fix` 에서 place-on-full / pick-on-empty 가 occupancy swap 때문에 새로 생기지 않음
- [x] pick JSON 실행 시 SLOT hide / ARM show 가 빌드 시 강제 정규화
- [x] 동일 시각대 Aligner 타임라인 soft 오점등 감소 (lot+cassette 키)
- [x] 설정만으로 `full_occ_correct` / `raw` 전환 가능
- [ ] wafer-ID dry-run 국소 corrector 로 greedy swap 대체
- [ ] 진단 패널에 swap 한 줄 요약 UI

---

## 5. 비범위 (이번 패치에서 하지 않음)

- 이벤트 JSON 파일 일괄 덮어쓰기 (런타임 정규화로 대체)
- Federation 파서·웹 스키마 변경
- TBS 쪽 playback frontier

---

## 6. 실무 검증 메모

1. `aligner_fix` 로 재생 → Aligner 경로·웨이퍼 번호 추적  
2. Cool→FOUP 직행 데이터가 있어도 swap 미적용으로 “보상발” 감소 여부  
3. Overlay 토글 연속 클릭 시 프레임/로그 안정성  
4. 필요 시만 `full_occ_correct` 로 재검증 후 진단 요약 확인  
