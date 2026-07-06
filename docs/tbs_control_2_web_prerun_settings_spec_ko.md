# TBS Control 2 — 웹·프리런 설정 연동 설계

> **상태:** API v0 실무 합의 SSOT · Kit 코드 반영됨 (`ebs_handler`, `tbs_sim_bridge`)  
> **SSOT:** 본 문서 §5 API + `data/sim_prerun/prerun_*.json` v2 형식  
> **전송:** WebRTC + `omni.kit.livestream.messaging` (HTTP `:8720` **사용 안 함**)

---

## 목차 (빠른 이동)

| § | 제목 | 누가 보나 |
|---|------|-----------|
| 1~8 | API·프리런 스키마 | 웹·Kit 공통 |
| **12** | **실무 환경 적용** | 배포 담당 |
| **13** | **로컬 테스트** | 개발·QA |
| **14** | **API 변경 시 수정 위치** | 유지보수 담당 |
| 11 | 질문·미결 | — |

### 이 문서만 보고 따라하는 순서

| 순서 | § | 할 일 |
|------|---|--------|
| 1 | §5 | API 형식·payload 이해 (실무 웹과 동일 SSOT) |
| 2 | §12 | 실무 Kit 서버 배포·확인 |
| 3 | §13 | 로컬 PC에서 hyview_client 로 검증 |
| 4 | §14 | API 변경 시 어느 파일을 고칠지 참고 |

---

## 1. 문서 목적

- 프리런 결과 JSON 및 **웹 ↔ Kit 이벤트 API**의 스키마·적용 규칙을 정의한다.
- Kit 내부(EBS 제어창, 시뮬 모니터, Viewport HUD)와 웹 UI의 **기능 대응**을 명시한다.
- 미확정 항목은 **§11 질문·미결**에 기록한다.

---

## 2. 용어·방향

| 약어 | 의미 |
|------|------|
| **T2V** | **T**o **V**iewer — 웹 → Kit (요청) |
| **V2T** | **V**iewer **T**o (웹) — Kit → 웹 (응답) |
| **case** | 화면 식별자 (0-based). `case = screen - 1` |
| **case 0** | 화면 1 — CASE A (`ext._sim_*`, Viewport HUD 공유) |
| **case 1** | 화면 2 — CASE B (`ext._ebs_b_*`) |

**Kit 내부 상수** `CASE_A = 1`, `CASE_B = 2`와 JSON `case` 0/1은 번호 체계가 다름.

### 공통 응답 래퍼 (V2T)

```json
{
  "code": 0,
  "message": "success",
  "data": { }
}
```

| `code` | 의미 |
|--------|------|
| `0` | 성공 |
| `0` 외 | 실패 (`message`에 사유) — 상세 코드표 **미정** (§11 Q8) |

---

## 3. 프리런 JSON 스키마 (v2)

### 3.1 루트

| 필드 | 타입 | 설명 |
|------|------|------|
| `version` | int | **`2`** |
| `case` | int | **0** = 화면1, **1** = 화면2 |
| `sim` | object | §3.2 |
| `timeline` | object | 기존 유지 |
| `bar_graph` | object | 기존 유지 |

- **파일명:** `prerun_screen{N}_{stamp}.json` **유지** (N = Kit screen 1-based)
- **루트만** `screen` → `case`. `timeline` / `bar_graph` 내부는 변경 없음

### 3.2 `sim` 블록

| 필드 | 타입 | 설명 |
|------|------|------|
| `ep_count_idx` | int | 0 = EP 2개, 1 = EP 3개 |
| `ep_count` | int | **2** 또는 **3** |
| `ebs_enable` | bool | EBS 적용 |
| `speed` | number | 재생 배속 (**최소/최대 제한 없음**) |
| `buffer_ports` | string[] | |
| `fault_ports` | string[] | |
| `final_sim_time_sec` | number | |
| `total_est_sec` | number | |
| `settings_snapshot` | object | §4 |

### 3.3 v2 루트 예시

