# Federation 웹 시뮬 — 실무(배포) 진단 가이드

웹 `T2V_request_start_simulation` 후 **데이터는 오는데 시뮬이 안 돌거나 멈춘 것처럼 보일 때**  
콘솔에서 `[LAM/FED-DIAG]` 로 단계를 찾고, 아래 표로 **파일·함수**를 확인한다.

관련 코드 변경은 **Federation 경로만** 대상으로 하며, CSV Play UI 수동 재생과는 분리되어 있다.

---

## 1. 로그 수집

Kit / livestream 콘솔에서:

```text
[LAM/FED-DIAG]
```

추가로 보면 좋은 기존 접두사:

| 접두사 | 의미 |
|--------|------|
| `[LamHandler]` | T2V 수신 |
| `[LAM/federation]` | HTTP fetch |
| `[LAM/federation-pipe]` | 파이프라인 |
| `[LAM/ScreenVisibility]` | 화면 표시 전환 |
| `[LAM/PlayStartSeq]` | Play preflight(카메라/prim hide) |
| `[LAM/csv-prerun]` | prerun JSON dump (웹 경로는 기본 OFF) |

---

## 2. 정상 시 단계 사다리 (순서)

성공 시 대략 아래 순서로 찍힌다.

| step | 메시지 예 | 의미 |
|------|-----------|------|
| `S01_t2v_received` | T2V 수신 | 웹→Kit 메시징 OK |
| `S01_configs` | configs 개수 | payload 파싱 |
| `S02_bridge_main` | bridge 메인 진입 | `lam_sim_bridge` |
| `S03_pipeline_enter` | show_1/show_2, url | 파이프라인 시작 |
| `S04_visibility_requested` | watchdog 대기 | 화면 표시 요청 |
| `S05_visibility_gate` | reason=on_complete \| watchdog | fetch/play 작업 기동 |
| `S06_work_begin` | after visibility | 본 작업 시작 |
| `S07_reap_ok` / `S07_reap_timeout` | 이전 재생 정리 | reap |
| `S08_screen_begin` | screen=N | 화면 N 처리 시작 |
| `[LAM/federation] fetch start/done` | HTTP | API 수신 |
| `S08_fetch_done` | pages/rows | fetch 메타 |
| `S09_parse_ok` | dwells=… | 파싱 |
| `S10_prerun_ok` | items/blocks | 메모리 재생 캐시 |
| `S11_timeline_ui` / `S11_auto_play` | UI·재생 시작 | |
| `S12_play_thread_started` | 스레드명 | 재생 스레드 |
| `S13_preflight_*` | 카메라/hide | preflight |
| `S14_run_simulation` | enter | CSV 시뮬 진입 |
| `S15_run_simulation_exit` | finished | 해당 화면 재생 종료 |
| `S16_pipeline_done` | code=0 | 파이프라인 완료 |
| `S17_v2t_dispatch_ok` | V2T 응답 | 웹 응답 전송 |

**어디서 끊겼는지** = 마지막에 찍힌 `Sxx` 다음 단계의 파일/함수를 보면 된다.

---

## 3. 끊긴 위치 → 확인할 파일·코드

### A. `S01` 자체가 없음

| 항목 | 내용 |
|------|------|
| 증상 | 웹 요청해도 Kit에 FED-DIAG 없음 |
| 확인 | livestream / HyView 메시징, 이벤트명 `T2V_request_start_simulation` |
| 파일 | `sk.hyview_messaging/.../lam_handler.py` → `_on_req_start_simulation` |
| 계약 | `hyview_event_contract.py` 이벤트 상수 |

---

### B. `S01` 있고 `S02` 없음

| 항목 | 내용 |
|------|------|
| 증상 | T2V는 받았는데 bridge 미진입 |
| 파일 | `lam_sim_bridge.py` → `handle_start_simulation` |
| 함수 | `schedule_on_main_thread(_run)` — Kit main dispatch 정체 가능 |
| 파일 | `morph.lam_control_1/.../kit_main_dispatch.py` → `ensure_kit_main_dispatch` / `_pump_main_queue` |

---

### C. `S02` 있고 `S03` 없음 / `S02_bridge_fail`

| 항목 | 내용 |
|------|------|
| 증상 | LAM extension 인스턴스 없음 |
| 파일 | `lam_extension_singleton.py` → `require_lam_extension_instance` |
| 확인 | 배포 앱에 `morph.lam_control_1` 로드·활성화 여부 |

---

### D. `S03` 있고 `S04`/`S05` 없음 (또는 `S05_visibility_watchdog`만)

| 항목 | 내용 |
|------|------|
| 증상 | 화면 표시 전환에서 막힘 → 20초 후 watchdog로 진행 |
| 파일 | `lam_federation_pipeline.py` → `_federation_run_after_visibility` |
| 파일 | `lam_screen_visibility.py` → `request_screen_visibility` |
| 포인트 | generation race로 `on_complete` 유실, stop_reset/`_apply_layout_async` 지연 |
| 로그 | `[LAM/ScreenVisibility] 적용 완료` 유무 |

`S05` reason:

- `on_complete` — 정상 레이아웃 완료
- `watchdog` — 레이아웃 콜백 유실/지연 (배포에서 흔함, 이후 단계는 계속됨)
- `visibility-exception` — 예외 후 강제 진행

---

### E. `S06` 이후 침묵 / `S07_reap_timeout` 반복

| 항목 | 내용 |
|------|------|
| 증상 | 이전 play worker가 안 죽음 |
| 파일 | `lam_federation_pipeline.py` → `_federation_reap_best_effort` |
| 파일 | `simulation_play.py` → `stop_and_reap_csv_play_worker`, `join_csv_play_child_workers` |
| 참고 | 타임아웃 후에도 detach 후 계속 진행하도록 되어 있음. 그래도 애니 충돌이면 여기 |

