# TBS `control-tab` 샘플 — UI 구조 · API 매핑 (초안)

> **전제:** 좌측 `control-tab` 안에 **Kit TBS 제어창과 동일한 기능**이 들어간다고 가정한다.  
> **상태:** 샘플·참조용 — 회사 피드백(OQ) 확정 후 [`TBS_Web_Streaming_Control_Tab_Integration.md`](TBS_Web_Streaming_Control_Tab_Integration.md) 와 함께 구현.  
> **기능별 웹 적용:** [`docs/tbs_web_api_user_guide_ko.md`](docs/tbs_web_api_user_guide_ko.md) (권장)  
> **참조 구현:** `web/streaming_ui/TbsSimulation.tsx` (가장 완전), `web/tbs_kit_remote/index.html` (구형·일부 필드 누락), `morph/tbs_control_1/kit_remote_http_bridge.py`

---

## 1. 회사 페이지에서의 배치 (샘플)

```html
<!-- 회사 Vite/React 앱 (예시) -->
<div class="page-layout">
  <!-- 좌: Kit 확장 원격 제어 (HTTP 브리지) -->
  <div class="control-tab">
    <!-- 아래 §2 블록 전체 또는 <TbsControlTab /> React 컴포넌트 -->
  </div>

  <!-- 우: Kit Viewport WebRTC 스트리밍 (브리지와 무관) -->
  <div class="kit-app-streaming-area">
    <!-- StreamManager 등 회사 스트리밍 위젯 -->
  </div>
</div>
```

**연결 설정 (프론트)**

| 항목 | 값 |
|------|-----|
| Kit HTTP 브리지 | 기본 `http://127.0.0.1:8720` |
| Vite 프록시 | `/api` → `127.0.0.1:8720` (`web/streaming_ui/vite.config.snippet.txt`) |
| env | `VITE_TBS_KIT_API_BASE=` (비우면 same-origin `/api`) |
| 폴링 | `GET /api/state` **400ms** 권장 (`POLL_MS`) |

**Kit 웹 접속 URL (Kit 실행 후 브라우저)**

| URL | 설명 |
|-----|------|
| http://127.0.0.1:8720/api_tester.html | **API 테스터 (권장)** |
| http://127.0.0.1:8720/api/registry | **레지스트리 JSON** |
| http://127.0.0.1:8720/ | **제어 패널** |

**스트리밍 모드 권장 초기화 (페이지 mount 시 1회)**

```json
POST /api/command  { "cmd": "kit_chrome_hide", "hidden": true }
POST /api/command  { "cmd": "ui_windows", "hide": true }
```

---

## 2. `control-tab` 내부 UI 블록 (제어창 1:1 가정)

아래는 **섹션 순서·위젯**을 Kit `build_control_window()` 및 `TbsSimulation.tsx` 기준으로 정리한 샘플이다.  
HTML id는 `tbs_kit_remote/index.html` / React `name` 속성과 맞추기 쉽게 예시만 적었다.

### 2.1 연결 상태 · 화면 옵션

| UI | 타입 | Kit 제어창 대응 | API |
|----|------|-----------------|-----|
| 연결 배너 | 읽기 전용 텍스트 | — | `GET /api/state` → `kit_app` |
| 기본 메뉴·패널 숨기기 | checkbox | `_kit_chrome_hide_model` | `POST cmd:kit_chrome_hide` `{ hidden: bool }` |
| 스트리밍용 Kit TBS 창 숨기기 | checkbox | (웹 전용) | `POST cmd:ui_windows` `{ hide: bool }` |

**state 폴링:** `kit_chrome_hidden` → checkbox 동기화.

---

### 2.2 USD Load

| UI | 타입 | Kit 대응 | API |
|----|------|----------|-----|
| 샘플 USD | `<select>` | `_resource_combo` | `GET /api/resources` → `{ items: [{name, path}] }` |
| 경로 | text | `_path_model` | `fields.usd_path` / `load_usd.path` |
| Load | button | Load 클릭 | `POST cmd:load_usd` `{ path, resource_index }` |
| 상태 줄 | 읽기 전용 | `_load_status_label` | `GET /api/state` → `usd_status` |

**샘플 HTML fragment**

