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

> ViewportWidget 2분할(화면1 default ctx / 화면2 `morph_tbs_split_aux_1`)에서 Hydra·RTX·camera는 동일하지만  
> **UsdLux만 다름**: main=`/OmniKit_Viewport_LightRig` (Dome intensity 1.0), aux는 대개 `/World/TBS_DefaultDomeLight`(300) → 우측 밝음.  
> `Sdf.CopySpec`으로 LightRig를 aux에 넣는 시도는 **비결정적**(어떤 런은 session 복제 성공+fallback 잔류로 밝음 유지, 어떤 런은 root 껍데만 복사 후 fallback 제거로 **검정 실루엣**, 지금은 again `copied=0`+fallback → 밝음).  
> **per-UsdContext Embedded ViewportWidget에 Kit Default LightRig를 공식적으로 생성/복제하는 API**가 필요하다.

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

### 14.3 복제 성공 로그 (2026-07-09 00:17경) — 그러나 시각은 여전히 밝음

```
[TBS/p0b-rig-clone] src_layer='anon:...'   # main session
[TBS/p0b-rig-clone] copied=1 ok=True
  aux_after=['/World/TBS_DefaultDomeLight',
             '/OmniKit_Viewport_LightRig/Lights/DomeLight',
             '/OmniKit_Viewport_LightRig/Lights/DistantLight']
```

LightRig는 들어왔지만 **fallback(intensity=300)이 남아 이중 조명** → 우측 계속 밝음.

### 14.4 `RemovePrim` no-op → fallback 강제 제거 시도

session-only RemovePrim은 authoring layer가 root/다른 layer일 때 no-op.
`_remove_aux_fallback_dome_light()`로 used-layers 전수 삭제 + SetActive(False).

### 14.5 회귀 A: 검정 실루엣 (00:25)

```
src_layer='.../master_1.usd'   # root 이름만 (빈 LightRig 껍데)
remove_fallback gone=True
aux_after=[]                   # Lux 0 → 객체 검정
```

### 14.6 회귀 B: 다시 밝음 (00:31, 현재)

안전장치로 “복사 실패 시 fallback 유지”를 넣자:

```
src_layer='.../master_1.usd'
keep_fallback: LightRig copy did not produce Lux (copied=0 aux_mid=['/World/TBS_DefaultDomeLight'])
copied=0 ok=False has_rig=False has_fallback=True
aux_after=['/World/TBS_DefaultDomeLight']   # intensity=300 → 우측 밝음
```

즉 **검정(무조명) ↔ 밝음(fallback 300)** 사이에서만 오가고,
**main과 동일한 LightRig-only 상태**에는 안정적으로 도달하지 못함.

### 14.7 사용자 관찰 (중요)

- **앱 재로드 순간**에 좌·우 톤이 **잠깐 동일**해 보인 적이 있음 → Kit이 어떤 시점에 LightRig/환경을 맞췄다가, finalize/fallback/클론 경로가 다시 어긋남.
- 껐다 켜면: 우측 객체 검정 → (이번 keep_fallback 수정 후) 다시 밝음.

→ Sdf.CopySpec 타이밍/레이어 선택에 의존하는 현재 접근은 **비결정적**이며, 근본 fix라기보다 레이스에 가깝다.

### 14.8 시도·실패 타임라인 (GPT용 압축)

| # | 시도 | 로그 결과 | 시각 |
|---|------|-----------|------|
| 1 | early `_sync_aux_stage_lighting_from_main` (session/root FindSpec) | copied=0 → fallback Dome 300 추가 | 우측 밝음 |
| 2 | layer-stack + ancestor CopySpec | 여전히 early copied=0 | 우측 밝음 |
| 3 | ViewportAPI visual profile 복사 | hydra/mode 동일, 톤 무변화 | 우측 밝음 |
| 4 | post-READY `CopySpec` LightRig (session 잡힘) | copied=1, **fallback 잔류** | 우측 밝음(이중조명) |
| 5 | fallback 강제 제거 | 어떤 런은 root 껍데만 복사 + Lux 0 | **검정 실루엣** |
| 6 | 복사 실패 시 fallback 유지 | copied=0, has_fallback=True | **다시 밝음** |
| — | (재로드 순간) | — | **잠깐 좌우 동일** (재현 불안정) |

### 14.9 현재 막힌 기술적 사실 (확정)

