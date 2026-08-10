# TBS Control 2 — LOT별 OHT↔EP 고정 공정시간 (Fix 공정 입력) 요구사항

> **상태:** 최종 사양 (구현 대기)  
> **문서:** 피드백 반영 완료 — 이 문서 기준으로 코드 수정 진행

---

## 1. 요약

| 항목 | 사양 |
|------|------|
| 기능 | fix 공정 입력 창에 텍스트로 LOT별 **OHT→EP(초)**, **EP→OHT(초)** 고정값 지정 |
| 입력 상한 | `lot_count`와 동일 (예: lot 30 → 최대 30줄 의미; 초과 행은 **무시**) |
| 부분 입력 | N줄 입력 시 `LOT_001`~`LOT_N`만 fix, 나머지는 UI 랜덤 범위 |
| 빈 창 | 전 LOT 랜덤 (현행과 동일) |
| 적용 시점 | **시작** 클릭 시 백그라운드 프리런 (유일한 시뮬 경로) |
| 저장 | **없음** — 매 시뮬 시작 시 창 텍스트 유무·내용만 판단 |
| 분할 화면 | fix 데이터 **공통 1벌** |
| 창 표시 | 2D 패널 **「창 표시」** 기존 5개 창과 **동일 구조**로 6번째 항목 추가, 기본 `True` |

---

## 2. 입력 형식

### 2.1 문법

```text
<라벨>, <oht_ep_sec>, <ep_oht_sec>
```

- **구분자:** 쉼표(`,`)
- **라벨(1열):** 메모용. 엔진 내부 ID 변경 없음.
- **초(2·3열):** 정수가 일반적이나 **소수 허용** (예: `586.5`)
- **빈 줄:** 스킵
- **잘못된 행** (열 부족·숫자 아님·0 이하 등): 해당 행만 **랜덤 fallback**
- **행 수 > lot_count:** 초과 행 **무시**

### 2.2 예시

```text
tacny80, 586, 143
te2bm15, 197, 159
ts2hc72, 669, 226
```

`lot_count = 30`, 위 3줄만 있을 때:

| LOT | OHT→EP | EP→OHT | 비고 |
|-----|--------|--------|------|
| `LOT_001` | 586 fix | 143 fix | 라벨 `tacny80` |
| `LOT_002` | 197 fix | 159 fix | 라벨 `te2bm15` |
| `LOT_003` | 669 fix | 226 fix | 라벨 `ts2hc72` |
| `LOT_004`~`LOT_030` | UI 범위 랜덤 | UI 범위 랜덤 | |

---

## 3. LOT ID 표시 규칙 (전 UI 공통)

| 조건 | 표시 문자열 |
|------|-------------|
| fix 매핑 **없음** | `LOT_001` (현행과 동일) |
| fix 매핑 **있음** (N번째 줄에 유효한 fix) | `LOT_001(tacny80)` — **괄호 안에 1열 라벨** |

- 엔진 내부 키·이벤트 매칭용 ID는 기존 `LOT_{seq:03d}` **유지** (로직·JSON 키는 변경 없음).
- **사용자에게 보이는 모든 출력**에 동일한 표시 규칙 적용:

| 적용 대상 |
|-----------|
| 타임테이블 (프리런) |
| 진행현황 패널 |
| 시뮬 모니터 |
| 콘솔 로그 (`print` / `[SIM EVENT …]` 등) |
| 기타 `lot_id`를 노출하는 HUD·footer |

- 구현: `lot_id` (원본) + `format_lot_id_display(lot_id, fix_label)` 헬퍼로 **단일 진실 공급원** 유지.

---

## 4. 공정 범위

### 4.1 Fix 대상

| UI 명칭 | 엔진 풀 | 적용 경로 |
|---------|---------|-----------|
| **OHT→EP** | `oht_to_bp1` | OHT → EP **직접 투입** + OHT → **IN/OUT** 안착 (**동일 고정 초**) |
| **EP→OHT** | `ep_to_oht` | EP → OHT 회수 이동 |

### 4.2 Fix 비대상 (랜덤 유지)

