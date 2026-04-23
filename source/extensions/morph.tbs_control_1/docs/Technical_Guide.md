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

### 1.1 문서에서 자주 나오는 단어를 더 풀어서 설명(특히 `emit`)

- **`emit`(에밋/방출)**
  - 뜻: “어딘가로 **내보내서(방출해서)** 다음 처리가 일어나게 만든다”는 의미입니다.
  - 이 프로젝트에서 `emit`이 실제로 하는 일:
    - 타임라인의 한 항목(`SimTimelineItem`)을 꺼내서
    - `kind`에 따라 **로그/진행현황/이벤트 처리 함수로 전달**합니다.
  - JS 비유:

```javascript
// emit = "이 이벤트/데이터를 다른 처리기로 넘긴다"
function emit(kind, payload, screen) {
  if (kind === "log") console.log(payload);
  if (kind === "progress") updateUI(payload);
  if (kind === "event") handleEvent(payload);
}
```

- **`dispatch`(디스패치/분기)**
  - 뜻: “문자열/타입 같은 값에 따라 **어떤 함수를 실행할지 선택**하는 것”입니다.
  - 예: 웹 브릿지의 `_dispatch_command`는 `cmd` 값(`"sim_start"` 등)에 따라 `on_sim_start_clicked` 같은 함수를 호출합니다.

- **`enqueue` / `queue`(큐에 넣기 / 큐)**
  - 뜻: “바로 처리하지 않고 **줄 세워서 나중에 처리**하는 방식”
  - 왜 필요하나: Kit UI는 DOM처럼 **메인(UI) 스레드에서만** 안전하게 바꿀 수 있어서, 다른 스레드(프리런 스레드/HTTP 스레드)에서 바로 UI를 만지지 않습니다.

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

### 2.3 실제 코드에서의 데이터 타입(진짜 저장 형식)

프리런 결과는 “그냥 아무 리스트”가 아니라, 아래 dataclass 타입으로 고정되어 있습니다.

- **파일**: `morph/tbs_control_1/control_sim_prerun_playback.py`
- **타입**: `SimTimelineItem`, `SimPreRunResult`

```9:26:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_sim_prerun_playback.py
@dataclass(frozen=True)
class SimTimelineItem:
    t: float
    kind: str  # "log" | "event" | "progress"
    payload: Any

@dataclass(frozen=True)
class SimPreRunResult:
    screen: int
    final_sim_time: float
    total_est_sec: float
    items: Tuple[SimTimelineItem, ...]
```

그리고 **화면이 여러 개인 경우**(분할 화면)는 이게 화면별 dict로 모입니다.

- 저장 변수: `ext._sim_prerun_results_by_screen`
- 형태(의사 코드):

```js
// ext._sim_prerun_results_by_screen (개념)
{
  1: { screen: 1, final_sim_time: 342.1, items: [ ... ] },
  2: { screen: 2, final_sim_time: 350.0, items: [ ... ] }
}
```

> 여기서 중요한 점: **화면마다 타임라인이 독립**입니다.
> 즉, 화면2가 화면1과 “같은 시간에 같은 이벤트”가 아닐 수도 있고, 재생도 화면별로 따로 진행됩니다(`SimTimelinePlayer.tick()`이 화면별로 계산).

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

---

## 3-A) (중요) “코드 따라가기 지도” — Start 클릭부터 프리런/재생까지

이 섹션은 **문서만 보면서 실제 코드를 따라가서 전체 맥락을 파악**할 수 있게, “어디서 시작해서 어디로 흘러가는지”를 **파일/함수/라인 범위**로 연결해줍니다.

### 3-A.1 시작점: Start 버튼 클릭 → `on_sim_start_clicked`

- **파일**: `morph/tbs_control_1/control_window.py`
- **함수**: `on_sim_start_clicked(ext)`

여기서 하는 일(요약):
- 이전 시뮬 정리(`on_sim_stop_clicked`)
- 화면 분할 수(`ext._sim_viewport_split_count`)에 따라 “엔진 1개 또는 N개” 생성
- UI 초기화(진행현황/포트상태/막대 초기 스냅샷)
- 마지막에 **프리런 스레드를 띄움**(실시간 tick 스레드는 사용하지 않음)

