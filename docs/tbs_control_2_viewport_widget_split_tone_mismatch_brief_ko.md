# TBS Control 2 — ViewportWidget 2분할: 화면2 배경/조명/톤 불일치(P0‑B) 정리 (ChatGPT 문의용)

작성일: 2026-07-08  
목적: Widget 2분할에서 **화면2 렌더 톤/배경/조명**이 화면1(원본 USD/기본 Viewport)과 **동일하지 않은 문제**를 외부(ChatGPT 등)에 전달해 해결책을 문의하기 위한 요약 문서.

---

## 1) 문제 정의 (What)

Viewport를 **Dock 방식이 아니라 ViewportWidget 방식**(`USE_VIEWPORT_WIDGET_SPLIT=True`)으로 2분할할 때,

- **화면1(좌측)**: `master_1.usd`가 정상 렌더, 원하는 뷰포트 배경/그리드/톤이 나옴
- **화면2(우측)**: `master_2.usd`는 렌더되지만 **배경색/그리드/ambient/HDR/tone mapping 등 “뷰포트 렌더 결과”가 화면1과 다르게 보임**

요구사항은 “비슷하게”가 아니라 **픽셀 단위로 동일**이다.

---

## 2) 절대 제약 (Hard constraints)

사용자 요구로 아래 제약을 깨는 해결책은 채택 불가:

1. Workspace `Viewport` 탭은 **1개만** 유지
2. `ViewportWindow.get_frame(...)` 내부에서 **ui.HStack 50:50**으로 분할
3. 화면1/2 모두 **`omni.kit.widget.viewport.ViewportWidget`** 사용
4. `TBS_SimSplit_*` 같은 **별도 Workspace 창 생성 금지**
5. Dock + `create_viewport_window` 폴백 금지(Widget 실패 시 Dock 폴백도 금지)

---

## 3) 현재 구현 아키텍처 (How it’s built)

문서/코드 기준으로 현재 목표 구조는 다음:

```
Workspace "Viewport" 탭 (1개)
└─ ViewportWindow.get_frame("...split_viewport_widgets")
    └─ ui.HStack (50% : 50%)
        ├─ [화면1] ui.ZStack
        │     ├─ ViewportWidget (usd_context = 기본 ctx, master_1.usd)
        │     └─ SceneView + ViewportCameraManipulator
        └─ [화면2] ui.ZStack or ui.Frame(materialize)
              ├─ ViewportWidget (usd_context = morph_tbs_split_aux_1, master_2.usd)
              └─ SceneView + ViewportCameraManipulator
```

### 주요 파일

- `docs/tbs_control_2_viewport_widget_split_chatgpt_briefing_ko.md` (가장 상세, 최신)
- `docs/tbs_control_2_viewport_widget_split_status_ko.md` (요약 상태표)
- `docs/tbs_control_2_viewport_kit_structural_investigation_ko.md` (Kit 구조적 제약/근거)
- 코드:
  - `source/extensions/morph.tbs_control_2/morph/tbs_control_2/sim_multi_view_widget.py`
  - `source/extensions/morph.tbs_control_2/morph/tbs_control_2/sim_multi_view.py` (오케스트레이션)
  - `source/extensions/morph.tbs_control_2/morph/tbs_control_2/tbs_split_composed_loader.py` (dual-path USD)

---

## 4) 확인된 사실 (Facts)

문서들에서 “확정된 사실”로 반복되는 내용:

- **USD Stage 분리**는 되어 있음
  - 화면1: 기본 usd context / `master_1.usd`
  - 화면2: 별도 usd context (`morph_tbs_split_aux_1`) / `master_2.usd`
- 화면2도 RenderProduct/Hydra가 생성되어 **검정 화면 단계는 지나감**(과거엔 검정 이슈가 있었음)
- 그럼에도 화면2는 **뷰포트 렌더 프로필(톤/배경/그리드/환경)**이 화면1과 다르게 보임

---

## 5) 원인 후보 (Why — hypotheses)

> 아직 “단일 근본원인”은 확정되지 않았고, 아래는 문서 기준으로 유력하게 보는 후보들이다.

### 5.1 Viewport “렌더 프로필” 완전 복제 실패

