# Kit 앱 · 스트리밍 · HTTP 브리지 · TbsSimulation 연결 구조

이 문서는 **화면에 Kit이 스트리밍으로 보이는 것**과 **웹 제어창(`TbsSimulation.tsx`)이 Kit에 명령을 보내는 것**이 **서로 다른 경로**임을 설명합니다.
(아이도 이해할 수 있게, 비유와 실제 코드 위치를 함께 적었습니다.)

---

## 1. 머릿속에 둘 그림

| 구분 | 하는 일 | 쉬운 비유 |
|------|---------|-----------|
| **스트리밍** | Kit 화면을 브라우저에 **동영상처럼** 보여 줌 | TV로 경기 중계 보기 |
| **HTTP 브리지 + 웹 제어창** | 브라우저 패널이 Kit 프로세스에 **글자(JSON)** 로 “버튼 눌렀어”라고 보냄 | 리모컨으로 셋톱박스에 신호 보내기 |

**중요:** 스트리밍 영상을 클릭해서 시뮬이 돌아가는 구조가 **아닙니다.**
보는 화면(스트리밍)과 명령 줄(HTTP)은 **같은 전선이 아닙니다.**

---

## 2. 브라우저 한 페이지 안에서

```
[브라우저 탭 — 예: http://localhost:5173 ]

┌──────────────────────────────────────────────┐
│  StreamManager (또는 회사 템플릿 스트리밍 UI)   │  ← Kit 화면만 “보여 줌” (WebRTC 등)
├──────────────────────────────────────────────┤
│  <TbsSimulation />  (React 패널)              │  ← fetch 로 Kit API만 호출
└──────────────────────────────────────────────┘
```

- **위쪽 스트리밍**: GPU/인코딩/시그널링 파이프. **`TbsSimulation.tsx` 코드와 직접 연결되지 않음.**
- **아래/옆 패널**: `fetch` → **Kit 안의 HTTP 브리지** → **같은 Kit 앱**의 Python(`ext`) → **제어창·로드 로직**.

---

## 3. Kit 쪽: 브리지가 언제 켜지나

확장 진입점 `extension.py`에서 조건에 맞으면 **`start_tbs_remote_http_bridge(self)`** 가 호출됩니다.
여기서 넘기는 `self`가 곧 **확장 인스턴스 `ext`** 이며, 브리지가 **`_ext_ref`** 로 저장해 두었다가 명령을 처리할 때 같은 객체를 사용합니다.

- 파일: `morph/tbs_control_1/extension.py`
- 관련: `kit_remote_http_bridge` 모듈의 `start_tbs_remote_http_bridge` / `stop_tbs_remote_http_bridge`

즉, **Kit 프로세스 안**에서 **작은 HTTP 서버**가 뜨고, 그게 웹 제어창과 대화하는 **창구**입니다.
기본 포트는 `kit_remote_http_bridge.py` 안의 설정(환경 변수 `TBS_REMOTE_UI_PORT` 등)을 따릅니다(보통 8720).

---

## 4. 브리지가 실제로 하는 일 (`kit_remote_http_bridge.py`)

### 4.1 엔드포인트(브라우저가 부르는 주소)

| 메서드 | 경로 | 역할 |
|--------|------|------|
| `GET` | `/api/state` | Kit 제어창에 이미 올라가 있는 텍스트(진행 로그, USD 상태, 포트 등)를 **JSON으로 복사** |
| `GET` | `/api/resources` | 샘플 USD 목록 |
| `POST` | `/api/command` | `{ "cmd": "sim_start", ... }` 형태로 **명령**을 보내면, 내부에서 `control_window` / `load_window` 함수 호출 |

정적 파일(`index.html`, `tbs_panel.js` 등)은 브리지가 `web/tbs_kit_remote/` 에서 서빙할 수 있지만, **회사 Vite 앱의 `TbsSimulation`** 은 보통 **5173에서 돌아가므로** 별도로 `fetch`만 맞추면 됩니다.

### 4.2 왜 `_run_on_main` 이 있나

