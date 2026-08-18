# LAM Control 1 — 로드 병목 2구간 수정 정리

> **작성일:** 2026-08-18  
> **대상:** `morph.lam_control_1`  
> **전제:** 로드가 끝난 뒤 재생·Bake·화면2 재사용은 **기존과 동일**.  
> **이전 작업과 구분:** [`lam_control_1_startup_load_dedupe_ko.md`](lam_control_1_startup_load_dedupe_ko.md) 는 화면2 Extract 중복·OmniGraph Flatten 생략. **이번 두 구간은 그때 해소되지 않았다.**

이 문서는 **쉬운 설명**과 **보고용 표**를 한 파일에 둔다.

---

# 한 장 요약

배포 기동이 오래 걸리던 곳은 두 군데다.

| 구간 | 수정 전 (배포) | 원인 | 이번 수정 | 해결되나 |
|------|----------------|------|-----------|----------|
| **① 파일 열기** | 약 20분 | Master를 여는 동안 2화면이 **같이 그림** | 오픈·Extract가 끝날 때까지 **그리기만 멈춤** | **완화.** 열기 자체는 남음 |
| **② 첫 Extract** | 약 30~40분 | 장비 하나가 아니라 **공장 전체를 Flatten**한 뒤 필요한 부분만 자름 | 로컬에서 그 결과를 파일로 만들고, 배포에서는 **그 파일만 읽음** | **맞다.** 플래그 + 캐시 파일이 있으면 Flatten을 다시 하지 않음 |

로드가 끝난 뒤의 시뮬 동작은 바꾸지 않았다.

---

# 제1부. 알기 쉬운 설명

## 1. 로컬은 빠르고 배포만 느린 이유

코드가 다른 것이 아니다. **같은 프로그램, 같은 실무 USD**다.

- 로컬 테스트 USD는 작고 OmniGraph만 있는 경우가 많아, Flatten을 건너뛸 수 있었다.
- 배포 실무 USD는 크고 **전부 timeSamples**라서, 파일 열기와 공장 전체 Flatten을 **반드시** 해야 했다.

그래서 로그가 멈추는 곳이 두 곳이다.

1. Master USD를 Kit에 **여는 동안**
2. 첫 장비 Extract에서 **Flatten하는 동안**

---

## 2. 구간 ① — 열면서 그리던 것을, 열릴 때까지 그리지 않게

### USD / Viewport / RTX

| 이름 | 쉽게 |
|------|------|
| **USD** | 장면의 내용 (설계도) |
| **Viewport** | 그걸 보여주는 창 |
| **RTX** | 창 안에 **실제로 그림을 그리는 엔진** |

Master를 연다는 것은 일반 Kit에서 USD를 여는 것과 **같은 방식**이다 (`open_stage`).  
다른 점은 이 앱이 **화면을 두 칸으로 나눈 다음에** 연다는 점이다.

### 기존

Master USD를 여는 동안, 이미 켜져 있는 2화면이 RTX로 **같이 그리려고** 해서, 열기와 그리기가 서로를 방해했다.

### 현재

USD 오픈 + Extract가 끝날 때까지는 **그리지 않고**, 그다음에 그린다.

- Viewport/RTX를 나중에 만든 것이 아니다.
- **이미 있는 그리기만 잠시 멈춘 것**이다.
- 파일을 안 여는 것도 아니다. 열기는 그대로다.
- 이 변경은 구간 ②(Flatten) 시간을 줄이지 않는다.

---

## 3. 구간 ② — Flatten이 느린 이유, 플래그로 해결하는 방법

### Flatten이 뭔가

Master USD는 메시가 한 장에 다 들어 있는 파일이 아니다.  
장비마다 다른 파일을 **가리키고(reference)**, 필요할 때 따라가 조립한다.

재생하려면 이미 구워 둔 움직임(timeSamples)을 **한곳에 모은 레이어**가 필요하다.  
그래서 Extract 앞에서 Flatten을 한다.

