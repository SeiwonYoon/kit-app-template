"""
TBS Kit ↔ 웹 HTTP API 레지스트리 (SSOT).

웹 control-tab·api_tester·문서가 참조하는 cmd / state / fields 메타데이터.
실제 실행은 kit_remote_http_bridge._dispatch_command 가 담당한다.

확인:
  GET http://127.0.0.1:8720/api/registry
  브라우저 http://127.0.0.1:8720/api_tester.html

가이드:
  docs/tbs_web_api_user_guide_ko.md — 기능별 API · 웹 적용 방법
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

REGISTRY_VERSION = 1


@dataclass(frozen=True)
class WebEndpointMeta:
    """HTTP 엔드포인트 메타."""

    method: str
    path: str
    summary: str
    response_hint: str = ""


@dataclass(frozen=True)
class WebCommandMeta:
    """POST /api/command 의 cmd 하나."""

    cmd: str
    summary: str
    handler: str
    exposed: bool = True
    apply_fields_first: bool = False
    body_keys: tuple[str, ...] = ()
    example_body: Dict[str, Any] = field(default_factory=dict)
    web_ui: str = ""
    notes: str = ""


@dataclass(frozen=True)
class WebStateFieldMeta:
    """GET /api/state 응답 필드."""

    key: str
    summary: str
    source: str
    type_hint: str = "string"
    poll_ms: int = 400
    exposed: bool = True
    example: Any = None


@dataclass(frozen=True)
class WebFieldsKeyMeta:
    """apply_fields / sim_start 의 fields 객체 키."""

    key: str
    summary: str
    kit_model: str
    type_hint: str = "string"
    example: Any = None


WEB_ENDPOINTS: tuple[WebEndpointMeta, ...] = (
    WebEndpointMeta("GET", "/api/registry", "API 카탈로그 (이 파일 내용 JSON)", "commands, state_fields, web_fields"),
    WebEndpointMeta("GET", "/api/state", "Kit UI 스냅샷 — 웹 폴링용", "ports, progress, ep_timeline, channels, …"),
    WebEndpointMeta("GET", "/api/resources", "샘플 USD 목록", '{ "items": [{ "name", "path" }] }'),
    WebEndpointMeta("GET", "/api/prerun", "프리런 export JSON", "?screen=1 선택 또는 전체"),
    WebEndpointMeta("POST", "/api/command", "원격 명령 실행", '{ "ok": true } 또는 { "ok": false, "error" }'),
)


WEB_COMMANDS: tuple[WebCommandMeta, ...] = (
    WebCommandMeta(
        cmd="apply_fields",
        summary="제어창 입력값만 Kit 모델에 반영 (시뮬 시작 없음)",
        handler="kit_remote_http_bridge._apply_web_fields",
        apply_fields_first=True,
        body_keys=("fields",),
        example_body={"cmd": "apply_fields", "fields": {"lot_count": 6, "speed": 1.0}},
        web_ui="TbsControlTab — EP 개수 변경 시 자동 호출",
    ),
    WebCommandMeta(
        cmd="sim_start",
        summary="fields 적용 후 시뮬 시작 (프리런·타임라인 재생 파이프라인)",
        handler="control_window.on_sim_start_clicked",
        apply_fields_first=True,
        body_keys=("fields",),
        example_body={"cmd": "sim_start", "fields": {"lot_count": 6, "ep_count_index": 0, "speed": 1.0}},
        web_ui="TbsControlTab — 시작 버튼",
        notes="fields 생략 시 현재 Kit 모델 값으로 시작",
    ),
    WebCommandMeta(
        cmd="sim_stop",
        summary="시뮬 정지",
        handler="control_window.on_sim_stop_clicked",
        example_body={"cmd": "sim_stop"},
        web_ui="TbsControlTab — 정지 버튼",
    ),
    WebCommandMeta(
        cmd="sim_reset",
        summary="시뮬 리셋",
        handler="control_window.on_sim_reset_clicked",
        example_body={"cmd": "sim_reset"},
        web_ui="TbsControlTab — 리셋 버튼",
    ),
    WebCommandMeta(
        cmd="load_usd",
        summary="USD 마스터 스테이지 열기",
        handler="tbs_usd_window.TbsUsdWindow.open_master_at_path",
        body_keys=("path", "resource_index"),
        example_body={"cmd": "load_usd", "path": "", "resource_index": 0},
        web_ui="TbsControlTab — Load 버튼",
        notes="path 비우면 기본 경로. GET /api/resources 로 목록 조회",
    ),
    WebCommandMeta(
        cmd="xml_ok",
        summary="XML 제너레이터 OK — XML 문자열 생성",
        handler="control_window.on_xml_ok_clicked",
        apply_fields_first=True,
        body_keys=("fields",),
        example_body={
            "cmd": "xml_ok",
            "fields": {"xml_seq_index": 0, "xml_from": 1, "xml_to": 6, "xml_port_id": 1},
        },
        web_ui="tbs_kit_remote — OK 버튼",
    ),
    WebCommandMeta(
        cmd="xml_run",
        summary="생성된 XML 역파싱·샘플 실행",
        handler="control_window.on_xml_run_clicked",
        example_body={"cmd": "xml_run"},
        web_ui="tbs_kit_remote — 제너레이터 실행",
    ),
    WebCommandMeta(
        cmd="kit_chrome_hide",
        summary="Kit 기본 메뉴·패널 숨김",
        handler="kit_chrome_visibility.apply_kit_chrome_hidden",
        body_keys=("hidden",),
        example_body={"cmd": "kit_chrome_hide", "hidden": True},
        web_ui="TbsControlTab — 스트리밍 모드 체크박스",
    ),
    WebCommandMeta(
        cmd="ui_windows",
        summary="TBS 제어창·시뮬 모니터·타임테이블·시퀀스 편집기 visible 토글",
        handler="kit_remote_http_bridge._dispatch_command (inline)",
        body_keys=("hide",),
        example_body={"cmd": "ui_windows", "hide": True},
        web_ui="TbsControlTab autoStreamingMode",
        notes="hide=true → Kit 창 숨김, 웹 control-tab 만 사용",
    ),
    WebCommandMeta(
        cmd="sim_viewport_split",
        summary="뷰포트 1~4화면 분할",
        handler="sim_multi_view.apply_sim_viewport_split_layout",
        body_keys=("count",),
        example_body={"cmd": "sim_viewport_split", "count": 2},
        web_ui="TbsControlTab — 화면 분할 radio",
    ),
    WebCommandMeta(
        cmd="save_sim_screen",
        summary="현재 제어창 설정을 화면 N 스냅샷에 저장",
        handler="control_window._on_save_sim_settings_to_screen",
        body_keys=("screen",),
        example_body={"cmd": "save_sim_screen", "screen": 1},
        web_ui="TbsControlTab — 화면N 저장",
    ),
    WebCommandMeta(
        cmd="apply_per_screen_snapshot",
        summary="화면별 저장 설정 불러오기",
        handler="kit_remote_http_bridge._apply_per_screen_snapshot",
        body_keys=("snapshot",),
        example_body={"cmd": "apply_per_screen_snapshot", "snapshot": {"lot_count": 6, "ep_count_idx": 0}},
        web_ui="TbsControlTab — 화면N 불러오기",
    ),
    WebCommandMeta(
        cmd="gate_confirm",
        summary="공정 확인(Gate) 모달 통과",
        handler="control_window._close_sim_gate_dialog",
        example_body={"cmd": "gate_confirm"},
        web_ui="TbsControlTab — Gate 모달 확인",
        notes="state.gate_pending 이 있을 때만 의미 있음",
    ),
    WebCommandMeta(
        cmd="copy_progress",
        summary="진행현황+Sim로그 Kit 클립보드 복사",
        handler="control_window.on_copy_sim_progress",
        example_body={"cmd": "copy_progress"},
        web_ui="TbsControlTab",
    ),
    WebCommandMeta(
        cmd="prim_refresh",
        summary="장비 prim 목록 새로고침 (Kit 제어창 패널)",
        handler="control_window.refresh_object_list",
        example_body={"cmd": "prim_refresh"},
        web_ui="TbsControlTab — 목록 새로고침",
        notes="GET /api/prim_list 는 아직 없음 — Kit 창에서만 드롭다운 확인",
    ),
    WebCommandMeta(
        cmd="log_mode",
        summary="(호환) 과거 표시모드 — 현재 no-op",
        handler="kit_remote_http_bridge._dispatch_command",
        exposed=True,
        example_body={"cmd": "log_mode"},
        notes="항상 { ok: true } 반환",
    ),
    # ── 미노출(계획) ──
    WebCommandMeta(
        cmd="playback_pause",
        summary="프리런 타임라인 재생 일시정지",
        handler="(미구현) control_sim_screen_playback",
        exposed=False,
        example_body={"cmd": "playback_pause", "screen": 1},
        web_ui="(계획) 재생 툴바",
    ),
    WebCommandMeta(
        cmd="playback_resume",
        summary="프리런 재생 재개",
        handler="(미구현) control_sim_screen_playback",
        exposed=False,
        example_body={"cmd": "playback_resume", "screen": 1},
    ),
    WebCommandMeta(
        cmd="playback_stop",
        summary="프리런 재생만 정지 (시뮬 엔진과 별도)",
        handler="(미구현) control_sim_multi_playback.stop_playback_runtime",
        exposed=False,
        example_body={"cmd": "playback_stop", "screen": 1},
    ),
    WebCommandMeta(
        cmd="timetable_seek",
        summary="타임테이블 행 클릭 Seek",
        handler="(미구현) control_window._fast_apply_prerun_seek",
        exposed=False,
        body_keys=("screen", "row_index"),
        example_body={"cmd": "timetable_seek", "screen": 1, "row_index": 0},
        web_ui="(계획) 타임테이블 패널",
    ),
)


WEB_STATE_FIELDS: tuple[WebStateFieldMeta, ...] = (
    WebStateFieldMeta("usd_status", "USD Load 상태 문구", "TbsUsdWindow._log_label / master_path", "string"),
    WebStateFieldMeta("progress", "진행현황 패널 텍스트", "ext._sim_progress_label", "string"),
    WebStateFieldMeta("history", "이력 로그 패널 텍스트", "ext._sim_history_label", "string"),
    WebStateFieldMeta("sim_line", "이력과 동일 소스 (레거시)", "ext._sim_history_label", "string"),
    WebStateFieldMeta("port_header", "포트 상태 제목", "ext._sim_port_state_header_label", "string"),
    WebStateFieldMeta(
        "ports",
        "포트별 표시 문자열 (INOUT, BP1~4, EP1~3)",
        "ext._sim_port_cells",
        "object",
        example={"INOUT": "-", "BP1": "FULL", "EP1": "LOT001"},
    ),
    WebStateFieldMeta("ep3_visible", "EP3 포트 칸 표시", "ext._sim_port_ep3_cell_container.visible", "boolean"),
    WebStateFieldMeta("bp4_visible", "BP4 포트 칸 표시", "ext._sim_port_bp4_cell_container.visible", "boolean"),
    WebStateFieldMeta("kit_app", "Kit 앱 이름", "app.get_app().get_name()", "string"),
    WebStateFieldMeta("kit_chrome_hidden", "Kit chrome 숨김 여부", "kit_chrome_visibility.is_kit_chrome_hidden", "boolean"),
    WebStateFieldMeta("viewport_split_count", "뷰포트 분할 수 1~4", "ext._sim_viewport_split_count", "number"),
    WebStateFieldMeta(
        "sim_multi_split_row_visible",
        "멀티 분할 UI 행 표시 (USD 로드 후)",
        "ext._sim_multi_split_row.visible",
        "boolean",
    ),
    WebStateFieldMeta(
        "channels",
        "멀티 화면별 모니터 스냅샷 배열",
        "ext._sim_monitor_channels → _channel_snapshot_from_ch",
        "array",
    ),
    WebStateFieldMeta(
        "ep_timeline",
        "화면1 EP 막대 (5상태 세그먼트)",
        "_serialize_ep_timeline_for_screen(ext, '1')",
        "object",
    ),
    WebStateFieldMeta(
        "prerun_export_by_screen",
        "화면별 프리런 export JSON",
        "ext._sim_prerun_export_json_by_screen",
        "object",
    ),
    WebStateFieldMeta(
        "per_screen_snapshots",
        "화면 1~4 설정 스냅샷 (null 가능)",
        "ext._sim_per_screen_snapshots",
        "array",
    ),
    WebStateFieldMeta(
        "gate_pending",
        "공정 확인 대기 (null이면 모달 숨김)",
        "ext._sim_web_gate_pending",
        "object|null",
        example={"title": "공정 확인", "message": "…"},
    ),
)


# WebStateFieldMeta doesn't have notes - I used notes= on channels by mistake. Fix the file.

WEB_FIELDS_KEYS: tuple[WebFieldsKeyMeta, ...] = (
    WebFieldsKeyMeta("lot_count", "LOT 수", "ext._sim_lot_count_model", "integer", 6),
    WebFieldsKeyMeta("ep_count_index", "EP 개수 (0=2개, 1=3개)", "ext._sim_ep_count_combo", "integer", 0),
    WebFieldsKeyMeta("lot_spawn_min", "LOT 생성 간격 min(초)", "ext._sim_lot_spawn_min_model", "number", 15.0),
    WebFieldsKeyMeta("lot_spawn_max", "LOT 생성 간격 max(초)", "ext._sim_lot_spawn_max_model", "number", 40.0),
    WebFieldsKeyMeta("pickup_min", "회수 간격 min(초)", "ext._sim_pickup_evt_min_model", "number", 50.0),
    WebFieldsKeyMeta("pickup_max", "회수 간격 max(초)", "ext._sim_pickup_evt_max_model", "number", 70.0),
    WebFieldsKeyMeta("foup_proc_min", "FOUP 공정 min(초)", "ext._sim_foup_proc_min_model", "number", 5.0),
    WebFieldsKeyMeta("foup_proc_max", "FOUP 공정 max(초)", "ext._sim_foup_proc_max_model", "number", 10.0),
    WebFieldsKeyMeta("speed", "시뮬 속도 배율", "ext._sim_speed_model", "number", 1.0),
    WebFieldsKeyMeta("log_interval", "진행 로그 주기(초)", "ext._sim_log_interval_model", "number", 1.0),
    WebFieldsKeyMeta("confirm_each", "각 공정 확인", "ext._sim_confirm_each_step_model", "boolean", False),
    WebFieldsKeyMeta(
        "process_time_priority",
        "공정설정 시간 우선",
        "ext._sim_process_time_priority_model",
        "boolean",
        False,
    ),
    WebFieldsKeyMeta("init_inout", "초기 적재 IN/OUT", "ext._sim_init_inout_model", "boolean", False),
    WebFieldsKeyMeta("init_bp1", "초기 적재 BP1", "ext._sim_init_bp1_model", "boolean", False),
    WebFieldsKeyMeta("init_bp2", "초기 적재 BP2", "ext._sim_init_bp2_model", "boolean", False),
    WebFieldsKeyMeta("init_bp3", "초기 적재 BP3", "ext._sim_init_bp3_model", "boolean", False),
    WebFieldsKeyMeta("init_bp4", "초기 적재 BP4", "ext._sim_init_bp4_model", "boolean", False),
    WebFieldsKeyMeta("init_ep1", "초기 적재 EP1", "ext._sim_init_ep1_model", "boolean", False),
    WebFieldsKeyMeta("init_ep2", "초기 적재 EP2", "ext._sim_init_ep2_model", "boolean", False),
    WebFieldsKeyMeta("init_ep3", "초기 적재 EP3", "ext._sim_init_ep3_model", "boolean", False),
    WebFieldsKeyMeta("fault_inout", "고장 IN/OUT", "ext._sim_fault_inout_model", "boolean", False),
    WebFieldsKeyMeta("fault_bp1", "고장 BP1", "ext._sim_fault_bp1_model", "boolean", False),
    WebFieldsKeyMeta("fault_bp2", "고장 BP2", "ext._sim_fault_bp2_model", "boolean", False),
    WebFieldsKeyMeta("fault_bp3", "고장 BP3", "ext._sim_fault_bp3_model", "boolean", False),
    WebFieldsKeyMeta("fault_bp4", "고장 BP4", "ext._sim_fault_bp4_model", "boolean", False),
    WebFieldsKeyMeta("fault_ep1", "고장 EP1", "ext._sim_fault_ep1_model", "boolean", False),
    WebFieldsKeyMeta("fault_ep2", "고장 EP2", "ext._sim_fault_ep2_model", "boolean", False),
    WebFieldsKeyMeta("fault_ep3", "고장 EP3", "ext._sim_fault_ep3_model", "boolean", False),
    WebFieldsKeyMeta("oht_min", "OHT→BP/EP min(초)", "ext._sim_oht_bp1_min_model", "number", 5.0),
    WebFieldsKeyMeta("oht_max", "OHT→BP/EP max(초)", "ext._sim_oht_bp1_max_model", "number", 10.0),
    WebFieldsKeyMeta("bp1_bp_min", "IN/OUT→BP min(초)", "ext._sim_bp1_bp_min_model", "number", 5.0),
    WebFieldsKeyMeta("bp1_bp_max", "IN/OUT→BP max(초)", "ext._sim_bp1_bp_max_model", "number", 10.0),
    WebFieldsKeyMeta("bp_ep_min", "BP→EP min(초)", "ext._sim_bp_ep_min_model", "number", 5.0),
    WebFieldsKeyMeta("bp_ep_max", "BP→EP max(초)", "ext._sim_bp_ep_max_model", "number", 10.0),
    WebFieldsKeyMeta("ep_oht_min", "EP→OHT min(초)", "ext._sim_ep_oht_min_model", "number", 5.0),
    WebFieldsKeyMeta("ep_oht_max", "EP→OHT max(초)", "ext._sim_ep_oht_max_model", "number", 10.0),
    WebFieldsKeyMeta("priority_prefix", "prim 우선 표시 접두사", "ext._priority_prefix_model", "string", ""),
    WebFieldsKeyMeta("xml_seq_index", "XML 시퀀스 콤보 인덱스 0~6", "ext._xml_seq_combo", "integer", 0),
    WebFieldsKeyMeta("xml_from", "XML FROM 포트", "ext._xml_from_port_model", "integer", 1),
    WebFieldsKeyMeta("xml_to", "XML TO 포트", "ext._xml_to_port_model", "integer", 6),
    WebFieldsKeyMeta("xml_port_id", "XML PORT_ID", "ext._xml_port_id_model", "integer", 1),
    WebFieldsKeyMeta("usd_path", "USD 경로 (load_usd)", "ext._path_model", "string", ""),
    WebFieldsKeyMeta("resource_index", "샘플 USD 콤보 인덱스", "ext._resource_combo", "integer", 0),
)


def _meta_to_dict(obj: Any) -> Dict[str, Any]:
    d = asdict(obj)
    return {k: v for k, v in d.items() if v not in ((), "", None)}


def get_command_meta(cmd: str) -> Optional[WebCommandMeta]:
    key = str(cmd or "").strip()
    for m in WEB_COMMANDS:
        if m.cmd == key:
            return m
    return None


def list_exposed_commands() -> List[WebCommandMeta]:
    return [m for m in WEB_COMMANDS if m.exposed]


def build_registry_document() -> Dict[str, Any]:
    """GET /api/registry 응답 본문."""
    return {
        "version": REGISTRY_VERSION,
        "extension": "morph.tbs_control_2",
        "default_port": 8720,
        "poll_ms_recommended": 400,
        "test_page": "/api_tester.html",
        "main_panel": "/",
        "endpoints": [_meta_to_dict(e) for e in WEB_ENDPOINTS],
        "commands": [_meta_to_dict(c) for c in WEB_COMMANDS],
        "commands_exposed": [_meta_to_dict(c) for c in WEB_COMMANDS if c.exposed],
        "commands_planned": [_meta_to_dict(c) for c in WEB_COMMANDS if not c.exposed],
        "state_fields": [_meta_to_dict(s) for s in WEB_STATE_FIELDS],
        "web_fields": [_meta_to_dict(f) for f in WEB_FIELDS_KEYS],
    }


__all__ = [
    "REGISTRY_VERSION",
    "WEB_COMMANDS",
    "WEB_ENDPOINTS",
    "WEB_FIELDS_KEYS",
    "WEB_STATE_FIELDS",
    "WebCommandMeta",
    "WebEndpointMeta",
    "WebFieldsKeyMeta",
    "WebStateFieldMeta",
    "build_registry_document",
    "get_command_meta",
    "list_exposed_commands",
]
