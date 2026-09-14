# LAM Control 1 개발자 매뉴얼

Kit 확장 `morph.lam_control_1` — 보고서용 요점 정리.

- 패키지: `source/extensions/morph.lam_control_1`
- Python: `morph/lam_control_1/`
- 앱: `morph.editor.kit` 에서 `"morph.lam_control_1" = {}` 활성화 (`morph.lam_control` 은 끔)
- 웹 HTTP: `morph.lam_web_bridge` (포트 8720)
- HyView: `sk.hyview_messaging` → Federation 시뮬

본 확장은 `morph.tbs_control_*` 를 **import 하지 않는다.**

---

## 1. 개요

LAM 장비 USD를 Master stage에 합성하고, 인스턴스별 **virtual_time(Option E)** 으로 JSON 애니를 재생한다. 공정 입력은 **CSV dwell** 또는 **Federation API** 이다 (SimPy 공정 엔진 없음).

| 구분 | 내용 |
|------|------|
| 진입점 | `extension.py` → `lam_window.py` |
| 시뮬 실행 | `simulation_play.py` → `lam_sequence_engine.py` |
| Federation | `lam_federation_pipeline.py` + `lam_federation_client.py` |
| 원격 세션 | `remote_api.py` (`LamKitSession`) |

---

## 2. 개발환경 요약

| 항목 | 내용 |
|------|------|
| 언어 | Python 3.12 (Omniverse Kit 내장) |
| 앱 프레임워크 | NVIDIA Omniverse Kit 109.0.3 |
| UI | `omni.ui` |
| 3D/씬 | USD (`omni.usd`, pxr), RTX Viewport |
| 네트워크 | `urllib` (표준 라이브러리 HTTP) |
| 설정 | 리포 루트 `.env` (python-dotenv 없음, KEY=VALUE 파싱) |
| 스트리밍 | `omni.kit.livestream.app` / WebRTC |

### 주요 라이브러리

| 라이브러리 | 용도 |
|------------|------|
| **pxr (USD)** | Stage 합성, TIMESAMPLES, visibility |
| **urllib** | Federation POST/GET |
| **json** | 이벤트 시퀀스·CSV 프리런 |
| **SimPy (vendor)** | 본 확장은 공정 엔진에 쓰지 않음. 공용 vendor 패턴은 §3 |

Kit Python은 시스템 pip와 분리되어 있다. 외부 패키지가 필요하면 pip가 아니라 **확장 폴더 vendor** 를 쓴다.

---

## 3. simpy_vendor 적용 가이드

TBS와 **동일한 방식**. pip 없이 패키지를 확장에 넣는다.

### 폴더

```
source/extensions/morph.lam_control_1/
  simpy_vendor/
    simpy/
    simpy-*.dist-info/     ← 있으면
```

### 초기 적용 (한 번만)

1. `pip download simpy -d ./_tmp_simpy`
2. `.whl` 압축 해제 → `simpy/` 디렉터리 확보
3. `morph.lam_control_1/simpy_vendor/` 아래로 복사 후 커밋
4. Kit PC에는 pip/네트워크 불필요

### 코드에서 쓰는 방법

```python
import sys
from pathlib import Path

_vendor = Path(__file__).resolve().parents[2] / "simpy_vendor"
if _vendor.is_dir() and str(_vendor) not in sys.path:
    sys.path.insert(0, str(_vendor))

import simpy
```

- `parents[2]`: `morph/lam_control_1/*.py` → 확장 루트.
- LAM 시뮬 본선은 CSV/Federation 이므로 SimPy import는 **쓸 모듈에만** 넣는다.

---

## 4. streaming.kit 환경설정

실행: **`source/apps/morph.editor_streaming.kit`**  
초안: `test.streaming.kit` (복사해 반영)