화면2는 다른 `UsdContext`용 ViewportWidget이므로,
화면1의 뷰포트 렌더 프로필(그리드/IBL/HDR/톤매핑/ambient/환경 텍스처 등)이 **자동으로 동일 적용되지 않을 수 있음**.

이미 일부 속성(`render_mode` 등) 복사 시도는 있었으나, 사용자 요구 수준(픽셀 동일)을 만족하지 못함.

### 5.2 ViewportWindow / active viewport 단일 모델과의 충돌 가능성

Kit의 ViewportWindow는 “active ViewportAPI”가 단수로 동작하는 전역 바인딩을 갖는 경로가 존재하며,
같은 ViewportWindow 프레임 안에 ViewportWidget 2개를 넣을 때,
프로필/환경/톤 관련 일부가 **전역(singleton) 경로로 흘러** 타일별 완전 독립/완전 복제를 어렵게 만들 수 있음.

### 5.3 화면2 컨텍스트의 기본 조명/환경(UsdLux session / dome light) 차이

과거 문서에 `_ensure_aux_stage_default_lighting()` 미호출로 인해 화면2 셰이딩이 붕괴/검정화되는 회귀가 기록되어 있음.
현재는 “검정”보다는 “톤 불일치”이므로, 단순 DomeLight 추가로 해결될 문제는 아닐 수 있으나,
**세션 레이어/환경 조명** 차이가 톤 차이에 기여할 가능성은 여전히 있음.

---

## 6) 이미 시도했으나 효과 없었던 완화(문서 기록)

아래 류의 “증상 완화”는 효과 없다고 기록됨(근본 메커니즘 답이 필요):

- render_mode/일부 렌더 속성만 복사
- 입력 차단/조작 폴링 등(카메라 coupling 쪽 완화 포함)

---

## 7) ChatGPT에 물어볼 핵심 질문 (Ask)

### Q1. 서로 다른 `usd_context_name`의 ViewportWidget 두 개를 같은 ViewportWindow 프레임에 넣을 때
**화면1과 픽셀 단위로 동일한 렌더 톤/배경/그리드/환경(IBL/HDR/tone mapping/ambient)을 강제로 동일화**하는
Kit에서 공식적으로 지원하는 메커니즘이 있는가?

- “render_mode만 맞추는 수준”이 아니라 **완전 복제/동일화**가 필요
- `set_hd_engine`를 공유하면 양쪽이 같은 USD를 보게 되는 문제가 있었/있을 수 있어 **엔진 공유는 제약**이 있을 수 있음

### Q2. ViewportWidget의 “뷰포트 프로필”은 어떤 계층에 저장/적용되는가?

예를 들어:

- ViewportAPI 속성으로만 충분한가?
- ViewportWindow(확장) 전역 설정(`/exts/.../bindings/...`) 같은 경로가 섞이는가?
- per-UsdContext / per-ViewportAPI / per-ViewportWindow 중 어느 레벨이 SSOT인가?

### Q3. 동일화를 위해 권장되는 패턴은 무엇인가?

사용자 제약을 지키면서 가능한 대안:

- “두 번째 ViewportWidget”이 아니라 “SceneView 2개” 혹은 다른 공식 샘플 패턴이 필요한가?
- ViewportWidget 2개가 같은 프레임에 있을 때 프로필이 한쪽만 적용되는 known limitation/bug가 있는가?

---

## 8) 참고 링크(레포 내)

- `docs/tbs_control_2_viewport_widget_split_chatgpt_briefing_ko.md`
- `docs/tbs_control_2_viewport_widget_split_status_ko.md`
- `docs/tbs_control_2_viewport_kit_structural_investigation_ko.md`

---

## 9) 최신 조사·수정 이력 (2026-07-08 저녁)

### 9.1 로그로 **확정된 직접 원인 (smoking gun)**

2026-07-08 재현 로그에서 아래가 반복 확인됨:

```
[TBS multi-sim] 보조 스테이지 fallback DomeLight 추가 ctx='morph_tbs_split_aux_1'
[TBS/coupling-report] stage-lux 'after-lighting-sync' main_count=2 aux_count=1
[TBS/coupling-report]  main_lux=['/OmniKit_Viewport_LightRig/Lights/DomeLight', '/OmniKit_Viewport_LightRig/Lights/DistantLight']
[TBS/coupling-report]  aux_lux=['/World/TBS_DefaultDomeLight']
```

