# morph.tbs_control_1 기술 문서 (개발자용)

이 문서는 `morph.tbs_control_1` 확장 기능을 **다른 프로그래머에게 “코드 예시까지 포함해” 설명할 수 있는 수준**으로 정리한 기술 문서입니다.

목표:
- **어떤 모듈/함수/변수**가 무엇을 담당하는지
- 데이터가 **어떤 형식(payload/key)**으로 흘러가는지
- 스레드/큐/메인(UI) 스레드 제약을 어떻게 해결하는지
- 문서만 보고도 “간단한 시뮬/애니/웹 호출”을 **직접 작성**할 수 있는 수준의 예시 제공

---

## 0) 빠른 파일 지도

- **시뮬레이션 UI/오케스트레이션**: `morph/tbs_control_1/control_window.py`
- **프리런(오프라인)→재생(플레이백)**: `morph/tbs_control_1/control_sim_prerun_playback.py`
- **시뮬 엔진(SimPy 기반)**: `morph/tbs_control_1/simulation_engine.py`
- **이벤트→XML 생성/역파싱**: `morph/tbs_control_1/xml_generator.py`
- **시퀀스 실행 엔진(JSON step 실행)**: `morph/tbs_control_1/sequence_engine.py`
- **애니 편집 UI**: `morph/tbs_control_1/sequence_editor.py`
- **웹/HTTP 브리지(로컬 서버)**: `morph/tbs_control_1/kit_remote_http_bridge.py` + `web/tbs_kit_remote/*`

---

## 1) 시뮬레이션: “프리런(백그라운드) → 결과 재생” 구조

### 1.1 한 줄 요약

Start 클릭 시, 실시간으로 `TBSSimulationEngine.tick(dt)`를 돌리는 대신:

1) **백그라운드 스레드**에서 시뮬을 가능한 빠르게 끝까지 돌려(프리런)
2) 그 결과(이벤트/진행/로그의 시간순 목록)를 `SimTimelinePlayer`가 **wall-clock에 맞춰 재생**하며
3) 재생 중에는 기존 UI 파이프라인(`post_sim_*`)에 payload를 그대로 흘려보냅니다.

핵심 장점:
- 시작 시점에 **화면별 총 시뮬시간(=프리런 최종 env.now)** 를 확정할 수 있어 막대그래프 스케일/끝값이 정확해집니다.
- 재현성이 높아지고(프리런 결과가 고정), UI 부하/스레드 타이밍 이슈가 줄어듭니다.

### 1.2 프리런 결과 데이터 구조

프리런 결과는 화면별로 `SimPreRunResult`로 저장됩니다.

```1:25:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_sim_prerun_playback.py
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

- **`t`**: 시뮬 시간(초). UI에서 `t(sim)`으로 표시하는 값.
- **`kind`**: `"log" | "event" | "progress"` (UI에 어떤 sink로 보낼지 결정)
- **`payload`**: 기존과 동일한 dict/문자열(엔진이 emit하던 그대로)
- **`final_sim_time`**: 프리런 완료 시점의 `engine.env.now` (이 값이 “총 시뮬시간”)

### 1.3 프리런(오프라인) 계산 방식

프리런은 엔진 콜백을 “UI로 직접 보내지 않고” 수집합니다. 구현은 `prerun_engine_to_timeline()`입니다.

```141:228:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_sim_prerun_playback.py
def prerun_engine_to_timeline(...):
    items: List[SimTimelineItem] = []
    ...
    def on_event(payload: Dict[str, Any]) -> None:
        items.append(SimTimelineItem(t=_t_from_payload(payload), kind="event", payload=dict(payload)))
    def on_progress(payload: Dict[str, Any]) -> None:
        items.append(SimTimelineItem(t=_t_from_payload(payload), kind="progress", payload=dict(payload)))
    ...
    engine._on_log = on_log
    engine._on_event = on_event
    engine._on_progress = on_progress
    ...
    while True:
        if getattr(engine, "is_done", False): break
        if not getattr(engine, "is_running", False): break
        engine.tick(1e6)
        ...
    final_sim = float(getattr(engine.env, "now", 0.0) or 0.0)
    ...
    items.sort(key=lambda it: (float(it.t), int(kind_prio.get(str(it.kind), 9))))
    return SimPreRunResult(...)
