# LAM Control 1 — 앱 시작 로드 속도 개선 정리

이 문서는 **앱 로드 시 불필요·중복 작업을 줄인 수정**을, 코드에 익숙하지 않아도 이해할 수 있게 정리한 것입니다.

- 대상 확장: `morph.lam_control_1`
- 전제: **로드가 끝난 뒤 기능·동작은 기존과 동일**해야 한다.
- 배포: 동일 코드를 쓰고, 실무용 USD·prim 경로만 바꿔 배포하는 형태를 가정한다.

---

## 1. 무엇이 문제였나

로컬에서는 빨리 로드되고, 배포(실무 USD)에서는 아래 **두 구간**에서 매우 오래 걸렸다.

| 구간 | 로그 (시작 → 끝) | 배포에서 대략 | 실제로 막히던 일 |
|------|------------------|---------------|------------------|
| ① | `fps_sync` → `open_master OK` | ~20분 | Master USD를 Kit에 여는 중 (`open_stage`) |
| ② | `Extract 시작` → `Flatten cache STORE` | ~30분 | Master 전체를 평탄화 (`stage.Flatten`) |

로컬 로그에서도 **같은 순서**가 보이지만, 로컬 USD에서는 각각 수 초 / 1초 미만으로 끝난다.  
즉 “다른 로드 절차”가 아니라, **같은 두 무거운 호출**이 실무 자산·환경에서 길어지는 것이었다.

그 위·아래로도 **같은 일을 두 번** 하는 부분(화면2 Extract, activate 후 전수 재Extract, fps/스케줄 중복 등)이 있었다.

---

## 2. 개선 원칙

1. **결과를 바꾸는 우회는 하지 않는다**  
   (Master를 안 연다 / Flatten을 “대충 다른 계산”으로 대체한다 등)
2. **같은 최종 상태를 만들면서**, 이미 끝난 일·실패가 뻔한 무거운 일·중복 호출만 줄인다.
3. timeSamples가 있는 자산은 예전처럼 Flatten Extract를 유지한다.

---

## 3. 수정 내용 (쉽게)

### 3.1 OmniGraph만 있는 자산 → Flatten 생략

**이전:** Extract마다 Master 전체를 Flatten한 뒤, “timeSamples 없음 → Bake 필요”만 안내.

**이후:** composed stage를 가볍게 확인해

- OmniGraph가 있고
- timeSamples가 **없으면**

→ Flatten 없이 바로 **NEED-BAKE (OmniGraph)** 결과를 준다.  
UI·`source_asset`·Bake 안내 등 **최종 상태는 예전과 같음**.

- timeSamples가 **하나라도** 있으면 → 예전처럼 Flatten Extract.

로그 예:

```text
[LAM/Extract] extract NEED-BAKE-OR-EMPTY ... (skip Flatten: OmniGraph-only peek)
```

**실무 효과:** `/World/atm` 등이 OmniGraph 전용이면 구간 ②(~30분 Flatten)를 크게 줄일 수 있음.  
timeSamples 자산이면 Flatten은 그대로 필요.

관련 코드: `lam_extract_from_master.py` (`_try_fast_omnigraph_need_bake_without_flatten`)

---

### 3.2 화면2에서 Discover+Extract(Flatten) 중복 줄이기

**이전 (듀얼 경로):** 화면1에서 Extract한 뒤, 화면2가 다시 Discover + Flatten Extract를 돌리는 경우가 많음.  
특히 activate에서 mirror write가 0이면 **전 인스턴스를 다시 Extract(Flatten)** 하기도 함.

**이후:**

| 조건 | 동작 |
|------|------|
| 화면1 bake 레이어 attach 성공 | 화면2 Extract/Flatten **스킵** |
| 화면1에서 이미 kind = OMNIGRAPH / STATIC | 화면2 Extract/Flatten **스킵** (결과도 NEED-BAKE/정적과 동일) |
| attach 실패 + kind 미확정 | 예전처럼 해당 prim만 Extract |
| activate 후 runtime이 이미 ready | **전수 Flatten 재시도 금지**, 미준비 prim만 재시도 |

로그 예:

```text
[LAM/split-load] screen2 dual-path Extract 스킵 (bake attach 전부 성공 ...)
[LAM/split-load] screen2 dual-path Extract 스킵 (screen1 kind OMNIGRAPH/STATIC) ...
[LAM/split-load] screen2 Extract 재시도 스킵 (runtime ready, mirror_writes=...)
```

관련 코드: `lam_split_composed_loader.py` (`hydrate_split_screen_composed_stage`)

---

### 3.3 화면1 open이 끝난 뒤 화면2 open (직렬화)