코드 위치:

```5084:5209:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_window.py
def on_sim_start_clicked(ext: Any) -> None:
    ...
    n_ch = max(1, min(4, int(getattr(ext, "_sim_viewport_split_count", 1) or 1)))
    on_sim_stop_clicked(ext)
    ...
```

### 3-A.2 프리런 모드 진입: `# --- 프리런(오프라인) → 타임라인 재생 모드 ---`

`on_sim_start_clicked`의 아래 구간이 “프리런 시작”을 담당합니다.

여기서 핵심 상태 변수(“프리런 결과는 어디에 저장되나?”):
- `ext._sim_prerun_results_by_screen`: **프리런 결과(타임라인) 저장소** (메모리)
- `ext._sim_prerun_done_evt`: 프리런 완료 플래그(Event)

코드 위치:

```5717:5805:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_window.py
# --- 프리런(오프라인) → 타임라인 재생 모드 ---
ext._sim_prerun_done_evt = threading.Event()
ext._sim_prerun_results_by_screen = None
...
def _prerun_thread_body(run_gen: int) -> None:
    ...
    res = prerun_engine_to_timeline(screen=scr, engine=eng)
    results[int(scr)] = res
    ...
    ext._sim_prerun_results_by_screen = results
    ...
    ev.set()

th_pr = threading.Thread(target=_prerun_thread_body, ...)
th_pr.start()
return
```

> 웹 개발자 비유:
> - `ext._sim_prerun_results_by_screen` = “메모리에 저장된 `timelineByScreen` 객체”
> - `ext._sim_prerun_done_evt` = “Promise resolve 플래그 같은 것”

### 3-A.3 프리런 실행 본체: `prerun_engine_to_timeline`

프리런 스레드는 각 화면 엔진에 대해 `prerun_engine_to_timeline(...)`를 호출합니다.

- **파일**: `morph/tbs_control_1/control_sim_prerun_playback.py`
- **함수**: `prerun_engine_to_timeline(screen, engine)`
- 핵심: `engine.tick(1e6)`를 반복 호출하여 **가능한 빨리** 끝까지 진행

코드 위치:

```141:229:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_sim_prerun_playback.py
def prerun_engine_to_timeline(...):
    ...
    while True:
        if getattr(engine, "is_done", False):
            break
        if not getattr(engine, "is_running", False):
            break
        engine.tick(1e6)
    final_sim = float(getattr(engine.env, "now", 0.0) or 0.0)
    return SimPreRunResult(..., final_sim_time=final_sim, items=tuple(items))
```

> 여기서 “이벤트가 발생한다”는 뜻은:
> 엔진 내부에서 어떤 공정이 완료/이동 요청 등의 조건이 만족되면, 엔진이 `on_event(payload)` 같은 콜백을 호출하고,
> 프리런은 그 payload를 `items.append(...)`로 타임라인에 저장한다는 의미입니다.

### 3-A.4 프리런 완료 감지(메인/UI 스레드): `_drain_sim_log_queue`

프리런이 끝나도, UI는 “프리런 스레드”가 아니라 **메인(UI) 스레드**에서 플레이어를 시작해야 안전합니다.
이를 담당하는 곳이 `_drain_sim_log_queue()`입니다.

이 함수는 매 프레임 UI 업데이트 스트림에서 호출됩니다(= JS의 requestAnimationFrame 루프 같은 역할).

핵심 동작:
- `ext._sim_prerun_done_evt.is_set()`이면 “프리런 결과가 준비됨”
- `ext._sim_prerun_results_by_screen`에서 결과를 꺼내
  - 플레이어 생성/시작
  - `sim_playback_tick` 구독 생성(= 매 프레임 `_tick_playback` 호출)

코드 위치:

