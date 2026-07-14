# 왜 Timeline 방식은 안 되고 Bake 방식을 쓰는가

작성일: 2026-05-11
대상 독자: 전문가 / 비전문가 모두
한 문서 안에 **(1) Timeline 방식이 실패한 객관적·구조적 이유**, **(2) Bake 방식의 동작 원리**,
**(3) 그래서 어떻게 작업이 흘러가는가** 를 정리. 마지막 §9 는 "이것만 보면 됨" 요약.

---

## 0. 30 초 요약 (가장 바쁜 사람용)

> **USD 의 Stage 는 시계가 하나뿐**이라 한 stage 에 여러 USD 를 reference 로 얹어 각자 다른 시간으로
> 재생하려는 시도는 architectural 으로 불가능했다. 우회 시도 5+ 회 모두 실패.
>
> → **해결책 1단계 (Option E)**: 자산마다 별도 stage(=별도 시계) 를 메모리에 만들어 거기서만 시간
> 진행 → 결과 좌표/회전을 사용자 viewport 의 master stage 에 복사.
>
> → **그러나** 사용자의 자산은 **OmniGraph 라는 Kit 런타임 안에서만 평가되는 그래프** 로
> 애니메이션이 만들어져 있어서, 별도 stage 에 옮기면 OmniGraph 가 평가되지 않아 멈춰버림.
>
> → **해결책 2단계 (Bake)**: OmniGraph 를 Kit 안에서 한 번 굴려 매 frame 의 결과 좌표를
> **USD 표준 timeSamples 데이터** 로 박은 새 `*_baked.usd` 를 만든다. 이후로는 OmniGraph 가
> 없어도 standalone 으로 평가 가능 → Option E 의 offscreen stage 에서 멀쩡히 동작.

---

## 1. 한 줄 결론 (전문가용)

> **Timeline 방식은 두 단계에서 막혔다**: (1) USD `Stage::SetCurrentTimeCode(t)` 가
> **stage 단위 단일 시간축** 이라 멀티 인스턴스 독립 재생 불가, (2) 우회로 도입한 offscreen stage
> 도 **OmniGraph 가 Kit 런타임 컨텍스트에 강결합** 되어 있어 isolated `Usd.Stage` 에서는
> 평가되지 않음. **Bake** 는 OmniGraph 런타임을 standalone USD `timeSamples` 로 변환해
> 자산을 self-contained 하게 만들어 위 두 한계를 동시에 우회한다.

---

## 2. 비전문가용 비유 (왜 안 됐는지 한 번에 이해하기)

### 2.1 USD timeline = 극장 전체의 단 하나뿐인 시계

- USD 의 "stage" 는 **극장 한 곳** 이라고 생각.
- 그 극장의 **시계는 단 1 개**. 시계가 12시면 무대 위의 모든 배우(=USD reference)가 동시에
  12시 장면을 연기.
- 우리가 원하던 건: "장비 A 는 오전 9시부터 5분간, 장비 B 는 오전 9시 3분부터 3분간 같이
  움직여 줘" — 즉 **배우마다 자기 시계가 있어야 함**.
- 그런데 USD 의 룰: **시계는 극장당 1 개**. 배우별로 시계가 못 달림.

### 2.2 시도했던 우회 — "내가 시계 안 보는 척하면 되지 않냐"

- 배우 A 에게 "너는 -100시간 만큼 빠른 시계를 봐" 라고 적은 종이를 줌 (= LayerOffset).
- 배우 A 가 그 종이를 본 채로 연기하면 자기 장면을 다르게 잡을 수 있을 거라 기대.
- **결과**: 종이는 잘 줬는데 무대 조명(= Hydra 렌더러)이 결국 **극장 시계 1 개로** 모든
  배우를 비춤. 종이 본 효과가 무대 위에서 사라짐.
- 6 시간 동안 종이 색깔/크기/슬롯 위치 다 바꿔봤지만 결론 동일.

### 2.3 우리가 진짜 쓴 해법 (Option E) — "배우마다 분장실을 따로 줘"