```json
{
  "version": 2,
  "case": 0,
  "sim": {
    "ep_count_idx": 0,
    "ep_count": 2,
    "ebs_enable": true,
    "speed": 2.0,
    "buffer_ports": ["BP1", "BP2", "BP3"],
    "fault_ports": [],
    "final_sim_time_sec": 398.04,
    "total_est_sec": 654.072,
    "settings_snapshot": { }
  },
  "timeline": { },
  "bar_graph": { }
}
```

---

## 4. `settings_snapshot`

`capture_case_sim_settings()` dict — LOT, 간격, 초기적재/고장 등.

| 구분 | 필드 예 |
|------|---------|
| 요약 | `ep_count_idx`, `ebs_enabled`, `lot_count` |
| 간격 | `spawn_min/max`, `pue_min/max`, `oht_bp1_*`, `bp1_bp_*`, `bp_ep_*`, `ep_oht_*`, `foup_proc_*` |
| 초기 적재 | `init_inout`, `init_bp1`~`init_bp4`, `init_ep1`~`init_ep3` |
| 고장 | `fault_inout`, `fault_bp1`~`fault_bp4`, `fault_ep1`~`fault_ep3` |

### EP / EBS 적용 경로 (**확정**)

웹은 아래 **두 방식 모두** 사용 가능. Kit는 둘 다 처리한다.

| 방식 | 설명 |
|------|------|
| **A. 별도 이벤트** | `T2V_request_eqp_change`, `T2V_request_ebs_enable` → prim·레이아웃 **즉시 반영** |
| **B. 시작 시 snapshot** | `T2V_request_start_simulation`의 `configs[n]` 안 `settings_snapshot`에 `ep_count_idx` / `ebs_enabled` 포함 → 시작 직전 해당 case 설정 적용 |

- A만 쓰거나, B만 쓰거나, **A 후 B** 조합 모두 허용.
- `settings_snapshot` 내 키는 `ebs_enabled` (bool). 이벤트·`sim` 최상위는 `ebs_enable` — **이름 차이 유지** (웹·Kit 매핑 시 변환).

---

## 5. 웹 ↔ Kit 이벤트 API (v0)

> 전송 계층: **`omni.kit.livestream.messaging`** (WebRTC 데이터 채널). 로컬 테스트: §13, `hyview_client/README.md`

### 5.1 EP 포트 개수 변경

#### `T2V_request_eqp_change`

```json
{
  "case": 0,
  "eqp_id": "SPW1102",
  "ep_count": 2
}
```

| 필드 | 설명 |
|------|------|
| `case` | 0 = 화면1, 1 = 화면2 |
| `eqp_id` | 웹·MES 식별용. **Kit은 무시** (수신만 하고 사용 안 함) |
| `ep_count` | **2** 또는 **3** |

**Kit:** 해당 case EP 변경 → prim show/hide (`on_sim_ep_count_changed` / `on_sim_ep_count_changed_for_case`)

#### `V2T_response_eqp_change`

```json
{
  "code": 0,
  "message": "success",
  "data": { "case": 0 }
}
```

---

### 5.2 EBS 적용 여부 변경

#### `T2V_request_ebs_enable`

```json
{
  "case": 0,
  "ebs_enable": true
}
```

**Kit:** 해당 case EBS + prim 레이아웃 즉시 반영

#### `V2T_response_ebs_enable`

```json
{
  "code": 0,
  "message": "success",
  "data": { "case": 0, "ebs_enable": true }
}
```

---

### 5.3 시뮬레이션 시작 (2화면 동시)

#### `T2V_request_start_simulation`

```json
{
  "configs": [
    { },
    { }
  ]
}
```

| 항목 | 규칙 (**확정**) |
|------|----------------|
| `configs` | **길이 2 고정**. 형식 `configs: [{}, {}]` |
| `configs[0]` | **case 0** (화면 1) — `settings_snapshot` object |
| `configs[1]` | **case 1** (화면 2) — `settings_snapshot` object |
| 원소 내 `case` 필드 | **없음** (배열 인덱스로만 매핑) |

**예시:**

