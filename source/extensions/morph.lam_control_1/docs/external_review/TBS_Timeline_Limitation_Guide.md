# TBS / LAM 단일 Stage 타임라인 한계 — 기술 요약 (외부 전문가 검토용)

작성일: 2026-05-11
대상 독자: USD / Hydra / Omniverse Kit 에 익숙하지 않을 수 있는 외부 기술자
목적: "Unity 처럼 USD 마다 타임라인 따로 돌리면 되지 않냐" 라는 일반적 직관이 **왜 USD/Omniverse 에서는 architectural 으로 적용 불가능한지** 를 짧고 명확하게 설명하고, 어떤 방식으로 수정해야 하는지 가이드라인을 제시.

---

## 1. 한 줄 결론

> **"USD / Hydra 의 Stage 는 Unity 의 Scene 과 다릅니다. Stage 의 시간 (current time) 은 stage 단위로 1 개만 존재하고, 모든 reference / instancing / Hydra cache / prototype 이 그 단일 시간 위에서 평가됩니다.** 그래서 한 stage 안에 여러 USD 를 reference 한 뒤 각 USD 의 타임라인을 독립적으로 재생하는 것은 단순한 코드 수정이 아니라 **USD value resolution 모델 자체와 충돌** 합니다."

---

## 2. 게임 엔진 (Unity / Unreal) 모델 vs USD/Hydra 모델

| 항목 | Unity / Unreal (게임 엔진) | USD / Hydra (Omniverse) |
|---|---|---|
| Scene 단위 시간 | 없음. **각 GameObject / Animator 가 자기 시간을 가짐** | **Stage 단위로 시간 1 개** (`Stage::SetCurrentTimeCode`) |
| Animation 평가 | Animator / Timeline / Playable 컴포넌트가 **CPU 단위로 자기 시간에서 evaluate**. 각각 독립. | USD `attribute.Get(time)` 가 **stage 의 current time 을 받아 value resolution**. stage 가 진행하면 모든 reference 의 timeSamples 가 함께 평가됨. |
| Reference 모델 | Prefab instance 마다 자체 Animator 컴포넌트 → 독립 시간 | USD reference 는 **layer composition** 의 한 형태. 평가는 stage 시간을 **reference 의 LayerOffset 으로 변환**해서 수행 |
| Render | 매 frame 각 컴포넌트가 자기 결과를 mesh 로 출력 → renderer 가 합성 | Hydra 가 **단일 stage 의 SceneIndex** 를 traverse → 모든 prim 이 **stage 의 current time 으로 동시에** evaluate |
| Hydra cache (= Renderer cache) | per-object | **per-stage**. 같은 asset 의 여러 reference 는 prototype 으로 dedup |

→ 게임 엔진은 "**객체별 시간**" 모델. USD/Hydra 는 "**stage 단위 시간**" 모델. 이는 단순히 API 의 차이가 아니라 **value resolution 의 차이** 임. USD 의 `attribute.Get(time)` 은 stage time 1 개를 받지, prim 별 시간을 받지 않음.

---

## 3. 결과적으로 무엇이 안 되는가

한 stage 안에 자산 A, 자산 B 를 reference 로 추가했을 때:

```
master.usd
├── reference → A.usd  (자체 timeSamples 가짐, 0~100 frame)
└── reference → B.usd  (자체 timeSamples 가짐, 0~200 frame)
```

`stage.SetCurrentTimeCode(50)` 를 호출하면:

- A 의 attribute 가 frame 50 에서 평가됨
- B 의 attribute 도 **동시에** frame 50 에서 평가됨

→ "A 만 frame 50, B 는 frame 0 에서 멈추고 싶다" 는 **단일 stage 위에서는 USD value resolution 모델 자체와 충돌**.

이 한계를 우회하려면 **reference 마다 `Sdf.LayerOffset(offset, scale)` 로 stage time 을 reference 시간으로 다르게 매핑** 해야 함:

