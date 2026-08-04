# LAM Control — 뷰포트 카메라·라이트 Emissive 요구사항 정리

> **작성 목적**: 구현 전 이해 확인·일괄 수정용 SSOT  
> **작성일**: 2026-07-06  
> **최종 갱신**: 2026-07-06 01:22 (사용자 답변 반영)  
> **설정 SSOT**: `morph/lam_control/lam_viewport_overlay_config.py`  
> **관련 구현**: `lam_viewport_top_view.py`, `lam_play_camera_fly.py`, `lam_viewport_overlay_state.py`, `simulation_play.py`

---

## 1. 요약

| # | 영역 | 핵심 |
|---|------|------|
| A | 탑뷰 / Play 카메라 | preset 좌표 **또는** USD Camera prim — config 플래그로 선택 |
| B | 신호등 라이트 | 3색 shader Emissive 중 1개만 랜덤 ON, 주기·마스터·재생 연동 플래그 |

**플래그 규칙 (카메라)**

| `*_USE_PRESET_COORDS` | 동작 |
|------------------------|------|
| **`True` (기본)** | **지금과 100% 동일** — 뷰저장 eye/target preset |
| **`False`** | config에 적은 **USD Camera prim** 뷰 사용 (경로는 실무에서 채움) |

---

## 2. 확정 사항 (사용자 답변, 2026-07-06)

| # | 항목 | 확정 |
|---|------|------|
| 1 | **Perspective** | 뷰포트 상단 메뉴의 **기본 Perspective** (Alt+P, 자유 시점). **사용자가 만든 Camera prim이 아님** |
| 2 | Camera prim 전환 시 이동 | **부드러운 fly** 필요 (Play·탑뷰 모두 camera 모드에서도) |
| 3 | Camera prim 경로 | config 기본값은 **빈 문자열 `""`** — 현장 USD 경로는 사용자가 직접 입력 |
| 4 | Emissive 속성명 | Property **「Enable Emission」** 체크 UI만 확인 — 속성 키는 **코드 자동 탐색** (§5.5) |
| 5 | 라이트 마스터 OFF | 타이머 중지, **마지막 ON/OFF 상태 유지** |
| 6 | 라이트 타이머 시작 | **기본: 앱(stage) 로드 직후** + config 플래그로 **「재생 중에만」** 모드 지원 (정지·일시정지·시뮬 종료 시 타이머 정지) |
| 7 | `*_USE_PRESET_COORDS` 기본값 | **`True`** = 현재 preset 동작, **`False`** = USD Camera 모드 |

---

## 3. 현재 구현 (As-Is)

### 3.1 설정 (`lam_viewport_overlay_config.py`)

- `PLAY_CAMERA_PRESET` / `TOP_VIEW_PRESET` — `eye_xyz`, `target_xyz`, `up_xyz`
- 「뷰 저장」→ 콘솔에 preset 붙여넣기 블록 출력

### 3.2 탑뷰 (`lam_viewport_top_view.py`)

- ON: preset을 session 카메라 transform에 적용 + 조작 잠금
- OFF: **조작만 해제**, 시점 유지 (Perspective 복귀 없음)

### 3.3 Play fly (`lam_play_camera_fly.py`)

- ON + Play: 현재 뷰 → preset으로 **smooth fly** (`PLAY_CAMERA_FLY_DURATION_SEC`)
- session 카메라 prim transform + COI 수정 방식

### 3.4 라이트 Emissive

- **미구현**

---

## 4. 요구사항 A — 카메라

### 4.1 Config 스키마 (확정안)

```python
# ---------------------------------------------------------------------------
# 탑뷰 — preset 좌표 vs USD Camera prim
# True(기본)=TOP_VIEW_PRESET 사용(현재와 동일), False=camera_top_temp prim
# ---------------------------------------------------------------------------
TOP_VIEW_USE_PRESET_COORDS: bool = True
TOP_VIEW_CAMERA_PRIM_PATH: str = ""   # 예: "/World/camera_top_temp" — 현장에서 채움

TOP_VIEW_PRESET_ENABLED: bool = True
TOP_VIEW_PRESET: PlayCameraPresetSpec = ...

# ---------------------------------------------------------------------------
# Play fly — preset 좌표 vs USD Camera prim
# True(기본)=PLAY_CAMERA_PRESET fly(현재와 동일), False=camera_01 prim
# ---------------------------------------------------------------------------
PLAY_CAMERA_USE_PRESET_COORDS: bool = True
PLAY_CAMERA_PRIM_PATH: str = ""      # 예: "/World/camera_01" — 현장에서 채움

PLAY_CAMERA_PRESET_ENABLED: bool = True
PLAY_CAMERA_PRESET: PlayCameraPresetSpec = ...
# PLAY_CAMERA_FLY_DURATION_SEC — camera 모드 fly에도 동일 duration 사용
```

