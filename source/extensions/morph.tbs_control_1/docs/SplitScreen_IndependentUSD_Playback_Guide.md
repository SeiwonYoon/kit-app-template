# 분할화면(2~4)에서 USD를 “독립 스테이지로 복제”해 시뮬/애니를 재생하는 구조 (이야기 형식 가이드)

대상 독자: **웹 개발자(파이썬/Kit 처음)** + “왜 분할=여러 USD 컨텍스트인지”를 코드로 따라가고 싶은 사람
목표: 이 문서만 보면서 코드를 따라가면, 현재 구조가 **어떤 순서로** 동작하는지(데이터가 어디에 저장되고 어디로 전달되는지) 전체 맥락을 잡을 수 있게 한다.

관련 문서(비교):
- `SplitScreen_Comparison_SingleStageVsMultiStage.md` (단일 Stage 4분할 vs 멀티 Stage 비교)

---

## 0) 한 줄 요약 (진짜 핵심)

이 프로젝트의 “분할화면(2~4)”은 단순히 화면만 쪼개는 게 아니라,

- **화면 1**: 기본 `omni.usd` 컨텍스트(Kit 기본 Viewport가 보는 Stage)
- **화면 2~4**: `morph_tbs_split_aux_1..3` 같은 **이름 있는 USD 컨텍스트**를 만들고,
  그 컨텍스트에 **원본 USD를 복제(또는 래퍼+session)** 해서 **서로 다른 Stage**로 열고,
  각 컨텍스트에 연결된 **보조 Viewport(타일)** 를 생성하는 방식이다.

그래서 “화면2부터는 완전히 독립 Stage”이고, 애니메이션도 반드시 그 화면의 `usd_context_name`으로 실행해야 한다.

---

## 1) 주인공 소개 (등장인물)

이 구조에서 핵심 파일은 2개다.

- **`sim_multi_view.py`**: “3D 화면 분할” 담당
  - 보조 USD 컨텍스트 생성
  - 원본 USD 복제/래퍼 생성
  - 보조 컨텍스트에 Stage 열기
  - 보조 Viewport(Workspace 창) 생성/도킹/레이아웃
  - 결과로 `ext._sim_multi_context_names` 같은 “화면별 컨텍스트 목록”을 저장

- **`control_window.py`**: “시뮬 이벤트 → 애니 실행” 담당
  - 화면 번호(1~4)를 입력으로 받아, 어떤 `usd_context_name`을 써야 하는지 계산
  - `SequenceRunner.run(..., usd_context_name=...)`로 **해당 화면의 Stage**에 애니를 적용

---

## 2) 이야기 시작: 사용자가 “2분할/3분할/4분할”을 체크한다

### 2-1) 제어창에서 분할 적용을 요청한다

제어창 UI에서 분할 체크박스를 바꾸면 `sim_multi_view.apply_sim_viewport_split_layout(ext, idx)`가 호출된다.

핵심은 “분할 수”를 `ext._sim_viewport_split_count`로 들고 있고, 이 값을 기준으로 시뮬/모니터 UI도 화면 수를 결정한다는 점이다.

여기서도 이름이 직관적이지 않은 것들을 짧게 번역하면:

- **`ext`**: “이 확장의 상태 저장소(전역 객체)”
  - JS로 치면 `appState`나 `store` 같은 느낌이다.
  - 분할 결과(컨텍스트 이름 목록), 화면별 설정 스냅샷, 실행 중 상태 등이 `ext.*`에 저장된다.

- **`idx` / `split_n` / `n`**: “분할 개수(1~4)”
  - 화면을 몇 칸으로 나눌지에 대한 숫자다.

---

## 3) 분할 빌드의 핵심: 화면 2~4를 위해 “USD 컨텍스트 + 독립 Stage + 보조 Viewport”를 만든다

### 3-1) 보조 컨텍스트 이름은 이렇게 정해진다

`sim_multi_view._build_multi_split_async()`에서 타일 인덱스 `ti`(1..n-1)에 대해 컨텍스트 이름을 만든다.

```1476:1484:source/extensions/morph.tbs_control_1/morph/tbs_control_1/sim_multi_view.py
for ti in range(1, n):
    ctx_name = f"morph_tbs_split_aux_{ti}"
    ctx = _named_usd_context(ctx_name)
```

즉:

- 화면2(보조1) → `morph_tbs_split_aux_1`
- 화면3(보조2) → `morph_tbs_split_aux_2`
- 화면4(보조3) → `morph_tbs_split_aux_3`

여기서 `ti`는 “보조 타일 번호”다.