> **Flatten** = 가리킴을 전부 따라가서, 한 장에 펼쳐 놓은 결과를 메모리에 만드는 일.

이것은 화면 그리기(RTX)도 아니고, 파일 열기(`open_stage`)도 아니다.

**기존에 Extract가 느린 이유:** 장비 하나만 펼치는 것이 아니라, **공장 전체를 펼친 뒤 필요한 부분만 자르기** 때문이다.

- 첫 장비에서 30~40분
- 같은 실행의 2~4번째는 방금 펼친 것을 재사용해 빠름
- 프로그램을 끄면 메모리에서 사라지고, **다음 기동 때 또 전체 Flatten**

### 플래그로 해결하는가 — 맞다

그래서 **플래그를 두고**,

1. **로컬**에서 한 번 로드하면서 Flatten+Extract 결과를 파일로 만들고
2. **배포**에서는 그 파일을 읽어 붙이게

하면, 배포에서는 공장 전체를 다시 펼치지 않아도 된다.

스위치: `lam_sim_control_defaults.py` 의 `USE_PREEXTRACTED_LAYERS`

| 값 | 어디서 | 하는 일 | Flatten |
|----|--------|---------|---------|
| **False** | 로컬 (캐시 만들기) | 예전과 같이 Extract. 결과를 `data/preextract/`에 **덮어쓰기** | **한다** (이 시간이 캐시를 만드는 시간) |
| **True** | 배포 (캐시 사용) | 저장된 파일만 읽어 예전 Extract 성공과 **같이 붙임** | **안 한다** |

저장 위치: `source/extensions/morph.lam_control_1/data/preextract/`  
(`manifest.json` + 장비별 `.usdc`)

### 현장에서 쓰는 순서

1. 로컬에서 플래그를 **False**, 배포와 **같은 Master USD**로 앱을 한 번 연다.
2. `data/preextract/`에 파일이 생겼는지 확인한다.
3. 그 폴더를 배포 패키지에 넣고, 배포 플래그를 **True**로 둔다.
4. USD나 장비 prim 경로가 바뀌면 1~3을 다시 한다.

### 성립 조건

- 캐시와 Master의 장비 경로가 **같아야** 한다.
- 배포에 파일이 없으면 Flatten으로 몰래 넘어가지 않는다. 그 장비는 **Extract 실패**와 같다.
- False로 돌릴 때는 예전처럼 30~40분이 걸릴 수 있다. **배포 True일 때** 그 시간을 건너뛰는 것이다.

---

## 4. 바뀌지 않는 것

- 2분할 화면을 먼저 만드는 것
- Master USD를 여는 것
- 로드 후 재생, Bake, 화면2가 화면1 결과를 재사용하는 것

바뀐 것은 **기동 중 그리기 여부**와 **배포에서 Flatten을 다시 하는지**뿐이다.

---

# 제2부. 보고용

## 5. 기존 / 수정 후 대비

### 구간 ① Master 오픈

| 항목 | 기존 | 수정 후 |
|------|------|---------|
| 로그 정체 | `자동 로드 시작` / `fps_sync` → `open_master OK` (약 20분) | 같은 구간에서 그리기 OFF 로그가 먼저 나옴 |
| 레이아웃 | 2분할을 **먼저** 만듦 | **유지** |
| Viewport / RTX | 오픈 전부터 켜져 있고, 열면서 **같이 그림** | 생성 시점은 같음. 오픈·Extract 동안 **그리기만 OFF** |
| 열기 | `ctx.open_stage(master)` | **동일** (생략하지 않음) |
| 구현 | — | Viewport `updates_enabled=False`, RTX 샘플 최소(보험) → 완료 후 복구 |

로그:

```text
[LAM/OpenDraw] viewport draw OFF (open/extract) maxSamples=1
… open_master / Discover / Extract …
[LAM/OpenDraw] viewport draw ON maxSamples=1024
```

