# 장비시뮬레이션 가시화 프로그램 명세서 (클래스·메소드)

**시스템명:** 장비시뮬레이션 가시화 프로그램  
**패키지(확장):** `morph.lam_control_1`  
**작성 기준:** Kit 확장 소스 (`source/extensions/morph.lam_control_1/morph/lam_control_1/`)  
**관련 웹 연동:** `sk.hyview_messaging` (`lam_handler.py`, `lam_sim_bridge.py`)

---

## 1. LamControlExtension

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.lam_control_1.extension` |
| **클래스 ID** | `LamControlExtension` |
| **클래스명** | LAM Control 확장 진입점 |
| **파일** | `extension.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `on_startup` | 확장 시작 처리 | `ext_id: str` | 싱글톤·메인 디스패치 초기화, Registry/Scheduler/Evaluator 생성, LamWindow 생성, 듀얼 뷰포트 선적용(옵션), Master USD 자동 로드 예약. | 없음 |
| `on_shutdown` | 확장 종료 처리 | 없음 | 재생 정지, 뷰포트 해제, 세션·싱글톤 정리. | 없음 |

---

## 2. LamWindow

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.lam_control_1.lam_window` |
| **클래스 ID** | `LamWindow` |
| **클래스명** | LAM Multi-USD 로드·통합 제어 창 |
| **파일** | `lam_window.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `__init__` | 메인 창 초기화 | `ext: Any` | USD 로드 UI, 화면별 CSV 시뮬 창 참조, Viewport HUD 연결. | 없음 |
| `run_master_autoload_now` | Master USD 자동 로드 | 없음 | 화면1 Master Stage 로드, 화면2 composed USD hydrate 트리거. | 없음 |
| `_ensure_csv_sim_play_window` | 화면별 CSV 창 확보 | `screen: int` | 화면1/2 `LamSimulationCsvPlayWindow` 인스턴스 생성·반환. | `LamSimulationCsvPlayWindow` |
| `sync_csv_viewport_overlays_for_screen` | 화면별 오버레이 동기화 | `screen: int` | FOUP/웨이퍼 라벨·디바이스 라벨·탑뷰 등 체크박스 → 3D 오버레이 반영. | 없음 |

---

## 3. LamSimulationCsvPlayWindow

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.lam_control_1.simulation_play` |
| **클래스 ID** | `LamSimulationCsvPlayWindow` |
| **클래스명** | LAM CSV 시뮬 재생 창 |
| **파일** | `simulation_play.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `_on_play_clicked` | CSV 재생 시작 | 없음 (UI 상태) | **기본 흐름:** runtime 갱신 → 프리런(캐시) → 화면별 preflight(카메라 fly·prim hide) → `run_simulation_from_csv`. **분기:** 일시정지 이어서, 공정만보기(배속 1x). **예외:** 화면2 runtime 미준비 시 중단. | 없음 |
| `_on_csv_pause_clicked` | 일시정지 | 없음 | 백그라운드에서 checkpoint 저장·worker stop. 화면별 독립. | 없음 |
| `_on_csv_stop_reset_clicked` | 정지(초기화) | `on_complete: Optional[Callable]` | worker join 후 TBS/visibility/카메라 복원. `on_complete`는 화면 전환 완료 신호용. | 없음 |
| `_on_live_process_only_changed` | 공정만보기 실시간 전환 | 없음 | **기본 흐름:** UI 즉시 반환 → 백그라운드 pause·checkpoint·모드 전환 재개. **분기:** Federation/API 재생도 `_csv_play_thread` 추적. **예외:** 캐시 없음 시 로그. | 없음 |
| `_run_aux_screen_play_preflight` | 화면2+ Play 전처리 | `kit_ext` | `run_csv_screen_play_preflight` — 카메라 fly, prim hide, 탑뷰( fly 우선 시 스킵). | `bool` |
| `_apply_schedule_row_highlight` | 타임라인 행 강조 | `active_keys: frozenset` | 재생 중 JSON 실행 행 녹색 표시. 메인 스레드 전용. | 없음 |

---