**Perspective 복귀용 별도 prim 경로 config는 없음** — Kit 기본 Perspective 모드로 전환 (뷰포트 카메라 메뉴의 Perspective / Alt+P 와 동일 개념).

### 4.2 탑뷰 동작표

| | `USE_PRESET_COORDS=True` | `USE_PRESET_COORDS=False` |
|--|--------------------------|---------------------------|
| **체크 ON** | `TOP_VIEW_PRESET` 적용 + 조작 잠금 (현재와 동일) | `TOP_VIEW_CAMERA_PRIM_PATH` 카메라 뷰로 **fly 전환** + 조작 잠금 |
| **체크 OFF** | 조작만 해제, 시점 유지 (현재와 동일) | **Perspective(기본 뷰)** 로 전환 + 조작 가능 |

- Camera prim 경로가 비어 있으면: camera 모드 비활성 또는 경고 로그 (구현 시 정의)

### 4.3 Play 시점 동작표

| | `USE_PRESET_COORDS=True` | `USE_PRESET_COORDS=False` |
|--|--------------------------|---------------------------|
| Play 시작 | 현재 뷰 → preset **smooth fly** (현재와 동일) | 현재 뷰 → `PLAY_CAMERA_PRIM_PATH` 카메라 뷰로 **smooth fly** |
| 일시정지 후 이어하기 | fly 생략 (현재와 동일) | fly 생략 (현재와 동일) |

### 4.4 CSV 정지·초기화

| 조건 | 동작 |
|------|------|
| 재생이 **`PLAY_CAMERA_USE_PRESET_COORDS=False`** (camera 모드)였음 | 정지/초기화 시 **Perspective(기본 뷰)** 로 전환 |
| preset 모드였음 | 기존 정지 동작 유지 (Perspective 강제 복귀 없음) |

연동: `request_stop_csv_playback`, `_on_csv_stop_reset_clicked`, 시뮬 종료 경로.

### 4.5 Perspective 구현 메모 (개발용)

사용자 관점: 스크린샷의 **Perspective** 메뉴 항목 = 자유 orbit 가능한 기본 3D 뷰.

구현 후보 (코드 작성 시 선택):

- Viewport API / Kit command로 **Perspective 카메라 모드** 전환 (Alt+P 와 동등)
- 내부적으로 session `OmniverseKit_Persp` 를 쓰더라도, 사용자에게는 「특정 prim 이름」이 아닌 **기본 Perspective** 로 동작하면 됨

### 4.6 Camera prim fly 구현 메모

preset fly와 동일하게:

1. 현재 뷰 snapshot (eye/target)
2. 대상 Camera prim에서 eye/target snapshot 읽기 (`UsdGeom.Camera` world transform + COI)
3. `PLAY_CAMERA_FLY_DURATION_SEC` 동안 보간
4. camera 모드 탑뷰 ON 시: fly 완료 후 `camera_path`를 해당 prim으로 고정 + 조작 잠금

---

## 5. 요구사항 B — 신호등 라이트 Emissive

### 5.1 대상 shader 경로 (기본값)

| 색 | 경로 |
|----|------|
| 빨강 | `/Looks/Light_Red_01/Light_Red` |
| 초록 | `/Looks/Light_Green_01/Light_Green` |
| 노랑 | `/Looks/Light_Yellow_01/Light_Yellow` |

### 5.2 동작

- **30~45초** (config 범위)마다 3개 중 **랜덤 1개**만 Enable Emission ON, 나머지 2개 OFF
- 매 tick마다 정확히 1개만 켜짐 (0개·2개 이상 동시 ON 금지)

### 5.3 Config 스키마 (확정안)

```python
# ---------------------------------------------------------------------------
# 신호등 라이트 Emissive 랜덤
# ---------------------------------------------------------------------------
TRAFFIC_LIGHT_EMISSIVE_ENABLED: bool = True
# False → 타이머 중지, 마지막 emission 상태 유지 (USD 되돌리지 않음)

TRAFFIC_LIGHT_EMISSIVE_INTERVAL_MIN_SEC: float = 30.0
TRAFFIC_LIGHT_EMISSIVE_INTERVAL_MAX_SEC: float = 45.0

TRAFFIC_LIGHT_SHADER_PATHS: Tuple[str, str, str] = (
    "/Looks/Light_Red_01/Light_Red",
    "/Looks/Light_Green_01/Light_Green",
    "/Looks/Light_Yellow_01/Light_Yellow",
)

# False(기본)=앱(stage) 로드 직후 타이머 시작, 재생 여부와 무관
# True=CSV 재생 중에만 타이머 동작, 정지·일시정지·시뮬 종료 시 타이머 정지
TRAFFIC_LIGHT_EMISSIVE_ONLY_DURING_PLAYBACK: bool = False

# 비우면 Enable Emission 속성 자동 탐색 (기본). 특정 USD만 고정할 때만 채움.
TRAFFIC_LIGHT_EMISSIVE_ENABLE_ATTR: str = ""
```

