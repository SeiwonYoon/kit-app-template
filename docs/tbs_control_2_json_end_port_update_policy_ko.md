# TBS Control 2 — JSON 종료 시점 포트/위치 갱신 정책 (KO)

## 1. 용어 정의
- **공정시간(proc_sec)**: 시뮬레이션 이벤트별로 사용자가 설정하는 이동/대기 시간.  
  예: `OHT->EP`, `BP->EP`, `IN/OUT->EP`, `EP->OHT` 등의 이벤트에서 사용되는 시간.
- **FOUP 공정시간(EP 상)**: EP에서 FOUP이 “올라 공정”을 수행하는 별도 구간 시간.  
  이동 이벤트 JSON과는 무관하며, FOUP 공정은 별도 이벤트(`FOUP_PROCESS_START/END`)로 처리.
- **JSON 총 재생시간(anim_total)**: 이벤트에 매핑된 linked JSON 시퀀스가 1배속 기준으로 재생될 때의 합산 duration.  
  예: move/rotate/timesamples_replay/delay 등 스텝의 합.

## 2. 공정시간 우선(process_time_priority) 사용 정책
- 사용하지 않음(제어창 UI/엔진 동작 기준 모두에서 고정 OFF).
- 본 정책에서 이벤트의 “전체 길이”는 항상 **공정시간(proc_sec)** 을 기준으로 맞춘다.

## 3. 이벤트 시간 동기화 (공정시간 vs JSON 길이)
- 이벤트에 연결된 JSON은 **이벤트가 끝나는 시점**(=공정시간 종료 시점)에 맞춰 재생되어야 한다.
- 이벤트 연결 JSON의 재생 길이와 공정시간 비교 후 다음 규칙 적용:

### 3-1. `anim_total > proc_sec` (JSON이 더 김)
- JSON을 자동 배속으로 **공정시간(proc_sec) 안에 끝나도록** 재생한다.
- 배속 계수: `ratio = anim_total / proc_sec` (ratio > 1)
- 실제 JSON 재생 속도: `effective_speed_scale = (기존 UI의 sim 속도 배율) * ratio`

### 3-2. `anim_total <= proc_sec` (JSON이 더 짧거나 같음)
- JSON은 설정된 기존 속도로 그대로 재생한다.
- JSON 종료 후 남은 시간은 **대기**하여 이벤트 전체 길이를 `proc_sec`로 채운다.

## 4. 포트상태 갱신/보임-숨김/위치 초기화 트리거 시점
- 포트상태 갱신 + 보임/숨김(visibility) + 포트 LOT prim 위치 “동시 초기화(스냅)”는,
  반드시 **JSON이 마지막 스텝까지 모두 재생된 이후** 1회만 실행되어야 한다.
- 구현 상 트리거는 엔진이 포트 상태를 반영한 뒤 발생시키는 내부 이벤트 `PORT_OCC_REFRESH` 에 고정한다.
  - 이동 이벤트(예: ARRIVED/MOVE_REQ/REMOVED 등)는 UI에서 포트/visibility 갱신을 하지 않는다.
  - `PORT_OCC_REFRESH` 이벤트에서만 갱신을 수행한다.

## 5. 위치 초기화 규칙 — FOUP 공정 중 EP의 ±Y 오프셋 보존
- 포트상태 갱신과 위치 초기화 시점에,
  **FOUP 공정이 진행 중인 EP의 FOUP**은 “baseline 원위치”로 초기화하면 안 된다.
- FOUP 공정 중인 EP의 FOUP은 현재 상태에 해당하는 `±Y lift`를 반영한 위치로 스냅 초기화되어야 하며,
  튐/점프가 없어야 한다.
- Y 오프셋 값은 **SSOT 1곳에서 관리**한다.
  - `sim_control_defaults.py`의 `SimControlDefaults.foup_proc_y_lift`
  - 실제 적용 로직은 `port_lot_visibility.py`의 restore/sync 함수들이 `foup_proc_active_ep` 상태를 바탕으로 처리한다.

## 6. 구현 관련 변경 범위 (원칙)
- 시뮬레이션 “전체 흐름”은 건드리지 않는다.
- 수정 대상의 핵심은 아래 두 구간이다.
  1) 이벤트(JSON) 길이 vs 공정시간 동기화를 위한 재생 속도 산정
  2) `PORT_OCC_REFRESH`에서 포트상태/visibility/위치 초기화를 1회만 실행되도록 UI 갱신 시점 정리