## 4. simulation_play (모듈 — 재생 엔진)

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.lam_control_1.simulation_play` |
| **클래스 ID** | (모듈) |
| **클래스명** | CSV 시뮬 재생 엔진 |
| **파일** | `simulation_play.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `run_simulation_from_csv` | CSV 기반 시뮬 실행 | `registry`, `scheduler`, `prepared`, `speed_scale`, `process_only`, `play_screen`, `resume_from_csv_sec`, … | **기본 흐름:** dwell+JSON 블록 스케줄 실행. **분기:** `process_only=True` → 레인별 병렬·idle 점프. **예외:** stop 요청 시 worker 조기 종료. | 없음 |
| `run_csv_timed_playback` | 시간 기반 재생 | 블록 목록, 배속, resume | 일반 재생(블록별 worker) 또는 공정만보기 분기. | 없음 |
| `save_csv_play_pause_checkpoint` | 일시정지 체크포인트 저장 | `csv_path`, `speed_scale`, `process_only`, `screen` | CSV 시각·JSON 진행·실경과 저장. 화면별 세션. | `CsvPlayPauseCheckpoint` |
| `request_stop_csv_playback` | 재생 중지 요청 | `screen`, `registry`, `scheduler`, `kit_ext` | stop_event, runner stop, 화면별 MOVE/ROTATE 중지. | 없음 |
| `build_and_cache_from_dwells` | dwell → 재생 캐시 빌드 | `path`, `dwells` | API/CSV 파싱 결과를 `CachedCsvPlayback`으로 변환·캐시. | `CachedCsvPlayback` |

---

## 5. lam_federation_pipeline

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.lam_control_1.lam_federation_pipeline` |
| **클래스 ID** | (모듈) / `ScreenPipelineResult` |
| **클래스명** | Federation API → 파싱·프리런·재생 파이프라인 |
| **파일** | `lam_federation_pipeline.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `run_federation_start_simulation` | 웹/API 전체 시뮬 시작 | `ext`, `payload{configs}`, `on_complete`, `limit_override`, … | **기본 흐름:** `normalize_configs` → `request_screen_visibility`(완료 후) → 화면별 `fetch_federation_pages`(has_next 루프) → `_process_merged_response` → 자동 재생. **분기:** 화면1만/2만/동시. **예외:** fetch 실패 시 `code=1`. | `on_complete({code, message, data})` |
| `run_federation_response_simulation` | 편집기 JSON 파싱·시뮬 | `merged_response`, `body`, `screen`, … | API 재요청 없이 로그/편집기 JSON만 파싱·재생. 테스트창 「파싱/시뮬」과 동일 재생 경로. | `on_complete` |
| `_process_merged_response` | 병합 응답 처리 | `merged`, `body`, `screen`, `auto_play` | dwell 파싱 → prerun → 타임라인 UI → `_start_federation_playback`. | `ScreenPipelineResult` |
| `_start_federation_playback` | Federation 재생 기동 | `cached`, `csv_win`, `screen` | CSV Play와 동일 preflight·콜백·`run_simulation_from_csv`. `_prepared_playback`·`_csv_play_thread` 연결(공정만보기 실시간 전환). | `Optional[str]` 오류 메시지 |
| `_process_one_screen` | 화면 단위 fetch+처리 | `screen`, `body`, `url`, `limit`, … | `fetch_federation_pages` + `_process_merged_response`. | `ScreenPipelineResult` |

---

## 6. lam_federation_client

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.lam_control_1.lam_federation_client` |
| **클래스 ID** | (모듈) |
| **클래스명** | Federation HTTP 클라이언트 |
| **파일** | `lam_federation_client.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `fetch_federation_pages` | 페이지네이션 전체 fetch | `url`, `body`, `limit`, `initial_offset`, `screen`, … | **기본 흐름:** POST 반복, `pagination.has_next=false`까지 rows 병합. offset은 **행 오프셋**(0→50→100…). **예외:** HTTP 4xx/5xx, 10000페이지 초과. | `(merged: dict, meta: dict)` |
| `fetch_single_post` | 단일 POST | `url`, `body`, `limit`, `offset` | 1회 요청(테스트창 「POST 1회」). | `(status, data, raw)` |

---