- A 의 reference: `LayerOffset(offset=0, scale=1)`
- B 의 reference: `LayerOffset(offset=large_negative, scale=0)` ← B 를 freeze

이 우회로가 **"Hotfix 6 ~ 10"** 으로 우리가 6 시간 넘게 시도한 것입니다. 그런데 다음 절에서 설명하는 이유로 **모두 실패**했습니다.

---

## 4. 우리가 6 시간 넘게 시도한 우회 시도들 (LAM/L5 Hotfix history) 와 실패 이유

### Hotfix 4 — `omni.timeline.set_current_time()` 으로 stage 시간 진행

| 시도 | `attribute reauthor` (자산의 timeSamples 를 stage time 에 맞춰 매 frame default 값으로 다시 author) 로 안 되니 stage time 자체를 진행시킴 |
|---|---|
| **실패** | `omni.timeline` 은 stage 단위. 한 instance 만 시간 진행시키려 해도 모든 instance 가 **동시에** 따라 움직임. 마지막 set_current_time 의 winner 만 살아남는 race condition |

### Hotfix 5 — 정지 instance 의 reference 에 `LayerOffset(freeze_tc, 0)` 으로 freeze

| 시도 | LayerOffset 의 scale=0 으로 reference 의 시간을 동결 |
|---|---|
| **실패** | LayerOffset 변경이 **root layer** 에 author 됐는데, 자산 reference 의 strongest arc 가 다른 곳일 수 있어 평가에 반영 안 됨. 또한 `scale=0` 자체가 USD 에서 invalid (다음 Hotfix 10 에서 발견) |

### Hotfix 6 — Per-instance Layer Offset Mapping (wall clock → 각 instance 시간 변환)

| 시도 | wall clock `master_seconds` 도입. 각 instance 의 reference 에 `LayerOffset(offset, scale=1)` (재생 중) / `(freeze_tc, 0)` (정지) 를 dynamic 하게 author |
|---|---|
| **실패** | `prim.GetReferences().SetItems()` 가 **listOp merge semantics** 으로 동작. 기존 reference 가 살아남아 평가에 끼어듦 |

### Hotfix 6.2 — `Sdf.PrimSpec.referenceList.explicitItems` 직접 manipulation

| 시도 | listOp 의 prepended/appended/explicit 중 explicitItems 슬롯에 직접 대입 → 다른 reference 항목을 모두 무시하고 explicit 만 평가되도록 |
|---|---|
| **실패** | 여전히 root layer 에 author. root layer 가 stage 의 strongest 가 아닐 수 있음. 또한 자산의 timeSamples 가 reference 안쪽에 있어 그것이 master 의 default 보다 우선 평가 |

### Hotfix 6.3 — Stage FPS 강제 30fps 고정

| 시도 | 60fps 로 stage 가 평가되어 timeCode 계산이 어긋나는 문제 발견 → `stage.SetTimeCodesPerSecond(30)` + `SetFramesPerSecond(30)` 로 강제 |
|---|---|
| **결과** | FPS 는 30 으로 잡혔지만 동시 재생 문제는 그대로 |

### Hotfix 7 — Per-instance **Anonymous Sublayer** Override (root 가 아니라 sublayer 에 author)

| 시도 | instance 마다 `Sdf.Layer.CreateAnonymous()` 로 익명 sublayer 생성 + root layer 의 `subLayerPaths.insert(0, ...)` 로 가장 강한 슬롯에 attach. 그 sublayer 안에 LayerOffset 을 author |
|---|---|
| **실패** | 사용자 진단에서 `post-attach stack[2][LAM]` 발견. **root layer 의 sublayer 는 root layer 자체보다 약함**. master_1.usd (`stack[1]`) 가 LAM sublayer (`stack[2]`) 보다 위에 있음 |

### Hotfix 8 — Sublayer 를 **Session Layer** 의 sublayer 로 attach (root 가 아니라 session)