```html
<section data-tbs="usd-load">
  <h2>USD Load</h2>
  <select id="f_resource_combo"></select>
  <input type="text" id="f_usd_path" />
  <button type="button" id="btnLoadUsd">Load</button>
  <div id="usdStatus"></div>
</section>
```

**샘플 요청**

```http
GET /api/resources
→ { "items": [ { "name": "선택안함", "path": "" }, { "name": "sample_1", "path": "..." } ] }

POST /api/command
{ "cmd": "load_usd", "path": "omniverse://...", "resource_index": 1 }
```

> **#1 변경 후 정렬 예정:** Kit 데스크톱은 USD가 **`TbsUsdWindow`** 로 분리됨. 브리지 `load_usd` 는 아직 `load_window.on_load_usd` 를 호출 — #2 구현 시 **`TbsUsdWindow.open_master_at_path`** 로 맞출 예정.

---

### 2.3 XML 제너레이터

| UI | 타입 | Kit 대응 | API |
|----|------|----------|-----|
| 시퀀스 | `<select>` (7종) | `_xml_seq_combo` | `fields.xml_seq_index` (0~6) |
| FROM / TO | number (조건부 표시) | `_xml_from_port_model`, `_xml_to_port_model` | `fields.xml_from`, `fields.xml_to` |
| PORT_ID | number (조건부 표시) | `_xml_port_id_model` | `fields.xml_port_id` |
| OK | button | `on_xml_ok_clicked` | `POST cmd:xml_ok` `{ fields: {...} }` |
| 제너레이터 실행(역파싱) | button | `on_xml_run_clicked` | `POST cmd:xml_run` |

**시퀀스 옵션 (index → 이름)**

| index | 이름 |
|-------|------|
| 0 | EAPEIS_PORT_READYTOLOAD |
| 1 | EAPEIS_PORT_ARRIVED |
| 2 | EAPEIS_PORT_MOVE_TRANSFERING |
| 3 | EAPEIS_PORT_MOVE |
| 4 | EISEAP_PORT_MOVE_REQ |
| 5 | EAPEIS_PORT_READYTOUNLOAD |
| 6 | EAPEIS_PORT_REMOVED |

> Kit 제어창에서는 XML 블록이 **주석 처리**되어 있으나, 웹·브리지·`ext._xml_*` 모델은 **동작 가능**. `control-tab` 에 포함한다고 가정.

---

### 2.4 시뮬레이션 (simpy) — 입력 필드

| UI 라벨 | `fields` 키 | 타입 | Kit 모델 | 비고 |
|---------|-------------|------|----------|------|
| LOT 수 | `lot_count` | int | `_sim_lot_count_model` | min 1 |
| EP 개수 | `ep_count_index` | int | `_sim_ep_count_combo` | 0=EP2개, 1=EP3개 |
| LOT생성간격 min~max | `lot_spawn_min`, `lot_spawn_max` | float | spawn models | |
| 회수간격 min~max | `pickup_min`, `pickup_max` | float | pickup models | |
| FOUP공정(EP) min~max | `foup_proc_min`, `foup_proc_max` | float | `_sim_foup_proc_*` | **Kit 창에만 있음 — 브리지 `apply_fields` 미지원 (§6 갭)** |
| 초기 적재 IN/OUT | `init_inout` | bool | `_sim_init_inout_model` | TbsSimulation ✅ |
| 초기 적재 BP1~4 | `init_bp1` … `init_bp4` | bool | `_sim_init_bp*_model` | |
| 초기 적재 EP1~3 | `init_ep1` … `init_ep3` | bool | `_sim_init_ep*_model` | EP3는 ep_count에 따라 숨김 |
| 고장 IN/OUT | `fault_inout` | bool | `_sim_fault_inout_model` | TbsSimulation ✅ |
| 고장 BP1~4 | `fault_bp1` … `fault_bp4` | bool | `_sim_fault_*` | 실행 중 즉시 반영 |
| 고장 EP1~3 | `fault_ep1` … `fault_ep3` | bool | | |
| OHT→IN/OUT/EP | `oht_min`, `oht_max` | float | `_sim_oht_bp1_*` | 웹 라벨은 「OHT→BP/EP」 |
| IN/OUT→BP | `bp1_bp_min`, `bp1_bp_max` | float | `_sim_bp1_bp_*` | |
| BP→EP | `bp_ep_min`, `bp_ep_max` | float | | |
| EP→OHT | `ep_oht_min`, `ep_oht_max` | float | | |
| 시뮬 속도배율 | `speed` | float | `_sim_speed_model` | |
| 로그주기(s) | `log_interval` | float | `_sim_log_interval_model` | |
| 각 공정 확인 | `confirm_each` | bool | `_sim_confirm_each_step_model` | gate 모달 연동 |
| 공정설정 시간 우선 | `process_time_priority` | bool | `_sim_process_time_priority_model` | |

