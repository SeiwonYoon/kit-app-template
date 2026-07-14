# LAM Jenkins 배포 가이드 (본 레포 실측 기준)

**레포:** `kit-app-template_mine`
**앱:** `source/apps/morph.editor.kit` → 확장 `morph.lam_control`
**데이터 SoT:** 레포 루트 `lam/` (코드와 분리)

이 문서는 **ChatGPT 등에서 흔히 나오는 “repo.toml / 루트 premake 수정” 같은 일반 조언이 아니라**,
**지금 이 저장소에 실제로 있는 파일·경로만** 기준으로 Jenkins(Linux)까지 csv · JSON · USD 를 가져오는 방법을 **여러 루트(경로)** 로 나눠 적었습니다.

**관련:** `lam/README.md`, `LAM_Control_Maintenance_Guide.md`

---

## 0. 먼저 읽을 것 — 이 레포에서 건드리는 파일

| 파일 | LAM 데이터 배포와 관계 | 가이드에서 쓰는 루트 |
|------|------------------------|----------------------|
| `repo.toml` | Kit 빌드·패키징 **공통** 설정. **`lam/` 경로 설정 없음** | **루트 A·B:** 보통 **수정 안 함** |
| `premake5.lua` (레포 루트) | `morph.editor.kit` 등 **앱 등록만** | **수정 안 함** |
| `source/extensions/morph.lam_control/premake5.lua` | 확장 빌드 시 **어떤 폴더를 `exts`에 링크할지** | **루트 C·D** |
| `source/extensions/morph.lam_control/config/extension.toml` | 확장 ID·버전 `0.1.0` | 참고만 |
| `source/extensions/morph.lam_control/morph/lam_control/lam_window.py` | `default_load_usd_path`, `_find_lam_data_root()` | **루트 E** (코드) |
| `source/extensions/morph.lam_control/morph/lam_control/simulation_play.py` | CSV 경로 | **루트 E** |
| `source/extensions/morph.lam_control/morph/lam_control/lam_event_sequences.py` | JSON 경로 | **루트 E** |
| `lam/` (레포 루트) | csv · `lam_event_sequences` · `usd` 실제 파일 | **모든 루트** |
| `.github/workflows/.github-ci.yml` | `checkout` + `./repo.sh build` 만 (lam 미포함) | **루트 A** 시 Jenkins Job 보완 |

**하지 말 것 (테스트 불가능해지는 일반 잘못된 조언):**

- “`repo.toml`에 lam 경로 추가” → **이 레포 `repo.toml`에는 해당 항목 없음**
- “루트 `premake5.lua`에 lam 복사” → **앱 정의만 있음, 확장 데이터와 무관**
- `repo.toml`의 `[repo_package...]` `files_exclude`에 **`["data/**"]`** 가 있음 → `./repo.sh package` 로 zip 만들 때 **이름이 `data`인 경로가 통째로 빠질 수 있음** (루트 C 후 패키징 시 주의, §4.4)

---

## 1. 현재 프로젝트 구조 (실제 트리)

```text
kit-app-template_mine/                          ← OMNI_REPO_ROOT (= 프로젝트 루트)
├── repo.sh / repo.bat                          ← → tools/repoman/repoman.py
├── repo.toml                                   ← repo_build, repo_package (lam 항목 없음)
├── premake5.lua                                ← define_app("morph.editor.kit") 등
├── source/
│   ├── apps/
│   │   └── morph.editor.kit                    ← "morph.lam_control" = {}
│   └── extensions/
│       └── morph.lam_control/
│           ├── config/extension.toml           ← version 0.1.0, name morph.lam_control
│           ├── premake5.lua                    ← ★ 현재 docs + morph 만 링크
│           ├── docs/
│           └── morph/lam_control/*.py          ← 런타임 코드 (data 폴더 없음)
├── lam/                                        ← ★ 데이터 (확장 밖)
│   ├── csv/
│   ├── lam_event_sequences/
│   ├── usd/
│   └── lam_external_results/
└── _build/
    └── linux-x86_64/release/                   ← Jenkins CI 빌드 타깃 (ubuntu)
        ├── apps/                               ← morph.editor.kit 실행 스크립트
        └── exts/
            └── morph.lam_control-0.1.0/        ← 버전은 extension.toml 과 동일
                ├── config/                     ← (Kit가 소스에서 복사 — premake에 없어도 존재할 수 있음)
                ├── docs/
                └── morph/lam_control/
                └── (data/lam 없음 — 현재 premake 기준)
```