```3595:3714:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_window.py
def _drain_sim_log_queue(ext: Any) -> None:
    ev = getattr(ext, "_sim_prerun_done_evt", None)
    if ev is not None and ev.is_set():
        results = getattr(ext, "_sim_prerun_results_by_screen", None)
        ...
        player = SimTimelinePlayer(...)
        player.start()
        ext._sim_playback_player = player
        ...
        ext._sim_playback_ui_sub = app.get_app().get_update_event_stream().create_subscription_to_pop(
            lambda _e: _tick_playback(ext),
            name="morph.tbs_control_1:sim_playback_tick",
        )
```

### 3-A.5 재생 루프(플레이백): `_tick_playback` → `SimTimelinePlayer.tick()`

- **파일**: `morph/tbs_control_1/control_window.py`
- **함수**: `_tick_playback(ext)`

여기서 하는 일(요약):
- `player.tick()`을 호출하여 “현재 재생 시각”까지 도달한 타임라인 항목을 emit
- UI 막대/포트 아래 타임라인이 `env.now`를 읽기 때문에, 화면별 `PlaybackEngine.env.now`를 업데이트
- (추가) 막대가 끊기지 않게 `timeline_only` progress를 주기적으로 emit

코드 찾기:
- `control_window.py`에서 `def _tick_playback` 검색

---

## 3-B) “이벤트 → 애니(JSON) 실행” 코드 따라가기

이 섹션은 “타임라인의 event 항목이 emit된 뒤 어떤 코드가 JSON을 고르고 실행하는지”를 연결합니다.

### 3-B.1 event emit → `post_sim_anim_event` → UI 큐 → `_sim_ui_sink_anim_event`

재생 중 event 항목은 `_drain_sim_log_queue` 내부의 `_emit()`에서 `post_sim_anim_event(ext, payload)`로 전달됩니다.

코드 위치:

```3640:3660:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_window.py
def _emit(kind: str, payload: Any, screen: int) -> None:
    ...
    elif kind == "event":
        if isinstance(payload, dict):
            post_sim_anim_event(ext, payload)
```

`post_sim_anim_event`는 payload를 `_sim_log_queue`에 넣고, `_drain_sim_log_queue`가 꺼내서 `_sim_ui_sink_anim_event`로 보냅니다.

### 3-B.2 `_sim_ui_sink_anim_event`가 최종적으로 호출하는 함수: `handle_sim_event_for_animation`

이 함수가 “이벤트를 받아서 XML 만들고/역파싱하고/룰 매칭해서/JSON 실행”의 중심입니다.

```4772:4858:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_window.py
def handle_sim_event_for_animation(ext: Any, payload: Dict[str, str], verbose: bool = True) -> None:
    ...
    # 이벤트 -> XML 생성 -> 역파싱 -> 매핑 -> JSON 실행
    if seq in xml_generator.FROM_TO_SEQS:
        xml_text = xml_generator.build_xml_string(...)
    else:
        xml_text = xml_generator.build_xml_string(...)
    parsed = xml_generator.parse_xml_string(xml_text) or {}
    ...
    mapped_json, mapped_meta, matched_rule, matched_source = _resolve_event_animation_entry(...)
    if mapped_json:
        _execute_mapped_sequence_stub(...)
```

여기서 “어떤 JSON이 선택되는가?”는 `_resolve_event_animation_entry(...)`가 결정합니다.

---

## 3-C) “프리런 결과는 어디에 저장되고 어떻게 다시 재생되나?”

정답:
- **저장 위치**: 메모리(변수) — `ext._sim_prerun_results_by_screen`
- **재생 시작 위치**: `_drain_sim_log_queue`에서 `ext._sim_prerun_done_evt`를 감지한 순간
- **재생 루프**: `_tick_playback`(UI 업데이트 스트림 구독)에서 매 프레임 `player.tick()` 호출

즉, 파일로 저장하지 않고 “메모리에 저장했다가 바로 재생”하는 구조입니다.
파일 저장이 필요하면, `ext._sim_prerun_results_by_screen`를 JSON으로 덤프하는 기능을 추가하면 됩니다.

### 3.2 프리런(prerun)은 무엇을 하는가?