```json
{
  "configs": [
    {
      "lot_count": 6,
      "spawn_min": 5.0,
      "spawn_max": 10.0,
      "ep_count_idx": 1,
      "ebs_enabled": true,
      "init_ep1": false
    },
    {
      "lot_count": 4,
      "ep_count_idx": 0,
      "ebs_enabled": false
    }
  ]
}
```

> 각 `{}`는 §4 `settings_snapshot` 과 **동일한 flat dict** (wrapper 없이 snapshot 본문만 넣는 형태).

**Kit 처리 순서 (목표):**

1. `configs[0]` → CASE A UI/엔진 설정 적용 (`apply_case_sim_settings` 등)
2. `configs[1]` → CASE B 적용
3. snapshot 내 `ep_count_idx` / `ebs_enabled`가 있으면 EP·EBS도 적용 (§4 방식 B)
4. **Viewport HUD 「시작」과 동일** — `on_sim_start_clicked(ext)` (2화면 동시 프리런·재생)

#### `V2T_response_start_simulation`

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "result": [
      { },
      { }
    ]
  }
}
```

| 항목 | 규칙 (**확정**) |
|------|----------------|
| `data.result[0]` | case 0 — **프리런 v2 JSON 전체** (`version`, `case`, `sim`, `timeline`, `bar_graph`) |
| `data.result[1]` | case 1 — **프리런 v2 JSON 전체** |

---

### 5.4 시뮬레이션 제어 (재생 / 정지 / 배속)

#### `T2V_request_control_simulation`

```json
{
  "action": "play",
  "speed": 2.0
}
```

| 필드 | 설명 |
|------|------|
| `action` | **`play`** — 재생 요청 |
| | **`pause`** — 정지 |
| `speed` | 배속. **최소/최대 없음** (`play` 시 적용, `pause` 시 생략 가능) |

#### Kit `play` 동작 (**확정 + 구현 패턴**)

| 모드 | 동작 | 현재 기본 |
|------|------|-----------|
| **재시작 (restart)** | `pause` 후 `play` → HUD 「시작」과 동일, **처음부터** 프리런·재생 | **활성 (기본)** |
| **이어하기 (resume)** | 정지 시점부터 재생 재개 | **미구현 · 주석으로만 준비** |

**코드 반영 시 패턴 (Kit 개발자 요구):**

```python
# --- play: 기본 = 재시작 (HUD 시작과 동일) ---
on_sim_start_clicked(ext)

# --- [RESUME] 이어하기: 필요 시 위 줄을 주석 처리하고 아래 주석 해제 ---
# _resume_playback_from_pause(ext)  # 정지 시점 sim_now 기준 이어재생 (추후 구현)
```

- 기본 배포: **재시작만** 동작.
- 이어하기 필요 시: 재시작 호출을 주석 처리하고 resume 블록 주석 해제로 전환.

| action | Kit (확정) |
|--------|------------|
| `play` | 기본 → `on_sim_start_clicked(ext)` (재시작) |
| `pause` | `on_sim_stop_clicked(ext)` |

#### `V2T_response_control_simulation`

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "active": "play",
    "speed": 2.0
  }
}
```

---

## 6. Kit 기능 ↔ 웹 이벤트 매핑

| 웹 UI | Kit | 이벤트 |
|-------|-----|--------|
| EP 2/3 | EP 콤보 + prim | `eqp_change` 또는 snapshot `ep_count_idx` |
| EBS 체크 | EBS + prim | `ebs_enable` 또는 snapshot `ebs_enabled` |
| LOT·간격·적재·고장 | 제어창 | `configs[n]` (= settings_snapshot) |
| 시뮬 시작 2화면 | HUD 시작 | `start_simulation` |
| 시뮬 정지 | HUD 정지 | `control_simulation` `pause` |
| 배속 | 속도 | `control_simulation` `speed` + prerun `sim.speed` |
| 모니터·타임라인 | 시뮬 모니터 | `start_simulation` 응답 `result[]` + 추후 push |

---

## 7. 구현 순서 (제안)

1. prerun v2 — `case`, `sim.ebs_enable`, `sim.speed`, `settings_snapshot`
2. Kit T2V 핸들러 4종 + V2T 응답 (`result` = prerun 전체)
3. `control_simulation` play — 재시작 기본 + resume 주석 블록
4. 웹 UI·요청 조립
5. 재생 중 Kit → 웹 push (별도 이벤트 정의)

