# LAM 웹 UI — 확인 방법 · 수정 위치 · 브리지 원리

이 문서는 **`morph.lam_web_bridge`** 확장(HTTP 브리지 + 웹 파일)과 **`morph.lam_control`** 확장(Kit LAM 본체)의 연동을 설명합니다.

TBS의 `web/tbs_kit_remote/README.md` · `web/streaming_ui/CONNECTION_GUIDE.md` 와 같은 역할이며, LAM 전용입니다.

---

## 0. 확장 분리 (2026-05)

| 확장 | 역할 |
|------|------|
| **`morph.lam_control`** | LAM Window, registry/scheduler, CSV Play, Open Master 등 **Kit 전용** |
| **`morph.lam_web_bridge`** | HTTP **8720** 브리지, `web/lam_kit_remote`, `web/streaming_ui` |

- **의존:** `morph.lam_web_bridge` → `morph.lam_control` (역방향 아님).
- **연결:** `morph.lam_control.remote_api` — `LamKitSession` 등록 / `get_session()`.
- **앱:** `morph.editor.kit`에 두 확장 모두 등록. 웹만 끄려면 `morph.lam_web_bridge`만 제거.
- **코어만:** `lam_control`만 로드하면 Kit UI는 동작, 브라우저 8720은 **없음**.

---

## 1. 한 줄 요약

| 구분 | 설명 |
|------|------|
| **브리지** | `morph.lam_web_bridge` — `lam_remote_http_bridge.py` (기본 포트 **8720**) |
| **웹 UI** | 브라우저 HTML/JS 또는 React — Kit 화면을 직접 조작하지 않음 |
| **Kit 본체** | `morph.lam_control` — `LamWindow`, `simulation_play.py` 등 |

**스트리밍(WebRTC로 Kit 화면 보기)** 과 **HTTP 브리지(명령 JSON)** 는 **별개**입니다. 이 문서는 HTTP 브리지 + 웹 패널만 다룹니다.

---

## 2. 전체 구조

```
┌─────────────────────────────────────────────────────────────────┐
│  브라우저                                                         │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐ │
│  │ (선택) 스트리밍 영상    │  │ 웹 패널                           │ │
│  │ StreamManager 등      │  │ lam_kit_remote 또는 LamSimulation │ │
│  └──────────────────────┘  └───────────────┬──────────────────┘ │
└────────────────────────────────────────────│────────────────────┘
                                             │ fetch
                                             │ GET  /api/state
                                             │ POST /api/command
                                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Kit 프로세스                                                    │
│  ┌─ morph.lam_web_bridge ─────────────────────────────────────┐ │
│  │ lam_remote_http_bridge.py  (HTTP 스레드 + 메인 큐)          │ │
│  │   get_session() → _dispatch_command → _cmd_*               │ │
│  └───────────────────────────┬────────────────────────────────┘ │
│                              │ remote_api.LamKitSession         │
│  ┌─ morph.lam_control ───────▼────────────────────────────────┐ │
│  │ LamWindow · simulation_play · registry · scheduler         │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 2.1 웹 UI가 있는 세 가지 형태

| 형태 | 경로 (`morph.lam_web_bridge` 기준) | 언제 쓰나 |
|------|------|-----------|
| **A. Kit가 직접 서빙** | `web/lam_kit_remote/` | 가장 단순. 브라우저에서 `http://127.0.0.1:8720/` 만 열면 됨 |
| **B. Vite 개발 서버** | `web/streaming_ui/` | React 패널 로컬 미리보기 (`npm run dev`, 포트 **5174**) |
| **C. 회사 스트리밍 앱** | `LamSimulation.tsx` 복사 | StreamManager 옆에 `<LamSimulation />` 배치 |

A·B·C 모두 **같은 HTTP API**(`8720` 브리지)만 호출합니다. UI 구현만 HTML/JS vs React 차이입니다.

---

## 3. 웹 UI 확인 방법 (단계별)

### 3.0 사전 조건

1. **저장소 빌드 1회** — 새 확장 `morph.lam_web_bridge` 추가 후 최초 1회는 반드시 실행:
   ```powershell
   .\repo.bat build
   ```
   (`_build/apps/exts.deps.generated.kit` 에 확장이 등록되어야 Kit이 의존성을 찾습니다.)
