# HyView 로컬 테스트 가이드 (HTTP 디버그)

스트리밍(실무 WebRTC) 없이 **`morph.editor.kit`** 만 실행해서 웹↔Kit T2V/V2T 를 테스트하는 방법입니다.

---

## 먼저 알아둘 것 (30초 요약)

| 구분 | 설명 |
|------|------|
| **T2V** | 웹 → Kit 요청 (예: 시뮬 시작) |
| **V2T** | Kit → 웹 응답 (예: 프리런 결과) |
| **HTTP 디버그** | 브라우저가 `http://127.0.0.1:8721` 로 T2V/V2T 를 주고받음 (WebRTC 대신) |
| **Handler** | 실무와 **동일** — `ebs_handler` → `tbs_sim_bridge` → `control_window` |

시뮬 시작 시 V2T 는 **`V2T_response_start_simulation` 1번**으로 옵니다.
`timeline.timetable_rows` 는 **t 숫자 배열**(예: `[9.96, 19.59, 27.21, ...]`)이며,
개별 행 object 는 웹이 **`T2V_request_time_table`** 로 시간별 조회합니다.
(구 `V2T_response_simulation_timeline` chunk 방식은 폐기.)

---

## 준비 (최초 1회)

### 1. Kit 확장 확인

`morph.editor.kit` 에 아래 확장이 포함되어 있어야 합니다.

- `morph.tbs_control_2`
- `morph.hyview_messaging`

### 2. 웹 클라이언트 설치

```powershell
cd source/extensions/morph.hyview_messaging/web/hyview_client
npm install
```

---

## 테스트 순서 (매번)

### Step 1 — Kit 실행

일반 에디터 kit 실행 (`morph.editor_streaming.kit` **아님**).

Kit **콘솔/로그**에서 아래가 보이면 OK:

```
[HyViewDebugHttp] listening http://127.0.0.1:8721 ...
[morph.hyview_messaging] started ...
```

> HTTP 브리지 끄려면: `$env:TBS_HYVIEW_DEBUG_HTTP = "0"`  
> 포트 변경: `$env:TBS_HYVIEW_DEBUG_HTTP_PORT = "8722"`

---

### Step 2 — 웹 UI 실행

```powershell
cd source/extensions/morph.hyview_messaging/web/hyview_client
npm run dev
```

브라우저에서 **http://localhost:5173** 열기.

---

### Step 3 — HTTP 모드 연결 확인

1. 상단 **「HTTP 디버그 (8721)」** 가 선택되어 있는지 확인 (기본값)
2. 상태 표시가 **「HTTP 연결됨」** 이면 준비 완료

「HTTP 대기」가 계속이면 → Kit 이 실행 중인지, Step 1 로그를 다시 확인.

---

### Step 4 — EP 변경 테스트 (가장 가벼운 확인)

**목적**: T2V/V2T 왕복이 되는지 5초 안에 확인.

1. 화면1 EP 를 **3** 으로 바꾸고 **「EP 적용」** 클릭
2. **이벤트 로그**에 `→ T2V_request_eqp_change ...` 표시
3. 1~2초 후 `V2T_response_eqp_change code=0` 표시
4. **마지막 V2T** 패널에 `"code": 0`, `"ep_count": 3` 등 확인

여기까지 되면 HTTP 디버그 경로는 정상입니다.

---

### Step 5 — 시뮬 시작 테스트 (프리런 + chunk)

**목적**: 실무에서 쓰는 큰 JSON 응답 + timeline chunk 확인.

1. 화면1/2 LOT·EP·EBS 설정
2. **「시뮬 시작」** 클릭
3. **기다림** — 프리런은 **수십 초~수 분** 걸릴 수 있음 (정상)

**응답 (성공 시):**

```
V2T_response_start_simulation
     data.results[0].timeline.timetable_rows  →  [9.96, 19.59, 27.21, ...]  (t 배열)
     data.results[1].timeline.timetable_rows  →  [5.74, 13.02, ...]
     data.results[n].bar_graph.empty_pct      →  {all_ep_empty_pct, ep1_empty_pct, ...}
```

**개별 행 조회** — `T2V_request_time_table`:

```json
{ "event_type": "T2V_request_time_table", "payload": { "case": 0, "time": 9.96 } }
```