| 시도 | USD 의 layer strength 룰 (`Session > Root > Sublayers`) 에 따라 session layer 의 sublayer 로 옮김 |
|---|---|
| **실패** | composed metadata 에서 LAM sublayer 가 winner 로 인식됐지만 시각적으로는 여전히 동시 재생. 다음 4 가지 원인 의심:<br>1. `Sdf.SpecifierOver` 가 weaker reference 를 살림<br>2. `referenceList.SetItems()` 의 fallback chain 이 merge 처리됨<br>3. weaker reference 가 stack 에 살아남음<br>4. **Hydra prototype sharing** — 같은 assetPath 의 reference 가 GPU 에서 같은 prototype 으로 dedup 되어 timeline evaluation 공유 |

### Hotfix 9 — `SpecifierDef` + 순수 explicitItems + `SetInstanceable(False)`

| 시도 | 1. `Sdf.SpecifierDef` 로 강한 prim spec 생성<br>2. fallback chain 제거, `referenceList.explicitItems = [...]` 만 사용<br>3. `prim.SetInstanceable(False)` 로 prototype sharing 차단 |
|---|---|
| **실패** | composed metadata 의 winner 가 LAM sublayer + LayerOffset 적용된 것으로 출력됐지만 **여전히 시각적으로 동시 재생**. `TOP WINNER LAYER` 진단에서 `is_lam=False` 경고 발견. LayerOffset(scale=0) 자체가 invalid LayerOffset 이라 USD 가 identity 로 fallback 한 것 의심 |

### Hotfix 10 — `LayerOffset(scale)` 가 0 일 때 `1e-9` 로 clamp + WINNER 진단 정확화

| 시도 | `scale=0` 이 USD 에서 invalid LayerOffset 이라 fallback 발생 → `1e-9` 같은 매우 작은 양수로 clamp |
|---|---|
| **결과** | composed metadata 가 **완벽하게 LAM sublayer + valid LayerOffset 으로 winner** 표시됨. **그런데도 시각적으로 동시 재생.** |

→ **여기서 우회로의 한계 명확화**: USD composition 단계는 LAM sublayer 가 winner 로 잡혔지만, **Hydra 의 SceneIndex / RenderDelegate 단계에서 stage current time 이 모든 prim 에 동시 적용됨**. 즉 우리가 잡은 winner 는 "어떤 reference 가 보이는가" 만 결정했지, "그 reference 가 자기 시간으로 평가되는가" 를 결정하지 못함. **이는 LayerOffset 이 stage time → reference time 의 변환을 정의하지만, stage time 자체가 1 개라는 점은 변하지 않기 때문**.

---

## 5. 왜 우회로가 끝없이 실패하는가 — 근본 원인

| 단계 | USD/Hydra 가 동작하는 방식 | 우리가 우회로로 잡으려 한 곳 | 우회 가능성 |
|---|---|---|---|
| 1. Stage 가 current time 1 개를 가짐 | `Stage::SetCurrentTimeCode(t)` | (이걸 instance 별로 다르게 하고 싶음) | ❌ Stage 는 1 개의 시간만 가능. 단일 stage 에서는 architectural 으로 불가능 |
| 2. Composition (layer 합성, reference 평가) | `Pcp` 가 layer ordering / LayerOffset / variant / payload 합성 | LAM sublayer + explicitItems + LayerOffset | ✓ 여기까지는 우회 가능 (Hotfix 7-10 으로 winner 잡음) |
| 3. Value resolution | `attribute.Get(t)` 가 composed reference 의 timeSamples 를 stage time 에 LayerOffset 적용해서 lookup | LayerOffset(scale=1, offset=−master_tc + inst_tc) 로 instance 시간 매핑 | △ 이론적으로 가능하나 LayerOffset 의 (scale, offset) 이 매 frame 변하면 USD value cache 무효화 + Hydra cache 폭발 |
| 4. Hydra SceneIndex / RenderDelegate | stage 의 모든 prim 을 **stage current time 으로 동시 evaluate** → GPU 로 push | (여기는 건드릴 수 없음) | ❌ **여기가 fundamentally stage-global**. 우회 불가 |
| 5. Prototype sharing / instancing | 같은 assetPath + primPath 는 Hydra 가 prototype 으로 dedup. timeline evaluation 공유 | `SetInstanceable(False)` | △ instanceable 만 끄면 부분 우회. 그러나 4. 의 stage-global evaluation 은 여전히 적용 |

