# TBS Control 2 — ViewportWidget 2분할 P0 조사 보고서

> **작성일**: 2026-07-06  
> **최종 갱신**: 2026-07-06 21:40 (코드 수정 + 12:38 로그 분석 반영)  
> **목적**: P0-A(카메라 coupling) · P0-B(렌더 프로필 불일치) 근본 원인 확인 후 수정  
> **코드 기준**: `sim_multi_view_widget.py`, `sim_viewport_coupling_diag.py`, `sim_viewport_rp_diag.py`

---

## ① Orbit 입력 전달 경로

### 코드에서 확인된 구조

```
ViewportWindow (Workspace "Viewport" 탭)
└─ get_frame("morph.tbs_control_2:00_split_viewport_widgets")
    └─ HStack 50:50
        ├─ ZStack [화면1]
        │   ├─ ViewportWidget (viewport_api = morph.tbs_control_2:widget_tile_1)
        │   └─ omni.ui.scene.SceneView  ← ZStack 최상단 (입력 수신)
        │       └─ scene: ViewportCameraManipulator(api=화면1 API)  ← add_scene_view 성공 시
        └─ ZStack [화면2]
            ├─ ViewportWidget (viewport_api = morph.tbs_control_2:widget_tile_2)
            └─ SceneView + ViewportCameraManipulator(api=화면2 API)
```

### Kit 공식 Measure 오버레이 패턴 (레포 내 `omni.kit.tool.measure`)

```
ViewportWindow.get_frame → ZStack → SceneView
  → api.add_scene_view(scene_view)
  → scene 내 Manipulator
```

### Widget 분할에서의 실제 입력 경로 (추정이 아닌 코드 기반)

| 단계 | 화면1/2 공통 | 실패 시 폴백 |
|------|-------------|-------------|
| 마우스 press | `SceneView` (ZStack 최상단) | `ViewportWidget` 또는 `ViewportWindow` 전역 입력 |
| manipulator 부착됨 | `ViewportCameraManipulator` → 해당 타일 `ViewportAPI` | — |
| manipulator **미부착** | 이벤트가 Workspace `ViewportWindow` 네이티브 경로로 전달 | `get_viewport_from_window_name("Viewport")` manipulator |
| 네이티브 manipulator | **default USD context** (`master_1`) 의 `/OmniverseKit_Persp` 수정 | 화면2 orbit이 화면1 카메라를 움직이는 현상과 일치 |

**결론**: Orbit은 타일별 `SceneView` → `ViewportCameraManipulator(api=타일API)` 경로로 가야 한다. manipulator 미부착·네이티브 manipulator 활성 시 **default context 카메라**가 수정된다.

---

## ② CameraManipulator 생성 여부

| 타일 | 생성 시점 | 저장 위치 | READY 로그 |
|------|----------|----------|------------|
| 화면1 | `_ensure_tile_manipulator` — projection 유효 + `add_scene_view` 후 | `rec["camera_manipulator"]` | `manipulator attached tile='Viewport'` 또는 **무로그(실패)** |
| 화면2 | 동일 (deferred create + connect-aux 후) | `rec["camera_manipulator"]` | 동일 |

`_ensure_tile_manipulator` 선행 조건 (`sim_multi_view_widget.py`):

1. `rec["camera_manipulator"]` 없음
2. `manip_pending=True`
3. `api` / `scene_view` 존재
4. `_api_ready_for_manipulator` — resolution ≥ 8×8
5. **`_api_projection_ready`** — projection 행렬 비영
6. stage에 `camera_path` prim 유효
7. `_register_tile_scene_view` → `api.add_scene_view(scene_view)` 성공

**aux 타일 fps=0 / projection 미준비** 시 ⑤에서 차단되어 manipulator가 영구 미부착 가능.

---

## ③ `scene_view.camera_model=None` 인 이유

### 진단 코드 오류

`sim_viewport_rp_diag.py`는 `scene_view.camera_model`을 기록한다.

`_attach_camera_manipulator` 주석 (코드 명시):

> Kit `add_scene_view` 가 `SceneCameraModel`(view/projection) 을 **`scene_view.model`** 에 둔다.  
> `scene_view.model = manip.model` 로 덮으면 projection 동기화가 깨진다.