```

포인트:
- `engine.tick(1e6)`처럼 **큰 sim_delta**를 반복 호출해서 가능한 빠르게 `env.step()`를 진행시킵니다.
- 수집된 아이템은 `t` 기준으로 정렬하되, 동일 시각에서는 `log → event → progress` 순으로 정렬합니다.

### 1.4 재생(플레이백) 방식

재생은 `SimTimelinePlayer.tick()`이 담당합니다.

```60:138:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_sim_prerun_playback.py
class SimTimelinePlayer:
    def tick(self) -> None:
        with self._lock:
            sp = max(0.05, float(self._speed()))
            wall_dt = time.perf_counter() - float(self._t0_wall)
            for scr, res in self._results.items():
                t_sim = float(wall_dt) * float(sp)
                t_sim = min(float(res.final_sim_time), float(t_sim))
                self._sim_now_by_screen[scr] = float(t_sim)

        for scr, res in self._results.items():
            t_sim = self.sim_now(scr)
            i = self._cursor_by_screen[scr]
            while i < len(items) and float(items[i].t) <= float(t_sim) + 1e-9:
                self._emit(items[i].kind, items[i].payload, int(scr))
                i += 1
            self._cursor_by_screen[scr] = i
```

- wall-clock 경과시간 × UI 속도(`_sim_speed_model`)로 `t_sim`을 계산해 커서를 전진
- `t_sim` 이하의 아이템을 순서대로 emit → UI에 동일한 payload로 전달

### 1.5 control_window에서의 “프리런 완료 감지 → 플레이어 시작”

프리런이 끝나면 UI 메인 스레드에서 `_drain_sim_log_queue()`가 이를 감지하고 플레이어를 시작합니다.

```3595:3714:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_window.py
def _drain_sim_log_queue(ext: Any) -> None:
    # 프리런 완료 시점에 타임라인 플레이어를 시작한다(메인 스레드에서만).
    ev = getattr(ext, "_sim_prerun_done_evt", None)
    if (not started) and ev is not None and ev.is_set():
        results = getattr(ext, "_sim_prerun_results_by_screen", None)
        ...
        by[str(int(scr))] = float(res.final_sim_time)  # 화면별 총시간 확정
        ...
        playback_engs.append(PlaybackEngine(final_sim_time=float(rr.final_sim_time)))
        ext._sim_engines = playback_engs
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

여기서 중요한 값:
- `ext._sim_last_total_est_by_screen[screen] = final_sim_time`: **막대그래프 스케일/끝값의 단일 소스**
- `PlaybackEngine.env.now`: 포트 아래 EP 타임라인이 `env.now`를 참고하므로, 재생 중에도 `env.now`가 업데이트되어야 함

### 1.6 “프리런 모드에서 엔진 콜백을 UI로 직접 보내지 않는” 이유

프리런은 “계산” 단계이므로 UI에 직접 log/event/progress를 쏘면 실제 재생과 충돌합니다. 따라서 엔진 생성 시 콜백을 노옵으로 주입합니다(프리런 수집 단계에서만 engine 콜백을 덮어씀).

```5571:5594:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_window.py
# 프리런/재생 모드에서는 엔진 콜백을 UI로 직접 보내지 않는다(프리런 수집 → 재생 단계에서만 UI로 emit).
engine = TBSSimulationEngine(
    ...
    on_log=lambda _line: None,
    on_event=lambda _payload: None,
    on_progress=lambda _payload: None,
    on_gate=lambda payload: float(_estimate_anim_duration_for_gate_payload(ext, payload or {})),
    ...
)
```

### 1.7 예시 코드: “프리런 결과를 파일(JSON)로 저장” (개념 예시)

아래 코드는 프리런 결과의 핵심(`final_sim_time`, `items`)를 JSON으로 저장하는 예시입니다.
실제 프로젝트에서는 파일 IO 정책에 맞게 경로/권한을 조정하세요.

```python
import json
from pathlib import Path

def dump_prerun(results_by_screen: dict[int, "SimPreRunResult"], out_path: str):
    out = {
        "screens": {
            str(scr): {
                "final_sim_time": res.final_sim_time,
                "items": [{"t": it.t, "kind": it.kind, "payload": it.payload} for it in res.items],
            }
            for scr, res in results_by_screen.items()
        }
    }
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
```

