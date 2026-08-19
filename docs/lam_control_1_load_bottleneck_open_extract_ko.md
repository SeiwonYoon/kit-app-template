# LAM Control 1 — 로드 병목 2구간 수정 정리

> **작성일:** 2026-08-18 (구간 ① 외부 참조 스킵: 2026-08-20)
> **대상:** `morph.lam_control_1` (TBS `morph.tbs_control_2` 동일 플래그)
> **전제:** 로드가 끝난 뒤 재생·Bake·화면2 재사용은 **기존과 동일**.
> **이전 작업과 구분:** [`lam_control_1_startup_load_dedupe_ko.md`](lam_control_1_startup_load_dedupe_ko.md) 는 화면2 Extract 중복·OmniGraph Flatten 생략. **이번 두 구간은 그때 해소되지 않았다.**

이 문서는 **쉬운 설명**과 **보고용 표**를 한 파일에 둔다.

---

# 한 장 요약

배포 기동이 오래 걸리던 곳은 두 군데다.

| 구간 | 수정 전 (배포) | 원인 | 이번 수정 | 해결되나 |
|------|----------------|------|-----------|----------|
| **① 파일 열기** | 약 20분~그 이상 | 원본 USD에 남은 **외부 경로**를 Kit이 따라가며 대기 (배포는 보안상 막힘) | 로컬에서 외부를 뺀 복사본을 만들고, 배포에서는 **그 파일만** `open_stage` | **True + 캐시**면 외부를 따라가지 않음 |
| **② 첫 Extract** | 약 30~40분 | 장비 하나가 아니라 **공장 전체를 Flatten**한 뒤 필요한 부분만 자름 | 로컬에서 그 결과를 파일로 만들고, 배포에서는 **그 파일만 읽음** | **맞다.** 플래그 + 캐시가 있으면 Flatten을 다시 하지 않음 |

오픈 중 화면 그리기를 멈추는 시도는 코드에 남아 있으나, **구간 ① 시간에는 효과가 없었다.** 아래 본문은 그 설명을 쓰지 않는다.

로드가 끝난 뒤의 시뮬 동작은 바꾸지 않았다.

---

# 제1부. 알기 쉬운 설명

## 1. 로컬은 빠르고 배포만 느린 이유

코드가 다른 것이 아니다. **같은 프로그램, 같은 실무 USD**다.

그래서 로그가 멈추는 곳이 두 곳이다.

1. Master USD를 Kit에 **여는 동안** (`open_stage`)
2. 첫 장비 Extract에서 **Flatten하는 동안**

로컬에서는 외부 경로가 열려서 ①이 짧게 끝나고, 테스트 USD는 Flatten을 건너뛰는 경우도 많다. 배포 실무 USD는 크고, 남은 외부 경로는 막혀 있으며, Flatten을 반드시 해야 했다.

---

## 2. 구간 ① — 배포에서 막힌 외부 참조를 열고 지나가지 않게

실무 USD의 머티리얼·배경은 **로컬 경로를 보도록 이미 고친 상태**다. 배포는 보안상 외부 참조가 막혀 있다. 로컬에서는 같은 외부 경로가 있어도 열린다.

그래도 미처 못 고친 외부 경로가 남아 있을 수 있다. 배포에서는 그 경로는 **동작할 필요가 없다.** Kit이 그걸 따라가며 `open_stage`가 멈추는 것만 막으면 된다.

이 처방이 `USE_PREEXTRACTED_LAYERS`(구간 ② Flatten 캐시)와 **다른 플래그**인 이유다. Extract 결과가 아니라, **파일을 여는 순간**의 문제다.

스위치: `lam_sim_control_defaults.py` / `sim_control_defaults.py` 의 `USE_PRESTRIPPED_OPEN_STAGE`

저장 위치: `data/stripped_open/` (`manifest.json` + `.usdc`)  
원본 USD는 수정하지 않는다. 복사본에서만 외부 경로를 비운다.

### False / True

| 값 | 어디서 | 화면이 여는 파일 | 외부 참조 |
|----|--------|------------------|-----------|
| **False** | 로컬 (캐시 만들기) | **원본 USD** (기존과 같음. 로컬이면 외부도 따라감) | Kit이 **그대로 읽음**. 그와 별도로 외부를 뺀 복사본을 `data/stripped_open/`에 저장 |
| **True** | 배포 (캐시 사용) | **`data/stripped_open/`만** | 캐시에 이미 빠져 있어서 **따라가지 않고 넘어감**. 로컬 경로로 남은 머티리얼·배경은 읽음 |