---

## 8. 확정 사항 요약

| 항목 | 결정 |
|------|------|
| prerun 루트 | `case` 0-based, `version` 2 |
| prerun 파일명 | `prerun_screen{N}_*.json` 유지 |
| `eqp_id` | Kit **무시** |
| `configs` | `[{}, {}]` 고정, `[0]`=화면1, `[1]`=화면2, 원소=settings_snapshot |
| EP/EBS | 별도 이벤트 **또는** snapshot 내 포함 (둘 다 가능) |
| `start` 응답 `result` | 화면별 **프리런 v2 JSON 전체** |
| `play` after `pause` | 기본 **재시작**; resume는 주석 토글로 추후 활성화 |
| `speed` | 최소/최대 **없음** |

---

## 9. 변경 이력

| 일자 | 내용 |
|------|------|
| 2026-07-05 | 문서 생성 |
| 2026-07-05 | API v0 8종 |
| 2026-07-05 | eqp_id 무시, configs 인덱스 고정, EP/EBS 이중 경로, result 전체 JSON, play/restart+resume 주석 패턴, speed 무제한 |
| 2026-07-05 | §12~§14 실무·로컬·유지보수 가이드 상세화; Q7 전송계층 확정; 따라하기 순서·envelope 형식 추가 |

---

## 10. 다음에 추가할 요구사항 (대기)

- [ ] 재생 중 Kit → 웹 실시간 push 이벤트 (Q16)
- [ ] 시뮬 모니터·타임라인 웹 표현
- [ ] 오류 `code` 상세 표 (Q8)
- [x] 전송 계층 — livestream messaging (§12, §13)
- [ ] 기타 이벤트 추가·변경 → §14 절차 따름

---

## 11. 질문·미결

### 확정됨 (이전 질문)

| ID | 결정 |
|----|------|
| Q1 | 파일명 `prerun_screen{N}_*.json` 유지 |
| Q4 | `version` 2 |
| Q5/Q6 | 루트 `case`만, 내부·병기 없음 |
| Q9 | speed **범위 없음** |
| Q10 | `eqp_id` → Kit **무시** |
| Q11 | `configs[0]`=case0, `configs[1]`=case1 **고정** |
| Q12 | EP/EBS → **별도 이벤트 또는 snapshot** 둘 다 |
| Q13 | `result[]` = **프리런 v2 JSON 전체** |
| Q14 | `play` 기본 **재시작**; resume **주석 토글**로 추후 |
| Q15 | 이벤트 `ep_count` 2\|3, prerun `ep_count_idx`+`ep_count` 유지 |
| Q7 | **`omni.kit.livestream.messaging`** — T2V/V2T JSON envelope. HTTP `:8720` **폐기** (§12·§13) |

### 아직 미정

| ID | 내용 |
|----|------|
| Q8 | 실패 `code` 상세 코드표 (현재 `0`=성공, `0` 외=실패만 사용) |
| Q16 | 재생 중 Kit→웹 push; HTTP `GET /api/state` **폐기** — V2T·push 만 사용 |

---

## 12. 실무 환경 적용 가이드

> **전제:** §5 API 샘플은 **이미 실무 HyView 웹과 합의된 SSOT**이다.  
> 실무 웹 코드를 Kit에 맞게 다시 짤 필요는 **없다**. 배포 담당은 **Kit 스트리밍 서버**만 아래대로 준비하면 된다.

### 12.1 한눈에 보는 데이터 흐름

```
[실무 HyView 웹 (브라우저)]
  │
  ├─ (1) WebRTC 영상 수신
  │      omni.kit.livestream.webrtc — 시그널링 TCP 포트 (기본 49100)
  │
  └─ (2) T2V 요청 / V2T 응답
         JSON envelope → omni.kit.livestream.messaging
                │
                ▼
         [Kit 스트리밍 서버 — morph.editor_streaming.kit 등]
                │
                morph.hyview_messaging
                  └─ EBSHandler (ebs_handler.py)
                        └─ tbs_sim_bridge.py
                              └─ morph.tbs_control_2 (시뮬·EBS·프리런)
```