- IN→BP, BP→EP
- LOT 생성 간격 (`lot_spawn_interval`)
- Pickup 간격 (`pickup_event_interval`)
- FOUP 공정 시간 (`foup_process`)

---

## 5. 시뮬 흐름

```
fix 공정 입력 창 (세션 중 편집, 디스크 저장 없음)
        │
        ▼
[시작] — 창 텍스트 스냅샷 (있음/없음·파싱 결과)
        │
        ▼
백그라운드 프리런
  · 유효 fix 행 k → LOT_k 의 oht_to_bp1 / ep_to_oht presample 고정
  · 나머지 LOT → 기존 min~max 랜덤
  · 잘못된 행 → 해당 LOT만 랜덤
        │
        ▼
타임테이블·재생 (분할 화면 공통)
```

- **프리런 없는 라이브 시뮬 경로 없음** — fix는 항상 프리런 presample 단계에서 반영.

---

## 6. UI — fix 공정 입력 창

### 6.1 창

| 항목 | 값 |
|------|-----|
| 제목 | `fix 공정 입력` |
| 본문 | 멀티라인 텍스트 입력 (`StringField` / `TextEditor` 등) |
| 힌트(선택) | 형식 예: `이름, oht초, ep_oht초` |

### 6.2 2D 패널 「창 표시」

기존 5개(USD Load, 시퀀스, 타임테이블, 시뮬 모니터, EBS 제어창)와 **동일 패턴**으로 6번째 추가:

```python
# ebs_control_panel_ui._AUX_KIT_WINDOW_SPECS 에 추가 예시
("_ui_show_tbs_fix_proc_model", "fix 공정 입력", "fix_proc")
```

- `init_sim_control_models()` 루프 → `SimpleBoolModel(True)` (**기본 표시**)
- `_resolve_aux_kit_window(ext, "fix_proc")` → `ext._fix_proc_window`
- `sync_aux_kit_window_visibility()` 연동

---

## 7. 타임테이블·로그 — `{}` fix 메타 표시

fix가 적용된 공정 구간은 타임테이블 한 줄(및 동일 톤 로그)에 **`{}` 메타 블록**을 추가한다.

### 7.1 표시 원칙

- **fix가 적용된 step** (OHT→EP / EP→OHT 이동 시간이 presample fix에서 온 경우)에만 `{}` 표기.
- 랜덤 구간은 기존 표기 유지 (`공정시간: …` 등), `{}` **생략**.
- JSON·진행현황 등의 `lot_id` 필드는 §3 (`LOT_001(tacny80)`).
- `{}` 안에는 **LOT ID와 1열 라벨을 함께** 넣어 fix 매핑임을 한눈에 알 수 있게 한다.

### 7.2 `{}` 형식 (확정)

**키 이름 (통일)**

| 키 | 의미 |
|----|------|
| `lot` | `LOT_NNN(라벨)` — fix 매핑 시 라벨 포함; 매핑 없으면 `LOT_NNN` 만 |
| `fix_oht_ep` | OHT→EP( IN/OUT 포함) 고정 초 |
| `fix_ep_oht` | EP→OHT 고정 초 |

**OHT→EP (IN/OUT 포함) fix 적용 시**

```text
{"t":12.3,"lot_id":"LOT_001(tacny80)","event":"ARRIVED",...}  {lot:LOT_001(tacny80),fix_oht_ep:586}  공정시간: 586.0s  ...
```

**EP→OHT fix 적용 시**

```text
{"t":45.6,"lot_id":"LOT_001(tacny80)","event":"REMOVED",...}  {lot:LOT_001(tacny80),fix_ep_oht:143}  공정시간: 143.0s  ...
```

- `{}` 위치: JSON 블록 **뒤**, `공정시간:` 문구 **앞** (공백 2칸 구분 유지).
- 소수 fix 값: `{lot:LOT_001(tacny80),fix_oht_ep:586.5}` 처럼 실제 값 그대로.
- **한 step에 해당 fix 키만** 포함 (OHT→EP step → `fix_oht_ep`, EP→OHT step → `fix_ep_oht`).
- fix 매핑이 없는 LOT(랜덤 구간)에는 `{}` 블록 자체를 **출력하지 않음**.

### 7.3 구현 위치 (예상)