성공 응답 `V2T_response_time_table`:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "time": 9.96,
    "case": 0,
    "time_table": { "t": 9.96, "event": "ARRIVED", "lot_id": "LOT_001", "all_ep_empty_pct": 12.5 }
  }
}
```

- `time_table` 은 **object 그대로** (string 아님)
- 같은 `t` 에 행이 여러 개면 `FOUP_PROCESS_START` / `FOUP_PROCESS_END` 가 **아닌** 행 우선
- `all_ep_empty_pct`: 해당 행 `t` 까지 진행 시간 대비 ALL_EP empty 누적 %

**진행시간 동기화** — `T2V_request_time_sync` (웹 시계가 틀어졌을 때):

```json
{ "event_type": "T2V_request_time_sync", "payload": {} }
```

성공 응답 `V2T_response_time_sync`:

```json
{ "code": 0, "message": "success", "data": { "time": 6.09 } }
```

- Kit 화면1 현재 시뮬레이션 진행 시각(초)을 반환
- 웹은 이 값으로 로컬 진행 시계를 맞춘다

---

### Step 6 — Play / Pause / Seek (선택)

프리런 후 **Play** → `V2T_response_control_simulation` 확인.

**Seek** (막대그래프 시간축 클릭과 동일) — `t`는 **시뮬레이션 시간(초)**:

```json
{ "event_type": "T2V_request_seek_simulation", "payload": { "case": 0, "t": 120.0 } }
```

성공 응답 `V2T_response_seek_simulation`:

```json
{ "code": 0, "data": { "case": 0, "t": 118.5, "t_requested": 120.0, "row_index": 12 } }
```

- `t_requested`: 웹이 보낸 값
- `t`: 실제 적용된 sim 시각 (타임테이블 행 기준으로 스냅)
- `row_index`: 선택된 타임테이블 행

이벤트명·필드 rename 시 `hyview_event_contract.py` 를 먼저 수정.

**Restart** (직전 프리런으로 재생만 다시) — payload 비움, 응답 `data` 도 비움:

```json
{ "event_type": "T2V_request_restart_simulation", "payload": {} }
```

성공: `V2T_response_restart_simulation` → `{ "code": 0, "message": "success", "data": {} }`  
(웹은 start 때 받은 데이터를 그대로 사용)

---

## PowerShell 만으로 테스트 (UI 없이)

Kit 실행 후 PowerShell:

### 연결 확인

```powershell
Invoke-RestMethod http://127.0.0.1:8721/hyview/health
```

`ok : True` 이면 OK.

### EP 변경

```powershell
$body = '{"event_type":"T2V_request_eqp_change","payload":{"case":0,"eqp_id":"TEST","ep_count":3}}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8721/hyview/t2v -ContentType "application/json" -Body $body
```

2초 후:

```powershell
Invoke-RestMethod "http://127.0.0.1:8721/hyview/v2t?since=0"
```

### 시뮬 시작 + V2T 전부 보기

**① 시뮬 시작 요청 보내기**

```powershell
$body = @'
{
  "event_type": "T2V_request_start_simulation",
  "payload": {
    "configs": [
      {"lot_count": 6, "ep_count": 2, "ebs_enabled": true, "fab_id": "F1", "model_id": "M1", "eqp_id": "E1"},
      {"lot_count": 4, "ep_count": 2, "ebs_enabled": false, "fab_id": "F2", "model_id": "M2", "eqp_id": "E2"}
    ]
  }
}
'@
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8721/hyview/t2v -ContentType "application/json" -Body $body
```

**② V2T 이벤트 계속 받기 (프리런 끝날 때까지)**

```powershell
$since = 0
while ($true) {
  $r = Invoke-RestMethod "http://127.0.0.1:8721/hyview/v2t?since=$since"
  foreach ($ev in $r.events) {
    Write-Host "----" $ev.event_type "seq=" $ev.seq "----"
    if ($ev.event_type -eq "V2T_response_start_simulation") {
      $rows0 = $ev.payload.data.results[0].timeline.timetable_rows
      $rows1 = $ev.payload.data.results[1].timeline.timetable_rows
      Write-Host "  start_simulation: t 배열 개수 =" $rows0.Count "," $rows1.Count
      Write-Host "  첫 t 값 =" ($rows0 | Select-Object -First 3)
      Write-Host "  fab_id[0] =" $ev.payload.data.results[0].sim.fab_id
    }
    if ($ev.event_type -eq "V2T_response_time_table") {
      $d = $ev.payload.data
      Write-Host "  time_table: case=" $d.case " time=" $d.time " event=" $d.time_table.event
    }
  }
  if ($r.latest_seq -gt $since) { $since = $r.latest_seq }
  Start-Sleep -Milliseconds 500
}
```

**기대 결과:**

- `start_simulation` 1번 — `timetable_rows` 는 **t 숫자 배열** (행 개수만큼)
- `fab_id` 가 요청 configs 와 동일하게 echo
- 이후 `T2V_request_time_table` 로 t 값 하나를 보내면 해당 행 object 응답

`Ctrl+C` 로 중단.

---

## 자주 막히는 경우

| 증상 | 해결 |
|------|------|
| HTTP 연결 안 됨 | Kit 실행 확인, 콘솔 `[HyViewDebugHttp] listening` |
| EP 적용 V2T 없음 | `morph.tbs_control_2` 로드, Kit `[EBSHandler]` 로그 |
| 시뮬 V2T 한참 없음 | 프리런 대기 중 — 정상, Step 5② 폴링 유지 |
| timeline chunk 없음 | Kit 재시작 (최신 `ebs_handler` 반영), start_simulation 성공(code=0) 확인 |
| 포트 사용 중 | `$env:TBS_HYVIEW_DEBUG_HTTP_PORT = "8722"` |

---

## Livestream (실무) 모드

실무와 **완전 동일** 경로로 보려면:

- UI에서 **「Livestream」** 선택
- `morph.editor_streaming.kit` 실행 + WebRTC 연결

---

## 관련 코드

| 파일 | 내용 |
|------|------|
| `hyview_debug_http_bridge.py` | HTTP 8721 서버 |
| `ebs_handler.py` | T2V/V2T, `_TIMELINE_CHUNK_ROWS`, chunk 전송 |
| `hyview_client/` | 브라우저 테스트 UI |
