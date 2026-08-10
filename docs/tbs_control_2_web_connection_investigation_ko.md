# TBS 웹 연결 — 실무 현상 조사 (HyView / Livestream)

> **작성일**: 2026-07-06  
> **상태**: **Kit 측 1차 완화 패치 적용** (§7.8) — 실무 로그로 효과 검증 대기  
> **원칙**: 확인된 사실 / 추정 / 미확인 구분. 웹 측 수정은 별도.

---

## 0. 조사 목적

Kit 앱을 **streaming 모드**로 실행하고 **웹(hyview_client)** 과 연결했을 때,  
Kit **로컬 UI**(`ui.Window` 제어창)에서는 빠르게 동작하는 기능이 **웹 버튼·체크박스**로 동일 동작을 시키면 **수 분간 멈춤**이 발생한다.

---

## 1. 실무에서 보고된 현상 (2026-07-06)

### 1.1 기본 동작 비교 (2차 피드백 반영)

| 조건 | 동작 |
|------|------|
| Kit만 streaming 모드 | **정상** |
| 웹 연결 후 — **스트리밍 화면만** | **정상** (Kit 화면이 웹에 잘 보임) |
| 웹 연결 후 — **Kit 제어창(`ui.Window`) 클릭** | **정상** (즉시 반영) |
| 웹 연결 후 — **Kit Viewport 직접 조작** | **정상** |
| 웹 연결 후 — **웹 UI 버튼·체크박스** (T2V 전송) | **5분 전후 멈춤** (2~3분 ~ 5분 이상) |
| 웹 → Kit **다른 T2V API** 전송 | **동일** 패턴 |

**핵심**: 문제는 “웹 연결 자체”가 아니라 **웹 → Kit 메시지(T2V)가 발생하는 순간**에 국한된다.

---

## 1.0 실무 웹·Kit 연결 구조 (3차 — 레이아웃·실행 방식)

> 실무 웹 코드는 보안상 본 레포에 없음. 아래는 사용자 구두 설명 기준 **확인된 운영 구조**.

### 실행 순서

```
1. Kit — streaming 모드로 실행 (morph.editor_streaming 등)
       └─ 앱 시작 시 2분할 Viewport (화면1·화면2) 기본 ON
2. 웹 — 프로젝트 경로에서:
       pnpm i
       pnpm dev
       → http://localhost:3000 (예) 접속
3. 웹 페이지에서 Kit 스트림 연결
```

### 웹 페이지 레이아웃 (실무)

```
┌─────────────────────────────────────┐
│  상단 1/2 — 웹에서 만든 제어창        │  ← EP, EBS, 시뮬 버튼·체크박스 (T2V)
│  (HyView / EBS UI)                   │
├─────────────────────────────────────┤
│  하단 1/2 — Kit 스트리밍 영상         │  ← WebRTC / livestream
│  (가로로 긴 wide 형태)               │
└─────────────────────────────────────┘
```

### 연결 시 Kit 쪽 변화 (확인)

- 웹과 연결되면 Kit Viewport/스트림이 **하단 1/2 영역에 맞게** 리사이즈됨  
- 결과적으로 **가로로 긴(wide) 비율** — Kit 단독(16:9 풀창)과 **다른 aspect**  
- 이전 로그 `1855×501` → `1855×500` 과 **정합**: 넓고 낮은 뷰포트 높이(홀수→짝수 보정)

### 본 레포 테스트 클라이언트와의 차이

| 항목 | 실무 웹 | 레포 `hyview_client` (로컬 테스트) |
|------|---------|-----------------------------------|
| 실행 | `pnpm dev` → `:3000` | 동일 패턴 |
| 레이아웃 | **상 1/2 제어 / 하 1/2 스트림** | `main-grid`: 스트림·패널 **좌우** 배치 |
| 제어 UI | 실무 HyView 페이지 | `EbsSimPanel.tsx` |
| Kit | streaming + **2분할** | 문서상 동일 전제 |

→ 실무 현상(가로 긴 스트림, resize 로그)은 **하단 half-height 컨테이너**와 직접 연관될 가능성이 큼.

### 현재 운영 구성 (한 줄)

**Kit 2분할 Viewport + streaming 실행 → 웹(localhost) 상단 제어창 + 하단 스트림 embed → T2V는 상단 버튼만.**

### 1.2 멈춤 시 시각적 현상 (2차)

웹 버튼·체크박스 클릭 직후:

1. Kit 화면이 **전체화면처럼 확대**되는 듯함  
2. **내부 USD도 늘어난 것처럼** 보임 (스트레치/잘못된 aspect)  
3. Kit·웹 스트림 **동시 정지**  
4. **콘솔 로그 추가 출력 없음** (완전 정적 구간)  
5. **2~3분 ~ 5분 이상** 후 동작 반영 (예: EBS 적용 체크 결과가 화면에 나타남)  
6. **원래 화면 크기로 복구** 후 정상 동작 재개  