False는 “배포처럼 외부를 무시하고 연다”가 아니다. **화면은 원본**, 캐시만 만들어 둔다.  
True는 원본을 열다가 스킵하는 것이 아니라, **외부가 이미 빠진 파일을 연다.** 배포에서 원하는 결과(외부 무시, 로컬은 로드)는 같다.

캐시가 없으면 구간 ② True와 같다. **원본으로 몰래 넘어가지 않고 실패**한다.  
화면1은 `open_master`의 `open_stage`, 화면2는 보조 컨텍스트의 `open_stage`다. True일 때 둘 다 이 캐시를 쓴다. 화면2 전용 파일이 캐시에 없으면 화면1과 내용이 같은 복사본이라는 전제로 화면1 캐시를 복사해 연다.

### 현장에서 쓰는 순서

1. 로컬에서 플래그를 **False**, 배포와 **같은 Master**(화면1·화면2 USD)로 앱을 한 번 연다.
2. `data/stripped_open/`에 파일이 생겼는지 확인한다.
3. 그 폴더를 `data/preextract/`와 같이 배포 패키지에 넣고, 배포 플래그를 **True**로 둔다.
4. Master가 바뀌면 1~3을 다시 한다.

### 화면이 빨개지거나 머티리얼이 없을 때

True에서만 깨지고 False에서는 정상이면, 그 머티리얼은 **아직 로컬 경로가 아닌 외부 참조**다. 배포에서는 원래 못 쓰는 부분이므로 True가 무시한 것이다. 실무 파일에서 외부가 더 없으면 True여도 화면이 정상이어야 한다.

테스트용 USD처럼 외부가 남아 있으면 True에서 빨강이 정상이다.

---

## 3. 구간 ② — Flatten이 느린 이유, 플래그로 해결 (완료)

### Flatten이 뭔가

Master USD는 메시가 한 장에 다 들어 있는 파일이 아니다.
장비마다 다른 파일을 **가리키고(reference)**, 필요할 때 따라가 조립한다.

재생하려면 이미 구워 둔 움직임(timeSamples)을 **한곳에 모은 레이어**가 필요하다.
그래서 Extract 앞에서 Flatten을 한다.

> **Flatten** = 가리킴을 전부 따라가서, 한 장에 펼쳐 놓은 결과를 메모리에 만드는 일.

이것은 파일 열기(`open_stage`)가 아니다.

**기존에 Extract가 느린 이유:** 장비 하나만 펼치는 것이 아니라, **공장 전체를 펼친 뒤 필요한 부분만 자르기** 때문이다.

- 첫 장비에서 30~40분
- 같은 실행의 2~4번째는 방금 펼친 것을 재사용해 빠름
- 프로그램을 끄면 메모리에서 사라지고, **다음 기동 때 또 전체 Flatten**

### 플래그로 해결 — 적용됨

1. **로컬**에서 한 번 로드하면서 Flatten+Extract 결과를 파일로 만들고
2. **배포**에서는 그 파일을 읽어 붙인다

배포에서는 공장 전체를 다시 펼치지 않는다.

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
- Master를 **반드시 연다**는 것 (`open_stage` 생략 없음. 구간 ① True면 여는 파일이 캐시일 뿐)
- 로드 후 재생, Bake, 화면2가 화면1 결과를 재사용하는 것

바뀐 것은 **배포 `open_stage`가 원본의 외부 경로를 따라가는지**, **배포에서 Flatten을 다시 하는지**이다.

---

# 제2부. 보고용

## 5. 기존 / 수정 후 대비

### 구간 ① Master 오픈

| 항목 | 기존 | 수정 후 False (로컬) | 수정 후 True (배포) |
|------|------|---------------------|---------------------|
| 로그 정체 | `fps_sync` → `open_master OK` (외부 대기로 길어짐) | 원본 오픈 + 캐시 저장 | 캐시만 오픈. 외부 대기 없음 |
| 열기 | `ctx.open_stage(원본 master)` | **원본** | **`data/stripped_open/`만** |
| 외부 참조 | Kit이 따라감 (배포면 막혀 대기) | 그대로 읽음 | 캐시에서 비워 둠. **따라가지 않음** |
| 파일 없음 | — | 캐시 생성 | 오픈 **실패**. 원본 폴백 없음 |

로그 (False):

