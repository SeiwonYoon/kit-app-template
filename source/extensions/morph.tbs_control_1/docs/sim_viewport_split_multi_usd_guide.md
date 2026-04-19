## 분할화면(멀티 Viewport) 구성 가이드 (TBS / 우리 코드 기준)

이 문서는 `morph.tbs_control_1` 확장에서 **2~4 분할 화면을 만들고**, 각 화면(타일)마다 **서로 다른 USD를 로드**하는 방식을
“문서만 보고 따라할 수 있게” 정리한 가이드입니다.

대상 코드:
- 분할 3D/컨텍스트/스테이지: `morph/tbs_control_1/sim_multi_view.py`
- 제어창(UI)·분할 UI 연결: `morph/tbs_control_1/control_window.py`

---

## 0) 용어 정리

- **화면/타일(screen)**: 분할된 3D 뷰 1개. 화면 번호는 1~4.
- **메인 화면(화면1)**: Kit 기본 `Viewport` 창을 사용. 기본 `omni.usd` 컨텍스트의 Stage.
- **보조 화면(화면2~4)**: 화면마다 별도 Workspace 창 + 별도 Hydra viewport + **이름 있는 USD 컨텍스트**를 생성.
- **USD 컨텍스트(omni.usd context)**: stage를 담는 컨테이너. 컨텍스트가 다르면 stage가 분리될 수 있음.

---

## 1) 분할화면이 어떤 구조로 만들어지는가(핵심 동작)

우리 코드 기준 기본 정책:
- **화면1**: 기본 `Viewport` + 기본 컨텍스트 stage(“TBS Load 로 연 화면”)
- **화면2~4**: 각 화면마다
  - Workspace 보조 창(`TBS_SimSplit_1`, `TBS_SimSplit_2`, …)
  - 별도 Hydra 뷰(독립 3D)
  - 이름 있는 `omni.usd` 컨텍스트(독립 stage)
  를 만들고, **해당 컨텍스트에 stage를 open**한다.

핵심 엔트리포인트:
- `sim_multi_view.apply_sim_viewport_split_layout(ext, split_n)`

---

## 2) 사용자가 UI에서 분할화면을 만드는 절차(따라하기)

### 2.1 전제 조건(안 되면 여기부터 확인)

- **USD stage가 먼저 로드되어 있어야 함**
  - 제어창에서 “시뮬 화면(USD 로드 시)” 분할 행은
    `control_window._sync_sim_multi_split_row_visibility()`가 조건을 만족할 때만 표시된다.
  - 조건 미충족 시 분할은 1화면으로 강제 롤백될 수 있다(`control_window._force_sim_split_to_default`).

### 2.2 분할 적용(2/3/4)

1) USD를 먼저 로드한다(기본 Viewport에 stage가 열린 상태).

2) 제어창의 “시뮬 화면(USD 로드 시)” 체크박스에서 `2화면/3화면/4화면` 중 하나를 선택한다.

3) UI 이벤트 흐름:
- 체크박스 변경 → `control_window._on_sim_split_choice_changed(ext, idx, model)` 호출
- 내부에서 `sim_multi_view.apply_sim_viewport_split_layout(ext, idx)` 실행

4) 성공 시 “실제 적용된 분할 수”는 `ext._sim_viewport_split_count`로 저장되며,
   UI 체크박스는 이 값에 맞춰 동기화된다.

### 2.3 분할 해제(1화면 복귀)

- 체크박스를 1화면으로 되돌리면,
  `sim_multi_view` 쪽 teardown 경로가 실행되어 보조 창/보조 viewport/컨텍스트가 정리되고,
  기본 Viewport 레이아웃을 복원한다.

---

## 3) 분할 수/상태가 코드에서 어떻게 동기화되는가(운영 중 혼선 방지)