---

### F. `S08_screen_begin` 후 `fetch start` 없음 / `fetch done` 오래 걸림

| 항목 | 내용 |
|------|------|
| 파일 | `lam_federation_client.py` → `fetch_federation_pages` |
| 설정 | `lam_sim_control_defaults.py` → `FEDERATION_QUERY_URL`, `FEDERATION_FETCH_TIMEOUT_SEC`(페이지당) |
| 확인 | 배포망 DNS/방화벽, 인증 헤더 |

---

### G. `S08_fetch_done` rows=0 → `S09_parse_fail`

| 항목 | 내용 |
|------|------|
| 증상 | HTTP는 됐지만 dwell 0건 |
| 파일 | `lam_api_timeline_parser.py` → `merged_response_to_dwells` |
| 확인 | `eqp_id`, columns/rows 스키마가 로컬 fixture와 다른지 |

---

### H. `S10_prerun_ok` 있고 `S11`/`S12` 없음

| 항목 | 내용 |
|------|------|
| 파일 | `lam_federation_pipeline.py` → `_process_merged_response`, `_start_federation_playback` |
| `S11_registry_missing` | `lam_csv_play_screen.py` → `get_registry_scheduler_for_lam_screen` |
| 확인 | 화면2 aux stage/viewport 미준비 |

---

### I. `S12` 있고 `S14` 없음

| 항목 | 내용 |
|------|------|
| `S13_preflight_*` | `lam_play_start_sequence.py` — 실패해도 **재생은 계속** (`S13_preflight_fail_continue`) |
| `S12_play_abort_registry` | play 스레드 안에서 registry 재조회 실패 |
| 파일 | `lam_play_camera_fly.py`, `lam_play_prim_hide.py` (preflight만) |

---

### J. `S14` 있고 `S15` 없음 (또는 매우 김)

| 항목 | 내용 |
|------|------|
| 의미 | `run_simulation_from_csv` 진입 후 장시간/정지 |
| 파일 | `simulation_play.py` → `run_simulation_from_csv` / timed playback |
| 파일 | `lam_sequence_engine.py` → `_dispatch_main_wait` TIMEOUT 로그 |
| 확인 | USD/메인 스레드 데드락, JSON 이벤트 경로 |

---

### K. `S16`/`S17` 있는데 화면만 안 움직임

| 항목 | 내용 |
|------|------|
| 의미 | 파이프라인·V2T는 성공, 뷰포트/애니만 문제 |
| 확인 | 카메라, prim hide, stage 로드, 화면 표시 마스크 |
| 파일 | `lam_screen_visibility.py`, viewport/USD 로드 경로 |

---

### L. prerun JSON / Permission denied (참고)

| 항목 | 내용 |
|------|------|
| 웹 Federation | `export_default_prerun=False` — **디스크 저장 안 함** |
| CSV Play UI | 체크박스 ON일 때만 dump 시도. 실패해도 재생 계속 |
| 파일 | `lam_csv_prerun_playback.py` → `maybe_export_csv_prerun_json` |
| 전제 | **저장된 prerun JSON을 읽어 재생하지 않음** (메모리 `CachedCsvPlayback` SSOT) |

---

## 4. 빠른 판별 치트시트

```text
S01 없음     → 메시징/이벤트명
S01~S02 사이 → kit_main_dispatch / bridge
S03~S05 사이 → ScreenVisibility (S05 watchdog면 레이아웃 이슈)
S06~S08 사이 → reap / 화면 루프
fetch 구간   → lam_federation_client + 네트워크
S09 실패     → parser / eqp_id / rows
S11 registry → 화면 runtime/stage
S12~S14      → preflight / play thread
S14~S15      → simulation_play / sequence_engine
S16~S17 OK + 화면 정지 → viewport/USD (파이프라인 밖)
```

---

## 5. 핵심 파일 목록 (절대 경로 기준 패키지)

```text
source/extensions/sk.hyview_messaging/sk/hyview_messaging/
  extension_handlers/lam_handler.py     # S01, S17 V2T
  lam_sim_bridge.py                     # S02
  hyview_event_contract.py              # 이벤트명

source/extensions/morph.lam_control_1/morph/lam_control_1/
  lam_federation_pipeline.py            # S03~S16 (FED-DIAG 본체)
  lam_federation_client.py              # HTTP fetch
  lam_api_timeline_parser.py            # parse dwells
  lam_csv_prerun_playback.py            # prerun dump(옵션)
  lam_screen_visibility.py              # 화면 표시
  lam_play_start_sequence.py            # preflight
  simulation_play.py                    # 실제 CSV 재생
  lam_sequence_engine.py                # JSON step / main dispatch wait
  kit_main_dispatch.py                  # 메인 스레드 큐
  lam_sim_control_defaults.py           # URL/timeout/export 기본값
```

---

## 6. 실무에 붙여 넣을 최소 로그 요청

한 번의 실패 재현에서 아래만 순서대로 복사해 전달하면 원인을 거의 특정할 수 있다.

1. `[LAM/FED-DIAG]` 전체 줄  
2. 같은 구간의 `[LAM/federation] fetch start/done`  
3. 있으면 `[LAM/ScreenVisibility]` / `[LAM/PlayStartSeq]` / `[LAM/SEQ] _dispatch_main_wait TIMEOUT`

---

## 7. 문서·코드 동기화

- FED-DIAG step 이름은 `lam_federation_pipeline.py` 의 `_fed_diag("Sxx_...", ...)` 와 맞춰 둔다.
- 단계를 추가·변경하면 **이 문서 표와 step 문자열을 같이 수정**한다.
