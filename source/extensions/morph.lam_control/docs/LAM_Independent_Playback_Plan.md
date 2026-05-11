# LAM Independent Playback — 재설계 검토 페이지

작성일: 2026-05-11
상태: **검토중 (사용자 합의 전, 코드 변경 0)**
배포 전 합의 필요.

---

## 0. 한 줄 요약

> 단일 stage 위에서 multi-time 을 우회로 흉내내는 **핫픽스 6 ~ 10 의 모든 시도를 폐기**하고,
> 자산을 **별도 offscreen Stage 로 격리 평가**한 결과를 **master stage 의 mirror prim 의 attribute** 에 매 frame 직접 write 하는 정통 USD-native 구조로 전환한다.
> **다른 모든 기능 (MOVE/ROTATE/DELAY step, JSON I/O, prim 직접 입력 후 RUN, suffix 자동, 외부 event runner, TBS 전체) 은 변경 0**.

---

## 1. 왜 재설계가 필요한가 (architectural 진단 요약)

USD / Hydra / omni.timeline 은 stage-global 평가 모델:

- Stage current time = 1 개
- Hydra imaging cache = stage 단위
- prototype sharing = stage 단위
- `omni.timeline.set_current_time()` = stage 전체 적용

핫픽스 6 ~ 10 (LayerOffset / sublayer override / explicitItems / freeze scale / instanceable hack) 은 모두 이 한계를 우회하려는 시도였고 결국:

- composed metadata 에서 LAM 1 개가 winner 였음에도 **시각적으로 동시 재생** (핫픽스 9-10 시점)
- `SdfLayerOffset(0, 0)` invalid LayerOffset 등 USD 의 unspecified behavior 영역에 의존

→ **architectural 한계 도달**. 우회로는 더 이상 해결 불가.

---

## 2. 재설계 방향 — Option E (정통 USD-native)

### 2.1 핵심 아이디어

```
[Master Stage]  ← 1 개. 사용자 viewport 가 보는 stage.
   /World/aaa     ← mirror prim (mesh + material 만, animation timeSamples 없음)
   /World/aaa_1   ← mirror prim

[Offscreen Stages]  ← instance 마다 1 개 (in-memory, 화면 안 보임)
   stage_for_aaa     ← test1.usd 자체. 평가 전용.
   stage_for_aaa_1   ← test2.usd 자체.

매 frame:
  for inst in registry.all_instances():
    # 1. offscreen stage 에서 자기 virtual_time 의 attribute 값 평가
    inst_stage.SetCurrentTimeCode(inst.virtual_time * tps)
    values = evaluate_animatable_attributes(inst_stage, inst.prim_path)
    # values = { xform attrs, SkelAnim joint transforms, blendshape weights, ... }

    # 2. master stage 의 mirror prim 의 동일 attribute 에 default 값으로 write
    write_default_values(master_stage, inst.prim_path, values)

→ master stage 는 timeline 평가 안 함 (current time 0 고정).
→ instance 마다 완전 독립 virtual_time.
→ 1 viewport (Hydra) 가 master stage 만 평가하므로 depth-aware 자동.
→ Skel anim 은 SkinningQuery 로 deformed mesh points 평가 후 master mirror prim 의 points 에 write.
```

### 2.2 architectural 비교

| 항목 | 핫픽스 6-10 (현재) | 옵션 E (재설계) |
|---|---|---|
| Stage 개수 | 1 | 1 + N (N = instance 수, offscreen) |
| Stage 평가 충돌 | ❌ 발생 (LayerOffset 우회 의존) | ✅ 발생 안 함 (master 는 timeline 평가 안 함) |
| Hydra 평가 | master timeline 진행 시 자산 timeSamples 와 충돌 | master 는 정적, 매 frame default 값만 갱신 |
| Independent timeline | ❌ 우회로만 흉내 | ✅ 진짜 independent (각 stage 별 평가) |
| Depth-aware composite | (single stage 라 자동) | (single stage 라 자동) |
| 1 viewport 충족 | ✅ | ✅ |
| Skel anim 지원 | △ (LayerOffset 무력화 사례) | ✅ (UsdSkel.Cache 정통 평가) |
| Kit Python API | 모두 정통 | 모두 정통 |
| 사용자 master USD 변경 | 0 (핫픽스 7+) | 0 (mirror prim 만 LAM sublayer 에 author) |

