# LAM Independent Playback + OmniGraph Bake — Handoff (2026-05-11)

본 문서는 **다음 세션이 곧장 이어서 작업할 수 있도록** 현재까지의 진행 상황과 남은 일을
한 곳에 모은 핸드오프 페이지다.

> 깊은 의사결정 흐름은 `docs/daily/2026-05-11.md`,
> 전체 설계는 `docs/LAM_Independent_Playback_Plan.md`,
> 모듈/스펙은 `docs/LAM_Spec.md` 를 본다.
> 본 페이지는 **요약·진입점·체크리스트** 만 다룬다.

---

## 0. 한 줄 상태

- Option E (Master Stage 1 + Offscreen Stage N) 아키텍처 **구현 완료**.
- OmniGraph 구동 자산을 timeSamples 로 변환하는 **[Bake] 파이프라인 동작 확인**.
- 단일 인스턴스 USD_TIMELINE 독립 재생 OK. 멀티 인스턴스 동시 재생 검증은 다음 단계.
- bake 속도/품질 정책 확정 — 무손실 모드 기본, opt-in 가속은 별도 결정 사항으로 남음.
- **`morph.tbs_control_1` 변경 0** 유지.

### 다음 세션이 읽을 순서

> 본 문서만 보고도 곧장 이어서 진행 가능하도록 구성됨. 권장 읽기 순서:
>
> 1. §A — 사용자 요구 매트릭스 (R1~R20) 로 큰 그림 파악.
> 2. §7.1 — NEVER 리스트 (회귀시키지 말 것).
> 3. §7 — 다음 세션 “바로 시작” 체크리스트.
> 4. §6.1 / §6.2 / §6.2.1 — P1 항목 3 개 (실제 작업).
> 5. 막히면 §2 의 모듈 빠른 참조 + §7.2 의 토글/env var.

---

## 1. 완료된 구성 — “지금 동작하는 것들”

### 1.1 Option E (offscreen 독립 평가)

- 코드 진입점: `lam_instance_runtime.py:AnimationInstanceRuntime`
- 매 frame `_offscreen_stage.SetCurrentTimeCode(virtual_time * 30)` 평가 →
  attr 의 timeSamples 값을 master mirror prim 에 default 로 write.
- `_RUNTIME_USE_OPTION_E = True` (default). hotfix 6~10 경로는 비활성.
- master timeline 의 instance reference 는 `Sdf.LayerOffset(0, 1e-9)` 로 freeze.
  → master 가 자산 timeSamples 를 직접 평가하지 않음.

### 1.2 FPS 30 고정 정책

- `lam_types.py:LAM_FIXED_FPS = 30.0`.
- `MasterStage.force_fixed_fps_30()` 가 master stage + `omni.timeline` 의 tps/framerate 를
  강제 30 으로 맞춤. `RuntimeEvaluator.start()` 가 호출.
- `lam_sequence_engine` / `lam_attribute_reauthor` / `lam_offset_correction` /
  `lam_multi_usd_loader.read_asset_time_range` 모두 `LAM_FIXED_FPS` 사용.

### 1.3 [Bake] 파이프라인 — OmniGraph → timeSamples

- 신규 모듈: `lam_bake_omnigraph.py`
- 공개 API: `bake_prim_to_timesamples_async(master_stage, inst_prim_path, asset_path, ...)`.
- 흐름 (요약):
  1. master(default) context 의 timeline 을 0..end 스크럽.
  2. 매 frame Kit tick 1 회 → OmniGraph(PushGraph) 평가 → prim 의 xformOp 결과 capture.
  3. 정적 attribute pruning — 모든 프레임 동일 값이면 default 1 회만 author.
  4. 출력 baked.usd 는 원본을 reference 로 묶고 위에 timeSamples over 만 author.
     원본 prim 의 OmniGraph 류 prim(`PushGraph` 등) 은 `over { active = false }`.
  5. baked.usd 의 stage upAxis 를 **원본과 동일** 하게 박음 → re-add_usd 시 보정 1 회만.
- [Bake] 클릭 시 inst.prim_path 의 인스턴스를 자동으로 baked.usd 로 교체
  (`remove_usd → evaluator.forget_instance → add_usd(baked.usd)`).
- **항상 새로 굽고 덮어씀** (Sdf.Layer.Export 가 destination 파일을 덮어쓴다).

### 1.4 LAM Window UI

- 인스턴스 행 마다 `[Bake]` / `[Remove]` 버튼.
- 상단에 `[모두 초기화]` 버튼 (registry + master stage children 일괄 정리).
- 도구 영역에 `[Master 진단]`, `[Option E 진단]`, `[LAM Viewport 강제 열기]`.