**해석:**

| 구분 | 화면1 (메인) | 화면2 (보조) |
|------|-------------|-------------|
| UsdLux | Kit 기본 Viewport LightRig (Dome + Distant) | 코드가 넣은 generic fallback DomeLight 1개 |
| 조명 환경 | HDR/IBL 포함된 기본 rig | intensity=300 단색 DomeLight |
| 시각 결과 | 어두운 배경·낮은 ambient 톤 | 밝은 회색 배경·높은 ambient 톤 |

→ **Hydra 엔진/RenderProduct는 양쪽 모두 정상 생성**(`rtx`, `RealTimePathTracing`, RP 경로 존재)이나,  
**UsdLux(조명 rig)가 다르기 때문에** 픽셀 톤이 달라 보이는 것이 1차 원인으로 확정.

`[TBS multi-sim] aux 조명 동기화 완료 ... copied=N` 로그는 **한 번도 출력되지 않음** →  
`_sync_aux_stage_lighting_from_main()` 이 **copied=0** 으로 끝나 fallback 경로로 내려감.

### 9.2 시각 증상 (스크린샷)

- **화면1(좌)**: 검은 배경, 어두운 그리드, 장비 실루엣 대비 높음
- **화면2(우)**: 밝은 회색 배경, 밝은 그리드, 전체적으로 washed-out

UI 상단 툴바는 양쪽 모두 `RTX - Real-Time 2.0`, `Perspective`, lighting `Default` 로 보이나  
**실제 stage 조명 prim 구성은 로그상 완전히 다름**.

### 9.3 이번 세션에서 추가·수정한 코드

#### A) `sim_multi_view_widget.py` — aux 조명 동기화 강화

함수: `_sync_aux_stage_lighting_from_main(aux_ctx_name)`

**1차 수정 (이전):** `main_session` / `main_root` 에서만 `Sdf.FindSpec` → 복사 실패(copied=0)

**2차 수정 (현재 코드):**

1. `stage.GetLayerStack()` 전체를 순회해 LightRig spec 이 **어느 레이어에 작성됐는지** 탐색
2. 찾은 레이어에서 **ancestor 포함 prim tree** 를 aux `session layer` 로 `Sdf.CopySpec`
3. 성공 시 `[TBS multi-sim] aux 조명 동기화 완료 ... copied=N` 출력
4. 실패 시 기존대로 `_ensure_aux_stage_default_lighting()` → `/World/TBS_DefaultDomeLight` fallback

호출 시점: `_connect_widget_tile_aux_stage()` 내부, aux USD settle 후·ViewportWidget #2 생성 전

```python
# sim_control_defaults.py
VIEWPORT_AUX_LIGHTING_SYNC_FROM_MAIN: bool = True  # 기본 ON
```

#### B) `sim_viewport_p0b_diag.py` — P0-B 진단 모듈 (신규)

`finalize_widget_split_startup()` 마지막에 hook:

| 함수 | 역할 |
|------|------|
| `run_p0b_diagnostics()` | ViewportAPI / carb.settings / session layer / UsdLux / RenderProduct 덤프 |
| `run_p0b_fix_if_needed()` | main에 light 있고 aux에 없으면 session layer `TransferContent` 시도 |

**환경 변수 게이트:**

| 변수 | 기본 | 설명 |
|------|------|------|
| `TBS_P0B_DIAG=1` | OFF | `[TBS/p0b-api]`, `[TBS/p0b-carb]`, `[TBS/p0b-session]`, `[TBS/p0b-light]`, `[TBS/p0b-rp]` 출력 |
| `TBS_P0B_FIX=1` | OFF | session layer clone fix 시도 |

→ 사용자 제공 로그에 `[TBS/p0b-*]` 가 **없음** = 진단 모듈이 실행되지 않았거나 env 미설정 상태.

#### C) 기존에 이미 있던 시각 프로필 복사 (효과 없음 확인)

함수: `_copy_visual_render_profile_only()`, `_sync_aux_tile_render_from_main()`

복사 대상 (`_RENDER_PROFILE_ATTRS`):

