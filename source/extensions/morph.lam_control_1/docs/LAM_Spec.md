# LAM Control 확장 사양서 (Phase 0~5 — 실코드 반영)

> 본 문서는 새 확장 `morph.lam_control` 의 **자체 사양서** 다. 더 큰 설계 맥락(REQ-002 ~ REQ-010) 은 `morph.tbs_control_1/docs/USD_Timeline_Spec.md` 와 묶여 있고, 본 문서는 그 카드들의 결정값을 본 확장 안에서 **어떤 파일이 어떤 책임을 지는가** 로 풀어낸 것이다.
> 버전: **v0.4 (Phase 5.1 — REQ-007 결정 A→A' 재설정 + REQ-010 upAxis 자동 보정)**

---

## 1. 한 줄 정의

`morph.lam_control` 은 **여러 USD 자산을 한 화면(단일 master stage) 에 reference 로 합성** 하면서도, **각 인스턴스의 timeline 을 자체 가상 시각으로 독립 재생** 할 수 있게 해 주는 LAM 전용 확장이다. `omni.timeline` 을 사용하지 않으며, `morph.tbs_control_1` 의 어떤 모듈도 import 하지 않는다.

---

## 2. 절대 보호 영역 (반드시 지켜야 할 규약)

| # | 규약 | 위배 시 |
|---|---|---|
| 1 | 본 확장의 어떤 모듈도 `morph.tbs_control_1.*` 를 import 하지 않는다. | TBS 회귀 위험. REQ-002 0줄 변경 원칙 위배. |
| 2 | 본 확장의 어떤 모듈도 `omni.timeline.set_current_time()` / `tl.play()` 류를 호출하지 않는다. | USD_Timeline_Spec §3.1 단일 stage 멀티 평가 한계로 인스턴스 평가 시각이 섞인다. |
| 3 | LAM 이 author 하는 prim 은 **root layer** 에 author 한다(REQ-005 P-3). | session layer 만 author 시 master.usd 저장에 안 잡혀 다음 세션에서 사라진다. |
| 4 | step 의 USD 참조는 단일 키가 아니라 **4-튜플 `prim_path/guid/instance_id/source_asset`** 으로 저장한다(REQ-006). | 자산 갱신 시 시퀀스가 깨진다. |
| 5 | `instance_id` 는 충돌 시 **자동 suffix(_1, _2, …)** 를 붙인다(REQ-002 결정 4 = b). | 같은 표시 이름으로 여러 자산을 등록하면 한쪽이 거부되거나 의미가 섞인다. |

---

## 3. 파일 트리와 책임

```
source/extensions/morph.lam_control/
├ config/
│  └ extension.toml                  # Kit 확장 메타. omni.timeline 의존성 의도적 제외.
├ morph/
│  └ lam_control/
│     ├ __init__.py                  # IExt 진입점 export
│     ├ extension.py                 # on_startup / on_shutdown
│     ├ lam_types.py                 # AnimationInstance / StepRef / ResolveResult dataclass
│     ├ lam_master_stage.py          # L1-a: default USD context 사용 + open/save (REQ-005, REQ-007 A')
│     ├ lam_multi_usd_loader.py      # L1-b: USD reference attach + customData + REQ-010 upAxis 자동 보정
│     ├ lam_composition_discovery.py # L2: master 안에서 인스턴스 발견(R1·R2·R3) + 시간정보 채움
│     ├ lam_instance_registry.py     # L3: AnimationInstance 단일 진실 원천 + suffix
│     ├ lam_playback_scheduler.py    # L4: start/stop/pause/speed/loop API
│     ├ lam_attribute_reauthor.py    # L5 보조: 인스턴스별 attribute 캐시 + reauthor 실코드
│     ├ lam_runtime_evaluator.py     # L5: heartbeat tick + global_speed + reauthor 디스패치
│     ├ lam_viewport.py              # ctx="" 일 때 no-op + [강제 열기] 폴백 viewport(REQ-007 결정 A')
│     ├ lam_id_resolver.py           # REQ-006 4-튜플 ref + 우선순위 Resolver
│     ├ lam_sequence_engine.py       # LAM 시퀀스 step 실행기 (4 종 + 그룹/지연 + offset_corr + hide + start_snapshot)
│     ├ lam_sequence_editor.py       # LAM 시퀀스 편집기 UI (4 종 ComboBox + USD_TIMELINE 만 인스턴스 드롭다운)
│     ├ lam_translate_animation.py   # MOVE animator (TBS_OFFSET TranslateOp 누적 보간) — REQ-011
│     ├ lam_rotate_animation.py      # ROTATE animator 3 모드 (simple/lock_world_center/world_pivot_euler) — REQ-011
│     ├ lam_offset_correction.py     # USD_TIMELINE offset_correction (TBS 동일 수식 별도 구현) — REQ-011
│     ├ lam_hide_helper.py           # hide refcount + delayed unhide(0.2s) — REQ-011
│     ├ lam_external_event_runner.py # 외부 JSON 결과 → 시퀀스 트리거 (T1) + speed/pause/restart
│     ├ lam_json_test_window.py      # JSON 테스트 창(시퀀스 편집기와 별개, 연쇄 실행 검증)
│     └ lam_window.py                # 메인 창(가이드형 두 흐름 + 파일 다이얼로그 + 시퀀스 편집기 자동 오픈)
└ docs/
   ├ LAM_Spec.md                     # ← 이 문서
   ├ CHANGELOG.md
   └ README.md
```

데이터 자산은 본 확장 폴더가 아니라 **repo 루트의 `lam/` 폴더** 에 둔다.
```
<repo root>/
└ lam/
   ├ README.md
   ├ usd/                            # 자산 USD 보관(또는 절대경로 사용). master.usd 도 여기에 저장.
   ├ lam_event_sequences/            # event_N.json (외부 결과의 event 와 매칭)
   └ lam_external_results/           # 외부 시뮬 결과 샘플(*.json, t 정렬)
```

코드는 `__file__` 에서 부모를 거슬러 올라가며 `lam/` 폴더가 있는 첫 위치를 자동 탐지(`_find_lam_data_root`). 따라서 source 직접 실행이든 `_build/.../exts/...` junction 경유이든 같은 폴더를 본다.

---

## 4. 5-Layer 데이터 흐름 (한 줄로 보기)

```
사용자 USD 추가
  └→ L1-b MultiUsdLoader.add_usd()
       ├ Master root layer 에 /World/<usd_id> Xform define + reference attach
       ├ customData(`lam:guid/instance_id/source_asset/instance`) author
       └ L3 Registry.register()  ──┐
Master USD Open                       │
  └→ L1-a MasterStage.open_master()   │
       └ L2 CompositionDiscovery.discover()  ──┘   ← R1/R2/R3 으로 등록
                                             │
시퀀스 step 실행                              │
  └→ LAM SequenceRunner._run_usd_timeline()  │
       ├ StepRef.from_dict(step["ref"])      │
       ├ resolve_step_ref(L3.all_instances(), ref)
       │     ├ guid → prim_path → instance_id → source_asset
       │     └ 매칭 성공 시 step["ref"] 자동 갱신(Q-3)
       └ L4 PlaybackScheduler.start(prim_path, ...)
                                             │
매 프레임                                   │
  └→ L5 RuntimeEvaluator._on_update()        │
       └ for inst in L3.all_instances() if state=="playing":
            virtual_time += dt * inst.speed
            (Phase 2) attr.Get(timeCode) → reauthor on root layer
```

---

## 5. Phase 별 진행 상태 (현재 = Phase 0~5 모두 완료)

| Phase | 목표 | 상태 |
|---|---|---|
| **Phase 0** | 빈 스켈레톤 추가, kit 의존성 등록, 어디에서도 import 안 됨 → 회귀 0 | **완료** |
| **Phase 1** | LAM Window show 시점에 master context 자동 보장 + LAM 전용 Viewport 1개 자동 생성. Save/Open 후 root layer EditTarget 강제. | **완료** |
| **Phase 2** | L5 attribute reauthor 실코드. per-instance `attr.Get(timeCode) → attr.Set(val)` 으로 root layer default 에 박아 reference 안의 timeSamples 를 마스킹. → 단일 master stage 안에서 인스턴스마다 다른 시각의 동시 표현 가능. | **완료** |
| **Phase 3** | L2 Discovery 의 R1·R2·R3 모두에서 자산 USD 의 stage start/end/tps 자동 추출 → 인스턴스의 `range_mode="full"` 가 즉시 정확한 길이로 동작. master.usd 재오픈 왕복 시에도 시각 정보 그대로 복원. | **완료** |
| **Phase 4** | External Runner 정밀화(global speed scale, pause/resume, restart). LAM Window 에 Sim Speed/Pause/Resume/Restart 컨트롤. Sim Speed 는 Evaluator 의 reauthor 속도 + External Runner 의 trigger 속도 모두에 동시 적용. | **완료** |
| **Phase 5** | **REQ-007 결정 A** — 별도 LAM viewport 폐지, default viewport 의 `usd_context_name` 만 LAM master 로 마운트(`lam_viewport.py`). **REQ-008** — 가이드형 두 흐름(① 새로 시작 / ② 기존 합성 USD 열기) + CollapsableFrame + 파일 다이얼로그(`omni.kit.window.filepicker.FilePickerDialog`) + Master USD 표기 안내문구 + Sequence Editor 자동 오픈. **REQ-009** — `lam_json_test_window.py` 신설(드롭다운 ADD_JSON + DELAY 백그라운드 순차 실행). | **완료** |
| **Phase 5.1** | **REQ-007 결정 A → A' 재설정** — 일부 Kit 빌드의 setter silent fail 문제로 LAM 도 default 컨텍스트(`""`) 사용. `LAM_MASTER_CONTEXT_NAME = ""`, `lam_viewport.show()` 가 ctx="" 일 때 no-op. 모든 Kit 기본 패널이 자동 가시. **REQ-010 upAxis 자동 보정** — `lam_multi_usd_loader.add_usd()` 가 자산 reference attach 직후 upAxis 비교 → 다르면 `RotateX(±90)` 자동 author. customData 에 보정 정보 기록. | **완료** |

---

## 6. UI 사용 흐름 (Phase 5.1 기준 동작 검증)

1. Kit 실행 → 자동으로 `LAM Multi-USD Load` 창 + `LAM Sequence Editor` 창이 같이 열린다. **LAM 은 default 컨텍스트를 그대로 사용**(REQ-007 결정 A')하므로 기존 default viewport·Stage 패널·Property 패널이 자동으로 LAM 의 prim 을 본다.
2. **① 새로 시작 — USD 추가해 합성 만들기** 영역에서 `Asset path` 옆 `...` 버튼 → 파일 다이얼로그가 열리고, `lam/usd/` 가 시작 폴더. 두 USD 를 차례로 선택 → 각각 `+ USD 추가`. 등록된 인스턴스 목록에 두 줄이 추가되고, 같은 default viewport 한 화면에 두 USD 가 같이 보인다.
3. **② 기존 합성 USD 열기** 영역에서 `...` 버튼으로 이전에 저장한 합성 USD 1 개 파일을 선택 → 즉시 `Open Master…` 가 실행되고, **R1 Discovery** 가 인스턴스를 자동 복원. 또는 `Save Master As…` 로 새 파일명(임의) 으로 저장(파일명 강제 X).
4. 자동으로 떠 있는 LAM Sequence Editor 에서 `+ USD_TIMELINE` → step 추가됨. Instance 드롭다운으로 대상 인스턴스 선택. 상태 배지 ● OK 확인. `Run` 누르면 **L5 가 실제 attribute 를 reauthor** 하여 viewport 에서 애니메이션이 보인다.
5. **도구 → JSON 테스트 창 열기** 로 `LamJsonTestWindow` 을 열어, `+ Add JSON` 으로 `lam/lam_event_sequences/*.json` 을 드롭다운으로 골라 step 추가, `+ Add Delay` 로 사이사이 delay 부여 → `Run`. **JSON 실행 도중 다음 JSON 진입** 시나리오를 손쉽게 검증(USD_TIMELINE 은 Scheduler 등록만 하고 즉시 반환하므로 인스턴스마다 독립적으로 동시 진행).
6. (선택) **외부 시뮬 결과 → 시퀀스 트리거** 영역(기본 접힘) 을 펼쳐 `Results path` 옆 `...` 으로 JSON 선택 → `Run External` → 매 `t` 마다 매칭 시퀀스 실행. **`Pause/Resume/Restart`** 로 즉시 제어 가능. **`Sim Speed`** → `Apply Speed` 로 Evaluator 의 reauthor 속도 + External Runner 의 trigger 속도가 동시에 변경됨.

---

## 7. 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v0.1 | 2026-05-10 | Phase 0 골격 추가. L1~L5 + Resolver + Sequence Engine/Editor + External Runner + Window 모두 import 가능한 형태로. omni.timeline 0 사용, tbs_control_1 0 import. |
| v0.2 | 2026-05-10 | Phase 1~4 실코드. **Phase 1**: `lam_viewport.py` 신설(LAM 전용 Viewport 1개 자동), Window show 시점 master context 보장 + Save/Open 후 root layer EditTarget 강제. **Phase 2**: `lam_attribute_reauthor.py` 신설 — `attr.Get(timeCode)` 평가 후 `attr.Set(val)` 로 root layer default 에 박아 reference 안의 timeSamples 마스킹. RuntimeEvaluator 가 매 프레임 `set_root_layer_edit_target` + reauthor 호출. **Phase 3**: Discovery 의 R1·R2·R3 모두에서 `read_asset_time_range()` / 폴백 `_stage_local_time_range()` 로 인스턴스 시간 정보 자동 채움. **Phase 4**: External Runner 에 `pause/resume/restart/set_speed`, RuntimeEvaluator 에 `set_global_speed/get_global_speed/invalidate_attr_cache`, LAM Window 에 Sim Speed/Pause/Resume/Restart UI. omni.timeline 0 사용, tbs_control_1 0 import 유지. |
| v0.3 | 2026-05-10 | Phase 5 — UX 단순화. **REQ-007 결정 A**: `lam_viewport.py` 를 "별도 viewport 생성" 에서 "default viewport 의 `usd_context_name` 만 LAM master 로 마운트" 로 재설계(`show()`/`unmount()`/`destroy()`). **REQ-008**: `lam_window.py` 가이드형 두 흐름(`① 새로 시작 / ② 기존 합성 USD 열기`) + `CollapsableFrame` + "Master USD 파일명은 임의" 안내문구 + `omni.kit.window.filepicker.FilePickerDialog` 적용. LAM Window 가 뜨면 `LamSequenceEditor` 도 같이 자동 오픈. **REQ-009**: `lam_json_test_window.py` 신설(드롭다운 ADD_JSON + DELAY step 백그라운드 스레드 순차 실행, Stop 100ms 폴링). `extension.toml` 에 `omni.kit.window.filepicker` 의존성 추가. omni.timeline 0 사용, tbs_control_1 0 import 유지. |
| v0.4 | 2026-05-10 | Phase 5.1 — **REQ-007 결정 A → A' 재설정**. 일부 Kit 빌드의 `viewport.usd_context_name` setter silent fail 로 default viewport·Stage 패널이 LAM 을 못 보는 문제 확인. `lam_master_stage.py` 의 `LAM_MASTER_CONTEXT_NAME = ""` 로 변경 → LAM 도 default 컨텍스트 사용. `ensure_context()` 가 default context 의 stage 가 비어 있을 때만 새로 만들고 upAxis 를 Z 로 명시. `lam_viewport.show()` 가 ctx="" 일 때 no-op. `is_default_visible()` 의미 확장. LAM Window 에 default 컨텍스트 사용 + TBS USD Load 주의 안내 라벨 추가. **REQ-010 신규** — `lam_multi_usd_loader.py` 에 `read_asset_up_axis` / `_stage_up_axis` / `_author_up_axis_fix` 추가. `add_usd()` 가 자산 reference attach 직후 upAxis 비교 → 다르면 reference prim 에 `UsdGeom.Xform.AddRotateXOp(opSuffix="lamUpAxisFix")` 로 ±90° 보정 author. customData 에 `lam:asset_up_axis`/`lam:master_up_axis` 기록. omni.timeline 0 사용, tbs_control_1 0 import 유지. |
| v0.5 | 2026-05-10 | Phase 6 — **REQ-011** LAM Sequence Editor 가 TBS 시퀀스 편집기와 동일한 4 종 step 지원. **신규 모듈**: `lam_translate_animation.py` (TBS_OFFSET TranslateOp 누적), `lam_rotate_animation.py` (simple / lock_world_center / world_pivot_euler 3 모드), `lam_offset_correction.py` (TBS `_apply_world_space_offset_correction` 동일 수식), `lam_hide_helper.py` (refcount + delayed unhide 0.2s). **재구성**: `lam_sequence_engine.py` 가 4 종 step 모두 분기, `run_with_previous` 그룹 + `step_delay_ms` (initial / follower offset / group-to-group), 첫 step `_start_from_current` / `_start_snapshot` 메타(m16→TBS_OFFSET 두 op 분해), USD_TIMELINE 의 `offset_correction_enabled` 처리. **전면 재작성**: `lam_sequence_editor.py` 가 4 종 ComboBox + 각 종 UI 행(prim/duration/dx-dz/rx-rz/auto_pivot/user_axis/pivot_w*/start-end_frame/speed_scale/loop) + USD_TIMELINE 만 LAM 인스턴스 드롭다운/상태 배지/Re-bind + 'Stage 선택' 버튼 + 첫 step Snapshot 캡처/비우기 + JSON Save/Load(FilePickerDialog, TBS 와 동일 schema) + Run/Stop background thread. `lam_window.py` 는 Editor 생성 시 `default_dir = lam/lam_event_sequences` 만 전달. `morph.tbs_control_1` 코드 0줄 변경, `morph.tbs_control_1.*` 0 import, `omni.timeline` 0 import 유지. |