- **`ti=1`**: 화면2(첫 보조)
- **`ti=2`**: 화면3(둘째 보조)
- **`ti=3`**: 화면4(셋째 보조)

---

### 3-2) “독립 Stage”를 만드는 핵심: 원본 USD를 타일별로 복제해서 연다

왜 복제를 하냐?

USD는 “같은 파일”을 여러 컨텍스트에서 그냥 같이 열면, Kit 내부에서 레이어/캐시가 공유되면서
**한쪽의 변화가 다른쪽에도 보이는** 문제가 생길 수 있다(=완전 독립이 깨짐).

그래서 기본 정책은 **원본을 임시 파일로 복제**해서 “서로 다른 파일 식별자”로 만들어 Stage 공유를 끊는다.

```570:604:source/extensions/morph.tbs_control_1/morph/tbs_control_1/sim_multi_view.py
async def _clone_usd_for_aux_tile(...):
    dest = ... tempfile ... f"morph_tbs_clone_aux_{ti}_{token}_{os.getpid()}{suf}"
    await omni.client.copy_async(usd_path, dest_uri)
    return dest, ""
```

여기서 **`token`**은 “이번 분할 적용 시도(run) ID” 같은 값이다.

- 분할을 빠르게 연속 변경하면(2→4→3…) 이전 비동기 작업이 아직 진행 중일 수 있는데,
  그 결과가 나중에 도착해 “새 레이아웃”을 망가뜨리는 걸 막기 위해 `token`으로 세대를 구분한다.
- 즉 `token`은 “오래된 작업이면 무시하자”를 위한 안전장치다.

그리고 복제 결과(또는 복제 실패 시 래퍼)를, 보조 컨텍스트에서 Stage로 연다.

```728:786:source/extensions/morph.tbs_control_1/morph/tbs_control_1/sim_multi_view.py
async def _open_aux_stage_with_unique_session(...):
    if _use_aux_file_clone():
        clone_path, cerr = await _clone_usd_for_aux_tile(...)
        root_path = clone_path (성공하면)
    if root_path is None:
        wrap_path, werr = _make_aux_wrapper_root_layer(...)
        root_path = wrap_path
    ...
    ok, err = await _ctx_open_stage_path(ctx, root_path, sess_path)
```

여기서 이름이 헷갈리는 변수/함수들을 “사람 말”로 번역하면 아래와 같다.

- **`clone_path`**: “복제된 USD 파일의 로컬 임시 경로”
  - 예: `C:\\Temp\\morph_tbs_clone_aux_2_12345_9999.usd` 같은 파일 경로
- **`cerr`**: “복제 실패 이유 문자열(copy_async가 왜 실패했는지)”
  - `clone_path`가 `None`일 때, 왜 실패했는지를 담고 있는 메시지
  - 즉 `cerr = clone error`라고 생각하면 된다.

- **`root_path`**: “이번 보조 타일이 실제로 열어야 할 ‘루트 USD 경로’”
  - 복제에 성공하면 `root_path = clone_path`
  - 복제에 실패하면 `root_path = wrap_path` (래퍼로 폴백)

- **`wrap_path`**: “원본 USD를 subLayer로 가리키는 ‘래퍼(껍데기) USD’ 파일 경로”
  - 원본을 그대로 여는 대신, **새 파일(다른 identifier)** 을 하나 만들어서 그 안에서 원본을 참조하게 한다.
  - 목적: Kit의 레이어/스테이지 공유(‘같은 파일을 열면 같은 객체처럼 묶이는’ 현상)를 줄이기 위한 우회.

- **`werr`**: “래퍼 생성 실패 이유 문자열(wrapper 생성이 왜 실패했는지)”

- **`sess_path`**: “session layer 파일 경로(보조 타일 전용의 ‘임시 오버레이 레이어’)”
  - session layer는 ‘원본(또는 clone/wrapper) 위에 얹는 투명 덮개’라고 생각하면 된다.
  - 여기에는 보조 화면에서만 적용하고 싶은 변경(예: 일시적인 속성/가시성/저장하지 않을 수정)이 쌓일 수 있다.
  - 즉 `sess_path = session layer path`다.

- **`_make_aux_wrapper_root_layer(usd_path, ...)`**: “보조 타일 전용 래퍼 USD 파일을 만든다”
  - 결과: `wrap_path`(보조 타일만의 루트 파일 경로)
  - 동작: ‘원본을 subLayer로 포함하는 .usda’를 만들어서, 원본을 직접 열지 않고 래퍼를 연다.

