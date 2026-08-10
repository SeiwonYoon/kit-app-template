# TBS Control 2 — Kit Viewport 구조적 제약 조사 보고서 (P0 최종)

> **작성일**: 2026-07-06 21:50 (§12 패치 적용: 2026-07-06 22:00)  
> **목적**: Kit 내부·공식 문서·12:38 로그로 P0-A/P0-B 근본 원인 **확정/미확정** 분리  
> **원칙**: 확인된 사실 / 추정 / 공식 근거 명시.

---

## 0. 전제 — 이미 확정된 사실 (12:38 로그)

| 영역 | 상태 | 근거 |
|------|------|------|
| USD Stage 분리 | ✓ | `main_id=0x1dc45d12810` ≠ `aux_id=0x1cd65cc9bd0` |
| RenderProduct | ✓ | `ViewportTexture_1` / `ViewportTexture_2` |
| Hydra | ✓ | 양쪽 `hydra=rtx` |
| ViewportAPI 독립 | ✓ | `widget_tile_1` / `widget_tile_2` |
| Manipulator 부착 | ✓ | READY `ViewportCameraManipulator` + `SceneCameraModel` 양쪽 |
| ViewportAPI attr | ✓ | `render-profile-diff` DIFF 없음 |

**그럼에도** P0-A(camera coupling) · P0-B(톤/조명/셰이딩 불일치) 잔존.

---

## 조사 1 — ViewportCameraManipulator 내부 구현

### 공식 근거

| 출처 | 내용 |
|------|------|
| [ViewportCameraManipulator API](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.manipulator.camera/latest/omni.kit.manipulator.camera/omni.kit.manipulator.camera.ViewportCameraManipulator.html) | 생성자: `ViewportCameraManipulator(viewport_api, ...)` — **ViewportAPI를 인자로 받음** |
| [UsdCameraManipulator API](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.manipulator.camera/latest/omni.kit.manipulator.camera/omni.kit.manipulator.camera.UsdCameraManipulator.html) | 기반 클래스: `usd_context_name`, `prim_path` — **USD prim에 kit-commands로 기록** |
| [Stage Preview Widget](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.viewport.docs/latest/widget.html) | 공식 패턴: `ViewportCameraManipulator(self.viewport_api)` + `self.viewport_api.add_scene_view(scene_view)` |

### 소스 코드 접근

| 항목 | 결론 |
|------|------|
| `omni.kit.manipulator.camera` Python/C++ 소스 | **본 워크스페이스·일반 GitHub에 미포함** (Kit 설치 패키지 precompiled) |
| `get_active_viewport()` 내부 호출 여부 | **소스 미확인** — 문서만으로는 **확정 불가** |

### 문서로 확인 가능한 동작 모델

1. `ViewportCameraManipulator`는 생성 시 전달된 **`viewport_api`에 바인딩**되는 것이 API 계약이다 (공식 근거).
2. 조작 결과는 `UsdCameraManipulator` 계열을 통해 **`usd_context` + `camera_path`의 USD Camera prim**에 반영된다 (공식 근거).
3. **별도 경로**: `omni.kit.viewport.window`의 카메라 제스처는 carb 설정  
   `/exts/omni.kit.viewport.window/bindings/camera` 로 **전역 바인딩**된다 ([Camera Manipulation](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.viewport.docs/latest/camera_manipulator.html)).  
   이 경로는 **ViewportWindow 확장** 소속이며, **per-ViewportAPI가 아님** (공식 근거).

### 추정 (12:38 로그와 결합)

Coupling은 `ViewportCameraManipulator`가 `get_active_viewport()`를 쓴다기보다, **ViewportWindow 네이티브 manipulator + 전역 camera bindings + `get_active_viewport()`가 native API를 가리키는 구조**가 복합 작용했을 가능성이 높다.

---

## 조사 2 — Orbit 이벤트가 실제 어느 API/Stage를 수정하는가

### 확인된 사실 (12:38 `[TBS/coupling-trace]`)