기대: 열기와 그리기의 경합 **완화**. `open_stage`의 파일 읽기 자체는 남음.

### 구간 ② 첫 Extract

| 항목 | 기존 | 수정 후 False (로컬) | 수정 후 True (배포) |
|------|------|---------------------|---------------------|
| 로그 정체 | `Extract 시작` → Flatten cache STORE (약 30~40분) | 동일 + SAVE 로그 | Flatten 없음. LOAD 후 attach |
| Flatten | Master **전체** 1회 (첫 인스턴스가 비용 부담) | **동일** | **생략** |
| 디스크 | 없음. 재기동 시 다시 Flatten | `data/preextract/` **덮어쓰기** | 그 파일만 읽음 |
| attach 이후 | TIMESAMPLES_REPLAY | 동일 | 동일 (Extract 성공과 같은 경로) |
| 파일 없음 | — | — | Extract 실패. **Flatten 폴백 없음** |

로그 (False):

```text
Extract 시작 … (in-memory layer 생성 + …)
[LAM/Preextract] SAVE prim=/World/… path=…/data/preextract/….usdc
extract+attach OK
```

로그 (True):

```text
preextract 캐시 attach (Flatten 생략)
Extract 시작 … (preextract 캐시 로드 + attach…)
[LAM/Preextract] LOAD prim=/World/…
extract+attach OK … (preextract cache)
```

### Flatten (보고용 정의)

`stage.Flatten()` = Master stage의 composition(`reference` / `payload` / `variant`)을 **모두 평가**해 단일 레이어로 만듦.  
그다음 `/World/<인스턴스>`만 잘라 `/Root`로 복사하고 `attach_memory_baked_layer`에 넘긴다.

| 아닌 것 | 하는 것 |
|---------|---------|
| 파일 열기 (`open_stage`) | 이미 열린 stage를 **한 장으로 펼침** |
| 화면 그리기 (RTX) | 재생용 Extract 데이터를 만들기 위한 평탄화 |
| 해당 장비만 펼침 | **공장 전체**를 펼친 뒤 필요한 prim만 자름 |

---

## 6. 기대 효과와 한계

| 구간 | 기대 | 남는 것 |
|------|------|---------|
| ① | 그리기 경합 완화 | `open_stage` I/O·참조 해석. 스토리지가 느리면 한계 |
| ② False | 캐시 파일 생성. **시간 단축이 목적 아님** | 첫 Flatten 30~40분 그대로 |
| ② True + 캐시 있음 | 그 Flatten **생략** → 파일 읽기 + attach | 캐시 I/O. 파일/경로가 다르면 Extract 실패 |

수치(20분→X분)는 배포 로그로 재실측한다.

---

## 7. 관련 파일

| 파일 | 역할 |
|------|------|
| `lam_sim_control_defaults.py` | `USE_PREEXTRACTED_LAYERS` |
| `lam_viewport_open_draw.py` | 오픈·Extract 중 그리기 정지 |
| `lam_preextract_cache.py` | `data/preextract` 저장·로드 |
| `lam_runtime_evaluator.py` | Extract 후 저장 / True면 캐시 attach |
| `lam_window.py` | 오픈을 그리기 가드로 감쌈, True면 Flatten 캐시 미사용 |
| `lam_extract_from_master.py` | 기존 Flatten + CopySpec |

플래그는 모듈 상수이므로 변경 후 **Kit/확장 재시작**이 필요하다.

---

## 8. 한 줄

**①** 열면서 2화면이 같이 그리던 방해를, 그리기만 잠시 멈춰 줄였다.  
**②** Extract가 느린 이유(공장 전체를 펼친 뒤 자르기)는, 로컬에서 파일을 만들고 배포에서 그 파일만 읽도록 플래그를 두면 **해결된다.**  
로드 후 시뮬 동작은 기존과 같다.