단일 소스:
- `ext._sim_viewport_split_count`
  - “원하는 값”이 아니라 **실제로 적용에 성공한 값**만 담는다.
  - 실패/롤백 시 1로 되돌리고 UI도 그에 맞게 맞춘다.

관련 함수:
- `control_window._sync_sim_split_checkboxes_from_ext_count(ext)`:
  - `ext._sim_viewport_split_count` ↔ 체크박스 모델 동기화
- `sim_multi_view.notify_sim_split_ui_sync(ext)`:
  - 분할 모듈에서 UI 동기화가 필요할 때 호출(체크박스 맞추기)

---

## 4) “각 화면마다 다른 USD 로드”를 우리 코드에 적용하는 방법

### 4.1 현재 기본 동작(왜 ‘독립 stage’가 가능한가)

`sim_multi_view.py`는 보조 화면(2~4)에 대해:
- 이름 있는 USD 컨텍스트를 만들고(`_named_usd_context`)
- 그 컨텍스트에 stage를 open한다(`_ctx_open_stage_path`, `_open_aux_stage_with_unique_session`)

또한, “보조 타일 스테이지 분리”를 위해 기본적으로:
- 같은 원본을 그대로 sublayer로 공유하지 않도록
- 원본 USD를 임시 경로로 복제(`omni.client.copy_async`) 후
- **각 타일이 서로 다른 파일**을 open하도록 설계되어 있다.

관련 환경변수:
- `TBS_MULTI_SPLIT_FILE_CLONE=0`:
  - 파일 복제를 끄고 래퍼만 쓰는 모드(레이어 공유 위험이 커질 수 있음)
- `TBS_MULTI_SPLIT_SESSION_LAYER=0`:
  - session layer 사용을 끄는 모드

### 4.2 원하는 동작 정의(요구사항)

원하는 목표는 아래처럼 “화면별로 서로 다른 USD”를 여는 것:
- 화면1: `main.usd`
- 화면2: `aux_A.usd`
- 화면3: `aux_B.usd`
- 화면4: `aux_C.usd`

즉, “보조 화면이 메인 USD의 복제본을 여는 정책” 대신,
**화면별 USD 경로를 입력으로 받아 open_stage를 분기**해야 한다.

### 4.3 적용 포인트(코드 레벨 체크리스트)

다음 3가지만 확보되면 적용 가능하다.

#### (1) 화면번호 → 컨텍스트 이름 매핑

- 화면별 컨텍스트 이름은 `control_window._usd_context_name_for_sim_screen(ext, screen)`를 사용한다.
  - `sim_multi_view`가 만든 이름(`ext._sim_multi_context_names`)을 우선 사용
  - 없으면 폴백 `morph_tbs_split_aux_{screen-1}`

#### (2) “화면별 USD 경로”를 저장할 위치(정책)

문서 기준 추천(예시):
- `ext._sim_multi_usd_path_by_screen: Dict[str, str]`
  - 키: `"1"|"2"|"3"|"4"`
  - 값: 열고 싶은 usd 경로(로컬 경로/omniverse URL 등)

이 문서는 코드 변경을 포함하지 않지만, 적용 시 위 형태로 데이터를 준비해두면 가장 단순하다.

#### (3) 보조 화면 stage 오픈을 “화면별 경로”로 분기

실제 open이 일어나는 곳은 `sim_multi_view.py` 내부의:
- `_ctx_open_stage_path(...)`
- `_open_aux_stage_with_unique_session(...)`

따라서 구현 전략은 아래 둘 중 하나를 선택한다.

**전략 A (권장): 분할 생성 시점에 screen별 경로로 open**
- `apply_sim_viewport_split_layout`의 보조 타일 빌드 과정에서
  - “이 화면에 어떤 USD를 열지”를 screen 기준으로 결정
  - 지정된 경로가 있으면 그걸 열고
  - 없으면 기존 정책(메인 USD 복제/래핑)으로 폴백