| 속성 | manipulator 부착 후 기대값 |
|------|---------------------------|
| `scene_view.camera_model` | **None** (정상 — 이 속성을 쓰지 않음) |
| `scene_view.model` | `SceneCameraModel` 또는 유사 (add_scene_view 후) |
| `rec["camera_manipulator"]` | `ViewportCameraManipulator` 인스턴스 |
| `rec["camera_manipulator"].model` | manipulator model (navigation flags) |

**결론**: READY에서 `camera_model=None`은 **manipulator 미연결의 증거가 아님**. `scene_view.model`·`rec["camera_manipulator"]`를 봐야 한다.

---

## ④ Orbit 시 실제 수정되는 Camera Prim

| API | camera_path | stage | orbit 영향 |
|-----|-------------|-------|-----------|
| 화면1 Widget API | `/OmniverseKit_Persp` | `master_1.usd` (default ctx) | 화면1 manipulator 정상 시 여기만 변경 |
| 화면2 Widget API | `/OmniverseKit_Persp` | `master_2.usd` (aux ctx) | 화면2 manipulator 정상 시 여기만 변경 |
| 네이티브 `get_viewport_from_window_name("Viewport")` | `/OmniverseKit_Persp` | **default ctx = master_1** | 화면2 조작이 화면1을 움직이는 coupling |

경로 문자열이 같아도 **USD Stage가 다르면** 독립 prim이다. coupling은 **어느 Stage의 prim이 변하는지**로 판별한다.

**추적**: `sim_viewport_coupling_diag.py` — 양 stage `/OmniverseKit_Persp` xform UsdNotice + orbit 전후 스냅샷 (`[TBS/coupling-trace]`).

---

## ⑤ Widget2가 Active Viewport인지 여부

| API | Widget 분할 시 의미 |
|-----|-------------------|
| `get_active_viewport()` | Workspace **네이티브** Viewport API — embedded Widget #1/#2와 **다를 수 있음** |
| `get_viewport_from_window_name("Viewport")` | 네이티브 창 API (`id` ≠ `widget_tile_1` API) |
| `ViewportWidget.get_instances()` | count=3: #0·#1 native (ctx=""), #2 aux — 네이티브 presenter 숨김 후에도 인스턴스 잔존 |

이전 패치(`focus()`, `enable_input`, `_tbs_active_widget_tile`)는 **Kit 전역 active viewport를 바꾸지 못함**.  
타일 활성화는 **해당 타일 `ViewportCameraManipulator.model` navigation enable** 로만 유효하다.

---

## ⑥ ViewportWindow 내부 Multi Widget 공식 지원 여부

### 레포 내 근거

- `omni.kit.tool.measure`, `morph.tbs_control` overlay: `ViewportWindow.get_frame` + **SceneView 1개** + **네이티브 ViewportWidget 1개** 패턴.
- **동일 `get_frame` 슬롯에 `ViewportWidget` 2개**를 영구 배치한 공식 예제는 **레포에 없음**.
- `VIEWPORT_RP_ISOLATED_WINDOW_TEST`: 독립 `ui.Window`에서는 RP 생성, embedded에서만 실패하는 CASE-1 — **구조적 제약 가능성**.

### 판단

| 항목 | 결론 |
|------|------|
| `get_frame` + HStack + ViewportWidget N개 | 공식 문서·예제 **미명시** |
| RenderProduct / Hydra | 타일별 독립 API id로 **해결됨** |
| Camera input | **타일별 SceneView + ViewportCameraManipulator** 필수; 네이티브 manipulator 완전 차단 필요 |
| 구조적 한계 | 네이티브 단일-input 모델과 충돌 가능 — **우회 가능**, 공식 multi-widget 미지원 |

---

## ⑦ Render Profile이 어디에서 결정되는지