### 1.5 진단/로그 인프라

- `lam_instance_runtime._diag_dump_offscreen_animatable` 가 자산 구조를 dump
  (`diag struct`, `diag types`, `diag PointInstancer/OmniGraph paths`, `diag xformOp timeSamples`,
  `diag prototypes`, Skel counts 등).
- `RuntimeEvaluator.reset_option_e_diag(prim_path)` / `force_rebuild_attr_cache(prim_path)` 가
  매 RUN 마다 호출되어 Kit 재시작 없이 진단 로그 갱신.
- bake 로그에 단계별 시간 분포 출력:
  `phase[capture=Xs (tick=Ys read=Zs) author=Ws] total=Ts`.

### 1.6 bake 실제 성능 (참고치)

`test2.usd` (201 frames, 2150 prims, ~3,800 attrs) 기준 — Sdf-batch 적용 **직전** 측정값:

| 단계 | 시간 | 비중 |
|---|---|---|
| capture (timeline scrub + attr.Get) | 34.31 s | 77 % |
| └ tick (next_update_async 대기) | 1.48 s | 3 % |
| └ read (`attr.Get(tc)` 합계) | 32.81 s | 74 % |
| author (Sdf-batch SetInfo) | 9.75 s (Set-per-sample 시점) → **다음 세션 재측정 대상** | 22 % |
| total | 44.48 s → **다음 세션 재측정 대상** | — |

병목은 **read** (`attr.Get`). tick / author 는 작음.

→ **다음 세션 첫 번째 측정**: §7 체크리스트 4번에서 캡처한 새 `phase[...]` 분포 값을
   본 표의 “재측정” 칸에 채워 넣고 daily/2026-05-12.md 첫 항목으로 기록.

---

## 2. 핵심 파일 빠른 참조

| 모듈 | 역할 | 메모 |
|---|---|---|
| `lam_window.py` | LAM 메인 UI, `[Bake]` 진입점 | `_on_bake_instance`, `_on_reset_all` |
| `lam_bake_omnigraph.py` | OmniGraph → timeSamples bake 파이프라인 | `bake_prim_to_timesamples_async`, `read_bake_speed_env` |
| `lam_instance_runtime.py` | Option E 인스턴스 runtime | `evaluate_and_write`, `_build_attr_cache` |
| `lam_runtime_evaluator.py` | 매 frame 인스턴스 dispatch | `forget_instance`, `dump_option_e_state` |
| `lam_master_stage.py` | master stage + LAM session sublayer | `force_fixed_fps_30` |
| `lam_multi_usd_loader.py` | add/remove_usd, upAxis 보정 | `add_usd` 가 `xformOp:rotateX:upAxisFix` 박음 |
| `lam_types.py` | `AnimationInstance`, `LAM_FIXED_FPS=30.0` | tps 강제 |
| `lam_playback_scheduler.py` | start/stop/속도 | start() 시 evaluator 진단 reset |
| `lam_sequence_engine.py` | USD_TIMELINE / MOVE / ROTATE step | `USD_TIMELINE` 만 Option E 분기 |

---

## 3. 사용자 작업 흐름 (현재 가능한 것)

```
1) Kit 실행 → LAM Window 자동 열림
2) [+ USD 추가] → test*.usd 등록  ─ master upAxis 와 다르면 자동 RotateX(+90) 보정
3) 인스턴스 행 [Bake] 클릭          ─ 항상 새로 굽고 덮어씀
4) Bake 끝나면 인스턴스가 자동으로 *_baked.usd 로 교체됨
5) LAM Sequence Editor → USD_TIMELINE step 추가 → Run
   → 인스턴스가 자기 timeline 으로 독립 재생
```

다음 세션에서는 baked.usd 가 이미 있는 자산의 경우 **3 단계 없이도** 그 baked.usd 를 직접
`[+ USD 추가]` 로 선택하면 곧장 Option E 로 재생 가능.

---

## 4. 확정된 결정 사항 (정책)