### 1.1 빌드 명령 (이 레포)

| 환경 | 명령 | 비고 |
|------|------|------|
| Linux / Jenkins | `./repo.sh build` | `.github-ci.yml` 과 동일 |
| Windows | `repo.bat build` | CI에도 있음 |
| `repo.toml` | `[repo_build.build] enabled = true` | |
| | `"platform:windows-x86_64".enabled = false` | **네이티브 Windows MSBuild 빌드는 꺼짐** — 로컬은 `repo.bat`·Kit 동작 방식은 팀 환경 확인 |

### 1.2 빌드 후 확장이 놓이는 **정확한 경로**

```text
${OMNI_REPO_ROOT}/_build/linux-x86_64/release/exts/morph.lam_control-0.1.0/
```

로컬 Windows에서 빌드했다면 플랫폼 폴더가 `windows-x86_64` 일 수 있음. **아래 명령에서 플랫폼만 바꿔서** 확인.

### 1.3 `morph.lam_control/premake5.lua` **현재 내용 (실측)**

```lua
-- source/extensions/morph.lam_control/premake5.lua
local ext = get_current_extension_info()
project_ext (ext)
repo_build.prebuild_link {
    { "docs", ext.target_dir.."/docs" },
    { "morph", ext.target_dir.."/morph" },
}
```

→ **`lam/` 은 링크되지 않음.** 그래서 Jenkins가 `exts` 만 배포하면 csv/json/usd 가 없음.

**비교 (같은 레포의 다른 확장):** `source/extensions/morph.measure_control_1/premake5.lua` 는 `{ "data", ext.target_dir.."/data" }` 가 **있음** — LAM 은 아직 없음.

---

## 2. 런타임이 경로를 찾는 방식 (코드 — 수정 전)

다음 **3개 파일**에 같은 `_find_lam_data_root()` 가 있습니다.

- `morph/lam_control/lam_window.py`
- `morph/lam_control/simulation_play.py`
- `morph/lam_control/lam_event_sequences.py`

**동작 요약**

1. `__file__` 기준으로 부모를 최대 12단계 올리며 `{부모}/lam` 디렉터리 검색
2. 찾으면 → 예: `{OMNI_REPO_ROOT}/lam`
3. 못 찾으면 → `exts/.../morph/lam_control` 위로 6단계 fallback + **빈 `lam/` 생성 시도**

| 데이터 | 코드 | 상대 경로 |
|--------|------|-----------|
| CSV | `get_lam_csv_dir()` | `lam/csv/` |
| JSON | `get_event_sequences_dir()` | `lam/lam_event_sequences/` |
| USD (UI·autoload) | `resolve_default_load_usd_path()` | `lam/usd/...` (프로젝트 루트 기준 join) |

**현재 `lam_window.py` (실측):**

```python
default_load_usd_path = "lam/usd/LAM_v02/FBX/Combine_01.usd"
load_automatically = True
```

→ 로컬에서 되는 조건: `{OMNI_REPO_ROOT}/lam/usd/LAM_v02/FBX/Combine_01.usd` **파일이 디스크에 있음**.

**환경변수 (이 레포에 이미 있음):**

- `LAM_SIM_CSV` — **CSV 파일 1개** 경로만 override (`simulation_play.py`). JSON·USD 는 대체 안 함.

---

## 3. 증상 ↔ 원인 (실무 정리)

