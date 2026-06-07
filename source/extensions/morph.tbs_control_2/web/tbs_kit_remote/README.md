# TBS Kit 원격 UI — 연결 구조와 작업 가이드

이 문서는 **브라우저(웹 페이지)** 가 **Omniverse Kit 안의 `morph.tbs_control_1` 확장**과 어떻게 붙어 있는지, 그리고 **새 UI를 추가할 때 무엇을 어디에 고치면 되는지**를 처음 보는 사람도 따라 할 수 있게 설명합니다.

---

## 1. 한 줄로 요약

- **Kit 프로세스**가 작은 **HTTP 서버(브리지)** 를 띄웁니다.
- **브라우저**는 그 주소(`http://127.0.0.1:8720/`)로 **HTML/CSS/JS**를 받아 화면을 그립니다.
- 버튼을 누르면 브라우저가 **JSON 명령**을 `POST`로 보내고, 브리지가 **Kit 메인 스레드**에서 기존 확장이 쓰던 함수(예: 시뮬 시작)를 호출합니다.
- 진행현황·포트 상태 등은 브라우저가 **일정 시간마다 `GET`으로 상태를 읽어와** 화면을 갱신합니다.

**중요:** 시뮬 로직·USD·omni.ui 제어창 코드는 그대로 두고, **“원격에서 같은 함수를 부르는 통로”**만 브리지가 담당합니다.

---

## 2. 전체 그림 (데이터가 흐르는 순서)

```
[브라우저]
   │
   │ ① 페이지 열기
   ▼
GET http://127.0.0.1:8720/
   │     → kit_remote_http_bridge 가 web/tbs_kit_remote/ 안의
   │       index.html, tbs_panel.css, tbs_panel.js 를 파일로 돌려줌
   │
   │ ② (예) "시작" 클릭
   ▼
POST http://127.0.0.1:8720/api/command
   Body: {"cmd":"sim_start", "fields": { ... 모든 입력값 ... }}
   │
   │     → 브리지의 HTTP 스레드가 받음
   │     → “메인 스레드에서 실행할 함수”를 **큐**에 넣음
   ▼
[Kit 메인 스레드] (매 프레임 `update` 이벤트에서 큐 처리)
   │
   │     → ext(확장 인스턴스)의 모델에 fields 반영
   │     → on_sim_start_clicked(ext) 호출  ← 기존 제어창과 동일
   ▼
[simulation_engine 등] 시뮬 진행, on_progress 콜백으로 UI 큐에 전달 …

[브라우저]
   │
   │ ③ 약 0.4초마다
   ▼
GET http://127.0.0.1:8720/api/state
   │     → 메인 스레드에서 ext의 라벨/포트 셀 텍스트를 읽어 JSON으로 반환
   ▼
tbs_panel.js 가 진행현황·이력·포트 칸 등을 갱신
```

---

## 3. 관련 파일이 어디에 있는지

| 역할 | 경로 (저장소 기준) |
|------|-------------------|
| 브리지 (HTTP 서버 + Kit 연동) | `morph/tbs_control_1/kit_remote_http_bridge.py` |
| 확장 켜질 때 브리지 시작/종료 | `morph/tbs_control_1/extension.py` (기본 켜짐; `TBS_REMOTE_UI=0` 등으로 끔) |
| 웹 페이지 | `web/tbs_kit_remote/index.html`, `tbs_panel.css`, `tbs_panel.js` |
| 기존 Kit UI·시뮬 시작 등 | `morph/tbs_control_1/control_window.py`, `load_window.py` 등 (수정 없이 **함수만 재사용**) |

확장 패키지 루트는 보통 다음과 같습니다.

`source/extensions/morph.tbs_control_1/`
그 아래 `morph/tbs_control_1/` 가 Python 패키지, `web/tbs_kit_remote/` 가 정적 웹 파일입니다.

---

## 4. 브리지가 켜지는 조건

1. **`TBS_REMOTE_UI` 를 끄는 값으로 설정하지 않은 채** Kit를 실행하면 됩니다. (기본 **켜짐**.)
2. 브리지를 **끄려면** 환경 변수 **`TBS_REMOTE_UI=0`** (또는 `false`, `no`, `off`)를 Kit 실행 **전에** 설정합니다.
3. `extension.py`의 `on_startup` 끝에서 `start_tbs_remote_http_bridge(self)` 가 호출됩니다.
4. 기본 주소는 **`http://127.0.0.1:8720/`** 입니다.
   포트를 바꾸려면 **`TBS_REMOTE_UI_PORT`** (예: `8721`)를 설정합니다.

Kit 로그에 다음과 비슷한 줄이 보이면 성공입니다.

```text
[TBS Remote UI] http://127.0.0.1:8720/  (정적+API)
```