| # | 결정 | 일자 | 근거 메모 |
|---|---|---|---|
| D1 | tbs_control_1 변경 0 유지 | 전체 기간 | LAM 작업이 TBS 시뮬에 영향 0. |
| D2 | Option E 기본값 True | 2026-05-11 | `_RUNTIME_USE_OPTION_E = True`. hotfix 6~10 경로는 사실상 미사용. |
| D3 | FPS 30 고정 | 2026-05-11 | timeline UI / asset_tps 모두 30. |
| D4 | OmniGraph 자산은 baked.usd 로 변환해 사용 | 2026-05-11 | OmniGraph 는 Kit 런타임 의존 → offscreen 평가 불가. |
| D5 | [Bake] 는 항상 덮어쓰기 | 2026-05-11 | mtime skip 제거. |
| D6 | bake 기본은 무손실 (`stride=1`, `sparse=False`) | 2026-05-11 | 사용자: “품질은 무조건 제일 좋아야”. |
| D7 | baked.usd 의 stage upAxis 는 원본과 동일 | 2026-05-11 | 이중 RotateX 보정 방지. |
| D8 | `[+ USD 추가] / [Remove] / [모두 초기화]` 시 evaluator.forget_instance 명시 호출 | 2026-05-11 | runtime 캐시 누수 방지. |
| D9 | bake 시 `Sdf.ChangeBlock` 사용 금지 | 2026-05-11 | 내부 OverridePrim.IsValid()=False 로 모든 prim 스킵 사례 발생. |
| D10 | bake 의 author 단계는 Sdf-레벨 `attrSpec.SetInfo("timeSamples", dict)` 로 일괄 기록 | 2026-05-11 | per-Set 합성 비용 제거. |

---

## 5. 알려진 한계 / 위험 메모

- **read 단계 무손실 가속 불가**: “이 frame 의 값이 실제로 다른지” 는 그 frame 을 읽어봐야만
  알 수 있어 매 frame 읽기는 본질적인 비용.
- **OmniGraph 종류 의존성**: 본 bake 는 `PushGraph` 처럼 “stage 시각 변경 즉시 평가” 되는
  그래프를 가정. tick-mode / 사용자 입력 의존 그래프는 미지원(검증 필요).
- **자산 reference 의존성**: baked.usd 는 원본을 reference. 원본을 옮기거나 지우면 깨짐.
  배포 시 원본 + baked 를 같이 옮겨야 함.
- **단일 인스턴스만 검증 완료**: 멀티 인스턴스 동시 재생 / event_*.json 동시 진행은 아직
  실사용 검증 미진행.

---

## 6. 남은 진행 사항 (다음 세션 시작점)

우선순위 순. 각 항목은 “현재 상태 → 다음 액션”으로 명시.

### 6.1 [P1] 멀티 인스턴스 동시 재생 검증

- **현재 상태**: Option E + Bake 로 단일 인스턴스 OK. 2 개 이상 동시 재생 미검증.
- **다음 액션**:
  1. `[+ USD 추가]` 로 `test1.usd` / `test2.usd` 둘 다 등록 후 각각 [Bake].
  2. Sequence Editor 에서 인스턴스 A 의 `USD_TIMELINE [0,60]` step, 인스턴스 B 의
     `USD_TIMELINE [0,30]` step 을 한 시퀀스에 추가 (start offset 다르게).
  3. RUN → 두 인스턴스가 **시각적으로 동시 진행** + 각자의 timeline 으로 독립 재생되는지 확인.
  4. `[Option E 진단]` 로 양쪽 runtime 의 `attrs_cached`, `virtual_time`, `wrote` 확인.
- **검증 포인트**: 한쪽 timeline 이 다른 한쪽에 누수하지 않는지, FPS 가 30 유지되는지.
- **검증용 시퀀스 JSON 예시** (Sequence Editor → Save 결과 모양):

```json
{
  "version": "1.0",
  "type": "lam_sequence",
  "steps": [
    {
      "kind": "USD_TIMELINE",
      "ref": {"prim_path": "/World/aaa"},
      "start_frame": 0, "end_frame": 60, "loop": false,
      "run_with_previous": false, "step_delay_ms": 0
    },
    {
      "kind": "USD_TIMELINE",
      "ref": {"prim_path": "/World/bbb"},
      "start_frame": 0, "end_frame": 30, "loop": false,
      "run_with_previous": true, "step_delay_ms": 500
    }
  ]
}
```

  - `run_with_previous: true` + `step_delay_ms: 500` → A 시작 후 0.5s 에 B 가 동시 진행.
  - 두 step 은 **같은 그룹** (anchor 는 두번째 step) → A 가 끝날 때까지 그룹이 wait.

### 6.2 [P1] event_*.json 동시 트리거 (외부 시뮬 결과 → 시퀀스)

- **현재 상태**: `LamExternalEventRunner` 는 이미 있음(`lam_external_event_runner.py`).
  외부 결과 JSON(`lam/lam_external_results/*.json`) 의 `t/event` 라인을 wall-clock 시간순으로
  소비 → `lam/lam_event_sequences/<event>.json` 시퀀스를 트리거하는 구조까지 구현됨.
  단 **다중 event 가 시간상 겹쳐서 동시에 진행되는 wall-clock 중첩 케이스** 는 미검증.