- `render_mode`, `rendering_mode`, `shading_mode`, `hdr`
- `show_grid`, `grid_scale`
- `ambient_light_color`, `ambient_light_intensity`
- `background_color`, `background_enable`
- 기타 단순 타입 ViewportAPI 속성 (`dir(api)` 스캔)

`finalize_widget_split_startup()` 에서 `visual profile sync (no hydra/viewport_changed)` 로그 출력 후 실행.  
**로그상 hydra/render_mode는 양쪽 동일**이나 **톤 차이는 그대로** → ViewportAPI 속성 복사만으로는 부족.

### 9.4 수정 후에도 증상이 그대로인 이유 (현재 가설)

우선순위 순:

1. **LightRig spec 복사가 여전히 copied=0**
   - LightRig 가 session sublayer가 아니라 **Viewport 확장 내부 전용 경로**(stage traverse에는 보이지만 `Sdf.FindSpec` 으로 복사 불가한 transient layer)에 있을 수 있음
   - 또는 spec path가 `/OmniKit_Viewport_LightRig/...` 가 아닌 **다른 authoring layer / anonymous layer** 에 있을 수 있음
   - `GetLayerStack()` fallback 이 session+root 2개만 반환하는 Kit 빌드일 수 있음

2. **조명 외 SSOT 레이어가 따로 있음**
   - ViewportAPI `background_color` / `ambient_light_*` 는 복사했으나 **carb.settings** (`/rtx/...`, `/exts/omni.kit.viewport/...`) 가 per-context가 아닌 전역 singleton 일 수 있음
   - 화면1만 native ViewportWindow 경로의 프로필을 받고, embedded ViewportWidget(화면2)은 default 프로필을 받는 구조일 수 있음

3. **타이밍 문제**
   - `_ensure_aux_stage_default_lighting()` 이 aux ViewportWidget 생성 **전**에 호출됨
   - 이후 ViewportWidget #2 생성 시 Kit가 **자체 LightRig 또는 기본 환경을 다시 적용**해 복사 결과를 덮어쓸 가능성

4. **P0-B 진단 미실행**
   - `TBS_P0B_DIAG` 미설정으로 layer identifier / carb diff / ViewportAPI 전체 diff 가 수집되지 않아 SSOT 확정 불가

### 9.5 재현 로그 발췌 (2026-07-08 23:27 KST)

```
[TBS/hydra-diag] aux stage settled ctx='morph_tbs_split_aux_1' ... waited_frames=6
[TBS multi-sim] 보조 스테이지 fallback DomeLight 추가 ctx='morph_tbs_split_aux_1'
[TBS/coupling-report] stage-lux 'after-lighting-sync' main_count=2 aux_count=1
[TBS/coupling-report]  main_lux=['/OmniKit_Viewport_LightRig/Lights/DomeLight', '/OmniKit_Viewport_LightRig/Lights/DistantLight']
[TBS/coupling-report]  aux_lux=['/World/TBS_DefaultDomeLight']
...
[TBS/hydra-diag] finalize: aux render_product OK — visual profile sync (no hydra/viewport_changed)
...
[TBS/coupling-report] main_cam=(6969.94, 1458.23, 4828.58) aux_cam=(6969.94, 1458.23, 4828.58)
```

**동일한 것:** camera 위치, hydra_engine=rtx, render_mode=RealTimePathTracing, stage 파일 분리 OK  
**다른 것:** UsdLux prim 목록(위 표), 시각 톤/배경

### 9.6 아직 시도하지 않은 방향