프리런은 “시뮬 엔진을 빨리 돌려서” 타임라인 배열을 만드는 단계입니다.

#### 3.2.1 프리런이 “미리 저장”하는 방식의 원리(핵심 아이디어)

핵심 아이디어는 딱 2가지입니다.

1) **엔진이 원래 UI로 보내던 것(on_log / on_event / on_progress)을 가로채서 리스트에 저장한다.**
2) 그 상태로 `engine.tick(매우 큰 dt)`를 반복해서 **가능한 빨리 끝까지** 진행한다.

##### 3.2.1-A `engine.tick(dt)`가 “정확히” 하는 일 (그리고 `1e6`이 뭔가?)

- **`tick(dt)`의 의미**: 시뮬 “내부 시간”을 \(dt\) 만큼 앞으로 진행시키는 호출입니다.
  - JS 비유: 게임 루프의 `update(dt)`와 같습니다. (단, 여기 dt는 보통 **초 단위**로 생각하면 됩니다.)
- **그 안에서 일어나는 일(감각적으로)**:
  - 엔진은 “다음 이벤트가 언제 발생하는지(예: 3.2초 뒤, 10초 뒤)”를 알고 있고,
  - `tick(dt)`가 호출되면, 그 \(dt\) 구간 안에 들어있는 이벤트들을 순서대로 처리하면서
  - 그 과정에서 `on_event(...)`, `on_progress(...)`, `on_log(...)` 같은 콜백을 호출합니다.
- **`1e6`의 뜻**:
  - `1e6`은 과학 표기법으로 \(1 \times 10^6\) = **1,000,000** 입니다.
  - 즉 `engine.tick(1e6)`는 “시뮬 시간을 **1,000,000(초로 가정)** 만큼 크게 밀어보자”는 의미입니다.

왜 굳이 이렇게 큰 값을 쓰냐면:
- 프리런은 “실시간처럼 0.016초씩 조금씩” 굴리는 게 목적이 아니라,
- **최종 결과(타임라인)만 빨리 얻는 것**이 목적이라서,
- `dt`를 크게 주면 엔진이 “그 사이에 일어날 일”을 한 번에 처리하면서 **끝까지 빨리 도달**할 수 있습니다.

> 주의: `engine.tick(1e6)`가 “무조건 env.now가 1,000,000만큼 증가한다”는 뜻은 아닙니다.
> 실제 코드는 `while`에서 `engine.is_done` / `engine.is_running`을 보면서 반복하고, 내부적으로 “끝”에 도달하면 더 진행하지 않습니다.
> 그래서 결과적으로 `final_sim_time`은 “시뮬이 끝난 시각”에서 멈춥니다.

실제 구현을 보면:
- `prerun_engine_to_timeline()`이 엔진의 콜백을 프리런 전용 콜백으로 **덮어씌웁니다**.
- 그 콜백이 호출될 때마다 `items.append(SimTimelineItem(...))`로 저장합니다.

```141:187:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_sim_prerun_playback.py
def prerun_engine_to_timeline(...):
    items: List[SimTimelineItem] = []

    def on_log(line: str) -> None:
        items.append(SimTimelineItem(t=float(getattr(engine.env, "now", 0.0) or 0.0), kind="log", payload=str(line)))

    def on_event(payload: Dict[str, Any]) -> None:
        items.append(SimTimelineItem(t=_t_from_payload(payload), kind="event", payload=dict(payload)))

    def on_progress(payload: Dict[str, Any]) -> None:
        items.append(SimTimelineItem(t=_t_from_payload(payload), kind="progress", payload=dict(payload)))

    engine._on_log = on_log
    engine._on_event = on_event
    engine._on_progress = on_progress
```

그리고 “빨리 끝까지”는 아래 루프입니다:

```188:206:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_sim_prerun_playback.py
while True:
    if getattr(engine, "is_done", False): break
    if not getattr(engine, "is_running", False): break
    engine.tick(1e6)
```

마지막으로 재생 안정성을 위해 정렬합니다(같은 시간엔 log→event→progress 순):