- 무대(=master stage)는 유지하되, 배우마다 **분장실(=offscreen stage) 1 개씩** 따로 줌.
- 분장실에는 **각자 시계가 있음**. A 의 분장실 시계는 9시, B 의 분장실 시계는 9시 3분 등.
- 매 frame 시스템이: "A 분장실에서 9시 30분 포즈 잡고 → A 의 무대 위 더미에 그 포즈 복사" 를
  반복.
- 무대 시계는 정지 (안 보고 있음). 보이는 건 더미가 매 frame 새 포즈로 갱신되는 모습.
- **이걸 Option E 라고 부르고 이미 구현 완료** 됐다.

### 2.4 또 한 번 막힌 곳 — "OmniGraph 라는 특수 배우"

- 사용자의 자산에는 그냥 미리 정해진 안무가 적혀있는 게 아니라, **무대 컴퓨터(= Kit
  런타임) 가 매 순간 안무를 만들어줘야** 움직이는 특수 배우(=OmniGraph)가 들어있음.
- 이 배우를 분장실(=offscreen stage)에 넣으면 컴퓨터가 분장실까지 안 따라옴 → **움직이지 않음**.
- 단지 좌표가 0 인 채 가만히 있어서, 무대 위 더미가 갱신될 값이 없음 = 재생 안 됨.

### 2.5 그래서 Bake — "안무가 미리 종이에 적어두기"

- 무대 컴퓨터가 만들어주던 안무를, **사전에 무대에서 한 번 굴려서 매 순간 좌표를 종이에 다
  받아 적음** (= Bake).
- 종이에 적힌 좌표는 더 이상 컴퓨터가 필요 없음 — 그냥 시간만 알면 그 시간의 좌표를 읽을 수
  있음 (= 표준 USD timeSamples).
- 이 종이가 박힌 새 자산 `*_baked.usd` 를 만들어 분장실에 넣으면, 분장실에서도 시간에 따라
  좌표가 잘 나옴 → Option E 가 정상 동작.

### 2.6 요약 (비전문가용)

| 단계 | 설명 |
|---|---|
| 1. Timeline 우회 시도 | 극장 시계 1 개 한계라 6 시간 시도해도 못 넘음. |
| 2. Option E (분장실 분리) | 자산마다 별도 stage 만들어 시계 분리. 대부분 자산은 OK. |
| 3. OmniGraph 자산이 막힘 | 분장실에 컴퓨터가 안 따라와서 안무가 안 만들어짐. |
| 4. Bake | 안무를 미리 종이에 다 받아적어 분장실에서도 보이게 함. |

---

## 3. 전문가용 — 객관적·구조적 기술 설명

### 3.1 한계 ①: USD Stage 의 단일 시간축

USD 사양:

```cpp
class Usd.Stage {
    void SetCurrentTimeCode(double t);    // ← 시간은 stage 당 1 개
    Usd.Attribute.Get(time);              // ← attribute 평가에 시간을 인자로 받음
};
```

`Stage::SetCurrentTimeCode(t)` 는 stage 단위로만 존재. `attribute.Get(t)` 가 LayerOffset 을
적용해 reference 의 자체 시간으로 변환은 해주지만, **stage 자체의 t 가 1 개라는 사실은
변하지 않음**.

**Composition vs Value Resolution vs Render 의 분리**:

| 단계 | USD 구조 | 우회 가능성 |
|---|---|---|
| 1. Composition (Pcp) | layer ordering, LayerOffset, variant 합성 → composed prim | ✓ Hotfix 7-10 으로 winner 잡음 |
| 2. Value Resolution | `attribute.Get(t)` 가 composed reference 의 timeSamples 를 stage time 으로 lookup | △ 매 frame LayerOffset 변경 시 cache 무효화 폭발 |
| 3. Hydra SceneIndex / RenderDelegate | **stage 의 모든 prim 을 stage current time 으로 동시 evaluate** → GPU push | ❌ 여기는 stage-global 이 architectural fact |

→ 단계 3 (Hydra render-time) 이 stage-global 이라는 게 핵심. 우리가 단계 1-2 를 아무리 잡아도
   단계 3 이 모든 prim 을 stage 시간 1 개로 푸쉬해 버린다.

상세 우회 시도 history 는 `external_review/TBS_Timeline_Limitation_Guide.md` §4 참조 (Hotfix 4 ~ 10).