---

## 2) 애니메이션 편집(Sequence Editor)

### 2.1 역할

`sequence_editor.py`는 “JSON step 목록”을 UI로 편집하고, `SequenceRunner`로 실행/중지/일시정지하는 편집기입니다.

```4:44:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\sequence_editor.py
sequence_editor.py — TBS 시퀀스 편집기 UI (별도 창)
【역할】
- STEP_TYPES(USD_TIMELINE, MOVE, ROTATE, DELAY 등)별로 행 UI를 구성하고, JSON 저장/불러오기.
- SequenceRunner에 스텝 리스트를 넘검 실행·일시정지·중지.
...
```

### 2.2 핵심 데이터: Step dict

step은 dict이며 기본적으로 다음 키들을 사용합니다.
- **`type`**: `"USD_TIMELINE" | "MOVE" | "ROTATE" | "DELAY"`
- **`prim`**: 대상 prim 식별자(경로 또는 이름, 복수는 공백/콤마)
- **`duration`**: 초 단위
- **`run_with_previous`**: 이전 step과 병렬 그룹으로 묶을지 여부
- **`step_delay_ms`**: 병렬 그룹 내 오프셋/다음 그룹 지연(ms)

Step 타입 목록은 아래에 있습니다.

```70:84:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\sequence_editor.py
STEP_TYPES = ["USD_TIMELINE", "MOVE", "ROTATE", "DELAY"]
...
self._runner = SequenceRunner(...)
```

### 2.3 UI에서 “바로 clear()하지 않고 post_update로 refresh”하는 패턴

Omni UI 이벤트 도중 container를 clear하면 오류가 날 수 있어, 다음 프레임(post_update)로 넘깁니다.

```164:192:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\sequence_editor.py
def _schedule_refresh(self) -> None:
    ...
    stream = app.get_app().get_post_update_event_stream()
    self._refresh_sub = stream.create_subscription_to_pop(_do, name="...sequence_editor.refresh")
```

### 2.4 예시 코드: “MOVE step 1개짜리 시퀀스 실행”

```python
from morph.tbs_control_1.sequence_engine import SequenceRunner

runner = SequenceRunner()
steps = [
    {"type": "MOVE", "prim": "Mesh_308", "duration": 1.0, "dx": 100, "dy": 0, "dz": 0},
]
runner.run(steps, usd_context_name=None, speed_scale=1.0)
```

---

## 3) 시뮬 애니 매핑 방식 (이벤트 → JSON 선택 → 실행)

### 3.1 이벤트의 표준화: XML 생성/역파싱

시뮬 이벤트는 내부 payload로 들어오지만, 매핑 로직은 “장비 메시지(TIB) 형식”을 흉내낸 XML을 생성하고 다시 역파싱해 **표준 키**(`sequence_name/port_id/from/to`)를 뽑아 사용합니다.

XML 생성:

```203:266:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\xml_generator.py
def build_xml_string(sequence_name: str, port_id: Optional[int] = None, from_port_id: Optional[int] = None, to_port_id: Optional[int] = None) -> str:
    ...
    if seq in FROM_TO_SEQS:
        ...
    elif seq in PORT_ID_ONLY_SEQS:
        ...
```

역파싱:

```301:349:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\xml_generator.py
def parse_xml_string(xml_text: str) -> Optional[dict]:
    ...
    seq_name = body.get(ATTR_SEQUENCE_NAME, "") or ""
    port_id, from_port_id, to_port_id = _extract_values_from_tree(root)
    out: Dict[str, str] = { "sequence_name": seq_name_u, "port_id": port_id_s, "from_port_id": from_port_id_s, "to_port_id": to_port_id_s, ... }
```

### 3.2 JSON 시퀀스 실행 엔진: SequenceRunner

`SequenceRunner`는 step 리스트를 받아 병렬 그룹(run_with_previous) 규칙으로 실행합니다.

> 상세 실행 규칙(그룹/앵커/step_delay_ms)은 `sequence_engine.py` 상단 설명과 `SequenceRunner.run()`을 참고하세요.

---

## 4) 로그 방식 (스레드 → UI 큐 → 패널)

### 4.1 왜 큐가 필요한가