### 5.4 타이머 시작·정지 규칙

| `ONLY_DURING_PLAYBACK` | 시작 | 정지 |
|------------------------|------|------|
| **`False` (기본)** | stage 준비 후(앱 로드 직후) | `TRAFFIC_LIGHT_EMISSIVE_ENABLED=False` 일 때만 |
| **`True`** | CSV **재생 시작** 시 | **정지**, **일시정지**, **시뮬레이션 종료** 시 |

`TRAFFIC_LIGHT_EMISSIVE_ENABLED=False` 이면 어떤 모드든 타이머 미동작, **마지막 emission 상태 유지**.

### 5.5 Enable Emission — Property UI vs USD 속성명

**사용자 확인 (2026-07-06)**  
Property 창에서 **「Enable Emission」** 라벨 옆 **체크박스** 형태로만 확인됨. USD 속성의 정확한 키 이름은 **모름** → **추가 입력 불필요**.

UI 라벨과 stage 내부 속성 이름은 다를 수 있다. 예:

| 사용자가 보는 것 | 코드가 쓸 수 있는 것 (파일마다 다름) |
|------------------|--------------------------------------|
| Emissive → **Enable Emission** ✓ | `inputs:enable_emission`, `inputs:emissive_enable` 등 |

**구현方針 (확정)** — 사용자 조사 없이 코드가 처리:

1. `TRAFFIC_LIGHT_SHADER_PATHS` 각 prim에 대해  
2. `inputs:*` 및 bool 계열 속성 중 이름에 `emission` / `emissive` / `enable` 이 포함된 항목 **자동 탐색**  
3. Kit `UsdShade` / material binding으로 연결된 shader input 우선  
4. 탐색 실패 시 해당 prim만 스킵 + `[LAM/TrafficLight]` 로그  
5. (선택) config override — 필요 시에만 추가:

```python
# 비우면 자동 탐색 (기본). 특정 현장 USD만 고정할 때만 채움.
TRAFFIC_LIGHT_EMISSIVE_ENABLE_ATTR: str = ""
```

**사용자 할 일**: shader **prim 경로**만 config에 맞게 유지. Enable Emission 속성명은 **알 필요 없음**.

---

## 6. 수정 예정 파일

| 파일 | 내용 |
|------|------|
| `lam_viewport_overlay_config.py` | 카메라·라이트 플래그·경로 |
| `lam_play_camera_fly.py` | preset fly / camera prim fly, Perspective 전환 유틸 |
| `lam_viewport_top_view.py` | 탑뷰 preset vs camera, OFF 시 Perspective |
| `lam_viewport_overlay_state.py` | 탑뷰 OFF 분기 |
| `simulation_play.py` | 정지 시 Perspective, 재생 lifecycle → 라이트 타이머 |
| `lam_traffic_light_emissive.py` (신규) | 랜덤 emission 타이머 |
| `extension.py` | stage ready 시 라이트 타이머 (playback-only 아닐 때) |

---

## 7. 구현 순서 (일괄 수정 시)

1. `lam_viewport_overlay_config.py` — 확정 스키마 반영  
2. 공통: Perspective 전환, Camera prim snapshot, fly 보간  
3. `lam_viewport_top_view.py` — 탑뷰 분기  
4. `lam_play_camera_fly.py` — Play fly 분기 + 정지 시 Perspective  
5. `lam_traffic_light_emissive.py` — emission 랜덤 + playback 연동  
6. `extension.py` / `simulation_play.py` — 타이머 lifecycle  
7. UI 툴팁 갱신 (`simulation_play.py`)

---

## 8. 관련 문서

- `docs/LAM_Viewport_Overlay_Maintenance_Guide.md`
- `docs/LAM_Control_Operator_Technical_Guide.md`
- `morph/lam_control/lam_viewport_overlay_config.py`

---

*확정 사항 반영 완료. 다음 단계: 위 스키마대로 일괄 코드 수정.*