- **`_ctx_open_stage_path(ctx, root_path, sess_path)`**: “해당 USD 컨텍스트(ctx)에서 Stage를 연다”
  - `root_path`를 열고, `sess_path`가 있으면 session layer까지 같이 붙여 연다.

한 문장으로 다시 요약하면:

> “보조 화면은 먼저 원본 USD를 복제해서(독립 파일) 열려고 시도하고, 그게 실패하면 래퍼 USD를 만들어 열며, 필요하면 그 위에 session layer까지 얹어서 ‘보조 화면 전용 스테이지’를 만든다.”

여기서 옵션이 2개 더 등장한다.

- **`TBS_MULTI_SPLIT_FILE_CLONE`**: 복제 사용 여부(기본 on)
- **`TBS_MULTI_SPLIT_SESSION_LAYER`**: session layer 얹을지(기본 on)

> “복제 + session layer”는, 독립성과 실행 안정성을 높이기 위한 조합이다.

---

### 3-3) 보조 Viewport(타일)를 만든다: 컨텍스트와 Viewport를 1:1로 연결

Stage를 열었으면, 그 컨텍스트를 바라보는 Viewport를 생성한다.

```1518:1526:source/extensions/morph.tbs_control_1/morph/tbs_control_1/sim_multi_view.py
vp_obj = create_viewport_window(
    name=wname,
    usd_context_name=ctx_name,
    width=..., height=...,
)
```

이 한 줄의 의미는:

> “이 Viewport 타일은 `ctx_name`(예: morph_tbs_split_aux_2)의 Stage를 렌더링한다.”

추가로 변수 이름을 번역하면:

- **`wname`**: Workspace 창 이름(보조 타일 창의 이름)
  - 예: `TBS_SimSplit_1` 같은 문자열
- **`width/height`**: 보조 타일 창의 픽셀 크기

그리고 마지막에 중요한 저장이 있다.

```1601:1604:source/extensions/morph.tbs_control_1/morph/tbs_control_1/sim_multi_view.py
ext._sim_multi_viewport_entries = entries
ext._sim_multi_context_names = ctx_names
```

여기서 `ext._sim_multi_context_names`가 바로 “화면 2~4의 USD 컨텍스트 이름 목록”이다.

그리고 `entries`/`ctx_names`는 아래 의미다.

- **`ctx_names`**: 만들어진 보조 컨텍스트 이름 배열
  - 예: `["morph_tbs_split_aux_1", "morph_tbs_split_aux_2"]`
- **`entries`**: “분할 구성품 목록”
  - 어떤 창을 만들었는지, 어떤 컨텍스트를 붙였는지, 정리(teardown)할 때 무엇을 없애야 하는지 기록해 둔 리스트
  - 쉽게 말해 “나중에 정리할 때 필요한 영수증 목록”이다.

---

## 4) 시뮬레이션 이벤트가 발생하면, 왜 화면별로 애니가 따로 도는가?

이제부터는 `control_window.py`의 이야기다.

### 4-1) 화면 번호(1~4) → usd_context_name을 계산한다

```1656:1678:source/extensions/morph.tbs_control_1/morph/tbs_control_1/control_window.py
def _usd_context_name_for_sim_screen(ext: Any, screen: int) -> Optional[str]:
    if s <= 1:
        return None
    names = list(getattr(ext, "_sim_multi_context_names", []) or [])
    idx = s - 2
    if 0 <= idx < len(names):
        return names[idx] or None
    return f"morph_tbs_split_aux_{s - 1}"
```

“코드 한 줄씩 해석” 버전(주석으로 읽기):

```text
입력 screen: 1..4 (화면 번호)

if screen == 1:
  return None
  -> None은 "기본 omni.usd 컨텍스트"를 뜻함(=메인 Viewport가 보는 Stage)

names = ext._sim_multi_context_names
  -> 예: ["morph_tbs_split_aux_1", "morph_tbs_split_aux_2", ...]
  -> 이 목록은 sim_multi_view가 분할 빌드할 때 저장해둠

idx = screen - 2
  -> 화면2는 idx=0, 화면3은 idx=1 ...

if idx가 목록 범위 안이면:
  return names[idx]
  -> "실제로 만들어진 컨텍스트 이름"을 우선 사용

else:
  return f"morph_tbs_split_aux_{screen-1}"
  -> 최후 폴백(기본 규칙대로 이름을 만들어서 사용)
```

해석(그대로 읽으면 된다):