### 3.2 해결책 ① — Option E (Offscreen Stage + Master Mirror)

```
[Master Stage]              ← 사용자 viewport. SetCurrentTimeCode 호출 안 함 (=정지)
   /World/aaa               ← mirror prim (자산 reference + LayerOffset(0, 1e-9) 로 freeze)
   /World/bbb               ← mirror prim

[Offscreen Stage A]         ← in-memory. test1.usd 자체.
   defaultPrim 산하 모든 prim 의 xformOp/Skel timeSamples

[Offscreen Stage B]         ← in-memory. test2.usd 자체.

매 frame:
  for inst in instances:
    inst.offscreen_stage.SetCurrentTimeCode(inst.virtual_time * 30)
    for path, attr in inst.attr_cache:
        v = attr.Get(inst.virtual_time * 30)
        write_default_to_master_mirror(master_stage, path, v)
```

- Master stage 는 시간 평가 안 함 → Hydra 의 stage-global 평가에 자산 timeSamples 가 끼지 못함.
- LayerOffset(0, 1e-9) 로 master 의 자산 reference timeline 을 사실상 freeze (=시간이 1e-9
  배로 압축되어 실질 정지).
- 자산은 **offscreen stage 에서 자기 시간으로** 평가 → 진짜 independent.
- 평가 결과 (xformOp:* 의 timeSamples 값) 를 master mirror prim 의 동일 attribute 에
  **default value** 로 write → master 가 정지된 시간에서도 그 값이 보임.

코드: `lam_instance_runtime.py:AnimationInstanceRuntime`,
`lam_runtime_evaluator.py:_on_update_option_e`, `lam_master_stage.py`.

### 3.3 한계 ②: Option E 가 OmniGraph 자산에서 실패한 이유

#### 3.3.1 증상

사용자의 `test1.usd` 를 Option E 로 로드 후 USD_TIMELINE 재생 → 화면 변화 0.
로그:

```
[LAM/RT] attrs_cached=0 animatable_attr_count=0
[LAM/RT] diag types: PushGraph=1 OmniGraph_Node=N ...
```

`_build_attr_cache` 가 자산의 모든 xformOp 를 검사해도 **timeSamples 가 박힌 attribute 가 0 개**.
즉 평가할 시간 데이터가 없음.

#### 3.3.2 객관적 원인

자산의 애니메이션 데이터가 timeSamples 가 아닌 **OmniGraph (PushGraph) 노드 그래프** 로
표현됨. 그래프 구조:

```
PushGraph(@root) ── ReadPrim ── Compute ── WritePrim(translate/rotate/scale)
                                  ▲
                                  └── input: Kit 의 global time (omni.timeline)
```

OmniGraph 는 **Kit 의 활성 런타임 컨텍스트 안에서만 평가**:

- evaluator 가 Kit 의 update tick 마다 `OmniGraph::evaluate(graph_path, current_time)` 호출.
- graph 의 output (`WritePrim` 노드) 이 prim 의 attribute 에 **즉시 write** (USD authoring).
- 이 write 는 stage 의 attribute 에 들어가지만 **timeSamples 가 아니라 매 tick 의 현재 값** 만.

**Isolated `Usd.Stage`** (= Option E 의 offscreen stage) 는:

- Kit 의 default context 와 분리된 in-memory stage.
- OmniGraph 런타임이 이 stage 를 **populate 하지 않음** (등록되어 있지 않음).
- 따라서 `offscreen_stage.SetCurrentTimeCode(t)` 를 아무리 호출해도 OmniGraph 가 평가되지 않고,
  prim 의 attribute 는 **자산 파일에 박힌 default 값 그대로** 머무름.

#### 3.3.3 구조적 결론

| 자산 종류 | timeSamples 있나? | OmniGraph 의존? | Option E offscreen 평가 |
|---|---|---|---|
| FBX → USD 변환 (보통의 베이크된 자산) | O | X | ✓ 정상 동작 |
| Curve animation (USD 표준 curve) | O | X | ✓ 정상 동작 |
| OmniGraph 구동 자산 (`PushGraph` 등) | ✗ (또는 graph 가 동적 author) | O | ✗ **불가능** |