```216:228:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_sim_prerun_playback.py
kind_prio = {"log": 0, "event": 1, "progress": 2}
items.sort(key=lambda it: (float(it.t), int(kind_prio.get(str(it.kind), 9))))
return SimPreRunResult(..., items=tuple(items))
```

> 요약: 프리런은 “새 알고리즘”이라기보다, **원래 엔진이 emit하던 신호를 녹화해서 리스트로 만든 것**입니다.

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

#### 3.3.1 재생이 이벤트를 “발생시키는” 원리(emit의 정체)

재생은 아래 두 단계를 매 프레임 반복합니다.

1) **현재 재생 시각(simNow)**을 계산한다.
   - wall-clock(진짜 시간)에서 경과 시간을 구하고, `speed`를 곱해 sim-time으로 변환합니다.
2) 타임라인에서 `t <= simNow`인 항목들을 순서대로 꺼내서 **emit(kind,payload,screen)** 한다.

실제 구현은 `SimTimelinePlayer.tick()`이고, “emit = 다음 처리로 넘긴다”가 코드로 그대로 드러납니다.

```60:138:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_sim_prerun_playback.py
class SimTimelinePlayer:
    def tick(self) -> None:
        ...
        wall_dt = time.perf_counter() - float(self._t0_wall)
        for scr, res in self._results.items():
            t_sim = float(wall_dt) * float(sp)
            t_sim = min(float(res.final_sim_time), float(t_sim))
            self._sim_now_by_screen[scr] = float(t_sim)

        for scr, res in self._results.items():
            t_sim = self.sim_now(scr)
            i = int(self._cursor_by_screen.get(scr, 0))
            while i < len(res.items) and float(res.items[i].t) <= float(t_sim) + 1e-9:
                it = res.items[i]
                self._emit(it.kind, it.payload, int(scr))
                i += 1
            self._cursor_by_screen[scr] = int(i)
```

> 여기서 “이벤트가 발생한다”는 건, 시뮬 엔진이 다시 계산해서 이벤트를 만든다는 뜻이 아니라
> **이미 프리런에서 녹화해둔 `kind=="event"` 항목을 시간이 되면 꺼내서 emit**한다는 뜻입니다.

#### 3.3.2 emit된 event가 “애니 실행”으로 이어지는 구체 경로

재생 중 `_emit(kind, payload, screen)`은 `control_window.py`에서 다음처럼 연결되어 있습니다.

- `kind=="event"`이면 `post_sim_anim_event(ext, payload)`로 UI 큐에 넣음
- UI 큐가 drain될 때 최종적으로 `handle_sim_event_for_animation(ext, payload)`가 호출됨

```3640:3661:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_window.py
def _emit(kind: str, payload: Any, screen: int) -> None:
    if kind == "log":
        post_sim_history_line(ext, line)
    elif kind == "event":
        post_sim_anim_event(ext, payload)
    elif kind == "progress":
        post_sim_progress_update(ext, payload)
```

그리고 `handle_sim_event_for_animation`이 “event → XML 표준화 → 룰/맵으로 JSON 선택 → SequenceRunner 실행”의 중심입니다(섹션 3-B 참고).

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

## 5-A) (중요) 시퀀스 편집기 “코드 따라가기 지도” — 버튼 클릭 → JSON → `SequenceRunner.run()`

### 5-A.1 편집기 창이 뜨는 시작점: 확장 로드 → `SequenceEditorWindow()`

- **파일**: `morph/tbs_control_1/extension.py`
- **함수**: `Extension.on_startup`
- **핵심**: 확장 로드 시 `SequenceEditorWindow()`를 생성해서 “시퀀스 편집기 창”을 띄웁니다.

```149:180:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\extension.py
class Extension(omni.ext.IExt):
    def on_startup(self, ext_id: str) -> None:
        ...
        build_control_window(self)
        self._sequence_window = SequenceEditorWindow()
        ...
```

### 5-A.2 UI 버튼이 연결되는 곳: `SequenceEditor._build()`