예: EBS 적용 체크 → 한참 후 Kit·스트리밍 양쪽에 EBS 반영 → 크기 복구.

### 1.3 웹 연결 시 로그 (스트림 해상도)

```
processing dynamic resize of video stream with desired extents 1855x501 that are invalid
so they have been adjusted to 1855x500 in order to satisfy all the following criteria:

- Min: 356x256
- Max: 4096x4096
- Width: 32 byte aligned
- Height: an even number

The maximum extents is the resolution requested when the client connects to the stream
```

**관찰**: 클라이언트가 요청한 높이 **501(홀수)** → **500(짝수)** 로 보정.  
웹 클라이언트 연결 직후·조작 시 이 메시지와 멈춤이 함께 나타난다고 보고됨.

### 1.4 재현 예시 — 화면1 EP 포트 변경 / EBS 체크 (웹)

| 시점 | 현상 |
|------|------|
| 웹에서 EP 변경 클릭 | — |
| 즉시 | Kit 로그: `[EBSHandler] _on_req_eqp_change - {'sender_id': 1, 'case': 0, ...}` **정상 수신** |
| 직후 | Kit **전체화면처럼 확대**, USD 스트레치, 스트림 정지 |
| 2~3분 ~ 5분+ | **콘솔 로그 없음** (정적) |
| 완료 시 | `[TBS/EPVis] ... (hyview_eqp_change)` 등, 화면 **원래 크기 복구** |

**Kit 제어창에서 동일 EP/EBS 조작**: `[TBS/EPVis]` **즉시** (수 초 이내 아님, **바로**).

**요약**: `[EBSHandler]` 직후 ~ `[TBS/EPVis]` 사이 **2~3분 이상(최대 5분+)** 공백, 그동안 **로그 0줄**.

### 1.5 웹 브라우저 V2T 응답 (4차)

- 웹 페이지 **콘솔/이벤트 로그의 V2T 응답**은 **멈춤이 끝나고 Kit·스트림이 다시 움직일 때** 도착함  
- 멈춤 **중간**(예: 2분 시점)에는 **응답 없음** (사용자 관찰)  
- Kit `[TBS/EPVis]` · 화면 복구 · 웹 V2T · 실제 UI 반영이 **같은 시점(T_end)** 에 묶여 있음

→ 웹 T2V는 “메시지 수신”과 “처리 완료”가 **동시에 끝나지 않음**. **전체 파이프라인 완료까지** 웹도 대기.

---

## 2. 확인된 사실 (코드·로그 기준)

### 2.1 웹 → Kit 메시지 경로

```
웹 (hyview_client)
  → WebRTC / livestream messaging
  → EBSHandler._on_req_eqp_change
  → tbs_sim_bridge.handle_eqp_change
  → schedule_on_main_thread(_work)       ← 메인 스레드 마샬링 (비동기)
  → _apply_ep_port_layout_for_sim_screen(reason="hyview_eqp_change")
  → tbs_ep_port_visibility → [TBS/EPVis] 로그
  → dispatch → V2T_response_eqp_change
```

관련 파일:

| 파일 | 역할 |
|------|------|
| `morph.hyview_messaging/.../ebs_handler.py` | T2V 수신, `[EBSHandler]` 로그 |
| `morph.hyview_messaging/.../tbs_sim_bridge.py` | `handle_eqp_change`, `schedule_on_main_thread` |
| `morph.tbs_control_2/.../control_window.py` | `_apply_ep_port_layout_for_sim_screen` |
| `morph.tbs_control_2/.../tbs_ep_port_visibility.py` | `[TBS/EPVis]` 로그 |

### 2.2 Kit 로컬 UI 경로 (대비)

제어창 EP 콤보 변경 시:

```
ui.Window 콤보 변경
  → on_sim_ep_count_changed / on_sim_ep_count_changed_for_case
  → (이미 메인 스레드) _apply_ep_port_layout / EPVis
```

**차이점 (패치 후)**: 웹 경로는 `schedule_on_main_thread`로 **메시징 스레드를 block 하지 않음**.  
V2T는 `_work` 완료 콜백에서 전송. 로컬 UI는 **메인 스레드에서 직접** 실행.

**진단**: `[HyView/bridge] queued` → `work_start` → `work_done` → `[TBS/EPVis]` 순서로 구간 측정.

### 2.3 웹 클라이언트 스트림 설정

`hyview_client/AppStream.tsx`:

```typescript
width: 1920,
height: 1080,
fps: 60,
```

실제 resize 로그는 **1855×501** — 브라우저/컨테이너 크기에 따른 **동적 resize**로 보임 (요청 1920×1080과 다름).

### 2.4 EBSHandler는 요청을 “받는다”

`[EBSHandler] _on_req_*` 가 **즉시** 찍힌다는 것은:

- T2V 메시지 전달 ✓  
- 핸들러 진입 ✓  
- **`handle_*` → `run_on_main_thread(_work)` 완료·EPVis 반영은 그 이후** (2~5분+ 지연)