OmniGraph 의존 자산을 Option E 로 평가하려면 **Kit 런타임 컨텍스트를 offscreen stage 에 연결**
해야 하는데, 이는 Kit 의 OmniGraph public API 로 지원되지 않음. (OmniGraph 의 graph cache 는
`omni.usd.get_context().get_stage()` 1 개에 bound 됨)

### 3.4 해결책 ② — Bake: OmniGraph → timeSamples

#### 3.4.1 아이디어

> "OmniGraph 가 Kit 런타임 안에서만 평가된다면, **Kit 런타임 안에서 한 번 굴려서 매 frame 의
> 결과를 표준 USD timeSamples 로 박은 새 자산** 을 만들자."

이렇게 만든 `*_baked.usd` 는:
- 모든 애니메이션이 **표준 USD timeSamples** 로 표현됨.
- OmniGraph 노드는 `over { active = false }` 로 비활성화.
- 일반 USD 자산과 동일하게 isolated stage 에서도 평가 가능.
- → Option E offscreen 평가 호환.

#### 3.4.2 USD 표준 측면에서의 정당성

USD 사양상 `Animation` 이라는 별도 type 은 없고, **모든 시변 데이터는 attribute 의
timeSamples 메타** 로 표현. OmniGraph 가 만들어내는 결과도 결국 prim attribute 에 적용되는
값. 따라서 매 frame 의 그 값을 capture 해 timeSamples 로 박는 건 **USD 가 본래 지원하던
정통 표현 방식으로 변환** 하는 것 — lossy 가 아닌 representation change.

### 3.5 Option E 와 Bake 의 조합 — 최종 아키텍처

```
[원본 자산 *.usd]                          [Baked 자산 *_baked.usd]
 ├ OmniGraph PushGraph                      ├ over /Root/PushGraph { active = false }
 └ /Root/Geom/...                           ├ reference → *.usd  (mesh, material 그대로)
   (timeSamples 없음)                       └ /Root/Geom/... 의 xformOp timeSamples
                                              (Bake 가 박은 결과)
                          │
                          │ [Bake] 버튼
                          ▼
  매 frame Kit 런타임에서 OmniGraph 굴려 결과 capture → timeSamples 박기

[Master Stage] (사용자 viewport)
   /World/aaa  ← *_baked.usd reference (LayerOffset(0, 1e-9) 로 timeline freeze)

[Offscreen Stage A] (in-memory, Option E)
   *_baked.usd 자체. SetCurrentTimeCode 로 timeline 진행 OK.
   → /Root/Geom/...xformOp:translate.Get(t) 가 timeSamples 에서 정상 lookup.
```

---

## 4. Bake 의 기술 구현 디테일

코드 진입점: `lam_bake_omnigraph.py:bake_prim_to_timesamples_async`.

### 4.1 흐름 개요 (X3 — 2026-05-12 정책 기준)

```
0. inst_prim_path 의 자산 경로 (asset_path) + upAxis 읽기
1. master(default) timeline 의 rate-limit 일시 해제 (속도 최적화)
2. warm-up tick 2 회 (OmniGraph schema 안정화)
3. 매 frame:
     a. master timeline 을 frame t 로 set
     b. Kit tick 1 회 대기 (await app.next_update_async()) → OmniGraph 평가
     c. inst_prim 산하 모든 prim 의 xformOp 의 현재 값 capture (samples 누적)
4. 정적 attribute pruning (모든 frame 동일 값이면 default 1 회만 author)
5. 출력 layer (anonymous Sdf.Layer) 생성:
     a. 원본을 reference 로 묶음
     b. OmniGraph prim 은 `over { active = false }`
     c. 동적 attribute 는 Sdf-batch 로 timeSamples 일괄 author
     d. 정적 attribute 는 default 만 author
     e. stage upAxis 를 원본과 동일하게 set
6. 출력 분기 (`output_mode` 파라미터):
     - `"memory"` (UI [Bake] 기본): **layer 자체를 BakeResult.baked_layer 로 반환**.
       디스크 출력 없음. 콘솔에 layer dump 출력 (변환된 timeSamples 형식 확인용).
     - `"file"` (외부 도구용): `out_layer.Export(path)` 로 *_baked.usd 디스크 저장.
7. (X3 경로) `evaluator.attach_memory_baked_layer(prim_path, layer)` 호출 →
   해당 인스턴스 runtime 의 offscreen Stage 를 anonymous layer 로 재구성.
   master stage 의 reference 는 원본 자산 그대로 (변경 없음). 인스턴스 교체 없음.
```