| 요소 | 결정 위치 | 화면1 vs 화면2 차이 원인 |
|------|----------|-------------------------|
| `render_mode` / RTX | `ViewportAPI` | `_copy_visual_render_profile_only`로 복사 — **12:38 로그: API attr DIFF 없음** |
| Grid / ambient | `ViewportAPI` (`show_grid`, `ambient_light_*`) | 복사됨; 배경 톤은 여전히 상이할 수 있음 |
| **객체 셰이딩 (머티리얼)** | **USD Stage 조명** (`UsdLux`) + RTX | **aux stage 조명 부재 시 검은 실루엣** (아래 §⑩) |
| 배경색 / 톤 | Viewport composite + RTX env | 별도 Hydra (`hd_engine` 미공유) |
| IBL / Sky / DomeLight | USD Stage | `_ensure_aux_stage_default_lighting` — 톤 차이 원인이었으나 **객체 가시성에도 필수** |
| Post-process / tone | ViewportWindow / carb settings | Widget API attr만으로 미복사 |
| Presenter | 네이티브 presenter 숨김 + embedded Widget | composite 경로 상이 가능 |

### DomeLight 제거의 트레이드오프 (12:38 회귀 확인)

| 상태 | 화면2 객체 | 화면2 배경/톤 |
|------|-----------|--------------|
| **수정 전** (`_ensure_aux_stage_default_lighting` ON) | **머티리얼·형상 정상** | 화면1과 톤 불일치 (DomeLight aux 전용) |
| **수정 후** (DomeLight 호출 제거) | **검은 실루엣** (스크린샷 21:38) | 그리드는 보이나 객체 미조명 |

**결론**: P0-B “톤 동일”을 위해 DomeLight를 제거한 것은 **객체 렌더링 회귀**를 유발했다.  
다음 단계는 “DomeLight 없이”가 아니라 **화면1과 동일한 조명 환경을 aux에 복제**하거나, **ViewportAPI ambient만으로 RTX 셰이딩이 되는지** 별도 검증이 필요하다.

---

## ⑧ 현재 구조 자체가 공식 지원인지 여부

| 질문 | 답 |
|------|-----|
| ViewportWindow 1개 + get_frame HStack + ViewportWidget 2개 | **비공식** — Kit 예제에 없음, 동작은 API id·Hydra 분리로 **부분 달성** |
| Camera coupling | manipulator **양쪽 부착 성공**(12:38)이나 orbit 시 **main stage 카메라도 동시 변경** — **부분 미해결** |
| Render 동일 | ViewportAPI attr는 일치하나 **Stage 조명·RTX 셰이딩**이 분리 — DomeLight 제거로 **객체 검정 회귀** |

---

## ⑨ 2026-07-06 코드 수정 내역 (P0 패치)

> GPT 지시: 추측 패치 중단 → 조사 후 근본 수정. 아래는 **실제 적용된 diff** 요약.

### 신규 파일

| 파일 | 역할 |
|------|------|
| `sim_viewport_coupling_diag.py` | `[TBS/coupling-report]`, `[TBS/coupling-trace]`, UsdNotice 카메라 추적 |
| `docs/tbs_control_2_viewport_coupling_investigation_ko.md` | 본 보고서 |

### `sim_multi_view_widget.py` 변경

| 변경 | 내용 |
|------|------|
| **P0-A** | `_disable_native_viewport_navigation_permanent`, `_activate_tile_manipulator_only` — focus/enable_input 제거 |
| **P0-A** | `_api_projection_ready`: RenderProduct 존재 시 manipulator 부착 허용 |
| **P0-A** | ZStack 명시 (ViewportWidget 아래, SceneView 위) |
| **P0-A** | `_bind_tile_manipulator_activation` — SceneView press/hover 시 타일 manipulator만 활성 |
| **P0-A** | `_start_native_viewport_input_guard` / nav hold polling **제거** (`apply_split_widget_navigation` 단순화) |
| **P0-A** | `assign_widget_split_cameras` 12회 폴링 → `_bind_widget_split_cameras_once` 1회 |
| **P0-B** | `_copy_viewport_render_profile`(hydra 공유) → `_copy_visual_render_profile_only` |
| **P0-B** | `_RENDER_PROFILE_ATTRS` 확장 + `_copy_matching_viewport_display_attrs` |
| **P0-B** | **`_ensure_aux_stage_default_lighting` 호출 제거** ← **객체 검정 회귀 원인** |
| **P0-B** | aux connect 시 `ambient_light_intensity` 0.25 강제 상향 제거 |
| **finalize** | bootstrap/kick 루프 축소; READY 시 coupling diag 1회 |