**버튼**

| 버튼 | cmd | body |
|------|-----|------|
| 시작 | `sim_start` | `{ fields: { ...위 전체... } }` — fields 적용 후 `on_sim_start_clicked` |
| 정지 | `sim_stop` | `{ cmd: "sim_stop" }` |
| 리셋 | `sim_reset` | `{ cmd: "sim_reset" }` |
| 진행현황+Sim로그 복사 | `copy_progress` | Kit 클립보드 (`on_copy_sim_progress`) |
| (선택) 필드만 반영 | `apply_fields` | `{ fields: {...} }` — 시작 없이 모델만 동기화 |

**`fields` JSON 샘플 (sim_start)**

```json
{
  "cmd": "sim_start",
  "fields": {
    "lot_count": 6,
    "ep_count_index": 0,
    "lot_spawn_min": 15,
    "lot_spawn_max": 40,
    "pickup_min": 50,
    "pickup_max": 70,
    "speed": 1,
    "log_interval": 1,
    "confirm_each": false,
    "process_time_priority": false,
    "init_inout": false,
    "init_bp1": false,
    "init_bp2": false,
    "init_bp3": false,
    "init_bp4": false,
    "init_ep1": false,
    "init_ep2": false,
    "init_ep3": false,
    "fault_inout": false,
    "fault_bp1": false,
    "fault_bp2": false,
    "fault_bp3": false,
    "fault_bp4": false,
    "fault_ep1": false,
    "fault_ep2": false,
    "fault_ep3": false,
    "oht_min": 5,
    "oht_max": 10,
    "bp1_bp_min": 5,
    "bp1_bp_max": 10,
    "bp_ep_min": 5,
    "bp_ep_max": 10,
    "ep_oht_min": 5,
    "ep_oht_max": 10,
    "priority_prefix": "",
    "xml_seq_index": 0,
    "xml_from": 1,
    "xml_to": 6,
    "xml_port_id": 1,
    "usd_path": "",
    "resource_index": 0
  }
}
```

---

### 2.5 시뮼 뷰포트 분할 · 화면별 설정 (USD 로드 후)

| UI | 조건 | API |
|----|------|-----|
| 1~4화면 radio | `state.sim_multi_split_row_visible === true` | `POST cmd:sim_viewport_split` `{ count: 1..4 }` |
| 화면N에 설정 저장 | | `apply_fields` → `save_sim_screen` `{ screen: N }` |
| 화면N 불러오기 | `state.per_screen_snapshots[N-1]` 존재 시 | `apply_per_screen_snapshot` `{ snapshot: {...} }` |

**폴링으로 제어**

| state 필드 | UI 동작 |
|------------|---------|
| `viewport_split_count` | radio 선택 동기화 |
| `sim_multi_split_row_visible` | 분할 섹션 show/hide |
| `channels[]` | 멀티 컬럼 모드 — 포트·진행·이력·EP 타임라인 |
| `per_screen_snapshots` | 불러오기 버튼 생성 |

**단일 화면 모드 (`channels` 비어 있음)**

| UI | state 필드 |
|----|------------|
| 포트 그리드 BP1~EP3 | `ports`, `port_header`, `ep3_visible`, `bp4_visible` |
| EP 타임라인 막대 | `ep_timeline` |
| 진행현황 | `progress` |
| 이력로그 | `history` |

**멀티 화면 모드 (`channels` 있음)**

각 `channels[i]`: `{ screen, port_header, ports, progress, history, ep_timeline, ep3_visible, bp4_visible }`

---

### 2.6 공정 확인 (Gate) 모달

「각 공정 확인」체크 시 Kit이 시뮬 tick을 멈추고 확인창 표시.

