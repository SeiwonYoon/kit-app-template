# LAM 장비시뮬레이션 가시화(lam_control_1) 웹 연동 이벤트 명세

`lam_control_1` 확장은 HyView livestream 메시징(T2V/V2T)을 통해 웹과 통신한다.
웹 → Kit 요청은 `T2V_*`, Kit → 웹 응답은 `V2T_*` 이벤트로 주고받는다.

## 구성 요소

| 계층 | 파일 | 역할 |
|---|---|---|
| 이벤트/키 SSOT | `sk.hyview_messaging/hyview_event_contract.py` | 이벤트명·payload 키 상수 |
| 메시징 핸들러 | `sk.hyview_messaging/extension_handlers/lam_handler.py` | T2V 수신·payload 파싱·V2T envelope 조립 |
| Kit 실행 브리지 | `sk.hyview_messaging/lam_sim_bridge.py` | 메인 스레드 마샬링 + Kit 시뮬 실행 위임 |
| Kit 파이프라인 | `morph.lam_control_1/lam_federation_pipeline.py`, `simulation_play.py` | Federation fetch·prerun·재생·화면별 제어 |
| 메인 스레드 디스패치 | `morph.lam_control_1/kit_main_dispatch.py` | 메시징 스레드 → Kit main(UI) 스레드 큐잉 |

## 공통 규칙

- **화면(case) 매핑**: `case: 0` → 화면1, `case: 1` → 화면2. 각 화면은 독립적으로 동작하며 한 화면 제어가 다른 화면 재생에 영향을 주지 않는다.
- **비동기 처리**: 모든 요청은 `schedule_on_main_thread`로 Kit 메인 스레드에 큐잉되어 실행된다. 메시징 스레드를 block 하지 않으며, V2T 응답은 실제 작업 완료 콜백에서 전송된다.
- **공통 응답 envelope**:

```json
{ "code": 0, "message": "success", "data": { } }
```

  - 성공: `code = 0`, `message = "success"`
  - 실패: `code = 1`, `message = <오류 내용>`, `data`에는 요청 정보를 echo 유지

---

## 1. 시뮬레이션 시작

### 요청 — `T2V_request_start_simulation`

```json
{ "configs": [ { /* case0 설정 */ }, { /* case1 설정 */ } ] }
```

- `configs`: 화면별 설정 배열. Federation API fetch(limit/offset 페이지네이션, `has_next=false`까지) → 파싱 → prerun → 재생까지 수행한다.

### 응답 — `V2T_response_start_simulation`

```json
{ "code": 0, "message": "success", "data": { "results": [ {}, {} ] } }
```

- `data.results`: 화면별 결과 배열 2칸. **응답 형식 미확정** 상태로 현재는 빈 `{}` 2칸(placeholder)을 반환한다.
- 실패 시 `code: 1`, `message`에 오류, `data.results`는 빈 2칸.

### 동작

1. 웹 설정(`configs`)으로 Federation API를 요청, 데이터를 페이지 단위로 모두 수신
2. 수신 데이터를 파싱·prerun 후 화면 표시 전환(`request_screen_visibility`) 완료를 기다렸다가 재생 시작
3. 완료(또는 실패) 시 V2T 응답 전송

---

## 2. 시뮬레이션 중지

### 요청 — `T2V_request_stop_simulation`

```json
{ "case": 0 }
```

- `case`: 중지할 화면 (0=화면1, 1=화면2)

### 응답 — `V2T_response_stop_simulation`

```json
{ "code": 0, "message": "success", "data": { "case": 0 } }
```

- `data.case`: 요청한 case echo
- 실패 시 `code: 1`, `message`에 오류, `data.case`는 요청값 유지

### 동작

- 해당 화면만 UI 「정지(초기화)」와 동일 경로(`_on_csv_stop_reset_clicked`)로 중지·초기화한다.
  - 재생 worker 종료, 로봇 Z·MOVE prim 위치 복원(TBS_OFFSET→0), FOUP show / 나머지 hide 등