### 2.5 멈춤 구간에 로그가 없다 (2차 확인)

`[EBSHandler]` 와 `[TBS/EPVis]` **사이** Kit 콘솔에 **추가 로그가 전혀 없음**.

**의미 (추정)**:
- `_work()` 안의 `print`/`[TBS/EPVis]` 가 아직 실행되지 않았거나  
- 메인 스레드가 **로그 없는 블로킹 작업**(스트림 resize·렌더·동기 대기)에 묶여 **update pump가 `_work`에 도달하지 못함**

### 2.6 웹 연결만으로는 멈추지 않음 (2차 확인)

| 동작 | 연결 중 멈춤? |
|------|--------------|
| 스트리밍 시청 | 아니오 |
| Kit 제어창 버튼 | 아니오 |
| Kit Viewport 조작 | 아니오 |
| **웹 버튼·체크박스 (T2V)** | **예** |

→ 원인 후보를 **livestream 일반 부하**에서 **T2V 처리 경로**로 좁힌다.

### 2.7 지연 시간 실측 감 (2차)

| 구간 | 시간 |
|------|------|
| `[EBSHandler]` → `[TBS/EPVis]` | **2~3분** ~ **5분 이상** |
| Kit 제어창 동일 조작 → EPVis | **즉시** |

### 2.8 V2T 응답 타이밍 (4차 확인)

| 이벤트 | 시점 |
|--------|------|
| T2V 전송 | T0 |
| `[EBSHandler]` | T0 즉시 |
| 웹 V2T 응답 | **T_end** (멈춤 해소 후) |
| `[TBS/EPVis]` | **T_end** |

`EBSHandler`는 `handle_*()` **반환 후** `dispatch_event(V2T_*)` 한다.  
`handle_*` = `run_on_main_thread(_work)` **동기 대기** → V2T도 **_work 완료까지** 지연.

---

## 3. 추정 (미확정)

### H1. 메인 스레드 장시간 블로킹 — T2V 직후 (유력)

웹 T2V → `run_on_main_thread(_work)` 큐 적재 → 메인 스레드가 **다른 작업에 묶여** `_work` 실행이 2~5분 지연.  
멈춤 중 **로그 없음** = `_apply_ep_port_layout` / EPVis **진입 전**.

*근거*: 로그 공백, Kit UI는 같은 메인 스레드인데 **T2V 없이**는 즉시 동작  
*미확인*: 메인 스레드가 무엇에 묶이는지 (스트림 resize vs dead lock)

### H2. T2V 직후 스트림 dynamic resize → 화면 확대·USD 스트레치 (유력)

`1855×501 → 500` resize 로그와 **전체화면 확대·USD 늘어남**이 동시.  
실무 웹은 **하단 1/2 = 가로 긴 영역** → 클라이언트가 Kit에 **wide 해상도**를 요청 → livestream **dynamic resize** 발생.

T2V 처리 시점에 resize가 **재트리거**되면 수 분간 인코더·Kit 창이 **확대·스트레치**된 것처럼 보일 수 있음.

*근거*: 실무 레이아웃(하단 half) + resize 로그 + 사용자 시각 관찰  
*미확인*: T2V 핸들러가 resize를 직접 호출하는지, 메시지와 resize의 인과

### H3. `run_on_main_thread` 동기 대기 + V2T 지연 (확인)

`fut.result(timeout=120)` — 메시징 스레드가 **_work 완료까지 block**.  
V2T는 `handle_*` 반환 후에만 나가므로 **웹 응답 = T_end** (4차 확인과 일치).

실측 5분+ 인 경우:
- `_work` 자체가 5분이거나  
- 메인 스레드가 **먼저** 다른 작업(resize)에 묶여 `_work` **시작이 늦어짐** (로그 공백과 양립)

*코드*: `kit_main_dispatch.py`, `tbs_sim_bridge.py` 모든 `handle_*`

### H4. ~~웹 연결 직후 일괄 멈춤~~ → **기각**

연결만으로는 멈추지 않음. **웹 T2V 클릭 시에만** 재현.

### H5. 로컬 UI는 빠른 이유

동일 `_apply_ep_port_layout` 이나 **이미 메인 스레드**에서 실행.  
T2V 경로만 **큐 + 동기 대기 + (추정) resize 부수 효과**.

### H6. 홀수 높이(501) 반복 resize 루프

*미확인*: 멈춤 5분 동안 resize 로그가 **한 번**인지 **반복**인지

---

## 4. 피드백 답변 기록

| # | 질문 | 답 |
|---|------|-----|
| Q1 | 멈춤 실제 시간 | **약 5분** 전후 (2~3분 ~ 5분 이상) |
| Q2 | 멈춤 중 Kit 로그 | **추가 로그 없음** |
| Q3 | 연결만 vs 웹 클릭 | 스트리밍·Kit UI **정상** / **웹 T2V만** 멈춤 |
| Q4 | streaming + 2분할 | **예** |
| Q5 | CPU/GPU | **미확인** (내일 실무에서 확인 예정) |
| Q7 | EBSHandler → EPVis | **2~3분 ~ 5분+** |
| Q8 | Kit 제어창 | **즉시** |
| Q9 | 웹 V2T 응답 시각 | **멈춤 해소 후(T_end)** 만 도착 |