**이전:** 화면1 Master open과 화면2 open이 겹치며  
`Stage opening or closing already in progress` 가 날 수 있음.

**이후:** default(화면1) 컨텍스트가 로딩 중이 아닐 때까지 기다린 뒤 화면2를 연다.  
최종으로 두 화면이 열리는 결과는 같고, **경합·재시도만** 줄인다.

관련 코드:

- `lam_multi_viewport.py` (`_wait_default_usd_context_idle`, deferred aux load)
- `lam_viewport_split_notify.py` (Master 준비 후 settle 프레임 증가)

---

### 3.4 fps_sync · 시작 스케줄 중복 줄이기

| 항목 | 이전 | 이후 |
|------|------|------|
| FPS 30 고정 | open 직전·직후 등으로 여러 번 | `open_master`는 **open 성공 후 1회** |
| prim 숨김 / viewport focus | show 시점 + Open 후 둘 다 (자동 로드 시) | 자동 로드면 **Open 후만** |

동작 목표(30fps, hide/focus)는 동일하고, 불필요한 재실행만 줄임.

관련 코드: `lam_master_stage.py`, `lam_window.py`

---

### 3.5 화면2 activate 중복 한 곳 제거

hydrate 직후 async에서 activate를 한 번 더 호출하던 부분을 제거.  
hydrate 내부 + finalize 쪽 activate는 유지해, settle 후 표시는 그대로 맞춤.

관련 코드: `hydrate_split_screen_composed_stage_async`

---

## 4. 건드리지 않은 것 (의도)

| 항목 | 이유 |
|------|------|
| Master `open_stage` 자체 제거 | Master가 안 열리면 앱 기능이 성립하지 않음 |
| timeSamples 자산의 Flatten 제거 | Extract 의미가 바뀌어 재생 결과가 달라질 수 있음 |
| Extract Flatten 구간의 “임시 파일 삭제” | 해당 경로는 **메모리 Flatten**이라 임시 파일이 없음 |

구간 ①(~20분)이 **스토리지/실무 USD I/O** 때문이라면, 코드 중복 제거만으로는 한계가 있다.  
그 경우는 배포에서 동일 USD를 더 가까운 디스크에 두고 여는 운영 조치가 보완책이 된다.

---

## 5. 로드 흐름 (수정 후 개념)

```text
[레이아웃] 2분할 UI 먼저
    ↓
[화면1] open_stage(Master) → fps_sync 1회
    ↓
Discover → Extract
    · OG only → Flatten 생략, NEED-BAKE
    · timeSamples 있음 → Flatten 1회(배치 캐시) 후 attach
    ↓
화면1 컨텍스트 idle 대기
    ↓
[화면2] open (경합 완화)
    ↓
Discover + 화면1 메타 동기화
    · bake attach 성공 / OG·STATIC → Extract 스킵
    · 필요할 때만 Extract
    ↓
activate (미준비만 재시도, 전수 Flatten 금지)
```

---

## 6. 확인 방법 (로컬 / 배포)

### 로컬 (예: `master_1` + dual-path)

1. 앱 기동 후 2분할·자동 로드가 예전처럼 끝나는지.
2. OmniGraph 인스턴스: Bake 버튼·안내가 그대로인지.
3. 콘솔에 위 3절의 **스킵 로그**가 보이는지.
4. 시뮬/재생/화면 전환이 이전과 같은지.

### 배포 (실무 USD)

1. 구간 ②: OG 전용이면 Flatten STORE까지 기다리던 시간이 줄었는지.
2. 구간 ①: open 자체는 남을 수 있음. 화면2 경합 에러가 줄었는지만 확인.
3. `/World/atm` 등에 timeSamples가 있으면 Flatten은 **정상적으로 한 번** 돌 수 있음.

---

## 7. 관련 파일

| 파일 | 역할 |
|------|------|
| `lam_extract_from_master.py` | OG peek로 Flatten 생략 |
| `lam_split_composed_loader.py` | 화면2 Extract/재시도 정책 |
| `lam_master_stage.py` | open 후 fps 1회 |
| `lam_window.py` | 자동 로드 시 시작 스케줄 중복 제거 |
| `lam_multi_viewport.py` | 화면1 idle 대기 후 화면2 open |
| `lam_viewport_split_notify.py` | Master 준비 후 화면2 로드 스케줄 |

---

## 8. 한 줄 요약

**같은 로드 결과**를 유지하면서,  
OmniGraph에만 쓰이던 전체 Flatten·화면2 중복 Extract·open 경합·fps/스케줄 중복을 걷어내 시작 로드를 효율화했다.