→ **단계 4 (Hydra render-time) 가 stage 단위로 평가** 한다는 것이 **architectural fact**. USD spec 도 이렇게 정의되어 있고, Omniverse Kit 의 RTX/Hydra 도 이를 따름. 우회 불가.

→ 우리는 **단계 2~3 만 잡았고 단계 4 를 잡지 못해서** 6 시간 동안 모든 핫픽스가 실패한 것임.

---

## 6. 외부 기술자가 흔히 제안하는 "쉬운 해법" 들이 왜 안 통하는가

### 제안 (a): "Unity 처럼 GameObject 마다 Animator 컴포넌트 쓰면 되지 않냐"

→ USD 에는 Unity 의 Animator 같은 **per-object playback component** 가 없음. 모든 animation 은 prim attribute 의 `timeSamples` 형태로 저장되고, **stage current time 으로만 평가**. USD 의 디자인 자체가 "scene description = data, time = global axis" 이기 때문.

### 제안 (b): "각 USD 의 reference 에 LayerOffset 만 다르게 주면 되지 않냐"

→ 우리가 Hotfix 6, 6.2, 6.3, 7, 8, 9, 10 으로 정확히 그것을 시도. composition winner 까지 잡았는데도 **stage current time 1 개라는 fact 는 변하지 않아서** Hydra render-time 에서 모두 동시 평가됨. (위 §5 의 단계 4)

### 제안 (c): "각 USD 마다 omni.timeline 을 따로 만들면 되지 않냐"

→ `omni.timeline.get_timeline_interface()` 는 **stage 의 timecode 를 wrap 한 인터페이스**. 새 timeline 을 만들어도 결국 같은 stage 의 current time 을 set/get 함. 즉 timeline 인스턴스가 여러 개 있어도 **target stage 가 같으면 모두 같은 시간을 공유**. (Kit API 한계)

### 제안 (d): "Hydra Scene Index 단계에서 prim 별로 시간 다르게 주면 되지 않냐"

→ 이론적으로 가능. **Hydra Scene Index 의 time-shift filter 를 prim 별로 적용하는 custom filter 노드 작성** 이 필요. 단 이는 **C++ Hydra extension 영역** 이고 Kit Python API 로는 노출 안 되어 있음. 또한 GPU prototype sharing 으로 인해 같은 자산의 다른 instance 가 같은 deformed mesh 를 공유하려 해서 깨짐.

### 제안 (e): "Unity 처럼 viewport 여러 개 쓰면 되지 않냐"

→ 가능. Kit 의 viewport 위젯은 viewport 마다 USD context 를 따로 바인딩 가능. 그러나 우리 사용자 요구는 **"두 장비가 한 viewport 안에서 유기적으로 같이 보여야 함"**. Multi-viewport 는 사용자 요구 위반.

→ **유일하게 "쉽게 말하던 해법" 중 architectural 으로 동작하는 것은 (e) multi-viewport. 그러나 사용자 요구상 채택 불가**.

---

## 7. 그래서 어떤 방향으로 수정하는가 — 가이드라인

### 방향 A — 본래 의미의 "stage 분리" (Per-Instance Stage + Render Composition)

```
Instance A → Stage A → Hydra A → RenderProduct A → Texture A ─┐
Instance B → Stage B → Hydra B → RenderProduct B → Texture B ─┼─ GPU Composite → Shared Viewport
Instance C → Stage C → Hydra C → RenderProduct C → Texture C ─┘
```

- Stage 마다 **자체 current time** 보유 → 진짜 independent
- 합성은 **GPU compositor pass** 로 color + depth 텍스처 depth-aware composite
- USD architectural 정통