### 4.2 핵심 결정 4 가지 (= 핫픽스를 일으킨 것들)

| 항목 | 결정 | 이유 |
|---|---|---|
| **Sdf.ChangeBlock 사용 안 함** | author 단계는 ChangeBlock 바깥 | block 안에서 OverridePrim 이 `IsValid()=False` 로 평가되어 **0 attrs authored** 사고 발생. |
| **Sdf-level batch SetInfo** | 동적 attribute 는 `attrSpec.SetInfo("timeSamples", {tc:v, ...})` 단일 호출 | per-sample `attr.Set(v, tc)` 가 매번 composition 재실행 → author 단계 병목. Sdf 직접 쓰기로 우회. |
| **inst_prim 자체 capture 제외** | bake 가 자산 root 의 xform 은 **건들지 않음** | `add_usd` 의 upAxisFix RotateX(+90) 이 baked 결과에 박혀 재로딩 시 **이중 회전 → 누워있는 자산** 발생. |
| **stage upAxis 명시** | `out_stage.SetStageUpAxis()` 를 원본과 동일하게 | upAxisFix 가 한 번만 적용되도록 보장. |

### 4.3 성능 최적화 (무손실)

품질을 못 깎는 D6 정책 하에서 적용 가능한 가속:

| 기법 | 효과 | 손실 |
|---|---|---|
| **정적 attribute pruning** | 모든 frame 동일 값 → default 1 회만 author. runtime 의 attrs_cached 도 ↓ | 0 (정의상 같은 값) |
| **Sdf-batch SetInfo** | per-sample Set → 일괄 dict author. author 단계 ~70% 단축 기대. | 0 |
| **rate-limit 일시 해제** | Kit 의 main run-loop rate-limit 를 bake 동안 끔 → tick 빨라짐 | 0 (bake 후 원복) |
| **attribute metadata 캐싱** | `GetName()/GetTypeName()` 결과 캐싱 | 0 |

손실 가능 가속 (opt-in only, 현재 미적용):
- `LAM_BAKE_FRAME_STRIDE>1`: frame 건너뜀 → 짧은 블립 손실 가능
- `LAM_BAKE_SPARSE_SAMPLES=true`: 연속 동일값 압축 → 시간상 곡선이 미세 변하는 경우 손실 가능

### 4.4 read 단계는 가속 불가 (왜?)

> "그 frame 의 값이 실제로 다른지 알려면 그 frame 을 읽어봐야 한다." — 이게 read 단계의
> 본질적 비용. 무손실 가속 옵션이 없음.

opt-in 가속안 (§5 한계 §6.5):
- **probe pre-scan**: 시작/중간/끝 3-5 frame 만 먼저 읽어 "모두 같음" attr 만 정적 확정 →
  메인 루프에서 제외. 산업 장비처럼 단조 운동이면 거의 손실 0, 짧은 블립이 probe 사이에서만
  발생하면 미세 손실.
- **xformOp 통합**: 매 frame local matrix 1 개만 캡처. MOVE/ROTATE 의 `TBS_OFFSET` 와 호환성
  추가 검증 필요.

### 4.5 Bake 가 깨뜨리지 않는 것 / 깨뜨릴 수 있는 것

| 항목 | 영향 |
|---|---|
| 자산의 mesh / topology | **깨뜨리지 않음**. baked 가 원본을 reference 로 묶음. |
| 자산의 material / shader | **깨뜨리지 않음**. 같은 이유. |
| 자산의 SkelRoot / SkelAnim | (현재 검증 자산에는 없음) — Skel attribute 도 timeSamples 박힘. 별도 검증 필요. |
| 원본 파일 위치 | **깨뜨림 가능**. baked 가 원본을 reference 하므로 원본을 옮기면 깨짐. 배포 시 같이 옮겨야. |
| 자산의 OmniGraph | **비활성**. baked.usd 에서 `active = false`. 원본은 그대로 보존. |
| material 의 OmniGraph (Texture Procedural 등) | 자산 root 산하의 PushGraph 만 비활성화. material 산하 graph 는 보존. (검증 필요) |

