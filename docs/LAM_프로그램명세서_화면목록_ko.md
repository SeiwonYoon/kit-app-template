# 장비시뮬레이션 가시화 프로그램 명세서 (화면·프로그램 목록)

**시스템명:** 장비시뮬레이션 가시화 프로그램  
**패키지(확장):** `morph.lam_control_1`

---

| 업무영역 | 업무기능 | 화면명/프로그램명 | 화면 ID | 유형 | 기능구분 | 화면소스파일 경로 | 화면소스파일명 | Task 파일경로 | Task 파일명 | Handler 파일경로 | Handler 파일명 |
|----------|----------|-------------------|---------|------|----------|-------------------|----------------|---------------|-------------|------------------|----------------|
| 장비 시뮬 가시화 | Master USD 관리 | LAM Multi-USD Load | LAM-USD-001 | 온라인 | 조회/입력/실행 | `morph/lam_control_1/` | `lam_window.py` | — | — | — | — |
| 장비 시뮬 가시화 | CSV 시뮬 재생 | LAM CSV 시뮬 재생 (화면1) | LAM-CSV-001 | 온라인 | 조회/입력/실행 | `morph/lam_control_1/` | `simulation_play.py` (`LamSimulationCsvPlayWindow`) | — | — | — | — |
| 장비 시뮬 가시화 | CSV 시뮬 재생 | LAM CSV 시뮬 재생 (화면2) | LAM-CSV-002 | 온라인 | 조회/입력/실행 | `morph/lam_control_1/` | `simulation_play.py` (`LamSimulationCsvPlayWindow`) | — | — | — | — |
| 장비 시뮬 가시화 | Viewport HUD | LAM CSV Viewport Controls HUD | LAM-HUD-001 | 온라인 | 조회/입력/실행 | `morph/lam_control_1/` | `lam_csv_viewport_hud.py` | — | — | — | — |
| 장비 시뮬 가시화 | Federation API 테스트 | LAM Federation API Test | LAM-FED-001 | 온라인 | 조회/입력/실행/출력 | `morph/lam_control_1/` | `lam_federation_test_window.py` | — | — | — | — |
| 장비 시뮬 가시화 | JSON 체인 테스트 | LAM JSON Chain Tester | LAM-JSON-001 | 온라인 | 조회/입력/실행 | `morph/lam_control_1/` | `lam_json_test_window.py` | — | — | — | — |
| 장비 시뮬 가시화 | 시퀀스 편집 | LAM Sequence Editor | LAM-SEQ-001 | 온라인 | 조회/입력/수정/실행 | `morph/lam_control_1/` | `lam_sequence_editor.py` | — | — | — | — |
| 장비 시뮬 가시화 | 듀얼 뷰포트 (Dock) | LAM Multi Viewport | LAM-VP-001 | 온라인 | 조회/실행 | `morph/lam_control_1/` | `lam_multi_viewport.py` | — | — | — | — |
| 장비 시뮬 가시화 | 듀얼 뷰포트 (Widget) | LAM Multi Viewport Widget | LAM-VP-002 | 온라인 | 조회/실행 | `morph/lam_control_1/` | `lam_multi_viewport_widget.py` | — | — | — | — |
| 장비 시뮬 가시화 | 화면 표시 전환 | LAM 화면1·2 표시 제어 | LAM-SCR-001 | 온라인 | 입력/실행 | `morph/lam_control_1/` | `lam_screen_visibility.py` | — | — | — | — |
| 장비 시뮬 가시화 | HyView 메시징 | LAM HyView T2V/V2T 시뮬 시작 | LAM-HYV-001 | 온라인 | 입력/실행/출력 | `sk/hyview_messaging/` | `lam_sim_bridge.py` | — | — | `sk/hyview_messaging/extension_handlers/` | `lam_handler.py` |
| 장비 시뮬 가시화 | 확장 진입 | LAM Control Extension | LAM-EXT-001 | 온라인 | 실행 | `morph/lam_control_1/` | `extension.py` | — | — | — | — |
| 장비 시뮬 가시화 | 공통 모듈 | Federation 파이프라인 | — | 공통모듈 | 실행 | `morph/lam_control_1/` | `lam_federation_pipeline.py` | — | — | — | — |
| 장비 시뮬 가시화 | 공통 모듈 | Federation HTTP 클라이언트 | — | 공통모듈 | 조회/입력 | `morph/lam_control_1/` | `lam_federation_client.py` | — | — | — | — |
| 장비 시뮬 가시화 | 공통 모듈 | CSV 프리런·재생 코어 | — | 공통모듈 | 실행 | `morph/lam_control_1/` | `simulation_play.py` | — | — | — | — |
| 장비 시뮬 가시화 | 공통 모듈 | 화면별 재생 세션 | — | 공통모듈 | 실행 | `morph/lam_control_1/` | `lam_csv_play_screen.py` | — | — | — | — |
| 장비 시뮬 가시화 | 공통 모듈 | 화면2 뷰포트·카메라 fly | — | 공통모듈 | 실행 | `morph/lam_control_1/` | `lam_csv_screen_runtime.py`, `lam_play_camera_fly.py` | — | — | — | — |
| 장비 시뮬 가시화 | 공통 모듈 | 웨이퍼·FOUP 3D 라벨 | — | 공통모듈 | 조회/출력 | `morph/lam_control_1/` | `lam_wafer_viewport_labels.py` | — | — | — | — |
| 장비 시뮬 가시화 | 공통 모듈 | JSON 시퀀스 엔진 | — | 공통모듈 | 실행 | `morph/lam_control_1/` | `lam_sequence_engine.py` | — | — | — | — |

---

## 업무영역·기능 요약

| 업무영역 | 설명 |
|----------|------|
| 장비 시뮬 가시화 | 반도체 장비(LAM) 공정 이력을 Federation API 또는 CSV로 받아 3D 가시화·시뮬 재생한다. 화면1·2 독립/동시 재생, 공정만보기, 카메라 fly, 웨이퍼/FOUP 오버레이를 지원한다. |

| 업무기능 | 설명 |
|----------|------|
| Master USD 관리 | 화면1 Master·화면2 Composed USD 로드 |
| CSV/API 시뮬 재생 | dwell 타임라인·JSON 시퀀스 재생, 일시정지·공정만보기 |
| Federation 연동 | limit/offset pagination, has_next 병합, 파싱·프리런·자동 재생 |
| HyView/웹 연동 | `T2V_request_start_simulation` → fetch·재생 → `V2T_response_start_simulation` |
| 가시화 오버레이 | 웨이퍼 번호, FOUP 상태, 디바이스 라벨, 탑뷰, Play 카메라 |

---

## 웹 요청 vs API 테스트창 대응

| 사용자 동작 | 프로그램 ID | 비고 |
|-------------|-------------|------|
| 웹 → Kit `T2V_request_start_simulation` | LAM-HYV-001 → 공통 `lam_federation_pipeline` | fetch+파싱+재생 일괄 |
| 테스트창 「전체 fetch」 | LAM-FED-001 | fetch만 |
| 테스트창 「파싱/시뮬」 | LAM-FED-001 → 공통 파이프라인 | 재생 경로는 웹과 동일 |
| CSV 파일 직접 Play | LAM-CSV-001 / LAM-CSV-002 | Federation 우회 |

---

## 화면 ID 규칙 (본 명세)

- `LAM-{도메인}-{일련번호}`  
- 공통 모듈은 화면 ID 생략