### 2.3 비용 평가

- **자산 평가 비용**: instance 마다 1 회 stage 평가. instance=2, 60fps → 120 회/sec.
- **Skel skinning 비용**: 100K vertices * 60fps * 2 instance = 12M vertex deforms/sec. UsdSkel C++ 구현 사용 → **GPU 없이도 실시간 가능**.
- **stage 메모리**: instance 당 자산 stage 1 개 in-memory. 자산이 100 MB 이면 200 MB 추가.
- **USD attribute write 비용**: master mirror prim 의 attribute 에 default 값 set → ChangeBlock 으로 묶어 1 회 ChangeNotice.

→ instance=2~5 수준에서는 비용 부담 거의 없음. instance>10 으로 확장 시 재평가 필요.

---

## 3. 절대 변경하지 않는 보호 영역 (회귀 0 보장)

### 3.1 `morph.tbs_control_1` 전체 (TBS 시뮬레이션 측)

**코드 변경 행수: 0 (현재까지 모든 핫픽스도 0 유지 중)**

- `usd_animation_control.py` — 변경 0
- `sequence_editor.py` (TBS) — 변경 0
- `sequence_engine.py` (TBS) — 변경 0
- `simulation_engine.py` — 변경 0
- `port_lot_visibility.py` — 변경 0
- `control_window.py` — 변경 0
- 기타 TBS 관련 모든 모듈 — 변경 0

### 3.2 `morph.lam_control` 의 LAM 측 사용자 기능

| 기능 | 위치 | 변경 여부 |
|---|---|---|
| LAM Multi-USD Load Window UI | `lam_window.py` | **변경 0** |
| 2 개 이상 USD 로드 (suffix 자동 `aaa`/`aaa_1`) | `lam_instance_registry.py`, `lam_multi_usd_loader.py` | **변경 0** |
| LAM Sequence Editor UI (4 종 step) | `lam_sequence_editor.py` | **변경 0** |
| `MOVE` step (이동 애니) | `lam_translate_animation.py`, `lam_sequence_engine.py` | **변경 0** |
| `ROTATE` step (회전 애니) | `lam_rotate_animation.py`, `lam_sequence_engine.py` | **변경 0** |
| `DELAY` step | `lam_sequence_engine.py` | **변경 0** |
| `USD_TIMELINE` step | `lam_sequence_engine.py` | **변경 0** (단, 내부 evaluator 호출은 그대로) |
| `Reset` 버튼 (TBS_OFFSET 초기화) | `lam_sequence_editor.py`, `lam_sequence_engine.py` | **변경 0** |
| JSON Save/Load (sequence steps) | `lam_sequence_editor.py` | **변경 0** |
| **Stage 에서 prim 경로 확인 → step 에 prim_path 입력 → RUN** | (이건 시퀀스 step 의 `prim_path` 필드 사용) | **변경 0** |
| 4-tuple ref binding (`StepRef`) | `lam_types.py`, `lam_id_resolver.py` | **변경 0** |
| upAxis 자동 보정 (`lamUpAxisFix`) | `lam_offset_correction.py` | **변경 0** |
| Hide / Show step | `lam_hide_helper.py` | **변경 0** |
| 외부 simulation event runner | `lam_external_event_runner.py` | **변경 0** |
| JSON 테스트 창 | `lam_json_test_window.py` | **변경 0** |
| LAM Viewport (default context 사용) | `lam_viewport.py` | **변경 0** |
| Composition Discovery | `lam_composition_discovery.py` | **변경 0** (또는 mirror prim 등록 보조 추가) |

→ **사용자가 "기존과 동일" 이라고 한 모든 동작이 변경 없음.**

### 3.3 회귀 테스트 체크리스트 (재설계 후 반드시 통과)