---

## 5. 사용자 작업 흐름 (Timeline 방식 ↔ Bake 방식 비교)

### 5.1 Timeline 방식 (실패한 옛 방식)

```
1. 자산 *.usd 를 master 에 reference 로 add_usd
2. 시퀀스에 USD_TIMELINE step 추가
3. Run → 모든 인스턴스가 master timeline 1 개에 묶여 동시 평가 (= 독립 재생 실패)
```

### 5.2 Bake + Option E 방식 (현재 — X3 in-memory)

```
1. 자산 *.usd 를 master 에 add_usd (Option E offscreen 자동 생성)
2. [Bake] 클릭 → in-memory anonymous Sdf.Layer 생성 (디스크 *_baked.usd X)
   ├ runtime 의 offscreen Stage 가 baked layer 로 재구성됨
   ├ 인스턴스 교체 없음 — master.usd 의 reference 는 원본 그대로
   ├ 콘솔에 변환된 timeSamples 형식 dump 출력
   └ Option E 가 baked layer 의 timeSamples 를 매 frame offscreen 평가
3. 시퀀스에 TIMESAMPLES_REPLAY step 추가 (실무용) — 또는 USD_TIMELINE 도 동일 동작
4. Run → 각 인스턴스가 자기 offscreen stage 의 virtual_time 으로 독립 평가 → 독립 재생 OK
```

→ **Bake 는 휘발성** — Kit 종료 시 사라진다. 다음에 Kit 을 켜고 다시 자산을 add_usd
하면 [Bake] 를 다시 한 번 클릭 필요. 사용자 의도된 테스트 흐름 (prompt.txt 239~244).

---

## 6. FAQ — 흔한 의문

### Q1. "Unity 처럼 각 USD 마다 Animator 컴포넌트 두면 안 되나?"

→ USD 에는 per-object playback component 가 없다. 모든 animation 은 attribute 의 timeSamples
로 표현되고 stage current time 으로만 평가된다. (`TBS_Timeline_Limitation_Guide.md` §6 (a))

### Q2. "Multi-viewport (장비마다 viewport 1 개)는 안 되나?"

→ Kit 의 viewport 위젯은 viewport 마다 USD context 를 따로 바인딩 가능. 즉 진짜 독립 재생.
   그러나 사용자 요구는 "두 장비가 한 viewport 안에서 같이 보여야 함" — multi-viewport 위반.

### Q3. "원본 31MB 가 baked 171KB 인데 material 손실 아닌가?"

→ 아니다. baked layer (in-memory) 는 **원본을 reference** 로 묶고 timeSamples over 만
   author. material / shader / texture / mesh 는 모두 reference 로 통과. 시각 결과 동일.
   X3 (2026-05-12) 이후엔 디스크 파일조차 만들지 않으므로 "용량 감소" 자체가 사용자
   시야에 보이지 않게 됨.

### Q4. "FBX → USD 변환본은 OmniGraph 가 있나?"

→ 일반적으로 **없음**. 3ds Max → FBX → USD 변환은 보통 skeleton+keyframe 을 timeSamples 로
   박는다. 따라서 Bake 없이도 Option E 가 곧장 동작할 가능성 높음.
   단 실제 변환본을 받으면 `[Option E 진단]` 로 `diag types` 출력해 OmniGraph 유무 확인 필요.

### Q5. "왜 single stage 의 LayerOffset 우회로가 안 됐나?"

→ Hotfix 4 ~ 10 의 6 시간 시도 history 는 `external_review/TBS_Timeline_Limitation_Guide.md`
   §4 에 상세. 결론: Hydra render-time 이 stage-global evaluation 이라는 fact 는 LayerOffset 으로
   못 넘는다.

---

## 7. 결정 사항 — 정책 (Bake 운용 룰)