| UI | API |
|----|-----|
| 모달 제목·본문 | `GET /api/state` → `gate_pending: { title, message, ... }` |
| 확인 버튼 | `POST cmd:gate_confirm` |

`gate_pending` 이 null/비어 있으면 모달 숨김.

---

### 2.7 장비 prim

| UI | Kit 대응 | API |
|----|----------|-----|
| 우선 표시 접두사 | `_priority_prefix_model` | `fields.priority_prefix` |
| 목록 새로고침 | `refresh_object_list` | `POST cmd:prim_refresh` |

> **갭:** Kit 제어창 하단에는 **prim별 CollapsableFrame + 드롭다운·액션 버튼** 이 동적으로 많음. 웹은 **새로고침만** — prim 목록 JSON API는 **없음**. 스트리밍 UX에서는 Viewport 클릭·Kit 창 또는 **추가 API 설계** 필요.

---

### 2.8 `control-tab`에 넣지 않는 것 (별도 Kit 창)

| 기능 | Kit 창 | 웹 `control-tab` | 비고 |
|------|--------|------------------|------|
| USD Extract/Bake/Discover | `TbsUsdWindow` | Load만 (§2.2) | #1 분리 |
| 시퀀스 JSON 편집기 | `SequenceEditorWindow` | **미포함** | 별도 창 또는 #2+ API |
| FOUP 진행 라벨 (EP별) | 시뮼 모니터 컬럼 내부 | `progress` 텍스트에 포함 | 전용 state 필드 없음 |
| 애니 실행 이력 블록 | `_sim_anim_history_*` | **미포함** | 필요 시 state 확장 |

스트리밍 배포 시 `ui_windows hide` 로 위 Kit 창을 숨기면, **웹에 없는 기능은 사용 불가** — 회사 요구에 따라 #2에서 API·UI 추가.

---

## 3. HTTP API 요약 (TBS 확장)

**베이스:** `http://127.0.0.1:8720` (환경변수 `TBS_REMOTE_UI_PORT` 등으로 변경 가능)

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/state` | Kit UI 스냅샷 (폴링) |
| GET | `/api/resources` | 샘플 USD 목록 |
| POST | `/api/command` | 원격 명령 (JSON body) |

### 3.1 `GET /api/state` 응답 필드

```json
{
  "usd_status": "string",
  "progress": "string",
  "history": "string",
  "sim_line": "string",
  "port_header": "[포트상태]",
  "ports": {
    "INOUT": "-", "BP1": "FULL", "BP2": "-", "BP3": "-", "BP4": "-",
    "EP1": "LOT001", "EP2": "-", "EP3": "-"
  },
  "ep3_visible": true,
  "bp4_visible": true,
  "kit_app": "morph.editor.kit",
  "kit_chrome_hidden": false,
  "viewport_split_count": 1,
  "sim_multi_split_row_visible": false,
  "channels": [],
  "ep_timeline": { "row_order": [], "rows": {}, "empty_acc": {}, "total_est": 30 },
  "per_screen_snapshots": [ null, null, null, null ],
  "gate_pending": null
}
```

`gate_pending` 예:

```json
{
  "title": "공정 확인",
  "message": "BP1 → EP2 이동을 진행할까요?",
  "gate_xml_sequence_name": "..."
}
```

### 3.2 `POST /api/command` — cmd 목록

| cmd | 추가 body | Kit 핸들러 |
|-----|-----------|------------|
| `load_usd` | `path`, `resource_index` | `load_window.on_load_usd` → **#1 후 TbsUsdWindow** |
| `apply_fields` | `fields` | `_apply_web_fields` |
| `sim_start` | `fields` (선택) | `_apply_web_fields` + `on_sim_start_clicked` |
| `sim_stop` | — | `on_sim_stop_clicked` |
| `sim_reset` | — | `on_sim_reset_clicked` |
| `prim_refresh` | — | `refresh_object_list` |
| `copy_progress` | — | `on_copy_sim_progress` |
| `xml_ok` | `fields` (선택) | `_apply_web_fields` + `on_xml_ok_clicked` |
| `xml_run` | — | `on_xml_run_clicked` |
| `kit_chrome_hide` | `hidden` | `apply_kit_chrome_hidden` |
| `sim_viewport_split` | `count` (1~4) | `sim_multi_view.apply_sim_viewport_split_layout` |
| `save_sim_screen` | `screen` (1~4) | `_on_save_sim_settings_to_screen` |
| `apply_per_screen_snapshot` | `snapshot` | `_apply_per_screen_snapshot` + `on_sim_ep_count_changed` |
| `gate_confirm` | — | `_close_sim_gate_dialog` |
| `ui_windows` | `hide` | TBS 제어창·시퀀스 편집기 `visible` 토글 |
| `log_mode` | (무시) | 호환용 `{ ok: true }` |

**공통 응답:** `{ "ok": true }` 또는 `{ "ok": false, "error": "..." }`

---

## 4. React 마운트 샘플 (회사 앱)

**권장 — 단일 파일:** [`web/streaming_ui/TbsControlTab.tsx`](../web/streaming_ui/TbsControlTab.tsx)  
(CSS module 없음, `control-tab` 스코프 스타일 내장, API 전체 연결)

```tsx
// Home/index.tsx (회사 프로젝트)
import TbsControlTab from "./components/TbsControlTab";