**중요:** 예전 HTTP 브리지 `http://<kit>:8720` / `tbs_kit_remote` 는 **사용하지 않는다**.  
T2V/V2T 는 WebRTC 연결 **이후** `livestream.messaging` 으로만 주고받는다.

### 12.2 메시지 envelope 형식 (웹·Kit 공통)

웹이 보내는 T2V 한 건:

```json
{
  "event_type": "T2V_request_ebs_enable",
  "payload": { "case": 0, "ebs_enable": true }
}
```

Kit이 보내는 V2T 한 건:

```json
{
  "event_type": "V2T_response_ebs_enable",
  "payload": {
    "code": 0,
    "message": "success",
    "data": { "case": 0, "ebs_enable": true }
  }
}
```

| 방향 | `event_type` 접두 | 예 |
|------|-------------------|-----|
| 웹 → Kit | `T2V_request_*` | `T2V_request_start_simulation` |
| Kit → 웹 | `V2T_response_*` | `V2T_response_start_simulation` |

실무 웹: `AppStreamer.sendMessage(JSON.stringify(...))`  
로컬 동일 구현: `web/hyview_client/src/hyviewMessaging.ts`

### 12.3 Kit 앱·확장 구성

#### 스트리밍 Kit

| 파일 | 설명 |
|------|------|
| `source/apps/morph.editor_streaming.kit` | `morph.editor` + `omni.kit.livestream.app` |
| `source/apps/morph.editor.kit` | 베이스. `morph.hyview_messaging`, `omni.kit.livestream.messaging` 의존 |

`morph.editor_streaming.kit` 은 `morph.editor` 를 끌어오므로 hyview 확장은 **editor.kit** 에 선언된다.

### 12.4 Kit 서버 배포 체크리스트

#### 필수 확장

| 확장 ID | 역할 |
|---------|------|
| `morph.tbs_control_2` | 시뮬·EBS·프리런 |
| `morph.hyview_messaging` | T2V/V2T 핸들러 + bridge |
| `omni.kit.livestream.messaging` | 메시징 채널 |
| `omni.kit.livestream.app` | 앱 화면 WebRTC 스트림 |
| `omni.kit.livestream.webrtc` | WebRTC 구현 |

#### `morph.hyview_messaging` 핵심 파일

| 파일 | 역할 |
|------|------|
| `extension_handlers/ebs_handler.py` | T2V 4종 수신, V2T 응답 |
| `tbs_sim_bridge.py` | `tbs_control_2` 실제 동작 |
| `extension_handlers/base_handler.py` | 이벤트 등록 |
| `extension_handlers/__init__.py` | `HANDLERS = [EBSHandler]` |
| `extension.py` | Handler 시작 |
| `config/extension.toml` | 의존성 선언 |

경로 기준: `source/extensions/morph.hyview_messaging/morph/hyview_messaging/`

#### 배포 순서 (순서대로)

| 단계 | 작업 | 합격 기준 |
|------|------|-----------|
| 1 | `tbs_control_2`, `hyview_messaging` 포함 Kit 빌드·배포 | 최신 확장 바이너리 |
| 2 | 스트리밍 Kit 기동 (`morph.editor_streaming.kit` 등) | Kit 창 또는 headless 스트림 ready |
| 3 | 콘솔 로그 확인 | `[morph.hyview_messaging] started`, `EBSHandler registered` |
| 4 | 방화벽 | 시그널링 **49100/TCP** (Kit·웹 설정과 동일) |
| 5 | 실무 웹 연결 | WebRTC connected 후 §5 버튼 테스트 |

T2V 전송 시 콘솔에 `[EBSHandler] _on_req_*` 가 추가로 보이면 messaging 경로 정상이다.

### 12.5 실무 웹 (합의 API — 수정 불필요)

