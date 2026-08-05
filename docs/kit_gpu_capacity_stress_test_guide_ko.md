# Kit GPU 용량 스트레스 테스트 가이드

`morph.tbs_control_2` · `morph.lam_control_1` 각각에 대해  
**GPU 1개당 Kit을 몇 개까지 동시에 띄울 수 있는지**(≈ 동시 접속/세션 수)를 확인하는 절차입니다.

---

## 1. 한 줄 목표

| 질문 | 산출물 |
|------|--------|
| GPU 1장에서 확장 A를 N개 띄웠을 때 FPS·VRAM이 괜찮은가? | **권장 동시 Kit 수 N\*** |
| 로컬 창 여러 개 / 스트리밍 세션 여러 개 결과가 다른가? | 모드별 표 2장 |

권장 기본 **타겟 FPS = 30**.  
Pass/Fail 숫자는 팀 합의로 조정 (아래 §6는 **권장치**).

---

## 2. 측정 전에 맞출 것

### 2.1 하드웨어·드라이버 기록

한 번만 기록해 두면 나중에 비교 가능합니다.

| 항목 | 예 | 기록란 |
|------|----|--------|
| GPU 모델 | RTX A6000 / L40S … | |
| VRAM 총량 | 48 GB | |
| Driver | 535.xx | |
| OS | Windows 10/11 | |
| Kit / 앱 | `morph.editor` / `morph.editor_streaming` | |
| 해상도 | 1920×1080 (스트리밍 kit 기본과 맞춤 권장) | |
| 날짜·담당 | | |

### 2.2 확장 ON/OFF (`morph.editor.kit`)

테스트할 때는 **한 번에 하나의 제어 확장만** 켜는 것을 권장합니다.  
두 확장을 같이 켜면 “GPU당 인원”이 어떤 쪽 부하인지 구분이 안 됩니다.

파일: `source/apps/morph.editor.kit`

**LAM만 측정**

```toml
# "morph.tbs_control_2" = {}
"morph.lam_control_1" = {}
```

**TBS만 측정**

```toml
"morph.tbs_control_2" = {}
# "morph.lam_control_1" = {}
```

HyView 웹 연동·스트리밍까지 볼 때는 기존 문서대로:

- `omni.kit.livestream.messaging`
- `morph.hyview_messaging` (TBS) / LAM 쪽 메시징 설정

빌드·설정 반영 후 Kit을 다시 기동합니다. (`repo.bat build` 등 평소 절차)

### 2.3 FPS 표시

Kit Viewport에서 **Stats / FPS HUD**가 보이게 합니다.  
`morph.editor.kit`에 `omni.hydra.engine.stats`가 포함되어 있으면 Viewport 오버레이로 FPS·해상도를 볼 수 있습니다.

없으면 Viewport 메뉴에서 Display / HUD / Stats 계열을 켭니다.

---

## 3. 두 가지 테스트 모드

둘 다 진행하는 것을 권장합니다. **동시 접속 = 보통 스트리밍 세션 수**에 더 가깝습니다.

| 모드 | 의미 | 언제 쓰나 |
|------|------|-----------|
| **A. 로컬 멀티 프로세스** | PC에서 Kit `.kit`을 N개 프로세스 실행 | GPU·확장 순수 부하, 빠른 스크리닝 |
| **B. 스트리밍 멀티 세션** | `morph.editor_streaming.kit` + 웹/클라이언트 N연결 | **실제 동시 접속 인원**에 가까운 수치 |

스트리밍은 encode·네트워크가 추가되므로 **같은 N에서도 A보다 무겁게** 나오는 경우가 많습니다.  
용량 발표는 **모드 B를 우선**, A는 보조로 쓰면 됩니다.

---

## 4. 부하 시나리오 (유휴 + 재생)

각 N마다 **두 상태**를 각각 60초 이상 측정합니다.

| 시나리오 | 무엇을 하는지 | 왜 |
|----------|----------------|----|
| **Idle** | USD/마스터 로드 완료, 재생은 멈춤, Viewport만 돌아가게 | 세션 대기 시 VRAM·기본 RTX 부하 |
| **Play** | 각 인스턴스에서 **대표 시뮬 재생** (아래 §4.1) | 실제 사용 피크 |

### 4.1 Play 시 대표 작업 (확장별)

테스트마다 **같은 에셋·같은 배속·같은 화면 수**를 고정하세요.

| 확장 | Idle | Play (권장 고정안) |
|------|------|-------------------|
| `lam_control_1` | 합성 USD 로드, Viewport 표시 | 화면1(+화면2면 듀얼 고정) CSV/시뮬 **Play**, 배속 **1x**, 최소 60초 |
| `tbs_control_2` | 스테이지/포트 씬 로드 | EBS/시뮬 **재생** (대표 LOT 동선 또는 녹화 재생), 배속 **1x**, 최소 60초 |