**문제점**:
- Kit 109 Python API 만으로는 RenderProduct 외부 출력 + depth aov 추출 + GPU compositor 가 **표준화 안 됨** (비공식 / OmniGraph / 일부 C++ ext 영역)
- 검증 사이클이 많음 (2 ~ 4 주)

### 방향 B — Single-Stage 유지 + 자산은 별도 offscreen Stage 로 평가 (권장)

```
[Master Stage]            ← 1 개. 사용자 viewport 가 보는 stage. 시간 평가 안 함.
   /World/aaa            ← mirror prim (mesh + material 만)
   /World/aaa_1          ← mirror prim

[Offscreen Stages]       ← instance 마다 1 개 (in-memory)
   stage_for_aaa         ← test1.usd 자체. 여기서만 timeline 진행.
   stage_for_aaa_1       ← test2.usd 자체.

매 frame:
  for inst in instances:
    inst.offscreen_stage.SetCurrentTimeCode(inst.virtual_time * tps)
    values = evaluate(inst.offscreen_stage, inst.prim_path)
    write_default_to_master_mirror(master_stage, inst.prim_path, values)
```

- master stage 는 **시간 평가 안 함** → §5 단계 4 의 stage-global evaluation 회피 (frame 0 으로 고정)
- 자산은 별도 stage 에서 자기 시간으로 평가 → 진짜 independent
- 평가 결과 (xform / SkelAnim joint transforms / blendshape weights / mesh points) 를 master mirror prim 의 동일 attribute 에 **default value** 로 write
- master stage 1 개 → Hydra 가 자연스럽게 depth-aware 합성
- Kit Python API 만으로 **모두 정통 USD 표준** (`Usd.Stage.CreateInMemory`, `Usd.AttributeQuery`, `UsdSkel.Cache`)

**비용**:
- instance 마다 자산 stage 1 개 in-memory (예: 100 MB × 2 instance = 200 MB 추가)
- Skel skinning 비용: 100K vertex × 60fps × N instance (N=2~5 수준에서는 부담 없음)
- 단점: instance 수가 매우 많아지면 (>10) 평가 비용 누적

→ **6 시간 핫픽스 history 의 교훈**: 우회로는 USD architectural 한계를 못 넘는다. **자산 평가를 master stage 에서 분리** 하는 architectural 변경이 정답.

→ **방향 B 가 사용자 요구 (1 viewport, depth-aware, Skel 지원) 를 모두 충족하면서 Kit Python API 만으로 표준 구현 가능한 유일한 길**.

### 방향 C — Multi-Viewport (사용자 요구 위반이지만 가장 빠름)

- Kit 의 viewport 위젯을 여러 개 띄우고 각 viewport 가 다른 stage 바인딩
- 사용자 요구 ("두 장비가 한 viewport 안에서 유기적") 위반
- 5 분만에 동작은 가능

→ 사용자 요구상 채택 안 함.

---

## 8. 외부 전문가 설득 포인트 (요약)