| 항목 | SSOT |
|------|------|
| 연결 | WebRTC **connected 후** 버튼 |
| 전송 | `AppStreamer.sendMessage(JSON.stringify({ event_type, payload }))` |
| EBS | `T2V_request_ebs_enable`, payload `ebs_enable` |
| 시뮬 시작 | `configs` 길이 2 |
| 시뮬 응답 | `data.result` (`results` 아님) |
| 제어 | `T2V_request_control_simulation`, `action`: `play` \| `pause` |
| 성공 | `code === 0` |

### 12.6 동작 확인 기준

| 동작 | Kit 콘솔 | 웹 |
|------|----------|-----|
| EBS | `[EBSHandler] _on_req_ebs_enable` | `V2T_response_ebs_enable` code=0 |
| EP | `_on_req_eqp_change` | `V2T_response_eqp_change` code=0 |
| 시작 | `_on_req_start_simulation` | `data.result[0/1]` = prerun v2 |
| 제어 | `_on_req_control_simulation` | `V2T_response_control_simulation` |

### 12.7 장애 빠른 진단

| 증상 | 1차 확인 | 2차 확인 |
|------|----------|----------|
| 버튼 무반응 | WebRTC **connected** | connected 전 T2V 는 Kit에 안 감 |
| Kit에 `[EBSHandler]` 없음 | `morph.hyview_messaging` 로드 | `morph.editor.kit` dependencies |
| Handler 로그는 있는데 UI 안 변함 | Python traceback | `tbs_sim_bridge` / `tbs_control_2` |
| V2T 없음 | `event_type` 오타 | 웹 `onCustomEvent` |
| `code: 1` | `message` 필드 | payload 키 오타 (`configs` 등) |
| HTTP 8720 호출 | **폐기된 경로** | §13 방법 A 로 재검증 |

---

## 13. 로컬 테스트 가이드

로컬에서는 **실무와 같은 messaging 경로**를 쓰되, Kit는 개발 PC, 웹은 테스트 클라이언트 또는 실무 HyView 를 가리키면 된다.

### 13.0 로컬 vs 실무

| 구분 | 실무 | 로컬 |
|------|------|------|
| Kit | 현장/클라우드 GPU 서버 | PC에서 `morph.editor_streaming.kit` |
| 웹 | HyView 실무 빌드 | **A** hyview_client **또는** **B** 실무 웹 |
| API | §5 SSOT | **동일** |
| HTTP 8720 | 사용 안 함 | 사용 안 함 |

### 13.1 방법 A — hyview_client (권장)

실무와 **같은 envelope** 로 Kit 를 검증하는 전용 페이지.

**경로:** `source/extensions/morph.hyview_messaging/web/hyview_client/`  
**README:** 같은 폴더 `README.md`

#### 사전 조건

- Node.js 18+, Chromium 브라우저
- Kit: `morph.editor_streaming.kit` 실행 후 ready

#### 최초 1회

```powershell
cd source\extensions\morph.hyview_messaging\web\hyview_client
npm install
```

`npm install` 실패 시 `web/hyview_client/.npmrc` 확인.

#### 매번 테스트

| # | 작업 | 확인 |
|---|------|------|
| 1 | Kit 실행 | `[morph.hyview_messaging] started` |
| 2 | `npm run dev` | http://localhost:5173 |
| 3 | **스트림 연결** | `127.0.0.1:49100` |
| 4 | connected 후 EBS·EP·시뮬·Play | Kit: `[EBSHandler] _on_req_*` |
| 5 | 이벤트 로그 / 마지막 V2T | `code: 0` |

#### stream.config.json

```json
{
  "source": "local",
  "local": { "server": "127.0.0.1", "signalingPort": 49100 }
}
```

#### hyviewMessaging.ts ↔ §5 API

| 함수 | T2V event_type |
|------|----------------|
| `requestEqpChange` | `T2V_request_eqp_change` |
| `requestEbsEnable` | `T2V_request_ebs_enable` |
| `requestStartSimulation` | `T2V_request_start_simulation` |
| `requestControlSimulation` | `T2V_request_control_simulation` |

### 13.2 방법 B — 실무 웹 + 로컬 Kit

1. 로컬에서 `morph.editor_streaming.kit` 실행
2. 실무 HyView 스트리밍 설정만 `127.0.0.1:49100` 으로 변경
3. WebRTC connected 후 **기존 버튼 그대로** — payload 수정 없음