2. **Kit 앱**을 실행한다 (`morph.lam_control` + **`morph.lam_web_bridge`** 가 로드되어야 8720 사용 가능).
2. **TBS 확장과 동시에 8720 포트를 쓰지 않는다** — 둘 다 브리지를 켜면 포트 충돌 가능. LAM만 쓸 때는 TBS 원격 UI를 끄거나 포트를 나눈다.
3. 환경 변수 (Kit 실행 **전**에 설정):

| 변수 | 기본 | 설명 |
|------|------|------|
| `TBS_REMOTE_UI` | (켜짐) | `0` / `false` / `no` / `off` 이면 브리지 **끔** |
| `TBS_REMOTE_UI_PORT` | `8720` | HTTP 포트 |
| `TBS_REMOTE_UI_BIND` | `127.0.0.1` | `0.0.0.0` 이면 LAN에서 접속 가능 |

### 3.1 방법 A — Kit가 서빙하는 패널 (권장·가장 단순)

**① Kit 실행 후 로그 확인**

콘솔에 다음과 비슷한 줄이 있어야 합니다.

```text
[LAM Remote UI] http://127.0.0.1:8720/  (LAM 정적+API)
```

없으면: `TBS_REMOTE_UI=0` 이 설정됐는지, 확장 로드 실패 여부를 확인합니다.

**② 브라우저에서 페이지 열기**

- 주소: **http://127.0.0.1:8720/**
- 상단 배너가 초록(연결됨)으로 바뀌는지 확인합니다.
- `합성 USD` 경로, `CSV 폴더`가 채워지면 `GET /api/state` 가 정상입니다.
- **입력 유지:** 사용자가 바꾼 합성 경로·CSV 폴더·파일·배속은 400ms 폴링으로 **덮어쓰지 않음**. 타임라인·진행·로그만 서버와 동기. (목록 새로고침 / Play 등으로 서버에 반영된 뒤 해당 필드만 다시 맞춤)

**③ Open Master 동작 확인**

1. 합성 USD 경로 입력 (또는 기본값 유지).
2. **Open Master** 클릭.
3. Kit 쪽 LAM Window 로그 / Stage에 master가 열리고 Discover·Extract가 진행되는지 확인.

**④ CSV Play 동작 확인**

1. **목록 새로고침** → CSV 콤보에 파일 표시.
2. (선택) **타임라인 갱신** → 아래 텍스트 영역에 스케줄 문자열.
3. **CSV Play** → Kit 뷰포트에서 애니메이션·visibility 변화.
4. **CSV 중지** → 재생 멈춤.

**⑤ 개발자 도구로 API 직접 확인 (선택)**

브라우저 F12 → Network:

- 주기적 `GET http://127.0.0.1:8720/api/state` (약 400ms)
- 버튼 클릭 시 `POST http://127.0.0.1:8720/api/command` + JSON body

터미널에서 curl 예:

```powershell
curl http://127.0.0.1:8720/api/state
curl -X POST http://127.0.0.1:8720/api/command -H "Content-Type: application/json" -d "{\"cmd\":\"csv_refresh_list\"}"
```

### 3.2 방법 B — React 개발 서버 (Vite)

UI를 `LamSimulation.tsx` 기준으로 고칠 때 유용합니다.

```powershell
cd source\extensions\morph.lam_web_bridge\web\streaming_ui
copy .env.example .env
npm install
npm run dev
```

- 브라우저: **http://127.0.0.1:5174/** (TBS preview 5173과 구분)
- `vite.config.ts`에 `/api` → `8720` 프록시가 들어 있음.
- `.env`에서 `VITE_TBS_KIT_API_BASE=` 를 **비워 두면** 상대 경로 `/api/...` 로 프록시를 탑니다.

**체크리스트**

1. Kit 실행 + `[LAM Remote UI] http://127.0.0.1:8720/` 로그.
2. `npm run dev` 후 5174 페이지에서 연결 배너가 초록.
3. A와 동일하게 Open Master / CSV Play 테스트.

### 3.3 방법 C — 회사 스트리밍 페이지

1. `web/streaming_ui/LamSimulation.tsx` + `LamSimulation.module.css` 를 회사 프로젝트에 복사.
2. 스트리밍 레이아웃에 `<LamSimulation />` 추가.
3. API 베이스:
   - Vite 프록시: `VITE_TBS_KIT_API_BASE=` (빈 값)
   - 직접 연결: `VITE_TBS_KIT_API_BASE=http://127.0.0.1:8720`
   - 또는 HTML 로드 전: `window.TBS_KIT_REMOTE_API = "http://127.0.0.1:8720"`