- **파일**: `morph/tbs_control_1/sequence_editor.py`
- **함수**: `_build`
- **핵심**: “실행/일시정지/중지” 버튼이 각각 `_run_steps`, `_pause`, `_stop`에 연결됩니다.

```288:326:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\sequence_editor.py
def _build(self) -> None:
    ...
    with ui.HStack(spacing=8, height=28):
        ui.Button("Step 추가", width=90, height=28, clicked_fn=self._add_step_default)
        ui.Button("실행", width=80, height=28, clicked_fn=self._run_steps)
        ui.Button("일시정지", width=90, height=28, clicked_fn=self._pause)
        ui.Button("중지(초기화)", width=110, height=28, clicked_fn=self._stop)
        ui.Button("현재스탭으로 json 생성", width=160, height=28, clicked_fn=self._update_json_from_steps)
    ...
```

### 5-A.3 JSON → Steps 변환: “현재 JSON 상태로 스텝 생성하기” → `_load_steps_from_json()`

- **핵심**: `json.loads(...)`로 리스트를 만들고 `self._steps`에 저장 → UI를 리프레시합니다.

```905:918:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\sequence_editor.py
def _load_steps_from_json(self) -> None:
    data = json.loads(self._json_model.get_value_as_string() or "[]")
    if isinstance(data, list):
        self._steps = data
        ...
        self._schedule_refresh()
```

### 5-A.4 “실행” 버튼: `_run_steps()` → `SequenceRunner.run(self._steps)`

- **핵심 데이터 저장소**:
  - `self._steps`: 편집기에서 만든 “스텝 리스트”
  - `self._runner`: 실제 실행 엔진(`SequenceRunner`)

```920:925:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\sequence_editor.py
def _run_steps(self) -> None:
    self._flush_rotate_step_flags_to_dict()
    self._flush_timing_models_to_dict()
    self._sync_runtime_start_options_to_steps()
    self._runner.run(self._steps)
```

### 5-A.5 실제 애니메이션 실행 본체: `SequenceRunner`

- **파일**: `morph/tbs_control_1/sequence_engine.py`
- **클래스**: `SequenceRunner`
- **핵심**: 분할화면이라면 `self._usd_context_name`이 “어느 스테이지에 적용할지”를 결정합니다.

```1032:1143:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\sequence_engine.py
@dataclass
class SequenceRunner:
    def __post_init__(self) -> None:
        self._usd_context_name: Optional[str] = None
        self._steps: List[Dict[str, Any]] = []
        ...
    def _stage(self) -> Optional[Usd.Stage]:
        return _get_stage_for_context(getattr(self, "_usd_context_name", None))
```

### 5-A.6 LAM 라이브러리 “선택 실행”: `_play_selected_lam_json()`

- **핵심**: 파일을 읽어서 편집기 JSON 모델에 넣고 → `_load_steps_from_json()` → `_run_steps()`

```241:262:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\sequence_editor.py
def _play_selected_lam_json(self) -> None:
    txt = p.read_text(encoding="utf-8")
    self._json_model.set_value(txt)
    self._load_steps_from_json()
    self._run_steps()
```

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

## 7-A) (중요) 웹 브릿지 “코드 따라가기 지도” — HTTP POST → `cmd` 디스패치 → UI 함수 호출

### 7-A.1 서버 시작점: 확장 로드 시 `start_tbs_remote_http_bridge(self)`

- **파일**: `morph/tbs_control_1/extension.py`
- **함수**: `Extension.on_startup`
- **핵심**: 기본값으로 웹 브리지를 켭니다(환경변수로 끌 수 있음).

```233:251:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\extension.py
if _want_tbs_remote_http_bridge() and start_tbs_remote_http_bridge is not None:
    try:
        start_tbs_remote_http_bridge(self)
    except Exception:
        pass
```

### 7-A.2 “cmd 문자열”이 실제 기능으로 매핑되는 곳: `_dispatch_command`

여기가 웹 버튼과 Kit 기능이 “직접 연결”되는 스위치문입니다.