| # | 정책 | 근거 |
|---|---|---|
| D4 | OmniGraph 자산은 [Bake] 후 in-memory baked layer 로 사용 (2026-05-12 D13) | OmniGraph 는 isolated stage 평가 불가. |
| D5 | [Bake] 는 항상 새로 굽기 (mtime skip X) | 사용자 혼란 + 옛 매개변수 결과 남음 방지. |
| D6 | bake 기본은 무손실 (stride=1, sparse=False) | 사용자: "품질 최우선". |
| D7 | baked layer 의 stage upAxis 는 원본과 동일 | 이중 RotateX 보정 방지. |
| D8 | [Bake] / [Remove] / [모두 초기화] 시 `evaluator.forget_instance` 명시 호출 (단 [Bake] 는 X3 에서 `attach_memory_baked_layer` 가 forget 대신 attr_cache invalidate) | runtime offscreen_asset 캐시 누수 방지. |
| D9 | bake 의 author 단계는 `Sdf.ChangeBlock` 사용 금지 | OverridePrim.IsValid()=False 사고 방지. |
| D10 | bake 의 author 는 Sdf-level `attrSpec.SetInfo("timeSamples", dict)` 일괄 기록 | per-Set composition 비용 제거. |
| D13 (2026-05-12) | UI [Bake] 의 출력은 **in-memory anonymous `Sdf.Layer` 만**. 디스크 `*_baked.usd` 는 만들지 않는다. file 모드는 호출자가 `output_mode='file'` 로 명시 지정한 경우만 사용. Kit 종료 시 baked layer 가 메모리에서 소멸 (휘발성). 재사용하려면 [Bake] 다시 클릭. | 사용자 요청 (prompt.txt 239~244). 디스크 흔적 / master.usd 비대화 / 인스턴스 교체 사이클 회피. |

(D1, D2, D3 는 별건 — TBS 미변경 / Option E 기본 True / FPS 30 고정. Handoff §4 참조.)

### 7.1 step kind 정책 (2026-05-12 추가)

LAM 시퀀스 편집기의 step 종류는 다음 5 가지. 두 가지 인스턴스 재생 step (USD_TIMELINE /
TIMESAMPLES_REPLAY) 의 의미를 명확히 구분한다.

| step kind | 용도 | 평가 방식 | 멀티 인스턴스 독립 |
|---|---|---|---|
| `TIMESAMPLES_REPLAY` | **실무용** — 멀티 인스턴스 독립 재생 | Option E offscreen stage 에 자기 virtual_time 진행 → master mirror 에 default value 기록 | **O** |
| `USD_TIMELINE` | **테스트용** (TBS 호환 이름) — 현 단계에서는 TIMESAMPLES_REPLAY 와 동일 동작. **추후 단계에서** TBS 처럼 `omni.timeline.play()` 로 master stage 시간을 진행하는 방식으로 재구현 예정. | (현재) TIMESAMPLES_REPLAY 와 동일 / (추후) master timeline play | (현재) O / (추후 TBS 방식 재구현 후) X (의도된 한계) |
| `MOVE` | TBS 와 동일 — 지정 prim 을 `dx/dy/dz` 만큼 평행 이동 | translate op 직접 갱신 | (자산과 독립) |
| `ROTATE` | TBS 와 동일 — 지정 prim 을 회전 | rotateXYZ / 사용자 축 회전 | (자산과 독립) |
| `DELAY` | TBS 와 동일 — wall-clock 대기 | (없음) | — |

**| 정책 D11 (2026-05-12) | TIMESAMPLES_REPLAY = 실무 기본값. USD_TIMELINE 은 TBS 와의 이름
호환을 위해 남김. 실무 JSON 은 TIMESAMPLES_REPLAY 로 작성. |**

**| 정책 D12 (2026-05-12) | USD_TIMELINE step 의 TBS 방식 (`omni.timeline.play()`) 재구현은
TIMESAMPLES_REPLAY 의 멀티 인스턴스 독립 재생이 사용자 검증을 통과한 뒤 별도 단계에서 진행.
검증 전에는 두 step kind 가 동일 동작 → 회귀 위험 0. |**

---

## 8. 한계 / 위험 메모

