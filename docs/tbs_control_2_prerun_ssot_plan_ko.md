# TBS Control 2 — 프리런 SSOT + 재생 전용 (재작성 방향)

> **작성:** 2026-09-06  
> **상태:** 스케줄 표·빌더 골격 확정 / 재생기 연결은 후속  
> **관련:** `docs/tbs_control_2_proc_parallel_anim_serial_changelog_ko.md`  
> **코드:** `morph/tbs_control_2/prerun_plan_ssot.py`

---

## 1. 목표 구조

| 역할 | 책임 |
|------|------|
| **프리런 플래너 (SSOT)** | LOT 수·공정시간·애니시간·EP수·EBS·우선순위로 **전체 일정** 확정 |
| **재생기** | 해당 초에 공정표시·애니 재생·포트·막대만 **표시** (live 재판단 없음) |

사이드 버그 원인(엔진 live / progress occ / post-anim / plan 다중 SSOT)을 제거한다.

---

## 2. 스케줄 규칙 (확정)

1. **공정**: 포트·우선순위가 허용하면 **병렬 시작**.
2. **애니 길이**: 프리런 시 공정별 ``data/sim_sequences/*.json`` 을 읽어 **1배속 예상 초**를 반영  
   (예: arrived_ep1 / move_inout_bp1 / removed_ep1 …). 문서 LOT3의 “애니 10초”는 **설명용 가정**일 뿐.
3. **애니 큐**: 전역 **1큐 직렬**. 이상적 시작 = `t_proc_start + max(0, proc_sec - anim_sec)`.
4. **동시 ready 우선순위 (1차)**: `EP1 > EP2 > EP3 > INOUT > BP1.. > 기타`.
5. **공정 wall**: `t_wall_end = max(t_proc_start + proc_sec, t_anim_end)` (애니 밀리면 연장).
6. **포트 점유 변경**: renewal 시점(또는 애니 종료) — 프리런 키프레임.
7. **후속 공정**: 안착/비움이 확정된 직후 규칙에 맞으면 **즉시** 시작 (FOUP 종료→REMOVE, INOUT 안착→INOUT→BP 등).
8. **OHT→EP 금지**: INOUT 또는 BP에 LOT이 하나라도 있으면.
9. **BP→EP 우선**: 빈 EP + BP LOT → INOUT→BP 보류.
10. **FOUP**: EP **애니 종료(안착 완료)** 직후 시작. renewal(중간 포트 갱신)과 분리 — FOUP/REMOVE가 이송 애니 중에 끼어들지 않음. (엔진 FOUP capacity=1이면 겹칠 때 직렬)

---

## 3. 기준 예시 (검증 픽스처)

**설정:** LOT=3, EBS ON, EP=2, 이송 공정 전부 30s, FOUP 10s, **모든 애니 10s**.  
**LOT 배정(1차 웨이브):** EP1←LOT001, EP2←LOT002, INOUT←LOT003.

### 3.1 초 단위 일정 (플래너 정답)

| t(s) | 공정 | 애니 | 포트 변화(요약) |
|------|------|------|-----------------|
| 0 | 시작: `OHT→EP1`, `OHT→EP2`, `OHT→INOUT` | — | 전부 비어 있음 |
| 20 | — | `OHT→EP1` 시작 | — |
| 30 | `OHT→EP1` wall 종료 · **FOUP EP1** 시작 | `OHT→EP1` 끝 → `OHT→EP2` 시작 | EP1=LOT001 |
| 40 | FOUP EP1 종료 · **REMOVE EP1** 시작 · `OHT→EP2` wall 종료 · **FOUP EP2** 시작 | `OHT→EP2` 끝 → `OHT→INOUT` 시작 | EP2=LOT002 |
| 50 | `OHT→INOUT` wall 종료 · **INOUT→BP** 시작 · FOUP EP2 종료 · **REMOVE EP2** 시작 | `OHT→INOUT` 끝 | INOUT=LOT003 |
| 60 | — | `REMOVE EP1` 시작 (ready@60, 큐 여유) | — |
| 70 | REMOVE EP1 wall 종료 | `REMOVE EP1` 끝 → **`REMOVE EP2`** 시작 (ready@70, EP2>INOUT) | EP1 비움 |
| 80 | REMOVE EP2 wall 종료 | `REMOVE EP2` 끝 → **`INOUT→BP`** 시작 (ready@70이었으나 대기) | EP2 비움 |
| 90 | INOUT→BP wall 종료 · **BP→EP1** 시작 (빈 EP + BP LOT) | `INOUT→BP` 끝 | INOUT 비움, BP1=LOT003 |
| 110 | — | `BP→EP1` 시작 (lead 20) | — |
| 120 | BP→EP1 wall 종료 · **FOUP EP1** 시작 | `BP→EP1` 끝 | EP1=LOT003, BP1 비움 |
| 130 | FOUP EP1 종료 · **REMOVE EP1** 시작 | — | — |
| 150 | — | `REMOVE EP1` 시작 | — |
| 160 | REMOVE EP1 wall 종료 · (잔여 LOT 없으면 종료) | `REMOVE EP1` 끝 | EP1 비움 |

### 3.2 사용자 서술과의 차이 (의도적 정합)

사용자 초안은 “INOUT→BP 애니 시점에 다른 애니 없음”으로 읽힐 수 있으나,  
**같은 규칙(직렬 큐 + EP 우선)** 을 끝까지 적용하면 t=70에 `REMOVE EP2`와 `INOUT→BP`가 동시 ready → **EP2 REMOVE가 먼저**다.  
위 표가 SSOT 정답이며, 예시 검증도 이 표를 따른다.

### 3.3 애니 큐만 요약

```
20–30  OHT→EP1
30–40  OHT→EP2
40–50  OHT→INOUT
50–60  (idle)
60–70  REMOVE EP1
70–80  REMOVE EP2
80–90  INOUT→BP
90–110 (idle / BP→EP lead)
110–120 BP→EP1
120–150 (FOUP + REMOVE lead)
150–160 REMOVE EP1 (LOT003)
```

---

## 4. 구현 단계

| 단계 | 내용 | 상태 |
|------|------|------|
| A | 규칙·예시 표 문서화 | 완료 |
| B | `prerun_plan_ssot.py` 오프라인 플래너 + 예시 assert | 완료 |
| C | JSON별 애니 시간 (`sim_sequence_duration.py`) | 완료 |
| D | `prerun_plan_adapt.py` → `SimPreRunResult` | 완료 |
| E | `SIM_PRERUN_PLAN_SSOT=True` + 엔진 tick/start 생략 | 완료 |
| F | 재생 애니 시작 = 프리런 `anim_play_start` only (lead 재계산 금지) | 완료 |
| G | 재생 emit/progress/enrich/renewal/predict live 재계산 차단 | 완료 |
| H | 레거시 multi-wave / dead predict 코드 삭제 | 후속 |

### 플래그

- `SIM_PRERUN_PLAN_SSOT=True` — 오프라인 프리런 (기본)
- `SIM_PROC_PARALLEL_ANIM_SERIAL=False` — 엔진 multi-wave 비활성 (SSOT가 대체)
- 레거시 원복: `SIM_PRERUN_PLAN_SSOT=False` (+ 필요 시 `SIM_PROC_PARALLEL_ANIM_SERIAL=True`)

---

## 5. 원복

- 플래그 `SIM_PRERUN_PLAN_SSOT=False` → 기존 prerun(엔진 tick) 경로.
- 본 문서·`prerun_plan_ssot.py` 삭제/되돌리기로 SSOT 플래너 제거 가능.