### 내일 실무 확인 (Q5·Q10)

| 항목 | 목적 |
|------|------|
| CPU/GPU 점유 | resize 인코딩 부하 vs deadlock 구분 |
| resize 로그 **횟수** | 1회 vs 반복 루프 |
| 웹 콘솔 **2분 전후** `code=1` timeout 응답 유무 | `fut.result(120s)` 와 실측 정합 |

---

## 5. 타임라인 (웹 T2V — EP/EBS 공통 패턴)

```
T0        웹: 버튼/체크박스 → T2V 전송
T0        Kit: [EBSHandler] _on_req_*  ← 즉시, 마지막 로그
T0+ε      Kit 화면 전체화면처럼 확대, USD 스트레치, 스트림 정지
          (resize 로그 가능: 1855×501→500)
T0 ~ T_end  콘솔 **로그 0줄** (2~3분 ~ 5분+)
T_end     Kit: [TBS/EPVis] ... (hyview_*)
T_end     V2T_response → **웹 콘솔** (멈춤 해소와 동시)
T_end     동작 반영, 화면 크기 복구
```

**대비 — Kit 제어창 동일 조작**

```
T0   콤보/체크 변경
T0   [TBS/EPVis] 즉시
```

---

## 6. 원인 종합 (작업 가설 — 수정 전)

> **아직 단일 원인 확정은 아님.** 아래는 현재 증거에 가장 잘 맞는 **복합 가설**이다.

### 6.1 왜 Kit 제어창은 빠르고 웹만 느린가

| | Kit 제어창 | 웹 T2V |
|--|-----------|--------|
| 스레드 | **메인(UI) 스레드**에서 즉시 | livestream **메시징 스레드** → `run_on_main_thread` **동기 대기** |
| 동일 로직 | `on_sim_ep_count_changed` 등 | `_work` 안에서 **동일 함수** 호출 |
| 스트림 resize | 없음(추정) | T2V 직후 **dynamic resize**·화면 확대 관찰 |
| V2T | 해당 없음 | `_work` 끝날 때까지 **응답 불가** |

**결론**: 느린 것은 EPVis 알고리즘 자체만이 아니라, **웹 경로의 동기 마샬링 + (추정) 메인 스레드 장시간 점유(resize/렌더)** 조합.

### 6.2 멈춤 중 로그가 없는 이유 (가설)

1. **메인 스레드가 `_work` 실행 전** livestream resize 등 **무로그 블로킹**에 묶임 → update pump·EPVis 미진입  
2. 또는 `_work` 실행 중에도 **EPVis 로그 직전**까지 무거운 USD/렌더만 수행 (가능성 낮음 — 로컬은 즉시이므로)

### 6.3 화면 확대·USD 스트레치 (가설)

- 실무 웹 **하단 1/2 wide** → 스트림 해상도 **1855×500** 급  
- T2V 시 Kit 창/스트림이 **일시적으로 다른 해상도**로 재협상 → 전체화면처럼 보이고 USD aspect 깨짐  
- 완료 후 **하단 pane 크기로 복구**

### 6.4 2분할 Viewport와의 관계 (가설)

Kit **2분할 Widget** + streaming + **낮은 스트림 높이** → 타일별 RenderProduct·workspace 리사이즈 비용이 **단일 뷰보다 큼**.  
T2V가 viewport/EBS 레이아웃을 건드릴 때 **스트림 인코더 전체 재설정**이 겹칠 수 있음.

### 6.5 “적용 전에 멈춘다”는 관찰과 코드 정합

사용자 관찰: `[EBSHandler]` 직후 **EPVis 전** 화면 확대·정지 → **실제 EP/EBS 반영(`_work`) 전** 메인 스레드가 다른 일에 묶였을 가능성.

```
T0     EBSHandler (메시징 스레드) — [EBSHandler] 로그
T0     handle_eqp_change → run_on_main_thread(_work) 큐 적재 + fut.result() 대기 시작
       │
       ├─ (A) 메인 스레드: livestream dynamic resize / 창 크기 변경  ← 로그 공백·화면 확대
       │         update pump가 _work 실행 못 함
       │
       └─ (B) 이후 메인 스레드: _work → on_sim_ep_count_changed → EPVis 로그
```

**Kit 제어창**은 (A) 없이 (B)만 즉시 실행.

---

## 7. Kit 앱에서 우리가 수정할 수 있는 항목 (상세)

> 웹 코드 없이 **본 레포 Kit 확장**만으로 손댈 수 있는 범위.  
> NVIDIA `omni.kit.livestream.*` **내부 C++** 는 직접 수정 불가 → **설정·훅·우리 bridge** 로 우회.