| 단계 | 파일 | 내용 |
|------|------|------|
| presample 시 fix 메타 보관 | `simulation_engine.py` | LOT index별 fix 라벨·초값 |
| 이벤트/타임라인 row | `control_sim_prerun_playback.py` | row dict에 `fix_oht_ep` / `fix_ep_oht` / `lot_id_display` |
| 표시 문자열 | `format_timetable_display_line()` | `{}` 블록 조합 |

---

## 8. 아키텍처

```
┌─────────────────────────┐
│ FixProcInputWindow      │  ext._fix_proc_window
│  (텍스트, 비영속)        │
└───────────┬─────────────┘
            │ Start 시 read + parse
            ▼
┌─────────────────────────┐
│ ext (전역 1벌)           │  parsed rows + labels by lot index
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ simulation_engine       │
│ _presample_fill         │  index≤N: fix / else: rand / bad row: rand
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ prerun → timetable UI   │  lot_id(tacny80), {fix_oht_ep:586}, ...
└─────────────────────────┘
```

**원칙**

- 시뮬 이벤트 순서·FOUP·JSON 재생 emit 경로는 **최소 변경**.
- fix 없으면 **현행 100% 동작**.

---

## 9. 코드 대응표

| 요구 | 파일 | 변경 |
|------|------|------|
| fix 창 UI | 신규 `control_sim_fix_proc_ui.py` (또는 유사) | Window + 텍스트 모델 |
| 창 표시 토글 | `ebs_control_panel_ui.py` | `_AUX_KIT_WINDOW_SPECS` + resolver |
| ext 생명주기 | `extension.py` | 창 생성·destroy |
| Start 스냅샷 | `control_window.py` | fix 텍스트 → 엔진 cfg |
| presample fix | `simulation_engine.py` | LOT index별 풀 시드 |
| lot 표시 헬퍼 | 신규 또는 `sim_control_defaults` 인근 | `format_lot_id_display()` — 전 UI 공통 |
| lot 표시 적용 | `control_window.py`, 모니터, 콘솔 emit | 진행현황·시뮬 모니터·로그 동일 규칙 |
| 타임테이블 `{}` | `control_sim_prerun_playback.py` | `{lot:…,fix_oht_ep:…}` / `{lot:…,fix_ep_oht:…}` |

---

## 10. 비범위

- fix 텍스트 파일·스냅샷·웹 API 영속 저장
- 화면별 독립 fix 테이블
- IN→BP, BP→EP, FOUP fix
- `lot_count`를 fix 파일에서 결정
- 막대그래프·JSON 애니 로직 변경 (필요 시 lot_id 표시만 연동)

---

## 11. 구현 체크리스트

- [ ] fix 텍스트 파서 (쉼표, 소수 허용, 행별 fallback)
- [ ] `FixProcInputWindow` + ext 연동
- [ ] `_AUX_KIT_WINDOW_SPECS` 6번째 항목, `SimpleBoolModel(True)`
- [ ] Start 시 텍스트 스냅샷 → `SimulationInitConfig` 또는 엔진 인자
- [ ] `_presample_fill` LOT index fix/랜덤 분기
- [ ] `format_lot_id_display()` — 타임테이블·진행현황·시뮬 모니터·콘솔 **전 UI** 적용
- [ ] 타임테이블 `{lot:LOT_NNN(label),fix_oht_ep:…}` / `{lot:…,fix_ep_oht:…}` 표시
- [ ] py_compile·수동 시나리오 검증 (lot 30 / fix 5줄 등)

---

## 12. 변경 이력

| 일자 | 내용 |
|------|------|
| 2026-06-16 | 1차 초안 |
| 2026-06-16 | 2차 — lot_count 연동·부분 fix·공통 1벌·프리런 단일 경로 |
| 2026-06-16 | 3차 — 라벨/괄호 lot_id, 파싱·fallback, IN/OUT 포함, 창 표시 구조, 비영속, 타임테이블 `{}` |
| 2026-06-16 | **4차 최종** — `{}` 내 `lot`+라벨 병기, `fix_oht_ep`/`fix_ep_oht` 키 확정, 전 UI 동일 lot 표시 |
