# TBS Control 2 개발자 매뉴얼

Kit 확장 `morph.tbs_control_2` — 보고서용 요점 정리.

- 패키지: `source/extensions/morph.tbs_control_2`
- Python: `morph/tbs_control_2/`
- 앱: `source/apps/morph.editor.kit` (`"morph.tbs_control_2" = {}`)
- 스트리밍: `source/apps/morph.editor_streaming.kit`

---

## 1. 개요

TBS(장비) USD를 로드하고, **SimPy 공정 시뮬 → 프리런 타임라인 → JSON 애니 재생**을 화면 단위로 돌린다. 웹(HyView)은 livestream T2V/V2T로 제어한다.

| 구분 | 내용 |
|------|------|
| 진입점 | `extension.py` → `control_window.py` |
| 공정 | `simulation_engine.py` + `prerun_plan_ssot.py` |
| 재생 | `control_sim_prerun_playback.py` / `control_sim_screen_playback.py` |
| 애니 | `tbs_lam_sequence_engine.py` (JSON step) |
| 웹 | `morph.hyview_messaging` (T2V/V2T) |

---

## 2. 개발환경 요약

| 항목 | 내용 |
|------|------|
| 언어 | Python 3.12 (Omniverse Kit 내장) |
| 앱 프레임워크 | NVIDIA Omniverse Kit 109.0.3 |
| UI | `omni.ui` |
| 3D/씬 | USD (`omni.usd`, pxr), RTX Viewport |
| 타임라인 | `omni.timeline` |
| 그래프 | OmniGraph (`omni.graph.*`) — 에셋 bake용 |
| 스트리밍 | `omni.kit.livestream.app` / WebRTC |

### 주요 라이브러리

| 라이브러리 | 용도 |
|------------|------|
| **SimPy** | 이산사건 공정 시뮬 (`Environment`, `Resource`, `timeout`) |
| **openpyxl** | 시뮬 로그 Excel 내보내기 |
| **pxr (USD)** | Stage/Prim/Attribute, TIMESAMPLES |
| **omni.kit.pipapi** | (레거시) pip 설치 경로 — 현재는 vendor 우선 |

Kit Python은 시스템 pip와 분리되어 있다. 네트워크/`pipapi` 없이 쓰려면 아래 **simpy_vendor** 를 쓴다.

---

## 3. simpy_vendor 적용 가이드

**목적:** `pip install simpy` 없이 확장 폴더만으로 SimPy를 import 한다.

### 폴더

```
source/extensions/morph.tbs_control_2/
  simpy_vendor/
    simpy/                 ← 패키지 본체
    simpy-*.dist-info/     ← (있으면) 메타
```

### 초기 적용 (한 번만)

1. pip가 되는 PC에서 wheel을 받는다.  
   `pip download simpy -d ./_tmp_simpy`
2. `.whl` 을 zip으로 풀어 `simpy/` 가 나오게 한다.
3. 그 내용을 `morph.tbs_control_2/simpy_vendor/` 아래로 복사한다.
4. Git에 포함하여 Kit 실행 PC에는 pip가 필요 없다.

### 코드에서 쓰는 방법

모듈 최상단(SimPy import **전**)에 vendor 경로를 넣는다.

```python
import sys
from pathlib import Path

_vendor = Path(__file__).resolve().parents[2] / "simpy_vendor"
if _vendor.is_dir() and str(_vendor) not in sys.path:
    sys.path.insert(0, str(_vendor))

import simpy
```

- `parents[2]`: `morph/tbs_control_2/*.py` → 확장 루트.
- `extension.toml` 의 `[python.pipapi]` 는 오프라인·고정 버전일 때 쓰지 않는다 (`use_online_index = false` 또는 requirements 비움).
- 확인: Kit 콘솔에서 `import simpy; print(simpy.__file__)` 가 `simpy_vendor` 아래를 가리키면 성공.

---

## 4. streaming.kit 환경설정

실행 파일: **`morph.editor_streaming.kit`**  
정책 초안: `test.streaming.kit` (직접 실행하지 않음. 내용을 streaming kit에 반영)

| 설정 | 요점 |
|------|------|
| 의존 | `morph.editor` + `omni.kit.livestream.app` |
| 해상도 | 1920×1080, `allowDynamicResize = false` |
| `fastShutdown` | true |
| 확장 검색 | `${root}/source/extensions`, `exts`, `extscache` |
| exclude | developer.bundle, cache_indicator 등 개발 HUD |
| 템플릿 추가 | Viewport 전용 레이아웃, 메뉴바/콘솔/Stage 숨김, `streamingUi = true` |

로컬 개발은 `morph.editor.kit` (`debugMode=true`, `preferLocalVersions=true`).  
스트리밍 배포는 `morph.editor_streaming.kit`.