- 화면1 → `None` (기본 컨텍스트)
- 화면2 → `ext._sim_multi_context_names[0]` (보통 `morph_tbs_split_aux_1`)
- 화면3 → `ext._sim_multi_context_names[1]`
- 화면4 → `ext._sim_multi_context_names[2]`

---

### 4-2) 애니메이션 실행은 반드시 그 컨텍스트로 `SequenceRunner.run()` 해야 한다

시뮬 이벤트에서 매핑된 JSON 스텝을 실행할 때, 실제 호출은 이렇게 된다.

```1030:1033:source/extensions/morph.tbs_control_1/morph/tbs_control_1/control_window.py
_ctx_run = _usd_context_name_for_sim_screen(ext, scr_i)
if runner_obj is not None:
    runner_obj.run(job.get("parsed", []), usd_context_name=_ctx_run, speed_scale=sp)
```

“코드 한 줄씩 해석” 버전:

```text
scr_i = 1..4
  -> 이 이벤트가 "어느 화면의 시뮬"에서 나온 건지

_ctx_run = 화면번호 -> usd_context_name 변환 결과
  -> 화면1이면 None(기본 Stage), 화면2~4면 morph_tbs_split_aux_* 같은 이름

runner_obj.run(steps, usd_context_name=_ctx_run)
  -> 이 한 줄 때문에 "애니가 어느 Stage에 적용되는지"가 결정됨
  -> _ctx_run이 틀리면:
     - prim 경로가 맞아도 "다른 Stage"에서 찾게 되어
     - 결과적으로 아무 것도 안 움직이는 것처럼 보일 수 있음
```

여기서 중요한 것은 딱 2개다.

- **`scr_i`**: “이 이벤트가 어느 화면의 시뮬레이션 엔진에서 나온 이벤트인지”
- **`usd_context_name=_ctx_run`**: “그 화면의 Stage에 애니를 적용하라”

보강 설명: `SequenceRunner`는 스텝을 실행할 때 내부에서 “현재 Stage”를 얻어와 prim을 찾는다.
따라서 `usd_context_name`이 틀리면, **prim 경로가 맞아도 다른 Stage를 보고 있어서** “아무 동작도 안 하는 것처럼” 보일 수 있다.

즉, 분할 화면에서 “화면2부터 타임라인이 안 도는 버그”가 있다면,
대부분 이 경로에서 **잘못된 컨텍스트로 실행되었거나(None로 실행됨)**,
또는 컨텍스트는 맞는데 **타임라인 인터페이스가 컨텍스트별로 분리되지 않은 경우**를 의심하게 된다.

---

## 5) 데이터가 어디에 저장되고 어디로 전달되는가? (딱 필요한 것만)

### 5-1) `ext._sim_multi_context_names`

- 생성/저장: `sim_multi_view._build_multi_split_async()`
- 사용: `control_window._usd_context_name_for_sim_screen()`
- 의미: “화면2~4가 참조해야 할 USD 컨텍스트 이름 목록”

### 5-2) `ext._sim_per_screen_snapshots` (화면별 “설정 저장” 스냅샷)

분할 화면에서 “각 화면이 독립적으로 동작한다”는 건 3D(Stage)만 분리하는 게 아니라,
**시뮬 입력값(LOT 수/간격/초기포트/고장포트/EP 개수 등)** 도 화면별로 분리해서 쓸 수 있어야 한다.

이 확장은 그 화면별 설정을 `ext._sim_per_screen_snapshots`에 저장한다.

- 인덱스 `0..3`은 화면 `1..4`에 대응한다.
- 화면별 “현재 설정 저장” 버튼을 누르면 해당 인덱스에 dict가 저장된다.
- **중요 정책(최근 반영)**:
  - 화면1은 기본값(전역 UI) 성격이라 즉시 갱신될 수 있다.
  - 화면2~4는 저장 버튼을 누르기 전에는 `None(미저장)` 상태를 유지한다.
  - 화면2~4가 `None`일 때는 “현재 UI값”을 바로 따라가지 않고, **화면1 스냅샷(기본값)** 을 폴백으로 쓴다.
    (그래야 “저장하지 않은 화면”이 현재 UI 변경에 의해 갑자기 바뀌지 않는다.)

특히 `ep_count_idx`(0=EP2 구성, 1=EP3 구성)는 아래 2군데 UI에 직접 영향을 준다.

- **포트 패널(BP4/EP3 칸 표시)**: 화면별 `_ep_count_idx_for_port_panel(ext, screen)`을 사용
- **막대그래프(EP 타임라인에서 EP3 라인 표시 여부)**: `_update_ep_timeline_under_port_state()`가 이제 화면별 `_ep_count_idx_for_port_panel(ext, screen)`을 사용