---

## 4. UI는 어디서 수정하나

### 4.1 파일 맵

| 수정 목적 | 파일 (`morph.lam_web_bridge` 또는 `morph.lam_control`) |
|-----------|------|
| **레이아웃·버튼·라벨 (순수 HTML)** | `lam_web_bridge/web/lam_kit_remote/index.html` |
| **동작·fetch·폴링 (순수 JS)** | `lam_web_bridge/web/lam_kit_remote/lam_panel.js` |
| **스타일 (순수 CSS)** | `lam_web_bridge/web/lam_kit_remote/lam_panel.css` |
| **React 컴포넌트** | `lam_web_bridge/web/streaming_ui/LamSimulation.tsx` |
| **React 스타일** | `lam_web_bridge/web/streaming_ui/LamSimulation.module.css` |
| **새 API 명령·HTTP 연동** | `lam_web_bridge/morph/lam_web_bridge/lam_remote_http_bridge.py` |
| **세션 공개 API** | `lam_control/morph/lam_control/remote_api.py` |
| **실제 Open Master / CSV 로직** | `lam_control/.../lam_window.py`, `simulation_play.py` |
| **브리지 켜기/끄기** | `lam_web_bridge/morph/lam_web_bridge/extension.py` |
| **세션 등록** | `lam_control/morph/lam_control/extension.py` (`set_session`) |

**원칙:** 화면만 바꿀 때는 `lam_web_bridge/web/`. Kit **새 동작**은 `lam_control`에 구현 후, 브리지 `cmd`에서 `get_session()` + `simulation_play` 호출.

### 4.2 UI 수정 예시

**버튼 하나 추가 (HTML 패널)**

1. `index.html`에 `<button id="btnMyAction">...</button>` 추가.
2. `lam_panel.js`의 `wire()` / `apiCommand({ cmd: "my_action", ... })` 추가.
3. `lam_remote_http_bridge.py`의 `_dispatch_command`에 `if cmd == "my_action":` 분기 + `_cmd_my_action()` 구현.

**React 패널도 동일 API** — `LamSimulation.tsx`에 버튼 + `apiCommand` 만 맞추면 됩니다. HTML/JS를 고칠 필요 없습니다.

### 4.3 수정 후 반영

| 패널 종류 | 반영 방법 |
|-----------|-----------|
| `lam_kit_remote` | **Kit 재시작 불필요** — 브라우저 **새로고침(F5)** 만으로 JS/CSS/HTML 반영 (브리지가 디스크에서 직접 서빙) |
| `streaming_ui` (Vite dev) | 저장 시 HMR; 안 되면 페이지 새로고침 |
| `streaming_ui` (빌드 산출물) | `npm run build` 후 `dist/` 를 회사 앱에 배포 |

**Python 브리지(`lam_remote_http_bridge.py`) 수정 시에는 Kit 확장 재로드 또는 Kit 재시작**이 필요합니다.

---

## 5. 브리지 동작 원리

### 5.1 시작·종료

1. **`morph.lam_control`** `on_startup` → `LamWindow` 생성 후 `remote_api.set_session(LamKitSession(...))`.
2. **`morph.lam_web_bridge`** `on_startup` (의존으로 나중) → `get_session()` 확인 → `start_lam_remote_http_bridge()`.
3. `on_shutdown` — web bridge가 먼저 `stop_lam_remote_http_bridge()`, 이후 lam_control이 `clear_session()`.

브리지는 `LamKitSession` (`registry`, `scheduler`, `open_master_at_path`) 만 사용합니다. `LamWindow` private 멤버에 직접 접근하지 않습니다.

### 5.2 HTTP 엔드포인트

| 메서드 | 경로 | 역할 |
|--------|------|------|
| `GET` | `/` , `/index.html` , `*.js` , `*.css` | `web/lam_kit_remote/` 정적 파일 |
| `GET` | `/api/state` | 웹 표시용 JSON 스냅샷 |
| `POST` | `/api/command` | `{ "cmd": "...", ... }` 명령 실행 |
| `OPTIONS` | `/api/*` | CORS preflight |

### 5.3 스레드와 `_run_on_main` (중요)

