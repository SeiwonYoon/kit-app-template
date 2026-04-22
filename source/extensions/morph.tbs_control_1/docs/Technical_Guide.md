# morph.tbs_control_1 기술 문서 (웹 개발자 친화 버전)

대상 독자: **JS만 사용해본 웹 개발자(파이썬/Omniverse/SimPy 처음)**
목표: 이 문서만 보고도 “대략 어떤 구조인지” 이해하고, 작은 예제를 따라 하면서 **기능을 추가/수정할 수 있는 수준**까지 안내합니다.

---

## 0) 이 확장은 무엇을 하는가? (30초 요약)

`morph.tbs_control_1`은 Kit(Omniverse) 안에서:
- **시뮬레이션**을 돌려서(공정 이벤트가 언제 발생하는지 계산)
- 그 이벤트에 맞춰 **애니메이션(JSON 시퀀스)** 을 실행하고
- 진행현황/포트상태/로그를 UI에 보여주고
- 웹 브라우저에서도 같은 기능을 호출할 수 있게(HTTP 브리지) 해주는 확장입니다.

그리고 이 프로젝트의 큰 특징은:
> Start를 누르면 “실시간으로 계산하면서 보여주는 방식”이 아니라, 먼저 **백그라운드에서 끝까지 계산(프리런)** 하고
> 그 결과를 **재생(플레이백)** 해서 사용자에게 “시뮬이 진행되는 것처럼” 보여주는 구조라는 점입니다.

웹 개발로 비유하면:
- **프리런 = 녹화** (나중에 재생할 타임라인을 미리 만든다)
- **재생 = 동영상 플레이어처럼 재생** (시간에 맞춰 이벤트를 순서대로 UI에 뿌린다)

---

## 1) 용어를 “웹 개발자 언어”로 번역

아래 5개만 이해하면 문서의 80%가 해결됩니다.

- **`env.now` (시뮬 시간)**
  - JS로 치면 “게임 내부 시간” 같은 개념입니다.
  - `Date.now()`처럼 진짜 시간이 아니라, **시뮬 안에서만 쓰는 시간**입니다.

- **`engine.tick(dt)`**
  - JS 게임에서 `update(dt)` 같은 겁니다.
  - “시뮬 시간(dt 만큼)”을 앞으로 진행시키는 호출입니다.

- **콜백(callback)**
  - JS의 `button.onclick = () => {}`와 동일합니다.
  - 시뮬이 “이벤트 발생”하면 `on_event(payload)` 같은 함수를 호출하는 방식입니다.

- **`payload` (dict)**
  - 파이썬 dict = JS object입니다.
  - 예: `{"seq": "ARRIVED", "sim_time": "135.57", ...}`는 JS로 보면 `{ seq: "ARRIVED", sim_time: "135.57" }` 입니다.

- **`SimTimelinePlayer.tick()`**
  - 프리런 결과(타임라인 배열)를 “현재 재생 시간”에 맞춰 `emit()`하는 **플레이어 루프**입니다.
  - 웹으로 치면 `setInterval(() => player.tick(), 16)` 같은 느낌입니다.

---

## 2) 가장 중요한 데이터: “타임라인(시간표) 배열”

프리런의 결과는 결국 아래 같은 배열입니다.

### 2.1 개념(의사 JSON)

```json
[
  { "t": 0.00,   "kind": "progress", "payload": { "label": "대기", ... } },
  { "t": 83.28,  "kind": "event",    "payload": { "seq": "ARRIVED", ... } },
  { "t": 83.28,  "kind": "progress", "payload": { "status": "RUNNING", ... } },
  { "t": 94.28,  "kind": "log",      "payload": "🟩 -> 완료 | ..." }
]
```

- `t`: **시뮬 시간(초)**
- `kind`: 이 항목이 “무엇인지”
  - `log`: 로그창에 찍을 문자열
  - `event`: 애니 매핑/실행으로 이어질 이벤트 payload
  - `progress`: 진행현황/막대그래프 갱신 payload
- `payload`: 실제 데이터(JS object와 같은 구조)

### 2.2 왜 이게 좋은가?

실시간 계산 방식에서는 “UI가 바쁠 때 이벤트가 밀려서” 애니가 몰아서 실행되는 문제가 생길 수 있는데,
타임라인 배열이 있으면 **이미 정렬된 정답지**가 생기므로 재생이 훨씬 안정적입니다.

웹 개발 비유:
- 실시간 방식: 서버가 요청 받을 때마다 즉석에서 계산해서 응답하는데, 서버가 느리면 응답이 몰림
- 타임라인 방식: 미리 계산해 둔 결과(JSON 배열)를 **정해진 시간에 맞춰** 순서대로 보여줌