듀얼 뷰포트(화면1·2)를 쓰는 구성이면 **Idle/Play 모두 듀얼을 켠 상태**로 측정해야 “동시 접속”과 맞습니다.  
싱글만 쓰는 현장은 싱글로 통일합니다.

---

## 5. 측정 방법

### 5.1 GPU (공통)

관리자 PowerShell이 아니어도 됩니다. **별도 창**에서 1초 간격 로깅:

```powershell
# 예: docs/_gpu_logs 폴더를 만들어 두고 실행
New-Item -ItemType Directory -Force -Path "docs\_gpu_logs" | Out-Null
$out = "docs\_gpu_logs\gpu_{0:yyyyMMdd_HHmmss}.csv" -f (Get-Date)
nvidia-smi --query-gpu=timestamp,index,name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu --format=csv -l 1 | Tee-Object -FilePath $out
```

중지: `Ctrl+C`

볼 것:

- `utilization.gpu` (%) — GPU 연산 점유
- `memory.used` / `memory.total` — VRAM
- `temperature.gpu` — 과열 여부

### 5.2 FPS

각 Kit 창(또는 스트림 뷰어)에서:

- 평균 FPS (눈으로 5~10초 평균, 가능하면 메모)
- 최저 FPS (버벅임이 있으면 기록)

가능하면 Viewport Stats의 FPS를 **Idle / Play 각각** 기록합니다.

### 5.3 로컬 멀티 프로세스 (모드 A) — 따라 하기

1. §2.2에서 **측정할 확장 하나만** ON.
2. `nvidia-smi` 로깅 시작.
3. Kit을 **1개** 실행 → 에셋 Idle 안정 → 60초 기록 → Play 60초 기록.
4. 같은 설정으로 Kit을 **하나 더** 실행 (프로세스 N = 2, 3, …).
5. N을 늘릴 때마다 Idle 60초 + Play 60초를 기록.
6. §6 권장치에서 **Fail**이 나는 최소 N을 찾으면, 권장 동시 수는 **N−1**.

실행 예 (환경에 맞게 경로만 수정):

```bat
rem 프로젝트 루트에서 (평소 Kit 실행 방식과 동일하게)
repo.bat launch morph.editor.kit
```

또는 빌드된 실행 파일을 여러 번 실행합니다.  
**창마다 같은 사용자 설정을 쓰므로**, 가능하면 에셋 경로·해상도를 통일합니다.

> Tip: 창을 최소화하면 Kit이 스로틀될 수 있습니다. 측정 중에는 **창을 보이거나**, 스트리밍 headless 정책을 문서화하세요.

### 5.4 스트리밍 멀티 세션 (모드 B) — 따라 하기

1. `morph.editor_streaming.kit` 기동 (평소 스트리밍 배포와 동일).
2. 시그널링/포트·웹 클라이언트는 기존 문서 참고:
   - `docs/tbs_control_2_web_prerun_settings_spec_ko.md` (§ 스트리밍·포트)
   - `docs/lam_control_1_web_connection_spec_ko.md`
3. **세션 1개** 연결 → Idle 60초 → Play 60초.
4. **세션을 하나씩 추가** (같은 GPU의 Kit 인스턴스/컨테이너가 세션당 1개인지, 멀티유저 공인스턴스인지는 배포 아키텍처에 따름).

**중요 — “동시 접속” 정의**

| 배포 형태 | “1명”의 의미 |
|-----------|----------------|
| **세션당 Kit 프로세스 1개** (일반적) | Kit N개 = 동시 N명 |
| **한 Kit에 여러 시청자** | 인코더·대역폭이 병목. 이 가이드의 N은 **Kit 프로세스 수**로 두고, 시청자 수는 별도 표 |

현장 설계가 “유저 1명 = Kit 1프로세스”이면 이 문서의 N이 곧 동시접속 수입니다.

N 증가 방법은 모드 A와 동일: Fail 직전 N−1을 권장값으로 기록.

---

## 6. 판정 기준 (권장치 — 팀에서 조정)

기본안 (문서 작성 시점 권장):

| 항목 | Pass (권장) | Fail 신호 |
|------|-------------|-----------|
| Viewport / 스트림 평균 FPS | **≥ 30** | 지속 25 미만, 또는 잦은 끊김 |
| VRAM | **총량의 ≤ 약 70~80%** | OOM, 프로세스 kill, 급격한 스와핑 |
| GPU Util | 참고용 (Play 시 높을 수 있음) | 장시간 100% + FPS 붕괴 |
| 안정성 | 60초 측정 중 크래시 없음 | 크래시·블랙스크린 |