## 7. LamFederationTestWindow

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.lam_control_1.lam_federation_test_window` |
| **클래스 ID** | `LamFederationTestWindow` |
| **클래스명** | LAM Federation API 테스트 창 |
| **파일** | `lam_federation_test_window.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `_on_fetch_all` | 전체 fetch | UI url/body/limit/offset | `fetch_federation_pages` 호출, 응답 편집기 반영. | 없음 |
| `_on_parse_sim` | 파싱·시뮬 | 편집기 JSON, body, screen | `run_federation_response_simulation` — 웹 재생과 동일 파이프라인( fetch 제외). | `on_complete` 로그 |

---

## 8. LamHandler / lam_sim_bridge (HyView)

| 항목 | 내용 |
|------|------|
| **패키지** | `sk.hyview_messaging.extension_handlers.lam_handler` / `lam_sim_bridge` |
| **클래스 ID** | `LamHandler` |
| **클래스명** | LAM HyView T2V 시뮬 시작 핸들러 |
| **파일** | `lam_handler.py`, `lam_sim_bridge.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `_on_req_start_simulation` | T2V 시뮬 시작 수신 | `event.payload.configs` | `handle_start_simulation` 위임. | V2T dispatch |
| `handle_start_simulation` | Kit 시뮬 브리지 | `payload`, `dispatch` | `run_federation_start_simulation(ext, payload, limit=FEDERATION_FETCH_LIMIT)`. 완료 시 V2T `{code, data.pipeline}`. | V2T envelope |

---

## 9. CsvPlayScreenSession / lam_csv_screen_runtime

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.lam_control_1.lam_csv_play_screen` / `lam_csv_screen_runtime` |
| **클래스 ID** | `CsvPlayScreenSession` / `CsvScreenRuntime` |
| **클래스명** | 화면별 CSV 재생 세션 / 화면별 뷰포트 런타임 |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `csv_play_screen_session` | 화면 세션 조회 | `screen: Optional[int]` | 화면1·2 독립 stop/pause/progress/timeline 상태. | `CsvPlayScreenSession` |
| `run_csv_screen_play_preflight` | 화면2+ preflight | `runtime: CsvScreenRuntime` | 카메라 fly 대기, prim hide. fly ON 시 overlay 탑뷰 선점 방지. | `bool` |
| `sync_csv_screen_overlays` | 화면별 오버레이 sync | `lam_window`, `screen` | 3D 라벨·탑뷰·prim hide 체크박스 반영. | 없음 |

---

## 10. LamSequenceRunner

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.lam_control_1.lam_sequence_engine` |
| **클래스 ID** | `LamSequenceRunner` |
| **클래스명** | LAM JSON 시퀀스 실행기 |
| **파일** | `lam_sequence_engine.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `run` | 시퀀스 동기 실행 | `steps`, `speed_scale`, `usd_context_name` | MOVE/ROTATE/VISIBILITY/USD_TIMELINE 스텝 순차 실행. 메인 스레드 USD write는 `_dispatch_main_wait`. | 없음 |
| `stop` | 시퀀스 중지 | `cancel_all_move_rotate` | stop_flag, (옵션) 화면별 애니 중지. | 없음 |

---

## 부록: 웹·테스트창·재생 경로 일치

| 경로 | fetch | 파싱·프리런 | 재생·공정만보기·카메라 |
|------|-------|-------------|------------------------|
| 테스트창 「파싱/시뮬」 | 없음 | `run_federation_response_simulation` | `_start_federation_playback` → `run_simulation_from_csv` |
| 테스트창 「전체 fetch」+「파싱/시뮬」 | 수동 2단계 | 동일 | 동일 |
| 웹 `T2V_request_start_simulation` | `run_federation_start_simulation` 내 자동 | 동일 | 동일 |

최근 수정(공정만보기 started 시드, visibility 완료 후 재생, fly/overlay 경합 방지, 화면별 세션)은 **위 재생 공통 경로**에 반영되어 웹·테스트창 모두 동일 동작을 목표로 한다.

---

## 부록: 데이터 저장

| 구분 | 내용 |
|------|------|
| DB | RDB 미사용. Federation 응답·프리런은 `data/api_queries/`, `data/csv_prerun/` JSON 파일. |
| API | Federation REST POST (pagination). HyView T2V/V2T. |