```
[HTTP 스레드]  POST /api/command 수신
      │
      │  _run_on_main(lambda: _dispatch_command(win, data))
      │       → 큐에 작업 enqueue
      │       → Future.result() 로 대기 (HTTP 스레드 블록)
      ▼
[Kit 메인 스레드]  update 이벤트마다 _pump_main_queue()
      │
      │  _dispatch_command → _cmd_* 실행
      ▼
[Python] LamWindow / simulation_play 함수 호출
```

- Omni UI·USD 조작은 **메인 스레드**에서만 안전합니다.
- 그래서 POST/GET `/api/state` 는 `_run_on_main` 으로 메인에서 실행합니다.

**데드락 주의 (유지보수 시 필수)**

- `_dispatch_command` 안에서는 **다시 `_run_on_main` 을 호출하지 마세요.**
- 이미 메인 스레드인데 `Future.result()` 로 기다리면 업데이트 루프가 큐를 비우지 못해 **Kit이 멈춥니다.**
- Open Master 수정 사례: `_cmd_open_master` 에서 `win._open_master_at_path(...)` 를 **직접** 호출해야 합니다.

### 5.4 웹 상태 (`_web_state`)

브리지가 유지하는 JSON 스냅샷 (일부):

| 키 | 의미 |
|----|------|
| `log` | 하단 로그 한 줄 |
| `master_path` | 합성 USD 경로 |
| `instance_count` | `win._registry.all_instances()` 개수 |
| `csv_dir`, `csv_selected`, `csv_files` | CSV 폴더·선택·목록 |
| `schedule` | 타임라인 텍스트 |
| `progress` | 빌드/재생 진행 문자열 |
| `playing`, `building` | 스레드 alive 여부 (폴링용) |

`GET /api/state` → `_snapshot(win)` 이 위 값 + registry 개수를 합쳐 반환합니다.

### 5.5 긴 작업과 백그라운드 스레드

| 작업 | 스레드 | 이유 |
|------|--------|------|
| `open_master` | **메인** (POST 처리 중) | USD open / Discover / Extract |
| `csv_timeline_refresh` (전체 빌드) | **별도 daemon** `_csv_build_thread` | 파싱·플랜 빌드가 오래 걸림 |
| `csv_play` | **별도 daemon** `_csv_play_thread` | `run_simulation_from_csv` 가 sleep·애니 대기 |
| `csv_refresh_list` | **메인** | 디렉터리 목록만 읽음 |

---

## 6. 버튼 → Kit 함수 연결표

웹 `cmd` 문자열은 `lam_panel.js` / `LamSimulation.tsx` 의 `apiCommand({ cmd: "..." })` 와 **반드시 동일**해야 합니다.

### 6.1 Open Master

| 단계 | 위치 | 내용 |
|------|------|------|
| 1 | `lam_panel.js` → `onOpenMaster()` | `POST /api/command` body: `{ "cmd": "open_master", "path": "<입력 경로>" }` |
| 2 | `lam_remote_http_bridge.py` → `_dispatch_command` | `cmd == "open_master"` → `_cmd_open_master(session, data)` |
| 3 | `_cmd_open_master` | 경로 검증 후 **`session.open_master_at_path(resolved, log_prefix="Web")`** |
| 4 | `lam_window.py` → `_open_master_at_path` | `self._master.open_master(path)` → `set_root_layer_edit_target` → `_discovery.discover()` → `_auto_extract_after_master_open()` |

Kit UI의 **Open Master** 버튼도 같은 `_open_master_at_path` 를 호출합니다. 웹은 omni.ui 를 거치지 않고 **동일 Python 메서드**만 탑니다.

### 6.2 목록 새로고침

| 단계 | 함수 |
|------|------|
| 웹 | `{ "cmd": "csv_refresh_list", "csv_dir": "..." }` |
| 브리지 | `_cmd_csv_refresh_list` → `list_csv_paths_in_directory(csv_dir)` (`simulation_play.py`) |
| 응답 | `{ "ok": true, "items": [{ "name", "path" }, ...] }` + `_web_state` 갱신 |

Kit UI: `LamSimulationCsvPlayWindow._on_refresh_clicked` → 같은 `list_csv_paths_in_directory`.

### 6.3 타임라인 갱신

