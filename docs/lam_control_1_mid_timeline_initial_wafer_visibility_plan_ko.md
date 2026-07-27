# LAM Control 1 — 중간 타임라인 초기 웨이퍼 배치 (설계·작업 메모)

> **상태:** v0.1 — **미구현** (원인·방안 파악 완료)  
> **대상:** `morph.lam_control_1` CSV / Federation POST / Simulation GET 재생  
> **원칙:** **재생 t=0 웨이퍼 visibility만** 조정. 플랜·이송·타이밍·파싱은 **변경 금지**.  
> **관련:** [웹 연동 설계](lam_control_1_web_connection_spec_ko.md) · [Federation GET camelCase](source/extensions/morph.lam_control_1/docs/lam_control_federation_get_camelcase_field_guide_ko.md)

---

## 0. 한 줄 요약

API/CSV 데이터가 **FOUP pick부터가 아니라 PM·챔버 중간**부터 올 때, 재생 시작 시 웨이퍼가 **FOUP에만 보이고** 실제 첫 pick 슬롯은 **비어 있어** pick 순간 **갑자기 나타나는(pop-in)** 문제가 있다.  
**가장 심플한 해결:** dwell만 보고 「투어별 첫 dwell이 FOUP/ATM pick 경로가 아니면」 t=0에 해당 모듈 슬롯 show + 대응 FOUP 슬롯 hide.  
**override 0건이면 기존 코드 경로를 그대로** 유지해 사이드 이펙트를 막는다.

---

## 1. 배경·증상

### 1.1 사용자가 보는 현상

- 타임라인이 **공정 중간**(예: PM1, PM2, 챔버)부터 시작하는 데이터로 재생
- t=0에는 해당 PM/챔버 슬롯에 웨이퍼가 **없어 보임**
- 첫 이송이 그 슬롯에서 **pick**일 때, 웨이퍼가 **갑자기 출현**

### 1.2 데이터 출처와 무관

다음 경로는 모두 **동일한 재생 파이프라인**을 탄다.

| 경로 | dwell 생성 | 캐시 |
|------|------------|------|
| 로컬 CSV | CSV 파서 | `build_and_cache_csv_playback` |
| Federation POST | `lam_api_timeline_parser` | `build_and_cache_from_dwells` |
| Simulation GET (`execId`) | 동일 파서 | `build_and_cache_from_dwells` |

→ 문제는 **파서가 아니라 재생 시작 시 3D wafer prim visibility** 에 있다.

---

## 2. 원인 분석 (현재 코드)

### 2.1 재생 시작 시 고정 visibility

`simulation_play.py` — `apply_csv_play_initial_wafer_visibility_on_stage()`:

- **FOUP1~3 × 슬롯 1~25** → wafer prim **전부 show**
- **그 외 모든 슬롯**(PM, 챔버, ATM/VTM 팔 등) → **전부 hide**

상수: `_CSV_PLAY_INITIAL_VISIBLE_FOUP_SLOT_KEYS` (약 4629행)

**완전 투어**(첫 dwell = ATM Arm → FOUP pick)에서는 FOUP pick 블록의 이벤트 JSON이 pick 시 FOUP hide / 팔 show를 처리하므로 정상 동작한다.

### 2.2 플랜 빌드는 FOUP pick 블록을 조건부로만 생성

`build_csv_playback_plan()` (약 2163행):

- 웨이퍼 투어 `(lot_id, cassette_slot)` 단위로 `_group_dwell_tours()` 그룹
- **`first.slot_key == LOGICAL_SLOT_ATM_ARM` 일 때만** FOUP pick + aligner 합성 블록 생성
- 중간 시작 데이터는 첫 dwell이 PM/챔버 등 → **FOUP pick 블록 없음**
- 연속 dwell 사이 이송만 `build_steps_for_dwell_transfer(prev, curr)` 로 생성

→ t=0 visibility(FOUP만 show)와 첫 이송(pick from PM)이 **불일치**.

### 2.3 호출 지점 (현재 dwells 미전달)