| 설정 | 요점 |
|------|------|
| 의존 | `morph.editor` + `omni.kit.livestream.app` |
| 해상도 | 1920×1080, `allowDynamicResize = false` |
| 확장 검색 | `${root}/source/extensions` |
| HyView LAM | streaming kit에서 `sk.hyview_messaging`, `morph.lam_web_bridge` 주석 해제 |
| 템플릿 | Viewport 중심, Kit 기본 패널·메뉴 exclude, `streamingUi = true` |

로컬: `morph.editor.kit` + `app.extensions.debugMode=true` (저장 시 hot reload).

---

## 5. API

항목 = **목적**, API detail = **실제 호출**.

### 5.1 Federation — LOT 이력 조회

| 목적 | 화면별 MCC target 이전 LOT 이력을 받아 웨이퍼 맵·이벤트를 만든다 |
| API detail | `POST {FEDERATION_QUERY_URL}`  (= `.env` 호스트 + `/queries/mcc-target-prev-lot-history/run`) |
| 호출 | `lam_federation_client.fetch_federation_pages` |
| 헤더 | `Content-Type: application/json`, 선택 `Authorization: Bearer …`, `FEDERATION_EXTRA_HEADERS` |
| Body | `limit`, `offset` + 쿼리 조건. `pagination.has_next` 로 페이지 반복 |

### 5.2 Federation — Simulation GET

| 목적 | 실행 ID 기준 시뮬 결과(object 배열)를 받아 재생 계획을 만든다 |
| API detail | `GET {FEDERATION_SIMULATION_GET_BASE_URL}/api/v1/lam/simulations/{exec_id}?offset=&limit=` |
| 호출 | `lam_federation_client.fetch_simulation_get_pages` |
| 헤더 | `Fx-Service-Key`, `Fx-Employee-Key`, `accept` (`lam_sim_control_defaults`) |

### 5.3 HyView livestream (웹 → Kit)

계약: `sk.hyview_messaging/hyview_event_contract.py`  
처리: `lam_handler.py` → `lam_sim_bridge.py` → `lam_federation_pipeline.run_federation_start_simulation`

| 목적 | API detail |
|------|------------|
| Federation fetch + 프리런 + 재생 시작 | `T2V_request_start_simulation` (`configs[0]`=화면1, `configs[1]`=화면2) |
| 시뮬 중지 | `T2V_request_stop_simulation` |
| 화면별 오버레이·배속 | `T2V_control_simulation` (`proc_only`, `show_top_view`, `foup_info_show`, `eqp_info_show`, `wafer_number_show`, `prim_hide`, `speed`) |
| STATUS 패널 스냅샷 통지 (Kit→웹) | `V2T_notify_status_panel` |

### 5.4 LAM HTTP 원격 UI (`morph.lam_web_bridge`)

Kit 세션: `remote_api.get_session()` (`registry` / `scheduler` / `open_master_at_path`)

| 목적 | API detail |
|------|------------|
| 상태 조회 | `GET /api/state` |
| 명령 (USD 로드 등) | `POST /api/command` 예: `{"cmd":"load_usd","path":"..."}` |

호스트 URL은 리포 루트 `.env` (`FEDERATION_QUERY_URL`, `FEDERATION_SIMULATION_GET_BASE_URL`). 오프라인은 `FEDERATION_USE_FIXTURE=True` + `data/federation_fixture/`.

---

## 6. 파일·폴더 기능

```
morph.lam_control_1/
├── config/extension.toml
├── simpy_vendor/                 공용 vendor (pip 불필요)
├── data/
│   ├── csv/                      dwell CSV
│   ├── csv_prerun/               CSV 프리런 dump
│   ├── lam_event_sequences/      pick/place JSON
│   ├── federation_fixture/       API 샘플 JSON
│   ├── preextract/ · stripped_open/
│   └── usd/
└── morph/lam_control_1/
```

---

## 7. 파일별 역할 (요점)

### 기동·UI