| 증상 | 이 레포에서의 원인 |
|------|-------------------|
| Jenkins에서 csv/json/usd 전부 실패 | 배포물에 **`lam/` 없음** 또는 Git에 파일 없음 |
| 로컬만 됨 | 로컬 디스크에 `lam/` 있음 + 상위 탐색 성공 |
| UI CSV 경로가 `.../exts/.../../../../../../../lam/csv` | **빈 fallback `lam`** — 진짜 데이터 루트 아님 |
| 상대 USD 설정은 맞는데 서버만 실패 | **파일 미배포** (경로 문법 문제 아님) |
| `os.path` 때문 | **아님** — Linux에서도 동작. 문제는 **경로에 파일이 없음** |

---

## 4. 배포 루트 선택 (하나만 골라 순서대로 진행)

```text
                    ┌─────────────────────────────────────┐
                    │  목표: Jenkins에서 csv/json/usd OK   │
                    └─────────────────────────────────────┘
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        ▼                             ▼                             ▼
   [루트 A]                      [루트 C]                      [루트 D]
 Jenkins만                      premake +                    lam 을 확장 안으로
 lam/ 동봉                      data/lam 링크                 이전 + premake
 (코드 0)                       (빌드 1파일)                  (구조 변경)
        │                             │                             │
        └──────────────┬──────────────┴──────────────┬──────────────┘
                       ▼                             ▼
                  [루트 B]                      [루트 E]
                  Git LFS/커밋                  + Python 경로 SSOT
                  (A/C/D 공통)                  (별도 PR)
```

| 루트 | 난이도 | 수정하는 것 | 로컬 | Jenkins | 추천 |
|------|--------|-------------|------|---------|------|
| **A** | 낮음 | Jenkins deploy 스크립트만 | 유지 | `lam/` 수동 동봉 | **내일 1순위** |
| **B** | 낮음 | Git (+ LFS) | 유지 | checkout 시 파일 존재 | A와 함께 |
| **C** | 중간 | `morph.lam_control/premake5.lua` 만 | rebuild | exts 안 `data/lam` | exts만 배포 팀 |
| **D** | 중간~높음 | `lam/` → 확장 `data/lam` 이전 | 경로 습관 변경 | C와 동일 | 장기 구조 정리 |
| **E** | 높음 | `lam_data_paths.py` + premake | fallback 유지 | C/D + 코드 | 안정화 |

---

# 루트 A — 코드·premake 변경 없음 (Jenkins에 `lam/` 동봉)

**적합:** 지금 로컬이 정상이고, Jenkins Job 만 손댈 수 있을 때.

### A-1. 전제: Git에 배포할 파일이 있는지 (개발 PC)

레포 루트에서:

```bash
cd kit-app-template_mine   # OMNI_REPO_ROOT

git ls-files lam/csv/
git ls-files lam/lam_event_sequences/ | head -5
git ls-files "lam/usd/LAM_v02/FBX/Combine_01.usd"
```

- **출력 없음** → Jenkins `git checkout` 만으로는 **절대 안 올라감**. → **루트 B** 먼저.
- 로컬에만 있고 untracked → `git status lam/` 로 확인 후 커밋.

### A-2. Jenkins 서버 디렉터리 (이 레포 기준 권장 형태)

Kit 이 `_build/linux-x86_64/release` 에서 뜬다고 가정:

```text
DEPLOY_ROOT/                          ← 팀이 정한 설치 루트 (예: /opt/kit-app-template_mine)
├── _build/linux-x86_64/release/      ← ./repo.sh build 결과 (exts 포함)
└── lam/                              ← 레포의 lam/ 을 그대로 복사
    ├── csv/
    ├── lam_event_sequences/
    └── usd/LAM_v02/FBX/Combine_01.usd
```

**`lam/` 은 `_build` 와 형제**로 `DEPLOY_ROOT` 직하에 둡니다.
(코드가 exts → … → `_build` → `DEPLOY_ROOT` 올라가며 `DEPLOY_ROOT/lam` 을 찾음)

### A-3. Jenkins Pipeline 예시 (이 레포 경로 그대로)