---

## 5. 외부 연동 (HyView)

Kit HTTP가 아니라 **livestream 메시지**. 계약: `morph.hyview_messaging/hyview_event_contract.py`.

| 목적 | API (이벤트) |
|------|----------------|
| 장비/EP 수 변경 | `T2V_request_eqp_change` → `V2T_response_eqp_change` |
| EBS on/off | `T2V_request_ebs_enable` |
| 시뮬 시작(프리런+재생) | `T2V_request_start_simulation` |
| 직전 프리런으로 재시작 | `T2V_request_restart_simulation` |
| 배속·일시정지 등 | `T2V_request_control_simulation` |
| 시각 이동 | `T2V_request_seek_simulation` |
| 타임테이블 조회 | `T2V_request_time_table` |
| 시각 동기 | `T2V_request_time_sync` |
| 화면 1/2 표시 | `T2V_request_screen_visibility` |

처리 본체: `morph.hyview_messaging/tbs_sim_bridge.py` → `tbs_control_2` 제어창 API.

---

## 6. 파일·폴더 기능

```
morph.tbs_control_2/
├── config/extension.toml          Kit 확장 선언·의존성
├── config/event_animation_*.json  이벤트 → JSON 애니 매핑
├── simpy_vendor/                  SimPy 내장 (pip 불필요)
├── data/
│   ├── sim_sequences/             공정 JSON 애니
│   ├── sim_prerun/                프리런 타임라인 dump
│   └── preextract/                USD 사전 추출 캐시
└── morph/tbs_control_2/           Python 본체
```

---

## 7. 파일별 역할 (요점)

### 기동·UI

| 파일 | 역할 |
|------|------|
| `extension.py` | 확장 on/off, 창 생성, HyView 스트림 잠금 |
| `control_window.py` | 시뮬 모니터·시작/정지·JSON 디스패치·진행현황 |
| `ebs_control_panel_ui.py` | EBS 케이스 시간 설정 UI |
| `ebs_case_models.py` | EBS A/B 설정 모델 |
| `control_sim_bar_graph.py` | 포트 occupancy 막대 |
| `control_sim_timetable_ui.py` | 공정 타임테이블 하이라이트 |
| `sim_multi_view.py` / `_widget.py` | 분할 뷰포트 |

### 공정·재생

| 파일 | 역할 |
|------|------|
| `simulation_engine.py` | SimPy 엔진 (OHT/BP/EP/FOUP, 이벤트 emit) |
| `prerun_plan_ssot.py` | 오프라인 프리런 플래너 (SSOT) |
| `prerun_plan_adapt.py` | 플랜 → 타임라인 아이템 |
| `control_sim_prerun_playback.py` | sim_now 시계, emit |
| `control_sim_screen_playback.py` | 화면별 플레이어 런타임 |
| `control_sim_playback_gate.py` | JSON wall / emit 게이트 |
| `control_sim_playback_plan.py` | 재생 중 occupancy·renewal |
| `playback_schedule.py` / `playback_plan.py` | 스케줄 구조체 |
| `sim_parallel_rails.py` | oht/move 병렬 레일 |
| `sim_control_defaults.py` | 기본 시간·플래그 SSOT |
| `xml_generator.py` | 이벤트 XML 생성/파싱 |

### USD·애니

| 파일 | 역할 |
|------|------|
| `tbs_master_stage.py` | Master USD 합성 |
| `tbs_multi_usd_loader.py` | 다중 USD 로드 |
| `tbs_split_composed_loader.py` | 화면별 aux 컨텍스트 |
| `tbs_instance_registry.py` | 인스턴스 레지스트리 |
| `tbs_runtime_evaluator.py` | Option E virtual_time 평가 |
| `tbs_lam_sequence_engine.py` | JSON 시퀀스 실행 |
| `tbs_playback_scheduler.py` | 인스턴스 재생 스케줄 |
| `translate_animation.py` / `rotate_animation.py` | MOVE/ROTATE |
| `port_lot_visibility.py` | 포트 LOT prim 가시성·FOUP lift |
| `tbs_usd_stage_context.py` | 화면별 USD 컨텍스트 |
| `kit_main_dispatch.py` | 메인 스레드 USD dispatch |
| `hyview_stream.py` | 스트리밍 레이아웃 잠금 |

### 기타

| 파일 | 역할 |
|------|------|
| `tbs_preextract_cache.py` | 로드 가속 캐시 |
| `tbs_data_paths.py` | 확장 data 경로 |
| `json_playback_timing.py` | JSON 길이·renewal 타이밍 |
| `progress_step_state.py` | 진행현황 단계 상태 |
| `sequence_renewal.py` | renewal 마커 |
| `usd_https_asset_fixup.py` | https 텍스처 로컬 캐시 |