1. **Hydra/RP/카메라/스테이지 분리는 OK** — 톤 문제의 직접 원인은 UsdLux 불일치.
2. main의 LightRig 실제 Lux는 **Traverse로 보이지만**, `Sdf.CopySpec` 소스로 쓸 authoring layer가 **런마다 session anon vs root 파일로 갈림**.
3. root의 `/OmniKit_Viewport_LightRig`는 **이름만 있고 child Lux가 비어 있는 껍데**인 경우가 있음 → 그걸 복사해도 aux Lux=0.
4. early에 넣는 `/World/TBS_DefaultDomeLight` (300)는 검정 방지용이지만, main LightRig(1.0)와 **픽셀 동일 톤을 만들 수 없음**.
5. `get_active_viewport()`는 항상 **native #0**. embedded 타일은 active가 되지 않음.
6. `omni.kit.viewport.lights` 모듈 **없음**. utility에도 LightRig inject API 없음(introspect).

### 14.10 ChatGPT에 추가로 물을 것 (최신)

**Q8.** 서로 다른 `usd_context_name`의 embedded `ViewportWidget`에 Kit Default Viewport LightRig(`/OmniKit_Viewport_LightRig`)를 **공식 API로 생성**하는 방법이 있는가? (`Sdf.CopySpec` 없이)

**Q9.** LightRig가 session anonymous layer에만 실내용이 author되고 root에는 empty shell만 있는 패턴이 known인가? 다른 context로 복제할 때 **권장 패턴**은?

**Q10.** early fallback DomeLight(300)를 쓰지 않고도 aux stage를 암전 없이 띄우려면, Kit이 제공하는 **per-context default lighting** 설정/초기화가 있는가?

**Q11.** 사용자 제약을 지키며(Viewport 탭 1개 + HStack ViewportWidget 2개) 톤을 동일하게 하려면, LightRig 복제 대신 **Render Settings / Environment / Viewport lighting mode(Default)** 를 어떤 키·API로 tile별 동기화해야 하는가?

**성공 정의:** aux traverse 결과 = main과 동일하게  
`['/OmniKit_Viewport_LightRig/Lights/DomeLight', '.../DistantLight']` only (Dome intensity≈1.0),  
`/World/TBS_DefaultDomeLight` **없음**, 시각적으로 좌·우 톤 일치.

### 14.11 관련 코드 위치

| 항목 | 파일 | 함수 |
|------|------|------|
| LightRig post-READY clone | `sim_multi_view_widget.py` | `_clone_default_light_rig_from_main_to_aux()` |
| fallback 강제 제거 | `sim_multi_view_widget.py` | `_remove_aux_fallback_dome_light()` |
| early sync / fallback 추가 | `sim_multi_view_widget.py` | `_sync_aux_stage_lighting_from_main()`, `_ensure_aux_stage_default_lighting()` |
| clone 호출 | `sim_multi_view_widget.py` | `finalize_widget_split_startup()` |
| P0-B 진단 (기본 ON) | `sim_viewport_p0b_diag.py` | `run_p0b_diagnostics()` 등 |

---

## 15. Ordered Investigation (추측 패치 금지 — 로그로 Q1–Q5 확정)

### 15.1 배경 재확인

- Master USD 동일 / Hydra·RP·카메라 OK.
- 우측 wash-out ≈ `/World/TBS_DefaultDomeLight` (intensity=300).
- LightRig `copied=1` 후에도 톤 불일치 → **Clone만으로 설명이 안 됨**.
- reload 직후 좌우가 잠깐 같아 보였다가 finalize/fallback/clone 이후 갈라짐 → **레이스 / 우리 코드가 Kit init을 깨는지** 검증 필요.

### 15.2 구현된 진단 (패치 없음, 관측만)

| Prefix | 내용 |
|--------|------|
| `[TBS/p0b-ord]` | `_connect_widget_tile_aux_stage` / `_sync_*` / `_ensure_*` / `_clone_*` / `_sync_aux_tile_render_*` / `finalize_*` 호출 순서 + frame/ts/main_lux/aux_lux/API id/RP |
| `[TBS/p0b-frame]` | connect 직후부터 최대 120프레임 lux·bg·ambient·grid·hdr·mode·carb tone keys; `FIRST_LUX_DIFF` |
| `[TBS/p0b-who]` | `Define` 직전 stack + aux stage `Usd.Notice.ObjectsChanged` (fallback 생성 주체) |
| `[TBS/p0b-rs]` | RenderSettings / Environment 관련 prim·attr dump |