| Q (외부 전문가의 의견) | A (반박) |
|---|---|
| "Unity 처럼 USD 마다 타임라인 따로 돌리면 되지 않냐" | USD 에는 per-object playback component 가 없음. 모든 animation 은 stage current time 으로만 평가됨. (§2, §6-(a)) |
| "LayerOffset 만 잘 주면 되지 않냐" | 6 시간 동안 LayerOffset 의 7 가지 변종을 모두 시도했고 composition winner 까지 잡았는데도 동시 재생 발생. **LayerOffset 은 stage time → reference time 의 변환만 정의하고, stage time 자체가 1 개라는 사실은 변하지 않음.** (§4 Hotfix 6-10, §5 단계 4) |
| "각 USD 마다 omni.timeline 만들면 되지 않냐" | omni.timeline 은 **stage 의 timecode 를 wrap 한 인터페이스**. timeline 인스턴스가 여러 개여도 target stage 가 같으면 모두 같은 시간 공유. (§6-(c)) |
| "Hydra Scene Index 에서 시간 따로 주면 되지 않냐" | 이론적으로 가능. **C++ Hydra extension** 작성 + GPU prototype sharing 차단 필요. **Kit 109 Python 만으로는 비표준 영역**. (§6-(d)) |
| "Multi-viewport 쓰면 되지 않냐" | 가능하나 **사용자 요구 (한 viewport 에서 두 장비가 유기적 동작 확인)** 위반. (§6-(e)) |
| "그러면 어떻게 해결?" | **방향 B (Single-stage + offscreen evaluation)**. 자산을 master stage 에 reference 로 두지 않고, 별도 offscreen stage 에서 자기 시간으로 평가 → master mirror prim 에 매 frame default 값 write. **§5 단계 4 의 stage-global evaluation 을 architectural 으로 회피** 하면서도 1 viewport 를 유지. Kit Python API 만으로 정통 USD 표준 구현 가능. (§7-방향 B) |

---

## 9. 한 페이지 요약 (인쇄용)

```
=== USD/Hydra 단일 stage 한계 ===

1. Stage::current_time = 1 개 (architectural fact)
2. Hydra render-time = 모든 prim 을 stage current_time 으로 동시 evaluate
3. LayerOffset 은 (stage time → reference time) 변환만 정의. stage time 1 개 fact 는 변경 불가

=== 6 시간 우회 시도 history (모두 실패) ===

H4  omni.timeline.set_current_time()      → race condition
H5  LayerOffset(freeze, 0)                 → root layer arc 가 strongest 아님
H6  per-instance Layer Offset Mapping     → listOp merge semantics 로 weaker reference 살아남음
H6.2 Sdf.PrimSpec.referenceList.explicitItems  → root layer arc 한계 동일
H6.3 stage FPS 30 강제                     → FPS 만 잡힘. 동시재생 그대로
H7  per-instance anonymous sublayer (root) → root sublayer < root layer (strength 룰)
H8  session layer 의 sublayer attach        → composition winner 잡혔지만 시각적 동시재생
H9  SpecifierDef + explicitItems + SetInstanceable(False)  → 동일
H10 LayerOffset scale clamp 1e-9 + WINNER 정확화           → 동일

=== 결론 ===

우회로의 한계는 §2 의 stage-global render evaluation 단계.
Composition layer 까지는 잡아도 Hydra render-time 단계에서
stage current_time 1 개로 모두 동시 evaluate 됨.
→ Architectural 변경 필요.

=== 권장 해결 (방향 B) ===

Master Stage (1 개, 시간 평가 안 함)
    ↑ 매 frame default 값 write
Offscreen Stage A (test1.usd, 자기 virtual_time 진행)
Offscreen Stage B (test2.usd, 자기 virtual_time 진행)

→ 자산은 master 에 reference 로 추가 안 함 (stage-global eval 회피)
→ instance 마다 자기 시간으로 자기 stage 평가
→ master mirror prim 의 attribute 에 default 값 write
→ Hydra 는 master 1 개 stage 만 봄 (depth-aware 자동, 1 viewport)
→ Kit Python API 만으로 정통 USD 표준 구현
```

---

## 10. 부록 — 본 검토와 함께 제공되는 원문 문서

- `LAM_Independent_Playback_Plan.md` — 방향 B 의 단계별 구현 plan (Phase A ~ E, 회귀 테스트 R1 ~ R10, 보호 영역 명시)
- `USD_Timeline_Spec.md` — LAM 사양 v0.20 (REQ-001 ~ REQ-011, 변경 이력)
- `daily/2026-05-10.md` — Hotfix 1 ~ 10 의 일별 상세 디버깅 로그
- `LAM_Control_Source_Dump.md` — `morph.lam_control` 확장의 모든 .py 코드 dump
- `TBS_Control_1_Source_Dump.md` — `morph.tbs_control_1` 확장의 모든 .py 코드 dump

끝.