export function EquipmentPage() {
  return (
    <div className="page-layout">
      {/* 방법 A: 부모가 control-tab */}
      <div className="control-tab">
        <TbsControlTab autoStreamingMode />
      </div>

      {/* 방법 B: 컴포넌트가 control-tab 래퍼 포함 */}
      {/* <TbsControlTab wrapControlTab autoStreamingMode /> */}

      <div className="kit-app-streaming-area">
        {/* <StreamManager sessionId="..." /> */}
      </div>
    </div>
  );
}
```

**레거시:** `TbsSimulation.tsx` + `TbsSimulation.module.css` (2파일, 동일 기능·FOUP/prim 일부 누락)

---

## 5. Kit 확장 측 — 브리지가 호출하는 Python (참조)

```
kit_remote_http_bridge.py
  GET  /api/state     → _snapshot(ext)           → control_window 라벨/포트셀 읽기
  GET  /api/resources → get_resource_usd_list()
  POST /api/command   → _dispatch_command(ext)   → control_window / load_window / sim_multi_view
```

**스레드:** HTTP 워커 → `_run_on_main` → Kit 메인 스레드에서 확장 함수 실행.

**확장 기동:** `morph.tbs_control_2` extension `on_startup` 에서 브리지 listen (기본 8720).  
`TBS_REMOTE_UI=0` 이면 브리지 비활성.

---

## 6. 현재 갭 — 「제어창 동일」 가정 시 #2에서 맞출 항목

| # | 항목 | 현재 | 목표 |
|---|------|------|------|
| G1 | FOUP공정 min/max | Kit 창만, `_apply_web_fields` 없음 | `fields.foup_proc_min/max` + UI |
| G2 | USD Load | `load_window` 경로 | `TbsUsdWindow` (#1 정렬) |
| G3 | prim 드롭다운 목록 | Kit UI만 | `GET /api/prim_list` 등 (필요 시) |
| G4 | 시퀀스 편집기 | 별도 Window | 웹 포함 여부 · OQ |
| G5 | FOUP EP별 진행 라벨 | progress 텍스트 혼재 | state 필드 분리 (선택) |
| G6 | `index.html` | fault/init_inout/foup/chrome 누락 | `TbsSimulation.tsx` 기준 통일 |
| G7 | 애니 실행 이력 | Kit만 | 웹 표시 필요 시 state 확장 |

---

## 7. 다른 확장(LAM)에 적용할 때

동일 패턴:

| | TBS | LAM |
|---|-----|-----|
| control-tab 컴포넌트 | `TbsControlTab` | `LamControlTab` |
| 브리지 | `tbs_control_2` 내장 | `morph.lam_web_bridge` |
| state/cmd | TBS 표 §3 | `open_master`, `csv_play`, … |
| 스트리밍 영역 | 공통 `kit-app-streaming-area` | 동일 |

한 페이지에서 LAM/TBS 탭 전환 시 **control-tab 내용만 교체**, 스트리밍 세션은 Kit 앱별로 분리.

---

## 8. 변경 이력

| 날짜 | 내용 |
|------|------|
| 2026-05-28 | 초안 — control-tab UI 블록·fields·cmd·state 샘플, Kit 갭 정리 |