### 4.1 원격 PC에서 브라우저만 쓰는 경우 (다른 기기에서 접속)

기본값은 **`127.0.0.1`** 에만 서버를 붙입니다. 이 주소는 **“Kit가 돌아가는 그 PC 자기 자신”** 에서만 의미가 있어서, **집/회사의 다른 노트북·폰 브라우저**에서는 `http://127.0.0.1:8720` 으로는 **절대** 붙을 수 없습니다. (127.0.0.1은 항상 “지금 보고 있는 그 기기”를 가리킵니다.)

원격에서 접속하려면 아래를 **순서대로** 맞춥니다.

#### (1) Kit 쪽: 어떤 네트워크 인터페이스에 열지 — 환경 변수

| 환경 변수 | 하는 일 | 어디서 설정 |
|-----------|---------|-------------|
| **`TBS_REMOTE_UI_BIND`** | HTTP 서버가 **어느 주소에서 들을지** | Kit 실행 **전에** OS 환경 변수 또는 `repo.bat` / 런처 앞의 `set` / PowerShell `$env:...` |
| **`TBS_REMOTE_UI_PORT`** | 포트 번호 (기본 `8720`) | 위와 동일 |

**값 예시**

- **로컬만 (기본, 가장 안전)**
  - 설정 안 함 또는 `TBS_REMOTE_UI_BIND=127.0.0.1`
  - 같은 PC의 브라우저만 `http://127.0.0.1:8720/` 로 접속.

- **같은 공유기(LAN) 안의 다른 PC/폰에서 접속**
  - `TBS_REMOTE_UI_BIND=0.0.0.0`
  - 의미: “이 PC의 **모든 IPv4 주소**에서 포트를 연다.”
  - 원격 브라우저 주소는 `http://<Kit가-돌아가는-PC의-LAN-IP>:8720/`
    예: `http://192.168.0.15:8720/`
  - LAN IP 확인: Kit PC에서 `ipconfig`(Windows) / `ip a`(Linux) 등.

- **별칭 (구현상 동일)**
  - `TBS_REMOTE_UI_BIND=*` 또는 `all` → 내부적으로 `0.0.0.0` 과 같이 처리됩니다.

**코드에서 실제로 읽는 위치**
`morph/tbs_control_1/kit_remote_http_bridge.py` 의 `start_tbs_remote_http_bridge()` 안에서 `ThreadingHTTPServer((bind, port), ...)` 로 바인드합니다. 동작을 바꾸고 싶다면 이 파일과 위 환경 변수를 보면 됩니다.

`0.0.0.0` 으로 띄우면 Kit 로그에 대략 다음처럼 나옵니다.

```text
[TBS Remote UI] listen 0.0.0.0:8720 — 로컬: http://127.0.0.1:8720/ | 원격 PC 브라우저: http://<이-Kit-PC의-LAN-IP>:8720/
```

#### (2) OS / 클라우드 방화벽

브리지가 열려도 **방화벽이 포트를 막으면** 원격 브라우저는 연결되지 않습니다.

- **Windows**: “고급 보안이 포함된 Windows 방화벽”에서 **인바운드 규칙**으로 `TBS_REMOTE_UI_PORT`(예: 8720) TCP 허용. (가능하면 **프로필을 Private** 으로만 제한하는 것이 안전합니다.)
- **클라우드 VM**(AWS/Azure 등): 보안 그룹 / NSG 에 해당 포트 인바운드 허용 + **공인 IP**로 접속하는 경우, **인증 없이 열면 누구나 API를 호출할 수 있음**에 유의합니다.

#### (3) 웹 페이지(JS) 쪽: API 주소를 “원격 Kit”로 지정

`index.html` 을 `127.0.0.1` 에서 직접 열지 않고, **다른 서버의 React** 등에서 쓰는 경우:

```javascript
window.TBS_KIT_REMOTE_API = "http://192.168.0.15:8720";  // Kit PC의 LAN IP + 포트
```

`fetch` / 폴링이 전부 이 베이스 URL로 가야 합니다. (`tbs_panel.js` 는 이미 `window.TBS_KIT_REMOTE_API` 를 지원합니다.)

HTTPS 로만 서비스하는 **프로덕션 웹**에서 HTTP 인 Kit API를 부르면 **mixed content** 로 브라우저가 막을 수 있습니다. 그때는 **리버스 프록시(Nginx 등)로 HTTPS 터미네이션**하거나, Kit/브리지 앞단을 설계해야 합니다(별도 인프라 작업).

#### (4) 보안 (원격 시 필수으로 생각할 것)

`0.0.0.0` + 방화벽 개방은 **누구나 `/api/command` 로 시뮬 시작·USD 로드 등을 호출할 수 있는 상태**와 같습니다. 운영 환경에서는 **VPN**, **인증 토큰**, **리버스 프록시에서 IP 제한** 등을 검토하세요. (현재 브리지 코드에는 로그인 기능이 없습니다.)

