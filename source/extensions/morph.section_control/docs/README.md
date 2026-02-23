# Section Control Web ↔ Kit Extension 통신 규약

## 1. 개요

본 문서는 Web Viewer(React)와 Omniverse Kit
Extension(SectionControlService) 간의\
Section Control 관련 MessageBus 통신 규약을 정의합니다.

모든 메시지는 아래 구조를 따릅니다.

``` json
{
  "event_type": "string",
  "payload": { ... }
}
```

-   요청(Request)과 응답(Response)은 `id` 값으로 매칭됩니다.
-   모든 Request에는 반드시 `id`가 포함되어야 합니다.

------------------------------------------------------------------------

## 2. 이벤트 정의

### 2.1 상태 조회

  구분       이벤트명
  ---------- ----------------------
  Request    section_get_request
  Response   section_get_response

### 2.2 전체 상태 변경

  구분       이벤트명
  ---------- --------------------------
  Request    section_set_all_request
  Response   section_set_all_response

### 2.3 (선택) 부분 변경 이벤트

  ------------------------------------------------------------------------------
  기능              Request                       Response
  ----------------- ----------------------------- ------------------------------
  enabled 변경      section_set_enabled_request   section_set_enabled_response

  axis 변경         section_set_axis_request      section_set_axis_response

  flip 변경         section_set_flip_request      section_set_flip_response

  offset 변경       section_set_offset_request    section_set_offset_response
  ------------------------------------------------------------------------------

※ 현재 React(App.tsx)에서는 `section_get_request`와
`section_set_all_request`만 사용 중입니다.\
부분 변경 이벤트는 Extension에서 지원하지만 Web에서는 사용하지 않고
있습니다.

------------------------------------------------------------------------

## 3. 메시지 포맷

### 3.1 section_get_request

``` json
{
  "event_type": "section_get_request",
  "payload": {
    "id": 1
  }
}
```

### 3.2 section_get_response (성공)

``` json
{
  "event_type": "section_get_response",
  "payload": {
    "id": 1,
    "response": {
      "enabled": true,
      "axis": "X",
      "flip": false,
      "offset": 0.0,
      "widget_path": "/Section/Widget",
      "stage_ready": true,
      "sec_mgr_ready": true,
      "base_world_pos": [0, 0, 0],
      "applied_axis": "X",
      "applied_signed_offset": 0.0,
      "dirty_axis": false,
      "dirty_offset": false
    }
  }
}
```

### 3.3 section_get_response (에러)

``` json
{
  "event_type": "section_get_response",
  "payload": {
    "id": 1,
    "error": "에러 메시지"
  }
}
```

------------------------------------------------------------------------

### 3.4 section_set_all_request

``` json
{
  "event_type": "section_set_all_request",
  "payload": {
    "id": 2,
    "enabled": true,
    "axis": "Y",
    "flip": false,
    "offset": 12.5
  }
}
```

#### 필드 설명

  필드      타입                설명
  --------- ------------------- -----------------------------
  id        number              요청 식별자
  enabled   boolean             Section 활성화 여부
  axis      "X" \| "Y" \| "Z"   절단 기준 축 (권장: 대문자)
  flip      boolean             방향 반전 여부
  offset    float               절단 오프셋 값

### 3.5 section_set_all_response (성공)

``` json
{
  "event_type": "section_set_all_response",
  "payload": {
    "id": 2,
    "response": { "... 현재 section 상태 ..." }
  }
}
```

### 3.6 section_set_all_response (에러)

``` json
{
  "event_type": "section_set_all_response",
  "payload": {
    "id": 2,
    "error": "에러 메시지"
  }
}
```

------------------------------------------------------------------------

## 4. 설계 원칙

1.  모든 Request는 `id`를 포함해야 합니다.
2.  Response는 반드시 다음 중 하나여야 합니다.
    -   `{ id, response }`
    -   `{ id, error }`
3.  axis 값은 "X", "Y", "Z" 중 하나입니다.
4.  Extension 내부 apply loop 특성상, 응답 직후 시각적 반영이 약간
    지연될 수 있습니다.

------------------------------------------------------------------------

## 5. React 송신 예시

### section_set_all_request

``` ts
const msg = {
  event_type: "section_set_all_request",
  payload: {
    id: Date.now(),
    enabled: true,
    axis: "Y",
    flip: false,
    offset: 12.5,
  },
};

AppStream.sendMessage(JSON.stringify(msg));
```

### section_get_request

``` ts
const msg = {
  event_type: "section_get_request",
  payload: { id: Date.now() },
};

AppStream.sendMessage(JSON.stringify(msg));
```