| 관찰 | 의미 |
|------|------|
| READY 시 `main_cam` ≈ `aux_cam` (동일 좌표) | 초기 카메라 동기 상태 |
| Orbit 초반: **master_1·master_2 Persp가 교차 변경** | 양쪽 Stage 카메라 prim 동시 변형 |
| Orbit 후반: **master_1만 연속 변경** 구간 다수 | 한쪽(주로 default ctx)으로 입력 수렴 |
| `get_active_viewport()` = `0x1cd8e71ea70` | **native #0 API** — `widget_tile_1/2`와 다름 |

### Widget 인스턴스 맵 (12:38)

```
ViewportWidget.get_instances count=3
  #0 ctx=''  api=0x1cd8e71ea70  ← get_active_viewport() 와 동일 (native)
  #1 ctx=''  api=0x1dbf21e4950  ← embedded 화면1 (widget_tile_1)
  #2 ctx='morph_tbs_split_aux_1' api=0x1cd65d1f600 ← embedded 화면2
```

### 공식 입력 스택 (문서 기반 재구성)

```
[경로 A — embedded 타일, Stage Preview 패턴]
Mouse → SceneView (ZStack top)
     → ViewportCameraManipulator(gesture)
     → CameraManipulatorModel
     → ViewportAPI (생성자에 전달된 api)
     → camera_path → UsdCameraManipulator → USD prim (해당 api.usd_context)

[경로 B — ViewportWindow 네이티브, 문서상 기본]
Mouse → ViewportWindow 내장 manipulator
     → carb .../omni.kit.viewport.window/bindings/camera
     → ViewportWindow.viewport_api (active, 단수)
     → default context /OmniverseKit_Persp
```

### 미확인 (코드 수정 없이 추가 로그 불가)

Manipulator 내부에서 **프레임별 `api.id`** 를 찍는 계측은 이번 작업 범위에서 **미수행** (사용자 지시: 패치 금지).  
다음 단계에서만 가능: SceneView gesture hook 또는 Kit extension hook (조사용, 완화 패치 아님).

### 진단 버그 (확인된 사실)

현재 `sim_viewport_coupling_diag.py` UsdNotice 콜백이 `label='main'/'aux'` 대신  
`Usd.Stage.Open(...)` 문자열을 출력 — **Persp 전용 필터 미적용**.  
coupling 여부 판독 시 로그 해석에 주의 필요.

---

## 조사 3 — Active Viewport와 Manipulator 관계

### 공식 근거

| API | 문서 |
|-----|------|
| `get_active_viewport()` | [omni.kit.viewport.utility](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.viewport.utility/latest/omni.kit.viewport.utility.html) — **활성** viewport API 반환 |
| `ViewportWindow.viewport_api` | [ViewportWindow](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.viewport.window/latest/omni.kit.viewport.window/omni.kit.viewport.window.ViewportWindow.html) — **"Return the active ViewportAPI for the ViewportWindow"** (단수) |
| NVIDIA 샘플 전반 | `get_active_viewport_window()` + **단일** `viewport_window.viewport_api.add_scene_view(...)` |

### 확인된 사실 (12:38)

- `get_active_viewport()` = native #0 (`0x1cd8e71ea70`)
- embedded `widget_tile_1` (`0x1dbf21e4950`) ≠ active viewport
- manipulator는 양쪽 tile API에 부착됨

### 결론 (문서 + 로그)

| 질문 | 답 |
|------|-----|
| `ViewportCameraManipulator`가 생성자 API만 쓰는가? | API **계약상** `viewport_api` 인자 사용 (공식). 내부 `get_active_viewport` 호출은 **미확인** |
| Active viewport가 coupling에 관여하는가? | **예, 구조적으로 관여** — Kit의 active는 embedded가 아닌 **ViewportWindow 네이티브 API** (12:38 로그 **확인**) |
| ViewportWindow는 ViewportAPI를 몇 개 전제하는가? | 속성명·문서상 **active 1개 (단수)** (공식 근거) |