방법 B: 실무 웹 연동 버그 재현. 방법 A: Kit handler만 빠른 검증.

### 13.3 로컬에서 확인 가능 / 불가

| 로컬 OK | 실무에서만 |
|---------|------------|
| T2V→handler→V2T 전 경로 | 다중 사용자·원격 GPU |
| EP/EBS/시뮬 Kit UI | 현장 방화벽·프록시 |
| `data.result[]` prerun v2 | MES `eqp_id` (Kit 무시) |

### 13.4 로컬 실패 시

| 증상 | 조치 |
|------|------|
| npm 404 | `web/hyview_client/.npmrc`, NVIDIA registry |
| 스트림 실패 | `morph.editor_streaming.kit`, 포트 49100 |
| T2V 무응답 | `morph.hyview_messaging`, `morph.tbs_control_2` 로드 |
| `code: 1` | Kit 콘솔 `message`, §5 payload 대조 |
| HTTP 8720 호출 | **잘못된 경로** — livestream.messaging (§12) |

---

## 14. API 변경·추가·삭제 시 수정 위치 (유지보수 가이드)

> **원칙:** **먼저 본 문서 §5 수정** → 코드·웹이 문서를 따른다. 한 곳만 고치면 실무·로컬·Kit 이 어긋난다.

### 14.1 코드 계층

```
[실무 HyView 또는 hyviewMessaging.ts]
  sendMessage({ event_type, payload })
       ↓
[ebs_handler.py]  — 이벤트명·V2T·payload 키
  get_event_handlers() / dispatch_event()
       ↓
[tbs_sim_bridge.py]  — handle_* + run_on_main_thread
       ↓
[morph.tbs_control_2]  — control_window, prerun export
```

| 계층 | 수정 시점 |
|------|-----------|
| `ebs_handler.py` | 이벤트 **이름**, V2T **형식**, payload **키** |
| `tbs_sim_bridge.py` | Kit **동작** (EP, 프리런, play/pause) |
| `tbs_control_2` | 시뮬 알고리즘, prerun **스키마** |
| 실무 웹 / `hyviewMessaging.ts` | T2V 조립·V2T 파싱 |

### 14.2 파일 맵 (전체 경로)

| 경로 | 역할 |
|------|------|
| `docs/tbs_control_2_web_prerun_settings_spec_ko.md` | API SSOT (**가장 먼저**) |
| `source/extensions/morph.hyview_messaging/morph/hyview_messaging/extension_handlers/ebs_handler.py` | T2V/V2T |
| `source/extensions/morph.hyview_messaging/morph/hyview_messaging/tbs_sim_bridge.py` | bridge |
| `source/extensions/morph.hyview_messaging/web/hyview_client/src/hyviewMessaging.ts` | 로컬 T2V |
| `source/extensions/morph.tbs_control_2/morph/tbs_control_2/control_window.py` | HUD·EP 레이아웃 |
| `source/apps/morph.editor.kit` | `morph.hyview_messaging` 의존 |

`ebs_handler.py` **파일 상단 주석**에 payload 키 SSOT (`configs`, `result` 등) 요약.

### 14.3 현재 등록 이벤트 (기준선)

| T2V | V2T | bridge |
|-----|-----|--------|
| `T2V_request_eqp_change` | `V2T_response_eqp_change` | `handle_eqp_change` |
| `T2V_request_ebs_enable` | `V2T_response_ebs_enable` | `handle_ebs_enable` |
| `T2V_request_start_simulation` | `V2T_response_start_simulation` | `handle_start_simulation` |
| `T2V_request_control_simulation` | `V2T_response_control_simulation` | `handle_control_simulation` |

### 14.4 시나리오별 수정표

#### A. T2V 이벤트 **이름** 변경

| 순서 | 파일 | 내용 |
|------|------|------|
| 1 | 본 문서 §5 | 이벤트명 |
| 2 | `ebs_handler.py` | `get_event_handlers()` 키 |
| 3 | 실무 웹 | `event_type` |
| 4 | `hyviewMessaging.ts` | `sendT2V("...", ...)` |

