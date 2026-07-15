# EBS 시뮬레이션 프로그램 명세서 (클래스·메소드)

**시스템명:** EBS 시뮬레이션 프로그램  
**패키지(확장):** `morph.tbs_control_2`  
**작성 기준:** Kit 확장 소스 (`source/extensions/morph.tbs_control_2/morph/tbs_control_2/`)  
**관련 웹 연동:** `morph.hyview_messaging` (`ebs_handler.py`, `tbs_sim_bridge.py`), HTTP Remote UI (`kit_web_api_registry.py`)

---

## 1. Extension

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.tbs_control_2.extension` |
| **클래스 ID** | `Extension` |
| **클래스명** | TBS Control 확장 진입점 |
| **파일** | `extension.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `on_startup` | 확장 시작 처리 | `ext_id: str` | Kit 확장 로드 시 싱글톤 등록, 메인 스레드 디스패치 초기화, USD Load 창·제어창·시퀀스 편집기 생성, 듀얼 뷰포트 레이아웃(옵션), HyView 스트리밍 설정, 선택/오버레이 구독 시작. 예외 시 로그 출력 후 해당 단계만 스킵. | 없음 |
| `on_shutdown` | 확장 종료 처리 | 없음 | 시뮬 정지, 애니메이션·타임라인 정지, 뷰포트/구독 해제, 싱글톤 해제. | 없음 |

---

## 2. TbsUsdWindow

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.tbs_control_2.tbs_usd_window` |
| **클래스 ID** | `TbsUsdWindow` |
| **클래스명** | TBS Master USD 로드 창 |
| **파일** | `tbs_usd_window.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `__init__` | USD 로드 창 초기화 | `ext: Any` | omni.ui.Window 생성, 리소스 목록·로드 버튼·상태 라벨 바인딩. | 없음 |
| `run_master_autoload_now` | Master USD 자동 로드 | 없음 | 기본 설정(`sim_control_defaults`)에 정의된 Master USD 경로를 화면1 컨텍스트에 로드. 듀얼 레이아웃 선적용 후 호출. | 없음 |
| `_on_load_clicked` | USD 수동 로드 | 선택 파일 경로 | 사용자가 선택한 USD를 Master Stage에 오픈, EP/포트 visibility 초기화 트리거. | 없음 |

---

## 3. control_window (모듈 함수 — TBS 제어창 핵심)

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.tbs_control_2.control_window` |
| **클래스 ID** | (모듈) `control_window` |
| **클래스명** | EBS 시뮬레이션 제어창 |
| **파일** | `control_window.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `build_control_window` | 제어창 UI 조립 | `ext: Any` | 타임라인·XML·시뮬 버튼·포트 패널·EBS 토글·듀얼 화면 제어 UI를 생성하고 Extension에 참조 저장. | 없음 |
| `on_sim_start_clicked` | 시뮬레이션 시작 | `ext: Any` | **기본 흐름:** UI 설정 스냅샷 수집 → 프리런(스케줄 빌드) → 화면별 재생 worker 기동. **분기:** 화면1/2 단독(`on_sim_start_for_screen`) 또는 동시 재생(`control_sim_multi_playback`). EBS on/off·EP 개수·배속 반영. **예외:** registry/scheduler 미준비 시 로그 후 중단. | 없음 |
| `on_sim_stop_clicked` | 시뮬레이션 정지 | `ext: Any`, `freeze_ep_timeline: bool` | 재생 worker 중지 요청, 타임라인 동결(옵션), 포트/바 그래프 상태 유지 또는 초기화. | 없음 |
| `on_sim_reset_clicked` | 시뮬레이션 초기화 | `ext: Any` | 정지 후 stage/포트/타임라인/바 그래프를 초기 상태로 복원. | 없음 |
| `on_sim_start_for_screen` | 화면별 시뮬 시작 | `ext: Any`, `screen: int` | 지정 화면(1=CASE A, 2=CASE B)만 프리런·재생. USD context·registry 분리. | 없음 |
| `on_sim_stop_for_screen` | 화면별 시뮬 정지 | `ext: Any`, `screen: int` | 해당 화면 worker만 stop, 타 화면 재생 유지. | 없음 |
| `on_sim_ebs_enabled_changed_for_case` | 케이스별 EBS 활성 변경 | `ext: Any`, `case_id: int` | EBS on/off에 따라 EP 포트 visibility·바 그래프·타임라인 표시 갱신. | 없음 |
| `handle_sim_event_for_animation` | 시뮬 이벤트 애니메이션 처리 | `ext: Any`, `payload: Dict`, `verbose: bool` | **기본 흐름:** payload → XML 생성/역파싱 → event_animation_rules/map 조회 → JSON 시퀀스 실행(`SequenceRunner`). **분기:** 포트 ID·이벤트 키별 매핑. **예외:** 매핑 없음 시 로그만. | 없음 |

---

## 4. SimTimelinePlayer / PlaybackEngine

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.tbs_control_2.control_sim_prerun_playback` |
| **클래스 ID** | `SimTimelinePlayer` |
| **클래스명** | EBS 시뮬 타임라인 재생기 |
| **파일** | `control_sim_prerun_playback.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `play` | 타임라인 재생 | `items`, `speed_scale`, `screen` | 프리런 결과(`SimPreRunResult`)를 시각 순서대로 실행. JSON 애니·dwell·바 그래프 갱신 연동. 중지/seek 시 checkpoint 저장. | 없음 |
| `seek_to_index` | 타임라인 탐색 | `index: int` | 지정 항목 시각으로 점프, 포트 occupancy·바 상태 스냅샷 복원. | 없음 |

