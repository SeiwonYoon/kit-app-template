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

시뮬 시작 시 V2T는 **2종류**로 옵니다.

1. **`V2T_response_start_simulation`** — bar_graph, sim 등 (타임라인 행은 **빈 배열**)
2. **`V2T_response_simulation_timeline`** — timetable_rows 를 **20행씩** 잘라서 여러 번 (chunk)

chunk 크기는 `ebs_handler.py` 상단 `_TIMELINE_CHUNK_ROWS = 20` 에서 변경.

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

**응답 순서 (성공 시):**

```
① V2T_response_start_simulation
     data.results[0].timeline.timetable_rows  →  []  (비어 있음)
     data.results[1].timeline.timetable_rows  →  []

② V2T_response_simulation_timeline  offset=0, end=false
     data.timelines[0].timetable_rows  →  최대 20행 (string)
     data.timelines[1].timetable_rows  →  최대 20행 (string)

③ V2T_response_simulation_timeline  offset=1, end=false
     ...

④ V2T_response_simulation_timeline  offset=N, end=true  ← 마지막
```

**예시 (화면1=45행, 화면2=30행, chunk=20):**

| offset | end | 화면1 행 수 | 화면2 행 수 |
|--------|-----|------------|------------|
| 0 | false | 20 | 20 |
| 1 | false | 20 | 10 |
| 2 | **true** | 5 | **0 (빈 배열)** |

한쪽이 먼저 끝나도 **빈 배열 `[]` 로 계속 보냅니다.**

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
      $n0 = $ev.payload.data.results[0].timeline.timetable_rows.Count
      $n1 = $ev.payload.data.results[1].timeline.timetable_rows.Count
      Write-Host "  start_simulation: timetable_rows count =" $n0 "," $n1 "(둘 다 0이어야 함)"
      Write-Host "  fab_id[0] =" $ev.payload.data.results[0].sim.fab_id
    }
    if ($ev.event_type -eq "V2T_response_simulation_timeline") {
      $d = $ev.payload.data
      $c0 = $d.timelines[0].timetable_rows.Count
      $c1 = $d.timelines[1].timetable_rows.Count
      Write-Host "  timeline chunk offset=" $d.offset " end=" $d.end " rows=" $c0 "," $c1
    }
  }
  if ($r.latest_seq -gt $since) { $since = $r.latest_seq }
  Start-Sleep -Milliseconds 500
}
```

**기대 결과:**

- `start_simulation` 1번 — `timetable_rows` 개수 **0, 0**
- `simulation_timeline` 여러 번 — `offset` 0→1→… 마지막 `end=True`
- `fab_id` 가 요청 configs 와 동일하게 echo

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