### 7.1 스트리밍 해상도 — dynamic resize 끄기 ✅ 적용

**증상과 연결**: 로그 `processing dynamic resize ... 1855x501` = 클라이언트가 연결 후 **다른 해상도**를 요청하고 Kit가 **재인코딩** 중.

**NVIDIA 공식 설정** ([omni.kit.livestream.app Overview](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.livestream.app/latest/Overview.html)):

```toml
# morph.editor_streaming.kit [settings] 에 추가
exts."omni.kit.livestream.app".primaryStream.allowDynamicResize = false
```

또는 런타임:

```python
import carb.settings
carb.settings.get_settings().set(
    "/exts/omni.kit.livestream.app/primaryStream/allowDynamicResize", False
)
```

| 값 | 동작 |
|----|------|
| `true` (기본일 수 있음) | 클라이언트·창 크기 변경 시 **dynamic resize** — 성능 저하 경고 있음 |
| **`false`** | 연결 시 해상도 **고정** — 이후 클라이언트 요청 **무시**(추정) |

**기대**: T2V·창 변동 시 **수 분 resize 멈춤** 완화.  
**검증**: 동일 조작 후 `dynamic resize` 로그 **미출력** 여부.

**적용**:
- `source/apps/morph.editor_streaming.kit` — `[settings.exts."omni.kit.livestream.app".primaryStream] allowDynamicResize = false`
- `hyview_stream.py` — 진단 로그 + layout lock + resize 훅 (단일 모듈)
- SSOT: `sim_control_defaults.STREAMING_ALLOW_DYNAMIC_RESIZE` (기본 `False`)

**참고**: NVIDIA 포럼 — windowed Kit + `fillViewport`/`fill_frame` 이 resize를 유발; **`--no-window`** 가 권장 패턴이나 실무는 Kit 창 + 웹 동시 사용.

---

### 7.2 `fill_frame` / 뷰포트 해상도 동기화 억제 ✅ 적용

**문제**: `fill_frame=True` 이면 Viewport 렌더 해상도가 **창 크기에 따라 변함** → livestream이 **프레임 해상도 불일치** → dynamic resize 또는 경고.

**관련 우리 코드**:

| 파일 | 함수 | 동작 |
|------|------|------|
| `sim_multi_view.py` | `set_viewport_fill_frame_for_split_count(sn, True)` | native Viewport API `fill_frame` |
| `extension.py` | `_deferred_apply_streaming_viewport_polish` | streaming 시작 후 **fill_frame=True** |
| `hyview_stream.py` | `_on_app_window_resize` | **창 resize마다** fill_frame=True 재적용 (lock 시 skip) |
| `sim_multi_view_widget.py` | `sync_split_widget_fill_frame` | 2분할 타일 `resolution` 을 Workspace 크기에 맞춤 |

**수정 방향 (streaming + HyView 연결 중)**:

1. `allowDynamicResize=false` 와 함께 **`fill_frame=False`** + **고정 `resolution`** (연결 시 1회만 설정)  
2. `hyview_stream._on_app_window_resize` — HyView 세션 중 **no-op**  
3. Widget 2분할: `sync_split_widget_fill_frame` — streaming 중 **타일 resolution 갱신 스킵**

```python
# 의사 코드 — hyview_stream.py
def _on_app_window_resize(ext, _event):
    if bool(getattr(ext, "_hyview_stream_lock_layout", False)):
        return  # T2V·스트리밍 중 resize 연쇄 차단
    ...
```

**기대**: EP 변경이 간접적으로 창/타일 리사이즈를 타도 **스트림 재협상 없음**.

**적용**:
- `hyview_stream.py` — `enable_hyview_stream_layout_lock(ext)` → `ext._hyview_stream_lock_layout=True`
- layout lock 은 polish 완료 후 켜짐
- `hyview_stream._on_app_window_resize` — lock 시 no-op + `[HyView/stream] window_resize SKIP`
- `set_viewport_fill_frame_for_split_count` — lock 시 skip + `[HyView/stream] fill_frame SKIP`
- `sync_split_widget_fill_frame` — lock 시 skip + `[HyView/stream] sync_split_widget_fill_frame SKIP`
- SSOT: `sim_control_defaults.HYVIEW_STREAM_LOCK_LAYOUT` (기본 `True`)

---

### 7.3 스레드 / `run_on_main_thread` ✅ 적용 (비동기 bridge)

**이전 구조** (`kit_main_dispatch.py`):

```python
# 메시징 스레드에서 호출
run_on_main_thread(_work)  # 큐에 넣고
return fut.result(timeout=120.0)  # ← 완료까지 BLOCK (최대 2분)
```

**문제점**:

| 이슈 | 설명 |
|------|------|
| **동기 대기** | 메시징 스레드 block → V2T가 `_work` 끝날 때까지 불가 (확인됨) |
| **메인 큐 지연** | 메인 스레드가 resize 등으로 바쁘면 `_work` **시작 자체가 늦음** → EPVis 로그 공백 |
| **timeout 120s vs 실측 5분** | 120s timeout 후 예외 가능하나 `_work`는 큐에 남아 **나중에** 실행 → 5분 뒤 EPVis |
| **한 프레임에 전부** | `_pump_main_queue`가 while로 큐 drain — 긴 `_work` 하나가 **한 update 틱 점유** |