HTTP 요청은 **별도 스레드**에서 들어오지만, Omni UI·시뮬 로직은 **메인 스레드**에서만 안전합니다.
그래서 브리지는 요청을 **메인 큐에 넣었다가** Kit 업데이트 루프에서 실행합니다.

흐름 요약:

1. 브라우저가 `POST /api/command` 로 JSON 전송
2. 브리지가 `_dispatch_command(ext, data)` 를 **메인 스레드에서** 실행
3. `cmd` 가 `sim_start` 이면 → `on_sim_start_clicked(ext)` 등 **제어창과 동일한 함수** 호출

→ **웹 전용 새 엔진이 아니라, 기존 `control_window.py` 로직을 재사용**합니다.

---

## 5. 웹 쪽: `TbsSimulation.tsx`

### 5.1 역할

- **스트리밍 컴포넌트(`StreamManager`)를 포함하지 않습니다.**
  회사 페이지에서 **스트리밍과 나란히** `<TbsSimulation />` 만 배치하면 됩니다.
- **오직 `fetch`** 로 `/api/state`, `/api/command` 를 호출합니다.

### 5.2 API 베이스 URL (`getKitApiBase`)

- 환경 변수 `VITE_TBS_KIT_API_BASE` 가 있으면 그 주소(예: `http://127.0.0.1:8720`)를 앞에 붙입니다.
- 비어 있으면 **상대 경로**만 씁니다 → 개발 시 Vite에서 **`/api` → `http://127.0.0.1:8720`** 프록시를 쓰는 방식과 맞춥니다.

자세한 프록시 예시는 같은 폴더의 `vite-dev-proxy-snippet.txt` 를 참고하세요.

### 5.3 상태 폴링

`GET /api/state` 를 주기적으로 호출해 패널 글자를 갱신합니다.
이건 **스트리밍 영상을 분석하는 것이 아니라**, Kit 쪽 **제어창이 이미 갱신한 라벨·텍스트**를 JSON으로 읽어 오는 것에 가깝습니다.

### 5.4 버튼·체크박스

`POST /api/command` 에 `cmd` 와 필요한 필드를 넣습니다.
예: `sim_start`, `load_usd`, `kit_chrome_hide` 등 — 모두 `kit_remote_http_bridge.py` 의 `_dispatch_command` 분기와 대응합니다.

---

## 6. 한 줄 요약

**스트리밍 = 보여 주기**, **HTTP 브리지 = 명령 보내기** — 둘은 **다른 줄**이다.
`TbsSimulation`은 **브리지가 열어 둔 포트(기본 8720)** 만 알면 되고, **스트리밍 URL·포트와는 별개**로 동작한다.

---

## 7. 실행 순서 체크리스트

1. **Kit 앱 실행** — 확장이 로드되고 브리지가 떠 있어야 함.
2. (웹 개발 시) **Vite `pnpm dev`** — 5173 등.
3. 브라우저에서 스트리밍 + `TbsSimulation` 이 있는 페이지 열기.
4. `TbsSimulation` 의 `fetch` 가 **같은 PC의 브리지**에 도달하는지(프록시 또는 `VITE_TBS_KIT_API_BASE`) 확인.

---

## 8. 관련 파일 (이 저장소 기준)

| 경로 | 설명 |
|------|------|
| `morph/tbs_control_1/extension.py` | 브리지 시작/종료 |
| `morph/tbs_control_1/kit_remote_http_bridge.py` | HTTP 서버, `/api/*`, `_dispatch_command` |
| `morph/tbs_control_1/control_window.py` | 실제 TBS 제어 UI·핸들러 |
| `morph/tbs_control_1/load_window.py` | USD Load 로직 |
| `web/streaming_ui/TbsSimulation.tsx` | 회사 웹 프로젝트에 복사해 쓰는 React 패널 |
| `web/tbs_kit_remote/tbs_panel.js` | 브리지가 서빙하는 순수 HTML/JS 원격 패널(동일 API) |
| `web/streaming_ui/vite-dev-proxy-snippet.txt` | Vite 프록시 설정 예시 |

---

*이 문서는 `web/streaming_ui/CONNECTION_GUIDE.md` 에 있으며, 스트리밍 UI와 별도로 버전 관리할 수 있습니다.*
