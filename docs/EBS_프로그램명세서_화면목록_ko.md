# EBS 시뮬레이션 프로그램 명세서 (화면·프로그램 목록)

**시스템명:** EBS 시뮬레이션 프로그램  
**패키지(확장):** `morph.tbs_control_2`

---

| 업무영역 | 업무기능 | 화면명/프로그램명 | 화면 ID | 유형 | 기능구분 | 화면소스파일 경로 | 화면소스파일명 | Task 파일경로 | Task 파일명 | Handler 파일경로 | Handler 파일명 |
|----------|----------|-------------------|---------|------|----------|-------------------|----------------|---------------|-------------|------------------|----------------|
| EBS 시뮬레이션 | Master USD 관리 | TBS USD Load | TBS-USD-001 | 온라인 | 조회/입력/실행 | `morph/tbs_control_2/` | `tbs_usd_window.py` | — | — | — | — |
| EBS 시뮬레이션 | 공정 시뮬 제어 | TBS 제어창 (Control Window) | TBS-CTL-001 | 온라인 | 조회/입력/실행/수정 | `morph/tbs_control_2/` | `control_window.py` | — | — | — | — |
| EBS 시뮬레이션 | 시퀀스 편집 | TBS Sequence Editor | TBS-SEQ-001 | 온라인 | 조회/입력/수정/실행 | `morph/tbs_control_2/` | `sequence_editor.py` | — | — | — | — |
| EBS 시뮬레이션 | LAM 호환 시퀀스 편집 | TBS LAM Sequence Editor | TBS-SEQ-002 | 온라인 | 조회/입력/수정/실행 | `morph/tbs_control_2/` | `tbs_lam_sequence_editor.py` | — | — | — | — |
| EBS 시뮬레이션 | 시뮬 타임라인 모니터 | 시뮬 Timetable 창 | TBS-MON-001 | 온라인 | 조회 | `morph/tbs_control_2/` | `control_window.py` (`build_sim_timetable_window`) | — | — | — | — |
| EBS 시뮬레이션 | 시뮬 모니터 | 시뮬 Monitor 창 | TBS-MON-002 | 온라인 | 조회 | `morph/tbs_control_2/` | `control_window.py` (`build_sim_monitor_window`) | — | — | — | — |
| EBS 시뮬레이션 | 뷰포트 HUD | TBS Viewport Control HUD | TBS-HUD-001 | 온라인 | 조회/입력 | `morph/tbs_control_2/` | `tbs_viewport_control_hud.py` | — | — | — | — |
| EBS 시뮬레이션 | 듀얼 뷰포트 | 시뮬 Multi View (Dock) | TBS-VP-001 | 온라인 | 조회/실행 | `morph/tbs_control_2/` | `sim_multi_view.py` | — | — | — | — |
| EBS 시뮬레이션 | 듀얼 뷰포트 (Widget) | 시뮬 Multi View Widget | TBS-VP-002 | 온라인 | 조회/실행 | `morph/tbs_control_2/` | `sim_multi_view_widget.py` | — | — | — | — |
| EBS 시뮬레이션 | 원격 제어 (HTTP) | TBS Remote UI / API Tester | TBS-WEB-001 | 온라인 | 조회/입력/실행 | `morph/tbs_control_2/` | `kit_web_api_registry.py` (및 정적 `web/`) | — | — | — | — |
| EBS 시뮬레이션 | HyView 메시징 | EBS HyView T2V/V2T | TBS-HYV-001 | 온라인 | 입력/실행/출력 | `morph/hyview_messaging/` | `tbs_sim_bridge.py` | — | — | `morph/hyview_messaging/extension_handlers/` | `ebs_handler.py` |
| EBS 시뮬레이션 | 확장 진입 | TBS Control Extension | TBS-EXT-001 | 온라인 | 실행 | `morph/tbs_control_2/` | `extension.py` | — | — | — | — |
| EBS 시뮬레이션 | 공통 모듈 | XML 생성·파싱 | — | 공통모듈 | 변환 | `morph/tbs_control_2/` | `xml_generator.py` | — | — | — | — |
| EBS 시뮬레이션 | 공통 모듈 | 프리런·재생 엔진 | — | 공통모듈 | 실행 | `morph/tbs_control_2/` | `control_sim_prerun_playback.py` | — | — | — | — |
| EBS 시뮬레이션 | 공통 모듈 | 다중 화면 재생 | — | 공통모듈 | 실행 | `morph/tbs_control_2/` | `control_sim_multi_playback.py` | — | — | — | — |
| EBS 시뮬레이션 | 공통 모듈 | EP/포트 가시성 | — | 공통모듈 | 실행 | `morph/tbs_control_2/` | `tbs_ep_port_visibility.py` | — | — | — | — |
| EBS 시뮬레이션 | 공통 모듈 | 바 그래프(EP 점유) | — | 공통모듈 | 조회/출력 | `morph/tbs_control_2/` | `control_sim_bar_graph.py` | — | — | — | — |

---

## 업무영역·기능 요약

| 업무영역 | 설명 |
|----------|------|
| EBS 시뮬레이션 | 반도체 EBS(Equipment Backend System) 공정을 XML/JSON 시퀀스 기반으로 시뮬레이션하고, 듀얼 뷰포트(화면1·2)에서 독립·동시 재생을 지원한다. |

| 업무기능 | 설명 |
|----------|------|
| Master USD 관리 | 시뮬 대상 장비 USD Stage 로드·자동 로드 |
| 공정 시뮬 제어 | 시작/정지/초기화, EBS on/off, EP 개수, 배속, seek |
| HyView/웹 연동 | T2V `configs` 수신 → 프리런·재생 → V2T 응답 |
| 원격 제어 (HTTP) | 브라우저 API 테스터·제어 패널 (`POST /api/command`, `GET /api/state`) |

---

## 화면 ID 규칙 (본 명세)

- `TBS-{도메인}-{일련번호}`  
- 공통 모듈(전용 UI 없음)은 화면 ID 생략
