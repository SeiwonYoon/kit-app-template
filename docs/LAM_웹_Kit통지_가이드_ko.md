# LAM Kit → 웹 통지 가이드 (웹 전용)

Kit이 이미 하는 동작(시뮬, prim 숨김, 좌상단 로딩 HUD)은 그대로입니다.  
이 문서는 **Kit이 웹으로 추가로 보내는 알림**만 설명합니다.

웹이 아래 이벤트를 받고 **아무 처리도 하지 않아도** Kit는 지금과 같이 동작합니다.  
웹 UI(체크박스, 로딩 표시)를 Kit와 맞추고 싶을 때만 사용하면 됩니다.

Kit에 다시 요청하지 마세요. 아래 통지는 **이미 끝난 상태**를 알려 주는 것입니다.

---

## 공통

| 항목 | 값 |
|---|---|
| 방향 | Kit → 웹 (요청 없음, 단방향) |
| 봉투 | `{ "code": 0, "message": "success", "data": { ... } }` |
| 화면 | `data.case` **0 = 화면1**, **1 = 화면2** |
| 전송 경로 | 기존 HyView livestream V2T와 동일 (`V2T_notify_status_panel` 과 같은 방식) |

성공 통지의 `code`는 항상 `0`입니다. Kit가 웹 응답을 기다리지 않습니다.

---

## 1. prim 숨김 — `V2T_notify_control_simulation`

시뮬이 시작되고 카메라 fly가 끝난 뒤, Kit이 prim을 자동으로 숨긴 **그 순간**에 옵니다.  
Kit 화면의 「prim 숨김」 체크가 켜지는 시점과 같습니다.

### 이벤트명

`V2T_notify_control_simulation`

웹 → Kit 제어 요청 `T2V_control_simulation`과 **필드명이 같습니다.**  
이벤트명만 `notify`이고, 웹이 보낸 요청에 대한 응답이 아닙니다.

### 언제 오나

1. 해당 화면 재생 시작
2. 카메라 fly 완료
3. Kit이 prim 자동 숨김 완료
4. 이 통지 전송

한쪽 화면만 재생을 시작하면 그 화면 `case`만 옵니다. 두 화면이면 각각 한 번씩 옵니다.

### payload

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "case": 0,
    "prim_hide": true
  }
}
```

| `data` 키 | 타입 | 의미 |
|---|---|---|
| `case` | number | 0=화면1, 1=화면2 |
| `prim_hide` | boolean | 자동 숨김 완료 시 `true` |

다른 제어 키(`proc_only`, `speed` 등)는 이 통지에 **없습니다.**  
있는 키만 반영하면 됩니다. (`T2V_control_simulation`과 같은 규칙)

### 웹에서 할 일

- `data.case`에 해당하는 **prim 숨김 체크박스를 ON** 으로 맞춥니다.
- **하지 말 것:** `T2V_control_simulation`으로 `prim_hide: true`를 다시 보내지 않습니다. Kit은 이미 숨겼습니다.

### 웹에서 안 해도 되는 일

이 이벤트를 무시하면 Kit 체크박스와 3D 숨김은 그대로 유지됩니다. 웹 체크만 Kit와 어긋날 수 있습니다.

---

## 2. 로딩·재생 준비 — `V2T_notify_load_status`

API를 요청하고, 데이터를 받고, 파싱하는 동안 Kit 좌상단 로딩 HUD와 **같은 단계**가 화면별로 옵니다.  
그 화면 재생 준비가 끝나면 `ready`가 옵니다.

### 이벤트명

`V2T_notify_load_status`

### 언제 오나 (`data.status`)

화면마다(`case`) 아래 순서로 올 수 있습니다. 한쪽이 `ready`여도 다른 쪽은 아직 `parsing`일 수 있습니다.

| `status` | 의미 | 웹 UI 예시 |
|---|---|---|
| `requesting` | API 요청 시작 | 로딩 표시 |
| `received` | 데이터 수신 완료 | 로딩 유지 (파싱 직전) |
| `parsing` | 파싱·프리런 중 | 로딩 유지 |
| `ready` | **이 화면 재생 준비 완료** | 로딩 종료, 재생 가능 |
| `failed` | 이 화면 실패 | 오류 표시 |
| `playing` | 이 화면 재생이 실제로 시작됨 (Kit 재생 버튼 클릭 후) | 재생 중 |

웹 로딩 UI에는 보통 `requesting` → `received` → `parsing` → `ready` 만 쓰면 됩니다.  
`playing` / `failed`는 필요하면 사용하고, 아니면 무시해도 됩니다.

`ready`는 **그 화면만** 준비된 것입니다. 두 화면을 기다릴 거면 화면1 `case:0` 의 `ready`와 화면2 `case:1` 의 `ready`를 각각 받아야 합니다.

### payload

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "case": 0,
    "status": "parsing"
  }
}
```

실패 시 `detail`이 붙을 수 있습니다.

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "case": 1,
    "status": "failed",
    "detail": "eqp_id missing in config body"
  }
}
```

| `data` 키 | 타입 | 필수 | 의미 |
|---|---|---|---|
| `case` | number | 예 | 0=화면1, 1=화면2 |
| `status` | string | 예 | 위 표의 단계 |
| `detail` | string | 아니오 | 실패 사유 등. 있을 때만 포함 |

### 웹에서 할 일

- `data.case`별로 로딩 상태를 따로 그립니다. 화면1 로딩이 끝나도 화면2는 계속 돌 수 있습니다.
- `status === "ready"` 이면 해당 화면 로딩을 끄고 재생 준비 완료로 표시합니다.
- **하지 말 것:** 이 통지를 받고 Kit에 시작/중지를 다시 보내지 않습니다. 조회·파싱은 Kit이 이미 하고 있습니다.

### 웹에서 안 해도 되는 일

이 이벤트를 무시하면 Kit 좌상단 HUD와 시뮬은 지금처럼 동작합니다.

---

## 웹 처리 예시

```js
function onV2T(eventType, body) {
  const data = body?.data || {};
  const caseIndex = data.case; // 0 | 1

  if (eventType === "V2T_notify_control_simulation") {
    if (typeof data.prim_hide === "boolean") {
      setPrimHideChecked(caseIndex, data.prim_hide);
      // T2V_control_simulation 재전송 금지
    }
    return;
  }

  if (eventType === "V2T_notify_load_status") {
    setLoadStatus(caseIndex, data.status, data.detail);
    // status === "ready" → 해당 화면 재생 준비 완료
    return;
  }
}
```

---

## 기존 API와의 차이

| | 웹 → Kit (`T2V_*`) | 이번 통지 (Kit → 웹) |
|---|---|---|
| prim 숨김 | `T2V_control_simulation` `{ case, prim_hide }` | `V2T_notify_control_simulation` `{ case, prim_hide }` — Kit이 먼저 숨긴 뒤 알림 |
| 시뮬 시작 | `T2V_request_start_simulation` `{ configs }` — 두 화면 한 요청 | 시작 API는 그대로. 로딩 진행만 `V2T_notify_load_status`로 화면별 추가 |
| 시작 완료 응답 | `V2T_response_start_simulation` — 요청한 화면이 다 끝난 뒤 **한 번** | `ready`는 **화면마다** 따로 옴 |

시작/중지/제어 요청 API는 바뀌지 않았습니다.  
웹은 기존처럼 시작·중지·제어를 보내고, 이번 통지는 **받아서 UI만 맞추면** 됩니다.