| 함수 | 위치(대략) | `reset_wafer_visibility` 시 |
|------|------------|------------------------------|
| `run_csv_timed_playback` | ~5234 | `apply_csv_play_initial_wafer_visibility_for_screen(screen, kit_ext=...)` |
| `_run_csv_timed_playback_process_only` | ~4419 | 동일 |
| `reset_csv_play_stop_initial_state` | ~5000 | `apply_csv_play_initial_wafer_visibility_on_stage(st, screen=si)` — **정지(초기화)는 FOUP baseline 유지가 맞음** |

`CachedCsvPlayback`에는 이미 `dwells`가 들어 있으나 (~451행), visibility 호출에 **넘기지 않음**.

---

## 3. 도메인 개념 (FOUP·투어)

| 개념 | 정의 |
|------|------|
| **FOUP 개수** | 서로 다른 `lot_id` 개수 (최대 3, `build_lot_id_to_foup_index()`) |
| **웨이퍼 투어 키** | `(lot_id, cassette_slot)` |
| **`cassette_slot`** | FOUP 내 웨이퍼 번호 (1~25) |
| **FOUP 슬롯 키** | `foup{foup_index}_{cassette_slot}` (예: `foup1_7`) |
| **ATM Arm 논리 슬롯** | `LOGICAL:ATM_ARM` |
| **`cassette_id`** | 파서에서 **미사용** |

투어 그룹핑: `_group_dwell_tours()` — `start_sec` 기준 정렬 후 첫 dwell이 투어 시작점.

---

## 4. 목표 (비기능 요구)

1. 중간 시작 데이터: t=0에 **데이터상 웨이퍼가 있는 모듈 슬롯**에 wafer show, **대응 FOUP 슬롯** hide
2. FOUP pick부터 시작하는 **기존 전체 투어**: 동작 **100% 동일** (회귀 없음)
3. 변경 범위: **초기 visibility 한 계층** — 플랜·이송 JSON·타이밍·파서 **수정 금지**
4. CSV / Federation / GET **동일 dwell → 동일 초기 상태**

---

## 5. 제안 방안 (1단계 — 심플)

### 5.1 감지 규칙: 투어별 **첫 dwell**만 검사

각 `(lot_id, cassette_slot)` 투어에 대해 `tour[0].slot_key` 확인:

| 첫 dwell `slot_key` | 처리 |
|---------------------|------|
| `LOGICAL:ATM_ARM` | **기존과 동일** — FOUP pick 블록·이벤트가 visibility 처리 |
| `foup{n}_{m}` 형태 | **기존과 동일** — 기본 FOUP 전체 show와 일치 |
| **그 외** (PM, 챔버, Aligner, VTM 팔 등) | **중간 시작** → override 추가 |

**override 내용 (투어당 1건):**

- `show_slot_key` = `first.slot_key`
- `hide_foup_slot_key` = `foup{first.foup_index}_{cassette_slot}`

### 5.2 「place 없이 pick만」을 별도 역추적하지 않는 이유

이송은 **연속 dwell 사이**만 생성된다. 첫 이송의 `prev`는 항상 **첫 dwell**.  
첫 dwell 위치에 웨이퍼가 없으면 **첫 pick부터** 깨진다.  
→ 「타임라인 시작점이 FOUP/ATM pick 경로가 아니다」로 **한 번에** 커버.

### 5.3 사이드 이펙트 방지 원칙

1. **override 목록이 비어 있으면** → 기존 `apply_csv_play_initial_wafer_visibility_on_stage` **본문 그대로** (early return)
2. override가 있을 때만:
   - 기존 FOUP show / 기타 hide 수행 후
   - override의 FOUP 슬롯만 **재 hide**
   - override의 모듈 슬롯 wafer prim만 **show**
3. `build_csv_playback_plan`, `build_steps_for_dwell_transfer`, dwell 파서 **미수정**
4. synthetic place 스텝·타임라인 주입 **하지 않음**

### 5.4 wafer 라벨 tracker