| # | 테스트 | 기대 동작 |
|---|---|---|
| R1 | LAM Window 열기 → 2 개 USD 로드 → suffix 자동 (`aaa` / `aaa_1`) | 변경 전과 동일 |
| R2 | LAM Sequence Editor 열기 → MOVE step 추가 → 빈 prim_path 박스에 사용자가 직접 `/World/aaa` 입력 → RUN | 변경 전과 동일 (prim 이동) |
| R3 | LAM Sequence Editor → ROTATE step 추가 → prim_path 입력 → RUN | 변경 전과 동일 (prim 회전) |
| R4 | LAM Sequence Editor → DELAY step → RUN | 시간 지연 후 다음 step |
| R5 | MOVE → DELAY → ROTATE 순서 step → RUN | 순차 실행, 변경 전과 동일 |
| R6 | `Reset` 버튼 → TBS_OFFSET 초기화 → prim 위치 원위치 | 변경 전과 동일 |
| R7 | JSON Save → 파일 → 다시 Load → 같은 step 복원 | 변경 전과 동일 |
| R8 | TBS Control Window 열기 → TBS 시퀀스 시작 → 정상 종료 | TBS 영역 변경 0 → 100% 통과 |
| R9 | 외부 simulation event runner 시작 → 이벤트 처리 | 변경 전과 동일 |
| R10 | upAxis 다른 자산 로드 → RotateX 자동 보정 author | 변경 전과 동일 |

→ R8 은 **TBS 코드 변경 0** 으로 자동 보장. R1~R7, R9~R10 도 변경 영역 외라 자동 보장.

---

## 4. 변경되는 영역 (이번 재설계의 영향 범위)

### 4.1 변경 / 신규

| 파일 | 변경 종류 | 내용 |
|---|---|---|
| `lam_instance_runtime.py` | **신규** | `AnimationInstanceRuntime` 클래스. instance 마다 1 개. offscreen Stage open + virtual_time 평가 + master stage mirror prim 에 default 값 write. |
| `lam_runtime_evaluator.py` | **대폭 단순화** | 핫픽스 6-10 의 모든 LayerOffset/sublayer 우회 코드 제거. 매 frame `for runtime in runtimes: runtime.update(dt)` 만. |
| `lam_multi_usd_loader.py` | **author 방식 변경** | 자산을 reference 로 추가하던 것을 → 자산의 mesh + material 만 mirror prim 으로 import (자산의 timeSamples 는 master 에 author 안 함). 기존 기능 (`add_usd`, `remove_usd` API) 시그니처는 동일. |
| `lam_master_stage.py` | **소폭 단순화** | 핫픽스 7-8 의 `ensure_inst_sublayer / remove_inst_sublayer / clear_all_inst_sublayers / _pick_attach_layer` 제거 (더 이상 필요 없음). 단 mirror prim 보존을 위한 LAM sublayer 1 개는 유지 가능 (선택). |

### 4.2 폐기 / 정리

| 파일 / 항목 | 폐기 이유 |
|---|---|
| `_set_prim_layer_offset` (전체) | LayerOffset 우회 폐기 |
| `_sync_layer_offset_mapping` | 매핑 우회 폐기 |
| `_extract_source_ref_template` | reference 추출 불필요 |
| `_sync_freeze_state` (이미 deprecated) | freeze 개념 폐기 |
| `_has_lam_reference` (이미 deprecated) | reference 우회 폐기 |
| `_advance_stage_time` | master stage timeline 진행 안 함 → 폐기 |
| `_use_omni_timeline`, `_timeline_iface`, `_get_timeline` | omni.timeline 사용 안 함 → 폐기 |
| `_master_seconds`, `_last_mapping_sig`, `_src_ref_tmpl_cache` | wall clock + mapping cache 폐기 |
| `LAM_FIXED_FPS`, `_ensure_stage_fps_lam_fixed` | 30fps 강제 설정 불필요 (master stage 평가 안 함) |
| `LAM_FREEZE_MIN_SCALE` | freeze scale 개념 폐기 |
| `lam_attribute_reauthor.py` 의 `AttributeReauthorCache` | wrote=0 의 원인이었던 master stage timeSamples reauthor 폐기. 새 runtime 이 다른 방식으로 대체 |

→ **단순 코드 line 수 감소 (대폭 단순화)**. 핫픽스 6-10 흔적 정리.

---

## 5. 단계별 구현 plan

### Phase A — 신규 모듈 단독 추가 (회귀 0)

**A-1**. `lam_instance_runtime.py` 신규 생성. `AnimationInstanceRuntime` 클래스 작성.
- `__init__(instance, master_stage)` — instance 정보 받음.
- `setup_offscreen_stage(asset_path)` — `Usd.Stage.Open(asset_path)` 호출 (in-memory).
- `setup_master_mirror_prim(prim_path)` — master stage 의 prim 을 보장 (이미 reference 로 있음 — 그대로 사용 가능).
- `evaluate_at(virtual_time)` — offscreen stage 에서 attribute 평가.
- `write_to_master_mirror(values)` — master stage 의 동일 prim 에 default 값 write.
- `dispose()` — offscreen stage 폐기.