**전략 B: 분할 후 ‘화면별 다시 로드’ 기능 제공**
- 분할이 만들어진 다음, 각 screen의 컨텍스트를 찾아
  - `omni.usd.get_context(context_name).open_stage(path)`
  처럼 “해당 컨텍스트에만” stage를 로드

전략 B는 UI/운영 측면에서 직관적이지만, “언제 rebind/카메라/레이아웃이 안정화되는지” 타이밍 관리가 필요할 수 있다.

---

## 5) 분할 화면에 애니/시뮬 변경이 ‘해당 화면 USD’에 적용되게 하는 조건

우리 코드는 이미 화면별로 애니 적용이 분리되도록 설계되어 있다.

핵심 조건:
- 이벤트 payload에 `tbs_sim_screen="1".."N"`이 들어가야 함
- 그 screen을 기준으로 `usd_context_name`을 선택해야 함

적용 흐름(개요):
- (엔진 → UI 큐) 이벤트 payload에 `tbs_sim_screen`이 병합됨
- `control_window._sim_ui_sink_anim_event`가 screen을 읽고
- `handle_sim_event_for_animation` → `SequenceRunner.run(..., usd_context_name=해당 screen 컨텍스트)`

따라서 “화면별로 다른 USD”를 로드해도, 위 `usd_context_name` 라우팅이 유지되면
MOVE/ROTATE/USD_TIMELINE 등이 해당 화면의 stage에 정상 적용된다.

---

## 6) 장애/회피 옵션(운영 체크)

보조 3D 뷰 생성이 불안정하거나 GPU 크래시가 있는 환경에서:
- `TBS_SIM_VIEWPORT_SPLIT_3D=0`:
  - 보조 Hydra 3D 뷰 생성 자체를 끔(안정성 우선)
- `TBS_SIM_VIEWPORT_SPLIT_DOCK=0`:
  - Dock 시도 없이 격자 폴백(레이아웃 안정성 우선)

레이어 공유/편집 전파가 문제면:
- 기본값(파일 복제 + session layer)을 유지하는 것을 권장
- 대용량 파일로 복제가 부담되면 `TBS_MULTI_SPLIT_FILE_CLONE=0`을 검토하되,
  레이어 공유로 인한 부작용 가능성을 함께 고려해야 한다.

---

## 7) 제어창 시뮼 모니터(이력 로그·EP 막대) 분할 시 (2026-04 정리)

**이력 로그(화면별 SIM 이력 패널)**

- 멀티 엔진 실행 시 각 엔진 `on_log`는 `[화면1] `, `[화면2] ` 형태로 한 줄을 보낸다.
- UI `_append_sim_log`는 **`_format_history_line` 호출 전**에 원문에서만 `[화면N]` 접두를 파싱해,
  해당 채널의 `history_label`에만 붙인다.
- 이유: `_format_history_line`이 줄 앞에 이모지(가독성)를 붙이면, 포맷 **후** 문자열에서는 `^\[화면…` 매칭이 깨져
  전 로그가 화면1에만 쌓이는 회귀가 생길 수 있다.

**EP 타임라인 막대(포트 상태 바로 아래)**

- 분할 수(`ext._sim_viewport_split_count`)에 따라 `control_window._ep_occ_timeline_layout_dims`가
  막대 폭·행 라벨 폭·프레임 패딩 등을 줄인다.
- 열 폭이 매우 좁을 수 있어 `ep_timeline_host`(ScrollingFrame)는 분할 시 **가로 스크롤**을 허용한다
  (`_ep_timeline_host_horizontal_scroll_policy`: 환경에 따라 AS_NEEDED / AUTO / ALWAYS_ON 순 선택).

**EP 막대 시간 축**

- 엔진 `timeline_only`의 `sim_time`은 **`env.now`**(진행현황 `t(sim)`과 동일 시계). 상세는 `ep_timeline_progress_postmortem.md`의 “현행 정책 보정” 절 참고.
