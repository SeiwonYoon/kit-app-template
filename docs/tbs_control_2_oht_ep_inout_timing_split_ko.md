# TBS Control 2 — OHT→EP / OHT→INOUT 이동시간 분리

작성일: 2026-08-10  
상태: 구현 반영

---

## 1. 목표

기존 **OHT→IN/OUT/EP** 공통 설정(`oht_bp1_min` / `oht_bp1_max`, `rand_oht_to_bp1`)이

- **OHT → EP** 직접 투입
- **OHT → IN/OUT** 안착

둘 다에 쓰이던 것을 **서로 다른 min~max**로 분리한다.

---

## 2. 동작 요약

| 경로 | 동작 |
|------|------|
| 제어창 직접 시작 | OHT→EP / OHT→INOUT **각각** 제어창 값 적용 |
| 웹 start에 `oht_inout_min/max` **있음** | 그 값으로 INOUT 반영 |
| 웹 start에 `oht_inout_*` **없음** | INOUT도 EP와 같게 + **제어창 OHT→INOUT UI를 OHT→EP 값으로 강제 맞춤** |
| EBS OFF | OHT→INOUT UI 비활성/숨김 |
| fix 공정시간 | `이름, OHT→EP초, EP→OHT초`만 — **INOUT은 fix 없음**, min~max만 |

---

## 3. 키·의미 (Kit 내부 / 스냅샷)

| 키 | 의미 | 비고 |
|----|------|------|
| `oht_bp1_min` / `oht_bp1_max` | **OHT→EP** 이동시간 난수 구간 | 기존 키 유지 (이름에 bp1이 남아 있어도 EP 전용) |
| `oht_inout_min` / `oht_inout_max` | **OHT→INOUT** 이동시간 난수 구간 | 신규 |

EBS OFF:

- INOUT 경로 없음 → `oht_inout_*` UI 숨김, 엔진도 INOUT 타이밍 미사용
- `oht_bp1_*`만 OHT→EP에 사용

라벨:

- EBS ON: `OHT→EP`, `OHT→IN/OUT` 각각 표시
- EBS OFF: `OHT→EP`만 표시

---

## 4. 웹 `T2V_request_start_simulation` 형식

`configs[n]`은 화면별 flat settings. OHT 구간만 아래처럼 확장한다.

### 4.1 권장 (신규 필드 포함)

```json
{
  "configs": [
    {
      "fab_id": "FAB01",
      "model_id": "MODEL01",
      "eqp_id": "EQP01",
      "lot_count": 6,
      "ep_count_idx": 0,
      "ebs_enabled": true,
      "oht_bp1_min": 5.0,
      "oht_bp1_max": 10.0,
      "oht_inout_min": 7.0,
      "oht_inout_max": 12.0
    },
    {
      "fab_id": "FAB01",
      "model_id": "MODEL02",
      "eqp_id": "EQP02",
      "lot_count": 4,
      "ep_count_idx": 0,
      "ebs_enabled": false,
      "oht_bp1_min": 5.0,
      "oht_bp1_max": 10.0
    }
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `oht_bp1_min` | number | 권장 | OHT→EP 최소(초) |
| `oht_bp1_max` | number | 권장 | OHT→EP 최대(초) |
| `oht_inout_min` | number | EBS ON 시 권장 | OHT→INOUT 최소(초) |
| `oht_inout_max` | number | EBS ON 시 권장 | OHT→INOUT 최대(초) |

### 4.2 호환 규칙

1. **`oht_inout_min` / `oht_inout_max` 가 없음**  
   → 시뮬 타이밍은 `oht_bp1_*`로 폴백. **동시에** `apply_case_sim_settings`가 제어창 OHT→INOUT UI를 EP 값으로 강제한다 (잔여 UI 값이 덮어쓰지 못함).
2. **`ebs_enabled: false`**  
   → `oht_inout_*`는 있어도 무시. OHT→EP만 `oht_bp1_*` 사용.
3. **min > max**  
   → 기존 `_timing_and_init_from_snapshot` / TimingConfig 정규화 패턴.
4. **fix 공정시간**  
   → OHT→EP / EP→OHT만. OHT→INOUT은 항상 `oht_inout_*`(또는 폴백 `oht_bp1_*`) 난수.

### 4.3 기존 키 유지 이유

`oht_bp1_*`를 `oht_ep_*`로 즉시 개명하지 않는다. 의미만 **OHT→EP 전용**으로 고정하고 INOUT은 `oht_inout_*`를 추가한다.

---

## 5. 구현 파일

- `sim_control_defaults.py` — `oht_to_inout_min/max`
- `simulation_engine.py` — TimingConfig, `rand_oht_to_inout`, `_pre_pool`, `_load_lot_to_inout` (fix 미적용)
- `ebs_case_models.py` — 필드·캡처·apply(폴백 시 UI 강제)
- `ebs_case_panel_ui.py` / `ebs_control_panel_ui.py` — EP/INOUT 행 분리, EBS OFF 숨김
- `control_window.py` — `_timing_and_init_from_snapshot`, `_sync_ebs_control_visibility*`

---

## 6. 확인 체크리스트

- [x] EBS ON: 제어창·HUD에 OHT→EP / OHT→INOUT 각각 min~max
- [x] EBS OFF: OHT→INOUT 숨김, OHT→EP만
- [x] 엔진: EP 직접투입은 `oht_bp1_*`, INOUT 안착은 `oht_inout_*`
- [x] fix LOT: OHT→EP·EP→OHT만 고정, INOUT은 설정 구간 난수
- [x] 웹: `oht_inout_*` 포함 시작 / 생략 시 `oht_bp1_*` 폴백 + UI 강제
- [x] 구 페이로드(inout 키 없음) 회귀: EP·INOUT 둘 다 `oht_bp1_*`로 동작