```groovy
// 예: Jenkinsfile — 팀 Job 에 맞게 stage 이름만 조정
pipeline {
  agent any
  stages {
    stage('Checkout') {
      steps { checkout scm }
    }
    stage('Build Kit') {
      steps {
        sh './repo.sh build'
      }
    }
    stage('Stage Deploy') {
      steps {
        sh '''
          set -e
          DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/kit-app-template_mine}"
          rm -rf "${DEPLOY_ROOT}"
          mkdir -p "${DEPLOY_ROOT}"
          cp -a _build/linux-x86_64/release "${DEPLOY_ROOT}/_build/linux-x86_64/release"
          cp -a lam "${DEPLOY_ROOT}/lam"
          ls -la "${DEPLOY_ROOT}/lam/csv" | head
          test -f "${DEPLOY_ROOT}/lam/usd/LAM_v02/FBX/Combine_01.usd"
        '''
      }
    }
  }
}
```

**팀이 `exts` 만 rsync 하는 경우:** 위 `cp -a lam` 단계가 **없으면 루트 A 실패** — Job 에 반드시 추가.

### A-4. 검증 (Linux 서버)

```bash
DEPLOY_ROOT=/opt/kit-app-template_mine

test -d "$DEPLOY_ROOT/lam/csv" && echo OK_csv || echo FAIL_csv
test -d "$DEPLOY_ROOT/lam/lam_event_sequences" && echo OK_json || echo FAIL_json
test -f "$DEPLOY_ROOT/lam/usd/LAM_v02/FBX/Combine_01.usd" && echo OK_usd || echo FAIL_usd
```

### A-5. Kit 실행 · LAM 테스트

```bash
cd "$DEPLOY_ROOT/_build/linux-x86_64/release"
# 팀 실행 방식: 예) ./morph.editor.sh 또는 kit.sh + morph.editor.kit
ls apps/morph.editor*
```

| # | 확인 |
|---|------|
| 1 | LAM Window → CSV 디렉터리가 `$DEPLOY_ROOT/lam/csv` 를 가리키는지 |
| 2 | CSV 목록 1개 이상 |
| 3 | Play 후 `[LAM/SIMPLAY]` 치명적 파일 없음 |
| 4 | `vtm_chamber5_right_place.json` 등 이벤트 동작 |
| 5 | autoload 또는 Open `lam/usd/LAM_v02/FBX/Combine_01.usd` |

**실패 시:** UI CSV 경로가 `.../exts/morph.lam_control-0.1.0/.../../../../../../../lam` 이면 **A-2 트리** 미준수.

---

# 루트 B — Git에 데이터 올리기 (A/C/D 공통 전제)

**수정 파일:** 없음 (Git 작업만)

```bash
# 예: USD 가 큰 경우
git lfs install
git lfs track "lam/usd/**/*.usd"
git add .gitattributes lam/usd/LAM_v02/FBX/Combine_01.usd
git add lam/csv lam/lam_event_sequences
git commit -m "Add LAM deploy data for Jenkins"
git push
```

Jenkins agent 에 `git-lfs` + checkout 후 `git lfs pull` 필요.

---

# 루트 C — `morph.lam_control/premake5.lua` 만 수정 (exts에 `data/lam` 포함)

**적합:** Jenkins가 **`_build/.../exts/morph.lam_control-*` 만** 갱신하는 팀.
**수정 파일 1개:** `source/extensions/morph.lam_control/premake5.lua`
**수정 안 함:** `repo.toml`, 루트 `premake5.lua`

### C-1. premake 수정 (복사해서 적용)

`source/extensions/morph.lam_control/premake5.lua` 를 아래처럼 변경:

```lua
local ext = get_current_extension_info()
project_ext (ext)

-- 레포 루트 lam/ → 빌드 산출물 data/lam (소스 lam/ 은 그대로 SoT)
repo_build.prebuild_link {
    { "config", ext.target_dir .. "/config" },
    { "docs", ext.target_dir .. "/docs" },
    { "morph", ext.target_dir .. "/morph" },
    { "../../../lam", ext.target_dir .. "/data/lam" },
}
```

**상대경로 근거:**
`premake5.lua` 위치 = `source/extensions/morph.lam_control/`
→ `../../../lam` = `kit-app-template_mine/lam`