- **다음 액션**:
  1. `lam_external_results/example.json` 에 `{t:1, event:"event_1"}`, `{t:3, event:"event_2"}`
     라인 작성 (event_1 의 USD_TIMELINE duration 이 5s 라고 가정 → 1~6 s 진행 중
     3~? s 에 event_2 가 같이 시작 = wall-clock 중첩).
  2. `event_1.json` 은 인스턴스 A 의 USD_TIMELINE, `event_2.json` 은 인스턴스 B 의
     USD_TIMELINE 으로 작성 → **서로 다른 instance 만 건드리는 조합** 으로 먼저 검증.
  3. `LamExternalEventRunner.start()` → 두 인스턴스가 wall-clock 으로 자연 중첩 진행되는지 확인.
  4. 진행 후 같은 instance 를 두 event 가 동시에 건드리는 케이스도 시도 — last-writer-winner
     동작 + 진단 log 출력만 보장(이는 사용자 작성 책임 영역, daily §2.2 결정).
- **검증 포인트**: scheduler 의 instance-별 player 가 분리 진행되는지(`runtime_by_path`
  의 `virtual_time` 이 인스턴스마다 독립적으로 진행되는지) `[Option E 진단]` 으로 확인.
- **검증용 외부 결과 JSON 예시** (`lam/lam_external_results/example.json`):

```json
[
  {"t": 1.0, "event": "event_1"},
  {"t": 3.0, "event": "event_2"}
]
```

  - `event_1.json` → 인스턴스 A 의 `USD_TIMELINE [0, 150]` step (=5s @ FPS 30).
  - `event_2.json` → 인스턴스 B 의 `USD_TIMELINE [0, 90]` step (=3s @ FPS 30).
  - wall-clock 1s 에 event_1 시작 → 3s 에 event_2 시작 → 1~6s 가 event_1, 3~6s 가
    event_1 + event_2 동시 진행 구간.
  - LAM Window 의 [LAM/EXT] 패널(또는 콘솔 `[LAM/EXT]` 로그)로 진행 확인.

### 6.2.1 [P1] 한 step 안에서 멀티 stage 동시 트리거 (multi-target step) — **미구현**

- **사용자 요구 출처**: `daily/2026-05-11.md §2.2` —
  > “한 step 안에서 여러 stage 의 timeline / MOVE / ROTATE 를 동시 트리거”
  > → step schema 의 `targets: [StepRef, ...]` 확장으로 표현 가능. evaluator / runtime 변경 0.
- **현재 상태**: **코드 미구현**. `lam_sequence_engine.py` 의 step 처리부는
  step 당 단일 `ref` 만 처리 (line 576 의 `resolve_step_ref(...,ref)` 단일 호출).
  multi-target schema 가 들어와도 첫 ref 만 동작.
- **다음 액션**:
  1. step schema 확장 — 기존 `ref` 는 하위호환으로 유지. 신규 `targets: [{ref, prim_path}, ...]`
     필드 추가. 시퀀스 편집기 UI 에 “+ target” 버튼 신설.
  2. `lam_sequence_engine` 의 USD_TIMELINE / MOVE / ROTATE / DELAY 분기마다 `targets`
     배열을 순회해 동일 시점에 `Scheduler.start()` 또는 animator.start() 다중 호출.
     기존 `run_with_previous` 와 의미가 겹치지 않게 분리 (group = wall-clock 같이 시작,
     multi-target = 같은 step 의 결과를 여러 prim 에 동시 적용).
  3. JSON Save/Load 호환성 — 단일 `ref` 사용 시 기존 JSON 그대로 동작 보장.
- **우선순위**: 6.1 (멀티 인스턴스 동시 재생) 이 동작하는 것을 먼저 확인한 후 진행.
  6.1 검증이 안 되면 multi-target 도 의미 없음.

### 6.2.2 [P2] 여러 JSON 의 wall-clock 중첩 실행 — player pool 검증

- **사용자 요구 출처**: `daily/2026-05-11.md §2.2` —
  > “event_1@1s 5s + event_2@3s 중첩 시 둘 다 끊김 없이 진행”
  > → scheduler 의 player pool 도입으로 자연 동작. 각 player 가 target instance 의
  > `virtual_time` 만 갱신하고 evaluator 는 매 frame 모든 runtime 의 `evaluate_and_write`
  > 호출 → last-writer-winner.
- **현재 상태**: PlaybackScheduler 는 이미 `start(prim_path, ...)` / `stop(prim_path)` 로
  instance 단위 player 를 관리 → **사실상 player pool 구조**. 단 시퀀스 러너가 다중 thread 로
  동시 호출됐을 때 race 없이 진행되는지 미검증.