---

## 조사 4 — 공식 샘플: `get_frame` + HStack + ViewportWidget × 2

### 검색 범위

- NVIDIA-Omniverse/kit-extension-sample-ui-scene
- NVIDIA-Omniverse/kit-extension-sample-ui-window
- omni.kit.viewport.docs (Stage Preview, Viewport API)
- omni.kit.widget.viewport Overview
- Isaac Sim 이슈/예제 (다중 뷰포트)

### 공식 지원 사례

| 패턴 | 샘플/문서 | 독립 Orbit |
|------|----------|------------|
| **ViewportWindow 1 + get_frame 1 + SceneView 1** + `add_scene_view` | [object_info tutorial](https://github.com/NVIDIA-Omniverse/kit-extension-sample-ui-scene), [light_manipulator](https://github.com/NVIDIA-Omniverse/kit-extension-sample-ui-scene/tree/main/exts/omni.example.ui_scene.light_manipulator) | 오버레이는 **창의 단일** `viewport_api`에 종속 |
| **StagePreviewWidget** = ZStack(VW + SceneView + Manipulator) | [widget.html](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.viewport.docs/latest/widget.html) | **1 ViewportWidget per widget** |
| **ViewportWidget in ui.Window** | [widget Overview](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.widget.viewport/latest/Overview.html) | 독립 `viewport_api` per instance |
| **다중 뷰포트** | `create_viewport_window()` | [API 문서](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.viewport.utility/latest/omni.kit.viewport.utility/omni.kit.viewport.utility.create_viewport_window.html) — **별도 Window** per viewport |
| **Dock 분할** | Isaac Lab 등 | `ui.Window` + `dock_in` — **별도 Window** |

### 공식 지원 사례 없음 (확인)

```
ViewportWindow
  └─ get_frame()
       └─ HStack
            ├─ ViewportWidget (ctx A)
            └─ ViewportWidget (ctx B)
            각각 독립 Orbit
```

**레포·NVIDIA 공식 샘플·Kit 문서 어디에도 이 구조의 공식 예제는 없음.**

### 추정

현재 TBS 구조는 **공식 문서에 없는 조합**.  
동작 일부(RP, Hydra, manipulator 부착)는 가능하나, **ViewportWindow 단일 active 모델과 충돌**할 여지가 있다.

---

## 조사 5 — SceneView 다중 사용 / Singleton 여부

### 공식 근거

[Viewport API — SceneView](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.viewport.docs/latest/viewport_api.html):

> ViewportAPI can push view and projection matrices into SceneView.model  
> `add_scene_view(scene_view)` — **list of models** (복수)  
> weak-reference로 보관, `remove_scene_view` 지원

### 해석

| 수준 | 결론 |
|------|------|
| **ViewportAPI 당** SceneView **복수 등록 가능** | 공식 근거 ✓ |
| **ViewportWindow 당** SceneView 1개만 | 문서 **미명시** — 샘플은 사실상 1개 |
| **앱 전역 Singleton SceneView** | 문서·샘플 **근거 없음** |

### 현재 TBS 구조

- SceneView 2개 (타일별 1개)
- 각각 **서로 다른** `ViewportAPI.add_scene_view` 에 등록 (12:38 `scene_view_registered=True`)

→ SceneView 자체는 **API별로 독립 등록** 가능 (공식).  
문제는 SceneView singleton이 아니라 **ViewportWindow + native active + 전역 bindings** 층일 가능성 (추정).

---

## 조사 6 — Active CameraManipulatorModel (Orbit 중)

### 코드 수정 없이 확인 가능한 것 (12:38)

| 항목 | READY 로그 |
|------|------------|
| 화면1 `manip.model` | `CameraManipulatorModel@0x1cd65ca9430` |
| 화면2 `manip.model` | `CameraManipulatorModel@0x1dbc2d354f0` |
| `manip_pending` | 양쪽 False |

→ Orbit **전** manipulator·model **존재**는 확인.  
Orbit **중** 어느 model이 gesture를 처리했는지는 **미로깅** (이번 작업 패치 금지).

### 12:38 Orbit 중 USD 관측 (확인된 사실)

- 초기: main·aux **동시** Persp 변경
- 이후: **main만** 변경되는 구간

### 추정

- 양쪽 manipulator model이 **동시에 navigation enabled** 이면, 동일 제스처가 양쪽에 전달될 수 있음 (앱 코드 `_activate_tile_manipulator_only` 의도와 별개로, **네이티브 경로 B**가 추가로 default ctx를 움직일 수 있음).
- 후반 main-only 변경은 **native/active 경로**가 지배적이었을 가능성.

---

## 조사 7 — Render Tone (ViewportAPI 이후 단계)

### 확인된 사실

| 레이어 | 화면1 vs 화면2 | 근거 |
|--------|---------------|------|
| ViewportAPI attr | **동일** | `render-profile-diff` DIFF 없음 |
| RenderProduct | **별도** | `ViewportTexture_1` / `_2` |
| Hydra engine | 둘 다 `rtx` | hydra-diag |
| USD Stage | **별도** | master_1 / master_2 |
| aux fps @ READY | **0** vs main ~0.33 | hydra-diag |
| 객체 셰이딩 | 화면2 **검은 실루엣** (DomeLight 제거 후) | 사용자 스크린샷 + 회귀 보고 |

### 공식 근거 — 렌더 파이프라인

[ViewportWidget Overview](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.widget.viewport/latest/Overview.html):

> ViewportWidget — low level widget, **no menus or manipulators**  
> Used **by** ViewportWindow for default experience

각 `ViewportWidget`은 **자체 ViewportAPI → 자체 RenderProduct → Hydra texture** (12:38 로그 확인).

### ViewportAPI로 복제되지 않는 항목 (공식·구조)

| 항목 | 결정 위치 | TBS에서 |
|------|----------|---------|
| RTX / IBL / Sky | USD Stage `UsdLux`, `RenderSettings`, env | Stage 파일·세션 레이어 |
| Tone mapping / post | Viewport composite, carb RTX, Presenter | **타일별 Hydra 인스턴스** |
| Grid / ambient (API) | ViewportAPI | **동일** (로그) |
| Presenter | ViewportWindow native presenter vs embedded | native **숨김** + embedded composite |

### 결론

**P0-B의 "톤/배경/환경 불일치"는 ViewportAPI 문제가 아님** (12:38 DIFF 없음 — **확인**).  
**Hydra / Stage 조명 / Presenter / fps=0** 레이어 문제 ( **확인 + 추정** ).

---

## DomeLight / Stage Lighting (조사만)

### 확인된 사실

| 상태 | 객체 | 배경/톤 |
|------|------|---------|
| DomeLight **있음** (이전) | 머티리얼 정상 | 화면1과 톤 불일치 |
| DomeLight **제거** (현재) | **검은 실루엣** | API attr는 동일 |

### 공식·구조적 방향 (코드 미적용)

| 접근 | 설명 | 근거 유형 |
|------|------|----------|
| **화면1 Stage Lux 스냅샷 → aux 복제** | master_1의 DomeLight/원격 env를 master_2에 동일 prim 복사 | USD 표준 — **추정** (톤 동일화에 유력) |
| **StagePreviewWidget 패턴** | 타일마다 독립 VW+SceneView+Manipulator, **동일 usd_context_name** 아님 — TBS는 **의도적으로 다른 ctx** | 공식 widget.html |
| **ViewportAPI ambient만** | attr 동일해도 RTX PathTracing에서 **객체 검정** — ambient만으로 부족 (12:38 **확인**) |
| **create_viewport_window × 2** | 창마다 독립 ViewportWindow → Kit **공식 다중 뷰** 패턴 | utility API **공식** |

목표는 **DomeLight 제거가 아니라 화면1과 동일 Lighting** — Stage 레벨 동기화 또는 공식 다중-Window 구조 검토.

---

# 최종 질문 5가지 — 답변

### Q1. ViewportCameraManipulator는 전달받은 ViewportAPI만 사용하는가?

| 구분 | 답 |
|------|-----|
| **API 계약** | **예** — 생성자 `viewport_api` 필수 (공식 문서) |
| **내부 구현** | **미확인** — 소스 비공개 |
| **실질 coupling 원인** | Manipulator 외에 **ViewportWindow native manipulator + `/exts/.../bindings/camera` 전역 바인딩 + `get_active_viewport()`=native** (로그·문서) |

### Q2. `get_frame` + HStack + ViewportWidget 2개 + 독립 Orbit — 공식 지원?

**아니오 — 공식 지원 사례 없음** (NVIDIA 샘플·Kit 문서 전수 검색).  
공식 다중 뷰: `create_viewport_window()` 또는 **별도 `ui.Window` + ViewportWidget**.

### Q3. Camera Coupling — 앱 코드 vs Kit 구조?

| | |
|--|--|
| **앱 단독 버그** | **아님** — stage/api/manipulator 독립 확인 |
| **Kit 구조적 제약** | **높은 가능성** — ViewportWindow **단일 active ViewportAPI**, native manipulator, 전역 camera bindings, 비공식 multi-embedded 레이아웃 |
| **복합** | embedded manipulator **양쪽 부착됐음에도** native 경로가 default ctx를 계속 변경 (12:38 trace) |

### Q4. Render Tone 차이 — ViewportAPI vs Hydra/Environment?

**ViewportAPI 아님** (DIFF 없음 — 확인).  
**Stage 조명(UsdLux) + per-widget Hydra/Presenter + aux fps=0** (확인·추정).

### Q5. 현재 구조 유지하며 공식적으로 해결 가능한가?

| 판단 | 내용 |
|------|------|
| **완전한 공식 해법** | **문서화된 바 없음** — 현재 레이아웃은 비공식 |
| **부분 완화** | 타일별 StagePreview 패턴 정합 + native manipulator/bindings **완전 차단** (효과는 이전 패치에서 불충분) |
| **공식에 가까운 대안** | `create_viewport_window` / Dock 으로 **ViewportWindow 2개** (사용자 제약: Dock/`TBS_SimSplit` 금지와 **충돌**) |
| **현실적 타협** | 동일 Viewport 탭 유지 시 **Kit 비공식 영역** — NVIDIA 포럼/지원 확인 권장 |

---

## §12 적용 패치 (2026-07-06) — 조사 결론 기반, 구조 변경 없음

조사에서 **효과 가능성이 있고** 이전에 시도하지 않았거나 **진단 보강**인 항목만 적용.  
`focus` / polling / `viewport_changed` / render profile 확대 등 **실패한 패치는 재시도하지 않음**.

### P0-A — Camera coupling 완화

| 패치 | 파일 | 내용 |
|------|------|------|
| **전역 camera bindings 비활성** | `sim_multi_view_widget.py` | `carb.settings` `/exts/omni.kit.viewport.window/bindings/camera` → `{}` (native manipulator 마우스 경로 차단). 플래그: `VIEWPORT_DISABLE_NATIVE_CAMERA_BINDINGS` |
| **Orbit 계측** | `sim_viewport_coupling_diag.py` | `MANIP_ACTIVATE` / `ORBIT_CTX` — 타일 활성화·UsdNotice Persp 변경 시 `api`·`manip.model`·`active_tile`·`native_api` 1줄 |
| **Persp 필터** | `sim_viewport_coupling_diag.py` | `USD_CHANGED` — `/OmniverseKit_Persp` 및 xformOp 하위만, `label=main/aux` 명시 |

**기대**: native ViewportWindow manipulator가 default ctx 카메라를 움직이는 경로 차단.  
**검증**: 화면2 Orbit 후 `[TBS/coupling-trace]` 에 `USD_CHANGED label='aux'` 만, `label='main'` 없어야 함.

### P0-B — Render / Lighting

| 패치 | 파일 | 내용 |
|------|------|------|
| **화면1 UsdLux → aux 복제** | `sim_multi_view_widget.py` | `_sync_aux_stage_lighting_from_main()` — main root layer UsdLux를 aux **session layer** 동일 경로로 `UsdUtils.CopySpec`. 플래그: `VIEWPORT_AUX_LIGHTING_SYNC_FROM_MAIN` |
| **fallback DomeLight** | 동일 | 복제 0건 + aux 조명 없을 때만 `/World/TBS_DefaultDomeLight` (검은 실루엣 방지) |
| **stale DomeLight 제거** | 동일 | 동기화 시 이전 generic `TBS_DefaultDomeLight` session 제거 |
| **조명 diff 로그** | `sim_viewport_coupling_diag.py` | READY·sync 후 `[TBS/coupling-report] stage-lux` main/aux 경로 비교 |
| **aux fps bootstrap** | (기존 유지) | READY 후 `fps<0.5` 이면 `_bootstrap_tile_viewport_render_async` 16프레임 |

**기대**: 객체 검정 회귀 해소 + 화면1과 동일 IBL/Dome 톤.  
**검증**: `stage-lux` 에서 `main_count` ≈ `aux_count`, 경로 목록 유사.

### SSOT 플래그 (`sim_control_defaults.py`)

```python
VIEWPORT_AUX_LIGHTING_SYNC_FROM_MAIN: bool = True
VIEWPORT_DISABLE_NATIVE_CAMERA_BINDINGS: bool = True
VIEWPORT_COUPLING_DIAG_ENABLED: bool = True  # 계측용
```

### 새 로그 태그

| 태그 | 용도 |
|------|------|
| `[TBS/coupling-trace] MANIP_ACTIVATE` | 타일 클릭/활성화 시 manipulator 상태 |
| `[TBS/coupling-trace] ORBIT_CTX` | Persp USD 변경 직후 active tile·native API |
| `[TBS/coupling-report] stage-lux` | main vs aux UsdLux 경로 diff |
| `[TBS multi-sim] aux 조명 동기화` | CopySpec 완료·copied 수 |

---

## 권장 다음 단계 (조사만, 패치 아님)

1. **Kit 설치 경로**에서 `omni.kit.manipulator.camera` / `omni.kit.viewport.window` **바이너리 역추적 또는 NVIDIA 소스 요청** — Q1 내부 구현 확정
2. **조사 전용 계측** (완화 패치와 분리): Orbit 1회에 `api.id` / `manip.model` / `UsdNotice stage_id` 동시 1줄 로그
3. **USD 비교**: master_1 vs master_2 `UsdLux` / `RenderSettings` prim diff (DomeLight 복제 전략)
4. **NVIDIA에 질문**: ViewportWindow.get_frame 내 embedded ViewportWidget N개 + 독립 ctx orbit — supported?

---

## 관련 문서

- **[통합 가이드 (단일 문서)](tbs_control_2_viewport_widget_split_complete_ko.md)** — 독립 Orbit 성공 원인·8단계 구현·코드·패치·검증

## 참고 URL (공식)

- https://docs.omniverse.nvidia.com/kit/docs/omni.kit.viewport.docs/latest/viewport_api.html
- https://docs.omniverse.nvidia.com/kit/docs/omni.kit.viewport.docs/latest/widget.html
- https://docs.omniverse.nvidia.com/kit/docs/omni.kit.viewport.docs/latest/camera_manipulator.html
- https://docs.omniverse.nvidia.com/kit/docs/omni.kit.viewport.window/latest/omni.kit.viewport.window/omni.kit.viewport.window.ViewportWindow.html
- https://docs.omniverse.nvidia.com/kit/docs/omni.kit.widget.viewport/latest/Overview.html
- https://github.com/NVIDIA-Omniverse/kit-extension-sample-ui-scene