---

## 3) 시뮬레이션(프리런 → 재생) 흐름을 “JS 이벤트 루프”로 이해하기

### 3.1 전체 흐름(그림)

```text
[사용자] Start 클릭
   |
   v
[프리런 스레드] 가능한 빨리 시뮬 끝까지 계산
   |
   v
타임라인 배열(시간표) 완성  --->  (총 시뮬시간 = 마지막 t)
   |
   v
[UI 메인 루프] 플레이어가 time에 맞춰 항목을 emit
   |
   +--> log 항목: 로그 패널에 출력
   +--> progress 항목: 진행현황/막대 갱신
   +--> event 항목: 애니 매핑 -> JSON 시퀀스 실행
```

### 3.2 프리런(prerun)은 무엇을 하는가?

프리런은 “시뮬 엔진을 빨리 돌려서” 타임라인 배열을 만드는 단계입니다.

JS로 비유한 의사코드:

```js
// (의사 코드) prerun: 시뮬을 끝까지 계산해 timeline을 만든다
function prerun(engine) {
  const timeline = [];

  engine.onLog = (line) => timeline.push({ t: engine.simTime(), kind: "log", payload: line });
  engine.onEvent = (payload) => timeline.push({ t: payload.sim_time, kind: "event", payload });
  engine.onProgress = (payload) => timeline.push({ t: payload.sim_time, kind: "progress", payload });

  // 매우 큰 dt로 tick을 반복해서 "가능한 빨리" 끝까지 감
  while (!engine.isDone()) {
    engine.tick(1_000_000);
  }

  timeline.sort((a, b) => a.t - b.t);
  return { finalTime: engine.simTime(), timeline };
}
```

파이썬 코드에서 이 역할은 `prerun_engine_to_timeline()`가 합니다.

```141:229:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_sim_prerun_playback.py
def prerun_engine_to_timeline(...):
    ...
    while True:
        if getattr(engine, "is_done", False): break
        if not getattr(engine, "is_running", False): break
        engine.tick(1e6)
    final_sim = float(getattr(engine.env, "now", 0.0) or 0.0)
    ...
```

### 3.3 재생(playback)은 무엇을 하는가?

재생은 “타임라인 배열을 플레이”하는 단계입니다.

JS 의사코드:

```js
// (의사 코드) player: timeline을 time 기준으로 emit한다
function makePlayer(timeline, emit) {
  let cursor = 0;
  let t0 = performance.now();

  return {
    tick(speed = 1.0) {
      const now = performance.now();
      const simNow = ((now - t0) / 1000) * speed;  // wall-clock -> sim-time

      while (cursor < timeline.length && timeline[cursor].t <= simNow) {
        emit(timeline[cursor]);
        cursor++;
      }
    }
  };
}
```

파이썬에서는 `SimTimelinePlayer.tick()`이 같은 역할을 합니다.

```60:138:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_sim_prerun_playback.py
class SimTimelinePlayer:
    def tick(self) -> None:
        ...
        while i < len(items) and float(items[i].t) <= float(t_sim) + 1e-9:
            self._emit(items[i].kind, items[i].payload, int(scr))
            i += 1
```

---

## 4) “이벤트(event)”가 오면 애니메이션(JSON)이 어떻게 실행되는가?

여기서 핵심은 2단계입니다.

1) 이벤트 payload를 표준 포맷(XML)으로 맞춰서 “어떤 상황인지”를 안정적으로 뽑는다
2) 그 결과로 “어떤 JSON을 실행할지”를 고르고, `SequenceRunner`로 실행한다

### 4.1 XML 생성/역파싱(표준화)의 이유

웹 개발 비유:
- 여러 API가 서로 다른 필드명을 보내면 매번 조건문이 복잡해짐
- 그래서 중간에 “표준 DTO”로 변환해서 이후 처리를 단순화하는 것과 같습니다.

여기서는 XML이 그 표준 포맷 역할을 합니다.

```203:266:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\xml_generator.py
def build_xml_string(...):
    ...
def parse_xml_string(xml_text: str) -> Optional[dict]:
    ...
```

### 4.2 JSON 시퀀스(steps) 실행: SequenceRunner

시퀀스는 “steps 배열”입니다.

```json
[
  {"type": "MOVE", "prim": "Mesh_308", "duration": 1.0, "dx": 100, "dy": 0, "dz": 0},
  {"type": "ROTATE", "prim": "Mesh_308", "duration": 1.0, "rx": 0, "ry": 90, "rz": 0}
]
```