- `omni.kit.viewport.utility` / `get_active_viewport()` 가 아닌 **per-tile ViewportAPI 에 LightRig 를 공식 API로 주입**하는 방법
- main ViewportWindow 의 **RenderSettings / Environment profile 을 aux context 에 clone** 하는 Kit API 탐색
- aux ViewportWidget 생성 **후** 1~N 프레임 뒤 조명 동기화 재시도 (타이밍 가설 검증)
- `UsdUtils.FlattenLayerStack` 또는 session `TransferContent` 를 lighting sync 전에 선행
- native ViewportWidget(#0, workspace 기본) 의 프로필을 reference 로 삼아 embedded #2 에 **carb.settings registry key 단위** 복제

---

## 10) ChatGPT에 추가로 물어볼 질문 (업데이트)

### Q4. `/OmniKit_Viewport_LightRig/...` 는 어느 레이어에 authoring 되며, 다른 UsdContext로 어떻게 복제해야 하는가?

- `stage.Traverse()` 로는 보이지만 `Sdf.FindSpec(main_session, path)` / `Sdf.FindSpec(main_root, path)` 모두 None 인 경우가 있는가?
- `GetLayerStack()` 의 어느 anonymous sublayer 에 있는지 찾는 공식 패턴은?
- **다른 usd_context 의 stage session layer 로 `Sdf.CopySpec` 이 유효한가**, 아니면 Viewport 확장이 per-context로 LightRig 를 다시 생성해야 하는가?

### Q5. ViewportWidget embedded tile 의 배경색/ambient/grid 는 어디서 결정되는가?

후보 SSOT 우선순위를 알려달라:

1. ViewportAPI 속성 (`background_color`, `ambient_light_*`, `show_grid`)
2. carb.settings (`/rtx/...`, `/exts/omni.kit.viewport...`)
3. UsdLux (DomeLight texture / intensity)
4. ViewportWindow singleton / active viewport 바인딩

**같은 ViewportWindow 프레임 안에 ViewportWidget 2개**일 때 2번·4번이 tile별로 분리되는가?

### Q6. 권장 fix 패턴 (제약 유지)

제약(§2)을 지키면서 화면2 톤을 화면1과 픽셀 동일하게 맞추려면:

- A) aux stage session 에 LightRig 복제
- B) aux ViewportWidget 생성 후 ViewportAPI 프로필 강제 동기화
- C) carb.settings per-viewport-id 복제
- D) SceneView 2개 + RenderProduct 공유(또는 profile 공유) 패턴
- E) Kit 공식 multi-viewport 샘플이 따르는 다른 구조

어느 쪽이 Omniverse Kit 107+ / RTX Real-Time 2.0 기준으로 맞는가?

### Q7. `_sync_aux_stage_lighting_from_main()` 이 copied=0 인데 Traverse 에는 LightRig 가 보이는 이유는?

이 조합이 known behavior 인지, 그리고 올바른 workaround 는 무엇인지.

---

## 11) 다음 디버깅 실행 방법 (GPT 답변 전 우리 측 수집용)

진단 로그 `[TBS/p0b-*]` 는 **기본 ON**. 끄려면 `TBS_P0B_DIAG_OFF=1`.  
session TransferContent 실험 fix만 `TBS_P0B_FIX=1`.

수집할 로그 prefix:

- `[TBS/p0b-session]` — main/aux session layer identifier, sublayers, rootPrims
- `[TBS/p0b-light]` — UsdLux paths, dome texture/intensity
- `[TBS/p0b-carb]` — `/rtx`, `/renderer`, `/exts/omni.kit.viewport` diff
- `[TBS/p0b-api]` — ViewportAPI 속성 전체 diff
- `[TBS/p0b-rig-clone]` — LightRig CopySpec / fallback 제거 결과
- `[TBS/coupling-report] stage-lux` — lighting sync 전후
- `[TBS multi-sim] aux 조명 동기화 완료` 또는 `fallback DomeLight 추가` 유무

**성공 기준 로그 (최신):**

```
[TBS/p0b-rig-clone] src_layer='anon:...'
[TBS/p0b-rig-clone] remove_fallback ... gone=True
[TBS/p0b-rig-clone] copied=1 ok=True has_rig=True has_fallback=False
  aux_after=['/OmniKit_Viewport_LightRig/Lights/DomeLight',
             '/OmniKit_Viewport_LightRig/Lights/DistantLight']
# /World/TBS_DefaultDomeLight 가 active 로 남아 있으면 실패
```

---

## 12) 관련 코드 위치 요약