| 단계 | 함수 |
|------|------|
| 웹 | `{ "cmd": "csv_timeline_refresh", "csv_dir", "csv_path", "speed_scale" }` |
| 브리지 (캐시 hit) | `get_cached_csv_playback` → `format_csv_playback_schedule` |
| 브리지 (캐시 miss) | `preview_csv_playback_schedule` (즉시 미리보기) + 백그라운드 `_worker`: `load_csv_dwell_timeline` → `build_csv_playback_plan` → 캐시 저장 |

Kit UI: `_on_schedule_refresh_clicked` — 동일 빌드 함수 사용, omni.ui StringField에 텍스트 반영.

### 6.4 CSV Play (상세 추적)

**웹에서 버튼 클릭 시 전체 흐름:**

```
[브라우저] CSV Play 클릭
    │
    ▼ POST /api/command
    {
      "cmd": "csv_play",
      "csv_dir": "...",
      "csv_path": "C:/.../file.csv",
      "speed_scale": 1.0
    }
    │
    ▼ [HTTP 스레드] _run_on_main(_dispatch_command)
    │
    ▼ [메인] _cmd_csv_play(win, data)
    │     · 경로 resolve (_resolve_csv_path)
    │     · registry = session.registry, scheduler = session.scheduler
    │     · set_csv_play_progress_ui_callback(_on_play_ui)  ← 진행 문자열을 _web_state["progress"]에
    │     · threading.Thread(target=_worker).start()  ← 메인은 즉시 return {"ok": true}
    │
    ▼ [백그라운드 _worker 스레드]
    │     get_cached_csv_playback(path)
    │     run_simulation_from_csv(registry, scheduler, csv_path=..., speed_scale=..., prepared=...)
    │
    ▼ [simulation_play.py] run_simulation_from_csv
    │     · clear_csv_playback_stop()
    │     · 캐시/빌드로 blocks 확보
    │     · run_csv_timed_playback(...)  등 — dwell 대기, JSON 이벤트, LamSequenceRunner
    │     · apply_csv_play_initial_wafer_visibility() (Play 시작 1회)
    │
    ▼ [브라우저] 400ms마다 GET /api/state → progress, playing 갱신
```

**Kit CSV Play 창과의 관계**

| 항목 | Kit UI (`LamSimulationCsvPlayWindow`) | 웹 브리지 |
|------|--------------------------------------|-----------|
| 최종 재생 함수 | **`run_simulation_from_csv(registry, scheduler, ...)`** | **동일** |
| 스레드 | `lam-sim-csv-play` daemon | `lam-web-csv-play` daemon |
| 진행 UI | `_post_kit_main_thread` → omni.ui StringField | `_on_play_ui` → `_web_state["progress"]` |
| Play 전 빌드 | UI 스레드에서 빌드 가능 | 웹은 주로 **타임라인 갱신**으로 선행 빌드; 없으면 Play 시 캐시/빌드 |

**중지 (CSV 중지)**

```
웹: { "cmd": "csv_stop" }
  → _cmd_csv_stop
  → request_stop_csv_playback(session.registry, session.scheduler)
       · _csv_play_stop_event.set()
       · LamSequenceRunner.stop()
       · stop_all_translate_animations / stop_all_rotate_animations
       · scheduler.stop_all()
```

Kit UI: `_on_csv_stop_clicked` → **동일** `request_stop_csv_playback`.

### 6.5 웹에 없는 기능 (의도적)

Kit `LamSimulationCsvPlayWindow` 하단의 **매크로/이벤트 함수 실행**·스크립트 편집기는 웹 패널에 **포함하지 않았습니다.** 추가하려면 새 `cmd` 와 `simulation_play` 의 해당 runner를 브리지에서 연결해야 합니다.

---

## 7. API 레퍼런스

### 7.1 `GET /api/state`

응답 예 (필드):

```json
{
  "log": "(대기)",
  "master_path": "C:/.../master.usd",
  "instance_count": 42,
  "csv_dir": "C:/.../lam/csv",
  "csv_selected": "C:/.../lam/csv/run1.csv",
  "csv_files": [{ "name": "run1.csv", "path": "C:/.../run1.csv" }],
  "schedule": "(타임라인 텍스트 …)",
  "progress": "(빌드·재생 진행 …)",
  "playing": false,
  "building": false
}
```

### 7.2 `POST /api/command`

공통: `Content-Type: application/json`, body는 객체.