---

## 5. 왜 “메인 스레드 큐”가 필요한가

Omniverse Kit / `omni.ui` 위젯은 **메인 스레드에서만** 안전하게 다룹니다.
HTTP 요청은 **별도 스레드**에서 처리되므로, 브리지는 요청을 받으면 곧바로 `ext`를 건드리지 않고:

1. `Future`와 함께 실행할 함수를 **`_pending_main` 큐**에 넣고
2. **`get_update_event_stream()`** 구독 콜백(`_pump_main_queue`)이 **Kit 메인 스레드**에서 큐를 비우며 실행합니다.

그래서 `on_sim_start_clicked`, `open_stage` 같은 기존 코드를 **그대로** 호출할 수 있습니다.

---

## 6. API 상세 (문서만 보고 연동할 때)

### 6.1 정적 파일

- `GET /` → `index.html`
- `GET /tbs_panel.css`, `GET /tbs_panel.js` → 같은 폴더의 파일

### 6.2 상태 스냅샷 (폴링용)

**`GET /api/state`**

응답 JSON 예시 (필드는 구현에 따라 조금 달라질 수 있음):

- `usd_status` — TBS 제어창 상단 USD Load 영역 상태 문구
- `progress` — 진행현황 패널 텍스트
- `history` — 이력 로그 패널 텍스트
- `port_header` — 포트 상태 제목 줄
- `ports` — `BP1`, `BP2`, … 각 칸에 표시할 문자열
- `ep3_visible` — EP3 칸 표시 여부
- `kit_app` — 앱 이름(참고용)

### 6.3 샘플 USD 목록

**`GET /api/resources`**

```json
{ "items": [ { "name": "표시이름", "path": "실제경로" }, ... ] }
```

`load_window` / `usd_loader_utils.get_resource_usd_list()` 와 동일 소스입니다.

### 6.4 명령 실행

**`POST /api/command`**
`Content-Type: application/json`

본문은 **반드시 JSON 객체**이며, 최소한 `"cmd"` 가 필요합니다.

| cmd | 설명 | 추가 필드 |
|-----|------|-----------|
| `load_usd` | USD 열기 | `path` (문자열), `resource_index` (콤보 인덱스, 0=직접경로) |
| `sim_start` | 시뮬 시작 | `fields` — 아래 **7절**과 같은 키들 |
| `sim_stop` | 정지 | 없음 |
| `sim_reset` | 리셋 | 없음 |
| `prim_refresh` | prim 목록 새로고침 | 없음 |
| `log_mode` | 표시모드 | `index` : 0=둘다, 1=진행현황만, 2=이력만 |
| `copy_progress` | 진행현황 클립보드 복사 | 없음 |
| `xml_ok` | XML 생성 OK | `fields` (시퀀스·포트 번호 등) |
| `xml_run` | 제너레이터 역파싱 실행 | 없음 (이미 생성된 XML 사용) |

성공 시 대략 `{ "ok": true }` 가 돌아옵니다.

### 6.5 CORS (다른 포트의 React 개발 서버)

`/api/*` 응답에 `Access-Control-Allow-Origin: *` 가 붙습니다.
브라우저에서 `http://localhost:3000` 같은 **다른 출처**로 페이지를 띄울 때는, JS에서 기본 URL을 지정합니다.

```html
<script>
  window.TBS_KIT_REMOTE_API = "http://127.0.0.1:8720";
</script>
<script src="http://127.0.0.1:8720/tbs_panel.js"></script>
```

또는 React 코드에서 `fetch("http://127.0.0.1:8720/api/state")` 처럼 **전체 URL**을 씁니다.

---

## 7. `sim_start` 의 `fields` — 웹 입력과 Kit 모델 매핑

브리지의 `_apply_web_fields()` (`kit_remote_http_bridge.py`) 가 이 값들을 읽어 **`ext`의 `Simple*Model`** 에 넣은 뒤 `on_sim_start_clicked(ext)` 를 호출합니다.
즉 **Kit 제어창에서 손으로 맞추던 것과 같은 상태**를 만든 다음 시작하는 것과 동일합니다.