#### B. V2T 응답 **이름** 변경

| 순서 | 파일 | 내용 |
|------|------|------|
| 1 | 본 문서 §5 | 응답명 |
| 2 | `ebs_handler.py` | `get_outgoing_events()`, 모든 `dispatch_event("V2T_...")` |
| 3 | `tbs_sim_bridge.py` | `handle_start_simulation` 의 `dispatch(...)` 이름 |
| 4 | 실무 웹 | 수신 `event_type` 분기 |

#### C. payload **필드명** 변경

| 순서 | 파일 | 내용 |
|------|------|------|
| 1 | 본 문서 §5 | 필드 표 |
| 2 | `ebs_handler.py` | `event.payload["키"]`, V2T `data` |
| 3 | `tbs_sim_bridge.py` | `pl.get("키")` |
| 4 | 실무 웹 + `hyviewMessaging.ts` | payload 객체 |

#### D. API **추가**

| 순서 | 파일 | 내용 |
|------|------|------|
| 1 | 본 문서 §5 | 새 절 + JSON 예시 |
| 2 | `ebs_handler.py` | `get_outgoing_events` + `get_event_handlers` + `_on_req_*` |
| 3 | `tbs_sim_bridge.py` | `handle_*` + import |
| 4 | 실무 웹 | 버튼·sendMessage |
| 5 | (선택) `hyviewMessaging.ts` | 헬퍼 함수 |

`extension_handlers/__init__.py` 는 `HANDLERS = [EBSHandler]` 이면 **수정 불필요**.

#### E. API **삭제**

문서 §5 삭제 → `ebs_handler.py` 핸들러·메서드 → `tbs_sim_bridge.py` `handle_*` → 실무 웹·ts 함수.

#### F. API 형식 동일, **동작만** 변경

`tbs_sim_bridge.py` `handle_*` / `control_window.py` 만. `ebs_handler.py` 는 보통 그대로.

#### G. 프리런 JSON 스키마 변경

문서 §3~§4 → `tbs_control_2` export → `tbs_sim_bridge` 결과 조립 → 실무 웹 `result[]` 파싱.

### 14.5 신규 T2V 핸들러 템플릿

`ebs_handler.py`:

```python
def _on_req_my_feature(self, event: carb.events.IEvent) -> None:
    bridge_res = handle_my_feature(event.payload)
    if int(bridge_res.get("code", 0)) != 0:
        self.dispatch_event("V2T_response_my_feature", {
            "code": 1, "message": str(bridge_res.get("message", "failed")), "data": {},
        })
        return
    self.dispatch_event("V2T_response_my_feature", {
        "code": 0, "message": "success", "data": bridge_res.get("data") or {},
    })
```

`tbs_sim_bridge.py`:

```python
def handle_my_feature(payload: Any) -> Dict[str, Any]:
    pl = _event_payload_to_dict(payload)
    def _work() -> Dict[str, Any]:
        ext = require_tbs_extension_instance()
        return _ok({})
    try:
        return run_on_main_thread(_work)
    except Exception as exc:
        return _err(str(exc))
```

### 14.6 수정 후 검증

- [ ] §5 = 실무 웹 = `hyviewMessaging.ts`
- [ ] Kit: `EBSHandler registered N incoming events`
- [ ] T2V → V2T `code: 0`
- [ ] `start` → `data.result[]` prerun v2
- [ ] §9 변경 이력 한 줄 추가

### 14.7 흔한 실수

| 실수 | 결과 | 올바른 방법 |
|------|------|-------------|
| connected 전 T2V | 무반응 | 연결 후 전송 |
| `ebs_active`, `config`, `results` | 구버전/불일치 | §5: `ebs_enable`, `configs`, `result` |
| V2T 이름만 코드 변경 | 웹 무시 | 웹 수신부 동시 수정 |
| handler에만 UI 로직 | 스레드 오류 | `tbs_sim_bridge` + `run_on_main_thread` |
| HTTP 8720 | 동작 안 함 | livestream.messaging |
| 문서 미수정 | 재불일치 | **문서 먼저** |