기존 visibility 함수 끝에서 `tracker.reset_foup_baseline(wafer_map, stage=stage)` 호출 (~4717행).  
override로 FOUP 슬롯을 hide한 경우, baseline도 **hide된 FOUP**을 반영해야 라벨 번호가 어긋나지 않을 수 있음 → 구현 시 **동일 함수 안에서** baseline 갱신 순서 확인.

---

## 6. 구현 스케치 (다음 작업용)

### 6.1 신규 함수 (제안)

파일: `simulation_play.py` 또는 `lam_csv_initial_wafer_state.py` (재생 초기 상태 전용)

```python
@dataclass(frozen=True)
class MidprocessInitialWaferOverride:
    lot_id: str
    cassette_slot: int
    show_slot_key: str
    hide_foup_slot_key: str

def infer_midprocess_initial_wafer_overrides(
    dwells: List[DwellRecord],
) -> List[MidprocessInitialWaferOverride]:
    """첫 dwell이 FOUP/ATM pick 경로가 아닌 투어만 반환. 없으면 []."""

def _is_foup_slot_key(slot_key: str) -> bool:
    """foup{n}_{m} 패턴 (n=1..3, m=1..25)."""
```

**로직 요약:**

```
for (lot_id, cassette_slot), tour in _group_dwell_tours(dwells):
    first = tour[0]
    sk = first.slot_key
    if sk == LOGICAL_SLOT_ATM_ARM:
        continue
    if _is_foup_slot_key(sk):
        continue
    append override(show=sk, hide=f"foup{first.foup_index}_{cassette_slot}")
return overrides
```

### 6.2 기존 함수 시그니처 확장

```python
def apply_csv_play_initial_wafer_visibility_on_stage(
    stage: Any,
    *,
    screen: int = 1,
    dwells: Optional[List[DwellRecord]] = None,
) -> Tuple[int, int]:
    overrides = infer_midprocess_initial_wafer_overrides(dwells) if dwells else []
    if not overrides:
        ...  # 기존 코드 블록 변경 없이 유지
    else:
        ...  # 기존 loop + override 적용
```

```python
def apply_csv_play_initial_wafer_visibility_for_screen(
    screen: int,
    *,
    wait: bool = True,
    kit_ext: Any = None,
    dwells: Optional[List[DwellRecord]] = None,
) -> None:
    ... apply_csv_play_initial_wafer_visibility_on_stage(st, screen=si, dwells=dwells)
```

### 6.3 호출측 연동

| 호출처 | 변경 |
|--------|------|
| `run_csv_timed_playback` | `prepared`/`cached`에서 `dwells=cached.dwells` 전달 |
| `_run_csv_timed_playback_process_only` | 동일 |
| `run_simulation_from_csv` | `prepared.dwells` 있으면 전달 |
| `reset_csv_play_stop_initial_state` | **dwells 전달하지 않음** (FOUP baseline 리셋 유지) |

Federation prerun → `run_simulation_from_csv(prepared=cached)` 경로에서 `cached.dwells` 이미 존재 (`build_and_cache_from_dwells`).

### 6.4 `CachedCsvPlayback` 구조 변경

**불필요** — play 시점에 `cached.dwells`로 추론하면 됨.  
(선택) 빌드 시 1회 계산해 `midprocess_overrides` 필드 캐시 — 성능·디버그용, 필수 아님.

---

## 7. 2단계 (나중에, 필요 시만)

1단계로 안 잡히는 경우:

- 투어 **중간**에, 이전 dwell에 없던 슬롯에서 pick (연속 dwell 모델에서는 드묾)

그때 `build_csv_playback_plan`과 동일한 transfer dry-run으로  
「pick 대상 slot에 대한 직전 dwell 없음」 추가 검출.  
**1단계 구현·회귀 테스트 후**에만 검토.

### 7.1 이어서 재생 (`resume_from_csv_sec > 0`)