(`morph.lam_web_bridge/premake5.lua` 가 `config` 를 링크하는 것과 동일 패턴)

### C-2. 로컬에서 빌드·확인

```bash
./repo.sh build
# Windows: repo.bat build

EXT="_build/linux-x86_64/release/exts/morph.lam_control-0.1.0"
ls -la "$EXT/data/lam/csv" | head
ls -la "$EXT/data/lam/lam_event_sequences" | head
test -f "$EXT/data/lam/usd/LAM_v02/FBX/Combine_01.usd" && echo OK
```

**주의:** 현재 코드는 **`data/lam` 을 보지 않고** 레포 루트 `lam/` 만 탐색합니다.
→ premake 만 고치면 **디스크에는 exts 안에 복사되지만**, **런타임은 아직 루트 A 트리 또는 루트 E 코드**가 필요합니다.

| 단계 | 효과 |
|------|------|
| C만 | Jenkins 아티팩트에 **파일은 들어감**, **앱이 아직 안 읽을 수 있음** |
| C + A | exts 배포 + DEPLOY_ROOT/lam → **즉시 동작** |
| C + E | exts 만으로 **동작** (목표) |

### C-3. Jenkins

```bash
./repo.sh build
# 배포: _build/linux-x86_64/release/exts/... 만 올려도
# ext/data/lam/... 파일 존재 여부는 C-2 ls 로 확인
```

### C-4. `repo.toml` 패키징 주의 (이 레포 실측)

`repo.toml` 의 `[repo_package.packages.fat_package]` / `thin_package`:

```toml
files_exclude = [
    ...
    ["data/**"],
]
```

`./repo.sh package` 로 zip 만들 때 **`data/` 이름 하위가 제외**될 수 있습니다.
→ 루트 C 후 **zip 배포**를 쓰면 패키지 안에 `exts/.../data/lam` 이 빠졌는지 **반드시 unzip 후 확인**.
→ **exts 폴더 직접 rsync** 배포면 문제 없을 수 있음.

**`repo.toml` 을 LAM 때문에 고치려면** — 팀 인프라 담당과 **`files_exclude`에서 확장 data 만 예외** 할지 논의 (본 가이드에서는 필수 아님).

---

# 루트 D — `lam/` 을 확장 소스 트리 안으로 이전

**적합:** 데이터를 확장 레포 안에서만 관리하고 싶을 때.

### D-1. 폴더 이동 (개념)

```text
source/extensions/morph.lam_control/data/lam/
├── csv/
├── lam_event_sequences/
└── usd/
```

레포 루트 `lam/` 내용을 **복사 또는 이동** (팀 정책).
이후 SoT 는 `source/extensions/morph.lam_control/data/lam/`.

### D-2. premake5.lua

```lua
repo_build.prebuild_link {
    { "config", ext.target_dir .. "/config" },
    { "data", ext.target_dir .. "/data" },
    { "docs", ext.target_dir .. "/docs" },
    { "morph", ext.target_dir .. "/morph" },
}
```

(`measure_control_1` 과 동일 — `data` 폴더 통째 링크)

### D-3. 문서·습관

- `lam/README.md` → 확장 `data/lam/README.md` 로 안내 수정
- **루트 E** 없이는 런타임이 여전히 **레포 루트 `lam/`** 만 찾음 → 이전 후에도 **루트 E 또는 A** 필요

---

# 루트 E — Python 경로 SSOT (별도 PR, C/D 와 함께)

**신규 파일 (권장):** `morph/lam_control/lam_data_paths.py`

**조회 순서 (제안):**

1. `LAM_DATA_ROOT` 환경변수
2. `omni.kit.app` → `morph.lam_control-0.1.0` → `{ext}/data/lam`
3. 기존 `_find_lam_data_root()` 상위 탐색 → `{OMNI_REPO_ROOT}/lam`
4. (선택) `carb` `${root}/lam`

`lam_window.py`, `simulation_play.py`, `lam_event_sequences.py` 의 `_find_lam_data_root` 제거 후 import.