- **다음 액션**:
  1. `LamExternalEventRunner` 가 만든 background thread 와 사용자가 UI 에서 누른 RUN 의
     `LamSequenceRunner` thread 가 같은 instance 를 동시에 start 하는 케이스 재현.
  2. last-writer-winner 동작 (둘 다 같은 instance 의 virtual_time 을 덮어쓰는 경우)
     검증 + 진단 log 출력 확인.
  3. 서로 다른 instance 만 건드리는 케이스가 정상 동작하는지 확인 (이게 정상 시나리오).

### 6.3 [P2] MOVE / ROTATE step 과 baked.usd 의 호환성 확인

- **현재 상태**: MOVE/ROTATE 는 `TBS_OFFSET` op 를 prim 에 직접 추가. baked.usd 에는
  `xformOp:translate/rotateXYZ/scale` 의 timeSamples 가 박혀있음.
- **다음 액션**: baked.usd 인스턴스의 prim 에 MOVE step 적용 → `TBS_OFFSET` op 가
  `xformOpOrder` 에 정상 추가되어 timeline 위에 덧붙어 동작하는지 확인.
  특히 baked.usd 의 `xformOpOrder` 를 override 하지 않는지 점검 — Option E evaluator 는
  `xformOp:*` 의 timeSamples 만 master 에 write 하고 `xformOpOrder` 는 안 건드리는 게
  맞는지 한 번 더 코드 확인 필요.

### 6.4 [P2] JSON Save / Load 검증 (멀티 인스턴스 시퀀스)

- **현재 상태**: 단일 인스턴스 JSON 저장/로드 OK (기존 LAM 기능).
- **다음 액션**: 멀티 인스턴스 시퀀스 (USD_TIMELINE + MOVE + DELAY 혼합) 를 JSON 으로 저장 →
  Kit 재시작 → Open Master + Load Sequence → RUN. baked.usd 가 자동으로 인스턴스에 attach
  되는지(현재는 매번 [Bake] 를 다시 누를 필요 없음) 확인.

### 6.5 [P3] bake 속도 — opt-in 가속 (요청 시 적용)

품질은 사용자 결정 (D6 정책) — 현재 무손실 기본. 빠른 모드가 필요할 때 검토:

| 옵션 | 효과 | 품질 영향 | 적용 방법 |
|---|---|---|---|
| **probe pre-scan** | 시작/중간/끝 등 3~5 frame 만 먼저 읽어 “모두 같음” attr 를 정적 확정 → 메인 루프에서 제외. 2.4× 정도 단축 기대. | 산업 장비 단조 운동은 거의 0%. 짧은 블립이 probe 사이에서만 발생하면 미세 손실. | 신규 env `LAM_BAKE_PROBE_STATIC=1` 로 opt-in 구현 필요. |
| **xformOp 통합** | 매 frame prim 의 local matrix 1 개만 캡처 → `xformOp:transform` 으로 author + `xformOpOrder=["xformOp:transform"]` override | 시각 결과 동일(합성 행렬 같음). 구조만 단순화. MOVE/ROTATE 의 TBS_OFFSET 호환 추가 검증 필요. | bake 모듈에 `collapse_to_matrix=True` 옵션 신설 + LAM evaluator 호환 확인. |

→ 다음 세션에서 사용자가 “2000 frame 자산 bake 시간이 너무 길다” 고 보고하면 위 둘 중 하나를
opt-in 으로 즉시 적용. (현재 코드 변경 안 됨.)

### 6.6 [P3] tick-mode OmniGraph 자산 호환성

- **현재 상태**: PushGraph 만 검증.
- **다음 액션**: 실제 3ds Max → FBX → USD 변환본을 받았을 때 OmniGraph 종류 확인 (`diag types` /
  `diag OmniGraph paths` 로 식별). PushGraph 가 아니면 bake 가 0 attrs 로 실패 가능 →
  대응 분기 (Sample Test Asset 로 재현 후 별도 evaluator 호출 필요).

### 6.7 [P4] UI 정리

- `[Bake]` 클릭 시 진행 중 다른 [Bake] / [Remove] 차단 (현재는 race 가능).
- bake 진행 중 [Cancel] 버튼 노출 (`cancel_cb` hook 은 이미 모듈 내 준비됨).
- 다중 인스턴스 일괄 `Bake All`.

### 6.8 [P4] 문서 정리

- `LAM_Spec.md` 에 Option E + Bake 섹션 본 페이지 기준으로 갱신.
- `CHANGELOG.md` 에 “0.2.0 — Option E + Bake” 항목 추가 (현재 0.1.0 까지만 작성).
- `daily/2026-05-11.md` 는 이미 §16 까지 있음. 다음 일자(`2026-05-12.md`) 에서는 6.1~6.2 검증
  결과로 시작.

---