| JSON 키 (fields 안) | 의미 |
|---------------------|------|
| `lot_count` | LOT 수 |
| `ep_count_index` | 0 → EP 2개, 1 → EP 3개 |
| `lot_spawn_min`, `lot_spawn_max` | LOT 생성 간격(초) |
| `pickup_min`, `pickup_max` | 회수 이벤트 간격(초) |
| `speed` | 시뮬 속도 배율 |
| `log_interval` | 진행 로그 주기(초) → `SimulationLogConfig.progress_interval_sec` |
| `confirm_each` | 각 공정 확인 체크 |
| `init_bp1` … `init_ep3` | 초기 적재 포트 체크 |
| `oht_min`, `oht_max` | OHT→BP/EP |
| `bp1_bp_min`, `bp1_bp_max` | BP1→BP |
| `bp_ep_min`, `bp_ep_max` | BP→EP |
| `ep_oht_min`, `ep_oht_max` | EP→OHT |
| `priority_prefix` | 우선 표시 접두사 |
| `xml_seq_index` | XML 시퀀스 콤보 인덱스 (0~6) |
| `xml_from`, `xml_to` | FROM/TO 포트 번호 |
| `xml_port_id` | PORT_ID |

새 입력란을 웹에 추가했다면:

1. `fields` 에 키를 하나 정해 넣고
2. `kit_remote_http_bridge.py` 의 `_apply_web_fields()` 에서 해당 `ext._sim_*_model` 에 반영하는 한 줄을 추가하고
3. (필요하면) `on_sim_start_clicked` 가 이미 그 모델을 읽는지 `control_window.py` 에서 확인합니다.

---

## 8. 초보자용 체크리스트 — “웹에서 새 버튼 하나 연결하기”

아래 순서대로 하면 됩니다.

1. **Kit 쪽에 이미 있는 동작 확인**
   예: `control_window.py` 에 `on_foo_clicked(ext)` 가 있다면 그 함수가 최종적으로 무슨 일을 하는지 읽습니다.

2. **브리지에 `cmd` 추가** (`kit_remote_http_bridge.py`)
   - `_dispatch_command()` 안에 `if cmd == "my_foo":` 분기를 추가합니다.
   - 메인 스레드에서만 실행되므로, 안에서는 `on_foo_clicked(_ext_ref)` 처럼 **기존 함수 호출**만 하면 됩니다.

3. **웹에서 POST 보내기** (`tbs_panel.js` 또는 여러분의 React 코드)
   ```javascript
   await fetch((window.TBS_KIT_REMOTE_API || "") + "/api/command", {
     method: "POST",
     headers: { "Content-Type": "application/json" },
     body: JSON.stringify({ cmd: "my_foo" }),
   });
   ```

4. **버튼에 연결**
   `addEventListener("click", ...)` 안에서 위 `fetch` 를 호출합니다.

5. **Kit 재시작**
   Python 브리지 코드를 바꿨으므로 확장이 다시 로드되도록 Kit를 재실행하거나 핫리로드 정책에 맞게 갱신합니다.

6. **브라우저 개발자 도구(F12) → Network**
   `POST /api/command` 가 200 인지, 응답 JSON에 `ok: true` 인지 확인합니다.

---

## 9. 자주 생기는 문제

| 증상 | 확인할 것 |
|------|-----------|
| 페이지가 안 열림 | `TBS_REMOTE_UI` 로 브리지를 끄지 않았는지, Kit가 떠 있는지, 포트 8720이 다른 프로그램에 안 쓰이는지 |
| 원격 PC에서 타임아웃 | `TBS_REMOTE_UI_BIND=0.0.0.0` 인지, Kit PC 방화벽·LAN IP·포트가 맞는지 |
| API만 CORS 에러 | `fetch` URL이 `http://127.0.0.1:8720` 전체인지, OPTIONS 프리플라이트가 막히지 않는지 |
| 버튼은 눌리는데 Kit 반응 없음 | Network에서 POST 본문의 `cmd` 철자, 브리지 `_dispatch_command` 분기 유무 |
| 상태만 안 갱신됨 | `GET /api/state` 가 200인지, 폴링 간격·방화벽 |

---

## 10. 보안 참고

- 기본 **`TBS_REMOTE_UI_BIND=127.0.0.1`** 이면 같은 PC의 브라우저에서만 접근하기 쉽습니다.
- **`0.0.0.0`** 으로 열면 LAN/인터넷에 따라 **다른 사람도 API에 접근할 수 있으므로**, 방화벽·VPN·인증 전략을 반드시 같이 설계하세요.

---

## 11. 요약

- **연결 고리**는 `kit_remote_http_bridge.py` 하나이고, **역할**은 “HTTP JSON ↔ 메인 스레드에서 `ext` + 기존 함수 호출”입니다.
- **웹 파일**은 정적 자산이며, 동작의 진실은 여전히 **Kit 안의 확장 코드**에 있습니다.
- 기능을 늘리려면 **브리지에 `cmd` 한 줄**, **웹에서 `fetch` 한 번**, 필요 시 **`fields` 매핑**만 맞추면 됩니다.

이 README는 `web/tbs_kit_remote/README.md` 에 있으므로, 저장소에서 웹 UI 폴더와 함께 버전 관리할 수 있습니다.