팀에서 더 보수적으로 가려면 FPS 35, VRAM 65% 등으로 올리면 됩니다.  
**발표 수치에는 사용한 Pass 기준을 꼭 같이 적습니다.**

---

## 7. 결과 기록표 (복사해서 사용)

### 7.1 확장: `morph.lam_control_1`

**모드:** □ A 로컬  □ B 스트리밍  
**구성:** □ 싱글 뷰  □ 듀얼 뷰 / 해상도 ______  
**Pass 기준:** FPS≥___ / VRAM≤___%

| N (동시 Kit) | Idle FPS | Idle VRAM(GB) | Idle GPU% | Play FPS | Play VRAM(GB) | Play GPU% | Pass? | 비고 |
|--------------|----------|---------------|-----------|----------|---------------|-----------|-------|------|
| 1 | | | | | | | | |
| 2 | | | | | | | | |
| 3 | | | | | | | | |
| 4 | | | | | | | | |
| … | | | | | | | | |

**권장 N\* (LAM) = ______**  
(최대 Pass N, 또는 Fail 직전)

### 7.2 확장: `morph.tbs_control_2`

**모드:** □ A 로컬  □ B 스트리밍  
**구성:** □ 싱글  □ 멀티 스플릿 / 해상도 ______  
**Pass 기준:** (위와 동일하게 명시)

| N | Idle FPS | Idle VRAM | Idle GPU% | Play FPS | Play VRAM | Play GPU% | Pass? | 비고 |
|---|----------|-----------|-----------|----------|-----------|-----------|-------|------|
| 1 | | | | | | | | |
| 2 | | | | | | | | |
| … | | | | | | | | |

**권장 N\* (TBS) = ______**

### 7.3 요약 (경영/기획 공유용)

| 확장 | 모드 | GPU | 타겟 FPS | 권장 동시 수 N\* | 비고 |
|------|------|-----|----------|-----------------|------|
| lam_control_1 | B 스트리밍 | (모델) | 30 | | |
| tbs_control_2 | B 스트리밍 | (모델) | 30 | | |
| lam_control_1 | A 로컬 | | 30 | | 참고 |
| tbs_control_2 | A 로컬 | | 30 | | 참고 |

---

## 8. 권장 진행 순서 (체크리스트)

- [ ] GPU·드라이버·해상도 기록 (§2.1)
- [ ] `morph.editor.kit`에서 **한 확장만** ON (§2.2)
- [ ] FPS HUD 확인 (§2.3)
- [ ] `nvidia-smi` CSV 로깅 시작 (§5.1)
- [ ] **LAM** Idle→Play, N=1… (§4, §5, §7.1)
- [ ] kit 설정 TBS로 전환 후 **TBS** 동일 절차 (§7.2)
- [ ] 가능하면 **모드 B(스트리밍)** 를 동일 표로 한 번 더
- [ ] Fail 직전 N−1을 N\*로 확정, Pass 기준 문구와 함께 공유 (§6, §7.3)

---

## 9. 자주 나는 함정

| 함정 | 결과 | 대응 |
|------|------|------|
| TBS+LAM 동시 ON | 부하가 섞여 N\* 의미 없음 | 확장 분리 측정 |
| Idle만 측정 | 실제 사용 시 과대 평가 | Play 필수 |
| 창 최소화 / 백그라운드 스로틀 | FPS가 이상하게 좋게 나옴 | 창 표시 유지 또는 headless 정책 명시 |
| 인스턴스마다 다른 씬 | N 비교 불가 | 동일 USD·배속·화면 수 |
| “스트림 시청자 수”와 “Kit 수” 혼동 | 동시접속 과대/과소 | §5.4 정의 확인 |
| VRAM만 보고 GPU Util 무시 | FPS 미달인데 Pass 처리 | FPS 우선 |

---

## 10. 관련 파일·문서

| 경로 | 용도 |
|------|------|
| `source/apps/morph.editor.kit` | 확장 ON/OFF |
| `source/apps/morph.editor_streaming.kit` | 스트리밍 앱·해상도 |
| `docs/lam_control_1_web_connection_spec_ko.md` | LAM 웹·스트리밍 |
| `docs/tbs_control_2_web_prerun_settings_spec_ko.md` | TBS 스트리밍·메시징 |
| `docs/TBS_웹연동_이벤트_명세_ko.md` | TBS HyView 이벤트 |

---

## 11. 다음에 자동화할 여지 (선택)

지금은 **수동 절차로 재현 가능**한 수준입니다. 이후 필요하면:

- N개 프로세스 일괄 기동 스크립트
- FPS를 로그/텔레메트리로 CSV 추출
- CI GPU 러너에서의 smoke (N=1만)

를 추가하면 됩니다. 1차 용량 확인에는 본 문서 표만으로 충분합니다.