| 항목 | 내용 |
|------|------|
| **클래스 ID** | `PlaybackEngine` |
| **클래스명** | EBS 재생 엔진 |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `run` | 재생 루프 실행 | `env: PlaybackEnv` | worker 스레드에서 타임라인 항목 순회, 중지 이벤트 폴링. | 없음 |

---

## 5. control_sim_multi_playback

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.tbs_control_2.control_sim_multi_playback` |
| **클래스 ID** | (모듈) |
| **클래스명** | 듀얼 화면 동시 재생 제어 |
| **파일** | `control_sim_multi_playback.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | OUTPUT |
|-----------|----------|------------|---------------------|--------|
| `start_multi_screen_playback` | 다중 화면 재생 시작 | `ext`, `screens: List[int]` | 화면1·2 각각 독립 worker·세션으로 동시 재생. 화면별 stop/pause 분리. | 없음 |

---

## 6. xml_generator

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.tbs_control_2.xml_generator` |
| **클래스 ID** | (모듈) |
| **클래스명** | EBS 시뮬 XML 생성·파싱 |
| **파일** | `xml_generator.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `build_xml_string` | 시뮬 XML 생성 | 시퀀스명, lot/carrier/process 파라미터 | MES 스타일 XML 문자열 생성. | `str` (XML) |
| `parse_xml_string` | 시뮬 XML 역파싱 | `xml_text: str` | XML → dict (포트·이벤트·시각). 파싱 실패 시 `None`. | `Optional[dict]` |

---

## 7. EBSHandler (HyView 메시징)

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.hyview_messaging.extension_handlers.ebs_handler` |
| **클래스 ID** | `EBSHandler` |
| **클래스명** | EBS HyView T2V 이벤트 핸들러 |
| **파일** | `morph.hyview_messaging/.../ebs_handler.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `get_event_handlers` | 수신 이벤트 맵 반환 | 없음 | `T2V_request_start_simulation`, `T2V_request_seek_simulation`, `T2V_request_ebs_enable` 등 → 콜백 매핑. | `Dict[str, Callable]` |
| `_on_req_start_simulation` | 시뮬 시작 요청 처리 | `event: carb.events.IEvent` | payload `configs[2]` 파싱 → `tbs_sim_bridge.handle_start_simulation` 위임 → 완료 시 V2T `data.result` 응답. | V2T 이벤트 dispatch |
| `_on_req_seek_simulation` | 시뮬 탐색 요청 | `event` | seek index/시각 → `handle_seek_simulation`. | V2T 응답 |
| `_on_req_ebs_enable` | EBS 활성 요청 | `event` | `ebs_enable`, `case` → `handle_ebs_enable`. | V2T 응답 |

---

## 8. tbs_sim_bridge (Kit 시뮬 브리지)

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.hyview_messaging.tbs_sim_bridge` |
| **클래스 ID** | (모듈) |
| **클래스명** | HyView–Kit EBS 시뮬 브리지 |
| **파일** | `tbs_sim_bridge.py` |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `handle_start_simulation` | 웹 시뮬 시작 | `payload`, `dispatch`, `event_name` | **기본 흐름:** 메인 스레드 큐 → `configs` 정규화 → 화면 표시 전환 → 프리런 v2 JSON 생성 → (옵션) 자동 재생. **분기:** case0/case1 비어 있으면 해당 화면 스킵. **예외:** Extension 미준비 시 `code=1`. | `dispatch(event_name, {code, message, data})` |
| `handle_seek_simulation` | 웹 시뮬 탐색 | `payload`, `dispatch` | 재생 중/정지 상태에서 타임라인 인덱스 이동. | V2T envelope |
| `handle_ebs_enable` | 웹 EBS 토글 | `payload`, `dispatch` | case별 `ebs_enabled` 적용, 포트 레이아웃 갱신. | V2T envelope |
| `handle_eqp_change` | 장비 변경 | `payload`, `dispatch` | eqp_id 변경 시 케이스 설정·포트 레이아웃 재적용. | V2T envelope |
| `handle_control_simulation` | 시뮬 제어(시작/정지/초기화) | `payload`, `dispatch` | `action` 필드에 따라 start/stop/reset 분기. | V2T envelope |

---

## 9. AnimationInstanceRegistry / PlaybackScheduler

| 항목 | 내용 |
|------|------|
| **패키지** | `morph.tbs_control_2.tbs_instance_registry` / `tbs_playback_scheduler` |
| **클래스 ID** | `AnimationInstanceRegistry` / `PlaybackScheduler` |
| **클래스명** | 애니메이션 인스턴스 등록소 / 재생 스케줄러 |

| 메소드 ID | 메소드명 | 입력데이터 | 메소드 설명 및 로직 | 출력데이터 |
|-----------|----------|------------|---------------------|------------|
| `register` | 인스턴스 등록 | prim 경로, 스텝 정의 | USD prim별 애니메이션 인스턴스 등록. | 없음 |
| `begin_master_timeline_mode` | 마스터 타임라인 모드 시작 | `prim_path` | OmniGraph/USD Timeline 연동 재생 준비. | `bool` |
| `stop_all` | 전체 스케줄 정지 | 없음 | 진행 중 MOVE/ROTATE·타임라인 정지. | 없음 |

---

## 부록: 데이터 저장

| 구분 | 내용 |
|------|------|
| DB | 본 확장은 RDB 미사용. 프리런·설정은 `data/sim_prerun/*.json`, `data/sim_sequences/*.json`, `config/*.json` 파일 기반. |
| API | Federation/HTTP Remote UI는 REST(JSON). HyView는 carb.events T2V/V2T. |