| 항목 | 파일 | 함수/상수 |
|------|------|-----------|
| aux 조명 sync (early) | `sim_multi_view_widget.py` | `_sync_aux_stage_lighting_from_main()` |
| fallback DomeLight 추가 | `sim_multi_view_widget.py` | `_ensure_aux_stage_default_lighting()` |
| LightRig post-READY clone | `sim_multi_view_widget.py` | `_clone_default_light_rig_from_main_to_aux()` |
| fallback 강제 제거 | `sim_multi_view_widget.py` | `_remove_aux_fallback_dome_light()` |
| 조명 sync 호출 | `sim_multi_view_widget.py` | `_connect_widget_tile_aux_stage()` |
| 시각 프로필 복사 | `sim_multi_view_widget.py` | `_copy_visual_render_profile_only()`, `_sync_aux_tile_render_from_main()` |
| finalize hook | `sim_multi_view_widget.py` | `finalize_widget_split_startup()` |
| P0-B 진단 (기본 ON) | `sim_viewport_p0b_diag.py` | `run_p0b_diagnostics()`, `p0b_diag_enabled()` |
| 조명 로그 | `sim_viewport_coupling_diag.py` | `log_stage_lighting_summary()` |
| 기본 플래그 | `sim_control_defaults.py` | `VIEWPORT_AUX_LIGHTING_SYNC_FROM_MAIN` |

---

## 13) GPT에게 전달할 한 줄 요약

> ViewportWidget 2분할에서 화면2(aux) Hydra/RTX는 정상이다.  
> 초기에는 LightRig 복제(`Sdf.CopySpec`)가 FindSpec 실패로 copied=0 → fallback DomeLight(intensity=300)만 적용되어 톤이 달랐다.  
> 이후 **main session layer에서 LightRig 복제에는 성공**했으나, fallback이 제거되지 않아 **두 조명이 겹쳐** 화면2가 계속 밝게 보였다.  
> 현재 수정은 authoring layer 전수에서 fallback 강제 삭제(+비활성)다. **검증 중.**

---

## 14) 최신 진행 (2026-07-09 00:00~) — LightRig 복제 성공 / fallback 잔류

### 14.1 진단이 기본 ON으로 바뀐 뒤 확정된 추가 사실

`sim_viewport_p0b_diag.py` 진단은 기본 ON (`TBS_P0B_DIAG_OFF` 로만 끔).

READY 시점 로그에서 반복 확인:

| 항목 | 화면1 | 화면2 |
|------|--------|--------|
| UsdLux (clone 전) | `/OmniKit_Viewport_LightRig/Lights/{Dome,Distant}` | `/World/TBS_DefaultDomeLight` only |
| Dome intensity | `1.0` | `300.0` |
| hydra / render_mode | `rtx` / `RealTimePathTracing` | 동일 |
| ViewportAPI 의미있는 tone 속성 diff | 거의 없음 (id/RP/ctx/fps 정도) | — |
| `get_active_viewport()` | **native** ViewportWidget `#0` (embedded 타일 아님) | |
| `omni.kit.viewport.lights` | import 불가 | |
| session.rootPrims | main session에 `/OmniKit_Viewport_LightRig` 존재 | aux session에는 LightRig **없음** (초기) |
| root.rootPrims | main/aux **양쪽 root에** `/OmniKit_Viewport_LightRig` 이름 존재 | 단, aux stage traverse 시 Lux 조명은 fallback만 |

해석: 톤 차이의 SSOT는 당분간 **ViewportAPI / carb 전역이 아니라 UsdLux 구성**이다.

### 14.2 `_clone_default_light_rig_from_main_to_aux()` 도입

호출: `finalize_widget_split_startup()` READY 직후.

의도:

1. main에 LightRig Lux가 있고 aux에 없으면 clone 필요
2. `/OmniKit_Viewport_LightRig` 스펙을 main의 실제 authoring layer에서 찾아
3. aux **session layer**로 `Sdf.CopySpec`
4. 이후 fallback `/World/TBS_DefaultDomeLight` 제거

초기 실패 로그:

```
[TBS/p0b-rig-clone] skip: FindSpec missing for /OmniKit_Viewport_LightRig
  (main_session=True, main_root=True)
[TBS/p0b-rig-clone] finalize_call result=0
```

원인: `Sdf.FindSpec(session)` / `Sdf.FindSpec(root)`만으로는 스펙을 못 잡는 경우가 있었고,
`GetUsedLayers()` + `GetPrimAtPath` / `rootPrims` 스캔(`_layer_has_spec`)으로 탐색을 강화함.

### 14.3 복제 성공 로그 (2026-07-09 00:17경)