**수정 방향**:

| # | 안 | 파일 |
|---|-----|------|
| 1 | **`schedule_on_main_thread(fn, on_done)`** — `fut.result()` 제거 | `kit_main_dispatch.py` 신규 |
| 2 | `handle_eqp_change` 등 **비동기 + 완료 시 V2T** | `tbs_sim_bridge.py`, `ebs_handler.py` |
| 3 | T2V 수신 시 **`[HyView/bridge] queued`** / **`work start`** / **`work done`** 타임스탬프 | 계측 (원인 확정용) |
| 4 | timeout 시 **명시 로그** + V2T `code=1` (웹 2분 응답 여부와 대조) | `tbs_sim_bridge.py` |

**기대**: (1)은 메시징 block 해소. **화면 멈춤**은 (7.1·7.2)와 함께 봐야 함.

**적용**:
- `kit_main_dispatch.schedule_on_main_thread(fn, on_done=..., on_error=...)` — `fut.result()` 없음
- `tbs_sim_bridge.handle_eqp_change` / `handle_ebs_enable` / `handle_control_simulation` — `dispatch` 콜백으로 V2T
- `handle_start_simulation` — `_begin` 도 `schedule_on_main_thread` (프리런 대기는 기존 async 유지)
- `ebs_handler.py` — bridge 완료 콜백에서 `dispatch_event`
- `hyview_stream.py` — 진단 로그 + layout lock + resize 훅 (아래 §7.8)

---

### 7.4 HyView bridge 경로 — EP/EBS 동작 자체 (우리 코드)

T2V 시 실제 호출 (`tbs_sim_bridge.py` → `control_window.py`):

```
_apply_ep_count_for_case → on_sim_ep_count_changed → on_sim_ep_count_combo_changed
_apply_ep_port_layout_for_sim_screen(reason="hyview_eqp_change")
```

**Kit 제어창과 동일 로직** — 여기를 **느리게 만드는 것은 로직 복잡도가 아니라 (7.1~7.3)**.

보조 완화:

- `_work` 안에서 **UI 위젯 동기화 최소화** (웹 경로에만 `skip_ui_sync=True` 등) — 효과는 제한적  
- 화면1은 이미 `schedule_apply_ep_port_layout(delay_frames=2)` — 화면2는 `apply_ep_port_layout_for_context` **동기**

---

### 7.5 streaming 전용 훅 (이미 있음 — 확장 가능)

| 파일 | 현재 | 확장안 |
|------|------|--------|
| `hyview_stream.py` | 진단·layout lock·창 resize → fill_frame | streaming 중 **가드** |
| `extension.py` | `install_streaming_window_resize_hooks` | HyView 연결 시 `ext._hyview_stream_lock_layout=True` |
| `kit_chrome_visibility.py` | `is_streaming_deployment()` | streaming 여부 SSOT |

`morph.editor_streaming.kit` 고정 해상도:

```toml
renderer.resolution.width = 1920
renderer.resolution.height = 1080
window.width = 1920
window.height = 1080
```

→ 실무 웹 하단 pane(1855×500)과 **불일치** — `allowDynamicResize=false` 시 **연결 시점 해상도**와 웹 요청을 **맞추는 것**이 중요 (웹도 수정 필요, Kit만으로는 연결 해상도 고정만 가능).

---

### 7.6 Kit만 vs 웹 협업

| 조치 | Kit만 | 웹 협업 필요 |
|------|-------|-------------|
| `allowDynamicResize=false` | ✓ `.kit` 설정 | 연결 해상도를 웹 pane에 맞게 **최초 1회** 맞추기 |
| `fill_frame` / resize 훅 가드 | ✓ | — |
| `run_on_main_thread` 비동기 | ✓ | V2T “처리 중” UX |
| 짝수 높이·32 width align | — | ✓ AppStream `width`/`height` |

---

### 7.7 권장 적용 순서 (내일 실무)

| 순서 | 조치 | 리스크 | 기대 |
|------|------|--------|------|
| **1** | `allowDynamicResize=false` in `.kit` | 연결 해상도 고정 — 웹 pane과 안 맞으면 크롭/레터박스 | resize 로그·멈춤 **급감** 가능 |
| **2** | bridge **타임스탬프 로그** 3줄 (queued/start/done) | 없음 | EPVis 전 **어느 구간**이 긴지 확정 |
| **3** | `run_on_main_thread` **비동기화** | V2T 타이밍 변경 — 웹 “처리 중” 필요 | V2T 즉시 ack 가능 |
| **4** | streaming 중 `fill_frame` / resize 훅 **가드** | streaming 창 크기 고정 | 2분할+스트림 안정 |
| **5** | (웹) pane 크기로 connect resolution | — | 501 홀수 제거 |