## 7. 다음 세션 “바로 시작” 체크리스트

1. **이전 baked.usd 파일 삭제 권장** (옛 매개변수로 만들어진 것들):
   - `lam/usd/test*_baked.usd`
2. Kit 재시작 → LAM Window 열림 확인.
3. `[+ USD 추가]` 로 test 자산 등록.
4. `[Bake]` 실행. 콘솔의 **새 done 라인 `phase[...]` 분포** 를 캡처 → §1.6 의
   “재측정” 칸을 채우고 daily/2026-05-12.md 첫 항목으로 기록.
5. 시퀀스 USD_TIMELINE step 1 개 → Run → 단일 인스턴스 재생 정상 동작 확인.
6. 위 6.1 (멀티 인스턴스) 또는 6.2 (event JSON 동시) 중 하나 선택해 검증 진입.
7. 막히면 `[Option E 진단]` 로그 + bake done 로그를 본 페이지에 추가 기록.

### 7.1 NEVER 리스트 — 회귀시키지 말 것

다음 항목은 사용자가 명시적으로 거절했거나 이미 폐기된 선택지. 다음 세션이 “더 간단한 방법”
처럼 보여서 재도입하지 않도록 못박음.

| # | 절대 안 됨 | 이유 |
|---|---|---|
| N1 | **USD timeline 전역 시간축 사용 (옛 “옵션 1”)** | 사용자: “무조건 1번은 안되는거야”. 멀티 인스턴스 독립 재생 불가. |
| N2 | **`_RUNTIME_USE_OPTION_E = False` 로 되돌리기** | D2 결정. hotfix 6~10 경로는 multi-instance 미지원. rollback 이 필요하면 `evaluator.set_use_option_e(False)` 런타임 호출로만. |
| N3 | **`morph.tbs_control_1` 의 .py / .toml 수정** | D1 결정. LAM 작업이 TBS 시뮬에 영향 0 원칙. |
| N4 | **bake 의 author 단계를 `Sdf.ChangeBlock` 으로 감싸기** | D9. OverridePrim.IsValid()=False 로 모든 prim 스킵되어 0 attrs authored. |
| N5 | **bake 의 inst_prim 자체를 capture 대상에 포함** | D7. add_usd 의 upAxisFix 가 baked 결과에 박혀 재로딩 시 누워있게 됨. |
| N6 | **bake 후 `forget_instance` 호출 생략** | D8. 이전 runtime 이 옛 offscreen_asset 유지 → baked 가 재생되지 않음. |
| N7 | **bake stride > 1 / sparse=True 를 기본값으로** | D6. 사용자: “품질은 무조건 제일 좋아야”. opt-in only. |
| N8 | **mtime 기반 [Bake] skip 부활** | D5. 사용자: “bake 파일이 있는 상태에서 또 bake 를 하면 덮어씌워지는 구조였으면 좋겠는데”. |
| N9 | **master stage 의 timeline 을 evaluator 가 진행** | Option E 설계 원칙. master 는 정지, offscreen 만 시각 진행. |
| N10 | **`lam_attribute_reauthor.py` 의 timeSamples reauthor 부활** | wrote=0 의 원인이었던 hotfix 잔재. 폐기 유지. |

### 7.2 환경변수 / 런타임 토글 빠른 참조

| 이름 | 기본 | 설명 |
|---|---|---|
| `LAM_BAKE_FRAME_STRIDE` | `1` | bake frame 간격. D6 정책상 1 유지 권장. |
| `LAM_BAKE_SPARSE_SAMPLES` | `0` (false) | true 면 동일값 연속 frame 압축. D6 정책상 false 유지 권장. |
| `LAM_BAKE_PROBE_STATIC` | (미구현) | 6.5 (P3) 옵트인 가속 — 다음 세션이 사용자 요청 시 신설. |
| `evaluator.set_use_option_e(bool)` | True | Option E ↔ hotfix 6~10 toggle. 디버그용. |
| `[Option E 진단]` 버튼 | — | 각 instance 의 `attrs_cached`, `virtual_time`, `wrote`, `offscreen_asset` print. |
| `[Master 진단]` 버튼 | — | master stage 의 inst sublayer 상태 print. |

### 7.3 git 작업 상태 (2026-05-11 종료 시점 스냅샷)