**A-2**. 단위 테스트 (코드 작성 + 사용자 측 1 회 실행 확인).
- 동작 검증: master 에서 한 prim 만 골라 매 frame 다른 frame 의 평가값을 write → viewport 갱신 확인.

**진행 상태**: 새 모듈만 추가, 기존 코드는 미수정. 사용자가 LAM Window 열어 기존처럼 사용 가능 (단지 새 runtime 은 활성화 안 됨).

---

### Phase B — `RuntimeEvaluator` 가 새 runtime 을 사용하도록 전환

**B-1**. `RuntimeEvaluator._on_update` 수정:
- 현재의 `_sync_layer_offset_mapping` 호출을 **flag** 로 분기.
- 새 `_RUNTIME_USE_OPTION_E = True` 면 새 코드 경로 사용.
- 새 경로: instance 마다 `AnimationInstanceRuntime` 1 개 만들고, 매 frame `runtime.update(dt)` 호출.

**B-2**. master stage 의 자산 reference 처리 결정 (2 가지 후보):
- **B-2-a**: 자산 reference 를 master 에 그대로 두되, **자산 reference 의 LayerOffset 으로 timeline 평가를 freeze** (LAM sublayer 안에서 LayerOffset(0, 1e-9)) — 핫픽스 7-10 의 sublayer 1 개를 freeze 용으로만 유지. 자산의 mesh / material 은 reference 로 자연스럽게 보임. animation timeSamples 만 freeze.
- **B-2-b**: 자산 reference 를 폐기하고 mirror prim 만 author. 단 자산의 mesh / material / SkelRoot / SkelAnim 등을 master 에 1 회 import (`Sdf.CreatePrimInLayer` + 메타 복사). 비용 큼.

→ **B-2-a 권장** (간단, 안전). LAM sublayer 1 개는 freeze 전용으로 유지.

**B-3**. 사용자 측 1 회 실행 확인 — instance 1 개로 시작, USD_TIMELINE step 으로 RUN → 정상 재생되는지 확인.

---

### Phase C — Skel 평가 + multi-instance 검증

**C-1**. Skel anim 자산 (사용자 FBX→USD) 로 테스트.
- `UsdSkel.Cache.Populate(SkelRoot)` + `UsdSkelSkeletonQuery.ComputeJointLocalTransforms(time)` 로 매 frame joint transforms 추출.
- master stage 의 SkelAnimation prim 의 `joints / translations / rotations / scales` 에 default 값 write.

**C-2**. 2 개 instance 동시 RUN → 진짜 독립 timeline 검증.
- `aaa` PLAY, `aaa_1` 멈춤 → `aaa_1` 시각적으로 frozen 인지.
- `aaa` 와 `aaa_1` 둘 다 PLAY → 둘 다 자기 timeline 으로 진행.

---

### Phase D — 핫픽스 6-10 의 dead code 제거

**D-1**. `_RUNTIME_USE_OPTION_E` flag 가 안정화되면 flag 자체 폐기 + 옛날 코드 path 삭제.
**D-2**. 폐기된 helper 메서드들 일괄 삭제 (4.2 표).
**D-3**. lam_attribute_reauthor.py 정리 또는 삭제.

---

### Phase E — 회귀 테스트 통과 확인

§3.3 의 R1~R10 모두 통과 확인. 특히 R2 (사용자가 직접 prim_path 입력 후 RUN) 가 정상 동작하는지.

---

## 6. 호환성 / 하위 호환

- **사용자 master USD 파일 (`master_1.usd` 등)**: 변경 0. `Save Master` 도 동일 동작.
- **JSON sequence 파일**: 형식 변경 0. 기존 JSON 그대로 load 가능.
- **사용자 자산 USD 파일**: 변경 0. offscreen 으로 open 만.
- **TBS 와의 공존**: TBS 가 기존처럼 default context 의 stage 를 열고 시뮬 진행. LAM 측은 변경된 evaluator 사용. **두 시스템은 같은 default context 를 공유하지만 evaluator 가 timeline 을 건드리지 않으므로 충돌 0**.

---

## 7. Migration / Rollback 전략

### Migration