---

### 7.8 적용 내역 요약 (2026-07-06 코드 반영)

| # | 조치 | 파일 |
|---|------|------|
| 1 | `allowDynamicResize=false` | `morph.editor_streaming.kit`, `hyview_stream.py`, `extension.py` |
| 2 | bridge 타임스탬프·watchdog 로그 | `hyview_stream.py`, `tbs_sim_bridge.py` |
| 3 | `schedule_on_main_thread` + 비동기 bridge | `kit_main_dispatch.py`, `tbs_sim_bridge.py`, `ebs_handler.py` |
| 4 | streaming layout lock + resize/fill_frame 가드 | `hyview_stream.py`, `sim_multi_view.py`, `sim_multi_view_widget.py` |
| 5 | SSOT 플래그 | `sim_control_defaults.py` |

#### 실무에서 찾을 로그 태그

| 태그 | 의미 | 정상 패턴 (EP 변경 예) |
|------|------|------------------------|
| `[EBSHandler] _on_req_*` | T2V 수신 | 즉시 1줄 |
| `[HyView/bridge] eqp_change-N queued` | bridge 큐 적재 | EBSHandler 직후 |
| `[HyView/bridge] eqp_change-N work_start` | 메인 스레드 `_work` 시작 | queued 직후 ~ 수 초 이내 |
| `[HyView/bridge] eqp_change-N work_done dt_sec=...` | `_work` 완료 | work_start 직후 ~ 수 초 이내 |
| `[TBS/EPVis] ... (hyview_eqp_change)` | EPVis 반영 | work_done 직후 |
| `[HyView/bridge] eqp_change-N watchdog ...` | 120s 내 start/done 없음 | **있으면** 메인 스레드 block 의심 |
| `[HyView/stream] allowDynamicResize=false applied` | streaming 시작 | Kit startup 1회 |
| `[HyView/stream] layout_lock=ON` | polish 후 lock | startup 후 수 초 |
| `[HyView/stream] * SKIP reason=layout_locked` | resize/fill_frame 억제 | T2V 중 반복 가능 |

#### SSOT 플래그 (`sim_control_defaults.py`)

| 플래그 | 기본 | 설명 |
|--------|------|------|
| `HYVIEW_BRIDGE_DIAG_ENABLED` | `True` | `[HyView/bridge]` / `[HyView/stream]` 로그 |
| `HYVIEW_BRIDGE_WATCHDOG_SEC` | `120.0` | queued→work_start / work_start→work_done watchdog |
| `HYVIEW_STREAM_LOCK_LAYOUT` | `True` | resize·fill_frame 가드 |
| `STREAMING_ALLOW_DYNAMIC_RESIZE` | `False` | `True` 로 두면 allowDynamicResize 끄기 생략 |

---

## 8. 수정 방안 요약 (기존 §7 — 우선순위)

> 코드 수정은 **내일 CPU/GPU·resize 횟수 확인 후** P0부터 적용 권장.  
> 아래는 **방향성**이며, 일부는 계측 추가만으로도 검증 가능.

### P0 — Kit: T2V 처리 **비동기화** (최우선, 효과 기대 큼)

**문제**: `tbs_sim_bridge.handle_*` 가 `run_on_main_thread` + `fut.result()` 로 **완료까지 block** → V2T·UI 모두 T_end까지 정지.

**안**:
```python
# 의사 코드 — 동기 대기 제거
def handle_eqp_change_async(payload, on_done):
    def _work():
        ...  # 기존 _work
        on_done(result)
    schedule_on_main_thread(_work)  # result() 호출 안 함
    return None  # EBSHandler는 즉시 return 하지 말고 on_done에서 V2T dispatch
```

- `EBSHandler`: `handle_*` **반환 즉시 V2T 보내지 말고**, `_work` 완료 콜백에서 `dispatch_event`  
- 웹: 로딩 표시 + timeout UX (처리 중 상태)

**기대**: 메시징 스레드·livestream **block 해소** (멈춤 일부 완화).  
**한계**: 메인 스레드 resize가 여전히 길면 **시각적 멈춤**은 남을 수 있음 → P1과 병행.

**수정 파일**: `tbs_sim_bridge.py`, `ebs_handler.py` (모든 `handle_*`)

---

### P0 — 웹: 스트림 연결 해상도 **컨테이너에 맞추기**

**문제**: 하단 1/2 pane → **홀수 높이(501)** → livestream dynamic resize → Kit 부하.

**안** (실무 웹, AppStream 연결부):
```typescript
const el = document.getElementById(VIDEO_ID);
const w = el.clientWidth;
const h = Math.floor(el.clientHeight / 2) * 2;  // 짝수 높이 강제
// width도 32 정렬: Math.floor(w / 32) * 32
directConfig.width = wAligned;
directConfig.height = h;
```