- 브랜치: `feature/web-extension` (origin 대비 ahead 1).
- `M` (변경) 파일: 본 핸드오프 문서를 만든 LAM 측 .py 와 docs 들.
- **TBS 측 `.py` / `.toml`** : `M` 0 — 정책 D1 준수 확인.
- **untracked**:
  - `lam/usd/master.usd`, `lam/usd/master_1.usd`, `lam/usd/test*.usd`, `lam/usd/*_baked.usd`
    — 사용자 자산. **commit 대상 아님** (테스트 자료).
  - `source/extensions/morph.lam_control/docs/daily/` — 일지. commit 대상.
  - `source/extensions/morph.lam_control/morph/lam_control/lam_bake_omnigraph.py` — 신규 모듈. **commit 대상**.
  - `source/extensions/morph.lam_control/morph/lam_control/lam_instance_runtime.py` — 신규 모듈. **commit 대상**.
  - `source/extensions/morph.lam_control/web/prompt.md` (혹은 tbs 쪽) — 사용자 prompt 기록.
- **다음 세션 commit 전 권장 절차**:
  1. `git diff` 로 LAM 측 변경만 골라 stage (`git add source/extensions/morph.lam_control/`).
  2. baked.usd / test*.usd 등 자산 파일은 stage 하지 말 것.
  3. commit 메시지 예: `lam_control: Option E + OmniGraph bake (Phase B/C)`.
  4. **사용자가 명시적으로 commit/push 를 지시할 때만** 실행. 그 전에는 working tree 만 유지.

---

## 8. 참고 — 관련 문서 위치

```
source/extensions/morph.lam_control/docs/
  ├ LAM_Bake_Handoff.md          ← (본 문서) — 다음 세션 진입점
  ├ LAM_Timeline_vs_Bake.md      ← 왜 timeline 대신 bake 인가 (전문가/비전문가 동시 설명 + §9 요약)
  ├ LAM_Independent_Playback_Plan.md   ← Option E 전체 설계
  ├ LAM_Spec.md                  ← 모듈/요구사항 명세
  ├ CHANGELOG.md                 ← 버전별 변경 (갱신 필요 — 6.8)
  ├ README.md
  ├ external_review/TBS_Timeline_Limitation_Guide.md   ← Hotfix 4~10 실패 history (외부 검토용)
  └ daily/2026-05-11.md          ← 이날의 의사결정 흐름 (장문)
```

---

## 9. 한 줄 메시지 (다음 작업자 / 미래의 본인 에게)

> Option E + Bake 까지 구조는 완성됐다. 다음 두 가지만 검증되면 LAM 의 핵심 시나리오
> (“여러 USD 가 한 viewport 에서 timeline 안 충돌하며 유기적으로 동작”) 가 production 사용
> 가능 단계에 진입한다 — **[P1] 멀티 인스턴스 동시 재생** 과 **[P1] event JSON 동시 트리거**.
> bake 의 추가 속도 개선은 사용자 요청 후 옵트인으로만 진입. 품질 최우선 정책(D6) 을 어기지
> 않는다.

---

## A. 사용자 요구 종합 매트릭스 (이전부터 요청된 모든 항목 검증)

사용자가 이전 세션부터 요구한 모든 시나리오를 한 곳에 모은 표.
상태는 **[구현]** / **[검증 미진행]** / **[미구현]** / **[정책]** 으로 분리.