```
[TBS/p0b-rig-clone] src_layer='anon:000003916C281D00'   # = main session layer
[TBS/p0b-rig-clone] copied=1 ok=True
  main_lux=['.../DomeLight', '.../DistantLight']
  aux_before=['/World/TBS_DefaultDomeLight']
  aux_after=['/World/TBS_DefaultDomeLight',
             '/OmniKit_Viewport_LightRig/Lights/DomeLight',
             '/OmniKit_Viewport_LightRig/Lights/DistantLight']
[TBS/p0b-rig-clone] finalize_call result=1
```

**의미:**

- LightRig 복제 자체는 **성공** (main session → aux session).
- 그런데 `aux_after`에 **fallback DomeLight가 그대로 남음**.
- → LightRig(intensity≈1) + fallback(intensity=300) **이중 조명** → 화면2 톤이 계속 밝음.
- 사용자 체감: “하나도 안 변함” (스크린샷도 동일 패턴: 좌 어두운 톤 / 우 밝은 회색 + Default 라벨).

### 14.4 왜 `RemovePrim`이 안 먹혔나

기존 코드는 aux **session EditContext**에서만:

```python
aux_stage.RemovePrim("/World/TBS_DefaultDomeLight")
```

을 시도함. fallback은 대개 `_ensure_aux_stage_default_lighting()`이
**Define한 authoring layer(often root 또는 다른 used layer)** 에 남아,
session-only RemovePrim이 composed stage에서 no-op처럼 보임.

호출 순서도 문제:

1. aux settle 후 `_ensure_aux_stage_default_lighting()` → fallback 추가 (early, Widget #2 전)
2. early `_sync_aux_stage_lighting_from_main()` → copied=0 (시점에 LightRig authoring을 못 찾음)
3. READY 후 clone 성공
4. fallback은 제거되지 않음 → 겹침

### 14.5 현재 코드 수정 (검증 대기)

`sim_multi_view_widget.py`에 `_remove_aux_fallback_dome_light()` 추가:

1. `stage.GetUsedLayers()` (+ session/root) 전수에서 spec 소유 레이어를 찾아 `layer.RemovePrim`
2. session EditContext `stage.RemovePrim` 추가 시도
3. 그래도 composed에 남으면 `prim.SetActive(False)` (Hydra 비표시)
4. clone 성공 판정: `has_rig=True` **그리고** `has_fallback=False` (active 기준)

성공 시 기대 로그:

```
[TBS/p0b-rig-clone] remove_fallback label='after-rig-clone' removed=True gone=True
[TBS/p0b-rig-clone] copied=1 ok=True has_rig=True has_fallback=False
  aux_after=['/OmniKit_Viewport_LightRig/Lights/DomeLight',
             '/OmniKit_Viewport_LightRig/Lights/DistantLight']
# /World/TBS_DefaultDomeLight 가 active 목록에 없어야 함
```

시각 성공 기준: 화면2 배경/장비가 화면1과 동일 톤(밝은 회색/washed-out 해소).

### 14.6 아직 남는 리스크 / 다음 확인 항목

fallback 제거 후에도 톤이 남으면 2차 SSOT 후보:

1. aux **root**에 이름만 있는 `/OmniKit_Viewport_LightRig` vs session에 실제 Lux 내용 — 구성/override 충돌
2. Kit viewport “Default” lighting 모드 / environment profile (toolbar Default)이 embedded tile에만 따로 적용
3. carb `/rtx` 전역은 동일해도 **per-RenderProduct** 쪽 설정 차이
4. early에 fallback을 **아예 만들지 않기** (LightRig clone을 settle 직후·#2 생성 전으로 앞당기기) — 근본 흐름 정리

### 14.7 관련 코드 위치 (추가)

| 항목 | 파일 | 함수 |
|------|------|------|
| LightRig post-READY clone | `sim_multi_view_widget.py` | `_clone_default_light_rig_from_main_to_aux()` |
| fallback 강제 제거 | `sim_multi_view_widget.py` | `_remove_aux_fallback_dome_light()` |
| clone 호출 | `sim_multi_view_widget.py` | `finalize_widget_split_startup()` |
| P0-B 진단 (기본 ON) | `sim_viewport_p0b_diag.py` | `p0b_diag_enabled()`, `run_p0b_diagnostics()` |