### `sim_control_defaults.py`

```python
VIEWPORT_COUPLING_DIAG_ENABLED: bool = True
```

### `sim_viewport_rp_diag.py`

- `scene_view.model` 로깅 추가 (`camera_model=None`은 정상일 수 있음)

### 제거·축소한 (효과 없었던) 패치

- `_wire_tile_input`의 widget/frame 다중 타겟 + focus
- `_start_native_viewport_input_guard` post_update 루프
- `apply_split_widget_navigation` hold_ticks 재적용 루프
- `assign_widget_split_cameras` async 폴링
- finalize 내 `_bootstrap_tile_viewport_render` 반복

---

## ⑩ 2026-07-06 12:38 실행 로그 분석

### 인프라 — 정상

| 항목 | 화면1 (`Viewport`) | 화면2 (`TBS_SimSplit_1`) |
|------|-------------------|-------------------------|
| RenderProduct | `ViewportTexture_1` ✓ | `ViewportTexture_2` ✓ |
| `api.id` | `morph.tbs_control_2:widget_tile_1` | `morph.tbs_control_2:widget_tile_2` |
| `render_mode` | `RealTimePathTracing` | `RealTimePathTracing` |
| `hydra_engine` | `rtx` | `rtx` |
| Stage | `master_1.usd` id=`0x1dc45d12810` | `master_2.usd` id=`0x1cd65cc9bd0` |
| stage isolation | — | **OK** (로그 명시) |
| `create_total` | 2 (기대값 일치) | |

### P0-A Camera — 부분 개선, coupling 잔존

**개선된 점 (이전 대비)**

```
[TBS/multi-sim] manipulator attached tile='Viewport' scene_view.model=SceneCameraModel@... manip.model=CameraManipulatorModel@...
[TBS/multi-sim] manipulator attached tile='TBS_SimSplit_1' scene_view.model=SceneCameraModel@... manip.model=CameraManipulatorModel@...
```

READY `[TBS/coupling-report]`:

| 필드 | 화면1 | 화면2 |
|------|-------|-------|
| `camera_manipulator` | `ViewportCameraManipulator@...` ✓ | `ViewportCameraManipulator@...` ✓ |
| `scene_view.model` | `SceneCameraModel@...` ✓ | `SceneCameraModel@...` ✓ |
| `scene_view_registered` | True | True |
| `manip_pending` | False | False |

→ **③ 조사 결론 확인**: `camera_model=None`은 정상; manipulator는 **양쪽 모두 부착됨**.

**미해결 / 주의**