| 파일 | 역할 |
|------|------|
| `extension.py` | 확장 기동, `LamKitSession` 등록 |
| `lam_window.py` | 메인 창, Master open, 오버레이 조립 |
| `lam_federation_test_window.py` | Federation GET/POST 테스트 UI |
| `lam_aux_kit_window_ui.py` | 보조 Kit 창 |
| `lam_equipment_floorplan_ui.py` | 평면도 occupancy UI |
| `lam_multi_viewport.py` / `_widget.py` | 듀얼 뷰포트 |
| `lam_csv_viewport_hud.py` | Viewport CSV 미니 패널 |
| `lam_federation_load_hud.py` | Federation 로드 % → 화면별 Play 버튼 |
| `config.py` | `.env` 호스트 로드 |
| `lam_sim_control_defaults.py` | Federation URL·HUD·색 등 SSOT |
| `remote_api.py` | 웹 브리지용 Kit 세션 |

### 시뮬·이벤트

| 파일 | 역할 |
|------|------|
| `simulation_play.py` | CSV/매크로 실행, step runner 연결 |
| `lam_sim_actions.py` | pick/place 매크로 (함수명 = JSON 파일명) |
| `lam_event_sequences.py` | 이벤트 JSON → step 조립, Z MOVE 삽입 |
| `lam_sequence_engine.py` | `LamSequenceRunner` (MOVE/TIMESAMPLES/가시성) |
| `lam_sequence_editor.py` | 시퀀스 편집기 |
| `lam_slot_z_config.py` | 슬롯 Z·prim 경로 SSOT |
| `lam_wafer_prim_paths.py` | 웨이퍼 prim 경로 |
| `lam_csv_prerun_playback.py` | CSV 프리런 시계 |
| `lam_csv_occupancy_scheduler.py` | occupancy 스케줄 |
| `lam_aligner_process_rules.py` | Aligner 공정 규칙 |
| `lam_buffer_return_rules.py` | 버퍼 복귀 규칙 |

### Federation

| 파일 | 역할 |
|------|------|
| `lam_federation_client.py` | HTTP POST/GET, pagination |
| `lam_federation_pipeline.py` | fetch → 파싱 → 프리런 → 화면별 재생 |
| `lam_api_timeline_parser.py` | API object 배열 → 타임라인 |
| `lam_hyview_stream.py` | 스트리밍 레이아웃 잠금 |

### USD·재생 코어

| 파일 | 역할 |
|------|------|
| `lam_master_stage.py` | Master USD |
| `lam_multi_usd_loader.py` | 다중 USD 참조 |
| `lam_split_composed_loader.py` | 화면별 합성 로드 |
| `lam_instance_registry.py` | 인스턴스 SSOT |
| `lam_playback_scheduler.py` | 재생 API |
| `lam_runtime_evaluator.py` | Option E reauthor |
| `lam_translate_animation.py` / `lam_rotate_animation.py` | MOVE/ROTATE |
| `kit_main_dispatch.py` | 메인 스레드 USD dispatch |
| `lam_preextract_cache.py` | 로드 캐시 |
| `lam_usd_strip_external.py` | 외부 참조 제거 |

### 오버레이·표시

| 파일 | 역할 |
|------|------|
| `lam_viewport_overlay_config.py` / `_state.py` | 오버레이 SSOT·상태 |
| `lam_viewport_status_panel.py` | 2D STATUS HUD |
| `lam_viewport_foup_status_3d.py` | FOUP 3D 패널 |
| `lam_viewport_device_labels_3d.py` | 기기 라벨 |
| `lam_wafer_viewport_labels.py` | 웨이퍼 번호 |
| `lam_foup_lot_display.py` | FOUP lot 텍스트 |
| `lam_play_prim_hide.py` / `lam_play_camera_fly.py` | Play 시 숨김·카메라 |
| `lam_visibility_occupancy_bus.py` | 가시성 occupancy 버스 |