### 15.3 실험용 환경 변수 (기능 off만, intensity 튜닝 금지)

| Env | 효과 |
|-----|------|
| `TBS_P0B_DIAG=1` | 진단 ON (기본 OFF) |
| `TBS_P0B_FRAME_TRACK=1` | 120프레임 추적 (`TBS_P0B_DIAG=1` 필요) |

**권장 실행 순서**

1. **Baseline (기본)** — env 없음. ord / frame / who / rs 수집 → Q1–Q4.
2. **실험 4** — `TBS_P0B_DISABLE_FALLBACK=1` only. aux_lux가 비는지 / Kit이 LightRig를 만드는지.
3. **실험 5** — `TBS_P0B_DISABLE_FALLBACK=1` + `TBS_P0B_DISABLE_CLONE=1`. reload 순간에 `main_lux==aux_lux` 프레임이 실제로 있는지, 우리 코드 없이 톤이 유지되는지 → **Q5**.

### 15.4 로그로만 답할 질문

- **Q1** WHO/WHEN: `[TBS/p0b-who] CREATE` vs `NOTICE`
- **Q2** reload 직후 same_lux 프레임 존재?: `[TBS/p0b-frame] same_lux=True`
- **Q3** 최초 분기 프레임: `FIRST_LUX_DIFF`
- **Q4** 그 프레임에서 바뀐 것: lux vs rs vs api-diff vs carb
- **Q5** fallback/clone이 Kit init을 깨는지: 실험 4/5 vs baseline 비교

### 15.5 금지 (유지)

원인 추측 패치 / intensity 튜닝 / CopySpec 강화 / master_2·Hydra·카메라 가설.

---

## 16. Baseline 로그로 Q1–Q5 확정 (2026-07-08 run)

### 16.1 답

| Q | 답 (로그) |
|---|-----------|
| **Q1** WHO/WHEN fallback? | **우리 코드.** `frame=98` `[TBS/p0b-who] CREATE` stack: `finalize → _connect_widget_tile_aux_stage → _ensure_aux_stage_default_lighting` → `UsdLux.DomeLight.Define(/World/TBS_DefaultDomeLight)` intensity=300. Kit이 아님. |
| **Q2** reload 순간 main==aux? | 이 run에서는 **early에 same_lux 프레임 없음.** Widget #2 생성 전·후 내내 `main=LightRig` vs `aux=[]` 또는 `aux=[TBS_DefaultDomeLight]`. (`[TBS/p0b-frame]`는 `IApp.create_task` 없어 schedule_fail → 미수집; 이후 asyncio로 수정) |
| **Q3** 톤 분기 최초 프레임? | Lux 분기 = **frame 98** (`exit_created_fallback`, aux에 300 Dome 등장). 시각 wash-out는 그 직후 Widget #2 bind부터. |
| **Q4** 그 프레임에서 변한 것? | **UsdLux만.** RenderSettings/API hydra/mode 동일(rtx/RTPT). ViewportAPI tone 필드는 의미 있는 차이 거의 없음(`fps`/`id`/`rp`/`ctx`만). |
| **Q5** 우리 fallback/clone이 Kit init을 깨뜨리나? | **fallback이 wash-out의 직접 원인(확정).** clone은 `src_layer=master_1.usd`(empty shell) → `copied=0` → fallback 유지 → 우측 밝음. Kit LightRig init을 “깨서” 밝아진 게 아니라 **우리가 300 Dome을 넣은 것**. |

### 16.2 적용한 수정 (로그 기반, intensity 튜닝 아님)

1. **fallback 기본 OFF** — `TBS_P0B_ALLOW_FALLBACK=1` 일 때만 intensity=300 Dome 생성.
2. **LightRig clone** — root USD shell을 last-resort로 쓰지 않음; session 우선; CopySpec 실패 시 **composed stage 속성으로** Dome(1.0)+Distant 재작성 (`composed_author`).
3. **clone 실패 시 fallback 재주입 금지.**
4. **120프레임 트래커** — `asyncio.ensure_future` (Kit `IApp`에 `create_task` 없음).

### 16.3 reload 후 기대 로그

- `enter_no_fallback` / `exit_sync_only` (fallback CREATE 없음)
- `[TBS/p0b-rig-clone] composed_author n=2` 또는 session CopySpec 성공
- `aux_lux` ≈ `['/.../DomeLight', '/.../DistantLight']`, `TBS_DefaultDomeLight` 없음
- 좌·우 톤 일치 (성공 정의 §14.10)