`SequenceRunner.run(steps)`가 이 배열을 읽고, 타입별로 실제 애니메이션을 실행합니다.

---

## 5) 애니메이션 편집기(Sequence Editor)는 무엇을 하는가?

웹 UI로 비유하면:
- “폼으로 배열 데이터를 편집”하고
- “저장 버튼을 누르면 JSON 문자열로 export”
- “실행 버튼을 누르면 그 배열을 엔진에 전달”

실제 파일:
- `morph/tbs_control_1/sequence_editor.py`

기본 구조:
- UI에서 step dict를 만들고/수정하고
- `json.dumps(self._steps)`로 저장하고
- `SequenceRunner.run(self._steps)`로 실행합니다.

---

## 6) 로그/진행현황은 왜 ‘큐(queue)’를 쓰는가?

핵심 이유는 “스레드”입니다.

웹 개발 비유:
- 웹 워커(worker)에서 DOM을 직접 만지면 안 되는 것처럼,
- Kit/Omni UI도 **메인(UI) 스레드에서만** 안전하게 UI를 변경할 수 있습니다.

그래서 다른 스레드에서 올라온 이벤트는 `_sim_log_queue`에 넣고,
UI 메인 루프가 `_drain_sim_log_queue()`에서 꺼내 UI를 갱신합니다.

```3578:3592:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_window.py
def _dispatch_sim_ui_queue_item(ext: Any, kind: str, payload: Any, panel_mode: SimLogPanelMode) -> None:
    ...
```

---

## 7) 웹 연결(HTTP Bridge)은 어떤 구조인가?

목표:
- 브라우저에서 `/api/command`로 “sim_start / sim_stop / sim_reset”을 호출하면
- Kit 내부에서 실제 `on_sim_start_clicked()` 같은 함수가 실행되게 만드는 것

핵심 제약:
- HTTP 서버는 별도 스레드에서 돌기 때문에,
- UI/확장 함수 호출은 반드시 “메인 스레드로 넘겨서 실행”해야 안전합니다.

그래서 `_run_on_main()` 큐를 둡니다.

```62:86:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\kit_remote_http_bridge.py
def _run_on_main(fn: Callable[[], Any]) -> Any:
    ...
```

---

## 8) (따라하기) 웹 개발자가 이해하기 쉬운 최소 실습 3개

### 8.1 “타임라인 배열”을 JS로 직접 만들어 재생해보기(개념 실습)

```js
const timeline = [
  { t: 0, kind: "log", payload: "시작" },
  { t: 1, kind: "log", payload: "1초 지남" },
  { t: 2, kind: "log", payload: "2초 지남" },
];

let cursor = 0;
const t0 = performance.now();

function tick() {
  const simNow = (performance.now() - t0) / 1000;
  while (cursor < timeline.length && timeline[cursor].t <= simNow) {
    console.log("[emit]", timeline[cursor]);
    cursor++;
  }
  requestAnimationFrame(tick);
}
tick();
```

이게 프로젝트에서 `SimTimelinePlayer.tick()`이 하는 일과 거의 같습니다.

### 8.2 “웹에서 sim_start 호출” 실습

```bash
curl -X POST "http://127.0.0.1:8720/api/command" ^
  -H "Content-Type: application/json" ^
  -d "{\"cmd\":\"sim_start\",\"data\":{}}"
```

### 8.3 “시퀀스 편집기 step JSON” 실습

```json
[
  {"type":"ROTATE","prim":"Mesh_308","duration":1.0,"rx":0,"ry":90,"rz":0},
  {"type":"DELAY","duration":0.5},
  {"type":"MOVE","prim":"Mesh_308","duration":1.0,"dx":100,"dy":0,"dz":0}
]
```

이 JSON을 편집기 상단 JSON 박스에 붙여넣고 “현재 JSON 상태로 스텝 생성하기” → “실행”을 누르면 동작합니다.

---

## 9) 파일 지도(실제 수정은 여기서 시작)

- 시뮬 프리런/재생: `morph/tbs_control_1/control_window.py`, `control_sim_prerun_playback.py`
- 애니 편집: `morph/tbs_control_1/sequence_editor.py`
- 애니 실행(스텝 엔진): `morph/tbs_control_1/sequence_engine.py`
- 이벤트 표준화(XML): `morph/tbs_control_1/xml_generator.py`
- 웹 브리지: `morph/tbs_control_1/kit_remote_http_bridge.py`, `web/tbs_kit_remote/*`