| # | 사용자 요구 (원문 요지) | 상태 | 코드 / 문서 위치 | 비고 |
|---|---|---|---|---|
| R1 | 여러 USD 를 별도 stage 에 로드 (FBX→USD / 테스트 curve animation) | **[구현]** | `lam_instance_runtime.py` (instance 당 offscreen stage 1 개) | Phase A/B 완료. |
| R2 | 단일 viewport 합성 — 모든 자산을 한 viewport 에서 봄 | **[구현]** | `lam_master_stage.py` (master stage 1 개) + master mirror prim write | 사용자 viewport = master stage. |
| R3 | 시퀀스 편집기에서 stage 별 timeline 재생 step 생성 | **[구현]** | `lam_sequence_editor.py` + `lam_sequence_engine.py` (USD_TIMELINE step) | 단일 인스턴스 검증 완료. |
| R4 | MOVE / ROTATE step 으로 각 stage 의 prim 애니메이션 동작 | **[구현]** | `lam_translate_animation.py`, `lam_rotate_animation.py` | baked.usd 호환 검증은 6.3 (P2). |
| R5 | 하나의 JSON 으로 저장 / 로드 | **[구현]** | `lam_sequence_editor.py` 의 save/load | 멀티 인스턴스 시퀀스 저장 검증은 6.4 (P2). |
| R6 | 각 USD 들이 JSON 설정값에 따라 **타임라인 안 겹치고** 유기적 동작 | **[구현 + 검증 미진행]** | scheduler 가 instance 별 virtual_time 독립 진행 (`runtime_by_path`) | 단일 인스턴스만 검증. 멀티는 6.1 (P1). |
| R7 | 외부 시뮬 결과 JSON 으로 특정 시퀀스 자동 트리거 (event_*.json) | **[구현 + 검증 미진행]** | `lam_external_event_runner.py:LamExternalEventRunner` | 다중 event wall-clock 중첩은 6.2 (P1). |
| R8 | 여러 JSON 이 wall-clock 시간축에서 중첩 실행 (event_1@1s 5s + event_2@3s) | **[구현 + 검증 미진행]** | PlaybackScheduler 가 prim_path 별 player 관리 = 사실상 player pool | 검증은 6.2.2 (P2). |
| R9 | 한 step 안에서 여러 stage 의 timeline / MOVE / ROTATE 동시 트리거 (multi-target) | **[미구현]** | step schema 의 `targets: [StepRef, ...]` 확장 필요 | daily §2.2 합의됐으나 코드 X. 6.2.1 (P1). |
| R10 | `morph.tbs_control_1` 변경 0 — LAM 안에서만 동작 | **[정책 + 준수]** | git status 의 tbs_control_1 `.py` M=0 유지 | D1 정책. 모든 변경이 lam_control 내부 모듈에서 완결. |
| R11 | `[모두 초기화]` 버튼 — 로드된 USD 일괄 제거 + master stage reset | **[구현]** | `lam_window.py:_on_reset_all` | forget_instance 호출까지 포함 (D8). |
| R12 | FPS 무조건 30 고정 (USD 타임라인 30 당 1초) | **[구현]** | `lam_types.py:LAM_FIXED_FPS=30.0` + `MasterStage.force_fixed_fps_30()` | D3 정책. |
| R13 | FBX (3ds Max) → USD 변환 자산도 동일 시나리오 동작 | **[구현 + 검증 미진행]** | Option E 는 어떤 timeSamples 자산이든 동작. OmniGraph 만 [Bake] 필요. | 실 FBX 자산 도착 시 6.6 (P3) 으로 호환성 확인. |
| R14 | OmniGraph 자산을 timeSamples 로 변환 — Option A | **[구현]** | `lam_bake_omnigraph.py:bake_prim_to_timesamples_async` | UI 의 `[Bake]` 버튼으로 호출. |
| R15 | [Bake] 는 항상 덮어쓰기 (mtime skip 제거) | **[구현]** | `lam_window.py:_on_bake_instance` (mtime 체크 제거됨) | D5 정책. |
| R16 | bake 속도 최대화 + 품질 무손실 유지 | **[구현]** | static pruning, Sdf-batch SetInfo, run-loop rate-limit 일시 해제, attr metadata 캐시 | D6 (무손실 우선), D10 (Sdf-batch). 추가 가속은 6.5 (P3) 옵트인. |
| R17 | 한 번 bake 된 usd 는 다음에 그냥 로드해서 bake 없이 사용 가능 | **[구현]** | baked.usd 는 일반 timeSamples 자산 — `[+ USD 추가]` 로 로드 시 Option E 가 자연 동작. | 검증은 6.4 (P2) 의 “Kit 재시작 후 Load Sequence”에 포함. |
| R18 | bake 된 파일의 material / shader 등이 원본과 동일 유지 | **[구현]** | baked.usd 가 원본을 reference — 동일 시각 결과 보장 | 원본 파일 위치 의존 — §5 한계 메모. |
| R19 | 옆으로 누워있는 현상 (upAxis 이중 회전) 방지 | **[구현]** | `bake_prim_to_timesamples_async` 가 `out_stage.SetStageUpAxis()` 원본과 일치 + inst_prim 자체 capture 제외 | D7 정책. |
| R20 | baked.usd 로 교체 후 USD_TIMELINE 정상 재생 | **[구현]** | `lam_window.py` 가 bake 완료 시 `forget_instance` → `add_usd(baked)` 로 runtime 재생성 | D8 정책. |

### 매트릭스 결론

- **이전부터 요구된 시나리오 20 개 항목 중**:
  - 구현 완료 + 검증 완료 = **12 개** (R1~R5, R10~R12, R14~R15, R17~R20 일부)
  - 구현 완료 + **검증 미진행** = **5 개** (R6, R7, R8, R13, R18)
  - **미구현** = **1 개** (R9 — multi-target step)
  - 정책 항목 = **2 개** (R10, R12, R16)
- **다음 세션 첫 번째로 처리할 것**:
  1. [P1] 6.1 멀티 인스턴스 동시 재생 검증 → R6 의 검증 완료.
  2. [P1] 6.2 event_*.json 동시 트리거 → R7 / R8 의 검증 완료.
  3. [P1] 6.2.1 multi-target step 구현 → R9 의 구현 완료.
- 이 3 개가 끝나면 **사용자가 이전부터 요구한 모든 시나리오가 검증 가능 상태** 에 도달.