현재도 `reset_wafer_visibility=True`면 **t=0 FOUP baseline**으로 리셋 후 resume 시점 블록부터 실행.  
중간 resume 시 **resume 시점의 웨이퍼 위치**와 visibility 불일치 가능 — **별도 이슈**.  
본 작업 1단계는 **t=0부터 재생** 케이스 우선.

---

## 8. 검증 시나리오 (회귀 체크리스트)

| # | 케이스 | 기대 |
|---|--------|------|
| R1 | FOUP부터 시작 (첫 dwell `LOGICAL:ATM_ARM`) | override 0 → **기존과 픽셀·타이밍 동일** |
| R2 | 중간 시작 (첫 dwell PM1/PM2/챔버) | t=0 해당 슬롯 show, FOUP hide, **첫 pick pop-in 없음** |
| R3 | 첫 dwell이 `foup1_5` 등 FOUP 슬롯 | override 0 → 기존과 동일 |
| R4 | 웨이퍼 2~3장, 각각 다른 중간 슬롯에서 시작 | 투어별 override 각각 적용 |
| R5 | Federation POST prerun → Play | R2와 동일 |
| R6 | Simulation GET prerun → Play | R2와 동일 |
| R7 | 로컬 CSV (기존 전체 투어 파일) | R1 |
| R8 | 듀얼 화면 (screen1/2 각각 다른 dwell) | 화면별 `dwells`로 독립 적용 |
| R9 | 정지(초기화) | FOUP baseline (dwells 없음) — **기존과 동일** |
| R10 | 공정만보기 (`process_only`) | visibility + 첫 블록 동작 |

---

## 9. 관련 소스 위치

| 파일 | 역할 |
|------|------|
| `simulation_play.py` | `DwellRecord`, `_group_dwell_tours`, `build_csv_playback_plan`, `CachedCsvPlayback`, 초기 visibility, `run_csv_timed_playback` |
| `lam_api_timeline_parser.py` | API → dwell, `build_lot_id_to_foup_index` |
| `lam_federation_pipeline.py` | prerun, `build_and_cache_from_dwells` 연결 |
| `lam_wafer_prim_paths.py` | `LOGICAL_SLOT_ATM_ARM`, 슬롯 키·prim 경로 |
| `lam_wafer_viewport_labels.py` | `reset_foup_baseline` |

### 9.1 핵심 심볼

- `apply_csv_play_initial_wafer_visibility_on_stage` (~4664)
- `apply_csv_play_initial_wafer_visibility_for_screen` (~4883)
- `build_csv_playback_plan` (~2163)
- `build_and_cache_from_dwells` (~2335)
- `_group_dwell_tours` (~501)

---

## 10. 작업 순서 (TODO)

- [ ] `infer_midprocess_initial_wafer_overrides(dwells)` 구현 + 단위 테스트(순수 함수, dwell 리스트 fixture)
- [ ] `apply_csv_play_initial_wafer_visibility_on_stage(..., dwells=)` — override 0건 분기로 기존 경로 보존
- [ ] `apply_csv_play_initial_wafer_visibility_for_screen(..., dwells=)` 전달
- [ ] `run_csv_timed_playback` / `_run_csv_timed_playback_process_only` / `run_simulation_from_csv` 에서 `cached.dwells` 연결
- [ ] wafer label `reset_foup_baseline` 순서 확인
- [ ] R1~R10 수동 검증 (특히 R1 회귀)
- [ ] (선택) Federation 테스트 창 GET 파싱/시뮬로 중간 시작 JSON 재현

---

## 11. 변경하지 않을 것 (명시)

- dwell 파싱 규칙 (CSV / Federation / GET)
- `build_csv_playback_plan` 블록·시간·FOUP pick 조건
- `build_steps_for_dwell_transfer` 및 이벤트 JSON
- Federation GET `execId` 분기 (이미 구현 완료)
- 재생 배속·barrier·듀얼 화면 동시 시작 로직

---

## 12. 변경 이력

| 날짜 | 버전 | 내용 |
|------|------|------|
| 2026-07-27 | v0.1 | 원인 분석·1단계 방안·회귀 원칙 문서화 (구현 전) |