| `cmd` | 추가 필드 | Kit 쪽 핵심 |
|-------|-----------|-------------|
| `open_master` | `path` 또는 `master_path` | `LamWindow._open_master_at_path` |
| `csv_refresh_list` | `csv_dir` (선택) | `list_csv_paths_in_directory` |
| `csv_timeline_refresh` | `csv_dir`, `csv_path`, `speed_scale` | `build_csv_playback_plan` 등 |
| `csv_play` | `csv_dir`, `csv_path`, `speed_scale` | `run_simulation_from_csv` |
| `csv_stop` | 없음 | `request_stop_csv_playback` |

성공 시 대부분 `{ "ok": true, ... }`, 실패 시 HTTP 200 + `{ "ok": false, "error": "..." }` 또는 HTTP 5xx.

---

## 8. 새 기능 추가 체크리스트

1. **Kit에 이미 있는가?** → `lam_window.py` / `simulation_play.py` 에서 호출할 함수 확정.
2. **`lam_remote_http_bridge.py`**
   - `_cmd_xxx()` 구현
   - `_dispatch_command` 에 분기 추가
   - 필요 시 `_web_state` 키 추가 + `_snapshot` 반영
   - 메인 전용 작업인지 / 백그라운드 스레드인지 결정 (**중첩 `_run_on_main` 금지**)
3. **웹 UI**
   - `lam_kit_remote`: `index.html` + `lam_panel.js` (+ `lam_panel.css`)
   - `streaming_ui`: `LamSimulation.tsx` (+ module css)
4. **문서** — 이 파일의 §6 표에 한 줄 추가.
5. **확인** — §3 체크리스트로 A 또는 B에서 테스트.

---

## 9. 문제 해결

| 증상 | 확인 |
|------|------|
| `Failed to resolve extension dependencies` / `morph.lam_web_bridge` none found | `.\repo.bat build` 실행 후 Kit 재시작 (`exts.deps.generated.kit` 갱신) |
| 페이지 안 열림 | Kit 로그에 `[LAM Remote UI]` 있는지, 포트 8720 사용 중인지 |
| 배너 «연결 실패» | Kit 실행 여부, 방화벽, `TBS_REMOTE_UI_BIND` |
| Open Master 시 Kit 멈춤 | `_cmd_*` 안에서 `_run_on_main` 중첩 호출 여부 |
| CSV Play 무반응 | master 열림·Discover·Extract 완료 여부, CSV 경로, 콘솔 `[LAM]` 로그 |
| TBS와 충돌 | 동시에 두 확장이 8720 바인드 — 하나 끄거나 `TBS_REMOTE_UI_PORT` 변경 |
| React(5174)만 실패 | `.env`·Vite 프록시, Kit 8720 동작 여부 |
| UI 수정이 안 보임 | Python 수정 → Kit 재시작; JS/CSS → 브라우저 강력 새로고침 |

---

## 10. 관련 파일 경로 (저장소 기준)

```
source/extensions/morph.lam_control/
├── morph/lam_control/
│   ├── extension.py          # set_session / clear_session
│   ├── remote_api.py         # LamKitSession (웹 확장용 공개 API)
│   ├── lam_window.py
│   └── simulation_play.py
└── docs/LAM_Web_UI.md        # → lam_web_bridge 가이드 링크

source/extensions/morph.lam_web_bridge/
├── morph/lam_web_bridge/
│   ├── extension.py              # 브리지 start/stop
│   └── lam_remote_http_bridge.py
├── web/lam_kit_remote/
├── web/streaming_ui/
└── docs/LAM_Web_UI_Guide.md      # ← 이 문서
```

---

## 11. TBS 웹 UI와의 대응

| TBS (한 확장) | LAM (분리) |
|-----|-----|
| `morph.tbs_control_1` + 내장 bridge | `morph.lam_control` + `morph.lam_web_bridge` |
| `kit_remote_http_bridge.py` | `lam_web_bridge/.../lam_remote_http_bridge.py` |
| `web/tbs_kit_remote/` | `lam_web_bridge/web/lam_kit_remote/` |
| `control_window` | `lam_control` + `remote_api` |

환경 변수 이름(`TBS_REMOTE_UI*`)은 **의도적으로 공유**합니다. LAM만 쓸 때 TBS 확장을 끄거나 포트를 분리하세요.

---

*문서 버전: morph.lam_web_bridge + morph.lam_control (Open Master + CSV Play, 매크로 제외).*