시뮬/웹/기타 스레드에서 올라오는 업데이트를 Omni UI 위젯에 직접 적용하면 스레드-unsafe 문제가 생길 수 있어,
`control_window.py`는 `_sim_log_queue`로 **kind/payload**를 큐잉하고, 메인 스레드에서 `_drain_sim_log_queue()`가 소비합니다.

```3568:3593:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\control_window.py
def _dispatch_sim_ui_queue_item(ext: Any, kind: str, payload: Any, panel_mode: SimLogPanelMode) -> None:
    if kind == SimUiQueueKind.PROGRESS.value: ...
    elif kind == SimUiQueueKind.ANIM_EVENT.value: ...
    elif kind == SimUiQueueKind.ACTION.value: ...
    elif kind == SimUiQueueKind.GATE.value: ...
    elif kind == SimUiQueueKind.HISTORY_LINE.value: ...
```

프리런/플레이백 모드에서도 최종적으로는 동일한 `post_sim_*` 경로를 타므로, UI 로깅 방식은 동일하게 유지됩니다.

---

## 5) 웹 연결 방식 (Kit HTTP Bridge)

### 5.1 목적

브라우저에서 Kit 내부의 TBS 제어창 기능을 호출(시작/정지/리셋/스냅샷/리소스 등)하기 위해 로컬 HTTP 서버를 제공합니다.

```4:16:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\kit_remote_http_bridge.py
Kit 내 HTTP 브리지 — 브라우저에서 TBS 제어창·USD Load 와 동일 동작을 호출한다.
...
모든 ext / omni.ui 접근은 메인 스레드(업데이트 스트림)에서만 수행한다.
```

### 5.2 “메인 스레드에서만 ext/UI 호출”을 보장하는 방법

웹 서버 스레드는 `_run_on_main()`을 통해 “메인 스레드 큐”에 작업을 넣고 결과를 기다립니다.

```62:86:c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.tbs_control_1\morph\tbs_control_1\kit_remote_http_bridge.py
def _run_on_main(fn: Callable[[], Any]) -> Any:
    fut: Future = Future()
    def _wrap() -> None:
        try: fut.set_result(fn())
        except Exception as e: fut.set_exception(e)
    with _pending_lock:
        _pending_main.append((fut, _wrap))
    return fut.result(timeout=120.0)

def _pump_main_queue(_e: Any) -> None:
    while True:
        with _pending_lock:
            if not _pending_main: break
            _, run = _pending_main.popleft()
        run()
```

### 5.3 API 엔드포인트 개요(대표)

- `GET /api/state`: 현재 채널 스냅샷(포트 상태/진행/이력/EP 타임라인 등)
- `POST /api/command`: `sim_start`, `sim_stop`, `sim_reset`, `xml_run` 등

> 웹 UI 구현은 `web/tbs_kit_remote/index.html`, `tbs_panel.js`, `tbs_panel.css`를 참고하세요.

### 5.4 예시 코드: 브라우저/스크립트에서 시뮬 시작 호출

```bash
curl -X POST http://127.0.0.1:8720/api/command ^
  -H "Content-Type: application/json" ^
  -d "{\"cmd\":\"sim_start\",\"data\":{}}"
```

---

## 부록 A) “왜 prim_matches=0이 생기나?”

MOVE/ROTATE step의 `prim`이 현재 stage에서 하나도 매칭되지 않으면(경로 불일치/이름 변경/분할 USD에 prim 부재),
해당 step은 “실행되더라도 움직일 대상이 없어” 결과적으로 애니가 안 움직일 수 있습니다.

prim 해석의 단일 소스는 `sequence_engine.resolve_prim_paths()` / `resolve_prim_paths_multi()` 입니다.

---

## 부록 B) 문서 작성 팁(유지보수)

이 문서의 핵심은 “코드 라인 인용이 최신 상태를 반영”하는 것입니다.
리팩터링으로 파일/함수 이동이 있으면 아래 순서로 업데이트하세요.

- 1) 엔트리포인트: `on_sim_start_clicked`, `SimTimelinePlayer`, `SequenceEditorWindow`
- 2) 데이터 구조: payload keys (`sim_time`, `tbs_sim_screen`, `sim_total_est_sec`, `seq`, `from_port_id`, `to_port_id`)
- 3) UI/스레드 경계: `_drain_sim_log_queue`, `_run_on_main`, update stream subscription