- 다른 화면의 재생에는 영향을 주지 않는다.
- 중지·초기화가 **완전히 끝난 뒤** 완료 콜백에서 V2T 응답을 전송한다.

---

## 3. 실시간 제어

### 요청 — `T2V_control_simulation`

```json
{
  "case": 0,
  "proc_only": true,
  "show_top_view": false,
  "foup_info_show": true,
  "eqp_info_show": true,
  "wafer_number_show": false,
  "prim_hide": true,
  "speed": 2.0
}
```

- `case`(필수): 적용 대상 화면 (0=화면1, 1=화면2)
- 나머지 항목은 **모두 optional** — payload에 존재하는 항목만 적용하고, 누락 항목은 현재 상태를 유지한다.

| 키 | 타입 | 의미 |
|---|---|---|
| `proc_only` | bool | 공정만보기 (idle 구간 압축, JSON 공정 단계만 재생) |
| `show_top_view` | bool | 탑뷰 시점 고정 + 카메라 조작 잠금 |
| `foup_info_show` | bool | FOUP 상태 3D 표시 |
| `eqp_info_show` | bool | 기기정보(디바이스 라벨) 3D 표시 |
| `wafer_number_show` | bool | 웨이퍼 번호 3D 라벨 표시 |
| `prim_hide` | bool | 지정 prim 숨김/복원 |
| `speed` | float | 재생 배속 (0.1~20.0, 공정만보기 ON 시 1x 강제) |

### 응답 — `V2T_response_control_simulation`

전달받은 payload를 그대로 echo 하며 성공 여부를 담는다.

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "case": 0,
    "proc_only": true,
    "show_top_view": false,
    "foup_info_show": true,
    "eqp_info_show": true,
    "wafer_number_show": false,
    "prim_hide": true,
    "speed": 2.0
  }
}
```

- 실패 시 `code: 1`, `message`에 오류, `data`는 전달 payload echo 유지.

### 동작

- `case`가 지정한 화면의 CSV 재생창 모델에 전달된 항목만 반영한다. 각 omni.ui 모델 변경이 화면별 overlay/카메라 동기화를 자동 유발하므로 **재생 중에도 실시간 적용**된다.
- 적용 순서: 오버레이(FOUP·기기·웨이퍼·탑뷰·prim숨김) → 배속 → 공정만보기(pause/resume 유발하므로 마지막).
- 잘못된 `case`(0·1 외), 숫자가 아닌 `speed` 등은 실패 응답으로 처리한다.

---

## 이벤트 요약

| 방향 | 이벤트명 | payload 핵심 키 | 응답 data |
|---|---|---|---|
| T2V → | `T2V_request_start_simulation` | `configs[]` | — |
| → V2T | `V2T_response_start_simulation` | — | `results[2]` (미확정, 빈 2칸) |
| T2V → | `T2V_request_stop_simulation` | `case` | — |
| → V2T | `V2T_response_stop_simulation` | — | `case` |
| T2V → | `T2V_control_simulation` | `case` + optional 7종 | — |
| → V2T | `V2T_response_control_simulation` | — | 전달 payload echo |

## 참고 — 구조 정합성 (TBS 대비)

- `lam_handler`는 TBS `ebs_handler`와 동일한 구조(요청 수신 로그 → bridge 위임 → 완료 콜백에서 `_dispatch_v2t_ok/_err` envelope 조립)를 따른다. LAM은 `start_simulation` 계열만 제공한다.
- `lam_sim_bridge`는 TBS `tbs_sim_bridge` 패턴(메인 스레드 마샬링 + Kit 실행 위임)을 따른다.
- `kit_main_dispatch`는 TBS와 동일하게 update 이벤트 스트림 구독 + 큐 방식이다. (존재하지 않는 `IApp.post_update` 미사용)