즉, 분할 화면에서 화면별 EP 포트 개수가 “별도로” 보여야 한다면,
이 두 UI가 전역 콤보가 아니라 **화면별 스냅샷 값**을 봐야 한다.

“코드로 어디서 적용되나?”를 더 직관적으로 보면:

```text
포트 패널(칸 숨김/보이기):
  ep_idx = _ep_count_idx_for_port_panel(ext, screen)
  if ep_idx == 1:
    BP4/EP3 칸 visible = True
  else:
    BP4/EP3 칸 visible = False

막대그래프(EP 타임라인):
  _update_ep_timeline_under_port_state(..., screen=화면번호)
    내부에서 ep_idx = _ep_count_idx_for_port_panel(ext, screen)을 읽고
    EP3 라인을 추가/제거한다
```

### 5-3) `tbs_sim_screen` (이벤트 payload 속 screen 번호)

시뮬 이벤트 payload에는 `tbs_sim_screen="2"`처럼 화면 정보가 포함되고,
이 값이 결국 `_usd_context_name_for_sim_screen()`의 입력으로 들어가서
애니를 “그 화면 Stage”에 적용한다.

---

## 6) (현상 이해용) 왜 ‘창이 여러 개 생기는 것처럼’ 보이나?

`sim_multi_view`는 보조 타일마다 `create_viewport_window(name=..., usd_context_name=...)`를 호출한다.
이 과정에서 Workspace 창(예: `TBS_SimSplit_1`)이 등록되고, Dock에 붙지 못하면 좌표 격자로 배치되기도 한다.

그래서 사용자는 체감상 “독립 viewport 창이 생긴다”라고 느낄 수 있다.

하지만 구조적으로 중요한 것은 “창이 몇 개인가”가 아니라,
**각 타일이 어떤 `usd_context_name`을 바라보는가**다.

보조 설명(도킹 관련):

- `dock_in`이 성공하면 “메인 Viewport 창 안에서 분할된 것처럼” 보일 수 있다.
- `dock_in`이 실패하면 “창이 여러 개 떠 있는 것처럼” 보일 수 있다.
- 하지만 두 경우 모두 핵심은 “보조 타일은 `morph_tbs_split_aux_*` 컨텍스트를 바라본다”는 점이다.

---

## 7) 여기까지 읽고 나면 얻는 것

이 문서를 따라가면 다음 질문에 답할 수 있다.

- 왜 분할에서 컨텍스트를 분리해야 하는가?
- 왜 USD 파일 복제가 필요한가?
- 화면 번호(2,3,4)가 어떤 컨텍스트(`morph_tbs_split_aux_*`)로 매핑되는가?
- 시뮬 이벤트가 들어왔을 때, 어떤 코드가 `usd_context_name=...`을 결정해서 애니를 실행하는가?

---

## 8) 체크리스트(디버깅 관점에서 “어디를 보면 되나”)

버그를 고칠 때는 아래 3개만 먼저 본다.

1. 분할 빌드가 성공했나? (`ext._sim_multi_context_names`가 실제 값으로 채워졌나)
2. 이벤트 payload의 `tbs_sim_screen`이 올바른가?
3. `SequenceRunner.run(..., usd_context_name=...)`에 전달되는 값이 올바른가?

그리고 “화면별 설정(특히 EP 포트 개수)이 분할 화면에 제대로 반영되지 않는다”면 아래도 같이 본다.

4. 화면별 스냅샷이 의도대로 저장/유지되나?
   - 화면2~4가 “저장 전”에는 `ext._sim_per_screen_snapshots[i] is None` 상태를 유지하는가?
   - 저장 버튼을 눌렀을 때만 해당 화면 인덱스가 dict로 채워지는가?

5. EP 포트 개수(`ep_count_idx`)가 “전역 콤보”가 아니라 “화면별 스냅샷”으로 반영되나?
   - 포트 패널(BP4/EP3 표시): `_ep_count_idx_for_port_panel(ext, screen)` 경로를 타는가
   - 막대그래프(EP 타임라인): `_update_ep_timeline_under_port_state()`가 화면별 `_ep_count_idx_for_port_panel(ext, screen)`을 쓰는가
   - 화면2~4가 미저장(None)인 동안에는 현재 UI값이 아니라 **화면1 스냅샷(기본값)** 으로 폴백되는가

이 3개가 맞으면, 그 다음에야 “타임라인이 컨텍스트별로 분리되어 동작하는가” 같은 Kit 레벨 이슈를 의심한다.