| 관찰 | 의미 |
|------|------|
| `get_active_viewport()` = `ProxyType@0x1cd8e71ea70` (native #0) | embedded 타일이 active viewport가 **아님** |
| `native_api` ≠ embedded tile API | 네이티브 경로 잔존 |
| `ViewportWidget.get_instances` #1 api = 화면1 embedded | native #1이 화면1 embedded API와 **동일 id** 공유 가능성 |
| orbit 초반: main·aux **양쪽** `USD_CHANGED` 교차 출력 | coupling **잔존** (양 stage Persp 동시 변경) |
| orbit 후반: **main stage만** 연속 `USD_CHANGED` | aux 조작 시에도 main이 변하는 구간 존재 |

**진단 버그**: `[TBS/coupling-trace] USD_CHANGED label=Usd.Stage.Open(...)` — notice 콜백이 `main`/`aux` 라벨 대신 Stage open 이벤트 문자열을 출력. **경로 필터(`/OmniverseKit_Persp` 한정) 보강 필요**.

### P0-B Render — 회귀 (객체 검정)

| 관찰 | 분석 |
|------|------|
| `[TBS/coupling-report] -- render-profile-diff --` **DIFF 줄 없음** | ViewportAPI 시각 attr는 main↔aux **일치** |
| 화면2 `api.fps=0` (READY), 화면1 `fps≈0.33` | aux 렌더 펌프 **미가동** — shading 업데이트 지연 가능 |
| 스크린샷: 화면2 객체 **검은 실루엣**, 그리드만 보임 | **Stage 조명 부재** + RTX PathTracing → unlit silhouette |
| `_ensure_aux_stage_default_lighting` 제거 후 발생 | 이전에는 DomeLight로 **객체는 정상**, 톤만 다름 |

**회귀 원인 가설 (로그·스크린샷 일치)**

1. `master_2.usd` / aux context에 **유효 UsdLux 없음** (또는 RTX가 인식 못함)
2. 화면1은 default context + Kit viewport 기본 ambient/환경으로 셰이딩됨
3. DomeLight 제거로 aux의 **유일한 인위 조명** 소실 → 객체 검정
4. ViewportAPI attr 복사만으로는 **Stage 레벨 조명**을 대체 못함

### 권장 다음 수정 — **§12 패치 적용됨 (2026-07-06)**

| 우선순위 | 작업 | 상태 |
|----------|------|------|
| P0-B | aux stage 조명 — 화면1 UsdLux session 복제 | **적용** `_sync_aux_stage_lighting_from_main` |
| P0-B | aux `fps=0` bootstrap | **기존 유지** (READY 후 16프레임) |
| P0-A | coupling-trace Persp 필터 + label | **적용** |
| P0-A | orbit 시 manipulator.model 로그 | **적용** `MANIP_ACTIVATE` / `ORBIT_CTX` |
| P0-A | native camera bindings 차단 | **적용** carb `bindings/camera` → `{}` |

---

## 수정 방향 (본 보고서 이후 코드 변경) — **갱신**

### P0-A Camera (12:38 + §12 패치)

- [x] manipulator 양쪽 부착
- [x] `scene_view.model` / `camera_manipulator` READY 확인
- [x] coupling-trace Persp 필터 + `MANIP_ACTIVATE` / `ORBIT_CTX`
- [x] native camera bindings carb 비활성
- [ ] orbit 시 **한쪽 stage만** 변경 — **런타임 검증 필요**

### P0-B Render (§12 패치)

- [x] ViewportAPI attr 동기화 (DIFF 없음)
- [x] 화면1 UsdLux → aux session 복제 + fallback DomeLight
- [x] `stage-lux` diff 로그
- [ ] aux fps>0 · 픽셀 톤 동일 — **런타임 검증 필요**

---

## 관련 로그 태그

| 태그 | 용도 |
|------|------|
| `[TBS/coupling-report]` | READY 시 manipulator·model·native API 상태 |
| `[TBS/coupling-trace]` | orbit 전후 양 stage 카메라 xform · `MANIP_ACTIVATE` · `ORBIT_CTX` |
| `[TBS/coupling-report] stage-lux` | main vs aux UsdLux 경로 |
| `[TBS/rp-invest]` | RenderProduct 체인 (기존) |

---

## §11 P0 최종 조사 (Kit 구조) — 코드 수정 없음

**전체 보고서**: [tbs_control_2_viewport_kit_structural_investigation_ko.md](tbs_control_2_viewport_kit_structural_investigation_ko.md)

### 최종 답 (요약)

| # | 질문 | 답 |
|---|------|-----|
| 1 | Manipulator가 전달 API만 쓰는가? | API 계약상 **예**; 내부 `get_active_viewport`는 **소스 미확인**. Coupling은 **native manipulator + 전역 bindings** 가능성 |
| 2 | `get_frame`+HStack+ViewportWidget×2 공식 지원? | **공식 사례 없음** |
| 3 | Coupling 앱 vs Kit? | **Kit 구조적 제약 가능성 높음** (active= native, stage/api 독립 확인됨) |
| 4 | Tone 차이 ViewportAPI? | **아님** — Stage Lux / Hydra / Presenter |
| 5 | 현 구조로 공식 해결? | **문서화된 해법 없음**; `create_viewport_window`는 공식이나 Dock 금지 제약과 충돌 |

증상 완화 패치(`enable_input`, `focus`, polling, DomeLight **무조건 제거** 등)는 재시도하지 않음.  
**통합 가이드 (단일 문서)**: [tbs_control_2_viewport_widget_split_complete_ko.md](tbs_control_2_viewport_widget_split_complete_ko.md)