예시:
- `cmd=="sim_start"` → `on_sim_start_clicked(ext)` (즉, 웹에서 시뮬 시작 = UI Start 클릭과 동일 경로)
- `cmd=="sim_viewport_split"` → `sim_multi_view.apply_sim_viewport_split_layout(ext, n)` (웹에서 분할 변경)

```625:708:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\kit_remote_http_bridge.py
def _dispatch_command(ext: Any, data: Dict[str, Any]) -> Dict[str, Any]:
    cmd = str(data.get("cmd", "") or "").strip()
    ...
    if cmd == "sim_start":
        ...
        on_sim_start_clicked(ext)
        return {"ok": True}
    ...
    if cmd == "sim_viewport_split":
        ...
        sim_multi_view.apply_sim_viewport_split_layout(ext, n)
        return {"ok": True, "count": n}
```

> 핵심 포인트: 웹 요청도 결국 “UI 코드 함수(on_sim_start_clicked 등)”를 그대로 호출합니다.
> 그래서 문서에서 “웹 → 기능”을 이해하는 가장 빠른 방법은 `_dispatch_command`에서 `cmd` 케이스를 보고, 거기서 호출하는 함수로 점프하는 겁니다.

---

## 7-B) (중요) 분할화면/화면별 USD “코드 따라가기 지도” — split 적용 → 컨텍스트 생성 → 화면별 Stage 오픈

여기서 말하는 “분할화면”은 단순히 뷰포트를 나눈 게 아니라, **화면별로 서로 다른 USD 컨텍스트(=스테이지)를 가진다**는 뜻입니다.

### 7-B.1 분할 적용 시작점: `apply_sim_viewport_split_layout(ext, n)`

- **파일**: `morph/tbs_control_1/sim_multi_view.py`
- **함수**: `apply_sim_viewport_split_layout`
- **핵심**: 경합을 피하려고 “다음 프레임”에 실제 적용 함수(`_apply_sim_viewport_split_layout_impl`)를 호출합니다.

```1738:1757:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\sim_multi_view.py
def apply_sim_viewport_split_layout(ext: Any, split_n: int) -> None:
    ...
    async def _deferred() -> None:
        await kit_app.get_app().next_update_async()
        ...
        _apply_sim_viewport_split_layout_impl(ext, n)
    asyncio.ensure_future(_deferred())
```

### 7-B.2 실제 분할/컨텍스트 생성/스테이지 오픈: `_build_multi_split_async(...)`

아래 루프가 “보조 화면(2~N)”을 만들며, 각 화면에 대해:
- `ctx_name = "morph_tbs_split_aux_{ti}"`
- USD 컨텍스트 생성/획득
- 같은 USD 파일을 **보조 컨텍스트에 오픈**
- `create_viewport_window(... usd_context_name=ctx_name ...)`로 화면 생성

그리고 최종적으로:
- `ext._sim_multi_context_names = ctx_names` 에 **보조 컨텍스트 이름 리스트를 저장**합니다.

```1465:1604:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\sim_multi_view.py
for ti in range(1, n):
    ctx_name = f"morph_tbs_split_aux_{ti}"
    ctx = _named_usd_context(ctx_name)
    ctx_names.append(ctx_name)
    ok_open, err_open = await _open_aux_stage_with_unique_session(ctx, usd_path, ext, token, ti)
    ...
    vp_obj = create_viewport_window(name=wname, usd_context_name=ctx_name, ...)
...
ext._sim_multi_context_names = ctx_names
```

### 7-B.3 “화면별로 시뮬을 각각 진행”과의 연결(개념)

- “분할화면 생성/컨텍스트 저장소”: `ext._sim_multi_context_names`
- “프리런 결과(시뮬 로그/이벤트/진행) 저장소”: `ext._sim_prerun_results_by_screen` (화면 번호별 dict)
- “애니메이션 실행 대상 스테이지”: `SequenceRunner._usd_context_name` → `_stage()`가 해당 컨텍스트의 stage를 가져옴

### 7-B.4 (중요) screen 번호 → `usd_context_name` → “어느 USD(Stage)에 적용되는가?”를 코드로 1:1로 연결