```text
[LAM/UsdStrip] data/stripped_open copied=… open=…
open_stage 원본=…/master_1.usd
오픈스테이지1111111111111
```

로그 (True):

```text
[LAM/UsdStrip] prestripped open …/data/stripped_open/….usdc
오픈스테이지1111111111111
```

### 구간 ② 첫 Extract — 해결됨

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
| 해당 장비만 펼침 | **공장 전체**를 펼친 뒤 필요한 prim만 자름 |

---

## 6. 기대 효과와 한계

| 구간 | 기대 | 남는 것 |
|------|------|---------|
| ① False | 캐시 생성. **시간 단축이 목적 아님** | 원본 `open_stage` (로컬이면 외부도 읽음) |
| ① True + 캐시 있음 | 외부 경로 대기 **생략** | 로컬 경로만 로드. 외부에만 있던 머티리얼은 없음 |
| ① True + 캐시 없음 | — | `open_master`/화면2 오픈 **실패**. 원본 폴백 없음 |
| ② False | 캐시 파일 생성. **시간 단축이 목적 아님** | 첫 Flatten 30~40분 그대로 |
| ② True + 캐시 있음 | 그 Flatten **생략** → 파일 읽기 + attach | 캐시 I/O. 파일/경로가 다르면 Extract 실패 |

구간 ① True의 실제 단축 폭은 배포 로그로 재실측한다. 구간 ② True는 Flatten을 다시 하지 않는다.

---

## 7. 관련 파일

| 파일 | 역할 |
|------|------|
| `lam_sim_control_defaults.py` | `USE_PREEXTRACTED_LAYERS`, `USE_PRESTRIPPED_OPEN_STAGE` |
| `lam_usd_strip_external.py` | `data/stripped_open` 생성·조회. 화면2는 캐시 세트 분리 복사 |
| `lam_master_stage.py` | 화면1 `open_stage` (False=원본, True=캐시) |
| `lam_multi_viewport.py` | 화면2 `open_stage`에 같은 플래그 |
| `lam_preextract_cache.py` | `data/preextract` 저장·로드 |
| `lam_runtime_evaluator.py` | Extract 후 저장 / True면 캐시 attach |
| `lam_window.py` | True면 Flatten 캐시 미사용 |
| `lam_extract_from_master.py` | 기존 Flatten + CopySpec |

플래그는 모듈 상수이므로 변경 후 **Kit/확장 재시작**이 필요하다.

---

## 8. 한 줄

**①** 배포에서 막힌 외부 경로는 False로 캐시를 만들고 True로 그 캐시만 열어 **따라가지 않는다.**
**②** Extract의 Flatten은 로컬에서 파일로 만들고 배포에서 그 파일만 읽도록 해서 **해결됐다.**
로드 후 시뮬 동작은 기존과 같다.

---

# 회의용 1분 설명 (원인 + 개선)

아래는 회의에서 **1분 안에 읽으면 되는 말**이다. 기술 용어는 최소만 쓴다.

---

배포에서만 기동이 오래 걸렸습니다. 프로그램이 다른 게 아니라 **실무 USD** 때문입니다. 병목은 두 구간이었습니다.

**첫째**는 Master USD를 여는 구간입니다. 머티리얼·배경은 로컬 경로로 이미 고친 상태입니다. 그런데 못 찾은 외부 경로가 남아 있으면, 배포에서는 보안상 그 주소가 막혀 열기가 오래 걸립니다. 로컬에서는 같은 외부가 열려서 문제가 안 보입니다. 배포에서는 그 외부를 쓸 일이 없습니다.  
**개선**은 로컬에서 외부 경로를 뺀 복사본을 만들어 두고, 배포에서는 그 파일만 여는 것입니다. 동작할 필요 없는 외부는 무시하고, 로컬 경로로 남은 부분만 읽습니다.

**둘째**는 재생용 레이어를 만드는 Extract 구간입니다. 한 장비만 쓰더라도 Master 장면 전체의 참조를 풀어 한 레이어로 합치는 작업이 먼저 필요했고, 그 비용이 첫 장비에 몰려 약 30~40분이 걸렸습니다.  
**개선은 적용됐습니다.** 로컬에서 Extract 결과를 파일로 저장해 두고, 배포에서는 그 합치는 작업 없이 그 파일만 읽어 붙입니다.

로드가 끝난 뒤 시뮬이 돌아가는 방식은 바꾸지 않았습니다. TBS에도 같은 방식을 넣었습니다.