**이 레포에서 Extension ID:** `config/extension.toml` → `morph.lam_control`, 빌드 폴더명 `morph.lam_control-0.1.0`.

---

## 5. 루트별 “내일 할 일” 체크리스트

### 5.1 공통 (모든 루트)

- [ ] `lam_window.py` 의 `default_load_usd_path` 가 배포 브랜치에서
      `lam/usd/LAM_v02/FBX/Combine_01.usd` 인지 확인
- [ ] §B `git ls-files` 로 csv/json/usd tracked 확인
- [ ] 실패 시 LAM UI **CSV 디렉터리 한 줄** + `[LAM/WIN] autoload` 로그 저장

### 5.2 루트 A만

- [ ] Jenkins에 `cp -a lam "${DEPLOY_ROOT}/lam"` 추가
- [ ] §A-4 서버 `test -f` 3종
- [ ] §A-5 Kit 테스트 5항목

### 5.3 루트 C 추가

- [ ] `source/extensions/morph.lam_control/premake5.lua` §C-1 적용
- [ ] `./repo.sh build` 후 `exts/.../data/lam/...` ls
- [ ] (코드 E 전) 루트 A 트리도 유지하거나 루트 E 진행

### 5.4 루트 D 추가

- [ ] `data/lam` 이전 + premake §D-2
- [ ] `lam/README.md` 팀 공유

---

## 6. 데이터 종류별 — 이 레포 파일·함수 인덱스

| 종류 | 로컬 SoT (현재) | 읽는 코드 |
|------|-----------------|-----------|
| CSV | `lam/csv/*.csv` | `simulation_play.get_lam_csv_dir`, `list_lam_csv_paths`, env `LAM_SIM_CSV` |
| JSON | `lam/lam_event_sequences/<이벤트>.json` | `lam_event_sequences.event_json_path`, `build_steps_for_event` |
| USD | `lam/usd/...` | `lam_window.resolve_default_load_usd_path`, Open Master |
| 외부 결과 | `lam/lam_external_results/` | `lam_external_event_runner` |

---

## 7. 트러블슈팅 (이 레포 한정)

| 현상 | 확인 | 조치 |
|------|------|------|
| 로컬 OK / Jenkins NG | 서버 `ls DEPLOY_ROOT/lam` | 루트 A |
| exts만 배포 | `exts/.../data/lam` 유무 | 없으면 루트 C, 있어도 코드 E 전엔 A 필요 |
| package zip 에 data 없음 | unzip 후 `data/lam` | §C-4 `repo.toml` exclude |
| CSV만 env로 됨 | `LAM_SIM_CSV` | JSON/USD 는 A 또는 E |
| Windows 빌드 이상 | `repo.toml` windows build false | `repo.bat`·`_build/windows-*` 팀 확인 |

---

## 8. 권장 진행 순서 (실무)

1. **루트 B** — Git에 배포 파일 있는지 (10분)
2. **루트 A** — Jenkins `lam/` 동봉 + §A-4 검증 (당일 효과最大)
3. **§A-5** — csv · json · usd Launch 테스트
4. 팀이 **exts만 배포** → **루트 C** premake + **루트 E** 코드 (주 단위)
5. 구조 단순화 원하면 **루트 D** 검토

---

## 9. 문서·설정 파일 빠른 링크 (이 레포)

| 경로 |
|------|
| `lam/README.md` |
| `source/extensions/morph.lam_control/premake5.lua` |
| `source/extensions/morph.lam_control/config/extension.toml` |
| `source/apps/morph.editor.kit` |
| `repo.toml` (`[repo_build]`, `[repo_package]`) |
| `premake5.lua` (루트, 앱만) |
| `.github/workflows/.github-ci.yml` |
| `source/extensions/morph.lam_control/docs/LAM_Control_Maintenance_Guide.md` |

---

*문서 버전: 2026-05-19 — `kit-app-template_mine` 트리·`morph.lam_control` premake·`lam_window.py` default USD 경로 실측 반영.*