여기서 가장 헷갈리는 포인트는 보통 이겁니다.

- 화면2에서 이벤트가 발생했는데, 왜 화면1(기본) USD가 움직이기도 했지?
- “event payload의 screen 정보”는 어디서 붙고, 그게 어떻게 `SequenceRunner`로 전달되지?

이 프로젝트의 연결고리는 아래 3단계입니다.

#### (1) 이벤트 payload에 `tbs_sim_screen="1..N"`이 붙는다

시뮬 엔진이 `on_event(payload)`를 호출할 때, 엔진 생성 시점에 `event_tags={"tbs_sim_screen": "2"}` 같은 태그가 합쳐져 들어옵니다.
그래서 이후 파이프라인은 payload에서 `payload["tbs_sim_screen"]`를 읽어 “어느 화면 이벤트인지”를 판단할 수 있습니다.

> 문서 팁: `control_window.py`에서 `event_tags={"tbs_sim_screen": ...}`를 검색하면 “화면별 엔진 생성” 구간을 찾을 수 있습니다.

#### (2) `tbs_sim_screen` → “그 화면의 USD 컨텍스트 이름”으로 변환한다

이 변환 함수가 바로 `_usd_context_name_for_sim_screen(ext, screen)` 입니다.

```1578:1600:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_window.py
def _usd_context_name_for_sim_screen(ext: Any, screen: int) -> Optional[str]:
    ...
    if s <= 1:
        return None
    names = list(getattr(ext, "_sim_multi_context_names", []) or [])
    idx = s - 2
    if 0 <= idx < len(names):
        nm = str(names[idx] or "").strip()
        return nm if nm else None
    return f"morph_tbs_split_aux_{s - 1}"
```

해석:
- **화면1** → `None` (기본 `omni.usd` 컨텍스트)
- **화면2** → `"morph_tbs_split_aux_1"` (또는 `ext._sim_multi_context_names[0]`)
- **화면3** → `"morph_tbs_split_aux_2"` …

즉, screen(1-based)이 곧 “어느 USD 컨텍스트/스테이지에 적용할지”를 결정합니다.

#### (3) 선택된 `usd_context_name`이 `SequenceRunner.run(..., usd_context_name=...)`로 들어간다

시뮬 이벤트가 JSON으로 매핑되면 `_execute_mapped_sequence_stub(...)`에서 실제 실행 job을 만들고,
job 안의 `tbs_sim_screen`을 읽어서 **해당 화면용 runner**를 꺼낸 뒤, 아래처럼 실행합니다.

```1016:1019:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_window.py
_ctx_run = _usd_context_name_for_sim_screen(ext, scr_i)
if runner_obj is not None:
    runner_obj.run(job.get("parsed", []), usd_context_name=_ctx_run, speed_scale=sp)
```

그 다음 `SequenceRunner.run()`이 그 값을 내부 상태로 저장합니다.

```1367:1376:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\sequence_engine.py
def run(..., usd_context_name: Optional[str] = None, ...) -> None:
    self._usd_context_name = (usd_context_name or "").strip() or None
```

그리고 실제 Stage 선택은 `_get_stage_for_context(self._usd_context_name)`로 됩니다.

```817:824:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\sequence_engine.py
def _get_stage_for_context(usd_context_name: Optional[str]) -> Optional[Usd.Stage]:
    nm = (usd_context_name or "").strip()
    ctx = ou.get_context(nm) if nm else ou.get_context()
    return ctx.get_stage() if ctx else None
```

정리하면:
- **event payload**의 `tbs_sim_screen`이
- `_usd_context_name_for_sim_screen`을 통해 **컨텍스트 이름**으로 바뀌고
- `SequenceRunner.run(usd_context_name=...)`로 들어가
- 최종적으로 `ou.get_context(name).get_stage()`가 선택됩니다.

이 연결고리를 따라가면 “왜 분할화면에서 기본화면이 같이 움직이는지” 같은 문제도 원인을 좁힐 수 있습니다.


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