1. Phase A (신규 모듈만 추가) → 사용자 측에서 기존처럼 사용 가능. 회귀 0.
2. Phase B (flag 분기) → 사용자가 flag 끄면 즉시 핫픽스 10 동작. 위험 격리.
3. Phase C / D → flag 안정화 후 폐기 진행.

### Rollback

- 각 Phase 끝마다 git commit. 문제 발생 시 git revert 1-2 회로 이전 Phase 로 복원.
- Phase B 의 flag 가 안전 net — 코드 path 가 살아있으므로 즉시 핫픽스 10 mode 로 복원 가능.

---

## 8. 사용자 합의 필요 항목

| # | 항목 | 결정 필요 |
|---|---|---|
| Q1 | Phase B-2 의 자산 reference 처리 — B-2-a (sublayer freeze 유지, 권장) vs B-2-b (mirror prim 만, 비용 큼) | 권장 = B-2-a |
| Q2 | `_RUNTIME_USE_OPTION_E` flag 를 코드 안에 선언하는 게 좋은지 (rollback 안전망) vs flag 없이 바로 전환 | 권장 = flag 사용 |
| Q3 | 폐기 timing — Phase A/B 안정화 후 D 진행 (안전) vs A 부터 청소 같이 진행 (단축) | 권장 = 단계 분리 (안전) |
| Q4 | `lam_attribute_reauthor.py` 의 `AttributeReauthorCache` 처리 — 폐기 vs 유지 (다른 곳에서 사용 안 함 확인 필요) | 폐기 가능 (내부 단일 사용처) |

---

## 9. 예상 작업량 (사용자 측 reproduction cycle 포함)

| Phase | AI 작성 | 사용자 측 실행/검증 cycle | 예상 소요 |
|---|---|---|---|
| A (신규 모듈 + 단위 테스트) | 1 ~ 2 시간 | 1 회 (10 분) | **반나절** |
| B (RuntimeEvaluator 분기 + flag) | 1 ~ 2 시간 | 1 ~ 2 회 (각 15 분) | **1 일** |
| C (Skel + multi-instance 검증) | 2 ~ 3 시간 | 2 ~ 3 회 | **1 ~ 2 일** |
| D (dead code 제거) | 1 시간 | 1 회 (회귀 테스트) | **반나절** |
| E (회귀 테스트 R1~R10) | (사용자 측 작업) | R1~R10 각 5 분 | **반나절** |

**총합**: 사용자 체감 **3 ~ 5 일** (사용자가 매 cycle 빠르게 실행해주는 가정).

---

## 10. 진행 합의 방식

이 페이지를 사용자가 검토 후:

- **승인**: Phase A 부터 시작.
- **수정 요청**: 본 페이지를 먼저 갱신.
- **거부**: 옵션 C (사용자 본래 요구) 로 전환 검토.

---

## 11. 보호 영역 재확인 — 핫픽스 6-10 과의 일관성

| 항목 | 핫픽스 6-10 | 옵션 E |
|---|---|---|
| `morph.tbs_control_1` 코드 변경 | 0 | 0 (그대로) |
| 사용자 master USD 파일 변경 | 0 (핫픽스 7+) | 0 |
| 1 viewport 사용 | ✅ | ✅ |
| LAM Sequence Editor 모든 step type | 정상 | 정상 (변경 0) |
| Stage prim 경로 직접 입력 후 RUN | 정상 | 정상 (변경 0) |
| JSON Save/Load | 정상 | 정상 (변경 0) |
| Reset 버튼 | 정상 | 정상 (변경 0) |
| TBS 시뮬레이션 | 정상 | 정상 (변경 0) |

---

## 12. 진단 / 로그 정책

옵션 E 도입 시 다음 형태로 로그:

```
[LAM/Runtime] init prim=/World/aaa offscreen_stage=anon:... source=test1.usd
[LAM/Runtime] update prim=/World/aaa virtual_time=0.523s skel_joints=14 wrote=14
[LAM/Runtime] update prim=/World/aaa_1 virtual_time=0.000s skel_joints=14 wrote=0(frozen)
```

핫픽스 6-10 의 진단 로그 (`[LAM/L5] sublayer mapping authored`, `composed metadata`, `TOP WINNER LAYER`) 는 더 이상 출력되지 않는다 — 평가 모델이 다르므로 의미 없음.

---

## 끝

이 페이지를 사용자가 검토 후 합의하면 Phase A 부터 시작합니다.