- `ResizeObserver`로 **연결 후 resize 폭주** 방지 — 디바운스 후에만 재협상  
- 레포 `hyview_client/AppStream.tsx` 의 고정 `1920×1080` 도 동일 원칙 적용 권장

**기대**: 연결·T2V 시 **resize 횟수·홀수 보정** 감소.

---

### P1 — Kit: T2V 직후 **스트림 resize 유발 요인** 분리

**조사 후 수정**:
- EP/EBS 변경이 `ui.Workspace` / window size / `fill_frame` / 2분할 tile resolution 을 건드리는지 추적  
- **불필요한 창 크기 변경**이 있으면 웹 스트리밍 모드에서는 **스킵**

**안**: streaming 연결 플래그 `ext._hyview_stream_connected` 시 viewport 레이아웃 동기화 **최소화**.

---

### P1 — Kit: EPVis를 **프레임 분할** (로컬은 빠르지만 웹 부하 완화 보조)

`_work` 안에서 `on_sim_ep_count_changed` + 대량 USD visibility를 **한 프레임에 몰지 않기**  
→ `schedule_apply_ep_port_layout` 처럼 **N프레임에 나눠 적용** (이미 화면1은 `delay_frames=2` 사용 중).

---

### P2 — Kit: `run_on_main_thread` timeout·계측

- `timeout=120` vs 실측 5분 — **timeout 예외 로그** 추가 (`[HyView/bridge] main_thread timeout`)  
- `_work` 진입/종료 타임스탬프: `[HyView/bridge] eqp_change work start/done`  
- 내일 CPU/GPU와 함께 **어느 구간이 긴지** 확정

---

### P2 — livestream Kit 설정

- **`allowDynamicResize = false`** — §7.1 (가장 먼저 시도)
- `morph.editor_streaming.kit`: 연결 해상도와 웹 pane 정합

---

### 수정하지 말 것 (당분간)

- EPVis hide/show **로직 자체** 대폭 변경 — 로컬에서는 즉시이므로 알고리즘 단독 원인 아님  
- 2분할 Viewport 구조 변경 — 웹 이슈와 **직교**할 수 있으나 비용 큼

---

## 9. 내일 실무 체크리스트 (효과 검증)

| # | 확인 | 기대 결과 |
|---|------|-----------|
| 1 | T2V 클릭 직후 **GPU** 사용률 | 90%+ 이면 resize/인코딩 부하 (H2 강화) |
| 2 | T2V 클릭 직후 **CPU** | 100% 한 코어 고정이면 메인 스레드 block (H1) |
| 3 | Kit 로그 **`[HyView/bridge]`** 3단계 | `queued` → `work_start` → `work_done` 간격이 **수 초 이내**인지 |
| 4 | `work_start` 없이 **watchdog** 120s | 있으면 메인 스레드가 `_work` 실행 전 block |
| 5 | Kit 로그 **resize 문구 횟수** | `dynamic resize` **0회** 또는 대폭 감소 |
| 6 | **`[HyView/stream] * SKIP`** | T2V 중 layout lock 가드 동작 확인 |
| 7 | 웹 V2T 도착 시점 | `work_done` · `[TBS/EPVis]` · V2T 가 **같은 시점**에 가까운지 |
| 8 | 멈춤 **총 시간** | 패치 전 2~5분+ → **수 초~수십 초**로 단축되는지 |

---

## 10. 아직 미적용 / 추가 조사

- **웹** pane 짝수 높이·32 width align (`AppStream` connect resolution) — 실무 웹 코드
- EPVis **프레임 분할** (로컬은 빠르지만 웹 부하 완화 보조) — 효과 확인 후
- 원인 **단일 항목**으로 단정 — 패치 후 로그로 재평가

---

## 11. 관련 기존 문서

| 문서 | 내용 |
|------|------|
| `tbs_control_2_web_prerun_settings_spec_ko.md` | T2V/V2T API 스펙 |
| `morph.hyview_messaging/web/hyview_client/README.md` | 로컬 스트림 테스트 |
| `morph.tbs_control_2/TBS_Web_API_Flow_Guide.md` | 웹 필드 ↔ Kit 매핑 |

---

## 12. 변경 이력

| 일자 | 내용 |
|------|------|
| 2026-07-06 | 초기 실무 현상 정리 |
| 2026-07-06 | 2~3차: 레이아웃, T2V만 멈춤, 로그 공백 |
| 2026-07-06 | 5차: §7 Kit 수정 가능 항목(allowDynamicResize, fill_frame, 스레드, 적용 순서) |
| 2026-07-06 | 6차: §7.1~7.3·§7.8 **코드 반영** — allowDynamicResize, layout lock, async bridge, 진단 로그 |

---

## 13. 다음 단계

1. **실무** §9 체크리스트로 패치 효과·로그 구간 확정  
2. `watchdog` / `work_start` 지연 시 **메인 스레드 block** 원인 추가 추적  
3. 여전히 느리면 **웹 connect resolution** (§7.6 #5) 협업  
4. 필요 시 EPVis 프레임 분할 (§8 P1)