- **read 단계 무손실 가속 불가**. frame 마다 attr.Get 은 본질 비용.
- **OmniGraph 종류 의존**. 본 bake 는 `PushGraph` 처럼 "stage 시각 변경 즉시 평가" 되는
  그래프 가정. tick-mode / 사용자 입력 의존 그래프는 미지원 (검증 필요).
- **자산 reference 의존**. baked.usd 가 원본을 reference 하므로 원본 이동 시 깨짐. 배포 시
  같이 옮겨야 함.
- **Skel 자산 미검증**. UsdSkel 의 joint transforms / blendshape weights bake 는 별도 호환
  검증 필요 (현재 자산에 없음).
- **material 산하 OmniGraph 미검증**. 자산 root 의 PushGraph 만 비활성화. material 안의 그래프
  (Texture Procedural 등)는 그대로 유지 — 호환성 별도 점검.

---

## 9. 마지막 — "이것만 보면 됨" 요약

> Timeline 방식이 안 되는 이유는 둘이다.
>
> **① USD Stage 의 시계는 1 개뿐이다.** 한 stage 에 여러 자산을 reference 로 얹어 각자 시간을
> 다르게 주려는 시도는, LayerOffset 으로 composition 단계는 잡을 수 있어도 **Hydra render-time
> 이 stage-global 로 평가**한다는 architectural fact 를 못 넘는다.
>
> **② OmniGraph 는 Kit 런타임에 강결합되어 있다.** 그래서 자산마다 별도 stage(=offscreen
> stage)를 만들어 시계를 분리해도 (Option E), OmniGraph 가 평가될 컨텍스트가 없어 멈춰버린다.
>
> 그래서 **Bake** 를 쓴다.
>
> **Bake = Kit 런타임에서 OmniGraph 를 한 번 굴려 매 frame 의 결과 좌표를 표준 USD
> timeSamples 로 박은 새 layer 를 만드는 변환.** 결과물은 OmniGraph 없이도 standalone
> 평가 가능 → Option E 의 offscreen stage 에서 자기 시간으로 독립 재생 OK → 멀티
> 인스턴스가 한 viewport 에서 timeline 충돌 없이 유기적으로 동작.
>
> **X3 (2026-05-12)**: Bake 결과는 **anonymous Sdf.Layer 로 메모리에만** 보관 (휘발성).
> 디스크 `*_baked.usd` 를 만들지 않는다. Kit 종료 시 사라지며, 다시 사용하려면 [Bake]
> 를 다시 클릭. 인스턴스 교체 (remove_usd / add_usd) 도 일어나지 않음. mesh / material
> 은 원본 reference 라 깨지지 않고, OmniGraph 만 비활성. 품질 손실 0, 정통 USD 표현
> 변환.
>
> → **결과**: 사용자 요구 ("여러 자산 한 viewport, 독립 timeline, JSON 시퀀스, 외부 시뮬
> 트리거") 가 모두 같은 architectural skeleton (Option E + Bake) 위에서 자연 동작한다.
>
> **시퀀스 step kind 도 분리.** 실무 = `TIMESAMPLES_REPLAY` (멀티 인스턴스 독립). 테스트
> = `USD_TIMELINE` (TBS 호환 이름, 추후 단계에서 진짜 TBS 방식 `omni.timeline.play()` 로
> 재구현 예정. 현재는 TIMESAMPLES_REPLAY 와 동일 동작 — 회귀 위험 0).

---

## 부록 — 관련 문서 빠른 링크

```
source/extensions/morph.lam_control/docs/
  ├ LAM_Timeline_vs_Bake.md             ← (본 문서) — 왜 timeline 대신 bake 인가
  ├ LAM_Bake_Handoff.md                  ← 다음 세션 진입점 (작업 상태)
  ├ LAM_Independent_Playback_Plan.md     ← Option E 전체 설계 (architectural rationale)
  ├ LAM_Spec.md                          ← 모듈/요구사항 명세
  ├ CHANGELOG.md
  ├ README.md
  ├ external_review/
  │  └ TBS_Timeline_Limitation_Guide.md  ← Hotfix 4 ~ 10 의 상세 실패 history (외부 검토용)
  └ daily/
      └ 2026-05-11.md                    ← 의사결정 흐름 (장문)
```
